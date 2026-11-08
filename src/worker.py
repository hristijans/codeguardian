"""The background worker: takes review jobs off RabbitMQ and runs them.

Run it with `python -m src.worker`. In Compose it is the `worker` service.
"""

import asyncio
import logging
import signal
from datetime import datetime, timezone
from typing import Any

import aio_pika
import redis.asyncio as aioredis
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage
from pydantic import ValidationError

from src.agent.diff import limit_diff
from src.agent.graph import build_graph
from src.agent.nodes import AnalysisError
from src.cache import JobStore
from src.config import get_settings
from src.github import GitHubClient, GitHubError
from src.queue import QUEUE_NAME, declare_queue
from src.schemas import JobStatus, JobStatusDetails, ReviewJobMessage

log = logging.getLogger("codeguardian.worker")

# Statuses that mean a job is done. A message for one of them is a repeat.
FINISHED = {JobStatus.COMPLETED, JobStatus.FAILED}


class Worker:
    """Runs one review job per message.

    `process_job` moves the job's record through its statuses. `handle_message`
    wraps it and decides what to tell the broker. The store, the GitHub client
    and the review graph are passed in, so tests can use their own. The graph
    is anything with `ainvoke(state)`, which is what `build_graph()` returns.
    """

    def __init__(
        self,
        store: JobStore,
        github: GitHubClient,
        graph: Any,
        max_diff_chars: int,
    ) -> None:
        self._store = store
        self._github = github
        self._graph = graph
        self._max_diff_chars = max_diff_chars

    async def process_job(self, message: ReviewJobMessage) -> None:
        """Run a job, ending with its record `completed` or `failed`.

        Problems with the job itself, such as GitHub answering 404, are saved
        on the record and not raised: retrying them would fail the same way.
        Anything else, such as Redis being down, is raised for the caller.
        """
        job = await self._store.get(message.job_id)
        if job is None:
            # Expired or never saved. With no record there is nowhere to put a
            # result, and nobody can look the job up, so there's nothing to do.
            log.warning("Skipping job %s: no record", message.job_id)
            return
        if job.status in FINISHED:
            log.info("Skipping job %s: already %s", job.job_id, job.status)
            return

        # Saved before the slow part, so a lookup shows the job has started.
        job = await self._set_status(job, JobStatus.PROCESSING)
        try:
            diff = await self._github.fetch_diff(message.repo_name, message.pr_number)
        except (GitHubError, ValueError) as error:
            # Both messages are written to be safe to store: GitHubError never
            # includes the response body.
            await self._set_status(job, JobStatus.FAILED, error=str(error))
            log.info("Job %s failed: %s", job.job_id, error)
            return

        limited = limit_diff(diff, self._max_diff_chars)
        # Only sizes are logged, because a diff can contain secrets.
        log.info(
            "Job %s: reviewing a diff of %d characters%s",
            job.job_id,
            limited.original_chars,
            " (truncated)" if limited.truncated else "",
        )
        try:
            result = await self._graph.ainvoke(
                {
                    "job_id": job.job_id,
                    "repo_name": job.repo_name,
                    "pr_number": job.pr_number,
                    "head_sha": job.head_sha,
                    "title": job.title,
                    "diff_text": limited.text,
                    "diff_truncated": limited.truncated,
                }
            )
        except AnalysisError as error:
            # The model answered, but not with a usable review. Asking again
            # would most likely give the same answer. The message never quotes
            # the reply, so it is safe to store. Any other error, such as the
            # model being unreachable, is raised and gets the retry.
            await self._set_status(job, JobStatus.FAILED, error=str(error))
            log.info("Job %s failed: %s", job.job_id, error)
            return

        await self._set_status(job, JobStatus.COMPLETED, report=result["final_summary"])
        log.info("Job %s completed", job.job_id)

    async def handle_message(self, incoming: AbstractIncomingMessage) -> None:
        """Process one message, then ack or reject it.

        The ack is sent only after the job's final status is saved. If the
        worker stops before then, the broker gives the message to the next
        consumer. This method never raises, so one bad message can't stop the
        consumer.
        """
        try:
            message = ReviewJobMessage.model_validate_json(incoming.body)
        except ValidationError:
            # It will never parse, so putting it back would only loop it.
            log.error("Rejecting a message that isn't a review job")
            await incoming.reject(requeue=False)
            return

        try:
            await self.process_job(message)
        except Exception:
            # One retry. `redelivered` is set by the broker when this message
            # was delivered before, so a second failure drops it rather than
            # looping forever.
            retry = not incoming.redelivered
            log.exception(
                "Job %s raised, %s",
                message.job_id,
                "requeueing it" if retry else "dropping it",
            )
            if not retry:
                await self._give_up(message.job_id)
            await incoming.reject(requeue=retry)
            return

        await incoming.ack()

    async def _give_up(self, job_id: str) -> None:
        """Mark a dropped job `failed`, so it doesn't stay `processing`.

        Best effort: the error that got here may be Redis itself failing.
        """
        try:
            job = await self._store.get(job_id)
            if job is not None and job.status not in FINISHED:
                await self._set_status(
                    job, JobStatus.FAILED, error="The worker could not process the job"
                )
        except Exception:
            log.exception("Could not mark job %s as failed", job_id)

    async def _set_status(
        self,
        job: JobStatusDetails,
        status: JobStatus,
        error: str | None = None,
        report: str | None = None,
    ) -> JobStatusDetails:
        updated = job.model_copy(
            update={
                "status": status,
                "error": error,
                "report": report,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await self._store.save(updated)
        return updated


async def start_consuming(
    channel: AbstractChannel, worker: Worker, queue_name: str = QUEUE_NAME
) -> str:
    """Start delivering messages from the queue to the worker.

    Returns the consumer tag, which can cancel the subscription.
    """
    # Without a limit the broker pushes every waiting message to this worker
    # at once. With 1, it sends the next only after the current one is acked,
    # so a second worker would share the load instead of finding the queue
    # empty.
    await channel.set_qos(prefetch_count=1)
    queue = await declare_queue(channel, queue_name)
    return await queue.consume(worker.handle_message)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()

    # Docker stops a container with SIGTERM. The main process of a container
    # ignores it unless it sets a handler, so without one every stop would wait
    # ten seconds and end with SIGKILL.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    redis = aioredis.from_url(settings.redis_url)
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    try:
        async with GitHubClient(settings.GITHUB_TOKEN) as github:
            channel = await connection.channel()
            graph = build_graph()
            worker = Worker(JobStore(redis), github, graph, settings.MAX_DIFF_CHARS)
            await start_consuming(channel, worker)
            log.info("Waiting for jobs on %s", QUEUE_NAME)
            await stop.wait()
            log.info("Stopping")
    finally:
        # A job still running is cut off here. Its message was never acked, so
        # the broker gives it to the next worker.
        await connection.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())

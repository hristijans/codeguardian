"""The background worker: takes review jobs off RabbitMQ and runs them.

Run it with `python -m src.worker`. In Compose it is the `worker` service.
"""

import asyncio
import logging
import signal
from datetime import datetime, timezone

import aio_pika
import redis.asyncio as aioredis
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage
from pydantic import ValidationError

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
    wraps it and decides what to tell the broker. The store and the GitHub
    client are passed in, so tests can use their own.
    """

    def __init__(self, store: JobStore, github: GitHubClient) -> None:
        self._store = store
        self._github = github

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

        # Simulated review: Unit 3 replaces this with the LangGraph agent.
        # Only the size is logged, because a diff can contain secrets.
        log.info("Job %s: fetched a diff of %d lines", job.job_id, diff.count("\n"))

        await self._set_status(job, JobStatus.COMPLETED)
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
    ) -> JobStatusDetails:
        updated = job.model_copy(
            update={
                "status": status,
                "error": error,
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
            await start_consuming(channel, Worker(JobStore(redis), github))
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

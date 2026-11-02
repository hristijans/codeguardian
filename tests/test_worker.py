import asyncio
from typing import Optional

import aio_pika
import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from src.cache import JobStore
from src.github import GitHubClient
from src.queue import JobPublisher
from src.schemas import JobStatus, JobStatusDetails, ReviewJobMessage
from src.worker import Worker, start_consuming

DIFF = "diff --git a/app.py b/app.py\n+print('hello')\n"


@pytest.fixture
def store(redis_client) -> JobStore:
    return JobStore(redis_client)


class FakeGitHub:
    """Stands in for api.github.com through httpx.MockTransport.

    The handler may be async, which lets a test look at Redis while the
    worker is waiting for the diff.
    """

    def __init__(self, handler=None):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._handler is None:
            return httpx.Response(200, text=DIFF)
        result = self._handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        return result


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub()


@pytest_asyncio.fixture
async def worker(store: JobStore, fake_github: FakeGitHub):
    github = GitHubClient(
        SecretStr("example-token"), transport=httpx.MockTransport(fake_github)
    )
    async with github:
        yield Worker(store, github)


def message_for(job: JobStatusDetails, **changes) -> ReviewJobMessage:
    fields = {
        "job_id": job.job_id,
        "repo_name": job.repo_name,
        "pr_number": job.pr_number,
        "head_sha": job.head_sha,
    }
    fields.update(changes)
    return ReviewJobMessage(**fields)


# process_job: the job's status changes


@pytest.mark.asyncio
async def test_queued_job_is_completed(worker: Worker, store: JobStore, make_job):
    job = make_job()
    await store.save(job)

    await worker.process_job(message_for(job))

    done = await store.get(job.job_id)
    assert done.status == JobStatus.COMPLETED
    assert done.error is None
    assert done.updated_at > job.updated_at
    # Everything else about the job stays as it was.
    assert done.model_copy(
        update={"status": job.status, "updated_at": job.updated_at}
    ) == job


@pytest.mark.asyncio
async def test_job_is_processing_while_the_diff_is_fetched(
    store: JobStore, make_job
):
    job = make_job()
    await store.save(job)
    seen: list[Optional[JobStatus]] = []

    async def look_at_redis(request):
        seen.append((await store.get(job.job_id)).status)
        return httpx.Response(200, text=DIFF)

    github = GitHubClient(
        SecretStr("example-token"),
        transport=httpx.MockTransport(FakeGitHub(look_at_redis)),
    )
    async with github:
        await Worker(store, github).process_job(message_for(job))

    assert seen == [JobStatus.PROCESSING]


@pytest.mark.asyncio
async def test_diff_is_fetched_for_the_message_pull_request(
    worker: Worker, store: JobStore, fake_github: FakeGitHub, make_job
):
    job = make_job(pr_number=42)
    await store.save(job)

    await worker.process_job(message_for(job))

    [request] = fake_github.requests
    assert request.url.path == "/repos/example-org/example-repo/pulls/42"


@pytest.mark.asyncio
async def test_github_error_fails_the_job(store: JobStore, make_job):
    job = make_job()
    await store.save(job)
    fake = FakeGitHub(lambda request: httpx.Response(404, text="Not Found"))
    github = GitHubClient(SecretStr("example-token"), transport=httpx.MockTransport(fake))

    async with github:
        # Doesn't raise: a failed job is a finished job.
        await Worker(store, github).process_job(message_for(job))

    failed = await store.get(job.job_id)
    assert failed.status == JobStatus.FAILED
    assert "404" in failed.error
    assert failed.updated_at > job.updated_at


@pytest.mark.asyncio
async def test_unreachable_github_fails_the_job(store: JobStore, make_job):
    job = make_job()
    await store.save(job)

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    github = GitHubClient(
        SecretStr("example-token"), transport=httpx.MockTransport(FakeGitHub(refuse))
    )
    async with github:
        await Worker(store, github).process_job(message_for(job))

    failed = await store.get(job.job_id)
    assert failed.status == JobStatus.FAILED
    assert "ConnectError" in failed.error


@pytest.mark.asyncio
async def test_invalid_repository_name_fails_the_job(
    worker: Worker, store: JobStore, fake_github: FakeGitHub, make_job
):
    job = make_job()
    await store.save(job)

    await worker.process_job(message_for(job, repo_name="not a repo"))

    failed = await store.get(job.job_id)
    assert failed.status == JobStatus.FAILED
    assert failed.error
    assert fake_github.requests == []


@pytest.mark.asyncio
async def test_missing_record_is_skipped(
    worker: Worker, store: JobStore, fake_github: FakeGitHub, make_job
):
    # The record expired or was never saved. There's nowhere to put a result,
    # so the worker does no work and saves nothing.
    job = make_job()

    await worker.process_job(message_for(job))

    assert fake_github.requests == []
    assert await store.get(job.job_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
async def test_finished_job_is_not_processed_again(
    worker: Worker, store: JobStore, fake_github: FakeGitHub, make_job, status
):
    # A message can arrive twice, for example when the worker stopped after
    # finishing a job but before its ack reached the broker.
    job = make_job(status=status)
    await store.save(job)

    await worker.process_job(message_for(job))

    assert fake_github.requests == []
    assert await store.get(job.job_id) == job


@pytest.mark.asyncio
async def test_processing_job_is_processed_again(
    worker: Worker, store: JobStore, make_job
):
    # A job left in "processing" means a worker stopped partway through, and
    # the broker gave the unacked message back.
    job = make_job(status=JobStatus.PROCESSING)
    await store.save(job)

    await worker.process_job(message_for(job))

    assert (await store.get(job.job_id)).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_unexpected_error_is_raised(fake_github: FakeGitHub, make_job):
    class BrokenStore:
        async def get(self, job_id):
            raise ConnectionError("Redis is down")

    github = GitHubClient(
        SecretStr("example-token"), transport=httpx.MockTransport(fake_github)
    )
    async with github:
        with pytest.raises(ConnectionError):
            await Worker(BrokenStore(), github).process_job(message_for(make_job()))


# handle_message: what the broker is told


class FakeIncoming:
    """Records what the worker tells the broker about a message."""

    def __init__(self, body: bytes, redelivered: bool = False, on_ack=None):
        self.body = body
        self.redelivered = redelivered
        self.calls: list[tuple] = []
        self._on_ack = on_ack

    async def ack(self):
        if self._on_ack is not None:
            await self._on_ack()
        self.calls.append(("ack",))

    async def reject(self, requeue: bool = False):
        self.calls.append(("reject", requeue))


def body_for(job: JobStatusDetails, **changes) -> bytes:
    return message_for(job, **changes).model_dump_json().encode()


@pytest.mark.asyncio
async def test_processed_message_is_acked(worker: Worker, store: JobStore, make_job):
    job = make_job()
    await store.save(job)
    incoming = FakeIncoming(body_for(job))

    await worker.handle_message(incoming)

    assert incoming.calls == [("ack",)]


@pytest.mark.asyncio
async def test_ack_comes_after_the_job_is_finished(
    worker: Worker, store: JobStore, make_job
):
    job = make_job()
    await store.save(job)
    status_at_ack: list[JobStatus] = []

    async def record_status():
        status_at_ack.append((await store.get(job.job_id)).status)

    await worker.handle_message(FakeIncoming(body_for(job), on_ack=record_status))

    assert status_at_ack == [JobStatus.COMPLETED]


@pytest.mark.asyncio
async def test_failed_job_is_still_acked(store: JobStore, make_job):
    # The failure is recorded on the job. Retrying a 404 would fail again.
    job = make_job()
    await store.save(job)
    fake = FakeGitHub(lambda request: httpx.Response(404))
    github = GitHubClient(SecretStr("example-token"), transport=httpx.MockTransport(fake))
    incoming = FakeIncoming(body_for(job))

    async with github:
        await Worker(store, github).handle_message(incoming)

    assert incoming.calls == [("ack",)]
    assert (await store.get(job.job_id)).status == JobStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not json", b'{"job_id": "only-this"}'])
async def test_unreadable_message_is_rejected_for_good(worker: Worker, body: bytes):
    incoming = FakeIncoming(body)

    await worker.handle_message(incoming)

    assert incoming.calls == [("reject", False)]


@pytest.mark.asyncio
async def test_unexpected_error_requeues_the_message_once(
    fake_github: FakeGitHub, make_job
):
    class BrokenStore:
        async def get(self, job_id):
            raise ConnectionError("Redis is down")

    github = GitHubClient(
        SecretStr("example-token"), transport=httpx.MockTransport(fake_github)
    )
    first = FakeIncoming(body_for(make_job()))
    again = FakeIncoming(body_for(make_job()), redelivered=True)

    async with github:
        # Neither call raises: the consumer must keep running.
        await Worker(BrokenStore(), github).handle_message(first)
        await Worker(BrokenStore(), github).handle_message(again)

    assert first.calls == [("reject", True)]
    assert again.calls == [("reject", False)]


@pytest.mark.asyncio
async def test_dropped_job_is_marked_failed(store: JobStore, make_job):
    # A bug, not a problem with the job: retried once, then given up on.
    job = make_job()
    await store.save(job)

    def crash(request):
        raise RuntimeError("bug in the worker")

    github = GitHubClient(
        SecretStr("example-token"), transport=httpx.MockTransport(FakeGitHub(crash))
    )
    first = FakeIncoming(body_for(job))
    again = FakeIncoming(body_for(job), redelivered=True)

    async with github:
        worker = Worker(store, github)
        await worker.handle_message(first)
        # Still processing: the retry may succeed.
        assert (await store.get(job.job_id)).status == JobStatus.PROCESSING
        await worker.handle_message(again)

    assert first.calls == [("reject", True)]
    assert again.calls == [("reject", False)]
    failed = await store.get(job.job_id)
    assert failed.status == JobStatus.FAILED
    # The exception text could hold anything, so it isn't stored.
    assert "bug in the worker" not in failed.error


# start_consuming: the worker against the real broker


@pytest.mark.asyncio
async def test_published_job_is_consumed_and_acked(
    worker: Worker, store: JobStore, rabbit_channel, test_queue, settings, make_job
):
    job = make_job()
    await store.save(job)
    await JobPublisher(rabbit_channel, test_queue.name).publish(message_for(job))

    # Waiting for the status alone could close the channel between the save
    # and the ack, so wait for the whole handler instead.
    handled = asyncio.Event()
    handle = worker.handle_message

    async def handle_and_signal(incoming):
        await handle(incoming)
        handled.set()

    worker.handle_message = handle_and_signal

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    try:
        channel = await connection.channel()
        await start_consuming(channel, worker, test_queue.name)
        await asyncio.wait_for(handled.wait(), timeout=5)
    finally:
        # Closing the channel gives back any message it didn't ack.
        await connection.close()

    assert (await store.get(job.job_id)).status == JobStatus.COMPLETED
    # Only an ack keeps the message from coming back to the queue.
    assert await test_queue.get(fail=False) is None

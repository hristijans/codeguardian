import asyncio
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

import aio_pika
import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient, Response
from pydantic import SecretStr

from src.cache import JobStore, delivery_key, job_key
from src.config import Settings, get_settings
from src.github import GitHubClient
from src.main import app, get_job_publisher, get_job_store
from src.queue import JobPublisher, PublishError
from src.schemas import JobStatus, ReviewJobMessage
from src.worker import Worker, start_consuming
from tests.fakes import FakeGraph

URL = "/api/v1/webhooks/github/pr"
SECRET = "example-webhook-secret"
FIXTURE = Path(__file__).parent / "fixtures" / "pull_request_opened.json"


@pytest.fixture(autouse=True)
def webhook_secret(settings: Settings):
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={
            "GITHUB_WEBHOOK_SECRET": SecretStr(SECRET),
            "REVIEW_REPOSITORIES": "example-org/example-repo",
        }
    )
    yield
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(autouse=True)
async def store(redis_client) -> AsyncIterator[JobStore]:
    """Point the route at the test's Redis client, and clean up afterwards.

    Tests send delivery IDs starting with `test-`, so their claims and jobs can
    be found and deleted without touching anything the running app saved.
    """
    store = JobStore(redis_client)
    app.dependency_overrides[get_job_store] = lambda: store
    yield store
    async for key in redis_client.scan_iter(match=delivery_key("test-*")):
        job_id = await redis_client.get(key)
        if job_id is not None:
            await redis_client.delete(job_key(job_id.decode()))
        await redis_client.delete(key)


@pytest.fixture(autouse=True)
def publisher(rabbit_channel, test_queue) -> JobPublisher:
    """Send the route's messages to this test's own queue."""
    publisher = JobPublisher(rabbit_channel, test_queue.name)
    app.dependency_overrides[get_job_publisher] = lambda: publisher
    return publisher


async def drain(queue) -> list[ReviewJobMessage]:
    """Take every message off a queue and return them in order."""
    messages = []
    while (message := await queue.get(fail=False)) is not None:
        await message.ack()
        messages.append(ReviewJobMessage.model_validate_json(message.body))
    return messages


@pytest.fixture
def raw_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def deliver(
    client: AsyncClient,
    body: bytes,
    event: Optional[str] = "pull_request",
    signature: Optional[str] = None,
    delivery: Optional[str] = "new",
) -> Response:
    headers = {"Content-Type": "application/json"}
    headers["X-Hub-Signature-256"] = signature or sign(body)
    if event is not None:
        headers["X-GitHub-Event"] = event
    # "new" gives each call its own ID, as GitHub does for each new delivery.
    if delivery == "new":
        delivery = f"test-{uuid.uuid4()}"
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    return await client.post(URL, content=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
async def test_reviewed_action_is_accepted_as_a_queued_job(
    async_client: AsyncClient, raw_payload: dict, action: str
):
    raw_payload["action"] = action

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["job_id"]


@pytest.mark.asyncio
async def test_each_delivery_gets_its_own_job_id(
    async_client: AsyncClient, raw_payload: dict
):
    body = json.dumps(raw_payload).encode()

    first = await deliver(async_client, body)
    second = await deliver(async_client, body)

    assert first.json()["job_id"] != second.json()["job_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["closed", "labeled", "edited"])
async def test_unreviewed_action_creates_no_job(
    async_client: AsyncClient, raw_payload: dict, action: str
):
    raw_payload["action"] = action

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_ping_is_acknowledged_without_a_job(async_client: AsyncClient):
    # A ping has no pull_request key, so it must never reach the payload model.
    body = json.dumps({"zen": "Example.", "hook_id": 1}).encode()

    response = await deliver(async_client, body, event="ping")

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["push", "issues", None])
async def test_other_events_create_no_job(
    async_client: AsyncClient, raw_payload: dict, event: Optional[str]
):
    # Even a full pull request body is skipped when the event type differs.
    body = json.dumps(raw_payload).encode()

    response = await deliver(async_client, body, event=event)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_signature_is_checked_over_the_exact_bytes_received(
    async_client: AsyncClient, raw_payload: dict
):
    # Re-encoding this body would lose the spacing and break the signature.
    body = json.dumps(raw_payload, indent=4, sort_keys=True).encode() + b"\n"

    response = await deliver(async_client, body)

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_bad_signature_is_rejected(
    async_client: AsyncClient, raw_payload: dict
):
    body = json.dumps(raw_payload).encode()

    response = await deliver(async_client, body, signature="sha256=" + "0" * 64)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_is_rejected(
    async_client: AsyncClient, raw_payload: dict
):
    response = await async_client.post(
        URL,
        content=json.dumps(raw_payload).encode(),
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bad_signature_is_rejected_before_the_body_is_parsed(
    async_client: AsyncClient,
):
    # 401 rather than 422 shows that untrusted data was never parsed.
    response = await deliver(
        async_client, b"not json", signature="sha256=" + "0" * 64
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bad_signature_is_rejected_for_ignored_events(
    async_client: AsyncClient,
):
    response = await deliver(
        async_client, b"{}", event="ping", signature="sha256=" + "0" * 64
    )

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"not json", b"{}", b'{"action": "opened"}'], ids=["text", "empty", "partial"]
)
async def test_signed_pull_request_with_a_bad_body_is_rejected(
    async_client: AsyncClient, body: bytes
):
    response = await deliver(async_client, body)

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid payload"}


@pytest.mark.asyncio
async def test_repository_not_on_the_list_creates_no_job(
    async_client: AsyncClient, raw_payload: dict
):
    raw_payload["repository"]["full_name"] = "example-org/another-repo"

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_repository_match_ignores_case(
    async_client: AsyncClient, raw_payload: dict
):
    raw_payload["repository"]["full_name"] = "Example-Org/Example-Repo"

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_empty_list_reviews_nothing(
    async_client: AsyncClient, raw_payload: dict, settings: Settings
):
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={
            "GITHUB_WEBHOOK_SECRET": SecretStr(SECRET),
            "REVIEW_REPOSITORIES": "",
        }
    )

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_accepted_job_is_saved_as_queued(
    async_client: AsyncClient, raw_payload: dict, store: JobStore
):
    response = await deliver(async_client, json.dumps(raw_payload).encode())

    job = await store.get(response.json()["job_id"])
    assert job is not None
    assert job.status == JobStatus.QUEUED
    assert job.repo_name == raw_payload["repository"]["full_name"]
    assert job.pr_number == raw_payload["pull_request"]["number"]
    assert job.head_sha == raw_payload["pull_request"]["head"]["sha"]
    assert job.title == raw_payload["pull_request"]["title"]
    assert job.created_at == job.updated_at
    assert job.created_at.tzinfo is not None


@pytest.mark.asyncio
async def test_repeated_delivery_returns_the_first_job(
    async_client: AsyncClient, raw_payload: dict
):
    body = json.dumps(raw_payload).encode()
    delivery = f"test-{uuid.uuid4()}"

    first = await deliver(async_client, body, delivery=delivery)
    second = await deliver(async_client, body, delivery=delivery)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json() == {
        "status": "duplicate",
        "job_id": first.json()["job_id"],
    }


@pytest.mark.asyncio
async def test_repeated_delivery_saves_no_second_job(
    async_client: AsyncClient, raw_payload: dict, redis_client
):
    body = json.dumps(raw_payload).encode()
    delivery = f"test-{uuid.uuid4()}"
    before = {key async for key in redis_client.scan_iter(match="job:*")}

    await deliver(async_client, body, delivery=delivery)
    await deliver(async_client, body, delivery=delivery)

    after = {key async for key in redis_client.scan_iter(match="job:*")}
    assert len(after - before) == 1


@pytest.mark.asyncio
async def test_missing_delivery_id_is_rejected_without_a_job(
    async_client: AsyncClient, raw_payload: dict, redis_client
):
    before = {key async for key in redis_client.scan_iter(match="job:*")}

    response = await deliver(
        async_client, json.dumps(raw_payload).encode(), delivery=None
    )

    assert response.status_code == 400
    after = {key async for key in redis_client.scan_iter(match="job:*")}
    assert after == before


@pytest.mark.asyncio
async def test_ignored_event_does_not_claim_its_delivery(
    async_client: AsyncClient, raw_payload: dict, redis_client
):
    # Otherwise a later, reviewable event can't reuse the ID. Not expected from
    # GitHub, but claims should only mean "a job exists for this delivery".
    raw_payload["action"] = "closed"
    delivery = f"test-{uuid.uuid4()}"

    await deliver(async_client, json.dumps(raw_payload).encode(), delivery=delivery)

    assert await redis_client.exists(delivery_key(delivery)) == 0


class FailingStore(JobStore):
    async def save(self, job) -> None:
        raise ConnectionError("Redis went away")


@pytest.mark.asyncio
async def test_failed_save_releases_the_claim(
    async_client: AsyncClient, raw_payload: dict, redis_client
):
    # A failed delivery can be redelivered. A leftover claim would make the
    # redelivery look like a duplicate of a job that was never saved.
    app.dependency_overrides[get_job_store] = lambda: FailingStore(redis_client)
    delivery = f"test-{uuid.uuid4()}"

    with pytest.raises(ConnectionError):
        await deliver(
            async_client, json.dumps(raw_payload).encode(), delivery=delivery
        )

    assert await redis_client.exists(delivery_key(delivery)) == 0


@pytest.mark.asyncio
async def test_accepted_job_is_published(
    async_client: AsyncClient, raw_payload: dict, test_queue
):
    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert await drain(test_queue) == [
        ReviewJobMessage(
            job_id=response.json()["job_id"],
            repo_name=raw_payload["repository"]["full_name"],
            pr_number=raw_payload["pull_request"]["number"],
            head_sha=raw_payload["pull_request"]["head"]["sha"],
        )
    ]


@pytest.mark.asyncio
async def test_repeated_delivery_publishes_once(
    async_client: AsyncClient, raw_payload: dict, test_queue
):
    body = json.dumps(raw_payload).encode()
    delivery = f"test-{uuid.uuid4()}"

    await deliver(async_client, body, delivery=delivery)
    await deliver(async_client, body, delivery=delivery)

    assert len(await drain(test_queue)) == 1


@pytest.mark.asyncio
async def test_ignored_event_publishes_nothing(
    async_client: AsyncClient, raw_payload: dict, test_queue
):
    raw_payload["action"] = "closed"

    await deliver(async_client, json.dumps(raw_payload).encode())

    assert await drain(test_queue) == []


@pytest.mark.asyncio
async def test_failed_publish_marks_the_job_failed_and_releases_the_claim(
    async_client: AsyncClient, raw_payload: dict, redis_client, rabbit_channel,
    store: JobStore,
):
    # A job left as "queued" with no message would never be picked up, and a
    # leftover claim would turn the redelivery into a duplicate.
    app.dependency_overrides[get_job_publisher] = lambda: JobPublisher(
        rabbit_channel, "test-queue-that-does-not-exist"
    )
    delivery = f"test-{uuid.uuid4()}"
    before = {key async for key in redis_client.scan_iter(match="job:*")}

    with pytest.raises(PublishError):
        await deliver(
            async_client, json.dumps(raw_payload).encode(), delivery=delivery
        )

    assert await redis_client.exists(delivery_key(delivery)) == 0
    new_keys = {key async for key in redis_client.scan_iter(match="job:*")} - before
    assert len(new_keys) == 1
    job_id = new_keys.pop().decode().removeprefix("job:")
    job = await store.get(job_id)
    try:
        assert job.status == JobStatus.FAILED
        assert job.error
        assert job.updated_at >= job.created_at
    finally:
        # The released claim no longer points here, so the fixture can't.
        await redis_client.delete(job_key(job_id))


@pytest.mark.asyncio
async def test_accepted_job_can_be_looked_up(
    async_client: AsyncClient, raw_payload: dict
):
    response = await deliver(async_client, json.dumps(raw_payload).encode())
    job_id = response.json()["job_id"]

    lookup = await async_client.get(f"/api/v1/jobs/{job_id}")

    assert lookup.status_code == 200
    assert lookup.json()["status"] == "queued"
    assert lookup.json()["pr_number"] == raw_payload["pull_request"]["number"]


@pytest.mark.asyncio
async def test_signed_webhook_is_reviewed_by_the_worker(
    async_client: AsyncClient,
    raw_payload: dict,
    store: JobStore,
    test_queue,
    settings: Settings,
):
    """The Unit 2 milestone in one test: webhook, queue, worker, status.

    Everything is real except GitHub: the route, Redis, RabbitMQ, and the
    worker's consumer. The worker reads from this test's own queue.
    """
    diff_requests: list[httpx.Request] = []

    def fake_github(request: httpx.Request) -> httpx.Response:
        diff_requests.append(request)
        return httpx.Response(200, text="diff --git a/app.py b/app.py\n+x = 1\n")

    response = await deliver(async_client, json.dumps(raw_payload).encode())
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    # Checked before the worker starts, which could otherwise finish first.
    before = await async_client.get(f"/api/v1/jobs/{job_id}")
    assert before.json()["status"] == "queued"

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    github = GitHubClient(
        SecretStr("example-token"), transport=httpx.MockTransport(fake_github)
    )
    try:
        async with github:
            channel = await connection.channel()
            await start_consuming(channel, Worker(store, github, FakeGraph(), 6000), test_queue.name)
            for _ in range(50):
                job = (await async_client.get(f"/api/v1/jobs/{job_id}")).json()
                if job["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.1)
    finally:
        await connection.close()

    assert job["status"] == "completed"
    assert job["error"] is None
    [request] = diff_requests
    number = raw_payload["pull_request"]["number"]
    assert request.url.path == f"/repos/example-org/example-repo/pulls/{number}"

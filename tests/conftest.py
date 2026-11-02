import uuid
from datetime import datetime, timezone

import aio_pika
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.cache import job_key
from src.config import get_settings, Settings
from src.schemas import JobStatus, JobStatusDetails


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def redis_client(settings: Settings):
    """A client for the real Redis container, closed after each test."""
    client = aioredis.from_url(settings.redis_url)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def rabbit_channel(settings: Settings):
    """A channel on the real RabbitMQ container, closed after each test."""
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    yield channel
    await connection.close()


@pytest_asyncio.fixture
async def test_queue(rabbit_channel):
    """A durable queue with a unique name, deleted after the test.

    Tests never use the real queue, where a running worker would pick their
    messages up.
    """
    from src.queue import declare_queue

    queue = await declare_queue(rabbit_channel, f"test-{uuid.uuid4()}")
    yield queue
    await queue.delete(if_unused=False, if_empty=False)


@pytest_asyncio.fixture
async def make_job(redis_client):
    """Build job records, and delete their keys after the test.

    Real Redis is shared with the running app, so only this test's keys go.
    """
    created: list[str] = []

    def build(**changes) -> JobStatusDetails:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        fields = {
            "job_id": str(uuid.uuid4()),
            "status": JobStatus.QUEUED,
            "repo_name": "example-org/example-repo",
            "pr_number": 1,
            "head_sha": "0" * 40,
            "title": "Example change",
            "created_at": now,
            "updated_at": now,
        }
        fields.update(changes)
        created.append(fields["job_id"])
        return JobStatusDetails(**fields)

    yield build
    if created:
        await redis_client.delete(*(job_key(job_id) for job_id in created))

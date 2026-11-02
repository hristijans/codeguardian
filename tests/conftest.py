import uuid

import aio_pika
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.config import get_settings, Settings


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

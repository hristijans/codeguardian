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

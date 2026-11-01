import pytest
from httpx import AsyncClient
from src.config import Settings


@pytest.mark.asyncio
async def test_root_endpoint(async_client: AsyncClient):
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "CodeGuardian AI"
    assert data["status"] == "operational"


@pytest.mark.asyncio
async def test_settings_loading(settings: Settings):
    assert settings.APP_NAME == "CodeGuardian AI"
    assert "postgresql+asyncpg://" in settings.postgres_dsn
    assert "redis://" in settings.redis_url
    assert "amqp://" in settings.rabbitmq_url


@pytest.mark.asyncio
async def test_health_check_endpoint(async_client: AsyncClient):
    response = await async_client.get("/health")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "services" in data
    assert "postgres" in data["services"]
    assert "redis" in data["services"]
    assert "rabbitmq" in data["services"]
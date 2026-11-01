import pytest
from httpx import AsyncClient
from src.config import Settings, get_settings
from src.main import app


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
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["services"]["postgres"]["status"] == "healthy"
    assert data["services"]["redis"]["status"] == "healthy"
    assert data["services"]["rabbitmq"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_check_reports_failed_service(
    async_client: AsyncClient, settings: Settings
):
    # Nothing listens on port 1, so the Redis check fails straight away.
    broken_settings = settings.model_copy(
        update={"REDIS_HOST": "localhost", "REDIS_PORT": 1}
    )
    app.dependency_overrides[get_settings] = lambda: broken_settings
    try:
        response = await async_client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["services"]["redis"]["status"] == "unhealthy"
    assert data["services"]["postgres"]["status"] == "healthy"
    assert data["services"]["rabbitmq"]["status"] == "healthy"
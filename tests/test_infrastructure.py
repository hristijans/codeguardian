import gc
import warnings

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from src import main
from src.config import Settings, get_settings
from src.main import app, check_postgres


async def run_and_collect_resource_warnings(settings: Settings):
    """Run the Postgres check and return its result plus any leak warnings."""
    # Clear garbage left by earlier tests so only this check's leaks are seen.
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        result = await check_postgres(settings)
        # A leaked connection only warns once it is garbage collected.
        gc.collect()
    leaks = [w for w in caught if issubclass(w.category, ResourceWarning)]
    return result, leaks


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


@pytest.mark.asyncio
async def test_check_postgres_closes_connection_when_healthy(settings: Settings):
    result, leaks = await run_and_collect_resource_warnings(settings)

    assert result == {"status": "healthy"}
    assert leaks == []


@pytest.mark.asyncio
async def test_check_postgres_closes_connection_when_query_fails(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    # Bad SQL fails after the connection opens, which is when a leak can happen.
    monkeypatch.setattr(main, "text", lambda _: text("SELECT * FROM no_such_table"))

    result, leaks = await run_and_collect_resource_warnings(settings)

    assert result["status"] == "unhealthy"
    assert "no_such_table" in result["error"]
    assert leaks == []

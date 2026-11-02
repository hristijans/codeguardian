import uuid

import pytest
from httpx import AsyncClient

from src.cache import JobStore
from src.main import app, get_job_store
from src.schemas import JobStatus, JobStatusDetails


@pytest.fixture(autouse=True)
def store(redis_client) -> JobStore:
    store = JobStore(redis_client)
    app.dependency_overrides[get_job_store] = lambda: store
    yield store
    app.dependency_overrides.clear()


def url(job_id: str) -> str:
    return f"/api/v1/jobs/{job_id}"


@pytest.mark.asyncio
async def test_saved_job_is_returned(
    async_client: AsyncClient, store: JobStore, make_job
):
    job = make_job()
    await store.save(job)

    response = await async_client.get(url(job.job_id))

    assert response.status_code == 200
    assert JobStatusDetails.model_validate(response.json()) == job


@pytest.mark.asyncio
async def test_failed_job_includes_its_error(
    async_client: AsyncClient, store: JobStore, make_job
):
    job = make_job(status=JobStatus.FAILED, error="Could not queue the job")
    await store.save(job)

    response = await async_client.get(url(job.job_id))

    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "Could not queue the job"


@pytest.mark.asyncio
async def test_unknown_job_is_404(async_client: AsyncClient):
    response = await async_client.get(url(str(uuid.uuid4())))

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize("job_id", ["not-a-uuid", "*", "1234"])
async def test_id_that_is_not_a_uuid_is_422(async_client: AsyncClient, job_id: str):
    # Rejected before Redis is asked, so odd values never reach a key name.
    response = await async_client.get(url(job_id))

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_uppercase_id_finds_the_job(
    async_client: AsyncClient, store: JobStore, make_job
):
    # Job IDs are stored in the lowercase form `str(uuid4())` produces.
    job = make_job()
    await store.save(job)

    response = await async_client.get(url(job.job_id.upper()))

    assert response.status_code == 200
    assert response.json()["job_id"] == job.job_id

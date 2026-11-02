import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from src.cache import JOB_TTL_SECONDS, JobStore, job_key
from src.config import Settings
from src.schemas import JobStatus, JobStatusDetails


@pytest_asyncio.fixture
async def redis_client(settings: Settings):
    client = aioredis.from_url(settings.redis_url)
    yield client
    await client.aclose()


@pytest.fixture
def store(redis_client) -> JobStore:
    return JobStore(redis_client)


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


def test_key_uses_job_prefix():
    assert job_key("abc") == "job:abc"


def test_ttl_is_24_hours():
    assert JOB_TTL_SECONDS == 24 * 60 * 60


@pytest.mark.asyncio
async def test_saved_job_reads_back_equal(store: JobStore, make_job):
    job = make_job()
    await store.save(job)
    assert await store.get(job.job_id) == job


@pytest.mark.asyncio
async def test_unknown_job_returns_none(store: JobStore):
    assert await store.get(str(uuid.uuid4())) is None


@pytest.mark.asyncio
async def test_saved_job_expires_in_24_hours(store: JobStore, redis_client, make_job):
    job = make_job()
    await store.save(job)
    ttl = await redis_client.ttl(job_key(job.job_id))
    # A second or two may pass between the save and the check.
    assert JOB_TTL_SECONDS - 5 <= ttl <= JOB_TTL_SECONDS


@pytest.mark.asyncio
async def test_saving_again_replaces_the_record(store: JobStore, make_job):
    job = make_job()
    await store.save(job)
    failed = job.model_copy(update={"status": JobStatus.FAILED, "error": "boom"})
    await store.save(failed)
    loaded = await store.get(job.job_id)
    assert loaded.status == JobStatus.FAILED
    assert loaded.error == "boom"


@pytest.mark.asyncio
async def test_record_is_stored_as_json(store: JobStore, redis_client, make_job):
    job = make_job()
    await store.save(job)
    raw = await redis_client.get(job_key(job.job_id))
    assert JobStatusDetails.model_validate_json(raw) == job

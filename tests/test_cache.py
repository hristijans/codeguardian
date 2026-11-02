import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from src.cache import JOB_TTL_SECONDS, JobStore, delivery_key, job_key
from src.schemas import JobStatus, JobStatusDetails


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


@pytest_asyncio.fixture
async def delivery_id(redis_client):
    """A fresh delivery ID whose claim is deleted after the test."""
    value = f"test-{uuid.uuid4()}"
    yield value
    await redis_client.delete(delivery_key(value))


def test_delivery_key_uses_delivery_prefix():
    assert delivery_key("abc") == "delivery:abc"


@pytest.mark.asyncio
async def test_first_claim_wins(store: JobStore, delivery_id: str):
    assert await store.claim_delivery(delivery_id, "job-1") is None


@pytest.mark.asyncio
async def test_second_claim_returns_the_first_job_id(
    store: JobStore, delivery_id: str
):
    await store.claim_delivery(delivery_id, "job-1")
    assert await store.claim_delivery(delivery_id, "job-2") == "job-1"


@pytest.mark.asyncio
async def test_claim_expires_in_24_hours(
    store: JobStore, redis_client, delivery_id: str
):
    await store.claim_delivery(delivery_id, "job-1")
    ttl = await redis_client.ttl(delivery_key(delivery_id))
    assert JOB_TTL_SECONDS - 5 <= ttl <= JOB_TTL_SECONDS


@pytest.mark.asyncio
async def test_released_claim_can_be_claimed_again(
    store: JobStore, delivery_id: str
):
    await store.claim_delivery(delivery_id, "job-1")
    await store.release_delivery(delivery_id)
    assert await store.claim_delivery(delivery_id, "job-2") is None


@pytest.mark.asyncio
async def test_losing_claim_does_not_replace_the_winner(
    store: JobStore, delivery_id: str
):
    # A repeat must leave the claim alone. Otherwise it would point at the
    # repeat's job ID, which is never saved.
    await store.claim_delivery(delivery_id, "job-1")
    await store.claim_delivery(delivery_id, "job-2")
    assert await store.claim_delivery(delivery_id, "job-3") == "job-1"

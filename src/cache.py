from typing import Optional

import redis.asyncio as aioredis

from src.schemas import JobStatusDetails

# Job records are working state, not history. A day is long enough to look up a
# review's outcome, and the expiry keeps Redis from filling up with old jobs.
JOB_TTL_SECONDS = 24 * 60 * 60


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


class JobStore:
    """Saves and loads job status records in Redis.

    Each job is one JSON string under `job:{job_id}`. The Redis client is passed
    in rather than created here, so the app and the tests decide how to connect.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def save(self, job: JobStatusDetails) -> None:
        # `ex` sets the expiry in the same command as the value. Saving again
        # replaces the record and restarts the 24 hours.
        await self._redis.set(
            job_key(job.job_id), job.model_dump_json(), ex=JOB_TTL_SECONDS
        )

    async def get(self, job_id: str) -> Optional[JobStatusDetails]:
        raw = await self._redis.get(job_key(job_id))
        if raw is None:
            return None
        return JobStatusDetails.model_validate_json(raw)

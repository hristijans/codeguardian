import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, Any, Optional, Union
from fastapi import FastAPI, Depends, Header, HTTPException, Request, status, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import redis.asyncio as aioredis
import aio_pika

from src.cache import JobStore
from src.config import Settings, get_settings
from src.github import verify_signature
from src.queue import JobPublisher, declare_queue
from src.schemas import (
    JobResponse,
    JobStatus,
    JobStatusDetails,
    PRWebhookPayload,
    ReviewJobMessage,
)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Code before `yield` runs once at startup, and code after it at shutdown.
    # One shared client keeps a pool of connections, instead of each request
    # opening its own. Creating it doesn't connect yet.
    settings = get_settings()
    app.state.redis = aioredis.from_url(settings.redis_url)
    # Unlike Redis, this connects now, so the API won't start without
    # RabbitMQ. `connect_robust` reconnects on its own if the link drops later.
    app.state.rabbitmq = await aio_pika.connect_robust(settings.rabbitmq_url)
    app.state.rabbitmq_channel = await app.state.rabbitmq.channel()
    # A message sent before its queue exists is dropped, so declare it first.
    await declare_queue(app.state.rabbitmq_channel)
    yield
    await app.state.rabbitmq.close()
    await app.state.redis.aclose()


app = FastAPI(
    title="CodeGuardian AI API",
    description="Autonomous Multi-Agent Code Review & Security Audit Pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

REVIEWED_ACTIONS = {"opened", "reopened", "synchronize"}


def get_job_store(request: Request) -> JobStore:
    return JobStore(request.app.state.redis)


def get_job_publisher(request: Request) -> JobPublisher:
    return JobPublisher(request.app.state.rabbitmq_channel)


async def check_postgres(settings: Settings) -> Dict[str, Any]:
    try:
        engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
        try:
            async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with async_session() as session:
                result = await session.execute(text("SELECT 1"))
                return {"status": "healthy" if result.scalar() == 1 else "unhealthy"}
        finally:
            # Runs after the session has returned its connection to the pool,
            # and on failure too. dispose() only closes pooled connections.
            await engine.dispose()
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def check_redis(settings: Settings) -> Dict[str, Any]:
    try:
        client = aioredis.from_url(settings.redis_url, socket_timeout=2.0)
        pong = await client.ping()
        await client.aclose()
        return {"status": "healthy" if pong else "unhealthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def check_rabbitmq(settings: Settings) -> Dict[str, Any]:
    try:
        connection = await aio_pika.connect_robust(settings.rabbitmq_url, timeout=2.0)
        await connection.close()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/", tags=["General"])
async def root(settings: Settings = Depends(get_settings)):
    return {
        "app": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "status": "operational"
    }


@app.get("/health", tags=["Infrastructure Checks"])
async def health_check(response: Response, settings: Settings = Depends(get_settings)):
    postgres_task = check_postgres(settings)
    redis_task = check_redis(settings)
    rabbitmq_task = check_rabbitmq(settings)

    postgres_res, redis_res, rabbitmq_res = await asyncio.gather(
        postgres_task, redis_task, rabbitmq_task
    )

    all_healthy = all(
        res.get("status") == "healthy"
        for res in [postgres_res, redis_res, rabbitmq_res]
    )

    if not all_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "healthy" if all_healthy else "unhealthy",
        "services": {
            "postgres": postgres_res,
            "redis": redis_res,
            "rabbitmq": rabbitmq_res,
        }
    }


@app.post(
    "/api/v1/webhooks/github/pr",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Webhooks"],
)
async def github_webhook(
    request: Request,
    response: Response,
    x_github_event: Optional[str] = Header(default=None),
    x_hub_signature_256: Optional[str] = Header(default=None),
    x_github_delivery: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
    publisher: JobPublisher = Depends(get_job_publisher),
) -> Union[JobResponse, Dict[str, str]]:
    # The body is read by hand because a Pydantic parameter would parse it
    # before the signature check, and the check needs the exact bytes.
    body = await request.body()
    secret = settings.GITHUB_WEBHOOK_SECRET.get_secret_value()
    if not verify_signature(body, x_hub_signature_256, secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")

    # Checked before parsing, because other events, such as ping, have a
    # different shape. Answering with success keeps them from showing as
    # failed deliveries in GitHub's webhook log.
    if x_github_event != "pull_request":
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored"}

    try:
        payload = PRWebhookPayload.model_validate_json(body)
    except ValidationError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid payload"
        ) from None

    repository = payload.repository.full_name.lower()
    if (
        payload.action not in REVIEWED_ACTIONS
        or repository not in settings.review_repositories
    ):
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored"}

    # Checked only now, so that ignored events don't need one.
    if not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing delivery ID")

    # A redelivery, sent by hand or through GitHub's API, reuses the original
    # delivery ID. Claiming the ID before saving means a repeat finds the claim
    # and creates nothing.
    job_id = str(uuid.uuid4())
    existing_job_id = await store.claim_delivery(x_github_delivery, job_id)
    if existing_job_id is not None:
        response.status_code = status.HTTP_200_OK
        return {"status": "duplicate", "job_id": existing_job_id}

    now = datetime.now(timezone.utc)
    job = JobStatusDetails(
        job_id=job_id,
        status=JobStatus.QUEUED,
        repo_name=payload.repository.full_name,
        pr_number=payload.pull_request.number,
        head_sha=payload.pull_request.head.sha,
        title=payload.pull_request.title,
        created_at=now,
        updated_at=now,
    )
    try:
        await store.save(job)
    except Exception:
        # Without this, a redelivery would be called a duplicate of a job that
        # was never saved. The error still reaches the caller as a 500.
        await store.release_delivery(x_github_delivery)
        raise

    # Saved before publishing, so the worker never receives a job that has no
    # record yet.
    try:
        await publisher.publish(
            ReviewJobMessage(
                job_id=job.job_id,
                repo_name=job.repo_name,
                pr_number=job.pr_number,
                head_sha=job.head_sha,
            )
        )
    except Exception as error:
        # A "queued" job with no message would wait forever, so record the
        # failure. Releasing the claim lets a redelivery create a new job.
        failed = job.model_copy(
            update={
                "status": JobStatus.FAILED,
                "error": f"Could not queue the job: {type(error).__name__}",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await store.save(failed)
        await store.release_delivery(x_github_delivery)
        raise

    return JobResponse(job_id=job_id, status=JobStatus.QUEUED)

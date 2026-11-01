import asyncio
from typing import Dict, Any
from fastapi import FastAPI, Depends, status, Response
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import redis.asyncio as aioredis
import aio_pika

from src.config import Settings, get_settings

app = FastAPI(
    title="CodeGuardian AI API",
    description="Autonomous Multi-Agent Code Review & Security Audit Pipeline",
    version="0.1.0"
)


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
"""Health tasks — prove the queue round-trips.

Epic 1 scope item: "Redis + job queue setup". These are the tasks the
acceptance evidence exercises; real pipeline stages land in Epic 2+.
"""

from __future__ import annotations

from typing import Any

from ..celery_app import celery_app


@celery_app.task(name="avp.health.ping")
def ping() -> str:
    """Simplest possible round trip: enqueue, execute, return."""
    return "pong"


@celery_app.task(name="avp.health.echo")
def echo(value: Any) -> dict[str, Any]:
    """Round-trip a JSON payload, proving serialisation is configured."""
    return {"echoed": value}


@celery_app.task(name="avp.health.check_datastores")
def check_datastores() -> dict[str, str]:
    """Verify a worker can reach Postgres and Redis.

    Workers connect to the same datastores as the API but from a different
    process and often a different host, so API readiness does not imply worker
    readiness.

    Note this runs an event loop inside a synchronous Celery task. That is not
    incidental: the project has no synchronous Postgres driver, because both
    psycopg2 and psycopg3 are LGPL-3.0 and ip-safety.md #6 puts LGPL on the
    stop-and-ask list. asyncpg (Apache-2.0) is async-only, so every worker that
    touches Postgres bridges through `asyncio.run`. The pipeline stages from
    Epic 2 onward follow the same pattern.
    """
    import asyncio

    import redis as redis_lib

    from ..config import settings

    checks: dict[str, str] = {}

    async def _check_postgres() -> str:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        url = settings.database_url
        if "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return "ok"
        finally:
            await engine.dispose()

    try:
        checks["postgres"] = asyncio.run(_check_postgres())
    except Exception as exc:  # noqa: BLE001 - report, never leak
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        client = redis_lib.Redis.from_url(settings.redis_url)
        client.ping()
        client.close()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"

    return checks

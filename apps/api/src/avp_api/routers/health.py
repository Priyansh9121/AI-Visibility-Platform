"""Liveness and readiness."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from ..deps import DbDep, RedisDep
from ..schemas.common import ApiModel

router = APIRouter(tags=["health"])


class HealthOut(ApiModel):
    status: str
    version: str


class ReadyOut(ApiModel):
    status: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthOut)
async def health() -> dict[str, Any]:
    """Liveness. Deliberately touches no dependency.

    A liveness probe that checks the database causes an orchestrator to kill
    and restart healthy API pods during a database blip, turning a partial
    outage into a total one. Readiness is what should fail then.
    """
    from .. import __version__

    return {"status": "ok", "version": __version__}


@router.get("/ready", response_model=ReadyOut)
async def ready(db: DbDep, redis: RedisDep, response: Response) -> dict[str, Any]:
    """Readiness. Verifies the datastores this service cannot serve without."""
    checks: dict[str, str] = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, never leak
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"

    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if healthy else "degraded", "checks": checks}

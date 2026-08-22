"""Redis connection management.

Redis serves two roles in this system (product-spec.md §5.1):
  * the session store (see sessions.py)
  * the Celery broker and result backend (see apps/workers)

Separate logical databases keep them apart, so that flushing a stuck job queue
during an incident does not sign every user out.
"""

from __future__ import annotations

from redis.asyncio import ConnectionPool, Redis

from .config import Settings, get_settings

_pool: ConnectionPool | None = None


def get_redis(settings: Settings | None = None) -> Redis:
    global _pool
    if _pool is None:
        s = settings or get_settings()
        _pool = ConnectionPool.from_url(s.redis_url, decode_responses=True)
    return Redis(connection_pool=_pool)


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
    _pool = None

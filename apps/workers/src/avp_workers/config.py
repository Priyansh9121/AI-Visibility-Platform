"""Worker settings.

Reads the same environment as the API (see apps/api/.env.example), so a single
`.env` configures both services in development.

**Redis database separation.** The broker, the result backend, and the API's
session store use three different logical Redis databases:

    /0  API session store
    /1  Celery broker
    /2  Celery result backend

They are separated so that flushing a wedged queue during an incident does not
sign out every logged-in user — an operation that is otherwise tempting at
exactly the wrong moment.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


def _positive_seconds(name: str, default: str) -> float:
    """A timeout of 0, a negative, nan or inf would disable the bound; refuse."""
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number greater than 0")
    return value


def _redis_db(url: str, db: int) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", "", ""))


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    redis_url: str
    database_url: str
    broker_url: str
    result_backend: str
    task_default_queue: str = "scans"
    # Same defaults and env names as the API, so one setting bounds both.
    redis_connect_timeout: float = 2.0
    redis_socket_timeout: float = 2.0

    @classmethod
    def from_env(cls) -> WorkerSettings:
        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        return cls(
            redis_url=redis_url,
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql+asyncpg://avp@127.0.0.1:5432/avp"
            ),
            broker_url=os.environ.get("CELERY_BROKER_URL", _redis_db(redis_url, 1)),
            result_backend=os.environ.get("CELERY_RESULT_BACKEND", _redis_db(redis_url, 2)),
            redis_connect_timeout=_positive_seconds("REDIS_CONNECT_TIMEOUT_SECONDS", "2"),
            redis_socket_timeout=_positive_seconds("REDIS_SOCKET_TIMEOUT_SECONDS", "2"),
        )


settings = WorkerSettings.from_env()

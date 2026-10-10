"""F4: `redis.memory` and `redis.memory_high` (`avp_api/redis_watch.py`).

Fed from a fake `INFO memory` so every size is exact: used and max, max 0 (no limit: `null`, never a
warning), a ratio above, at and below `redis_memory_warn_ratio`, a Redis error (a debug line with
the class only), and the 15-minute throttle against an injected clock. No new line carries a key,
an address, an id or error text.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from structlog.testing import capture_logs

from avp_api import redis_watch
from avp_api.config import Settings

MIB = 1024 * 1024


class FakeInfoRedis:
    def __init__(
        self, used: int, limit: int | None = 0, error: BaseException | None = None
    ) -> None:
        self.used, self.limit, self.error = used, limit, error
        self.info_calls = 0

    async def info(self, section: str) -> dict[str, Any]:
        assert section == "memory"
        self.info_calls += 1
        if self.error is not None:
            raise self.error
        reply: dict[str, Any] = {"used_memory": self.used}
        if self.limit is not None:
            reply["maxmemory"] = self.limit
        return reply

    async def set(self, key: str, value: str, *, nx: bool = False) -> object:  # noqa: ARG002
        return None


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == name]


def test_default_ratio() -> None:
    assert Settings(_env_file=None).redis_memory_warn_ratio == 0.5


async def test_used_and_max_are_logged_in_mb_with_a_ratio() -> None:
    redis = FakeInfoRedis(used=5 * MIB, limit=25 * MIB)
    with capture_logs() as logs:
        await redis_watch.log_memory(redis, warn_ratio=0.5)  # type: ignore[arg-type]
    assert _events(logs, "redis.memory") == [
        {"event": "redis.memory", "log_level": "info", "used_mb": 5.0, "max_mb": 25.0,
         "used_ratio": 0.2}
    ]
    assert _events(logs, "redis.memory_high") == []


@pytest.mark.parametrize("used_mb, warns", [(12, False), (12.5, True), (20, True)])
async def test_the_warning_is_at_or_above_the_ratio(used_mb: float, warns: bool) -> None:
    redis = FakeInfoRedis(used=int(used_mb * MIB), limit=25 * MIB)
    with capture_logs() as logs:
        await redis_watch.log_memory(redis, warn_ratio=0.5)  # type: ignore[arg-type]
    high = _events(logs, "redis.memory_high")
    assert bool(high) is warns
    assert len(_events(logs, "redis.memory")) == 1
    if warns:
        assert set(high[0]) == {"event", "log_level", "used_mb", "max_mb", "used_ratio"}
        assert high[0]["log_level"] == "warning"


async def test_the_setting_moves_the_line() -> None:
    redis = FakeInfoRedis(used=10 * MIB, limit=25 * MIB)  # 0.4
    with capture_logs() as logs:
        await redis_watch.log_memory(redis, warn_ratio=0.3)  # type: ignore[arg-type]
        await redis_watch.log_memory(redis, warn_ratio=0.9)  # type: ignore[arg-type]
    assert len(_events(logs, "redis.memory_high")) == 1


@pytest.mark.parametrize("limit", [0, None])
async def test_no_limit_is_null_and_never_warns(limit: int | None) -> None:
    redis = FakeInfoRedis(used=900 * MIB, limit=limit)
    with capture_logs() as logs:
        await redis_watch.log_memory(redis, warn_ratio=0.0001)  # type: ignore[arg-type]
    (line,) = _events(logs, "redis.memory")
    assert (line["max_mb"], line["used_ratio"], line["used_mb"]) == (None, None, 900.0)
    assert _events(logs, "redis.memory_high") == []


async def test_an_error_is_an_info_line_with_the_class_only() -> None:
    """Info, not debug: production logs at INFO, and a Redis that refuses `INFO` must show."""
    redis = FakeInfoRedis(0, error=RedisConnectionError("redis://secret-host:6379 refused"))
    with capture_logs() as logs:
        await redis_watch.log_memory(redis, warn_ratio=0.5)  # type: ignore[arg-type]
    assert [e["event"] for e in logs] == ["redis.memory.check_skipped"]
    assert logs[0]["log_level"] == "info"
    assert logs[0]["error"] == "ConnectionError"
    assert "secret-host" not in repr(logs)


async def test_a_deployed_limit_of_zero_warns_once_and_locally_never(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployed, `maxmemory` 0 means the limit is unknown and the high line could never fire."""
    monkeypatch.setattr(redis_watch, "_limit_unknown_warned", False)
    redis = FakeInfoRedis(used=5 * MIB, limit=0)
    with capture_logs() as local:
        await redis_watch.log_memory(redis, warn_ratio=0.5)  # type: ignore[arg-type]
    assert _events(local, "redis.memory_limit_unknown") == []
    with capture_logs() as deployed:
        for _ in range(3):
            await redis_watch.log_memory(redis, warn_ratio=0.5, deployed=True)  # type: ignore[arg-type]
    (warned,) = _events(deployed, "redis.memory_limit_unknown")
    assert warned["log_level"] == "warning"
    assert set(warned) == {"event", "log_level"}
    assert len(_events(deployed, "redis.memory")) == 3


async def test_a_deployed_limit_that_reads_does_not_warn_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redis_watch, "_limit_unknown_warned", False)
    redis = FakeInfoRedis(used=5 * MIB, limit=25 * MIB)
    with capture_logs() as logs:
        await redis_watch.log_memory(redis, warn_ratio=0.5, deployed=True)  # type: ignore[arg-type]
    assert _events(logs, "redis.memory_limit_unknown") == []


async def test_at_most_one_reading_every_15_minutes() -> None:
    redis = FakeInfoRedis(used=MIB, limit=25 * MIB)
    every = redis_watch.MEMORY_LOG_INTERVAL_S
    assert every == 900
    last = await redis_watch.log_memory_if_due(redis, warn_ratio=0.5, now=1000.0, last=None)  # type: ignore[arg-type]
    assert (last, redis.info_calls) == (1000.0, 1)
    for now in (1060.0, 1000 + every - 1):
        last = await redis_watch.log_memory_if_due(redis, warn_ratio=0.5, now=now, last=last)  # type: ignore[arg-type]
        assert (last, redis.info_calls) == (1000.0, 1)
    last = await redis_watch.log_memory_if_due(redis, warn_ratio=0.5, now=1000 + every, last=last)  # type: ignore[arg-type]
    assert (last, redis.info_calls) == (1000 + every, 2)


async def test_the_watcher_logs_at_start_then_only_when_the_clock_says() -> None:
    redis = FakeInfoRedis(used=20 * MIB, limit=25 * MIB)
    now = [0.0]
    task = asyncio.create_task(
        redis_watch.watch_sentinel(
            redis, interval_s=0.001, warn_ratio=0.5, clock=lambda: now[0]  # type: ignore[arg-type]
        )
    )
    with capture_logs() as logs:
        await asyncio.sleep(0.05)  # many ticks, the clock never moved
        first = redis.info_calls
        now[0] = 901.0
        await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.wait({task})
    assert first == 1
    assert redis.info_calls == 2
    assert len(_events(logs, "redis.memory")) == 2
    assert len(_events(logs, "redis.memory_high")) == 2

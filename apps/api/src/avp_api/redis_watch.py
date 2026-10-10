"""What the API says about its Redis: the eviction policy at start, and a wiped instance — K1.

One Redis instance holds the sessions, the rate-limit windows and the Google sign-in state.
Redis can lose all of it at once without the API noticing: an instance restart on a plan that
keeps nothing on disk, a manual flush, or an eviction policy that drops keys under memory
pressure. Everyone is then signed out, every window starts again and a Google sign-in in
flight fails, and nothing in the API's own log said so. Two lines say it now (runbook:
"Reading the Redis lines").

`redis.eviction_policy` — info, once, as the API starts. The instance's `maxmemory-policy`
(`noeviction` is the one that loses nothing silently). It is `unknown` whenever the answer
cannot be had: a managed Redis may refuse `CONFIG` (an error reply, or the command renamed), the
call may time out or find nobody listening, or the reply may not be a policy name. It never
raises, and it waits no longer than one Redis call's own connect and read timeouts
(`redis_connect_timeout_seconds`, `redis_socket_timeout_seconds`, 2 s each), which is all the
start-up ever waits for.

`redis.data_lost` — warning, no fields. A sentinel key (`SENTINEL_KEY`, no expiry, holding the
time it was written) is set with `SET ... NX`. The process whose SET succeeds found the key
missing, so Redis was emptied since the last check, and it logs once; every other process, and
every later check, finds the key and says nothing. That is what makes two processes (the old
and the new instance of a deploy, or two checks racing) one line, not two. It runs when the API
starts and then every `CHECK_INTERVAL_S` (60 s) in a background task for as long as the app
runs, so a Redis restart while the API stays up is noticed within a minute.

**The very first start against a new instance logs `redis.data_lost` once too**, and so does the
first start after this code reaches an existing instance: a fresh instance and a wiped one look
the same from here, and there was no earlier sentinel to tell them apart. That one line is
expected, not a loss.

**Nothing here can fail a start or a request.** Any Redis error in a check, a timeout included,
is skipped with at most a debug line (the error's class, never its text, which can carry the
host) and the next check tries again; after a dead connection it first tries once more at once
(`_set_sentinel`), because that is what a restart leaves the pool holding. While Redis is full
under `noeviction` it refuses every write, this SET too: that is such an error, so a full Redis
logs nothing and the check resumes when there is room. The task ends only when it is cancelled,
which the shutdown does before it closes the connection pool.

`redis.memory` — info, with `used_mb`, `max_mb` and `used_ratio`, from `INFO memory`: at start
and then at most every `MEMORY_LOG_INTERVAL_S` (15 minutes), not every tick. `max_mb` and
`used_ratio` are null when `maxmemory` is 0 (no limit, as locally). `redis.memory_high` — warning,
same fields, written with that line whenever `used_ratio` is at or above `redis_memory_warn_ratio`
(0.5); never when there is no limit. A Redis error reading it is a debug line, like the sentinel's.
No key, address or id is in either. One instance holds sessions, windows and sign-in state at
`noeviction`: when it is full every sign-in fails, so the runbook acts on this line.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .config import Settings
from .redis_client import get_redis

logger = structlog.get_logger(__name__)

# One key, written once and never expired: its absence is the signal. In the same database as
# the sessions (the one `REDIS_URL` names), so whatever empties them empties it. Not a key a
# request ever reads, so nothing about it is on a user's path.
SENTINEL_KEY = "avp:sentinel"
# A wipe is noticed within this long, plus one Redis call.
CHECK_INTERVAL_S = 60.0
# The watcher task's name, so a test (or a thread dump) can find it.
TASK_NAME = "redis-sentinel"
# How long `stop` waits for the cancelled task to be gone. The watcher never swallows a
# cancellation, so in practice this is a few turns of the loop; the bound is for the day something
# does, so that a deploy's shutdown cannot wait for ever on the watcher and leave the pools open.
STOP_TIMEOUT_S = 5.0
# The memory line is written at most this often (the watcher ticks every CHECK_INTERVAL_S).
MEMORY_LOG_INTERVAL_S = 15 * 60.0
_MIB = 1024 * 1024

# What the policy line says when it cannot say.
UNKNOWN = "unknown"

# Redis's policy names are lower-case words joined by hyphens: `noeviction`, `allkeys-lru`,
# `volatile-ttl`. A reply that is anything else (an error message from a proxy, an empty string)
# is not logged as a policy, so the line never carries text that is not one.
_POLICY_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")


async def read_eviction_policy(redis: Redis) -> str:
    """`CONFIG GET maxmemory-policy`, or `unknown`. Never raises."""
    try:
        reply = await redis.config_get("maxmemory-policy")
    except Exception:  # noqa: BLE001 - a refusal, a timeout, a dead connection: all "unknown"
        return UNKNOWN
    policy = reply.get("maxmemory-policy") if isinstance(reply, dict) else None
    if isinstance(policy, str) and _POLICY_NAME.fullmatch(policy):
        return policy
    return UNKNOWN


async def log_eviction_policy(redis: Redis) -> str:
    """Log `redis.eviction_policy` (info, field `policy`) and return the policy."""
    policy = await read_eviction_policy(redis)
    logger.info("redis.eviction_policy", policy=policy)
    return policy


# What a connection raises when Redis went away under it: reset, closed, or no answer in time.
_CONNECTION_FAILURES = (RedisConnectionError, RedisTimeoutError, OSError)


async def _set_sentinel(redis: Redis) -> bool:
    """`SET sentinel <now> NX`: True when this call wrote it. One more try after a dead connection.

    The pool does not retry (a repeated rate-limit script could count twice), and after a Redis
    restart it can still hold a connection that looks fine and answers nothing: the first command
    on it times out, and the failure is what clears it, so the second command opens a fresh one.
    Without this second try a restart would be noticed on the tick after the next, up to two
    minutes late. The sentinel can be set again safely: a repeat of a SET that had already
    succeeded finds the key and writes nothing, the same answer a later check would give.
    """
    now = datetime.now(UTC).isoformat()
    try:
        return bool(await redis.set(SENTINEL_KEY, now, nx=True))
    except _CONNECTION_FAILURES:
        return bool(await redis.set(SENTINEL_KEY, now, nx=True))


async def check_sentinel(redis: Redis) -> bool:
    """Look at the sentinel once. True when it was missing and this call put it back.

    The one call that wrote it logs `redis.data_lost`; see the module docstring for what that
    means and why the first start logs it too. False for "present", and also for "could not
    tell": an error of any kind is skipped, not raised and not logged above debug.
    """
    try:
        written = await _set_sentinel(redis)
    except Exception as exc:  # noqa: BLE001 - a check that fails is skipped; the next one tries
        logger.debug("redis.sentinel.check_skipped", error=type(exc).__name__)
        return False
    if not written:
        return False
    logger.warning("redis.data_lost")
    return True


_limit_unknown_warned = False


async def log_memory(redis: Redis, *, warn_ratio: float, deployed: bool = False) -> None:
    """Read `INFO memory` and log `redis.memory` (and `redis.memory_high` at or over the ratio).

    `max_mb` and `used_ratio` are None when `maxmemory` is 0: unlimited locally, so nothing to
    warn about. Deployed, a 0 means the limit could not be read (a managed Redis may report it
    so), and then `redis.memory_high` could never fire: `redis.memory_limit_unknown` says so, once
    per process. A failed read is an `info` line (the class, never its text), so a Redis that
    refuses `INFO` is visible at the production log level; the next call tries again.
    """
    global _limit_unknown_warned
    try:
        info = await redis.info("memory")
        used = int(info["used_memory"])
        limit = int(info.get("maxmemory", 0))
    except Exception as exc:  # noqa: BLE001 - a read that fails is skipped; the next one tries
        logger.info("redis.memory.check_skipped", error=type(exc).__name__)
        return
    used_mb = round(used / _MIB, 1)
    max_mb = round(limit / _MIB, 1) if limit > 0 else None
    ratio = round(used / limit, 3) if limit > 0 else None
    logger.info("redis.memory", used_mb=used_mb, max_mb=max_mb, used_ratio=ratio)
    if ratio is not None and ratio >= warn_ratio:
        logger.warning("redis.memory_high", used_mb=used_mb, max_mb=max_mb, used_ratio=ratio)
    elif ratio is None and deployed and not _limit_unknown_warned:
        _limit_unknown_warned = True
        logger.warning("redis.memory_limit_unknown")


async def log_memory_if_due(
    redis: Redis, *, warn_ratio: float, now: float, last: float | None, deployed: bool = False
) -> float | None:
    """`log_memory` when none was written yet or `MEMORY_LOG_INTERVAL_S` has passed.

    Returns the time of the latest attempt (the new `last`). An attempt that hit an error still
    counts, so a Redis that is down is not asked again every tick.
    """
    if last is not None and now - last < MEMORY_LOG_INTERVAL_S:
        return last
    await log_memory(redis, warn_ratio=warn_ratio, deployed=deployed)
    return now


async def watch_sentinel(
    redis: Redis,
    *,
    interval_s: float = CHECK_INTERVAL_S,
    warn_ratio: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    deployed: bool = False,
) -> None:
    """Check now, then every `interval_s`, until cancelled. Only cancellation ends it.

    Redis's memory is logged on the first pass and then at most every `MEMORY_LOG_INTERVAL_S`.
    """
    last: float | None = None
    while True:
        await check_sentinel(redis)
        last = await log_memory_if_due(
            redis, warn_ratio=warn_ratio, now=clock(), last=last, deployed=deployed
        )
        await asyncio.sleep(interval_s)


async def start(settings: Settings) -> asyncio.Task[None] | None:
    """Log the eviction policy, then start the sentinel watch. Never raises.

    The policy is read before this returns (bounded by Redis's own timeouts); the first sentinel
    check runs in the task, so it adds nothing to the start-up. Returns the task for `stop`, or
    None when there is no Redis client to watch with (a malformed `REDIS_URL`: every request
    will say so for itself).
    """
    try:
        redis = get_redis(settings)
    except Exception:  # noqa: BLE001 - the start-up is not where a bad URL gets reported
        logger.info("redis.eviction_policy", policy=UNKNOWN)
        return None
    await log_eviction_policy(redis)
    # The interval is read here, at call time, so a test can shorten it for the whole app.
    watcher = watch_sentinel(
        redis,
        interval_s=CHECK_INTERVAL_S,
        warn_ratio=settings.redis_memory_warn_ratio,
        deployed=settings.is_deployed,
    )
    return asyncio.create_task(watcher, name=TASK_NAME)


async def stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the watcher and wait for it to be gone, at most `STOP_TIMEOUT_S`. Safe with None.

    `asyncio.wait` and not a bare `await` under a `suppress`: it waits for the cancelled task
    without also swallowing a cancellation of the caller, and it gives up after the bound, which
    a `gather` would not. A task that is still running after that is let go of, so the shutdown
    goes on to close the pools.
    """
    if task is None:
        return
    task.cancel()
    done, _still_running = await asyncio.wait({task}, timeout=STOP_TIMEOUT_S)
    for finished in done:
        if not finished.cancelled():
            # Read, so that a failure of the loop itself is not reported a second time, by the
            # event loop, when the task is collected.
            finished.exception()

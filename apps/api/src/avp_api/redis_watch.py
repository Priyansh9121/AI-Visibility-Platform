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
"""

from __future__ import annotations

import asyncio
import re
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


async def watch_sentinel(redis: Redis, *, interval_s: float = CHECK_INTERVAL_S) -> None:
    """Check now, then every `interval_s`, until cancelled. Only cancellation ends it."""
    while True:
        await check_sentinel(redis)
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
    return asyncio.create_task(watch_sentinel(redis, interval_s=CHECK_INTERVAL_S), name=TASK_NAME)


async def stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the watcher and wait for it to be gone. Safe with None.

    `gather(..., return_exceptions=True)` and not a bare `await` under a `suppress`: it waits for
    the cancelled task without also swallowing a cancellation of the caller.
    """
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

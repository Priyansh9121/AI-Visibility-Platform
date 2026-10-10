"""Platform-wide daily spend breakers — D2 of the 2026-10-10 hardening brief.

The scan allowance (`scan_allowance`) is per agency. Nothing bounded what AVP as a whole spends
in a day, so a burst of sign-ups, or a bug, could run up provider bills with every agency
inside its own limit. This is that bound: one atomic daily counter per `kind`, in Redis.

**One claim.** `claim(redis, settings, kinds=[...])` runs ONE Lua script over the day keys
`cap:{kind}:{YYYY-MM-DD, UTC}` of every kind the request needs. It reads every count first; if
any is at or above the setting `KINDS[kind]` names it refuses and increments nothing, so a
refused request spends no unit of any kind. Otherwise it INCRs them all (48 h TTL on a first
hit). It never DECRs: a unit taken is spent even if the work then fails (giving it back would
let a failing path keep spending), exactly as `claim_detection` counts. Adding a kind is one
entry in `KINDS`.

**Fails closed.** A Redis error is a 503 (`ServiceUnavailable`): an unmetered scan is worse than
a refused one. Cap 0 refuses everything: the founder's kill switch.

**What counts.** `scan_start.start_scan` claims `scans` for every new scan and `free_scans`
too when the agency is not paid up (`billing.is_active`). The exempt operator counts toward
`scans` (the spend is real) and not toward `free_scans` (not a sign-up). Both apply whether or
not billing is configured. Running scans are never re-checked.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ..config import Settings
from ..errors import PlatformCapReached, ServiceUnavailable

logger = structlog.get_logger(__name__)

# kind -> the Settings field holding its daily limit. F3 adds prompt_runs, fix_runs and
# classifications here.
KINDS: dict[str, str] = {
    "scans": "max_scans_per_day_platform",
    "free_scans": "max_signup_free_scans_per_day",
}

# Two days: the key outlives its UTC day, and goes by itself.
_TTL_MS = 48 * 60 * 60 * 1000

# KEYS: one day key per kind claimed together. ARGV[1]: ttl ms; ARGV[2..]: each key's limit, in
# the same order. First every count is READ: if any is at or above its limit the script returns
# that key's 1-based index and increments NOTHING (a refused request spends no unit of any kind).
# Otherwise every key is INCRed, the TTL set on a key's first hit (and repaired if it ever lacks
# one), and 0 is returned.
_CLAIM = """
for i = 1, #KEYS do
  local v = redis.call('GET', KEYS[i])
  local n = 0
  if v then n = tonumber(v) end
  if n >= tonumber(ARGV[i + 1]) then return i end
end
for i = 1, #KEYS do
  local n = redis.call('INCR', KEYS[i])
  if n == 1 then redis.call('PEXPIRE', KEYS[i], ARGV[1]) end
  if redis.call('PTTL', KEYS[i]) < 0 then redis.call('PEXPIRE', KEYS[i], ARGV[1]) end
end
return 0
"""

_SENTENCES = {
    "scans": (
        "AVP has started as many scans as it allows in a day, so this one was not started. "
        "Try again after midnight UTC."
    ),
    "free_scans": (
        "AVP has started as many free scans as it allows in a day, so this one was not "
        "started. Try again after midnight UTC."
    ),
}


def day_key(kind: str, moment: datetime) -> str:
    return f"cap:{kind}:{moment.astimezone(UTC).strftime('%Y-%m-%d')}"


def seconds_to_midnight(moment: datetime) -> int:
    """Whole seconds until the next UTC midnight, rounded up, never below one."""
    utc = moment.astimezone(UTC)
    midnight = (utc + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((midnight - utc).total_seconds()))


async def _eval(redis: Redis, keys: Sequence[str], limits: Sequence[int]) -> int:
    """0 when every key was claimed, else the 1-based index of the first key at its limit."""
    args = [str(_TTL_MS), *(str(n) for n in limits)]
    pending = cast("Awaitable[int]", redis.eval(_CLAIM, len(keys), *keys, *args))
    return int(await pending)


async def claim(
    redis: Redis,
    settings: Settings,
    *,
    kinds: Sequence[str],
    agency_id: str,
    now: datetime | None = None,
) -> None:
    """Take one unit of EACH of `kinds` for today (UTC), or none and raise `PlatformCapReached`.

    All-or-nothing, in one script: a request any of its kinds refuses increments none of them.
    The refusal names the first kind in `kinds` that is full. `ServiceUnavailable` (503) when
    Redis cannot say. Logs `scan.platform_cap_reached` (warning: `cap`, `limit`, `agency_id`) on
    a refusal and nothing on success. Ids only. The limit is logged, never sent to the caller.
    """
    moment = now or datetime.now(UTC)
    limits = [int(getattr(settings, KINDS[k])) for k in kinds]
    try:
        refused = await _eval(redis, [day_key(k, moment) for k in kinds], limits)
    except (RedisError, OSError):
        logger.warning("scan.platform_cap_unavailable", cap=",".join(kinds))
        raise ServiceUnavailable(
            detail="This is temporarily unavailable. Please try again shortly."
        ) from None
    if refused == 0:
        return
    kind = kinds[refused - 1]
    logger.warning(
        "scan.platform_cap_reached", cap=kind, limit=limits[refused - 1], agency_id=agency_id
    )
    raise PlatformCapReached(
        detail=_SENTENCES[kind],
        headers={"Retry-After": str(seconds_to_midnight(moment))},
        cap=kind,
    )

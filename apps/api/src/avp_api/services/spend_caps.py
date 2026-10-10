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

**F3 (2026-10-10): the paid AI calls outside a scan.** `prompt_runs` (an ad hoc question,
`routers/prompt_runs`), `fix_runs` (a scan's fixes written again, `routers/action_items`) and
`classifications` (a site read and classified, `routers/clients`) are independent kinds with
their own caps, each claimed after its per-caller limit and before the provider. A refusal is
the same 503 `platform-cap-reached` with the kind's own sentence and the title "Daily limit
reached" (the scan kinds keep "Daily scan limit reached"), and logs `ai.platform_cap_reached`
where the scan kinds log `scan.platform_cap_reached`. `claim_classification` adds one agency's
own day in the same script. A scan's own fix phase and its classification are not counted.
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
from ..errors import PlatformCapReached, RateLimited, ServiceUnavailable
from ..models.tenancy import Agency, User
from . import billing, scan_allowance

logger = structlog.get_logger(__name__)

# kind -> the Settings field holding its daily limit. `prompt_runs`, `fix_runs` and
# `classifications` are F3's: the paid AI calls outside a scan.
KINDS: dict[str, str] = {
    "scans": "max_scans_per_day_platform",
    "free_scans": "max_signup_free_scans_per_day",
    "prompt_runs": "max_prompt_runs_per_day_platform",
    "fix_runs": "max_fix_runs_per_day_platform",
    "classifications": "max_classifications_per_day_platform",
    # The share of `classifications` that agencies not paid up (and not the exempt operator) may
    # take, so free sign-ups cannot spend every unit for every tenant. Mirrors `free_scans`.
    "free_classifications": "max_free_classifications_per_day_platform",
}

# The kinds that are not scans: they log `ai.platform_cap_reached` and title their refusal
# "Daily limit reached". The scan kinds keep `scan.platform_cap_reached` and their title.
AI_KINDS = frozenset({"prompt_runs", "fix_runs", "classifications", "free_classifications"})
AI_TITLE = "Daily limit reached"

# F3: one agency's classifications per UTC day (`classifications_per_agency_per_day`). Its key
# sits beside the platform's, `cap:classifications_agency:{agency id}:{day}`, 48 h TTL.
AGENCY_CLASSIFICATIONS = "classifications_agency"

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
    "prompt_runs": (
        "AVP has answered as many questions as it allows in a day. Try again after midnight UTC."
    ),
    "fix_runs": (
        "AVP has written as many fixes as it allows in a day. Try again after midnight UTC."
    ),
    "classifications": (
        "AVP has classified as many sites as it allows in a day. Try again after midnight UTC."
    ),
    "free_classifications": (
        "AVP has classified as many sites for free accounts as it allows in a day. "
        "Try again after midnight UTC."
    ),
}

# One agency's own share of a day, per kind: key name (`cap:{key}:{agency id}:{day}`), the
# setting holding its limit, and the 429's sentence (this caller tripped it, so not a 503).
AGENCY_CAPS: dict[str, tuple[str, str, str]] = {
    "prompt_runs": (
        "prompt_runs_agency",
        "prompt_runs_per_agency_per_day",
        "This agency has asked as many questions as it can today. Try again after midnight UTC.",
    ),
    "fix_runs": (
        "fix_runs_agency",
        "fix_runs_per_agency_per_day",
        "This agency has written fixes again as many times as it can today. "
        "Try again after midnight UTC.",
    ),
    "classifications": (
        AGENCY_CLASSIFICATIONS,
        "classifications_per_agency_per_day",
        "This agency has classified as many sites as it can today. Try again after midnight UTC.",
    ),
}
AGENCY_CLASSIFICATIONS_SENTENCE = AGENCY_CAPS["classifications"][2]

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
        ai = any(k in AI_KINDS for k in kinds)
        logger.warning(
            "ai.platform_cap_unavailable" if ai else "scan.platform_cap_unavailable",
            cap=",".join(kinds),
        )
        raise ServiceUnavailable(
            detail="This is temporarily unavailable. Please try again shortly."
        ) from None
    if refused == 0:
        return
    _raise_platform_refusal(kinds[refused - 1], limits[refused - 1], agency_id, moment)


def _raise_platform_refusal(kind: str, limit: int, agency_id: str, moment: datetime) -> None:
    """The 503 for a platform kind at its limit, and its one log line (ids and numbers only)."""
    ai = kind in AI_KINDS
    logger.warning(
        "ai.platform_cap_reached" if ai else "scan.platform_cap_reached",
        cap=kind,
        limit=limit,
        agency_id=agency_id,
    )
    raise PlatformCapReached(
        detail=_SENTENCES[kind],
        headers={"Retry-After": str(seconds_to_midnight(moment))},
        cap=kind,
        **({"title": AI_TITLE} if ai else {}),
    )


def agency_key(kind: str, agency_id: str, moment: datetime) -> str:
    return f"cap:{AGENCY_CAPS[kind][0]}:{agency_id}:{moment.astimezone(UTC):%Y-%m-%d}"


async def claim_for_agency(
    redis: Redis,
    settings: Settings,
    *,
    kind: str,
    agency_id: str,
    free: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """Take one unit of `kind` from this agency's day AND the platform's, or none.

    F3. `kind` is `prompt_runs`, `fix_runs` or `classifications`. One script over the agency's
    day key, the platform's, and (`free`, classifications only) the `free_classifications`
    share, in that order: a refusal on ANY increments none. The agency's own day refuses with a
    `RateLimited` 429 (`Retry-After` to UTC midnight, its own sentence, `ai.agency_cap_reached`);
    the platform's and the free share with the 503 `PlatformCapReached` of their kind
    (`ai.platform_cap_reached`). `ServiceUnavailable` when Redis cannot say. Returns the keys
    taken, for `give_back`. A unit is spent even if the work then fails, except the one case
    `give_back` is for.
    """
    moment = now or datetime.now(UTC)
    _name, agency_setting, agency_sentence = AGENCY_CAPS[kind]
    platform_kinds = [kind, *(["free_classifications"] if free else [])]
    agency_limit = int(getattr(settings, agency_setting))
    if free:
        # An agency the free share applies to has the smaller own day, so a few sign-ups cannot
        # take the whole share between them.
        agency_limit = min(agency_limit, int(settings.classifications_per_unpaid_agency_per_day))
    limits = [agency_limit, *(int(getattr(settings, KINDS[k])) for k in platform_kinds)]
    keys = [agency_key(kind, agency_id, moment), *(day_key(k, moment) for k in platform_kinds)]
    try:
        refused = await _eval(redis, keys, limits)
    except (RedisError, OSError):
        logger.warning("ai.platform_cap_unavailable", cap=",".join(platform_kinds))
        raise ServiceUnavailable(
            detail="This is temporarily unavailable. Please try again shortly."
        ) from None
    if refused == 0:
        return keys
    if refused == 1:
        logger.warning(
            "ai.agency_cap_reached",
            cap=AGENCY_CAPS[kind][0],
            limit=agency_limit,
            agency_id=agency_id,
        )
        raise RateLimited(
            detail=agency_sentence,
            headers={"Retry-After": str(seconds_to_midnight(moment))},
        )
    _raise_platform_refusal(
        platform_kinds[refused - 2], limits[refused - 1], agency_id, moment
    )
    raise AssertionError("unreachable")  # pragma: no cover


async def claim_classification(
    redis: Redis,
    settings: Settings,
    *,
    agency: Agency,
    user: User,
    now: datetime | None = None,
) -> list[str]:
    """`claim_for_agency` for a classification, with the free share for an unpaid agency.

    Called by `POST /clients` (with `classify`) and `POST /clients/{id}/reclassify`, after
    `one_run_per_agency` is held, the URL checks have passed, and before any crawl or model
    call. An agency that is not paid up (`billing.is_active`) and a user who is not the exempt
    operator (`scan_allowance.is_exempt`) also take a `free_classifications` unit, in the same
    script, as `scan_start` takes `free_scans`: unpaid sign-ups can spend only their share of the
    platform's day. Applies whether or not billing is configured.
    """
    free = not billing.is_active(agency) and not scan_allowance.is_exempt(settings, user)
    return await claim_for_agency(
        redis, settings, kind="classifications", agency_id=agency.id, free=free, now=now
    )


# KEYS: the keys a claim took. Gives one unit back on each, never below zero.
_GIVE_BACK = """
for i = 1, #KEYS do
  local v = tonumber(redis.call('GET', KEYS[i]))
  if v and v > 0 then redis.call('DECR', KEYS[i]) end
end
return 0
"""


async def give_back(redis: Redis, keys: Sequence[str]) -> None:
    """Return the units a claim took, in one script, floor 0. Never raises.

    The ONE case this is for: a classification whose browser was busy for its whole wait
    (`BROWSER_BUSY`). Nothing was launched, read or asked of a model, so nothing was spent, and
    the person is told to try again; keeping the units would charge them (and the platform's
    day) for a refusal that cost nothing. Every other claim stays spent when the work fails,
    because there the provider was reached or the failing path could keep spending. A Redis
    error here is logged and swallowed: the caller is already refusing.
    """
    try:
        pending = cast("Awaitable[int]", redis.eval(_GIVE_BACK, len(keys), *keys))
        await pending
    except (RedisError, OSError):
        logger.warning("ai.give_back_unavailable")

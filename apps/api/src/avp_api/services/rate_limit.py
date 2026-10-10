"""Fixed-window rate limits for the credential routes: login, sign-up and
reset-request (unauthenticated) and change-password (authenticated).

One counter per (action, kind, subject) in Redis. Keys are
`rl:{action}:{kind}:{digest}` where the digest is a truncated HMAC-SHA256 over
the normalised email or the IP bucket, keyed from `APP_SECRET`, so neither is
stored in the clear.

**IP first.** One Lua script counts the IP key and, only if that is within its
limit, the email key. A request refused on IP never writes an email key, so an
attacker cannot fill Redis with a key per made-up address.

**Login counts failures only.** The attempt is counted atomically before
Argon2; a successful login gives both counts back (`release_login`), and a
successful password reset clears that email's login key.

**Change-password is the one authenticated route here.** It verifies the
current password, so a stolen session cookie would otherwise be an unmetered
guessing oracle. It counts failures only, like login, and is keyed on the
signed-in account's email (never in the clear).

**Fails closed for login, sign-up and change-password** (503, `auth.rate_limit.unavailable`):
failing open there would switch off the guard exactly when it is needed.
Reset-request fails open: it does not use Redis and replies identically either
way.

**Detection from the button is limited here too, and is not a credential guard.**
`claim_detection` gives each client one `POST /clients/{clientId}/competitors/detect`
run per 24 hours (launch checklist 3.53). One key per client,
`rl:detect_competitors:client:{digest}`, with the client id hashed like every subject
above, taken with `SET NX PX` in one Lua step so two presses at once cannot both win.
It fails closed, because the run spends paid searches, and a scan's own detection never
passes through it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import math
import secrets
from collections.abc import Awaitable
from typing import Literal, cast

import structlog
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ..config import Settings
from ..errors import RateLimited, ServiceUnavailable
from .auth import normalise_email

logger = structlog.get_logger(__name__)

Action = Literal[
    "login", "signup", "reset", "change_password", "google_start", "shared_report", "shared_pdf"
]

UNPARSED_BUCKET = "unparsed"
MISSING_HEADER_BUCKET = "missing-header"

# KEYS: ip key, email key. ARGV: ip limit, window ms.
# INCR the IP key (TTL on first hit, repaired if it ever lacks one). Over the
# limit: return without touching the email key. Otherwise INCR the email key
# the same way. Returns {limited_by_ip, ip_count, ip_ttl_ms, email_count,
# email_ttl_ms}.
_HIT = """
local function hit(key)
  local n = redis.call('INCR', key)
  if n == 1 then redis.call('PEXPIRE', key, ARGV[2]) end
  local ttl = redis.call('PTTL', key)
  if ttl < 0 then redis.call('PEXPIRE', key, ARGV[2]) ttl = tonumber(ARGV[2]) end
  return n, ttl
end
local ip_n, ip_ttl = hit(KEYS[1])
if ip_n > tonumber(ARGV[1]) then return {1, ip_n, ip_ttl, 0, 0} end
local em_n, em_ttl = hit(KEYS[2])
return {0, ip_n, ip_ttl, em_n, em_ttl}
"""

# KEYS: the IP key. ARGV: ip limit, window ms. The same count as `_HIT`, for a route that has
# no email: one key, so a refused request writes nothing but this counter. Returns
# {limited, count, ttl_ms}.
_HIT_IP = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) ttl = tonumber(ARGV[2]) end
if n > tonumber(ARGV[1]) then return {1, n, ttl} end
return {0, n, ttl}
"""

# Give one attempt back on each key, never below zero.
_RELEASE = """
for i = 1, #KEYS do
  local v = tonumber(redis.call('GET', KEYS[i]))
  if v and v > 0 then redis.call('DECR', KEYS[i]) end
end
return 0
"""


async def _eval(
    redis: Redis, script: str, keys: list[str], args: list[str]
) -> list[int]:
    # redis-py types `eval` as sync-or-async; the asyncio client returns an
    # awaitable, and these scripts return integers or a list of integers.
    pending = cast("Awaitable[list[int]]", redis.eval(script, len(keys), *keys, *args))
    return await pending


def _key(settings: Settings, action: str, kind: str, subject: str) -> str:
    secret = settings.app_secret.get_secret_value().encode()
    derived = hmac.new(secret, b"avp-rate-limit-v1", hashlib.sha256).digest()
    digest = hmac.new(derived, subject.encode(), hashlib.sha256).digest()[:16]
    token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"rl:{action}:{kind}:{token}"


def _parse_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    value = raw.strip()
    if value.startswith("["):  # [v6] or [v6]:port
        end = value.find("]")
        if end == -1:
            return None
        rest = value[end + 1 :]
        if rest and not (rest[0] == ":" and rest[1:].isdigit()):
            return None
        value = value[1:end]
    elif value.count(":") == 1 and "." in value:  # v4:port
        host, _, port = value.partition(":")
        if not port.isdigit():
            return None
        value = host
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def client_ip(request: Request, settings: Settings) -> str:
    """The per-IP bucket subject: an IPv4 address, an IPv6 /64, or a fixed bucket.

    With `client_ip_header` set, only that header is read; if it is absent the
    request goes to a shared bucket, never to the connection's host (which
    behind a proxy is the client-controlled leftmost X-Forwarded-For entry).
    """
    header = settings.client_ip_header.strip()
    if header:
        raw = request.headers.get(header)
        if raw is None or not raw.strip():
            logger.warning("auth.rate_limit.ip_header_missing")
            return MISSING_HEADER_BUCKET
    else:
        raw = request.client.host if request.client else None
    ip = _parse_ip(raw) if raw else None
    if ip is None:
        logger.warning("auth.rate_limit.ip_unparsed")
        return UNPARSED_BUCKET
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        bare = ipaddress.IPv6Address(int(ip))  # drop any %scope
        return str(ipaddress.ip_network(f"{bare}/64", strict=False))
    return str(ip)


def _limits(settings: Settings, action: Action) -> tuple[int, int, int]:
    """(per email, per IP, window seconds)."""
    if action == "login":
        return (
            settings.login_limit_per_email,
            settings.login_limit_per_ip,
            settings.login_window_seconds,
        )
    if action == "change_password":
        return (
            settings.change_password_limit_per_email,
            settings.change_password_limit_per_ip,
            settings.change_password_window_seconds,
        )
    if action == "google_start":
        # No email exists on this route; the per-email figure is unused.
        return (
            settings.google_start_limit_per_ip,
            settings.google_start_limit_per_ip,
            settings.google_start_window_seconds,
        )
    if action == "shared_report":
        return (
            settings.shared_report_limit_per_ip,
            settings.shared_report_limit_per_ip,
            settings.shared_window_seconds,
        )
    if action == "shared_pdf":
        return (
            settings.shared_pdf_limit_per_ip,
            settings.shared_pdf_limit_per_ip,
            settings.shared_window_seconds,
        )
    if action == "signup":
        return (
            settings.signup_limit_per_email,
            settings.signup_limit_per_ip,
            settings.signup_window_seconds,
        )
    return (
        settings.reset_limit_per_email,
        settings.reset_limit_per_ip,
        settings.reset_window_seconds,
    )


def _too_many(seconds: int) -> RateLimited:
    minutes = max(1, math.ceil(seconds / 60))
    unit = "minute" if minutes == 1 else "minutes"
    return RateLimited(
        detail=f"Too many attempts. Try again in {minutes} {unit}.",
        headers={"Retry-After": str(seconds)},
    )


def _keys(
    settings: Settings, request: Request, action: Action, email: str
) -> tuple[str, str]:
    """(ip key, email key)."""
    return (
        _key(settings, action, "ip", client_ip(request, settings)),
        _key(settings, action, "email", normalise_email(email)),
    )


async def enforce(
    redis: Redis,
    settings: Settings,
    request: Request,
    *,
    action: Action,
    email: str,
) -> None:
    """Count this attempt (IP first); raise 429 if over, 503 if Redis is down.

    Reset-request alone fails open on a Redis error.
    """
    per_email, per_ip, window = _limits(settings, action)
    ip_key, email_key = _keys(settings, request, action, email)
    try:
        by_ip, _, ip_ttl, email_count, email_ttl = await _eval(
            redis, _HIT, [ip_key, email_key], [str(per_ip), str(window * 1000)]
        )
    except (RedisError, OSError):
        logger.warning("auth.rate_limit.unavailable")
        if action == "reset":
            return
        raise ServiceUnavailable(
            detail="This is temporarily unavailable. Please try again shortly."
        ) from None
    if int(by_ip):
        kind, ttl_ms = "ip", int(ip_ttl)
    elif int(email_count) > per_email:
        kind, ttl_ms = "email", int(email_ttl)
    else:
        return
    logger.warning("auth.rate_limited", action=action, kind=kind)
    raise _too_many(max(1, math.ceil(ttl_ms / 1000)))


async def hit_google_start(
    redis: Redis, settings: Settings, request: Request
) -> Literal["ok", "limited", "unavailable"]:
    """Count one `GET /auth/google/start` against its IP bucket, before anything is written.

    The route is a browser navigation, so this returns a verdict for the router to turn into
    a redirect and does not raise a problem document. One key per IP bucket and nothing else
    (no email exists here). Fails closed: `unavailable` on a Redis error, as login does.
    The one log line is the refusal's (`auth.rate_limited`, no IP).
    """
    _, per_ip, window = _limits(settings, "google_start")
    key = _key(settings, "google_start", "ip", client_ip(request, settings))
    try:
        limited, _, _ = await _eval(redis, _HIT_IP, [key], [str(per_ip), str(window * 1000)])
    except (RedisError, OSError):
        logger.warning("auth.rate_limit.unavailable")
        return "unavailable"
    if int(limited):
        logger.warning("auth.rate_limited", action="google_start", kind="ip")
        return "limited"
    return "ok"


async def enforce_shared(
    redis: Redis,
    settings: Settings,
    request: Request,
    *,
    action: Literal["shared_report", "shared_pdf"],
) -> None:
    """Count one public share-link request against its IP bucket; raise 429 when over.

    For `POST /reports/shared` and `POST /reports/shared/pdf`, which need no session and so had
    no limit. One key per IP bucket (no email exists), counted before the token is looked up, so
    a refused request costs one counter and no query. The refusal is the same rate-limit problem
    the credential routes give, with `Retry-After` the seconds left in the window.

    **On a Redis error the JSON report fails OPEN** (a warning is logged, no detail): a prospect
    opening a report has no account and no other way in, and this guards cost, not a credential,
    so availability wins, as for reset-request. **The PDF fails CLOSED** (503): it is the costly
    route, and during an outage a prospect can still read the report and only loses the download.
    """
    _, per_ip, window = _limits(settings, action)
    key = _key(settings, action, "ip", client_ip(request, settings))
    try:
        limited, _, ttl_ms = await _eval(redis, _HIT_IP, [key], [str(per_ip), str(window * 1000)])
    except (RedisError, OSError):
        logger.warning("shared_report.rate_limit.unavailable", action=action)
        if action == "shared_pdf":
            # The costly route fails closed: during an outage a prospect can still read the
            # report (below) and only loses the download.
            raise ServiceUnavailable(
                detail="This is temporarily unavailable. Please try again shortly."
            ) from None
        return
    if int(limited):
        logger.warning("auth.rate_limited", action=action, kind="ip")
        raise _too_many(_whole_seconds(int(ttl_ms)))


async def _release(
    redis: Redis, settings: Settings, request: Request, action: Action, email: str
) -> None:
    keys = _keys(settings, request, action, email)
    try:
        await _eval(redis, _RELEASE, list(keys), [])
    except (RedisError, OSError):
        logger.warning("auth.rate_limit.unavailable")


async def release_login(
    redis: Redis, settings: Settings, request: Request, *, email: str
) -> None:
    """A login succeeded: give its attempt back to both buckets (floor 0)."""
    await _release(redis, settings, request, "login", email)


async def release_change_password(
    redis: Redis, settings: Settings, request: Request, *, email: str
) -> None:
    """A password change succeeded: give its attempt back, as login does."""
    await _release(redis, settings, request, "change_password", email)


async def clear_login(redis: Redis, settings: Settings, *, email: str) -> None:
    """A password reset succeeded: forget that email's failed logins and failed
    change-password attempts (the same inbox proof unlocks both)."""
    subject = normalise_email(email)
    keys = [
        _key(settings, "login", "email", subject),
        _key(settings, "change_password", "email", subject),
    ]
    try:
        await redis.delete(*keys)
    except (RedisError, OSError):
        logger.warning("auth.rate_limit.unavailable")


# --- Competitor detection from the button: once per client per day ----------
#
# Launch checklist 3.53, the founder's decision (2026-10-03): once per client per day from
# the button, always allowed as part of a scan. A run is about six paid SerpApi searches,
# up to twelve with retries, out of a monthly quota the whole platform shares.

DETECT_ACTION = "detect_competitors"
DETECT_WINDOW_HOURS = 24
_HOUR_MS = 3_600_000
DETECT_WINDOW_MS = DETECT_WINDOW_HOURS * _HOUR_MS  # 86_400_000

# KEYS: the client's key. ARGV: window ms. Takes the day with SET NX PX, so of two presses
# at once exactly one wins. Returns {1, window ms} when this call took the day, otherwise
# {0, ms left on it}. A key that somehow has no expiry is given one rather than blocking
# the client for ever, as `_HIT` does.
_CLAIM = """
if redis.call('SET', KEYS[1], '1', 'NX', 'PX', ARGV[1]) then
  return {1, tonumber(ARGV[1])}
end
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {0, ttl}
"""


def _whole_seconds(ms: int) -> int:
    """Seconds left, rounded up and never below one: the `Retry-After` value."""
    return max(1, math.ceil(ms / 1000))


def _detect_refused(ms_left: int) -> RateLimited:
    """The 429 for a client whose day is taken, with `ms_left` still on it.

    The wait is rounded UP, so nobody is sent back before it would work: 1 h 10 min left
    reads "in about 2 hours". The last hour says "in under an hour", and an hour to the
    millisecond "in about an hour". The sentence names no key, id or code, and says nothing
    about scans: a scan does not re-detect a client that already has a set, so it is not
    a way round this.
    """
    if ms_left < _HOUR_MS:
        wait = "in under an hour"
    else:
        hours = math.ceil(ms_left / _HOUR_MS)
        wait = "in about an hour" if hours == 1 else f"in about {hours} hours"
    return RateLimited(
        detail=(
            f"Competitor detection already ran for this client in the last "
            f"{DETECT_WINDOW_HOURS} hours. It can run again {wait}."
        ),
        headers={"Retry-After": str(_whole_seconds(ms_left))},
    )


async def claim_detection(
    redis: Redis, settings: Settings, *, client_id: str, agency_id: str
) -> None:
    """Take this client's one detection run for the day, or refuse it.

    Called by `POST /clients/{clientId}/competitors/detect` after the client is found, so
    another agency's client is a 404 and cannot spend this client's day, and before any
    work. Raises `RateLimited` (429, `Retry-After` the whole seconds left) when the day is
    taken, and `ServiceUnavailable` (503) when Redis cannot say. `agency_id` is the
    caller's, and goes to the refusal's log line and nowhere else (it is not in the key):
    the limit is per client, so a burst across one agency's clients shows only there.

    **Counted when the run starts, not when it ends.** The run spends paid searches from
    its first step, so one that then fails (SerpApi down, an error writing the set) has
    still used its day: giving the day back on a failure would let a run that keeps failing
    keep spending searches.

    **Fails closed**, like login and unlike reset-request: the route spends money, so an
    unmetered run is worse than a refused one. A signed-in request already needs Redis for
    its session, so failing closed adds no outage that was not already there.

    A scan's detection (`ensure_set_for_scan`) never calls this: it is neither refused by
    the day nor counted in it. The one log line is the refusal's; an allowed run adds none.
    """
    key = _key(settings, DETECT_ACTION, "client", client_id)
    try:
        claimed, ms_left = await _eval(redis, _CLAIM, [key], [str(DETECT_WINDOW_MS)])
    except (RedisError, OSError):
        logger.warning("competitors.detect.rate_limit_unavailable")
        raise ServiceUnavailable(
            detail="This is temporarily unavailable. Please try again shortly."
        ) from None
    if int(claimed):
        return
    logger.info(
        "competitors.detect.rate_limited",
        client_id=client_id,
        agency_id=agency_id,
        retry_after_s=_whole_seconds(int(ms_left)),
    )
    raise _detect_refused(int(ms_left))


# --- Paid AI calls outside a scan: F3 ---------------------------------------
#
# Two more claims taken atomically in Redis BEFORE a provider is called, so that of N requests
# at once at most the limit reach it (the check-then-act they replace let a burst through):
# a client's ad hoc questions per hour, and a scan's fixes written again per cooldown.
# Both fail closed (the routes spend money) and neither is given back when the work fails.
# A scan's own fix phase never calls either.

PROMPT_RUN_ACTION = "prompt_run"
PROMPT_RUN_WINDOW_MS = _HOUR_MS
FIX_REGENERATE_ACTION = "fix_regenerate"

_UNAVAILABLE = "This is temporarily unavailable. Please try again shortly."


async def claim_prompt_run(
    redis: Redis, settings: Settings, *, client_id: str, agency_id: str
) -> tuple[bool, int]:
    """Count one ad hoc question against this client's hour, atomically, before it is asked.

    Returns `(True, 0)` when it is within `prompt_runs_per_client_per_hour`, otherwise
    `(False, ms left in the window)`: the caller turns that into the 429 and its sentence
    (`prompt_runs.throttle_sentence`) so the words live with the row-count check that shares
    them. One INCR on `rl:prompt_run:client:{digest}`, the window set by the first hit, so of
    any burst exactly the limit pass. `ServiceUnavailable` (503) when Redis cannot say. A
    refused attempt is counted too (the window does not slide, so the wait does not grow).
    The one log line is the refusal's: ids and the whole seconds left.
    """
    key = _key(settings, PROMPT_RUN_ACTION, "client", client_id)
    limit = int(settings.prompt_runs_per_client_per_hour)
    try:
        limited, _count, ttl = await _eval(
            redis, _HIT_IP, [key], [str(limit), str(PROMPT_RUN_WINDOW_MS)]
        )
    except (RedisError, OSError):
        logger.warning("prompt_run.rate_limit_unavailable")
        raise ServiceUnavailable(detail=_UNAVAILABLE) from None
    if not int(limited):
        return True, 0
    logger.info(
        "prompt_run.rate_limited",
        client_id=client_id,
        agency_id=agency_id,
        retry_after_s=_whole_seconds(int(ttl)),
    )
    return False, int(ttl)


def _regenerate_refused(ms_left: int) -> RateLimited:
    """The 429 for a scan whose fixes were written again inside the cooldown."""
    minutes = math.ceil(ms_left / 60_000)
    if minutes <= 1:
        wait = "in about a minute"
    elif minutes < 120:
        wait = f"in about {minutes} minutes"
    else:
        wait = f"in about {math.ceil(ms_left / _HOUR_MS)} hours"
    return RateLimited(
        detail=f"This scan's fixes were written again recently. They can be written again {wait}.",
        headers={"Retry-After": str(_whole_seconds(ms_left))},
    )


# KEYS: the scan's key. ARGV: window ms, this request's token. Like `_CLAIM`, but the value is
# the token, so the holder alone can release it (`_RELEASE_IF`). Returns {1, window ms} or
# {0, ms left}, repairing a key with no expiry.
_CLAIM_TOKEN = """
if redis.call('SET', KEYS[1], ARGV[2], 'NX', 'PX', ARGV[1]) then
  return {1, tonumber(ARGV[1])}
end
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {0, ttl}
"""

# KEYS: the key. ARGV: the token. Deletes the key only if it still holds that token.
_RELEASE_IF = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


async def claim_fix_regeneration(
    redis: Redis, settings: Settings, *, scan_id: str, agency_id: str
) -> str:
    """Take this scan's one regeneration of its fixes per cooldown, or refuse it.

    Called by `POST /scans/{scanId}/fixes` after the scan is found (another agency's scan is a
    404) and the plan allows it, before the model call. `SET NX PX` with
    `fix_regenerate_cooldown_seconds`, as `claim_detection` takes its day, so of two presses at
    once exactly one wins. Not given back. Raises `RateLimited` (429, `Retry-After` the whole
    seconds left) and `ServiceUnavailable` (503). Ids only in the log.

    Returns this request's token, the key's value: `release_fix_regeneration` frees the
    cooldown only while it still holds it, so a request cannot free one another took after its
    own expired.
    """
    key = _key(settings, FIX_REGENERATE_ACTION, "scan", scan_id)
    window_ms = int(settings.fix_regenerate_cooldown_seconds) * 1000
    token = secrets.token_urlsafe(16)
    try:
        claimed, ms_left = await _eval(redis, _CLAIM_TOKEN, [key], [str(window_ms), token])
    except (RedisError, OSError):
        logger.warning("fixes.regenerate.rate_limit_unavailable")
        raise ServiceUnavailable(detail=_UNAVAILABLE) from None
    if int(claimed):
        return token
    logger.info(
        "fixes.regenerate.rate_limited",
        scan_id=scan_id,
        agency_id=agency_id,
        retry_after_s=_whole_seconds(int(ms_left)),
    )
    raise _regenerate_refused(int(ms_left))


async def release_prompt_run(redis: Redis, settings: Settings, *, client_id: str) -> None:
    """Give this client's one hourly unit back (floor 0), because the platform's or the agency's
    day refused the same question: nothing was asked, so it is not the client's to lose. Never
    raises (the caller is already refusing)."""
    key = _key(settings, PROMPT_RUN_ACTION, "client", client_id)
    try:
        await _eval(redis, _RELEASE, [key], [])
    except (RedisError, OSError):
        logger.warning("prompt_run.release_unavailable")


async def release_fix_regeneration(
    redis: Redis, settings: Settings, *, scan_id: str, token: str
) -> None:
    """Free this scan's cooldown, because the platform's or the agency's day refused the
    regeneration before any model call: the scan's hour is not burned by a refusal. Compare and
    delete: only while the key still holds `token`, this request's own. Never raises."""
    key = _key(settings, FIX_REGENERATE_ACTION, "scan", scan_id)
    try:
        await _eval(redis, _RELEASE_IF, [key], [token])
    except (RedisError, OSError):
        logger.warning("fixes.regenerate.release_unavailable")

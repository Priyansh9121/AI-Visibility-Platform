"""Fixed-window rate limits for the unauthenticated credential routes.

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

**Fails closed for login and sign-up** (503, `auth.rate_limit.unavailable`):
failing open there would switch off the guard exactly when it is needed.
Reset-request fails open: it does not use Redis and replies identically either
way.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import math
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

Action = Literal["login", "signup", "reset"]

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


async def release_login(
    redis: Redis, settings: Settings, request: Request, *, email: str
) -> None:
    """A login succeeded: give its attempt back to both buckets (floor 0)."""
    keys = _keys(settings, request, "login", email)
    try:
        await _eval(redis, _RELEASE, list(keys), [])
    except (RedisError, OSError):
        logger.warning("auth.rate_limit.unavailable")


async def clear_login(redis: Redis, settings: Settings, *, email: str) -> None:
    """A password reset succeeded: forget that email's failed logins."""
    key = _key(settings, "login", "email", normalise_email(email))
    try:
        await redis.delete(key)
    except (RedisError, OSError):
        logger.warning("auth.rate_limit.unavailable")

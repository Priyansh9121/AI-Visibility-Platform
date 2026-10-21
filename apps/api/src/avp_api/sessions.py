"""Redis-backed session store.

**Why opaque sessions rather than JWTs.** This is seat-based software. When an
agency removes a seat, that person's access has to end *now* — not whenever
their access token happens to expire. A JWT cannot be revoked without
consulting a server-side blocklist on every request, and a blocklist consulted
on every request is a session store with extra steps. So: a session store.

Consequences that fall out of the choice, all of them wanted:
  * the cookie is `httpOnly`, so XSS cannot read it (a token in `localStorage`
    can be exfiltrated by any script that gets in)
  * revocation is a `DEL`
  * "sign out everywhere" is a set scan, which seat management needs anyway
  * no refresh-token dance, no clock-skew class of bug

Only the SHA-256 digest of a token is stored, so a dump of Redis does not hand
an attacker working cookies.

Layout:
  sess:{digest}        STRING the session record as JSON, written with SET and a TTL
  user-sessions:{uid}  SET    digests for one user, for bulk revocation

**Two lifetimes (A3, the founder's D8).** A session ends at whichever comes first of
  * its ABSOLUTE end, `session_ttl_seconds` (14 days) after sign-in, however much it is used. That
    number is also the cookie's Max-Age. The record carries the end it was minted with
    (`expires_at`), but the end that counts is the EARLIER of that and sign-in plus the CURRENT
    `session_ttl_seconds` (`_absolute_end`): lowering the setting shortens sessions that are
    already open, and raising it never lengthens one past the end it was minted with;
  * its IDLE end, `session_idle_seconds` (72 hours) after it was last used.
The Redis TTL of the `sess:` key IS the idle clock: it is the idle window when the session is
minted and again whenever use refreshes it, and it is never set past the absolute end. So the key
disappears by itself at whichever end comes first, and `get` checks both ends on the record as
well, in case a record is ever written without a TTL.

Use refreshes the idle clock through `current_principal` (`touch`), but not on every request: a
refresh is a Redis write, so it is made only when the last refresh is older than
`session_touch_interval_seconds` (1 hour), judged from the record the request has already read.
The idle clock is therefore measured from the last refresh, which trails the last use by less
than that interval: a session ends between 71 and 72 hours after it was last used, not at an
exact 72. The refresh is one `SET ... XX`, so it can never bring back a session that a revoke
removed between the read and the write.

The user index must outlive every session in it, or "sign out everywhere" misses a session that is
still alive. A session can live as long as its absolute end however short its key's TTL is at any
moment, so the index is given the ABSOLUTE lifetime each time a session is added (`create`), never
the idle window. A refresh does not touch it. No session in the index signed in after the latest
sign-in, and none has an effective absolute end later than its own sign-in plus the current
lifetime, so an index that ends at the latest sign-in plus the lifetime in force at that sign-in
ends no earlier than any of them while `session_ttl_seconds` stays put or goes down (the end that
counts follows the setting down as the index does). One sequence still breaks it: lower the
setting, let the user sign in again (which cuts the index to the lower value), then raise it, and a
session minted before the lowering gets its minted end back and can outlive the index (seat removal
and suspension still end it, through the database). A3 part 2 makes `create` never shorten the
index.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings, get_settings
from .security import new_session_token, token_digest

logger = structlog.get_logger(__name__)

SESSION_PREFIX = "sess:"
USER_SESSIONS_PREFIX = "user-sessions:"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """What `sess:{digest}` holds.

    `expires_at` is the absolute end the session was MINTED with: sign-in plus the absolute lifetime
    of that day, fixed then and never moved by a refresh. The end that counts is the earlier of it
    and sign-in plus the current lifetime (`SessionStore._absolute_end`). `last_used_at` is when
    the idle clock was last refreshed (at sign-in, then by `SessionStore.touch`). It is None for a
    record written before idle windows existed, which production holds at the deploy: such a record
    is NOT idle since sign-in, its idle clock starts at its first request after the deploy (`touch`
    writes the field then). The field has no default on purpose: `from_json` is the only way to
    build a record with no idle end, and a constructor that forgets it is a `TypeError`.
    """

    user_id: str
    agency_id: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None

    def to_json(self) -> str:
        data = {
            "user_id": self.user_id,
            "agency_id": self.agency_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
        # Left out when unknown, so a record from before idle windows round-trips as it was, and
        # an older reader (a rolling deploy, a rollback) finds the four fields it knows.
        if self.last_used_at is not None:
            data["last_used_at"] = self.last_used_at.isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> SessionRecord:
        data = json.loads(raw)
        used = data.get("last_used_at")
        return cls(
            user_id=data["user_id"],
            agency_id=data["agency_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            last_used_at=datetime.fromisoformat(used) if used else None,
        )


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self._redis = redis
        self._settings = settings or get_settings()

    def _idle_seconds(self) -> int:
        """The idle window: `session_idle_seconds`, cut to the absolute lifetime if it is longer.

        A pair, not a range error: the absolute end comes first, so an idle window past it can
        never be the limit, and a start that refused would block a deploy over it.
        """
        return min(self._settings.session_idle_seconds, self._settings.session_ttl_seconds)

    def _refresh_after_seconds(self) -> float:
        """How old `last_used_at` may get before a request refreshes it.

        `session_touch_interval_seconds`, cut to half the idle window. Without the cut, an
        operator who shortened only the idle window below the interval would sign out people who
        are using the product: their key would run out before the next refresh was due.
        """
        return min(self._settings.session_touch_interval_seconds, self._idle_seconds() / 2)

    def _absolute_end(self, record: SessionRecord) -> datetime:
        """The session's effective absolute end: the earlier of the end it was minted with
        (`expires_at`) and sign-in plus the CURRENT `session_ttl_seconds`.

        Both halves matter. Lowering the setting has to apply to sessions already open: otherwise
        one that is used often enough outlives the new limit, and the user index, which each
        sign-in sets to the new, shorter lifetime, can end before it and leave "sign out
        everywhere" blind to it. Raising the setting must not lengthen a session past the end it
        was minted with, or it would live longer than it was ever promised. A lifetime too long to
        add to a date (a slip of units) is no limit at all: the end it was minted with governs, and
        a request is not turned into an error by a setting that only sign-in used to read.
        """
        try:
            lowered = record.created_at + timedelta(seconds=self._settings.session_ttl_seconds)
        except OverflowError:
            return record.expires_at
        return min(record.expires_at, lowered)

    async def create(self, *, user_id: str, agency_id: str) -> tuple[str, SessionRecord]:
        """Mint a session. Returns (raw token for the cookie, record)."""
        token = new_session_token()
        digest = token_digest(token)
        absolute = self._settings.session_ttl_seconds
        now = datetime.now(UTC)
        record = SessionRecord(
            user_id=user_id,
            agency_id=agency_id,
            created_at=now,
            expires_at=now + timedelta(seconds=absolute),
            last_used_at=now,
        )

        pipe = self._redis.pipeline()
        # Signing in is a use: the key lives for the idle window from now.
        pipe.set(f"{SESSION_PREFIX}{digest}", record.to_json(), ex=self._idle_seconds())
        pipe.sadd(f"{USER_SESSIONS_PREFIX}{user_id}", digest)
        # The index must not outlive the sessions it indexes, or it grows without bound as users
        # log in and out; and it must not end before the longest-living of them, which is until
        # its absolute end however short its key's TTL is now (see the module docstring).
        pipe.expire(f"{USER_SESSIONS_PREFIX}{user_id}", absolute)
        await pipe.execute()

        return token, record

    async def get(self, token: str) -> SessionRecord | None:
        raw = await self._redis.get(f"{SESSION_PREFIX}{token_digest(token)}")
        if raw is None:
            return None
        record = SessionRecord.from_json(raw)
        now = datetime.now(UTC)
        # Redis TTL is the real expiry; these are defence-in-depth checks in case a record is ever
        # written without one: the absolute end (the effective one, `_absolute_end`: a lowered
        # setting ends a session that was minted under a longer one), and the idle end. A record
        # with no `last_used_at` has no idle end to check: it is treated as used now (see
        # `SessionRecord`).
        used = record.last_used_at
        idle_over = used is not None and used + timedelta(seconds=self._idle_seconds()) <= now
        if self._absolute_end(record) <= now or idle_over:
            await self.revoke(token)
            return None
        return record

    async def revoke(self, token: str) -> None:
        digest = token_digest(token)
        raw = await self._redis.get(f"{SESSION_PREFIX}{digest}")
        pipe = self._redis.pipeline()
        pipe.delete(f"{SESSION_PREFIX}{digest}")
        if raw is not None:
            pipe.srem(f"{USER_SESSIONS_PREFIX}{SessionRecord.from_json(raw).user_id}", digest)
        await pipe.execute()

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Kill every session for a user.

        Called when a seat is removed, a user is suspended, or a password
        changes. This is the operation that makes seat-based billing
        enforceable rather than advisory.
        """
        key = f"{USER_SESSIONS_PREFIX}{user_id}"
        digests = await self._redis.smembers(key)
        if not digests:
            return 0
        pipe = self._redis.pipeline()
        for digest in digests:
            pipe.delete(f"{SESSION_PREFIX}{digest}")
        # Remove only the digests read above, never the whole index: a session
        # indexed after the SMEMBERS must stay reachable for the next revoke.
        pipe.srem(key, *digests)
        await pipe.execute()
        return len(digests)

    async def touch(self, token: str, record: SessionRecord) -> bool:
        """Refresh the idle clock of a session just read, if a refresh is due. True if it wrote.

        `record` is the one `get` returned for this request, so deciding that no refresh is due
        costs no Redis call. A refresh is due when the record has no `last_used_at` (it was written
        before idle windows existed) or the last refresh is `_refresh_after_seconds` old.

        The key then lives for the idle window from now, **but never past the absolute end** (the
        effective one, `_absolute_end`, so a lowered `session_ttl_seconds` caps it too): a session
        in constant use still ends 14 days after sign-in. Nothing else about the record changes,
        so the end it was minted with cannot be moved by use.

        **A revoke that lands between the read and this write wins.** The write is `SET ... XX`,
        one atomic command that does nothing when the key is gone, so a session that was signed
        out, removed or revoked in between is not brought back (a read-then-`SET` would). The user
        index is not touched: see the module docstring.

        Best effort. A refresh only ever keeps a session alive, so one that fails (Redis full under
        `noeviction`, a refused or dropped connection after the read answered) must not turn a
        request that was already let in into an error: it is logged, as a fixed event naming the
        error class and nothing about the session, and the next request tries again.
        """
        now = datetime.now(UTC)
        used = record.last_used_at
        if used is not None and (now - used).total_seconds() < self._refresh_after_seconds():
            return False
        left_ms = int((self._absolute_end(record) - now).total_seconds() * 1000)
        window_ms = min(self._idle_seconds() * 1000, left_ms)
        if window_ms <= 0:
            return False  # at its absolute end: the next `get` revokes it
        refreshed = replace(record, last_used_at=now)
        try:
            written = await self._redis.set(
                f"{SESSION_PREFIX}{token_digest(token)}",
                refreshed.to_json(),
                px=window_ms,
                xx=True,
            )
        except (RedisError, OSError) as exc:
            logger.warning("session.touch_failed", error=type(exc).__name__)
            return False
        return bool(written)

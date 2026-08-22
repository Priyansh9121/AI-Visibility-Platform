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
  sess:{digest}        HASH   the session record, TTL'd
  user-sessions:{uid}  SET    digests for one user, for bulk revocation
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from .config import Settings, get_settings
from .security import new_session_token, token_digest

SESSION_PREFIX = "sess:"
USER_SESSIONS_PREFIX = "user-sessions:"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    user_id: str
    agency_id: str
    created_at: datetime
    expires_at: datetime

    def to_json(self) -> str:
        return json.dumps(
            {
                "user_id": self.user_id,
                "agency_id": self.agency_id,
                "created_at": self.created_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> SessionRecord:
        data = json.loads(raw)
        return cls(
            user_id=data["user_id"],
            agency_id=data["agency_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self._redis = redis
        self._settings = settings or get_settings()

    async def create(self, *, user_id: str, agency_id: str) -> tuple[str, SessionRecord]:
        """Mint a session. Returns (raw token for the cookie, record)."""
        token = new_session_token()
        digest = token_digest(token)
        ttl = self._settings.session_ttl_seconds
        now = datetime.now(UTC)
        record = SessionRecord(
            user_id=user_id,
            agency_id=agency_id,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )

        pipe = self._redis.pipeline()
        pipe.set(f"{SESSION_PREFIX}{digest}", record.to_json(), ex=ttl)
        pipe.sadd(f"{USER_SESSIONS_PREFIX}{user_id}", digest)
        # The index must not outlive the sessions it indexes, or it grows
        # without bound as users log in and out.
        pipe.expire(f"{USER_SESSIONS_PREFIX}{user_id}", ttl)
        await pipe.execute()

        return token, record

    async def get(self, token: str) -> SessionRecord | None:
        raw = await self._redis.get(f"{SESSION_PREFIX}{token_digest(token)}")
        if raw is None:
            return None
        record = SessionRecord.from_json(raw)
        # Redis TTL is the real expiry; this is a defence-in-depth check in
        # case a record is ever written without one.
        if record.expires_at <= datetime.now(UTC):
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
        pipe.delete(key)
        await pipe.execute()
        return len(digests)

    async def touch(self, token: str) -> None:
        """Slide the expiry window on activity."""
        digest = token_digest(token)
        raw = await self._redis.get(f"{SESSION_PREFIX}{digest}")
        if raw is None:
            return
        ttl = self._settings.session_ttl_seconds
        record = SessionRecord.from_json(raw)
        refreshed = SessionRecord(
            user_id=record.user_id,
            agency_id=record.agency_id,
            created_at=record.created_at,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )
        pipe = self._redis.pipeline()
        pipe.set(f"{SESSION_PREFIX}{digest}", refreshed.to_json(), ex=ttl)
        pipe.expire(f"{USER_SESSIONS_PREFIX}{record.user_id}", ttl)
        await pipe.execute()

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
  user-sessions:{uid}  SET    digests for one user, for bulk revocation and for the cap on sessions

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
the idle window. A refresh does not touch it. And `create` only ever RAISES the index's TTL: it sets
it when the index has none, or has less time left than the new session's lifetime, and leaves it
alone when it has more (A3 part 2, in the same Lua step that adds the session; not `EXPIRE ... GT`,
which needs Redis 7). So the index ends no earlier than the latest of sign-in plus the lifetime in
force at that sign-in, over every session in it, and that is no earlier than any session's own
absolute end (`expires_at`, fixed when it is minted), however `session_ttl_seconds` is changed in
between: lower it, let the user sign in, raise it again, and a session minted before the lowering
still has its index. (Before A3 part 2 every sign-in SET the index to the lifetime then in force, so
that sequence cut the index short and left such a session outside it.)

**At most `session_max_per_user` sessions per user (A3 part 2, the founder's 3.121).** A sign-in
that would make it one more ends the user's OLDEST session by `created_at` (sign-in time, not the
longest unused), and the new session is never the one ended. `create` does all of it in ONE Lua
script, so that the add, the count, the cut and the index's TTL are a single step that two sign-ins
at once cannot interleave. The script drops from the index every digest whose `sess:` key is already
gone (an idle-ended session leaves its digest behind; it does not count), counts the rest, and if
that count with the new session is over the cap it deletes the oldest keys and their digests, the
same effect as `revoke`. What counts:
  * a key that still exists counts, even one past its EFFECTIVE absolute end (`_absolute_end`, after
    `session_ttl_seconds` was lowered): the script does not judge ends, the key's own TTL does, and
    at most one idle window later it is gone. After a LOWERED setting such a session is older than
    every session still inside the new lifetime, so the cut reaches it first;
  * a record the script cannot read (not JSON, or no `created_at` it can parse) counts too and is
    cut FIRST, before any readable one, so a damaged record can neither turn a sign-in into an error
    nor cost a real session its place.
"Oldest" compares the instants `created_at` names (parsed in the script, offset included), never the
text, and ties go to the smaller digest so the choice is the same every time. The script reads the
`sess:` keys that the index's members name, which are not in `KEYS`: that is fine on one Redis
instance (Render Key Value is one, not a cluster) and would need rethinking in a cluster. The index
stays a SET, as the live indexes in production are. A sign-in reads one key for each entry in the
user's index: the live ones are bounded by the cap (so by the top of its range), and the entries
of sessions that have ended are dropped by that same sign-in.

**`created_at` has two readers.** `SessionRecord.from_json` reads it in Python and `created()` in
`_CREATE` reads it in Lua. A change to its format changes both in the same commit, and the script
must go on reading the old format too (every session open at that deploy has it). Otherwise, during
a rolling deploy, an instance still running the older script reads every new-format record as
unreadable, and unreadable records are the first it cuts.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings, get_settings
from .security import new_session_token, token_digest

logger = structlog.get_logger(__name__)

SESSION_PREFIX = "sess:"
USER_SESSIONS_PREFIX = "user-sessions:"

# Mint a session, keep the user's index, and hold the user to the cap, as ONE step (A3 part 2).
# KEYS: the new session's key, the user's index. ARGV: the new record as JSON, the new key's TTL in
# ms (the idle window), the new digest, the index's TTL in ms (the absolute lifetime), the cap, and
# the prefix of the session keys. Returns how many sessions the cap ended.
#
# It READS first and writes after, so a script that fails while reading (the index is not a set)
# has changed nothing: SMEMBERS of the index, then a GET for each member, through `pcall` (a key
# of the wrong type is an error reply, not an abort) and `cjson.decode` through `pcall` (junk is
# not an abort). A member whose key is gone is `gone`: dropped from the index, not counted. Every
# other member is counted, readable or not (`sec` is nil when the record has no `created_at` the
# script can parse: not JSON, not an object, no such field, not an ISO 8601 date and time with an
# offset). The new digest is skipped if it is somehow in the set already, because its key does not
# exist yet and it would be read as `gone` and removed after it was added.
#
# `older` is the order the cap cuts in: an unreadable record first, then the earlier `created_at`
# INSTANT (seconds and microseconds since the epoch, the offset applied; `created_at` is written by
# `datetime.isoformat()`, which leaves the fraction out when the microseconds are 0 and could
# carry another offset), then the smaller digest, so the choice never depends on set order. The new
# session is not in `others`, so it is never cut, whatever the cap is (it is held to at least 1).
#
# Writes: SET the key (a sign-in is a use: the key lives the idle window), SADD the digest, and
# give the index the absolute lifetime ONLY when it has none or has less left than that (N1: a
# sign-in never shortens the index; no `EXPIRE ... GT`, which needs Redis 7). Then SREM the gone
# digests, and DEL and SREM the cut ones, in chunks because `unpack` cannot pass thousands of
# arguments (an old index can hold that many digests of ended sessions).
#
# It reads keys named by the set's members, which are not in KEYS: fine on one Redis instance
# (Render Key Value is one), not in a cluster. Run with `eval`, never retried (a retried EVAL could
# apply twice; `redis_client.py` retries nothing).
_CREATE = """
local cap = math.max(1, tonumber(ARGV[5]))

local function days_from_civil(y, m, d)
  if m <= 2 then y = y - 1 end
  local era = math.floor(y / 400)
  local yoe = y - era * 400
  local doy = math.floor((153 * ((m + 9) % 12) + 2) / 5) + d - 1
  return era * 146097 + yoe * 365 + math.floor(yoe / 4) - math.floor(yoe / 100) + doy - 719468
end

local function created(raw)
  if type(raw) ~= 'string' then return nil end
  local decoded, rec = pcall(cjson.decode, raw)
  if not decoded or type(rec) ~= 'table' then return nil end
  local at = rec['created_at']
  if type(at) ~= 'string' then return nil end
  local y, mo, d, h, mi, s, rest =
    string.match(at, '^(%d%d%d%d)-(%d%d)-(%d%d)[Tt ](%d%d):(%d%d):(%d%d)(.*)$')
  if not y then return nil end
  y, mo, d = tonumber(y), tonumber(mo), tonumber(d)
  h, mi, s = tonumber(h), tonumber(mi), tonumber(s)
  if mo < 1 or mo > 12 or d < 1 or d > 31 or h > 23 or mi > 59 or s > 59 then return nil end
  local us = 0
  local frac = string.match(rest, '^%.(%d+)')
  if frac then
    us = tonumber(string.sub(frac .. '000000', 1, 6))
    rest = string.sub(rest, #frac + 2)
  end
  local off = 0
  if rest ~= 'Z' and rest ~= 'z' then
    local sign, oh, om = string.match(rest, '^([+-])(%d%d):(%d%d)$')
    if not sign then return nil end
    off = (tonumber(oh) * 3600 + tonumber(om) * 60) * (sign == '-' and -1 or 1)
  end
  return days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s - off, us
end

local function older(a, b)
  if (a.sec == nil) ~= (b.sec == nil) then return a.sec == nil end
  if a.sec == nil then return a.digest < b.digest end
  if a.sec ~= b.sec then return a.sec < b.sec end
  if a.us ~= b.us then return a.us < b.us end
  return a.digest < b.digest
end

local others, gone, cut = {}, {}, {}
for _, digest in ipairs(redis.call('SMEMBERS', KEYS[2])) do
  if digest ~= ARGV[3] then
    local raw = redis.pcall('GET', ARGV[6] .. digest)
    if raw == false then
      gone[#gone + 1] = digest
    else
      local sec, us = created(raw)
      others[#others + 1] = { digest = digest, sec = sec, us = us }
    end
  end
end
local over = #others + 1 - cap
if over > 0 then
  table.sort(others, older)
  for i = 1, over do cut[i] = others[i].digest end
end

redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
redis.call('SADD', KEYS[2], ARGV[3])
local left = redis.call('PTTL', KEYS[2])
if left < 0 or left < tonumber(ARGV[4]) then redis.call('PEXPIRE', KEYS[2], ARGV[4]) end

local function srem(list)
  for i = 1, #list, 200 do
    redis.call('SREM', KEYS[2], unpack(list, i, math.min(i + 199, #list)))
  end
end
srem(gone)
for _, digest in ipairs(cut) do redis.call('DEL', ARGV[6] .. digest) end
srem(cut)
return #cut
"""


def _user_id_of(raw: str) -> str | None:
    """The user a stored record names, or None when the record cannot be read.

    Reads one field and nothing else, so a record that `SessionRecord.from_json` would refuse for
    another field (a date it cannot parse) still lets `revoke` take its digest out of the index.
    """
    try:
        user_id = json.loads(raw)["user_id"]
    except (ValueError, KeyError, TypeError):
        return None
    return user_id if isinstance(user_id, str) else None


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
        one that is used often enough outlives the new limit that the operator set to end it. (It
        used to matter for the user index as well, which each sign-in then cut to the new, shorter
        lifetime; a sign-in now only ever raises the index.) Raising the setting must not lengthen
        a session past the end it was minted with, or it would live longer than it was ever
        promised. A lifetime too long to add to a date (a slip of units) is no limit at all: the
        end it was minted with governs, and a request is not turned into an error by a setting that
        only sign-in used to read.
        """
        try:
            lowered = record.created_at + timedelta(seconds=self._settings.session_ttl_seconds)
        except OverflowError:
            return record.expires_at
        return min(record.expires_at, lowered)

    async def create(self, *, user_id: str, agency_id: str) -> tuple[str, SessionRecord]:
        """Mint a session. Returns (raw token for the cookie, record).

        One Lua script (`_CREATE`) does everything, atomically: it writes the key and adds the
        digest to the user's index; it makes sure the index outlives this session without ever
        SHORTENING it (A3 part 2, N1: after the setting is lowered and raised again, a session
        minted earlier must not outlive its index); and it holds the user to
        `session_max_per_user` by ending their oldest sessions, never this one (the founder's
        3.121). The module docstring says what counts and in what order they go.

        The script is not retried: a retried `EVAL` could apply twice.
        """
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

        # Signing in is a use: the key lives for the idle window from now. The index must not end
        # before the longest-living session in it, which can live to its absolute end however short
        # its key's TTL is now, so it is given the absolute lifetime; but only when that is later
        # than where it already ends, so a sign-in never shortens it. Ending no later than its
        # latest session's lifetime is also what stops it growing without bound as people sign in
        # and out (see the module docstring).
        ended = await cast(
            "Awaitable[int]",
            self._redis.eval(
                _CREATE,
                2,
                f"{SESSION_PREFIX}{digest}",
                f"{USER_SESSIONS_PREFIX}{user_id}",
                record.to_json(),
                str(self._idle_seconds() * 1000),
                digest,
                str(absolute * 1000),
                str(self._settings.session_max_per_user),
                SESSION_PREFIX,
            ),
        )
        if ended:
            # Ids and a count only: never a digest or a token, and no address of the person.
            logger.info("session.cap_revoked", user_id=user_id, revoked=ended)

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
        """End one session: delete its record and take its digest out of its user's index.

        A record that cannot be read (not JSON, not an object, no `user_id`: a hand edit, or a
        format a future version writes and this instance cannot decode during a rolling deploy) is
        still deleted; there is just no index to leave, because the record is the only thing that
        names the user. The entry it leaves in some index is dropped by that user's next sign-in
        (`_CREATE` prunes digests whose key is gone). Nothing is logged: no token, no digest.
        A sign-in that ends the session its browser carries (rotation) must not turn into an
        error because that session's record is damaged (A3 part 3, the review's I2).
        """
        digest = token_digest(token)
        raw = await self._redis.get(f"{SESSION_PREFIX}{digest}")
        pipe = self._redis.pipeline()
        pipe.delete(f"{SESSION_PREFIX}{digest}")
        user_id = _user_id_of(raw) if raw is not None else None
        if user_id is not None:
            pipe.srem(f"{USER_SESSIONS_PREFIX}{user_id}", digest)
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

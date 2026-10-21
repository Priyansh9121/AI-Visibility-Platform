"""At most `session_max_per_user` sessions per user, and a sign-in never shortens the user's index —
A3 part 2 (the founder's 3.121, and N1 from part 1's security confirmation).

Until now a user could hold any number of sessions: every sign-in added one and nothing but its own
expiry took one away. Now `SessionStore.create` is ONE Lua script that, in a single step, writes the
new session and its index entry, drops the index entries of sessions that have already ended (they
do not count), and, when the user would hold more than the cap, ends their OLDEST sessions by
sign-in time until the cap is met, never the one just made. The default cap is 10, so the 11th
sign-in signs the person out of the place they signed in to longest ago.

The same step also settles N1. The user index must outlive every session in it, or "sign out
everywhere" misses one. Every sign-in used to SET the index to the lifetime in force then, so
lowering `SESSION_TTL_SECONDS`, signing in, and raising it again cut the index short of a session
minted before the lowering. Now a sign-in only ever RAISES the index's TTL.

Time is controlled through the records and the keys' TTLs, and nothing sleeps: `_plant` writes a
session as the store keeps one, with the sign-in time and the TTL a test wants. Most tests that need
exact ages call the store directly; the headline and one of the two races go through the real
routes.

The two races (five calls at the store, two real logins) do not lean on `asyncio.gather` alone,
which proves little: redis-py's pool hands out connections under a lock that it holds while a new
one connects, and on a route Argon2 runs in threads, so the calls can run one after another and a
script split into a read and a write would still pass. `_MeetAtRedis` warms the pool with one
connection per call and holds the FIRST Redis command each `create` sends, after it is sent and
before its answer is read, at an `asyncio.Barrier`, so every call is on the wire before any is
answered. A rewrite that read in one call and wrote in the next then fails them (control
`split-script`).

What this file holds, group by group:

1. **The setting.** The founder's ten, the range, a refused start naming the variable and not the
   value, and zero refused rather than read as "no limit".
2. **The cap through the routes.** Eleven sign-ins leave ten sessions and the first cookie is the
   usual 401; a sign-in under the cap ends nothing; two sign-ins at once at the cap leave ten.
3. **Who goes.** The oldest by sign-in time (not the longest unused, not the smaller digest, not
   by the text of the date: a date with no fraction, and one written with another offset, order by
   the instant they name); ties go to the smaller digest; the new session is never the one ended;
   another user's sessions are never touched; a key past its effective absolute end still counts.
4. **What counts.** An index entry whose session has ended does not, and is dropped. A record the
   script cannot read does, and is the first to go; it never turns a sign-in into an error.
5. **The setting changes the cap.** A lower figure cuts a user who is over it at their next
   sign-in; a higher one lets more live; one means only the newest.
6. **N1: the index is never shortened.** Lower, sign in, raise: the first session still has its
   index. An index with more time left is left alone, with less is raised, with none is given one.
7. **At the deploy.** An index that already holds more than the cap (it can today) is cut at that
   user's next sign-in; the index stays a SET; thousands of ended entries are dropped in one
   sign-in; a script that fails while reading has written nothing.
8. **The log line and the shape.** One event with the user id and the count and nothing else, none
   when nothing is cut; `create` keeps its signature and result; losing Redis at the mint is still
   a 503; nothing but the store writes a session key.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import structlog
from httpx import AsyncClient, Response
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

import avp_api
from avp_api import redis_client as redis_module
from avp_api import sessions
from avp_api.config import SESSION_MAX_PER_USER_RANGE, Settings
from avp_api.security import token_digest
from avp_api.sessions import SESSION_PREFIX, USER_SESSIONS_PREFIX, SessionRecord, SessionStore

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
EMAIL = "cap@session.example"

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
ABSOLUTE = 14 * DAY
ABSOLUTE_MS = int(ABSOLUTE.total_seconds() * 1000)
IDLE_MS = 72 * 3600 * 1000
# How far under its full window a key just written may already be: a test takes seconds.
FRESH_MS = 30_000


# --- helpers --------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def redis(client: AsyncClient, settings: Settings) -> Redis:
    """The suite's test Redis, emptied first: these tests expect exactly the keys they make."""
    # `client` is asked for so the app is wired to this Redis, and its teardown empties it again.
    r = redis_module.get_redis(settings)
    await r.flushdb()
    return r


@pytest.fixture
def store(redis: Redis, settings: Settings) -> SessionStore:
    return SessionStore(redis, settings)


async def _sign_up(client: AsyncClient, email: str = EMAIL) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Cap Agency", "fullName": "Operator",
              "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _cookie_of(resp: Response, settings: Settings) -> str:
    value = resp.cookies.get(settings.session_cookie_key)
    assert value, resp.headers
    return value


def _token(client: AsyncClient, settings: Settings) -> str:
    token = client.cookies.get(settings.session_cookie_key)
    assert token
    return token


async def _login(client: AsyncClient, settings: Settings, email: str = EMAIL) -> str:
    """Sign in once more, as a browser that has no cookie yet does. Returns the new cookie."""
    client.cookies.clear()
    resp = await client.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return _cookie_of(resp, settings)


async def _me_as(client: AsyncClient, settings: Settings, token: str) -> Response:
    """`GET /auth/me` carrying only that cookie."""
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_key, token)
    return await client.get(f"{BASE}/auth/me")


async def _statuses(client: AsyncClient, settings: Settings, tokens: list[str]) -> list[int]:
    return [(await _me_as(client, settings, token)).status_code for token in tokens]


def _digest(n: int) -> str:
    """A fixed 64-character hex digest, in the order of `n` (so a test controls digest order)."""
    return f"{n:02x}" * 32


def _index(user_id: str) -> str:
    return f"{USER_SESSIONS_PREFIX}{user_id}"


async def _plant(
    redis: Redis,
    user_id: str,
    digest: str,
    created: datetime,
    *,
    used: datetime | None = None,
    left: timedelta = HOUR,
    raw: str | None = None,
) -> None:
    """Put a session in Redis as the store keeps one: a record under its digest with a TTL, and
    the digest in the user's index. `raw` replaces the record's text, to plant one the store could
    never have written."""
    record = SessionRecord(
        user_id=user_id,
        agency_id="agy_cap",
        created_at=created,
        expires_at=created + ABSOLUTE,
        last_used_at=created if used is None else used,
    )
    await redis.set(
        f"{SESSION_PREFIX}{digest}",
        record.to_json() if raw is None else raw,
        px=int(left.total_seconds() * 1000),
    )
    await redis.sadd(_index(user_id), digest)


def _record_with_created_at(user_id: str, created_at: str, shape: str = "old") -> str:
    """A record's text with the given `created_at` word for word. "old" is the shape production
    holds now (four fields, no `last_used_at`)."""
    data: dict[str, Any] = {
        "user_id": user_id,
        "agency_id": "agy_cap",
        "created_at": created_at,
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    if shape == "new":
        data["last_used_at"] = created_at
    return json.dumps(data)


async def _alive(redis: Redis, digests: list[str] | set[str]) -> set[str]:
    return {d for d in digests if await redis.exists(f"{SESSION_PREFIX}{d}")}


async def _session_keys(redis: Redis) -> list[str]:
    return sorted([key async for key in redis.scan_iter(f"{SESSION_PREFIX}*")])


async def _create(store: SessionStore, user_id: str) -> str:
    """Mint one session for the user. Returns the token's digest."""
    token, _record = await store.create(user_id=user_id, agency_id="agy_cap")
    return token_digest(token)


def _cap_events(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == "session.cap_revoked"]


# Set while one `SessionStore.create` call is made to meet the others at Redis (`_MeetAtRedis`).
_HOLD_FIRST_COMMAND: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "hold_the_first_command_of_create", default=False
)


class _MeetAtRedis:
    """Make N calls of `SessionStore.create` truly concurrent at Redis, and say so.

    * `warm()` opens one pooled connection per call and gives them back, so no call has to connect
      (the pool connects under a lock that would otherwise serialise them).
    * The FIRST command each `create` sends (the script, or the read of a rewritten one, whatever
      its shape) is held after it has been sent and before its answer is read, at an
      `asyncio.Barrier` for all N. So every call is on the wire before any is answered, and a
      rewrite that reads in one call and writes in the next gives every call the same snapshot.
    * Each wait is bounded, so a call that never arrives fails the test and does not hang it.

    `answered_with_sent` records, for each call, how many had been sent when its answer was read:
    N for every call when they really met. A test asserts it, so a hold that did not happen is a
    failure and not a quiet pass.

    This wraps `Redis._send_command_parse_response`, a private redis-py method (6.4.0), which every
    plain command goes through. A first command sent in a pipeline, or a later redis-py that
    changes the method, is not seen: nothing is held, `answered_with_sent` stays empty, and the
    tests fail on it. It holds the first command, not the read: a rewrite whose first command is
    not its read (a WATCH, an unrelated write) would be held at the wrong point and might pass, so
    such a rewrite moves the hold to its read.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, redis: Redis, parties: int) -> None:
        self.parties = parties
        self.answered_with_sent: list[int] = []
        self._redis = redis
        self._sent = 0
        self._barrier = asyncio.Barrier(parties)
        send_and_parse = Redis._send_command_parse_response  # noqa: SLF001
        create = SessionStore.create

        async def hold_after_sending(
            client: Redis, conn: Any, command_name: str, *args: Any, **options: Any
        ) -> Any:
            if not _HOLD_FIRST_COMMAND.get():
                return await send_and_parse(client, conn, command_name, *args, **options)
            _HOLD_FIRST_COMMAND.set(False)  # only the first command of this call
            await conn.send_command(*args)
            self._sent += 1
            await asyncio.wait_for(self._barrier.wait(), timeout=10)
            self.answered_with_sent.append(self._sent)
            return await client.parse_response(conn, command_name, **options)

        async def create_held(store: SessionStore, **kwargs: str) -> Any:
            token = _HOLD_FIRST_COMMAND.set(True)
            try:
                return await create(store, **kwargs)
            finally:
                _HOLD_FIRST_COMMAND.reset(token)

        monkeypatch.setattr(Redis, "_send_command_parse_response", hold_after_sending)
        monkeypatch.setattr(SessionStore, "create", create_held)

    async def warm(self) -> None:
        pool = self._redis.connection_pool
        taken = [await pool.get_connection() for _ in range(self.parties)]
        for connection in taken:
            await pool.release(connection)


# --- 1. the setting -------------------------------------------------------------------------


class TestTheSetting:
    def test_the_default_is_the_founders_ten(self) -> None:
        assert Settings(_env_file=None).session_max_per_user == 10

    def test_the_range_is_one_to_a_hundred(self) -> None:
        assert SESSION_MAX_PER_USER_RANGE == (1, 100)

    def test_each_bound_is_accepted_and_one_past_it_stops_the_start_naming_the_setting(
        self,
    ) -> None:
        low, high = SESSION_MAX_PER_USER_RANGE
        for good in (low, high):
            assert Settings(_env_file=None, session_max_per_user=good).session_max_per_user == good
        for bad in (low - 1, high + 1):  # zero is refused, not read as "no limit"
            with pytest.raises(ValidationError) as refused:
                Settings(_env_file=None, session_max_per_user=bad)
            assert "session_max_per_user" in str(refused.value), bad

    def test_the_start_refuses_a_slip_naming_the_variable_and_not_the_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As a deploy sees it: the environment variable, and the message names the setting."""
        for value in ("0", "-5", "1000", "many", "10.5", ""):
            monkeypatch.setenv("SESSION_MAX_PER_USER", value)
            with pytest.raises(ValidationError) as refused:
                Settings(_env_file=None)
            said = str(refused.value)
            assert "session_max_per_user" in said.lower(), (value, said)
            if len(value) > 2:
                assert value not in said, (value, "the value was printed")
            monkeypatch.delenv("SESSION_MAX_PER_USER")

    def test_the_variable_sets_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SESSION_MAX_PER_USER", "3")
        assert Settings(_env_file=None).session_max_per_user == 3


# --- 2. the cap through the routes ----------------------------------------------------------


class TestTheCapThroughTheRoutes:
    async def test_the_eleventh_sign_in_ends_the_first_session_and_leaves_ten(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        user = (await _sign_up(client))["user"]["id"]  # sign-in number one
        tokens = [_token(client, settings)]
        for _ in range(9):
            tokens.append(await _login(client, settings))
        # Ten sessions, and every one of them works.
        assert await _statuses(client, settings, tokens) == [200] * 10
        assert await redis.scard(_index(user)) == 10

        tokens.append(await _login(client, settings))  # the eleventh sign-in

        assert await _statuses(client, settings, tokens) == [401] + [200] * 10
        gone = await _me_as(client, settings, tokens[0])
        assert gone.json()["type"] == "/problems/authentication-required"
        assert await redis.scard(_index(user)) == 10
        assert len(await _session_keys(redis)) == 10
        assert token_digest(tokens[0]) not in await redis.smembers(_index(user))

    async def test_a_sign_in_under_the_cap_ends_nothing(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        user = (await _sign_up(client))["user"]["id"]
        tokens = [_token(client, settings)]
        with structlog.testing.capture_logs() as logs:
            for _ in range(4):
                tokens.append(await _login(client, settings))

        assert await _statuses(client, settings, tokens) == [200] * 5
        assert await redis.scard(_index(user)) == 5
        assert _cap_events(logs) == []

    async def test_two_sign_ins_at_once_at_the_cap_leave_exactly_ten(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two real logins whose session calls MEET at the store: Argon2 runs in threads, so the
        two requests would otherwise reach Redis one after the other. Each `create` is held,
        after its first command is sent and before the answer is read, until both are on the wire
        (`_MeetAtRedis`). The cap still leaves ten, and both new sessions stay."""
        user = (await _sign_up(client))["user"]["id"]  # this session is the newest of the ten
        signed_up = _token(client, settings)
        now = datetime.now(UTC)
        older = [_digest(i) for i in range(9)]
        for i, digest in enumerate(older):
            await _plant(redis, user, digest, now - (10 - i) * HOUR)
        assert await redis.scard(_index(user)) == 10

        meeting = _MeetAtRedis(monkeypatch, redis, parties=2)
        await meeting.warm()
        client.cookies.clear()
        body = {"email": EMAIL, "password": PASSWORD}
        a, b = await asyncio.gather(
            asyncio.wait_for(client.post(f"{BASE}/auth/login", json=body), timeout=30),
            asyncio.wait_for(client.post(f"{BASE}/auth/login", json=body), timeout=30),
        )

        assert (a.status_code, b.status_code) == (200, 200), (a.text, b.text)
        assert meeting.answered_with_sent == [2, 2]  # both were sent before either was answered
        new = [_cookie_of(a, settings), _cookie_of(b, settings)]
        members = await redis.smembers(_index(user))
        assert len(members) == 10
        assert len(await _session_keys(redis)) == 10
        # The two oldest went, and neither of the two new sessions, nor the sign-up's, did.
        assert members == {*older[2:], token_digest(signed_up), *map(token_digest, new)}
        assert await _statuses(client, settings, [signed_up, *new]) == [200, 200, 200]


# --- 3. who goes ----------------------------------------------------------------------------


class TestWhoGoes:
    async def test_five_sign_ins_at_once_at_the_cap_leave_exactly_ten_and_end_none_of_themselves(
        self, store: SessionStore, redis: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Five calls whose first Redis commands are all on the wire before any is answered
        (`_MeetAtRedis`, on five pre-opened connections). A script that read the user's sessions
        in one call and wrote in the next would give all five the same ten sessions, cut the same
        one five times, and leave fourteen."""
        user = "usr_cap_race"
        now = datetime.now(UTC)
        planted = [_digest(i) for i in range(10)]
        for i, digest in enumerate(planted):
            await _plant(redis, user, digest, now - (10 - i) * HOUR)

        meeting = _MeetAtRedis(monkeypatch, redis, parties=5)
        await meeting.warm()
        made = await asyncio.gather(
            *(asyncio.wait_for(_create(store, user), timeout=30) for _ in range(5))
        )

        assert meeting.answered_with_sent == [5] * 5  # all five were sent before any was answered
        members = await redis.smembers(_index(user))
        assert len(members) == 10
        assert await _alive(redis, members) == members  # every entry has its key
        assert len(await _session_keys(redis)) == 10  # and no key is outside the index
        assert set(made) <= members
        assert members - set(made) == set(planted[5:])  # the five oldest went

    async def test_the_oldest_by_sign_in_goes_even_when_it_is_the_most_recently_used(
        self, store: SessionStore, redis: Redis
    ) -> None:
        user = "usr_cap_oldest"
        now = datetime.now(UTC)
        digests = [_digest(i) for i in range(10)]
        for i, digest in enumerate(digests):
            # i = 0 signed in ten hours ago and was used a minute ago; the rest were used when
            # they signed in.
            used = now - timedelta(minutes=1) if i == 0 else None
            await _plant(redis, user, digest, now - (10 - i) * HOUR, used=used)

        new = await _create(store, user)

        assert await _alive(redis, digests) == set(digests[1:])
        assert await redis.smembers(_index(user)) == {*digests[1:], new}

    async def test_the_oldest_is_by_sign_in_time_and_not_by_digest(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """Digests run the other way to the sign-in times, so ordering by digest cuts the newest."""
        user = "usr_cap_digest_order"
        now = datetime.now(UTC)
        digests = [_digest(0xF0 - i) for i in range(10)]  # i = 0 is the oldest and sorts last
        for i, digest in enumerate(digests):
            await _plant(redis, user, digest, now - (10 - i) * HOUR)

        new = await _create(store, user)

        assert await redis.smembers(_index(user)) == {*digests[1:], new}

    async def test_the_new_session_is_never_the_one_ended(
        self, store: SessionStore, redis: Redis, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session whose sign-in time is in the future (a clock that ran ahead) makes the new one
        look the OLDEST. It is still the one that stays."""
        monkeypatch.setattr(settings, "session_max_per_user", 1)
        user = "usr_cap_new"
        ahead = datetime.now(UTC) + DAY
        await _plant(redis, user, _digest(1), ahead)

        new = await _create(store, user)

        assert await redis.smembers(_index(user)) == {new}
        assert await _alive(redis, {_digest(1), new}) == {new}

    async def test_a_date_with_no_fraction_is_ordered_by_its_instant(
        self, store: SessionStore, redis: Redis, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`datetime.isoformat()` leaves the fraction out when the microseconds are 0. Three
        records within five microseconds of 10:00:00 UTC, planted newest first: .000005 past it, on
        it (no fraction), and .000001 before it. The one before it is the oldest, so it goes."""
        monkeypatch.setattr(settings, "session_max_per_user", 3)
        user = "usr_cap_zero_us"
        whole = datetime(2026, 10, 21, 10, 0, 0, tzinfo=UTC)
        records = {
            _digest(1): whole.replace(microsecond=5),
            _digest(2): whole,
            _digest(3): whole - timedelta(microseconds=1),
        }
        for digest, created in records.items():
            await _plant(redis, user, digest, created)
        texts = [json.loads(await redis.get(f"{SESSION_PREFIX}{d}"))["created_at"] for d in records]
        assert "2026-10-21T10:00:00+00:00" in texts, texts  # the one with no fraction

        new = await _create(store, user)  # four sessions, cap three: one goes

        assert await _alive(redis, set(records)) == {_digest(1), _digest(2)}
        assert await redis.smembers(_index(user)) == {_digest(1), _digest(2), new}

    async def test_a_date_written_with_another_offset_is_ordered_by_its_instant(
        self, store: SessionStore, redis: Redis, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing writes one today, but the order is of instants and not of text: 12:00 at plus two
        hours is 10:00 UTC, EARLIER than 11:00 UTC, though its text sorts later. And a trailing Z
        is UTC."""
        monkeypatch.setattr(settings, "session_max_per_user", 3)
        user = "usr_cap_offset"
        texts = {
            _digest(1): "2026-10-21T12:00:00+02:00",  # 10:00 UTC: the oldest
            _digest(2): "2026-10-21T11:00:00+00:00",
            _digest(3): "2026-10-21T11:30:00Z",
        }
        for digest, text in texts.items():
            await _plant(redis, user, digest, datetime.now(UTC),
                         raw=_record_with_created_at(user, text))

        new = await _create(store, user)

        assert await _alive(redis, set(texts)) == {_digest(2), _digest(3)}
        assert await redis.smembers(_index(user)) == {_digest(2), _digest(3), new}

    @pytest.mark.parametrize(
        "planted_first", [1, 2], ids=["small_digest_first", "large_digest_first"]
    )
    async def test_ties_go_to_the_smaller_digest_whatever_order_they_were_planted_in(
        self, store: SessionStore, redis: Redis, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, planted_first: int,
    ) -> None:
        monkeypatch.setattr(settings, "session_max_per_user", 2)
        user = "usr_cap_tie"
        same = datetime(2026, 10, 21, 10, 0, 0, 7, tzinfo=UTC)
        for n in (planted_first, 3 - planted_first):
            await _plant(redis, user, _digest(n), same)

        new = await _create(store, user)

        assert await redis.smembers(_index(user)) == {_digest(2), new}

    async def test_another_users_sessions_are_never_touched(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """Two people of one agency are two users: each has their own ten."""
        now = datetime.now(UTC)
        mine = [_digest(i) for i in range(10)]
        theirs = [_digest(0x40 + i) for i in range(10)]
        for i in range(10):
            await _plant(redis, "usr_cap_me", mine[i], now - (10 - i) * HOUR)
            await _plant(redis, "usr_cap_them", theirs[i], now - (10 - i) * HOUR)

        await _create(store, "usr_cap_me")

        assert await _alive(redis, mine) == set(mine[1:])
        assert await _alive(redis, theirs) == set(theirs)
        assert await redis.smembers(_index("usr_cap_them")) == set(theirs)

    async def test_a_key_past_its_effective_end_still_counts_until_it_expires_and_goes_first(
        self, store: SessionStore, redis: Redis, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`session_ttl_seconds` lowered to two days: a session signed in five days ago is past
        its effective absolute end (`SessionStore._absolute_end`) though its key, which a request
        would remove, has an hour left. The script judges no ends, so it counts: the cap cuts it
        (the event says one session was cut) and it is the one that goes, being the oldest."""
        monkeypatch.setattr(settings, "session_ttl_seconds", 2 * 24 * 3600)
        monkeypatch.setattr(settings, "session_max_per_user", 3)
        user = "usr_cap_past_end"
        now = datetime.now(UTC)
        past, inside_a, inside_b = _digest(1), _digest(2), _digest(3)
        await _plant(redis, user, past, now - 5 * DAY)
        await _plant(redis, user, inside_a, now - 30 * HOUR)
        await _plant(redis, user, inside_b, now - 6 * HOUR)

        with structlog.testing.capture_logs() as logs:
            new = await _create(store, user)

        assert [entry["revoked"] for entry in _cap_events(logs)] == [1]
        assert await _alive(redis, {past, inside_a, inside_b}) == {inside_a, inside_b}
        assert await redis.smembers(_index(user)) == {inside_a, inside_b, new}


# --- 4. what counts -------------------------------------------------------------------------

# Records the script cannot read, each as the text a key holds. `wrong_type_key` is planted by hand.
UNREADABLE = {
    "not_json": "this is not json",
    "empty_text": "",
    "json_null": "null",
    "json_array": "[1, 2]",
    "json_string": '"a string"',
    "empty_object": "{}",
    "created_at_a_number": '{"created_at": 12345}',
    "created_at_not_a_date": '{"created_at": "yesterday"}',
    "created_at_with_no_offset": '{"created_at": "2026-10-21T10:00:00"}',
    "created_at_month_thirteen": '{"created_at": "2026-13-21T10:00:00+00:00"}',
}


class TestWhatCounts:
    async def test_index_entries_whose_sessions_have_ended_do_not_count_and_are_dropped(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """Nine live sessions and five digests left behind by sessions that went idle: the new
        session is the tenth, so nobody is cut. Counted, the five would cost five live sessions."""
        user = "usr_cap_expired"
        now = datetime.now(UTC)
        live = [_digest(i) for i in range(9)]
        for i, digest in enumerate(live):
            await _plant(redis, user, digest, now - (9 - i) * HOUR)
        ended = [_digest(0x80 + i) for i in range(5)]
        await redis.sadd(_index(user), *ended)  # no key behind them
        assert await redis.scard(_index(user)) == 14

        with structlog.testing.capture_logs() as logs:
            new = await _create(store, user)

        assert _cap_events(logs) == []
        assert await _alive(redis, live) == set(live)
        assert await redis.smembers(_index(user)) == {*live, new}

    @pytest.mark.parametrize("text", list(UNREADABLE.values()), ids=list(UNREADABLE))
    async def test_a_record_the_script_cannot_read_is_cut_first_and_never_an_error(
        self, store: SessionStore, redis: Redis, text: str
    ) -> None:
        user = "usr_cap_unreadable"
        now = datetime.now(UTC)
        readable = [_digest(i) for i in range(9)]
        for i, digest in enumerate(readable):
            await _plant(redis, user, digest, now - (10 - i) * HOUR)
        await _plant(redis, user, _digest(0xAA), now, raw=text)  # newer than any: still cut first

        new = await _create(store, user)  # nine, one that is damaged, and this one: eleven

        assert await _alive(redis, {*readable, _digest(0xAA)}) == set(readable)
        assert await redis.smembers(_index(user)) == {*readable, new}

    async def test_a_key_of_the_wrong_type_is_cut_first_and_never_an_error(
        self, store: SessionStore, redis: Redis
    ) -> None:
        user = "usr_cap_wrong_type"
        now = datetime.now(UTC)
        readable = [_digest(i) for i in range(9)]
        for i, digest in enumerate(readable):
            await _plant(redis, user, digest, now - (10 - i) * HOUR)
        odd = _digest(0xAB)
        await redis.rpush(f"{SESSION_PREFIX}{odd}", "a", "b")  # a list where a string belongs
        await redis.expire(f"{SESSION_PREFIX}{odd}", 3600)
        await redis.sadd(_index(user), odd)

        new = await _create(store, user)

        assert await redis.exists(f"{SESSION_PREFIX}{odd}") == 0
        assert await redis.smembers(_index(user)) == {*readable, new}

    async def test_under_the_cap_a_record_the_script_cannot_read_is_left_alone(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """It counts as a session, but the cap is not reached, so nothing is cut. Its own TTL
        ends it."""
        user = "usr_cap_unreadable_quiet"
        now = datetime.now(UTC)
        for i in range(3):
            await _plant(redis, user, _digest(i), now - (3 - i) * HOUR)
        await _plant(redis, user, _digest(0xAA), now, raw="this is not json")

        with structlog.testing.capture_logs() as logs:
            new = await _create(store, user)

        assert _cap_events(logs) == []
        assert await redis.smembers(_index(user)) == {
            _digest(0), _digest(1), _digest(2), _digest(0xAA), new,
        }

    async def test_a_record_of_the_shape_production_holds_today_is_counted_and_ordered(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """Four fields and no `last_used_at`: what sessions open at the deploy look like."""
        user = "usr_cap_old_shape"
        now = datetime.now(UTC)
        digests = [_digest(i) for i in range(10)]
        for i, digest in enumerate(digests):
            created = (now - (10 - i) * HOUR).isoformat()
            await _plant(redis, user, digest, now, raw=_record_with_created_at(user, created))

        new = await _create(store, user)

        assert await _alive(redis, digests) == set(digests[1:])
        assert await redis.smembers(_index(user)) == {*digests[1:], new}


# --- 5. the setting changes the cap ---------------------------------------------------------


class TestTheSettingChangesTheCap:
    @pytest.mark.parametrize(
        ("cap", "survivors"),
        [(1, 0), (3, 2), (9, 8), (12, 8)],
        ids=["one", "three", "nine_which_all_fit", "twelve_which_all_fit"],
    )
    async def test_the_figure_is_the_cap_at_the_next_sign_in(
        self, store: SessionStore, redis: Redis, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, cap: int, survivors: int,
    ) -> None:
        """Eight sessions already open, the cap changed, one more sign-in: the newest
        `cap - 1` of the eight stay (all of them when they fit) and so does the new one."""
        monkeypatch.setattr(settings, "session_max_per_user", cap)
        user = "usr_cap_setting"
        now = datetime.now(UTC)
        eight = [_digest(i) for i in range(8)]
        for i, digest in enumerate(eight):
            await _plant(redis, user, digest, now - (8 - i) * HOUR)

        new = await _create(store, user)

        kept = set(eight[8 - survivors:])
        assert await _alive(redis, eight) == kept
        assert await redis.smembers(_index(user)) == {*kept, new}

    async def test_one_means_only_the_newest_session_lives(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "session_max_per_user", 1)
        await _sign_up(client)
        first = _token(client, settings)
        second = await _login(client, settings)

        assert await _statuses(client, settings, [first, second]) == [401, 200]

    @pytest.mark.parametrize("cap", [0, -3])
    async def test_a_figure_that_reaches_the_script_below_one_is_held_to_one(
        self, store: SessionStore, redis: Redis, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, cap: int,
    ) -> None:
        """The start refuses a figure below one, but the script is the last line: whatever
        reaches it, the new session is never ended and a sign-in is never an error."""
        monkeypatch.setattr(settings, "session_max_per_user", cap)
        user = "usr_cap_floor"
        now = datetime.now(UTC)
        for i in range(3):
            await _plant(redis, user, _digest(i), now - (3 - i) * HOUR)

        new = await _create(store, user)

        assert await redis.smembers(_index(user)) == {new}
        assert await _alive(redis, {new, _digest(0), _digest(1), _digest(2)}) == {new}

    async def test_the_figure_is_read_at_each_sign_in(
        self, store: SessionStore, redis: Redis, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = "usr_cap_each"
        now = datetime.now(UTC)
        a, b = _digest(1), _digest(2)
        await _plant(redis, user, a, now - 2 * HOUR)
        await _plant(redis, user, b, now - HOUR)
        monkeypatch.setattr(settings, "session_max_per_user", 3)
        c = await _create(store, user)
        assert await redis.smembers(_index(user)) == {a, b, c}  # three fit
        monkeypatch.setattr(settings, "session_max_per_user", 2)
        d = await _create(store, user)
        assert await redis.smembers(_index(user)) == {c, d}  # now two do


# --- 6. N1: the index is never shortened ----------------------------------------------------


class TestASignInNeverShortensTheIndex:
    async def test_lower_the_lifetime_sign_in_and_raise_it_and_the_first_session_has_its_index(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mint under 14 days, lower to two, sign in again, raise back. Before this change the
        second sign-in SET the index to two days, and the first session, whose minted end is still
        14 days out once the setting is back, outlived its index: sign out everywhere, a password
        change, a reset and a Google link would miss it."""
        user = (await _sign_up(client))["user"]["id"]
        (first_key,) = await _session_keys(redis)
        first = SessionRecord.from_json(await redis.get(first_key))
        index = _index(user)

        monkeypatch.setattr(settings, "session_ttl_seconds", 2 * 24 * 3600)
        await _login(client, settings)
        # Never shortened, not even for a moment.
        assert await redis.pttl(index) >= ABSOLUTE_MS - FRESH_MS
        monkeypatch.setattr(settings, "session_ttl_seconds", 14 * 24 * 3600)

        left_ms = (first.expires_at - datetime.now(UTC)).total_seconds() * 1000
        assert await redis.pttl(index) >= left_ms - 1_000
        assert first_key.removeprefix(SESSION_PREFIX) in await redis.smembers(index)

    async def test_an_index_with_more_time_left_than_the_new_lifetime_is_left_alone(
        self, store: SessionStore, redis: Redis
    ) -> None:
        user = "usr_cap_n1_more"
        await redis.sadd(_index(user), _digest(1))
        twenty_days_ms = 20 * 24 * 3600 * 1000
        await redis.pexpire(_index(user), twenty_days_ms)

        await _create(store, user)

        assert twenty_days_ms - FRESH_MS <= await redis.pttl(_index(user)) <= twenty_days_ms

    async def test_an_index_with_less_time_left_is_raised_to_the_new_lifetime(
        self, store: SessionStore, redis: Redis
    ) -> None:
        user = "usr_cap_n1_less"
        await redis.sadd(_index(user), _digest(1))
        await redis.pexpire(_index(user), 60_000)

        await _create(store, user)

        assert ABSOLUTE_MS - FRESH_MS <= await redis.pttl(_index(user)) <= ABSOLUTE_MS

    async def test_an_index_with_no_expiry_is_given_one(
        self, store: SessionStore, redis: Redis
    ) -> None:
        user = "usr_cap_n1_none"
        await redis.sadd(_index(user), _digest(1))
        assert await redis.pttl(_index(user)) == -1

        await _create(store, user)

        assert ABSOLUTE_MS - FRESH_MS <= await redis.pttl(_index(user)) <= ABSOLUTE_MS

    async def test_a_new_index_is_given_the_absolute_lifetime(
        self, store: SessionStore, redis: Redis
    ) -> None:
        await _create(store, "usr_cap_n1_new")

        assert ABSOLUTE_MS - FRESH_MS <= await redis.pttl(_index("usr_cap_n1_new")) <= ABSOLUTE_MS

    async def test_the_session_key_still_lives_the_idle_window(
        self, store: SessionStore, redis: Redis
    ) -> None:
        digest = await _create(store, "usr_cap_n1_key")

        assert IDLE_MS - FRESH_MS <= await redis.pttl(f"{SESSION_PREFIX}{digest}") <= IDLE_MS


# --- 7. at the deploy -----------------------------------------------------------------------


class TestAtTheDeploy:
    async def test_an_index_over_the_cap_is_cut_at_that_users_next_sign_in(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """Nothing capped sessions before this change, so an index can hold more than ten live
        sessions on the day it ships: fifteen, in the old four-field shape. The user's next sign-in
        ends their oldest six and leaves ten, the new one among them."""
        user = "usr_cap_deploy"
        now = datetime.now(UTC)
        digests = [_digest(i) for i in range(15)]
        for i, digest in enumerate(digests):
            created = (now - (15 - i) * HOUR).isoformat()
            await _plant(redis, user, digest, now, raw=_record_with_created_at(user, created),
                         left=5 * DAY)

        with structlog.testing.capture_logs() as logs:
            new = await _create(store, user)

        assert [entry["revoked"] for entry in _cap_events(logs)] == [6]
        assert await _alive(redis, digests) == set(digests[6:])
        assert await redis.smembers(_index(user)) == {*digests[6:], new}
        assert len(await _session_keys(redis)) == 10

    async def test_the_index_stays_a_set(self, store: SessionStore, redis: Redis) -> None:
        user = "usr_cap_type"
        await _create(store, user)
        assert await redis.type(_index(user)) == "set"
        # Production's existing indexes are sets that old code made with SADD, and keep working.
        await redis.sadd("user-sessions:usr_cap_type_old", _digest(1))
        await _create(store, "usr_cap_type_old")
        assert await redis.type("user-sessions:usr_cap_type_old") == "set"

    async def test_thousands_of_ended_entries_are_dropped_in_one_sign_in(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """An index that old code kept for fourteen days can hold a great many digests of ended
        sessions. `unpack` cannot pass thousands of arguments, so the script removes in chunks."""
        user = "usr_cap_thousands"
        live = [_digest(1), _digest(2)]
        now = datetime.now(UTC)
        for i, digest in enumerate(live):
            await _plant(redis, user, digest, now - (2 - i) * HOUR)
        ended = [f"{i:064x}" for i in range(0x1000, 0x1000 + 9000)]
        for start in range(0, len(ended), 1000):
            await redis.sadd(_index(user), *ended[start : start + 1000])
        assert await redis.scard(_index(user)) == 9002

        new = await asyncio.wait_for(_create(store, user), timeout=30)

        assert await redis.smembers(_index(user)) == {*live, new}

    async def test_a_script_that_fails_while_reading_has_written_nothing(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """The index is not a set (nothing here ever makes it one). The sign-in fails, as it
        always did, and leaves no session key behind: the script reads before it writes."""
        user = "usr_cap_not_a_set"
        await redis.set(_index(user), "not a set")

        with pytest.raises(ResponseError):
            await _create(store, user)

        assert await _session_keys(redis) == []
        assert await redis.get(_index(user)) == "not a set"


# --- 8. the log line and the shape ----------------------------------------------------------


class TestTheLogLineAndTheShape:
    async def test_the_cap_logs_one_event_with_the_user_id_and_the_count_and_nothing_else(
        self, store: SessionStore, redis: Redis
    ) -> None:
        user = "usr_cap_log"
        now = datetime.now(UTC)
        digests = [_digest(i) for i in range(10)]
        for i, digest in enumerate(digests):
            await _plant(redis, user, digest, now - (10 - i) * HOUR)

        with structlog.testing.capture_logs() as logs:
            token, _record = await store.create(user_id=user, agency_id="agy_cap")

        assert _cap_events(logs) == [
            {"event": "session.cap_revoked", "user_id": user, "revoked": 1, "log_level": "info"}
        ]
        # No token and no digest, of the new session or the one that went, anywhere in the logs.
        everything = json.dumps(logs, default=str)
        for secret in (token, token_digest(token), *digests):
            assert secret not in everything

    async def test_nothing_is_logged_when_nothing_is_cut(self, store: SessionStore) -> None:
        with structlog.testing.capture_logs() as logs:
            await _create(store, "usr_cap_quiet")

        assert logs == []

    async def test_create_keeps_its_signature_and_its_result(
        self, store: SessionStore, redis: Redis
    ) -> None:
        """The routes and several tests call it, or wrap it, with exactly these arguments."""
        parameters = list(inspect.signature(SessionStore.create).parameters.values())
        assert [p.name for p in parameters] == ["self", "user_id", "agency_id"]
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters[1:])

        token, record = await store.create(user_id="usr_cap_shape", agency_id="agy_cap_shape")

        assert isinstance(token, str)
        assert (record.user_id, record.agency_id) == ("usr_cap_shape", "agy_cap_shape")
        assert record.last_used_at == record.created_at
        assert record.expires_at - record.created_at == ABSOLUTE
        stored = await redis.get(f"{SESSION_PREFIX}{token_digest(token)}")
        assert SessionRecord.from_json(stored) == record
        assert await redis.exists(f"{SESSION_PREFIX}{token}") == 0  # only the digest is kept
        assert await store.get(token) == record

    async def test_a_sign_in_whose_session_script_cannot_reach_redis_is_a_503_and_mints_nothing(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The mint used to be a pipeline and is now a script; losing Redis at that step is still
        an outage (fail closed, 503) and not a 500, and no session is left behind. (Only that one
        script is refused: the limiter, which also runs scripts, has already answered.)"""
        await _sign_up(client)
        real = Redis.eval

        async def refuse_the_mint(self: Redis, script: str, *args: Any, **kwargs: Any) -> Any:
            if script == sessions._CREATE:  # noqa: SLF001 - the script under test
                raise RedisConnectionError("refused")
            return await real(self, script, *args, **kwargs)

        monkeypatch.setattr(Redis, "eval", refuse_the_mint)
        client.cookies.clear()
        resp = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})

        assert resp.status_code == 503, resp.text
        assert resp.json()["type"] == "/problems/service-unavailable"
        assert "set-cookie" not in resp.headers
        assert len(await _session_keys(redis)) == 1  # the sign-up's, and no other


class TestOnlyTheStoreWritesASessionKey:
    def test_no_other_module_names_the_session_prefixes(self) -> None:
        """Every sign-in route mints through `SessionStore.create`, so the cap covers them all
        (sign-up, login, change-password, an invitation accept, Google's callback and its sign-up
        completion). That holds only while nothing else builds a session key or an index key. The
        sweep covers the `avp_api` package and `apps/api/scripts`, where the operator's reset script
        lives: a script may name `SessionStore` (the reset script calls `revoke_all_for_user`),
        never the prefixes."""
        package = Path(avp_api.__file__).resolve().parent
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        assert (scripts / "issue_password_reset.py").is_file(), "the sweep must reach the script"
        names = {"SESSION_PREFIX", "USER_SESSIONS_PREFIX"}
        found: list[str] = []
        swept = {"avp_api": 0, "scripts": 0}
        for label, root in (("avp_api", package), ("scripts", scripts)):
            for path in sorted(root.rglob("*.py")):
                rel = f"{label}/{path.relative_to(root).as_posix()}"
                if rel == "avp_api/sessions.py":
                    continue
                swept[label] += 1
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.Name) and node.id in names:
                        found.append(f"{rel}:{node.lineno} {node.id}")
                    elif isinstance(node, ast.Attribute) and node.attr in names:
                        found.append(f"{rel}:{node.lineno} {node.attr}")
                    elif isinstance(node, ast.alias) and node.name in names:
                        found.append(f"{rel} imports {node.name}")
                    elif (
                        isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and node.value.startswith(("sess:", "user-sessions:"))
                    ):
                        found.append(f"{rel}:{node.lineno} {node.value[:20]!r}")
        # The sweep cannot quietly cover nothing: it reached the package and the scripts.
        assert swept["avp_api"] >= 50 and swept["scripts"] >= 5, swept
        assert found == []

"""A session ends at its idle end or its absolute end, whichever comes first — A3 part 1 (D8).

Until A3 a session lasted 14 days from sign-in, used or not, and `SessionStore.touch` had no
caller. Now a session ends at whichever comes first of

* its ABSOLUTE end: `session_ttl_seconds` (14 days) after sign-in, however often it is used. The
  setting keeps the name it had, so a SESSION_TTL_SECONDS set on a deployed service still means
  what it did, and it is still the cookie's Max-Age;
* its IDLE end: `session_idle_seconds` (72 hours) after it was last used. The key's Redis TTL IS
  that clock. `current_principal` refreshes it, but only once the last refresh is older than
  `session_touch_interval_seconds` (1 hour), so most requests make no write, and never past the
  absolute end, and never for a session a revoke has already removed.

Time is controlled through the record and the key's TTL, and nothing sleeps: `_age` rewrites the
session record as it would look some hours or days on, and gives the key the TTL it would then have
left. A request is then made the way a browser makes it. Redis writes are counted by a spy on
redis-py's command dispatch (plain commands and pipelines), so a write that a request made is seen
whatever code made it.

What this file holds, group by group:

1. **The settings.** The founder's numbers, the ranges, a refused start naming the variable and not
   the value, the absolute lifetime keeping its name, and the two pairs that are cut and not
   refused (an idle window above the absolute lifetime; an interval above half the idle window).
2. **A new session.** Its key lives the idle window, its index and cookie the absolute lifetime,
   and the index ends no earlier than the session can.
3. **The record.** One from before idle windows existed (what production holds at the deploy) is
   read, and written back as it was; a new one is still readable by the reader before this change;
   and only reading old JSON can give a record with no idle end (a constructor that forgets the
   field is a `TypeError`).
4. **The two ends.** Unused for the idle window: refused and removed. Used inside it: kept, and
   the clock restarts. Past the absolute end: refused however recently it was used. A refresh
   never runs the key past the absolute end, even for a session used every single day, and a
   session within a millisecond of that end makes no write at all.
4b. **A lowered lifetime** (the security review's L1). Lowering `session_ttl_seconds` applies to
   sessions already open: refused and removed at sign-in plus the new value, and a refresh never
   runs the key past it. Raising it never lengthens a session past the end it was minted with, and
   a lifetime too long to add to a date does not turn a request into an error.
5. **The interval.** A session just refreshed makes no write; one that is due makes exactly one,
   a `SET ... XX` on its own key alone; the interval setting moves when that is; and the response
   to that request renews no cookie (the cookie is not renewed by use).
6. **A revoke wins.** A revoke (or a sign-out everywhere) between the read and the refresh leaves
   the session revoked.
7. **Sessions that exist at the deploy** keep working: an old-shape record is let in and its idle
   clock starts at its first request after the deploy, never "idle since sign-in".
8. **A refresh that fails** is logged and the request goes on; **a refused request** refreshes
   nothing; **`reconfirm_principal`** still only reads.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
import structlog
from httpx import AsyncClient
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import ResponseError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import redis_client as redis_module
from avp_api import sessions
from avp_api.config import (
    SESSION_IDLE_SECONDS_RANGE,
    SESSION_TOUCH_SECONDS_RANGE,
    Settings,
)
from avp_api.deps import Principal, reconfirm_principal
from avp_api.models import Agency, User, UserRole, UserStatus
from avp_api.security import token_digest
from avp_api.sessions import SessionRecord, SessionStore

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
EMAIL = "lifetimes@session.example"

MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
IDLE = 72 * HOUR
ABSOLUTE = 14 * DAY
IDLE_MS = int(IDLE.total_seconds() * 1000)
ABSOLUTE_MS = int(ABSOLUTE.total_seconds() * 1000)
# How far under its full window a key just written may already be: a test takes seconds.
FRESH_MS = 30_000

# What the spy lets pass as a read; every other command counts as a write, so a command this file
# has never heard of cannot hide.
READS = frozenset({"GET", "MGET", "EXISTS", "TTL", "PTTL", "SMEMBERS", "SISMEMBER", "SCAN", "TYPE"})

OLD_SHAPE = {
    "user_id": "usr_before",
    "agency_id": "agy_before",
    "created_at": "2026-10-01T00:00:00+00:00",
    "expires_at": "2026-10-15T00:00:00+00:00",
}


# --- helpers --------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def redis(client: AsyncClient, settings: Settings) -> Redis:
    """The suite's test Redis, emptied first: these tests expect exactly the keys they make."""
    # `client` is asked for so the app is wired to this Redis, and its teardown empties it again.
    r = redis_module.get_redis(settings)
    await r.flushdb()
    return r


async def _sign_up(client: AsyncClient, email: str = EMAIL) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Lifetimes Agency", "fullName": "Operator",
              "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _keys(redis: Redis, prefix: str) -> list[str]:
    return sorted([key async for key in redis.scan_iter(f"{prefix}*")])


async def _the_session(redis: Redis) -> tuple[str, str, SessionRecord]:
    """The one session in Redis: its key, its index key and its record."""
    (key,) = await _keys(redis, sessions.SESSION_PREFIX)
    (index,) = await _keys(redis, sessions.USER_SESSIONS_PREFIX)
    return key, index, SessionRecord.from_json(await redis.get(key))


async def _age(
    redis: Redis,
    key: str,
    *,
    signed_in: timedelta,
    last_used: timedelta | None,
    key_left: timedelta,
    absolute: timedelta = ABSOLUTE,
) -> SessionRecord:
    """Make the session look as it would if it was signed in `signed_in` ago and last refreshed
    `last_used` ago (None: a record from before idle windows), with `key_left` on its key.

    Time is controlled here, through the record and the key's TTL, so no test sleeps.
    """
    old = SessionRecord.from_json(await redis.get(key))
    now = datetime.now(UTC)
    created = now - signed_in
    record = SessionRecord(
        user_id=old.user_id,
        agency_id=old.agency_id,
        created_at=created,
        expires_at=created + absolute,
        last_used_at=None if last_used is None else now - last_used,
    )
    await redis.set(key, record.to_json(), px=int(key_left.total_seconds() * 1000))
    return record


def _spy_on_redis(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Record every command the API's Redis clients send, plain and queued in a pipeline."""
    seen: list[tuple[Any, ...]] = []
    plain = Redis.execute_command
    queued = Pipeline.execute_command

    async def _plain(self: Redis, *args: Any, **options: Any) -> Any:
        seen.append(args)
        return await plain(self, *args, **options)

    def _queued(self: Pipeline, *args: Any, **options: Any) -> Any:
        seen.append(args)
        return queued(self, *args, **options)

    monkeypatch.setattr(Redis, "execute_command", _plain)
    monkeypatch.setattr(Pipeline, "execute_command", _queued)
    return seen


def _writes(seen: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [args for args in seen if str(args[0]).upper() not in READS]


async def _me(client: AsyncClient, times: int = 1) -> list[int]:
    return [(await client.get(f"{BASE}/auth/me")).status_code for _ in range(times)]


def _used(record: SessionRecord) -> datetime:
    assert record.last_used_at is not None
    return record.last_used_at


def _token(client: AsyncClient, settings: Settings) -> str:
    token = client.cookies.get(settings.session_cookie_key)
    assert token
    return token


# --- 1. the settings ------------------------------------------------------------------------


class TestTheSettings:
    def test_the_defaults_are_the_founders_numbers(self) -> None:
        s = Settings(_env_file=None)
        assert (
            s.session_ttl_seconds, s.session_idle_seconds, s.session_touch_interval_seconds
        ) == (14 * 24 * 3600, 72 * 3600, 3600)

    def test_the_ranges_are_the_ones_documented(self) -> None:
        assert SESSION_IDLE_SECONDS_RANGE == (900, 2_592_000)
        assert SESSION_TOUCH_SECONDS_RANGE == (60, 86_400)

    @pytest.mark.parametrize(
        ("name", "low", "high"),
        [
            ("session_idle_seconds", *SESSION_IDLE_SECONDS_RANGE),
            ("session_touch_interval_seconds", *SESSION_TOUCH_SECONDS_RANGE),
        ],
    )
    def test_each_bound_is_accepted_and_one_past_it_stops_the_start_naming_the_setting(
        self, name: str, low: int, high: int
    ) -> None:
        for good in (low, high):
            assert getattr(Settings(_env_file=None, **{name: good}), name) == good
        for bad in (low - 1, high + 1):
            with pytest.raises(ValidationError) as refused:
                Settings(_env_file=None, **{name: bad})
            assert name in str(refused.value), (name, bad)

    def test_the_start_refuses_a_slip_of_units_naming_the_variable_and_not_the_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As a deploy sees it: the environment variable, and the message names the setting."""
        for variable, value in (
            ("SESSION_IDLE_SECONDS", "72"),  # hours typed as seconds: signed out a minute later
            ("SESSION_IDLE_SECONDS", "259200000"),  # milliseconds typed as seconds: no limit
            ("SESSION_IDLE_SECONDS", "3 days"),
            ("SESSION_IDLE_SECONDS", ""),
            ("SESSION_TOUCH_INTERVAL_SECONDS", "0"),
            ("SESSION_TOUCH_INTERVAL_SECONDS", "3600000"),
        ):
            monkeypatch.setenv(variable, value)
            with pytest.raises(ValidationError) as refused:
                Settings(_env_file=None)
            said = str(refused.value)
            assert variable.lower() in said.lower(), (variable, said)
            if len(value) > 2:
                assert value not in said, (variable, "the value was printed")
            monkeypatch.delenv(variable)

    def test_the_absolute_lifetime_keeps_its_name_so_a_value_set_on_a_service_still_means_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`Settings` has `extra="ignore"`: a renamed setting would be dropped without a word and
        a deployed SESSION_TTL_SECONDS would silently stop deciding anything."""
        assert "session_ttl_seconds" in Settings.model_fields
        assert not [name for name in Settings.model_fields if "absolute" in name]
        monkeypatch.setenv("SESSION_TTL_SECONDS", str(7 * 24 * 3600))
        assert Settings(_env_file=None).session_ttl_seconds == 7 * 24 * 3600

    def test_an_idle_window_above_the_absolute_lifetime_is_a_pair_that_is_cut_not_a_range_error(
        self,
    ) -> None:
        pair = Settings(
            _env_file=None, session_idle_seconds=20 * 24 * 3600, session_ttl_seconds=14 * 24 * 3600
        )
        assert (pair.session_idle_seconds, pair.session_ttl_seconds) == (
            20 * 24 * 3600, 14 * 24 * 3600,
        )

    async def test_an_idle_window_above_the_absolute_lifetime_is_cut_to_it_at_sign_in(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "session_idle_seconds", 20 * 24 * 3600)
        await _sign_up(client)
        key, _index, _record = await _the_session(redis)
        assert ABSOLUTE_MS - FRESH_MS <= await redis.pttl(key) <= ABSOLUTE_MS

    async def test_a_refresh_interval_above_half_the_idle_window_is_cut_to_half_of_it(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An operator who shortens only the idle window (to an hour, the interval's own default)
        must not sign out people who are using the product: the key would run out before the next
        refresh was due. The interval is cut to half the idle window, here 30 minutes."""
        monkeypatch.setattr(settings, "session_idle_seconds", 3600)
        assert settings.session_touch_interval_seconds == 3600
        await _sign_up(client)
        key, _index, _record = await _the_session(redis)
        seen = _spy_on_redis(monkeypatch)

        await _age(redis, key, signed_in=3 * HOUR, last_used=29 * MINUTE, key_left=31 * MINUTE)
        seen.clear()
        assert await _me(client) == [200]
        assert _writes(seen) == [], "a refresh made before half the idle window had passed"

        await _age(redis, key, signed_in=3 * HOUR, last_used=31 * MINUTE, key_left=29 * MINUTE)
        seen.clear()
        assert await _me(client) == [200]
        assert len(_writes(seen)) == 1, "no refresh once half the idle window had passed"
        # And the key got the whole of the idle window back, the last refresh's own.
        assert 3_600_000 - FRESH_MS <= await redis.pttl(key) <= 3_600_000


# --- 2. a new session -----------------------------------------------------------------------


class TestANewSession:
    async def test_its_key_lives_the_idle_window_and_its_index_and_cookie_the_absolute_lifetime(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        signed_up = await client.post(
            f"{BASE}/auth/sign-up",
            json={"agencyName": "Cookie Agency", "fullName": "Op",
                  "email": "cookie@session.example", "password": PASSWORD},
        )
        assert signed_up.status_code == 201, signed_up.text
        key, index, record = await _the_session(redis)

        assert IDLE_MS - FRESH_MS <= await redis.pttl(key) <= IDLE_MS
        assert ABSOLUTE_MS - FRESH_MS <= await redis.pttl(index) <= ABSOLUTE_MS
        # The record: signing in is a use, and the absolute end is fixed now.
        assert record.last_used_at == record.created_at
        assert record.expires_at - record.created_at == ABSOLUTE
        # The cookie keeps for the longest a session can live; the server decides sooner.
        name = settings.session_cookie_key
        cookie = next(c for c in signed_up.headers.get_list("set-cookie") if c.startswith(name))
        assert f"Max-Age={int(ABSOLUTE.total_seconds())}" in cookie, cookie

    async def test_its_index_ends_no_earlier_than_the_session_can(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        """The key's TTL is 72 hours now, but the session can live to its absolute end by being
        used: an index that ended with the key's TTL would miss it at "sign out everywhere". This
        is the user's FIRST session, which makes the index and so gives it the lifetime; a later
        sign-in raises an index that would end sooner and never shortens one (A3 part 2, N1:
        `test_session_cap.py`)."""
        await _sign_up(client)
        _key, index, record = await _the_session(redis)
        left_ms = (record.expires_at - datetime.now(UTC)).total_seconds() * 1000
        assert await redis.pttl(index) >= left_ms - 1_000

    async def test_a_refresh_leaves_the_index_and_the_rest_of_the_record_alone(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        await _sign_up(client)
        key, index, _ = await _the_session(redis)
        before = await _age(redis, key, signed_in=9 * DAY, last_used=2 * HOUR, key_left=HOUR)
        await redis.pexpire(index, int((ABSOLUTE - 9 * DAY).total_seconds() * 1000))
        index_before = await redis.pttl(index)
        members_before = await redis.smembers(index)

        assert await _me(client) == [200]

        after = SessionRecord.from_json(await redis.get(key))
        assert (after.user_id, after.agency_id, after.created_at, after.expires_at) == (
            before.user_id, before.agency_id, before.created_at, before.expires_at,
        )
        assert after.last_used_at is not None
        assert abs(after.last_used_at - datetime.now(UTC)) < timedelta(seconds=30)
        assert 0 < await redis.pttl(index) <= index_before, "a refresh renewed the index"
        assert await redis.smembers(index) == members_before


# --- 3. the record --------------------------------------------------------------------------


class TestTheRecord:
    def test_a_record_from_before_idle_windows_is_read_with_no_last_used(self) -> None:
        record = SessionRecord.from_json(json.dumps(OLD_SHAPE))
        assert record.last_used_at is None
        assert (record.user_id, record.agency_id) == ("usr_before", "agy_before")
        assert record.created_at == datetime(2026, 10, 1, tzinfo=UTC)
        assert record.expires_at == datetime(2026, 10, 15, tzinfo=UTC)

    def test_a_record_with_no_last_used_is_written_back_as_it_was(self) -> None:
        record = SessionRecord.from_json(json.dumps(OLD_SHAPE))
        assert json.loads(record.to_json()) == OLD_SHAPE

    def test_a_record_with_a_null_last_used_is_read_the_same_way(self) -> None:
        record = SessionRecord.from_json(json.dumps({**OLD_SHAPE, "last_used_at": None}))
        assert record.last_used_at is None

    def test_a_record_cannot_be_built_without_saying_when_it_was_last_used(self) -> None:
        """Only `from_json` may give a record with no idle end (an old one). A constructor that
        forgets the field is an error, not a session that never goes idle."""
        with pytest.raises(TypeError):
            SessionRecord(
                user_id="usr_a", agency_id="agy_a",
                created_at=datetime(2026, 10, 1, tzinfo=UTC),
                expires_at=datetime(2026, 10, 15, tzinfo=UTC),
            )
        # The one way in that stays open: old JSON still reads, with no idle end.
        assert SessionRecord.from_json(json.dumps(OLD_SHAPE)).last_used_at is None

    def test_a_new_record_is_still_readable_by_the_reader_before_this_change(self) -> None:
        """A rolling deploy or a rollback: an instance that has not heard of `last_used_at` reads
        the four fields it knows, in the formats it knows, and ignores the new one."""
        used = datetime(2026, 10, 3, 12, tzinfo=UTC)
        record = SessionRecord(
            user_id="usr_a", agency_id="agy_a",
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
            expires_at=datetime(2026, 10, 15, tzinfo=UTC), last_used_at=used,
        )
        data = json.loads(record.to_json())
        assert set(data) == {*OLD_SHAPE, "last_used_at"}
        # The reader as it was at HEAD before this change: these four keys, nothing else.
        assert (
            data["user_id"], data["agency_id"],
            datetime.fromisoformat(data["created_at"]), datetime.fromisoformat(data["expires_at"]),
        ) == ("usr_a", "agy_a", record.created_at, record.expires_at)
        assert SessionRecord.from_json(record.to_json()) == record


# --- 4. the two ends ------------------------------------------------------------------------


class TestTheIdleEndAndTheAbsoluteEnd:
    async def test_a_session_unused_for_the_idle_window_is_refused_and_removed(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        """The key's TTL is the real expiry; this is the check on the record in case a record is
        ever written without one (here the key is still there, with an hour on it)."""
        await _sign_up(client)
        key, index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=80 * HOUR, last_used=73 * HOUR, key_left=HOUR)

        resp = await client.get(f"{BASE}/auth/me")

        assert resp.status_code == 401, resp.text
        assert resp.json()["type"] == "/problems/authentication-required"
        assert await redis.exists(key) == 0
        assert await redis.smembers(index) == set()

    async def test_a_session_used_inside_the_idle_window_stays_and_its_clock_restarts(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=100 * HOUR, last_used=71 * HOUR, key_left=HOUR)

        assert await _me(client) == [200]

        record = SessionRecord.from_json(await redis.get(key))
        assert record.last_used_at is not None
        assert abs(record.last_used_at - datetime.now(UTC)) < timedelta(seconds=30)
        assert IDLE_MS - FRESH_MS <= await redis.pttl(key) <= IDLE_MS

    async def test_a_session_unused_for_less_than_the_window_is_not_refused_at_its_edge(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=80 * HOUR, last_used=IDLE - MINUTE, key_left=MINUTE)
        assert await _me(client) == [200]

    async def test_a_session_past_its_absolute_end_is_refused_however_recently_it_was_used(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        await _sign_up(client)
        key, index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=ABSOLUTE + MINUTE, last_used=MINUTE, key_left=HOUR)

        resp = await client.get(f"{BASE}/auth/me")

        assert resp.status_code == 401, resp.text
        assert await redis.exists(key) == 0
        assert await redis.smembers(index) == set()

    async def test_a_refresh_never_runs_the_key_past_the_absolute_end(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        """An hour of its fourteen days is left. The idle window would give it 72 more hours; it
        gets the hour, and nothing else about the record moves."""
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        before = await _age(
            redis, key, signed_in=ABSOLUTE - HOUR, last_used=2 * HOUR, key_left=30 * MINUTE
        )

        assert await _me(client) == [200]

        assert 0 < await redis.pttl(key) <= 3_600_000, "the key outlives the absolute end"
        after = SessionRecord.from_json(await redis.get(key))
        assert after.expires_at == before.expires_at
        assert _used(after) > _used(before)

    async def test_a_session_used_every_day_still_ends_at_its_absolute_end(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        """Day by day: each day it was used the day before (refreshed then, 48 hours of its key
        left). The key never gets more than what remains of the fourteen days, and on the first
        minute past them the session is refused, used yesterday as ever."""
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        for day in range(1, 14):
            await _age(redis, key, signed_in=day * DAY, last_used=DAY, key_left=48 * HOUR)
            assert await _me(client) == [200], day
            left_ms = int((ABSOLUTE - day * DAY).total_seconds() * 1000)
            ttl = await redis.pttl(key)
            assert 0 < ttl <= min(IDLE_MS, left_ms), (day, ttl, left_ms)
        await _age(redis, key, signed_in=ABSOLUTE + MINUTE, last_used=DAY, key_left=48 * HOUR)
        assert await _me(client) == [401]
        assert await redis.exists(key) == 0

    @pytest.mark.parametrize(
        "left",
        [timedelta(microseconds=300), -timedelta(seconds=1)],
        ids=["a_fraction_of_a_millisecond_left", "already_over"],
    )
    async def test_a_session_within_a_millisecond_of_its_absolute_end_makes_no_write(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch, left: timedelta,
    ) -> None:
        """Due a refresh, but there is no window left to give: not even a refused attempt is
        made (a `SET ... PX 0`, which Redis would refuse and the refusal be logged), and nothing
        is raised."""
        await _sign_up(client)
        token = _token(client, settings)
        key, _index, _ = await _the_session(redis)
        record = await _age(
            redis, key, signed_in=ABSOLUTE - left, last_used=2 * HOUR, key_left=HOUR
        )
        store = SessionStore(redis, settings)
        seen = _spy_on_redis(monkeypatch)

        with structlog.testing.capture_logs() as logs:
            assert await store.touch(token, record) is False

        assert _writes(seen) == [], seen
        assert [entry for entry in logs if entry["event"] == "session.touch_failed"] == []
        assert SessionRecord.from_json(await redis.get(key)) == record


# --- 4b. a lowered lifetime applies to the sessions already open (the review's L1) ------------


class TestALoweredLifetimeAppliesToSessionsAlreadyOpen:
    """`session_ttl_seconds` lowered from 14 days to 2 after a session was minted under 14 days.

    The end that counts is the earlier of the end the session was minted with and its sign-in plus
    the CURRENT lifetime. Before, a session used often enough outlived the new limit, and the user
    index, which each sign-in then set to the new and shorter lifetime, could end before it and
    leave "sign out everywhere" blind to it. (A sign-in no longer shortens the index at all: A3
    part 2, `test_session_cap.py`.)"""

    TWO_DAYS_S = 2 * 24 * 3600

    async def test_a_session_signed_in_longer_ago_than_the_new_value_is_refused_and_removed(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        key, index, _ = await _the_session(redis)
        # Signed in three days ago under the 14 days it was minted with; used a minute ago.
        await _age(redis, key, signed_in=3 * DAY, last_used=MINUTE, key_left=HOUR)
        monkeypatch.setattr(settings, "session_ttl_seconds", self.TWO_DAYS_S)

        resp = await client.get(f"{BASE}/auth/me")

        assert resp.status_code == 401, resp.text
        assert resp.json()["type"] == "/problems/authentication-required"
        assert await redis.exists(key) == 0
        assert await redis.smembers(index) == set()

    async def test_a_session_still_inside_the_new_value_is_kept_and_a_refresh_stops_at_it(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        # Signed in 40 hours ago: eight hours of the new two days are left, and thirteen days
        # of the fourteen it was minted with. A refresh is due.
        before = await _age(
            redis, key, signed_in=40 * HOUR, last_used=2 * HOUR, key_left=30 * MINUTE
        )
        monkeypatch.setattr(settings, "session_ttl_seconds", self.TWO_DAYS_S)

        assert await _me(client) == [200]

        eight_hours_ms = 8 * 3600 * 1000
        assert eight_hours_ms - FRESH_MS <= await redis.pttl(key) <= eight_hours_ms, (
            "the refresh ran the key past the lowered lifetime"
        )
        after = SessionRecord.from_json(await redis.get(key))
        assert after.expires_at == before.expires_at, "a refresh moves the end it was minted with"
        assert _used(after) > _used(before)

    async def test_the_session_is_refused_the_minute_the_new_value_runs_out(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        monkeypatch.setattr(settings, "session_ttl_seconds", self.TWO_DAYS_S)

        await _age(redis, key, signed_in=2 * DAY - MINUTE, last_used=MINUTE, key_left=HOUR)
        assert await _me(client) == [200]
        await _age(redis, key, signed_in=2 * DAY + MINUTE, last_used=MINUTE, key_left=HOUR)
        assert await _me(client) == [401]
        assert await redis.exists(key) == 0

    async def test_raising_the_lifetime_never_lengthens_a_session_past_the_end_it_was_minted_with(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        # Minted under 14 days and a minute past them; the operator then raises the setting to 30.
        await _age(redis, key, signed_in=ABSOLUTE + MINUTE, last_used=MINUTE, key_left=HOUR)
        monkeypatch.setattr(settings, "session_ttl_seconds", 30 * 24 * 3600)

        assert await _me(client) == [401]
        assert await redis.exists(key) == 0

    async def test_a_lifetime_too_long_to_add_to_a_date_is_no_limit_and_no_error(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A slip of units (microseconds typed as seconds). Sign-in would fail on it, as it always
        did; a request from a session that is already open must not turn into a 500 as well. The
        end it was minted with governs."""
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        before = await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=HOUR)
        monkeypatch.setattr(settings, "session_ttl_seconds", 10**13)

        assert await _me(client) == [200]

        after = SessionRecord.from_json(await redis.get(key))
        assert _used(after) > _used(before)
        assert IDLE_MS - FRESH_MS <= await redis.pttl(key) <= IDLE_MS


# --- 5. the interval ------------------------------------------------------------------------


class TestTheRefreshInterval:
    async def test_a_session_refreshed_a_moment_ago_makes_no_write(
        self, client: AsyncClient, redis: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _sign_up(client)
        seen = _spy_on_redis(monkeypatch)

        assert await _me(client, 6) == [200] * 6

        assert _writes(seen) == [], seen
        # The spy does see the reads: one per request, so a count of zero writes means something.
        assert [args[0] for args in seen].count("GET") == 6

    async def test_a_session_that_is_due_makes_one_write_and_the_requests_after_it_make_none(
        self, client: AsyncClient, redis: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=5 * HOUR, last_used=HOUR + MINUTE, key_left=71 * HOUR)
        seen = _spy_on_redis(monkeypatch)

        assert await _me(client, 6) == [200] * 6

        writes = _writes(seen)
        assert len(writes) == 1, writes
        assert str(writes[0][0]).upper() == "SET"

    async def test_the_one_write_is_a_set_xx_on_the_session_key_alone(
        self, client: AsyncClient, redis: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`XX`: only if the key still exists, in one atomic command. That is what lets a revoke
        that lands between the read and the write win (group 6)."""
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        seen = _spy_on_redis(monkeypatch)

        assert await _me(client) == [200]

        (write,) = _writes(seen)
        assert write[0] == "SET" and write[1] == key
        assert "XX" in write and "NX" not in write
        assert "PX" in write
        px = int(write[write.index("PX") + 1])
        assert IDLE_MS - FRESH_MS <= px <= IDLE_MS

    @pytest.mark.parametrize(
        ("interval_s", "last_used", "writes"),
        [
            (600, 9 * MINUTE, 0),
            (600, 11 * MINUTE, 1),
            (86_400, 23 * HOUR, 0),
            (86_400, 25 * HOUR, 1),
        ],
    )
    async def test_the_interval_setting_moves_when_a_write_is_due(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch, interval_s: int, last_used: timedelta, writes: int,
    ) -> None:
        monkeypatch.setattr(settings, "session_touch_interval_seconds", interval_s)
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=30 * HOUR, last_used=last_used, key_left=40 * HOUR)
        seen = _spy_on_redis(monkeypatch)

        assert await _me(client) == [200]

        assert len(_writes(seen)) == writes, (interval_s, last_used, seen)

    async def test_a_request_whose_refresh_is_due_sends_no_session_cookie(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """The cookie is not renewed by use: its Max-Age is set when it is minted and runs to the
        session's absolute end. A refresh moves the server's idle clock and nothing the browser
        holds."""
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        cookie = _token(client, settings)

        for path in ("/auth/me", "/dashboard", "/clients"):
            before = await _age(
                redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR
            )

            resp = await client.get(f"{BASE}{path}")

            assert resp.status_code == 200, (path, resp.text)
            # The refresh really was made, so this is the due case and not a quiet one.
            assert _used(SessionRecord.from_json(await redis.get(key))) > _used(before), path
            assert resp.headers.get_list("set-cookie") == [], (path, resp.headers)
            assert _token(client, settings) == cookie

    async def test_a_refresh_touches_the_one_session_and_not_the_users_other_one(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        await _sign_up(client)
        first = _token(client, settings)
        again = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert again.status_code == 200, again.text
        second = _token(client, settings)
        assert first != second
        key_first, key_second = (
            f"{sessions.SESSION_PREFIX}{digest}"
            for digest in (token_digest(first), token_digest(second))
        )
        await _age(redis, key_first, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        other = await _age(
            redis, key_second, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR
        )
        other_json = await redis.get(key_second)
        other_ttl = await redis.pttl(key_second)

        client.cookies.clear()
        client.cookies.set(settings.session_cookie_key, first)
        assert await _me(client) == [200]

        assert await redis.get(key_second) == other_json
        assert 0 < await redis.pttl(key_second) <= other_ttl
        assert _used(SessionRecord.from_json(await redis.get(key_first))) > _used(other)


# --- 6. a revoke wins -----------------------------------------------------------------------


class TestARevokeBetweenTheReadAndTheRefreshWins:
    async def test_a_revoke_leaves_the_session_revoked(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        await _sign_up(client)
        token = _token(client, settings)
        key, index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        store = SessionStore(redis, settings)

        record = await store.get(token)  # the read
        assert record is not None
        await store.revoke(token)  # a sign-out lands here
        assert await store.touch(token, record) is False  # the write

        assert await redis.exists(key) == 0, "the refresh brought a revoked session back"
        assert await redis.smembers(index) == set()
        assert await store.get(token) is None

    async def test_a_session_whose_ttl_ended_between_the_read_and_the_refresh_stays_gone(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        await _sign_up(client)
        token = _token(client, settings)
        key, _index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        store = SessionStore(redis, settings)
        record = await store.get(token)
        assert record is not None

        await redis.delete(key)  # what the TTL does at that moment

        assert await store.touch(token, record) is False
        assert await redis.exists(key) == 0

    async def test_a_sign_out_everywhere_inside_a_request_leaves_the_session_revoked(
        self, client: AsyncClient, redis: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The request was let in at its start, and the revoke lands before its refresh writes:
        the request goes on, and the session is gone for the next one."""
        await _sign_up(client)
        key, index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        real = SessionStore.touch

        async def _revoked_first(self: SessionStore, token: str, record: SessionRecord) -> bool:
            await self.revoke_all_for_user(record.user_id)
            return await real(self, token, record)

        with monkeypatch.context() as patch:
            patch.setattr(SessionStore, "touch", _revoked_first)
            assert await _me(client) == [200]

        assert await redis.exists(key) == 0, "the refresh brought a revoked session back"
        assert await redis.smembers(index) == set()
        assert await _me(client) == [401]


# --- 7. sessions that exist at the deploy ---------------------------------------------------


class TestSessionsThatExistAtTheDeploy:
    async def _as_production_holds_it(
        self, client: AsyncClient, redis: Redis, *, signed_in: timedelta, left: timedelta
    ) -> str:
        """A session in today's shape: four fields, the absolute end as `expires_at`, and a key
        whose TTL runs to that end (what the code before this change wrote)."""
        await _sign_up(client)
        key, _index, record = await _the_session(redis)
        now = datetime.now(UTC)
        old = {
            "user_id": record.user_id,
            "agency_id": record.agency_id,
            "created_at": (now - signed_in).isoformat(),
            "expires_at": (now + left).isoformat(),
        }
        await redis.set(key, json.dumps(old), px=int(left.total_seconds() * 1000))
        return key

    async def test_an_old_shape_session_is_let_in_and_its_idle_clock_starts_at_that_request(
        self, client: AsyncClient, redis: Redis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Signed in five days ago: 120 hours, far past the 72-hour window. It is NOT idle since
        sign-in; it is treated as used at its first request after the deploy."""
        key = await self._as_production_holds_it(
            client, redis, signed_in=5 * DAY, left=9 * DAY
        )
        before = SessionRecord.from_json(await redis.get(key))
        assert before.last_used_at is None
        seen = _spy_on_redis(monkeypatch)

        assert await _me(client) == [200]

        (write,) = _writes(seen)
        assert write[0] == "SET" and write[1] == key and "XX" in write
        after = SessionRecord.from_json(await redis.get(key))
        assert after.last_used_at is not None
        assert abs(after.last_used_at - datetime.now(UTC)) < timedelta(seconds=30)
        assert (after.created_at, after.expires_at) == (before.created_at, before.expires_at)
        # Nine days were left on the key; it now has the idle window.
        assert IDLE_MS - FRESH_MS <= await redis.pttl(key) <= IDLE_MS
        # And from here on it is a session like any other: the next requests make no write.
        seen.clear()
        assert await _me(client, 3) == [200] * 3
        assert _writes(seen) == []

    async def test_an_old_shape_session_with_little_of_its_fourteen_days_left_gets_no_more(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        key = await self._as_production_holds_it(
            client, redis, signed_in=ABSOLUTE - HOUR, left=HOUR
        )

        assert await _me(client) == [200]

        assert 0 < await redis.pttl(key) <= 3_600_000

    async def test_an_old_shape_session_past_its_absolute_end_is_still_refused(
        self, client: AsyncClient, redis: Redis
    ) -> None:
        await _sign_up(client)
        key, index, record = await _the_session(redis)
        now = datetime.now(UTC)
        old = {
            "user_id": record.user_id,
            "agency_id": record.agency_id,
            "created_at": (now - ABSOLUTE - HOUR).isoformat(),
            "expires_at": (now - HOUR).isoformat(),
        }
        await redis.set(key, json.dumps(old), px=int(HOUR.total_seconds() * 1000))

        assert await _me(client) == [401]
        assert await redis.exists(key) == 0
        assert await redis.smembers(index) == set()


# --- 8. a refresh that fails, a refused request, reconfirm ---------------------------------


class TestWhoRefreshesAndWhatHappensWhenItFails:
    async def test_a_refresh_redis_refuses_is_logged_and_the_request_goes_on(
        self, client: AsyncClient, settings: Settings, redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A refresh only keeps a session alive, so one that fails (Redis full under `noeviction`)
        must not turn a request that was let in into an error. It is logged without anything about
        the session, and the next request, once Redis answers, makes it."""
        await _sign_up(client)
        token = _token(client, settings)
        key, _index, _ = await _the_session(redis)
        before = await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        real_set = Redis.set

        def _refuse_the_refresh(self: Redis, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("xx"):
                async def _full() -> None:
                    raise ResponseError("OOM command not allowed when used memory > 'maxmemory'.")

                return _full()
            return real_set(self, *args, **kwargs)

        with monkeypatch.context() as patch, structlog.testing.capture_logs() as logs:
            patch.setattr(Redis, "set", _refuse_the_refresh)
            assert await _me(client) == [200]

        failed = [entry for entry in logs if entry["event"] == "session.touch_failed"]
        assert len(failed) == 1 and set(failed[0]) == {"event", "error", "log_level"}, logs
        assert failed[0]["error"] == "ResponseError" and failed[0]["log_level"] == "warning"
        said = repr(failed)
        for private in (token, token_digest(token), before.user_id, EMAIL, "OOM"):
            assert private not in said, "the log line carries something about the session"
        assert SessionRecord.from_json(await redis.get(key)) == before
        # Once Redis answers, the next request makes the refresh that was missed.
        assert await _me(client) == [200]
        refreshed = SessionRecord.from_json(await redis.get(key))
        assert _used(refreshed) > _used(before)

    async def test_a_request_that_is_refused_refreshes_nothing(
        self, client: AsyncClient, redis: Redis, session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only a caller who is actually let in refreshes the clock: a suspended user's request is
        answered 401 and their session removed, with no write that keeps it alive in between."""
        await _sign_up(client)
        key, _index, _ = await _the_session(redis)
        await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        await session.execute(
            update(User).where(User.email == EMAIL).values(status=UserStatus.SUSPENDED)
        )
        await session.commit()
        seen = _spy_on_redis(monkeypatch)

        assert await _me(client) == [401]

        assert await redis.exists(key) == 0
        assert [args[0] for args in _writes(seen)] == ["DEL", "SREM"], seen

    async def test_reconfirming_a_caller_under_a_lock_does_not_refresh_the_clock(
        self, client: AsyncClient, settings: Settings, redis: Redis, session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`reconfirm_principal` only reads (3.124): it asks again of the session store, it does
        not refresh it."""
        await _sign_up(client)
        token = _token(client, settings)
        key, _index, _ = await _the_session(redis)
        before = await _age(redis, key, signed_in=5 * HOUR, last_used=2 * HOUR, key_left=70 * HOUR)
        user = (await session.execute(select(User))).scalar_one()
        agency = (await session.execute(select(Agency))).scalar_one()
        principal = Principal(user=user, agency=agency, session_token=token)
        seen = _spy_on_redis(monkeypatch)

        await reconfirm_principal(
            session, SessionStore(redis, settings), principal, UserRole.OWNER
        )

        assert _writes(seen) == []
        assert SessionRecord.from_json(await redis.get(key)) == before

"""A credential version checked on every request, and rotation at sign-in — A3 part 3 (3.125).

Until now a person's sessions ended only if Redis said so: `revoke_all_for_user` reads the list
`user-sessions:{uid}` and deletes what it names. That list can be evicted, a revoke can fail, and a
session can be minted between the two revokes of a change (launch checklist 3.125: a Google link
whose second revoke fails). `users.sessions_valid_after` is the backstop that does not depend on any
of it. `deps.current_principal` (and `reconfirm_principal`) refuse, as an expired session is
refused, a session signed in BEFORE that instant, and revoke it; the places that end a person's
sessions set it, from the app's clock and inside the transaction of the change, after their Redis
revoke (`services.auth.set_session_cutoff`). Every Redis revoke stays. Since the security review's
M1 a seat's removal sets it too, and so does the invitation that revives a removed seat: the same
user id comes back, and its old sessions must not.

And a sign-in that follows a person's credentials (password login, both steps of Google sign-in)
ends the session the browser carried, before it makes the new one, so no older session survives on
that browser.

What this file holds:

1. **The check.** A session signed in before the cutoff is a 401 and is revoked, with an expired
   session's own answer; one signed in after it, or at that very instant, works; a user with no
   cutoff is untouched; a refused session does not refresh its idle clock; the check adds no query.
2. **The writers.** Sign out everywhere, a password change, a reset confirm: each ends the sessions
   the Redis list does not hold (a list evicted; a revoke that finds nothing; a session minted
   between the two revokes with the second failing), the changer keeps a session that is newer than
   the cutoff, and a refused attempt sets nothing. What the cutoff cannot catch, and what does.
3. **Google link (3.125).** A session minted between the link's two revokes dies when the second
   fails; a link ends a session the list does not hold; an invited seat's link sets it too.
4. **The operator's reset script.** It sets the cutoff after the Redis revoke and after the mint
   (the user row is written after the reset-link rows, the order `confirm_reset` takes them in),
   also when the mint is refused, and sets nothing when Redis cannot be reached.
5. **Rotation.** A login or Google sign-in that carries a session ends it, whoever's it was, only
   once the credentials are accepted, before the mint (so it does not cost another device its
   place under the cap), and a record the server cannot read does not turn the sign-in into an
   error. Sign-up and an invitation accept do not rotate.
6. **`reconfirm_principal`.** A request that waited for the agency's lock and whose session was cut
   off meanwhile is refused as its next request would be.
7. **The cutoff itself.** The app's clock, at the call; forward only; the caller's transaction; one
   user's; and never in a response.
8. **Removing a seat and inviting the address again (the review's M1).** A removal whose Redis
   revoke found nothing cannot be undone by inviting again, a session signed in after the removal
   dies at the revival, and a removal reversed in SQL does not bring the old session back. The
   removal writes the cutoff in the one UPDATE of the user row, which stays the last write.
9. **The read-backs after minting take a share lock (the review's L1).** A login, a password change
   and an invitation accept that read their row back while a credential change is in flight, after
   it wrote its cutoff and before it commits, wait for it and are refused, though the revoke after
   that commit never happens or fails.

`tests/` is not a package, so the helpers other session tests share are written out again.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
import io
import secrets
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
import structlog
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings, get_settings
from avp_api.deps import google_provider
from avp_api.models import Invitation, PasswordResetToken, User, UserStatus
from avp_api.schemas.auth import UserOut
from avp_api.security import token_digest
from avp_api.services import auth as auth_service
from avp_api.services import password_reset as reset_service
from avp_api.services import seats
from avp_api.services.google_oauth import GoogleExchangeError, GoogleIdentity
from avp_api.sessions import SESSION_PREFIX, USER_SESSIONS_PREFIX, SessionRecord, SessionStore

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
THIRD_PASSWORD = "a-third-passphrase-entirely"
EMAIL = "cutoff@session.example"
OTHER = "other@session.example"
GONE = "Session is expired or invalid."
WEB = "http://localhost:3000"

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "issue_password_reset.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("issue_password_reset_for_cutoff", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


# --- helpers --------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def redis(client: AsyncClient, settings: Settings) -> Redis:
    """The suite's test Redis; `client` is asked for so the app is wired to it."""
    return redis_module.get_redis(settings)


@pytest.fixture
def store(redis: Redis, settings: Settings) -> SessionStore:
    return SessionStore(redis, settings)


def _db(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _sign_up(
    client: AsyncClient, email: str = EMAIL, agency: str = "Cutoff Agency"
) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": agency, "fullName": "Operator", "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(
    client: AsyncClient, email: str = EMAIL, password: str = PASSWORD
) -> Response:
    """Sign in with whatever cookie the client holds right now (that is the point of rotation)."""
    return await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


def _token(client: AsyncClient, settings: Settings) -> str:
    token = client.cookies.get(settings.session_cookie_key)
    assert token, "the client holds no session cookie"
    return token


async def _me(client: AsyncClient, settings: Settings, token: str) -> Response:
    """`GET /auth/me` carrying exactly this session cookie and no other."""
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_key, token)
    return await client.get(f"{BASE}/auth/me")


def _carry(client: AsyncClient, settings: Settings, token: str) -> None:
    """Make the browser carry exactly this session cookie, as the one a sign-in's answer replaces.

    On the domain a response's own cookie gets (the test server's), so a sign-in that sets a cookie
    replaces it. A cookie set with no domain would stay beside it, and the client could no longer
    say which one it holds.
    """
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_key, token, domain="testserver.local", path="/")


async def _statuses(client: AsyncClient, settings: Settings, tokens: list[str]) -> list[int]:
    return [(await _me(client, settings, token)).status_code for token in tokens]


async def _alive(redis: Redis, token: str) -> bool:
    """Is there a record for this token in Redis? Read from Redis itself, not through the store."""
    return bool(await redis.exists(f"{SESSION_PREFIX}{token_digest(token)}"))


async def _indexed(redis: Redis, user_id: str, token: str) -> bool:
    return bool(await redis.sismember(f"{USER_SESSIONS_PREFIX}{user_id}", token_digest(token)))


async def _record(redis: Redis, token: str) -> SessionRecord:
    raw = await redis.get(f"{SESSION_PREFIX}{token_digest(token)}")
    assert raw is not None
    return SessionRecord.from_json(raw)


async def _user_id(engine: AsyncEngine, email: str) -> str:
    async with _db(engine)() as s:
        return (await s.execute(select(User.id).where(User.email == email))).scalar_one()


async def _cutoff(engine: AsyncEngine, user_id: str) -> datetime | None:
    async with _db(engine)() as s:
        return (
            await s.execute(select(User.sessions_valid_after).where(User.id == user_id))
        ).scalar_one()


async def _end_sessions(engine: AsyncEngine, user_id: str) -> None:
    """What every writer does, on its own: set the cutoff and commit."""
    async with _db(engine)() as s:
        await auth_service.set_session_cutoff(s, user_id)
        await s.commit()


async def _write_cutoff(engine: AsyncEngine, user_id: str, value: datetime | None) -> None:
    """Put an exact value in the column, as a test needs it (the helper never writes backwards)."""
    async with _db(engine)() as s:
        await s.execute(update(User).where(User.id == user_id).values(sessions_valid_after=value))
        await s.commit()


async def _reset_token(engine: AsyncEngine, email: str) -> str:
    """A live reset link's token for the user (the API never returns one: a test writes its own)."""
    raw = "cutoff-reset-" + secrets.token_hex(16)
    async with _db(engine)() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET),
                user_id=user.id,
                token_digest=token_digest(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()
    return raw


async def _another_device(store: SessionStore, user_id: str, agency_id: str) -> str:
    """A session on a device we do not hold: minted straight into Redis. Returns its token."""
    token, _ = await store.create(user_id=user_id, agency_id=agency_id)
    return token


@contextlib.contextmanager
def _statements(engine: AsyncEngine) -> Iterator[list[str]]:
    """Every SQL statement the engine runs while the block is open."""
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:  # noqa: ARG001
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


# --- 1. the check ---------------------------------------------------------------------------


class TestTheCheck:
    async def test_a_session_signed_in_before_the_cutoff_is_a_401_and_is_revoked(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings, redis: Redis
    ) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        token = _token(client, settings)
        assert (await _me(client, settings, token)).status_code == 200
        assert await _alive(redis, token)

        await _end_sessions(engine, user_id)

        refused = await _me(client, settings, token)
        assert refused.status_code == 401, refused.text
        assert refused.json()["type"] == "/problems/authentication-required"
        assert refused.json()["detail"] == GONE
        # Revoked, not merely refused: the record and its index entry are gone from Redis itself.
        assert not await _alive(redis, token)
        assert not await _indexed(redis, user_id, token)

    async def test_the_refusal_is_the_answer_an_expired_session_gets(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        token = _token(client, settings)
        unknown = await _me(client, settings, "not-a-session-" + "x" * 30)
        await _end_sessions(engine, me["user"]["id"])
        cut = await _me(client, settings, token)

        assert unknown.status_code == cut.status_code == 401
        for field in ("type", "title", "detail", "status"):
            assert cut.json()[field] == unknown.json()[field], field

    async def test_a_session_signed_in_after_the_cutoff_works(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        await _end_sessions(engine, me["user"]["id"])

        client.cookies.clear()
        assert (await _login(client)).status_code == 200
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    async def test_the_cutoff_refuses_only_strictly_before_it(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings, redis: Redis
    ) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        token = _token(client, settings)
        signed_in = (await _record(redis, token)).created_at

        await _write_cutoff(engine, user_id, signed_in)  # the very instant it was signed in
        assert (await _me(client, settings, token)).status_code == 200
        await _write_cutoff(engine, user_id, signed_in + timedelta(microseconds=1))
        assert (await _me(client, settings, token)).status_code == 401

    async def test_a_user_with_no_cutoff_is_unaffected(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        first = _token(client, settings)
        client.cookies.clear()
        assert (await _login(client)).status_code == 200
        second = _token(client, settings)

        assert await _cutoff(engine, user_id) is None  # sign-up and login never write it
        assert await _statuses(client, settings, [first, second]) == [200, 200]
        assert await _cutoff(engine, user_id) is None  # and neither does use

    async def test_a_cut_off_session_does_not_refresh_its_idle_clock(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        me = await _sign_up(client)
        token = _token(client, settings)
        touched: list[str] = []
        real = SessionStore.touch

        async def counting(self: SessionStore, tok: str, record: SessionRecord) -> bool:
            touched.append(tok)
            return await real(self, tok, record)

        monkeypatch.setattr(SessionStore, "touch", counting)

        assert (await _me(client, settings, token)).status_code == 200
        assert touched == [token]  # a session that is let in is a use

        await _end_sessions(engine, me["user"]["id"])
        assert (await _me(client, settings, token)).status_code == 401
        assert touched == [token]  # a refused one is not

    async def test_the_check_adds_no_query(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        token = _token(client, settings)

        with _statements(engine) as with_no_cutoff:
            assert (await _me(client, settings, token)).status_code == 200
        # A cutoff in the past: the session is newer than it, so it works, on the same statements.
        await _write_cutoff(engine, user_id, datetime.now(UTC) - timedelta(days=1))
        with _statements(engine) as with_a_cutoff:
            assert (await _me(client, settings, token)).status_code == 200
        assert with_a_cutoff == with_no_cutoff

        # And a refused request stops at the user row it already reads: one statement, no agency.
        await _end_sessions(engine, user_id)
        with _statements(engine) as refused:
            assert (await _me(client, settings, token)).status_code == 401
        assert len(refused) == 1, refused
        assert "FROM users" in refused[0]

    async def test_one_users_cutoff_ends_nobody_elses_session(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        first = await _sign_up(client, EMAIL)
        mine = _token(client, settings)
        client.cookies.clear()
        second = await _sign_up(client, OTHER, "Other Agency")
        theirs = _token(client, settings)

        await _end_sessions(engine, first["user"]["id"])

        assert await _statuses(client, settings, [mine, theirs]) == [401, 200]
        assert await _cutoff(engine, second["user"]["id"]) is None


# --- 2. the writers -------------------------------------------------------------------------


class TestSignOutEverywhere:
    async def test_it_ends_sessions_even_when_the_redis_list_is_gone(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        store: SessionStore,
    ) -> None:
        """The addendum: sign out everywhere no longer depends on the user index."""
        me = await _sign_up(client)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        mine = _token(client, settings)
        elsewhere = await _another_device(store, user_id, agency_id)
        await redis.delete(f"{USER_SESSIONS_PREFIX}{user_id}")  # evicted: the revoke finds nothing
        before = datetime.now(UTC)

        client.cookies.clear()
        client.cookies.set(settings.session_cookie_key, mine)
        out = await client.post(f"{BASE}/auth/logout-all")
        assert out.status_code == 204, out.text

        # Both records are still in Redis, which the revoke never reached ...
        assert await _alive(redis, mine) and await _alive(redis, elsewhere)
        # ... and both are refused: the cutoff did it.
        assert await _statuses(client, settings, [mine, elsewhere]) == [401, 401]
        cutoff = await _cutoff(engine, user_id)
        assert cutoff is not None and before <= cutoff <= datetime.now(UTC)

    async def test_it_ends_this_session_and_the_others_when_the_list_is_whole(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        store: SessionStore,
    ) -> None:
        me = await _sign_up(client)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        mine = _token(client, settings)
        elsewhere = await _another_device(store, user_id, agency_id)

        assert (await client.post(f"{BASE}/auth/logout-all")).status_code == 204

        assert not await _alive(redis, mine) and not await _alive(redis, elsewhere)
        assert await _cutoff(engine, user_id) is not None
        client.cookies.clear()
        assert (await _login(client)).status_code == 200  # a new sign-in is after the cutoff

    async def test_a_redis_failure_changes_nothing(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        me = await _sign_up(client)

        async def refuse(self: SessionStore, user_id: str) -> int:
            raise RedisConnectionError("boom")

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", refuse)
        out = await client.post(f"{BASE}/auth/logout-all")

        assert out.status_code == 503, out.text
        assert await _cutoff(engine, me["user"]["id"]) is None  # nothing was committed
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200


class TestChangePassword:
    async def test_the_changer_stays_signed_in_and_the_others_end_though_the_revokes_find_nothing(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        me = await _sign_up(client)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        old = _token(client, settings)
        elsewhere = await _another_device(store, user_id, agency_id)

        async def finds_nothing(self: SessionStore, user_id: str) -> int:
            return 0  # both revokes: an evicted list, or a Redis that lost the index

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", finds_nothing)
        changed = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        )
        assert changed.status_code == 200, changed.text
        fresh = _token(client, settings)
        assert fresh not in (old, elsewhere)

        # The changer's browser works on the cookie the change gave it ...
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200
        # ... its replacement is NEWER than the cutoff, which is why ...
        cutoff = await _cutoff(engine, user_id)
        assert cutoff is not None
        assert (await _record(redis, fresh)).created_at > cutoff
        # ... and every session that was signed in before the change is refused, the one that made
        # the request included, though Redis still held all of them.
        assert await _alive(redis, old) and await _alive(redis, elsewhere)
        assert await _statuses(client, settings, [old, elsewhere]) == [401, 401]

    async def test_a_failing_second_revoke_leaves_a_session_minted_between_them_dead(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        me = await _sign_up(client)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        old = _token(client, settings)
        calls: list[int] = []
        late: list[str] = []
        real = SessionStore.revoke_all_for_user

        async def second_one_fails(self: SessionStore, uid: str) -> int:
            calls.append(1)
            if len(calls) == 2:
                raise RedisConnectionError("boom")
            ended = await real(self, uid)
            # Somebody signs in between the revoke and the commit.
            late.append(await _another_device(self, uid, agency_id))
            return ended

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", second_one_fails)
        changed = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        )
        # The password changed and committed; the second revoke failed, so the answer is a 503 ...
        assert changed.status_code == 503, changed.text
        assert len(calls) == 2
        # ... and the session minted between the two revokes is refused all the same.
        assert await _alive(redis, late[0])
        assert await _statuses(client, settings, [late[0], old]) == [401, 401]
        client.cookies.clear()
        assert (await _login(client, password=NEW_PASSWORD)).status_code == 200
        assert await _cutoff(engine, user_id) is not None

    async def test_a_wrong_current_password_sets_no_cutoff(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        refused = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": "not-the-password-at-all", "newPassword": NEW_PASSWORD},
        )
        assert refused.status_code == 401
        assert await _cutoff(engine, me["user"]["id"]) is None
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    async def test_a_session_signed_in_after_the_cutoff_is_left_to_the_revoke_after_the_commit(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """What the cutoff cannot catch, and what does: a session minted AFTER it, by a sign-in that
        checked the old password before the change committed, is newer than the cutoff. The revoke
        after the commit ends it (and so does the re-read that sign-in makes after minting)."""
        me = await _sign_up(client)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        calls: list[int] = []
        minted: list[SessionRecord] = []
        tokens: list[str] = []
        real = SessionStore.revoke_all_for_user

        async def mints_before_the_second(self: SessionStore, uid: str) -> int:
            calls.append(1)
            if len(calls) == 2:  # after the commit, before this revoke
                token, record = await self.create(user_id=uid, agency_id=agency_id)
                tokens.append(token)
                minted.append(record)
            return await real(self, uid)

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", mints_before_the_second)
        changed = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        )
        assert changed.status_code == 200, changed.text

        cutoff = await _cutoff(engine, user_id)
        assert cutoff is not None and minted[0].created_at > cutoff  # the cutoff does not cover it
        assert not await _alive(redis, tokens[0])  # the revoke after the commit did


class TestResetConfirm:
    async def test_a_reset_ends_every_session_though_the_revokes_find_nothing(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        me = await _sign_up(client)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        mine = _token(client, settings)
        elsewhere = await _another_device(store, user_id, agency_id)
        raw = await _reset_token(engine, EMAIL)

        async def finds_nothing(self: SessionStore, uid: str) -> int:
            return 0

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", finds_nothing)
        confirmed = await client.post(
            f"{BASE}/auth/reset-password/confirm", json={"token": raw, "newPassword": NEW_PASSWORD}
        )
        assert confirmed.status_code == 200, confirmed.text

        assert await _alive(redis, mine) and await _alive(redis, elsewhere)
        assert await _statuses(client, settings, [mine, elsewhere]) == [401, 401]
        # The person signs in with the new password, after the cutoff.
        client.cookies.clear()
        assert (await _login(client, password=NEW_PASSWORD)).status_code == 200
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    async def test_a_failing_second_revoke_leaves_a_session_minted_between_them_dead(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        me = await _sign_up(client)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        raw = await _reset_token(engine, EMAIL)
        calls: list[int] = []
        late: list[str] = []
        real = SessionStore.revoke_all_for_user

        async def second_one_fails(self: SessionStore, uid: str) -> int:
            calls.append(1)
            if len(calls) == 2:
                raise RedisConnectionError("boom")
            ended = await real(self, uid)
            late.append(await _another_device(self, uid, agency_id))
            return ended

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", second_one_fails)
        confirmed = await client.post(
            f"{BASE}/auth/reset-password/confirm", json={"token": raw, "newPassword": NEW_PASSWORD}
        )

        assert confirmed.status_code == 503, confirmed.text  # committed; the second revoke failed
        assert await _alive(redis, late[0])
        assert await _statuses(client, settings, [late[0]]) == [401]
        assert await _cutoff(engine, user_id) is not None

    async def test_a_refused_link_sets_no_cutoff(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        refused = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": "no-such-link-" + "z" * 32, "newPassword": NEW_PASSWORD},
        )
        assert refused.status_code == 400, refused.text
        assert await _cutoff(engine, me["user"]["id"]) is None
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200


# --- 3. the Google link (3.125) -------------------------------------------------------------


class FakeGoogle:
    """Hands back whatever identity the test filed under a code, echoing the minted nonce."""

    def __init__(self) -> None:
        self.identities: dict[str, GoogleIdentity] = {}
        self.last_nonce: str | None = None

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.last_nonce = nonce
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&fake=1"

    async def exchange(self, *, code: str, code_verifier: str) -> GoogleIdentity:
        identity = self.identities.get(code)
        if identity is None:
            raise GoogleExchangeError
        return replace(identity, nonce=self.last_nonce)


def _identity(email: str, sub: str) -> GoogleIdentity:
    return GoogleIdentity(
        sub=sub, email=email, email_verified=True, name="Dana Whitfield", nonce="<minted>"
    )


@pytest.fixture
def google(client: AsyncClient, settings: Settings) -> FakeGoogle:
    """A configured deployment with a fake Google behind it."""
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    fake = FakeGoogle()
    configured = settings.model_copy(
        update={
            "google_oauth_client_id": "test-client-id.apps.googleusercontent.com",
            "google_oauth_client_secret": "not-a-real-secret",
            "google_oauth_redirect_url": "http://localhost:8000/api/v1/auth/google/callback",
        }
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[google_provider] = lambda: fake
    return fake


async def _start(client: AsyncClient) -> str:
    resp = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


async def _callback(client: AsyncClient, *, code: str, state: str) -> Response:
    params = {"code": code, "state": state}
    return await client.get(
        f"{BASE}/auth/google/callback", params=params, follow_redirects=False
    )


def _lands_on(resp: Response, path: str) -> None:
    assert resp.status_code == 302, resp.text
    assert urlparse(resp.headers["location"]).path == path, resp.headers["location"]


def _ticket_of(resp: Response) -> str:
    assert resp.status_code == 302, resp.text
    (ticket,) = parse_qs(urlparse(resp.headers["location"]).fragment)["ticket"]
    return ticket


async def _google_account(
    client: AsyncClient, google: FakeGoogle, email: str, sub: str
) -> dict[str, Any]:
    """Create an account through Google (new address, then the agency name). The client keeps the
    session it was signed in with."""
    google.identities["code-new"] = _identity(email, sub)
    ticket = _ticket_of(await _callback(client, code="code-new", state=await _start(client)))
    done = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": ticket, "agencyName": "Google Agency", "fullName": "Dana Whitfield"},
    )
    assert done.status_code == 201, done.text
    return done.json()


class TestTheGoogleLink:
    async def test_a_session_minted_between_the_links_two_revokes_dies_when_the_second_fails(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """3.125. The squatter signs in with the password after the first revoke and before the
        link commits; the revoke after the commit fails. The session survived that, once."""
        email = "victim@cutoff.example"
        me = await _sign_up(client, email)
        user_id, agency_id = me["user"]["id"], me["agency"]["id"]
        google.identities["code-1"] = _identity(email, "sub-victim")
        calls: list[int] = []
        late: list[str] = []
        real = SessionStore.revoke_all_for_user

        async def second_one_fails(self: SessionStore, uid: str) -> int:
            calls.append(1)
            if len(calls) == 2:
                raise RedisConnectionError("boom")
            ended = await real(self, uid)
            late.append(await _another_device(self, uid, agency_id))
            return ended

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", second_one_fails)
        client.cookies.clear()
        resp = await _callback(client, code="code-1", state=await _start(client))

        # The link stands, and the sign-in ends as `unavailable`: no new session was made.
        assert resp.status_code == 302
        assert parse_qs(urlparse(resp.headers["location"]).query)["reason"] == ["unavailable"]
        assert len(calls) == 2
        async with _db(engine)() as s:
            row = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        assert row.google_sub == "sub-victim" and row.password_hash is None
        # The session minted in between is refused, though Redis still holds it.
        assert await _alive(redis, late[0])
        assert await _statuses(client, settings, [late[0]]) == [401]
        assert not await _alive(redis, late[0])  # and the refusal revoked it

    async def test_a_link_ends_sessions_the_redis_list_does_not_hold(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
    ) -> None:
        email = "victim2@cutoff.example"
        me = await _sign_up(client, email)
        user_id = me["user"]["id"]
        squatter = _token(client, settings)
        await redis.delete(f"{USER_SESSIONS_PREFIX}{user_id}")  # evicted: the revokes find nothing

        google.identities["code-1"] = _identity(email, "sub-victim2")
        client.cookies.clear()
        _lands_on(await _callback(client, code="code-1", state=await _start(client)), "/dashboard")

        assert await _alive(redis, squatter)  # Redis never heard of it
        assert (await _me(client, settings, squatter)).status_code == 401
        assert await _cutoff(engine, user_id) is not None

    async def test_activating_an_invited_seat_sets_the_cutoff_and_the_new_session_works(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        google: FakeGoogle,
        store: SessionStore,
    ) -> None:
        """Every link sets it, an invited seat's too, where it ends nothing but a stale session."""
        owner = await _sign_up(client, "owner@cutoff.example")
        agency_id = owner["agency"]["id"]
        invited = await client.post(
            f"{BASE}/agencies/{agency_id}/invitations", json={"email": "rae@cutoff.example"}
        )
        assert invited.status_code == 201, invited.text
        rae_id = await _user_id(engine, "rae@cutoff.example")
        stale = await _another_device(store, rae_id, agency_id)

        google.identities["code-1"] = _identity("rae@cutoff.example", "sub-rae")
        client.cookies.clear()
        # Adapted by design (3.126): the sign-in asks first and writes nothing, so the cutoff is
        # set by the answer, "Join".
        ticket = _ticket_of(await _callback(client, code="code-1", state=await _start(client)))
        assert await _cutoff(engine, rae_id) is None
        joined = await client.post(f"{BASE}/auth/google/join", json={"ticket": ticket})
        assert joined.status_code == 200, joined.text

        assert await _cutoff(engine, rae_id) is not None
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200  # signed in after it
        assert (await _me(client, settings, stale)).status_code == 401

    async def test_a_repeat_sign_in_of_a_linked_account_sets_no_cutoff(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        google: FakeGoogle,
    ) -> None:
        made = await _google_account(client, google, "linked@cutoff.example", "sub-linked")
        user_id = made["user"]["id"]
        first = _token(client, settings)
        client.cookies.clear()
        google.identities["code-2"] = _identity("linked@cutoff.example", "sub-linked")
        _lands_on(await _callback(client, code="code-2", state=await _start(client)), "/dashboard")

        assert await _cutoff(engine, user_id) is None
        assert (await _me(client, settings, first)).status_code == 200  # another device, left alone


# --- 4. the operator's reset script ---------------------------------------------------------


def _streams() -> tuple[io.StringIO, io.StringIO]:
    return io.StringIO(), io.StringIO()


class TestTheResetScript:
    async def test_it_ends_sessions_the_redis_list_does_not_hold(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        store: SessionStore,
    ) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        mine = _token(client, settings)
        await redis.delete(f"{USER_SESSIONS_PREFIX}{user_id}")  # evicted
        out, err = _streams()

        async with _db(engine)() as session:
            code = await script.issue(
                session, email=EMAIL, settings=settings, store=store, out=out, err=err
            )

        assert code == script.ISSUED, err.getvalue()
        assert await _alive(redis, mine)  # the script's Redis revoke never reached it
        assert (await _me(client, settings, mine)).status_code == 401
        assert await _cutoff(engine, user_id) is not None

    async def test_a_refused_mint_still_commits_the_cutoff(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The refusal says the sessions have ended, so they have."""
        me = await _sign_up(client)
        mine = _token(client, settings)

        async def another_reset_is_running(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(reset_service, "request_reset", another_reset_is_running)
        out, err = _streams()
        async with _db(engine)() as session:
            code = await script.issue(
                session, email=EMAIL, settings=settings, store=store, out=out, err=err
            )

        assert code == script.REFUSED
        assert "Its sessions have ended and no link was issued" in err.getvalue()
        assert await _cutoff(engine, me["user"]["id"]) is not None
        assert (await _me(client, settings, mine)).status_code == 401

    async def test_with_redis_down_it_writes_nothing(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        dead_client = Redis.from_url("redis://127.0.0.1:1/15", decode_responses=True)
        out, err = _streams()
        try:
            async with _db(engine)() as session:
                code = await script.issue(
                    session,
                    email=EMAIL,
                    settings=settings,
                    store=SessionStore(dead_client, settings),
                    out=out,
                    err=err,
                )
        finally:
            await dead_client.aclose()

        assert code == script.REDIS_UNREACHABLE
        assert await _cutoff(engine, me["user"]["id"]) is None
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    async def test_an_account_it_refuses_is_not_cut_off(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings, store: SessionStore
    ) -> None:
        me = await _sign_up(client)
        async with _db(engine)() as session:
            await session.execute(
                update(User).where(User.id == me["user"]["id"]).values(status=UserStatus.SUSPENDED)
            )
            await session.commit()
        out, err = _streams()
        async with _db(engine)() as session:
            code = await script.issue(
                session, email=EMAIL, settings=settings, store=store, out=out, err=err
            )

        assert code == script.REFUSED
        assert await _cutoff(engine, me["user"]["id"]) is None

    async def test_it_writes_the_user_row_after_the_reset_link_rows(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings, store: SessionStore
    ) -> None:
        """The review's L2. `confirm_reset` locks the link row and then writes the user row, so a
        script that wrote the user row first and the link rows after it could deadlock against a
        link being redeemed (`services/password_reset.py` records the same trap for a password
        change). The cutoff is the last write, after the links are retired and the new one is in."""
        me = await _sign_up(client)
        await _reset_token(engine, EMAIL)  # a live link, so that there is one to retire
        out, err = _streams()
        with _statements(engine) as seen:
            async with _db(engine)() as session:
                code = await script.issue(
                    session, email=EMAIL, settings=settings, store=store, out=out, err=err
                )

        assert code == script.ISSUED, err.getvalue()
        tables = [s.split()[1] for s in seen if s.lstrip().startswith("UPDATE")]
        assert tables == ["password_reset_tokens", "users"], tables
        assert await _cutoff(engine, me["user"]["id"]) is not None


# --- 5. rotation at sign-in -----------------------------------------------------------------


class TestRotationAtLogin:
    async def test_a_login_that_carries_a_session_ends_it(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        old = _token(client, settings)

        assert (await _login(client)).status_code == 200  # the jar still holds `old`
        new = _token(client, settings)

        assert new != old
        assert not await _alive(redis, old)
        assert not await _indexed(redis, user_id, old)
        assert await _alive(redis, new) and await _indexed(redis, user_id, new)
        assert len(await redis.smembers(f"{USER_SESSIONS_PREFIX}{user_id}")) == 1
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    async def test_it_ends_another_users_session_too(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """The cookie belongs to the browser, and the browser is being handed another person's."""
        first = await _sign_up(client, EMAIL)
        theirs = _token(client, settings)
        client.cookies.clear()
        second = await _sign_up(client, OTHER, "Other Agency")
        mine = _token(client, settings)
        other_device = await _another_device(
            SessionStore(redis, settings), second["user"]["id"], second["agency"]["id"]
        )

        _carry(client, settings, theirs)
        assert (await _login(client, OTHER)).status_code == 200
        new = _token(client, settings)

        assert not await _alive(redis, theirs)
        assert not await _indexed(redis, first["user"]["id"], theirs)
        # Only the carried session goes: the signing-in person's other sessions are left alone.
        assert await _alive(redis, mine) and await _alive(redis, other_device)
        assert await _alive(redis, new)

    async def test_a_refused_login_leaves_the_carried_session_alone(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        await _sign_up(client)
        old = _token(client, settings)

        refused = await _login(client, password="not-the-password-at-all")

        assert refused.status_code == 401
        assert await _alive(redis, old)
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    @pytest.mark.parametrize("carried", ["nothing", "made up", "ended"])
    async def test_a_login_with_no_usable_cookie_just_works(
        self, client: AsyncClient, settings: Settings, redis: Redis, carried: str
    ) -> None:
        await _sign_up(client)
        ended = _token(client, settings)
        await client.post(f"{BASE}/auth/logout")
        client.cookies.clear()
        if carried == "made up":
            _carry(client, settings, "not-a-session-" + "q" * 30)
        elif carried == "ended":
            _carry(client, settings, ended)

        resp = await _login(client)

        assert resp.status_code == 200, resp.text
        assert await _alive(redis, _token(client, settings))

    @pytest.mark.parametrize("damaged", ["not json at all", "[]", '{"agency_id": "agcy_x"}'])
    async def test_a_login_that_carries_a_record_the_server_cannot_read_still_signs_in(
        self, client: AsyncClient, settings: Settings, redis: Redis, damaged: str
    ) -> None:
        """The review's I2. Before rotation, signing in again replaced a bad cookie and got the
        person out of it. The record the cookie names is deleted though it cannot be read, nothing
        names the person's token or its digest in a log, and the entry it leaves in the person's
        list is dropped by the very sign-in that follows."""
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        old = _token(client, settings)
        await redis.set(f"{SESSION_PREFIX}{token_digest(old)}", damaged, px=3_600_000)

        with structlog.testing.capture_logs() as logs:
            resp = await _login(client)  # the jar still carries `old`

        assert resp.status_code == 200, resp.text
        new = _token(client, settings)
        assert new != old
        assert not await _alive(redis, old)
        assert await _alive(redis, new)
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200
        assert await redis.smembers(f"{USER_SESSIONS_PREFIX}{user_id}") == {token_digest(new)}
        shown = repr(logs)
        for secret in (old, token_digest(old)):
            assert secret not in shown

    async def test_it_comes_before_the_mint_so_it_does_not_cost_another_device_its_place(
        self,
        client: AsyncClient,
        settings: Settings,
        redis: Redis,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """At the cap of two, a browser holding one of the person's sessions signs in again. The
        session it replaces is freed first, so the person's OTHER device keeps its place; minted
        first, the cap would end the oldest session, which is that other device."""
        monkeypatch.setattr(settings, "session_max_per_user", 2)
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        browser = _token(client, settings)
        # An older session on another device: planted so that it is the oldest by sign-in time.
        record = await _record(redis, browser)
        older = "e" * 64
        planted = replace(
            record, created_at=record.created_at - timedelta(hours=1), last_used_at=None
        )
        await redis.set(f"{SESSION_PREFIX}{older}", planted.to_json(), px=3_600_000)
        await redis.sadd(f"{USER_SESSIONS_PREFIX}{user_id}", older)

        assert (await _login(client)).status_code == 200
        new = _token(client, settings)

        assert not await _alive(redis, browser)  # the replaced session
        assert await redis.exists(f"{SESSION_PREFIX}{older}")  # the other device, kept
        assert await _alive(redis, new)

    async def test_sign_up_does_not_end_the_session_the_browser_carried(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """Not in the brief, and said so: the answer's Set-Cookie replaces the browser's cookie, but
        the session it named stays valid on the server until it ends by itself or is revoked.
        (An invitation accept does the same.)"""
        await _sign_up(client, EMAIL)
        old = _token(client, settings)

        await _sign_up(client, OTHER, "Other Agency")  # the jar still held `old`

        assert _token(client, settings) != old
        assert await _alive(redis, old)


class TestRotationAtGoogleSignIn:
    async def test_a_repeat_google_sign_in_ends_the_session_the_browser_carried(
        self,
        client: AsyncClient,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
    ) -> None:
        made = await _google_account(client, google, "repeat@cutoff.example", "sub-repeat")
        user_id = made["user"]["id"]
        first = _token(client, settings)

        google.identities["code-2"] = _identity("repeat@cutoff.example", "sub-repeat")
        _lands_on(await _callback(client, code="code-2", state=await _start(client)), "/dashboard")
        second = _token(client, settings)

        assert second != first
        assert not await _alive(redis, first)
        assert not await _indexed(redis, user_id, first)
        assert await _alive(redis, second)
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    async def test_a_google_sign_in_that_carries_a_record_the_server_cannot_read_still_signs_in(
        self,
        client: AsyncClient,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
    ) -> None:
        """The review's I2, at the callback: the same helper, the same answer."""
        await _google_account(client, google, "damaged@cutoff.example", "sub-damaged")
        old = _token(client, settings)
        await redis.set(f"{SESSION_PREFIX}{token_digest(old)}", "not json at all", px=3_600_000)

        google.identities["code-2"] = _identity("damaged@cutoff.example", "sub-damaged")
        with structlog.testing.capture_logs() as logs:
            _lands_on(
                await _callback(client, code="code-2", state=await _start(client)), "/dashboard"
            )

        new = _token(client, settings)
        assert new != old
        assert not await _alive(redis, old) and await _alive(redis, new)
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200
        shown = repr(logs)
        for secret in (old, token_digest(old)):
            assert secret not in shown

    async def test_it_ends_another_users_session_and_leaves_the_persons_other_ones(
        self,
        client: AsyncClient,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
    ) -> None:
        made = await _google_account(client, google, "person@cutoff.example", "sub-person")
        persons_other_device = _token(client, settings)
        client.cookies.clear()
        stranger = await _sign_up(client, OTHER, "Other Agency")
        carried = _token(client, settings)

        google.identities["code-2"] = _identity("person@cutoff.example", "sub-person")
        _lands_on(await _callback(client, code="code-2", state=await _start(client)), "/dashboard")

        assert not await _alive(redis, carried)
        assert not await _indexed(redis, stranger["user"]["id"], carried)
        assert await _alive(redis, persons_other_device)
        assert await _alive(redis, _token(client, settings))
        assert made["user"]["id"] != stranger["user"]["id"]

    async def test_a_sign_in_that_goes_no_further_than_the_ticket_ends_nothing(
        self,
        client: AsyncClient,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
    ) -> None:
        """A new address only gets a ticket: no session is made, so none is replaced."""
        await _sign_up(client)
        old = _token(client, settings)

        google.identities["code-new"] = _identity("new-person@cutoff.example", "sub-new")
        _ticket_of(await _callback(client, code="code-new", state=await _start(client)))

        assert await _alive(redis, old)

    async def test_completing_a_new_account_ends_the_session_the_browser_carried(
        self,
        client: AsyncClient,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
    ) -> None:
        await _sign_up(client)
        old = _token(client, settings)

        made = await _google_account(client, google, "new-person@cutoff.example", "sub-new")
        new = _token(client, settings)

        assert new != old
        assert not await _alive(redis, old)
        assert await _alive(redis, new) and await _indexed(redis, made["user"]["id"], new)

    async def test_a_refused_completion_ends_nothing(
        self,
        client: AsyncClient,
        settings: Settings,
        redis: Redis,
        google: FakeGoogle,
    ) -> None:
        """A ticket that does not resolve is refused before any session is made."""
        await _sign_up(client)
        old = _token(client, settings)

        refused = await client.post(
            f"{BASE}/auth/google/complete",
            json={
                "ticket": "no-such-ticket-" + "t" * 30,
                "agencyName": "Refused Agency",
                "fullName": "Refused Person",
            },
        )

        assert refused.status_code == 400, refused.text
        assert await _alive(redis, old)


# --- 6. reconfirm_principal -----------------------------------------------------------------


@pytest.mark.parametrize("request_", ["removal", "invitation"])
async def test_a_session_cut_off_while_the_request_waits_for_the_lock_is_refused(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    request_: str,
) -> None:
    """The request is let in, then waits for the agency's lock; meanwhile the person's sessions are
    ended by the cutoff alone (Redis still holds the session). It is refused as its next request
    would be, and nothing is written."""
    me = await _sign_up(client)
    user_id, agency_id = me["user"]["id"], me["agency"]["id"]
    token = _token(client, settings)
    seat = None
    if request_ == "removal":
        invited = await client.post(
            f"{BASE}/agencies/{agency_id}/invitations", json={"email": "seat@cutoff.example"}
        )
        assert invited.status_code == 201, invited.text
        seat = invited.json()["user"]["id"]

    real = seats.lock_agency
    cut: list[int] = []

    async def lock_then_the_cutoff(session: AsyncSession, agency: str) -> Any:
        locked = await real(session, agency)
        if not cut:
            cut.append(1)
            await _end_sessions(engine, user_id)  # another request's commit, while this waited
        return locked

    monkeypatch.setattr(seats, "lock_agency", lock_then_the_cutoff)
    if request_ == "removal":
        resp = await client.delete(f"{BASE}/users/{seat}")
    else:
        resp = await client.post(
            f"{BASE}/agencies/{agency_id}/invitations", json={"email": "new@cutoff.example"}
        )

    assert cut, "the cutoff was never set while the request waited"
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == GONE
    assert "acceptUrl" not in resp.text
    async with _db(engine)() as s:
        if request_ == "removal":
            assert (
                await s.execute(select(User.deleted_at).where(User.id == seat))
            ).scalar_one() is None
        else:
            sent = select(Invitation).where(Invitation.email == "new@cutoff.example")
            assert (await s.execute(sent)).first() is None
    # `reconfirm_principal` only reads: the caller's next request is what revokes the session.
    assert await _alive(redis, token)
    assert (await _me(client, settings, token)).status_code == 401
    assert not await _alive(redis, token)


# --- 7. the cutoff itself -------------------------------------------------------------------


class TestTheCutoffItself:
    async def test_it_is_the_apps_clock_at_the_call_and_not_the_transactions_start(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`SessionStore.create` stamps `created_at` from the app's clock. A cutoff from the
        database's `now()` is the START of the transaction and could sit before a session that was
        minted since, so the helper takes its instant from the same clock, when it is called."""
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        fixed = datetime(2031, 5, 17, 9, 30, 15, 123456, tzinfo=UTC)

        class Frozen(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # noqa: ARG003
                return fixed

        monkeypatch.setattr(auth_service, "datetime", Frozen)
        await _end_sessions(engine, user_id)

        assert await _cutoff(engine, user_id) == fixed

    async def test_it_only_moves_forward(self, client: AsyncClient, engine: AsyncEngine) -> None:
        """Two changes that overlap, or an instance whose clock runs behind, cannot bring a session
        back by writing an earlier instant."""
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        later = datetime.now(UTC) + timedelta(hours=1)

        await _write_cutoff(engine, user_id, later)
        await _end_sessions(engine, user_id)
        assert await _cutoff(engine, user_id) == later  # an earlier instant did not replace it

        earlier = datetime.now(UTC) - timedelta(hours=1)
        await _write_cutoff(engine, user_id, earlier)
        await _end_sessions(engine, user_id)
        moved = await _cutoff(engine, user_id)
        assert moved is not None and moved > earlier  # a later one does

    async def test_it_sets_a_null_cutoff(self, client: AsyncClient, engine: AsyncEngine) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        assert await _cutoff(engine, user_id) is None
        before = datetime.now(UTC)
        await _end_sessions(engine, user_id)
        cutoff = await _cutoff(engine, user_id)
        assert cutoff is not None and before <= cutoff <= datetime.now(UTC)

    async def test_it_is_in_the_callers_transaction_and_does_not_commit(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        me = await _sign_up(client)
        user_id = me["user"]["id"]
        async with _db(engine)() as s:
            await auth_service.set_session_cutoff(s, user_id)
            await s.rollback()  # the change did not commit

        assert await _cutoff(engine, user_id) is None

    async def test_it_belongs_to_one_user(self, client: AsyncClient, engine: AsyncEngine) -> None:
        first = await _sign_up(client, EMAIL)
        client.cookies.clear()
        second = await _sign_up(client, OTHER, "Other Agency")

        await _end_sessions(engine, first["user"]["id"])

        assert await _cutoff(engine, first["user"]["id"]) is not None
        assert await _cutoff(engine, second["user"]["id"]) is None

    async def test_no_response_carries_it(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        await _end_sessions(engine, me["user"]["id"])
        client.cookies.clear()
        login = await _login(client)
        me_again = await client.get(f"{BASE}/auth/me")

        for resp in (login, me_again):
            assert resp.status_code == 200, resp.text
            lowered = resp.text.lower()
            assert "sessionsvalidafter" not in lowered and "sessions_valid_after" not in lowered
        assert not [name for name in UserOut.model_fields if "session" in name.lower()]


# --- 8. removing a seat and inviting the address again (the review's M1) --------------------------
#
# A removed seat's row is revived, not replaced, when the address is invited again: the same user id
# comes back, and a session the removal's Redis revoke missed (it runs after the commit and can fail
# or find a list Redis evicted) would answer again once the new invitation is accepted. So the
# removal writes the cutoff, and so does the revival.

MEMBER = "member@seat.example"


@dataclass(frozen=True)
class SeatWorld:
    """An agency, its owner, and one member who accepted an invitation and holds a session."""

    agency_id: str
    owner: str  # the owner's session token
    member_id: str
    member: str  # the member's first session token


async def _invite_member(
    client: AsyncClient, engine: AsyncEngine, agency_id: str, salt: str
) -> str:
    """The signed-in owner the client carries invites MEMBER (a new row, a re-send, or a revival).

    The API never returns the link's token, so the test writes its own digest onto the live
    invitation. `salt` keeps two invitations in one test apart (the digest is unique).
    """
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": MEMBER, "role": "member"}
    )
    assert invited.status_code == 201, invited.text
    token = f"seat-token-{salt}"
    async with _db(engine)() as s:
        live = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == MEMBER,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        live.token_digest = token_digest(token)
        await s.commit()
    return token


async def _accept(client: AsyncClient, token: str, password: str) -> Response:
    client.cookies.clear()
    return await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Mem Ber", "password": password},
    )


async def _a_seat_with_a_session(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> SeatWorld:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    owner = _token(client, settings)
    token = await _invite_member(client, engine, agency_id, "first")
    accepted = await _accept(client, token, PASSWORD)
    assert accepted.status_code == 200, accepted.text
    return SeatWorld(agency_id, owner, accepted.json()["user"]["id"], _token(client, settings))


async def _remove_seat(client: AsyncClient, settings: Settings, world: SeatWorld) -> Response:
    _carry(client, settings, world.owner)
    return await client.delete(f"{BASE}/users/{world.member_id}")


async def _invite_again_and_accept(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, world: SeatWorld
) -> Response:
    _carry(client, settings, world.owner)
    token = await _invite_member(client, engine, world.agency_id, "second")
    return await _accept(client, token, NEW_PASSWORD)


class TestRemovingASeatAndInvitingTheAddressAgain:
    async def test_a_removal_whose_revoke_finds_nothing_does_not_wake_the_old_session(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fails with neither of the two cutoff writes, passes with either."""
        world = await _a_seat_with_a_session(client, engine, settings)

        async def finds_nothing(self: SessionStore, user_id: str) -> int:
            return 0  # the removal's revoke: failed, or a list Redis had evicted

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", finds_nothing)
        assert (await _remove_seat(client, settings, world)).status_code == 204
        assert await _alive(redis, world.member)  # the record is still in Redis, never presented

        accepted = await _invite_again_and_accept(client, engine, settings, world)
        assert accepted.status_code == 200, accepted.text
        fresh = _token(client, settings)

        assert (await _me(client, settings, fresh)).status_code == 200  # the new holder is in
        assert (await _me(client, settings, world.member)).status_code == 401  # the old one is not

    async def test_a_session_signed_in_after_the_removal_dies_when_the_seat_is_invited_again(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        store: SessionStore,
    ) -> None:
        """Needs the write in the revival: a sign-in that raced the removal is newer than the
        removal's own cutoff, so only the invitation that brings the row back can end it."""
        world = await _a_seat_with_a_session(client, engine, settings)
        assert (await _remove_seat(client, settings, world)).status_code == 204
        removed_at = await _cutoff(engine, world.member_id)
        assert removed_at is not None
        late, record = await store.create(user_id=world.member_id, agency_id=world.agency_id)
        assert record.created_at > removed_at  # newer than the removal's cutoff

        accepted = await _invite_again_and_accept(client, engine, settings, world)
        assert accepted.status_code == 200, accepted.text
        fresh = _token(client, settings)

        assert (await _me(client, settings, late)).status_code == 401
        assert (await _me(client, settings, fresh)).status_code == 200

    async def test_a_removal_reversed_in_sql_does_not_bring_the_old_session_back(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        redis: Redis,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Needs the write in the removal: nothing invites anyone here, so the row is just restored
        (an operator's UPDATE, a restore from a backup) and only the removal's cutoff is left."""
        world = await _a_seat_with_a_session(client, engine, settings)

        async def finds_nothing(self: SessionStore, user_id: str) -> int:
            return 0

        monkeypatch.setattr(SessionStore, "revoke_all_for_user", finds_nothing)
        assert (await _remove_seat(client, settings, world)).status_code == 204
        async with _db(engine)() as s:
            await s.execute(update(User).where(User.id == world.member_id).values(deleted_at=None))
            await s.commit()
        assert await _alive(redis, world.member)

        assert (await _me(client, settings, world.member)).status_code == 401
        # The person themselves is not locked out: a new sign-in is after the cutoff.
        client.cookies.clear()
        assert (await _login(client, MEMBER)).status_code == 200
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    async def test_the_removal_writes_the_cutoff_in_the_one_update_of_the_row(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        """Folded into the flush: the row is still written once, and last, after the invitations
        and the links (`test_seat_removal_races.py` holds the lock order to that)."""
        world = await _a_seat_with_a_session(client, engine, settings)

        with _statements(engine) as seen:
            assert (await _remove_seat(client, settings, world)).status_code == 204

        updates = [s for s in seen if s.lstrip().startswith("UPDATE users")]
        assert len(updates) == 1, updates
        assert "deleted_at" in updates[0] and "sessions_valid_after" in updates[0], updates[0]
        tables = [s.split()[1] for s in seen if s.lstrip().startswith("UPDATE")]
        assert tables[-1] == "users", tables

    async def test_only_a_revived_row_gets_a_cutoff_from_an_invitation_and_it_moves_forward(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        me = await _sign_up(client)
        agency_id = me["agency"]["id"]
        owner = _token(client, settings)

        first = await _invite_member(client, engine, agency_id, "first")  # a new address
        member_id = await _user_id(engine, MEMBER)
        assert await _cutoff(engine, member_id) is None
        again = await _invite_member(client, engine, agency_id, "resend")  # a re-send
        assert await _cutoff(engine, member_id) is None
        assert first != again

        world_member = (await _accept(client, again, PASSWORD)).json()["user"]["id"]
        assert world_member == member_id
        world = SeatWorld(agency_id, owner, member_id, _token(client, settings))
        assert (await _remove_seat(client, settings, world)).status_code == 204
        removed_at = await _cutoff(engine, member_id)
        assert removed_at is not None

        assert (await _invite_again_and_accept(client, engine, settings, world)).status_code == 200
        revived_at = await _cutoff(engine, member_id)
        assert revived_at is not None and revived_at > removed_at



# --- 9. the read-backs after minting take a share lock (the review's L1) --------------------------
#
# 3.125 is a race between a credential change and a password sign-in that checked the OLD state. The
# change writes the user row, takes its cutoff, and commits. A sign-in that mints after that cutoff
# and reads its row back before the commit saw the old hash, passed, and left a session newer than
# the cutoff; if the revoke after the commit then fails, nothing ends it. The read-back is now
# `FOR SHARE`: it waits for the change to commit and sees the new hash. Each test below puts the
# change in flight at the moment of the mint, after its cutoff and before its commit, and bounds
# every wait, so a lock that is missing fails the test and does not hang it.


async def _waiting_for_a_lock(engine: AsyncEngine) -> int:
    """How many backends of this database wait for a lock that another transaction holds."""
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        ).scalar_one()


async def _until(
    what: str, check: Callable[[], bool | Awaitable[bool]], *, seconds: float = 10.0
) -> None:
    """Poll until `check` holds, failing the test (and not hanging it) after `seconds`."""
    try:
        async with asyncio.timeout(seconds):
            while True:
                answer = check()
                if inspect.isawaitable(answer):
                    answer = await answer
                if answer:
                    return
                await asyncio.sleep(0.01)
    except TimeoutError:
        pytest.fail(f"timed out waiting for {what}")


class _HandWrittenChange:
    """A credential change the test writes by hand and keeps uncommitted.

    As a Google link does it: the password goes, a Google subject arrives, and the cutoff is written
    from the app's clock; the user row stays locked until `commit()`. There is no revoke after it
    (the case 3.125 is about: that revoke fails), so only what the sign-in does itself can end the
    session it minted.
    """

    def __init__(self, engine: AsyncEngine, user_id: str) -> None:
        self._engine = engine
        self._user_id = user_id
        self._conn: AsyncConnection | None = None
        self._trans: Any = None
        self.begun = False

    async def begin(self) -> None:
        self.begun = True
        self._conn = await self._engine.connect()
        self._trans = await self._conn.begin()
        await self._conn.execute(
            text("UPDATE users SET password_hash = NULL, google_sub = :sub WHERE id = :id"),
            {"sub": "sub-" + secrets.token_hex(4), "id": self._user_id},
        )
        await self._conn.execute(
            text(
                "UPDATE users SET sessions_valid_after = "
                "greatest(sessions_valid_after, :now) WHERE id = :id"
            ),
            {"now": datetime.now(UTC), "id": self._user_id},
        )

    async def commit(self) -> None:
        assert self._conn is not None
        await self._trans.commit()
        await self._conn.close()
        self._conn = None

    async def abandon(self) -> None:
        """Roll back whatever is still open, so a failed test leaves no lock behind."""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._trans.rollback()
            await self._conn.close()
            self._conn = None


@pytest.mark.parametrize("change", ["reset", "link"])
async def test_a_login_that_reads_back_while_a_change_is_in_flight_waits_for_it_and_is_refused(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    redis: Redis,
    google: FakeGoogle,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """3.125 as it was meant, with the real routes on both sides. The login checked the old
    password and minted after the change took its cutoff; the change then commits and the revoke
    after its commit fails. The login's read-back waits for the commit, sees the new state and
    refuses, and its session is revoked: it was newer than the cutoff, so nothing else would."""
    email = "racer@cutoff.example"
    await _sign_up(client, email)
    client.cookies.clear()  # whoever signs in below does it from a browser with no session
    elsewhere = AsyncClient(
        transport=ASGITransport(app=client._transport.app),  # type: ignore[attr-defined]
        base_url="http://testserver",
        headers={"Origin": client.headers["Origin"]},
    )
    raw = await _reset_token(engine, email) if change == "reset" else ""
    google.identities["code-1"] = _identity(email, "sub-racer")

    async def reset_confirm() -> Response:
        return await elsewhere.post(
            f"{BASE}/auth/reset-password/confirm", json={"token": raw, "newPassword": NEW_PASSWORD}
        )

    async def google_link() -> Response:
        return await _callback(elsewhere, code="code-1", state=await _start(elsewhere))

    the_change = reset_confirm if change == "reset" else google_link

    held, release = asyncio.Event(), asyncio.Event()
    real_cutoff = auth_service.set_session_cutoff

    async def pause_after_the_cutoff(session: AsyncSession, user_id: str) -> None:
        await real_cutoff(session, user_id)
        held.set()  # the change has written its cutoff and holds the user row
        await asyncio.wait_for(release.wait(), 30)

    revokes: list[int] = []
    real_revoke = SessionStore.revoke_all_for_user

    async def the_revoke_after_the_commit_fails(self: SessionStore, uid: str) -> int:
        revokes.append(1)
        if len(revokes) == 2:
            raise RedisConnectionError("boom")
        return await real_revoke(self, uid)

    real_create = SessionStore.create
    in_flight: list[asyncio.Task[Response]] = []
    minted: list[str] = []

    async def mint_while_the_change_holds_the_row(
        self: SessionStore, **kw: str
    ) -> tuple[str, SessionRecord]:
        if not in_flight:  # the login's mint is the first one
            in_flight.append(asyncio.create_task(the_change()))
            await asyncio.wait_for(held.wait(), 20)
        token, record = await real_create(self, **kw)
        minted.append(token)
        return token, record

    monkeypatch.setattr(auth_service, "set_session_cutoff", pause_after_the_cutoff)
    monkeypatch.setattr(SessionStore, "revoke_all_for_user", the_revoke_after_the_commit_fails)
    monkeypatch.setattr(SessionStore, "create", mint_while_the_change_holds_the_row)

    login = asyncio.create_task(_login(client, email))
    try:
        await _until(
            "the login to wait for the change in flight",
            lambda: login.done() or _waiting_for_a_lock(engine),
        )
        assert not login.done(), "the login read its row back without waiting for the change"
        release.set()
        changed = await asyncio.wait_for(in_flight[0], 20)
        refused = await asyncio.wait_for(login, 20)
    finally:
        release.set()
        await asyncio.gather(login, *in_flight, return_exceptions=True)
        await elsewhere.aclose()

    assert refused.status_code == 401, refused.text
    assert refused.json()["type"] == "/problems/invalid-credentials"
    assert len(revokes) == 2  # the change's revoke before its commit, and the one that failed
    if change == "reset":
        assert changed.status_code == 503, changed.text
    else:
        assert parse_qs(urlparse(changed.headers["location"]).query)["reason"] == ["unavailable"]
    # The login's session is gone: its read-back ended it, since the failed revoke could not.
    assert len(minted) == 1
    assert not await _alive(redis, minted[0])
    assert (await _me(client, settings, minted[0])).status_code == 401


async def test_a_password_change_that_reads_back_while_a_link_is_in_flight_waits_and_is_refused(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    me = await _sign_up(client)
    link = _HandWrittenChange(engine, me["user"]["id"])
    real_create = SessionStore.create
    minted: list[str] = []

    async def mint_while_the_link_holds_the_row(
        self: SessionStore, **kw: str
    ) -> tuple[str, SessionRecord]:
        if not link.begun:  # the replacement session is minted after the change's own commit
            await link.begin()
        token, record = await real_create(self, **kw)
        minted.append(token)
        return token, record

    monkeypatch.setattr(SessionStore, "create", mint_while_the_link_holds_the_row)
    request = asyncio.create_task(
        client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        )
    )
    try:
        await _until(
            "the password change to wait for the link in flight",
            lambda: request.done() or _waiting_for_a_lock(engine),
        )
        assert not request.done(), "the change read its row back without waiting for the link"
        await link.commit()
        refused = await asyncio.wait_for(request, 20)
    finally:
        await link.abandon()
        await asyncio.gather(request, return_exceptions=True)

    assert refused.status_code == 401, refused.text
    assert refused.json()["detail"] == "That is not your current password."
    assert len(minted) == 1 and not await _alive(redis, minted[0])


async def test_an_invitation_accept_that_reads_back_while_a_link_is_in_flight_waits_and_is_refused(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    me = await _sign_up(client)
    token = await _invite_member(client, engine, me["agency"]["id"], "first")
    link = _HandWrittenChange(engine, await _user_id(engine, MEMBER))
    real_create = SessionStore.create
    minted: list[str] = []

    async def mint_while_the_link_holds_the_row(
        self: SessionStore, **kw: str
    ) -> tuple[str, SessionRecord]:
        if not link.begun:  # the accept has committed; its session is minted next
            await link.begin()
        session_token, record = await real_create(self, **kw)
        minted.append(session_token)
        return session_token, record

    monkeypatch.setattr(SessionStore, "create", mint_while_the_link_holds_the_row)
    client.cookies.clear()
    request = asyncio.create_task(
        client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": token, "fullName": "Mem Ber", "password": PASSWORD},
        )
    )
    try:
        await _until(
            "the accept to wait for the link in flight",
            lambda: request.done() or _waiting_for_a_lock(engine),
        )
        assert not request.done(), "the accept read its row back without waiting for the link"
        await link.commit()
        refused = await asyncio.wait_for(request, 20)
    finally:
        await link.abandon()
        await asyncio.gather(request, return_exceptions=True)

    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == "/problems/invalid-invitation"
    assert len(minted) == 1 and not await _alive(redis, minted[0])


async def test_signing_in_and_changing_credentials_at_once_never_deadlocks(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    """What the share lock must not do. Five logins, a password change, a sign out everywhere and a
    reset confirm run at once against one account. Each login's read-back may wait for a change to
    commit, and a change may wait for a read-back to finish, but no wait is a cycle: a read-back
    holds no other row lock when it takes the share lock and asks for none after it. A deadlock
    would reach the app as an error (a 500 or an exception here), and any other answer is one of
    the honest outcomes of a race. The whole run is bounded."""
    await _sign_up(client, EMAIL)
    raw = await _reset_token(engine, EMAIL)
    app = client._transport.app  # type: ignore[attr-defined]

    def a_browser() -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        )

    browsers = [a_browser() for _ in range(8)]
    *logins, changer, leaver, resetter = browsers
    try:
        for signed_in in (changer, leaver):
            first = await signed_in.post(
                f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}
            )
            assert first.status_code == 200, first.text
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    b.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
                    for b in logins
                ),
                changer.post(
                    f"{BASE}/auth/change-password",
                    json={"currentPassword": PASSWORD, "newPassword": THIRD_PASSWORD},
                ),
                leaver.post(f"{BASE}/auth/logout-all"),
                resetter.post(
                    f"{BASE}/auth/reset-password/confirm",
                    json={"token": raw, "newPassword": NEW_PASSWORD},
                ),
                return_exceptions=True,
            ),
            60,
        )
    finally:
        for b in browsers:
            await b.aclose()

    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, errors
    *login_answers, change_answer, leave_answer, reset_answer = results
    assert {r.status_code for r in login_answers} <= {200, 401}, [r.text for r in login_answers]
    assert change_answer.status_code in (200, 401), change_answer.text
    assert leave_answer.status_code in (204, 401), leave_answer.text
    assert reset_answer.status_code in (200, 400), reset_answer.text

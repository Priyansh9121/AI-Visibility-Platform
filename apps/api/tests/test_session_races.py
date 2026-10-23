"""Races around sessions and the reset link (fixed together).

M2: `revoke_all_for_user` used to DEL the whole index after reading it, so a
session indexed in between dropped out of the index and could never be revoked.
M1: login verified a hash, then minted a session later; a password change that
committed in between left that session alive.
L3: two concurrent confirms of one reset link both succeeded.
L4: a rotated Redis password (AuthenticationError) must not read as an outage.
L5: `confirm.succeeded` is logged only after the commit.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from httpx import AsyncClient
from redis.exceptions import AuthenticationError, AuthorizationError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.models import PasswordResetToken, User
from avp_api.security import token_digest
from avp_api.sessions import SESSION_PREFIX, USER_SESSIONS_PREFIX, SessionStore

BASE = "/api/v1"
EMAIL = "race@test.example"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"


async def _sign_up(client: AsyncClient, email: str = EMAIL) -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Race Agency", "fullName": "Operator",
              "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _mint_token(engine, email: str, raw: str) -> str:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        s.add(PasswordResetToken(
            id=ids.new_id(ids.PASSWORD_RESET), user_id=user.id,
            token_digest=token_digest(raw),
            expires_at=datetime.now(UTC) + timedelta(hours=1)))
        await s.commit()
    return raw


async def test_a_session_indexed_during_revoke_all_stays_reachable(
    client: AsyncClient, settings: Settings
) -> None:
    user_id = (await _sign_up(client))["user"]["id"]
    redis = redis_module.get_redis(settings)
    store = SessionStore(redis, settings)
    key = f"{USER_SESSIONS_PREFIX}{user_id}"
    late = "f" * 64
    real = redis.smembers

    async def _smembers(name: str):  # noqa: ANN202
        got = await real(name)
        # A session lands after the read and before the delete.
        await redis.set(f"{SESSION_PREFIX}{late}", "{}", ex=600)
        await redis.sadd(key, late)
        return got

    redis.smembers = _smembers  # type: ignore[method-assign]
    assert await store.revoke_all_for_user(user_id) == 1
    redis.smembers = real  # type: ignore[method-assign]

    assert await redis.sismember(key, late)  # still indexed
    assert await redis.exists(f"{SESSION_PREFIX}{late}")
    assert await store.revoke_all_for_user(user_id) == 1  # the second reaches it
    assert not await redis.exists(f"{SESSION_PREFIX}{late}")


async def test_login_refuses_a_session_minted_across_a_password_change(
    client: AsyncClient, engine, settings: Settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    user_id = (await _sign_up(client))["user"]["id"]
    redis = redis_module.get_redis(settings)
    before = await redis.smembers(f"{USER_SESSIONS_PREFIX}{user_id}")  # the sign-up session
    real = SessionStore.create
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def _create_then_change(self: SessionStore, **kw: str):  # noqa: ANN202
        out = await real(self, **kw)
        async with factory() as s:  # the password changes just after the mint
            await s.execute(update(User).where(User.id == user_id)
                            .values(password_hash="$argon2id$changed"))
            await s.commit()
        return out

    monkeypatch.setattr(SessionStore, "create", _create_then_change)
    client.cookies.clear()
    resp = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert resp.status_code == 401, resp.text
    assert resp.json()["type"] == "/problems/invalid-credentials"
    assert await redis.smembers(f"{USER_SESSIONS_PREFIX}{user_id}") == before
    assert len([k async for k in redis.scan_iter(f"{SESSION_PREFIX}*")]) == len(before)
    # Not refunded: the refused login still counts against the budget.
    keys = [k async for k in redis.scan_iter("rl:login:email:*")]
    assert keys
    assert int(await redis.get(keys[0])) >= 1


async def test_two_concurrent_confirms_of_one_link_one_wins(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    await _sign_up(client)
    token = await _mint_token(engine, EMAIL, "race-token-" + "y" * 32)
    body = {"token": token, "newPassword": NEW_PASSWORD}

    # Open four connections at once and give them back, so the two requests start with idle
    # connections. Run cold, the second request waits to open one of its own and the first is done
    # by then. Since 3.129 each confirm also claims its link in Redis first, so the second request
    # is refused at the claim and never reaches the row lock: this test passes with or without the
    # lock. The lock itself is proven, with every claim granted, in
    # `test_password_reset_properties.py` by the test of two confirms that both hold a claim.
    async def hold_a_connection() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("select pg_sleep(0.3)"))

    await asyncio.gather(*(hold_a_connection() for _ in range(4)))

    a, b = await asyncio.gather(
        client.post(f"{BASE}/auth/reset-password/confirm", json=body),
        client.post(f"{BASE}/auth/reset-password/confirm", json=body),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 400], (a.text, b.text)
    loser = a if a.status_code == 400 else b
    assert loser.json()["type"] == "/problems/invalid-reset-token"
    assert loser.json()["detail"] == (
        "This link has already been used. If you just set a new password, sign in with it. "
        "Otherwise, request a new link."
    )


@pytest.mark.parametrize("exc", [AuthenticationError, AuthorizationError])
async def test_a_redis_credential_error_is_a_500_not_an_outage(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch, exc: type
) -> None:
    async def _boom(self: SessionStore, token: str) -> None:
        raise exc("credentials")

    monkeypatch.setattr(SessionStore, "get", _boom)
    client.cookies.set(settings.session_cookie_key, "some-session-token")
    with pytest.raises(exc):
        await client.get(f"{BASE}/auth/me")


async def test_confirm_succeeded_is_logged_only_after_the_commit(
    client: AsyncClient, engine, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    await _sign_up(client)
    token = await _mint_token(engine, EMAIL, "log-token-" + "z" * 32)
    body = {"token": token, "newPassword": NEW_PASSWORD}

    async def _boom(self: SessionStore, user_id: str) -> int:
        raise AuthenticationError("x")  # fails before the commit

    real = SessionStore.revoke_all_for_user
    monkeypatch.setattr(SessionStore, "revoke_all_for_user", _boom)
    with structlog.testing.capture_logs() as logs, pytest.raises(AuthenticationError):
        await client.post(f"{BASE}/auth/reset-password/confirm", json=body)
    assert not [e for e in logs if e["event"] == "password_reset.confirm.succeeded"]

    monkeypatch.setattr(SessionStore, "revoke_all_for_user", real)
    with structlog.testing.capture_logs() as logs:
        resp = await client.post(f"{BASE}/auth/reset-password/confirm", json=body)
    assert resp.status_code == 200
    ok = [e for e in logs if e["event"] == "password_reset.confirm.succeeded"]
    assert len(ok) == 1
    assert EMAIL not in repr(ok)

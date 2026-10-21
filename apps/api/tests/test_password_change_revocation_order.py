"""Sessions are revoked BEFORE the password commit, and again after it.

With the timeouts a Redis failure is now a fast 503. If the only revoke came
after the commit, that 503 would arrive with the password already changed, the
old sessions alive, and the retry refused (reset link used; old password
wrong). Revoking first makes a Redis failure leave everything unchanged; the
revoke after the commit stays to catch a session minted in between.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.models import PasswordResetToken, User
from avp_api.security import token_digest
from avp_api.sessions import USER_SESSIONS_PREFIX, SessionStore

BASE = "/api/v1"
EMAIL = "order@test.example"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"


async def _sign_up(client: AsyncClient, email: str) -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Order Agency",
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _token_for(engine, email: str) -> str:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        raw = "test-token-" + "x" * 32
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


def _fail_once(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = SessionStore.revoke_all_for_user

    async def _flaky(self: SessionStore, user_id: str) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RedisConnectionError("boom")
        return await real(self, user_id)

    monkeypatch.setattr(SessionStore, "revoke_all_for_user", _flaky)
    return calls


async def _login(client: AsyncClient, password: str):  # noqa: ANN202
    return await client.post(
        f"{BASE}/auth/login", json={"email": EMAIL, "password": password}
    )


async def _sessions(settings: Settings, user_id: str) -> set[str]:
    r = redis_module.get_redis(settings)
    return set(await r.smembers(f"{USER_SESSIONS_PREFIX}{user_id}"))


async def test_reset_confirm_redis_failure_changes_nothing(
    client: AsyncClient, engine, monkeypatch: pytest.MonkeyPatch, settings: Settings  # noqa: ANN001
) -> None:
    user_id = (await _sign_up(client, EMAIL))["user"]["id"]
    token = await _token_for(engine, EMAIL)
    _fail_once(monkeypatch)
    before = await _sessions(settings, user_id)
    assert before

    resp = await client.post(
        f"{BASE}/auth/reset-password/confirm",
        json={"token": token, "newPassword": NEW_PASSWORD},
    )
    assert resp.status_code == 503, resp.text
    assert (await _login(client, PASSWORD)).status_code == 200  # unchanged
    assert (await _login(client, NEW_PASSWORD)).status_code == 401
    # The link was not consumed, so the retry works and revokes everything.
    retry = await client.post(
        f"{BASE}/auth/reset-password/confirm",
        json={"token": token, "newPassword": NEW_PASSWORD},
    )
    assert retry.status_code == 200, retry.text
    assert await _sessions(settings, user_id) == set()
    assert (await _login(client, NEW_PASSWORD)).status_code == 200


async def test_change_password_redis_failure_changes_nothing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sign_up(client, EMAIL)
    _fail_once(monkeypatch)

    resp = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    )
    assert resp.status_code == 503, resp.text
    assert (await _login(client, NEW_PASSWORD)).status_code == 401
    assert (await _login(client, PASSWORD)).status_code == 200  # unchanged
    retry = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    )
    assert retry.status_code == 200, retry.text
    assert (await _login(client, NEW_PASSWORD)).status_code == 200


async def test_change_password_normal_path_leaves_only_the_new_session(
    client: AsyncClient, settings: Settings
) -> None:
    user_id = (await _sign_up(client, EMAIL))["user"]["id"]
    # A second DEVICE: a login that carries a session cookie replaces that session (A3 part 3).
    client.cookies.clear()
    assert (await _login(client, PASSWORD)).status_code == 200  # a second session
    assert len(await _sessions(settings, user_id)) == 2

    resp = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    assert len(await _sessions(settings, user_id)) == 1
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200  # new cookie works


async def test_a_wrong_current_password_revokes_nothing(
    client: AsyncClient, settings: Settings
) -> None:
    user_id = (await _sign_up(client, EMAIL))["user"]["id"]
    resp = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": "not-the-password-at-all", "newPassword": NEW_PASSWORD},
    )
    assert resp.status_code == 401
    assert len(await _sessions(settings, user_id)) == 1

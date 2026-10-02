"""Rate limit on `POST /auth/change-password`, the same shape as login's.

A stolen session cookie proves nothing about the password, and this route
verifies the current one; without a limit the cookie is an unmetered
password-guessing oracle. Counted per account and per client IP, failures only
(a success gives its attempt back), and BEFORE Argon2 so a refused attempt is
cheap, is not a timing oracle and revokes nothing.
"""

from __future__ import annotations

import pytest
import structlog
from httpx import AsyncClient
from pydantic import ValidationError

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.services import auth as auth_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
IP_HEADER = "X-Test-Client-IP"


@pytest.fixture(autouse=True)
def _limits(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)
    monkeypatch.setattr(settings, "change_password_limit_per_email", 3)
    monkeypatch.setattr(settings, "change_password_limit_per_ip", 4)


async def _sign_up(client: AsyncClient, email: str) -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Change Limit Agency",
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        },
        headers={IP_HEADER: "192.0.2.200"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _change(client: AsyncClient, current: str, new: str, ip: str):  # noqa: ANN202
    return await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": current, "newPassword": new},
        headers={IP_HEADER: ip},
    )


async def _keys(settings: Settings, pattern: str) -> list[str]:
    r = redis_module.get_redis(settings)
    return [k async for k in r.scan_iter(pattern)]


def test_default_limits_match_logins() -> None:
    s = Settings(_env_file=None)
    assert (
        s.change_password_limit_per_email,
        s.change_password_limit_per_ip,
        s.change_password_window_seconds,
    ) == (s.login_limit_per_email, s.login_limit_per_ip, s.login_window_seconds) == (10, 30, 900)


@pytest.mark.parametrize(
    "name",
    ["change_password_limit_per_email", "change_password_limit_per_ip",
     "change_password_window_seconds"],
)
@pytest.mark.parametrize("bad", [0, -1])
def test_a_limit_or_window_below_one_is_rejected(name: str, bad: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: bad})


async def test_wrong_current_passwords_trip_429_per_account_across_ips(
    client: AsyncClient,
) -> None:
    await _sign_up(client, "guessed@rate.example")
    for i in range(3):
        resp = await _change(client, "wrong-password", NEW_PASSWORD, ip=f"198.51.100.{i}")
        assert resp.status_code == 401, (i, resp.text)
    resp = await _change(client, "wrong-password", NEW_PASSWORD, ip="198.51.100.77")
    assert resp.status_code == 429
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert int(resp.headers["retry-after"]) > 0
    assert resp.json()["type"] == "/problems/rate-limited"
    assert resp.json()["detail"].startswith("Too many attempts. Try again in ")
    # Even the right password is refused while the account is over its limit.
    right = await _change(client, PASSWORD, NEW_PASSWORD, ip="198.51.100.78")
    assert right.status_code == 429


async def test_per_ip_limit_trips_across_different_users(client: AsyncClient) -> None:
    for i in range(4):  # two accounts, two guesses each, one IP
        if i % 2 == 0:
            await _sign_up(client, f"user{i}@rate.example")  # replaces the cookie
        resp = await _change(client, "wrong-password", NEW_PASSWORD, ip="203.0.113.9")
        assert resp.status_code == 401, (i, resp.text)
    await _sign_up(client, "user9@rate.example")
    resp = await _change(client, "wrong-password", NEW_PASSWORD, ip="203.0.113.9")
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0
    # A different IP is a different bucket, and this user has spent nothing.
    other = await _change(client, "wrong-password", NEW_PASSWORD, ip="203.0.113.10")
    assert other.status_code == 401


async def test_a_correct_change_succeeds_after_failures_and_gives_one_back(
    client: AsyncClient,
) -> None:
    await _sign_up(client, "mix@rate.example")
    for i in range(2):
        resp = await _change(client, "wrong-password", NEW_PASSWORD, ip=f"198.51.100.{i}")
        assert resp.status_code == 401
    ok = await _change(client, PASSWORD, NEW_PASSWORD, ip="198.51.100.10")
    assert ok.status_code == 200, ok.text
    # Two failures remain on the books: one more is allowed, the next is refused.
    assert (
        await _change(client, "wrong-password", PASSWORD + "-x", ip="198.51.100.11")
    ).status_code == 401
    refused = await _change(client, NEW_PASSWORD, PASSWORD + "-x", ip="198.51.100.12")
    assert refused.status_code == 429


async def test_successful_changes_do_not_consume_the_budget(client: AsyncClient) -> None:
    await _sign_up(client, "many@rate.example")
    current = PASSWORD
    for i in range(8):  # budgets are 3 per account, 4 per IP
        new = f"{NEW_PASSWORD}-{i}"
        resp = await _change(client, current, new, ip="198.51.100.5")
        assert resp.status_code == 200, (i, resp.text)
        current = new


async def test_keyed_on_the_account_with_the_login_scheme_and_no_email_anywhere(
    client: AsyncClient, settings: Settings
) -> None:
    email = "Leaky.Person@rate.example"
    await _sign_up(client, email)
    with structlog.testing.capture_logs() as logs:
        for i in range(4):
            await _change(client, "wrong-password", NEW_PASSWORD, ip=f"203.0.113.{60 + i}")
    tripped = [e for e in logs if e["event"] == "auth.rate_limited"]
    assert tripped == [
        {"event": "auth.rate_limited", "log_level": "warning",
         "action": "change_password", "kind": "email"}
    ]
    assert "leaky" not in repr(logs).lower()
    keys = await _keys(settings, "rl:*")
    assert keys
    for k in keys:
        assert k.startswith(("rl:change_password:email:", "rl:change_password:ip:", "rl:signup:"))
        assert "leaky" not in k.lower() and "rate.example" not in k and "203.0.113" not in k
    # The account key is the login scheme's digest over the normalised email.
    from avp_api.services.rate_limit import _key  # noqa: PLC0415

    assert _key(settings, "change_password", "email", "leaky.person@rate.example") in keys
    # Its own budget: login's is untouched.
    assert await _keys(settings, "rl:login:*") == []


async def test_unauthenticated_gets_401_before_any_rate_limit_work(
    client: AsyncClient, settings: Settings
) -> None:
    for i in range(8):  # more than both limits
        resp = await _change(client, "wrong-password", NEW_PASSWORD, ip="203.0.113.9")
        assert resp.status_code == 401, (i, resp.text)
        assert resp.json()["type"] != "/problems/rate-limited"
    assert await _keys(settings, "rl:change_password:*") == []


async def test_a_limited_request_neither_verifies_the_password_nor_revokes_sessions(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sign_up(client, "oracle@rate.example")
    for i in range(3):
        assert (
            await _change(client, "wrong-password", NEW_PASSWORD, ip=f"198.51.100.{i}")
        ).status_code == 401
    cookie = client.cookies.get(settings.session_cookie_key)
    r = redis_module.get_redis(settings)
    sessions_before = sorted([k async for k in r.scan_iter("sess:*")])
    assert sessions_before

    calls: list[str] = []
    real = auth_service.verify_password

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append("verify")
        return real(*args, **kwargs)

    monkeypatch.setattr(auth_service, "verify_password", spy)
    resp = await _change(client, PASSWORD, NEW_PASSWORD, ip="198.51.100.99")
    assert resp.status_code == 429
    assert calls == []
    assert "set-cookie" not in resp.headers
    assert client.cookies.get(settings.session_cookie_key) == cookie
    assert sorted([k async for k in r.scan_iter("sess:*")]) == sessions_before
    me = await client.get(f"{BASE}/auth/me", headers={IP_HEADER: "198.51.100.99"})
    assert me.status_code == 200


async def test_redis_error_fails_closed_with_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session lives in Redis too, so break only the limiter's script."""
    from redis.exceptions import ConnectionError as RedisConnectionError  # noqa: PLC0415

    from avp_api.services import rate_limit  # noqa: PLC0415

    await _sign_up(client, "down@rate.example")

    async def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RedisConnectionError("down")

    monkeypatch.setattr(rate_limit, "_eval", boom)
    resp = await _change(client, PASSWORD, NEW_PASSWORD, ip="203.0.113.9")
    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "/problems/service-unavailable"
    assert "down@rate.example" not in resp.text


async def test_a_completed_reset_unlocks_change_password(
    client: AsyncClient, settings: Settings, engine  # noqa: ANN001
) -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: PLC0415

    from avp_api import ids  # noqa: PLC0415
    from avp_api.models import PasswordResetToken, User  # noqa: PLC0415
    from avp_api.security import token_digest  # noqa: PLC0415

    email = "locked@rate.example"
    await _sign_up(client, email)
    for i in range(3):
        assert (
            await _change(client, "wrong-password", NEW_PASSWORD, ip=f"198.51.100.{i}")
        ).status_code == 401
    assert (
        await _change(client, PASSWORD, NEW_PASSWORD, ip="198.51.100.9")
    ).status_code == 429

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    raw = "change-limit-token-" + "x" * 32
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET), user_id=user.id,
                token_digest=token_digest(raw), expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()
    reset = await client.post(
        f"{BASE}/auth/reset-password/confirm",
        json={"token": raw, "newPassword": "reset-passphrase-number-one"},
    )
    assert reset.status_code == 200
    assert await _keys(settings, "rl:change_password:email:*") == []
    login = await client.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": "reset-passphrase-number-one"},
        headers={IP_HEADER: "198.51.100.50"},
    )
    assert login.status_code == 200
    ok = await _change(client, "reset-passphrase-number-one", NEW_PASSWORD, ip="198.51.100.51")
    assert ok.status_code == 200, ok.text

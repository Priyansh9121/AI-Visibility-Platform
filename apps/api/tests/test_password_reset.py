"""Password reset — Epic 9.13.

The assertions that matter are about what the endpoint REFUSES to tell you.
A reset endpoint is an account-enumeration oracle unless it is deliberately
built not to be, and `services/auth.authenticate` already burns a dummy Argon2
hash on the login path for exactly this reason.

No email provider is configured in the suite, which is the point: the
unconfigured path is the one that runs in development and it must be
indistinguishable from the configured one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from avp_api.models import PasswordResetToken, User, UserStatus
from avp_api.security import token_digest

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"


async def _sign_up(client: AsyncClient, email: str = "reset@test.example") -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Reset Test Agency",
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _request(client: AsyncClient, email: str):  # noqa: ANN202
    return await client.post(
        f"{BASE}/auth/reset-password/request", json={"email": email}
    )


async def _token_for(engine, email: str) -> str:  # noqa: ANN001
    """The raw token is never returned by the API, so tests mint their own.

    This mirrors what the service does rather than reaching into it: write a
    row whose digest matches a token the test chose. If the lookup ever stopped
    being digest-based, this would break — which is the point.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from avp_api import ids

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


class TestTheRequestRevealsNothing:
    async def test_an_unknown_address_gets_the_same_answer_as_a_known_one(
        self, client: AsyncClient
    ) -> None:
        """The whole reason this endpoint is shaped the way it is."""
        await _sign_up(client, "known@test.example")

        known = await _request(client, "known@test.example")
        unknown = await _request(client, "nobody@test.example")

        assert known.status_code == unknown.status_code == 200
        # Byte-identical, not merely "both 200". A different sentence is as good
        # an oracle as a different status.
        assert known.json() == unknown.json()
        assert known.text == unknown.text

    async def test_a_suspended_account_is_also_indistinguishable(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        from sqlalchemy.ext.asyncio import async_sessionmaker

        await _sign_up(client, "suspended@test.example")
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            user = (
                await s.execute(select(User).where(User.email == "suspended@test.example"))
            ).scalar_one()
            user.status = UserStatus.SUSPENDED
            await s.commit()

        suspended = await _request(client, "suspended@test.example")
        unknown = await _request(client, "nobody2@test.example")
        assert suspended.json() == unknown.json()

    async def test_no_token_row_is_created_for_an_unknown_address(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        from sqlalchemy.ext.asyncio import async_sessionmaker

        await _request(client, "ghost@test.example")
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            rows = (await s.execute(select(PasswordResetToken))).scalars().all()
        assert rows == []

    async def test_the_response_never_carries_a_token(self, client: AsyncClient) -> None:
        await _sign_up(client, "leak@test.example")
        resp = await _request(client, "leak@test.example")
        body = resp.text.lower()
        for tell in ("token", "reset-password/", "http://"):
            assert tell not in body, f"the response leaked {tell!r}"

    async def test_only_a_digest_is_ever_stored(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        """A dump of this table must hand an attacker nothing usable."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        await _sign_up(client, "digest@test.example")
        await _request(client, "digest@test.example")

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            row = (await s.execute(select(PasswordResetToken))).scalar_one()
        # SHA-256 hex is exactly 64 characters, and a token_urlsafe(32) is 43 —
        # so a stored raw token could not even fit this shape.
        assert len(row.token_digest) == 64
        assert all(c in "0123456789abcdef" for c in row.token_digest)

    async def test_asking_twice_retires_the_first_link(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        """A forwarded earlier email must not stay live for the rest of its hour."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        await _sign_up(client, "twice@test.example")
        await _request(client, "twice@test.example")
        await _request(client, "twice@test.example")

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            rows = (
                await s.execute(
                    select(PasswordResetToken).order_by(PasswordResetToken.id)
                )
            ).scalars().all()
        assert len(rows) == 2
        assert rows[0].used_at is not None, "the first link is still live"
        assert rows[1].used_at is None


class TestConfirm:
    async def test_a_valid_token_changes_the_password(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "change@test.example")
        raw = await _token_for(engine, "change@test.example")
        await client.post(f"{BASE}/auth/logout")

        resp = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": NEW_PASSWORD},
        )
        assert resp.status_code == 200, resp.text

        # The OLD password must stop working…
        old = await client.post(
            f"{BASE}/auth/login",
            json={"email": "change@test.example", "password": PASSWORD},
        )
        assert old.status_code == 401
        # …and the new one must work.
        new = await client.post(
            f"{BASE}/auth/login",
            json={"email": "change@test.example", "password": NEW_PASSWORD},
        )
        assert new.status_code == 200, new.text

    async def test_a_token_works_exactly_once(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        """A link that still opens the door after the password changed is a
        second key to the same door."""
        await _sign_up(client, "once@test.example")
        raw = await _token_for(engine, "once@test.example")

        first = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": NEW_PASSWORD},
        )
        assert first.status_code == 200

        second = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": "a-third-different-passphrase"},
        )
        assert second.status_code == 400

    async def test_an_expired_token_is_refused(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from avp_api import ids

        await _sign_up(client, "expired@test.example")
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        raw = "expired-token-" + "y" * 32
        async with factory() as s:
            user = (
                await s.execute(select(User).where(User.email == "expired@test.example"))
            ).scalar_one()
            s.add(
                PasswordResetToken(
                    id=ids.new_id(ids.PASSWORD_RESET),
                    user_id=user.id,
                    token_digest=token_digest(raw),
                    # One second in the past. The boundary, not a comfortable margin.
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await s.commit()

        resp = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": NEW_PASSWORD},
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize(
        "token",
        ["", "nope", "x" * 43, "../../etc/passwd", "' OR 1=1 --", "y" * 4000],
    )
    async def test_every_bad_token_is_the_same_refusal(
        self, client: AsyncClient, token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": token, "newPassword": NEW_PASSWORD},
        )
        # 400 for a rejected token, 422 for a body that is not even shaped like
        # one (empty string). Never 404 — that would confirm which guesses
        # looked like real tokens.
        assert resp.status_code in (400, 422), token
        assert resp.status_code != 404

    async def test_unknown_and_expired_are_indistinguishable(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from avp_api import ids

        await _sign_up(client, "same@test.example")
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        raw = "will-expire-" + "z" * 32
        async with factory() as s:
            user = (
                await s.execute(select(User).where(User.email == "same@test.example"))
            ).scalar_one()
            s.add(
                PasswordResetToken(
                    id=ids.new_id(ids.PASSWORD_RESET),
                    user_id=user.id,
                    token_digest=token_digest(raw),
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await s.commit()

        expired = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": NEW_PASSWORD},
        )
        unknown = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": "q" * 43, "newPassword": NEW_PASSWORD},
        )
        assert expired.status_code == unknown.status_code == 400
        assert expired.json()["type"] == unknown.json()["type"]
        assert expired.json()["detail"] == unknown.json()["detail"]

    async def test_a_reset_revokes_every_existing_session(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        """A reset is what someone does when they think they are compromised."""
        await _sign_up(client, "revoke@test.example")
        # Still signed in from sign-up.
        assert (await client.get(f"{BASE}/auth/me")).status_code == 200

        raw = await _token_for(engine, "revoke@test.example")
        resp = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": NEW_PASSWORD},
        )
        assert resp.status_code == 200
        # The session that existed before the reset is gone.
        assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    async def test_the_new_password_must_meet_the_signup_rules(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        """A reset path with weaker rules than registration is a way in."""
        await _sign_up(client, "weak@test.example")
        raw = await _token_for(engine, "weak@test.example")

        for bad in ("short", "aaaaaaaaaaaaaaaaaaaa"):
            resp = await client.post(
                f"{BASE}/auth/reset-password/confirm",
                json={"token": raw, "newPassword": bad},
            )
            assert resp.status_code == 422, bad


class TestTheEmailInterface:
    async def test_it_never_raises_when_unconfigured(self) -> None:
        """The endpoint's guarantee depends on this being true."""
        from avp_api.config import Settings
        from avp_api.services import email as email_service

        settings = Settings(
            environment="test",
            database_url="postgresql+asyncpg://unused/unused",
            redis_url="redis://unused",
            app_secret="test-secret-not-used-in-any-real-environment",
        )
        assert settings.resend_api_key is None
        # Returns None, does not raise, does not attempt a network call.
        assert (
            await email_service.send_password_reset(
                "someone@test.example", "http://localhost:3000/reset-password/x",
                settings=settings,
            )
            is None
        )

    async def test_a_provider_failure_is_swallowed_not_surfaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A provider outage must not become a different HTTP response."""
        import httpx

        from avp_api.config import Settings
        from avp_api.services import email as email_service

        settings = Settings(
            environment="test",
            database_url="postgresql+asyncpg://unused/unused",
            redis_url="redis://unused",
            app_secret="test-secret-not-used-in-any-real-environment",
            resend_api_key="re_test_key_never_sent",
        )

        class _Broken(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("provider down", request=request)

        real = httpx.AsyncClient

        def factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            kwargs["transport"] = _Broken()
            return real(*args, **kwargs)

        monkeypatch.setattr(email_service.httpx, "AsyncClient", factory)
        assert (
            await email_service.send_password_reset(
                "someone@test.example", "http://x/y", settings=settings
            )
            is None
        )

    async def test_the_request_endpoint_answers_the_same_with_a_key_set(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configured or not, the caller cannot tell."""
        from avp_api.services import email as email_service

        await _sign_up(client, "cfg@test.example")
        unconfigured = await _request(client, "cfg@test.example")

        sent: list[str] = []

        async def fake_send(to: str, reset_url: str, *, settings) -> None:  # noqa: ANN001
            sent.append(to)

        monkeypatch.setattr(email_service, "send_password_reset", fake_send)
        configured = await _request(client, "cfg@test.example")

        assert unconfigured.json() == configured.json()
        assert sent == ["cfg@test.example"]

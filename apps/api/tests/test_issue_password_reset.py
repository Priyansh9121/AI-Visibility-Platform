"""The operator's manual reset — `scripts/issue_password_reset.py`, 2026-09-29.

With email off in production, this script is the "we'll reset it by hand" the
forgot-password screen promises. What it must hold:

- the link it prints opens the EXISTING reset route, and its token comes from
  the route's own `request_reset` — no second token path;
- the link, the token and the address reach no log line (the capture from
  `test_secrets_stay_out_of_logs.py`: structlog's events, stdout and stdlib
  logging, all searched), while the link reaches the operator's stdout once;
- an unknown address exits non-zero, and a Google-only account is refused.

Two tests run the real command in a subprocess against the test database, so
the exit code and the printed output are the ones an operator would see.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api.config import Settings
from avp_api.models import PasswordResetToken, User, UserStatus
from avp_api.security import token_digest
from avp_api.services import password_reset as reset_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
OWNER = "owner@manual-reset.example"
STRANGER = "nobody-here@manual-reset.example"
SENTINEL = "MANUALRESETSENTINELtoken0123456789abcdefghijklmn"

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "issue_password_reset.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("issue_password_reset", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def _run_cli(settings: Settings, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """The command as an operator runs it, against the test database.

    From an empty directory, so no developer `.env` is read: the settings are
    the ones passed here and nothing else.
    """
    env = {
        **os.environ,
        "ENVIRONMENT": "test",
        "DATABASE_URL": settings.database_url,
        "PUBLIC_WEB_BASE_URL": settings.public_web_base_url,
    }
    return subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


async def _sign_up(client: AsyncClient, email: str = OWNER) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Manual Reset Agency", "fullName": "Owner", "email": email,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text


def _streams() -> tuple[io.StringIO, io.StringIO]:
    return io.StringIO(), io.StringIO()


class TestTheLinkIsTheRoutesOwn:
    async def test_the_printed_link_opens_the_existing_reset_route(
        self, client: AsyncClient, settings: Settings, tmp_path: Path
    ) -> None:
        await _sign_up(client)

        # Typed with capitals, as a person might: the address is normalised
        # the way sign-up normalised it.
        result = _run_cli(settings, tmp_path, "--email", OWNER.upper())
        assert result.returncode == 0, result.stderr

        prefix = reset_service.reset_url(settings, "")
        assert prefix == f"{settings.public_web_base_url.rstrip('/')}/reset-password/"
        links = [line for line in result.stdout.splitlines() if line.startswith(prefix)]
        assert len(links) == 1, result.stdout
        token = links[0].removeprefix(prefix)
        # Printed once: the token appears on the link's line and nowhere else
        # in everything the run wrote, logs included. No address anywhere.
        assert result.stdout.count(token) == 1
        assert token not in result.stderr
        assert OWNER not in result.stdout.lower() + result.stderr.lower()

        confirm = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": token, "newPassword": NEW_PASSWORD},
        )
        assert confirm.status_code == 200, confirm.text
        login = await client.post(
            f"{BASE}/auth/login", json={"email": OWNER, "password": NEW_PASSWORD}
        )
        assert login.status_code == 200, login.text

    async def test_the_token_is_minted_by_request_reset_and_nothing_else(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        calls: list[str] = []
        real = reset_service.request_reset

        async def spy(*args: Any, **kwargs: Any) -> Any:
            calls.append(kwargs["email"])
            return await real(*args, **kwargs)

        monkeypatch.setattr(reset_service, "request_reset", spy)
        monkeypatch.setattr(reset_service, "new_session_token", lambda: SENTINEL)

        out, err = _streams()
        code = await script.issue(session, email=OWNER, settings=settings, out=out, err=err)

        assert code == script.ISSUED, err.getvalue()
        assert calls == [OWNER]
        rows = (await session.execute(select(PasswordResetToken))).scalars().all()
        assert len(rows) == 1
        # The route's storage: the digest, never the token; the route's expiry.
        assert rows[0].token_digest == token_digest(SENTINEL)
        expected = datetime.now(UTC) + timedelta(seconds=settings.password_reset_ttl_seconds)
        assert abs(rows[0].expires_at - expected) < timedelta(minutes=1)
        assert out.getvalue().count(reset_service.reset_url(settings, SENTINEL)) == 1


class TestNothingSecretIsLogged:
    async def test_no_log_line_carries_the_token_the_link_or_the_address(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _sign_up(client)
        monkeypatch.setattr(reset_service, "new_session_token", lambda: SENTINEL)
        # Only what the script's run writes: sign-up's own output is dropped.
        capsys.readouterr()
        caplog.clear()
        caplog.set_level("DEBUG")

        out, err = _streams()
        with structlog.testing.capture_logs() as events:
            code = await script.issue(session, email=OWNER, settings=settings, out=out, err=err)
        assert code == script.ISSUED, err.getvalue()

        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        link = reset_service.reset_url(settings, SENTINEL)
        for secret in (SENTINEL, link, reset_service.reset_url(settings, ""), OWNER):
            assert secret not in everything, f"{secret!r} reached the logs"
        # The event is logged — by user id, which is how accounts are named.
        minted = [e for e in events if e["event"] == "password_reset.request.minted"]
        assert len(minted) == 1 and minted[0]["user_id"].startswith("user_")
        # And the link went where it should: the operator's stdout, once.
        assert out.getvalue().count(link) == 1
        assert err.getvalue() == ""


class TestRefusals:
    async def test_an_unknown_address_exits_non_zero(
        self, engine: Any, settings: Settings, tmp_path: Path
    ) -> None:
        result = _run_cli(settings, tmp_path, "--email", STRANGER)

        assert result.returncode != 0
        assert result.returncode == script.NO_SUCH_USER
        assert "No such user." in result.stderr
        assert "/reset-password/" not in result.stdout + result.stderr
        assert STRANGER not in result.stdout + result.stderr

    async def test_a_google_only_account_is_refused(
        self, client: AsyncClient, session: AsyncSession, settings: Settings
    ) -> None:
        await _sign_up(client)
        # What a Google sign-up leaves: a Google subject and no password.
        await session.execute(
            update(User)
            .where(User.email == OWNER)
            .values(password_hash=None, google_sub="google-subject-manual-reset")
        )
        await session.commit()

        out, err = _streams()
        code = await script.issue(session, email=OWNER, settings=settings, out=out, err=err)

        assert code == script.REFUSED
        assert "signs in with Google" in err.getvalue()
        assert "Sign in with Google" in err.getvalue()
        assert out.getvalue() == ""
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []

    async def test_a_suspended_account_is_refused(
        self, client: AsyncClient, session: AsyncSession, settings: Settings
    ) -> None:
        await _sign_up(client)
        await session.execute(
            update(User).where(User.email == OWNER).values(status=UserStatus.SUSPENDED)
        )
        await session.commit()

        out, err = _streams()
        code = await script.issue(session, email=OWNER, settings=settings, out=out, err=err)

        assert code == script.REFUSED
        assert "suspended" in err.getvalue()
        assert out.getvalue() == ""
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []

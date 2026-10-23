"""The operator's manual reset — `scripts/issue_password_reset.py`, 2026-09-29.

With email off in production, this script is the "we'll reset it by hand" the
forgot-password screen promises. What it must hold:

- the link it prints opens the EXISTING reset route, and its token comes from
  the route's own `request_reset` — no second token path;
- the link, the token and the address reach no log line (the capture from
  `test_secrets_stay_out_of_logs.py`: structlog's events, stdout and stdlib
  logging, all searched), while the link reaches the operator's stdout once;
- an unknown address exits non-zero, and a Google-only account is refused;
- the account is signed out everywhere BEFORE the link is minted (A5 follow-up,
  2026-10-15): the user's sessions are gone, another user's are not, and with
  Redis unreachable nothing is minted and the exit code is non-zero;
- the password is REPLACED too (A5 part 3, M2, 2026-10-23): afterwards the old
  password is refused and the issued link sets a new one; the stored hash is the
  hash of a random secret that reaches no output and no log line; it is made
  after the refusals and before the Redis revoke; it is replaced only while the
  account still has a password (one that became Google-only meanwhile gets none
  back, and no dead link is printed); a refused mint replaces it too and prints
  the founder's line, verbatim; and the output has the lines it always had.

Four tests run the real command in a subprocess against the test database, so
the exit code and the printed output are the ones an operator would see.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
import structlog
from argon2 import extract_parameters
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from avp_api.config import Settings
from avp_api.errors import ServiceUnavailable
from avp_api.models import PasswordResetToken, User, UserStatus
from avp_api.security import hash_password, needs_rehash, token_digest, verify_password
from avp_api.services import password_reset as reset_service
from avp_api.sessions import SESSION_PREFIX, USER_SESSIONS_PREFIX, SessionStore

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
OWNER = "owner@manual-reset.example"
STRANGER = "nobody-here@manual-reset.example"
OTHER = "other@manual-reset.example"
SECRET_PW = "REDISPWSENTINEL0123abcd"
SECRET_USER = "REDISUSERSENTINEL0123"
DEAD_REDIS = f"redis://{SECRET_USER}:{SECRET_PW}@127.0.0.1:1/15"


class Untouchable:
    """A session store that fails the test if the script reaches for Redis."""

    async def revoke_all_for_user(self, user_id: str) -> int:
        raise AssertionError("Redis was touched")
SENTINEL = "MANUALRESETSENTINELtoken0123456789abcdefghijklmn"

# The founder's line for a refused mint (2026-10-23, item j), typed out here and not imported,
# so that a drift in the script's own constant is seen.
FOUNDERS_REFUSAL = (
    "Refused: another reset was being issued for this account at that moment, or the account "
    "was removed. Its sessions have ended, its password has been replaced, and no link was "
    "issued. Run it again."
)
# The sentence for an account that signs in with Google only, unchanged since the script began.
GOOGLE_REFUSAL = (
    "Refused: this account signs in with Google and has no password to reset. "
    "They should use Sign in with Google."
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "issue_password_reset.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("issue_password_reset", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def _run_cli(
    settings: Settings,
    cwd: Path,
    *args: str,
    redis_url: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """The command as an operator runs it, against the test database.

    From an empty directory, so no developer `.env` is read: the settings are
    the ones passed here and nothing else (and `extra_env`, for a test that must set one).
    """
    env = {
        **os.environ,
        "ENVIRONMENT": "test",
        "DATABASE_URL": settings.database_url,
        "PUBLIC_WEB_BASE_URL": settings.public_web_base_url,
        "REDIS_URL": redis_url or settings.redis_url,
        **(extra_env or {}),
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


@pytest_asyncio.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def store(redis: Redis, settings: Settings) -> SessionStore:
    return SessionStore(redis, settings)


async def _user_id(session: AsyncSession, email: str) -> str:
    return (await session.execute(select(User.id).where(User.email == email))).scalar_one()


async def _sessions_of(redis: Redis, user_id: str) -> tuple[int, int]:
    """(session records, index entries) the user has in Redis right now."""
    indexed = await redis.smembers(f"{USER_SESSIONS_PREFIX}{user_id}")
    live = [d for d in indexed if await redis.exists(f"{SESSION_PREFIX}{d}")]
    return len(live), len(indexed)


async def _sign_up(client: AsyncClient, email: str = OWNER) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Manual Reset Agency", "fullName": "Owner", "email": email,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text


def _streams() -> tuple[io.StringIO, io.StringIO]:
    return io.StringIO(), io.StringIO()


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _credentials(
    engine: AsyncEngine, email: str
) -> tuple[str | None, datetime | None, str | None]:
    """(password hash, sign-out cutoff, Google subject) as the database holds them now.

    Read in a session of its own, so the script's session, which keeps rows in its identity map
    after a commit, cannot show a stale value.
    """
    async with _factory(engine)() as s:
        row = (
            await s.execute(
                select(User.password_hash, User.sessions_valid_after, User.google_sub).where(
                    User.email == email
                )
            )
        ).one()
    return row.password_hash, row.sessions_valid_after, row.google_sub


async def _login(client: AsyncClient, password: str, email: str = OWNER) -> int:
    resp = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    return resp.status_code


def _token_of(out: str, settings: Settings) -> str:
    """The token in the one link a run printed."""
    prefix = reset_service.reset_url(settings, "")
    links = [line for line in out.splitlines() if line.startswith(prefix)]
    assert len(links) == 1, out
    return links[0].removeprefix(prefix)


def _recording_hash(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Make the script's hashing record every secret it is asked to hash, then hash it for real."""
    real = script.hash_password_async

    async def record(password: str, settings: Settings | None = None) -> str:
        seen.append(password)
        return await real(password, settings)

    monkeypatch.setattr(script, "hash_password_async", record)


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
        assert prefix == f"{settings.public_web_base_url.rstrip('/')}/reset-password#"
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
        store: SessionStore,
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
        code = await script.issue(
            session, email=OWNER, settings=settings, store=store, out=out, err=err
        )

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
        store: SessionStore,
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
            code = await script.issue(
                session, email=OWNER, settings=settings, store=store, out=out, err=err
            )
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
        assert "/reset-password#" not in result.stdout + result.stderr
        assert STRANGER not in result.stdout + result.stderr

    async def test_a_google_only_account_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
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
        code = await script.issue(
            session,
            email=OWNER,
            settings=settings,
            store=Untouchable(),  # type: ignore[arg-type]
            out=out,
            err=err,
        )

        assert code == script.REFUSED
        assert "signs in with Google" in err.getvalue()
        assert "Sign in with Google" in err.getvalue()
        assert out.getvalue() == ""
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []

    async def test_a_suspended_account_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        store: SessionStore,
    ) -> None:
        await _sign_up(client)
        await session.execute(
            update(User).where(User.email == OWNER).values(status=UserStatus.SUSPENDED)
        )
        await session.commit()

        out, err = _streams()
        code = await script.issue(
            session, email=OWNER, settings=settings, store=store, out=out, err=err
        )

        assert code == script.REFUSED
        assert "suspended" in err.getvalue()
        assert out.getvalue() == ""
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []


class TestTheAccountIsSignedOutFirst:
    async def _two_users_with_sessions(
        self, client: AsyncClient, session: AsyncSession, store: SessionStore
    ) -> tuple[str, str]:
        await _sign_up(client)
        await _sign_up(client, OTHER)
        owner, other = await _user_id(session, OWNER), await _user_id(session, OTHER)
        for uid in (owner, other):
            for _ in range(2):
                await store.create(user_id=uid, agency_id="agency_x")
        return owner, other

    async def test_the_users_sessions_are_gone_and_another_users_are_not(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        store: SessionStore,
        redis: Redis,
    ) -> None:
        owner, other = await self._two_users_with_sessions(client, session, store)
        before_owner = await _sessions_of(redis, owner)
        before_other = await _sessions_of(redis, other)
        assert before_owner[0] >= 2 and before_other[0] >= 2

        out, err = _streams()
        code = await script.issue(
            session, email=OWNER, settings=settings, store=store, out=out, err=err
        )

        assert code == script.ISSUED, err.getvalue()
        assert await _sessions_of(redis, owner) == (0, 0)
        assert await _sessions_of(redis, other) == before_other
        # Said how many went and where; no digest, cookie or address.
        assert f"{before_owner[1]} session list entry(ies) removed on Redis " in out.getvalue()
        assert OWNER not in out.getvalue().lower() + err.getvalue().lower()

    async def test_the_count_line_names_the_redis_host_and_db_and_no_credential(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        store: SessionStore,
    ) -> None:
        await _sign_up(client)
        # The store is the test Redis; the settings carry the URL the line is
        # built from, with a username and a password that must never be printed.
        shown = settings.model_copy(
            update={"redis_url": f"redis://{SECRET_USER}:{SECRET_PW}@cache.internal:6379/7"}
        )

        out, err = _streams()
        code = await script.issue(
            session, email=OWNER, settings=shown, store=store, out=out, err=err
        )

        assert code == script.ISSUED, err.getvalue()
        assert "removed on Redis cache.internal db 7." in out.getvalue()
        both = out.getvalue() + err.getvalue()
        for secret in (SECRET_PW, SECRET_USER, "redis://"):
            assert secret not in both

    async def test_the_command_signs_the_account_out_before_it_prints_the_link(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        store: SessionStore,
        redis: Redis,
        tmp_path: Path,
    ) -> None:
        owner, other = await self._two_users_with_sessions(client, session, store)
        before_other = await _sessions_of(redis, other)

        result = _run_cli(settings, tmp_path, "--email", OWNER)

        assert result.returncode == 0, result.stderr
        assert "/reset-password#" in result.stdout
        assert await _sessions_of(redis, owner) == (0, 0)
        assert await _sessions_of(redis, other) == before_other

    async def test_with_redis_down_nothing_is_minted_and_it_exits_non_zero(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        await _sign_up(client)
        dead_client = Redis.from_url(DEAD_REDIS, decode_responses=True)
        dead = SessionStore(dead_client, settings)

        out, err = _streams()
        try:
            code = await script.issue(
                session, email=OWNER, settings=settings, store=dead, out=out, err=err
            )
        finally:
            await dead_client.aclose()

        assert code == script.REDIS_UNREACHABLE != 0
        assert "Redis could not be reached" in err.getvalue()
        assert "no link was issued" in err.getvalue()
        assert "may not have been signed out" in err.getvalue()
        assert "/reset-password#" not in out.getvalue() + err.getvalue()
        for secret in (SECRET_PW, SECRET_USER):
            assert secret not in out.getvalue() + err.getvalue()
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []

        # The same through the real command: non-zero, no link, nothing minted.
        result = _run_cli(settings, tmp_path, "--email", OWNER, redis_url=DEAD_REDIS)
        assert result.returncode == script.REDIS_UNREACHABLE
        assert "/reset-password#" not in result.stdout + result.stderr
        assert OWNER not in result.stdout.lower() + result.stderr.lower()
        for secret in (SECRET_PW, SECRET_USER):
            assert secret not in result.stdout + result.stderr
        await session.rollback()
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []

    async def test_an_unknown_or_refused_account_never_touches_redis(
        self, client: AsyncClient, session: AsyncSession, settings: Settings
    ) -> None:
        await _sign_up(client)
        await session.execute(
            update(User).where(User.email == OWNER).values(status=UserStatus.SUSPENDED)
        )
        await session.commit()

        for email, expected in ((STRANGER, script.NO_SUCH_USER), (OWNER, script.REFUSED)):
            out, err = _streams()
            code = await script.issue(
                session,
                email=email,
                settings=settings,
                store=Untouchable(),  # type: ignore[arg-type]
                out=out,
                err=err,
            )
            assert code == expected


class TestThePasswordIsReplaced:
    """A5 part 3 (M2, option B): whoever knows the old password cannot sign straight back in."""

    async def test_the_old_password_is_refused_and_the_issued_link_sets_a_new_one(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        store: SessionStore,
    ) -> None:
        await _sign_up(client)
        client.cookies.clear()
        assert await _login(client, PASSWORD) == 200  # before the script, the old password works

        out, err = _streams()
        code = await script.issue(
            session, email=OWNER, settings=settings, store=store, out=out, err=err
        )
        assert code == script.ISSUED, err.getvalue()

        client.cookies.clear()
        assert await _login(client, PASSWORD) == 401

        # The link was minted with the password already replaced, and is live: the replacement
        # retires no link (a reset link is bound to its user, digest, expiry and `used_at`).
        confirm = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": _token_of(out.getvalue(), settings), "newPassword": NEW_PASSWORD},
        )
        assert confirm.status_code == 200, confirm.text
        client.cookies.clear()
        assert await _login(client, NEW_PASSWORD) == 200
        client.cookies.clear()
        assert await _login(client, PASSWORD) == 401

    async def test_the_stored_hash_is_that_of_a_random_secret_which_reaches_no_output_or_log(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _sign_up(client)
        before, _, _ = await _credentials(engine, OWNER)
        assert before is not None
        seen: list[str] = []
        _recording_hash(monkeypatch, seen)
        capsys.readouterr()
        caplog.clear()
        caplog.set_level("DEBUG")

        printed: list[str] = []
        with structlog.testing.capture_logs() as events:
            for _ in range(2):
                out, err = _streams()
                code = await script.issue(
                    session, email=OWNER, settings=settings, store=store, out=out, err=err
                )
                assert code == script.ISSUED, err.getvalue()
                printed.append(out.getvalue() + err.getvalue())
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        everything += "".join(printed)

        after, _, _ = await _credentials(engine, OWNER)
        assert after is not None and after != before
        # One secret per run: never the same twice, never a password anybody typed, 256 bits.
        assert len(seen) == 2 and seen[0] != seen[1]
        for secret in seen:
            assert secret not in (PASSWORD, NEW_PASSWORD)
            assert len(secret) >= 43  # 32 random bytes as URL-safe base64, no padding
            assert secret not in everything, "the secret reached an output or a log line"
            assert secret not in after
        # What is stored is the hash of the LAST secret, at the configured parameters.
        assert verify_password(seen[1], after, settings)
        assert not verify_password(seen[0], after, settings)
        assert not verify_password(PASSWORD, after, settings)
        assert not needs_rehash(after, settings)
        assert after not in everything

    async def test_the_real_command_replaces_the_password_at_its_configured_cost(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        """The command itself, in a subprocess whose settings name an Argon2 cost of their own:
        the stored hash carries exactly that cost (not the library's, not this suite's cheap one),
        the old password is refused, and nothing the command printed holds a hash."""
        await _sign_up(client)
        before, _, _ = await _credentials(engine, OWNER)

        result = _run_cli(
            settings,
            tmp_path,
            "--email",
            OWNER,
            extra_env={
                "ARGON2_TIME_COST": "3",
                "ARGON2_MEMORY_COST_KIB": "9000",
                "ARGON2_PARALLELISM": "2",
            },
        )

        assert result.returncode == 0, result.stderr
        after, cutoff, _ = await _credentials(engine, OWNER)
        assert after is not None and after != before and cutoff is not None
        parameters = extract_parameters(after)
        cost = (parameters.time_cost, parameters.memory_cost, parameters.parallelism)
        assert cost == (3, 9000, 2)
        assert "$argon2" not in result.stdout + result.stderr
        client.cookies.clear()
        assert await _login(client, PASSWORD) == 401

    async def test_the_output_is_the_three_lines_it_always_was(
        self,
        client: AsyncClient,
        session: AsyncSession,
        settings: Settings,
        store: SessionStore,
    ) -> None:
        await _sign_up(client)
        user_id = await _user_id(session, OWNER)

        out, err = _streams()
        code = await script.issue(
            session, email=OWNER, settings=settings, store=store, out=out, err=err
        )

        assert code == script.ISSUED, err.getvalue()
        lines = out.getvalue().splitlines()
        assert len(lines) == 3, out.getvalue()
        minutes = settings.password_reset_ttl_seconds // 60
        assert lines[0].startswith("Signed the account out everywhere: ")
        expected = f"Reset link for {user_id} — it works once and expires in {minutes} minutes:"
        assert lines[1] == expected
        assert lines[2].startswith(reset_service.reset_url(settings, ""))
        assert err.getvalue() == ""
        # Nothing about the replacement is said: the runbook says it, the script does not.
        for word in ("password", "hash", "secret", "replaced"):
            assert word not in (lines[0] + lines[1]).lower(), word

    async def test_nothing_is_hashed_for_an_account_the_script_refuses(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        await _sign_up(client, OTHER)
        await session.execute(
            update(User).where(User.email == OWNER).values(status=UserStatus.SUSPENDED)
        )
        await session.execute(
            update(User)
            .where(User.email == OTHER)
            .values(password_hash=None, google_sub="google-subject-refused")
        )
        await session.commit()
        before = [await _credentials(engine, e) for e in (OWNER, OTHER)]

        async def never(password: str, settings: Settings | None = None) -> str:
            raise AssertionError("a secret was hashed for an account the script refuses")

        monkeypatch.setattr(script, "hash_password_async", never)
        for email, expected in (
            (STRANGER, script.NO_SUCH_USER),
            (OWNER, script.REFUSED),
            (OTHER, script.REFUSED),
        ):
            out, err = _streams()
            code = await script.issue(
                session,
                email=email,
                settings=settings,
                store=Untouchable(),  # type: ignore[arg-type]
                out=out,
                err=err,
            )
            assert code == expected, email

        assert [await _credentials(engine, e) for e in (OWNER, OTHER)] == before

    async def test_a_google_only_account_is_left_exactly_as_it_was(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
    ) -> None:
        await _sign_up(client)
        await session.execute(
            update(User)
            .where(User.email == OWNER)
            .values(password_hash=None, google_sub="google-subject-untouched")
        )
        await session.commit()

        out, err = _streams()
        code = await script.issue(
            session,
            email=OWNER,
            settings=settings,
            store=Untouchable(),  # type: ignore[arg-type]
            out=out,
            err=err,
        )

        assert code == script.REFUSED
        assert err.getvalue() == GOOGLE_REFUSAL + "\n"
        assert out.getvalue() == ""
        # No password given to it, no cutoff, no link: nothing changed.
        assert await _credentials(engine, OWNER) == (None, None, "google-subject-untouched")
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []

    async def test_with_redis_down_the_password_is_not_replaced(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
    ) -> None:
        await _sign_up(client)
        before = await _credentials(engine, OWNER)
        dead_client = Redis.from_url(DEAD_REDIS, decode_responses=True)
        out, err = _streams()
        try:
            code = await script.issue(
                session,
                email=OWNER,
                settings=settings,
                store=SessionStore(dead_client, settings),
                out=out,
                err=err,
            )
        finally:
            await dead_client.aclose()

        assert code == script.REDIS_UNREACHABLE
        await session.rollback()
        assert await _credentials(engine, OWNER) == before
        client.cookies.clear()
        assert await _login(client, PASSWORD) == 200  # still the old password

    async def test_a_hashing_failure_changes_nothing_and_comes_before_the_revoke(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The hash is the one step after the refusals that can fail (no hashing slot), so it is
        made before the Redis revoke, which cannot be taken back: `Untouchable` fails the test
        if the revoke comes first."""
        await _sign_up(client)
        before = await _credentials(engine, OWNER)

        async def no_slot(password: str, settings: Settings | None = None) -> str:
            raise ServiceUnavailable(
                detail="This is temporarily unavailable. Please try again shortly."
            )

        monkeypatch.setattr(script, "hash_password_async", no_slot)
        out, err = _streams()
        with pytest.raises(ServiceUnavailable):
            await script.issue(
                session,
                email=OWNER,
                settings=settings,
                store=Untouchable(),  # type: ignore[arg-type]
                out=out,
                err=err,
            )

        await session.rollback()
        assert await _credentials(engine, OWNER) == before
        assert out.getvalue() == ""
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []

    async def test_a_password_changed_while_the_script_runs_is_replaced_too(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The write replaces whatever hash the account has when it runs, not the one the script
        read at the start: a password set meanwhile is exactly what must not survive."""
        await _sign_up(client)
        changed_to = "changed-while-it-ran-1"
        changed = hash_password(changed_to, settings)
        real = reset_service.request_reset

        async def a_password_change_lands_after_the_mint(*args: Any, **kwargs: Any) -> Any:
            minted = await real(*args, **kwargs)
            async with _factory(engine)() as other:
                await asyncio.wait_for(
                    other.execute(
                        update(User).where(User.email == OWNER).values(password_hash=changed)
                    ),
                    10,
                )
                await other.commit()
            return minted

        monkeypatch.setattr(reset_service, "request_reset", a_password_change_lands_after_the_mint)
        out, err = _streams()
        code = await script.issue(
            session, email=OWNER, settings=settings, store=store, out=out, err=err
        )

        assert code == script.ISSUED, err.getvalue()
        stored, _, _ = await _credentials(engine, OWNER)
        assert stored is not None and stored != changed
        assert not verify_password(changed_to, stored, settings)
        client.cookies.clear()
        assert await _login(client, changed_to) == 401

    async def test_an_account_that_becomes_google_only_meanwhile_gets_no_password_and_no_link(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Google link clears the hash while the script runs. The write must not give a password
        back, the cutoff is still written, and the link just minted is dead (`_redeemable` refuses
        an account with no password), so it is not printed as if it worked.

        The link lands BEFORE the mint inserts its row, which is the only place it can: that
        insert's foreign key holds a share lock on the user row, and a link changes `google_sub`,
        a unique column, so one that starts after the insert waits for the script's commit."""
        await _sign_up(client)
        monkeypatch.setattr(reset_service, "new_session_token", lambda: SENTINEL)
        real = reset_service.request_reset

        async def a_google_link_lands_before_the_mint(*args: Any, **kwargs: Any) -> Any:
            async with _factory(engine)() as other:  # what the link does to the row, committed
                await asyncio.wait_for(
                    other.execute(
                        update(User)
                        .where(User.email == OWNER)
                        .values(password_hash=None, google_sub="google-subject-meanwhile")
                    ),
                    10,
                )
                await other.commit()
            return await real(*args, **kwargs)

        monkeypatch.setattr(reset_service, "request_reset", a_google_link_lands_before_the_mint)
        out, err = _streams()
        code = await script.issue(
            session, email=OWNER, settings=settings, store=store, out=out, err=err
        )

        assert code == script.REFUSED
        assert err.getvalue() == GOOGLE_REFUSAL + "\n"
        assert "/reset-password#" not in out.getvalue() + err.getvalue()
        assert SENTINEL not in out.getvalue() + err.getvalue()
        password_hash, cutoff, google_sub = await _credentials(engine, OWNER)
        assert password_hash is None  # no password given back
        assert google_sub == "google-subject-meanwhile"
        assert cutoff is not None  # the sessions have ended, as the Redis line said
        # The link that was minted cannot be used, and using it makes no password.
        dead = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": SENTINEL, "newPassword": NEW_PASSWORD},
        )
        assert dead.status_code == 400, dead.text
        assert (await _credentials(engine, OWNER))[0] is None

    async def test_a_refused_mint_replaces_the_password_and_prints_the_founders_line(
        self,
        client: AsyncClient,
        session: AsyncSession,
        engine: AsyncEngine,
        settings: Settings,
        store: SessionStore,
    ) -> None:
        """The real refusal, not a stand-in: another process holds the advisory lock a reset takes
        for this account, so `request_reset` mints nothing and returns None."""
        await _sign_up(client)
        before = await _credentials(engine, OWNER)
        key = {"key": f"password_reset:{await _user_id(session, OWNER)}"}

        out, err = _streams()
        async with engine.connect() as holder:
            await holder.execute(text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), key)
            try:
                code = await script.issue(
                    session, email=OWNER, settings=settings, store=store, out=out, err=err
                )
            finally:
                await holder.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), key
                )
                await holder.rollback()

        assert code == script.REFUSED
        assert err.getvalue() == FOUNDERS_REFUSAL + "\n"
        assert "/reset-password#" not in out.getvalue()
        after = await _credentials(engine, OWNER)
        assert after[0] not in (None, before[0])  # the password was replaced on this branch too
        assert after[1] is not None  # and the sessions ended
        assert (await session.execute(select(PasswordResetToken))).scalars().all() == []
        client.cookies.clear()
        assert await _login(client, PASSWORD) == 401

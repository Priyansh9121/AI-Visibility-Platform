"""The scan-finished email's switch, setting and loop — F1, 2026-10-06.

`EMAIL_SCAN_NOTIFICATIONS` (off by default, a blank is off, a bad value stops the start naming
the variable and not the value); the per-user choice (`users.scan_emails`, default on) read on
`GET /auth/me` as `scanEmails {available, enabled}` and written only through
`PUT /auth/me/scan-emails`, for the caller's own row; and the loop that runs the sweep, started
only when the feature is available and stopped within a bound.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from avp_api import ids, main, redis_watch, scan_email_watch
from avp_api.config import Settings
from avp_api.models import Agency, User, UserRole
from avp_api.services import scan_emails

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
OWNER = "owner@switch.example"
TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"
AUTH_ROUTER = Path(__file__).resolve().parents[1] / "src" / "avp_api" / "routers" / "auth.py"
DB = "postgresql+asyncpg://avp:avp@db.internal:5432/avp"


def _settings() -> Settings:
    return Settings(_env_file=None, environment="local", database_url=DB, redis_url="redis://x/0")


# ---------------------------------------------------------------------------
# the setting
# ---------------------------------------------------------------------------


def test_the_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_SCAN_NOTIFICATIONS", raising=False)
    assert _settings().email_scan_notifications is False


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_value_is_off(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("EMAIL_SCAN_NOTIFICATIONS", blank)
    assert _settings().email_scan_notifications is False


@pytest.mark.parametrize(("raw", "value"), [("true", True), ("1", True), (" TRUE ", True),
                                            ("false", False), ("0", False)])
def test_a_boolean_is_read_as_one(monkeypatch: pytest.MonkeyPatch, raw: str, value: bool) -> None:
    monkeypatch.setenv("EMAIL_SCAN_NOTIFICATIONS", raw)
    assert _settings().email_scan_notifications is value


def test_a_value_that_is_not_a_boolean_stops_the_start_and_does_not_repeat_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_SCAN_NOTIFICATIONS", "sometimes-SENTINELVALUE")
    with pytest.raises(ValidationError, match="email_scan_notifications") as failure:
        _settings()
    assert "SENTINELVALUE" not in str(failure.value)


def test_the_template_documents_it_and_ships_it_off() -> None:
    text = TEMPLATE.read_text()
    assert "\nEMAIL_SCAN_NOTIFICATIONS=false\n" in text
    assert "OFF by default" in text and "RESEND_API_KEY is\n# unset" in text


# ---------------------------------------------------------------------------
# the context carries the switch
# ---------------------------------------------------------------------------


async def _sign_up(client: AsyncClient, email: str = OWNER) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Switch Agency", "fullName": "Owner", "email": email,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


@pytest.fixture
def available(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "email_scan_notifications", True)
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))


async def test_by_default_the_switch_is_unavailable_and_the_choice_is_on(
    client: AsyncClient,
) -> None:
    signed_up = await _sign_up(client)
    assert signed_up["scanEmails"] == {"available": False, "enabled": True}
    me = await client.get(f"{BASE}/auth/me")
    assert me.json()["scanEmails"] == {"available": False, "enabled": True}


async def test_with_the_setting_and_email_it_is_available(
    client: AsyncClient, available: None
) -> None:
    await _sign_up(client)
    assert (await client.get(f"{BASE}/auth/me")).json()["scanEmails"] == {
        "available": True, "enabled": True,
    }


@pytest.mark.parametrize(
    ("setting", "key"), [(True, None), (False, "k")], ids=["setting only", "email only"]
)
async def test_one_of_the_two_is_not_enough(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch,
    setting: bool, key: str | None,
) -> None:
    monkeypatch.setattr(settings, "email_scan_notifications", setting)
    monkeypatch.setattr(settings, "resend_api_key", None if key is None else SecretStr(key))
    await _sign_up(client)
    assert (await client.get(f"{BASE}/auth/me")).json()["scanEmails"]["available"] is False


async def test_the_signed_out_context_says_nothing_of_it(
    client: AsyncClient, available: None
) -> None:
    resp = await client.get(f"{BASE}/auth/me")
    assert resp.status_code == 401
    assert "scanEmails" not in resp.text


async def test_the_schema_requires_it_on_the_context_and_nowhere_else(client: AsyncClient) -> None:
    schema = (await client.get(f"{BASE}/openapi.json")).json()["components"]["schemas"]
    assert "scanEmails" in schema["MeOut"]["required"]
    assert set(schema["ScanEmailsOut"]["properties"]) == {"available", "enabled"}
    homes = [n for n, body in schema.items() if "scanEmails" in body.get("properties", {})]
    assert homes == ["MeOut"], "other users' choices must not be on the seat list"


def test_every_construction_of_the_context_decides_it() -> None:
    tree = ast.parse(AUTH_ROUTER.read_text())
    builds = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "MeOut"
    ]
    assert builds
    for build in builds:
        given = {kw.arg: ast.unparse(kw.value) for kw in build.keywords}
        assert given.get("scan_emails", "").startswith("_scan_emails_for("), ast.unparse(build)


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------


async def _choice(session: AsyncSession, email: str) -> bool:
    session.expire_all()
    return (await session.execute(select(User.scan_emails).where(User.email == email))).scalar_one()


async def test_turning_it_off_and_on_is_saved_and_read_back(
    client: AsyncClient, session: AsyncSession, available: None
) -> None:
    await _sign_up(client)

    off = await client.put(f"{BASE}/auth/me/scan-emails", json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json() == {"available": True, "enabled": False}
    assert (await client.get(f"{BASE}/auth/me")).json()["scanEmails"]["enabled"] is False
    assert await _choice(session, OWNER) is False

    on = await client.put(f"{BASE}/auth/me/scan-emails", json={"enabled": True})
    assert on.json() == {"available": True, "enabled": True}
    assert await _choice(session, OWNER) is True


async def test_it_changes_only_the_callers_own_row(
    client: AsyncClient, session: AsyncSession
) -> None:
    signed_up = await _sign_up(client)
    agency_id = signed_up["agency"]["id"]
    other_agency = Agency(id=ids.new_id(ids.AGENCY), name="Elsewhere", slug="elsewhere")
    session.add(other_agency)
    await session.flush()
    people = (("mate@switch.example", agency_id), ("far@elsewhere.example", other_agency.id))
    for email, agency in people:
        session.add(User(id=ids.new_id(ids.USER), agency_id=agency, email=email,
                         full_name="Other", role=UserRole.MEMBER, password_hash="x"))
    await session.commit()

    resp = await client.put(f"{BASE}/auth/me/scan-emails", json={"enabled": False})
    assert resp.status_code == 200

    assert await _choice(session, OWNER) is False
    assert await _choice(session, "mate@switch.example") is True
    assert await _choice(session, "far@elsewhere.example") is True


@pytest.mark.parametrize(
    "body",
    [{}, {"enabled": "maybe"}, {"enabled": "false"}, {"enabled": 0}, {"enabled": None},
     {"enabled": False, "userId": "user_someone_else"}, {"enabled": False, "email": "x@y.z"}],
    ids=["empty", "maybe", "string false", "zero", "null", "names a user", "names an email"],
)
async def test_anything_but_a_json_boolean_alone_is_refused_and_changes_nothing(
    client: AsyncClient, session: AsyncSession, body: dict[str, Any]
) -> None:
    await _sign_up(client)
    resp = await client.put(f"{BASE}/auth/me/scan-emails", json=body)
    assert resp.status_code == 422, resp.text
    assert await _choice(session, OWNER) is True


async def test_it_needs_a_session(client: AsyncClient) -> None:
    resp = await client.put(f"{BASE}/auth/me/scan-emails", json={"enabled": False})
    assert resp.status_code == 401


async def test_it_is_accepted_while_the_feature_is_off_and_says_so(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _sign_up(client)
    resp = await client.put(f"{BASE}/auth/me/scan-emails", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "enabled": False}


async def test_the_answer_carries_nothing_else(client: AsyncClient) -> None:
    await _sign_up(client)
    resp = await client.put(f"{BASE}/auth/me/scan-emails", json={"enabled": False})
    assert set(resp.json()) == {"available", "enabled"}
    assert OWNER not in resp.text


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------


async def _until(predicate: Any, seconds: float = 5.0) -> None:
    try:
        async with asyncio.timeout(seconds):
            while not predicate():
                await asyncio.sleep(0.005)
    except TimeoutError:
        pytest.fail(f"still waiting after {seconds}s")


def _tasks() -> list[asyncio.Task[Any]]:
    return [t for t in asyncio.all_tasks() if t.get_name() == scan_email_watch.TASK_NAME]


@pytest.fixture
def swept(monkeypatch: pytest.MonkeyPatch) -> list[Settings]:
    calls: list[Settings] = []

    async def fake_sweep(settings: Settings, **_: Any) -> int:
        calls.append(settings)
        return 0

    monkeypatch.setattr(scan_emails, "sweep", fake_sweep)
    return calls


@pytest.fixture
def feature(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"email_scan_notifications": True, "resend_api_key": SecretStr("re_k")}
    )


class TestTheLoop:
    async def test_it_sweeps_at_once_and_again_after_every_interval(
        self, feature: Settings, swept: list[Settings]
    ) -> None:
        task = asyncio.create_task(scan_email_watch.watch_scans(feature, interval_s=0.01))
        try:
            await _until(lambda: len(swept) >= 3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()

    async def test_it_does_not_sweep_again_before_the_interval_is_up(
        self, feature: Settings, swept: list[Settings]
    ) -> None:
        task = asyncio.create_task(scan_email_watch.watch_scans(feature, interval_s=60.0))
        try:
            await _until(lambda: len(swept) == 1)
            await asyncio.sleep(0.2)
            assert len(swept) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_a_sweep_that_raises_is_logged_by_class_and_the_loop_goes_on(
        self, feature: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def flaky(settings: Settings, **_: Any) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("db said LOOPSENTINEL")
            return 0

        monkeypatch.setattr(scan_emails, "sweep", flaky)
        with capture_logs() as logs:
            task = asyncio.create_task(scan_email_watch.watch_scans(feature, interval_s=0.01))
            try:
                await _until(lambda: calls >= 3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        failed = [e for e in logs if e["event"] == "scan_emails.sweep_failed"]
        assert failed == [{"event": "scan_emails.sweep_failed", "error": "RuntimeError",
                           "log_level": "warning"}]
        assert "LOOPSENTINEL" not in repr(logs)

    def test_the_default_interval_is_a_minute(self) -> None:
        assert scan_email_watch.SWEEP_INTERVAL_S == 60.0


class TestStartAndStop:
    async def test_nothing_starts_when_the_setting_is_off(
        self, settings: Settings, swept: list[Settings]
    ) -> None:
        off = settings.model_copy(
            update={"email_scan_notifications": False, "resend_api_key": SecretStr("k")}
        )
        with capture_logs() as logs:
            assert scan_email_watch.start(off) is None
            await asyncio.sleep(0.05)
        assert _tasks() == [] and swept == [] and logs == []

    async def test_nothing_starts_without_email_and_one_line_says_why(
        self, settings: Settings, swept: list[Settings]
    ) -> None:
        unconfigured = settings.model_copy(
            update={"email_scan_notifications": True, "resend_api_key": None}
        )
        with capture_logs() as logs:
            assert scan_email_watch.start(unconfigured) is None
            await asyncio.sleep(0.05)
        assert _tasks() == [] and swept == []
        assert logs == [{"event": "scan_emails.not_started",
                         "reason": "email is not configured", "log_level": "info"}]

    async def test_it_starts_a_named_task_when_both_are_set(
        self, feature: Settings, swept: list[Settings]
    ) -> None:
        with capture_logs() as logs:
            task = scan_email_watch.start(feature)
            assert task is not None
            try:
                assert _tasks() == [task]
                await _until(lambda: len(swept) >= 1)
            finally:
                await scan_email_watch.stop(task)
        assert task.cancelled() and _tasks() == []
        assert [e["event"] for e in logs] == ["scan_emails.watch_started"]

    async def test_a_second_stop_and_a_stop_with_nothing_are_harmless(
        self, feature: Settings, swept: list[Settings]
    ) -> None:
        task = scan_email_watch.start(feature)
        await scan_email_watch.stop(task)
        await scan_email_watch.stop(task)
        await scan_email_watch.stop(None)

    async def test_stop_does_not_wait_for_ever_on_a_sweep_that_will_not_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scan_email_watch, "STOP_TIMEOUT_S", 0.05)
        release = asyncio.Event()

        async def stubborn() -> None:
            while True:
                try:
                    await release.wait()
                    return
                except asyncio.CancelledError:
                    continue

        task = asyncio.create_task(stubborn())
        await asyncio.sleep(0)
        stopping = asyncio.create_task(scan_email_watch.stop(task))
        try:
            finished, _ = await asyncio.wait({stopping}, timeout=5)
            assert finished, "`stop` is still waiting on a task that ignores its cancellation"
        finally:
            release.set()
            await asyncio.gather(stopping, task, return_exceptions=True)

    def test_stop_waits_a_bounded_few_seconds(self) -> None:
        assert 0 < scan_email_watch.STOP_TIMEOUT_S <= 10


@pytest.fixture
def lifespan_without_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(*_: Any) -> None:
        return None

    monkeypatch.setattr(main, "dispose_engine", nothing)
    monkeypatch.setattr(main, "close_redis", nothing)
    monkeypatch.setattr(redis_watch, "start", nothing)


def _bare_app(settings: Settings) -> Any:
    return SimpleNamespace(state=SimpleNamespace(settings=settings))


@pytest.mark.usefixtures("lifespan_without_pools")
class TestTheLifespan:
    async def test_the_sweep_runs_in_the_running_app_and_is_gone_after_shutdown(
        self, feature: Settings, swept: list[Settings]
    ) -> None:
        with capture_logs():
            async with main.lifespan(_bare_app(feature)):
                await _until(lambda: len(swept) >= 1)
                assert len(_tasks()) == 1
        assert _tasks() == []

    async def test_with_the_feature_off_the_app_starts_with_no_task_and_no_line(
        self, settings: Settings, swept: list[Settings]
    ) -> None:
        with capture_logs() as logs:
            async with main.lifespan(_bare_app(settings)):
                await asyncio.sleep(0.05)
                assert _tasks() == []
        assert swept == []
        assert not [e for e in logs if e["event"].startswith("scan_emails.")]

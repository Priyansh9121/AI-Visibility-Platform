"""Platform-wide daily cost breakers — D2, 2026-10-10.

`services/spend_caps` is one atomic Redis claim per kind and UTC day; `scan_start.start_scan`
takes `scans` (and `free_scans` for an agency that is not paid up) after the allowance and
before any row or provider. These tests drive the real route with an executor that records the
job and never runs it, so "no provider was called" is "no job was handed off".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.deps import scan_executor
from avp_api.errors import PlatformCapReached
from avp_api.models import (
    Agency,
    Client,
    Scan,
    ScanStatus,
    ScanTrigger,
    User,
    UserRole,
    UserStatus,
)
from avp_api.services import auto_rescan, spend_caps

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
EMAIL = "caps-owner@test.example"
FULL = (
    "AVP has started as many scans as it allows in a day, so this one was not started. "
    "Try again after midnight UTC."
)
FREE = (
    "AVP has started as many free scans as it allows in a day, so this one was not "
    "started. Try again after midnight UTC."
)


def _defer(client: AsyncClient) -> list:
    jobs: list = []

    class _Deferred:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
    return jobs


async def _sign_up(client: AsyncClient, email: str = EMAIL) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Caps Agency", "fullName": "Op", "email": email,
              "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    agency_id = resp.json()["agency"]["id"]
    factory = async_sessionmaker(bind=_engine_of(client), expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.agency_id == agency_id))).scalar_one()
        user.google_sub = f"sub-{email}"
        await s.commit()
    return agency_id


def _engine_of(client: AsyncClient):  # noqa: ANN202
    from avp_api.db import get_sessionmaker

    return get_sessionmaker().kw["bind"]


async def _make_client(client: AsyncClient, domain: str) -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _start(client: AsyncClient, cid: str) -> Any:
    return await client.post(f"{BASE}/clients/{cid}/scans", json={})


async def _scan_rows(agency_id: str) -> int:
    from avp_api.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        return int((await s.execute(
            select(func.count()).select_from(Scan).where(Scan.agency_id == agency_id)
        )).scalar_one())


async def _set_subscription(agency_id: str, status: str | None) -> None:
    from avp_api.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        agency = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        agency.subscription_status = status
        await s.commit()


async def _count(settings: Settings, kind: str) -> int:
    redis = redis_module.get_redis(settings)
    raw = await redis.get(spend_caps.day_key(kind, datetime.now(UTC)))
    return int(raw) if raw else 0


@pytest.fixture
def billing_off(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    monkeypatch.setattr(settings, "stripe_price_id", None)


@pytest.fixture
def billing_on(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_" + "test_fake"))
    monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")


class TestTheClaim:
    async def test_twenty_concurrent_claims_against_a_cap_of_five_let_exactly_five_through(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 5)
        redis = redis_module.get_redis(settings)

        async def one() -> bool:
            try:
                await spend_caps.claim(redis, settings, kinds=["scans"], agency_id="agy_x")
            except PlatformCapReached:
                return False
            return True

        got = await asyncio.gather(*(one() for _ in range(20)))
        assert sum(got) == 5
        assert await _count(settings, "scans") == 5  # a refusal increments nothing

    async def test_the_key_carries_the_utc_day_and_a_48_hour_ttl(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        redis = redis_module.get_redis(settings)
        moment = datetime(2026, 10, 10, 23, 59, tzinfo=UTC)
        await spend_caps.claim(
            redis, settings, kinds=["scans"], agency_id="agy_x", now=moment
        )
        assert await redis.get("cap:scans:2026-10-10") == "1"
        ttl = await redis.pttl("cap:scans:2026-10-10")
        assert 47 * 3600 * 1000 < ttl <= 48 * 3600 * 1000

    def test_retry_after_is_the_seconds_to_the_next_utc_midnight(self) -> None:
        assert spend_caps.seconds_to_midnight(datetime(2026, 10, 10, 23, 59, 30, tzinfo=UTC)) == 30
        assert spend_caps.seconds_to_midnight(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)) == 86400
        # Rounded up, never below one.
        edge = datetime(2026, 10, 10, 23, 59, 59, 500000, tzinfo=UTC)
        assert spend_caps.seconds_to_midnight(edge) == 1


class TestScanStart:
    async def test_the_second_scan_is_refused_with_the_sentence_and_nothing_is_written(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 1)
        jobs = _defer(client)
        agency_id = await _sign_up(client)
        first = await _make_client(client, "one.com")
        second = await _make_client(client, "two.com")

        accepted = await _start(client, first)
        assert accepted.status_code == 202, accepted.text
        assert len(jobs) == 1

        with structlog.testing.capture_logs() as logs:
            refused = await _start(client, second)

        assert refused.status_code == 503, refused.text
        assert refused.headers["content-type"].startswith("application/problem+json")
        body = refused.json()
        assert body["type"] == "/problems/platform-cap-reached"
        assert body["detail"] == FULL
        assert body["cap"] == "scans"
        assert "limit" not in body  # the platform's number is for the log, not the caller
        assert 1 <= int(refused.headers["retry-after"]) <= 86400
        assert len(jobs) == 1  # no provider was reached for the refusal
        assert await _scan_rows(agency_id) == 1  # no row for the refusal
        events = [e for e in logs if e["event"] == "scan.platform_cap_reached"]
        assert events == [{
            "event": "scan.platform_cap_reached", "log_level": "warning",
            "cap": "scans", "limit": 1, "agency_id": agency_id,
        }]
        assert EMAIL not in repr(logs)

    async def test_cap_zero_refuses_every_new_scan(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 0)
        jobs = _defer(client)
        agency_id = await _sign_up(client)
        cid = await _make_client(client, "zero.com")
        resp = await _start(client, cid)
        assert resp.status_code == 503
        assert resp.json()["type"] == "/problems/platform-cap-reached"
        assert jobs == []
        assert await _scan_rows(agency_id) == 0

    async def test_the_free_cap_counts_unpaid_agencies_only(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_signup_free_scans_per_day", 0)
        jobs = _defer(client)
        agency_id = await _sign_up(client)
        first = await _make_client(client, "paid.com")
        second = await _make_client(client, "unpaid.com")

        await _set_subscription(agency_id, "active")
        paid = await _start(client, first)
        assert paid.status_code == 202, paid.text
        assert await _count(settings, "free_scans") == 0
        assert await _count(settings, "scans") == 1

        await _set_subscription(agency_id, None)
        unpaid = await _start(client, second)
        assert unpaid.status_code == 503
        assert unpaid.json()["detail"] == FREE
        assert unpaid.json()["cap"] == "free_scans"
        assert len(jobs) == 1

    async def test_repeated_refused_free_starts_spend_no_scans_unit(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_signup_free_scans_per_day", 0)
        jobs = _defer(client)
        await _sign_up(client)
        cid = await _make_client(client, "free-full.com")
        for _ in range(50):
            refused = await _start(client, cid)
            assert refused.status_code == 503
            assert refused.json()["cap"] == "free_scans"
        assert (await _count(settings, "scans"), await _count(settings, "free_scans")) == (0, 0)
        assert jobs == []
        # Every agency's scanning is still open: another sign-up is not turned away.
        monkeypatch.setattr(settings, "max_signup_free_scans_per_day", 5)
        assert (await _start(client, cid)).status_code == 202

    async def test_a_refused_claim_increments_nothing_so_a_raised_cap_opens_the_day(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 0)
        jobs = _defer(client)
        await _sign_up(client)
        cid = await _make_client(client, "raised.com")
        for _ in range(10):
            assert (await _start(client, cid)).status_code == 503
        assert await _count(settings, "scans") == 0
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 2)
        accepted = await _start(client, cid)
        assert accepted.status_code == 202, accepted.text
        assert len(jobs) == 1
        assert await _count(settings, "scans") == 1

    async def test_twenty_parallel_starts_for_one_client_spend_exactly_one_unit(
        self, client: AsyncClient, settings: Settings, billing_off: None
    ) -> None:
        jobs = _defer(client)
        agency_id = await _sign_up(client)
        cid = await _make_client(client, "parallel.com")
        replies = await asyncio.gather(*(_start(client, cid) for _ in range(20)))
        assert {r.status_code for r in replies} == {202}
        assert len({r.json()["id"] for r in replies}) == 1
        assert await _scan_rows(agency_id) == 1
        # Each press may hand the one row to the executor again (it runs it once); only the
        # first submission spends a unit.
        assert jobs and {j.scan_id for j in jobs} == {replies[0].json()["id"]}
        assert (await _count(settings, "scans"), await _count(settings, "free_scans")) == (1, 1)

    async def test_a_start_that_adopts_an_already_submitted_row_spends_nothing(
        self, client: AsyncClient, settings: Settings, billing_off: None
    ) -> None:
        jobs = _defer(client)
        await _sign_up(client)
        cid = await _make_client(client, "adopt.com")
        first = await _start(client, cid)
        assert first.status_code == 202
        assert (await _count(settings, "scans"), await _count(settings, "free_scans")) == (1, 1)

        again = await _start(client, cid)
        assert again.status_code == 202
        assert again.json()["id"] == first.json()["id"]
        assert (await _count(settings, "scans"), await _count(settings, "free_scans")) == (1, 1)
        # A person's second press re-hands the same row to the executor (the recovery of a job
        # lost with its executor), and spends nothing.
        assert [j.scan_id for j in jobs] == [first.json()["id"]] * 2

    async def test_an_allowance_refusal_spends_no_platform_unit(
        self, client: AsyncClient, settings: Settings, billing_on: None
    ) -> None:
        _defer(client)
        await _sign_up(client)
        first = await _make_client(client, "a.com")
        second = await _make_client(client, "b.com")
        assert (await _start(client, first)).status_code == 202
        assert (await _count(settings, "scans"), await _count(settings, "free_scans")) == (1, 1)

        refused = await _start(client, second)
        assert refused.status_code == 402
        assert (await _count(settings, "scans"), await _count(settings, "free_scans")) == (1, 1)

    async def test_the_caps_apply_when_the_billing_gate_is_off(
        self, client: AsyncClient, settings: Settings, billing_off: None
    ) -> None:
        _defer(client)
        await _sign_up(client)
        cid = await _make_client(client, "off.com")
        assert (await _start(client, cid)).status_code == 202
        assert await _count(settings, "scans") == 1

    async def test_redis_down_is_a_503_and_no_row_is_written(
        self, client: AsyncClient, billing_off: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        jobs = _defer(client)
        agency_id = await _sign_up(client)
        cid = await _make_client(client, "down.com")

        async def down(*args: Any, **kwargs: Any) -> int:
            raise OSError("SENTINEL-redis-host is down")

        monkeypatch.setattr(spend_caps, "_eval", down)
        with structlog.testing.capture_logs() as logs:
            resp = await _start(client, cid)
        assert resp.status_code == 503
        assert resp.json()["type"] == "/problems/service-unavailable"
        assert "SENTINEL" not in resp.text and "SENTINEL" not in repr(logs)
        assert jobs == []
        assert await _scan_rows(agency_id) == 0

    async def test_a_scan_already_running_is_untouched_when_the_cap_trips(
        self, client: AsyncClient, settings: Settings, billing_off: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        jobs = _defer(client)
        agency_id = await _sign_up(client)
        cid = await _make_client(client, "running.com")
        from avp_api.db import get_sessionmaker

        scan_id = ids.new_id(ids.SCAN)
        async with get_sessionmaker()() as s:
            s.add(Scan(
                id=scan_id, client_id=cid, agency_id=agency_id, status=ScanStatus.RUNNING,
                trigger=ScanTrigger.MANUAL, formula_version="test", prompt_count=0,
                engine_result_count=0,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                submitted_at=datetime.now(UTC),
            ))
            await s.commit()
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 0)

        resp = await _start(client, cid)
        assert resp.status_code == 202, resp.text
        assert resp.json()["id"] == scan_id
        assert jobs == []
        assert await _count(settings, "scans") == 0
        async with get_sessionmaker()() as s:
            row = (await s.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
            assert row.status is ScanStatus.RUNNING


async def test_a_start_that_races_to_a_running_scan_returns_it_whole(
    client: AsyncClient, settings: Settings, billing_off: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first look finds no open scan; by the time the row is adopted, it is RUNNING.

    The branch rolls back, and a rollback expires every loaded object: without reading the row
    again, serialising it fails outside the async context and the start answers 500.
    """
    from avp_api.db import get_sessionmaker
    from avp_api.services import competitors as detection

    jobs = _defer(client)
    agency_id = await _sign_up(client)
    cid = await _make_client(client, "raced.com")
    scan_id = ids.new_id(ids.SCAN)
    async with get_sessionmaker()() as s:
        s.add(Scan(
            id=scan_id, client_id=cid, agency_id=agency_id, status=ScanStatus.RUNNING,
            trigger=ScanTrigger.MANUAL, formula_version="test", prompt_count=0,
            engine_result_count=0,
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            submitted_at=datetime.now(UTC),
        ))
        await s.commit()
    real_open_scan = detection.open_scan
    calls: list[int] = []

    async def misses_once(session, client_id, *, agency_id):  # noqa: ANN001, ANN202
        calls.append(1)
        if len(calls) == 1:
            return None
        return await real_open_scan(session, client_id, agency_id=agency_id)

    monkeypatch.setattr(detection, "open_scan", misses_once)
    resp = await _start(client, cid)
    assert resp.status_code == 202, resp.text
    assert resp.json()["id"] == scan_id
    assert resp.json()["status"] == "running"
    assert jobs == []
    assert await _count(settings, "scans") == 0


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class _Recorder:
    def __init__(self) -> None:
        self.jobs: list[Any] = []

    async def submit(self, job, *, settings) -> None:  # noqa: ANN001
        self.jobs.append(job)


class TestAutoRescan:
    async def _seed(self, factory: async_sessionmaker[AsyncSession], n: int) -> list[str]:
        async with factory() as s:
            agency = Agency(id=ids.new_id(ids.AGENCY), name="A", slug="caps-a")
            agency.subscription_status = "active"
            s.add(agency)
            await s.flush()
            user = User(
                id=ids.new_id(ids.USER), agency_id=agency.id, email="o@caps.example",
                full_name="O", role=UserRole.OWNER, status=UserStatus.ACTIVE, google_sub="g",
            )
            s.add(user)
            await s.flush()
            out: list[str] = []
            for i in range(n):
                c = Client(
                    id=ids.new_id(ids.CLIENT), agency_id=agency.id, name=f"c{i}",
                    domain=f"c{i}.example", auto_rescan=True, auto_rescan_by_user_id=user.id,
                )
                s.add(c)
                await s.flush()
                when = NOW - timedelta(days=40 + i)
                old = Scan(
                    id=ids.new_id(ids.SCAN), client_id=c.id, agency_id=agency.id,
                    status=ScanStatus.SUCCEEDED, trigger=ScanTrigger.MANUAL,
                    formula_version="test", prompt_count=0, engine_result_count=0,
                )
                old.created_at = when
                old.submitted_at = when
                s.add(old)
                out.append(c.id)
            await s.commit()
            return out

    async def test_a_refused_wake_logs_starts_nothing_and_holds_no_client_for_a_month(
        self, settings: Settings, engine, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
    ) -> None:
        redis_module._pool = None  # noqa: SLF001
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 0)
        cids = await self._seed(factory, 3)
        calls: list[str] = []
        real = auto_rescan.start_scan

        async def counting(db: AsyncSession, **kwargs: Any) -> Any:
            calls.append(kwargs["client"].id)
            return await real(db, **kwargs)

        monkeypatch.setattr(auto_rescan, "start_scan", counting)
        jobs = _Recorder()
        with structlog.testing.capture_logs() as logs:
            started = await auto_rescan.run_once(
                settings, factory=factory, executor=jobs, now=NOW
            )

        assert started is None and jobs.jobs == []
        assert len(calls) == 1  # the wake ended at the first refusal; no hot loop
        assert [e["cap"] for e in logs if e["event"] == "scan.platform_cap_reached"] == ["scans"]
        assert await _count(settings, "scans") == 0
        async with factory() as s:
            rows = (await s.execute(select(Client).where(Client.id.in_(cids)))).scalars().all()
            assert all(r.auto_rescan_skip_reason is None for r in rows)
            assert all(r.auto_rescan_attempted_at is None for r in rows)
            new = (await s.execute(
                select(func.count()).select_from(Scan).where(Scan.status == ScanStatus.QUEUED)
            )).scalar_one()
            assert new == 0

        # The next normal wake, with room under the cap, starts the longest-waiting client.
        monkeypatch.setattr(settings, "max_scans_per_day_platform", 5)
        again = await auto_rescan.run_once(settings, factory=factory, executor=jobs, now=NOW)
        assert again is not None and len(jobs.jobs) == 1

    async def test_a_started_rescan_claims_once_for_the_row_it_submits(
        self, settings: Settings, engine, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
    ) -> None:
        redis_module._pool = None  # noqa: SLF001
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        await self._seed(factory, 1)
        jobs = _Recorder()
        started = await auto_rescan.run_once(settings, factory=factory, executor=jobs, now=NOW)
        assert started is not None and len(jobs.jobs) == 1
        redis = redis_module.get_redis(settings)
        assert await redis.get(spend_caps.day_key("scans", NOW)) == "1"
        assert await redis.get(spend_caps.day_key("free_scans", NOW)) is None  # paid agency


@pytest.mark.parametrize(
    ("platform", "free", "ok"),
    [(60, 20, True), (60, 60, True), (60, 61, False), (0, 20, True), (0, 0, True)],
)
def test_the_free_cap_may_not_exceed_the_platform_cap(platform: int, free: int, ok: bool) -> None:
    """Above it, unpaid traffic could take every unit; a platform cap of 0 is the kill switch."""
    from pydantic import ValidationError

    def build() -> Settings:
        return Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="test",
            max_scans_per_day_platform=platform,
            max_signup_free_scans_per_day=free,
        )

    if ok:
        assert build().max_signup_free_scans_per_day == free
    else:
        with pytest.raises(ValidationError, match="must not be above"):
            build()

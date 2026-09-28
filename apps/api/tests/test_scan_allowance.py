"""The scan allowance — one free scan, then forty a month — 2026-09-28.

The gate is enforced server-side in `POST /clients/{clientId}/scans`, under a
row lock on the agency, and only when billing is configured. These tests turn
billing on with fake Stripe settings (the way `test_billing.py` does), seed
scans directly so a "completed" or "failed" scan is a fact in the table rather
than a run of stubbed engines, and drive the endpoint with an executor that
records the job and never runs it — the `test_scan_race.py` pattern.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import Agency, Scan, ScanStatus, ScanTrigger
from avp_api.services import scan_allowance as allowance

BASE = "/api/v1"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def billing_on(settings, monkeypatch):  # noqa: ANN001, ANN201
    """Billing configured, so the gate is ON. No Stripe call is ever made."""
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_fake"))
    monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")


@pytest.fixture
def billing_off(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    monkeypatch.setattr(settings, "stripe_price_id", None)


def _defer(client: AsyncClient) -> list:
    jobs: list = []

    class _Deferred:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
    return jobs


async def _sign_up(client: AsyncClient, email: str = "allowance@test.example") -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Allowance Test Agency", "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["agency"]["id"]


async def _make_client(client: AsyncClient, domain: str) -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_subscription(engine, agency_id: str, status: str | None) -> None:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        agency = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        agency.subscription_status = status
        await s.commit()


async def _seed_scans(
    engine,  # noqa: ANN001
    *,
    agency_id: str,
    client_id: str,
    n: int,
    status: ScanStatus = ScanStatus.SUCCEEDED,
    created_at: datetime | None = None,
) -> None:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        for _ in range(n):
            scan = Scan(
                id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=agency_id,
                status=status, trigger=ScanTrigger.MANUAL, formula_version="test",
                prompt_count=0, engine_result_count=0,
            )
            if created_at is not None:
                scan.created_at = created_at
            s.add(scan)
        await s.commit()


async def _counted(engine, agency_id: str) -> int:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        return int((await s.execute(
            select(func.count()).select_from(Scan).where(
                Scan.agency_id == agency_id, Scan.status.in_(allowance.COUNTED_STATUSES)
            )
        )).scalar_one())


async def _start(client: AsyncClient, cid: str) -> Any:
    return await client.post(f"{BASE}/clients/{cid}/scans", json={})


# ---------------------------------------------------------------------------
# the constants, argued
# ---------------------------------------------------------------------------


class TestTheNumbersAreArgued:
    def test_the_paid_cap_leaves_a_margin_over_engine_cost(self) -> None:
        # 40 × $1.70 ≈ $68 against $160 — about 57% gross margin before SerpApi.
        engine_cost = allowance.PAID_SCANS_PER_MONTH * 1.70
        assert engine_cost < 160 * 0.5, "the cap must leave at least half the price as margin"

    def test_the_paid_cap_fits_serpapis_free_tier_even_if_every_scan_is_a_new_client(
        self,
    ) -> None:
        # 6 searches per new client, 250 a month on the Free tier.
        assert allowance.PAID_SCANS_PER_MONTH * 6 <= 250

    def test_the_free_allowance_is_one_full_scan(self) -> None:
        assert allowance.FREE_SCAN_ALLOWANCE == 1

    def test_failed_and_cancelled_scans_hold_no_slot(self) -> None:
        assert ScanStatus.FAILED not in allowance.COUNTED_STATUSES
        assert ScanStatus.CANCELLED not in allowance.COUNTED_STATUSES
        # ... and in-flight ones do, or two requests at the cap both get through.
        assert ScanStatus.QUEUED in allowance.COUNTED_STATUSES
        assert ScanStatus.RUNNING in allowance.COUNTED_STATUSES

    def test_the_period_is_the_utc_calendar_month(self) -> None:
        start, next_start = allowance.period_bounds(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
        assert start == datetime(2026, 12, 1, tzinfo=UTC)
        assert next_start == datetime(2027, 1, 1, tzinfo=UTC)
        start, next_start = allowance.period_bounds(datetime(2026, 9, 28, 10, 0, tzinfo=UTC))
        assert start == datetime(2026, 9, 1, tzinfo=UTC)
        assert next_start == datetime(2026, 10, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# free
# ---------------------------------------------------------------------------


class TestFree:
    async def test_a_free_agency_with_no_scans_can_start_one(
        self, client: AsyncClient, billing_on
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        resp = await _start(client, cid)
        assert resp.status_code == 202, resp.text

    async def test_the_same_agency_then_gets_402_free_used(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        first = await _make_client(client, "helpscout.com")
        assert (await _start(client, first)).status_code == 202
        second = await _make_client(client, "zendesk.com")
        resp = await _start(client, second)
        assert resp.status_code == 402, resp.text
        body = resp.json()
        assert body["type"].endswith("/scan-limit-reached")
        assert body["code"] == "SCAN_LIMIT_FREE_USED"
        assert body["scansUsed"] == 1 and body["scanLimit"] == 1
        assert body["periodResetsAt"] is None
        assert "Subscribe" in body["detail"]
        # And nothing was created for the refused request.
        assert await _counted(engine, agency_id) == 1

    async def test_a_failed_scan_does_not_burn_the_free_scan(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1, status=ScanStatus.FAILED)
        assert (await _start(client, cid)).status_code == 202

    async def test_a_cancelled_scan_does_not_burn_it_either(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(
            engine, agency_id=agency_id, client_id=cid, n=1, status=ScanStatus.CANCELLED
        )
        assert (await _start(client, cid)).status_code == 202

    async def test_an_existing_completed_scan_counts_nobody_is_grandfathered(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1,
                          created_at=datetime.now(UTC) - timedelta(days=400))
        resp = await _start(client, cid)
        assert resp.status_code == 402 and resp.json()["code"] == "SCAN_LIMIT_FREE_USED"

    @pytest.mark.parametrize("status", ["canceled", "past_due", "unpaid", "paused", "incomplete"])
    async def test_a_lapsed_subscription_is_free(
        self, client: AsyncClient, billing_on, engine, status: str
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        await _set_subscription(engine, agency_id, status)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1)
        resp = await _start(client, cid)
        assert resp.status_code == 402 and resp.json()["code"] == "SCAN_LIMIT_FREE_USED"

    async def test_a_running_scan_is_reported_not_refused(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        """Nothing is being started, so nothing is gated — the pre-gate contract."""
        agency_id = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(
            engine, agency_id=agency_id, client_id=cid, n=1, status=ScanStatus.RUNNING
        )
        resp = await _start(client, cid)
        assert resp.status_code == 202 and resp.json()["status"] == "running"

    async def test_an_open_queued_scan_is_the_one_being_started_and_is_not_counted_twice(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        """`get_or_create_scan` adopts an open QUEUED row; adopting it is starting it."""
        agency_id = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1, status=ScanStatus.QUEUED)
        assert (await _start(client, cid)).status_code == 202
        assert await _counted(engine, agency_id) == 1


# ---------------------------------------------------------------------------
# paid
# ---------------------------------------------------------------------------


class TestPaid:
    @pytest.mark.parametrize("status", ["active", "trialing"])
    async def test_at_39_this_month_the_fortieth_starts(
        self, client: AsyncClient, billing_on, engine, status: str
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        await _set_subscription(engine, agency_id, status)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=39)
        assert (await _start(client, cid)).status_code == 202
        assert await _counted(engine, agency_id) == 40

    async def test_at_40_the_next_is_402_monthly_with_the_reset_date(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        await _set_subscription(engine, agency_id, "active")
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=40)
        resp = await _start(client, cid)
        assert resp.status_code == 402, resp.text
        body = resp.json()
        assert body["code"] == "SCAN_LIMIT_MONTHLY"
        assert body["scansUsed"] == 40 and body["scanLimit"] == 40
        _, next_start = allowance.period_bounds(datetime.now(UTC))
        assert body["periodResetsAt"] == next_start.isoformat().replace("+00:00", "Z")
        assert next_start.strftime("%d %b %Y") in body["detail"]
        # No upgrade is offered: there is no tier above this one.
        assert "upgrade" not in body["detail"].lower()

    async def test_last_months_scans_do_not_count(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        await _set_subscription(engine, agency_id, "active")
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        start, _ = allowance.period_bounds(datetime.now(UTC))
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=40,
                          created_at=start - timedelta(seconds=1))
        assert (await _start(client, cid)).status_code == 202

    async def test_failed_scans_do_not_count_toward_the_month(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        await _set_subscription(engine, agency_id, "active")
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=39)
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=5, status=ScanStatus.FAILED)
        assert (await _start(client, cid)).status_code == 202


# ---------------------------------------------------------------------------
# off when billing is not configured
# ---------------------------------------------------------------------------


class TestGateOff:
    async def test_no_limit_applies_without_a_stripe_key(
        self, client: AsyncClient, billing_off, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=45)
        assert (await _start(client, cid)).status_code == 202

    async def test_the_billing_response_says_no_limit_applies(
        self, client: AsyncClient, billing_off, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=3)
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["scanLimit"] is None
        assert body["scansUsedThisPeriod"] == 3
        assert body["periodResetsAt"] is None


# ---------------------------------------------------------------------------
# the billing response carries the allowance
# ---------------------------------------------------------------------------


class TestTheBillingResponse:
    async def test_free_reads_one_lifetime(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["scansUsedThisPeriod"] == 0
        assert (body["scanLimit"], body["periodResetsAt"]) == (1, None)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _start(client, cid)
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert (body["scansUsedThisPeriod"], body["scanLimit"]) == (1, 1)

    async def test_paid_reads_this_month_with_the_reset(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        await _set_subscription(engine, agency_id, "active")
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=12)
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert (body["scansUsedThisPeriod"], body["scanLimit"]) == (12, 40)
        _, next_start = allowance.period_bounds(datetime.now(UTC))
        assert body["periodResetsAt"] == next_start.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# the race
# ---------------------------------------------------------------------------


class TestTheLastSlot:
    async def test_two_concurrent_requests_at_39_of_40_produce_exactly_one_scan(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        """Two clients, one agency, one slot. The row lock decides; the count does not.

        Different clients on purpose: `uq_scans_one_open_per_client` would
        collapse two requests for ONE client on its own, and that is not the
        invariant under test.
        """
        agency_id = await _sign_up(client)
        await _set_subscription(engine, agency_id, "active")
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=a, n=39)

        first, second = await asyncio.gather(_start(client, a), _start(client, b))
        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [202, 402], (first.text, second.text)
        assert await _counted(engine, agency_id) == 40


# ---------------------------------------------------------------------------
# prompt re-runs are not scans
# ---------------------------------------------------------------------------


class TestPromptReRunsAreNotScans:
    async def test_a_re_run_does_not_move_the_count_and_is_not_gated(
        self, client: AsyncClient, billing_on, engine, monkeypatch
    ) -> None:  # noqa: ANN001
        """The 30-an-hour throttle governs re-runs; the allowance never does."""
        from decimal import Decimal

        from avp_api.models.engine_result import Sentiment
        from avp_api.services import prompt_runs as service
        from avp_api.services.engines import CitedSource, EngineAnswer

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=eng, engine_version="stub", prompt_text=prompt,
                    text="Help Scout is a good option.",
                    citations=[CitedSource(url="https://g2.com/x", domain="g2.com", position=1)],
                    latency_ms=1,
                )
                for eng in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ANN003, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(service.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(service.extraction_service, "classify_sentiment", fake_sentiment)

        agency_id = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        # The free scan is used...
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1)
        assert (await _start(client, cid)).status_code == 402
        # ...and a re-run still goes through, and moves nothing.
        for _ in range(2):
            resp = await client.post(
                f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best help desk"}
            )
            assert resp.status_code == 201, resp.text
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["scansUsedThisPeriod"] == 1
        assert await _counted(engine, agency_id) == 1


# ---------------------------------------------------------------------------
# the platform operator is exempt
# ---------------------------------------------------------------------------


class TestTheOperatorIsExempt:
    FOUNDER = "priyansh.ranpura9@gmail.com"

    def test_the_default_names_the_founder_and_nobody_else(self) -> None:
        from avp_api.config import Settings

        cfg = Settings(_env_file=None)
        assert cfg.scan_allowance_exempt_emails == [self.FOUNDER]

    def test_the_environment_replaces_the_default_lower_cased(self, monkeypatch) -> None:  # noqa: ANN001
        from avp_api.config import Settings

        monkeypatch.setenv("SCAN_ALLOWANCE_EXEMPT_EMAILS", " Ops@Example.com, second@example.com ,")
        cfg = Settings(_env_file=None)
        assert cfg.scan_allowance_exempt_emails == ["ops@example.com", "second@example.com"]
        monkeypatch.setenv("SCAN_ALLOWANCE_EXEMPT_EMAILS", "")
        assert Settings(_env_file=None).scan_allowance_exempt_emails == []

    def test_matching_is_case_insensitive(self, settings, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.FOUNDER])
        assert allowance.is_exempt(settings, "Priyansh.Ranpura9@Gmail.com")
        assert not allowance.is_exempt(settings, "someone@example.com")
        assert not allowance.is_exempt(settings, None)

    async def test_the_founder_scans_past_the_free_allowance(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """Signed in as the exempt address, with the free scan long used."""
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.FOUNDER])
        agency_id = await _sign_up(client, email=self.FOUNDER)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=45)
        assert (await _start(client, cid)).status_code == 202
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["scanLimit"] is None and body["periodResetsAt"] is None
        assert body["scansUsedThisPeriod"] == 46

    async def test_anyone_else_is_still_gated(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.FOUNDER])
        agency_id = await _sign_up(client, email="someone@example.com")
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1)
        assert (await _start(client, cid)).status_code == 402

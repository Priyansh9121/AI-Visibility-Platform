"""The scan allowance — one free scan, then forty a month — 2026-09-28.

The gate is enforced server-side in `POST /clients/{clientId}/scans`, under a
row lock on the agency, and only when billing is configured. These tests turn
billing on with fake Stripe settings (the way `test_billing.py` does), seed
scans directly so a "completed" or "failed" scan is a fact in the table rather
than a run of stubbed engines, and drive the endpoint with an executor that
records the job and never runs it — the `test_scan_race.py` pattern.

Since 2026-09-29 the free scan needs a linked Google identity, so `_sign_up`
links one by default and the tests that are about the count read exactly as
they did. The password-only account is asked for by name, `google=False`,
wherever the verification rule is what is under test.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api.db import get_sessionmaker
from avp_api.deps import google_provider, scan_executor
from avp_api.models import Agency, Scan, ScanStatus, ScanTrigger, User, UserRole
from avp_api.services import auth as auth_service
from avp_api.services import billing as billing_service
from avp_api.services import scan_allowance as allowance
from avp_api.services.google_oauth import GoogleIdentity

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"


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


async def _sign_up(
    client: AsyncClient, email: str = "allowance@test.example", *, google: bool = True
) -> str:
    """An agency through the real sign-up route, its owner signed in. Returns its id.

    Google-linked by default — see the module docstring. The link is written
    straight to the row, as `_link_google` writes it and for its reason.
    `google=False` leaves the password-only account `POST /auth/sign-up`
    actually creates, which is the one the verification rule refuses.
    """
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Allowance Test Agency", "fullName": "Op",
              "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    if google:
        async with get_sessionmaker()() as s:
            user_id = resp.json()["user"]["id"]
            user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
            user.google_sub = f"sub-{email}"
            await s.commit()
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


async def _link_google(engine, agency_id: str, sub: str = "sub-operator") -> None:  # noqa: ANN001
    """Give the agency's owner a linked Google identity.

    What `services/google_oauth` writes when the person signs in with Google.
    Set directly here because the exemption's question is only whether the
    column is populated, and driving the whole OAuth dance to populate it is
    `test_google_sign_in.py`'s job, not this file's.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.agency_id == agency_id))).scalars().first()
        assert user is not None
        user.google_sub = sub
        await s.commit()


async def _seed_scans(
    engine,  # noqa: ANN001
    *,
    agency_id: str,
    client_id: str,
    n: int,
    status: ScanStatus = ScanStatus.SUCCEEDED,
    created_at: datetime | None = None,
    submitted_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
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
            scan.submitted_at = submitted_at
            scan.lease_expires_at = lease_expires_at
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


async def _scan_rows(engine, agency_id: str) -> int:  # noqa: ANN001
    """Every scan row the agency has, in any status — "none was created" means none."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        return int((await s.execute(
            select(func.count()).select_from(Scan).where(Scan.agency_id == agency_id)
        )).scalar_one())


async def _seat_password_teammate(settings, agency_id: str, email: str) -> None:  # noqa: ANN001
    """A MEMBER seat in the agency, with a password and no Google link."""
    async with get_sessionmaker()() as s:
        await auth_service.create_user_in_agency(
            s, agency_id=agency_id, email=email, full_name="Teammate",
            password=PASSWORD, role=UserRole.MEMBER, settings=settings,
        )
        await s.commit()


async def _sign_in(client: AsyncClient, email: str) -> None:
    resp = await client.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text


class _FakeGoogle:
    """Google, faked at the seam `deps.google_provider` opens.

    `test_google_sign_in.py`'s pattern, cut down to one identity: the
    authorization URL carries the state, and the ID token echoes the nonce the
    flow minted, as a real one would. Everything past the provider — the state
    store, the callback, the linking policy, the cookie — runs for real.
    """

    def __init__(self, identity: GoogleIdentity) -> None:
        self.identity = identity
        self.nonce: str | None = None

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:  # noqa: ARG002
        self.nonce = nonce
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    async def exchange(self, *, code: str, code_verifier: str) -> GoogleIdentity:  # noqa: ARG002
        return replace(self.identity, nonce=self.nonce)


def _google_answers_as(
    client: AsyncClient, settings, monkeypatch, *, sub: str, email: str  # noqa: ANN001
) -> None:
    """Google configured on this deployment, and faked to vouch for one identity."""
    monkeypatch.setattr(settings, "google_oauth_client_id", "test.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_oauth_client_secret", SecretStr("not-a-secret"))
    monkeypatch.setattr(
        settings, "google_oauth_redirect_url", "http://localhost:8000/api/v1/auth/google/callback"
    )
    fake = _FakeGoogle(GoogleIdentity(
        sub=sub, email=email, email_verified=True, name="Op", nonce=None,
    ))
    client._transport.app.dependency_overrides[google_provider] = lambda: fake  # noqa: SLF001


async def _continue_with_google(client: AsyncClient) -> Any:
    """Press the notice's button and come back: `/start`, then the callback's 302."""
    started = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert started.status_code == 302, started.text
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    back = await client.get(
        f"{BASE}/auth/google/callback",
        params={"code": "code-1", "state": state},
        follow_redirects=False,
    )
    assert back.status_code == 302, back.text
    return back


# ---------------------------------------------------------------------------
# the constants, argued
# ---------------------------------------------------------------------------


class TestTheNumbersAreArgued:
    def test_the_paid_cap_leaves_a_margin_over_engine_cost(self) -> None:
        # 40 × $1.70 ≈ $68 against $150 — about 55% gross margin before SerpApi (it was $160, about
        # 57%, until the founder's repricing of 2026-10-04). The price is the one the API checks
        # Stripe against, so the margin is argued at the price the plan really has.
        engine_cost = allowance.PAID_SCANS_PER_MONTH * 1.70
        price = billing_service.PLAN_PRICE_USD
        assert engine_cost < price * 0.5, "the cap must leave at least half the price as margin"

    def test_the_paid_cap_fits_serpapis_free_tier_even_if_every_scan_is_a_new_client(
        self,
    ) -> None:
        # 6 searches per new client, 250 a month on the Free tier.
        assert allowance.PAID_SCANS_PER_MONTH * 6 <= 250

    def test_the_free_allowance_is_one_full_scan(self) -> None:
        assert allowance.FREE_SCAN_ALLOWANCE == 1

    def test_the_in_flight_window_clears_the_scan_budget_several_times_over(self) -> None:
        # Three times the committed 300s budget, and ~2.5x the 361.3s best
        # measured run — a live scan is never written off while it works.
        window = allowance.IN_FLIGHT_WINDOW
        assert window >= timedelta(seconds=300) * 3
        assert window > timedelta(seconds=361.3) * 2

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
# a stranded scan hands its slot back
# ---------------------------------------------------------------------------


class TestAStrandedScanDoesNotHoldASlotForever:
    """QUEUED and RUNNING count, but only inside `IN_FLIGHT_WINDOW`.

    A scan can sit in either state and never finish: the executor is a
    per-request `BackgroundTasks` job and Render restarts the process on every
    deploy, and detection deliberately leaves an open QUEUED row behind. Before
    2026-09-28 such a row burned the one free scan permanently, with nothing
    ever having run.

    Every test here starts the new scan on a SECOND client, so what is measured
    is the allowance and not `get_or_create_scan` adopting the open row (which
    `exclude_scan_id` already excuses).
    """

    # Either side of the 15-minute window, by a minute.
    OLD = 16
    FRESH = 1

    async def _agency_with_open_scan(
        self, client: AsyncClient, engine, *, status: ScanStatus, age_minutes: int
    ) -> tuple[str, str]:  # noqa: ANN001
        """A free agency whose client A holds one open scan of `age_minutes`.

        Returns the agency and the id of a SECOND client, which is where the
        test starts its scan.
        """
        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        await _seed_scans(
            engine, agency_id=agency_id, client_id=a, n=1, status=status,
            created_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
        )
        return agency_id, b

    @pytest.mark.parametrize("status", [ScanStatus.QUEUED, ScanStatus.RUNNING])
    async def test_an_in_flight_scan_past_the_window_hands_its_slot_back(
        self, client: AsyncClient, billing_on, engine, status: ScanStatus
    ) -> None:  # noqa: ANN001
        _, b = await self._agency_with_open_scan(
            client, engine, status=status, age_minutes=self.OLD
        )
        resp = await _start(client, b)
        assert resp.status_code == 202, resp.text

    @pytest.mark.parametrize("status", [ScanStatus.QUEUED, ScanStatus.RUNNING])
    async def test_an_in_flight_scan_inside_the_window_still_holds_its_slot(
        self, client: AsyncClient, billing_on, engine, status: ScanStatus
    ) -> None:  # noqa: ANN001
        """The half that must not regress: without this the gate is a fiction."""
        _, b = await self._agency_with_open_scan(
            client, engine, status=status, age_minutes=self.FRESH
        )
        resp = await _start(client, b)
        assert resp.status_code == 402, resp.text
        assert resp.json()["code"] == allowance.CODE_FREE_USED

    async def test_a_stale_detection_only_scan_does_not_block_the_first_real_scan(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        """Epic 3's detection leaves an open QUEUED row on client A.

        Built through `get_or_create_scan` — the function detection actually
        calls — rather than seeded, because the row's provenance is the point:
        a free agency that only ever ran competitor detection has not used a
        scan, and must still get its free one on client B.
        """
        from avp_api.models import Client as ClientModel
        from avp_api.services import competitors as detection

        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            row = (await s.execute(select(ClientModel).where(ClientModel.id == a))).scalar_one()
            detected = await detection.get_or_create_scan(s, row)
            detected.created_at = datetime.now(UTC) - timedelta(minutes=self.OLD)
            await s.commit()
            assert detected.status is ScanStatus.QUEUED

        assert (await _start(client, b)).status_code == 202
        # And the agency's free scan is now genuinely spent.
        assert (await _counted(engine, agency_id)) == 2


class TestTheAllowanceCountsFromSubmission:
    """`submitted_at`, not `created_at`, dates an in-flight scan — 2026-09-30."""

    OLD = 16
    FRESH = 1

    async def _detected(self, client, engine, *names):  # noqa: ANN001, ANN202
        """Free agency; detection rows (old `created_at`, never submitted) per domain."""
        from avp_api.models import Client as ClientModel
        from avp_api.services import competitors as detection

        agency_id = await _sign_up(client)
        _defer(client)
        cids = [await _make_client(client, d) for d in names]
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            for cid in cids:
                row = (
                    await s.execute(select(ClientModel).where(ClientModel.id == cid))
                ).scalar_one()
                d = await detection.get_or_create_scan(s, row)
                d.created_at = datetime.now(UTC) - timedelta(minutes=self.OLD)
            await s.commit()
        return agency_id, cids

    async def test_an_adopted_detection_row_older_than_the_window_counts_while_in_flight(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        """The hole: two quick starts on two detected clients both began."""
        _, (a, b) = await self._detected(client, engine, "helpscout.com", "zendesk.com")

        assert (await _start(client, a)).status_code == 202
        second = await _start(client, b)

        assert second.status_code == 402, second.text
        assert second.json()["code"] == allowance.CODE_FREE_USED

    async def test_a_stranded_submission_past_the_window_no_longer_holds_a_slot(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        old = datetime.now(UTC) - timedelta(minutes=self.OLD)
        await _seed_scans(
            engine, agency_id=agency_id, client_id=a, n=1, status=ScanStatus.QUEUED,
            created_at=old, submitted_at=old,
        )

        assert (await _start(client, b)).status_code == 202
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            rows = (await s.execute(
                select(Scan).where(Scan.client_id == a)
            )).scalars().all()
        assert [r.status for r in rows] == [ScanStatus.FAILED]
        assert rows[0].error_code == "EXECUTOR_NEVER_STARTED"

    async def test_a_submission_inside_the_window_still_holds_its_slot(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        recent = datetime.now(UTC) - timedelta(minutes=self.FRESH)
        await _seed_scans(
            engine, agency_id=agency_id, client_id=a, n=1, status=ScanStatus.QUEUED,
            created_at=recent, submitted_at=recent,
        )

        resp = await _start(client, b)

        assert resp.status_code == 402, resp.text
        assert await _counted(engine, agency_id) == 1


class TestTheMonthAndLivenessFollowSubmission:
    """Review fixes, 2026-09-30: the month is dated by submission; RUNNING by lease."""

    async def test_a_detection_row_from_last_month_scanned_this_month_counts_now(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        now = datetime.now(UTC)
        start, _ = allowance.period_bounds(now)
        await _seed_scans(
            engine, agency_id=agency_id, client_id=a, n=1, status=ScanStatus.SUCCEEDED,
            created_at=start - timedelta(days=1), submitted_at=now,
        )
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            used = await allowance.count_scans(s, agency_id, now=now, since=start)
        assert used == 1

    async def test_a_leased_running_scan_past_the_window_still_holds_the_slot(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        old = datetime.now(UTC) - timedelta(minutes=20)
        await _seed_scans(
            engine, agency_id=agency_id, client_id=a, n=1, status=ScanStatus.RUNNING,
            created_at=old, submitted_at=old,
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )

        resp = await _start(client, b)

        assert resp.status_code == 402, resp.text
        assert resp.json()["code"] == allowance.CODE_FREE_USED

    async def test_a_running_scan_with_an_expired_lease_holds_no_slot(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        recent = datetime.now(UTC) - timedelta(minutes=1)
        await _seed_scans(
            engine, agency_id=agency_id, client_id=a, n=1, status=ScanStatus.RUNNING,
            created_at=recent, submitted_at=recent,
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=5),
        )

        assert (await _start(client, b)).status_code == 202

    async def test_an_unleased_running_row_counts_inside_the_window_and_not_after(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        now = datetime.now(UTC)
        for cid, age in ((a, 1), (b, 16)):
            when = now - timedelta(minutes=age)
            await _seed_scans(
                engine, agency_id=agency_id, client_id=cid, n=1,
                status=ScanStatus.RUNNING, created_at=when, submitted_at=when,
            )
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            assert await allowance.count_scans(s, agency_id, now=now) == 1


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
    # Not the founder's real address: the default is empty now, and the live
    # value lives in the Render environment. Any address does here.
    OPERATOR = "operator@example.com"

    def test_the_default_exempts_nobody(self) -> None:
        """A personal address does not belong in source, and a fresh
        environment — a new deployment, a restored database — must exempt
        nobody until somebody sets `SCAN_ALLOWANCE_EXEMPT_EMAILS`."""
        from avp_api.config import Settings

        cfg = Settings(_env_file=None)
        assert cfg.scan_allowance_exempt_emails == []

    def test_the_environment_replaces_the_default_lower_cased(self, monkeypatch) -> None:  # noqa: ANN001
        from avp_api.config import Settings

        monkeypatch.setenv("SCAN_ALLOWANCE_EXEMPT_EMAILS", " Ops@Example.com, second@example.com ,")
        cfg = Settings(_env_file=None)
        assert cfg.scan_allowance_exempt_emails == ["ops@example.com", "second@example.com"]
        monkeypatch.setenv("SCAN_ALLOWANCE_EXEMPT_EMAILS", "")
        assert Settings(_env_file=None).scan_allowance_exempt_emails == []

    def test_matching_is_case_insensitive(self, settings, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.OPERATOR])
        assert allowance.is_exempt(settings, User(email="Operator@Example.com", google_sub="s"))
        assert not allowance.is_exempt(settings, User(email="someone@example.com", google_sub="s"))
        assert not allowance.is_exempt(settings, None)

    def test_a_listed_address_is_not_enough_without_google(
        self, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """`User.email` is a string somebody typed.

        Password sign-up creates an ACTIVE account without ever proving the
        address is theirs, so matching on the email alone would hand the
        exemption to whoever registered it first on a fresh database.
        """
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.OPERATOR])
        assert not allowance.is_exempt(settings, User(email=self.OPERATOR, google_sub=None))
        assert allowance.is_exempt(settings, User(email=self.OPERATOR, google_sub="sub-operator"))

    async def test_the_operator_scans_past_the_free_allowance(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """Signed in as the exempt address, Google-linked, free scan long used."""
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.OPERATOR])
        agency_id = await _sign_up(client, email=self.OPERATOR)
        await _link_google(engine, agency_id)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=45)
        assert (await _start(client, cid)).status_code == 202
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["scanLimit"] is None and body["periodResetsAt"] is None
        assert body["scansUsedThisPeriod"] == 46
        assert body["freeScanRequiresGoogle"] is False

    async def test_anyone_else_is_still_gated(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.OPERATOR])
        agency_id = await _sign_up(client, email="someone@example.com")
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1)
        assert (await _start(client, cid)).status_code == 402

    async def test_the_listed_address_without_google_is_gated_like_anyone_else(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The whole point of the second condition.

        Same address as the exempt operator, signed up by password, no Google
        link — the 402 lands, and the billing response shows the real limit
        rather than the exempt shape. Since 2026-09-29 the 402 is
        `SCAN_LIMIT_VERIFY_REQUIRED`: gated like anyone else means gated like
        any other password-only user, and for this address the Google sign-in
        it asks for is also exactly what would make it exempt.
        """
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.OPERATOR])
        agency_id = await _sign_up(client, email=self.OPERATOR, google=False)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1)

        resp = await _start(client, cid)
        assert resp.status_code == 402, resp.text
        assert resp.json()["code"] == allowance.CODE_VERIFY_REQUIRED

        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["scanLimit"] == allowance.FREE_SCAN_ALLOWANCE
        assert body["scansUsedThisPeriod"] == 1
        assert body["freeScanRequiresGoogle"] is True

    async def test_an_unlisted_google_user_is_not_exempt(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """Google vouching for an address is necessary, never sufficient."""
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", [self.OPERATOR])
        agency_id = await _sign_up(client, email="stranger@example.com")
        await _link_google(engine, agency_id, sub="sub-stranger")
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1)
        assert (await _start(client, cid)).status_code == 402


# ---------------------------------------------------------------------------
# the free scan needs a Google sign-in — 2026-09-29
# ---------------------------------------------------------------------------


class TestTheFreeScanNeedsGoogle:
    """Password sign-up never verifies the address, and an agency is free to make.

    So the free scan starts only for a requesting user with a linked Google
    identity, and anyone else gets `SCAN_LIMIT_VERIFY_REQUIRED`. Paid agencies
    and exempt operators are untouched. The argument is in the module
    docstring of `services/scan_allowance.py`.
    """

    async def test_a_password_only_free_agency_with_no_scans_is_asked_for_google(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client, google=False)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")

        resp = await _start(client, cid)
        assert resp.status_code == 402, resp.text
        body = resp.json()
        assert body["type"].endswith("/scan-limit-reached")
        assert body["code"] == "SCAN_LIMIT_VERIFY_REQUIRED"
        assert (body["scansUsed"], body["scanLimit"], body["periodResetsAt"]) == (0, 1, None)
        assert "Google" in body["detail"]
        # Not merely nothing counted: nothing created.
        assert await _scan_rows(engine, agency_id) == 0

    async def test_the_same_agency_once_google_is_linked_starts_its_scan(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        agency_id = await _sign_up(client, google=False)
        jobs = _defer(client)
        cid = await _make_client(client, "helpscout.com")
        assert (await _start(client, cid)).status_code == 402

        await _link_google(engine, agency_id)
        resp = await _start(client, cid)
        assert resp.status_code == 202, resp.text
        assert len(jobs) == 1
        assert await _scan_rows(engine, agency_id) == 1

    async def test_continue_with_google_on_the_same_address_is_the_way_through(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The notice's button, end to end, pressed while still signed in.

        No sign-out first: `/auth/google/start` takes no session, and the
        callback links the password account by its Google-verified email and
        replaces the cookie. `test_google_sign_in.py` proves the same link for
        somebody who signed out first.
        """
        _google_answers_as(
            client, settings, monkeypatch, sub="sub-way-through", email="allowance@test.example"
        )
        agency_id = await _sign_up(client, google=False)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        refused = await _start(client, cid)
        assert refused.json()["code"] == allowance.CODE_VERIFY_REQUIRED

        back = await _continue_with_google(client)
        assert urlparse(back.headers["location"]).path == "/dashboard"

        resp = await _start(client, cid)
        assert resp.status_code == 202, resp.text
        me = (await client.get(f"{BASE}/auth/me")).json()
        assert me["agency"]["id"] == agency_id
        assert me["user"]["signInMethods"] == ["email", "google"]

    async def test_a_different_unused_google_address_starts_a_new_sign_up_and_links_nothing(
        self, client: AsyncClient, billing_on, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """What the notice's wording warns about since 2026-09-29.

        Signed in by password as one address, the person picks a Google
        account with another address that nothing here holds. That is not
        refused: `resolve_identity` finds no user by `sub` or by email, so the
        callback takes it for a brand-new sign-up — a ticket, and
        `/sign-up/google` to name an agency. Nothing is linked, the session is
        untouched, and the free scan still asks for Google.
        """
        _google_answers_as(
            client, settings, monkeypatch, sub="sub-elsewhere", email="someone.else@test.example"
        )
        agency_id = await _sign_up(client, google=False)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")

        back = await _continue_with_google(client)
        landed = urlparse(back.headers["location"])
        assert landed.path == "/sign-up/google"
        assert landed.query == ""
        assert parse_qs(landed.fragment)["ticket"][0]

        me = (await client.get(f"{BASE}/auth/me")).json()
        assert me["agency"]["id"] == agency_id
        assert me["user"]["signInMethods"] == ["email"]
        assert (await _start(client, cid)).json()["code"] == allowance.CODE_VERIFY_REQUIRED

    async def test_a_password_only_teammate_is_refused_where_the_google_owner_is_not(
        self, client: AsyncClient, billing_on, engine, settings
    ) -> None:  # noqa: ANN001
        """The requirement is the requesting user's, not the agency's.

        Same free agency, same client, same moment: its Google-linked owner may
        start the free scan, and its password-only teammate may not.
        """
        agency_id = await _sign_up(client)  # the owner, Google-linked
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seat_password_teammate(settings, agency_id, "teammate@test.example")

        await _sign_in(client, "teammate@test.example")
        resp = await _start(client, cid)
        assert resp.status_code == 402, resp.text
        assert resp.json()["code"] == allowance.CODE_VERIFY_REQUIRED
        assert await _scan_rows(engine, agency_id) == 0

        await _sign_in(client, "allowance@test.example")
        assert (await _start(client, cid)).status_code == 202

    @pytest.mark.parametrize("status", ["active", "trialing"])
    async def test_a_password_only_user_in_a_paid_agency_starts_a_scan(
        self, client: AsyncClient, billing_on, engine, status: str
    ) -> None:  # noqa: ANN001
        """A payment is identity enough: the forty a month is for any seat."""
        agency_id = await _sign_up(client, google=False)
        await _set_subscription(engine, agency_id, status)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        assert (await _start(client, cid)).status_code == 202

    async def test_with_billing_unconfigured_nothing_is_asked_for(
        self, client: AsyncClient, billing_off
    ) -> None:  # noqa: ANN001
        """The rule is part of the gate, so it is off where the gate is off."""
        await _sign_up(client, google=False)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        assert (await _start(client, cid)).status_code == 202

    async def test_it_is_asked_before_the_count_is_consulted(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        """A count is not proof that an agency ever scanned.

        Client A holds a fresh detection-only QUEUED row — inside the window,
        so it holds a slot — and this agency has never run a scan. Starting on
        client B must ask for Google, not say the free scan is used.
        """
        from avp_api.models import Client as ClientModel
        from avp_api.services import competitors as detection

        agency_id = await _sign_up(client, google=False)
        _defer(client)
        a = await _make_client(client, "helpscout.com")
        b = await _make_client(client, "zendesk.com")
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            row = (await s.execute(select(ClientModel).where(ClientModel.id == a))).scalar_one()
            await detection.get_or_create_scan(s, row)
            await s.commit()

        resp = await _start(client, b)
        assert resp.status_code == 402, resp.text
        assert resp.json()["code"] == allowance.CODE_VERIFY_REQUIRED
        assert resp.json()["scansUsed"] == 1  # the count saw the row; it did not decide
        assert await _scan_rows(engine, agency_id) == 1

    async def test_where_the_free_scan_is_spent_google_is_still_asked_for_first(
        self, client: AsyncClient, billing_on, engine
    ) -> None:  # noqa: ANN001
        """The accepted cost of asking first, pinned so it is never a surprise.

        A password-only user whose agency has already used its free scan is
        asked for Google, and hears the scan is used only once linked.
        """
        agency_id = await _sign_up(client, google=False)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed_scans(engine, agency_id=agency_id, client_id=cid, n=1)

        assert (await _start(client, cid)).json()["code"] == allowance.CODE_VERIFY_REQUIRED
        await _link_google(engine, agency_id)
        assert (await _start(client, cid)).json()["code"] == allowance.CODE_FREE_USED

    @pytest.mark.parametrize(
        ("gate", "subscription", "google", "expected"),
        [
            pytest.param(True, None, False, True, id="free-password-only"),
            pytest.param(True, "canceled", False, True, id="lapsed-password-only"),
            pytest.param(True, None, True, False, id="free-google"),
            pytest.param(True, "active", False, False, id="paid-password-only"),
            pytest.param(False, None, False, False, id="ungated-password-only"),
        ],
    )
    async def test_the_billing_response_says_who_needs_google(
        self,
        client: AsyncClient,
        engine,  # noqa: ANN001
        settings,  # noqa: ANN001
        monkeypatch,  # noqa: ANN001
        gate: bool,
        subscription: str | None,
        google: bool,
        expected: bool,
    ) -> None:
        """True exactly when the gate is on, the agency free, the user unlinked."""
        key = SecretStr("sk_test_fake") if gate else None
        monkeypatch.setattr(settings, "stripe_secret_key", key)
        monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake" if gate else None)
        agency_id = await _sign_up(client, google=google)
        await _set_subscription(engine, agency_id, subscription)

        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["freeScanRequiresGoogle"] is expected

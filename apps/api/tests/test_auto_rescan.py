"""Automatic monthly rescans — F2, 2026-10-06.

What one wake does (`services/auto_rescan.run_once`): which clients are due, the claim two API
instances race for, the skips and their once-a-month retry, and the loop that calls it. The rows
are seeded straight into the table (a scan's age is a fact there, not a run of stubbed engines),
the executor records the job and never runs it, and every wake is given its clock, so the month
the allowance is asked about is the test's own. No provider is reached.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import structlog
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from avp_api import auto_rescan_watch, ids
from avp_api.config import Settings
from avp_api.errors import ValidationProblem
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
from avp_api.services import auto_rescan
from avp_api.services.scan_start import start_scan

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"


class Recorder:
    """An executor that records the job and never runs it."""

    def __init__(self) -> None:
        self.jobs: list[Any] = []

    async def submit(self, job, *, settings) -> None:  # noqa: ANN001
        self.jobs.append(job)


@pytest.fixture
def factory(engine) -> async_sessionmaker[AsyncSession]:  # noqa: ANN001
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def billing_on(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_" + "test_fake"))
    monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")


async def _agency(
    factory, *, slug: str = "one", subscription: str | None = "active", email: str | None = None
) -> tuple[str, str]:  # noqa: ANN001
    """An agency with one active OWNER (Google-linked, so the free scan is theirs too)."""
    async with factory() as s:
        agency = Agency(id=ids.new_id(ids.AGENCY), name=f"Agency {slug}", slug=slug)
        agency.subscription_status = subscription
        s.add(agency)
        await s.flush()
        user = User(
            id=ids.new_id(ids.USER), agency_id=agency.id, email=email or f"{slug}@test.example",
            full_name="Owner", role=UserRole.OWNER, status=UserStatus.ACTIVE,
            google_sub=f"sub-{slug}",
        )
        s.add(user)
        await s.commit()
        return agency.id, user.id


async def _client(
    factory,  # noqa: ANN001
    agency_id: str,
    user_id: str | None,
    *,
    domain: str = "acme.example",
    on: bool = True,
    last_scan_days: float | None = 31,
    last_status: ScanStatus = ScanStatus.SUCCEEDED,
    last_trigger: ScanTrigger = ScanTrigger.MANUAL,
) -> str:
    """A client, rescan on, whose latest scan (if any) started `last_scan_days` before NOW."""
    async with factory() as s:
        client = Client(
            id=ids.new_id(ids.CLIENT), agency_id=agency_id, name=domain, domain=domain,
            auto_rescan=on, auto_rescan_by_user_id=user_id if on else None,
        )
        s.add(client)
        await s.flush()
        if last_scan_days is not None:
            await _scan(
                s, client, status=last_status, days_ago=last_scan_days, trigger=last_trigger
            )
        await s.commit()
        return client.id


async def _scan(
    s: AsyncSession,
    client: Client,
    *,
    status: ScanStatus = ScanStatus.SUCCEEDED,
    days_ago: float = 31,
    trigger: ScanTrigger = ScanTrigger.MANUAL,
    submitted: bool = True,
) -> Scan:
    when = NOW - timedelta(days=days_ago)
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=client.agency_id, status=status,
        trigger=trigger, formula_version="test", prompt_count=0, engine_result_count=0,
    )
    scan.created_at = when
    scan.submitted_at = when if submitted else None
    if status is ScanStatus.RUNNING:
        scan.lease_expires_at = NOW + timedelta(minutes=5)
    s.add(scan)
    return scan


async def _wake(
    settings, factory, executor: Recorder | None = None, *, now: datetime = NOW  # noqa: ANN001
) -> str | None:
    return await auto_rescan.run_once(
        settings, factory=factory, executor=executor or Recorder(), now=now
    )


async def _scans(factory, client_id: str) -> list[Scan]:  # noqa: ANN001
    async with factory() as s:
        return list(
            (await s.execute(select(Scan).where(Scan.client_id == client_id).order_by(Scan.id)))
            .scalars()
            .all()
        )


async def _row(factory, client_id: str) -> Client:  # noqa: ANN001
    async with factory() as s:
        return (await s.execute(select(Client).where(Client.id == client_id))).scalar_one()


class TestDue:
    async def test_a_client_whose_latest_scan_is_31_days_old_is_rescanned_as_who_turned_it_on(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=31)
        jobs = Recorder()
        started = await _wake(settings, factory, jobs)
        assert started is not None
        assert [j.scan_id for j in jobs.jobs] == [started]
        newest = (await _scans(factory, cid))[-1]
        assert newest.id == started
        assert newest.status is ScanStatus.QUEUED
        assert newest.trigger is ScanTrigger.SCHEDULED
        assert newest.requested_by_user_id == user
        assert newest.submitted_at == NOW

    async def test_one_whose_latest_scan_is_29_days_old_is_not(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=29)
        jobs = Recorder()
        assert await _wake(settings, factory, jobs) is None
        assert jobs.jobs == []
        assert len(await _scans(factory, cid)) == 1

    async def test_exactly_30_days_is_due(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        await _client(factory, agency, user, last_scan_days=30)
        assert await _wake(settings, factory) is not None

    async def test_the_latest_scan_decides_not_an_older_one(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=90)
        async with factory() as s:
            await _scan(s, await s.get(Client, cid), days_ago=5)
            await s.commit()
        assert await _wake(settings, factory) is None

    async def test_a_client_with_the_switch_off_is_never_rescanned(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        await _client(factory, agency, user, on=False, last_scan_days=400)
        assert await _wake(settings, factory) is None

    async def test_a_client_never_scanned_is_not_due(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        await _client(factory, agency, user, last_scan_days=None)
        assert await _wake(settings, factory) is None

    async def test_a_deleted_client_is_not_rescanned(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            (await s.get(Client, cid)).deleted_at = NOW
            await s.commit()
        assert await _wake(settings, factory) is None

    async def test_a_client_with_a_scan_in_flight_is_not_due(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            await _scan(s, await s.get(Client, cid), status=ScanStatus.RUNNING, days_ago=0.01,
                        trigger=ScanTrigger.MANUAL)
            await s.commit()
        assert await _wake(settings, factory) is None

    async def test_a_failed_scan_that_old_is_due_like_any_other(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        await _client(factory, agency, user, last_scan_days=31, last_status=ScanStatus.FAILED)
        assert await _wake(settings, factory) is not None

    async def test_a_failed_automatic_scan_is_not_retried_before_30_days_after_it(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(
            factory, agency, user, last_scan_days=5, last_status=ScanStatus.FAILED,
            last_trigger=ScanTrigger.SCHEDULED,
        )
        jobs = Recorder()
        assert await _wake(settings, factory, jobs) is None
        # 29 days on: still not. 30 days after it: due again.
        assert await _wake(settings, factory, jobs, now=NOW + timedelta(days=24)) is None
        assert await _wake(settings, factory, jobs, now=NOW + timedelta(days=25)) is not None
        assert len(jobs.jobs) == 1
        assert len(await _scans(factory, cid)) == 2

    async def test_a_detection_row_nothing_submitted_is_adopted_and_does_not_hide_the_old_scan(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            row = await _scan(s, await s.get(Client, cid), status=ScanStatus.QUEUED, days_ago=2,
                              submitted=False)
            await s.commit()
            detection_id = row.id
        started = await _wake(settings, factory)
        assert started == detection_id
        assert len(await _scans(factory, cid)) == 2
        adopted = (await _scans(factory, cid))[-1]
        assert adopted.trigger is ScanTrigger.SCHEDULED and adopted.submitted_at == NOW
        assert adopted.requested_by_user_id == user, "the user it runs as, not whoever opened it"


class TestOneAtATime:
    async def test_one_scan_per_wake_however_many_are_due(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        first = await _client(factory, agency, user, domain="a.example", last_scan_days=50)
        second = await _client(factory, agency, user, domain="b.example", last_scan_days=40)
        jobs = Recorder()
        started = await _wake(settings, factory, jobs)
        assert len(jobs.jobs) == 1
        # The longest-waiting first.
        assert [s.id for s in await _scans(factory, first)][-1] == started
        assert len(await _scans(factory, second)) == 1

    async def test_none_while_another_automatic_scan_is_queued_or_running_anywhere(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        one, owner_one = await _agency(factory, slug="one")
        two, owner_two = await _agency(factory, slug="two")
        first = await _client(factory, one, owner_one, domain="a.example", last_scan_days=50)
        second = await _client(factory, two, owner_two, domain="b.example", last_scan_days=45)
        jobs = Recorder()
        started = await _wake(settings, factory, jobs)
        assert started is not None
        # Still queued: the other agency's due client waits.
        assert await _wake(settings, factory, jobs, now=NOW + timedelta(minutes=10)) is None
        assert len(jobs.jobs) == 1
        # Running: the same.
        async with factory() as s:
            scan = await s.get(Scan, started)
            scan.status = ScanStatus.RUNNING
            scan.lease_expires_at = NOW + timedelta(hours=1)
            await s.commit()
        assert await _wake(settings, factory, jobs, now=NOW + timedelta(minutes=20)) is None
        # Finished: the next one goes.
        async with factory() as s:
            scan = await s.get(Scan, started)
            scan.status = ScanStatus.SUCCEEDED
            scan.lease_expires_at = None
            await s.commit()
        again = await _wake(settings, factory, jobs, now=NOW + timedelta(minutes=30))
        assert again is not None
        assert [s.id for s in await _scans(factory, second)][-1] == again
        assert len(await _scans(factory, first)) == 2

    async def test_a_manual_scan_in_flight_elsewhere_does_not_hold_the_others_back(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        one, owner_one = await _agency(factory, slug="one")
        two, owner_two = await _agency(factory, slug="two")
        busy = await _client(factory, one, owner_one, domain="a.example", last_scan_days=0.5)
        async with factory() as s:
            (await s.execute(select(Scan).where(Scan.client_id == busy))).scalar_one().status = (
                ScanStatus.RUNNING
            )
            await s.commit()
        await _client(factory, two, owner_two, domain="b.example", last_scan_days=40)
        assert await _wake(settings, factory) is not None

    async def test_a_wake_that_finds_another_mid_wake_starts_nothing(
        self, settings, factory, engine  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with engine.connect() as holder:
            got = await holder.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": auto_rescan.WAKE_LOCK_KEY}
            )
            assert got.scalar_one() is True
            jobs = Recorder()
            assert await _wake(settings, factory, jobs) is None
            assert jobs.jobs == []
            await holder.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": auto_rescan.WAKE_LOCK_KEY}
            )
        assert await _wake(settings, factory) is not None
        assert len(await _scans(factory, cid)) == 2


class TestTwoInstances:
    async def test_two_sessions_racing_the_claim_one_wins(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)

        async def one_claim() -> bool:
            async with factory() as s:
                return await auto_rescan.claim(s, cid, NOW)

        results = await asyncio.gather(*(one_claim() for _ in range(4)))
        assert sorted(results) == [False, False, False, True]
        assert (await _row(factory, cid)).auto_rescan_attempted_at == NOW

    async def test_two_instances_awake_together_start_one_scan_not_two(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        jobs = Recorder()
        results = await asyncio.gather(
            _wake(settings, factory, jobs), _wake(settings, factory, jobs)
        )
        assert len([r for r in results if r is not None]) == 1
        assert len(jobs.jobs) == 1
        assert len(await _scans(factory, cid)) == 2

    async def test_a_second_instance_with_the_due_list_already_in_hand_is_refused_the_claim(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        """The wake's lock is not what protects the client: the claim is, on its own row."""
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            seen = await auto_rescan.due_client_ids(s, NOW)
        assert seen == [cid]
        async with factory() as s:
            assert await auto_rescan.claim(s, cid, NOW) is True
        async with factory() as s:
            assert await auto_rescan.claim(s, cid, NOW + timedelta(seconds=3)) is False

    async def test_a_claim_holds_for_an_hour_and_then_lets_go(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            assert await auto_rescan.claim(s, cid, NOW) is True
        async with factory() as s:
            assert await auto_rescan.claim(s, cid, NOW + timedelta(minutes=59)) is False
        async with factory() as s:
            assert await auto_rescan.claim(s, cid, NOW + timedelta(minutes=61)) is True


class TestSkips:
    async def test_an_agency_not_paid_up_is_skipped_and_the_start_path_is_not_called(
        self, settings, factory, billing_on, monkeypatch  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory, subscription=None)
        cid = await _client(factory, agency, user, last_scan_days=40)

        async def must_not_run(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("the start path was called for an agency that is not paid up")

        monkeypatch.setattr(auto_rescan, "start_scan", must_not_run)
        jobs = Recorder()
        assert await _wake(settings, factory, jobs) is None
        assert jobs.jobs == []
        row = await _row(factory, cid)
        assert row.auto_rescan_skip_reason == auto_rescan.SKIP_NOT_PAID
        assert row.auto_rescan_attempted_at == NOW
        assert len(await _scans(factory, cid)) == 1

    @pytest.mark.parametrize("status", ["canceled", "past_due", "unpaid", "paused"])
    async def test_every_status_that_is_not_paid_up_is_skipped(
        self, settings, factory, billing_on, status: str  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory, subscription=status)
        cid = await _client(factory, agency, user, last_scan_days=40)
        assert await _wake(settings, factory) is None
        assert (await _row(factory, cid)).auto_rescan_skip_reason == auto_rescan.SKIP_NOT_PAID

    @pytest.mark.parametrize("status", ["active", "trialing"])
    async def test_a_paid_or_trialing_agency_is_rescanned(
        self, settings, factory, billing_on, status: str  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory, subscription=status)
        await _client(factory, agency, user, last_scan_days=40)
        assert await _wake(settings, factory) is not None

    async def test_with_billing_not_configured_an_agency_that_is_not_paid_is_still_skipped(
        self, settings, factory, monkeypatch  # noqa: ANN001
    ) -> None:
        """Round 2: no gate must not mean every free agency is rescanned unattended."""

        async def must_not_run(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("the start path was called for an agency that is not paid up")

        monkeypatch.setattr(auto_rescan, "start_scan", must_not_run)
        agency, user = await _agency(factory, subscription=None)
        cid = await _client(factory, agency, user, last_scan_days=40)
        assert await _wake(settings, factory) is None
        assert (await _row(factory, cid)).auto_rescan_skip_reason == auto_rescan.SKIP_NOT_PAID

    async def test_with_billing_not_configured_a_paid_agency_is_rescanned(
        self, settings, factory  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory, subscription="active")
        await _client(factory, agency, user, last_scan_days=40)
        assert await _wake(settings, factory) is not None

    async def test_a_listed_operator_is_not_held_to_the_plan(
        self, settings, factory, billing_on, monkeypatch  # noqa: ANN001
    ) -> None:
        listed = frozenset({"op@test.example"})
        monkeypatch.setattr(settings, "scan_allowance_exempt_emails", listed)
        agency, user = await _agency(factory, subscription=None, email="op@test.example")
        await _client(factory, agency, user, last_scan_days=40)
        assert await _wake(settings, factory) is not None

    async def _use_the_month(self, factory, agency: str, user: str) -> None:  # noqa: ANN001
        other = await _client(factory, agency, user, domain="busy.example", on=False,
                              last_scan_days=None)
        async with factory() as s:
            row = await s.get(Client, other)
            for _ in range(40):
                await _scan(s, row, days_ago=5)
            await s.commit()

    async def test_the_allowance_refusing_is_a_skip_with_its_reason_and_no_scan(
        self, settings, factory, billing_on  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        await self._use_the_month(factory, agency, user)
        jobs = Recorder()
        assert await _wake(settings, factory, jobs) is None
        assert jobs.jobs == []
        row = await _row(factory, cid)
        assert row.auto_rescan_skip_reason == auto_rescan.SKIP_ALLOWANCE
        assert row.auto_rescan_attempted_at == NOW
        assert len(await _scans(factory, cid)) == 1

    async def test_a_skipped_month_is_tried_again_on_the_first_of_the_next_month_and_never_sooner(
        self, settings, factory, billing_on, monkeypatch  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        await self._use_the_month(factory, agency, user)
        calls: list[int] = []
        real = auto_rescan.start_scan

        async def counting(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            return await real(*args, **kwargs)

        monkeypatch.setattr(auto_rescan, "start_scan", counting)
        jobs = Recorder()
        assert await _wake(settings, factory, jobs) is None
        assert len(calls) == 1
        # Every wake for the rest of the month, a day apart and ten minutes apart: no attempt.
        for step in (timedelta(minutes=10), timedelta(hours=2), timedelta(days=1),
                     timedelta(days=10, hours=11, minutes=59)):
            assert await _wake(settings, factory, jobs, now=NOW + step) is None
        assert len(calls) == 1, "a skipped month was retried inside the month"
        # The first instant of November (UTC): the allowance has reset, and it goes.
        november = datetime(2026, 11, 1, tzinfo=UTC)
        started = await _wake(settings, factory, jobs, now=november)
        assert started is not None
        assert len(calls) == 2
        row = await _row(factory, cid)
        assert row.auto_rescan_skip_reason is None, "a started scan clears the old notice"

    async def test_a_second_refusal_in_the_next_month_is_one_attempt_and_not_a_loop(
        self, settings, factory, billing_on, monkeypatch  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory)
        await _client(factory, agency, user, last_scan_days=40)
        await self._use_the_month(factory, agency, user)
        calls: list[int] = []
        real = auto_rescan.start_scan

        async def counting(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            return await real(*args, **kwargs)

        monkeypatch.setattr(auto_rescan, "start_scan", counting)
        await _wake(settings, factory)
        # The allowance is still spent in December's own count too (scans dated that month).
        december = datetime(2026, 12, 3, tzinfo=UTC)
        async with factory() as s:
            found = await s.execute(select(Client).where(Client.domain == "busy.example"))
            row = found.scalar_one()
            for _ in range(40):
                scan = await _scan(s, row, days_ago=0)
                scan.submitted_at = december - timedelta(days=1)
            await s.commit()
        await _wake(settings, factory, now=december)
        await _wake(settings, factory, now=december + timedelta(minutes=10))
        await _wake(settings, factory, now=december + timedelta(days=20))
        assert len(calls) == 2

    @pytest.mark.parametrize(
        "change",
        ["member", "suspended", "deleted", "other_agency", "unset"],
    )
    async def test_the_user_who_turned_it_on_no_longer_an_owner_or_admin_is_a_skip(
        self, settings, factory, change: str  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory)
        other_agency, other_user = await _agency(factory, slug="two")
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            row = await s.get(User, user)
            if change == "member":
                row.role = UserRole.MEMBER
            elif change == "suspended":
                row.status = UserStatus.SUSPENDED
            elif change == "deleted":
                row.deleted_at = NOW
            elif change == "other_agency":
                (await s.get(Client, cid)).auto_rescan_by_user_id = other_user
            else:
                (await s.get(Client, cid)).auto_rescan_by_user_id = None
            await s.commit()
        jobs = Recorder()
        assert await _wake(settings, factory, jobs) is None
        assert jobs.jobs == []
        assert (await _row(factory, cid)).auto_rescan_skip_reason == auto_rescan.SKIP_USER

    async def test_an_admin_is_enough(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            (await s.get(User, user)).role = UserRole.ADMIN
            await s.commit()
        assert await _wake(settings, factory) is not None

    async def test_removing_the_user_leaves_the_client_skipping_not_scanning_for_nobody(
        self, settings, factory
    ) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            await s.execute(text("UPDATE scans SET requested_by_user_id = NULL"))
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
            await s.commit()
        assert (await _row(factory, cid)).auto_rescan_by_user_id is None
        assert await _wake(settings, factory) is None
        assert (await _row(factory, cid)).auto_rescan_skip_reason == auto_rescan.SKIP_USER

    async def test_a_skip_costs_the_wake_nothing_the_next_due_client_still_starts(
        self, settings, factory, billing_on  # noqa: ANN001
    ) -> None:
        unpaid, unpaid_owner = await _agency(factory, slug="unpaid", subscription=None)
        paid, paid_owner = await _agency(factory, slug="paid")
        skipped = await _client(
            factory, unpaid, unpaid_owner, domain="a.example", last_scan_days=60
        )
        started_for = await _client(
            factory, paid, paid_owner, domain="b.example", last_scan_days=40
        )
        started = await _wake(settings, factory)
        assert started is not None
        assert (await _row(factory, skipped)).auto_rescan_skip_reason == auto_rescan.SKIP_NOT_PAID
        assert [s.id for s in await _scans(factory, started_for)][-1] == started

    async def test_a_refusal_of_the_start_path_is_a_skip_and_the_loop_goes_on(
        self, settings, factory, monkeypatch  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory)
        first = await _client(factory, agency, user, domain="a.example", last_scan_days=60)
        second = await _client(factory, agency, user, domain="b.example", last_scan_days=40)
        real = auto_rescan.start_scan
        seen: list[str] = []

        async def refusing_once(db: AsyncSession, **kwargs: Any) -> Any:
            seen.append(kwargs["client"].id)
            if kwargs["client"].id == first:
                raise ValidationProblem(detail="No key configured for engine(s)")
            return await real(db, **kwargs)

        monkeypatch.setattr(auto_rescan, "start_scan", refusing_once)
        started = await _wake(settings, factory)
        assert started is not None and seen == [first, second]
        refused = await _row(factory, first)
        assert refused.auto_rescan_skip_reason == auto_rescan.SKIP_COULD_NOT_START

    async def test_an_unexpected_failure_is_a_skip_and_logs_its_class_only(
        self, settings, factory, monkeypatch  # noqa: ANN001
    ) -> None:
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("secret-looking text sk-abc 1.2.3.4 a@b.example")

        monkeypatch.setattr(auto_rescan, "start_scan", boom)
        with structlog.testing.capture_logs() as events:
            assert await _wake(settings, factory) is None
        failed_row = await _row(factory, cid)
        assert failed_row.auto_rescan_skip_reason == auto_rescan.SKIP_COULD_NOT_START
        assert "sk-abc" not in repr(events) and "a@b.example" not in repr(events)
        failed = [e for e in events if e["event"] == "rescan.failed"]
        assert failed and failed[0]["error"] == "RuntimeError"

    async def test_a_started_scan_clears_an_old_skip_notice(self, settings, factory) -> None:  # noqa: ANN001
        agency, user = await _agency(factory)
        cid = await _client(factory, agency, user, last_scan_days=40)
        async with factory() as s:
            row = await s.get(Client, cid)
            row.auto_rescan_skip_reason = auto_rescan.SKIP_ALLOWANCE
            row.auto_rescan_attempted_at = NOW - timedelta(days=40)
            await s.commit()
        assert await _wake(settings, factory) is not None
        assert (await _row(factory, cid)).auto_rescan_skip_reason is None


class TestRoundTwo:
    async def test_a_persons_submitted_queued_scan_is_not_adopted_or_relabelled(
        self, settings, factory  # noqa: ANN001
    ) -> None:
        agency, owner = await _agency(factory)
        async with factory() as s:
            other = User(
                id=ids.new_id(ids.USER), agency_id=agency, email="p@test.example",
                full_name="P", role=UserRole.MEMBER, status=UserStatus.ACTIVE, google_sub="sub-p",
            )
            s.add(other)
            await s.commit()
            person = other.id
        cid = await _client(factory, agency, owner, last_scan_days=40)
        async with factory() as s:
            row = await _scan(s, await s.get(Client, cid), status=ScanStatus.QUEUED, days_ago=0.01)
            row.requested_by_user_id = person
            await s.commit()
            queued = row.id
        jobs = Recorder()
        async with factory() as s:
            got = await start_scan(
                s, settings=settings, executor=jobs, client=await s.get(Client, cid),
                user=await s.get(User, owner), trigger=ScanTrigger.SCHEDULED, now=NOW,
            )
        assert got.id == queued and jobs.jobs == []
        after = (await _scans(factory, cid))[-1]
        assert after.trigger is ScanTrigger.MANUAL
        assert after.requested_by_user_id == person
        assert after.submitted_at == NOW - timedelta(days=0.01)

    async def test_the_attempt_reports_it_as_already_running_and_records_no_skip(
        self, settings, factory  # noqa: ANN001
    ) -> None:
        agency, owner = await _agency(factory)
        cid = await _client(factory, agency, owner, last_scan_days=40)
        async with factory() as s:
            await _scan(s, await s.get(Client, cid), status=ScanStatus.QUEUED, days_ago=0.01)
            await s.commit()
        with structlog.testing.capture_logs() as events:
            got = await auto_rescan._attempt(factory, settings, Recorder(), cid, NOW)  # noqa: SLF001
        assert got is None
        assert [e["reason"] for e in events if e["event"] == "rescan.skipped"] == [
            "SCAN_ALREADY_RUNNING"
        ]
        assert (await _row(factory, cid)).auto_rescan_skip_reason is None

    async def test_the_claim_clears_last_months_notice(self, settings, factory) -> None:  # noqa: ANN001
        agency, owner = await _agency(factory)
        cid = await _client(factory, agency, owner, last_scan_days=40)
        async with factory() as s:
            row = await s.get(Client, cid)
            row.auto_rescan_skip_reason = auto_rescan.SKIP_ALLOWANCE
            row.auto_rescan_attempted_at = NOW - timedelta(days=40)
            await s.commit()
        async with factory() as s:
            assert await auto_rescan.claim(s, cid, NOW) is True
        row = await _row(factory, cid)
        assert row.auto_rescan_skip_reason is None and row.auto_rescan_attempted_at == NOW

    async def test_the_wake_lock_is_gone_after_a_wake_and_after_a_wake_that_fails(
        self, settings, factory, engine, monkeypatch  # noqa: ANN001
    ) -> None:
        async def free() -> bool:
            async with engine.connect() as conn:
                got = await conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": auto_rescan.WAKE_LOCK_KEY}
                )
                ok = bool(got.scalar_one())
                if ok:
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": auto_rescan.WAKE_LOCK_KEY}
                    )
                return ok

        await _wake(settings, factory)
        assert await free()

        async def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("x")

        monkeypatch.setattr(auto_rescan, "automatic_scan_open", boom)
        with pytest.raises(RuntimeError):
            await _wake(settings, factory)
        assert await free()

    async def test_the_lock_is_a_transaction_lock_not_a_session_lock(self) -> None:
        import inspect

        source = inspect.getsource(auto_rescan.run_once)
        assert "pg_try_advisory_xact_lock" in source
        assert "pg_advisory_unlock" not in source


class TestLogs:
    async def test_started_and_skipped_lines_carry_ids_and_a_reason_only(
        self, settings, factory, billing_on  # noqa: ANN001
    ) -> None:
        paid, paid_owner = await _agency(factory, slug="paid", email="paid@test.example")
        unpaid, unpaid_owner = await _agency(
            factory, slug="unpaid", subscription=None, email="unpaid@test.example"
        )
        a = await _client(factory, unpaid, unpaid_owner, domain="skipped-client.example",
                          last_scan_days=60)
        b = await _client(factory, paid, paid_owner, domain="started-client.example",
                          last_scan_days=40)
        with structlog.testing.capture_logs() as events:
            started = await _wake(settings, factory)
        by_event = {e["event"]: e for e in events if e["event"].startswith("rescan.")}
        skipped = by_event["rescan.skipped"]
        began = by_event["rescan.started"]
        assert {k for k in skipped if k not in ("event", "log_level")} == {
            "reason", "client_id", "agency_id"
        }
        assert skipped["reason"] == "AGENCY_NOT_PAID"
        assert (skipped["client_id"], skipped["agency_id"]) == (a, unpaid)
        assert {k for k in began if k not in ("event", "log_level")} == {
            "client_id", "agency_id", "scan_id", "user_id"
        }
        assert (began["client_id"], began["agency_id"], began["scan_id"], began["user_id"]) == (
            b, paid, started, paid_owner,
        )
        flat = repr(events)
        for private in ("@test.example", "skipped-client.example", "started-client.example"):
            assert private not in flat


class TestTheLoop:
    def test_the_setting_off_starts_no_loop(self, settings) -> None:  # noqa: ANN001
        assert settings.auto_rescan_enabled is False
        assert auto_rescan_watch.start(settings) is None

    async def test_the_setting_on_starts_one_task_and_stop_ends_it(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(settings, "auto_rescan_enabled", True)
        task = auto_rescan_watch.start(settings)
        assert task is not None and task.get_name() == auto_rescan_watch.TASK_NAME
        await asyncio.wait_for(auto_rescan_watch.stop(task), timeout=10)
        assert task.done()

    async def test_stop_is_safe_with_none(self) -> None:
        await auto_rescan_watch.stop(None)

    async def test_it_wakes_again_and_again_and_a_failed_wake_does_not_end_it(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        wakes: list[int] = []

        async def run_once(*args: Any, **kwargs: Any) -> None:
            wakes.append(1)
            raise RuntimeError("one wake fails")

        monkeypatch.setattr(auto_rescan, "run_once", run_once)
        monkeypatch.setattr(auto_rescan_watch, "get_sessionmaker", lambda settings: None)
        task = asyncio.create_task(auto_rescan_watch.watch_rescans(settings, interval_s=0.01))
        for _ in range(200):
            if len(wakes) >= 3:
                break
            await asyncio.sleep(0.01)
        await auto_rescan_watch.stop(task)
        assert len(wakes) >= 3

    async def test_it_waits_one_interval_before_the_first_wake(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        wakes: list[int] = []

        async def run_once(*args: Any, **kwargs: Any) -> None:
            wakes.append(1)

        monkeypatch.setattr(auto_rescan, "run_once", run_once)
        monkeypatch.setattr(auto_rescan_watch, "get_sessionmaker", lambda settings: None)
        task = asyncio.create_task(auto_rescan_watch.watch_rescans(settings, interval_s=3600))
        await asyncio.sleep(0.05)
        await auto_rescan_watch.stop(task)
        assert wakes == []

    def test_a_wake_is_every_ten_minutes(self) -> None:
        assert auto_rescan_watch.WAKE_INTERVAL_S == 600


class TestTheSetting:
    def _settings(self, **given: Any) -> Settings:
        return Settings(
            _env_file=None, environment="local", database_url="postgresql+asyncpg://x@h/d",
            redis_url="redis://x/0", **given,
        )

    def test_the_default_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTO_RESCAN_ENABLED", raising=False)
        assert self._settings().auto_rescan_enabled is False

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_is_off(self, monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
        monkeypatch.setenv("AUTO_RESCAN_ENABLED", blank)
        assert self._settings().auto_rescan_enabled is False

    @pytest.mark.parametrize("raw", ["true", "1", " TRUE "])
    def test_true_turns_it_on(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("AUTO_RESCAN_ENABLED", raw)
        assert self._settings().auto_rescan_enabled is True

    def test_a_value_that_is_not_a_boolean_stops_the_start_and_prints_no_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = "LEAK-rescan-qz7x"
        monkeypatch.setenv("AUTO_RESCAN_ENABLED", sentinel)
        with pytest.raises(ValidationError) as caught:
            self._settings()
        message = str(caught.value)
        assert "auto_rescan_enabled" in message.lower()
        assert sentinel not in message and "input_value" not in message

    def test_the_template_ships_it_off_and_says_so(self) -> None:
        lines = TEMPLATE.read_text().splitlines()
        at = next(i for i, line in enumerate(lines) if line.strip() == "AUTO_RESCAN_ENABLED=false")
        comment: list[str] = []
        for line in reversed(lines[:at]):
            if not line.startswith("#"):
                break
            comment.append(line.lower())
        said = " ".join(comment)
        assert "off" in said and "allowance" in said


async def test_the_migration_columns_exist_on_the_model_with_their_defaults(factory) -> None:  # noqa: ANN001
    agency, user = await _agency(factory)
    async with factory() as s:
        row = Client(id=ids.new_id(ids.CLIENT), agency_id=agency, name="x", domain="x.example")
        s.add(row)
        await s.commit()
    got = await _row(factory, row.id)
    assert got.auto_rescan is False
    assert got.auto_rescan_by_user_id is None
    assert got.auto_rescan_attempted_at is None and got.auto_rescan_skip_reason is None
    total = None
    async with factory() as s:
        total = (await s.execute(select(func.count()).select_from(Client))).scalar_one()
    assert total == 1


async def test_a_start_by_a_person_stamps_them_on_the_detection_row_it_adopts(
    settings, factory  # noqa: ANN001
) -> None:
    from avp_api.services.scan_start import start_scan

    agency, owner = await _agency(factory)
    cid = await _client(factory, agency, owner, on=False, last_scan_days=None)
    async with factory() as s:
        row = await _scan(s, await s.get(Client, cid), status=ScanStatus.QUEUED, days_ago=1,
                          submitted=False)
        await s.commit()
        detection_id = row.id
    async with factory() as s:
        starter = await s.get(User, owner)
        client = await s.get(Client, cid)
        scan = await start_scan(
            s, settings=settings, executor=Recorder(), client=client, user=starter, now=NOW
        )
    assert scan.id == detection_id
    assert scan.requested_by_user_id == owner
    assert scan.trigger is ScanTrigger.MANUAL

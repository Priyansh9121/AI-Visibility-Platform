"""A failed prompt phase is free up to three times per agency per rolling day (founder, 2026-10-09).

The decision is made where the executor marks the failure, recorded on the row as a distinct
error code (PROMPTS_UNAVAILABLE_COUNTED), and `scan_allowance` counts that code like a settled
scan. Scans are seeded straight into the table; `_mark_failed` is called as the executor calls it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api.models import Scan, ScanStatus, ScanTrigger
from avp_api.services import scan_allowance as allowance
from avp_api.services import scan_executor as executor_module
from avp_api.services.prompts import PromptsUnavailable
from test_scan_allowance import _defer, _make_client, _sign_up, _start

FREE = "PROMPTS_UNAVAILABLE"
COUNTED = "PROMPTS_UNAVAILABLE_COUNTED"


@pytest.fixture
def billing_on(settings, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_" + "test_fake"))
    monkeypatch.setattr(settings, "stripe_price_id", "price_test_fake")


async def _seed(
    engine, agency_id, client_id, *, status, code=None, finished_ago=None, running=False  # noqa: ANN001
) -> str:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=agency_id,
            status=status, trigger=ScanTrigger.MANUAL, formula_version="test",
            prompt_count=0, engine_result_count=0, error_code=code,
        )
        if finished_ago is not None:
            scan.finished_at = datetime.now(UTC) - finished_ago
        if running:
            scan.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        s.add(scan)
        await s.commit()
        return scan.id


async def _fail_prompts(
    engine, settings, scan_id: str, reason: str = "too_few_questions"  # noqa: ANN001
) -> Scan:
    await executor_module._mark_failed(
        scan_id, PromptsUnavailable(reason), claimed=True, settings=settings
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        return (await s.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()


async def _held(engine, agency_id: str) -> int:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        return await allowance.count_scans(s, agency_id, now=datetime.now(UTC))


async def _running(engine, agency_id, client_id) -> str:  # noqa: ANN001
    return await _seed(engine, agency_id, client_id, status=ScanStatus.RUNNING, running=True)


async def _prior_free_failures(engine, agency_id, client_id, n, ago=timedelta(hours=1)) -> None:  # noqa: ANN001
    for _ in range(n):
        await _seed(
            engine, agency_id, client_id, status=ScanStatus.FAILED, code=FREE, finished_ago=ago
        )


class TestThreeFreeThenCounted:
    async def test_the_first_three_are_free_and_the_fourth_counts(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        for _ in range(3):
            scan = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
            assert scan.status is ScanStatus.FAILED and scan.error_code == FREE
            assert await _held(engine, agency) == 0
        fourth = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert fourth.status is ScanStatus.FAILED
        assert fourth.error_code == COUNTED
        assert await _held(engine, agency) == 1
        fifth = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert fifth.error_code == COUNTED, "counted failures count toward the three"
        assert await _held(engine, agency) == 2

    async def test_the_error_detail_stays_the_exception_type(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 3)
        scan = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert scan.error_code == COUNTED and scan.error_detail == "PromptsUnavailable"

    async def test_a_failure_25_hours_after_three_others_is_free_again(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 3, ago=timedelta(hours=25))
        scan = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert scan.error_code == FREE
        assert await _held(engine, agency) == 0

    async def test_three_inside_the_window_and_one_outside_is_still_free(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 2)
        await _prior_free_failures(engine, agency, cid, 5, ago=timedelta(hours=30))
        scan = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert scan.error_code == FREE

    async def test_another_agencys_failures_never_count_toward_the_three(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        other = await _sign_up(client, "other@test.example")
        other_client = await _make_client(client, "zendesk.com")
        await _prior_free_failures(engine, other, other_client, 5)
        mine = await _sign_up(client, "mine@test.example")
        cid = await _make_client(client, "helpscout.com")
        scan = await _fail_prompts(engine, settings, await _running(engine, mine, cid))
        assert scan.error_code == FREE
        assert await _held(engine, mine) == 0

    async def test_other_failure_codes_neither_count_nor_count_toward_the_three(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        others = (
            "EXECUTION_FAILED", "ALL_ENGINE_CALLS_FAILED", "SCAN_DEADLINE_EXCEEDED", "EXECUTOR_LOST"
        )
        for code in others:
            await _seed(engine, agency, cid, status=ScanStatus.FAILED, code=code,
                        finished_ago=timedelta(hours=1))
        assert await _held(engine, agency) == 0
        scan = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert scan.error_code == FREE

    async def test_an_execution_failure_is_never_made_counted_whatever_the_history(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 6)
        sid = await _running(engine, agency, cid)
        await executor_module._mark_failed(sid, RuntimeError("x"), claimed=True, settings=settings)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            scan = (await s.execute(select(Scan).where(Scan.id == sid))).scalar_one()
        assert scan.error_code == "EXECUTION_FAILED"
        assert await _held(engine, agency) == 0


class TestOnlyTheModelsAnswerIsCharged:
    async def test_generation_failed_is_always_free_and_never_counts_toward_the_three(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        for _ in range(4):
            scan = await _fail_prompts(
                engine, settings, await _running(engine, agency, cid), "generation_failed"
            )
            assert scan.error_code == FREE
        assert await _held(engine, agency) == 0
        # The four outages did not use up the three: an answer-caused failure is still free.
        first = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert first.error_code == FREE

    async def test_an_outage_after_three_answer_failures_is_still_free(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 3)
        scan = await _fail_prompts(
            engine, settings, await _running(engine, agency, cid), "generation_failed"
        )
        assert scan.error_code == FREE and await _held(engine, agency) == 0

    async def test_each_answer_caused_reason_goes_through_the_cap(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 3)
        for reason in ("too_few_questions", "unusable_answer", "schema_violation"):
            sid = await _running(engine, agency, cid)
            scan = await _fail_prompts(engine, settings, sid, reason)
            assert scan.error_code == COUNTED, reason


class TestARacingFailureIsSeen:
    async def test_a_failure_stamped_just_after_this_ones_clock_still_counts(
        self, client: AsyncClient, engine, settings  # noqa: ANN001
    ) -> None:
        """A racing failure commits before our lock with a `finished_at` later than our `now`."""
        agency = await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 3, ago=timedelta(seconds=-30))
        scan = await _fail_prompts(engine, settings, await _running(engine, agency, cid))
        assert scan.error_code == COUNTED


class TestTheAllowanceCountsIt:
    async def test_a_counted_failure_uses_up_the_free_scan(
        self, client: AsyncClient, billing_on, engine  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _seed(engine, agency, cid, status=ScanStatus.FAILED, code=COUNTED,
                    finished_ago=timedelta(minutes=1))
        resp = await _start(client, cid)
        assert resp.status_code == 402, resp.text
        assert resp.json()["code"] == "SCAN_LIMIT_FREE_USED"
        assert resp.json()["scansUsed"] == 1

    async def test_a_free_failure_leaves_the_free_scan_alone(
        self, client: AsyncClient, billing_on, engine  # noqa: ANN001
    ) -> None:
        agency = await _sign_up(client)
        _defer(client)
        cid = await _make_client(client, "helpscout.com")
        await _prior_free_failures(engine, agency, cid, 3)
        assert (await _start(client, cid)).status_code == 202

    async def test_a_counted_failure_counts_in_the_paid_month(
        self, client: AsyncClient, billing_on, engine, settings  # noqa: ANN001
    ) -> None:
        from test_scan_allowance import _set_subscription

        agency = await _sign_up(client)
        await _set_subscription(engine, agency, "active")
        cid = await _make_client(client, "helpscout.com")
        await _seed(engine, agency, cid, status=ScanStatus.FAILED, code=COUNTED,
                    finished_ago=timedelta(minutes=1))
        await _seed(engine, agency, cid, status=ScanStatus.FAILED, code=FREE,
                    finished_ago=timedelta(minutes=1))
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as s:
            from avp_api.models import Agency, User

            ag = (await s.execute(select(Agency).where(Agency.id == agency))).scalar_one()
            user = (await s.execute(select(User).where(User.agency_id == agency))).scalars().first()
            got = await allowance.allowance(s, ag, settings, user=user)
        assert got.scans_used == 1

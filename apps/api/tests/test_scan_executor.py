"""Out-of-band scan execution: the failure paths — Epic 9.5.

Making the endpoint return early moves the scan's fate out of the request's
hands. Two things then have to hold that did not have to before:

* a pipeline that raises must land the scan at FAILED, not leave it at RUNNING.
  Inline, an exception surfaced as a 500 and somebody saw it; in the background
  there is nobody to tell, so the row has to carry the news.
* an executor that disappears without raising at all — a deploy, a crash, which
  `BackgroundTasks` makes ordinary — must not strand the row at RUNNING forever.

Both are tested against constructed failures rather than the happy path, since
the happy path is exactly the case that never exercises them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update as sa_update

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import Client, Scan, ScanStatus
from avp_api.services import scan_executor as executor_module
from avp_api.services import scan_runner
from avp_api.services.engines import DEFAULT_ENGINES
from avp_api.services.scan_executor import (
    EXECUTION_FAILED,
    EXECUTOR_LOST,
    LEASE,
    ScanJob,
    execute_scan,
    reap_stale_scans,
    renew_lease,
)

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "exec@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Executor Test Agency", "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _defer(client: AsyncClient) -> list:
    """Executor that records the job and never runs it.

    Used where a test cares about what queueing DOES, not about the pipeline.
    Without it the suite's inline executor runs a real scan against real engine
    adapters, which is both slow and a live network call.
    """
    jobs: list = []

    class _Deferred:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
    return jobs


async def _add_scan(  # noqa: ANN001
    session, cid: str, *, status: ScanStatus, started_at, lease_expires_at=None
) -> Scan:
    row = await session.get(Client, cid)
    scan = Scan(
        id=ids.new_id(ids.SCAN),
        client_id=cid,
        agency_id=row.agency_id,
        status=status,
        started_at=started_at,
        lease_expires_at=lease_expires_at,
    )
    session.add(scan)
    await session.commit()
    return scan


class TestAPipelineThatRaises:
    async def test_the_scan_lands_failed_rather_than_stuck_running(
        self, client: AsyncClient, session, monkeypatch
    ) -> None:  # noqa: ANN001
        """Nobody is listening for the exception, so the row has to record it."""
        await _sign_up(client)
        cid = await _make_client(client)

        async def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("engine adapter exploded: SECRET VENDOR BODY")

        monkeypatch.setattr(scan_runner, "run_scan", boom)

        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        detail = (await client.get(f"{BASE}/scans/{sid}")).json()
        assert detail["status"] == "failed"
        assert detail["errorCode"] == EXECUTION_FAILED
        assert detail["finishedAt"] is not None

    async def test_the_recorded_detail_is_our_own_diagnostic_never_vendor_text(
        self, client: AsyncClient, session, monkeypatch
    ) -> None:  # noqa: ANN001
        """ip-safety.md #7 — `error_detail` carries our diagnostics only.

        An exception message can contain a provider response body. Only the
        exception TYPE is persisted.
        """
        await _sign_up(client)
        cid = await _make_client(client)

        async def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("engine adapter exploded: SECRET VENDOR BODY")

        monkeypatch.setattr(scan_runner, "run_scan", boom)

        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        # Asserted on the ROW, because `error_detail` is deliberately not on
        # ScanOut — it is an operator diagnostic, not a client-facing field.
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan.error_detail == "RuntimeError"
        assert "SECRET VENDOR BODY" not in (scan.error_detail or "")

        # And nothing leaks through the API either.
        resp = await client.get(f"{BASE}/scans/{sid}")
        assert "SECRET VENDOR BODY" not in resp.text

    async def test_the_queue_response_still_succeeds(
        self, client: AsyncClient, monkeypatch
    ) -> None:  # noqa: ANN001
        """A failure after acceptance is not a failure to accept."""
        await _sign_up(client)
        cid = await _make_client(client)

        async def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("nope")

        monkeypatch.setattr(scan_runner, "run_scan", boom)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert resp.status_code == 202, resp.text


class TestTheExecutorClaimsBeforeItRuns:
    """The TERMINAL-only guard gap — API key discipline audit, 2026-09-07.

    `execute_scan` refused only finished scans, and QUEUED / RUNNING are
    deliberately outside TERMINAL so the first executor can run at all. Two
    POSTs adopting one open scan therefore produced two executors, and the
    second re-ran the paid chain until `prompt_sets`' unique constraint
    stopped it. `uq_scans_one_open_per_client` bounds rows, not executors.
    """

    async def test_two_executors_handed_one_queued_scan_run_it_once(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        jobs = _defer(client)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert len(jobs) == 1

        runs: list[str] = []

        async def counting_run_scan(session_, scan, client_, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
            runs.append(scan.id)
            await asyncio.sleep(0.05)  # hold the slot so the two genuinely overlap
            return scan

        monkeypatch.setattr(scan_runner, "run_scan", counting_run_scan)

        await asyncio.gather(
            execute_scan(jobs[0], settings=settings),
            execute_scan(jobs[0], settings=settings),
        )

        assert runs == [sid], "the loser must exit before the chain, not after it"
        detail = (await client.get(f"{BASE}/scans/{sid}")).json()
        # The loser did not mark the winner's scan failed on its way out.
        assert detail["status"] == "succeeded"
        assert detail["errorCode"] is None

    async def test_the_claim_precedes_the_first_paid_phase(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """Competitor detection runs before the engine loop and is the first
        phase that spends money. A claim taken any later would let a duplicate
        executor pay for detection before discovering it had lost."""
        await _sign_up(client)
        cid = await _make_client(client)
        jobs = _defer(client)
        await client.post(f"{BASE}/clients/{cid}/scans", json={})

        seen: dict = {}

        async def observing(session_, scan, client_, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
            seen["status"] = scan.status
            seen["started_at"] = scan.started_at

        async def noop_run_scan(session_, scan, client_, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
            return scan

        monkeypatch.setattr(executor_module.detection, "ensure_set_for_scan", observing)
        monkeypatch.setattr(scan_runner, "run_scan", noop_run_scan)

        await execute_scan(jobs[0], settings=settings)

        assert seen["status"] is ScanStatus.RUNNING
        assert seen["started_at"] is not None

    async def test_a_scan_already_running_is_left_to_its_executor(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING, started_at=datetime.now(UTC)
        )

        async def must_not_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            raise AssertionError("a running scan was run again")

        monkeypatch.setattr(scan_runner, "run_scan", must_not_run)

        await execute_scan(
            ScanJob(scan_id=scan.id, client_id=cid, engines=DEFAULT_ENGINES),
            settings=settings,
        )

        await session.refresh(scan)
        assert scan.status is ScanStatus.RUNNING
        assert scan.error_code is None


class TestTheEngineLoopDoesNotHoldTheScanRow:
    """The lease's precondition, and it is a row lock — 2026-09-08.

    `run_scan` sets `prompt_count` and flushes it. That flush is an `UPDATE
    scans`, which takes a row lock held until the transaction ends — and the
    next statement on that session used to be the commit AFTER the engine
    loop. So for the whole engine phase, minutes long, nothing else could
    write the scan row: not a lease renewal on its own session, not
    `reap_stale_scans` from a dashboard read.

    A heartbeat that blocks is a lease that expires during ordinary work, so
    this is tested before the lease depends on it. Asserted from INSIDE the
    loop, on a second session, because that is the only moment the lock was
    ever held.
    """

    async def test_another_session_can_write_the_scan_row_mid_loop(
        self, client: AsyncClient, session, engine, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        from sqlalchemy import update as sa_update
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from avp_api.models.prompt import PromptIntent
        from avp_api.services.engines import EngineAnswer
        from avp_api.services.prompts import GeneratedPrompt

        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(session, cid, status=ScanStatus.QUEUED, started_at=None)
        row = await session.get(Client, cid)
        await session.commit()

        outcome: dict = {}
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return [GeneratedPrompt(text="q1", intent=PromptIntent.AWARENESS)], "stub"

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            # Mid-loop: the executor's transaction is open and has already
            # flushed prompt_count. Renew the lease from somewhere else.
            async with factory() as other:
                try:
                    await asyncio.wait_for(
                        other.execute(
                            sa_update(Scan)
                            .where(Scan.id == scan.id)
                            .values(lease_expires_at=datetime.now(UTC))
                        ),
                        timeout=5.0,
                    )
                    await other.commit()
                    outcome["renewed"] = True
                except TimeoutError:
                    outcome["renewed"] = False
            return [
                EngineAnswer(engine=e, engine_version="stub", prompt_text=prompt, text="hi")
                for e in engines
            ]

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)

        await scan_runner.run_scan(session, scan, row, settings=settings)

        assert outcome.get("renewed") is True, (
            "the engine loop still holds the scans row lock; a heartbeat would block on it"
        )


class TestTheLeaseStopsAnExecutorThatLostIt:
    """The property the whole lease exists for — 2026-09-08.

    A reaper alone stamps the row and the work carries on, billing against a
    scan somebody has already written off. A renewal that matches zero rows is
    what tells the executor to stop, and these tests are the proof it does,
    because nothing else in the system would notice if it did not.
    """

    async def test_a_renewal_pushes_the_lease_out(
        self, client: AsyncClient, session, settings
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        before = datetime.now(UTC) + timedelta(seconds=5)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING,
            started_at=datetime.now(UTC), lease_expires_at=before,
        )

        assert await renew_lease(scan.id, settings=settings) is True

        await session.refresh(scan)
        assert scan.lease_expires_at > before

    @pytest.mark.parametrize(
        "status", [ScanStatus.FAILED, ScanStatus.SUCCEEDED, ScanStatus.QUEUED]
    )
    async def test_a_renewal_against_a_row_that_is_not_running_matches_nothing(
        self, client: AsyncClient, session, settings, status: ScanStatus
    ) -> None:  # noqa: ANN001
        """Zero rows is a definite answer: this row is not ours any more.

        FAILED is the reaper having taken it; SUCCEEDED is a finalise that beat
        us; QUEUED is a state no executor should be renewing from at all.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=status, started_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + LEASE,
        )

        assert await renew_lease(scan.id, settings=settings) is False

    async def test_the_chain_stops_mid_phase_when_the_lease_is_taken_away(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The billing question, asked directly.

        The chain is held inside its first phase. The lease is then reaped out
        from under it on another connection, exactly as `reap_stale_scans`
        would. The next heartbeat matches zero rows, and the phases after this
        one must never run — each of them spends money.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        jobs = _defer(client)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        monkeypatch.setattr(
            executor_module, "HEARTBEAT_INTERVAL", timedelta(milliseconds=20)
        )
        reached: list[str] = []
        in_first_phase = asyncio.Event()

        async def blocking_competitors(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            reached.append("competitors")
            in_first_phase.set()
            await asyncio.sleep(30)  # cancelled long before this returns

        async def later_phase(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            reached.append("run_scan")

        monkeypatch.setattr(
            executor_module.detection, "ensure_set_for_scan", blocking_competitors
        )
        monkeypatch.setattr(scan_runner, "run_scan", later_phase)

        async def take_the_lease() -> None:
            await in_first_phase.wait()
            await session.execute(
                sa_update(Scan)
                .where(Scan.id == sid)
                .values(status=ScanStatus.FAILED, error_code=EXECUTOR_LOST)
            )
            await session.commit()

        await asyncio.gather(
            execute_scan(jobs[0], settings=settings), take_the_lease()
        )

        assert reached == ["competitors"], "the chain kept spending after the lease went"
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        # Untouched by the executor on its way out: the reaper's verdict stands.
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTOR_LOST

    async def test_a_claim_stamps_a_lease_the_reaper_will_not_take(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        jobs = _defer(client)
        await client.post(f"{BASE}/clients/{cid}/scans", json={})

        seen: dict = {}

        async def observing(session_, scan, client_, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
            seen["lease"] = scan.lease_expires_at

        async def noop(session_, scan, client_, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
            return scan

        monkeypatch.setattr(executor_module.detection, "ensure_set_for_scan", observing)
        monkeypatch.setattr(scan_runner, "run_scan", noop)

        await execute_scan(jobs[0], settings=settings)

        assert seen["lease"] is not None, "the claim did not stamp a lease"
        assert seen["lease"] > datetime.now(UTC)
        assert await reap_stale_scans(session) == 0


class TestStaleScanReaper:
    """Reaped for going quiet, not for taking long — the lease, 2026-09-08.

    Every test here used to stamp `started_at` far enough in the past to clear
    `STALE_AFTER`. They now stamp an EXPIRED LEASE, which is the same
    situation asked about correctly: a maximal scan legitimately runs longer
    than the old constant allowed, so duration never distinguished a working
    executor from a dead one.
    """

    @staticmethod
    def _long_ago() -> datetime:
        return datetime.now(UTC) - LEASE - timedelta(seconds=60)

    async def test_a_running_scan_whose_lease_expired_is_failed(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING, started_at=self._long_ago(),
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        assert await reap_stale_scans(session) == 1
        await session.commit()

        await session.refresh(scan)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTOR_LOST
        assert scan.finished_at is not None

    async def test_a_long_running_scan_with_a_live_lease_is_left_alone(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """The case the old predicate got wrong.

        Started 40 minutes ago — well past the retired 900s STALE_AFTER — and
        still renewing. Under duration it was reaped while running and still
        billing; under a lease it is obviously alive.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(seconds=2_400),
            lease_expires_at=datetime.now(UTC) + LEASE,
        )

        assert await reap_stale_scans(session) == 0
        await session.commit()

        await session.refresh(scan)
        assert scan.status is ScanStatus.RUNNING

    async def test_a_running_scan_with_no_lease_is_left_alone(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """NULL is not expired.

        `scripts/verify_e2e.py`, `scripts/verify_scoring.py` and this suite's
        own fixtures drive `run_scan` directly: it sets RUNNING itself and
        holds no lease. Reaping those mid-flight would fail live work. The
        accepted cost is on the reaper's docstring.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING, started_at=self._long_ago(),
            lease_expires_at=None,
        )

        assert await reap_stale_scans(session) == 0
        await session.commit()

        await session.refresh(scan)
        assert scan.status is ScanStatus.RUNNING

    async def test_a_queued_scan_is_never_reaped_however_old(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """QUEUED means open, not claimed — and detection leaves one behind.

        `POST /clients/{clientId}/competitors` opens a scan for a CompetitorSet
        to hang off, and an operator may run it days later. Reaping QUEUED would
        break detect-now-scan-tomorrow, so the reaper only ever touches RUNNING.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.QUEUED, started_at=None,
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        assert await reap_stale_scans(session) == 0
        await session.commit()

        await session.refresh(scan)
        assert scan.status is ScanStatus.QUEUED

    @pytest.mark.parametrize(
        "status", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED]
    )
    async def test_a_finished_scan_is_not_re_failed(
        self, client: AsyncClient, session, status: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=status, started_at=self._long_ago(),
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        assert await reap_stale_scans(session) == 0
        await session.commit()

        await session.refresh(scan)
        assert scan.status is status


class TestTheReaperRunsWhereItMatters:
    async def test_the_dashboard_reaps_on_read(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """Where a stranded row does its visible damage.

        Epic 9.3's screen renders RUNNING as "Running…" and disables re-run for
        that client on the strength of it. Correcting it at the point of reading
        means the lie is never told.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING,
            started_at=datetime.now(UTC) - LEASE - timedelta(seconds=60),
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        board = (await client.get(f"{BASE}/dashboard")).json()

        row = next(s for s in board["recentScans"] if s["id"] == scan.id)
        assert row["status"] == "failed"
        await session.refresh(scan)
        assert scan.error_code == EXECUTOR_LOST

    async def test_queueing_a_scan_clears_a_stranded_row_first(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """Where a stranded row does its worst damage.

        `get_or_create_scan` reuses any QUEUED/RUNNING scan. Reusing a stranded
        one would re-run the pipeline against a scan that already has a prompt
        set, which dies on `prompt_sets`' unique constraint as a 500. Reaping
        first turns that into an ordinary new scan.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stranded = await _add_scan(
            session, cid, status=ScanStatus.RUNNING,
            started_at=datetime.now(UTC) - LEASE - timedelta(seconds=60),
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        _defer(client)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})

        assert resp.status_code == 202, resp.text
        assert resp.json()["id"] != stranded.id, "the stranded scan was reused"
        await session.refresh(stranded)
        assert stranded.status is ScanStatus.FAILED
        assert stranded.error_code == EXECUTOR_LOST

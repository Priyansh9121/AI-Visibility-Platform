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

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import Client, Scan, ScanStatus
from avp_api.services import scan_runner
from avp_api.services.scan_executor import (
    EXECUTION_FAILED,
    EXECUTOR_LOST,
    STALE_AFTER,
    reap_stale_scans,
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


async def _add_scan(session, cid: str, *, status: ScanStatus, started_at) -> Scan:  # noqa: ANN001
    row = await session.get(Client, cid)
    scan = Scan(
        id=ids.new_id(ids.SCAN),
        client_id=cid,
        agency_id=row.agency_id,
        status=status,
        started_at=started_at,
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


class TestStaleScanReaper:
    @staticmethod
    def _long_ago() -> datetime:
        return datetime.now(UTC) - STALE_AFTER - timedelta(seconds=60)

    async def test_a_running_scan_past_the_deadline_is_failed(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING, started_at=self._long_ago()
        )

        assert await reap_stale_scans(session) == 1
        await session.commit()

        await session.refresh(scan)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTOR_LOST
        assert scan.finished_at is not None

    async def test_a_running_scan_inside_the_deadline_is_left_alone(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """A slow scan is not a lost one. Epic 9.2 measured ~303s for this
        endpoint's share; the deadline is 900s precisely so real work survives."""
        await _sign_up(client)
        cid = await _make_client(client)
        scan = await _add_scan(
            session, cid, status=ScanStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(seconds=300),
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
            session, cid, status=ScanStatus.QUEUED, started_at=None
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
        scan = await _add_scan(session, cid, status=status, started_at=self._long_ago())

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
            started_at=datetime.now(UTC) - STALE_AFTER - timedelta(seconds=60),
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
            started_at=datetime.now(UTC) - STALE_AFTER - timedelta(seconds=60),
        )
        _defer(client)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})

        assert resp.status_code == 202, resp.text
        assert resp.json()["id"] != stranded.id, "the stranded scan was reused"
        await session.refresh(stranded)
        assert stranded.status is ScanStatus.FAILED
        assert stranded.error_code == EXECUTOR_LOST

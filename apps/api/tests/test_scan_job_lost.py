"""A scan whose job vanished is failed by the never-started path, within its window — K1 (d).

Scans do not use Redis. They run in the API's own process (`BackgroundTasks`), and the API never
sends a Celery task, so a Redis wipe cannot lose one. What CAN lose a scan's job is the process:
a deploy or a crash between the `202` and the start leaves a QUEUED row nothing will ever claim
(the broker, had there been one, would leave the same row). This is that case, built through the
real endpoint with an executor that takes the job and drops it.

What is pinned:

* `POST /clients/{id}/scans` answers `202` with the scan QUEUED and stamps `submitted_at`; the
  executor is handed the job, and nothing runs;
* 14 minutes after submission a read leaves it QUEUED; 16 minutes after, the same read fails it
  with `EXECUTOR_NEVER_STARTED`, and the read's payload carries that `errorCode` (the dashboard
  turns it into a sentence and never prints it: `scanOutcome.ts`, `DashboardView.test.tsx`);
* the window is MORE than `IN_FLIGHT_WINDOW` (15 minutes) since submission: at exactly 15 minutes
  the row is still QUEUED, a moment later it is not;
* nothing was spent: the row was never claimed, so it has no start time and no results.

Which reads do it (read from the code, and what these parametrize over): `GET /dashboard` and
`POST /clients/{id}/scans` run `reap_stale_scans` for every agency; `GET /scans/{id}` fails that
one scan. `GET /clients/{id}/scans` runs nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from avp_api.deps import scan_executor
from avp_api.models import EngineResult, Scan, ScanStatus
from avp_api.services.scan_allowance import IN_FLIGHT_WINDOW
from avp_api.services.scan_executor import (
    EXECUTOR_NEVER_STARTED,
    ScanJob,
    fail_never_started_scans,
    reap_stale_scans,
)

BASE = "/api/v1"


class VanishingExecutor:
    """Takes the job and loses it: what a restart between the response and the start does."""

    def __init__(self) -> None:
        self.handed_over: list[ScanJob] = []

    async def submit(self, job: ScanJob, *, settings: Any) -> None:  # noqa: ARG002
        self.handed_over.append(job)  # and nothing else: no task, no run, no later claim


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Lost Job Agency", "fullName": "Op",
              "email": "lost-job@test.example", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def _start_a_scan_whose_job_vanishes(
    client: AsyncClient,
) -> tuple[str, str, VanishingExecutor]:
    """Sign up, add a client, press Run. Returns (client id, scan id, the executor)."""
    await _sign_up(client)
    made = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert made.status_code == 201, made.text
    cid = made.json()["id"]
    executor = VanishingExecutor()
    client._transport.app.dependency_overrides[scan_executor] = lambda: executor  # noqa: SLF001

    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "queued"
    return cid, resp.json()["id"], executor


async def _submitted(session, scan_id: str, ago: timedelta) -> None:  # noqa: ANN001
    """Move the submission back in time: the same row, `ago` earlier than now."""
    await session.execute(
        sa_update(Scan).where(Scan.id == scan_id).values(submitted_at=datetime.now(UTC) - ago)
    )
    await session.commit()


async def _read_dashboard(client: AsyncClient, scan_id: str) -> dict[str, Any]:
    resp = await client.get(f"{BASE}/dashboard")
    assert resp.status_code == 200, resp.text
    return next(s for s in resp.json()["recentScans"] if s["id"] == scan_id)


async def _read_scan(client: AsyncClient, scan_id: str) -> dict[str, Any]:
    resp = await client.get(f"{BASE}/scans/{scan_id}")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


READS = {"the dashboard": _read_dashboard, "a single scan": _read_scan}


class TestAScanWhoseJobVanished:
    @pytest.mark.parametrize("read", READS, ids=list(READS))
    async def test_it_stays_queued_at_14_minutes_and_is_failed_never_started_at_16(
        self, client: AsyncClient, session, read: str  # noqa: ANN001
    ) -> None:
        _, scan_id, executor = await _start_a_scan_whose_job_vanishes(client)

        # What the 202 left behind: queued, submitted, handed to an executor that lost it.
        queued = await session.get(Scan, scan_id)
        assert queued.status is ScanStatus.QUEUED
        assert queued.submitted_at is not None
        assert queued.started_at is None
        assert [job.scan_id for job in executor.handed_over] == [scan_id]

        # 14 minutes on: inside the window, so the read leaves it alone.
        await _submitted(session, scan_id, timedelta(minutes=14))
        row = await READS[read](client, scan_id)
        assert row["status"] == "queued"
        assert row["errorCode"] is None
        await session.refresh(queued)
        assert queued.status is ScanStatus.QUEUED
        assert queued.finished_at is None

        # 16 minutes on: past it. The same read fails the scan, and says why in its payload.
        await _submitted(session, scan_id, timedelta(minutes=16))
        row = await READS[read](client, scan_id)
        assert row["status"] == "failed"
        assert row["errorCode"] == EXECUTOR_NEVER_STARTED
        assert row["finishedAt"] is not None

        # On the row: failed once, never claimed, nothing run and nothing spent.
        await session.refresh(queued)
        assert queued.status is ScanStatus.FAILED
        assert queued.error_code == EXECUTOR_NEVER_STARTED
        assert "15 minutes" in (queued.error_detail or "")
        assert queued.started_at is None
        assert queued.finished_at is not None
        engine_results = await session.scalar(
            select(func.count()).select_from(EngineResult).where(EngineResult.scan_id == scan_id)
        )
        assert engine_results == 0

    async def test_the_window_is_more_than_fifteen_minutes_not_fifteen_or_more(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        """Strict: at exactly `IN_FLIGHT_WINDOW` after submission the row is still QUEUED, and one
        microsecond later the reaper takes it. Driven with the reaper's own clock, so there is no
        race with the wall clock."""
        _, scan_id, _ = await _start_a_scan_whose_job_vanishes(client)
        submitted = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
        await session.execute(
            sa_update(Scan).where(Scan.id == scan_id).values(submitted_at=submitted)
        )
        await session.commit()
        assert IN_FLIGHT_WINDOW.total_seconds() == 15 * 60
        scan = await session.get(Scan, scan_id)

        at_the_window = submitted + IN_FLIGHT_WINDOW
        assert await fail_never_started_scans(session, now=at_the_window) == []
        assert await reap_stale_scans(session, now=at_the_window) == 0
        await session.commit()
        await session.refresh(scan)
        assert scan.status is ScanStatus.QUEUED

        just_past = at_the_window + timedelta(microseconds=1)
        assert await reap_stale_scans(session, now=just_past) == 1
        await session.commit()
        await session.refresh(scan)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTOR_NEVER_STARTED

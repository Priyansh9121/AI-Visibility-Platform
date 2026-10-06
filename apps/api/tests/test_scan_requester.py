"""Starting a scan makes the starter its requester, even on a row detection opened — F1 round 2.

`POST /clients/{id}/scans` adopts the QUEUED scan competitor detection opened. That row carries
the colleague who ran detection; the scan-finished email (and the opt-out it honours) must be the
one of the user who pressed Run.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import Scan, ScanStatus, User, UserRole

BASE = "/api/v1"


async def test_the_user_who_starts_the_scan_is_its_requester_not_the_one_who_ran_detection(
    client: AsyncClient, session: AsyncSession
) -> None:
    signed_up = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Req Agency", "fullName": "Starter", "email": "starter@req.example",
              "password": "correct-horse-battery-staple"},
    )
    assert signed_up.status_code == 201, signed_up.text
    starter_id = signed_up.json()["user"]["id"]
    agency_id = signed_up.json()["agency"]["id"]
    created = await client.post(f"{BASE}/clients", json={"url": "req-test.com", "classify": False})
    client_id = created.json()["id"]

    colleague = User(id=ids.new_id(ids.USER), agency_id=agency_id, email="mate@req.example",
                     full_name="Colleague", role=UserRole.MEMBER, password_hash="x")
    session.add(colleague)
    await session.flush()
    opened = Scan(id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=agency_id,
                  requested_by_user_id=colleague.id, status=ScanStatus.QUEUED)
    session.add(opened)
    await session.commit()
    opened_id = opened.id

    jobs: list[Any] = []

    class _Deferred:
        async def submit(self, job: Any, *, settings: Any) -> None:  # noqa: ANN401
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # type: ignore[attr-defined]  # noqa: SLF001

    resp = await client.post(f"{BASE}/clients/{client_id}/scans", json={})
    assert resp.status_code == 202, resp.text

    session.expire_all()
    row = (await session.execute(select(Scan).where(Scan.id == opened_id))).scalar_one()
    assert row.requested_by_user_id == starter_id
    assert row.submitted_at is not None
    assert len(jobs) == 1 and jobs[0].scan_id == opened_id

"""Action list endpoints — Epic 8.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep
from ..errors import NotFound
from ..models import Client, Scan
from ..schemas.action_item import ActionItemListOut, ActionItemOut
from ..services import fix_runner

router = APIRouter(tags=["actions"])


async def _load_scan(db: Any, scan_id: str, agency_id: str) -> Scan:
    scan = (
        await db.execute(select(Scan).where(Scan.id == scan_id, Scan.agency_id == agency_id))
    ).scalar_one_or_none()
    if scan is None:
        # 404 not 403 — confirming an id exists leaks across tenants.
        raise NotFound(detail="No scan with that identifier.")
    return scan


def _out(scan_id: str, rows: list[Any], status_: str, reason_code: str | None) -> Any:
    items = [ActionItemOut.model_validate(row) for row in rows]
    return ActionItemListOut(
        scan_id=scan_id,
        status=status_,
        reason_code=reason_code,
        generated_by=next((i.generated_by for i in items if i.generated_by), None),
        items=items,
    )


@router.post(
    "/scans/{scanId}/fixes",
    response_model=ActionItemListOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_fixes(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """Generate the scan's prioritised fix list.

    Makes **one model call**. The candidates — which dimension gaps are worth a
    line, which audit findings warrant a fix, and in what order — are decided
    before the call by the same rules Epic 7 already applies on the client. The
    model words them and judges priority and effort; it cannot add a
    recommendation the measurement did not produce.

    One row per candidate per scan, refreshed on re-run. A regenerated list is a
    better statement of the same measurement, not a second measurement — but
    `status` is the operator's, so it survives regeneration, and a candidate
    that has been worked is kept even once it stops being measured.

    A provider failure writes nothing and returns `status: "failed"` with a
    reason code. The report still renders its deterministic fix list.
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    client = (
        await db.execute(select(Client).where(Client.id == scan.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise NotFound(detail="The scan's client no longer exists.")

    rows, outcome = await fix_runner.generate_for_scan(db, scan, client)
    await db.commit()

    refreshed = await fix_runner.latest_fixes(db, scan.id)
    return _out(scan.id, refreshed or rows, outcome.status, outcome.reason_code)


@router.get("/scans/{scanId}/fixes", response_model=ActionItemListOut)
async def get_fixes(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """The scan's fix list as last generated, in rank order.

    Returns an empty list with `status: "empty"` rather than 404 when nothing
    has been generated yet: "no fixes yet" is a state of a scan that exists,
    and the report renders its deterministic list in that case.
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    rows = await fix_runner.latest_fixes(db, scan.id)
    return _out(scan.id, rows, "generated" if rows else "empty", None)

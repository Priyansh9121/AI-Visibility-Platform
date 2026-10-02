"""Action list endpoints — Epic 8.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep, SettingsDep
from ..errors import Conflict, NotFound
from ..models import Client, Scan
from ..schemas.action_item import ActionItemListOut, ActionItemOut
from ..services import fix_runner
from ..services.failed_scan import score_is_withheld, served_fixes

router = APIRouter(tags=["actions"])

# The generator is handed the scan's score, and what it writes can quote it, so a
# FAILED scan (no score is ever shown for one: 3.24, 3.30) has no fixes served and none
# written. See `services/failed_scan.py`.
FAILED_NO_FIXES = "This scan failed, so no fixes are written for it."


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
    settings: SettingsDep,
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

    **A FAILED scan has no fixes served** (3.30, extending 3.24). For a scan still
    running, the generator's input includes its score (founder's decision), and what it
    writes can quote it. If the scan then fails, nothing it wrote is served, on any
    route, and the rows are kept. So `POST` for a failed scan is `409`
    `/problems/conflict`, "This scan failed, so no fixes are written for it.", with no
    model call and no row written or changed. The agency check comes first, so another
    agency's failed scan is the same `404` as an unknown one. A scan that fails while the
    generation runs gets the same `409`: what the call wrote is kept, and not served.
    Every other status is unchanged.

    **Errors:** `401`, `404` (unknown scan, or another agency's), `409` (failed scan).
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    if score_is_withheld(scan):
        # Before the client lookup and the model call, which would spend a call on
        # a list that is never served and rewrite the rows the scan kept.
        raise Conflict(detail=FAILED_NO_FIXES)
    client = (
        await db.execute(select(Client).where(Client.id == scan.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise NotFound(detail="The scan's client no longer exists.")

    # Settings come from the request's dependency, the same way the scan
    # chain passes its own through — never from the module-level getter,
    # which reads the process environment and `.env`. The CI gate (Epic 18)
    # found this: the endpoint's tests passed only where a developer's `.env`
    # happened to hold a real key for a call the tests mock.
    rows, outcome = await fix_runner.generate_for_scan(db, scan, client, settings=settings)
    await db.commit()

    # The call took seconds, and the scan can have failed in that time (its deadline, or
    # the reaper). `scan` was loaded before it and the commit does not expire it
    # (`expire_on_commit=False`), so its status is the one from before the call. Read it
    # again. What the call wrote is committed above and stays; none of it is served.
    await db.refresh(scan)
    if score_is_withheld(scan):
        raise Conflict(detail=FAILED_NO_FIXES)

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

    **A FAILED scan has no fixes served** (3.30, extending 3.24): the same `200` with
    `status: "empty"` and `items: []` as a scan whose fixes were never generated,
    whatever rows the scan kept. The generator's input includes a running scan's score
    (founder's decision), so if the scan then fails nothing it wrote is served, on any
    route. The rows are kept, not changed or deleted. Every other status is unchanged.

    **Errors:** `401`, `404` (unknown scan, or another agency's).
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    rows = served_fixes(scan, await fix_runner.latest_fixes(db, scan.id))
    return _out(scan.id, rows, "generated" if rows else "empty", None)

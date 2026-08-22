"""Report endpoint — Epic 7.

Recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep
from ..errors import NotFound
from ..models import Scan
from ..schemas.report import ReportOut
from ..services import report as report_service

router = APIRouter(tags=["reports"])


@router.get("/scans/{scanId}/report", response_model=ReportOut)
async def get_report(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """Everything the narrative report needs, in one response.

    **Presents; does not compute.** Score, competitors and audit are read from
    the rows Epics 3-6 wrote. The only new numbers are aggregates — citation
    counts per domain, mention shares, per-engine coverage — and each is a
    count over stored facts.

    Degraded data is reported, never smoothed over. `score` is null when the
    scan was never scored, and a score with `status: "insufficient_data"` is a
    different thing again: an unrunnable scan is never rendered as a low score.
    `competitorSet` is null when detection never ran, and `audit` is null when
    the site was never audited — in each case the report says so rather than
    quietly omitting a beat.

    **Errors:** `401`, `404` (unknown scan, or another agency's).
    """
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == principal.agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        # 404, never 403 — confirming an id exists is a cross-tenant leak.
        raise NotFound(detail="No scan with that identifier.")

    return await report_service.build_report(db, scan)

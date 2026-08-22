"""Agency dashboard.

Epic 1's acceptance criterion is "an agency can sign up, log in, and see an
empty dashboard", so this endpoint's most important behaviour is being correct
when there is nothing to show.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..deps import DbDep, PrincipalDep
from ..models import Client, Scan, Score, ScoreStatus
from ..schemas.auth import AgencyOut, SeatUsageOut
from ..schemas.dashboard import DashboardOut, ScanSummaryOut
from ..services import seats as seat_service

router = APIRouter(tags=["dashboard"])

DEFAULT_RECENT_SCANS = 10


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=DEFAULT_RECENT_SCANS, ge=1, le=50),
) -> Any:
    agency_id = principal.agency_id

    client_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Client)
                .where(Client.agency_id == agency_id, Client.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    scan_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Scan).where(Scan.agency_id == agency_id)
            )
        ).scalar_one()
    )

    # Left-join the score so an unscored or insufficient-data scan still
    # appears in the list, with a null score rather than being filtered out.
    stmt = (
        select(Scan, Client.name, Client.domain, Score.composite)
        .join(Client, Client.id == Scan.client_id)
        .outerjoin(
            Score,
            (Score.scan_id == Scan.id) & (Score.status == ScoreStatus.SCORED),
        )
        .where(Scan.agency_id == agency_id)
        # id is a ULID, so this is creation order without a second index.
        .order_by(Scan.id.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    recent = [
        ScanSummaryOut(
            id=scan.id,
            client_id=scan.client_id,
            client_name=client_name,
            client_domain=client_domain,
            status=scan.status,
            composite_score=composite,
            created_at=scan.created_at,
            finished_at=scan.finished_at,
        )
        for scan, client_name, client_domain, composite in rows
    ]

    used, limit_seats = await seat_service.seat_usage(db, agency_id)

    return DashboardOut(
        agency=AgencyOut.model_validate(principal.agency),
        seats=SeatUsageOut(used=used, limit=limit_seats),
        client_count=client_count,
        scan_count=scan_count,
        recent_scans=recent,
        is_empty=scan_count == 0 and client_count == 0,
    )

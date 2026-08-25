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
from ..services.scan_executor import reap_stale_scans

router = APIRouter(tags=["dashboard"])

DEFAULT_RECENT_SCANS = 10


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=DEFAULT_RECENT_SCANS, ge=1, le=50),
) -> Any:
    agency_id = principal.agency_id

    # Check-on-read — Epic 9.5. This is a GET that writes, which is deliberate.
    #
    # The failure mode is a scan left at RUNNING because the process executing
    # it went away (a deploy, a crash), which BackgroundTasks makes an ordinary
    # event rather than an exotic one. Nothing else in the system corrects it,
    # and the damage is entirely here: the row renders "Running…" forever and
    # Epic 9.3's screen disables re-run for that client on the strength of it.
    #
    # Reaping at the point of reading means the correction happens exactly where
    # and when the lie would otherwise be told, and it needs no scheduler — which
    # matters for the same reason Epic 9.4 chose BackgroundTasks over Celery: a
    # periodic task means a process to run and supervise, and this product is
    # pre-pilot and single-instance. A startup-only check would miss a scan
    # stranded by a worker dying while its peers keep serving; this does not.
    #
    # One UPDATE against `ix_scans_status`, and it no-ops when nothing is stale.
    if await reap_stale_scans(db):
        await db.commit()

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

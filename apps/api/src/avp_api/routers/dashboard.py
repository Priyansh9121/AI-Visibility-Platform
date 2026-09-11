"""Agency dashboard.

Epic 1's acceptance criterion is "an agency can sign up, log in, and see an
empty dashboard", so this endpoint's most important behaviour is being correct
when there is nothing to show.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, select

from ..deps import DbDep, PrincipalDep
from ..models import Agency, Client, Scan, Score, ScoreStatus
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

    # THE GETTING-STARTED FACTS — Epic 19. Derived, not stored.
    #
    # The checklist on the dashboard says whether the agency has a client, a
    # scan, a score, a teammate and a shared report. Four of those five are
    # already on this response (`client_count`, `scan_count`, `seats`), or
    # would be if `recent_scans` were the whole history — which it is not: it
    # is a page of ten, so "has any scan ever been scored?" needs its own
    # count once the scored one has scrolled off. Same for "is any report
    # shared?", which `recent_scans` does not carry at all.
    #
    # Two COUNT queries rather than an `onboarding_progress` table or a flag
    # per step. A stored flag would have to be written by the scoring path,
    # the share path and the seat path, each of which would then own a piece
    # of the checklist's truth; a count reads the truth from where it already
    # lives, and cannot disagree with it. The cost is two indexed counts per
    # dashboard read, on the same `ix_scans_agency_created` the list uses.
    #
    # `share_token IS NOT NULL` is "a live link exists". Mint sets it, revoke
    # clears it, expiry leaves it — so revoking a report's only share link
    # un-does the step, and letting one expire does not. That is the honest
    # reading of the column: the checklist describes the account's present
    # state, not its history, the same way `client_count` drops when a client
    # is deleted.
    scored_scan_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Score)
                .join(Scan, Scan.id == Score.scan_id)
                .where(Scan.agency_id == agency_id, Score.status == ScoreStatus.SCORED)
            )
        ).scalar_one()
    )
    shared_scan_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Scan)
                .where(Scan.agency_id == agency_id, Scan.share_token.is_not(None))
            )
        ).scalar_one()
    )

    return DashboardOut(
        agency=AgencyOut.model_validate(principal.agency),
        seats=SeatUsageOut(used=used, limit=limit_seats),
        client_count=client_count,
        scan_count=scan_count,
        recent_scans=recent,
        is_empty=scan_count == 0 and client_count == 0,
        scored_scan_count=scored_scan_count,
        shared_scan_count=shared_scan_count,
        getting_started_dismissed=principal.agency.getting_started_dismissed_at is not None,
    )


@router.post("/dashboard/getting-started/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_getting_started(principal: PrincipalDep, db: DbDep) -> Response:
    """Hide the getting-started checklist for this agency. **Auth required.**

    **Per agency, and any seat holder may do it.** The checklist describes the
    agency's account, so hiding it is an account-level choice, and it is not a
    setting that changes what anyone can do — a member who dismisses it takes
    nothing from the owner except a card the owner could also have closed.
    That is why this is not behind `RequireAdmin`, unlike everything under
    `/agencies/{id}`, which changes who holds a seat or who pays.

    **Idempotent.** Dismissing twice is not an error and does not move the
    timestamp: the first dismissal is the fact, and a second click on a stale
    page is not a second decision.

    **There is no un-dismiss.** The checklist is a first-week affordance; an
    agency that closed it and wants it back has, by then, either done the
    steps or decided not to. If that turns out to be wrong, clearing the
    column is a one-line endpoint, not a design.

    **Errors:** `401 authentication-required`.
    """
    agency = (
        await db.execute(select(Agency).where(Agency.id == principal.agency_id))
    ).scalar_one()
    if agency.getting_started_dismissed_at is None:
        agency.getting_started_dismissed_at = datetime.now(UTC)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

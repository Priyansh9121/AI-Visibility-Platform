"""Alert feed and acknowledgement — Epic E.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from ..deps import DbDep, PrincipalDep
from ..models.alert import Alert
from ..models.scan import Scan, ScanStatus
from ..schemas.alert import AcknowledgeAlertOut, AlertFeedOut, AlertOut
from ..services import intake as intake_service
from ..services.alerts import COMPARABLE, MIN_BASELINE_HOURS

router = APIRouter(tags=["alerts"])


@router.get("/clients/{clientId}/alerts", response_model=AlertFeedOut)
async def list_client_alerts(
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """This client's alerts, newest first.

    **Newest first**, unlike `GET /clients/{id}/history`. That one is a trend
    and a trend reads left to right from its earliest point; this is a LOG an
    operator checks, and the thing they are looking for is the most recent
    entry. `prompt-runs` orders itself the same way for the same reason.

    **Reads only.** Alerts are generated once at the end of a scan (see
    `services/alerts.py`), never recomputed here — an alert that re-derived on
    every request could appear and disappear between two page loads of the same
    data, and an acknowledgement would have nothing stable to attach to.

    `scansTotal` and `scansCompared` are returned because an empty feed is
    ambiguous on its own: most clients have one scan and can never have an
    alert. Reporting only "0 alerts" would read as an all-clear the data cannot
    support.

    Scoped to the caller's agency by `get_client`, which 404s rather than 403s
    on another agency's id.
    """
    client = await intake_service.get_client(
        db, agency_id=principal.agency_id, client_id=client_id
    )

    # Both scans in one statement rather than two lookups per alert row.
    triggered = aliased(Scan)
    baseline = aliased(Scan)
    #
    # `COALESCE(finished_at, created_at)` — the SAME expression
    # `client_history.build_history` uses for `HistoryScanOut.scanned_at`, and
    # it has to be identical rather than merely similar.
    #
    # The trend charts annotate a point by matching an alert's `scannedAt`
    # against the point's `stamp`, and that stamp IS the history's
    # `scanned_at`. The first draft selected `created_at` here, the two
    # differed by the scan's duration, no key ever matched, and **every marker
    # silently failed to draw** — with no error, and invisible to unit tests
    # whose fixtures had matching stamps by construction. Found by counting
    # markers in a live browser.
    #
    stamp = func.coalesce(triggered.finished_at, triggered.created_at)
    baseline_stamp = func.coalesce(baseline.finished_at, baseline.created_at)
    rows = (
        await db.execute(
            select(Alert, stamp, baseline_stamp)
            .join(triggered, triggered.id == Alert.scan_id)
            .join(baseline, baseline.id == Alert.baseline_scan_id)
            .where(Alert.client_id == client.id)
            .order_by(Alert.id.desc())
        )
    ).all()

    alerts = [
        AlertOut(
            id=a.id,
            kind=a.kind.value,
            detail=a.detail,
            scan_id=a.scan_id,
            baseline_scan_id=a.baseline_scan_id,
            scanned_at=scanned,
            baseline_scanned_at=base_scanned,
            engine=a.engine,
            created_at=a.created_at,
            acknowledged_at=a.acknowledged_at,
        )
        for a, scanned, base_scanned in rows
    ]

    scans_total = (
        await db.execute(
            select(func.count())
            .select_from(Scan)
            .where(Scan.client_id == client.id, Scan.status.in_(COMPARABLE))
        )
    ).scalar_one()

    # How many of this client's scans could have produced an alert at all. The
    # figure that separates "nothing changed" from "nothing was comparable".
    compared = (
        await db.execute(
            select(func.count(func.distinct(Alert.scan_id))).where(
                Alert.client_id == client.id
            )
        )
    ).scalar_one()

    return AlertFeedOut(
        client_id=client.id,
        alerts=alerts,
        unacknowledged=sum(1 for a in alerts if a.acknowledged_at is None),
        scans_total=scans_total,
        scans_compared=compared,
        min_baseline_hours=MIN_BASELINE_HOURS,
    )


@router.post("/alerts/{alertId}/acknowledge", response_model=AcknowledgeAlertOut)
async def acknowledge_alert(
    principal: PrincipalDep,
    db: DbDep,
    alert_id: str = Path(alias="alertId"),
) -> Any:
    """Mark an alert as seen.

    **Idempotent**: acknowledging an already-acknowledged alert returns the
    original timestamp rather than moving it. An operator double-clicking must
    not rewrite when they first saw something.

    There is deliberately no un-acknowledge. Dismissing is a record that a
    person looked, and a log an operator can quietly un-read is not a log.

    Scoped by `agency_id` on the row itself — the denormalised column exists so
    this is one predicate rather than a join through the client.
    """
    alert = (
        await db.execute(
            select(Alert).where(
                Alert.id == alert_id, Alert.agency_id == principal.agency_id
            )
        )
    ).scalars().first()
    if alert is None:
        # 404 rather than 403 on another agency's id — the rule every
        # agency-scoped route here follows, so an id cannot be probed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such alert.")

    if alert.acknowledged_at is None:
        alert.acknowledged_at = datetime.now(UTC)
        await db.commit()

    return AcknowledgeAlertOut(id=alert.id, acknowledged_at=alert.acknowledged_at)


__all__ = ["router", "ScanStatus"]

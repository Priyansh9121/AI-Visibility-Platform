"""Competitor detection endpoints — Epic 3.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import DbDep, PrincipalDep, RedisDep, SettingsDep
from ..errors import NotFound
from ..models import CompetitorSet, Scan
from ..schemas.competitor import (
    CompetitorOut,
    CompetitorSetOut,
    ReplaceCompetitorsRequest,
)
from ..services import competitors as detection
from ..services import rate_limit, scan_allowance
from ..services.intake import get_client

router = APIRouter(prefix="/clients", tags=["competitors"])


async def _load_set(db: Any, scan_id: str) -> CompetitorSet | None:
    return (
        await db.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan_id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()


async def _latest_set_for_client(db: Any, client_id: str) -> CompetitorSet:
    """Most recent competitor set for a client, or 404.

    The 404 is `GET` and `PUT /clients/{clientId}/competitors` on a client that has no set on
    any scan. Its sentence is true for every caller, so it tells nobody to press the detect
    route: that is for paying agencies (launch checklist 3.61). It says what does make a set:
    a scan, whose competitor phase runs detection for a client with none
    (`competitors.ensure_set_for_scan`). It does not say the scan will be allowed, or will
    succeed: starting one is the scan allowance's question, and a phase can fail.
    """
    competitor_set = (
        await db.execute(
            select(CompetitorSet)
            .join(Scan, Scan.id == CompetitorSet.scan_id)
            .where(Scan.client_id == client_id)
            .order_by(CompetitorSet.id.desc())
            .limit(1)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()
    if competitor_set is None:
        raise NotFound(
            detail=(
                "No competitor set for this client yet. "
                "A scan runs detection for a client that has none."
            )
        )
    return competitor_set


def _sorted(competitor_set: CompetitorSet) -> CompetitorSetOut:
    """Serialise the ACTIVE competitors, in rank order.

    The relationship has no ordering guarantee, and a comparison table whose
    rows reshuffle between requests reads as a bug to the client looking at it.

    Built from `active_competitors` rather than the raw relationship so
    suppressed rows never reach the wire: a rival an operator struck is a
    tombstone kept for re-detection's benefit, and returning it would undo the
    correction in the one place the operator would actually look.
    """
    out = CompetitorSetOut.model_validate(competitor_set)
    out.competitors = [
        CompetitorOut.model_validate(c) for c in competitor_set.active_competitors
    ]
    # Set explicitly: `detected_competitors` is a property with a different
    # name, so model_validate cannot find it, and a silent 0 would understate
    # the confidence's scope on every response.
    out.confidence_covers = len(competitor_set.detected_competitors)
    return out


@router.post(
    "/{clientId}/competitors/detect",
    response_model=CompetitorSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def detect_competitors(
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    redis: RedisDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """Run SERP + AI co-citation discovery and rank the top competitors.

    Runs synchronously. A run makes several paid SerpApi searches and several
    model calls, so it is explicitly operator-triggered rather than implicit in
    intake — an accidental re-detection costs money.

    **For paying agencies since 2026-10-04** (launch checklist 3.61, the founder's
    decision). The 24-hour limit below caps a client, not an agency, and every run is
    paid searches, so the route is for an agency whose subscription is `active` or
    `trialing`, and for the platform operator (a listed address with a linked Google
    identity, the scan allowance's own exemption). Anyone else is refused with
    `402 subscription-required` and one plain sentence that names no status and no code.
    Where billing is not configured the route stays open, as the scan allowance does. It
    is checked after the client is found, so another agency's client is still a `404`
    whatever the caller's plan, and before the day below is taken, so a refused press
    spends none of the client's day: no search is made and no scan is opened. A scan's own
    detection is not this route and is unaffected: a client with no competitor set gets
    one when it is first scanned, on any plan.

    **Once per client per 24 hours** (2026-10-03, launch checklist 3.53), for those who
    are allowed. The day is taken when a run starts, so a run that then fails still
    counts: its searches may already be spent. Checked after the client is found, so
    another agency's client is still a `404` and cannot spend this client's day, and
    before anything is done. Another client of the same agency has its own day. A scan's
    own detection, for a client with no competitor set yet, is never refused by this
    limit and does not use the day up; neither does replacing the set by hand with `PUT`.

    Competitors an operator marked as overrides are preserved across runs.

    **Errors:** `401 authentication-required`; `402 subscription-required` ("Running
    competitor detection on its own needs an active subscription. A scan still finds a
    client's competitors the first time it runs."); `404 not-found`; `429 rate-limited`
    (with `Retry-After`, the whole seconds left in the 24 hours): "Competitor detection
    already ran for this client in the last 24 hours. It can run again in about N hours."
    (the last hour reads "in under an hour"); `503 service-unavailable` (Redis cannot say
    whether the day is taken, so nothing runs).
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    # The plan first, then the day, both after the client is found and before any work: a
    # press the plan refuses must not spend the client's day, and an agency's client is
    # still a 404 for anyone else. See `scan_allowance.assert_may_run_detection`.
    scan_allowance.assert_may_run_detection(
        settings, agency=principal.agency, user=principal.user, client_id=client.id
    )
    # The first thing a run does is open a scan, and the searches that follow are the
    # money. See `rate_limit.claim_detection`.
    await rate_limit.claim_detection(
        redis, settings, client_id=client.id, agency_id=principal.agency_id
    )
    scan = await detection.get_or_create_scan(db, client, user_id=principal.user_id)
    outcome = await detection.detect_for_client(client, settings=settings)
    competitor_set = await detection.persist_detection(db, scan, outcome)

    await db.commit()
    refreshed = await _load_set(db, scan.id)
    return _sorted(refreshed or competitor_set)


@router.get("/{clientId}/competitors", response_model=CompetitorSetOut)
async def get_competitors(
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """The client's most recent competitor set."""
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    return _sorted(await _latest_set_for_client(db, client.id))


@router.put("/{clientId}/competitors", response_model=CompetitorSetOut)
async def replace_competitors(
    payload: ReplaceCompetitorsRequest,
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """Replace the competitor set by hand.

    This matters more than a normal CRUD override. `Client.industry` confidence
    is uncalibrated (build-log Epic 2.6/2.8, Finding 2 — open), so a wrong
    classification can seed a poor competitor set, and an operator who knows the
    market is the only reliable corrective available today. Everything supplied
    here is marked `isManualOverride` and survives later re-detection.

    Anything currently in the set and not sent back has been STRUCK, and is
    recorded as such — Epic 3 deleted it, which recorded nothing, so the next
    detection run reinstated it. See `services.competitors.apply_override`.

    `detectionConfidence` is cleared: it measures agreement between two
    automated signals, and neither one produced this set. Leaving the previous
    value would attach a corroboration claim to rows that were never corroborated.
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    competitor_set = await _latest_set_for_client(db, client.id)

    await detection.apply_override(
        db,
        competitor_set,
        [(item.name, item.domain) for item in payload.competitors],
    )

    await db.commit()
    refreshed = await _load_set(db, competitor_set.scan_id)
    return _sorted(refreshed or competitor_set)

"""Competitor detection endpoints — Epic 3.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import DbDep, PrincipalDep, RedisDep, SettingsDep
from ..errors import Conflict, NotFound, ValidationProblem
from ..models import Client, CompetitorSet, ScanStatus
from ..schemas.competitor import (
    CompetitorOut,
    CompetitorSetOut,
    ReplaceCompetitorsRequest,
)
from ..services import competitors as detection
from ..services import rate_limit, scan_allowance
from ..services.intake import checked_domain, encode_hostname, get_client

router = APIRouter(prefix="/clients", tags=["competitors"])


async def _load_set(db: Any, set_id: str) -> CompetitorSet | None:
    return (
        await db.execute(
            select(CompetitorSet)
            .where(CompetitorSet.id == set_id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()


def _no_set() -> NotFound:
    """The 404 for `GET` and `PUT /clients/{clientId}/competitors` on a client with no list yet.

    Its sentence is true for every caller, so it tells nobody to press the detect
    route: that is for paying agencies (launch checklist 3.61). It says what does make a set:
    a scan, whose competitor phase runs detection for a client with none
    (`competitors.ensure_set_for_scan`). It does not say the scan will be allowed, or will
    succeed: starting one is the scan allowance's question, and a phase can fail.
    """
    return NotFound(
        detail=(
            "No competitor set for this client yet. "
            "A scan runs detection for a client that has none."
        )
    )


async def _next_list_to_replace(
    db: Any, client: Client, *, naming_rivals: bool
) -> CompetitorSet:
    """The set `PUT` writes: always the client's NEXT LIST, never a scan's own set.

    A scan's set never changes once the scan has started, so a save does not edit the set of the
    client's latest scan, finished or not (launch checklist 3.78/3.79). It writes the list the
    next scan will adopt:

      * the client's next list, if it has one: edited in place, and locked until the commit, so
        a scan adopting it at the same moment either takes it first (and the save then starts a
        new list from what is now that scan's set) or waits for the save;
      * else a copy of the client's latest set (rows, hand edits and struck rivals), which the
        replacement is then applied to;
      * else, for a client with no set on any scan, an empty list that a non-empty replacement
        fills. It is recorded as every hand edit is: each row `manual`, the status `ok`,
        `detected_at` null (no detection ran).

    An empty replacement for a client with no set at all stays the 404 and writes nothing: a set
    with no rivals would be copied by every later scan, and detection would never run for the
    client, and there is nothing to clear.
    """
    next_list = await detection.next_list_for_client(db, client, lock=True)
    if next_list is not None:
        return next_list
    source = await detection.latest_set_for_client(db, client)
    if source is None and not naming_rivals:
        raise _no_set()
    return await detection.create_next_list(db, client, source=source)


def _refuse_the_clients_own_domain(client: Client, payload: ReplaceCompetitorsRequest) -> None:
    """`422` for a rival whose domain is the client's own (3.81), before anything is written.

    The same shape as a schema refusal, `errors[].field` being `competitors.<i>.domain`. The
    payload's domains are already in the stored form; the client's is compared as DNS carries it.
    """
    try:
        # In its checked form: a client stored before 3.67 as `www.acme.com` is `acme.com`.
        own = encode_hostname(checked_domain(client.domain) or client.domain)
    except ValueError:
        return  # a client stored before 3.67 whose own name is not one: nothing to compare with
    errors = [
        {
            "field": f"competitors.{index}.domain",
            "message": "That is this client's own domain.",
            "type": "invalid_domain",
        }
        for index, item in enumerate(payload.competitors)
        if item.domain and _same_host(item.domain, own)
    ]
    if errors:
        raise ValidationProblem(detail="One or more fields were invalid.", errors=errors)


def _same_host(domain: str, own: str) -> bool:
    try:
        return encode_hostname(domain) == own
    except ValueError:
        return False


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

    **Where the result goes (launch checklist 3.78/3.79).** Into the client's NEXT LIST: the list
    the next scan that starts will adopt, which is what this answers (`scanId` is null, because it
    belongs to no scan yet). A scan's own set never changes once the scan has started, so a run
    does not touch a finished scan's set and does not touch a running scan's either. With no next
    list yet, one is made as a copy of the client's latest set and detection merges into the
    copy, so rivals an operator named or struck stay named and struck; a client with no set at all
    starts from an empty list. The open scan this route opens (or reuses, as before) carries no
    set until it runs.

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
    await detection.get_or_create_scan(db, client, user_id=principal.user_id)
    outcome = await detection.detect_for_client(client, settings=settings)
    competitor_set = await detection.persist_detection_for_client(db, client, outcome)

    await db.commit()
    refreshed = await _load_set(db, competitor_set.id)
    return _sorted(refreshed or competitor_set)


@router.get("/{clientId}/competitors", response_model=CompetitorSetOut)
async def get_competitors(
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """The client's current competitor list: its next list, else its latest scan's set.

    The next list is what a save or the button's detection last wrote and what the next scan
    will use (launch checklist 3.78/3.79), so it is ahead of the latest scan's own set, which
    never changes once the scan has started. It belongs to no scan: its `scanId` is null.
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    current = await detection.current_list_for_client(db, client)
    if current is None:
        raise _no_set()
    return _sorted(current)


@router.put("/{clientId}/competitors", response_model=CompetitorSetOut)
async def replace_competitors(
    payload: ReplaceCompetitorsRequest,
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """Replace the client's competitor list by hand: the list its later scans use.

    Writes the client's NEXT LIST, never a scan's own set (launch checklist 3.78/3.79). The next
    scan that starts adopts it, and a scan that has started keeps the list it started with, so
    a save moves nothing a finished scan shows: not its score, its report, its client-history
    figure, or an explicit re-score. With no next list yet, one is made as a copy of the
    client's latest set and the replacement applied to the copy; for a client with no set on
    any scan, a non-empty list makes one. An empty list for a client
    with no set, or a client with no scan at all, is the `404`, and nothing is written.

    While the client's most recent scan is RUNNING the answer is `409` and nothing is written,
    on every path: a running scan may or may not have taken the client's list yet, and a save
    cannot say which.

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
    _refuse_the_clients_own_domain(client, payload)
    latest_scan = await detection.latest_scan_for_client(db, client.id, client.agency_id)
    if latest_scan is None:
        # A client that has never been scanned has nothing a list could be for yet.
        raise _no_set()
    if latest_scan.status is ScanStatus.RUNNING:
        # A save does not reach a scan's own set whatever its status, so this is not about
        # scoring: it is that a running scan may or may not have taken the client's list yet,
        # and the person cannot be told which. Queued and finished scans are no reason to refuse.
        raise Conflict(
            detail="A scan of this client is running. Name the rivals after it finishes."
        )
    competitor_set = await _next_list_to_replace(
        db, client, naming_rivals=bool(payload.competitors)
    )

    await detection.apply_override(
        db,
        competitor_set,
        [(item.name, item.domain) for item in payload.competitors],
    )

    await db.commit()
    refreshed = await _load_set(db, competitor_set.id)
    return _sorted(refreshed or competitor_set)

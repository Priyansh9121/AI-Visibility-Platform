"""Scan execution endpoints — Epic 4.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import DbDep, PrincipalDep, RedisDep, ScanExecutorDep, SettingsDep
from ..errors import NotFound
from ..models import EngineResult, Prompt, PromptSet, Scan, ScanStatus
from ..schemas.common import Page
from ..schemas.scan import (
    EngineResultOut,
    PromptSetOut,
    RunScanRequest,
    ScanDetailOut,
    ScanOut,
)
from ..services import scan_allowance, scan_start
from ..services.intake import get_client
from ..services.scan_executor import fail_never_started_scans

router = APIRouter(tags=["scans"])

DEFAULT_PAGE_SIZE = 25


async def _load_scan(db: Any, scan_id: str, agency_id: str) -> Scan:
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        # 404 rather than 403 for another agency's scan — confirming an id
        # exists is itself a cross-tenant leak.
        raise NotFound(detail="No scan with that identifier.")
    return scan


async def _detail(db: Any, scan: Scan) -> ScanDetailOut:
    prompt_set = (
        await db.execute(
            select(PromptSet)
            .where(PromptSet.scan_id == scan.id)
            .options(selectinload(PromptSet.prompts))
        )
    ).scalar_one_or_none()

    results = list(
        (
            await db.execute(
                select(EngineResult)
                .where(EngineResult.scan_id == scan.id)
                .options(
                    selectinload(EngineResult.brand_mentions),
                    selectinload(EngineResult.citations),
                )
                .order_by(EngineResult.id)
            )
        )
        .scalars()
        .all()
    )

    out = ScanDetailOut.model_validate(scan)
    if prompt_set is not None:
        payload = PromptSetOut.model_validate(prompt_set)
        payload.prompts.sort(key=lambda p: p.position)
        out.prompt_set = payload
    out.results = [EngineResultOut.model_validate(r) for r in results]
    return out


@router.post(
    "/clients/{clientId}/scans",
    response_model=ScanOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_scan(
    payload: RunScanRequest,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    executor: ScanExecutorDep,
    redis: RedisDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """Queue a scan. Returns immediately; the work happens out of band.

    **`202`, not `201`, and `ScanOut`, not `ScanDetailOut`** — Epic 9.5. This
    used to run the whole pipeline inline, which Epic 9.2 measured at ~303s, and
    Epic 9.3 found the real cost: the scan row sat in an uncommitted transaction
    for that entire time, invisible to every other request. There was nothing
    for a dashboard to poll because, as far as Postgres was concerned, the scan
    did not exist yet.

    The response model is `ScanOut` rather than `ScanDetailOut` with empty
    fields. At `202` there is no prompt set and there are no results — the
    prompt set is generated *by* the scan. Returning `ScanDetailOut` would
    describe a shape this endpoint never has, and leave a caller unable to tell
    "not generated yet" from "generated, and empty".

    Poll `GET /scans/{scanId}` for completion; it carries the prompt set and
    results once they exist.

    Still costs real money once it runs — 20-30 prompts across every engine plus
    a sentiment call per mention. `promptLimit` caps the set for verification.

    Reuses the scan Epic 3's competitor detection created, if one is open, so a
    detect-then-scan flow does not strand an empty scan.

    **A scan already RUNNING is returned as it is, and no second executor is
    started** — API key discipline audit, 2026-09-07. `executor.submit` used
    to be unconditional, so re-running while a scan was in flight handed the
    same row to a second executor, which re-paid for the chain up to a unique
    violation. The executor now refuses a scan it cannot claim (see
    `execute_scan`); this check simply declines to start a task that would be
    refused. The `202` reports the scan in flight, which is what was asked for.

    **`402 scan-limit-reached` since 2026-09-28** — `services/scan_allowance`.
    One free scan per agency for life, forty a month on the plan; the gate is
    on when billing is configured and off when it is not. The agency row is
    locked before the count and the scan is inserted and committed under that
    lock, so two requests at the cap cannot both take the last slot. A scan
    already RUNNING is returned as before, and the open QUEUED scan a run
    adopts is not counted against starting it.

    **The free scan needs a Google sign-in since 2026-09-29.** A requesting
    user with no linked Google identity, in a free agency, gets the 402 with
    `SCAN_LIMIT_VERIFY_REQUIRED` — refused under the same lock, before the
    count is consulted, and with no scan row created. Paid agencies and
    exempt operators are unaffected.

    **`503 platform-cap-reached` since D2 (2026-10-10).** AVP as a whole starts at most
    `MAX_SCANS_PER_DAY_PLATFORM` scans per UTC day, and at most
    `MAX_SIGNUP_FREE_SCANS_PER_DAY` of them by agencies that are not paid up
    (`services/spend_caps`). Claimed after the allowance, by the one request that submits the scan,
    and before any provider is called; a refused start leaves no row and spends no unit of either
    cap. A request that finds the scan already submitted by another returns it and spends
    nothing. The problem carries `cap` (`scans` or `free_scans`) and `Retry-After`, the seconds
    until the next UTC midnight, and not the number. Scans already running finish. A Redis
    failure is a plain `503 service-unavailable`.
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    # One start path for this route and the monthly rescan (`services/scan_start.py`).
    return await scan_start.start_scan(
        db,
        settings=settings,
        executor=executor,
        client=client,
        user=principal.user,
        engines=payload.engines,
        prompt_limit=payload.prompt_limit,
        redis=redis,
    )


@router.get("/scans/{scanId}", response_model=ScanDetailOut)
async def get_scan(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """A scan with its prompt set and every engine result."""
    scan = await _load_scan(db, scan_id, principal.agency_id)
    # Lazy single-row reap: the UI polls this endpoint, so a scan that was
    # submitted and never claimed is failed here rather than left QUEUED until
    # somebody starts another scan. Scoped to this (already tenant-checked) id.
    if (
        scan.status is ScanStatus.QUEUED
        and scan.submitted_at is not None
        and scan.submitted_at < datetime.now(UTC) - scan_allowance.IN_FLIGHT_WINDOW
        and await fail_never_started_scans(db, now=datetime.now(UTC), scan_id=scan.id)
    ):
        await db.commit()
        await db.refresh(scan)
    return await _detail(db, scan)


@router.get("/scans/{scanId}/results", response_model=Page[EngineResultOut])
async def list_results(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> Any:
    """Cursor-paginated engine results.

    Separate from the scan detail because a full scan is 20-30 prompts times
    every engine — 60+ rows with nested mentions and citations, which is a lot
    to return in one body once a third engine is added.
    """
    await _load_scan(db, scan_id, principal.agency_id)

    stmt = (
        select(EngineResult)
        .where(EngineResult.scan_id == scan_id)
        .options(
            selectinload(EngineResult.brand_mentions),
            selectinload(EngineResult.citations),
        )
        .order_by(EngineResult.id)
        .limit(limit + 1)
    )
    if cursor:
        stmt = stmt.where(EngineResult.id > cursor)

    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[EngineResultOut](
        data=[EngineResultOut.model_validate(r) for r in rows],
        next_cursor=rows[-1].id if has_more and rows else None,
    )


@router.get("/clients/{clientId}/scans", response_model=Page[ScanOut])
async def list_scans(
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> Any:
    """A client's scans, newest first."""
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)

    # The caller's agency is named as well as the client's id, so a scan row that names this
    # client under another agency's id is never listed.
    stmt = (
        select(Scan)
        .where(Scan.client_id == client.id, Scan.agency_id == principal.agency_id)
        .order_by(Scan.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        stmt = stmt.where(Scan.id < cursor)

    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[ScanOut](
        data=[ScanOut.model_validate(r) for r in rows],
        next_cursor=rows[-1].id if has_more and rows else None,
    )


@router.get("/scans/{scanId}/prompts", response_model=PromptSetOut)
async def get_prompt_set(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """The scan's generated prompt set."""
    await _load_scan(db, scan_id, principal.agency_id)
    prompt_set = (
        await db.execute(
            select(PromptSet)
            .where(PromptSet.scan_id == scan_id)
            .options(selectinload(PromptSet.prompts))
        )
    ).scalar_one_or_none()
    if prompt_set is None:
        raise NotFound(detail="This scan has no prompt set yet.")
    out = PromptSetOut.model_validate(prompt_set)
    out.prompts.sort(key=lambda p: p.position)
    return out


__all__ = ["Prompt", "router"]

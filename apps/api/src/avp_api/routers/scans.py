"""Scan execution endpoints — Epic 4.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import DbDep, PrincipalDep, ScanExecutorDep, SettingsDep
from ..errors import NotFound, ValidationProblem
from ..models import EngineResult, Prompt, PromptSet, Scan
from ..schemas.common import Page
from ..schemas.scan import (
    EngineResultOut,
    PromptSetOut,
    RunScanRequest,
    ScanDetailOut,
    ScanOut,
)
from ..services import competitors as detection
from ..services.engines import DEFAULT_ENGINES, ENGINE_REGISTRY
from ..services.intake import get_client
from ..services.scan_executor import ScanJob, reap_stale_scans

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
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)

    # Checked BEFORE the reaper runs and before a scan row is created or
    # adopted: a request that cannot run should leave nothing behind. The
    # schema has already collapsed duplicates and validated each value against
    # the Engine enum; this is the second check, against the engines that have
    # an adapter and a key. `ask_all` subscripts ENGINE_REGISTRY bare, so an
    # enum-only engine such as `perplexity` used to fail INSIDE the executor —
    # after competitor detection had been paid for — and land the scan at
    # FAILED / EXECUTION_FAILED. Now it is a 422 and no scan exists.
    engines = tuple(payload.engines) if payload.engines else DEFAULT_ENGINES
    unsupported = [e.value for e in engines if e not in ENGINE_REGISTRY]
    if unsupported:
        raise ValidationProblem(
            detail=(
                f"No adapter for engine(s): {', '.join(unsupported)}. "
                f"Supported: {', '.join(e.value for e in ENGINE_REGISTRY)}."
            )
        )

    # Reap before reusing. A scan stranded at RUNNING by a lost executor would
    # otherwise be picked up by `get_or_create_scan` and re-run, which dies on
    # `prompt_sets`' unique constraint as a 500. This is the path where a
    # stranded row does real harm, so it is the path that clears it.
    if await reap_stale_scans(db):
        await db.commit()

    scan = await detection.get_or_create_scan(db, client, user_id=principal.user_id)

    # Commit BEFORE handing off. The executor loads the scan on its own session
    # and would not find it otherwise.
    await db.commit()
    await db.refresh(scan)

    await executor.submit(
        ScanJob(
            scan_id=scan.id,
            client_id=client.id,
            engines=engines,
            prompt_limit=payload.prompt_limit,
        ),
        settings=settings,
    )
    # Deliberately returns the row as committed — QUEUED — and does not re-read
    # after handing off. The `202` reports what was accepted, not how far it has
    # since got; an executor that happens to run synchronously must not change
    # this endpoint's contract. Completion is observed through
    # `GET /scans/{scanId}`, which is the one place that answers it.
    return scan


@router.get("/scans/{scanId}", response_model=ScanDetailOut)
async def get_scan(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """A scan with its prompt set and every engine result."""
    scan = await _load_scan(db, scan_id, principal.agency_id)
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

    stmt = (
        select(Scan)
        .where(Scan.client_id == client.id)
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

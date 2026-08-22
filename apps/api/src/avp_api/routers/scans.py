"""Scan execution endpoints — Epic 4.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import DbDep, PrincipalDep, SettingsDep
from ..errors import NotFound
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
from ..services import scan_runner
from ..services.engines import DEFAULT_ENGINES
from ..services.intake import get_client

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
    response_model=ScanDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_scan(
    payload: RunScanRequest,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """Generate a prompt set and run it against every engine.

    Runs **synchronously** and costs real money — a full scan is 20-30 prompts
    across every engine, plus a sentiment call per mention. The grounded engine
    can take 100s per prompt, so a full run takes minutes. `promptLimit` caps
    the set for verification runs.

    Reuses the scan Epic 3's competitor detection created, if one is open, so a
    detect-then-scan flow does not strand an empty scan.
    """
    client = await get_client(db, agency_id=principal.agency_id, client_id=client_id)
    scan = await detection.get_or_create_scan(db, client, user_id=principal.user_id)

    engines = tuple(payload.engines) if payload.engines else DEFAULT_ENGINES
    await scan_runner.run_scan(
        db, scan, client,
        settings=settings, engines=engines, prompt_limit=payload.prompt_limit,
    )
    await db.commit()
    await db.refresh(scan)
    return await _detail(db, scan)


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

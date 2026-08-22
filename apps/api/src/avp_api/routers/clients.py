"""Client intake and read endpoints — Epic 2.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep, SettingsDep
from ..models import Client
from ..schemas.client import (
    ClientDetailOut,
    ClientOut,
    CrawlSummaryOut,
    CreateClientRequest,
)
from ..schemas.common import Page
from ..services import intake as intake_service
from ..services.crawl import CrawlResult

router = APIRouter(prefix="/clients", tags=["clients"])

DEFAULT_PAGE_SIZE = 25


def _crawl_summary(crawl: CrawlResult | None) -> CrawlSummaryOut | None:
    """Project a crawl to the facts that may leave the process.

    This function is the last gate before crawl output reaches a response body.
    It names every field explicitly rather than spreading the dataclass, so
    adding a field to CrawlSignals cannot silently start returning it — and
    `text_extract` has no path here at all (ip-safety.md #7).
    """
    if crawl is None:
        return None
    s = crawl.signals
    return CrawlSummaryOut(
        pages_fetched=s.pages_fetched,
        urls_fetched=s.urls_fetched,
        schema_types=s.schema_types,
        word_count=s.word_count,
        h1_count=s.h1_count,
        has_title=s.has_title,
        has_meta_description=s.has_meta_description,
        detected_language=s.detected_language,
    )


@router.post("", response_model=ClientDetailOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: CreateClientRequest,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
) -> Any:
    """Submit a URL: creates the client, crawls it, and classifies its industry.

    Runs synchronously. §7's acceptance criterion is a classified industry
    within 30 seconds of submitting a URL, and the measured path is a few
    seconds, so one round trip is both sufficient and simpler than polling.

    Set `classify: false` to create the record without spending an LLM call.
    """
    client = await intake_service.create_client_from_url(
        db, agency_id=principal.agency_id, raw_url=payload.url, name=payload.name
    )

    crawl: CrawlResult | None = None
    if payload.classify:
        client, crawl = await intake_service.run_classification(
            db, client, settings=settings
        )

    await db.commit()
    await db.refresh(client)

    detail = ClientDetailOut.model_validate(client)
    detail.crawl = _crawl_summary(crawl)
    return detail


@router.get("", response_model=Page[ClientOut])
async def list_clients(
    principal: PrincipalDep,
    db: DbDep,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> Any:
    """Cursor-paginated client list, newest first.

    IDs are ULIDs, so `id < cursor ORDER BY id DESC` is a stable, index-backed
    window that does not skip or repeat rows when something is inserted
    mid-scroll — which for an active prospecting list is the common case.
    """
    stmt = (
        select(Client)
        .where(Client.agency_id == principal.agency_id, Client.deleted_at.is_(None))
        .order_by(Client.id.desc())
        # One extra row tells us whether another page exists, without a
        # second COUNT query.
        .limit(limit + 1)
    )
    if cursor:
        stmt = stmt.where(Client.id < cursor)

    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    return Page[ClientOut](
        data=[ClientOut.model_validate(r) for r in rows],
        next_cursor=rows[-1].id if has_more and rows else None,
    )


@router.get("/{clientId}", response_model=ClientOut)
async def get_client(
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    client = await intake_service.get_client(
        db, agency_id=principal.agency_id, client_id=client_id
    )
    return ClientOut.model_validate(client)


@router.post("/{clientId}/reclassify", response_model=ClientDetailOut)
async def reclassify_client(
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """Re-run crawl and classification for an existing client.

    Needed because a site changes, and because an AMBIGUOUS result is often
    worth retrying after the operator has looked at the site themselves.
    """
    client = await intake_service.get_client(
        db, agency_id=principal.agency_id, client_id=client_id
    )
    client, crawl = await intake_service.run_classification(db, client, settings=settings)
    await db.commit()
    await db.refresh(client)

    detail = ClientDetailOut.model_validate(client)
    detail.crawl = _crawl_summary(crawl)
    return detail

"""Client intake and read endpoints — Epic 2.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep, SettingsDep
from ..models import Client
from ..schemas.answer_gaps import AnswerGapsOut
from ..schemas.client import (
    ClientDetailOut,
    ClientOut,
    CrawlSummaryOut,
    CreateClientRequest,
)
from ..schemas.client_history import ClientHistoryOut
from ..schemas.common import Page
from ..services import answer_gaps as answer_gaps_service
from ..services import client_history as history_service
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


@router.get("/{clientId}/history", response_model=ClientHistoryOut)
async def get_client_history(
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
) -> Any:
    """Every scan of this client that produced a reading, oldest first.

    **Reads. Collects nothing, writes nothing, computes no new figure.** It
    exists because the two trends a client's space shows are not both available
    as stored columns: cited-domain counts aggregate persisted citation rows,
    while the per-rival comparison is derived on read by design and has no
    column at all (see `services/client_history.py` for the measurements that
    ruled out calling the report endpoint N times instead).

    Scoped to the caller's agency by `get_client`, which 404s rather than 403s
    on another agency's id — the same rule every other client route follows, so
    the endpoint cannot be used to probe whether an id exists elsewhere.
    """
    client = await intake_service.get_client(
        db, agency_id=principal.agency_id, client_id=client_id
    )
    return await history_service.build_history(db, client)


@router.get("/{clientId}/answer-gaps", response_model=AnswerGapsOut | None)
async def get_client_answer_gaps(
    principal: PrincipalDep,
    db: DbDep,
    client_id: str = Path(alias="clientId"),
    scan_id: str | None = Query(None, alias="scanId"),
) -> Any:
    """Prompts where a rival was named and this client was not — Epic B.

    **Reads. Collects nothing, calls no engine and no model, writes nothing.**
    Every figure is an aggregation over `engine_result_brand_mentions` and
    `engine_result_citations` rows some earlier scan already persisted.

    `scanId` selects one scan; without it the newest griddable scan is used.
    The response carries `availableScanIds` so a picker needs no second call.

    Returns `null` — not 404 — when the client has never produced a scan
    carrying a grid. A client with no scans is a normal state on a screen that
    has an empty view for it, not a missing resource. A `scanId` belonging to
    another client returns `null` for the same reason `get_client` 404s rather
    than 403s: neither may reveal that an id exists in another agency.

    Scoped to the caller's agency by `get_client`.
    """
    client = await intake_service.get_client(
        db, agency_id=principal.agency_id, client_id=client_id
    )
    return await answer_gaps_service.build_answer_gaps(db, client, scan_id=scan_id)


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

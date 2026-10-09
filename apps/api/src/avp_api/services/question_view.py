"""What the Prompts tab and the report say about the client's questions (F6, section 3).

Reads only. The rule for which set is the client's lives in `question_sets`; this module describes
that set (when it was written, since when it has been reused, whether the rivals have moved on)
and says how a scan's own set relates to it. Question text never leaves the database here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Client, PromptSet, Scan
from ..schemas.questions import ClientQuestionsOut, CurrentQuestionSetOut
from ..schemas.report import QuestionSetOut
from . import competitors as competitors_service
from . import question_sets


def _moment(
    finished_at: datetime | None, started_at: datetime | None, created_at: datetime | None
) -> datetime | None:
    """The moment a scan is known by: when it finished, else started, else was asked for."""
    return finished_at or started_at or created_at


def scan_date(scan: Scan | None) -> datetime | None:
    """The day a scan is known by: when it finished, else started, else was asked for."""
    if scan is None:
        return None
    return _moment(scan.finished_at, scan.started_at, scan.created_at)


def utc_day(at: datetime) -> date:
    """The day (UTC) a moment falls on; a moment without a zone is taken to be UTC already."""
    return (at.astimezone(UTC) if at.tzinfo is not None else at).date()


def _same_rivals(written: list[str], current: list[str]) -> bool:
    """The same names, whatever their order or case."""
    return sorted(n.strip().casefold() for n in written) == sorted(
        n.strip().casefold() for n in current
    )


async def _current_rival_names(session: AsyncSession, client: Client) -> list[str]:
    current = await competitors_service.current_list_for_client(session, client)
    return [row.name for row in current.active_competitors] if current is not None else []


async def _first_reuse_date(
    session: AsyncSession, client: Client, source_scan_id: str
) -> datetime | None:
    first = (
        await session.execute(
            select(Scan)
            .join(PromptSet, PromptSet.scan_id == Scan.id)
            .where(
                Scan.client_id == client.id,
                Scan.agency_id == client.agency_id,
                PromptSet.generation_params["reusedFromScanId"].as_string() == source_scan_id,
            )
            .order_by(Scan.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return scan_date(first)


async def client_questions(session: AsyncSession, client: Client) -> ClientQuestionsOut:
    """The flag and the set the client keeps, for `GET` and for the `PUT`'s answer."""
    found = await question_sets.clients_set(
        session, client_id=client.id, agency_id=client.agency_id, exclude_scan_id=""
    )
    if found is None:
        return ClientQuestionsOut(
            new_questions_next_scan=client.new_questions_next_scan, current_set=None
        )
    prompt_set, source_scan_id = found
    written = (prompt_set.generation_params or {}).get("writtenWithRivals")
    names = [n for n in written if isinstance(n, str)] if isinstance(written, list) else None
    changed = names is not None and not _same_rivals(
        names, await _current_rival_names(session, client)
    )
    return ClientQuestionsOut(
        new_questions_next_scan=client.new_questions_next_scan,
        current_set=CurrentQuestionSetOut(
            source_scan_id=source_scan_id,
            written_at=scan_date(await session.get(Scan, source_scan_id)),
            question_count=len(prompt_set.prompts),
            reused_since=await _first_reuse_date(session, client, source_scan_id),
            written_with_rivals=names,
            rivals_changed=changed,
        ),
    )


async def report_question_set(session: AsyncSession, scan: Scan) -> QuestionSetOut | None:
    """How the scan's questions relate to the client's earlier ones; None before fingerprints.

    A copy names its source by an id stored in its prompt set, and the shared report and PDF print
    the source's day to anyone holding the link. So the id is a claim, asked of the database: its
    day is given only when that scan is this client's own, in this agency. Any other id (a wrong
    write, an edited row) gives no day, and the report says "an earlier scan".
    """
    params = (
        await session.execute(
            select(PromptSet.generation_params).where(PromptSet.scan_id == scan.id)
        )
    ).scalar_one_or_none()
    if not isinstance(params, dict) or not params.get("fingerprint"):
        return None
    source = params.get("reusedFromScanId")
    if not isinstance(source, str):
        return QuestionSetOut(reused=False)
    known = (
        await session.execute(
            select(Scan.finished_at, Scan.started_at, Scan.created_at).where(
                Scan.id == source,
                Scan.client_id == scan.client_id,
                Scan.agency_id == scan.agency_id,
            )
        )
    ).first()
    at = _moment(known.finished_at, known.started_at, known.created_at) if known else None
    return QuestionSetOut(reused=True, source_scan_date=utc_day(at) if at is not None else None)

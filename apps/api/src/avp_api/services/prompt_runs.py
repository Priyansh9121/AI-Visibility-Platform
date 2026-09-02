"""Ad-hoc prompt runs — Epic 9.24.

One question an operator typed, asked of every engine, facts persisted.

WHAT IT REUSES, AND WHY THAT IS THE WHOLE DESIGN
------------------------------------------------
Nothing here talks to an engine or reads an answer. It calls
`engines.ask_all` and `extraction.extract_facts` — the same two functions
`scan_runner` calls, in the same order, with the same subject and the same
competitor list. That is deliberate: if this had its own extraction pass, "does
this prompt name my client?" could answer differently here than in the scan
whose score the operator is trying to explain, and the feature would be worse
than useless. A run is a scan's inner loop, run once, without the scoring.

It writes no `Scan` row and produces no score. A run is not a measurement of the
client and must never reach a trend, a dashboard figure, or the scan history,
where it would be read as one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import ids
from ..config import Settings, get_settings
from ..models import Client, CompetitorSet, Scan
from ..models.engine_result import Engine, EngineResultStatus
from ..models.prompt_run import (
    PromptRun,
    PromptRunBrand,
    PromptRunCitation,
    PromptRunResult,
    PromptRunStatus,
)
from . import engines as engine_service
from . import extraction as extraction_service

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# THE ABUSE THROTTLE — sized here, against what a scan already costs
# ---------------------------------------------------------------------------
#
# `PROMPT_CONCURRENCY` carries a note saying its value must be argued against
# measured cost rather than inherited from a default. The same discipline
# applies to this ceiling, so here is the argument.
#
# THE UNIT. One run is 1 prompt x 3 engines = **3 paid engine calls**. One scan
# is 24 prompts x 3 engines = **72**. So 24 runs cost exactly one scan.
#
# THE NUMBER: 30 runs per client per hour = 90 engine calls = **1.25 scans**.
#
# WHY THAT IS THE RIGHT CEILING. The worst an unattended loop on one client's
# Prompts screen can spend in an hour is a little over ONE SCAN — and a scan is
# spend this product already absorbs routinely, from a single click on the
# dashboard's Re-run button. The throttle's job is to keep an ad-hoc run from
# becoming a cheaper way to spend more than the expensive thing it sits beside,
# and 1.25x clears that with no room for argument.
#
# WHY NOT LOWER. An operator working out why a client scores badly genuinely
# iterates on phrasing — "best dentist in Leeds" against "top rated dentist
# Leeds" against "who should I see for a crown" — and five to ten runs in a few
# minutes is ordinary use, not abuse. A ceiling near that rate would spend its
# time blocking the exact work the feature exists for. 30/hour leaves that
# entirely unobstructed while still bounding the hour.
#
# WHY NOT HIGHER. Above ~1.25 scans an hour the ad-hoc path stops being a
# rounding error against scan spend and starts being its own line item, which is
# a pricing decision this epic has no business making on its own.
#
# WHY PER CLIENT RATHER THAN PER AGENCY. The endpoint is client-scoped and the
# abuse vector is a loop on one client's screen. A per-agency ceiling would let
# one busy client exhaust the allowance of every other client in the account —
# turning a throttle into an outage for people who did nothing.
#
# WHY COUNTED FROM THE TABLE RATHER THAN A REDIS COUNTER. The rows are being
# written anyway, so the count is free and it is exact. A Redis counter would
# reset on eviction or restart, which is precisely when a runaway loop is most
# likely to still be running.
RUNS_PER_CLIENT_PER_HOUR = 30
THROTTLE_WINDOW = timedelta(hours=1)

# A prompt is a question, not a document.
#
# Engines charge by token, so the cap is what stops one run from costing many
# runs' worth: pasting a 20,000-character brief into the box would turn a
# 3-call run into a 3-call run with a very large bill attached, and the
# throttle above counts RUNS, not tokens. 500 characters is roughly 125 tokens
# — longer than any real buyer question and short enough that 30 of them an
# hour stays the cheap thing this was sized as.
MAX_PROMPT_CHARS = 500


class PromptRunError(Exception):
    """A run that will not be attempted, with a machine code for the API layer."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def normalise_prompt(raw: str) -> str:
    """Collapse whitespace and trim. Raises if nothing usable is left.

    Collapsing first means "a  b" and "a b" are the same prompt, which matters
    because the digest comparison and the throttle both count runs, and two
    runs differing only in spacing are one question asked twice.
    """
    text = " ".join(raw.split())
    if not text:
        raise PromptRunError("PROMPT_EMPTY", "A prompt cannot be blank.")
    if len(text) > MAX_PROMPT_CHARS:
        raise PromptRunError(
            "PROMPT_TOO_LONG",
            f"A prompt is limited to {MAX_PROMPT_CHARS} characters; this one is {len(text)}.",
        )
    return text


async def runs_in_window(session: AsyncSession, client_id: str) -> int:
    """How many runs this client has had inside the throttle window."""
    since = datetime.now(UTC) - THROTTLE_WINDOW
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(PromptRun)
                .where(PromptRun.client_id == client_id, PromptRun.created_at >= since)
            )
        ).scalar_one()
    )


async def check_throttle(session: AsyncSession, client_id: str) -> None:
    """Raise before spending anything, if this client is over its hourly ceiling."""
    used = await runs_in_window(session, client_id)
    if used >= RUNS_PER_CLIENT_PER_HOUR:
        raise PromptRunError(
            "PROMPT_RUN_RATE_LIMITED",
            f"This client has run {used} prompts in the last hour, which is the "
            f"limit of {RUNS_PER_CLIENT_PER_HOUR}. Each run asks every engine, so "
            f"the limit is there to keep ad-hoc testing from costing more than a "
            f"scan. Try again shortly.",
        )


async def subject_competitors(
    session: AsyncSession, client_id: str
) -> list[tuple[str, str | None]]:
    """The client's most recent competitor set, as extraction wants it.

    Read from the newest scan that HAS one. A run made before any scan has
    detected rivals simply has none to look for, which is honest: the run then
    reports whether the client was named and by whom it was not, rather than
    inventing a field of competitors to compare against.
    """
    stmt: Select[tuple[CompetitorSet]] = (
        select(CompetitorSet)
        .join(Scan, Scan.id == CompetitorSet.scan_id)
        .where(Scan.client_id == client_id)
        .options(selectinload(CompetitorSet.competitors))
        .order_by(CompetitorSet.id.desc())
        .limit(1)
    )
    found = (await session.execute(stmt)).scalars().first()
    if found is None:
        return []
    return [
        (c.name, c.domain)
        for c in found.competitors
        if getattr(c, "suppressed_at", None) is None
    ]


def verdict_for(results: list[PromptRunResult]) -> PromptRunStatus:
    """A run's overall status from its per-engine outcomes.

    Deliberately the same three-way shape `terminal_status_for` gives a scan:
    an engine that answered without naming the subject ANSWERED and is not a
    failure — that is the finding.
    """
    answered = sum(
        1
        for r in results
        if r.status in (EngineResultStatus.OK, EngineResultStatus.ANSWERED_NO_MENTION)
    )
    if answered == len(results):
        return PromptRunStatus.OK
    if answered == 0:
        return PromptRunStatus.FAILED
    return PromptRunStatus.PARTIAL


async def run_prompt(
    session: AsyncSession,
    *,
    client: Client,
    agency_id: str,
    prompt_text: str,
    asked_by_user_id: str | None,
    engines: tuple[Engine, ...] | None = None,
    settings: Settings | None = None,
) -> PromptRun:
    """Ask one prompt of every engine and persist the facts.

    Throttle is checked by the caller BEFORE this is entered — see the router.
    Doing it here would still be correct but would hide a spend decision inside
    a function whose name says it spends.
    """
    settings = settings or get_settings()
    text = normalise_prompt(prompt_text)

    subject_name = client.brand_name or client.name or client.domain
    competitors = await subject_competitors(session, client.id)

    answers = await engine_service.ask_all(
        text,
        engines=engines or engine_service.DEFAULT_ENGINES,
        settings=settings,
    )

    run = PromptRun(
        id=ids.new_id(ids.PROMPT_RUN),
        client_id=client.id,
        agency_id=agency_id,
        asked_by_user_id=asked_by_user_id,
        prompt_text=text,
        status=PromptRunStatus.FAILED,  # replaced below; never persisted as-is
        subject_name=subject_name,
        subject_domain=client.domain,
    )

    results: list[PromptRunResult] = []
    for answer in answers:
        facts = extraction_service.extract_facts(
            answer,
            subject_name=subject_name,
            subject_domain=client.domain,
            competitors=competitors,
        )
        # Tone, on the same terms the scan path spends it — Epic A.
        #
        # ONLY when the subject was named. `classify_sentiment`'s docstring is
        # the authority: tone toward a brand that does not appear is
        # meaningless, and spending a model call on it would be both wasteful
        # and misleading. A run where no engine named the client therefore
        # costs exactly what it cost before this epic.
        if facts.mentioned:
            sentiment, confidence = await extraction_service.classify_sentiment(
                answer, subject_name=subject_name, settings=settings
            )
            facts.sentiment = sentiment
            facts.sentiment_confidence = confidence

        result = PromptRunResult(
            id=ids.new_id(ids.PROMPT_RUN_RESULT),
            run_id=run.id,
            engine=answer.engine,
            engine_version=answer.engine_version,
            status=facts.status,
            error_code=answer.error_code,
            latency_ms=answer.latency_ms,
            mentioned=facts.mentioned,
            position=facts.position,
            prominence=facts.prominence,
            brands_mentioned=facts.brands_mentioned,
            response_digest=answer.digest(),
            sentiment=facts.sentiment,
            sentiment_confidence=facts.sentiment_confidence,
        )
        result.brands = [
            PromptRunBrand(
                id=ids.new_id(ids.BRAND_MENTION),
                result_id=result.id,
                name=hit.name,
                domain=hit.domain,
                is_subject=hit.is_subject,
                position=hit.position,
            )
            for hit in facts.brand_hits
        ]
        result.citations = [
            PromptRunCitation(
                id=ids.new_id(ids.CITATION),
                result_id=result.id,
                url=cite.url[:2048],
                domain=cite.domain,
                source_type=cite.source_type,
                position=cite.position,
                cites_subject=cite.cites_subject,
            )
            for cite in facts.citations
        ]
        results.append(result)

        # Never the answer text — `redacted()` is the only log-safe view.
        logger.info("prompt_run.engine", run_id=run.id, **answer.redacted())

    run.status = verdict_for(results)
    run.results = results
    session.add(run)
    await session.flush()
    return run


async def history(
    session: AsyncSession, *, client_id: str, limit: int = 50
) -> list[PromptRun]:
    """This client's runs, newest first.

    ULID primary keys are creation-ordered, so `id DESC` is `created_at DESC`
    and the `(client_id, id)` index serves it — the same trick every other
    listing in this codebase uses.
    """
    stmt = (
        select(PromptRun)
        .where(PromptRun.client_id == client_id)
        .order_by(PromptRun.id.desc())
        .limit(limit)
        .options(
            selectinload(PromptRun.results).selectinload(PromptRunResult.brands),
            selectinload(PromptRun.results).selectinload(PromptRunResult.citations),
        )
    )
    return list((await session.execute(stmt)).scalars().all())

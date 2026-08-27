"""Scan execution — §5.4 steps 3-4 wired together.

    generate prompts -> run every prompt x engine -> extract facts -> persist

Produces EngineResult rows and nothing else. Aggregation into a score is Epic 5;
this module deliberately computes no rates and no composite.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import ids
from ..config import Settings, get_settings
from ..models import (
    BrandMention,
    Citation,
    Client,
    Competitor,
    CompetitorSet,
    EngineResult,
    Prompt,
    PromptSet,
    Scan,
    ScanStatus,
)
from ..models.engine_result import Engine, EngineResultStatus
from . import engines as engine_service
from . import extraction as extraction_service
from . import prompts as prompt_service

logger = structlog.get_logger(__name__)

# Bounded because every unit of concurrency is a paid model call, and the
# grounded engine can take 100s+ per prompt. Unbounded fan-out over 24 prompts x
# 3 engines is 72 simultaneous requests, which buys rate limits, not speed.
#
# STILL 4, AND DELIBERATELY UNMEASURED AT THREE ENGINES — Epic 9.13.
#
# This value has been 4 since Epic 4 and unraised since Epic 9.1 named raising
# it as candidate fix 2. Adding `chatgpt` changed what one slot costs, not how
# many slots there are: each slot now awaits THREE concurrent engine calls
# instead of two, so in-flight requests go from 8 to 12.
#
# Wall clock is expected to move very little, because the engines inside a slot
# run concurrently (`ask_all` gathers them) and the slot costs
# max(engine latencies), not their sum. Epic 9.8 measured a 22.7s median and a
# 96.4s worst for the Claude pair; the one live `chatgpt` call measured while
# building this adapter returned in 3.8s. A call that fast almost never becomes
# the max, so it should hide inside the slot.
#
# **That is a prediction, not a measurement, and it is labelled as one.** Epic
# 9.1's whole lesson was that the loop's cost had to be measured per phase
# before anything was tuned, and the same discipline forbids asserting a
# three-engine timing here from a two-engine run plus one isolated call.
# Re-timing `verify_e2e.py` at three engines, and only then sizing
# PROMPT_CONCURRENCY, is its own follow-up.
#
# What DID change and is not a prediction: per-scan COST. A 24-prompt scan goes
# from 48 engine calls to 72, and sentiment (charged only where the subject is
# named) from at most 48 to at most 72.
PROMPT_CONCURRENCY = 4


async def load_competitors(session: AsyncSession, scan: Scan) -> list[tuple[str, str | None]]:
    """The scan's competitor set as (name, domain) pairs.

    Empty is a legitimate state: Epic 3 detection may not have run, or may have
    returned NO_SIGNAL. The scan still proceeds — subject mentions are
    measurable without rivals, and Epic 5 flags a missing comparison set rather
    than this module refusing to run.
    """
    competitor_set = (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan.id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()
    if competitor_set is None:
        return []
    return [
        (c.name, c.domain)
        for c in competitor_set.active_competitors
    ]


async def build_prompt_set(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    *,
    competitors: list[tuple[str, str | None]],
    settings: Settings,
    limit: int | None = None,
) -> PromptSet:
    """Generate and persist the scan's prompt set.

    Prompt.text is our OWN generated text, not third-party content — the
    deliberate exception to the facts-only rule, recorded in test_ip_safety.py.
    """
    generated, generated_by = await prompt_service.generate_prompts(
        brand_name=client.brand_name,
        domain=client.domain,
        industry=client.industry,
        niche=client.industry_niche,
        competitors=[name for name, _ in competitors],
        settings=settings,
    )
    if limit is not None:
        generated = generated[:limit]

    prompt_set = PromptSet(
        id=ids.new_id(ids.PROMPT_SET),
        scan_id=scan.id,
        generated_by=generated_by,
        # Provenance: enough to explain a set after the fact, without storing
        # anything that is not ours.
        generation_params={
            "industry": client.industry,
            "niche": client.industry_niche,
            "used_industry_seed": bool(client.industry or client.industry_niche),
            "competitor_count": len(competitors),
            "requested": len(generated),
        },
        prompts=[],
    )
    session.add(prompt_set)
    await session.flush()

    for position, item in enumerate(generated, start=1):
        prompt_set.prompts.append(
            Prompt(
                id=ids.new_id(ids.PROMPT),
                prompt_set_id=prompt_set.id,
                text=item.text,
                intent=item.intent,
                position=position,
            )
        )
    await session.flush()
    return prompt_set


async def run_prompt(
    prompt: Prompt,
    *,
    subject_name: str,
    subject_domain: str,
    competitors: list[tuple[str, str | None]],
    engines: tuple[Engine, ...],
    settings: Settings,
) -> list[tuple[Engine, extraction_service.ExtractedFacts, engine_service.EngineAnswer]]:
    """Run one prompt across every engine and extract facts from each answer."""
    answers = await engine_service.ask_all(prompt.text, engines=engines, settings=settings)

    out = []
    for answer in answers:
        facts = extraction_service.extract_facts(
            answer,
            subject_name=subject_name,
            subject_domain=subject_domain,
            competitors=competitors,
        )
        # Sentiment costs a model call, so it is only spent where it means
        # something — see classify_sentiment's docstring.
        if facts.mentioned:
            sentiment, confidence = await extraction_service.classify_sentiment(
                answer, subject_name=subject_name, settings=settings
            )
            facts.sentiment = sentiment
            facts.sentiment_confidence = confidence
        out.append((answer.engine, facts, answer))
    return out


def persist_result(
    session: AsyncSession,
    *,
    scan: Scan,
    prompt: Prompt,
    engine: Engine,
    facts: extraction_service.ExtractedFacts,
    answer: engine_service.EngineAnswer,
    competitor_ids: dict[str, str],
) -> EngineResult:
    """Write one EngineResult plus its BrandMention and Citation rows.

    `response_digest` carries the SHA-256 of the answer; the answer itself is
    not written anywhere (ip-safety.md #7).
    """
    result = EngineResult(
        id=ids.new_id(ids.ENGINE_RESULT),
        scan_id=scan.id,
        prompt_id=prompt.id,
        engine=engine,
        engine_version=answer.engine_version,
        status=facts.status,
        mentioned=facts.mentioned,
        position=facts.position,
        prominence=facts.prominence,
        sentiment=facts.sentiment,
        sentiment_confidence=facts.sentiment_confidence,
        brands_mentioned=facts.brands_mentioned,
        response_digest=answer.digest(),
        latency_ms=answer.latency_ms,
        error_code=answer.error_code,
        brand_mentions=[],
        citations=[],
    )
    session.add(result)

    for hit in facts.brand_hits:
        result.brand_mentions.append(
            BrandMention(
                id=ids.new_id(ids.BRAND_MENTION),
                engine_result_id=result.id,
                entity_name=hit.name[:200],
                entity_domain=hit.domain,
                is_subject=hit.is_subject,
                competitor_id=None if hit.is_subject else competitor_ids.get(hit.name),
                position=hit.position,
            )
        )

    for cited in facts.citations:
        result.citations.append(
            Citation(
                id=ids.new_id(ids.CITATION),
                engine_result_id=result.id,
                source_domain=cited.domain,
                source_url=cited.url,
                source_type=cited.source_type,
                position=cited.position,
                cites_subject=cited.cites_subject,
                competitor_id=competitor_ids.get(cited.domain),
            )
        )
    return result


async def run_scan(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    *,
    settings: Settings | None = None,
    engines: tuple[Engine, ...] = engine_service.DEFAULT_ENGINES,
    prompt_limit: int | None = None,
) -> Scan:
    """Execute a scan end to end and persist every prompt x engine result."""
    settings = settings or get_settings()
    subject_name = client.brand_name or client.name or client.domain

    scan.status = ScanStatus.RUNNING
    scan.started_at = scan.started_at or datetime.now(UTC)
    scan.engine_versions = {
        e.value: engine_service.ENGINE_REGISTRY[e].version for e in engines
    }
    await session.flush()
    # Commit the RUNNING transition before any of the slow work starts — Epic
    # 9.5. This function runs for minutes (Epic 9.2 measured 288.5s in the loop
    # alone), and until this commit the row is invisible to every other request:
    # the dashboard cannot show a scan in flight, and `get_or_create_scan`
    # cannot see one to reuse. Committing here is what makes the status
    # pollable, and it is why the caller must be prepared for a partially
    # written scan rather than an all-or-nothing one.
    await session.commit()

    competitors = await load_competitors(session, scan)
    competitor_ids = await _competitor_id_map(session, scan)

    prompt_set = await build_prompt_set(
        session, scan, client, competitors=competitors, settings=settings, limit=prompt_limit
    )
    ordered = sorted(prompt_set.prompts, key=lambda p: p.position)
    scan.prompt_count = len(ordered)
    await session.flush()

    semaphore = asyncio.Semaphore(PROMPT_CONCURRENCY)

    async def one(prompt: Prompt):  # noqa: ANN202
        async with semaphore:
            return prompt, await run_prompt(
                prompt,
                subject_name=subject_name,
                subject_domain=client.domain,
                competitors=competitors,
                engines=engines,
                settings=settings,
            )

    completed = await asyncio.gather(*(one(p) for p in ordered))

    failures = 0
    for prompt, per_engine in completed:
        for engine, facts, answer in per_engine:
            persist_result(
                session,
                scan=scan,
                prompt=prompt,
                engine=engine,
                facts=facts,
                answer=answer,
                competitor_ids=competitor_ids,
            )
            if facts.status not in (
                EngineResultStatus.OK,
                EngineResultStatus.ANSWERED_NO_MENTION,
            ):
                failures += 1

    scan.engine_result_count = len(ordered) * len(engines)
    scan.finished_at = datetime.now(UTC)
    # PARTIAL exists so a report can say "one engine was down" rather than
    # presenting a depressed mention rate as fact.
    if failures == 0:
        scan.status = ScanStatus.SUCCEEDED
    elif failures < scan.engine_result_count:
        scan.status = ScanStatus.PARTIAL
    else:
        scan.status = ScanStatus.FAILED
        scan.error_code = "ALL_ENGINE_CALLS_FAILED"

    await session.flush()
    # The terminal status lands durably here rather than waiting for a caller to
    # commit. A scan that has finished has finished, whatever the caller does
    # next.
    await session.commit()
    logger.info(
        "scan.completed",
        scan_id=scan.id,
        status=scan.status.value,
        prompts=scan.prompt_count,
        results=scan.engine_result_count,
        failures=failures,
    )
    return scan


async def _competitor_id_map(session: AsyncSession, scan: Scan) -> dict[str, str]:
    """Name and domain -> competitor id, for linking mentions and citations."""
    competitor_set = (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan.id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()
    if competitor_set is None:
        return {}
    mapping: dict[str, str] = {}
    for competitor in competitor_set.active_competitors:
        mapping[competitor.name] = competitor.id
        if competitor.domain:
            mapping[competitor.domain] = competitor.id
    return mapping


__all__ = [
    "Competitor",
    "build_prompt_set",
    "load_competitors",
    "persist_result",
    "run_prompt",
    "run_scan",
]

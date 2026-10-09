"""Scan execution — §5.4 steps 3-4 wired together.

    generate prompts -> run every prompt x engine -> extract facts -> persist

Produces EngineResult rows and nothing else. Aggregation into a score is Epic 5;
this module deliberately computes no rates and no composite.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy import func, select, update
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
from ..models.engine_result import ANSWERED_STATUSES, Engine, EngineResultStatus
from . import engines as engine_service
from . import extraction as extraction_service
from . import probable_rivals, question_sets
from . import prompts as prompt_service
from .concurrency import gather_cancelling
from .intake import dns_form

logger = structlog.get_logger(__name__)

# Bounded because every unit of concurrency is a paid model call, and a
# grounded engine can take 100s+ per prompt. Unbounded fan-out over 20 prompts x
# 4 engines is 80 simultaneous requests, which buys rate limits, not speed.
#
# FOUR ENGINES AND TWENTY PROMPTS — 2026-09-12, the founder's decision (see
# `engines.DEFAULT_ENGINES` and `prompts.TARGET_PROMPTS`). Re-derived, not
# edited: a slot now awaits four concurrent calls, so at c=12 in-flight
# requests are at most 48 — 12 Anthropic, 12 OpenAI, and Perplexity's and
# Gemini's own gates below that (their `max_in_flight` and Perplexity's
# 1.25s start interval bound them regardless of this value) — plus at most
# 12 sentiment calls, now on Haiku. Twenty prompts at c=12 is two waves
# (12 + 8) instead of 24's two (12 + 12), so the loop's shape is unchanged
# and its tail is shorter: the grounded Claude call was the slowest single
# call in every measured scan (121.9s, 99.8s, 94.2s), and it is no longer
# in the default set. This value stays at 12; nothing measured argues for
# moving it, and the next `verify_e2e.py` run is what would.
#
# FIVE ENGINES, AND A SECOND KNOB — Epic 21 (superseded above, kept for its
# reasoning about the per-vendor gates). `perplexity` and `gemini` join
# the default set when their keys are present, so a slot may now await five
# concurrent calls and in-flight requests may reach 60 at c=12. That count is
# not what bounds the two new vendors: their rate limits at a new account's
# tier are far under Anthropic's and OpenAI's, so each adapter declares a
# `max_in_flight` and `engines.ask_all` gates on it PER VENDOR, independent of
# this value. A slot waiting on a vendor gate holds its slot, so the loop can
# only get slower with five engines than with three; how much slower is
# measured, not projected — and at the time of writing it could NOT be
# measured, because neither key existed in this environment. build-log
# Epic 21 records the debt and this number is unchanged until it is paid.
#
# RAISED TO 12 — Epic 18.1, the scan-speed budget. Sized against measured
# limits, not guessed: the answer models' accounts allow 10,000 requests a
# minute (Anthropic) and 500 (OpenAI), read from the rate-limit headers on
# 2026-09-10; twelve slots x three engines is 36 requests in flight, plus at
# most twelve sentiment calls. Both SDK paths already name a 429 as
# PROVIDER_RATE_LIMITED, and the Anthropic SDK retries one 429 within the
# existing ENGINE_CALL_CEILING, which is untouched. Measured, not projected:
# at c=4 Epic 9.2 saw a 288.5s loop in a 361.3s run; at c=8 Epic 18.1 saw
# 200.4s in 308.9s — over by 8.9s, with a 59s tail the semaphore cannot
# pack away (one 121s call at the ceiling holds its slot to the end); at
# c=12 the numbers are in build-log.md's Epic 18.1 entry.
#
# The paragraph below is Epic 9.13's, kept because its reasoning about what
# one slot costs still holds — only the count changed.
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
PROMPT_CONCURRENCY = 12


def terminal_status_for(failures: int, total: int) -> tuple[ScanStatus, str | None]:
    """The engine phase's verdict, as a pure function — Epic 9.17.

    Extracted because it now has two callers: `run_scan`, which knows the
    failure count it just produced, and `finalize_scan`, which recounts it from
    the persisted rows after the rest of the chain has run. Two copies of this
    rule would be two answers to "did this scan succeed", and they would
    disagree on the day it mattered.

    PARTIAL exists so a report can say "one engine was down" rather than
    presenting a depressed mention rate as fact.
    """
    if failures == 0:
        return ScanStatus.SUCCEEDED, None
    if failures < total:
        return ScanStatus.PARTIAL, None
    return ScanStatus.FAILED, "ALL_ENGINE_CALLS_FAILED"


async def count_engine_failures(session: AsyncSession, scan_id: str) -> int:
    """Persisted engine results that did not produce an answer.

    The same predicate `run_scan` applies in memory, asked of the database
    instead — `OK` and `ANSWERED_NO_MENTION` are both answers, and a subject
    the engine did not mention is a finding rather than a failure.
    """
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(EngineResult)
                .where(
                    EngineResult.scan_id == scan_id,
                    EngineResult.status.not_in(
                        (EngineResultStatus.OK, EngineResultStatus.ANSWERED_NO_MENTION)
                    ),
                )
            )
        ).scalar_one()
    )


async def count_answered_prompts(session: AsyncSession, scan_id: str) -> int:
    """Prompts with at least one answered engine result (`ANSWERED_STATUSES`).

    A prompt one engine answered and another errored on is answered.
    """
    return int(
        (
            await session.execute(
                select(func.count(func.distinct(EngineResult.prompt_id))).where(
                    EngineResult.scan_id == scan_id,
                    EngineResult.status.in_(ANSWERED_STATUSES),
                )
            )
        ).scalar_one()
    )


async def finalize_scan(session: AsyncSession, scan: Scan) -> Scan:
    """Stamp the terminal status, after every phase of the chain has run.

    The other half of `run_scan(defer_terminal_status=True)`. Recounts failures
    from the persisted `EngineResult` rows rather than being handed a number,
    so it is correct whether or not the caller kept one — and applies exactly
    the rule above.

    Idempotent by the same guard the executor uses: a scan already terminal is
    left alone, because a second stamp would move `finished_at` forward for a
    scan that finished earlier.

    **THE STAMP IS CONDITIONAL ON THE ROW, NOT ON THE CHAIN'S COPY OF IT** —
    launch checklist 3.36. The guard above reads `scan.status`, which is only
    this session's copy: `expire_on_commit` is off (`db.py`), so another
    transaction's write does not reach it. Only a refresh does, and the chain
    refreshes the scan only after a failed phase (`scan_executor._attempt`). A
    reaper that fails a scan whose chain is still alive therefore leaves that
    copy saying RUNNING until the next heartbeat finds the row changed and
    cancels the chain, up to `HEARTBEAT_INTERVAL` later. Stamped through the
    ORM, the write was an UPDATE with no condition on the status, so a finalize
    inside that window replaced FAILED with SUCCEEDED or PARTIAL, and a failed
    scan's share link (3.29) opened again.

    So the write is one `UPDATE ... WHERE status = 'running'`, the shape of the
    lease renewal (`scan_executor.renew_lease`) and of the deadline's stamp, and
    not a read followed by a write: Postgres checks the condition again once the
    row is free of any other transaction's write. Zero rows matched is a
    definite answer, that somebody else owns this scan's fate (the reaper, the
    deadline's finalise, `_mark_failed`). Nothing is written, `scan.finalize_lost`
    is logged with the status the row has, and the scan is returned as stored.
    """
    if scan.status in (
        ScanStatus.SUCCEEDED,
        ScanStatus.PARTIAL,
        ScanStatus.FAILED,
        ScanStatus.CANCELLED,
    ):
        return scan

    failures = await count_engine_failures(session, scan.id)
    status, error_code = terminal_status_for(failures, scan.engine_result_count)
    values: dict[str, object] = {"status": status, "finished_at": datetime.now(UTC)}
    if error_code is not None:
        values["error_code"] = error_code
    # Anything still pending on the session goes out BEFORE the stamp. The session does
    # not autoflush, so left for the commit below it would be written AFTER the stamp,
    # as an ORM UPDATE with no condition at all, and could replace what the stamp wrote.
    await session.flush()
    stamped = (
        await session.execute(
            update(Scan)
            .where(Scan.id == scan.id, Scan.status == ScanStatus.RUNNING)
            .values(**values)
            .returning(Scan.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    await session.commit()
    # `synchronize_session=False` leaves the chain's copy as it was, so it is read
    # again: the scan comes back as the row has it, whoever wrote it.
    await session.refresh(scan)
    if stamped is None:
        logger.warning("scan.finalize_lost", scan_id=scan.id, status=scan.status.value)
        return scan
    logger.info(
        "scan.finalized",
        scan_id=scan.id,
        status=scan.status.value,
        failures=failures,
        results=scan.engine_result_count,
    )
    return scan


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


def _as_prompt_result(
    produced: prompt_service.PromptResult | tuple[list[prompt_service.GeneratedPrompt], str],
) -> prompt_service.PromptResult:
    """A `PromptResult`, from one or from the `(prompts, generated_by)` pair older stubs return.

    The pair has no counts, so every question in it is counted as the generator's.
    """
    if isinstance(produced, prompt_service.PromptResult):
        return produced
    items, generated_by = produced
    return prompt_service.PromptResult(
        prompts=list(items), generated_by=generated_by, from_generator=len(items)
    )


async def build_prompt_set(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    *,
    competitors: list[tuple[str, str | None]],
    settings: Settings,
    limit: int | None = None,
) -> PromptSet:
    """Persist the scan's prompt set: the client's own questions again, or a freshly written set.

    A rescan COPIES the client's most recent all-generated set (`question_sets.clients_set`) and
    makes no generator call. A fresh set is written when the client has no such set or when the
    agency asked for new questions (`clients.new_questions_next_scan`). That flag is read here from
    the database, not from the copy of the client loaded before this scan was claimed, and it is
    cleared in the same transaction as the fresh set only when that set is one a rescan would
    copy (`question_sets.is_all_generated`): a set topped up with templates, or cut to a prompt
    limit, leaves it set. (F6.)

    Prompt.text is our OWN generated text, not third-party content — the
    deliberate exception to the facts-only rule, recorded in test_facts_only.py.
    """
    # A PUT can commit between the claim that loaded `client` and this point, and the session does
    # not expire on commit, so the copy in hand can be stale. Read the one column again.
    await session.refresh(client, attribute_names=["new_questions_next_scan"])
    if not client.new_questions_next_scan:
        # Scoped by the SCAN's own client and agency, not the job's `client` object: whatever the
        # caller passes in, the set copied is one that this scan's client and agency own.
        source = await question_sets.clients_set(
            session, client_id=scan.client_id, agency_id=scan.agency_id, exclude_scan_id=scan.id
        )
        if source is not None:
            return await _reuse_prompt_set(
                session, scan, client, source[0], source[1], limit=limit
            )
    result = _as_prompt_result(
        await prompt_service.generate_prompts(
            brand_name=client.brand_name,
            domain=client.domain,
            industry=client.industry,
            niche=client.industry_niche,
            competitors=[name for name, _ in competitors],
            settings=settings,
        )
    )
    if result.unavailable is not None:
        # Nothing was scanned and nothing is stored: the scan ends FAILED / PROMPTS_UNAVAILABLE
        # before any engine call (`scan_executor`). Free unless the model's answer caused it and the
        # agency has used its free three in 24 hours (`scan_executor._prompts_failure_code`).
        logger.warning("prompts.unavailable", scan_id=scan.id, reason=result.unavailable)
        raise prompt_service.PromptsUnavailable(result.unavailable)
    generated = result.prompts
    if limit is not None:
        generated = generated[:limit]
    # The counts describe the questions actually persisted. The generator's questions come first
    # and any templates after them, so a cut to `limit` takes templates first.
    from_generator = min(len(generated), result.from_generator)
    from_fallback = len(generated) - from_generator
    # Counts, never text: how many questions the generator wrote and kept, and how many it lost
    # (blank or over-long), then how many came from templates, said only when there were any.
    logger.info(
        "prompts.generated",
        scan_id=scan.id,
        generated=from_generator,
        dropped=result.dropped,
    )
    if from_fallback:
        logger.info(
            "prompts.fallback_used",
            scan_id=scan.id,
            fallback=from_fallback,
            reason="under_target",
        )

    prompt_set = PromptSet(
        id=ids.new_id(ids.PROMPT_SET),
        scan_id=scan.id,
        generated_by=result.generated_by,
        # Provenance: enough to explain a set after the fact, without storing
        # anything that is not ours.
        generation_params={
            "industry": client.industry,
            "niche": client.industry_niche,
            "used_industry_seed": bool(client.industry or client.industry_niche),
            "competitor_count": len(competitors),
            "requested": len(generated),
            # F5: where the questions came from. The CSV's Scan section reads these two; a set
            # stored before this carries neither and reads "Not recorded".
            "fromGenerator": from_generator,
            "fromFallback": from_fallback,
            # F6: the rivals' names the questions were written with, so a later view can say the
            # list has changed since; a cut set is a few of the questions and never the client's.
            "writtenWithRivals": [name for name, _ in competitors],
            **({"limited": True} if len(generated) < len(result.prompts) else {}),
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
    prompt_set.generation_params = {
        **prompt_set.generation_params,
        "fingerprint": question_sets.fingerprint(
            (p.text, p.intent.value) for p in prompt_set.prompts
        ),
    }
    # The flag is spent only by a set that becomes the client's set, by the test `clients_set`
    # applies: a set topped up with templates or cut to a prompt limit would not be copied, so
    # spending the flag on it would send the next scan back to the old set the agency asked to
    # replace. Same transaction as the set, never before it.
    if question_sets.is_all_generated(prompt_set):
        client.new_questions_next_scan = False
    await session.flush()
    return prompt_set


async def _reuse_prompt_set(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    source: PromptSet,
    source_scan_id: str,
    *,
    limit: int | None,
) -> PromptSet:
    """Copy the source set's questions into this scan's own new `PromptSet` (new rows)."""
    ordered = sorted(source.prompts, key=lambda p: p.position)
    cut = limit is not None and limit < len(ordered)
    if limit is not None:
        ordered = ordered[:limit]
    params = source.generation_params or {}
    carried: dict[str, object] = {
        key: min(params[key], len(ordered)) if key == "fromGenerator" else params[key]
        for key in ("fromGenerator", "fromFallback", "writtenWithRivals")
        if key in params
    }
    prompt_set = PromptSet(
        id=ids.new_id(ids.PROMPT_SET),
        scan_id=scan.id,
        generated_by=question_sets.REUSED,
        generation_params={
            "industry": client.industry,
            "niche": client.industry_niche,
            "requested": len(ordered),
            "reusedFromScanId": source_scan_id,
            **carried,
            **({"limited": True} if cut else {}),
        },
        prompts=[],
    )
    session.add(prompt_set)
    await session.flush()
    for position, item in enumerate(ordered, start=1):
        prompt_set.prompts.append(
            Prompt(
                id=ids.new_id(ids.PROMPT),
                prompt_set_id=prompt_set.id,
                text=item.text,
                intent=item.intent,
                position=position,
            )
        )
    prompt_set.generation_params = {
        **prompt_set.generation_params,
        "fingerprint": question_sets.fingerprint(
            (p.text, p.intent.value) for p in prompt_set.prompts
        ),
    }
    await session.flush()
    # Ids and counts only: never a question's text.
    logger.info(
        "prompts.reused", scan_id=scan.id, from_scan_id=source_scan_id, questions=len(ordered)
    )
    return prompt_set


async def run_prompt(
    prompt: Prompt,
    *,
    subject_name: str,
    subject_domain: str,
    competitors: list[tuple[str, str | None]],
    engines: tuple[Engine, ...],
    settings: Settings,
    subject_other_sites: frozenset[str] = frozenset(),
    competitor_other_sites: frozenset[str] = frozenset(),
) -> list[tuple[Engine, extraction_service.ExtractedFacts, engine_service.EngineAnswer]]:
    """Run one prompt across every engine and extract facts from each answer.

    The two other-sites sets are the scan's, read once by `run_scan`, so every answer of a scan
    is typed by one list.
    """
    answers = await engine_service.ask_all(prompt.text, engines=engines, settings=settings)

    out = []
    for answer in answers:
        facts = extraction_service.extract_facts(
            answer,
            subject_name=subject_name,
            subject_domain=subject_domain,
            competitors=competitors,
            subject_other_sites=subject_other_sites,
            competitor_other_sites=competitor_other_sites,
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
    not written anywhere (the facts-only rule).
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
                competitor_id=_competitor_of(cited.domain, competitor_ids),
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class NamedDomainsContext:
    """What `scans.named_domains` is computed from, besides the results themselves (F2).

    The client's name and domain, and the scan's rivals as `(name, domain)`: the matcher's
    `claimed_forms` come from them (`probable_rivals.claimed_short_forms`), the client's own
    registrable domain is left out of the result, and its ending says which cited domains are
    candidates. Held by `EnginePhaseProgress` so that the deadline's salvage can record the same
    fact `run_scan` would have: it persists the same results without the objects `run_scan` held.
    """

    subject_name: str
    client_domain: str
    competitors: tuple[tuple[str, str | None], ...]


# THE COUNT'S TIME LIMIT, in seconds (F2; the security review of 2026-10-10, should-fix 1).
#
# `probable_rivals.named_domains` puts the matcher every (cited domain with the client's ending,
# answer) pair that its pre-filter (`probable_rivals.may_name`) lets through, and the matcher costs
# ~0.25 ms a call at 3k characters and ~0.67 ms at 8k, linear in the text. Measured on this tree,
# 96 answers of ~5.4k characters over 60 candidate domains took 2,574 ms before the pre-filter and
# ~95 ms after. The review's top end, 120 answers of ~16k characters, is ~10 s without it (12 s
# measured at ~20k characters) and ~0.2 s with it. A count in which most pairs still pass (many
# short labels that any text holds) costs what it used to, so the limit stays, and 15 s clears
# the unfiltered top end. It is one term in the scan deadline's worst case (`MAX_SCAN_DURATION`;
# `test_scan_executor.py` recomputes the sum) and in what the deadline's finalise does
# (`DEADLINE_SCORING_BUDGET`).
#
# A count over its budget is DROPPED, not waited for: `scans.named_domains` stays NULL, which is
# "not recorded" and which every report reads as it read before F2 (nothing is taken out of "Get
# onto" and no rival is suggested). The scan carries on, with every answer it paid for.
NAMED_DOMAINS_BUDGET_S: Final = 15

# The count's threads. A thread cannot be stopped, so a count that outlasts its budget keeps its
# worker until it ends. In the loop's default pool (`asyncio.to_thread`) that would be a worker the
# loop also needs for the DNS lookups behind its database, Redis and HTTP connections, so the count
# has a pool of its own, of two: one worker can be held by a count past its budget while the next
# count (the deadline's salvage, or another scan's) has the other. A count that finds both held
# waits for one inside its own budget, and is dropped if that runs out.
_NAMED_DOMAINS_POOL: Final = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="avp-named-domains"
)


def _require_counts(found: object) -> dict[str, int]:
    """`found` if it is a map of text to whole numbers, else a TypeError that says nothing of it.

    The count is stored in a JSONB column and read as a list of facts, so a value of any other
    shape is not recorded. A boolean is not a count, and a value the database cannot store would
    fail the commit that writes every answer the scan paid for. The message is fixed on purpose:
    only the exception's class is ever logged, and a message could carry an answer.
    """
    if not isinstance(found, dict) or any(
        type(domain) is not str or type(answers) is not int for domain, answers in found.items()
    ):
        raise TypeError("not a map of domains to counts")
    return found


async def count_named_domains(
    scan_id: str,
    finished: Iterable[
        tuple[Engine, extraction_service.ExtractedFacts, engine_service.EngineAnswer]
    ],
    *,
    context: NamedDomainsContext | None,
) -> dict[str, int] | None:
    """The count `record_named_domains` stores, or None when there is none to store.

    None for a holder that was never told whose scan this is (`context` is None: nothing to say it
    from, so never a guess), for a count that raised or ran out of its budget, and for a result
    that is not a map of text to whole numbers. Everything is inside one guarded block, the count,
    the check of its shape and the line it leaves, so that nothing here can raise out of a scan
    that has been paid for. Only `CancelledError` (the scan's deadline) passes through. See
    `record_named_domains` for the budget and the pool.

    It takes the scan's id and not the scan: after a deadline the salvage counts BEFORE it opens
    the session it writes through (`scan_executor._named_domains_for_salvage`), so that no
    connection sits idle in an open transaction while the count runs.
    """
    if context is None:
        return None
    try:
        results = list(finished)
        async with asyncio.timeout(NAMED_DOMAINS_BUDGET_S):
            found = await asyncio.get_running_loop().run_in_executor(
                _NAMED_DOMAINS_POOL,
                functools.partial(
                    probable_rivals.named_domains,
                    results,
                    client_domain=context.client_domain,
                    claimed_forms=probable_rivals.claimed_short_forms(
                        context.subject_name, context.client_domain, context.competitors
                    ),
                ),
            )
        counted = _require_counts(found)
        logger.info(
            "scan.named_domains",
            scan_id=scan_id,
            domains=len(counted),
            named=sum(1 for answers in counted.values() if answers >= 1),
        )
    except Exception as exc:  # noqa: BLE001 - a recommendation's input must not cost a paid scan
        logger.warning("scan.named_domains_failed", scan_id=scan_id, error=type(exc).__name__)
        return None
    return counted


async def record_named_domains(
    scan: Scan,
    finished: Iterable[
        tuple[Engine, extraction_service.ExtractedFacts, engine_service.EngineAnswer]
    ],
    *,
    context: NamedDomainsContext | None,
    progress: EnginePhaseProgress | None = None,
) -> None:
    """Store on the scan how many answers named each cited domain with the client's ending (F2).

    Called by `run_scan` over the `(engine, facts, answer)` tuples it is about to write, because
    whether an answer named a brand can only be told while its text exists. See
    `probable_rivals.named_domains` for what is counted. The scan is changed in memory; the
    caller's commit writes it with the results. The deadline's salvage
    (`scan_executor._finalize_after_deadline`) records the same fact for the prompts it persists,
    and takes the outcome of THIS call from `progress`, where it is left right after the count
    (`EnginePhaseProgress.named_domains_attempted` and `named_domains_result`), instead of counting
    the same answers again.

    **A budget.** The count has `NAMED_DOMAINS_BUDGET_S` seconds. Over it, the column is left NULL
    ("not recorded", which every report reads as it read before F2) and the scan carries on.

    **Off the event loop, on a pool of its own.** The count reads every answer once per candidate
    domain, which on a big scan of a university (dozens of cited `edu.au` domains over ~100 long
    answers) is seconds of CPU. A scan runs as a task in the API's own loop, so the count runs in a
    thread and the loop keeps serving. Only the pure count is threaded: the results are
    materialised first, here, and the scan is changed back on the loop. **A thread cannot be
    stopped:** a count that outlasts its budget is not waited for but runs to its end in
    `_NAMED_DOMAINS_POOL`, two workers named `avp-named-domains`, so it never holds a worker the
    loop's default pool needs for its connections' DNS lookups, and the next count has the other
    worker.

    **It can never fail a scan.** It runs after every answer has been paid for and before they are
    written, so an exception here would throw them away. Whatever goes wrong, the column is left
    NULL and the line says the exception's class and the scan, never its message: a message could
    carry an answer. That includes a result that is not a map of text to whole numbers, which is
    checked inside the same guarded block before anything is set from it. With no `context` (a
    holder that was never told whose scan this is) there is nothing to say it from and the column
    is left alone: never a guess. A line on success has ids and counts only.
    """
    found = await count_named_domains(scan.id, finished, context=context)
    if context is None:
        return
    if progress is not None:
        progress.named_domains_result = found
        progress.named_domains_attempted = True
    if found is not None:
        scan.named_domains = found


@dataclass
class EnginePhaseProgress:
    """What the engine loop has finished, for a caller that may have to stop it.

    THE GAP IT CLOSES — decision 7, 2026-10-01. `run_scan` gathers every prompt
    and writes nothing until the gather returns, so a caller that cancels the
    loop (the executor's deadline) used to lose every answer already paid for:
    they lived in the gather's result, which a cancellation discards. This
    holder is filled as each prompt finishes, so the caller still has them.

    A holder, not a return value, because the caller only ever sees it when
    `run_scan` did NOT return. It is mutable on purpose and has one writer,
    `run_scan`, on one event loop: no lock.

    * `competitor_ids` and `engines` are set before the loop starts — what
      `persist_result` needs, and what a caller persisting these results
      itself must use for them to read exactly as `run_scan`'s own would.
      So is `named_domains_context` (F2): what `record_named_domains` needs to
      record the same `scans.named_domains` `run_scan` would have. A holder
      that never had it set records nothing.
    * `named_domains_attempted` and `named_domains_result` (F2, the security
      review's should-fix 1): `run_scan` sets both right after its count, so
      the deadline's salvage can tell "never counted" from "counted, and the
      count came to nothing". The result is the map that was counted, or None
      when the count failed or ran out of its budget; None stays NULL in the
      column and the salvage does not try again. Both stay unset when the
      deadline cancelled the count itself: that count did not finish, and the
      salvage makes it. They hold domains and counts, never an answer's text.
    * `completed` gains `(prompt_id, per-engine results)` as each prompt
      finishes — all of that prompt's engines, as `run_prompt` returns them.
      Completion order, not prompt order; a prompt that has not finished its
      engines is not here. It is EMPTIED the moment `persisted` is set: once
      the results are written the holder has nothing left to keep.
    * `persisted` turns True right after the commit that writes the loop's
      results. A caller that finds it False must still ask the DATABASE before
      writing anything: a cancellation can land after that commit and before
      this flag, and the table is the truth.

    The results hold the answers' text while they are held, which is what
    `persist_result` needs to take their digest — memory only, never written
    (the facts-only rule), and `repr=False` so that logging a holder cannot
    print it. Emptied on `persisted` so that raw text does not outlive the
    write: the holder lives for the whole job, through the audit, the score,
    the alerts and the fixes.
    """

    competitor_ids: dict[str, str] = field(default_factory=dict)
    engines: tuple[Engine, ...] = ()
    named_domains_context: NamedDomainsContext | None = None
    named_domains_attempted: bool = False
    named_domains_result: dict[str, int] | None = None
    completed: list[
        tuple[
            str,
            list[tuple[Engine, extraction_service.ExtractedFacts, engine_service.EngineAnswer]],
        ]
    ] = field(default_factory=list, repr=False)
    persisted: bool = False


async def run_scan(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    *,
    settings: Settings | None = None,
    engines: tuple[Engine, ...] = engine_service.DEFAULT_ENGINES,
    prompt_limit: int | None = None,
    defer_terminal_status: bool = False,
    progress: EnginePhaseProgress | None = None,
) -> Scan:
    """Execute a scan end to end and persist every prompt x engine result.

    `defer_terminal_status` leaves the scan RUNNING with its counts committed,
    for a caller that has more phases to run — Epic 9.17's chain. Without it
    the scan reaches SUCCEEDED the moment the engine loop ends, while the
    audit, the score and the fix list are still forty seconds away: the
    dashboard stops polling, the operator clicks through, and the report they
    open is the degraded one this chain exists to stop producing.

    The caller stamps the terminal status with `finalize_scan` once every
    phase has been attempted. Defaults to False, so every existing caller —
    `verify_e2e.py`, the tests, a direct call — behaves exactly as before.

    `progress`, when given, is filled as the loop runs — see
    `EnginePhaseProgress`. A caller that passes nothing gets exactly the
    behaviour it always had.
    """
    settings = settings or get_settings()
    subject_name = client.brand_name or client.name or client.domain

    scan.status = ScanStatus.RUNNING
    scan.started_at = scan.started_at or datetime.now(UTC)
    scan.engine_versions = {
        e.value: engine_service.ENGINE_REGISTRY[e].version for e in engines
    }
    # The process that runs a scan is the one that matches it (`extract_facts`, below), so it
    # says which rule that was, whatever the row read when it was queued: a scan an older
    # instance queued and this one runs is stamped by this one. Checklist 3.97; the column's note.
    scan.matcher_version = extraction_service.MATCHER_VERSION
    # THE ONE READ OF THE CLIENT'S CONFIRMED OTHER SITES (M1 task 1.2). Here, at the RUNNING
    # transition, and nowhere after it: every answer of this scan is typed by this one list, and
    # what the scan stored (`cites_subject` per citation) is what the report, the fixes, the
    # scoring and an explicit re-score read. A later edit of the list changes the next scan only.
    subject_other_sites = frozenset(client.other_sites)
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
    # The rivals' confirmed sites come from the scan's OWN frozen set, never the client's next list.
    competitor_other_sites = await _competitor_other_sites(session, scan)

    prompt_set = await build_prompt_set(
        session, scan, client, competitors=competitors, settings=settings, limit=prompt_limit
    )
    ordered = sorted(prompt_set.prompts, key=lambda p: p.position)
    scan.prompt_count = len(ordered)
    await session.flush()
    # COMMIT BEFORE THE LOOP, AND THE REASON IS A ROW LOCK — the scan lease,
    # 2026-09-08.
    #
    # The flush above issues `UPDATE scans SET prompt_count = ...`, which takes
    # a row lock on this scan's row and holds it until the transaction ends.
    # The next statement on this session is the commit AFTER the gather below,
    # so without this commit that lock is held across the entire engine phase —
    # minutes, and up to ~2,460s on a maximal scan.
    #
    # Everything that wants to write this row then blocks instead of running:
    # a lease renewal on its own session (which is how the executor says "still
    # alive"), and `reap_stale_scans` from the dashboard or a POST. A heartbeat
    # that cannot renew is a lease that expires during ordinary work, so this
    # commit is the precondition for the lease rather than a tidy-up beside it.
    # Measured on `avp_dev` with two connections before it was written: the
    # second UPDATE blocked until the first transaction ended.
    #
    # It is also correct on its own terms, for the reason the commit at the top
    # of this function gives. The prompt set is finished work: committing it
    # makes `prompt_count` pollable while the loop runs, and a crash mid-loop
    # keeps the generated set rather than discarding a paid generation call.
    # What it costs is that a crashed scan leaves a committed prompt set with
    # no results — inert, because that scan is marked FAILED and the next POST
    # opens a new row rather than reusing it.
    await session.commit()

    named_context = NamedDomainsContext(
        subject_name=subject_name, client_domain=client.domain, competitors=tuple(competitors)
    )
    if progress is not None:
        progress.competitor_ids = competitor_ids
        progress.engines = engines
        progress.named_domains_context = named_context

    semaphore = asyncio.Semaphore(PROMPT_CONCURRENCY)

    async def one(prompt: Prompt):  # noqa: ANN202
        async with semaphore:
            per_engine = await run_prompt(
                prompt,
                subject_name=subject_name,
                subject_domain=client.domain,
                competitors=competitors,
                engines=engines,
                settings=settings,
                subject_other_sites=subject_other_sites,
                competitor_other_sites=competitor_other_sites,
            )
            # Recorded the moment this prompt's engines are all done, not when
            # the whole loop is: a cancellation that arrives before the gather
            # returns would otherwise take every finished answer with it.
            if progress is not None:
                progress.completed.append((prompt.id, per_engine))
            return prompt, per_engine

    completed = await gather_cancelling(*(one(p) for p in ordered))

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

    # While the answers' text exists, and in the same commit as the rows (F2). The deadline's
    # salvage records it too (`scan_executor._persist_finished`), from what is left on `progress`
    # right after this count when there is something there: it does not count the same answers
    # twice.
    await record_named_domains(
        scan,
        (result for _prompt, per_engine in completed for result in per_engine),
        context=named_context,
        progress=progress,
    )
    scan.engine_result_count = len(ordered) * len(engines)

    if defer_terminal_status:
        # Counts committed, status left RUNNING. The results are durable — a
        # crash after this point loses no engine spend — and the reaper still
        # covers a chain whose process disappears, because RUNNING is exactly
        # what it looks for.
        await session.flush()
        await session.commit()
        if progress is not None:
            progress.persisted = True
            progress.completed.clear()
        logger.info(
            "scan.engine_phase_completed",
            scan_id=scan.id,
            prompts=scan.prompt_count,
            results=scan.engine_result_count,
            failures=failures,
        )
        return scan

    scan.finished_at = datetime.now(UTC)
    scan.status, error_code = terminal_status_for(failures, scan.engine_result_count)
    if error_code is not None:
        scan.error_code = error_code

    await session.flush()
    # The terminal status lands durably here rather than waiting for a caller to
    # commit. A scan that has finished has finished, whatever the caller does
    # next.
    await session.commit()
    if progress is not None:
        progress.persisted = True
        progress.completed.clear()
    logger.info(
        "scan.completed",
        scan_id=scan.id,
        status=scan.status.value,
        prompts=scan.prompt_count,
        results=scan.engine_result_count,
        failures=failures,
    )
    return scan


async def _scan_competitor_set(session: AsyncSession, scan: Scan) -> CompetitorSet | None:
    return (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan.id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()


async def _competitor_id_map(session: AsyncSession, scan: Scan) -> dict[str, str]:
    """Name, domain and confirmed other site -> competitor id, for linking mentions and citations.

    Every rival's own name and domain is mapped before any rival's other site, and an other site
    never overwrites an entry (`setdefault`): a brand's main domain takes precedence over a
    confirmed other site, the same order `extraction.classify_citation` types a citation in. The
    other sites are keyed by their stored DNS form; `_competitor_of` looks a cited domain up in
    that form only after its exact lookup fails.
    """
    competitor_set = await _scan_competitor_set(session, scan)
    if competitor_set is None:
        return {}
    mapping: dict[str, str] = {}
    active = competitor_set.active_competitors
    for competitor in active:
        mapping[competitor.name] = competitor.id
        if competitor.domain:
            mapping[competitor.domain] = competitor.id
    for competitor in active:
        for site in competitor.other_sites:
            mapping.setdefault(site, competitor.id)
    return mapping


def _competitor_of(cited_domain: str, competitor_ids: dict[str, str]) -> str | None:
    """The rival a cited domain belongs to: an exact match on a name or domain, else DNS form."""
    found = competitor_ids.get(cited_domain)
    if found is not None:
        return found
    return competitor_ids.get(dns_form(cited_domain))


async def _competitor_other_sites(session: AsyncSession, scan: Scan) -> frozenset[str]:
    """The scan's active rivals' confirmed other sites (DNS form), as frozen on its rows."""
    competitor_set = await _scan_competitor_set(session, scan)
    if competitor_set is None:
        return frozenset()
    return frozenset(
        site for competitor in competitor_set.active_competitors for site in competitor.other_sites
    )


__all__ = [
    "NAMED_DOMAINS_BUDGET_S",
    "Competitor",
    "EnginePhaseProgress",
    "NamedDomainsContext",
    "build_prompt_set",
    "count_engine_failures",
    "count_named_domains",
    "finalize_scan",
    "load_competitors",
    "persist_result",
    "record_named_domains",
    "run_prompt",
    "run_scan",
    "terminal_status_for",
]

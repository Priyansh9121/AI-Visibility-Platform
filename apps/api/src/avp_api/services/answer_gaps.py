"""Answer-gap analysis — Epic B.

Cross-references three things already persisted: the prompts a scan asked, the
brands each answer named (`engine_result_brand_mentions`), and the sources each
answer cited (`engine_result_citations`). It collects nothing, calls no model
and no engine, and writes nothing. Every figure is an aggregation over rows a
scan already wrote.

TWO PREMISES IN THE BRIEF THAT THE DATA DOES NOT SUPPORT
--------------------------------------------------------
Both were checked against real `avp_dev` rows before this module was written,
and both changed its shape. They are recorded here rather than in a commit
message because the corrected shape is only defensible if the correction is
visible.

1. **"Tracked prompts", recurring between scans.** There are none. A prompt
   belongs to a `PromptSet`, a `PromptSet` belongs to ONE scan, and
   `scan_runner.build_prompt_set` calls `prompts.generate_prompts` fresh every
   time. Measured: Plausible's three scans hold 72 prompts with 71 distinct
   texts; Notion's two hold 48 with 48 distinct. Recurrence therefore cannot
   key on a prompt. It is counted across ENGINES within a scan (`absent_on`)
   and across scans by RIVAL (`_rival_rollup`), which are the two axes that
   actually persist.

2. **"Where the client has relevant content (per Sources) but no citation."**
   There is no content inventory anywhere in this schema — `TechnicalAudit`
   stores `pages_crawled` as a COUNT and one `url_audited`, not a page list.
   "Has relevant content" cannot be read. What CAN be read is whether the
   engines ever cited this client's own domain in this scan: if they did, the
   domain is demonstrably citable, and a prompt that named the client without
   citing it is a real content gap. If they never did, that claim is
   unsupported and `GapKind.UNCITED` is not reported at all — see
   `subject_citable`.

WHY THIS IS ONE PASS OVER FOUR QUERIES AND NOT A JOIN
-----------------------------------------------------
The grid is prompts x brands. Joining mentions to citations to results in one
statement multiplies rows — a scan carrying 72 results, 1,355 mentions and
2,399 citations would return the product, not the grid. Each of the four reads
below is grouped in the database and returns at most a few hundred rows, and
they are assembled in Python where the cost is a dict lookup.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Client
from ..models.engine_result import (
    BrandMention,
    Citation,
    EngineResult,
    EngineResultStatus,
)
from ..models.prompt import Prompt, PromptSet
from ..models.scan import Scan, ScanStatus
from ..schemas.answer_gaps import (
    AnswerGapBrandOut,
    AnswerGapCellOut,
    AnswerGapRivalOut,
    AnswerGapRowOut,
    AnswerGapsOut,
    GapKind,
)

# A scan carries a grid only if it actually asked something and got answers.
# Same set `client_history.PLOTTABLE` uses, and for the same reason: QUEUED and
# RUNNING have measured nothing yet, FAILED and CANCELLED never will.
GRIDDABLE: frozenset[ScanStatus] = frozenset({ScanStatus.SUCCEEDED, ScanStatus.PARTIAL})

# An answer counts toward the grid only if the engine actually answered.
# An engine that timed out did not decline to name the client — it never got
# the question — and counting its silence as an absence would manufacture gaps
# out of an outage. `client_history._sentiment_counts` draws the same line.
ANSWERED: tuple[EngineResultStatus, ...] = (
    EngineResultStatus.OK,
    EngineResultStatus.ANSWERED_NO_MENTION,
)

# Columns in the grid, subject included. The subject is always column one and
# is never subject to this cap; rivals are ranked by answers won and cut here.
# A grid wider than this stops being readable on any screen, and the long tail
# of one-off names is noise — `rivals` reports the whole set anyway.
MAX_BRAND_COLUMNS = 8


def _result_rows(scan_id: str) -> Select[tuple[str, str, bool, int]]:
    """Per (prompt, engine): did it name the subject, and how many brands total.

    `brands_mentioned` is the stored count of DISTINCT brands the answer named,
    written by `extract_facts`. Reading it here rather than counting mention
    rows means "no brand at all" is decided by the same number the score used.
    """
    return (
        select(
            EngineResult.prompt_id,
            EngineResult.engine,
            EngineResult.mentioned,
            EngineResult.brands_mentioned,
        )
        .where(
            EngineResult.scan_id == scan_id,
            EngineResult.status.in_(ANSWERED),
        )
    )


def _mention_rows(scan_id: str) -> Select[tuple[str, str, str | None, bool, int]]:
    """Per (prompt, brand): on how many engines that brand was named.

    Grouped in the database. A scan carries ~1,355 mention rows against ~24
    prompts x ~8 brands of output, so grouping in Python would move two orders
    of magnitude more data than the answer needs.

    Keyed by entity NAME rather than by `competitor_id`, because `competitor_id`
    is nullable on `BrandMention` and a set re-detected between scans reissues
    ids for the same rival — grouping on the id would split one competitor into
    several columns across a history.

    WHAT THIS CANNOT SEE, AND WHY THE SCREEN MUST SAY SO
    ----------------------------------------------------
    `extract_facts` SEARCHES for the brands it is given; it does not discover
    new ones. Its loop is `for comp_name, comp_domain in competitors`, so a
    rival that is not in the scan's detected `CompetitorSet` leaves no
    `BrandMention` row and cannot appear here however often an engine named it.
    The grid is therefore bounded by competitor detection, exactly as Rankings
    is, and a client with no detected set has no rivals to be absent against —
    which reads as `no_brands`, not as coverage. `subject_citable` states the
    equivalent limit for the citation half.
    """
    return (
        select(
            EngineResult.prompt_id,
            BrandMention.entity_name,
            func.min(BrandMention.entity_domain).label("domain"),
            func.bool_or(BrandMention.is_subject).label("is_subject"),
            func.count().label("named_on"),
        )
        .join(EngineResult, EngineResult.id == BrandMention.engine_result_id)
        .where(
            EngineResult.scan_id == scan_id,
            EngineResult.status.in_(ANSWERED),
        )
        .group_by(EngineResult.prompt_id, BrandMention.entity_name)
    )


def _cited_prompts(scan_id: str) -> Select[tuple[str]]:
    """Prompts where some engine cited the SUBJECT's own domain."""
    return (
        select(EngineResult.prompt_id)
        .join(Citation, Citation.engine_result_id == EngineResult.id)
        .where(
            EngineResult.scan_id == scan_id,
            EngineResult.status.in_(ANSWERED),
            Citation.cites_subject.is_(True),
        )
        .group_by(EngineResult.prompt_id)
    )


async def _rival_rollup(
    session: AsyncSession, client_id: str, *, limit: int = 12
) -> list[AnswerGapRivalOut]:
    """Rivals that won answers this client was absent from, across ALL scans.

    This is the cross-scan recurrence the brief asked for, on the only axis
    that survives between scans. `EngineResult.mentioned` is false for exactly
    the answers where the subject was not named, so the join below counts
    "somebody else was named here and we were not" directly.

    Subject rows are excluded by `is_subject`, not by name comparison: a client
    whose brand name was corrected between scans would otherwise start counting
    as its own rival.
    """
    rows = (
        await session.execute(
            select(
                BrandMention.entity_name,
                func.min(BrandMention.entity_domain).label("domain"),
                func.count().label("answers_won"),
                func.count(func.distinct(EngineResult.scan_id)).label("scans_present"),
            )
            .join(EngineResult, EngineResult.id == BrandMention.engine_result_id)
            .join(Scan, Scan.id == EngineResult.scan_id)
            .where(
                Scan.client_id == client_id,
                Scan.status.in_(GRIDDABLE),
                EngineResult.status.in_(ANSWERED),
                EngineResult.mentioned.is_(False),
                BrandMention.is_subject.is_(False),
            )
            .group_by(BrandMention.entity_name)
            .order_by(func.count().desc(), BrandMention.entity_name)
            .limit(limit)
        )
    ).all()
    return [
        AnswerGapRivalOut(
            name=name, domain=domain, answers_won=won, scans_present=scans
        )
        for name, domain, won, scans in rows
    ]


def _classify(
    *,
    engines_answered: int,
    subject_named_on: int,
    rivals_named_on: int,
    subject_cited: bool,
    subject_citable: bool,
) -> GapKind:
    """One prompt's verdict. Precedence is `GapKind`'s declaration order.

    The `no_brands` branch comes FIRST among the non-covered cases and that
    ordering is the module's whole point: a prompt no engine answered with any
    brand at all is not a loss to anybody, and reporting it as one would
    overstate the gap by nearly 2x on the real rows measured.
    """
    if engines_answered == 0:
        return GapKind.UNANSWERED
    if subject_named_on == 0:
        # Absent — but only if somebody actually won it. With no brands named
        # anywhere there was nothing to be absent from.
        return GapKind.ABSENT if rivals_named_on > 0 else GapKind.NO_BRANDS
    if subject_named_on < engines_answered:
        return GapKind.PARTIAL
    # Named everywhere. The only remaining gap is a citation one, and it is
    # claimable only where the scan proves the domain is citable at all.
    if subject_citable and not subject_cited:
        return GapKind.UNCITED
    return GapKind.COVERED


async def build_answer_gaps(
    session: AsyncSession,
    client: Client,
    *,
    scan_id: str | None = None,
    max_brand_columns: int = MAX_BRAND_COLUMNS,
) -> AnswerGapsOut | None:
    """The gap grid for one scan of one client, plus the cross-scan rollup.

    Returns None when the client has no scan carrying a grid — the caller turns
    that into an empty screen rather than a 404, because a client with no scans
    is a normal state and not a missing resource.

    `scan_id` selects a specific scan; without it the newest griddable one is
    used. A scan id belonging to another client yields None for the same reason
    every other client-scoped read 404s rather than 403s: the endpoint must not
    reveal that an id exists elsewhere.
    """
    scans = (
        (
            await session.execute(
                select(Scan)
                .where(Scan.client_id == client.id, Scan.status.in_(GRIDDABLE))
                .order_by(Scan.created_at.desc(), Scan.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if not scans:
        return None

    if scan_id is None:
        scan = scans[0]
    else:
        scan = next((s for s in scans if s.id == scan_id), None)
        if scan is None:
            return None

    prompts = (
        (
            await session.execute(
                select(Prompt)
                .join(PromptSet, PromptSet.id == Prompt.prompt_set_id)
                .where(PromptSet.scan_id == scan.id)
                .order_by(Prompt.position.asc())
            )
        )
        .scalars()
        .all()
    )
    if not prompts:
        return None

    # --- the four grouped reads -------------------------------------------
    engines_by_prompt: dict[str, int] = defaultdict(int)
    no_brand_by_prompt: dict[str, int] = defaultdict(int)
    engines_seen: set[str] = set()
    for prompt_id, engine, _mentioned, brands in (
        await session.execute(_result_rows(scan.id))
    ).all():
        engines_by_prompt[prompt_id] += 1
        engines_seen.add(engine.value)
        if brands == 0:
            no_brand_by_prompt[prompt_id] += 1

    named: dict[str, dict[str, int]] = defaultdict(dict)
    brand_domain: dict[str, str | None] = {}
    subject_names: set[str] = set()
    answers_named: dict[str, int] = defaultdict(int)
    prompts_named: dict[str, int] = defaultdict(int)
    for prompt_id, name, domain, is_subject, count in (
        await session.execute(_mention_rows(scan.id))
    ).all():
        named[prompt_id][name] = count
        brand_domain.setdefault(name, domain)
        if is_subject:
            subject_names.add(name)
        answers_named[name] += count
        prompts_named[name] += 1

    cited = {
        row[0] for row in (await session.execute(_cited_prompts(scan.id))).all()
    }
    # The evidence test for a citation gap. See the module note.
    subject_citable = bool(cited)

    # --- brand columns -----------------------------------------------------
    # The subject first and unconditionally: a grid whose own client dropped out
    # of the columns because rivals outranked it would hide the finding.
    subject_label = client.brand_name or client.name
    subject_column = next(iter(sorted(subject_names)), subject_label)

    rivals_ranked = sorted(
        (n for n in answers_named if n not in subject_names),
        key=lambda n: (-answers_named[n], n),
    )
    columns = [subject_column, *rivals_ranked[: max(0, max_brand_columns - 1)]]

    brands = [
        AnswerGapBrandOut(
            name=name,
            domain=brand_domain.get(name)
            if name != subject_column
            else (brand_domain.get(name) or client.domain),
            is_subject=name == subject_column,
            answers_named=answers_named.get(name, 0),
            prompts_named=prompts_named.get(name, 0),
        )
        for name in columns
    ]

    # --- rows --------------------------------------------------------------
    rows: list[AnswerGapRowOut] = []
    totals: dict[GapKind, int] = defaultdict(int)
    for prompt in prompts:
        per_brand = named.get(prompt.id, {})
        engines_answered = engines_by_prompt.get(prompt.id, 0)
        subject_named_on = sum(n for b, n in per_brand.items() if b in subject_names)
        rivals_named_on = max(
            (n for b, n in per_brand.items() if b not in subject_names), default=0
        )
        subject_cited = prompt.id in cited

        kind = _classify(
            engines_answered=engines_answered,
            subject_named_on=subject_named_on,
            rivals_named_on=rivals_named_on,
            subject_cited=subject_cited,
            subject_citable=subject_citable,
        )
        totals[kind] += 1

        rows.append(
            AnswerGapRowOut(
                prompt_id=prompt.id,
                text=prompt.text,
                intent=prompt.intent.value,
                position=prompt.position,
                engines_answered=engines_answered,
                subject_named_on=subject_named_on,
                rivals_named_on=rivals_named_on,
                no_brand_on=no_brand_by_prompt.get(prompt.id, 0),
                subject_cited=subject_cited,
                kind=kind,
                # Recurrence: engines that answered without naming this client.
                # Zero when covered, and never negative.
                absent_on=max(0, engines_answered - subject_named_on),
                cells=[
                    AnswerGapCellOut(brand=name, named_on=per_brand.get(name, 0))
                    for name in columns
                ],
            )
        )

    return AnswerGapsOut(
        client_id=client.id,
        name=client.brand_name or client.name,
        domain=client.domain,
        scan_id=scan.id,
        scanned_at=scan.finished_at or scan.created_at,
        available_scan_ids=[s.id for s in scans],
        engines=sorted(engines_seen),
        brands=brands,
        rows=rows,
        rivals=await _rival_rollup(session, client.id),
        prompts=len(rows),
        absent=totals[GapKind.ABSENT],
        partial=totals[GapKind.PARTIAL],
        uncited=totals[GapKind.UNCITED],
        covered=totals[GapKind.COVERED],
        no_brands=totals[GapKind.NO_BRANDS],
        unanswered=totals[GapKind.UNANSWERED],
        subject_citable=subject_citable,
    )


__all__ = ["build_answer_gaps", "GRIDDABLE", "MAX_BRAND_COLUMNS"]

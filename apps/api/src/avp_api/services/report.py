"""Report assembly — Epic 7.

Reads what Epics 2-6 persisted and projects it into the shape the narrative
report needs. **Nothing here computes a score, detects a competitor, or audits a
site.** If a number in the report is not already in a table, it is an aggregate
(a count, a share, an ordinal comparison) over rows that are.

Determinism: every collection is sorted by an explicit total key before it is
returned, for the same reason scoring-spec.md rule 1 requires it — a report that
reorders its own evidence between two loads of identical data is not a document
anyone can be shown twice.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    ActionItem,
    ActionItemStatus,
    Agency,
    Client,
    Competitor,
    CompetitorSet,
    Engine,
    EngineResult,
    EngineResultStatus,
    Prompt,
    Scan,
    Score,
    TechnicalAudit,
)
from ..models.score import DEFAULT_WEIGHTS, DIMENSION_KEYS
from ..models.technical_audit import CheckStatus
from ..schemas.action_item import ActionItemOut
from ..schemas.audit import AuditCheckOut
from ..schemas.report import (
    CitedDomainOut,
    CrossEngineOut,
    EngineCoverageOut,
    EngineStandingOut,
    MentionShareOut,
    PromptShelfOut,
    ReportAgencyOut,
    ReportAuditFindingOut,
    ReportAuditOut,
    ReportCompetitorOut,
    ReportCompetitorSetOut,
    ReportDimensionOut,
    ReportOut,
    ReportProofOut,
    ReportSubjectOut,
    ShelfSlotOut,
    SplitPromptOut,
)
from ..schemas.score import CompetitorScoreOut, ScoreDetailOut
from . import divergence, scoring_runner
from .scoring import ResultFacts

# Heaviest dimension first. The Luminance Ledger stacks the heaviest segment at
# the BOTTOM and this list feeds it directly, so the order is part of the
# visualisation's contract, not a display preference. Derived from the §6 table
# rather than hardcoded, so a future per-industry weight set reorders itself.
DIMENSION_ORDER: tuple[str, ...] = tuple(
    sorted(DIMENSION_KEYS, key=lambda k: (-DEFAULT_WEIGHTS[k], k))
)

# Cap on how many cited domains the proof beat carries. A 24-prompt scan cites
# several hundred; a report that lists them all stops being an argument. The
# cap is applied AFTER ranking (see rank_domains), so it keeps the strongest
# evidence rather than an arbitrary slice.
MAX_CITED_DOMAINS = 12

# Cap on Answer Shelf rows (Epic 7.1, Direction A). design-direction.md §5
# flags the risk itself: "needs a sensible cap and grouping past ~40 prompts."
# A full scan is 24 prompts x 2 engines = 48 rows, so the cap bites on the
# largest scans only, and it keeps whole prompts rather than half a prompt's
# engines — a row missing one engine reads as that engine not answering.
MAX_SHELF_PROMPTS = 20

# A domain cited once by one answer is a coincidence, not a content gap worth
# putting in front of a client as a recommendation. Two is the smallest number
# that can show a pattern.
MIN_UNCLAIMED_CITATIONS = 2

# How many unclaimed domains the fix beat is given to name. The fix names the
# heaviest and lists the runners-up; past three it stops being a plan.
MAX_UNCLAIMED_DOMAINS = 3

# What counts as the engine having ANSWERED.
#
# `ANSWERED_NO_MENTION` is not a failure. `EngineResult`'s own comment says so:
# "the engine answered but the brand was absent. Distinct from ERROR: a
# confirmed absence is a valid, scoreable data point; an error is not."
#
# Epic 7.0 read `status is OK` here, which folded every confirmed absence in
# with the timeouts. On a scan where the subject is named in 3 of 6 answers
# that made the proof beat report "Answered 3 of 6" and "Named <subject> 3 of
# 3" — a 100% mention rate on a subject named half the time — and dropped the
# citations and rival mentions carried by the other three answers, which are
# the most damning evidence the beat has. It never showed on the Help Scout
# fixture because that scan names the subject in all six answers, so no row
# ever carried this status. Found while building the Answer Shelf (Epic 7.1),
# whose entire purpose is to draw those absences.
ANSWERED_STATUSES = (EngineResultStatus.OK, EngineResultStatus.ANSWERED_NO_MENTION)


async def build_report(session: AsyncSession, scan: Scan) -> ReportOut:
    """Assemble the full report projection for one scan."""
    client = (await session.execute(select(Client).where(Client.id == scan.client_id))).scalar_one()
    agency = (await session.execute(select(Agency).where(Agency.id == scan.agency_id))).scalar_one()

    score_row = await scoring_runner.latest_score(session, scan.id)
    competitor_set = await _load_competitor_set(session, scan.id)
    audit = await _load_audit(session, scan.id)
    results = await _load_results(session, scan.id)
    prompts = await _load_prompts(session, results)
    action_items = await _load_action_items(session, scan.id)

    # The competitor comparison is recomputed from persisted rows on read, the
    # same way GET /scans/{id}/score does it — it is a pure function of data
    # already stored, and a second stored copy could fall out of step.
    comparisons: list[Any] = []
    if score_row is not None:
        _row, _computed, comparisons = await scoring_runner.score_scan(session, scan, persist=False)

    return ReportOut(
        scan_id=scan.id,
        scan_status=scan.status.value,
        generated_at=datetime.now(UTC),
        scanned_at=scan.finished_at or scan.started_at,
        agency=ReportAgencyOut(
            id=agency.id,
            name=agency.name,
            slug=agency.slug,
            logo_url=agency.logo_url,
            accent_color=agency.accent_color,
        ),
        subject=ReportSubjectOut(
            client_id=client.id,
            name=client.name,
            domain=client.domain,
            industry=client.industry,
            industry_niche=client.industry_niche,
            brand_name=client.brand_name,
        ),
        score=_score_out(score_row, comparisons),
        dimensions=_dimensions(score_row),
        competitor_set=_competitor_set_out(competitor_set, comparisons),
        proof=_proof(results, competitor_set, prompts),
        audit=_audit_out(audit),
        action_items=_action_items_out(action_items),
    )


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


async def _load_competitor_set(session: AsyncSession, scan_id: str) -> CompetitorSet | None:
    return (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan_id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()


async def _load_audit(session: AsyncSession, scan_id: str) -> TechnicalAudit | None:
    return (
        await session.execute(
            select(TechnicalAudit)
            .where(TechnicalAudit.scan_id == scan_id)
            .options(selectinload(TechnicalAudit.checks))
        )
    ).scalar_one_or_none()


async def _load_results(session: AsyncSession, scan_id: str) -> list[EngineResult]:
    rows = (
        (
            await session.execute(
                select(EngineResult)
                .where(EngineResult.scan_id == scan_id)
                .options(
                    selectinload(EngineResult.citations),
                    selectinload(EngineResult.brand_mentions),
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda r: r.id)


async def _load_prompts(session: AsyncSession, results: list[EngineResult]) -> dict[str, Prompt]:
    """The generated questions behind the results, for the Answer Shelf's rows.

    `EngineResult` carries `prompt_id` but no relationship, so this is one
    extra SELECT keyed by the ids already in hand rather than a model change —
    and one query, not one per row.
    """
    prompt_ids = {r.prompt_id for r in results}
    if not prompt_ids:
        return {}
    rows = (
        (await session.execute(select(Prompt).where(Prompt.id.in_(prompt_ids)))).scalars().all()
    )
    return {row.id: row for row in rows}


async def _load_action_items(session: AsyncSession, scan_id: str) -> list[ActionItem]:
    """Epic 8's generated fix list, in rank order.

    Read, not generated. The module contract above — "nothing here computes a
    score, detects a competitor, or audits a site" — extends to this: the fix
    list is produced by POST /scans/{id}/fixes and merely projected here, so
    loading a report stays a read and never spends a model call.
    """
    rows = (
        (await session.execute(select(ActionItem).where(ActionItem.scan_id == scan_id)))
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda r: (r.rank, r.id))


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------


def _score_out(row: Score | None, comparisons: list[Any]) -> ScoreDetailOut | None:
    if row is None:
        return None
    out = ScoreDetailOut.model_validate(row)
    out.competitors = [
        CompetitorScoreOut(
            competitor_id=c.competitor_id,
            name=c.name,
            mention_rate=c.mention_rate,
            share_of_voice=c.share_of_voice,
            citation_strength=c.citation_strength,
        )
        for c in comparisons
    ]
    return out


def _dimensions(row: Score | None) -> list[ReportDimensionOut]:
    """The §6 dimensions as the ledger needs them, heaviest first.

    An excluded dimension keeps its place in the list with `included: false` and
    a null sub-score. Dropping it would leave the report unable to say WHY a
    dimension is missing, and emitting it as zero would assert something the
    scoring engine deliberately refused to assert.
    """
    if row is None:
        return []

    weights: dict[str, Any] = row.weights or {}
    excluded: dict[str, str] = row.excluded_dimensions or {}

    out: list[ReportDimensionOut] = []
    for key in DIMENSION_ORDER:
        reason = excluded.get(key)
        included = reason is None
        subscore = getattr(row, key) if included else None
        # Effective weights are string-encoded decimals in JSONB; fall back to
        # the §6 table only for a dimension the stored weights never mentioned.
        raw_weight = weights.get(key, DEFAULT_WEIGHTS.get(key, 0))
        out.append(
            ReportDimensionOut(
                key=key,
                weight=Decimal(str(raw_weight)),
                subscore=subscore,
                included=included,
                exclusion_reason=reason,
            )
        )
    return out


def _competitor_set_out(
    competitor_set: CompetitorSet | None, comparisons: list[Any]
) -> ReportCompetitorSetOut | None:
    if competitor_set is None:
        return None

    by_id = {c.competitor_id: c for c in comparisons}
    competitors: list[ReportCompetitorOut] = []
    for competitor in sorted(competitor_set.active_competitors, key=_competitor_key):
        comparison = by_id.get(competitor.id)
        competitors.append(
            ReportCompetitorOut(
                **_competitor_facts(competitor),
                mention_rate=comparison.mention_rate if comparison else None,
                share_of_voice=comparison.share_of_voice if comparison else None,
                citation_strength=comparison.citation_strength if comparison else None,
            )
        )

    return ReportCompetitorSetOut(
        status=competitor_set.status.value,
        detection_confidence=competitor_set.detection_confidence,
        confidence_covers=len(competitor_set.detected_competitors),
        competitors=competitors,
    )


def _competitor_key(c: Competitor) -> tuple[int, str]:
    """Rank, then id. Rank alone is not total — ties would reorder per load."""
    return (c.rank, c.id)


def _competitor_facts(c: Competitor) -> dict[str, Any]:
    """Name, domain and provenance. There is no description field to copy."""
    return {
        "id": c.id,
        "name": c.name,
        "domain": c.domain,
        "rank": c.rank,
        "detection_source": c.detection_source,
        "serp_mentions": c.serp_mentions,
        "co_citation_mentions": c.co_citation_mentions,
        "corroborated": c.corroborated,
        "signal_count": c.signal_count,
        "score": c.score,
        "is_manual_override": c.is_manual_override,
    }


def _proof(
    results: list[EngineResult],
    competitor_set: CompetitorSet | None,
    prompts: dict[str, Prompt] | None = None,
) -> ReportProofOut:
    """Aggregate the engine results into the evidence beat's raw material.

    Counts, domains, ordinals. Nothing else is available to aggregate —
    `engine_results` has no column capable of holding an answer.
    """
    answered = [r for r in results if r.status in ANSWERED_STATUSES]
    competitor_names = {
        c.id: c.name for c in (competitor_set.active_competitors if competitor_set else [])
    }

    # --- engine coverage --------------------------------------------------
    coverage: dict[Any, dict[str, int]] = defaultdict(
        lambda: {"prompts_run": 0, "answered": 0, "mentioned": 0}
    )
    for r in results:
        bucket = coverage[r.engine]
        bucket["prompts_run"] += 1
        if r.status in ANSWERED_STATUSES:
            bucket["answered"] += 1
            if r.mentioned:
                bucket["mentioned"] += 1

    engine_coverage = [
        EngineCoverageOut(engine=engine, **counts)
        for engine, counts in sorted(coverage.items(), key=lambda kv: kv[0].value)
    ]

    # --- citations --------------------------------------------------------
    # Grouped by domain. The URL is kept as a LINK OUT — the sanctioned way to
    # evidence a source without reproducing it (ip-safety.md #7).
    domains: dict[tuple[str, bool, str | None], dict[str, Any]] = {}
    total_citations = 0
    for r in answered:
        for citation in sorted(r.citations, key=lambda c: c.id):
            total_citations += 1
            competitor_name = competitor_names.get(citation.competitor_id or "")
            key = (citation.source_domain, citation.cites_subject, competitor_name)
            entry = domains.setdefault(
                key,
                {
                    "domain": citation.source_domain,
                    "citations": 0,
                    "cites_subject": citation.cites_subject,
                    "competitor_name": competitor_name,
                    "sample_url": citation.source_url,
                },
            )
            entry["citations"] += 1

    def rank_domains(entries: list[dict[str, Any]]) -> list[CitedDomainOut]:
        """Rank the evidence, then cap it.

        A citation attributed to a DETECTED COMPETITOR sorts above an
        unattributed third-party domain regardless of count. Found on the real
        Help Scout scan: zendesk.com and front.com were each cited once, and a
        pure count ranking pushed both off the end of a list otherwise full of
        review blogs — dropping exactly the evidence the proof beat exists to
        show, which is who is being cited instead of the subject.

        Within each group: most-cited first, domain name as a total tie-break so
        two reads of identical data never reorder.
        """
        entries.sort(
            key=lambda e: (
                0 if e["competitor_name"] else 1,
                -e["citations"],
                e["domain"],
            )
        )
        return [CitedDomainOut(**e) for e in entries[:MAX_CITED_DOMAINS]]

    subject_domains = rank_domains([e for e in domains.values() if e["cites_subject"]])
    competitor_domains = rank_domains([e for e in domains.values() if not e["cites_subject"]])
    subject_citations = sum(e["citations"] for e in domains.values() if e["cites_subject"])

    # --- mention share ----------------------------------------------------
    mentions: dict[str, dict[str, Any]] = {}
    for r in answered:
        for mention in sorted(r.brand_mentions, key=lambda m: m.id):
            entry = mentions.setdefault(
                mention.entity_name,
                {
                    "entity_name": mention.entity_name,
                    "entity_domain": mention.entity_domain,
                    "is_subject": mention.is_subject,
                    "appearances": 0,
                    "best_position": None,
                },
            )
            entry["appearances"] += 1
            if mention.position is not None:
                best = entry["best_position"]
                entry["best_position"] = (
                    mention.position if best is None else min(best, mention.position)
                )

    subject_entry = next((e for e in mentions.values() if e["is_subject"]), None)
    subject_appearances = subject_entry["appearances"] if subject_entry else 0
    subject_best = subject_entry["best_position"] if subject_entry else None

    shares: list[MentionShareOut] = []
    for entry in mentions.values():
        shares.append(
            MentionShareOut(
                **entry,
                outranks_subject=_outranks(entry, subject_appearances, subject_best),
            )
        )
    # Most-mentioned first; name as the tie-break so the order is total.
    shares.sort(key=lambda m: (-m.appearances, m.entity_name))

    # --- unclaimed cited domains (Epic 7.1, Direction C) -------------------
    # Cited, and attributable to NEITHER the subject NOR a detected competitor.
    # Computed from the full `domains` dict, BEFORE rank_domains' evidence
    # ordering and its cap: the proof table deliberately promotes
    # competitor-attributed domains above unattributed ones, which is right for
    # evidence and exactly wrong for this — the whole point is the domain
    # nobody owns that is out-citing everyone.
    unclaimed = [
        e
        for e in domains.values()
        if not e["cites_subject"]
        and e["competitor_name"] is None
        and e["citations"] >= MIN_UNCLAIMED_CITATIONS
    ]
    unclaimed.sort(key=lambda e: (-e["citations"], e["domain"]))
    unclaimed_domains = [CitedDomainOut(**e) for e in unclaimed[:MAX_UNCLAIMED_DOMAINS]]

    # --- the Answer Shelf (Epic 7.1, Direction A) --------------------------
    prompt_rows = prompts or {}
    # Which prompts make the cut, in the order the operator sees them. Whole
    # prompts, so a row is never missing one of its engines.
    ordered_prompt_ids = sorted(
        {r.prompt_id for r in results if r.prompt_id in prompt_rows},
        key=lambda pid: (prompt_rows[pid].position, pid),
    )[:MAX_SHELF_PROMPTS]
    kept = set(ordered_prompt_ids)
    rank = {pid: i for i, pid in enumerate(ordered_prompt_ids)}

    shelf: list[PromptShelfOut] = []
    for r in sorted(
        (r for r in results if r.prompt_id in kept),
        key=lambda r: (rank[r.prompt_id], r.engine.value),
    ):
        prompt = prompt_rows[r.prompt_id]
        answered_row = r.status in ANSWERED_STATUSES

        # Which entities this ANSWER cited, by each route attribution offers.
        subject_cited = any(c.cites_subject for c in r.citations)
        cited_competitor_ids = {c.competitor_id for c in r.citations if c.competitor_id}
        cited_domains = {c.source_domain for c in r.citations}

        slots: list[ShelfSlotOut] = []
        if answered_row:
            # Stored ordinals only. A mention with no recorded position cannot
            # be placed on a shelf whose entire meaning is the order, and
            # inventing one would put a fabricated ordinal on a client's page.
            # The subject is unaffected: `subject_present` below reads the
            # authoritative `mentioned` flag, never the length of this list, so
            # a positionless mention can never render as an absence.
            for m in sorted(
                (m for m in r.brand_mentions if m.position is not None),
                key=lambda m: (m.position, m.entity_name),
            ):
                if m.is_subject:
                    cited = subject_cited
                elif m.competitor_id:
                    cited = m.competitor_id in cited_competitor_ids
                elif m.entity_domain:
                    cited = m.entity_domain in cited_domains
                else:
                    cited = False
                slots.append(
                    ShelfSlotOut(
                        position=m.position,
                        entity_name=m.entity_name,
                        entity_domain=m.entity_domain,
                        is_subject=m.is_subject,
                        competitor_name=competitor_names.get(m.competitor_id or ""),
                        cited=cited,
                    )
                )

        shelf.append(
            PromptShelfOut(
                prompt_id=r.prompt_id,
                prompt_text=prompt.text,
                prompt_position=prompt.position,
                engine=r.engine,
                answered=answered_row,
                # The authoritative flag, not len(slots). An answer we could
                # not parse fully must not read as "you were not named".
                subject_present=answered_row and r.mentioned,
                subject_position=r.position if answered_row and r.mentioned else None,
                subject_cited=answered_row and subject_cited,
                slots=slots,
            )
        )

    # --- the cross-engine reading — Epic 9.23 -----------------------------
    #
    # Built from the rows already in hand rather than a second query, and
    # through `scoring.ResultFacts` rather than a value object of its own, so
    # the per-engine mention rate and sentiment come out of the SAME functions
    # the composite is built from. Two implementations of one number
    # eventually disagree, and the disagreement reaches a client.
    facts = [
        ResultFacts(
            result_id=r.id,
            prompt_id=r.prompt_id,
            engine=r.engine.value,
            status=r.status,
            mentioned=r.mentioned,
            sentiment=r.sentiment,
        )
        for r in results
    ]
    reading = divergence.analyse(facts)
    cross_engine = CrossEngineOut(
        standings=[
            EngineStandingOut(
                engine=Engine(s.engine),
                answered=s.answered,
                mentioned=s.mentioned,
                mention_rate=s.mention_rate.quantize(Decimal("0.01")),
                sentiment=(
                    s.sentiment.quantize(Decimal("0.01"))
                    if s.sentiment is not None
                    else None
                ),
            )
            for s in reading.standings
        ],
        splits=[
            SplitPromptOut(
                prompt_id=split.prompt_id,
                named_by=[Engine(e) for e in split.named_by],
                missed_by=[Engine(e) for e in split.missed_by],
            )
            for split in reading.splits
        ],
        comparable_prompts=reading.comparable_prompts,
        agreement_rate=reading.agreement_rate,
    )

    return ReportProofOut(
        prompts_run=len({r.prompt_id for r in results}),
        engine_results=len(results),
        answered_results=len(answered),
        results_mentioning_subject=sum(1 for r in answered if r.mentioned),
        engine_coverage=engine_coverage,
        cross_engine=cross_engine,
        total_citations=total_citations,
        subject_citations=subject_citations,
        subject_cited_domains=subject_domains,
        competitor_cited_domains=competitor_domains,
        mention_shares=shares,
        unclaimed_cited_domains=unclaimed_domains,
        prompt_shelf=shelf,
    )


def _outranks(entry: dict[str, Any], subject_appearances: int, subject_best: int | None) -> bool:
    """Does this brand beat the subject on the evidence actually collected?

    Arithmetic over stored counts and ordinals, not a judgement. More
    appearances wins; on a tie, the better (lower) best position wins. The
    subject never outranks itself.
    """
    if entry["is_subject"]:
        return False
    if entry["appearances"] != subject_appearances:
        return entry["appearances"] > subject_appearances
    position = entry["best_position"]
    if position is None or subject_best is None:
        return False
    return position < subject_best


def _audit_out(audit: TechnicalAudit | None) -> ReportAuditOut | None:
    if audit is None:
        return None

    tally = {status: 0 for status in CheckStatus}
    findings: list[ReportAuditFindingOut] = []
    for check in sorted(audit.checks, key=lambda c: c.check_key):
        tally[check.status] += 1
        if check.status in (CheckStatus.WARN, CheckStatus.FAIL, CheckStatus.ERROR):
            findings.append(
                ReportAuditFindingOut(**AuditCheckOut.model_validate(check).model_dump())
            )

    # Failures before warnings — the fix beat reads in severity order.
    severity = {CheckStatus.FAIL: 0, CheckStatus.ERROR: 1, CheckStatus.WARN: 2}
    findings.sort(key=lambda f: (severity.get(f.status, 3), f.check_key))

    return ReportAuditOut(
        status=audit.status,
        error_code=audit.error_code,
        url_audited=audit.url_audited,
        technical_foundation=audit.technical_foundation,
        audited_at=audit.audited_at,
        passed=tally[CheckStatus.PASS],
        warned=tally[CheckStatus.WARN],
        failed=tally[CheckStatus.FAIL] + tally[CheckStatus.ERROR],
        not_applicable=tally[CheckStatus.NOT_APPLICABLE],
        findings=findings,
    )


def _action_items_out(rows: list[ActionItem]) -> list[ActionItemOut]:
    """The generated fix list as the fix beat needs it.

    Only items still open or in progress. A fix an operator marked done or
    dismissed has left the plan, and a report that keeps listing it is telling
    a client to do work they already decided about.
    """
    return [
        ActionItemOut.model_validate(row)
        for row in rows
        if row.status in (ActionItemStatus.OPEN, ActionItemStatus.IN_PROGRESS)
    ]

"""Scoring persistence — loads facts, scores, writes a Score row.

Kept separate from `services/scoring.py` so the scoring functions stay pure:
no session, no I/O, no ORM objects. That separation is what makes the
determinism guarantees testable without a database, and it is why a float or an
unsorted iteration can be caught by unit tests at all.

**One Score row per (scan, formula_version)** — enforced by a unique constraint
Epic 1 put on the table, and it is the right shape. scoring-spec.md rule 5 is
about versioning the FORMULA, not about keeping every invocation: because
scoring is deterministic, re-running it under the same formula against the same
inputs produces an identical result, and storing N identical rows would be noise
rather than history. Changing the formula creates a NEW row, which is what
Epic 11's before/after reporting actually reads.

Re-scoring therefore updates the row for the current formula version in place
and leaves rows for other versions untouched. `inputs_digest` records which data
each row was computed from, so a changed composite is always attributable to
either changed inputs or a changed formula.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import ids
from ..models import (
    CompetitorSet,
    EngineResult,
    Prompt,
    Scan,
    Score,
    TechnicalAudit,
)
from ..models.prompt import PromptIntent
from ..models.score import ScoreStatus
from ..models.technical_audit import AuditStatus
from .scoring import (
    CompetitorComparison,
    CompetitorFacts,
    Dimension,
    ResultFacts,
    ScoreResult,
    compute_score,
)

logger = structlog.get_logger(__name__)


async def load_result_facts(session: AsyncSession, scan_id: str) -> list[ResultFacts]:
    """Read a scan's EngineResults into the value objects scoring consumes.

    Ordered by id so the list handed to scoring is stable regardless of what the
    planner returns. Scoring sorts defensively too, but a stable read makes the
    inputs_digest reproducible even if that ever regressed.

    Reads FACTS ONLY — booleans, labels, entity names, cited domains. There is
    no engine text in the database to read (the facts-only rule); Epic 4 stored a
    digest instead.
    """
    rows = list(
        (
            await session.execute(
                select(EngineResult)
                .where(EngineResult.scan_id == scan_id)
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

    # Prompt intent selects the scoring population for Mention Rate and Share
    # of Voice (scoring.awareness_only), so it has to travel with the facts.
    # One extra query rather than a join or a relationship load: the rows are
    # already in hand, this is a dozen ids, and it leaves the EngineResult
    # query above exactly as Epic 5 wrote it.
    intents: dict[str, PromptIntent] = dict(
        (
            await session.execute(
                select(Prompt.id, Prompt.intent).where(
                    Prompt.id.in_({row.prompt_id for row in rows})
                )
            )
        ).all()
    ) if rows else {}

    facts: list[ResultFacts] = []
    for row in rows:
        brands = tuple(
            sorted((m.entity_name, m.is_subject) for m in row.brand_mentions)
        )
        citations = tuple(
            sorted((c.source_domain, c.cites_subject) for c in row.citations)
        )
        facts.append(
            ResultFacts(
                result_id=row.id,
                prompt_id=row.prompt_id,
                intent=intents.get(row.prompt_id),
                engine=row.engine.value,
                status=row.status,
                mentioned=row.mentioned,
                sentiment=row.sentiment,
                brands=brands,
                citations=citations,
            )
        )
    return facts


async def prior_formula_versions(
    session: AsyncSession, scan_id: str, current: str
) -> list[str]:
    """Formula versions this scan carries OTHER than `current`, oldest first.

    Rule 5 keeps a row per (scan, formula_version), so a re-scored scan has
    history — and a reader looking at a number that moved needs to be told the
    definition moved with it. Ordered by `id`, which is a ULID and therefore
    sorts by when the row was written.
    """
    rows = (
        await session.execute(
            select(Score.formula_version)
            .where(Score.scan_id == scan_id, Score.formula_version != current)
            .order_by(Score.id)
        )
    ).scalars().all()
    return list(dict.fromkeys(rows))


async def load_competitor_facts(
    session: AsyncSession, scan_id: str
) -> tuple[list[CompetitorFacts], CompetitorSet | None]:
    """The scan's competitor set, as facts plus the set itself for its status."""
    competitor_set = (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan_id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()
    if competitor_set is None:
        return [], None

    facts = [
        CompetitorFacts(competitor_id=c.id, name=c.name, domain=c.domain)
        for c in sorted(competitor_set.active_competitors, key=lambda c: c.id)
    ]
    return facts, competitor_set


async def load_technical_foundation(session: AsyncSession, scan_id: str) -> Decimal | None:
    """The scan's Technical Foundation value, or None if it was never audited.

    Returns None — not zero — for a failed audit as well as a missing one. A
    site we could not read is not a site with a bad technical foundation, and
    the two must not collapse into the same number.
    """
    audit = (
        await session.execute(
            select(TechnicalAudit)
            .where(TechnicalAudit.scan_id == scan_id)
            .order_by(TechnicalAudit.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if audit is None or audit.status is AuditStatus.FAILED:
        return None
    return audit.technical_foundation


async def score_scan(
    session: AsyncSession, scan: Scan, *, persist: bool = True
) -> tuple[Score | None, ScoreResult, list[CompetitorComparison]]:
    """Score a scan from its persisted rows and write a Score.

    Returns the ORM row (None when `persist=False`), the computed result, and
    the per-competitor comparison. The comparison is **derived on read** rather
    than stored: it is a pure function of rows already persisted, so storing it
    would create a second copy that can fall out of step with the first.
    """
    results = await load_result_facts(session, scan.id)
    competitors, competitor_set = await load_competitor_facts(session, scan.id)

    # Epic 6 supplies the real value when the scan has a completed audit.
    # Passing None keeps the dimension excluded with NOT_YET_MEASURED, which is
    # still the correct outcome for a scan that was never audited — "we have not
    # checked" rather than "there is nothing there".
    #
    # scoring.py's public contract is unchanged: it has always taken an optional
    # Decimal. Epic 6 produces one; scoring consumes it exactly as before.
    technical_foundation = await load_technical_foundation(session, scan.id)

    computed = compute_score(
        results,
        competitors,
        competitor_set_status=competitor_set.status if competitor_set else None,
        technical_foundation=technical_foundation,
    )

    row: Score | None = None
    if persist:
        # One row per (scan, formula_version) — a unique constraint enforces it.
        # Re-scoring under the same formula refreshes that row; a formula bump
        # inserts alongside it, leaving prior versions intact.
        existing = (
            await session.execute(
                select(Score).where(
                    Score.scan_id == scan.id,
                    Score.formula_version == computed.formula_version,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            row = Score(id=ids.new_id(ids.SCORE), scan_id=scan.id)
            session.add(row)
        else:
            row = existing
        _apply(row, computed)
        await session.flush()

    logger.info(
        "scoring.completed",
        scan_id=scan.id,
        status=computed.status,
        composite=str(computed.composite),
        excluded=sorted(computed.excluded_dimensions),
        flags=computed.degradation_flags,
        inputs_digest=computed.inputs_digest[:16],
        results=len(results),
        competitors=len(competitors),
    )
    return row, computed, computed.competitors


def _apply(row: Score, computed: ScoreResult) -> None:
    """Write a computed result onto a Score row.

    Shared by insert and refresh so the two paths cannot drift — a field added
    here reaches both.
    """
    row.status = (
        ScoreStatus.SCORED if computed.status == "scored" else ScoreStatus.INSUFFICIENT_DATA
    )
    row.composite = computed.composite
    row.mention_rate = computed.value(Dimension.MENTION_RATE)
    row.share_of_voice = computed.value(Dimension.SHARE_OF_VOICE)
    row.citation_strength = computed.value(Dimension.CITATION_STRENGTH)
    row.sentiment = computed.value(Dimension.SENTIMENT)
    row.technical_foundation = computed.value(Dimension.TECHNICAL_FOUNDATION)
    row.formula_version = computed.formula_version
    # The EFFECTIVE weights actually used, not the §6 table — after exclusions
    # they differ, and a stored breakdown that cannot be re-summed against its
    # own weights is not auditable.
    row.weights = {
        d.value: str(sub.weight)
        for d, sub in sorted(computed.sub_scores.items(), key=lambda kv: kv[0].value)
        if sub.included
    }
    row.excluded_dimensions = computed.excluded_dimensions
    row.degradation_flags = computed.degradation_flags
    row.reason_code = computed.reason_code
    row.inputs_digest = computed.inputs_digest
    row.computed_at = datetime.now(UTC)


async def latest_score(session: AsyncSession, scan_id: str) -> Score | None:
    """The most recent Score for a scan.

    Ordered by id descending — ULIDs are time-ordered, so this is the newest
    row without depending on `computed_at`, which two re-scores in the same
    second could tie on.
    """
    return (
        await session.execute(
            select(Score).where(Score.scan_id == scan_id).order_by(Score.id.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def score_history(session: AsyncSession, scan_id: str) -> list[Score]:
    """Every Score computed for a scan, newest first.

    Re-scoring inserts, so this is the audit trail: which formula version
    produced which composite, from which inputs_digest.
    """
    return list(
        (
            await session.execute(
                select(Score).where(Score.scan_id == scan_id).order_by(Score.id.desc())
            )
        )
        .scalars()
        .all()
    )

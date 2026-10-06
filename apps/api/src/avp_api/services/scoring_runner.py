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
from .crawl import ROBOTS_DISALLOWED, ROBOTS_UNREACHABLE
from .domain_authority import CURATED_ONLY, load_domain_authority
from .egress import ADDRESS_NOT_PUBLIC
from .scoring import (
    CompetitorComparison,
    CompetitorFacts,
    Dimension,
    ReadComparison,
    ResultFacts,
    ScoreResult,
    comparison_from_facts,
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
        # Where each brand appeared — for `average_position`, reported and
        # never scored, so deliberately outside the digest.
        brand_positions = tuple(
            sorted(
                (m.entity_name, m.position)
                for m in row.brand_mentions
                if m.position is not None
            )
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
                position=row.position,
                brand_positions=brand_positions,
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


def technical_foundation_reason(status: AuditStatus, error_code: str | None) -> str | None:
    """Why a FAILED audit leaves Technical Foundation out; None for an audit that did not fail.

    The one place the mapping lives, so `scoring.completed` and `audit.completed` cannot
    disagree. A label only: the dimension is excluded whatever it says.
    """
    if status is not AuditStatus.FAILED:
        return None
    if error_code == ROBOTS_DISALLOWED:
        return "CRAWL_DISALLOWED"
    if error_code == ADDRESS_NOT_PUBLIC:
        return "CRAWL_ADDRESS_NOT_PUBLIC"
    if error_code == ROBOTS_UNREACHABLE:
        return "CRAWL_POLICY_UNREADABLE"
    return "AUDIT_FAILED"


async def load_technical_foundation(
    session: AsyncSession, scan_id: str
) -> tuple[Decimal | None, str | None]:
    """The scan's Technical Foundation value, and why it is missing if it is.

    Returns None — not zero — for a failed audit as well as a missing one. A
    site we could not read is not a site with a bad technical foundation, and
    the two must not collapse into the same number.

    The reason is None (NOT_YET_MEASURED, `compute_score`'s default: no audit row)
    except for an audit that FAILED (`technical_foundation_reason`: AUDIT_FAILED unless
    one of the three below), and for an audit that robots.txt kept off the page: CRAWL_DISALLOWED
    (2026-09-29). Stored as a failed audit with `ROBOTS_DISALLOWED`, it is
    excluded like any failed one — its reason is the only difference. So is an
    audit the egress guard refused (`ADDRESS_NOT_PUBLIC`): CRAWL_ADDRESS_NOT_PUBLIC. And
    an audit whose robots.txt could not be read (`ROBOTS_UNREACHABLE`, launch checklist
    3.41): CRAWL_POLICY_UNREADABLE, not CRAWL_DISALLOWED, because the site forbade nothing.
    Every one of them returns None for the value, so the same dimension is excluded in the
    same cases; only the recorded reason differs.
    """
    audit = (
        await session.execute(
            select(TechnicalAudit)
            .where(TechnicalAudit.scan_id == scan_id)
            .order_by(TechnicalAudit.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if audit is None:
        return None, None
    if audit.status is AuditStatus.FAILED:
        return None, technical_foundation_reason(audit.status, audit.error_code)
    return audit.technical_foundation, None


async def score_scan(
    session: AsyncSession, scan: Scan, *, persist: bool = True
) -> tuple[Score | None, ScoreResult, list[CompetitorComparison]]:
    """Score a scan from its persisted rows and write a Score.

    Returns the ORM row (None when `persist=False`), the computed result, and
    the per-competitor comparison. The comparison is **derived on read** rather
    than stored: it is a pure function of rows already persisted, so storing it
    would create a second copy that can fall out of step with the first.

    **A report does not call this to read a score.** `build_report` and
    `GET /scans/{id}/score` take the comparison from `compare_on_read`, which
    builds no Score. Callers that still pass `persist=False` are
    `client_history` (it reads only the per-rival comparison from it; the subject's
    share of voice there is the stored Score's), the `scripts/` measurements and one test.

    **`scoring.completed` says a Score was written, so it is logged only when
    `persist` is true.** It used to be logged for `persist=False` as well, so
    each time a finished scan's report was opened the logs read as the scan
    being scored again (the same `inputs_digest`, `status=scored`).
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
    technical_foundation, technical_foundation_exclusion = await load_technical_foundation(
        session, scan.id
    )

    # v3 — the citing-domain authority lookup. Tier A needs no data; Tier B is
    # a platform-wide aggregate over stored citations (15 ms, measured — see
    # `domain_authority.py`), loaded fresh for every scoring so the digest
    # records the tiers this score was actually computed with.
    authority = await load_domain_authority(session)

    computed = compute_score(
        results,
        competitors,
        competitor_set_status=competitor_set.status if competitor_set else None,
        technical_foundation=technical_foundation,
        technical_foundation_exclusion=technical_foundation_exclusion,
        authority=authority,
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

        # `excluded` is each dimension left out with the reason `compute_score` recorded for
        # it, in dimension order: `{'citation_strength': 'NO_AUTHORITY_DATA'}`, or `{}` when
        # nothing was left out. It was the dimension names alone until launch checklist 3.58,
        # so "why was Citation Strength left out?" took elimination. Both halves are fixed
        # vocabularies (a `Dimension` value, a reason code), never text from an answer or a
        # page, so the line stays facts only.
        logger.info(
            "scoring.completed",
            scan_id=scan.id,
            status=computed.status,
            composite=str(computed.composite),
            excluded=dict(sorted(computed.excluded_dimensions.items())),
            flags=computed.degradation_flags,
            inputs_digest=computed.inputs_digest[:16],
            results=len(results),
            competitors=len(competitors),
        )
    return row, computed, computed.competitors


async def compare_on_read(
    session: AsyncSession, scan: Scan, score: Score
) -> ReadComparison:
    """What a report shows beside a stored Score that the row does not hold — read only.

    The subject's mean position and the per-rival comparison, derived from the
    scan's persisted rows. **Nothing is written and no Score is built**: no
    composite, no sub-scores, no inputs digest, no Technical Foundation read,
    and nothing logged at info (`scoring.completed` is for a Score that was
    written, and opening a report is not that). `build_report` and
    `GET /scans/{id}/score` call it where they used to call
    `score_scan(persist=False)`; `test_reads_are_reads.py` holds the figures
    equal to that call's.

    **What it loads, and what it leaves alone.** The scan's results and its
    competitor set, by the loaders `score_scan` uses, so the facts are the
    same facts. The platform-wide tier aggregate (`load_domain_authority`, one
    statement and the largest single one) only when the set has rivals, since
    tiers are read only to score a rival's Citation Strength. Never the audit:
    Technical Foundation is a sub-score input and the comparison has none.

    **`score` is the row the caller is about to show, and its status decides
    the first thing.** An `insufficient_data` row has no comparison, as before,
    so nothing is loaded for it. `compute_score` made the same call from the
    facts and the audit; the stored status is that call, already made.

    **Which moment the rivals' figures belong to — read this before comparing
    them with the row.** They are computed now, by the current formula, with
    today's platform tiers; the row is as it was when it was scored, under its
    own `formula_version`, with the tiers of that day and the competitor set of
    that day. For a scan scored under the current formula, with nothing the
    comparison reads changed since (the platform's tiers, a set an operator
    corrected), they agree. A row stored under an earlier version (rule 5 keeps
    those and the report shows the latest) or scored before one of those moved
    is shown beside rival figures from a different definition or a different
    day. That is how the comparison has always been derived; this function does
    not change it.
    """
    if score.status is ScoreStatus.INSUFFICIENT_DATA:
        return ReadComparison()

    results = await load_result_facts(session, scan.id)
    competitors, competitor_set = await load_competitor_facts(session, scan.id)
    authority = await load_domain_authority(session) if competitors else CURATED_ONLY
    return comparison_from_facts(
        results,
        competitors,
        competitor_set_status=competitor_set.status if competitor_set else None,
        authority=authority,
    )


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

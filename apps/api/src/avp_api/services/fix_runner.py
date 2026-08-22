"""Fix generation, wired to the database — Epic 8.

`fix_generator` is pure: facts in, recommendations out, no session. This module
is the half that reads what Epics 2-6 persisted, calls it, and writes the
result — the same split as scoring.py/scoring_runner.py and
technical_audit.py/audit_runner.py.

Refresh vs version: refresh in place, keyed on (scan_id, source, source_key)
-----------------------------------------------------------------------------
Three precedents existed and none of them fits unmodified, so the reasoning is
recorded here rather than inferred.

`scoring_runner` versions, one Score row per (scan, formula_version), because
scoring is DETERMINISTIC: re-running under the same formula gives an identical
result, so extra rows would be "noise rather than history", and only a formula
change produces something genuinely new. Neither half of that applies. There is
no formula_version analogue for a fix list, and generation is not
deterministic — the same scan yields differently-worded fixes each run. So
versioning per invocation would accumulate near-identical rows that differ only
in phrasing, and the report would have to pick one arbitrarily. That is Score's
own argument against versioning, in a stronger form.

`audit_runner` is the right frame — "a scan is a point-in-time claim about a
site, so a second audit of the same scan is a correction rather than a new
observation". A regenerated fix list is likewise a better statement of the same
measurement, not a second measurement.

But `audit_runner` implements that by deleting its children and rebuilding
them, and ActionItem is the first re-runnable entity in this codebase carrying
OPERATOR state: `status` moves open -> in_progress -> done -> dismissed as an
agency works the list. Delete-and-rebuild would silently destroy every `done`
an agency had recorded, which is the exact failure `competitors.persist_detection`
exists to prevent ("an operator who has corrected a bad set must not have their
correction silently undone by the next run").

So: the audit's semantics with the competitor precedent's care. Each candidate
upserts onto its own row, carrying `status` and the row id across. A candidate
that has disappeared — the client fixed it and re-audited — is deleted only
while nobody has touched it; once it carries operator state it is kept as the
record of that work. A kept row no longer matches any candidate the client
derives, so it simply does not merge and does not render.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import ids
from ..config import Settings
from ..models import (
    ActionItem,
    ActionItemStatus,
    Client,
    CompetitorSet,
    EngineResult,
    EngineResultStatus,
    Scan,
    Score,
    TechnicalAudit,
)
from ..models.score import DEFAULT_WEIGHTS, DIMENSION_KEYS, ScoreStatus
from ..models.technical_audit import CheckStatus
from . import fix_generator, scoring_runner
from .fix_generator import DimensionFact, FixCandidate, FixFacts, FixOutcome, PersistableFix

logger = structlog.get_logger(__name__)

# How many cited domains the generator is told about. Enough to see where the
# authority actually sits, short enough that the list stays a signal.
MAX_CITED_DOMAINS = 8
MAX_COMPETITORS = 5

# Severity order for audit findings, matching services/report.py's `_audit_out`:
# failures before warnings, because the fix beat reads in severity order.
_SEVERITY = {CheckStatus.FAIL: 0, CheckStatus.ERROR: 1, CheckStatus.WARN: 2}


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


async def _load_audit(session: AsyncSession, scan_id: str) -> TechnicalAudit | None:
    return (
        await session.execute(
            select(TechnicalAudit)
            .where(TechnicalAudit.scan_id == scan_id)
            .options(selectinload(TechnicalAudit.checks))
        )
    ).scalar_one_or_none()


async def _load_competitor_set(session: AsyncSession, scan_id: str) -> CompetitorSet | None:
    return (
        await session.execute(
            select(CompetitorSet)
            .where(CompetitorSet.scan_id == scan_id)
            .options(selectinload(CompetitorSet.competitors))
        )
    ).scalar_one_or_none()


async def _load_results(session: AsyncSession, scan_id: str) -> list[EngineResult]:
    rows = (
        (
            await session.execute(
                select(EngineResult)
                .where(EngineResult.scan_id == scan_id)
                .options(selectinload(EngineResult.citations))
            )
        )
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda r: r.id)


async def latest_fixes(session: AsyncSession, scan_id: str) -> list[ActionItem]:
    """The scan's action items, in rank order.

    Sorted by an explicit total key for the same reason services/report.py
    sorts every collection: a list that reorders itself between two loads of
    identical data is not a document anyone can be shown twice.
    """
    rows = (
        (await session.execute(select(ActionItem).where(ActionItem.scan_id == scan_id)))
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda r: (r.rank, r.id))


# --------------------------------------------------------------------------
# fact collection — every value below is a name, domain, label, code or number
# --------------------------------------------------------------------------


def dimension_facts(score: Score | None) -> list[DimensionFact]:
    """Per-dimension weight, sub-score and gap, heaviest gap first.

    `gap = weight x (100 - subscore) / 100`, in Decimal — scoring-spec.md rule
    3 forbids float in this arithmetic, and the same reason applies to a number
    that decides which fixes get written. The weight normalisation mirrors
    `layoutLedger`, which rescales when the effective weights do not sum to 100.
    """
    if score is None or score.status is not ScoreStatus.SCORED:
        return []

    weights: dict[str, object] = score.weights or {}
    excluded: dict[str, str] = score.excluded_dimensions or {}

    included = [key for key in DIMENSION_KEYS if key not in excluded]
    raw = {key: Decimal(str(weights.get(key, DEFAULT_WEIGHTS.get(key, 0)))) for key in included}
    total = sum(raw.values(), Decimal(0))
    scale = (Decimal(100) / total) if total > 0 else Decimal(0)

    facts: list[DimensionFact] = []
    for key in included:
        subscore = getattr(score, key, None)
        if subscore is None:
            continue
        subscore = Decimal(str(subscore))
        weight = raw[key] * scale
        gap = (weight * (Decimal(100) - subscore) / Decimal(100)).quantize(Decimal("0.01"))
        facts.append(
            DimensionFact(
                key=key,
                weight=weight.quantize(Decimal("0.01")),
                subscore=subscore,
                gap=gap,
            )
        )
    facts.sort(key=lambda d: (-d.gap, d.key))
    return facts


def audit_findings(audit: TechnicalAudit | None) -> list[tuple[str, str, str | None]]:
    """(check_key, status, detail_code) for every check that did not pass."""
    if audit is None:
        return []
    findings = [
        (check.check_key, check.status, check.detail_code)
        for check in audit.checks
        if check.status in (CheckStatus.WARN, CheckStatus.FAIL, CheckStatus.ERROR)
    ]
    findings.sort(key=lambda f: (_SEVERITY.get(f[1], 3), f[0]))
    return [(key, status.value, code) for key, status, code in findings]


async def collect_facts(session: AsyncSession, scan: Scan, client: Client) -> FixFacts:
    """Assemble the facts-only bundle handed to the generator.

    Nothing here reads a column capable of holding third-party prose, because
    no such column exists: Epic 4 stored a digest instead of the answer, and
    Citation has domain and URL but deliberately no title.
    """
    score = await scoring_runner.latest_score(session, scan.id)
    audit = await _load_audit(session, scan.id)
    competitor_set = await _load_competitor_set(session, scan.id)
    results = await _load_results(session, scan.id)

    answered = [r for r in results if r.status is EngineResultStatus.OK]
    citations = [c for r in answered for c in r.citations]
    domains = Counter(c.source_domain for c in citations if not c.cites_subject)

    competitors: list[tuple[str, str]] = []
    if competitor_set is not None:
        for competitor in competitor_set.active_competitors[
            :MAX_COMPETITORS
        ]:
            competitors.append((competitor.name, competitor.domain or "domain unknown"))

    return FixFacts(
        domain=client.domain,
        brand_name=client.brand_name,
        industry=client.industry,
        niche=client.industry_niche,
        composite=score.composite if score is not None else None,
        formula_version=score.formula_version if score is not None else None,
        degradation_flags=sorted(score.degradation_flags or []) if score is not None else [],
        dimensions=dimension_facts(score),
        competitor_names=competitors,
        answers_analysed=len(answered),
        answers_naming_subject=sum(1 for r in answered if r.mentioned),
        citations_total=len(citations),
        citations_to_subject=sum(1 for c in citations if c.cites_subject),
        top_cited_domains=domains.most_common(MAX_CITED_DOMAINS),
        schema_types=sorted(audit.schema_types or []) if audit is not None else [],
        word_count=audit.word_count if audit is not None else None,
        h1_count=audit.h1_count if audit is not None else None,
        indexable=audit.is_indexable if audit is not None else None,
        has_sitemap=audit.has_sitemap if audit is not None else None,
    )


async def build_candidates_for(session: AsyncSession, scan: Scan) -> list[FixCandidate]:
    """The scan's fix candidates, by Epic 7's rules. See fix_generator."""
    score = await scoring_runner.latest_score(session, scan.id)
    audit = await _load_audit(session, scan.id)
    excluded: dict[str, str] = (score.excluded_dimensions or {}) if score is not None else {}
    return fix_generator.build_candidates(dimension_facts(score), audit_findings(audit), excluded)


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def _apply(row: ActionItem, fix: PersistableFix, model: str | None) -> None:
    """Write a generated fix onto an ActionItem row.

    Shared by insert and refresh so the two paths cannot drift — a field added
    here reaches both. `status` is deliberately absent: it belongs to the
    operator, not to the generator.
    """
    row.source = fix.source
    row.source_key = fix.source_key
    row.dimension_key = fix.dimension_key
    row.points_upside = fix.points_upside
    row.rank = fix.rank
    row.title = fix.title
    row.detail = fix.detail
    row.priority = fix.priority
    row.effort = fix.effort
    row.generated_by = model


async def persist_fixes(session: AsyncSession, scan: Scan, outcome: FixOutcome) -> list[ActionItem]:
    """Upsert the generated list onto the scan. See the module docstring."""
    existing = {(row.source, row.source_key): row for row in await latest_fixes(session, scan.id)}
    generated_keys = {(fix.source, fix.source_key) for fix in outcome.fixes}

    for key, row in existing.items():
        if key in generated_keys:
            continue
        if row.status is ActionItemStatus.OPEN:
            # Never touched, no longer measured — nothing to preserve.
            await session.delete(row)
        # Otherwise kept: it is the record of work an operator did or declined.

    rows: list[ActionItem] = []
    for fix in outcome.fixes:
        row = existing.get((fix.source, fix.source_key))
        if row is None:
            row = ActionItem(id=ids.new_id(ids.ACTION_ITEM), scan_id=scan.id)
            _apply(row, fix, outcome.model)
            row.status = ActionItemStatus.OPEN
            session.add(row)
        else:
            _apply(row, fix, outcome.model)
        rows.append(row)

    await session.flush()
    return sorted(rows, key=lambda r: (r.rank, r.id))


async def generate_for_scan(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    *,
    settings: Settings | None = None,
    persist: bool = True,
) -> tuple[list[ActionItem], FixOutcome]:
    """Generate and persist the scan's fix list.

    Returns the persisted rows and the outcome. A failed generation writes
    nothing and leaves any previous list intact — the report falls back to the
    client's deterministic derivation, which is a weaker list, not a wrong one.
    """
    candidates = await build_candidates_for(session, scan)
    facts = await collect_facts(session, scan, client)

    outcome = await fix_generator.generate_fixes(facts, candidates, settings=settings)
    if outcome.status != "generated":
        logger.warning("fixes.not_generated", scan_id=scan.id, reason=outcome.reason_code)
        return (await latest_fixes(session, scan.id) if persist else []), outcome

    if not persist:
        return [], outcome

    rows = await persist_fixes(session, scan, outcome)
    return rows, outcome

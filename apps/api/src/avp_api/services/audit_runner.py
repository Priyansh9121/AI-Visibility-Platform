"""Technical audit persistence — crawl, score, store.

Kept separate from `services/technical_audit.py` so the signal extraction and
normalisation stay pure and testable without a database, matching the
scoring.py / scoring_runner.py split from Epic 5.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import ids
from ..models import AiCrawlerAccess, Client, Scan, TechnicalAudit, TechnicalAuditCheck
from ..models.technical_audit import AuditStatus, CheckStatus
from .ai_crawlers import AccessVerdict
from .technical_audit import AuditOutcome, AuditSignals, audit_site, score_audit

logger = structlog.get_logger(__name__)

_CHECK_STATUS = {
    "pass": CheckStatus.PASS,
    "warn": CheckStatus.WARN,
    "fail": CheckStatus.FAIL,
    "not_applicable": CheckStatus.NOT_APPLICABLE,
    "error": CheckStatus.ERROR,
}


def _audit_status(signals: AuditSignals, outcome: AuditOutcome) -> AuditStatus:
    if not signals.ok or not outcome.scored:
        return AuditStatus.FAILED
    if outcome.excluded_components:
        # Read the page, but some component had no signal to measure. The score
        # is real; it just rests on fewer inputs, and a report should be able to
        # say so rather than presenting it as a complete picture.
        return AuditStatus.PARTIAL
    return AuditStatus.OK


def _apply(row: TechnicalAudit, signals: AuditSignals, outcome: AuditOutcome) -> None:
    """Write signals and verdicts onto an audit row.

    Only FACTS are written: booleans, counts, schema TYPE NAMES, durations and
    a day count. No page copy, no meta description text, no OG tag values, no
    HTML (ip-safety.md #7).
    """
    row.url_audited = signals.url
    row.status = _audit_status(signals, outcome)
    row.error_code = signals.error_code
    row.pages_crawled = 1 if signals.ok else 0

    row.lcp_ms = signals.lcp_ms
    row.inp_ms = signals.inp_ms
    row.cls = signals.cls

    row.schema_types = signals.schema_types
    row.has_organization_schema = signals.has_organization_schema
    row.has_localbusiness_schema = signals.has_localbusiness_schema
    row.has_faq_schema = signals.has_faq_schema
    row.has_product_schema = signals.has_product_schema

    row.is_indexable = signals.is_indexable
    row.robots_allows_crawl = signals.robots_allows_crawl
    row.has_sitemap = signals.has_sitemap
    row.canonical_present = signals.canonical_present

    row.h1_count = signals.h1_count
    row.word_count = signals.word_count
    row.content_age_days = signals.content_age_days

    row.technical_foundation = outcome.score
    row.excluded_components = outcome.excluded_components
    row.audited_at = datetime.now(UTC)


async def run_audit(
    session: AsyncSession, scan: Scan, client: Client
) -> tuple[TechnicalAudit, AuditOutcome]:
    """Audit the client's site and persist the result for this scan.

    One audit per scan, refreshed on re-run. A scan is a point-in-time claim
    about a site, so a second audit of the same scan is a correction rather than
    a new observation.
    """
    signals = await audit_site(f"https://{client.domain}")
    outcome = score_audit(signals)

    row = (
        await session.execute(
            select(TechnicalAudit)
            .where(TechnicalAudit.scan_id == scan.id)
            .options(
                selectinload(TechnicalAudit.checks),
                selectinload(TechnicalAudit.ai_crawler_access),
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = TechnicalAudit(
            id=ids.new_id(ids.TECHNICAL_AUDIT),
            scan_id=scan.id,
            checks=[],
            ai_crawler_access=[],
        )
        # _apply BEFORE the first flush: url_audited is NOT NULL, so flushing a
        # bare row fails the constraint. `checks=[]` at construction also marks
        # the collection loaded, avoiding a lazy load under the async session.
        _apply(row, signals, outcome)
        session.add(row)
        await session.flush()
    else:
        for existing in list(row.checks):
            await session.delete(existing)
        row.checks = []
        # Same replace-don't-merge treatment as the checks, and for the same
        # reason the unique constraint spells out: a re-audit is a CORRECTION
        # of this scan's claim, so last run's verdicts must not survive
        # alongside this one's.
        for stale in list(row.ai_crawler_access):
            await session.delete(stale)
        row.ai_crawler_access = []
        await session.flush()
        _apply(row, signals, outcome)

    for verdict in outcome.checks:
        # Appended through the relationship, not session.add() — the collection
        # was just reassigned and cascades delete-orphan, so a row attached only
        # by foreign key would be deleted as an orphan on flush. Same bug as
        # Epic 3's competitor persistence.
        row.checks.append(
            TechnicalAuditCheck(
                id=ids.new_id(ids.AUDIT_CHECK),
                audit_id=row.id,
                check_key=verdict.key,
                status=_CHECK_STATUS[verdict.status],
                value=verdict.value,
                detail_code=verdict.detail_code,
            )
        )

    for access in signals.ai_crawler_access:
        # Appended through the relationship for the same delete-orphan reason
        # the checks are, above.
        row.ai_crawler_access.append(
            AiCrawlerAccess(
                id=ids.new_id(ids.AI_CRAWLER_ACCESS),
                audit_id=row.id,
                agent_token=access.agent.token,
                # Vendor and purpose are COPIED, not looked up on read. The
                # roster is expected to change; this scan's claim is not.
                # `models/ai_crawler_access.py` carries the argument.
                vendor=access.agent.vendor,
                purpose=access.agent.purpose,
                verdict=access.verdict,
                rule_source=access.source,
                matched_token=access.matched_token,
                disallow_rules=access.disallow_rules,
            )
        )

    await session.flush()
    blocked = sum(
        1 for a in signals.ai_crawler_access if a.verdict is AccessVerdict.BLOCKED
    )
    logger.info(
        "audit.completed",
        ai_crawlers_measured=len(signals.ai_crawler_access),
        ai_crawlers_blocked=blocked,
        scan_id=scan.id,
        domain=client.domain,
        status=row.status.value,
        technical_foundation=str(outcome.score),
        checks=len(outcome.checks),
        excluded=sorted(outcome.excluded_components),
        **signals.redacted(),
    )
    return row, outcome


async def latest_audit(session: AsyncSession, scan_id: str) -> TechnicalAudit | None:
    return (
        await session.execute(
            select(TechnicalAudit)
            .where(TechnicalAudit.scan_id == scan_id)
            .options(selectinload(TechnicalAudit.checks))
            .order_by(TechnicalAudit.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

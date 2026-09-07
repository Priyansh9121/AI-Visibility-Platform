"""AI crawler access policy, projected for one scan — Epic F.

Reads only. No engine call, no model call, no network, no write. Every figure
is an aggregation over `ai_crawler_access` rows the scan's technical audit
already persisted, in the same request that fetched the site's robots.txt.

WHY THE SCAN LIST IS FILTERED ON HAVING AN AUDIT, NOT ON SCAN STATUS
---------------------------------------------------------------------
`answer_gaps` selects scans by `ScanStatus in GRIDDABLE`, because a grid needs
engine results and those only exist for a scan that ran engines. This screen
needs something different and narrower: an audit that actually read robots.txt.

Those are not the same set in either direction. A scan can succeed on every
engine while its audit failed, and — because the audit's side fetch runs before
the page render and has its own error handling — a scan whose audit is FAILED
can still carry a complete set of verdicts. Selecting on status would offer the
operator scans with nothing to show and hide scans that have something.

So the picker lists exactly the scans that have `ai_crawler_access` rows. What
is offered is what can be displayed.
"""

from __future__ import annotations

from collections import defaultdict

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.ai_crawler_access import AiCrawlerAccess
from ..models.client import Client
from ..models.scan import Scan
from ..models.technical_audit import TechnicalAudit
from ..schemas.crawler_access import (
    CrawlerAccessOut,
    CrawlerAccessSummaryOut,
    CrawlerAgentOut,
)
from .ai_crawlers import AccessVerdict, AgentPurpose

logger = structlog.get_logger(__name__)

# Worst first. The screen's default order, and the reason it is defined here
# rather than in the component: an operator opening this screen is looking for
# what is wrong, and a list that opens on fourteen greens with the one red
# below the fold has buried its own finding.
#
# `unknown` sorts above `unspecified` deliberately — "we could not read your
# robots.txt" is a live problem with the measurement, and it must not settle
# below fourteen rows of ordinary silence.
_VERDICT_RANK: dict[AccessVerdict, int] = {
    AccessVerdict.BLOCKED: 0,
    AccessVerdict.UNKNOWN: 1,
    AccessVerdict.UNSPECIFIED: 2,
    AccessVerdict.ALLOWED: 3,
}

# Within a verdict, a SEARCH crawler outranks a TRAINING one — blocking search
# costs citations, blocking training does not. See the schema's note.
_PURPOSE_RANK: dict[AgentPurpose, int] = {
    AgentPurpose.SEARCH: 0,
    AgentPurpose.USER_ACTION: 1,
    AgentPurpose.TRAINING: 2,
}


def _sort_key(row: AiCrawlerAccess) -> tuple[int, int, str]:
    return (
        _VERDICT_RANK.get(row.verdict, 99),
        _PURPOSE_RANK.get(row.purpose, 99),
        row.agent_token.lower(),
    )


async def build_crawler_access(
    session: AsyncSession,
    client: Client,
    *,
    scan_id: str | None = None,
) -> CrawlerAccessOut | None:
    """This client's AI crawler policy as read during one scan.

    Returns None when no scan of this client has ever recorded a policy — the
    caller turns that into an empty screen rather than a 404, because a client
    whose scans predate this feature is a normal state, not a missing resource.
    A `scan_id` belonging to another client also yields None, for the reason
    every client-scoped read here 404s rather than 403s: the endpoint must not
    reveal that an id exists in another agency.
    """
    # Scans that actually carry verdicts, newest first. One statement rather
    # than "list scans, then probe each" — see the module note on why the
    # filter is the audit's rows and not the scan's status.
    scan_rows = (
        await session.execute(
            select(Scan.id, TechnicalAudit.id, func.coalesce(Scan.finished_at, Scan.created_at))
            .join(TechnicalAudit, TechnicalAudit.scan_id == Scan.id)
            .join(AiCrawlerAccess, AiCrawlerAccess.audit_id == TechnicalAudit.id)
            .where(Scan.client_id == client.id)
            .group_by(Scan.id, TechnicalAudit.id, Scan.finished_at, Scan.created_at)
            .order_by(Scan.created_at.desc(), Scan.id.desc())
        )
    ).all()

    if not scan_rows:
        return None

    if scan_id is None:
        chosen = scan_rows[0]
    else:
        chosen = next((r for r in scan_rows if r[0] == scan_id), None)
        if chosen is None:
            return None

    chosen_scan_id, audit_id, scanned_at = chosen

    audit = (
        await session.execute(
            select(TechnicalAudit).where(TechnicalAudit.id == audit_id)
        )
    ).scalar_one()

    rows = (
        (
            await session.execute(
                select(AiCrawlerAccess).where(AiCrawlerAccess.audit_id == audit_id)
            )
        )
        .scalars()
        .all()
    )

    tally: dict[AccessVerdict, int] = defaultdict(int)
    search_blocked = 0
    explicit = 0
    for row in rows:
        tally[row.verdict] += 1
        if row.verdict is AccessVerdict.BLOCKED and row.purpose is AgentPurpose.SEARCH:
            search_blocked += 1
        if row.matched_token is not None and row.matched_token != "*":
            explicit += 1

    agents = [
        CrawlerAgentOut(
            agent=row.agent_token,
            vendor=row.vendor,
            purpose=row.purpose.value,
            verdict=row.verdict.value,
            rule_source=row.rule_source.value,
            matched_token=row.matched_token,
            disallow_rules=row.disallow_rules,
        )
        for row in sorted(rows, key=_sort_key)
    ]

    return CrawlerAccessOut(
        client_id=client.id,
        scan_id=chosen_scan_id,
        scanned_at=scanned_at,
        url_audited=audit.url_audited,
        # Every verdict being UNKNOWN is exactly the shape `unknown_access()`
        # produces, and it is the only way that shape occurs — a parse never
        # yields UNKNOWN. So this is derived rather than stored as a second
        # column that could disagree with the rows it describes.
        robots_readable=tally[AccessVerdict.UNKNOWN] != len(rows),
        agents=agents,
        summary=CrawlerAccessSummaryOut(
            total=len(rows),
            allowed=tally[AccessVerdict.ALLOWED],
            blocked=tally[AccessVerdict.BLOCKED],
            unspecified=tally[AccessVerdict.UNSPECIFIED],
            unknown=tally[AccessVerdict.UNKNOWN],
            search_blocked=search_blocked,
            explicit=explicit,
        ),
        available_scan_ids=[r[0] for r in scan_rows],
    )


__all__ = ["build_crawler_access"]

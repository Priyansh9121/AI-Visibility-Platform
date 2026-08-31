"""A client's scan history, shaped for a trend — Epic 9.20.

WHY THIS EXISTS RATHER THAN N CALLS TO `/scans/{id}/report`
-----------------------------------------------------------
Every figure here is derivable from the report endpoint, one scan at a time,
and that was the first design. Three things ruled it out, all measured against
the real `avp_dev` rows for a three-scan client rather than assumed:

1. **Cost.** `build_report` is ~17.6ms warm per scan against ~6.5ms for the
   scoring pass alone, because it also runs the narrative, the audit rollup and
   the fix list — none of which a trend line reads. Over HTTP that is also N
   round-trips carrying N full report documents to keep two numbers from each.

2. **Truncation.** The report's cited-domain lists are ranked and cut for
   display: 13 rows out of ~300 citations on the scans measured. A trend built
   from them would silently be a trend over "whatever survived the display cap",
   which moves between scans. This reads the citation rows directly.

3. **Ordering.** A trend needs the scans oldest-first and needs to know which
   scans have no point at all. That is a property of the SERIES, not of any one
   report, so nothing in a per-scan response can express it.

WHAT IS AND IS NOT ALREADY STORED — stated because the two halves differ
-----------------------------------------------------------------------
*Sources* reads `engine_result_citations`, which are persisted rows; the COUNT
per domain is an aggregation over them.

*Rankings* has no stored column at all. `CompetitorComparison` is **derived on
read** by design — `scoring_runner.score_scan`'s docstring: "a pure function of
rows already persisted, so storing it would create a second copy that can fall
out of step with the first." So this module calls the same
`scoring_runner.score_scan(persist=False)` the report calls, and cannot disagree
with the report for the same scan. It adds no data and computes no new figure.

This collects nothing. Every row it reads was written by a scan that already
ran, and nothing here writes.
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Client
from ..models.competitor import Competitor, CompetitorSet
from ..models.engine_result import Citation, EngineResult
from ..models.scan import Scan, ScanStatus
from ..models.score import Score
from ..schemas.client_history import (
    ClientHistoryOut,
    HistoryCitedDomainOut,
    HistoryCompetitorOut,
    HistoryScanOut,
)
from . import scoring_runner
from .scoring import Dimension

# Only a scan that actually produced answers can carry a point on a trend.
#
# QUEUED and RUNNING have not measured anything yet; FAILED and CANCELLED never
# will. Plotting them as a gap would be wrong too — they are not a dip, they are
# not a reading at all — so they are excluded from the series entirely and
# counted separately, which is what `scans_without_data` reports.
PLOTTABLE: frozenset[ScanStatus] = frozenset({ScanStatus.SUCCEEDED, ScanStatus.PARTIAL})


def _domain_counts(scan_id: str) -> Select[tuple[str, bool, int]]:
    """Citations per domain for one scan, straight from the persisted rows.

    Grouped in the database rather than in Python: a scan carries ~300 citation
    rows and a client can carry many scans, and there is an index on
    (`engine_result_id`, `source_domain`) already.
    """
    return (
        select(
            Citation.source_domain,
            func.bool_or(Citation.cites_subject).label("cites_subject"),
            func.count().label("citations"),
        )
        .join(EngineResult, EngineResult.id == Citation.engine_result_id)
        .where(EngineResult.scan_id == scan_id)
        .group_by(Citation.source_domain)
        .order_by(func.count().desc(), Citation.source_domain)
    )


async def _competitor_domains(session: AsyncSession, scan_id: str) -> dict[str, str]:
    """domain -> competitor name, for the rivals in this scan's set.

    Suppressed rows are excluded, the same way every other read path excludes
    them (see `Competitor.is_suppressed`): a tombstone is not a competitor.
    """
    rows = (
        await session.execute(
            select(Competitor.domain, Competitor.name)
            .join(CompetitorSet, CompetitorSet.id == Competitor.competitor_set_id)
            .where(
                CompetitorSet.scan_id == scan_id,
                Competitor.is_suppressed.is_(False),
                Competitor.domain.is_not(None),
            )
        )
    ).all()
    return {domain: name for domain, name in rows if domain}


async def build_history(
    session: AsyncSession, client: Client, *, max_domains_per_scan: int = 40
) -> ClientHistoryOut:
    """Every plottable scan for one client, oldest first.

    Oldest first because this is a time series and a chart reads left to right.
    The clients LIST is newest-first for the opposite and equally deliberate
    reason — an operator wants the most recent thing at the top of a list, and
    the earliest thing at the left of a trend.
    """
    scans = (
        (
            await session.execute(
                select(Scan)
                .where(Scan.client_id == client.id)
                .order_by(Scan.created_at.asc(), Scan.id.asc())
            )
        )
        .scalars()
        .all()
    )

    plottable = [s for s in scans if s.status in PLOTTABLE]

    out: list[HistoryScanOut] = []
    for scan in plottable:
        # The SAME call the report makes, so a figure here can never disagree
        # with the same figure on the report for the same scan.
        _row, computed, comparisons = await scoring_runner.score_scan(
            session, scan, persist=False
        )

        by_domain = await _competitor_domains(session, scan.id)
        domain_rows = (await session.execute(_domain_counts(scan.id))).all()

        cited = [
            HistoryCitedDomainOut(
                domain=domain,
                citations=count,
                cites_subject=cites_subject,
                competitor_name=by_domain.get(domain),
            )
            for domain, cites_subject, count in domain_rows[:max_domains_per_scan]
        ]

        # The stored Score row is what the report and the dashboard show, so the
        # composite here is READ rather than recomputed — a scan scored under an
        # older formula version must keep the number it was scored with.
        stored = (
            await session.execute(
                select(Score).where(Score.scan_id == scan.id).order_by(Score.created_at.desc())
            )
        ).scalars().first()

        out.append(
            HistoryScanOut(
                scan_id=scan.id,
                status=scan.status.value,
                scanned_at=scan.finished_at or scan.created_at,
                composite=stored.composite if stored else None,
                share_of_voice=computed.value(Dimension.SHARE_OF_VOICE) if computed else None,
                cited_domains=cited,
                competitors=[
                    HistoryCompetitorOut(
                        competitor_id=c.competitor_id,
                        name=c.name,
                        mention_rate=c.mention_rate,
                        share_of_voice=c.share_of_voice,
                        citation_strength=c.citation_strength,
                    )
                    for c in comparisons
                ],
            )
        )

    return ClientHistoryOut(
        client_id=client.id,
        name=client.brand_name or client.name,
        domain=client.domain,
        scans=out,
        scans_without_data=len(scans) - len(plottable),
    )

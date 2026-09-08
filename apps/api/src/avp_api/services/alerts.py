"""Alert generation — Epic E.

Runs once, at the end of a scan, after scoring. Reads rows the scan and its
baseline already wrote; calls no engine and no model.

=============================================================================
THREE PREMISES FROM THE BRIEF, ALL CHECKED AGAINST `avp_dev` FIRST
=============================================================================
Epic A's brief assumed stored answer text and Epic B's assumed recurring
prompts; both were wrong and both were caught by measuring before building.
The same check was run here, and all three alert kinds needed correcting.

**1. "Consecutive scans" is not a time series.** Of the three consecutive-scan
pairs in the whole database, TWO are re-runs 43 minutes and 2h11m apart:

    Plausible  11:32 -> 13:43 -> 14:27   all on one afternoon
    Notion     Aug 29 -> Aug 31          the only real interval

On those re-runs, with nothing having happened, the composite moved -0.93 and
+0.68, net tone moved up to 6 points, and competitor citations swung by 26.
An alert comparing a scan with whatever ran before it would report engine
nondeterminism as a business event — reliably, and most often for the operator
who re-runs a scan to check something. `MIN_BASELINE_HOURS` is the fix, and it
is the single most important line in this module.

**2. "Sentiment turning negative" never happens.** Across every engine of every
scan on record the minimum net tone is **+4**. A rule watching for a sign
change fires zero times, and would have said nothing about the clearest tone
event in the data: Notion's net falling 13->6, 12->5 and 10->4 across all three
engines at once. So the rule is a DECLINE, with the sign change kept as an
additional trigger for when it eventually does happen.

**3. "A rival taking a citation the client just lost" cannot occur.**
`classify_citation` returns `cites_subject` as `domain == subject_domain` — a
pure function of the domain string. A source the client owned in one scan is
its own in every scan, forever. Confirmed in the data: no domain in any
client's history carries two different `cites_subject` values. The measurable
version is the client's own domain going from cited to uncited, which is what
`OWNED_CITATION_LOST` detects.

=============================================================================
ON THE THRESHOLDS BELOW
=============================================================================
Each is set from measured re-run variance rather than picked because it sounds
right, and each is asserted as a RELATIONSHIP in `test_alerts.py` rather than
as a literal — so raising one forces the argument to be made again, the way
Epic 9.24's prompt-run throttle does.

**The honest caveat, recorded rather than buried:** the sample is two re-run
pairs and one real interval. That is enough to show the re-run problem is real;
it is nowhere near enough to be a variance estimate. These numbers should be
revisited against a client with a genuine scan history, and the tests are
written so that revisiting them is a deliberate act.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ids import ALERT, new_id
from ..models.alert import Alert, AlertKind
from ..models.client import Client
from ..models.engine_result import EngineResult, EngineResultStatus, Sentiment
from ..models.scan import Scan, ScanStatus
from ..models.score import Score

logger = structlog.get_logger(__name__)

COMPARABLE: frozenset[ScanStatus] = frozenset({ScanStatus.SUCCEEDED, ScanStatus.PARTIAL})

ANSWERED: tuple[EngineResultStatus, ...] = (
    EngineResultStatus.OK,
    EngineResultStatus.ANSWERED_NO_MENTION,
)

# ---------------------------------------------------------------------------
# The guard that makes every threshold below mean anything.
#
# A baseline must be at least this much older than the scan being judged. The
# longest re-run gap actually observed is 2h11m; a daily scanning cadence must
# still qualify. 20 hours sits above the first and below the second, and
# `test_alerts.py` asserts BOTH relationships rather than the number — so this
# cannot be lowered under a re-run gap or raised past a daily cadence without
# the test saying so.
MIN_BASELINE_HOURS = 20
OBSERVED_RERUN_GAP_HOURS = 2.2  # Plausible, 11:32 -> 13:43. The floor to clear.
DAILY_CADENCE_HOURS = 24  # The ceiling: a daily scan must still find a baseline.

# Composite is 0-100. Measured re-run movement is under 1 point either way
# (-0.93, +0.68); the one real-interval move on record is -2.56. A relative
# threshold rather than absolute points, because a 3-point fall means something
# very different at 8 than at 80.
VISIBILITY_DROP_FRACTION = Decimal("0.05")

# Net tone per engine. Worst re-run movement observed is -17% (18 -> 15);
# Notion's real decline was -54% to -60% across all three engines. 40% sits
# clear of the first and well under the second.
SENTIMENT_DECLINE_FRACTION = Decimal("0.40")
# Below this many classified answers a percentage is arithmetic, not evidence:
# 2 -> 1 is a 50% decline and means nothing.
SENTIMENT_MIN_BASE = 5


async def _baseline_for(session: AsyncSession, scan: Scan) -> Scan | None:
    """The most recent comparable scan at least `MIN_BASELINE_HOURS` older.

    Returns None when there is no such scan — in which case NO alerts are
    generated. A first scan has nothing to be a change from, and a scan whose
    only predecessor is a re-run of itself has nothing trustworthy to be a
    change from, which is the same answer for a better reason.
    """
    cutoff = scan.created_at - timedelta(hours=MIN_BASELINE_HOURS)
    return (
        await session.execute(
            select(Scan)
            .where(
                Scan.client_id == scan.client_id,
                Scan.id != scan.id,
                Scan.status.in_(COMPARABLE),
                Scan.created_at <= cutoff,
            )
            .order_by(Scan.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


async def _composite(session: AsyncSession, scan_id: str) -> tuple[Decimal | None, str | None]:
    """The STORED composite and the formula it was scored under, never a
    recomputed one.

    A scan scored under an older formula version keeps the number it was scored
    with — the rule `client_history.build_history` already follows. Comparing a
    stored figure with a freshly recomputed one would report a formula change
    as a visibility drop.

    **The version travels with the number for the same reason** — v2.1. Two
    stored composites under different formulas are not comparable either (rule
    5 is the whole reason the version is stored), and a bump that lowers scores
    would otherwise fire a "visibility fell" alert on the next scan of every
    client with a baseline. The caller declines to compare across versions.
    """
    row = (
        await session.execute(
            select(Score.composite, Score.formula_version)
            .where(Score.scan_id == scan_id)
            .order_by(Score.created_at.desc())
            .limit(1)
        )
    ).first()
    return (None, None) if row is None else (row[0], row[1])


async def _net_tone(session: AsyncSession, scan_id: str) -> dict[str, tuple[int, int]]:
    """Per engine: (net tone, classified answers).

    Net is positive minus negative. Neutral is deliberately not counted — it is
    a real classification, but it moves the net by zero and including it in the
    DENOMINATOR would let a scan look better simply by being blander.
    `classified` counts every answer that HAS a label, which is the base a
    percentage change is honest against.

    NULL sentiment is excluded entirely: it means the subject was never named,
    so tone was never asked. Epic A's fourth bucket, kept out of the arithmetic
    for the same reason it is kept out of the chart.
    """
    rows = (
        await session.execute(
            select(EngineResult.engine, EngineResult.sentiment)
            .where(
                EngineResult.scan_id == scan_id,
                EngineResult.status.in_(ANSWERED),
                EngineResult.sentiment.is_not(None),
            )
        )
    ).all()

    out: dict[str, tuple[int, int]] = {}
    for engine, sentiment in rows:
        net, total = out.get(engine.value, (0, 0))
        if sentiment is Sentiment.POSITIVE:
            net += 1
        elif sentiment is Sentiment.NEGATIVE:
            net -= 1
        out[engine.value] = (net, total + 1)
    return out


async def _owned_citations(session: AsyncSession, scan_id: str) -> int:
    """How many citations pointed at the client's own domain in this scan."""
    from ..models.engine_result import Citation

    rows = (
        await session.execute(
            select(Citation.id)
            .join(EngineResult, EngineResult.id == Citation.engine_result_id)
            .where(
                EngineResult.scan_id == scan_id,
                EngineResult.status.in_(ANSWERED),
                Citation.cites_subject.is_(True),
            )
        )
    ).all()
    return len(rows)


def _pct(before: Decimal | int, after: Decimal | int) -> Decimal:
    """Signed fractional change. Guards a zero base rather than dividing by it."""
    b = Decimal(str(before))
    if b == 0:
        return Decimal(0)
    return (Decimal(str(after)) - b) / abs(b)


async def generate_for_scan(
    session: AsyncSession, scan: Scan, client: Client
) -> list[Alert]:
    """Detect what changed between `scan` and its baseline. Writes the rows.

    Idempotent per scan: existing alerts for this scan are deleted and rebuilt,
    so a re-scored scan does not accumulate duplicates. Acknowledgements are
    lost in that case, which is correct — an acknowledgement is of a specific
    finding, and a re-scored scan's findings are new ones.
    """
    existing = (
        await session.execute(select(Alert).where(Alert.scan_id == scan.id))
    ).scalars().all()
    for row in existing:
        await session.delete(row)

    baseline = await _baseline_for(session, scan)
    if baseline is None:
        logger.info(
            "alerts.no_baseline",
            scan_id=scan.id,
            reason="no comparable scan at least MIN_BASELINE_HOURS older",
        )
        return []

    made: list[Alert] = []

    def add(kind: AlertKind, detail: str, engine: str | None = None) -> None:
        made.append(
            Alert(
                id=new_id(ALERT),
                client_id=scan.client_id,
                agency_id=scan.agency_id,
                kind=kind,
                scan_id=scan.id,
                baseline_scan_id=baseline.id,
                engine=engine,
                detail=detail,
            )
        )

    # --- visibility ---------------------------------------------------------
    now_score, now_version = await _composite(session, scan.id)
    was_score, was_version = await _composite(session, baseline.id)
    if now_score is not None and was_score is not None and now_version != was_version:
        # A number that moved because the definition changed is a claim about
        # us, not about the client's business — the report says so through
        # `previousFormulaVersions`, and an alert must not say otherwise.
        logger.info(
            "alerts.formula_changed",
            scan_id=scan.id,
            baseline_id=baseline.id,
            was=was_version,
            now=now_version,
        )
    elif now_score is not None and was_score is not None and was_score > 0:
        change = _pct(was_score, now_score)
        if change <= -VISIBILITY_DROP_FRACTION:
            add(
                AlertKind.VISIBILITY_DROP,
                f"Visibility fell {abs(change) * 100:.1f}%, "
                f"from {was_score:.1f} to {now_score:.1f}.",
            )

    # --- tone ---------------------------------------------------------------
    now_tone = await _net_tone(session, scan.id)
    was_tone = await _net_tone(session, baseline.id)
    for engine, (was_net, was_base) in was_tone.items():
        if engine not in now_tone or was_base < SENTIMENT_MIN_BASE:
            continue
        now_net, _ = now_tone[engine]
        # A sign change is worth saying however small, because it is the first
        # time this product has ever had one to report.
        if was_net >= 0 and now_net < 0:
            add(
                AlertKind.SENTIMENT_DECLINE,
                f"Net tone turned negative, from {was_net:+d} to {now_net:+d}.",
                engine,
            )
        elif was_net > 0:
            change = _pct(was_net, now_net)
            if change <= -SENTIMENT_DECLINE_FRACTION:
                add(
                    AlertKind.SENTIMENT_DECLINE,
                    f"Net tone fell {abs(change) * 100:.0f}%, "
                    f"from {was_net:+d} to {now_net:+d}.",
                    engine,
                )

    # --- the client's own citations ----------------------------------------
    now_owned = await _owned_citations(session, scan.id)
    was_owned = await _owned_citations(session, baseline.id)
    if was_owned > 0 and now_owned == 0:
        add(
            AlertKind.OWNED_CITATION_LOST,
            f"No answer cited {client.domain} this scan, against "
            f"{was_owned} citation{'s' if was_owned != 1 else ''} last time.",
        )

    for alert in made:
        session.add(alert)
    logger.info("alerts.generated", scan_id=scan.id, baseline_id=baseline.id, count=len(made))
    return made


__all__ = [
    "generate_for_scan",
    "MIN_BASELINE_HOURS",
    "VISIBILITY_DROP_FRACTION",
    "SENTIMENT_DECLINE_FRACTION",
    "SENTIMENT_MIN_BASE",
    "OBSERVED_RERUN_GAP_HOURS",
    "DAILY_CADENCE_HOURS",
]

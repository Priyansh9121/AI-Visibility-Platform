"""Seed the database with the state the free verification scripts assume.

Epic 3.7 audited whether `scripts/verify_*.py` could run in CI and found the
blocker was not CI at all: every one of them assumes an `avp_dev` built by hand
across Epics 3-8 and preserved by every script that has touched it since.
`verify_report.py` returns 1 without a succeeded, scored scan;
`verify_competitor_override.py` bails unless the newest competitor set contains
a specific rival and carries no overrides. This script produces that state from
nothing, so the two free scripts can run against a database that has only ever
had this script run against it.

Costs nothing. No SerpApi search, no model call, no Playwright crawl, no
network fetch of any kind — every row is written directly, and the one thing
that is *computed* rather than written is the Score, which comes from the real
`scoring_runner.score_scan`. That matters: `verify_report.py` asserts the
dimension breakdown re-sums to the stored composite, and hand-writing a Score
would make that assertion a test of my arithmetic rather than of the scorer.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/seed_dev.py

    # what it would do, without writing:
    ... uv run python scripts/seed_dev.py --dry-run

Idempotent. Re-running finds the existing seed by its stable slug/domain and
rebuilds the scan's child rows in place rather than inserting a second copy.

Everything it writes is SYNTHETIC and says so
---------------------------------------------
The subject is `Seedwell Supply` at `seed-fixture.example`, and every competitor
and cited domain is also `.example` — a reserved TLD (RFC 2606) that can never
resolve. This is deliberate on two counts. It keeps a real company's name out of
a committed fixture (ip-safety.md #7/#8), and it makes the data unmistakable in
any output: a reader who sees `seed-fixture.example` in a report knows they are
not looking at a measurement.

This is the first synthetic pipeline output in the repo. Every other row in
`avp_dev` was produced by a real crawl, a real search or a real model call, and
the verification scripts lean on that ("nothing here is a fixture"). Where a
script now runs against seeded data instead, it says so in its own output.

What it does NOT touch
----------------------
The existing Help Scout scan (`scan_01M0HDRGJNWNZDSJPP0NC3SV8W`) and its
agency, competitors, score, audit and action items. Epic 7's and Epic 8's
screenshots and `apps/web/src/lib/report/__fixtures__/reports.ts` are all taken
from it. The seed creates its own agency, client and scan, and only ever
deletes rows belonging to the scan it owns.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from avp_api import ids  # noqa: E402
from avp_api.models import (  # noqa: E402
    ActionItem,
    Agency,
    BrandMention,
    Citation,
    Client,
    Competitor,
    CompetitorSet,
    EngineResult,
    Prompt,
    PromptSet,
    Scan,
    Score,
    TechnicalAudit,
    TechnicalAuditCheck,
)
from avp_api.models.action_item import (  # noqa: E402
    ActionItemSource,
    ActionItemStatus,
    Effort,
    Priority,
)
from avp_api.models.client import ClassificationStatus, ClientKind  # noqa: E402
from avp_api.models.competitor import DetectionSource, DetectionStatus  # noqa: E402
from avp_api.models.engine_result import (  # noqa: E402
    CitationType,
    Engine,
    EngineResultStatus,
    Sentiment,
)
from avp_api.models.prompt import PromptIntent  # noqa: E402
from avp_api.models.scan import ScanStatus, ScanTrigger  # noqa: E402
from avp_api.models.technical_audit import AuditStatus, CheckStatus  # noqa: E402
from avp_api.services import scoring_runner  # noqa: E402

# --------------------------------------------------------------------------
# The seed's identity. These are the handles idempotency keys on, so they must
# not change casually — an edit here orphans the previous seed rather than
# replacing it.
# --------------------------------------------------------------------------
AGENCY_SLUG = "seed-fixture"
AGENCY_NAME = "Seed Fixture Agency"
SUBJECT_NAME = "Seedwell Supply"
SUBJECT_DOMAIN = "seed-fixture.example"

# The rival `verify_competitor_override.py` is pointed at to strike. It is
# serp-only and uncorroborated, which is what makes it the plausible thing for
# an operator to remove — the same shape as the rival that script strikes on
# the real Help Scout set.
STRIKE_ME = "Kestrel Trade"

# (name, domain, source, serp, cocit, corroborated, score)
COMPETITORS = [
    ("Northaven Group", "northaven.example", DetectionSource.BOTH, 4, 1, True, "3.100"),
    ("Marlowe Direct", "marlowe.example", DetectionSource.BOTH, 3, 1, True, "2.200"),
    (STRIKE_ME, "kestrel-trade.example", DetectionSource.SERP, 3, 0, False, "0.900"),
]

# (text, intent)
PROMPTS = [
    ("who are the best suppliers for this category", PromptIntent.AWARENESS),
    ("how do the main suppliers compare on price", PromptIntent.COMPARISON),
]

# Which brands each (prompt, engine) answer named, subject first where present.
# Enough breadth that `compare_competitors` has a non-empty population — an
# empty comparison list is the exact vacuity `verify_competitor_override.py`
# PART 4 exists to catch.
ANSWERS = [
    # (prompt index, engine, subject mentioned, position, sentiment, rivals named)
    (0, Engine.CLAUDE, True, 1, Sentiment.POSITIVE, ["Northaven Group", "Marlowe Direct"]),
    (0, Engine.CLAUDE_SEARCH, True, 2, Sentiment.POSITIVE, ["Northaven Group"]),
    (1, Engine.CLAUDE, True, 1, Sentiment.NEUTRAL, ["Marlowe Direct"]),
    (1, Engine.CLAUDE_SEARCH, True, 3, Sentiment.POSITIVE, ["Northaven Group", "Marlowe Direct"]),
]

# (domain, cites_subject, type). One owned citation against several third-party
# ones gives citation_strength a gap worth reporting rather than a perfect score.
CITED = [
    (SUBJECT_DOMAIN, True, CitationType.OWNED),
    ("directory-one.example", False, CitationType.DIRECTORY),
    ("review-hub.example", False, CitationType.REVIEW),
    ("northaven.example", False, CitationType.COMPETITOR),
]

# (check_key, status, detail_code). Two warns so the report's fix beat has
# something concrete to name, mirroring the real scan's shape without copying
# its specifics.
AUDIT_CHECKS = [
    ("site_reachable", CheckStatus.PASS, None),
    ("indexable", CheckStatus.PASS, None),
    ("robots_txt_present", CheckStatus.PASS, None),
    ("sitemap_present", CheckStatus.PASS, None),
    ("canonical_present", CheckStatus.PASS, None),
    ("schema_present", CheckStatus.PASS, None),
    ("schema_business_entity", CheckStatus.PASS, None),
    ("meta_title", CheckStatus.PASS, None),
    ("meta_description", CheckStatus.PASS, None),
    ("single_h1", CheckStatus.PASS, None),
    ("schema_faq", CheckStatus.WARN, "NO_FAQ_SCHEMA"),
    ("schema_product_or_service", CheckStatus.WARN, "NO_PRODUCT_OR_SERVICE_SCHEMA"),
]
TECHNICAL_FOUNDATION = Decimal("87.50")

# Two generated fixes, one per source.
#
# Present because of a defect this seed's ABSENCE of them concealed. Epic 8 put
# `actionItems` on the report payload; verify_report.py's forbidden-key sweep
# flagged their `title` and had been failing against avp_dev ever since. It
# passed against a seeded database purely because the seed produced no action
# items — so the seed was green on the exact payload branch that was broken.
# A fixture that omits a branch cannot verify it. See build-log Epic 3.8.
#
# `title`/`detail` are free text and that is correct here for the same reason it
# is correct in production: these are OUR OWN recommendations, not scraped
# material (models/action_item.py, ip-safety.md #7).
ACTION_ITEMS = [
    (
        ActionItemSource.GAP, "citation_strength", "citation_strength",
        Decimal("13.33"), 1, Priority.HIGH, Effort.L,
        "Publish reference pages on seed-fixture.example that answer the "
        "questions currently answered elsewhere",
        "Only 4 of the 16 citations behind these answers pointed at "
        "seed-fixture.example; the rest went to directory-one.example and "
        "review-hub.example.",
    ),
    (
        ActionItemSource.AUDIT, "schema_faq", "technical_foundation",
        None, 2, Priority.MEDIUM, Effort.S,
        "Add FAQPage markup to the pages that answer buyer questions",
        "The site declares Organization and WebSite types only, with no FAQ "
        "markup.",
    ),
]


def rule(title: str) -> None:
    print("\n" + "=" * 84)
    print(title)
    print("=" * 84)


async def _agency(session) -> Agency:  # noqa: ANN001
    row = (
        await session.execute(select(Agency).where(Agency.slug == AGENCY_SLUG))
    ).scalar_one_or_none()
    if row is None:
        row = Agency(id=ids.new_id(ids.AGENCY), name=AGENCY_NAME, slug=AGENCY_SLUG)
        session.add(row)
        await session.flush()
    return row


async def _client(session, agency: Agency) -> Client:  # noqa: ANN001
    row = (
        await session.execute(
            select(Client).where(
                Client.agency_id == agency.id, Client.domain == SUBJECT_DOMAIN
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = Client(id=ids.new_id(ids.CLIENT), agency_id=agency.id, domain=SUBJECT_DOMAIN)
        session.add(row)
    row.name = SUBJECT_NAME
    row.brand_name = SUBJECT_NAME
    row.kind = ClientKind.PROSPECT
    row.classification_status = ClassificationStatus.CLASSIFIED
    row.industry = "wholesale supply"
    row.industry_niche = "regional trade supply"
    await session.flush()
    return row


async def _scan(session, client: Client, agency: Agency) -> Scan:  # noqa: ANN001
    """The seed's own scan. Re-runs rebuild its children rather than add a scan.

    Reusing one scan is what keeps the script idempotent in the way that
    matters: `verify_competitor_override.py` selects the NEWEST competitor set
    by id, so a second seed that inserted a second scan would leave the first
    one's set stranded and unverifiable, and a third would strand two.
    """
    row = (
        await session.execute(
            select(Scan).where(Scan.client_id == client.id).order_by(Scan.id)
        )
    ).scalars().first()
    if row is None:
        row = Scan(id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=agency.id)
        session.add(row)
    started = datetime.now(UTC) - timedelta(minutes=5)
    row.status = ScanStatus.SUCCEEDED
    row.trigger = ScanTrigger.MANUAL
    row.formula_version = "v1.1"
    row.prompt_count = len(PROMPTS)
    row.engine_result_count = len(ANSWERS)
    row.started_at = started
    row.finished_at = started + timedelta(minutes=4)
    await session.flush()
    return row


async def _clear_children(session, scan: Scan) -> None:  # noqa: ANN001
    """Remove everything hanging off the seed's scan, newest dependency first.

    Scoped to this scan id only. Nothing here can reach another scan's rows,
    which is the property that keeps the Help Scout data safe on every re-run.
    """
    results = (
        await session.execute(select(EngineResult.id).where(EngineResult.scan_id == scan.id))
    ).scalars().all()
    if results:
        await session.execute(
            delete(BrandMention).where(BrandMention.engine_result_id.in_(results))
        )
        await session.execute(delete(Citation).where(Citation.engine_result_id.in_(results)))
        await session.execute(delete(EngineResult).where(EngineResult.scan_id == scan.id))

    sets = (
        await session.execute(select(PromptSet.id).where(PromptSet.scan_id == scan.id))
    ).scalars().all()
    if sets:
        await session.execute(delete(Prompt).where(Prompt.prompt_set_id.in_(sets)))
        await session.execute(delete(PromptSet).where(PromptSet.scan_id == scan.id))

    audits = (
        await session.execute(select(TechnicalAudit.id).where(TechnicalAudit.scan_id == scan.id))
    ).scalars().all()
    if audits:
        await session.execute(
            delete(TechnicalAuditCheck).where(TechnicalAuditCheck.audit_id.in_(audits))
        )
        await session.execute(delete(TechnicalAudit).where(TechnicalAudit.scan_id == scan.id))

    csets = (
        await session.execute(select(CompetitorSet.id).where(CompetitorSet.scan_id == scan.id))
    ).scalars().all()
    if csets:
        await session.execute(delete(Competitor).where(Competitor.competitor_set_id.in_(csets)))
        await session.execute(delete(CompetitorSet).where(CompetitorSet.scan_id == scan.id))

    await session.execute(delete(ActionItem).where(ActionItem.scan_id == scan.id))
    await session.execute(delete(Score).where(Score.scan_id == scan.id))
    await session.flush()


async def _build(session, scan: Scan) -> None:  # noqa: ANN001
    """Write the scan's children. Assumes `_clear_children` has just run."""
    prompt_set = PromptSet(
        id=ids.new_id(ids.PROMPT_SET),
        scan_id=scan.id,
        generated_by="seed_dev",
        generation_params={"seeded": True},
    )
    session.add(prompt_set)
    await session.flush()

    prompts = []
    for position, (text, intent) in enumerate(PROMPTS, start=1):
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT),
            prompt_set_id=prompt_set.id,
            text=text,
            intent=intent,
            position=position,
        )
        session.add(prompt)
        prompts.append(prompt)
    await session.flush()

    competitor_set = CompetitorSet(
        id=ids.new_id(ids.COMPETITOR_SET),
        scan_id=scan.id,
        status=DetectionStatus.OK,
        detection_confidence=Decimal("0.667"),
        serp_queries_run=4,
        co_citation_prompts_run=1,
        candidates_considered=18,
        used_industry_seed=True,
        detected_at=datetime.now(UTC),
        competitors=[],
    )
    session.add(competitor_set)
    await session.flush()

    by_name: dict[str, Competitor] = {}
    for rank, (name, domain, source, serp, cocit, corroborated, score) in enumerate(
        COMPETITORS, start=1
    ):
        competitor = Competitor(
            id=ids.new_id(ids.COMPETITOR),
            competitor_set_id=competitor_set.id,
            name=name,
            domain=domain,
            rank=rank,
            detection_source=source,
            signal_count=serp + cocit,
            serp_mentions=serp,
            co_citation_mentions=cocit,
            corroborated=corroborated,
            score=Decimal(score),
        )
        competitor_set.competitors.append(competitor)
        by_name[name] = competitor
    await session.flush()

    for index, (prompt_index, engine, mentioned, position, sentiment, rivals) in enumerate(
        ANSWERS
    ):
        result = EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT),
            scan_id=scan.id,
            prompt_id=prompts[prompt_index].id,
            engine=engine,
            engine_version="seed_dev/synthetic",
            status=EngineResultStatus.OK,
            mentioned=mentioned,
            position=position,
            brands_mentioned=len(rivals) + (1 if mentioned else 0),
            sentiment=sentiment,
            sentiment_confidence=Decimal("0.900"),
            latency_ms=1200 + index,
        )
        session.add(result)
        await session.flush()

        ordinal = 1
        if mentioned:
            session.add(
                BrandMention(
                    id=ids.new_id(ids.BRAND_MENTION),
                    engine_result_id=result.id,
                    entity_name=SUBJECT_NAME,
                    entity_domain=SUBJECT_DOMAIN,
                    is_subject=True,
                    position=ordinal,
                )
            )
            ordinal += 1
        for rival in rivals:
            session.add(
                BrandMention(
                    id=ids.new_id(ids.BRAND_MENTION),
                    engine_result_id=result.id,
                    entity_name=rival,
                    entity_domain=by_name[rival].domain,
                    is_subject=False,
                    competitor_id=by_name[rival].id,
                    position=ordinal,
                )
            )
            ordinal += 1

        for cite_position, (domain, cites_subject, source_type) in enumerate(CITED, start=1):
            session.add(
                Citation(
                    id=ids.new_id(ids.CITATION),
                    engine_result_id=result.id,
                    source_domain=domain,
                    source_url=f"https://{domain}/page-{cite_position}",
                    source_type=source_type,
                    position=cite_position,
                    cites_subject=cites_subject,
                    competitor_id=by_name["Northaven Group"].id
                    if source_type is CitationType.COMPETITOR
                    else None,
                )
            )

    audit = TechnicalAudit(
        id=ids.new_id(ids.TECHNICAL_AUDIT),
        scan_id=scan.id,
        url_audited=f"https://{SUBJECT_DOMAIN}",
        status=AuditStatus.OK,
        pages_crawled=1,
        technical_foundation=TECHNICAL_FOUNDATION,
        audited_at=datetime.now(UTC),
        schema_types=["Organization", "WebSite"],
        has_organization_schema=True,
        has_faq_schema=False,
        has_product_schema=False,
        is_indexable=True,
        has_sitemap=True,
        h1_count=1,
        word_count=820,
        checks=[],
    )
    session.add(audit)
    await session.flush()
    for check_key, status, detail_code in AUDIT_CHECKS:
        audit.checks.append(
            TechnicalAuditCheck(
                id=ids.new_id(ids.AUDIT_CHECK),
                audit_id=audit.id,
                check_key=check_key,
                status=status,
                detail_code=detail_code,
            )
        )
    await session.flush()

    for source, source_key, dimension_key, upside, rank, priority, effort, title, detail in (
        ACTION_ITEMS
    ):
        session.add(
            ActionItem(
                id=ids.new_id(ids.ACTION_ITEM),
                scan_id=scan.id,
                source=source,
                source_key=source_key,
                dimension_key=dimension_key,
                points_upside=upside,
                rank=rank,
                title=title,
                detail=detail,
                priority=priority,
                effort=effort,
                status=ActionItemStatus.OPEN,
                generated_by="seed_dev",
            )
        )
    await session.flush()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a database for the free verify scripts.")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be written, write nothing"
    )
    args = parser.parse_args()

    engine = create_async_engine(os.environ["DATABASE_URL"])
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        rule("SEED — what is already here")
        # .first(), not scalar_one_or_none(): uniqueness on clients is
        # (agency_id, domain), so a database carrying two agencies with a
        # same-named client would make scalar_one_or_none() raise here — in a
        # line that only exists to print a status.
        existing = (
            await session.execute(select(Client).where(Client.domain == SUBJECT_DOMAIN))
        ).scalars().first()
        print(f"  target database : {os.environ['DATABASE_URL'].rsplit('/', 1)[-1]}")
        state = "present — will be rebuilt" if existing else "absent — will be created"
        print(f"  seed client     : {state}")

        others = (
            await session.execute(select(Scan).join(Client).where(Client.domain != SUBJECT_DOMAIN))
        ).scalars().all()
        print(f"  other scans     : {len(others)} (untouched)")
        for other in others:
            print(f"      {other.id}")

        if args.dry_run:
            rule("DRY RUN — nothing written")
            print(f"  would seed {SUBJECT_NAME} ({SUBJECT_DOMAIN}) with:")
            print(f"    {len(PROMPTS)} prompts x {len({a[1] for a in ANSWERS})} engines"
                  f" = {len(ANSWERS)} engine results")
            print(f"    {len(COMPETITORS)} competitors (strike target: {STRIKE_ME})")
            print(f"    {len(CITED)} citations per result, {len(AUDIT_CHECKS)} audit checks")
            await engine.dispose()
            return 0

        rule("SEED — writing")
        agency = await _agency(session)
        client = await _client(session, agency)
        scan = await _scan(session, client, agency)
        await _clear_children(session, scan)
        await _build(session, scan)
        await session.commit()
        print(f"  agency : {agency.id}  [{agency.slug}]")
        print(f"  client : {client.id}  {client.name} ({client.domain})")
        print(f"  scan   : {scan.id}")

        # Scored by the REAL runner, never hand-written. verify_report.py
        # asserts the dimension breakdown re-sums to the stored composite; a
        # hand-written Score would turn that into a test of this file.
        rule("SEED — scoring through the real runner")
        row, computed, comparisons = await scoring_runner.score_scan(session, scan)
        await session.commit()
        print(f"  status    : {row.status.value}")
        print(f"  composite : {row.composite}")
        for key in ("mention_rate", "share_of_voice", "citation_strength",
                    "sentiment", "technical_foundation"):
            print(f"    {key:22} {getattr(row, key)}")
        print(f"  comparisons: {len(comparisons)} rivals"
              f" ({', '.join(c.name for c in comparisons)})")

        rule("SEED — preconditions the free scripts check")
        refreshed = (
            await session.execute(
                select(CompetitorSet)
                .where(CompetitorSet.scan_id == scan.id)
                .options(selectinload(CompetitorSet.competitors))
            )
        ).scalar_one()
        newest = (
            await session.execute(select(CompetitorSet).order_by(CompetitorSet.id.desc()).limit(1))
        ).scalar_one()
        checks = [
            ("verify_report: a succeeded scan exists", scan.status is ScanStatus.SUCCEEDED),
            ("verify_report: it is scored", row.status.value == "scored"),
            ("verify_report: an agency exists for PART 3", agency is not None),
            ("verify_override: the seed's set is the newest by id", newest.id == refreshed.id),
            (f"verify_override: it contains {STRIKE_ME!r}",
             any(c.name == STRIKE_ME for c in refreshed.competitors)),
            ("verify_override: it carries no overrides yet",
             not any(c.is_manual_override for c in refreshed.competitors)),
            ("verify_override: scoring yields a non-empty comparison",
             len(comparisons) > 0),
        ]
        for label, ok in checks:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}")

    await engine.dispose()

    rule("RESULT")
    if not all(ok for _, ok in checks):
        print("  FAIL — the seed did not satisfy every precondition.")
        return 1
    print("  PASS — seeded. Both free verification scripts can now run:")
    print(f"    AVP_OVERRIDE_STRIKE={STRIKE_ME!r} \\")
    print("        uv run python scripts/verify_competitor_override.py")
    print("    uv run python scripts/verify_report.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Live verification of the Epic 7 acceptance criterion.

§7 Epic 7: "a full report renders correctly for a real test scan, passes
IP-safety visual review (Section 2)."

Costs nothing — no model, search or crawl calls. The report PRESENTS data the
earlier epics already produced, so this script's job is to prove it presents the
real rows faithfully, and that the degraded states are real rather than mocked.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/verify_report.py

PART 3 creates a genuinely unscoreable scan (a client with no prompts, scored
through the real scoring runner) so the INSUFFICIENT_DATA report on screen is a
real database row, not a fixture.
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.ids import new_id  # noqa: E402
from avp_api.models import Agency, Client, Scan  # noqa: E402
from avp_api.models.client import ClassificationStatus, ClientKind  # noqa: E402
from avp_api.models.scan import ScanStatus, ScanTrigger  # noqa: E402
from avp_api.services import report as report_service  # noqa: E402
from avp_api.services import scoring_runner  # noqa: E402

DEGRADED_DOMAIN = "epic7-degraded.example"

FORBIDDEN_SUBSTRINGS = (
    '"answer"', '"answerText"', '"snippet"', '"excerpt"', '"quote"',
    '"responseText"', '"rawResponse"', '"description"', '"tagline"',
    '"summary"', '"title"', '"body"', '"content"',
)


def rule(title: str) -> None:
    print("\n" + "=" * 84)
    print(title)
    print("=" * 84)


async def main() -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    Session = async_sessionmaker(engine, expire_on_commit=False)
    failures: list[str] = []

    async with Session() as session:
        # ------------------------------------------------------------------
        rule("PART 1 — a full report from a REAL scan")
        # ------------------------------------------------------------------
        scan = (
            await session.execute(
                select(Scan)
                .join(Client, Client.id == Scan.client_id)
                .where(Client.domain != DEGRADED_DOMAIN)
                .where(Scan.status == ScanStatus.SUCCEEDED)
                .order_by(Scan.created_at.desc())
            )
        ).scalars().first()

        if scan is None:
            print("  no completed scan in this database — run verify_scoring.py first")
            return 1

        report = await report_service.build_report(session, scan)
        print(f"\n  scan     : {report.scan_id}")
        print(f"  subject  : {report.subject.name} ({report.subject.domain})")
        print(f"  agency   : {report.agency.name} [{report.agency.slug}]")
        print(f"  score    : {report.score.status if report.score else 'NEVER SCORED'}"
              f"  composite={report.score.composite if report.score else None}")

        print("\n  dimensions (heaviest first — the ledger stacks bottom-up):")
        for dim in report.dimensions:
            if dim.included:
                gap = dim.weight * (Decimal("100") - (dim.subscore or 0)) / Decimal("100")
                print(f"    {dim.key:22} weight={dim.weight:>6}  sub={dim.subscore:>6}"
                      f"  gap={gap:>6.2f}")
            else:
                print(f"    {dim.key:22} weight={dim.weight:>6}  EXCLUDED"
                      f"  [{dim.exclusion_reason}]")

        # The acceptance test that matters: the breakdown must re-sum to the
        # stored composite. If it does not, the chart and the number disagree.
        if report.score and report.score.composite is not None:
            included = [d for d in report.dimensions if d.included]
            weights = sum(d.weight for d in included)
            rebuilt = sum(d.weight * (d.subscore or 0) for d in included) / Decimal("100")
            print(f"\n  included weights sum : {weights}")
            print(f"  breakdown re-sums to : {rebuilt:.2f}")
            print(f"  stored composite     : {report.score.composite}")
            if abs(weights - Decimal("100")) > Decimal("0.01"):
                failures.append(f"included weights sum to {weights}, not 100")
            if abs(rebuilt - report.score.composite) > Decimal("0.01"):
                failures.append(
                    f"breakdown re-sums to {rebuilt}, not the stored {report.score.composite}"
                )
            else:
                print("  -> the ledger's lit height IS the stored composite. OK")

            gaps = {
                d.key: d.weight * (Decimal("100") - (d.subscore or 0)) / Decimal("100")
                for d in included
            }
            biggest = max(sorted(gaps), key=lambda k: gaps[k])
            print(f"\n  biggest gap (argmax weight_i x (100 - sub_i)/100):"
                  f" {biggest} = {gaps[biggest]:.2f} points")

        print(f"\n  competitors : {report.competitor_set.status if report.competitor_set else 'NONE'}")
        for competitor in (report.competitor_set.competitors if report.competitor_set else []):
            print(f"    #{competitor.rank} {competitor.name:14} {competitor.domain or '-':20}"
                  f" mention={competitor.mention_rate} sov={competitor.share_of_voice}"
                  f" cites={competitor.citation_strength}")

        print(f"\n  audit   : {report.audit.status if report.audit else 'NONE'}"
              f"  passed={report.audit.passed if report.audit else '-'}"
              f" warned={report.audit.warned if report.audit else '-'}"
              f" failed={report.audit.failed if report.audit else '-'}")
        for finding in (report.audit.findings if report.audit else []):
            print(f"    {finding.status:6} {finding.check_key:26} [{finding.detail_code}]")

        print(f"\n  proof   : {report.proof.answered_results}/{report.proof.engine_results}"
              f" results answered, {report.proof.total_citations} citations"
              f" ({report.proof.subject_citations} to the subject)")
        for coverage in report.proof.engine_coverage:
            print(f"    {coverage.engine.value:16} answered={coverage.answered}"
                  f"/{coverage.prompts_run} mentioned={coverage.mentioned}")

        # ------------------------------------------------------------------
        rule("PART 2 — ip-safety.md #7: the wire payload carries facts only")
        # ------------------------------------------------------------------
        payload = report.model_dump_json(by_alias=True)
        print(f"\n  payload size : {len(payload)} bytes")
        leaked = [s for s in FORBIDDEN_SUBSTRINGS if s in payload]
        if leaked:
            failures.append(f"report payload contains prose-bearing keys: {leaked}")
            print(f"  LEAKED       : {leaked}")
        else:
            print("  no prose-bearing key present. OK")

        cited = report.proof.subject_cited_domains + report.proof.competitor_cited_domains
        print(f"  cited domains: {len(cited)} shown, each as domain + link-out")
        for entry in cited[:4]:
            print(f"    {entry.domain:22} x{entry.citations:<3} -> {entry.sample_url}")
        print("  competitor rows carry name + domain + counts, and no other field:")
        if report.competitor_set and report.competitor_set.competitors:
            fields = set(report.competitor_set.competitors[0].model_dump().keys())
            prose = {"description", "tagline", "summary", "positioning", "about", "snippet"}
            print(f"    {sorted(fields)}")
            if fields & prose:
                failures.append(f"competitor row exposes {sorted(fields & prose)}")
            else:
                print("    -> no descriptive field exists. OK")

        # ------------------------------------------------------------------
        rule("PART 3 — a REAL degraded scan: INSUFFICIENT_DATA")
        # ------------------------------------------------------------------
        # Built through the real models and scored by the real scoring runner.
        # A scan with no prompts has nothing to answer, which is exactly the
        # scoring-spec.md edge case: "Score is not 0 — it is null with reason
        # INSUFFICIENT_DATA."
        agency = (await session.execute(select(Agency).limit(1))).scalar_one()
        degraded = (
            await session.execute(
                select(Client).where(
                    Client.agency_id == agency.id, Client.domain == DEGRADED_DOMAIN
                )
            )
        ).scalar_one_or_none()

        if degraded is None:
            degraded = Client(
                id=new_id("clnt"),
                agency_id=agency.id,
                name="Northwind Dental",
                domain=DEGRADED_DOMAIN,
                kind=ClientKind.PROSPECT,
                classification_status=ClassificationStatus.PENDING,
            )
            session.add(degraded)
            await session.flush()

        degraded_scan = (
            await session.execute(
                select(Scan).where(Scan.client_id == degraded.id).order_by(Scan.created_at.desc())
            )
        ).scalars().first()

        if degraded_scan is None:
            degraded_scan = Scan(
                id=new_id("scan"),
                client_id=degraded.id,
                agency_id=agency.id,
                status=ScanStatus.SUCCEEDED,
                trigger=ScanTrigger.MANUAL,
                formula_version="v1.1",
                prompt_count=0,
                engine_result_count=0,
            )
            session.add(degraded_scan)
            await session.flush()

        row, computed, _ = await scoring_runner.score_scan(session, degraded_scan)
        await session.commit()

        degraded_report = await report_service.build_report(session, degraded_scan)
        print(f"\n  scan    : {degraded_report.scan_id}")
        print(f"  subject : {degraded_report.subject.name} ({degraded_report.subject.domain})")
        print(f"  status  : {degraded_report.score.status if degraded_report.score else None}")
        print(f"  composite: {degraded_report.score.composite if degraded_report.score else None}")
        print(f"  reason  : {degraded_report.score.reason_code if degraded_report.score else None}")
        print("\n  every dimension excluded, each carrying its reason:")
        for dim in degraded_report.dimensions:
            print(f"    {dim.key:22} included={dim.included}  subscore={dim.subscore}"
                  f"  [{dim.exclusion_reason}]")
        print(f"\n  competitorSet : {degraded_report.competitor_set}")
        print(f"  audit         : {degraded_report.audit}")

        if degraded_report.score is None or degraded_report.score.status.value != "insufficient_data":
            failures.append("degraded scan did not produce an insufficient_data score")
        if degraded_report.score and degraded_report.score.composite is not None:
            failures.append("an unscoreable scan produced a composite — it must be null")
        if any(d.subscore is not None for d in degraded_report.dimensions):
            failures.append("an excluded dimension carried a sub-score — it must be null, not 0")
        if not failures:
            print("\n  -> null composite, no dimension scored zero. OK")

        print(f"\n  REPORT URLS")
        print(f"    real      : /scans/{report.scan_id}/report")
        print(f"    degraded  : /scans/{degraded_report.scan_id}/report")

    await engine.dispose()

    rule("RESULT")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1
    print("  PASS — a full report renders from real persisted data, the breakdown")
    print("         re-sums to the stored composite, the payload carries facts only,")
    print("         and a genuinely unscoreable scan reports as null rather than zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

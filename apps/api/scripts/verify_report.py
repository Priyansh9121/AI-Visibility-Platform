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
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic.alias_generators import to_snake  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.ids import new_id  # noqa: E402
from avp_api.models import Agency, Client, Prompt, PromptSet, Scan  # noqa: E402
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
    ran_against_synthetic = False

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
        # Same honesty rule verify_competitor_override.py carries: a reserved
        # TLD (RFC 2606) cannot be a measured business, so a run against seeded
        # data must not describe itself as running against real data.
        ran_against_synthetic = report.subject.domain.endswith(
            (".example", ".invalid", ".test", ".localhost")
        )
        synthetic = report.subject.domain.endswith(
            (".example", ".invalid", ".test", ".localhost")
        )
        print(f"\n  scan     : {report.scan_id}")
        print("  data     : " + ("SEEDED, synthetic (scripts/seed_dev.py)"
                                 if synthetic else "real, produced by the live pipeline"))
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

        # `actionItems` is lifted out before the sweep, and that exemption is
        # bounded immediately below rather than taken on trust.
        #
        # Epic 8 added the generated fix list to this payload, and every item
        # carries a `title` — which this list forbids. The forbidden list was
        # written in Epic 7, when every string in a report was somebody else's,
        # and an ActionItem is the one thing here that is OURS: our own
        # recommendation about our own client's site, generated from our own
        # measurements. models/action_item.py documents that exception and
        # test_ip_safety.py's `test_action_item_is_the_documented_free_text_
        # exception` asserts it deliberately stays out of FACTS_ONLY_MODELS.
        #
        # So the payload was right and this check was stale — a hand-written
        # list that drifted from what the code requires, which is the third
        # time that exact defect class has surfaced (CORS allow_methods was the
        # other two). Found in Epic 3.8 by running this script against avp_dev
        # for the first time since Epic 8 shipped; see build-log Epic 3.8.
        as_dict = json.loads(payload)
        generated = as_dict.pop("actionItems", [])
        swept = json.dumps(as_dict)

        leaked = [s for s in FORBIDDEN_SUBSTRINGS if s in swept]
        if leaked:
            failures.append(f"report payload contains prose-bearing keys: {leaked}")
            print(f"  LEAKED       : {leaked}")
        else:
            print("  no prose-bearing key present outside actionItems. OK")

        # The exemption is a hole in the guard, so its edges are asserted.
        #
        # The FIRST version of this check compared each item's keys against
        # `{to_camel(f) for f in ActionItemOut.model_fields}` and looked for a
        # surplus. That was a tautology: the items are serialised BY
        # ActionItemOut, so an undeclared key can never appear, and a prose
        # field ADDED to the schema lands on both sides and cancels out. It
        # could only ever have fired on post-serialisation tampering — which is
        # exactly what the negative control injected, so the control passed and
        # proved nothing. Adding `summary: str` to ActionItemOut sailed through
        # it. See build-log Epic 3.8.
        #
        # Replaced with a SUBSTRING sweep over each item's own keys, which is
        # the discipline the repo settled on in Epic 8 for the same reason:
        # nobody names a leak field `snippet`, they name it
        # `competitor_snippet`, and an exact-name set never sees it.
        DOCUMENTED_PROSE = {"title", "detail"}
        PROSE_WORDS = (
            "text", "answer", "response", "snippet", "excerpt", "quote", "body",
            "content", "html", "raw", "description", "tagline", "summary",
            "positioning", "about", "headline", "note", "context", "commentary",
            "rationale", "copy", "blurb", "verbatim", "prose",
        )
        def walk(node: object, path: str) -> list[tuple[str, str]]:
            """Every key under `node`, at any depth, with its dotted path.

            Recursive rather than depth-1 on purpose. The sweep it replaces
            searched raw serialised JSON, so nesting could not hide a key from
            it; a depth-1 replacement would have turned a one-field exemption
            into an arbitrarily deep one, and `evidence: [{"snippet": ...}]` is
            exactly the shape a future epic would reach for.
            """
            found: list[tuple[str, str]] = []
            if isinstance(node, dict):
                for key, value in node.items():
                    found.append((key, f"{path}.{key}"))
                    found.extend(walk(value, f"{path}.{key}"))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    found.extend(walk(value, f"{path}[{index}]"))
            return found

        if generated:
            inspected = 0
            for position, item in enumerate(generated):
                for key, where in walk(item, f"actionItems[{position}]"):
                    inspected += 1
                    if key in DOCUMENTED_PROSE:
                        continue
                    snake = to_snake(key)
                    hits = [w for w in PROSE_WORDS if w in snake]
                    if hits:
                        failures.append(
                            f"{where} looks like prose ({', '.join(hits)}) and is not "
                            "one of the two documented fields"
                        )
            # A sweep that covered nothing must not read as a sweep that passed.
            if inspected == 0:
                failures.append("action items carried no fields — the sweep was vacuous")
            fields = sorted({k for item in generated for k in item} & DOCUMENTED_PROSE)
            print(f"  actionItems  : {len(generated)} items, {inspected} keys inspected,"
                  f" prose fields = {fields} (ours by design — models/action_item.py)")
            if not failures:
                print("    -> and no other field on them looks like prose. OK")
        else:
            # Not a failure — a scan with no generated fixes is a real state —
            # but PASS must not be read as "the exemption was checked".
            print("  actionItems  : none on this scan — THE EXEMPTION BOUNDARY WAS NOT"
                  " EXERCISED by this run")

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

        # ------------------------------------------------------------------
        rule("PART 4 — Epic 7.1: the Answer Shelf, from the same REAL rows")
        # ------------------------------------------------------------------
        shelf = report.proof.prompt_shelf
        print(f"\n  rows: {len(shelf)} (one per answer)\n")
        for row in shelf:
            named = " > ".join(
                f"{'*' if s.is_subject else ''}{s.entity_name}({s.position})"
                + ("^" if s.cited else "")
                for s in row.slots
            )
            place = (
                "NOT ANSWERED" if not row.answered
                else f"#{row.subject_position}" if row.subject_present
                else "ABSENT"
            )
            shown = named or "(nobody named)"
            print(f"    p{row.prompt_position} {row.engine.value:14} {place:12} {shown}")
        print("\n    * = subject   ^ = cited in that same answer")

        # THE IDENTITY. Every answered row carries exactly one subject mark:
        # a place, or an explicit absence. A row that carries neither renders
        # nothing, and a chart that silently omits an absence deletes the only
        # finding it exists to show.
        unmarked = [
            f"p{r.prompt_position}/{r.engine.value}"
            for r in shelf
            if r.answered and not r.subject_present and r.subject_position is not None
        ]
        if unmarked:
            failures.append(f"rows carry a position while reporting absence: {unmarked}")
        answered_rows = [r for r in shelf if r.answered]
        absent_rows = [r for r in answered_rows if not r.subject_present]
        print(f"\n    {len(answered_rows)} answered rows, {len(absent_rows)} of them absences")
        if len(shelf) != report.proof.engine_results:
            failures.append(
                f"shelf has {len(shelf)} rows for {report.proof.engine_results} answers —"
                " an answer with no row draws nothing at all"
            )
        else:
            print("    -> every answer has a row. OK")

        # The row label is OUR generated question. Asserted against the prompts
        # table rather than eyeballed, because this is the one prose field in
        # the whole report projection and ip-safety.md #7 turns on it being ours.
        prompt_texts = {
            row[0] for row in (await session.execute(
                select(Prompt.text).join(PromptSet, PromptSet.id == Prompt.prompt_set_id)
                .where(PromptSet.scan_id == scan.id)
            )).all()
        }
        foreign = sorted({r.prompt_text for r in shelf} - prompt_texts)
        if foreign:
            failures.append(f"shelf row labels are not our stored prompts: {foreign}")
        else:
            print(f"    -> all {len(shelf)} row labels are our own stored prompts, verbatim. OK")

        # ------------------------------------------------------------------
        rule("PART 5 — Epic 7.1: the unclaimed domain, and the fix it produces")
        # ------------------------------------------------------------------
        print(f"\n  subject cited {report.proof.subject_citations} times"
              f" of {report.proof.total_citations} total\n")
        print("  cited domains, as the EVIDENCE table ranks them (attributed first):")
        for row in report.proof.competitor_cited_domains[:6]:
            who = row.competitor_name or "third party"
            print(f"    {row.citations:3}  {row.domain:24} {who}")

        print("\n  cited domains, as the FIX ranks them (unclaimed, by weight):")
        for row in report.proof.unclaimed_cited_domains:
            print(f"    {row.citations:3}  {row.domain}")

        if report.proof.unclaimed_cited_domains:
            heaviest = report.proof.unclaimed_cited_domains[0]
            claimed = {
                d.domain for d in report.proof.competitor_cited_domains if d.competitor_name
            } | {d.domain for d in report.proof.subject_cited_domains}
            if heaviest.domain in claimed:
                failures.append(f"{heaviest.domain} is attributed — it is not unclaimed")
            # The point of the separate field: the evidence table's ranking
            # would have buried this behind every attributed domain.
            table_rank = [d.domain for d in report.proof.competitor_cited_domains]
            position = (
                table_rank.index(heaviest.domain) + 1 if heaviest.domain in table_rank else None
            )
            print(f"\n    heaviest unclaimed : {heaviest.domain} ({heaviest.citations} citations)")
            print(f"    its rank in the evidence table : {position}")
            if heaviest.citations <= report.proof.subject_citations:
                print("    note: the subject is cited at least as often — a weaker case")
            else:
                print(f"    -> out-cites the subject's own pages"
                      f" ({heaviest.citations} vs {report.proof.subject_citations}). OK")
        else:
            print("\n    no unclaimed domain cleared the floor on this scan")

        print("\n  REPORT URLS")
        print(f"    real      : /scans/{report.scan_id}/report")
        print(f"    degraded  : /scans/{degraded_report.scan_id}/report")

    await engine.dispose()

    rule("RESULT")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1
    source = "SEEDED" if ran_against_synthetic else "real"
    print(f"  PASS — a full report renders from {source} persisted data, the breakdown")
    print("         re-sums to the stored composite, the payload carries facts only,")
    print("         and a genuinely unscoreable scan reports as null rather than zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

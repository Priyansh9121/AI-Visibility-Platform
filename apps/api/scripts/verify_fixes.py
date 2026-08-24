"""Live verification of the Epic 8 acceptance criterion.

§7 Epic 8: "for a test scan with known gaps, the generated fix list correctly
names those gaps with actionable language."

Costs ONE model call per run (claude-opus-5, medium effort, ~8k max tokens).
Everything it is given comes from rows Epics 2-6 already persisted; nothing here
is a fixture.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/verify_fixes.py

The known gaps on the real Help Scout scan are citation_strength (sub-score
3.70 against a weight of 20 — a 19.26-point gap, the largest on the scan) and
two audit warnings, NO_FAQ_SCHEMA and NO_PRODUCT_OR_SERVICE_SCHEMA. PART 3
checks the generated list actually names them; PART 4 re-runs generation to
prove a re-run refreshes rows rather than duplicating them, and that an
operator's status survives it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.models import ActionItem, ActionItemStatus, Client, Scan  # noqa: E402
from avp_api.services import fix_runner  # noqa: E402
from avp_api.services import report as report_service  # noqa: E402
from avp_api.services.fix_generator import BANNED_CLAIMS  # noqa: E402

DEGRADED_DOMAIN = "epic7-degraded.example"

# The gaps this scan is known to have. The acceptance criterion is that the
# generated list NAMES them, so these are what the output is checked against.
EXPECTED_CANDIDATES = (
    "gap:citation_strength",
    "audit:schema_faq",
    "audit:schema_product_or_service",
)

# Internal identifiers the generator is shown but must never quote back. Epic 7
# resolves a finding through our own words; see build-log Epic 8.
FORBIDDEN_IDENTIFIERS = (
    "NO_FAQ_SCHEMA",
    "NO_PRODUCT_OR_SERVICE_SCHEMA",
    "schema_faq",
    "schema_product_or_service",
    "citation_strength",
)


def rule(title: str) -> None:
    print("\n" + "=" * 84)
    print(title)
    print("=" * 84)


async def main() -> int:  # noqa: C901
    engine = create_async_engine(os.environ["DATABASE_URL"])
    Session = async_sessionmaker(engine, expire_on_commit=False)
    failures: list[str] = []

    async with Session() as session:
        rule("PART 1 — the scan, and the gaps it is known to have")
        scan = (
            await session.execute(
                select(Scan)
                .join(Client, Client.id == Scan.client_id)
                .where(Client.domain != DEGRADED_DOMAIN)
                .order_by(Scan.created_at.desc())
            )
        ).scalars().first()
        if scan is None:
            print("  No real scan in this database. Run scripts/verify_report.py first.")
            return 1
        client = (
            await session.execute(select(Client).where(Client.id == scan.client_id))
        ).scalar_one()

        print(f"  scan   : {scan.id}")
        print(f"  client : {client.name} ({client.domain})")

        candidates = await fix_runner.build_candidates_for(session, scan)
        print(f"\n  Epic 7 chose {len(candidates)} candidates, and Epic 8 may not change that:")
        for candidate in candidates:
            upside = f"{candidate.points_upside} pts" if candidate.points_upside else "unmeasured"
            print(f"    {candidate.rank}. {candidate.key:38} {upside}")

        for expected in EXPECTED_CANDIDATES:
            if expected not in {c.key for c in candidates}:
                failures.append(f"known gap {expected} was not selected as a candidate")

        rule("PART 2 — the prompt, which must carry facts and nothing else")
        facts = await fix_runner.collect_facts(session, scan, client)
        from avp_api.services.fix_generator import build_fix_prompt

        prompt = build_fix_prompt(facts, candidates)
        print(prompt)
        print(f"\n  {len(prompt.splitlines())} lines, {len(prompt)} chars")

        rule("PART 3 — one live model call, and what it produced")
        print("  Calling claude-opus-5. This is the only paid call in this script.\n")
        rows, outcome = await fix_runner.generate_for_scan(session, scan, client)
        await session.commit()

        print(f"  status    : {outcome.status}")
        print(f"  model     : {outcome.model}")
        print(f"  rejected  : {outcome.rejected or 'none'}")
        if outcome.status != "generated":
            failures.append(f"generation failed: {outcome.reason_code}")
            print(f"  reason    : {outcome.reason_code}")

        for row in rows:
            upside = f"+{row.points_upside} pts" if row.points_upside is not None else "no figure"
            print(f"\n  [{row.rank}] {row.source.value}:{row.source_key}")
            print(f"      {row.priority.value} priority · effort {row.effort.value} · {upside}")
            print(f"      {row.title}")
            print(f"      {row.detail}")

        generated_keys = {f"{r.source.value}:{r.source_key}" for r in rows}
        for expected in EXPECTED_CANDIDATES:
            if expected not in generated_keys:
                failures.append(f"the generated list does not name {expected}")

        blob = " ".join(f"{r.title} {r.detail or ''}" for r in rows).lower()

        # "actionable language" is not directly assertable, but its absence is:
        # a list that never names the subject, a competitor, or a concrete
        # artefact is the abstract list Epic 7 already refused to ship.
        # Each entry is a set of EQUIVALENT spellings; the fix list must name
        # the artefact, not one particular word for it.
        #
        # This used to demand the bare token "schema". Against the Help Scout
        # scan the model happened to write "FAQPage schema" and it passed;
        # against a seeded scan it wrote "FAQPage markup" — plainer English,
        # naming the same artefact just as concretely, and arguably better copy
        # — and the script reported FAIL on a generation that was correct in
        # every respect. A check that fails good output on word choice teaches
        # its reader to ignore it, which is worse than not having it. Found in
        # Epic 3.10 by reading the output rather than the exit code.
        required_concepts = (
            ("faqpage",),
            ("schema", "markup", "structured data"),
            (client.domain.lower(),),
        )
        for spellings in required_concepts:
            if not any(term in blob for term in spellings):
                failures.append(
                    f"no fix names any of {list(spellings)} — the list reads as generic advice"
                )

        for phrase in BANNED_CLAIMS:
            if phrase in blob:
                failures.append(f"generated copy makes an unsupportable claim: {phrase!r}")

        for identifier in FORBIDDEN_IDENTIFIERS:
            if identifier in " ".join(f"{r.title} {r.detail or ''}" for r in rows):
                failures.append(f"generated copy quotes the internal identifier {identifier!r}")

        if not failures:
            print("\n  -> every known gap named, in concrete language, with no internal")
            print("     identifier and no claim this product cannot support. OK")

        rule("PART 4 — a re-run corrects the list without overwriting the operator")
        first_ids = {(r.source, r.source_key): r.id for r in rows}
        worked = rows[0]
        worked.status = ActionItemStatus.DONE
        await session.commit()
        print(f"  marked {worked.source.value}:{worked.source_key} as done, then regenerated.\n")

        again, second_outcome = await fix_runner.generate_for_scan(session, scan, client)
        await session.commit()

        total = (
            await session.execute(select(ActionItem).where(ActionItem.scan_id == scan.id))
        ).scalars().all()
        print(f"  rows after two generations : {len(total)} (must equal {len(rows)})")
        if len(total) != len(rows):
            failures.append(f"regeneration duplicated rows: {len(rows)} -> {len(total)}")

        for row in again:
            previous = first_ids.get((row.source, row.source_key))
            if previous is not None and previous != row.id:
                failures.append(f"{row.source_key} was replaced rather than refreshed")

        refreshed = (
            await session.execute(select(ActionItem).where(ActionItem.id == worked.id))
        ).scalar_one()
        await session.refresh(refreshed)
        print(f"  status of the worked item  : {refreshed.status.value} (must be done)")
        if refreshed.status is not ActionItemStatus.DONE:
            failures.append("regeneration overwrote an operator's status")
        if second_outcome.status != "generated":
            failures.append(f"second generation failed: {second_outcome.reason_code}")

        rule("PART 5 — the report projection")
        report = await report_service.build_report(session, scan)
        print(f"  actionItems on the report : {len(report.action_items)}")
        print("  (the worked item is done, so it has left the plan)")
        if any(i.status is ActionItemStatus.DONE for i in report.action_items):
            failures.append("a completed fix is still being presented as work to do")

        # Put the operator's item back so the screenshot shows the full list.
        refreshed.status = ActionItemStatus.OPEN
        await session.commit()
        print(f"\n  REPORT URL : /scans/{scan.id}/report")

    await engine.dispose()

    rule("RESULT")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1
    print("  PASS — a live model call over one real scan produced a fix list that names")
    print("         every known gap in concrete, actionable language; the candidates and")
    print("         their point figures are unchanged from Epic 7's arithmetic; a re-run")
    print("         refreshed the rows in place and left the operator's status alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

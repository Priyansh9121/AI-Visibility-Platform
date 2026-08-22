"""Live verification of the Epic 3 manual-override guarantee — shipped in 3.6.

§7 Epic 3: "Manual override/edit UI for competitor list."

Epic 3 shipped `Competitor.is_manual_override` and `persist_detection`'s
preserve-on-re-detection logic, and Epic 3.6 shipped the UI that reaches them.
The mechanism has had unit coverage since Epic 3 but has never been run against
real data, and the claim it makes — "an operator who has corrected a bad set
must not have their correction silently undone by the next run" — is the kind
that is only worth as much as its last real execution.

Costs nothing. Detection is NOT re-run: a `DetectionOutcome` is constructed
directly and handed to the real `persist_detection`, so no SerpApi search and
no model call is made. Everything else is the real service path.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/verify_competitor_override.py

The database is restored to its exact starting state before exit — the Epic 7
and Epic 8 screenshots and the checked-in report fixtures are taken from this
scan, and a competitor set left in a corrected state would silently invalidate
them. PART 6 verifies the restore rather than assuming it.
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

from avp_api.models import (  # noqa: E402
    Competitor,
    CompetitorSet,
    DetectionStatus,
    Scan,
)
from avp_api.services import report as report_service  # noqa: E402
from avp_api.services import scoring_runner  # noqa: E402
from avp_api.services.competitors import (  # noqa: E402
    Candidate,
    DetectionOutcome,
    apply_override,
    persist_detection,
)

# The rival an operator would plausibly strike: SERP-only, uncorroborated, and
# a listicle publisher rather than a help-desk vendor.
REMOVE = "Thecxlead"
# A rival detection never surfaced, added by hand.
ADD_NAME = "Intercom"
ADD_DOMAIN = "intercom.com"


def rule(title: str) -> None:
    print("\n" + "=" * 84)
    print(title)
    print("=" * 84)


def snapshot(competitors: list[Competitor]) -> list[dict[str, object]]:
    """Everything needed to rebuild the set exactly as it was."""
    return [
        {
            "id": c.id,
            "name": c.name,
            "domain": c.domain,
            "rank": c.rank,
            "detection_source": c.detection_source,
            "signal_count": c.signal_count,
            "serp_mentions": c.serp_mentions,
            "co_citation_mentions": c.co_citation_mentions,
            "corroborated": c.corroborated,
            "score": c.score,
            "is_manual_override": c.is_manual_override,
        }
        for c in sorted(competitors, key=lambda c: c.rank)
    ]


def show(competitors: list[Competitor]) -> None:
    for c in sorted(competitors, key=lambda c: c.rank):
        mark = "  <- set by hand" if c.is_manual_override else ""
        print(
            f"    {c.rank}. {c.name:14} {(c.domain or '-'):18} "
            f"source={c.detection_source.value:11}{mark}"
        )


async def main() -> int:  # noqa: C901
    engine = create_async_engine(os.environ["DATABASE_URL"])
    Session = async_sessionmaker(engine, expire_on_commit=False)
    failures: list[str] = []

    async with Session() as session:
        rule("PART 1 — the detected set, as it stands")
        competitor_set = (
            await session.execute(
                select(CompetitorSet).order_by(CompetitorSet.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if competitor_set is None:
            print("  No competitor set in this database. Run detection first.")
            return 1
        await session.refresh(competitor_set, ["competitors"])

        scan = (
            await session.execute(select(Scan).where(Scan.id == competitor_set.scan_id))
        ).scalar_one()

        original = snapshot(competitor_set.competitors)
        original_confidence = competitor_set.detection_confidence
        original_status = competitor_set.status
        print(f"  scan  : {scan.id}")
        print(f"  set   : {competitor_set.id}  confidence={original_confidence}")
        show(competitor_set.active_competitors)

        if not any(c["name"] == REMOVE for c in original):
            print(f"\n  {REMOVE} is not in this set; nothing to correct. Aborting.")
            return 1
        if any(c["is_manual_override"] for c in original):
            print("\n  This set already carries overrides. Aborting to avoid confusing state.")
            return 1

        rule("PART 2 — an operator corrects it")
        print(f"  Removing {REMOVE} (serp-only, uncorroborated) and adding {ADD_NAME}.\n")

        # THE REAL SERVICE, not a copy of it. The first version of this
        # script reimplemented the endpoint's logic inline, and passed against
        # its own reimplementation while the real endpoint was still deleting
        # struck rivals without recording them — the script and the code under
        # test were two copies of the same mistake. `apply_override` was pulled
        # out of the router precisely so there is one implementation to verify.
        kept = [c for c in original if c["name"] != REMOVE]
        corrected: list[tuple[str, str | None]] = [
            (str(c["name"]), c["domain"]) for c in kept  # type: ignore[arg-type]
        ]
        corrected.append((ADD_NAME, ADD_DOMAIN))

        await apply_override(session, competitor_set, corrected)
        await session.commit()
        await session.refresh(competitor_set, ["competitors"])

        show(competitor_set.active_competitors)
        names = {c.name for c in competitor_set.active_competitors}
        if REMOVE in names:
            failures.append(f"{REMOVE} survived the correction")
        if ADD_NAME not in names:
            failures.append(f"{ADD_NAME} was not added")
        if not all(c.is_manual_override for c in competitor_set.active_competitors):
            failures.append("the correction did not mark every row as an override")
        if competitor_set.detection_confidence is not None:
            failures.append(
                "the corroboration figure survived a correction nothing corroborated"
            )
        if not failures:
            print("\n  -> every row is now an override, corroboration cleared. OK")

        rule("PART 3 — re-detection runs, and must not undo the correction")
        # A real DetectionOutcome, constructed rather than fetched: this script
        # must not spend money to prove a persistence guarantee. It re-offers
        # the rival the operator struck, plus one it never saw.
        rerun = DetectionOutcome(
            candidates=[
                Candidate(
                    name=REMOVE,
                    domain="thecxlead.com",
                    serp_positions=[1, 2, 3],
                    serp_queries=["q1", "q2", "q3"],
                    score=0.889,
                ),
                Candidate(
                    name="Gorgias",
                    domain="gorgias.com",
                    serp_positions=[4],
                    serp_queries=["q1"],
                    co_citation_positions=[2],
                    score=1.4,
                ),
            ],
            status=DetectionStatus.OK,
            detection_confidence=Decimal("0.500"),
            serp_queries_run=6,
            co_citation_prompts_run=1,
            candidates_considered=30,
            used_industry_seed=True,
        )
        print(f"  Re-detection re-offers {REMOVE} (struck by the operator) and Gorgias (new).\n")

        await persist_detection(session, scan, rerun)
        await session.commit()
        await session.refresh(competitor_set, ["competitors"])
        show(competitor_set.active_competitors)

        after = {c.name: c for c in competitor_set.active_competitors}
        for kept_row in kept:
            name = str(kept_row["name"])
            if name not in after:
                failures.append(f"re-detection dropped the operator's row {name}")
            elif not after[name].is_manual_override:
                failures.append(f"re-detection cleared the override flag on {name}")
        if ADD_NAME not in after:
            failures.append(f"re-detection dropped the operator's addition {ADD_NAME}")
        if REMOVE in after:
            failures.append(
                f"re-detection reinstated {REMOVE}, which the operator had struck"
            )
        if "Gorgias" not in after:
            failures.append("re-detection failed to add a genuinely new candidate")
        else:
            manual_ranks = [
                c.rank for c in competitor_set.active_competitors if c.is_manual_override
            ]
            if manual_ranks and after["Gorgias"].rank <= max(manual_ranks):
                failures.append("a newly detected row outranked the operator's picks")
        if not failures:
            print("\n  -> every operator row survived with its flag intact, the struck rival")
            print("     stayed struck, and the new candidate was appended below them. OK")

        rule("PART 4 — the correction reaches scoring")
        _row, _computed, comparisons = await scoring_runner.score_scan(
            session, scan, persist=False
        )
        print(f"  score_scan compared against {len(comparisons)} rivals:")
        for c in comparisons:
            print(f"    {c.name:14} mention_rate={c.mention_rate} sov={c.share_of_voice}")
        # Asserted BEFORE the membership check: `all()` over an empty list is
        # true, and an empty comparison list is exactly the failure this part
        # exists to detect. The unit-test version of this check passed for that
        # reason and was removed rather than left looking like evidence.
        if not comparisons:
            failures.append("scoring produced no competitor comparison at all")
        else:
            compared = {c.name for c in comparisons}
            if REMOVE in compared:
                failures.append(f"scoring still compares against {REMOVE}")
            if ADD_NAME not in compared:
                failures.append(f"scoring does not compare against the added rival {ADD_NAME}")
            if not failures:
                print("\n  -> Share of Voice is computed against the corrected set, with no")
                print("     change to any Epic 5 code. OK")

        rule("PART 5 — the correction reaches the report")
        report = await report_service.build_report(session, scan)
        assert report.competitor_set is not None
        marked = [c.name for c in report.competitor_set.competitors if c.is_manual_override]
        print(f"  report competitors : {[c.name for c in report.competitor_set.competitors]}")
        print(f"  marked set-by-hand : {marked}")
        print(f"  detectionConfidence: {report.competitor_set.detection_confidence}")
        if not marked:
            failures.append("the report projection lost the provenance flag")

        # FINDING 3 (api-contracts.md, Open findings) — known, deliberately open.
        #
        # `apply_override` clears detection_confidence, but the re-detection in
        # PART 3 sets it again from its own outcome — and that outcome measured
        # agreement across only the rows detection found, not the operator's.
        # The report therefore presents one corroboration figure for a set that
        # is now part-detected and part-hand-set.
        #
        # Left alone because changing it means redefining what the field
        # measures, which is an Epic 3 scoring-semantics decision rather than a
        # UI one. Epic 3.6's badge is what made it visible; recorded in
        # build-log Epic 3.6 as open.
        if report.competitor_set.detection_confidence is not None and marked:
            print(
                f"\n  NOTE  detectionConfidence={report.competitor_set.detection_confidence} "
                f"describes only the {len(report.competitor_set.competitors) - len(marked)} "
                "detected rows, but is presented for the whole set. Finding 3, open."
            )

        payload = report.model_dump_json(by_alias=True)
        for forbidden in ('"description"', '"tagline"', '"summary"', '"note"'):
            if forbidden in payload:
                failures.append(f"the report payload carries {forbidden} after an override")
        if not failures:
            print("\n  -> the badge the report renders reads a real column, and the payload")
            print("     is still facts only. OK")

        rule("PART 6 — restoring the database, and verifying the restore")
        for existing in list(competitor_set.competitors):
            await session.delete(existing)
        competitor_set.competitors = []
        await session.flush()
        for row in original:
            competitor_set.competitors.append(
                Competitor(competitor_set_id=competitor_set.id, **row)
            )
        competitor_set.detection_confidence = original_confidence
        competitor_set.status = original_status
        await session.commit()
        await session.refresh(competitor_set, ["competitors"])

        restored = snapshot(competitor_set.competitors)  # includes any tombstone
        show(competitor_set.active_competitors)
        if restored != original:
            failures.append("the database was NOT restored to its starting state")
            print("\n  MISMATCH — Epic 7/8 fixtures are taken from this set.")
        elif competitor_set.detection_confidence != original_confidence:
            failures.append("detection_confidence was not restored")
        else:
            print("\n  -> byte-identical to the starting snapshot, ids included. OK")

    await engine.dispose()

    rule("RESULT")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1
    print("  PASS — an operator's correction to a real competitor set survived a real")
    print("         re-detection with its provenance intact, reached both scoring and the")
    print("         report with no Epic 5-7 code involved, and the database was restored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

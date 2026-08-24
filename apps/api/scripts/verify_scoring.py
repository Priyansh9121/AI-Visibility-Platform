"""Live verification of the Epic 5 acceptance criterion.

§7 Epic 5: "score recalculates correctly and deterministically from a given
EngineResult set."

Runs a REAL pipeline end to end against the dev database — real SerpApi
competitor detection, real Claude engine calls, real extraction — then scores
the persisted rows. **Scoring itself makes no provider calls**; the cost is all
in producing genuine data to score.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/verify_scoring.py --prompts 3

Re-runs the scoring step several times to demonstrate reproducibility on real
data, not a synthetic fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api import ids  # noqa: E402
from avp_api.config import Settings  # noqa: E402
from avp_api.models import Agency, Client, Scan  # noqa: E402
from avp_api.models.client import ClassificationStatus, ClientKind  # noqa: E402
from avp_api.services import audit_runner, scan_runner, scoring_runner  # noqa: E402
from avp_api.services import competitors as detection  # noqa: E402
from avp_api.services.scoring import Dimension  # noqa: E402

SUBJECT_DOMAIN = "helpscout.com"
SUBJECT_BRAND = "Help Scout"
SUBJECT_INDUSTRY = "customer support software"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=int, default=3)
    parser.add_argument("--skip-detection", action="store_true")
    # The audit is a Playwright crawl of one page and costs nothing. It is
    # on by default because without it this script only ever exercised the
    # degraded four-dimension path — Finding 6.
    parser.add_argument("--skip-audit", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    url = os.environ.get("DATABASE_URL", str(settings.database_url))
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        agency = Agency(
            id=ids.new_id(ids.AGENCY), name="Scoring Verification",
            slug=f"scoring-{ids.new_id(ids.AGENCY)[-8:].lower()}",
        )
        session.add(agency)
        await session.flush()

        client = Client(
            id=ids.new_id(ids.CLIENT), agency_id=agency.id,
            name=SUBJECT_BRAND, domain=SUBJECT_DOMAIN, brand_name=SUBJECT_BRAND,
            kind=ClientKind.PROSPECT, industry=SUBJECT_INDUSTRY,
            # Required by ck_clients_industry_matches_classification_status:
            # `industry` may only be set when the status is CLASSIFIED. The
            # constraint is Epic 2's guard against a guess being stored as a
            # result, and it caught this script setting one without the other.
            classification_status=ClassificationStatus.CLASSIFIED,
            industry_confidence="high",
        )
        session.add(client)
        await session.commit()
        print(f"client   : {client.id}  {client.domain}  industry={client.industry}")

        scan = await detection.get_or_create_scan(session, client)
        await session.commit()
        # Captured as a plain str up front. `scan` gets expired and re-fetched
        # twice below, and a `scan.id` read on an expired instance is a lazy
        # load outside a greenlet — MissingGreenlet, not a nice error.
        scan_id = scan.id
        print(f"scan     : {scan.id}")

        if not args.skip_detection:
            print("\n[1/4] competitor detection (real SerpApi + Claude)...")
            outcome = await detection.detect_for_client(client, settings=settings)
            cset = await detection.persist_detection(session, scan, outcome)
            await session.commit()
            print(f"      status={cset.status.value} confidence={cset.detection_confidence} "
                  f"competitors={len(outcome.candidates)}")
            for c in outcome.candidates:
                print(f"        {c.resolved_name():22} {str(c.domain):26} {c.source.value}")

        print(f"\n[2/4] scan: {args.prompts} prompts x 2 engines (real Claude calls)...")
        await scan_runner.run_scan(session, scan, client,
                                   settings=settings, prompt_limit=args.prompts)
        await session.commit()
        print(f"      status={scan.status.value} prompts={scan.prompt_count} "
              f"results={scan.engine_result_count}")

        print("\n[3/4] scoring, BEFORE any audit (no provider calls)...")
        row, computed, competitors = await scoring_runner.score_scan(session, scan)
        await session.commit()

        print(f"\n{'='*78}")
        print(f"  AI VISIBILITY SCORE : {computed.composite}   (status={computed.status})")
        print(f"  formula_version     : {computed.formula_version}")
        print(f"  inputs_digest       : {computed.inputs_digest}")
        print(f"{'-'*78}")
        for dim, sub in sorted(computed.sub_scores.items(), key=lambda kv: kv[0].value):
            if sub.included:
                contribution = (sub.weight * sub.value) / 100
                print(f"  {dim.value:22} {str(sub.value):>7}  x {str(sub.weight):>6}%"
                      f"  = {contribution:>6.2f}")
            else:
                print(f"  {dim.value:22} {'--':>7}    EXCLUDED  {sub.reason}")
        print(f"{'-'*78}")
        print(f"  degradation_flags   : {computed.degradation_flags}")
        if competitors:
            print(f"{'-'*78}")
            print(f"  {'competitor':24} {'mention':>8} {'SoV':>8} {'citations':>10}")
            for c in competitors:
                print(f"  {c.name[:24]:24} {str(c.mention_rate):>8} "
                      f"{str(c.share_of_voice):>8} {str(c.citation_strength):>10}")
        print(f"{'='*78}")

        # --- FINDING 6: the five-dimension path ------------------------------
        #
        # Everything above scored a scan with no technical audit, so
        # `technical_foundation` was NOT_YET_MEASURED and only four of the five
        # dimensions were included. That was this script's whole coverage until
        # Epic 3.11, and it is why nothing caught `compute_inputs_digest`
        # omitting `technical_foundation` for four epics (Epic 3.10).
        #
        # A re-audit would not close the gap: it is the TRANSITION out of the
        # exclusion that has never been exercised live. So the audit runs here,
        # after a first score, and the second score is compared against it.
        five_dimension_ok = True
        if not args.skip_audit:
            print(f"\n[4/4] technical audit of {client.domain} (Playwright crawl, no API cost)...")

            before_excluded = {
                dim for dim, sub in computed.sub_scores.items() if not sub.included
            }
            before_digest = computed.inputs_digest
            before_composite = computed.composite
            if Dimension.TECHNICAL_FOUNDATION not in before_excluded:
                print("  WARN  technical_foundation was already included before the audit;")
                print("        this run does not exercise the transition.")
                five_dimension_ok = False

            audit, outcome = await audit_runner.run_audit(session, scan, client)
            await session.commit()
            print(f"      status={audit.status.value} score={outcome.score} "
                  f"checks={len(audit.checks)}")

            scan = await session.get(Scan, scan_id)
            _, after, _ = await scoring_runner.score_scan(session, scan)
            await session.commit()

            included = sorted(d.value for d, sub in after.sub_scores.items() if sub.included)
            print(f"\n  composite    : {before_composite}  ->  {after.composite}")
            print(f"  inputs_digest: {before_digest}  ->  {after.inputs_digest}")
            print(f"  included     : {len(included)}/{len(list(Dimension))}  {included}")
            print(f"  flags        : {after.degradation_flags}")

            checks = [
                (
                    "technical_foundation is now included",
                    after.sub_scores[Dimension.TECHNICAL_FOUNDATION].included,
                ),
                ("every dimension is included", len(included) == len(list(Dimension))),
                (
                    "the NOT_YET_MEASURED flag is gone",
                    "TECHNICAL_FOUNDATION_NOT_MEASURED" not in after.degradation_flags,
                ),
                # The regression test for the Epic 3.10 defect. Everything else
                # about the scan is byte-identical across these two scores, so
                # if the digest does not move, `technical_foundation` is not in
                # it — which is exactly the bug that shipped for four epics and
                # let two genuinely different composites share one digest.
                ("the digest moved when the fifth input appeared",
                 after.inputs_digest != before_digest),
            ]
            for label, passed in checks:
                print(f"  {'OK  ' if passed else 'FAIL'}  {label}")
                if not passed:
                    five_dimension_ok = False

            # The determinism loop below now runs over the FIVE-dimension path
            # rather than the degraded one, which is the other half of what
            # Finding 6 was missing: reproducibility was only ever demonstrated
            # for a formula that excluded one of its inputs.
            computed = after
        else:
            print("\n[4/4] SKIPPED (--skip-audit) — five-dimension path NOT covered.")

        # Reproducibility on REAL data, now over the FIVE-dimension path.
        print("\nre-scoring the same persisted rows 5 times:")
        composites, digests, row_ids = set(), set(), set()
        for _ in range(5):
            # Expire the cached rows before each pass, so every iteration
            # genuinely re-reads from Postgres.
            #
            # The sessionmaker sets expire_on_commit=False, so without this the
            # identity map hands back the SAME Python objects loaded on the
            # first pass. The loop still re-queried, but compared values it had
            # already cached — so it could catch nondeterminism in the scoring
            # arithmetic while being blind to nondeterminism in the READ path,
            # which is exactly what compute_inputs_digest's explicit sorting
            # exists to defend against (scoring-spec rule 1).
            #
            # The Scan is re-fetched with an awaited get() rather than left
            # expired: a bare expire_all() expires `scan` too, and the next
            # `scan.id` access is then a lazy load outside a greenlet, which
            # raises MissingGreenlet. Found by running this change before
            # committing it — the first version of this fix broke the script.
            session.expire_all()
            scan = await session.get(Scan, scan_id)
            r2, c2, _ = await scoring_runner.score_scan(session, scan)
            await session.commit()
            composites.add(str(c2.composite))
            digests.add(c2.inputs_digest)
            row_ids.add(r2.id)
        print(f"  distinct composites : {composites}")
        print(f"  distinct digests    : {len(digests)}")
        print(f"  distinct score rows : {len(row_ids)} (idempotent per formula version)")

        deterministic = len(composites) == 1 and len(digests) == 1 and len(row_ids) == 1

        if args.skip_audit:
            # Kept as an explicit caveat rather than dropped: a PASS from this
            # mode covers four dimensions, and saying so is the difference
            # between a verified formula and a verified subset of one.
            print("\nNOTE: --skip-audit, so technical_foundation is excluded and the")
            print("      FIVE-dimension path is NOT covered by this run.")

        print(f"\ndeterministic across re-scores : {deterministic}")
        print(f"five-dimension path verified   : "
              f"{'n/a (--skip-audit)' if args.skip_audit else five_dimension_ok}")

        ok = deterministic and computed.composite is not None and five_dimension_ok
        print("RESULT:", "PASS" if ok else "NEEDS REVIEW")

    await engine.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

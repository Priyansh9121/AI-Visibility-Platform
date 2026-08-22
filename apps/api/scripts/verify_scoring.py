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
from avp_api.models import Agency, Client  # noqa: E402
from avp_api.models.client import ClassificationStatus, ClientKind  # noqa: E402
from avp_api.services import competitors as detection  # noqa: E402
from avp_api.services import scan_runner, scoring_runner  # noqa: E402

SUBJECT_DOMAIN = "helpscout.com"
SUBJECT_BRAND = "Help Scout"
SUBJECT_INDUSTRY = "customer support software"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=int, default=3)
    parser.add_argument("--skip-detection", action="store_true")
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
        print(f"scan     : {scan.id}")

        if not args.skip_detection:
            print("\n[1/3] competitor detection (real SerpApi + Claude)...")
            outcome = await detection.detect_for_client(client, settings=settings)
            cset = await detection.persist_detection(session, scan, outcome)
            await session.commit()
            print(f"      status={cset.status.value} confidence={cset.detection_confidence} "
                  f"competitors={len(outcome.candidates)}")
            for c in outcome.candidates:
                print(f"        {c.resolved_name():22} {str(c.domain):26} {c.source.value}")

        print(f"\n[2/3] scan: {args.prompts} prompts x 2 engines (real Claude calls)...")
        await scan_runner.run_scan(session, scan, client,
                                   settings=settings, prompt_limit=args.prompts)
        await session.commit()
        print(f"      status={scan.status.value} prompts={scan.prompt_count} "
              f"results={scan.engine_result_count}")

        print("\n[3/3] scoring (no provider calls)...")
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

        # Reproducibility on REAL data.
        print("\nre-scoring the same persisted rows 5 times:")
        composites, digests, row_ids = set(), set(), set()
        for _ in range(5):
            r2, c2, _ = await scoring_runner.score_scan(session, scan)
            await session.commit()
            composites.add(str(c2.composite))
            digests.add(c2.inputs_digest)
            row_ids.add(r2.id)
        print(f"  distinct composites : {composites}")
        print(f"  distinct digests    : {len(digests)}")
        print(f"  distinct score rows : {len(row_ids)} (idempotent per formula version)")

        deterministic = len(composites) == 1 and len(digests) == 1 and len(row_ids) == 1
        print(f"\ndeterministic across re-scores : {deterministic}")
        print("RESULT:", "PASS" if deterministic and computed.composite is not None
              else "NEEDS REVIEW")

    await engine.dispose()
    return 0 if deterministic else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

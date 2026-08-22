"""Live verification of the Epic 6 acceptance criterion.

§7 Epic 6: "audit returns pass/fail + detail for each check on a known test
site."

Runs a REAL audit against real sites (a page load plus robots.txt and
sitemap.xml fetches — no model or search calls), then proves the scoring
integration by scoring a real scan BEFORE and AFTER the audit exists.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/verify_audit.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.models import Client, Scan  # noqa: E402
from avp_api.services import audit_runner, scoring_runner  # noqa: E402
from avp_api.services.scoring import Dimension  # noqa: E402
from avp_api.services.technical_audit import audit_site, score_audit  # noqa: E402

SITES = ["helpscout.com", "anthropic.com", "basecamp.com"]


async def main() -> int:
    print("=" * 84)
    print("PART 1 — live audits (page load + robots.txt + sitemap.xml)")
    print("=" * 84)

    for domain in SITES:
        signals = await audit_site(f"https://{domain}")
        outcome = score_audit(signals)
        print(f"\n{domain}   TECHNICAL FOUNDATION = {outcome.score}")
        print("  components : " + ", ".join(
            f"{k}={v}" for k, v in sorted(outcome.components.items())))
        if outcome.excluded_components:
            print(f"  excluded   : {outcome.excluded_components}")
        for check in outcome.checks:
            value = f" value={check.value}" if check.value is not None else ""
            code = f"  [{check.detail_code}]" if check.detail_code else ""
            print(f"    {check.status:15} {check.key:26}{value}{code}")

    print("\n" + "=" * 84)
    print("PART 2 — scoring integration: BEFORE vs AFTER the audit exists")
    print("=" * 84)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("  DATABASE_URL not set — skipping the integration proof")
        return 0

    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        scan = (
            await session.execute(select(Scan).order_by(Scan.id.desc()).limit(1))
        ).scalar_one_or_none()
        if scan is None:
            print("  no scan in the database — run scripts/verify_scoring.py first")
            await engine.dispose()
            return 0
        client = (
            await session.execute(select(Client).where(Client.id == scan.client_id))
        ).scalar_one()

        existing = await audit_runner.latest_audit(session, scan.id)
        if existing is not None:
            for check in list(existing.checks):
                await session.delete(check)
            await session.delete(existing)
            await session.commit()

        _, before, _ = await scoring_runner.score_scan(session, scan, persist=False)
        print(f"\n  scan {scan.id}  ({client.domain})")
        print("\n  BEFORE audit:")
        print(f"    composite            : {before.composite}")
        print(f"    technical_foundation : "
              f"{before.value(Dimension.TECHNICAL_FOUNDATION)}")
        print(f"    excluded             : {before.excluded_dimensions}")
        print(f"    flags                : {before.degradation_flags}")

        audit, outcome = await audit_runner.run_audit(session, scan, client)
        await session.commit()
        print(f"\n  audit run: status={audit.status.value} "
              f"technical_foundation={audit.technical_foundation} "
              f"checks={len(audit.checks)}")

        _, after, _ = await scoring_runner.score_scan(session, scan)
        await session.commit()
        print("\n  AFTER audit:")
        print(f"    composite            : {after.composite}")
        print(f"    technical_foundation : {after.value(Dimension.TECHNICAL_FOUNDATION)}")
        print(f"    excluded             : {after.excluded_dimensions}")
        print(f"    flags                : {after.degradation_flags}")
        print("    weights              : " + ", ".join(
            f"{d.value}={s.weight}" for d, s in sorted(
                after.sub_scores.items(), key=lambda kv: kv[0].value) if s.included))

        closed = (
            after.value(Dimension.TECHNICAL_FOUNDATION) is not None
            and "technical_foundation" not in after.excluded_dimensions
            and before.composite != after.composite
        )
        print(f"\n  NOT_YET_MEASURED exclusion closed : {closed}")
        print(f"  composite moved                   : "
              f"{before.composite} -> {after.composite}")
        print("\nRESULT:", "PASS" if closed else "NEEDS REVIEW")

    await engine.dispose()
    return 0 if closed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

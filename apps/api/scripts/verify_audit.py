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

# Epic 7's unscoreable fixture. Its domain does not resolve, so it must
# never be chosen as the audit target.
DEGRADED_DOMAIN = "epic7-degraded.example"


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
        # `return 1`, not 0. A script that verifies nothing must not exit green:
        # that is how verify_report.py's failure hid for four epics.
        print("  DATABASE_URL not set — the integration proof did NOT run")
        return 1

    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        # Newest scan whose client is NOT the degraded fixture, and not soft
        # deleted — the same selection verify_report.py and verify_fixes.py use.
        #
        # This used to be a bare `order_by(Scan.id.desc())`. Scan ids are
        # time-ordered ULIDs, so when Epic 7's verify_report.py PART 3 created
        # the degraded scan for `epic7-degraded.example`, that scan became the
        # newest and this script silently began auditing it. The domain does
        # not resolve, so from Epic 7 until Epic 3.10 this script crawled
        # nothing, produced a failed audit, and committed it over a fixture
        # scan — while still reporting PASS. Nobody noticed because nobody ran
        # it. Same failure shape as verify_report.py's stale sweep in Epic 3.8.
        scan = (
            await session.execute(
                select(Scan)
                .join(Client, Client.id == Scan.client_id)
                .where(Client.domain != DEGRADED_DOMAIN)
                .where(Client.deleted_at.is_(None))
                .order_by(Scan.created_at.desc())
            )
        ).scalars().first()
        if scan is None:
            print("  no auditable scan in the database — run scripts/seed_dev.py first")
            await engine.dispose()
            return 1
        client = (
            await session.execute(select(Client).where(Client.id == scan.client_id))
        ).scalar_one()

        # The previous version deleted the scan's audit and its checks and
        # COMMITTED that eleven lines before it attempted the replacement
        # crawl, with no restore anywhere in the file. A network failure, a
        # missing Chromium or a Ctrl-C in between left the scan with no audit
        # at all, permanently.
        #
        # The delete was also unnecessary: `audit_runner.run_audit` is
        # documented as "one audit per scan, refreshed on re-run" and already
        # deletes and rebuilds the checks itself. Removing it costs nothing and
        # removes the only irreversible step in the script.
        existing = await audit_runner.latest_audit(session, scan.id)
        had_audit = existing is not None

        # BEFORE is now an honest read of the current state rather than a
        # manufactured one. When the scan already carries an audit this is a
        # before/after over a RE-audit, not over first-audit; the printout says
        # which, so the reader is not misled about what was demonstrated.
        _, before, _ = await scoring_runner.score_scan(session, scan, persist=False)
        print(f"\n  scan {scan.id}  ({client.domain})")
        shape = (
            "ALREADY had an audit — measuring a RE-audit"
            if had_audit
            else "had no audit — measuring first-audit"
        )
        print(f"  this scan {shape}")
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

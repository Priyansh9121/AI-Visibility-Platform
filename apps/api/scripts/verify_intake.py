"""Live verification of the Epic 2 acceptance criterion.

§7 Epic 2: "submitting a URL returns a correctly classified industry within 30
seconds."

Runs the real pipeline — real Playwright crawl, real Anthropic call — against a
set of genuinely different businesses, and reports wall-clock time and the
classification for each. Nothing is mocked.

    uv run python scripts/verify_intake.py            # full run
    uv run python scripts/verify_intake.py --crawl-only   # skip the LLM call

`--crawl-only` verifies the fetch/extract half without spending credits; it
cannot verify the acceptance criterion, and says so.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avp_api.config import Settings  # noqa: E402
from avp_api.services.classify import classify  # noqa: E402
from avp_api.services.crawl import crawl_site  # noqa: E402

# Deliberately varied: different sectors, page structures, and sizes. A set of
# five SaaS homepages would prove far less than this does.
TEST_SITES: list[tuple[str, str]] = [
    ("anthropic.com", "AI research / AI model provider"),
    ("basecamp.com", "project management software"),
    ("allbirds.com", "footwear / direct-to-consumer apparel retail"),
    ("stripe.com", "payments infrastructure"),
    ("ycombinator.com", "startup accelerator / venture capital"),
]

BUDGET_SECONDS = 30.0


async def check(url: str, expectation: str, settings: Settings, crawl_only: bool) -> dict:
    started = time.perf_counter()
    crawl = await crawl_site(url, max_pages=3)
    crawl_seconds = time.perf_counter() - started

    row = {
        "url": url,
        "expected": expectation,
        "crawl_ok": crawl.ok,
        "crawl_seconds": round(crawl_seconds, 1),
        "pages": crawl.signals.pages_fetched,
        "words": crawl.signals.word_count,
        "schema_types": crawl.signals.schema_types[:3],
    }

    if crawl_only:
        row["total_seconds"] = round(crawl_seconds, 1)
        return row

    outcome = await classify(crawl, settings=settings)
    total = time.perf_counter() - started

    row.update(
        {
            "status": outcome.status,
            "industry": outcome.industry,
            "niche": outcome.niche,
            "brand": outcome.brand_name,
            "confidence": outcome.confidence,
            "score": str(outcome.confidence_score) if outcome.confidence_score else None,
            "reason": outcome.reason_code,
            "total_seconds": round(total, 1),
            "within_budget": total < BUDGET_SECONDS,
        }
    )
    return row


async def main() -> int:
    crawl_only = "--crawl-only" in sys.argv
    settings = Settings()

    print(f"{'site':22} {'crawl':>6} {'total':>6}  result")
    print("-" * 100)

    rows = []
    for url, expectation in TEST_SITES:
        try:
            row = await check(url, expectation, settings, crawl_only)
        except Exception as exc:  # noqa: BLE001 - a verification script reports, never raises
            print(f"{url:22} {'—':>6} {'—':>6}  ERROR {type(exc).__name__}: {exc}")
            continue
        rows.append(row)

        if crawl_only:
            print(
                f"{url:22} {row['crawl_seconds']:>5}s {row['total_seconds']:>5}s  "
                f"crawl_ok={row['crawl_ok']} pages={row['pages']} words={row['words']}"
            )
        else:
            mark = "OK " if row.get("within_budget") else "SLOW"
            print(
                f"{url:22} {row['crawl_seconds']:>5}s {row['total_seconds']:>5}s  "
                f"[{mark}] {row['status']:14} {str(row['industry'])[:34]:34} "
                f"conf={row['score']}"
            )
            print(f"{'':22} {'':>6} {'':>6}  expected: {expectation}")

    print("-" * 100)
    if crawl_only:
        print(
            f"{len(rows)}/{len(TEST_SITES)} sites crawled. "
            "CRAWL ONLY — the acceptance criterion is NOT verified by this mode."
        )
        return 0

    classified = [r for r in rows if r.get("status") == "classified"]
    in_budget = [r for r in rows if r.get("within_budget")]
    slowest = max((r["total_seconds"] for r in rows), default=0)

    print(f"classified:      {len(classified)}/{len(TEST_SITES)}")
    print(
        f"within {BUDGET_SECONDS:.0f}s:      {len(in_budget)}/{len(TEST_SITES)}"
        f"  (slowest {slowest}s)"
    )
    print("\nIndustry labels are judged by a human against the expectation column above.")

    ok = len(classified) == len(TEST_SITES) and len(in_budget) == len(TEST_SITES)
    print("\nRESULT:", "PASS (pending human review of labels)" if ok else "NEEDS REVIEW")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

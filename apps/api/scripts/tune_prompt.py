"""Prompt-tuning harness for Finding 1 (label over-generalisation).

Experimental design, because "the label changed" is worthless without it:

  * **The crawl is held constant.** Every site is fetched ONCE, and the same
    in-memory `CrawlResult` is fed to every prompt variant. Re-crawling between
    variants would let page changes and different secondary-page choices move
    the result, and a difference could not be attributed to the prompt.
  * **Crawls are never written to disk.** They live in memory for the process
    lifetime only — ip-safety.md #7 forbids persisting page text, and a "just
    for testing" cache file is still a durable store.
  * **One variable per variant.** B changes only the exemplar. C adds only the
    explicit negative on top of B.

    uv run python scripts/tune_prompt.py A B
    uv run python scripts/tune_prompt.py A B C
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avp_api.config import Settings  # noqa: E402
from avp_api.services.classify import SYSTEM_PROMPT, classify  # noqa: E402
from avp_api.services.crawl import CrawlResult, crawl_site  # noqa: E402

# (url, expected label, why this site stresses the failure mode)
SITES: list[tuple[str, str, str]] = [
    # The original five.
    ("anthropic.com", "AI research / AI model provider", "baseline — was correct"),
    ("basecamp.com", "project management software", "FAILED: returned 'b2b saas'"),
    ("allbirds.com", "footwear / DTC apparel retail", "baseline — was correct"),
    ("stripe.com", "payments infrastructure", "baseline — was correct"),
    ("ycombinator.com", "startup accelerator", "FAILED: returned 'venture capital'"),
    # Added to stress "generalise to business model".
    ("savvycal.com", "meeting scheduling software", "niche SaaS — 'b2b saas' would be wrong"),
    ("helpscout.com", "customer support / help desk software", "niche SaaS — same trap"),
    ("roto-rooter.com", "plumbing and drain services",
     "service business — 'home services' too broad"),
    ("ooni.com", "pizza oven manufacturer / retailer", "single product — 'e-commerce' too broad"),
]

# ---------------------------------------------------------------------------
# Variant A — current production prompt, unchanged. The baseline.
# ---------------------------------------------------------------------------
VARIANT_A = SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Variant B — ONE change: the exemplar.
#
# The production prompt offers "b2b logistics software" as an example, which is
# itself a business-model-plus-vertical construction — it models the exact
# generalisation the label should avoid. Replacing all three exemplars with
# concrete what-they-sell phrasings is the single variable under test.
# ---------------------------------------------------------------------------
VARIANT_B = SYSTEM_PROMPT.replace(
    '("dental practice", "independent bookshop", "b2b logistics software")',
    '("dental practice", "independent bookshop", "warehouse management software")',
)

# ---------------------------------------------------------------------------
# Variant C — B plus ONE further change: an explicit negative.
# ---------------------------------------------------------------------------
VARIANT_C = VARIANT_B.replace(
    "- `niche` narrows it only when the site clearly specialises. Otherwise null.",
    "- Name what the business SELLS or DOES, never its business model or delivery\n"
    "channel. \"b2b saas\", \"saas\", \"e-commerce\", \"marketplace\", \"technology\n"
    "company\", \"home services\" and \"consumer goods\" are never acceptable answers —\n"
    "if one is your first instinct, go one level more specific and name the actual\n"
    "product or service.\n"
    "- `niche` narrows it only when the site clearly specialises. Otherwise null.",
)

VARIANTS = {"A": VARIANT_A, "B": VARIANT_B, "C": VARIANT_C}


async def main() -> int:
    wanted = [a.upper() for a in sys.argv[1:] if a.upper() in VARIANTS] or ["A", "B"]
    settings = Settings()

    # Sanity: a variant that failed to substitute would silently be a duplicate
    # of its parent, and the comparison would be meaningless.
    if "B" in wanted and VARIANT_B == VARIANT_A:
        print("FATAL: variant B is identical to A — the exemplar substitution failed.")
        return 1
    if "C" in wanted and VARIANT_C == VARIANT_B:
        print("FATAL: variant C is identical to B — the negative substitution failed.")
        return 1

    print(f"Crawling {len(SITES)} sites once; the same crawl feeds every variant.\n")
    crawls: dict[str, CrawlResult] = {}
    for url, _, _ in SITES:
        crawls[url] = await crawl_site(url, max_pages=3)
        c = crawls[url]
        print(f"  {url:20} ok={c.ok} words={c.signals.word_count}")

    results: dict[str, dict[str, str]] = {v: {} for v in wanted}
    for variant in wanted:
        print(f"\n--- variant {variant} ---")
        for url, _, _ in SITES:
            outcome = await classify(
                crawls[url], settings=settings, system_prompt=VARIANTS[variant]
            )
            label = outcome.industry if outcome.status == "classified" else f"<{outcome.status}>"
            results[variant][url] = label or "<none>"
            print(f"  {url:20} {label}")

    print("\n" + "=" * 118)
    print(f"{'site':20} {'expected':38} " + " ".join(f"{v:26}" for v in wanted))
    print("=" * 118)
    for url, expected, _ in SITES:
        row = " ".join(f"{results[v][url][:26]:26}" for v in wanted)
        print(f"{url:20} {expected[:38]:38} {row}")
    print("=" * 118)

    if len(wanted) > 1:
        base, last = wanted[0], wanted[-1]
        changed = [u for u, _, _ in SITES if results[base][u] != results[last][u]]
        print(f"\nlabels changed {base} -> {last}: {len(changed)}/{len(SITES)}")
        for u in changed:
            print(f"  {u:20} {results[base][u]!r}  ->  {results[last][u]!r}")
    print("\nCorrectness is a human call. Judge each column against `expected`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

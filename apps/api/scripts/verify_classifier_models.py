"""Industry classifier, Opus 5 against the cheapest current model — the cost
brief, 2026-09-11.

Same design as `tune_prompt.py`, with the prompt held constant and the MODEL
as the one variable: every site is crawled once and the same in-memory
`CrawlResult` is fed to every model. Crawls are never written to disk (the
facts-only rule). The production prompt, schema and `decide()` threshold
policy are used verbatim, so a stored outcome would be exactly what each
model would have persisted.

    uv run python scripts/verify_classifier_models.py
    uv run python scripts/verify_classifier_models.py --runs 2

Costs real money: nine classify calls per model per run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.services.classify import (  # noqa: E402
    CLASSIFIER_BOUND,
    CLASSIFIER_MAX_TOKENS,
    SYSTEM_PROMPT,
    IndustryClassification,
    build_prompt,
    decide,
)
from avp_api.services.crawl import CrawlResult, crawl_site  # noqa: E402

# tune_prompt.py's nine, unchanged: five baselines and four chosen to stress
# label over-generalisation, the classifier's known failure mode.
SITES: list[tuple[str, str]] = [
    ("anthropic.com", "AI research / AI model provider"),
    ("basecamp.com", "project management software"),
    ("allbirds.com", "footwear / DTC apparel retail"),
    ("stripe.com", "payments infrastructure"),
    ("ycombinator.com", "startup accelerator"),
    ("savvycal.com", "meeting scheduling software"),
    ("helpscout.com", "customer support / help desk software"),
    ("roto-rooter.com", "plumbing and drain services"),
    ("ooni.com", "pizza oven manufacturer / retailer"),
]

RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

CONFIGS: dict[str, dict[str, object]] = {
    # Production today: classify.py's CLASSIFIER_MODEL / CLASSIFIER_EFFORT.
    "opus5-low": {"model": "claude-opus-5", "output_config": {"effort": "low"}},
    "haiku45": {"model": "claude-haiku-4-5-20251001"},
}


@dataclass
class Row:
    status: str
    industry: str | None
    niche: str | None
    brand: str | None
    score: str
    input_tokens: int
    output_tokens: int
    seconds: float
    served_by: str


def rate_for(model: str) -> tuple[float, float]:
    matches = [k for k in RATES if model.startswith(k)]
    return RATES[max(matches, key=len)]


async def one(client, config: dict[str, object], crawl: CrawlResult) -> Row:  # noqa: ANN001
    started = time.perf_counter()
    try:
        async with CLASSIFIER_BOUND.deadline():
            response = await client.messages.parse(
                max_tokens=CLASSIFIER_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(crawl)}],
                output_format=IndustryClassification,
                **config,
            )
    except ValidationError as exc:
        # The model answered and its answer did not fit the schema — seen on
        # the first run of this script: Opus 5 wrote a 300+ character
        # `rationale` for stripe.com. Reported as its own status rather than
        # crashing the comparison; the token count is lost with the response.
        fields = ",".join(".".join(str(x) for x in e.get("loc", ())) for e in exc.errors()[:3])
        return Row(f"<schema:{fields}>", None, None, None, "-", 0, 0,
                   time.perf_counter() - started, str(config["model"]))
    seconds = time.perf_counter() - started
    parsed = response.parsed_output
    if parsed is None:
        return Row(f"<{response.stop_reason}>", None, None, None, "-", response.usage.input_tokens,
                   response.usage.output_tokens, seconds, response.model)
    outcome = decide(parsed)
    return Row(outcome.status, outcome.industry, outcome.niche, outcome.brand_name,
               str(outcome.confidence_score), response.usage.input_tokens,
               response.usage.output_tokens, seconds, response.model)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    settings = Settings()
    client = CLASSIFIER_BOUND.client(settings)

    print(f"Crawling {len(SITES)} sites once; the same crawl feeds every model.\n")
    crawls: dict[str, CrawlResult] = {}
    for url, _ in SITES:
        crawls[url] = await crawl_site(url, max_pages=3)
        c = crawls[url]
        print(f"  {url:20} ok={c.ok} words={c.signals.word_count}")
    usable = [u for u, _ in SITES if crawls[u].ok]

    results: dict[str, list[dict[str, Row]]] = {}
    for name in args.models:
        results[name] = []
        for _ in range(args.runs):
            rows = await asyncio.gather(*(one(client, CONFIGS[name], crawls[u]) for u in usable))
            results[name].append(dict(zip(usable, rows, strict=True)))

    for name in args.models:
        print(f"\n--- {name} (served by {results[name][0][usable[0]].served_by}) ---")
        for url, _expected in SITES:
            if url not in usable:
                print(f"  {url:18} <crawl failed>")
                continue
            for r in (run[url] for run in results[name]):
                ind = r.industry if r.status == "classified" else f"<{r.status}>"
                print(f"  {url:18} {ind!s:42} niche={r.niche!s:38} brand={r.brand!s:16}"
                      f" score={r.score:5} {r.seconds:4.1f}s")

    print("\n" + "=" * 110)
    print(f"{'site':18} {'expected':36} " + " ".join(f"{m:26}" for m in args.models))
    print("=" * 110)
    for url, expected in SITES:
        if url not in usable:
            continue
        cells = []
        for m in args.models:
            r = results[m][0][url]
            label = r.industry if r.status == "classified" else f"<{r.status}>"
            cells.append(f"{(label or '')[:26]:26}")
        print(f"{url:18} {expected[:36]:36} " + " ".join(cells))
    print("=" * 110)

    base = args.models[0]
    for m in args.models[1:]:
        same = sum(results[m][0][u].industry == results[base][0][u].industry for u in usable)
        print(f"\nidentical `industry` string {m} vs {base}: {same}/{len(usable)}"
              " (a different string can still be the same call — judge the table)")

    print("\nTokens and money, summed over all runs:")
    print(f"  {'config':12} {'calls':>5} {'in':>8} {'out':>7} {'$/call':>8}"
          f" {'p50 s':>6} {'max s':>6}")
    for m in args.models:
        rs = [r for run in results[m] for r in run.values()]
        rate = rate_for(rs[0].served_by)
        tin = sum(r.input_tokens for r in rs)
        tout = sum(r.output_tokens for r in rs)
        cost = (tin * rate[0] + tout * rate[1]) / 1_000_000
        secs = sorted(r.seconds for r in rs)
        print(f"  {m:12} {len(rs):5} {tin:8,} {tout:7,} {cost / len(rs):8.4f}"
              f" {secs[len(secs) // 2]:6.1f} {secs[-1]:6.1f}")
    print("\nCorrectness is a human call. Judge each column against `expected`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

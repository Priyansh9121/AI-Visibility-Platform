"""Live verification of the Epic 4 acceptance criterion.

§7 Epic 4: "a scan produces structured EngineResult records for every prompt x
engine pair, with mentions and citations correctly parsed."

Runs the real pipeline — real prompt generation, real Claude parametric and
Claude web-search calls, real extraction — and prints the resulting matrix.
Nothing is mocked.

    uv run python scripts/verify_scan.py            # default 6 prompts
    uv run python scripts/verify_scan.py --prompts 24   # a full-size scan

Costs real money and time. The grounded engine can take 100s per prompt, so a
24-prompt run is roughly 48 model calls plus a sentiment call per mention.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avp_api.config import Settings  # noqa: E402
from avp_api.models.engine_result import Engine  # noqa: E402
from avp_api.services import engines as engine_service  # noqa: E402
from avp_api.services import extraction as extraction_service  # noqa: E402
from avp_api.services import prompts as prompt_service  # noqa: E402

SUBJECT_NAME = "Help Scout"
SUBJECT_DOMAIN = "helpscout.com"
INDUSTRY = "customer support software"
COMPETITORS: list[tuple[str, str | None]] = [
    ("Zendesk", "zendesk.com"), ("Freshdesk", "freshworks.com"),
    ("Intercom", "intercom.com"), ("Front", "front.com"),
    ("Gorgias", "gorgias.com"),
]
ENGINES: tuple[Engine, ...] = (Engine.CLAUDE, Engine.CLAUDE_SEARCH)
CONCURRENCY = 3


async def one_prompt(prompt, settings, semaphore):  # noqa: ANN001, ANN202
    async with semaphore:
        answers = await engine_service.ask_all(prompt.text, engines=ENGINES, settings=settings)
        rows = []
        for answer in answers:
            facts = extraction_service.extract_facts(
                answer, subject_name=SUBJECT_NAME, subject_domain=SUBJECT_DOMAIN,
                competitors=COMPETITORS,
            )
            if facts.mentioned:
                facts.sentiment, facts.sentiment_confidence = (
                    await extraction_service.classify_sentiment(
                        answer, subject_name=SUBJECT_NAME, settings=settings
                    )
                )
            rows.append((answer, facts))
        return prompt, rows


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=int, default=6)
    args = parser.parse_args()

    settings = Settings()
    print(f"Generating a prompt set for {SUBJECT_NAME} ({SUBJECT_DOMAIN})...")
    generated, generated_by = await prompt_service.generate_prompts(
        brand_name=SUBJECT_NAME, domain=SUBJECT_DOMAIN, industry=INDUSTRY,
        niche=None, competitors=[n for n, _ in COMPETITORS], settings=settings,
    )
    print(f"  generated_by={generated_by}  total={len(generated)}")
    print(f"  intent mix={dict(Counter(p.intent.value for p in generated))}")

    selected = generated[: args.prompts]
    print(f"\nRunning {len(selected)} prompts x {len(ENGINES)} engines "
          f"= {len(selected) * len(ENGINES)} engine calls\n")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    completed = await asyncio.gather(
        *(one_prompt(p, settings, semaphore) for p in selected)
    )

    pairs = 0
    per_engine: dict[str, Counter] = {e.value: Counter() for e in ENGINES}
    total_citations = 0
    for prompt, rows in completed:
        print(f"[{prompt.intent.value}] {prompt.text[:78]}")
        for answer, facts in rows:
            pairs += 1
            stats = per_engine[answer.engine.value]
            stats["results"] += 1
            stats["mentions"] += int(facts.mentioned)
            stats["citations"] += len(facts.citations)
            stats["brands"] += facts.brands_mentioned
            if not answer.ok:
                stats["failed"] += 1
            total_citations += len(facts.citations)
            digest = (answer.digest() or "-")[:12]
            print(
                f"    {answer.engine.value:14} {answer.status.value:20} "
                f"mentioned={str(facts.mentioned):5} pos={str(facts.position):4} "
                f"brands={facts.brands_mentioned} cites={len(facts.citations)} "
                f"sent={facts.sentiment.value if facts.sentiment else '-':8} "
                f"digest={digest} {answer.latency_ms}ms"
            )
        print()

    expected = len(selected) * len(ENGINES)
    print("=" * 92)
    print(f"engine result rows : {pairs}/{expected} prompt x engine pairs")
    for engine, stats in per_engine.items():
        rate = stats["mentions"] / stats["results"] if stats["results"] else 0
        print(f"  {engine:14} results={stats['results']:3} "
              f"mentioned={stats['mentions']:3} ({rate:.0%})  "
              f"citations={stats['citations']:3}  failed={stats['failed']}")
    print(f"total citations    : {total_citations}")
    print("=" * 92)

    # `pairs == expected` is `n == n`. `ask_all` gathers one coroutine per
    # engine and filters nothing, and each engine's `ask` catches its own
    # errors and returns an EngineAnswer with ok=False rather than raising —
    # so the row count is the pair count by construction and this could never
    # be false. It reported True on every run since Epic 4 while proving
    # nothing. Kept as a structural sanity line, demoted out of the verdict.
    complete = pairs == expected
    cited = total_citations > 0

    # The verdict a reader actually wants: did the engines ANSWER? Failures
    # were counted and printed and then left out of the exit code, so a run in
    # which every provider call failed still printed PASS — it produced twelve
    # rows, all of them errors. Found in Epic 3.10.
    failed = sum(stats["failed"] for stats in per_engine.values())
    answered = pairs - failed

    print(f"\nevery prompt x engine pair produced a row : {complete}"
          "   (structural: ask_all always returns one row per engine)")
    print(f"engine calls that actually answered       : {answered}/{pairs}")
    print(f"citations parsed from the grounded engine : {cited}")

    ok = complete and cited and failed == 0
    if failed:
        print(f"\n  FAIL  {failed} of {pairs} engine calls failed")
    print("\nRESULT:", "PASS" if ok else "NEEDS REVIEW")

    # §7 Epic 4 asks for "structured EngineResult RECORDS". This script makes
    # no database call — it imports neither scan_runner nor EngineResult, and
    # commits nothing — so it verifies extraction from live answers, not
    # persistence. The persisted path is covered by tests/test_scan_endpoints.py.
    # Said plainly because the docstring quotes the criterion verbatim and a
    # reader would otherwise reasonably assume this run had checked it.
    print("\nNOTE: this script verifies EXTRACTION from live engine answers.")
    print("      It writes nothing, so persistence of EngineResult rows is NOT")
    print("      covered here — see tests/test_scan_endpoints.py for that half.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

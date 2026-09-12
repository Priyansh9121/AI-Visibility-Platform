"""Held-out comparison of sentiment classifiers — the cost brief, 2026-09-11.

Sentiment classification was ~$3.20 of the measured $6.50 five-engine scan
(build log, Epic 21.1: 82 `claude-opus-5` calls), more than any single answer
engine. It is a three-way categorisation of ONE answer toward ONE brand, and
the brief asks whether the cheapest current model does it as well.

Why a fixture set and not a live scan: the facts-only rule means no answer
text is ever persisted, so there is no stored corpus to re-classify offline.
The sixteen excerpts below are written to cover the rubric's own distinctions
— plain listings (neutral), praise for a competitor WITHOUT a comparison
(neutral), caveats presented as reasons not to choose (negative), unfavourable
direct comparisons (negative) — plus two a reader could label either way.

Every model sees the PRODUCTION prompt: `SENTIMENT_SYSTEM`, the same user
message shape `classify_sentiment` builds, the same `SentimentJudgement`
schema, the same `CallBound` client. Only the model (and, where the model
supports it, effort) differs. Usage is read off every response so the cost
column is measured on the same inputs, not projected.

    uv run python scripts/verify_sentiment_models.py
    uv run python scripts/verify_sentiment_models.py --models opus5-low haiku45 --runs 2

Costs real money: 16 calls per model per run. At one run of three models
that is 48 calls, most of the spend on the Opus baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avp_api.config import Settings  # noqa: E402
from avp_api.services.extraction import (  # noqa: E402
    SENTIMENT_BOUND,
    SENTIMENT_MAX_TOKENS,
    SENTIMENT_SYSTEM,
    SentimentJudgement,
)

SUBJECT = "Help Scout"

# Published $/MTok (input, output). platform.claude.com/docs/en/about-claude/
# pricing, read 2026-09-11.
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Request shape per candidate. `effort` is only accepted on models that
# support it — Haiku 4.5 returns 400 for `output_config.effort` (docs, effort
# page, read 2026-09-11) — so the shape is per model, not a shared kwarg.
CONFIGS: dict[str, dict[str, object]] = {
    # Production today: extraction.py's SENTIMENT_MODEL / SENTIMENT_EFFORT.
    "opus5-low": {"model": "claude-opus-5", "output_config": {"effort": "low"}},
    "sonnet5-low": {"model": "claude-sonnet-5", "output_config": {"effort": "low"}},
    "haiku45": {"model": "claude-haiku-4-5-20251001"},
}


@dataclass(frozen=True)
class Case:
    id: str
    expected: frozenset[str]  # more than one label = a reader could go either way
    text: str


CASES: list[Case] = [
    # --- clearly positive -------------------------------------------------
    Case("P1", frozenset({"positive"}),
         "If you're a small team that lives in email, Help Scout is the one I'd "
         "recommend. The shared inbox is genuinely simple, the Docs knowledge base "
         "is excellent, and pricing is transparent with no per-seat surprises."),
    Case("P2", frozenset({"positive"}),
         "Best overall for small businesses: Help Scout. It nails the basics — "
         "collision detection, saved replies, a clean knowledge base — and "
         "customers consistently rate its support team highly. Runner-up: "
         "Freshdesk, which offers more automation at a lower entry price."),
    Case("P3", frozenset({"positive"}),
         "Between Zendesk and Help Scout for a team of ten, I'd choose Help Scout. "
         "Zendesk's configuration overhead is real, whereas Help Scout is usable on "
         "day one and its per-user price is lower at that size."),
    Case("P4", frozenset({"positive"}),
         "Help Scout stands out for its customer-first philosophy and ease of use. "
         "Reviewers on G2 repeatedly call out how quickly new agents become "
         "productive, and its Beacon widget is a well-liked way to surface help "
         "articles in-app."),
    Case("P5", frozenset({"positive"}),
         "Options worth shortlisting: Zendesk for enterprise ticketing, Intercom "
         "for chat-led support, and Help Scout — a solid, well-regarded choice for "
         "small teams who want something simple that works."),
    # --- clearly negative -------------------------------------------------
    Case("N1", frozenset({"negative"}),
         "Help Scout's reporting is thin and it has no real ticket model — "
         "everything is a conversation — so teams that need SLAs, escalations or "
         "multi-tier queues should look at Zendesk or Freshdesk instead."),
    Case("N2", frozenset({"negative"}),
         "Compared with Intercom, Help Scout feels dated. Its automation rules are "
         "basic, there's no proactive messaging to speak of, and the price climbs "
         "quickly once you add users, which makes Intercom the stronger choice for "
         "a product-led company."),
    Case("N3", frozenset({"negative"}),
         "I would avoid Help Scout if you need phone support, multi-brand inboxes "
         "or deep CRM integration. It doesn't offer them natively, and users report "
         "that feature development has been slow."),
    Case("N4", frozenset({"negative"}),
         "Help Scout: simple and pleasant to use, but the lack of a true ticketing "
         "system and the weak third-party integrations make it hard to recommend "
         "for a team that expects to grow beyond a handful of agents."),
    Case("N5", frozenset({"negative"}),
         "Help Scout used to be my default recommendation, but the 2024 pricing "
         "changes made it noticeably more expensive for small teams, and several "
         "of the teams I work with have since moved to Front or Freshdesk."),
    # --- neutral ----------------------------------------------------------
    Case("U1", frozenset({"neutral"}),
         "Popular help desk tools include Zendesk, Freshdesk, Help Scout, Front, "
         "Intercom and Zoho Desk. The right choice depends on team size, channels "
         "and budget."),
    Case("U2", frozenset({"neutral"}),
         "Help Scout is a customer support platform founded in 2011. It provides a "
         "shared inbox, a knowledge base product called Docs, live chat via its "
         "Beacon widget, and reporting. It is priced per user per month with a "
         "free trial."),
    Case("U3", frozenset({"neutral"}),
         "Zendesk is the most feature-complete option on the market and remains "
         "the standard for enterprise support teams, with mature ticketing, SLAs "
         "and an enormous integration marketplace. Other tools in the category "
         "include Help Scout, Front and Freshdesk."),
    Case("U4", frozenset({"neutral"}),
         "Help Scout — shared inbox and knowledge base. Freshdesk — ticketing with "
         "automation. Zendesk — full enterprise suite. Front — team email with "
         "collaboration features."),
    # --- genuinely ambiguous ----------------------------------------------
    Case("A1", frozenset({"positive", "neutral"}),
         "Help Scout is loved for its simplicity, but that simplicity is also its "
         "ceiling: larger teams often outgrow it. For a five-person team it's a "
         "great fit; for fifty, probably not."),
    Case("A2", frozenset({"neutral", "negative"}),
         "Some teams have switched from Help Scout to Front for stronger "
         "collaboration features, though others prefer Help Scout's lighter "
         "interface and stayed."),
]


@dataclass
class Result:
    label: str
    confidence: float
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    seconds: float
    served_by: str


def rate_for(model: str) -> tuple[float, float]:
    matches = [k for k in RATES if model.startswith(k)]
    if not matches:
        raise KeyError(f"no published rate recorded for {model!r}")
    return RATES[max(matches, key=len)]


async def one(client, config: dict[str, object], case: Case, sem: asyncio.Semaphore) -> Result:  # noqa: ANN001
    async with sem:
        started = time.perf_counter()
        async with SENTIMENT_BOUND.deadline():
            response = await client.messages.parse(
                max_tokens=SENTIMENT_MAX_TOKENS,
                system=SENTIMENT_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": f"Subject brand: {SUBJECT}\n\nAnswer:\n{case.text}",
                }],
                output_format=SentimentJudgement,
                **config,
            )
        seconds = time.perf_counter() - started
    parsed = response.parsed_output
    label = parsed.sentiment.value if parsed is not None else f"<{response.stop_reason}>"
    u = response.usage
    return Result(
        label=label,
        confidence=parsed.confidence if parsed is not None else 0.0,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read=int(getattr(u, "cache_read_input_tokens", 0) or 0),
        cache_write=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        seconds=seconds,
        served_by=response.model,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    settings = Settings()
    client = SENTIMENT_BOUND.client(settings)
    sem = asyncio.Semaphore(4)

    # results[name][run][case.id]
    results: dict[str, list[dict[str, Result]]] = {}
    for name in args.models:
        results[name] = []
        for run in range(args.runs):
            outs = await asyncio.gather(*(one(client, CONFIGS[name], c, sem) for c in CASES))
            results[name].append({c.id: r for c, r in zip(CASES, outs, strict=True)})
            print(f"  {name} run {run + 1}: {len(outs)} calls, served by "
                  f"{sorted({r.served_by for r in outs})}")

    width = 8 + 20 * len(args.models)
    print("\n" + "=" * width)
    print(f"{'case':5} {'expected':18} " + " ".join(f"{m:19}" for m in args.models))
    print("=" * width)
    for c in CASES:
        cells = []
        for m in args.models:
            labels = [results[m][r][c.id] for r in range(args.runs)]
            cell = "/".join(f"{x.label[:3]}{x.confidence:.2f}" for x in labels)
            cells.append(f"{cell:19}")
        exp = "|".join(sorted(c.expected))
        print(f"{c.id:5} {exp:18} " + " ".join(cells))
    print("=" * width)

    baseline = args.models[0]
    print(f"\nAgreement (run 1 labels; baseline = {baseline}):")
    for m in args.models:
        vs_expected = sum(results[m][0][c.id].label in c.expected for c in CASES)
        clear = [c for c in CASES if len(c.expected) == 1]
        vs_expected_clear = sum(results[m][0][c.id].label in c.expected for c in clear)
        line = (f"  {m:12} matches reader label {vs_expected}/{len(CASES)}"
                f"  (clear cases {vs_expected_clear}/{len(clear)})")
        if m != baseline:
            agree = sum(
                results[m][0][c.id].label == results[baseline][0][c.id].label for c in CASES
            )
            line += f"  agrees with {baseline} {agree}/{len(CASES)}"
        if args.runs > 1:
            stable = sum(
                len({results[m][r][c.id].label for r in range(args.runs)}) == 1 for c in CASES
            )
            line += f"  self-consistent across {args.runs} runs {stable}/{len(CASES)}"
        print(line)

    print("\nDisagreements with the reader label (run 1):")
    any_dis = False
    for m in args.models:
        for c in CASES:
            r = results[m][0][c.id]
            if r.label not in c.expected:
                any_dis = True
                print(f"  {m:12} {c.id}: got {r.label} ({r.confidence:.2f}),"
                      f" expected {'|'.join(sorted(c.expected))}")
    if not any_dis:
        print("  none")

    print("\nTokens and money, summed over all runs (usage as reported by the API):")
    print(f"  {'config':12} {'calls':>5} {'in':>7} {'out':>7} {'cache rd':>8} {'cache wr':>8}"
          f" {'$/call':>8} {'p50 s':>6} {'max s':>6}")
    for m in args.models:
        rs = [r for run in results[m] for r in run.values()]
        model = rs[0].served_by
        rate = rate_for(model)
        tin = sum(r.input_tokens for r in rs)
        tout = sum(r.output_tokens for r in rs)
        cost = (tin * rate[0] + tout * rate[1]) / 1_000_000
        secs = sorted(r.seconds for r in rs)
        print(f"  {m:12} {len(rs):5} {tin:7,} {tout:7,} {sum(r.cache_read for r in rs):8,}"
              f" {sum(r.cache_write for r in rs):8,} {cost / len(rs):8.4f}"
              f" {secs[len(secs) // 2]:6.1f} {secs[-1]:6.1f}")
    print("\nCache columns are expected to be 0: nothing here sends cache_control, and the"
          " static prefix is far below every model's minimum cacheable length.")
    dist = {m: Counter(r.label for run in results[m] for r in run.values()) for m in args.models}
    print(f"\nLabel distribution: {dict(dist)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Prompt generation, Opus 5 against cheaper models — the last-lever brief,
2026-09-11.

The prompt set is the measuring instrument, so a cheaper model is checked
against the things a plausible-looking set can still get wrong:

  * intent labels — `enforce_intent_mix` trusts them, so a systematic
    mislabel silently changes what the awareness population contains;
  * the brand-name rule — "most questions must NOT contain the subject
    brand's name", and never in an awareness question;
  * diversity — varied buyer questions, not ten wordings of one.

Every model sees the production system prompt, `build_generation_input`,
the production `GeneratedPromptSet` schema and the production `CallBound`
client; only the model (and effort where the model takes it) differs. The
sets are printed in full because the last two checks are a reader's call;
the heuristics beside them are signals, not verdicts.

    uv run python scripts/verify_prompt_models.py --runs 2

Costs real money: one call per subject per model per run.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avp_api.config import Settings  # noqa: E402
from avp_api.models.prompt import PromptIntent  # noqa: E402
from avp_api.services.prompts import (  # noqa: E402
    GENERATOR_BOUND,
    GENERATOR_MAX_TOKENS,
    MIN_PROMPTS,
    SYSTEM_PROMPT,
    GeneratedPrompt,
    GeneratedPromptSet,
    build_generation_input,
    enforce_intent_mix,
)

RATES = {"claude-opus-5": (5.00, 25.00), "claude-sonnet-5": (2.00, 10.00),
         "claude-haiku-4-5": (1.00, 5.00)}

CONFIGS: dict[str, dict[str, object]] = {
    # Production today: prompts.py's GENERATOR_MODEL / GENERATOR_EFFORT.
    "opus5-medium": {"model": "claude-opus-5", "output_config": {"effort": "medium"}},
    "sonnet5-medium": {"model": "claude-sonnet-5", "output_config": {"effort": "medium"}},
    "haiku45": {"model": "claude-haiku-4-5-20251001"},
}

# (brand, domain, industry, niche, competitors) — shapes production sends:
# a SaaS with a detected competitor set, a local service business, a
# consumer product maker, and a practice with no competitors detected.
SUBJECTS = [
    ("Helply", "helply.com", "customer support software",
     "ai-native support platform for b2b saas companies",
     ["Zendesk", "Intercom", "Help Scout", "Freshdesk", "Front"]),
    ("Roto-Rooter", "roto-rooter.com", "plumbing services",
     "drain cleaning, sewer repair and water damage cleanup",
     ["Mr. Rooter", "ARS Rescue Rooter", "Benjamin Franklin Plumbing"]),
    ("Ooni", "ooni.com", "pizza oven and cooking appliance manufacturer",
     "portable pizza ovens, dough mixers and pizza-making accessories",
     ["Gozney", "Solo Stove", "Bertello", "Big Green Egg"]),
    ("Northaven Dental", "northavendental.com", "dental practice",
     "cosmetic and implant dentistry", []),
]

COMPARISON_MARKERS = ("vs", "versus", "compare", "comparison", "alternative", "better",
                      "difference", "instead of", "or ", "which is", "worth switching",
                      "switch from", "over ")
BOTTOM_MARKERS = ("price", "pricing", "cost", "how much", "trial", "demo", "book", "appointment",
                  "sign up", "signup", "migrat", "contract", "discount", "plan", "quote",
                  "near me", "warranty", "ship", "deliver", "financ", "insurance", "cancel",
                  "refund", "get started", "onboard", "implement", "available", "in stock",
                  "schedule", "emergency", "same-day", "same day", "24/7", "weekend", "buy",
                  "order", "return policy", "payment", "install")
MARKETING_WORDS = ("leading", "best-in-class", "cutting-edge", "world-class", "innovative",
                   "solution", "leverage", "seamless", "robust", "premier")


@dataclass
class Out:
    raw: list[GeneratedPrompt]
    kept: list[GeneratedPrompt]
    input_tokens: int
    output_tokens: int
    seconds: float
    served_by: str
    status: str = "ok"


def rate_for(model: str) -> tuple[float, float]:
    return RATES[max((k for k in RATES if model.startswith(k)), key=len)]


def mentions(text: str, names: list[str]) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in names if n)


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2}


async def one(client, config, subject) -> Out:  # noqa: ANN001
    brand, domain, industry, niche, competitors = subject
    started = time.perf_counter()
    try:
        async with GENERATOR_BOUND.deadline():
            response = await client.messages.parse(
                max_tokens=GENERATOR_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_generation_input(
                    brand_name=brand, domain=domain, industry=industry, niche=niche,
                    competitors=competitors)}],
                output_format=GeneratedPromptSet,
                **config,
            )
    except Exception as exc:  # noqa: BLE001 - a comparison, reported not raised
        return Out([], [], 0, 0, time.perf_counter() - started, str(config["model"]),
                   status=type(exc).__name__)
    seconds = time.perf_counter() - started
    parsed = response.parsed_output
    if parsed is None:
        return Out([], [], response.usage.input_tokens, response.usage.output_tokens, seconds,
                   response.model, status=f"<{response.stop_reason}>")
    return Out(parsed.prompts, enforce_intent_mix(parsed.prompts), response.usage.input_tokens,
               response.usage.output_tokens, seconds, response.model)


def judge(out: Out, subject) -> dict[str, object]:  # noqa: ANN001
    brand, domain, _industry, _niche, competitors = subject
    stem = domain.split(".")[0]
    brand_names = [brand, stem] + brand.split()[:1]
    kept = out.kept
    by = {i: [p for p in kept if p.intent is i] for i in PromptIntent}
    aw, cmp_, bf = (by[PromptIntent.AWARENESS], by[PromptIntent.COMPARISON],
                    by[PromptIntent.BOTTOM_FUNNEL])
    named = [p for p in kept if mentions(p.text, brand_names)]
    aw_named = [p for p in aw if mentions(p.text, brand_names)]
    aw_comp = [p for p in aw if mentions(p.text, competitors)]
    cmp_flat = [p for p in cmp_ if not (
        mentions(p.text, brand_names) or mentions(p.text, competitors)
        or any(m in p.text.lower() for m in COMPARISON_MARKERS))]
    bf_flat = [p for p in bf if not any(m in p.text.lower() for m in BOTTOM_MARKERS)]
    lead = Counter(" ".join(p.text.lower().split()[:3]) for p in kept)
    sims = []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            a, b = tokens(kept[i].text), tokens(kept[j].text)
            if a and b:
                sims.append(len(a & b) / len(a | b))
    near_dup = sum(1 for s in sims if s >= 0.5)
    marketing = [p for p in kept if any(w in p.text.lower() for w in MARKETING_WORDS)]
    upper = [p for p in kept if p.text[:1].isupper()]
    return {
        "raw": len(out.raw), "kept": len(kept),
        "mix": f"{len(aw)}/{len(cmp_)}/{len(bf)}",
        "under_floor": len(kept) < MIN_PROMPTS,
        "brand_named": f"{len(named)}/{len(kept)}",
        "awareness_names_brand": len(aw_named),
        "awareness_names_competitor": len(aw_comp),
        "comparison_without_comparison": len(cmp_flat),
        "bottom_without_specifics": len(bf_flat),
        "repeated_openers": sum(c - 1 for c in lead.values() if c > 1),
        "near_duplicate_pairs": near_dup,
        "mean_pair_overlap": round(sum(sims) / len(sims), 3) if sims else 0.0,
        "marketing_words": len(marketing), "capitalised": len(upper),
        "mean_chars": round(sum(len(p.text) for p in kept) / len(kept)) if kept else 0,
        "_flags": {"aw_named": aw_named, "aw_comp": aw_comp, "cmp_flat": cmp_flat,
                   "bf_flat": bf_flat},
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    client = GENERATOR_BOUND.client(Settings())

    results: dict[str, list[list[Out]]] = {}
    for name in args.models:
        results[name] = []
        for _ in range(args.runs):
            outs = await asyncio.gather(*(one(client, CONFIGS[name], s) for s in SUBJECTS))
            results[name].append(list(outs))

    # Full sets, run 1, so a reader can judge quality and intent labels.
    for si, subject in enumerate(SUBJECTS):
        for m in args.models:
            o = results[m][0][si]
            print("=" * 100)
            print(f"[{subject[0]}] {m} — {o.status}, {len(o.raw)} generated, {len(o.kept)} kept")
            for p in sorted(o.kept, key=lambda p: p.intent.value):
                print(f"  {p.intent.value[:10]:10} {p.text}")

    print("\n" + "=" * 100)
    print("HEURISTICS (every run; flags are signals to read, not verdicts)")
    print("=" * 100)
    keys = ["raw", "kept", "mix", "brand_named", "awareness_names_brand",
            "awareness_names_competitor", "comparison_without_comparison",
            "bottom_without_specifics", "repeated_openers", "near_duplicate_pairs",
            "mean_pair_overlap", "marketing_words", "capitalised", "mean_chars"]
    for m in args.models:
        print(f"\n--- {m} ---")
        for si, subject in enumerate(SUBJECTS):
            for r, run in enumerate(results[m]):
                o = run[si]
                if o.status != "ok":
                    print(f"  {subject[0]:17} r{r + 1} !! {o.status}")
                    continue
                j = judge(o, subject)
                print(f"  {subject[0]:17} r{r + 1} " + "  ".join(f"{k}={j[k]}" for k in keys))
                for label, items in j["_flags"].items():  # type: ignore[union-attr]
                    for p in items:
                        print(f"      flag {label:9} [{p.intent.value}] {p.text}")
        rs = [o for run in results[m] for o in run if o.status == "ok"]
        if rs:
            rate = rate_for(rs[0].served_by)
            cost = sum(o.input_tokens * rate[0] + o.output_tokens * rate[1] for o in rs) / 1e6
            secs = sorted(o.seconds for o in rs)
            out_per_call = sum(o.output_tokens for o in rs) // len(rs)
            print(f"  ${cost / len(rs):.4f}/call  out tokens/call {out_per_call}"
                  f"  p50 {secs[len(secs) // 2]:.1f}s  max {secs[-1]:.1f}s")
    print("\nQuality and intent correctness are a reader's call: read the sets above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

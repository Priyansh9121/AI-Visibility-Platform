"""Co-citation discovery, Opus 5 against cheaper models — the last-lever
brief, 2026-09-11.

Co-citation is closer to measurement than analysis: `run_seed_prompt` asks
one model which brands it names for a buyer question, and those names seed
competitor detection. So the comparison is not "does it parse" but **which
brands each model names, and in what order**, on the real seed prompts
`build_seed_prompts` produces for five subjects in five industries.

Every model sees the production system prompt, the production user-message
shape, the production `CoCitationAnswer` schema and the production
`CallBound` client; only the model (and effort where the model takes it)
differs. Usage is read off every response.

    uv run python scripts/verify_cocitation_models.py --runs 2

Costs real money: 20 seeds per model per run.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avp_api.config import Settings  # noqa: E402
from avp_api.services.cocitation import (  # noqa: E402
    CO_CITATION_BOUND,
    CO_CITATION_MAX_TOKENS,
    MAX_BRANDS_PER_PROMPT,
    SYSTEM_PROMPT,
    CoCitationAnswer,
    build_seed_prompts,
)
from avp_api.services.crawl import registrable_domain  # noqa: E402
from avp_api.services.serp import NON_COMPETITOR_DOMAINS  # noqa: E402

RATES = {"claude-opus-5": (5.00, 25.00), "claude-sonnet-5": (2.00, 10.00),
         "claude-haiku-4-5": (1.00, 5.00)}

CONFIGS: dict[str, dict[str, object]] = {
    # Production today: cocitation.py's CO_CITATION_MODEL / CO_CITATION_EFFORT.
    "opus5-low": {"model": "claude-opus-5", "output_config": {"effort": "low"}},
    "sonnet5-low": {"model": "claude-sonnet-5", "output_config": {"effort": "low"}},
    "haiku45": {"model": "claude-haiku-4-5-20251001"},
}

# (brand, domain, industry, niche) — the classifier's own labels for these
# sites from verify_classifier_models.py, so the seeds are what production
# would build.
SUBJECTS = [
    ("Help Scout", "helpscout.com", "customer support software",
     "shared inbox and help desk platform for growing businesses"),
    ("Basecamp", "basecamp.com", "project management software",
     "team collaboration and project management tools for small businesses"),
    ("Roto-Rooter", "roto-rooter.com", "plumbing services",
     "drain cleaning, sewer repair and water damage cleanup"),
    ("Allbirds", "allbirds.com", "footwear brand",
     "sustainable everyday sneakers made from natural materials"),
    ("Ooni", "ooni.com", "pizza oven and cooking appliance manufacturer",
     "portable pizza ovens, dough mixers and pizza-making accessories"),
]

# Names that the system prompt forbids outright: places people read about a
# category. Matched on the normalised name as well as the domain, because a
# model that names "G2" usually gives no domain.
FORBIDDEN_NAMES = {
    "g2", "capterra", "trustpilot", "trustradius", "getapp", "software advice",
    "softwareadvice", "yelp", "reddit", "producthunt", "product hunt", "wikipedia",
    "amazon", "ebay", "etsy", "walmart", "home depot", "lowes", "lowe's", "angi",
    "angie's list", "thumbtack", "homeadvisor", "houzz", "nextdoor", "google",
    "forbes", "techcrunch", "youtube", "quora", "alternativeto", "crunchbase",
    "gartner", "pcmag", "wirecutter", "consumer reports", "zapier",
}


def norm(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"\b(inc|llc|ltd|co|corp|corporation|company)\.?$", "", n).strip(" .,")
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def same(a: str, b: str) -> bool:
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a))


@dataclass
class Out:
    names: list[str]
    domains: list[str | None]
    subject_named: bool
    input_tokens: int
    output_tokens: int
    seconds: float
    served_by: str
    status: str = "ok"


def rate_for(model: str) -> tuple[float, float]:
    return RATES[max((k for k in RATES if model.startswith(k)), key=len)]


async def one(client, config, subject: str, prompt: str, sem) -> Out:  # noqa: ANN001
    async with sem:
        started = time.perf_counter()
        try:
            async with CO_CITATION_BOUND.deadline():
                response = await client.messages.parse(
                    max_tokens=CO_CITATION_MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user",
                               "content": f"Subject brand: {subject}\n\nQuestion: {prompt}"}],
                    output_format=CoCitationAnswer,
                    **config,
                )
        except Exception as exc:  # noqa: BLE001 - a comparison, reported not raised
            return Out([], [], False, 0, 0, time.perf_counter() - started,
                       str(config["model"]), status=type(exc).__name__)
        seconds = time.perf_counter() - started
    parsed = response.parsed_output
    if parsed is None:
        return Out([], [], False, response.usage.input_tokens, response.usage.output_tokens,
                   seconds, response.model, status=f"<{response.stop_reason}>")
    brands = parsed.brands[:MAX_BRANDS_PER_PROMPT]
    return Out(
        names=[b.name.strip() for b in brands if b.name.strip()],
        domains=[registrable_domain(b.domain) if b.domain else None for b in brands],
        subject_named=parsed.subject_named,
        input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
        seconds=seconds, served_by=response.model,
    )


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = {norm(x) for x in a}, {norm(x) for x in b}
    if not sa and not sb:
        return 1.0
    inter = sum(1 for x in sa if any(same(x, y) for y in sb))
    return inter / (len(sa) + len(sb) - inter) if (len(sa) + len(sb) - inter) else 1.0


def recall(base: list[str], other: list[str]) -> tuple[int, int]:
    sb = {norm(x) for x in base}
    so = {norm(x) for x in other}
    return sum(1 for x in sb if any(same(x, y) for y in so)), len(sb)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    client = CO_CITATION_BOUND.client(Settings())
    sem = asyncio.Semaphore(3)

    seeds: list[tuple[str, str]] = []
    for brand, domain, industry, niche in SUBJECTS:
        built = build_seed_prompts(
            brand_name=brand, domain=domain, industry=industry, niche=niche
        )
        for p in built:
            seeds.append((brand, p))
    print(f"{len(seeds)} seed prompts across {len(SUBJECTS)} subjects\n")

    results: dict[str, list[list[Out]]] = {}
    for name in args.models:
        results[name] = []
        for _ in range(args.runs):
            outs = await asyncio.gather(*(one(client, CONFIGS[name], s, p, sem) for s, p in seeds))
            results[name].append(list(outs))

    base = args.models[0]
    for i, (subject, prompt) in enumerate(seeds):
        print("=" * 100)
        print(f"[{subject}] {prompt}")
        for m in args.models:
            for r, run in enumerate(results[m]):
                o = run[i]
                doms = sum(1 for d in o.domains if d)
                flag = "" if o.status == "ok" else f"  !! {o.status}"
                print(f"  {m:12} r{r + 1} subj={'Y' if o.subject_named else 'n'} "
                      f"n={len(o.names):2} dom={doms:2}{flag}: {', '.join(o.names)}")

    print("\n" + "=" * 100)
    print(f"AGAINST THE BASELINE ({base}, run 1 vs run 1), per seed, then averaged")
    print("=" * 100)
    for m in args.models:
        rows = []
        forbidden = 0
        no_domain = 0
        total_named = 0
        dom_conflicts = 0
        for i in range(len(seeds)):
            b = results[base][0][i]
            o = results[m][0][i]
            hit, n = recall(b.names, o.names)
            rows.append((len(o.names), jaccard(b.names, o.names), hit, n,
                         o.subject_named == b.subject_named,
                         jaccard(o.names[:3], b.names[:3])))
            total_named += len(o.names)
            no_domain += sum(1 for d in o.domains if not d)
            forbidden += sum(
                1 for nm, d in zip(o.names, o.domains, strict=True)
                if norm(nm) in FORBIDDEN_NAMES or (d and d in NON_COMPETITOR_DOMAINS)
            )
            # Same brand, both gave a domain, domains differ.
            for nm, d in zip(o.names, o.domains, strict=True):
                for bn, bd in zip(b.names, b.domains, strict=True):
                    if d and bd and same(norm(nm), norm(bn)) and d != bd:
                        dom_conflicts += 1
        k = len(rows)
        mean_n = sum(r[0] for r in rows) / k
        mean_j = sum(r[1] for r in rows) / k
        rec = sum(r[2] for r in rows), sum(r[3] for r in rows)
        subj_agree = sum(1 for r in rows if r[4])
        top3 = sum(r[5] for r in rows) / k
        stable = ""
        if args.runs > 1:
            sj = sum(jaccard(results[m][0][i].names, results[m][1][i].names)
                     for i in range(k)) / k
            stable = f"  run1~run2 jaccard {sj:.2f}"
        rs = [o for run in results[m] for o in run]
        rate = rate_for(rs[0].served_by)
        cost = sum(o.input_tokens * rate[0] + o.output_tokens * rate[1] for o in rs) / 1e6
        secs = sorted(o.seconds for o in rs)
        print(f"  {m:12} brands/seed {mean_n:4.1f}  jaccard vs base {mean_j:.2f}"
              f"  base brands recalled {rec[0]}/{rec[1]}  top-3 jaccard {top3:.2f}"
              f"  subject_named agrees {subj_agree}/{k}{stable}")
        print(f"  {'':12} forbidden names {forbidden}/{total_named}"
              f"  no-domain {no_domain}/{total_named}"
              f"  domain conflicts with base {dom_conflicts}"
              f"  ${cost / len(rs):.4f}/call  p50 {secs[len(secs) // 2]:.1f}s max {secs[-1]:.1f}s"
              f"  failures {sum(1 for o in rs if o.status != 'ok')}")
    print("\nWhich brands are the right ones is a human call: read the lists above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Fix generation, Opus 5 at medium effort against the cheapest current model
— the cost brief, 2026-09-11.

One scan's facts and candidates (the latest scan for `--domain`; the Epic 8
acceptance fixture `epic7-degraded.example` has no open candidates any more,
so the default is the Epic 18.1 subject) are assembled
exactly as `fix_runner.generate_for_scan` assembles them, and the SAME prompt
is sent to each candidate model. The output goes through the production
`accept()` filter, so the accepted/rejected split is what each model would
have persisted. Nothing is written to the database.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \\
        uv run python scripts/verify_fix_models.py [--domain helpwise.io]

Costs real money: one call per config, the Opus one being the most expensive
single generation the pipeline makes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.models import Client, Scan  # noqa: E402
from avp_api.services import fix_runner  # noqa: E402
from avp_api.services.fix_generator import (  # noqa: E402
    FIX_BOUND,
    FIX_MAX_TOKENS,
    SYSTEM_PROMPT,
    GeneratedFixSet,
    accept,
    build_fix_prompt,
)

DEFAULT_DOMAIN = "helpwise.io"

RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

CONFIGS: dict[str, dict[str, object]] = {
    # Production today: fix_generator.py's FIX_MODEL / FIX_EFFORT.
    "opus5-medium": {"model": "claude-opus-5", "output_config": {"effort": "medium"}},
    # Haiku 4.5 has no effort parameter and no thinking unless asked for.
    "haiku45": {"model": "claude-haiku-4-5-20251001"},
    # Haiku 4.5 with extended thinking, the nearest thing to "medium effort"
    # the model offers (budget must be < max_tokens and >= 1024).
    "haiku45-think": {
        "model": "claude-haiku-4-5-20251001",
        "thinking": {"type": "enabled", "budget_tokens": 4_000},
    },
}


def rate_for(model: str) -> tuple[float, float]:
    matches = [k for k in RATES if model.startswith(k)]
    return RATES[max(matches, key=len)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=DEFAULT_DOMAIN)
    args = ap.parse_args()
    settings = Settings()
    url = os.environ.get("DATABASE_URL") or settings.database_url
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        # A domain can be a client of more than one agency; the latest scan
        # across all of them is the one with the freshest facts.
        scan = (
            await session.execute(
                select(Scan)
                .join(Client, Client.id == Scan.client_id)
                .where(Client.domain == args.domain)
                .order_by(Scan.created_at.desc())
            )
        ).scalars().first()
        assert scan is not None, f"no scan for {args.domain}"
        client = await session.get(Client, scan.client_id)
        assert client is not None
        candidates = await fix_runner.build_candidates_for(session, scan)
        facts = await fix_runner.collect_facts(session, scan, client)
    await engine.dispose()

    prompt = build_fix_prompt(facts, candidates)
    print(f"scan {scan.id}: {len(candidates)} candidates, prompt {len(prompt)} chars")
    print("  " + ", ".join(c.key for c in candidates))

    api = FIX_BOUND.client(settings)
    summary = []
    for name, config in CONFIGS.items():
        print("\n" + "=" * 84 + f"\n{name}\n" + "=" * 84)
        started = time.perf_counter()
        try:
            async with FIX_BOUND.deadline():
                response = await api.messages.parse(
                    max_tokens=FIX_MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_format=GeneratedFixSet,
                    **config,
                )
        except ValidationError as exc:
            seconds = time.perf_counter() - started
            print(f"  SCHEMA VIOLATION after {seconds:.1f}s"
                  " — production would degrade to Epic 7's list")
            for err in exc.errors()[:5]:
                loc = ".".join(str(x) for x in err.get("loc", ()))
                print(f"    {loc}: {err.get('type')}")
            summary.append((name, "schema_violation", 0, 0, seconds, None, None))
            continue
        seconds = time.perf_counter() - started
        parsed = response.parsed_output
        if parsed is None:
            print(f"  no parsed output: stop_reason={response.stop_reason}")
            summary.append((name, response.stop_reason, 0, 0, seconds, None, None))
            continue
        accepted, rejected = accept(parsed, candidates)
        print(f"  {seconds:.1f}s, served by {response.model}, accepted {len(accepted)},"
              f" rejected {rejected or 'none'}")
        for fix in accepted:
            print(f"\n  [{fix.rank}] {fix.source.value}:{fix.source_key}  {fix.priority.value} /"
                  f" {fix.effort.value}")
            print(f"      {fix.title}")
            print(f"      {fix.detail}")
        for g in parsed.fixes:
            print(f"\n  reasoning {g.candidate_id}: priority — {g.priority_reason}")
            print(f"            effort — {g.effort_reason}")
        u = response.usage
        rate = rate_for(response.model)
        cost = (u.input_tokens * rate[0] + u.output_tokens * rate[1]) / 1_000_000
        summary.append((name, "generated", u.input_tokens, u.output_tokens, seconds, cost,
                        (len(accepted), len(rejected))))

    print("\n" + "=" * 84)
    print(f"  {'config':14} {'status':16} {'in':>7} {'out':>7} {'secs':>6} {'$':>8}"
          "  accepted/rejected")
    for name, status, tin, tout, secs, cost, split in summary:
        c = f"{cost:8.4f}" if cost is not None else "       -"
        print(f"  {name:14} {status:16} {tin:7,} {tout:7,} {secs:6.1f} {c}  {split}")
    print("\nQuality is a human call: read the titles and details against the candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

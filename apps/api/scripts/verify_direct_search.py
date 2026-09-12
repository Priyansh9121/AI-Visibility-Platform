"""Grounded Claude engine, dynamic filtering (production) against direct
calls, at a representative sample — the citation-definition brief,
2026-09-12.

The six-call probe (`verify_search_filtering.py`) found the direct path is
the only one whose text carries the model's own citations, at a cost
within a few percent of the filtered default. This re-measures on twelve
real prompts from the last scan — four per intent, the first twelve by
position, which the round-robin order guarantees — twice each, and
records what the PRODUCT would have extracted from each answer:
`extract_facts` with the scan's own subject and competitor set, so any
change in mentions or brand counts is visible in the pipeline's terms.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \\
        uv run python scripts/verify_direct_search.py --runs 2

Costs real money: 12 x 2 shapes x runs grounded calls (~$0.16 each).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import anthropic  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.models import Client, Prompt, PromptSet, Scan  # noqa: E402
from avp_api.models.engine_result import Engine, EngineResultStatus  # noqa: E402
from avp_api.services import extraction  # noqa: E402
from avp_api.services.crawl import registrable_domain  # noqa: E402
from avp_api.services.engines import (  # noqa: E402
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    CLAUDE_STOPS,
    DEFAULT_TIMEOUT,
    ENGINE_CALL_CEILING,
    MAX_RETRIES,
    SEARCH_MAX_USES,
    CitedSource,
    EngineAnswer,
    _extract_citations,
    classify_stop,
)
from avp_api.services.scan_runner import load_competitors  # noqa: E402

SCAN_ID = "scan_01M285WHT33F9DRKGWGEAFNQ8H"  # groovehq.com, 2026-09-11
RATE_IN, RATE_OUT, FEE = 5.00, 25.00, 0.01

SHAPES: dict[str, dict[str, object]] = {
    "production": {"type": "web_search_20260209", "name": "web_search",
                   "max_uses": SEARCH_MAX_USES},
    "direct": {"type": "web_search_20260209", "name": "web_search",
               "max_uses": SEARCH_MAX_USES, "allowed_callers": ["direct"]},
}


def inline_citations(response) -> list[CitedSource]:  # noqa: ANN001
    """What `_extract_citations` would read if it read the answer's own
    citations: `web_search_result_location` entries on text blocks."""
    out: list[CitedSource] = []
    seen: set[str] = set()
    for block in response.content:
        if getattr(block, "type", None) != "text":
            continue
        for c in getattr(block, "citations", None) or []:
            url = getattr(c, "url", None)
            if getattr(c, "type", "") != "web_search_result_location" or not url or url in seen:
                continue
            domain = registrable_domain(url)
            if not domain:
                continue
            seen.add(url)
            out.append(CitedSource(url=url[:2048], domain=domain, position=len(out) + 1))
    return out


@dataclass
class Obs:
    shape: str
    intent: str
    index: int
    run: int
    status: str = "ok"
    error: str = ""
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    chars: int = 0
    block_cit: int = 0
    inline_cit: int = 0
    inline_subject: int = 0
    block_subject: int = 0
    mentioned: bool = False
    brands: int = 0
    subject_position: int | None = None
    names: list[str] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return (self.input_tokens * RATE_IN + self.output_tokens * RATE_OUT) / 1e6 + (
            self.searches * FEE
        )


async def one(client, shape, prompt, run, subject_name, subject_domain, competitors, sem):  # noqa: ANN001, E501
    async with sem:
        obs = Obs(shape=shape, intent=prompt.intent.value, index=prompt.position, run=run)
        kwargs: dict = {
            "model": ANSWER_MODEL, "max_tokens": ANSWER_MAX_TOKENS,
            "output_config": {"effort": ANSWER_EFFORT},
            "messages": [{"role": "user", "content": prompt.text}],
            "tools": [dict(SHAPES[shape])],
        }
        started = time.perf_counter()
        try:
            async with asyncio.timeout(ENGINE_CALL_CEILING):
                response = await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            obs.status, obs.error = "error", type(exc).__name__
            obs.seconds = time.perf_counter() - started
            return obs
        obs.seconds = time.perf_counter() - started
    u = response.usage
    obs.input_tokens, obs.output_tokens = u.input_tokens, u.output_tokens
    server = getattr(u, "server_tool_use", None)
    obs.searches = int(getattr(server, "web_search_requests", 0) or 0) if server else 0
    if response.stop_reason == "refusal":
        obs.status = "refusal"
        return obs
    incomplete = classify_stop(response.stop_reason, CLAUDE_STOPS)
    if incomplete is not None:
        obs.status = incomplete[1]
        return obs
    text = "".join(b.text for b in response.content if b.type == "text")
    obs.chars = len(text)
    block = _extract_citations(response)
    inline = inline_citations(response)
    obs.block_cit, obs.inline_cit = len(block), len(inline)
    obs.block_subject = sum(1 for c in block if c.domain == subject_domain)
    obs.inline_subject = sum(1 for c in inline if c.domain == subject_domain)
    answer = EngineAnswer(
        engine=Engine.CLAUDE_SEARCH, engine_version="probe", prompt_text=prompt.text,
        text=text, citations=block, status=EngineResultStatus.OK,
    )
    facts = extraction.extract_facts(
        answer, subject_name=subject_name, subject_domain=subject_domain,
        competitors=competitors,
    )
    obs.mentioned, obs.brands = facts.mentioned, facts.brands_mentioned
    obs.subject_position = facts.position
    obs.names = [h.name for h in facts.brand_hits]
    return obs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--prompts", type=int, default=12)
    args = ap.parse_args()
    settings = Settings()
    engine = create_async_engine(os.environ.get("DATABASE_URL") or settings.database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        scan = await session.get(Scan, SCAN_ID)
        assert scan is not None
        client_row = await session.get(Client, scan.client_id)
        assert client_row is not None
        prompt_set = (
            await session.execute(select(PromptSet).where(PromptSet.scan_id == scan.id))
        ).scalars().first()
        prompts = list((await session.execute(
            select(Prompt).where(Prompt.prompt_set_id == prompt_set.id).order_by(Prompt.position)
        )).scalars())[: args.prompts]
        competitors = await load_competitors(session, scan)
    await engine.dispose()
    subject_name = client_row.brand_name or client_row.domain
    subject_domain = client_row.domain
    print(f"subject {subject_name} ({subject_domain}), {len(competitors)} competitors,"
          f" {len(prompts)} prompts: "
          + ", ".join(f"{p.intent.value[:4]}" for p in prompts))

    api = anthropic.AsyncAnthropic(
        api_key=settings.provider_key("anthropic_api_key"),
        timeout=DEFAULT_TIMEOUT, max_retries=MAX_RETRIES,
    )
    sem = asyncio.Semaphore(6)
    jobs = [(s, p, r) for s in SHAPES for r in range(args.runs) for p in prompts]
    results: list[Obs] = await asyncio.gather(*(
        one(api, s, p, r, subject_name, subject_domain, competitors, sem) for s, p, r in jobs
    ))

    print("\nPER CALL")
    print(f"  {'shape':10} {'intent':6} {'p':>2} r {'status':8} {'in':>7} {'out':>6} {'srch':>4}"
          f" {'chars':>5} {'cit blk':>7} {'cit inl':>7} {'subj blk/inl':>12} {'named':>5}"
          f" {'pos':>3} {'brands':>6} {'$':>7} {'s':>5}")
    for o in results:
        print(f"  {o.shape:10} {o.intent[:6]:6} {o.index:2} {o.run + 1} {o.status[:8]:8}"
              f" {o.input_tokens:7,} {o.output_tokens:6,} {o.searches:4} {o.chars:5}"
              f" {o.block_cit:7} {o.inline_cit:7} {o.block_subject:5}/{o.inline_subject:<6}"
              f" {'Y' if o.mentioned else 'n':>5} {str(o.subject_position or '-'):>3}"
              f" {o.brands:6} {o.cost:7.4f} {o.seconds:5.1f}")

    print("\nBY SHAPE")
    for s in SHAPES:
        rs = [o for o in results if o.shape == s]
        ok = [o for o in rs if o.status == "ok"]
        n = max(1, len(ok))
        secs = sorted(o.seconds for o in ok) or [0.0]
        print(f"  {s:10} calls {len(rs)}  ok {len(ok)}  failures {len(rs) - len(ok)}"
              f" ({', '.join(o.error or o.status for o in rs if o.status != 'ok') or 'none'})")
        print(f"  {'':10} in/call {sum(o.input_tokens for o in ok) // n:7,}"
              f"  out/call {sum(o.output_tokens for o in ok) // n:5,}"
              f"  searches/call {sum(o.searches for o in ok) / n:.2f}"
              f"  zero-search calls {sum(1 for o in ok if o.searches == 0)}"
              f"  $/call {sum(o.cost for o in ok) / n:.4f}"
              f"  p50 {secs[len(secs) // 2]:.1f}s"
              f"  p90 {secs[min(len(secs) - 1, int(0.9 * len(secs)))]:.1f}s"
              f"  max {secs[-1]:.1f}s")
        print(f"  {'':10} chars/answer {sum(o.chars for o in ok) // n:5}"
              f"  subject named {sum(1 for o in ok if o.mentioned)}/{len(ok)}"
              f"  brands/answer {sum(o.brands for o in ok) / n:.1f}"
              f"  citations/answer block {sum(o.block_cit for o in ok) / n:.1f}"
              f" inline {sum(o.inline_cit for o in ok) / n:.1f}"
              f"  answers citing subject block {sum(1 for o in ok if o.block_subject)}"
              f" inline {sum(1 for o in ok if o.inline_subject)}")

    print("\nPAIRED, per prompt and run (production vs direct): mentioned, brands, in tokens")
    for r in range(args.runs):
        for p in prompts:
            a = next(o for o in results
                     if o.shape == "production" and o.index == p.position and o.run == r)
            b = next(o for o in results
                     if o.shape == "direct" and o.index == p.position and o.run == r)
            named = f"{'Y' if a.mentioned else 'n'}/{'Y' if b.mentioned else 'n'}"
            print(f"  r{r + 1} p{p.position:2} {p.intent.value[:4]}  named {named}"
                  f"  brands {a.brands:2}/{b.brands:<2}"
                  f"  in {a.input_tokens:6,}/{b.input_tokens:<6,}"
                  f"  searches {a.searches}/{b.searches}"
                  f"  cit {a.block_cit:2}/{b.block_cit:<2} (direct inline {b.inline_cit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

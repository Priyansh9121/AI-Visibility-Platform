"""Is the grounded Claude engine's dynamic filtering actually running, and
is `response_inclusion` safe for its citations? — 2026-09-12.

The grounded engine (`ClaudeSearchAdapter`) sends `web_search_20260209`,
whose documentation says search results are filtered by code execution
before they reach the context ("dynamic filtering", on by default). The
measured cost — about 21,000 input tokens a call — reads like unfiltered
pages. This probe sends EXACTLY the request `_ClaudeBase.ask` sends (no
system prompt, the tool with `max_uses`, low effort) for three real
awareness prompts from the last measured scan, and records what comes
back: the block sequence, the `caller` on every search block, usage, and
the citations both ways `engines.py` could read them.

Shapes:
  production   what the adapter sends today (web_search_20260209, defaults)
  direct       the same, with allowed_callers=["direct"] — filtering off
  full         web_search_20260318, response_inclusion "full"
  excluded     web_search_20260318, response_inclusion "excluded"

    uv run python scripts/verify_search_filtering.py --shapes production direct excluded --runs 2

Costs real money: each call is a grounded engine call (~$0.17 measured).
Raw responses are written to --dump-dir (default: none) for inspection;
they contain answer text and must never be committed or persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import anthropic  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.services.engines import (  # noqa: E402
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    SEARCH_MAX_USES,
    _extract_citations,
)

RATE_IN, RATE_OUT, FEE = 5.00, 25.00, 0.01  # claude-opus-5 $/MTok; $10 per 1k searches

# Awareness prompts from scan_01M285WHT33F9DRKGWGEAFNQ8H (groovehq.com,
# 2026-09-11), the product's own generated shape.
PROMPTS = [
    "what's the best help desk software for a small b2b saas company",
    "which customer support tools actually use ai to resolve tickets instead of just "
    "suggesting replies",
    "we're a 20 person saas startup drowning in support emails, what software should we "
    "look at",
]

SHAPES: dict[str, dict[str, object]] = {
    "production": {"type": "web_search_20260209", "name": "web_search",
                   "max_uses": SEARCH_MAX_USES},
    "direct": {"type": "web_search_20260209", "name": "web_search",
               "max_uses": SEARCH_MAX_USES, "allowed_callers": ["direct"]},
    "full": {"type": "web_search_20260318", "name": "web_search",
             "max_uses": SEARCH_MAX_USES, "response_inclusion": "full"},
    "excluded": {"type": "web_search_20260318", "name": "web_search",
                 "max_uses": SEARCH_MAX_USES, "response_inclusion": "excluded"},
}


@dataclass
class Obs:
    shape: str
    prompt_index: int
    run: int
    stop_reason: str = ""
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    searches: int = 0
    blocks: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    search_result_blocks: int = 0
    search_results: int = 0
    code_exec_blocks: int = 0
    text_chars: int = 0
    block_citations: list[str] = field(default_factory=list)   # what engines.py reads today
    inline_citations: list[str] = field(default_factory=list)  # web_search_result_location
    error: str = ""

    @property
    def cost(self) -> float:
        return (self.input_tokens * RATE_IN + self.output_tokens * RATE_OUT) / 1e6 + (
            self.searches * FEE
        )


def _caller_of(block) -> str:  # noqa: ANN001
    caller = getattr(block, "caller", None)
    if caller is None:
        return "-"
    return str(getattr(caller, "type", caller))


async def one(client, shape: str, i: int, run: int, dump: Path | None) -> Obs:  # noqa: ANN001
    obs = Obs(shape=shape, prompt_index=i, run=run)
    kwargs: dict = {
        "model": ANSWER_MODEL,
        "max_tokens": ANSWER_MAX_TOKENS,
        "output_config": {"effort": ANSWER_EFFORT},
        "messages": [{"role": "user", "content": PROMPTS[i]}],
        "tools": [dict(SHAPES[shape])],
    }
    started = time.perf_counter()
    try:
        response = await client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        obs.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        obs.seconds = time.perf_counter() - started
        return obs
    obs.seconds = time.perf_counter() - started
    obs.stop_reason = response.stop_reason or ""
    u = response.usage
    obs.input_tokens, obs.output_tokens = u.input_tokens, u.output_tokens
    obs.cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    obs.cache_write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    server = getattr(u, "server_tool_use", None)
    obs.searches = int(getattr(server, "web_search_requests", 0) or 0) if server else 0

    seen_inline: set[str] = set()
    for block in response.content:
        t = getattr(block, "type", "?")
        obs.blocks.append(t)
        if t in ("server_tool_use", "web_search_tool_result"):
            obs.callers.append(f"{t}<-{_caller_of(block)}")
        if t == "web_search_tool_result":
            obs.search_result_blocks += 1
            content = getattr(block, "content", None)
            if isinstance(content, list):
                obs.search_results += len(content)
        if "code_execution" in t:
            obs.code_exec_blocks += 1
        if t == "text":
            obs.text_chars += len(block.text)
            for c in getattr(block, "citations", None) or []:
                if getattr(c, "type", "") == "web_search_result_location":
                    url = getattr(c, "url", None)
                    if url and url not in seen_inline:
                        seen_inline.add(url)
                        obs.inline_citations.append(url)
    obs.block_citations = [c.url for c in _extract_citations(response)]
    if dump is not None:
        dump.mkdir(parents=True, exist_ok=True)
        (dump / f"{shape}-p{i}-r{run}.json").write_text(
            json.dumps(response.model_dump(mode="json"), indent=1)
        )
    return obs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes", nargs="+", default=list(SHAPES), choices=list(SHAPES))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--dump-dir", default=None)
    args = ap.parse_args()
    settings = Settings()
    client = anthropic.AsyncAnthropic(
        api_key=settings.provider_key("anthropic_api_key"),
        timeout=DEFAULT_TIMEOUT, max_retries=MAX_RETRIES,
    )
    dump = Path(args.dump_dir) if args.dump_dir else None
    sem = asyncio.Semaphore(4)

    async def guarded(shape, i, run):  # noqa: ANN001, ANN202
        async with sem:
            return await one(client, shape, i, run, dump)

    jobs = [(s, i, r) for s in args.shapes for r in range(args.runs) for i in range(len(PROMPTS))]
    results: list[Obs] = await asyncio.gather(*(guarded(*j) for j in jobs))

    print("PER CALL")
    print(f"  {'shape':10} {'p':>1} {'r':>1} {'stop':9} {'in':>7} {'out':>6} {'srch':>4} "
          f"{'res':>4} {'cx':>3} {'cit blk':>7} {'cit inl':>7} {'chars':>5} {'$':>7} {'s':>5}")
    for o in results:
        if o.error:
            print(f"  {o.shape:10} {o.prompt_index} {o.run + 1} !! {o.error}")
            continue
        print(f"  {o.shape:10} {o.prompt_index} {o.run + 1} {o.stop_reason:9} {o.input_tokens:7,} "
              f"{o.output_tokens:6,} {o.searches:4} {o.search_results:4} {o.code_exec_blocks:3} "
              f"{len(o.block_citations):7} {len(o.inline_citations):7} {o.text_chars:5} "
              f"{o.cost:7.4f} {o.seconds:5.1f}")

    print("\nBLOCK STRUCTURE (run 1 of each shape, prompt 0)")
    for o in results:
        if o.run == 0 and o.prompt_index == 0 and not o.error:
            print(f"  {o.shape:10} blocks: {' '.join(o.blocks)}")
            print(f"  {'':10} callers: {' '.join(o.callers) or '(no search blocks)'}")

    print("\nBY SHAPE (means over calls; searches and results summed)")
    for s in args.shapes:
        rs = [o for o in results if o.shape == s and not o.error]
        if not rs:
            print(f"  {s:10} no successful calls")
            continue
        n = len(rs)
        callers = sorted({c.split("<-")[1] for o in rs for c in o.callers})
        print(f"  {s:10} n={n} in {sum(o.input_tokens for o in rs) // n:7,}"
              f"  out {sum(o.output_tokens for o in rs) // n:6,}"
              f"  cache rd {sum(o.cache_read for o in rs)}"
              f"  searches {sum(o.searches for o in rs)}"
              f"  results {sum(o.search_results for o in rs)}"
              f"  code-exec blocks {sum(o.code_exec_blocks for o in rs)}"
              f"  citations blk {sum(len(o.block_citations) for o in rs)}"
              f" / inl {sum(len(o.inline_citations) for o in rs)}"
              f"  ${sum(o.cost for o in rs) / n:.4f}/call"
              f"  callers {callers}")

    print("\nCITATIONS, block path vs inline path, per call"
          " (urls in both / only block / only inline)")
    for o in results:
        if o.error:
            continue
        b, i = set(o.block_citations), set(o.inline_citations)
        print(f"  {o.shape:10} p{o.prompt_index} r{o.run + 1}: both {len(b & i):2}  "
              f"block-only {len(b - i):2}  inline-only {len(i - b):2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

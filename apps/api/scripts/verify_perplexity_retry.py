"""Perplexity `Retry-After`, honoured once — the probe, 2026-09-12.

Two parts, both against the real account, both cheap (refused requests are
not billed; each answered call is under a cent):

  1. Five calls started TOGETHER through `PerplexityAdapter.ask` directly —
     bypassing the start pacer — which Epic 21.1 showed draws 429s from the
     one-start-per-second bucket. With the fix, each 429 that names a wait
     is retried once, and the retry re-enters the pacer.
  2. Five calls through `ask_all`'s normal gating, the production path, to
     show the pacer still spaces starts and the retry does not fight it.

    uv run python scripts/verify_perplexity_retry.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.models.engine_result import Engine  # noqa: E402
from avp_api.services import engines  # noqa: E402

PROMPT = "Reply with the single word OK."
seen: list[tuple[float, int, str | None]] = []  # (t, status, retry-after)
_real_post = httpx.AsyncClient.post


async def _metered_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
    response = await _real_post(self, url, *args, **kwargs)
    if "perplexity.ai" in str(url):
        seen.append((time.perf_counter(), response.status_code, response.headers.get("retry-after")))
    return response


async def main() -> int:
    settings = Settings()
    httpx.AsyncClient.post = _metered_post  # type: ignore[method-assign]
    adapter = engines.PerplexityAdapter()

    print("PART 1 — five starts together, no pacer (the shape that drew 429s in Epic 21.1)")
    t0 = time.perf_counter()
    seen.clear()
    answers = await asyncio.gather(*(adapter.ask(PROMPT, settings=settings) for _ in range(5)))
    for a in answers:
        print(f"  {a.status.value:13} {a.error_code or '-':22} {a.latency_ms:6}ms")
    http = [(round(t - t0, 2), s, ra) for t, s, ra in seen]
    print(f"  HTTP responses (t, status, retry-after): {http}")
    print(f"  429s: {sum(1 for _, s, _ in seen if s == 429)}  retried-and-answered:"
          f" {sum(1 for a in answers if a.ok)}/5")

    print("\nPART 2 — five through ask_all (pacer + gate), the production path")
    t0 = time.perf_counter()
    seen.clear()
    results = await asyncio.gather(*(
        engines.ask_all(PROMPT, engines=(Engine.PERPLEXITY,), settings=settings) for _ in range(5)
    ))
    for (a,) in results:
        print(f"  {a.status.value:13} {a.error_code or '-':22} {a.latency_ms:6}ms")
    starts = [round(t - t0, 2) for t, _, _ in seen]
    print(f"  HTTP responses: {[(round(t - t0, 2), s, ra) for t, s, ra in seen]}")
    gaps = [round(b - a, 2) for a, b in zip(starts, starts[1:], strict=False)]
    print(f"  response-time gaps: {gaps}  429s: {sum(1 for _, s, _ in seen if s == 429)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

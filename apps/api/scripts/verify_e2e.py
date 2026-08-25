"""Live verification of the Epic 9 end-to-end timing criterion.

§7 Epic 9: "End-to-end test: URL in -> report out, under 5 minutes."

Chains the REAL pipeline against a FRESH client — create -> crawl + classify
(Epic 2) -> competitor detection (Epic 3) -> prompt generation and the scan loop
(Epic 4) -> technical audit (Epic 6) -> scoring (Epic 5) -> fix generation
(Epic 8) -> report projection (Epic 7/7.1). Nothing is mocked and nothing is
reused: the subject is created by this script so the Help Scout rows that
build-log.md and the checked-in web fixtures refer to are never touched.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/verify_e2e.py --prompts 24

COSTS REAL MONEY AND TIME. At --prompts 24 that is 55-103 claude-opus-5 calls
(48 engine calls, up to 48 sentiment calls, plus one classification, four
co-citation, one prompt-generation and one fix-generation call) and 6 SerpApi
searches against a 250/month quota. Run it once and read the table.

WHY THIS TIMES PHASES RATHER THAN THE WHOLE RUN
-----------------------------------------------
The only timed scan on record (build-log Epic 9.0) had a 109.3s wall clock
against a 42.8s worst single-engine call, over 3 prompts. Nothing recorded
where the other ~67s went, so "multiply by 8 for 24 prompts" could be wrong in
either direction: if the gap is fixed setup it barely grows, and if it scales
with prompts it dominates. A total cannot tell those apart. So this script
records three things per phase:

    duration        — time.perf_counter, the primitive EngineAnswer.latency_ms
                      already uses (engines.py:155)
    external calls  — how many, to which provider
    DB round trips  — every statement the phase issued

Those three together separate "slow because an LLM is slow" from "slow for a
reason we control" — which is the exact gap Epic 9.0 found in the one existing
data point.

HOW THE COUNTS ARE OBTAINED
---------------------------
By wrapping the real service functions at import time, from this script only.
No production module is edited: `install_meters` rebinds module attributes to
wrappers that await the original and record what it cost. The alternative —
recomputing what a phase "should" have called — measures the script's model of
the pipeline rather than the pipeline, which is the defect Finding 5 recorded
against verify_competitors.py (competitors.py:436). These wrappers observe the
real calls the real code makes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import event, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.models import Agency, Client, EngineResult  # noqa: E402
from avp_api.services import (  # noqa: E402  # noqa: E402  # noqa: E402  # noqa: E402
    audit_runner,
    cocitation,
    competitors,
    extraction,
    fix_generator,
    fix_runner,
    intake,
    scan_runner,
    scoring_runner,
    serp,
)
from avp_api.services import engines as engine_service  # noqa: E402
from avp_api.services import prompts as prompt_service
from avp_api.services import report as report_service  # noqa: E402

BUDGET_SECONDS = 300.0  # §7 Epic 9: "under 5 minutes"

DEFAULT_URL = "basecamp.com"


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass
class Call:
    """One external call, as observed by a wrapper."""

    provider: str
    label: str
    seconds: float


@dataclass
class Phase:
    """One pipeline stage: what it cost, and what it spent that time on."""

    name: str
    epic: str
    seconds: float = 0.0
    calls: list[Call] = field(default_factory=list)
    queries: int = 0
    note: str = ""

    @property
    def providers(self) -> str:
        if not self.calls:
            return "-"
        counts = Counter(c.provider for c in self.calls)
        return ", ".join(f"{p} x{n}" for p, n in sorted(counts.items()))

    @property
    def call_seconds(self) -> float:
        """Summed external-call time. Exceeds `seconds` when calls overlap —
        which is itself the signal that a phase ran concurrently."""
        return sum(c.seconds for c in self.calls)


class Meter:
    """Collects calls and DB statements against whichever phase is open.

    A single mutable `current` rather than a stack: the pipeline is a flat
    sequence of phases, and a nested phase would mean a phase boundary this
    script drew in the wrong place.
    """

    def __init__(self) -> None:
        self.phases: list[Phase] = []
        self.current: Phase | None = None

    def record(self, provider: str, label: str, seconds: float) -> None:
        if self.current is not None:
            self.current.calls.append(Call(provider, label, seconds))

    def record_query(self) -> None:
        if self.current is not None:
            self.current.queries += 1

    @asynccontextmanager
    async def phase(self, name: str, epic: str):  # noqa: ANN201
        phase = Phase(name=name, epic=epic)
        self.phases.append(phase)
        self.current = phase
        started = time.perf_counter()
        try:
            yield phase
        finally:
            phase.seconds = time.perf_counter() - started
            self.current = None


METER = Meter()


def install_meters() -> None:
    """Rebind real service functions to timing wrappers.

    Each target is patched where it is *looked up*, not where it is defined —
    `intake.py:26` does `from .classify import classify`, so patching
    `classify.classify` would leave intake's already-bound reference untouched.
    Every wrapper awaits the genuine function and changes no behaviour.
    """

    def wrap(module: object, attr: str, provider: str, label: str) -> None:
        original = getattr(module, attr)

        async def metered(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            started = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                METER.record(provider, label, time.perf_counter() - started)

        setattr(module, attr, metered)

    # Epic 2 — intake binds both of these by value at import.
    wrap(intake, "crawl_site", "playwright", "crawl")
    wrap(intake, "classify", "anthropic", "classify")

    # Epic 3 — serp.search_many and cocitation.run_seed_prompts both dispatch
    # through a module-global, so patching the singular call catches each one.
    wrap(serp, "search", "serpapi", "serp-search")
    wrap(cocitation, "run_seed_prompt", "anthropic", "co-citation")

    # Epic 4 — prompt generation, the engine calls, and the sentiment call that
    # run_prompt makes sequentially after each answer.
    wrap(prompt_service, "generate_prompts", "anthropic", "prompt-generation")
    wrap(engine_service, "ask_all", "anthropic", "engine-pair")
    wrap(extraction, "classify_sentiment", "anthropic", "sentiment")

    # Epic 6 — audit_runner binds audit_site by value (audit_runner.py:20).
    wrap(audit_runner, "audit_site", "http", "audit-fetch")

    # Epic 8 — fix_runner calls this as a module attribute (fix_runner.py:320).
    wrap(fix_generator, "generate_fixes", "anthropic", "fix-generation")


def install_query_counter(engine: object) -> None:
    """Count every statement the pipeline issues, attributed to the open phase.

    DB round trips are one of the three candidate explanations for Epic 9.0's
    unexplained ~67s, so they are counted rather than assumed negligible.
    """

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(*_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        METER.record_query()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def rule(title: str) -> None:
    print("\n" + "=" * 92)
    print(title)
    print("=" * 92)


def phase_table(phases: list[Phase], total: float) -> None:
    print(f"\n  {'phase':26} {'epic':9} {'secs':>8} {'%':>6} {'db':>5}  external calls")
    print("  " + "-" * 88)
    for phase in phases:
        share = (phase.seconds / total * 100) if total else 0
        print(
            f"  {phase.name:26} {phase.epic:9} {phase.seconds:8.1f} {share:5.1f}%"
            f" {phase.queries:5}  {phase.providers}"
        )
    print("  " + "-" * 88)
    print(f"  {'TOTAL':26} {'':9} {total:8.1f} {100.0:5.1f}%"
          f" {sum(p.queries for p in phases):5}")


def scan_loop_decomposition(
    loop: Phase, results: list[EngineResult], concurrency: int
) -> tuple[float, float]:
    """Split the scan loop into engine time and everything else.

    Returns (engine_bound_floor, overhead). The floor is what the loop would
    take if the only cost were the model calls themselves:

        per prompt : max(its engine latencies)   — ask_all gathers them
                     + its sentiment calls       — run_prompt awaits these
                                                   SEQUENTIALLY after the answers
        loop       : sum of that, divided by PROMPT_CONCURRENCY

    Sentiment time is spread evenly across prompts rather than attributed to
    the prompt that incurred it — the wrappers time each call but do not know
    which prompt it belonged to. Stated because it makes the floor an estimate
    where the engine half is exact.
    """
    by_prompt: dict[str, list[int]] = {}
    for row in results:
        by_prompt.setdefault(row.prompt_id, []).append(row.latency_ms or 0)

    engine_serial = sum(max(latencies) for latencies in by_prompt.values()) / 1000
    sentiment_serial = sum(c.seconds for c in loop.calls if c.label == "sentiment")

    floor = (engine_serial + sentiment_serial) / concurrency
    return floor, loop.seconds - floor


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=int, default=6)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    settings = Settings()
    install_meters()

    engine = create_async_engine(os.environ["DATABASE_URL"])
    install_query_counter(engine)
    Session = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806

    failures: list[str] = []

    rule(f"EPIC 9 END-TO-END TIMING — {args.url} @ {args.prompts} prompts")
    print(f"\n  budget      : {BUDGET_SECONDS:.0f}s (§7 Epic 9)")
    print(f"  concurrency : PROMPT_CONCURRENCY={scan_runner.PROMPT_CONCURRENCY}"
          f" (scan_runner.py:42)")

    overall = time.perf_counter()

    async with Session() as session:
        agency = (await session.execute(select(Agency).limit(1))).scalars().first()
        if agency is None:
            print("\n  no agency in this database — run scripts/seed_dev.py first")
            await engine.dispose()
            return 1

        # -- Phase 1: the client row -------------------------------------
        # The freshness check compares the NORMALISED domain, not the raw
        # argument: intake stores `example.com` for an input of
        # `https://www.example.com/`, so comparing the raw string would miss an
        # existing client and only discover it as a Conflict several calls later.
        try:
            _url, domain = intake.parse_domain(args.url)
        except intake.InvalidUrl as exc:
            print(f"\n  {exc}")
            await engine.dispose()
            return 1

        async with METER.phase("client creation", "—") as phase:
            existing = (
                await session.execute(
                    select(Client).where(
                        Client.agency_id == agency.id, Client.domain == domain
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                print(f"\n  {domain} already exists as {existing.id} — this run must"
                      " use a FRESH client so existing verification data stays intact.")
                await engine.dispose()
                return 1
            client = await intake.create_client_from_url(
                session, agency_id=agency.id, raw_url=args.url
            )
            await session.commit()
            phase.note = f"client {client.id}"
        print(f"\n  client      : {client.id} ({client.domain})")

        # -- Phase 2: crawl + classify (Epic 2) --------------------------
        async with METER.phase("crawl + classify", "2"):
            client, crawl = await intake.run_classification(
                session, client, settings=settings
            )
            await session.commit()
        print(f"  classified  : {client.classification_status.value}"
              f" industry={client.industry!r} brand={client.brand_name!r}")

        # -- Phase 3: competitor detection (Epic 3) ----------------------
        async with METER.phase("competitor detection", "3") as phase:
            scan = await competitors.get_or_create_scan(session, client)
            outcome = await competitors.detect_for_client(client, settings=settings)
            competitor_set = await competitors.persist_detection(session, scan, outcome)
            await session.commit()
            phase.note = outcome.status.value
        print(f"  competitors : {outcome.status.value},"
              f" {len(competitor_set.active_competitors)} active")

        # -- Phases 4 + 5: prompt generation, then the scan loop ---------
        # build_prompt_set is called from INSIDE run_scan, so the boundary
        # between "the generation call" and "the 48 engine calls" is drawn by
        # wrapping it: the wrapper opens its own phase, and closing it reopens
        # the loop phase. This is the one place a phase boundary sits inside a
        # service function rather than between two calls.
        original_build = scan_runner.build_prompt_set

        async def timed_build(*a, **kw):  # noqa: ANN002, ANN003, ANN202
            loop_phase = METER.current
            async with METER.phase("prompt generation", "4"):
                built = await original_build(*a, **kw)
            METER.current = loop_phase
            return built

        scan_runner.build_prompt_set = timed_build
        try:
            async with METER.phase("scan loop", "4") as loop_phase:
                scan = await scan_runner.run_scan(
                    session, scan, client, settings=settings, prompt_limit=args.prompts
                )
                await session.commit()
        finally:
            scan_runner.build_prompt_set = original_build

        # The loop phase was opened before prompt generation, so its wall clock
        # still includes it; subtract to leave the engine work alone.
        generation = next(p for p in METER.phases if p.name == "prompt generation")
        loop_phase.seconds -= generation.seconds
        # Reorder so the table reads in execution order.
        METER.phases.remove(generation)
        METER.phases.insert(METER.phases.index(loop_phase), generation)

        print(f"  scan        : {scan.status.value} prompts={scan.prompt_count}"
              f" results={scan.engine_result_count}")

        # -- Phase 6: technical audit (Epic 6) ---------------------------
        async with METER.phase("technical audit", "6") as phase:
            audit, audit_outcome = await audit_runner.run_audit(session, scan, client)
            await session.commit()
            phase.note = audit.status.value
        checks = Counter(c.status.value for c in audit.checks)
        print(f"  audit       : {audit.status.value} checks={dict(checks)}"
              f" foundation={audit.technical_foundation}")

        # -- Phase 7: scoring (Epic 5) -----------------------------------
        async with METER.phase("scoring", "5"):
            row, computed, _comparisons = await scoring_runner.score_scan(session, scan)
            await session.commit()
        print(f"  score       : {row.status.value} composite={row.composite}")

        # -- Phase 8: fix generation (Epic 8) ----------------------------
        async with METER.phase("fix generation", "8") as phase:
            fixes, fix_outcome = await fix_runner.generate_for_scan(
                session, scan, client, settings=settings
            )
            await session.commit()
            phase.note = fix_outcome.status
        print(f"  fixes       : {fix_outcome.status}, {len(fixes)} items")

        # -- Phase 9: report projection (Epic 7/7.1) ---------------------
        async with METER.phase("report projection", "7/7.1"):
            report = await report_service.build_report(session, scan)
        print(f"  report      : {len(report.dimensions)} dimensions,"
              f" {report.proof.engine_results} results,"
              f" {len(report.proof.prompt_shelf)} shelf rows")

        total = time.perf_counter() - overall

        results = list(
            (
                await session.execute(
                    select(EngineResult).where(EngineResult.scan_id == scan.id)
                )
            ).scalars()
        )

    # ------------------------------------------------------------------
    rule("PHASE BREAKDOWN")
    # ------------------------------------------------------------------
    phase_table(METER.phases, total)

    # ------------------------------------------------------------------
    rule("EXTERNAL CALLS BY PROVIDER")
    # ------------------------------------------------------------------
    by_provider: Counter = Counter()
    seconds_by_provider: Counter = Counter()
    for phase in METER.phases:
        for call in phase.calls:
            by_provider[call.provider] += 1
            seconds_by_provider[call.provider] += call.seconds
    print()
    for provider, count in sorted(by_provider.items()):
        print(f"  {provider:12} {count:4} calls   {seconds_by_provider[provider]:8.1f}s"
              " of call time (overlapping where concurrent)")

    # `ask_all` is one wrapper call per PROMPT, covering both engines, so the
    # billed engine-call count is the persisted row count, not the wrapper's.
    print(f"\n  billed engine calls (EngineResult rows) : {len(results)}")
    sentiment_calls = sum(
        1 for p in METER.phases for c in p.calls if c.label == "sentiment"
    )
    print(f"  billed sentiment calls                  : {sentiment_calls}")
    print(f"  billed SerpApi searches                 : {by_provider['serpapi']}")

    # ------------------------------------------------------------------
    rule("IS THE SCAN LOOP ENGINE-BOUND?")
    # ------------------------------------------------------------------
    loop = next(p for p in METER.phases if p.name == "scan loop")
    floor, overhead = scan_loop_decomposition(
        loop, results, scan_runner.PROMPT_CONCURRENCY
    )
    latencies = [r.latency_ms or 0 for r in results]
    print(f"\n  {'observed scan loop':29} : {loop.seconds:8.1f}s")
    label = f"engine-bound floor at c={scan_runner.PROMPT_CONCURRENCY}"
    print(f"  {label:29} : {floor:8.1f}s"
          "   (per-prompt max engine latency + sentiment, / concurrency)")
    print(f"  {'unexplained overhead':29} : {overhead:8.1f}s"
          f"   ({overhead / loop.seconds * 100:.0f}% of the phase)")
    if latencies:
        print(f"\n  slowest single engine call    : {max(latencies) / 1000:8.1f}s")
        print(f"  median engine call            :"
              f" {sorted(latencies)[len(latencies) // 2] / 1000:8.1f}s")
        print(f"  summed engine latency         : {sum(latencies) / 1000:8.1f}s"
              f"   across {len(latencies)} calls")

    # ------------------------------------------------------------------
    rule("VERDICT")
    # ------------------------------------------------------------------
    dominant = max(METER.phases, key=lambda p: p.seconds)
    print(f"\n  total wall clock : {total:.1f}s")
    print(f"  budget           : {BUDGET_SECONDS:.0f}s")
    if total <= BUDGET_SECONDS:
        print(f"  -> UNDER budget by {BUDGET_SECONDS - total:.1f}s")
    else:
        print(f"  -> OVER budget by {total - BUDGET_SECONDS:.1f}s")
        failures.append(f"total {total:.1f}s exceeds the {BUDGET_SECONDS:.0f}s budget")
    print(f"\n  dominant phase   : {dominant.name}"
          f" ({dominant.seconds:.1f}s, {dominant.seconds / total * 100:.0f}% of total)")

    if scan.status.value not in ("succeeded", "partial"):
        failures.append(f"scan finished {scan.status.value}")
    if not results:
        failures.append("no EngineResult rows — the timing describes nothing")

    print()
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
    print("RESULT:", "PASS" if not failures else "NEEDS REVIEW")
    print(f"\n  report URL : /scans/{scan.id}/report")

    await engine.dispose()
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

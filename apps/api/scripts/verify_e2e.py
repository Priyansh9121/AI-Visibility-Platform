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
from collections import Counter, defaultdict
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


# ---------------------------------------------------------------------------
# TOKENS AND MONEY — Epic 9.24
# ---------------------------------------------------------------------------
# north-star.md §5.1 has wanted a real per-scan cost since it was written, and
# every run before this one recorded call COUNTS and never token usage — so the
# dollar figure was always an estimate multiplied by a guess. This meters the
# providers' own reported usage at the SDK boundary, which is the number they
# bill from.
#
# Wrapped at the SDK, not at our call sites, for the same reason `install_meters`
# patches where a function is looked up: a per-site wrapper measures the sites
# this script remembered to wrap, and the SDK boundary measures every call that
# actually happened.

# Published rates, $ per million tokens, as (input, output). Both are read from
# the vendors' own pricing rather than inferred: a fabricated rate in a cost
# table is worse than an honest gap, and this table is the first thing in the
# project to put a real dollar figure on a scan.
#
#   claude-opus-5   $5 / $25    Anthropic's published rate.
#   gpt-5.5         $5 / $30    developers.openai.com/api/docs/pricing, read
#                               2026-09-08, for the <272K context tier this
#                               product's prompts sit far inside. Cached input
#                               is $0.50/MTok and is reported separately below;
#                               nothing here sends a cacheable prefix yet.
#
# A model that answers and is NOT in this table is metered in tokens and shown
# unpriced, so a model swap surfaces as a gap rather than as a silent zero.
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "gpt-5.5": (5.00, 30.00),
}


def rate_for(model: str) -> tuple[float, float] | None:
    """The published rate for a model id, matched by longest prefix.

    Providers report the DATED SNAPSHOT they actually served — a request for
    `gpt-5.5` comes back as `gpt-5.5-2026-04-23` — so an exact-key lookup
    silently prices every call at nothing. Longest prefix rather than any
    prefix, so `gpt-5.5-pro` could never be priced from the `gpt-5.5` row if it
    is ever added above it.
    """
    if model in RATES:
        return RATES[model]
    matches = [key for key in RATES if model.startswith(key)]
    if not matches:
        return None
    return RATES[max(matches, key=len)]
# Anthropic bills the server-side web_search tool PER REQUEST, on top of tokens.
# $10 per 1,000 searches — platform.claude.com/docs/en/agents-and-tools/tool-use/
# web-search-tool, read 2026-09-08. The retrieved pages are billed again as
# input tokens, which is already counted above and is the larger half by far.
# A search that ERRORS is not billed, and this counts what the API reported
# rather than what was attempted, so the two agree.
WEB_SEARCH_PER_1K = 10.00


@dataclass
class TokenUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    web_searches: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


TOKENS: dict[str, TokenUsage] = defaultdict(TokenUsage)


def install_token_meter() -> None:
    """Record provider-reported usage for every model call this run makes.

    Anthropic reports `usage` on the response object; OpenAI reports it in the
    JSON body, and `ChatGptAdapter` speaks raw `httpx`, so the two are metered
    at the two different boundaries they actually cross. The httpx wrapper
    filters on the host so it does not also count SerpApi and the audit's
    side-fetches, which carry no tokens and are already counted elsewhere.
    """
    import anthropic
    import httpx

    def _record(model: str, usage: object) -> None:
        entry = TOKENS[model]
        entry.calls += 1
        entry.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        entry.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        entry.cache_read += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        entry.cache_write += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        server = getattr(usage, "server_tool_use", None)
        if server is not None:
            entry.web_searches += int(getattr(server, "web_search_requests", 0) or 0)

    messages = anthropic.resources.messages.AsyncMessages
    for attr in ("create", "parse"):
        original = getattr(messages, attr)

        async def metered(self, *args, __original=original, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            response = await __original(self, *args, **kwargs)
            usage = getattr(response, "usage", None)
            if usage is not None:
                _record(getattr(response, "model", "anthropic/unknown"), usage)
            return response

        setattr(messages, attr, metered)

    real_post = httpx.AsyncClient.post

    async def metered_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        response = await real_post(self, url, *args, **kwargs)
        if "openai.com" not in str(url):
            return response
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON body carries no usage
            return response
        usage = payload.get("usage") or {}
        entry = TOKENS[payload.get("model", "openai/unknown")]
        entry.calls += 1
        entry.input_tokens += int(usage.get("prompt_tokens") or 0)
        entry.output_tokens += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        entry.cache_read += int(details.get("cached_tokens") or 0)
        return response

    httpx.AsyncClient.post = metered_post


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
    install_token_meter()

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
    rule("TOKENS AND MONEY")
    # ------------------------------------------------------------------
    print()
    print(f"  {'model':22} {'calls':>6} {'in':>10} {'out':>10} {'cache rd':>9} {'$':>9}")
    priced = 0.0
    unpriced: list[str] = []
    for model in sorted(TOKENS):
        u = TOKENS[model]
        rate = rate_for(model)
        if rate is None:
            cost_cell = "  unpriced"
            unpriced.append(model)
        else:
            cost = (u.input_tokens * rate[0] + u.output_tokens * rate[1]) / 1_000_000
            priced += cost
            cost_cell = f"{cost:9.4f}"
        print(f"  {model:22} {u.calls:6} {u.input_tokens:10,} {u.output_tokens:10,}"
              f" {u.cache_read:9,} {cost_cell}")
    searches = sum(u.web_searches for u in TOKENS.values())
    search_cost = searches * WEB_SEARCH_PER_1K / 1000
    print(f"\n  model spend         : ${priced:.4f}")
    if unpriced:
        print(f"  UNPRICED, tokens only: {', '.join(unpriced)}"
              "  (no published rate recorded here; not guessed)")
    print(f"  web_search requests : {searches:4}  ${search_cost:.4f}"
          f"   at ${WEB_SEARCH_PER_1K:.2f}/1k")
    print("  ---------------------------------")
    print(f"  TOTAL PROVIDER SPEND: ${priced + search_cost:.4f}")
    print(f"\n  SerpApi searches    : {by_provider['serpapi']} of a 250/month quota"
          "  (prepaid, no marginal charge)")

    # ------------------------------------------------------------------
    rule("CROSS-ENGINE READING (Epic 9.23)")
    # ------------------------------------------------------------------
    cross = report.proof.cross_engine
    print()
    for standing in cross.standings:
        sentiment = (
            f"{standing.sentiment}" if standing.sentiment is not None else "none"
        )
        print(f"  {standing.engine.value:16} answered {standing.answered:3}"
              f"   named {standing.mentioned:3}"
              f"   rate {standing.mention_rate:>6}"
              f"   sentiment {sentiment:>6}")
    agreement = cross.agreement_rate if cross.agreement_rate is not None else "n/a"
    print(f"\n  comparable prompts : {cross.comparable_prompts}")
    print(f"  split prompts      : {len(cross.splits)}")
    print(f"  agreement rate     : {agreement}")
    for split in cross.splits:
        print(f"    {split.prompt_id}"
              f"  named by {[e.value for e in split.named_by]}"
              f"  missed by {[e.value for e in split.missed_by]}")

    # THE QUESTION THIS RUN EXISTS TO ANSWER: on prompts where two or more
    # engines DID name the subject, do they agree about the tone? Divergence in
    # visibility and divergence in sentiment are different products.
    by_prompt: dict[str, list] = defaultdict(list)
    for row in results:
        if row.mentioned and row.sentiment is not None:
            by_prompt[row.prompt_id].append(row)
    shared = {p: rows for p, rows in by_prompt.items() if len(rows) >= 2}
    disagreeing = {
        p: rows for p, rows in shared.items()
        if len({r.sentiment for r in rows}) > 1
    }
    print(f"\n  prompts where 2+ engines named the subject AND both were scored"
          f" : {len(shared)}")
    print(f"  of those, engines that disagreed on SENTIMENT               "
          f" : {len(disagreeing)}")
    for prompt_id, rows in sorted(disagreeing.items()):
        detail = ", ".join(
            f"{r.engine.value}={r.sentiment.value}" for r in sorted(
                rows, key=lambda r: r.engine.value
            )
        )
        print(f"    {prompt_id}  {detail}")
    if shared and not disagreeing:
        print("    -> every engine that named the subject agreed on the tone")

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

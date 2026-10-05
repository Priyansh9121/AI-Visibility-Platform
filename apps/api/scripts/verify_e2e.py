"""Live verification of the Epic 9 end-to-end timing criterion.

§7 Epic 9: "End-to-end test: URL in -> report out, under 5 minutes."

Chains the REAL pipeline against a FRESH client — create -> crawl + classify
(Epic 2) -> competitor detection (Epic 3) -> prompt generation and the scan loop
(Epic 4) -> technical audit (Epic 6) -> scoring (Epic 5) -> fix generation
(Epic 8) -> report projection (Epic 7/7.1). Nothing is mocked and nothing is
reused: the subject is created by this script so the Help Scout rows that
build-log.md and the checked-in web fixtures refer to are never touched.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \
        uv run python scripts/verify_e2e.py --prompts 20

PRICING A SCAN YOU DID NOT RUN HERE (U1, 2026-10-04). Production logs one `scan.usage` line of
counts when each scan ends (`services/usage.py`). To say what a scan cost from that line:

    uv run python scripts/verify_e2e.py --price-usage '<the line as Render shows it>'
    pbpaste | uv run python scripts/verify_e2e.py --price-usage -

It prices each line at the rates in `RATES`, each dated and sourced, and lists every rate it
does not have rather than pricing the line at nothing. It needs no database, key or provider.

COSTS REAL MONEY AND TIME. At --prompts 20 on the four default engines that
is 27-107 Anthropic calls (20 parametric Claude engine calls, up to 80
sentiment calls on claude-haiku-4-5 — one per answer that names the subject,
across all four engines — plus one classification, four co-citation, one
prompt-generation and one fix-generation call on claude-opus-5), 60 calls to
the other three vendors, and 6 SerpApi searches against a 250/month quota.
Run it once and read the table. (Until 2026-09-12 the default was 24 prompts
on five engines, the grounded Claude engine included; see the build log.)

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
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import event, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.logs import configure_logging  # noqa: E402
from avp_api.models import Agency, Client, EngineResult  # noqa: E402
from avp_api.services import (  # noqa: E402  # noqa: E402  # noqa: E402  # noqa: E402
    audit_runner,
    classify,
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

# Published rates, $ per million tokens, as (input, output), EACH WITH THE DAY IT WAS READ
# AND THE PAGE IT WAS READ FROM (U1, 2026-10-04). Both numbers are read from the vendors' own
# pricing rather than inferred: a fabricated rate in a cost table is worse than an honest
# gap, and a rate is perishable, so a figure with no date is a guess that looks like a fact.
# The 2026-10-03 re-read covers every row below (the brief for U1 names the five pages).
# Before it each had been read on the day its old note gave: 2026-09-08 for `gpt-5.5`,
# 2026-09-11 for the other four.
#
#   claude-opus-5    $5 / $25      platform.claude.com/docs/en/about-claude/pricing
#   claude-haiku-4-5 $1 / $5       the same page — the sentiment classifier since the cost
#                                  brief. Matched by prefix: the API reports the dated
#                                  snapshot.
#   gpt-5.5          $5 / $30      developers.openai.com/api/docs/pricing, for the <272K
#                                  context tier this product's prompts sit far inside. Cached
#                                  input is $0.50/MTok and is reported separately; nothing
#                                  here sends a cacheable prefix yet.
#   perplexity/sonar $0.25 / $2.50 docs.perplexity.ai/docs/agent-api/models. Plus a
#                                  per-invocation web_search fee, in TOOL_FEES.
#   gemini-3.8-flash $0.75 / $3.75 ai.google.dev/gemini-api/docs/pricing — the promotional
#                                  rate through 2026-12-31; thinking tokens are billed as
#                                  output and are `thoughtsTokenCount`, a sibling of
#                                  `candidatesTokenCount`, added into the output count.
#
# A model that answers and is NOT in this table is metered in tokens and shown unpriced, so a
# model swap surfaces as a gap rather than as a silent zero.
READ_2026_10_03 = "2026-10-03"
ANTHROPIC_PRICING = "platform.claude.com/docs/en/about-claude/pricing"


class Rate(NamedTuple):
    """One model's published rate, and the page and day it was read from.

    `input` and `output` are dollars per MILLION tokens, as `Decimal`: money is not kept in a
    float, so the pricing of a pasted line is exact. Indexable, so `rate[0]` and `rate[1]` still
    read as (input, output), the shape this table had before it carried dates.
    """

    input: Decimal
    output: Decimal
    read: str
    source: str


RATES: dict[str, Rate] = {
    "claude-opus-5": Rate(Decimal("5.00"), Decimal("25.00"), READ_2026_10_03, ANTHROPIC_PRICING),
    "claude-haiku-4-5": Rate(
        Decimal("1.00"), Decimal("5.00"), READ_2026_10_03, ANTHROPIC_PRICING
    ),
    "gpt-5.5": Rate(
        Decimal("5.00"), Decimal("30.00"), READ_2026_10_03, "developers.openai.com/api/docs/pricing"
    ),
    "perplexity/sonar": Rate(
        Decimal("0.25"), Decimal("2.50"), READ_2026_10_03,
        "docs.perplexity.ai/docs/agent-api/models",
    ),
    "gemini-3.8-flash": Rate(
        Decimal("0.75"), Decimal("3.75"), READ_2026_10_03, "ai.google.dev/gemini-api/docs/pricing"
    ),
}


def rate_for(model: str) -> Rate | None:
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


class ToolFee(NamedTuple):
    """What a provider charges for each invocation of a tool, on top of the tokens."""

    per_invocation: Decimal
    read: str
    source: str


# Anthropic bills the server-side web_search tool PER REQUEST, on top of tokens.
# $10 per 1,000 searches — platform.claude.com/docs/en/agents-and-tools/tool-use/
# web-search-tool, read 2026-09-08. The retrieved pages are billed again as
# input tokens, which is already counted above and is the larger half by far.
# A search that ERRORS is not billed, and this counts what the API reported
# rather than what was attempted, so the two agree.
#
# Perplexity bills its `web_search` tool per invocation — $0.0025 each, i.e.
# $2.50 per 1,000 (docs/agent-api/models, read 2026-09-11) — reported back
# in `usage.tool_calls_details.web_search.invocation`. Metered separately
# from Anthropic's because the rate differs by 4x.
#
# These two keep the dates they were read on: the 2026-10-03 re-read names the pricing pages
# above, and whether it also covered these two figures is not something this table can say.
TOOL_FEES: dict[str, ToolFee] = {
    "anthropic web_search": ToolFee(
        Decimal("0.01"), "2026-09-08",
        "platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool",
    ),
    "perplexity web_search": ToolFee(
        Decimal("0.0025"), "2026-09-11", "docs.perplexity.ai/docs/agent-api/models"
    ),
}
WEB_SEARCH_PER_1K = float(TOOL_FEES["anthropic web_search"].per_invocation * 1000)
PERPLEXITY_SEARCH_PER_1K = float(TOOL_FEES["perplexity web_search"].per_invocation * 1000)


@dataclass
class TokenUsage:
    """One LINE of the cost table — see `anthropic_line` for why not one model."""

    # The model(s) that actually served the line, as the vendor reported them.
    # A set because the API echoes dated snapshots and a line is defined by
    # what the call is FOR, not by what served it.
    models: set[str] = field(default_factory=set)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    web_searches: int = 0
    # Anthropic web_search requests PER CALL, in call order — the cost brief's
    # Step 3: whether `SEARCH_MAX_USES` is headroom or load-bearing is a
    # distribution question the scan-wide sum above cannot answer.
    search_uses: list[int] = field(default_factory=list)
    perplexity_searches: int = 0
    # Token cost at the published rate, accumulated per call from the model
    # that served it, so a line served by two snapshots is still priced.
    token_cost: float = 0.0
    unpriced_models: set[str] = field(default_factory=set)
    # The vendor's OWN dollar figure, where one is reported. Perplexity's Agent
    # API returns `usage.cost.total_cost` on every call — Epic 21.1 — and the
    # billed figure beats any rate table: its input side is billed in tiers
    # this table does not model (measured: 1,758 input tokens cost $0.00014,
    # not the $0.00044 the published rate implies).
    reported_cost: float = 0.0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


# Keyed by LINE — what the call is for — not by model (the cost brief's
# Step 1, 2026-09-11). Both Claude engines share `claude-opus-5`, as do
# classification, fix generation, co-citation and prompt generation, so a
# per-model table could not say what the grounded engine costs on its own —
# very likely the single most expensive thing in a scan, and invisible.
TOKENS: dict[str, TokenUsage] = defaultdict(TokenUsage)

# The ten things a scan pays a model for: five engines, five analysis calls.
LINE_ORDER = (
    "engine: claude",
    "engine: claude_search",
    "engine: chatgpt",
    "engine: perplexity",
    "engine: gemini",
    "sentiment",
    "classification",
    "fix generation",
    "co-citation",
    "prompt generation",
)

# Every Anthropic analysis call goes through `messages.parse` with its own
# output schema, so the schema IS the call site — matched by identity, the
# way conftest's `dispatching_parse` already dispatches on `output_format`.
_PARSE_LINES: dict[type, str] = {
    extraction.SentimentJudgement: "sentiment",
    classify.IndustryClassification: "classification",
    fix_generator.GeneratedFixSet: "fix generation",
    cocitation.CoCitationAnswer: "co-citation",
    prompt_service.GeneratedPromptSet: "prompt generation",
}


def anthropic_line(kwargs: dict) -> str:
    """Which line of the cost table an Anthropic request belongs to.

    Read off the request's own shape rather than off a context variable set
    by production code, so the pipeline is measured without being changed:
    an `output_format` names an analysis call; a `web_search` tool names the
    grounded engine; a bare `create` is the parametric engine (`_ClaudeBase`
    is the only caller that sends neither).
    """
    fmt = kwargs.get("output_format")
    if fmt is not None:
        return _PARSE_LINES.get(fmt, f"parse: {getattr(fmt, '__name__', fmt)}")
    tools = kwargs.get("tools") or []
    if any(
        isinstance(tool, dict) and str(tool.get("type", "")).startswith("web_search")
        for tool in tools
    ):
        return "engine: claude_search"
    return "engine: claude"


def record_tokens(
    line: str, model: str, *, input_tokens: int, output_tokens: int,
    cache_read: int = 0, cache_write: int = 0,
) -> TokenUsage:
    """Add one call to a line, priced from the model that served it."""
    entry = TOKENS[line]
    entry.models.add(model)
    entry.calls += 1
    entry.input_tokens += input_tokens
    entry.output_tokens += output_tokens
    entry.cache_read += cache_read
    entry.cache_write += cache_write
    rate = rate_for(model)
    if rate is None:
        entry.unpriced_models.add(model)
    else:
        entry.token_cost += float(
            (input_tokens * rate.input + output_tokens * rate.output) / 1_000_000
        )
    return entry


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

    def _record(line: str, model: str, usage: object) -> None:
        entry = record_tokens(
            line, model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )
        server = getattr(usage, "server_tool_use", None)
        searches = int(getattr(server, "web_search_requests", 0) or 0) if server else 0
        entry.web_searches += searches
        if line == "engine: claude_search":
            # Zero is a data point: a grounded call that chose not to search.
            entry.search_uses.append(searches)

    messages = anthropic.resources.messages.AsyncMessages
    for attr in ("create", "parse"):
        original = getattr(messages, attr)

        async def metered(self, *args, __original=original, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            response = await __original(self, *args, **kwargs)
            usage = getattr(response, "usage", None)
            if usage is not None:
                _record(
                    anthropic_line(kwargs),
                    getattr(response, "model", "anthropic/unknown"),
                    usage,
                )
            return response

        setattr(messages, attr, metered)

    real_post = httpx.AsyncClient.post

    async def metered_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        response = await real_post(self, url, *args, **kwargs)
        host = str(url)
        if not any(h in host for h in ("openai.com", "perplexity.ai", "googleapis.com")):
            return response
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON body carries no usage
            return response
        # A refused request (429, 5xx) carries no usage and no model, and the
        # vendor does not bill it; counting it as a call under an unknown
        # model overstated Perplexity's calls by its two 429s on 2026-09-11.
        if not (payload.get("usage") or payload.get("usageMetadata")):
            return response
        if "openai.com" in host:
            usage = payload.get("usage") or {}
            details = usage.get("prompt_tokens_details") or {}
            record_tokens(
                "engine: chatgpt", payload.get("model", "openai/unknown"),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                cache_read=int(details.get("cached_tokens") or 0),
            )
        elif "perplexity.ai" in host:
            # Agent API usage — Epic 21. Tool invocations are billed per call
            # and reported by tool name.
            usage = payload.get("usage") or {}
            details = usage.get("input_tokens_details") or {}
            entry = record_tokens(
                "engine: perplexity", payload.get("model", "perplexity/unknown"),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read=int(details.get("cache_read_input_tokens") or 0),
            )
            tools = usage.get("tool_calls_details") or {}
            entry.perplexity_searches += int(
                (tools.get("web_search") or {}).get("invocation") or 0
            )
            entry.reported_cost += float((usage.get("cost") or {}).get("total_cost") or 0)
        else:
            # Gemini `usageMetadata` — Epic 21. The model is not echoed in the
            # body, so it is read off the URL (`.../models/{model}:generate
            # Content`). Thought tokens are billed as output.
            usage = payload.get("usageMetadata") or {}
            model = host.rsplit("/models/", 1)[-1].split(":", 1)[0] or "gemini/unknown"
            record_tokens(
                "engine: gemini", model,
                input_tokens=int(usage.get("promptTokenCount") or 0),
                output_tokens=int(usage.get("candidatesTokenCount") or 0)
                + int(usage.get("thoughtsTokenCount") or 0),
                cache_read=int(usage.get("cachedContentTokenCount") or 0),
            )
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
# PRICING A PASTED `scan.usage` LINE — U1, 2026-10-04
# ---------------------------------------------------------------------------
# Production logs COUNTS and never a dollar figure (`services/usage.py`): each paid call, and,
# when a scan ends, one `scan.usage` line totalling them per line of what a scan pays for. This
# is where counts become money: the line as Render shows it, priced at the rates above, per
# line and in total, with the date each rate was read, and with every rate it does NOT have
# said out loud and never priced at nothing.
#
#     uv run python scripts/verify_e2e.py --price-usage '<the line>'
#     pbpaste | uv run python scripts/verify_e2e.py --price-usage -
#     ... --serp-per-search 0.015       the plan's price for one SerpApi search, if you know it
#
# It needs no database, no key and no provider. `price_usage` is the function the tests call.

MILLION = Decimal(1_000_000)
USAGE_COUNTERS = (
    "calls", "unanswered", "retries", "input_tokens", "output_tokens", "reasoning_tokens",
    "cache_read_tokens", "cache_write_tokens", "search_invocations",
)
# The lines of a `scan.usage` that are not engines, in the order they are shown. `engines_*` is
# every engine added together and is NEVER priced: it would count each engine twice.
USAGE_LINES = (
    ("sentiment", "sentiment"),
    ("prompt_generation", "prompt generation"),
    ("fixes", "fixes"),
    ("detection", "detection"),
)
_ENGINE_CALLS = re.compile(r"^engine_(?P<id>.+)_calls$")
_USAGE_FIELD = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*)=(\S+)")


@dataclass
class PricedLine:
    """One line of the scan, priced, or not priced and why (`missing`)."""

    name: str
    model: str | None
    calls: int
    unanswered: int
    retries: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    search_invocations: int
    rate: Rate | None = None
    fee: ToolFee | None = None
    token_cost: Decimal | None = None
    search_cost: Decimal | None = None
    cost: Decimal | None = None
    missing: str | None = None


@dataclass
class SerpSummary:
    """What SerpApi was asked, and what the searches that may have been billed come to."""

    attempted: int = 0
    ok: int = 0
    error: int = 0
    no_answer: int = 0
    # `serpapi_billed_or_unknown`: the answered searches and the ones nobody answered. An upper
    # bound on what was billed: an answered search is billed unless SerpApi served it from its
    # cache, an error answer is not billed, and a search nobody answered may have been. Read off
    # the line when it has the field, else worked out as `ok + no_answer`, which is what it is.
    billed_or_unknown: int = 0
    per_search: Decimal | None = None
    # `billed_or_unknown` searches at the plan's price: an upper bound, and in the total as one.
    cost: Decimal | None = None


@dataclass
class UsagePricing:
    scan_id: str | None
    rows: list[PricedLine]
    serp: SerpSummary
    total: Decimal
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_usage_line(text: str) -> dict[str, str | int | None]:
    """The fields of the first `scan.usage` line in `text`.

    Reads the line as Render shows it (`2026-10-04 03:49:18 [info     ] scan.usage  a=1 ...`,
    structlog's console renderer, which is what production writes), or a JSON object with
    `"event": "scan.usage"`, which is what a JSON renderer would. Integers come back as `int`,
    `None` as `None`, everything else as the text.
    """
    for raw in text.splitlines():
        if "scan.usage" not in raw:
            continue
        stripped = raw.strip()
        if stripped.startswith("{"):
            try:
                body = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(body, dict) and body.get("event") == "scan.usage":
                return {k: v for k, v in body.items() if isinstance(v, str | int | None)}
            continue
        fields: dict[str, str | int | None] = {}
        for key, value in _USAGE_FIELD.findall(raw):
            if re.fullmatch(r"-?\d+", value):
                fields[key] = int(value)
            elif value == "None":
                fields[key] = None
            else:
                fields[key] = value
        return fields
    raise ValueError(
        "no scan.usage line found. Paste the line Render shows for the scan's `scan.usage` "
        "event, with its fields (`scan_id=... engines_calls=...`)."
    )


def _count(fields: dict[str, str | int | None], key: str) -> int:
    value = fields.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def tool_fee_for(model: str) -> ToolFee | None:
    """The tool fee of the provider a model belongs to, if this table records one."""
    if model.startswith("claude"):
        return TOOL_FEES["anthropic web_search"]
    if model.startswith("perplexity/"):
        return TOOL_FEES["perplexity web_search"]
    return None


def _price_line(name: str, prefix: str, fields: dict[str, str | int | None]) -> PricedLine:
    model_field = fields.get(f"{prefix}_model")
    row = PricedLine(
        name=name,
        model=model_field if isinstance(model_field, str) else None,
        **{counter: _count(fields, f"{prefix}_{counter}") for counter in USAGE_COUNTERS},
    )
    if not (row.input_tokens or row.output_tokens or row.search_invocations):
        # Nothing was spent, so no rate is needed to say what it cost.
        row.cost = Decimal(0)
        return row
    if row.model is None:
        row.missing = f"{name}: no model recorded"
        return row
    # A line can name several models (`a+b`): two snapshots of one family are one rate, and are
    # priced as one. Models with no rate, or with different ones, are named and not guessed at.
    served = row.model.split("+")
    rates = {model: rate_for(model) for model in served}
    unknown = [model for model, rate in rates.items() if rate is None]
    distinct = {id(rate) for rate in rates.values() if rate is not None}
    if unknown:
        row.missing = f"{name}: no rate for {', '.join(unknown)}"
    elif len(distinct) > 1:
        row.missing = (
            f"{name}: served by more than one model with different rates ({row.model}); price "
            "it from its llm.call lines"
        )
    else:
        row.rate = rates[served[0]]
        assert row.rate is not None
        row.token_cost = (
            Decimal(row.input_tokens) * row.rate.input
            + Decimal(row.output_tokens) * row.rate.output
        ) / MILLION
        row.cost = row.token_cost
        if row.search_invocations:
            row.fee = tool_fee_for(served[0])
            if row.fee is None:
                row.cost = None
                row.missing = f"{name}: no per-search fee recorded for {row.model}"
            else:
                row.search_cost = Decimal(row.search_invocations) * row.fee.per_invocation
                row.cost = row.token_cost + row.search_cost
    return row


def price_usage(text: str, *, serp_per_search: Decimal | None = None) -> UsagePricing:
    """Price a pasted `scan.usage` line: per line, and in total, at the dated rates above.

    A line whose model has no rate (or that more than one model served, or that names none)
    is left out of the total and listed in `missing`: pricing it at nothing would make a model
    swap look like a saving. Reasoning is inside the output count and is not charged again. A
    line with cache tokens is priced at the plain input rate and says so, since no cached-token
    rate is recorded. SerpApi's per-search price depends on the plan, so unless it is given
    (`serp_per_search`) the searches are counted and the price is listed as missing. Given, it
    prices `serpapi_billed_or_unknown`, an upper bound (a line from before that field existed
    has it worked out from the two counts it is the sum of). The price must be a finite number
    of at least zero, or this raises `ValueError`.
    """
    if serp_per_search is not None:
        serp_per_search = valid_per_search(serp_per_search)
    fields = parse_usage_line(text)
    scan_id = fields.get("scan_id")
    rows: list[PricedLine] = []
    engine_ids = sorted(
        m.group("id") for key in fields if (m := _ENGINE_CALLS.match(key)) is not None
    )
    for engine_id in engine_ids:
        if _count(fields, f"engine_{engine_id}_calls"):
            rows.append(_price_line(f"engine: {engine_id}", f"engine_{engine_id}", fields))
    for prefix, name in USAGE_LINES:
        if _count(fields, f"{prefix}_calls"):
            rows.append(_price_line(name, prefix, fields))

    serp_ok = _count(fields, "serpapi_ok")
    serp_no_answer = _count(fields, "serpapi_no_answer")
    stated = fields.get("serpapi_billed_or_unknown")
    serp = SerpSummary(
        attempted=_count(fields, "serpapi_attempted"), ok=serp_ok,
        error=_count(fields, "serpapi_error"), no_answer=serp_no_answer,
        # The line's own figure when it has one, else the sum it is: older lines have no field.
        billed_or_unknown=(
            stated if isinstance(stated, int) and not isinstance(stated, bool)
            else serp_ok + serp_no_answer
        ),
        per_search=serp_per_search,
    )
    missing = [row.missing for row in rows if row.missing]
    if _count(fields, "engines_calls") and not engine_ids:
        # `engines_*` is every engine together and is never priced, so a line with no per-engine
        # fields has nothing to price the engines from.
        missing.insert(0, "engines: the line has no engine_<id>_* fields, so no engine is priced")
    notes: list[str] = []
    for row in rows:
        if row.cache_read_tokens or row.cache_write_tokens:
            notes.append(
                f"{row.name}: {row.cache_read_tokens:,} cache read and {row.cache_write_tokens:,}"
                " cache write tokens present; not priced separately (no cached-token rate is "
                "recorded), so its input is priced at the plain rate"
            )
    if serp.attempted:
        if serp_per_search is None:
            missing.append(
                "serpapi: no per-search rate recorded (the plan sets it; pass --serp-per-search)"
            )
        else:
            serp.cost = Decimal(serp.billed_or_unknown) * serp_per_search
    total = sum((row.cost for row in rows if row.cost is not None), Decimal(0))
    if serp.cost is not None:
        total += serp.cost
    if missing:
        notes.append("the total leaves out every line listed under MISSING RATES")
    return UsagePricing(
        scan_id=scan_id if isinstance(scan_id, str) else None,
        rows=rows, serp=serp, total=total, missing=missing, notes=notes,
    )


def format_usage_pricing(pricing: UsagePricing) -> str:
    """The priced scan as a table, with the rates it used and the day each was read."""
    out: list[str] = []
    out.append(f"SCAN USAGE PRICED{' — ' + pricing.scan_id if pricing.scan_id else ''}")
    out.append(
        "  Rates are perishable: each was read from the vendor's page on the date shown below,"
        " and nothing here is a quote."
    )
    out.append(
        f"\n  {'line':20} {'model':28} {'calls':>5} {'unans':>5} {'retry':>5} {'in':>10}"
        f" {'out':>9} {'(reason)':>9} {'srch':>5} {'$ tokens':>9} {'$ line':>9}"
    )
    out.append("  " + "-" * 128)
    for row in pricing.rows:
        tokens = f"{row.token_cost:9.4f}" if row.token_cost is not None else f"{'-':>9}"
        line = f"{row.cost:9.4f}" if row.cost is not None else f"{'unpriced':>9}"
        out.append(
            f"  {row.name:20} {(row.model or '-')[:28]:28} {row.calls:5} {row.unanswered:5}"
            f" {row.retries:5} {row.input_tokens:10,} {row.output_tokens:9,}"
            f" {row.reasoning_tokens:9,} {row.search_invocations:5} {tokens} {line}"
        )
    out.append("  " + "-" * 128)
    out.append(f"  {'TOTAL PRICED':20} {'':28} {'':5} {'':5} {'':5} {'':10} {'':9} {'':9} {'':5}"
               f" {'':9} {pricing.total:9.4f}")
    out.append("  ($ per line = tokens at the rate + each search at its fee; the reasoning column")
    out.append("  is INSIDE the output count and is not charged again.)")

    out.append("\n  RATES USED, each as read on the date shown")
    shown_rates: set[int] = set()
    shown_fees: set[int] = set()
    for row in pricing.rows:
        if row.rate is not None and id(row.rate) not in shown_rates:
            shown_rates.add(id(row.rate))
            family = next(key for key, rate in RATES.items() if rate is row.rate)
            out.append(
                f"    {family:28} ${row.rate.input} in / ${row.rate.output} out per million"
                f"   read {row.rate.read}   {row.rate.source}"
            )
        if row.fee is not None and id(row.fee) not in shown_fees:
            shown_fees.add(id(row.fee))
            family = next(key for key, fee in TOOL_FEES.items() if fee is row.fee)
            out.append(
                f"    {family:28} ${row.fee.per_invocation} per invocation"
                f"   read {row.fee.read}   {row.fee.source}"
            )
    if not shown_rates:
        out.append("    (none)")

    serp = pricing.serp
    if serp.attempted:
        out.append(
            f"\n  SerpApi: {serp.attempted} searches attempted (retries included): {serp.ok} "
            f"answered ok, {serp.error} answered with an error (not billed), {serp.no_answer} "
            "with no answer"
        )
        out.append(
            f"  SerpApi billed or unknown: {serp.billed_or_unknown}, an upper bound on what was "
            "billed: an answered search is billed unless SerpApi served it from its cache, an "
            "error answer is not billed, and a search nobody answered may have been"
        )
        if serp.cost is not None:
            out.append(
                f"    priced at {serp.per_search} a search: {serp.cost:.4f}, an upper bound, "
                "and it is in the total as one"
            )
    if pricing.missing:
        out.append("\n  MISSING RATES, left out of the total")
        out.extend(f"    {message}" for message in pricing.missing)
    for note in pricing.notes:
        out.append(f"\n  NOTE  {note}")
    return "\n".join(out)


PRICE_RULE = "give a finite number of 0 or more, such as 0.015"


def valid_per_search(value: Decimal) -> Decimal:
    """The price of one SerpApi search, if it is a price: finite, and not below zero.

    `Decimal` accepts NaN, a signalling NaN, the infinities and negatives without complaint and
    carries them into arithmetic, where a signalling NaN raises `InvalidOperation` and the rest
    come out as a figure that means nothing. A negative zero is zero.
    """
    if not value.is_finite() or value < 0:
        raise ValueError(f"{str(value)!r} is not a price: {PRICE_RULE}")
    return value.copy_abs() if value == 0 else value


def _decimal_argument(text: str) -> Decimal:
    """`--serp-per-search`'s value: a price, or an argparse error (a message and exit code 2)."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        # `Decimal("abc")`, `Decimal("")`, `Decimal("1e")` and an exponent it cannot hold.
        raise argparse.ArgumentTypeError(f"{text!r} is not a number: {PRICE_RULE}") from None
    try:
        return valid_per_search(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a price: {PRICE_RULE}") from None


def price_usage_command(source: str, serp_per_search: Decimal | None) -> int:
    """`--price-usage`: read the line (`-` is standard input), price it, print it."""
    text = sys.stdin.read() if source == "-" else source
    try:
        pricing = price_usage(text, serp_per_search=serp_per_search)
    except ValueError as exc:
        print(f"verify_e2e: {exc}", file=sys.stderr)
        return 2
    except ArithmeticError as exc:
        # `decimal.Overflow` and the rest of `decimal`'s signals: a price that passes the checks
        # above (finite, not negative) and is still too large to multiply.
        print(
            f"verify_e2e: a number was too large to price ({type(exc).__name__}); "
            f"check --serp-per-search, which must be {PRICE_RULE.removeprefix('give ')}",
            file=sys.stderr,
        )
        return 2
    print(format_usage_pricing(pricing))
    return 0


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


async def main(argv: list[str] | None = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=int, default=6)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--price-usage", metavar="LINE",
        help="price a scan.usage line from the logs (- reads it from standard input) and "
             "exit: no database, no key and no provider is touched",
    )
    parser.add_argument(
        "--serp-per-search", type=_decimal_argument, default=None, metavar="DOLLARS",
        help="with --price-usage: what the SerpApi plan charges for one search, a finite number "
             "of 0 or more. It prices serpapi_billed_or_unknown, an upper bound on what SerpApi "
             "billed (an answered search, unless SerpApi's cache served it, and a search nobody "
             "answered); without it SerpApi is listed as a missing rate",
    )
    args = parser.parse_args(argv)

    if args.price_usage is not None:
        return price_usage_command(args.price_usage, args.serp_per_search)

    settings = Settings()
    # Before any call to SerpApi: pins httpx and the SDKs quiet, so that no log line,
    # whatever the shell exports (ANTHROPIC_LOG, say), prints the SerpApi key's URL.
    configure_logging(settings)
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
                    session,
                    scan,
                    client,
                    settings=settings,
                    # The engines with a key, as the endpoint runs — Epic 21.
                    engines=engine_service.configured_engines(settings),
                    prompt_limit=args.prompts,
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
    # One row per LINE — what the call was for — with the model beside it,
    # so the two Claude engines and the five analysis calls that share Opus 5
    # are priced apart. `$ line` is what that line cost in full: tokens plus
    # the per-request search fee for the grounded Claude engine, or the
    # vendor's own reported figure (*) where one exists.
    print()
    print(f"  {'line':22} {'model':27} {'calls':>5} {'in':>9} {'out':>8}"
          f" {'cache rd':>8} {'srch':>5} {'$ tokens':>9} {'$ line':>9}")
    priced = 0.0
    unpriced: list[str] = []
    ordered = [line for line in LINE_ORDER if line in TOKENS] + sorted(
        line for line in TOKENS if line not in LINE_ORDER
    )
    for line in ordered:
        u = TOKENS[line]
        model = "+".join(sorted(u.models)) or "-"
        fee = u.web_searches * WEB_SEARCH_PER_1K / 1000
        if u.reported_cost > 0:
            # The vendor said what it billed, tools included; that wins.
            line_cost = u.reported_cost
            line_cell = f"{line_cost:8.4f}*"
        elif u.unpriced_models:
            line_cost = 0.0
            line_cell = " unpriced"
            unpriced.extend(sorted(u.unpriced_models))
        else:
            line_cost = u.token_cost + fee
            line_cell = f"{line_cost:9.4f}"
        priced += line_cost
        print(f"  {line:22} {model[:27]:27} {u.calls:5} {u.input_tokens:9,}"
              f" {u.output_tokens:8,} {u.cache_read:8,} {u.web_searches:5}"
              f" {u.token_cost:9.4f} {line_cell}")

    searches = sum(u.web_searches for u in TOKENS.values())
    search_cost = searches * WEB_SEARCH_PER_1K / 1000
    pplx_searches = sum(u.perplexity_searches for u in TOKENS.values())
    pplx_cost = pplx_searches * PERPLEXITY_SEARCH_PER_1K / 1000
    reported = any(u.reported_cost > 0 for u in TOKENS.values())
    if unpriced:
        print(f"\n  UNPRICED, tokens only: {', '.join(unpriced)}"
              "  (no published rate recorded here; not guessed)")
    print(f"\n  web_search requests : {searches:4}  ${search_cost:.4f}"
          f"   at ${WEB_SEARCH_PER_1K:.2f}/1k  (Anthropic; inside the claude_search line)")
    if reported:
        print("  * vendor-reported cost, tool calls included — not the rate table")
    print(f"  perplexity searches : {pplx_searches:4}  ${pplx_cost:.4f}"
          f"   at ${PERPLEXITY_SEARCH_PER_1K:.2f}/1k"
          + ("  (inside the * figure; not added again)" if reported else ""))

    # Step 3 of the cost brief: is SEARCH_MAX_USES headroom or load-bearing?
    grounded = TOKENS.get("engine: claude_search")
    uses = sorted(grounded.search_uses) if grounded is not None else []
    if uses:
        n = len(uses)
        cap = engine_service.SEARCH_MAX_USES
        p90 = uses[max(0, -(-9 * n // 10) - 1)]
        at_cap = sum(1 for x in uses if x >= cap)
        print(f"\n  web_search per claude_search call ({n} calls, max_uses={cap}):"
              f" median {uses[n // 2]}, p90 {p90}, max {uses[-1]},"
              f" at the cap {at_cap} ({100 * at_cap / n:.0f}%), zero {uses.count(0)}")
        print(f"  distribution        : {dict(Counter(uses))}")
    print("  ---------------------------------")
    # NOT `total`. That name is the run's wall clock, read by the VERDICT
    # below; Epic 21.1's first draft of this line rebound it to dollars and
    # the verdict then reported a $6.50 scan as 6.5 seconds, "UNDER budget by
    # 293.5s", with the dominant phase at "2847% of total". Caught by reading
    # the log against its own phase table, and recorded so a reviewer of that
    # log knows the corrected figures: 239.0s total, 61.0s under budget.
    # The search fee is already inside the claude_search line, and
    # Perplexity's inside its reported figure; neither is added again.
    spend_total = priced + (0.0 if reported else pplx_cost)
    print(f"  TOTAL PROVIDER SPEND: ${spend_total:.4f}")
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

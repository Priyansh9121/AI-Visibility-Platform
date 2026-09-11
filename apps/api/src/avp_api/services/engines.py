"""Engine adapters — §5.4 step 4, "run each prompt against each AI engine".

=============================================================================
FACTS-ONLY BOUNDARY
=============================================================================
`EngineAnswer.text` holds a live engine response. It is a plain dataclass with
no SQLAlchemy mapping and no persistence path — the same contract as
`CrawlResult` (Epic 2) and `SerpResult` (Epic 3). It exists so
`extraction.py` can derive facts in the same request, and is discarded with it.

What survives is on EngineResult: booleans, ordinal positions, a sentiment
label, cited domains and URLs, and a SHA-256 digest of the answer.
`response_digest` is how re-scan change-detection works *without* keeping the
text — two scans can be compared for "did the answer change?" by comparing
hashes.
=============================================================================

Adding an engine
----------------
Implement `EngineAdapter` and register it in `ENGINE_REGISTRY`. Nothing in the
runner, extraction or persistence layer knows which engines exist. Adding
ChatGPT or Perplexity is a new class plus a provider key — no pipeline change.
The one thing every adapter must ALSO say is how its vendor reports "I
stopped": a `StopVocabulary`, so a response the engine did not finish is never
read as an answer (see "Incomplete answers" below).

Why two Claude modes rather than two vendors
--------------------------------------------
Only ANTHROPIC_API_KEY is provisioned. The two adapters below are genuinely
different answer engines in the way this product measures:

* `claude` answers from parametric recall — it names what it learned in
  training, and cites nothing. This is how a non-browsing assistant behaves.
* `claude_search` retrieves live and answers with real cited URLs. This is how
  a grounded assistant (Perplexity, AI Overviews) behaves, and it is the only
  source of genuine Citation rows available today.

They routinely disagree, which is the point — a brand can be absent from
grounded answers while present in parametric ones, and that gap is exactly what
a report should surface.

Cross-vendor variance — Epic 9.13
----------------------------------
Epic 4.2 ended: "**They are still one vendor and one model**, so this does not
test cross-vendor variance. That limitation is credential-bound, not
design-bound." An `OPENAI_API_KEY` has since been provisioned, so `chatgpt`
joins as a third engine and that limitation is now partly closed.

`chatgpt` is deliberately the PARAMETRIC analogue of `claude`, not of
`claude_search`. Epic 4.2's reasoning is applied unchanged rather than replaced:
two modes of one vendor answer differently, and so do two vendors in the same
mode. Holding the mode constant is what makes "Claude names you, ChatGPT does
not" a statement about the vendors rather than about browsing. A grounded
OpenAI engine is a fourth adapter for a later brief, not a variant of this one.

No `openai` SDK. Its current major requires `httpx2`, a second HTTP stack
alongside the `httpx` this repo already pins, and the answer path needs only
text out of a JSON POST. `httpx` is already vetted (BSD-3-Clause) — the same
call Epic 3.1 made for SerpApi, for the same reason.

Perplexity and Gemini — Epic 21
-------------------------------
The fourth and fifth adapters close the gap north-star.md named: two of the
four planned vendors were an enum member with nothing behind them. Both are
raw `httpx`, for the OpenAI adapter's reason, and both keep the mode
discipline Epic 4.2 set:

* `perplexity` is GROUNDED — search is what Perplexity is — so it is the
  second engine after `claude_search` that produces real `Citation` rows,
  and the first from a second vendor. Sonar's chat-completions endpoint is
  being sunset on 2026-09-27 (docs.perplexity.ai, read 2026-09-11), so this
  adapter speaks the Agent API that replaces it, with Perplexity's own
  `sonar` model and an explicit `web_search` tool: "what Perplexity answers"
  has to mean Perplexity's model, not the API's default preset, which is
  an OpenAI model behind a Perplexity search.
* `gemini` is PARAMETRIC — no Google Search grounding tool — so it is the
  third vendor in the `claude` / `chatgpt` mode, and a disagreement among
  the three stays a statement about vendors. A grounded Gemini is a later
  adapter, as a grounded OpenAI is; and Google's AI Overview is neither: it
  has no API at all and would be a two-step SerpApi scrape competing with
  competitor detection for that quota. Deferred, in build-log Epic 21.

An engine with no key is not live. `DEFAULT_ENGINES` names the five this
product measures; `configured_engines(settings)` is the subset with a key
behind it, and it is what a scan actually runs — see the note on it below.
Each adapter also declares how many of its calls may be in flight at once
(`max_in_flight`), because Perplexity's and Google's rate limits at the
tiers a new account starts on are one and two orders of magnitude under
Anthropic's; `ask_all` gates on it, and the runner's `PROMPT_CONCURRENCY`
stays what it is.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
import httpx
import structlog

from ..config import Settings, get_settings
from ..models.engine_result import Engine, EngineResultStatus
from .crawl import registrable_domain

logger = structlog.get_logger(__name__)

ANSWER_MODEL = "claude-opus-5"
ANSWER_EFFORT = "low"
ANSWER_MAX_TOKENS = 4_000
SEARCH_MAX_USES = 4

# --- The engine-call time bound (Epic 9.2) -----------------------------------
# `timeout` on the Anthropic client bounds ONE HTTP attempt, not the call. The
# SDK retries `APIConnectionError`, and `APITimeoutError` subclasses it, so with
# the pinned anthropic 0.125.0's `DEFAULT_MAX_RETRIES = 2` a "90 second timeout"
# was really 3 x 90s plus backoff. Epic 9.1 measured exactly that: one call
# burned 271.6s and returned nothing — 16.5% of all engine time in that run.
#
# Both numbers below are explicit and never inherited: pyproject.toml allows
# anywhere in `anthropic>=0.40,<1`, and the SDK default is not ours to assume.
#
# The trade-off, chosen deliberately rather than silently:
#   * `max_retries = 1`, not 0. Epic 9.1's latency distribution has two calls at
#     117s and 118s. No single attempt could exceed the 90s per-attempt bound,
#     so each was a timed-out attempt plus a retry that SUCCEEDED at ~27s.
#     Dropping to zero retries would have turned 2 of 48 calls (4.2%) from
#     answers into PROVIDER_UNREACHABLE. One retry is worth keeping.
#   * 60s per attempt, not 90s. The slowest call that actually succeeded in that
#     run was 46s, and 21 of 24 grounded calls landed between 11s and 44s. 60s
#     keeps ~30% headroom over the slowest observed success while letting two
#     attempts sum to something bounded.
#
# The cost is real and accepted: a genuinely slow-but-alive call between 60s and
# 90s that would previously have completed now fails as a timeout. Fewer retries
# also means more transient failures surface as a status instead of silently
# recovering. That is the price of a ceiling that is actually a ceiling.
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 1
# Backoff between attempts is the SDK's, not ours: INITIAL_RETRY_DELAY 0.5s
# doubling toward MAX_RETRY_DELAY 8.0s, times jitter in (0.75, 1.0]. At one
# retry that is at most 0.5s; 2.0s is a deliberately generous allowance.
RETRY_BACKOFF_ALLOWANCE = 2.0
# The stated ceiling, enforced directly by an outer deadline in `ask()` so the
# bound holds by construction even if the SDK's retry or backoff internals move
# under the version range above.
ENGINE_CALL_CEILING = DEFAULT_TIMEOUT * (MAX_RETRIES + 1) + RETRY_BACKOFF_ALLOWANCE

# --- OpenAI (Epic 9.13) ------------------------------------------------------
# Called over raw httpx, so there is no SDK retry policy to inherit and nothing
# to pin defensively — `httpx.AsyncClient` does not retry at all. The same outer
# `asyncio.timeout(ENGINE_CALL_CEILING)` still wraps the call, so the bound this
# module advertises holds for every engine by the same mechanism rather than by
# two different ones.
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_ANSWER_MODEL = "gpt-5.5"
OPENAI_MAX_TOKENS = 4_000
# The OpenAI analogue of ANSWER_EFFORT, and pinned for the same reason the
# Anthropic knobs above are: a provider default is not ours to assume. gpt-5.5
# is a reasoning model whose effort DEFAULTS TO MEDIUM (its model page lists
# none / low / medium / high / xhigh), and Chat Completions counts reasoning
# tokens against `max_completion_tokens`. Left unset, every call spent
# medium-effort reasoning inside a 4,000-token budget this module sized for a
# low-effort answer — and reasoning that eats the whole budget is precisely
# how a response comes back `finish_reason: "length"` with an EMPTY body,
# which the audit found being recorded as "ChatGPT answered and did not name
# you". Low here, low on the Claude side: the two parametric engines are
# compared on equal footing (Epic 9.13), and that footing includes effort.
OPENAI_REASONING_EFFORT = "low"

# --- Perplexity (Epic 21) ----------------------------------------------------
# The Agent API, not Sonar chat completions: the latter "will be supported
# until September 27, 2026" (docs.perplexity.ai/docs/agent-api/migrate-from-
# sonar, read 2026-09-11), seventeen days after this was written. Building on
# it would have been building on a door already marked for closure.
PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
# Perplexity's own model, named explicitly. The API's `preset: "fast"` — the
# documented replacement for plain Sonar — is `openai/gpt-5.6-luna` behind a
# Perplexity search, and a row recorded as "Perplexity named you" must mean
# Perplexity's model did. `perplexity/sonar` is $0.25 in / $2.50 out per
# million (agent-api/models, read 2026-09-11) plus $0.0025 per web_search
# invocation; the search-context size below is the knob on the retrieved
# tokens that fill the input side.
PERPLEXITY_ANSWER_MODEL = "perplexity/sonar"
PERPLEXITY_MAX_OUTPUT_TOKENS = 4_000
PERPLEXITY_SEARCH_CONTEXT = "low"
PERPLEXITY_MAX_RESULTS = 10
# MEASURED — Epic 21.1, 2026-09-11. Stage 2, four realistic prompts in
# sequence against the real Agent API: 6.0s, 7.2s, 10.1s, 15.2s, every one a
# complete answer with ten to twenty search results. Stage 3, the 24-prompt
# scan with up to ~10 in flight: 18 successes from 5.3s to 29.9s (median
# ~12s), so concurrency roughly doubled the tail. 45s is 1.5x the slowest
# success at n=18 — the same order of headroom DEFAULT_TIMEOUT keeps over its
# 46s — and was chosen at Stage 2 as 3x the then-slowest, which the fuller
# sample bore out. Under DEFAULT_TIMEOUT, so the shared 122s ceiling holds,
# and under half of it, so a single retry would still fit if one is added.
PERPLEXITY_TIMEOUT = 45.0
# THE GATE IS A START RATE, NOT AN IN-FLIGHT CAP — Epic 21.1. Written first
# as "three in flight" against the documented Tier 0 figure of 50 requests a
# minute. The real account answers every call with `x-ratelimit-limit: 1`,
# `x-ratelimit-remaining: 0` and a reset one second out, and three calls
# started together drew two `429 request_rate_limit_exceeded` with
# `Retry-After: 1` (a $0 probe; refused requests are not billed). That is a
# bucket of ONE START PER SECOND, and how many are in flight does not
# matter to it. So this engine spaces its starts instead: 1.25s apart is 48
# a minute, under the documented 50 and under the observed 1/s, and at the
# measured 6–15s a call it still puts eight to twelve in flight — which is
# why there is no in-flight cap here at all. At Tier 1 (150/min, $50
# cumulative spend) this could be 0.4s; the header says which tier applies.
#
# WHAT THE FULL SCAN THEN SHOWED, AND WHAT IT DID NOT SETTLE. At this
# spacing the 24-prompt scan still had 6 of 24 Perplexity calls answered
# 429: two inside a second (the entry bucket) and four after 11–17s, which
# the docs describe as an overloaded upstream model. A ten-call probe at the
# same spacing, up to nine in flight, then came back 10 of 10 clean, so the
# pacing is not the whole story and the error bodies were not persisted to
# say which kind each was. Refused requests are not billed. The candidate
# fix — honour `Retry-After` once, inside the ceiling — is a deliberate
# decision recorded in build-log Epic 21.1, not made here.
PERPLEXITY_MIN_START_INTERVAL = 1.25

# --- Gemini (Epic 21) --------------------------------------------------------
# The Generative Language API over raw httpx, keyed by the `x-goog-api-key`
# header (gemini-api/docs/api-key, read 2026-09-11) — never the `?key=`
# query form, which lands the credential in access logs.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Chosen, not inherited, the way ANSWER_MODEL and OPENAI_ANSWER_MODEL are.
# `gemini-3.8-flash` is the current stable Flash (gemini-api/docs/models, read
# 2026-09-11): the brief's `gemini-2.5-flash` is two generations back, and
# "what Gemini answers today" has to mean the model Google ships today. It is
# $0.75 in / $3.75 out per million through 2026-12-31, thinking billed as
# output — and it is PAID TIER ONLY: the free tier serves `gemini-3-flash-
# preview` at 10 requests a minute and 1,500 a day, which a 24-prompt scan
# would spend a fifth of. A billed Google AI account is the founder's to
# create, and this module cannot pretend otherwise; see build-log Epic 21.
GEMINI_ANSWER_MODEL = "gemini-3.8-flash"
GEMINI_MAX_OUTPUT_TOKENS = 4_000
# The Gemini analogue of ANSWER_EFFORT and OPENAI_REASONING_EFFORT, pinned for
# their reason: Gemini 3 Flash defaults to HIGH thinking, and thinking tokens
# count against `maxOutputTokens` — the same empty-`length` failure gpt-5.5
# produced at medium effort. Gemini 3 cannot turn thinking off ("minimal" is
# "not guaranteed"); "low" is the honest floor and the same footing the two
# other parametric engines answer on. `thinkingLevel`, not the 2.5-era
# `thinkingBudget`: the two cannot be sent together and 3.x takes the former.
GEMINI_THINKING_LEVEL = "low"
# MEASURED — Epic 21.1, 2026-09-11. Stage 2, four realistic prompts in
# sequence on a billed account (`serviceTier: standard` in every response):
# 5.5s, 6.0s, 6.5s, 6.8s — a tight cluster, and 30s (five times the slowest)
# was chosen from it. Stage 3, the 24-prompt scan with eight in flight, then
# showed the tail the small sample could not: 24 of 24 answered, median
# 7.5s, slowest 24.8s — 3.6x Stage 2's slowest, and only 17% under the 30s
# just chosen. Re-argued from the fuller sample, as the comment above said
# to: 40s is 1.6x the slowest success at n=24, the headroom DEFAULT_TIMEOUT
# keeps, and still well under the shared ceiling. Raised on evidence, not
# lowered on expectation.
GEMINI_TIMEOUT = 40.0
# A billed account — confirmed at Stage 0/1 of Epic 21.1: the free tier does
# not serve this model, and every response carries `serviceTier: standard`.
# Google publishes the live per-minute number only in AI Studio and sends no
# rate-limit headers, so this cap is argued from the documented Tier 1 order
# of magnitude (150–300/min): eight in flight at ~6s a call is ~80 a minute.
# Tested by the 24-prompt scan (Epic 21.1): 24 of 24 answered, no 429. A
# 429 here would be recorded, not retried.
GEMINI_MAX_IN_FLIGHT = 8

# --- Incomplete answers (API key discipline audit, 2026-09-07) ---------------
# An engine reports WHY it stopped, and until this audit the adapters read that
# field for one value — "refusal" — and treated everything else as a finished
# answer. A response cut off by `max_tokens` therefore reached `extract_facts`
# as OK: the brand was absent from the truncated prefix, the row was recorded
# ANSWERED_NO_MENTION, and scoring counted a billed non-answer against the
# mention rate. Nothing anywhere said a cell of the grid was empty.
#
# The vocabularies below are ALLOWLISTS of complete answers, not blocklists of
# incomplete ones. A stop reason this module has never seen defaults to "not
# an answer" (STOP_REASON_UNKNOWN), so an SDK or API upgrade that introduces a
# new value fails visibly as a status rather than silently as a finding.
# `tests/test_engine_stop_reasons.py` reads the pinned SDK's own `StopReason`
# literal and asserts every member is sorted into a bucket, which is what turns
# an SDK bump into a decision rather than a surprise.
#
# Claude, from anthropic 0.125.0's `types/stop_reason.py`:
#   end_turn, max_tokens, stop_sequence, tool_use, pause_turn, refusal,
#   model_context_window_exceeded
# OpenAI, from the Chat Completions `finish_reason` contract (read from
# openai-python's `types/chat/chat_completion.py` on 2026-09-07 — no SDK is
# pinned here, see the module docstring):
#   stop, length, tool_calls, content_filter, function_call
#
# "refusal" and "content_filter" are absent from every set on purpose: both
# adapters map them to PROVIDER_REFUSED before classification runs, and were
# that check ever removed they would land on STOP_REASON_UNKNOWN — still not an
# answer — rather than quietly becoming one.
#
# `max_uses_exceeded` is NOT here and must not be added. It is a
# WebSearchToolResultErrorCode inside a tool-result content block, not a stop
# reason: hitting SEARCH_MAX_USES produces a complete, less-grounded answer
# with `stop_reason == "end_turn"`. That `_extract_citations` skips the error
# block without a log line is a separate, smaller observability gap.
#
# The `tool_use` / `tool_calls` / `function_call` members are unreachable
# today and must stay: `models/engine_result.py` records the trigger for each
# vendor and why that is not the same kind of unreachable as a dead guard.


@dataclass(frozen=True, slots=True)
class StopVocabulary:
    """How one vendor says "I stopped", sorted into what it means for us."""

    complete: frozenset[str]
    truncated: frozenset[str]
    paused: frozenset[str]


CLAUDE_STOPS = StopVocabulary(
    complete=frozenset({"end_turn", "stop_sequence"}),
    truncated=frozenset({"max_tokens", "model_context_window_exceeded"}),
    paused=frozenset({"pause_turn", "tool_use"}),
)
OPENAI_STOPS = StopVocabulary(
    complete=frozenset({"stop"}),
    truncated=frozenset({"length"}),
    paused=frozenset({"tool_calls", "function_call"}),
)
# Perplexity's Agent API reports a run `status` rather than a stop reason
# (api-reference/agent-post, read 2026-09-11): completed, incomplete, failed,
# in_progress, queued, cancelled. `failed` and `cancelled` are handled before
# classification, with the response's own error; the rest sort as below, and
# a status this module has never seen is not an answer.
PERPLEXITY_STOPS = StopVocabulary(
    complete=frozenset({"completed"}),
    truncated=frozenset({"incomplete"}),
    paused=frozenset({"in_progress", "queued"}),
)
# Gemini's `finishReason` (api/generate-content and docs/api-errors, read
# 2026-09-11): STOP, MAX_TOKENS, SAFETY, RECITATION, LANGUAGE, OTHER,
# BLOCKLIST, PROHIBITED_CONTENT, SPII, MALFORMED_FUNCTION_CALL,
# UNEXPECTED_TOOL_CALL, TOO_MANY_TOOL_CALLS, IMAGE_SAFETY. The blocked
# family is a refusal and is mapped to PROVIDER_REFUSED before classification
# (GEMINI_BLOCKED below); the tool-call family is unreachable for the same
# reason OpenAI's is — this adapter sends no tools — and LANGUAGE / OTHER /
# MALFORMED_* fall to STOP_REASON_UNKNOWN on purpose: an answer Gemini could
# not finish for a reason it will not name is not an answer.
GEMINI_STOPS = StopVocabulary(
    complete=frozenset({"STOP"}),
    truncated=frozenset({"MAX_TOKENS"}),
    paused=frozenset(),
)
GEMINI_BLOCKED = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)

# Our own diagnostic codes, shared by both vendors exactly as the error codes
# in `_map_openai_error` are — one vocabulary, read by one scoring path.
ANSWER_TRUNCATED = "ANSWER_TRUNCATED"
ANSWER_PAUSED = "ANSWER_PAUSED"
STOP_REASON_UNKNOWN = "STOP_REASON_UNKNOWN"


def classify_stop(
    reason: str | None, vocabulary: StopVocabulary
) -> tuple[EngineResultStatus, str] | None:
    """Why the engine stopped, as a status — or None for a complete answer.

    Pure, and shared by both adapters so the two vendors cannot drift into
    different codes for the same event. Anything outside the vocabulary,
    including a missing value, is incomplete by default: that is the allowlist
    property argued for above, and it is the whole point.
    """
    if reason in vocabulary.complete:
        return None
    if reason in vocabulary.truncated:
        return EngineResultStatus.TRUNCATED, ANSWER_TRUNCATED
    if reason in vocabulary.paused:
        return EngineResultStatus.PAUSED, ANSWER_PAUSED
    return EngineResultStatus.ERROR, STOP_REASON_UNKNOWN


@dataclass(slots=True)
class CitedSource:
    """A source an engine cited. Facts only — no title, no snippet."""

    url: str
    domain: str
    position: int


@dataclass(slots=True)
class EngineAnswer:
    """One engine's response. **Transient — never persist `text`.**"""

    engine: Engine
    engine_version: str
    prompt_text: str
    text: str = ""
    citations: list[CitedSource] = field(default_factory=list)
    status: EngineResultStatus = EngineResultStatus.OK
    error_code: str | None = None
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status in (EngineResultStatus.OK, EngineResultStatus.ANSWERED_NO_MENTION)

    def digest(self) -> str | None:
        """SHA-256 of the answer text.

        Lets a re-scan detect "the answer changed" without retaining the answer.
        Normalised on whitespace first, so trivial reformatting does not read as
        a substantive change.
        """
        if not self.text:
            return None
        normalised = " ".join(self.text.split())
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    def redacted(self) -> dict[str, object]:
        """Log-safe view — never includes answer text."""
        return {
            "engine": self.engine.value,
            "status": self.status.value,
            "citations": len(self.citations),
            "chars": len(self.text),
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
        }


class EngineAdapter(Protocol):
    """The contract every engine implements."""

    engine: Engine
    version: str
    # The `Settings` attribute holding this vendor's key — Epic 21. An engine
    # whose key is unset is not live; `configured_engines` reads this.
    key_setting: str
    # How many of this engine's calls may be in flight at once, or None for
    # no cap. Set where a vendor limits concurrency; `ask_all` enforces it.
    max_in_flight: int | None
    # The least time between two of this engine's call STARTS, or None. Set
    # where a vendor limits the start rate — a token bucket — which no
    # in-flight cap can express (Epic 21.1). `ask_all` enforces it.
    min_start_interval: float | None

    async def ask(self, prompt: str, *, settings: Settings) -> EngineAnswer: ...


def _map_error(exc: Exception) -> tuple[EngineResultStatus, str]:
    if isinstance(exc, anthropic.RateLimitError):
        return EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED"
    if isinstance(exc, anthropic.AuthenticationError):
        return EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"
    if isinstance(exc, anthropic.BadRequestError):
        if "credit balance" in str(exc).lower():
            return EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"
        return EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"
    # Order matters: `APITimeoutError` SUBCLASSES `APIConnectionError`, so the
    # connection branch below otherwise swallows every timeout and reports it as
    # PROVIDER_UNREACHABLE. That is what Epic 9.1 saw on the 271.6s call, and it
    # is why EngineResultStatus.TIMEOUT was unreachable on this path.
    if isinstance(exc, anthropic.APITimeoutError):
        return EngineResultStatus.TIMEOUT, "TIMEOUT"
    if isinstance(exc, anthropic.APIConnectionError):
        return EngineResultStatus.ERROR, "PROVIDER_UNREACHABLE"
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return EngineResultStatus.TIMEOUT, "TIMEOUT"
    return EngineResultStatus.ERROR, "PROVIDER_ERROR"


class _ClaudeBase:
    """Shared plumbing for both Claude adapters."""

    engine: Engine
    version: str
    uses_search: bool = False
    key_setting = "anthropic_api_key"
    # Anthropic allows 10,000 requests a minute (Epic 18.1): no gate needed.
    max_in_flight: int | None = None
    min_start_interval: float | None = None

    async def ask(self, prompt: str, *, settings: Settings) -> EngineAnswer:
        client = anthropic.AsyncAnthropic(
            api_key=settings.provider_key("anthropic_api_key"),
            timeout=DEFAULT_TIMEOUT,
            max_retries=MAX_RETRIES,
        )
        answer = EngineAnswer(
            engine=self.engine, engine_version=self.version, prompt_text=prompt
        )
        started = time.perf_counter()

        kwargs: dict = {
            "model": ANSWER_MODEL,
            "max_tokens": ANSWER_MAX_TOKENS,
            "output_config": {"effort": ANSWER_EFFORT},
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.uses_search:
            kwargs["tools"] = [
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": SEARCH_MAX_USES,
                }
            ]

        try:
            # The outer deadline is what makes ENGINE_CALL_CEILING a guarantee
            # rather than an arithmetic claim about someone else's internals.
            async with asyncio.timeout(ENGINE_CALL_CEILING):
                response = await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a status, never raised
            answer.status, answer.error_code = _map_error(exc)
            answer.latency_ms = int((time.perf_counter() - started) * 1000)
            return answer

        answer.latency_ms = int((time.perf_counter() - started) * 1000)

        if response.stop_reason == "refusal":
            answer.status = EngineResultStatus.ERROR
            answer.error_code = "PROVIDER_REFUSED"
            return answer

        # An answer the engine did not finish is not an answer. `text` stays
        # empty on purpose: a digest of a truncated prefix would let a re-scan
        # report "the answer changed" about an answer nobody read.
        incomplete = classify_stop(response.stop_reason, CLAUDE_STOPS)
        if incomplete is not None:
            answer.status, answer.error_code = incomplete
            return answer

        answer.text = "".join(b.text for b in response.content if b.type == "text")
        answer.citations = _extract_citations(response)
        return answer


def _extract_citations(response) -> list[CitedSource]:  # noqa: ANN001
    """Pull cited URLs out of a response.

    Reads the structured `web_search_tool_result` blocks rather than parsing
    URLs out of prose: the blocks are already facts, and regexing the answer
    text would pick up whatever the model happened to type.

    **Titles and page snippets in those blocks are deliberately ignored.** They
    are publisher copy (the facts-only rule). Only the URL and its registrable
    domain are kept.
    """
    citations: list[CitedSource] = []
    seen: set[str] = set()

    for block in response.content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        # An error result is a single object, not a list — branch before
        # indexing, or an upstream search failure raises a TypeError here.
        if not isinstance(content, list):
            continue
        for item in content:
            url = getattr(item, "url", None)
            if not url or url in seen:
                continue
            domain = registrable_domain(url)
            if not domain:
                continue
            seen.add(url)
            citations.append(
                CitedSource(url=url[:2048], domain=domain, position=len(citations) + 1)
            )
    return citations


class ClaudeParametricAdapter(_ClaudeBase):
    """Answers from training only. No browsing, no citations."""

    engine = Engine.CLAUDE
    version = f"{ANSWER_MODEL}/parametric"
    uses_search = False


class ClaudeSearchAdapter(_ClaudeBase):
    """Answers with live retrieval. Produces real cited URLs."""

    engine = Engine.CLAUDE_SEARCH
    version = f"{ANSWER_MODEL}/web_search_20260209"
    uses_search = True


def _map_openai_error(exc: Exception) -> tuple[EngineResultStatus, str]:
    """Map an OpenAI failure onto the SAME statuses and codes Claude uses.

    **Mirrors `_map_error`; it does not invent a vocabulary.** `EngineResult`
    rows from different vendors are read by one scoring path and rendered by one
    report, so a rate limit has to look like a rate limit whoever refused. Every
    code below already exists on the Claude path.

    The branch ORDER carries the same lesson Epic 9.2 paid for on the Anthropic
    side: `httpx.TimeoutException` and `httpx.ConnectError` are both
    `httpx.TransportError`, so the timeout branch must come first or every
    timeout is reported as PROVIDER_UNREACHABLE and `TIMEOUT` is unreachable.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED"
        if code in (401, 403):
            return EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"
        if code == 400:
            # OpenAI reports an exhausted balance as a 400 with a typed body,
            # the same way Anthropic reports it in a message string. Read the
            # type rather than the prose, which is vendor copy and may change.
            body = ""
            try:
                body = str(exc.response.json().get("error", {}).get("code", ""))
            except Exception:  # noqa: BLE001 - a non-JSON 400 is just a bad request
                body = ""
            if body in ("insufficient_quota", "billing_hard_limit_reached"):
                return EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"
            return EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"
        if code == 402:
            return EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"
        return EngineResultStatus.ERROR, "PROVIDER_ERROR"
    return _map_transport_error(exc)


def _map_transport_error(exc: Exception) -> tuple[EngineResultStatus, str]:
    """The non-HTTP tail every raw-httpx adapter shares — Epic 21.

    Extracted when the third and fourth httpx adapters arrived, so the
    branch ORDER Epic 9.2 paid for lives in exactly one place: `httpx.
    TimeoutException` subclasses `httpx.TransportError`, and so does
    `ConnectError`, so the timeout branch must be checked first or every
    timeout is reported as PROVIDER_UNREACHABLE and `TIMEOUT` is unreachable.
    The outer `asyncio.timeout` deadline raises the builtin `TimeoutError`,
    which is not an httpx type at all and is checked on its own.
    """
    if isinstance(exc, httpx.TimeoutException):
        return EngineResultStatus.TIMEOUT, "TIMEOUT"
    if isinstance(exc, httpx.TransportError):
        return EngineResultStatus.ERROR, "PROVIDER_UNREACHABLE"
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return EngineResultStatus.TIMEOUT, "TIMEOUT"
    return EngineResultStatus.ERROR, "PROVIDER_ERROR"


def _error_body(exc: httpx.HTTPStatusError) -> dict:
    """The vendor's `error` object, or `{}` — a non-JSON body is not an error
    handler's problem to raise about."""
    try:
        body = exc.response.json()
    except Exception:  # noqa: BLE001 - see above
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _map_perplexity_error(exc: Exception) -> tuple[EngineResultStatus, str]:
    """Perplexity failures onto the SAME statuses and codes — Epic 21.

    Mirrors `_map_openai_error`: nothing here is a new code. The Agent API
    answers a rate limit or an overloaded upstream model with 429 and a
    `Retry-After` (and does not bill it), auth with 401/403, a malformed
    request with 400 or a FastAPI-style 422, and — undocumented but the
    OpenAI shape — an exhausted balance is treated as 402 where it appears.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED"
        if code in (401, 403):
            return EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"
        if code == 402:
            return EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"
        if code in (400, 422):
            return EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"
        return EngineResultStatus.ERROR, "PROVIDER_ERROR"
    return _map_transport_error(exc)


def _map_gemini_error(exc: Exception) -> tuple[EngineResultStatus, str]:
    """Gemini failures onto the SAME statuses and codes — Epic 21.

    Google's two shapes that would otherwise land on the wrong code, read
    from gemini-api/docs/api-errors on 2026-09-11:

    * A 429 is EITHER a per-minute limit (`rate_limit_exceeded`,
      `too_many_requests`, a `QuotaFailure` whose quota is per minute) OR the
      per-day quota (`quota_exceeded`, a quota id containing "PerDay"). The
      first is PROVIDER_RATE_LIMITED and passes; the second is
      PROVIDER_QUOTA_EXHAUSTED and will not clear until tomorrow. The brief
      asked whether either vendor had a genuinely novel quota shape: this is
      it, and it fits the existing code without a new one.
    * A 400 is usually a bad request, but an INVALID API KEY is also a 400
      (`authentication` / `API_KEY_INVALID` / `UNAUTHENTICATED`), and a
      billing precondition (`failed_precondition`) is a 400 too. The body is
      read for those; the rest is PROVIDER_BAD_REQUEST.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        error = _error_body(exc)
        marker = " ".join(
            str(x)
            for x in (
                error.get("code", ""),
                error.get("status", ""),
                error.get("message", ""),
                *(
                    str(d.get("reason", "")) + " " + " ".join(
                        str(v.get("quotaId", "")) for v in (d.get("violations") or [])
                        if isinstance(v, dict)
                    )
                    for d in (error.get("details") or [])
                    if isinstance(d, dict)
                ),
            )
        ).lower()
        if code == 429:
            if "quota_exceeded" in marker or "perday" in marker or "per day" in marker:
                return EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"
            return EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED"
        if code in (401, 403):
            return EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"
        if code == 400:
            if any(k in marker for k in ("api_key_invalid", "authentication", "unauthenticated")):
                return EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"
            if "failed_precondition" in marker:
                return EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"
            return EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"
        if code == 404:
            # `model_not_found`: our request names a model that does not
            # exist. That is a bad request on our side, not a vendor outage.
            return EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"
        return EngineResultStatus.ERROR, "PROVIDER_ERROR"
    return _map_transport_error(exc)


class ChatGptAdapter:
    """OpenAI, answering from training only — the cross-vendor analogue of
    `ClaudeParametricAdapter`.

    No browsing and no tools, so it produces no `Citation` rows. That is not a
    gap: it is the same shape as `claude`, and holding the mode constant is what
    makes a disagreement between the two a statement about the VENDORS.

    Structurally conforms to `EngineAdapter` (engine, version, ask) without
    inheriting it — the Protocol is structural, and `_ClaudeBase` does not
    inherit it either. It does NOT extend `_ClaudeBase`: that class is Anthropic
    plumbing end to end (its client, its kwargs, its refusal field), and sharing
    it would mean a base class with two vendors' branches in it.
    """

    engine = Engine.CHATGPT
    version = f"{OPENAI_ANSWER_MODEL}/parametric"
    key_setting = "openai_api_key"
    # OpenAI allows 500 requests a minute (Epic 18.1): no gate needed.
    max_in_flight: int | None = None
    min_start_interval: float | None = None

    async def ask(self, prompt: str, *, settings: Settings) -> EngineAnswer:
        answer = EngineAnswer(
            engine=self.engine, engine_version=self.version, prompt_text=prompt
        )
        started = time.perf_counter()

        try:
            async with asyncio.timeout(ENGINE_CALL_CEILING):
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.post(
                        f"{OPENAI_BASE_URL}/chat/completions",
                        headers={
                            "Authorization": (
                                f"Bearer {settings.provider_key('openai_api_key')}"
                            ),
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": OPENAI_ANSWER_MODEL,
                            "max_completion_tokens": OPENAI_MAX_TOKENS,
                            "reasoning_effort": OPENAI_REASONING_EFFORT,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
        except Exception as exc:  # noqa: BLE001 - mapped to a status, never raised
            answer.status, answer.error_code = _map_openai_error(exc)
            answer.latency_ms = int((time.perf_counter() - started) * 1000)
            return answer

        answer.latency_ms = int((time.perf_counter() - started) * 1000)

        choices = payload.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        # `content_filter` is OpenAI's refusal signal, and `refusal` is the
        # typed field on the message. Either maps to the SAME PROVIDER_REFUSED
        # code `_ClaudeBase` sets on `stop_reason == "refusal"`.
        if choice.get("finish_reason") == "content_filter" or message.get("refusal"):
            answer.status = EngineResultStatus.ERROR
            answer.error_code = "PROVIDER_REFUSED"
            return answer

        # The same rule as the Claude path, through the same classifier. An
        # empty `choices` list leaves `finish_reason` absent, which lands on
        # STOP_REASON_UNKNOWN: a malformed response is not an answer either.
        # This is the branch that used to let `finish_reason == "length"` with
        # an empty body through as OK, to be recorded as "answered, no
        # mention" one call later.
        incomplete = classify_stop(choice.get("finish_reason"), OPENAI_STOPS)
        if incomplete is not None:
            answer.status, answer.error_code = incomplete
            return answer

        answer.text = message.get("content") or ""
        # No citations by construction: this adapter sends no tools, so there is
        # nothing retrieved to cite. An empty list, never a fabricated one.
        return answer


class PerplexityAdapter:
    """Perplexity, grounded — the second vendor that cites — Epic 21.

    Speaks the Agent API (`POST /v1/agent`) with Perplexity's own `sonar`
    model and an explicit `web_search` tool; see the module docstring for why
    not the sunset chat-completions endpoint and not the `fast` preset.
    `store: false` because the answer text is transient here and should be
    transient there too (the facts-only rule, applied to the vendor).

    Structurally an `EngineAdapter`, extending nothing — as `ChatGptAdapter`
    is, for its reason: `_ClaudeBase` is Anthropic plumbing end to end.
    """

    engine = Engine.PERPLEXITY
    version = f"{PERPLEXITY_ANSWER_MODEL}/web_search"
    key_setting = "perplexity_api_key"
    max_in_flight: int | None = None
    min_start_interval: float | None = PERPLEXITY_MIN_START_INTERVAL

    async def ask(self, prompt: str, *, settings: Settings) -> EngineAnswer:
        answer = EngineAnswer(
            engine=self.engine, engine_version=self.version, prompt_text=prompt
        )
        started = time.perf_counter()

        try:
            async with asyncio.timeout(ENGINE_CALL_CEILING):
                async with httpx.AsyncClient(timeout=PERPLEXITY_TIMEOUT) as client:
                    response = await client.post(
                        f"{PERPLEXITY_BASE_URL}/v1/agent",
                        headers={
                            "Authorization": (
                                f"Bearer {settings.provider_key('perplexity_api_key')}"
                            ),
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": PERPLEXITY_ANSWER_MODEL,
                            "input": prompt,
                            "max_output_tokens": PERPLEXITY_MAX_OUTPUT_TOKENS,
                            "store": False,
                            "tools": [
                                {
                                    "type": "web_search",
                                    "search_context_size": PERPLEXITY_SEARCH_CONTEXT,
                                    "max_results": PERPLEXITY_MAX_RESULTS,
                                }
                            ],
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
        except Exception as exc:  # noqa: BLE001 - mapped to a status, never raised
            answer.status, answer.error_code = _map_perplexity_error(exc)
            answer.latency_ms = int((time.perf_counter() - started) * 1000)
            return answer

        answer.latency_ms = int((time.perf_counter() - started) * 1000)

        status = payload.get("status")
        # A 200 carrying a failed run is the Agent API's documented shape for
        # "the model side broke"; the `error` object says how. It is not an
        # answer and it is not a stop reason, so it is handled here.
        if status in ("failed", "cancelled"):
            answer.status = EngineResultStatus.ERROR
            error = payload.get("error") or {}
            code = str(error.get("code") or error.get("type") or "").lower()
            answer.error_code = (
                "PROVIDER_RATE_LIMITED" if "rate" in code else "PROVIDER_ERROR"
            )
            if answer.error_code == "PROVIDER_RATE_LIMITED":
                answer.status = EngineResultStatus.RATE_LIMITED
            return answer

        incomplete = classify_stop(status, PERPLEXITY_STOPS)
        if incomplete is not None:
            answer.status, answer.error_code = incomplete
            return answer

        answer.text, answer.citations = _read_agent_output(payload.get("output") or [])
        return answer


def _read_agent_output(output: list) -> tuple[str, list[CitedSource]]:
    """The answer and its sources out of an Agent API `output` array.

    Text is every `output_text` part of every `message` item, in order.
    Sources are the message's `url_citation` annotations — what the answer
    actually cited — and, only when it cited nothing inline, the
    `search_results` items it retrieved. Titles and snippets in both are
    publisher copy and are dropped (the facts-only rule); URL and registrable
    domain are kept, first occurrence wins.
    """
    texts: list[str] = []
    cited: list[str] = []
    retrieved: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                texts.append(str(part.get("text") or ""))
                for note in part.get("annotations") or []:
                    if isinstance(note, dict) and note.get("type") == "url_citation":
                        url = note.get("url")
                        if url:
                            cited.append(str(url))
        elif item.get("type") == "search_results":
            for result in item.get("results") or []:
                if isinstance(result, dict) and result.get("url"):
                    retrieved.append(str(result["url"]))
    return "".join(texts), _sources(cited or retrieved)


def _sources(urls: list[str]) -> list[CitedSource]:
    citations: list[CitedSource] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        domain = registrable_domain(url)
        if not domain:
            continue
        seen.add(url)
        citations.append(CitedSource(url=url[:2048], domain=domain, position=len(citations) + 1))
    return citations


class GeminiAdapter:
    """Gemini, answering from training only — the third parametric vendor.

    No `google_search` grounding tool, so no `Citation` rows, by the same
    reasoning `ChatGptAdapter` gives: holding the mode constant is what makes
    a disagreement among `claude`, `chatgpt` and `gemini` a statement about
    the vendors.

    The response is one level deeper than either other vendor's:
    `candidates[0].content.parts[].text`. A blocked PROMPT has no candidates
    and says so in `promptFeedback.blockReason`; a blocked ANSWER has a
    candidate whose `finishReason` is in GEMINI_BLOCKED. Both are refusals.
    """

    engine = Engine.GEMINI
    version = f"{GEMINI_ANSWER_MODEL}/parametric"
    key_setting = "google_ai_api_key"
    max_in_flight: int | None = GEMINI_MAX_IN_FLIGHT
    min_start_interval: float | None = None

    async def ask(self, prompt: str, *, settings: Settings) -> EngineAnswer:
        answer = EngineAnswer(
            engine=self.engine, engine_version=self.version, prompt_text=prompt
        )
        started = time.perf_counter()

        try:
            async with asyncio.timeout(ENGINE_CALL_CEILING):
                async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
                    response = await client.post(
                        f"{GEMINI_BASE_URL}/models/{GEMINI_ANSWER_MODEL}:generateContent",
                        headers={
                            "x-goog-api-key": settings.provider_key("google_ai_api_key"),
                            "Content-Type": "application/json",
                        },
                        json={
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS,
                                "thinkingConfig": {"thinkingLevel": GEMINI_THINKING_LEVEL},
                            },
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
        except Exception as exc:  # noqa: BLE001 - mapped to a status, never raised
            answer.status, answer.error_code = _map_gemini_error(exc)
            answer.latency_ms = int((time.perf_counter() - started) * 1000)
            return answer

        answer.latency_ms = int((time.perf_counter() - started) * 1000)

        feedback = payload.get("promptFeedback") or {}
        candidates = payload.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        finish = candidate.get("finishReason")
        if feedback.get("blockReason") or finish in GEMINI_BLOCKED:
            answer.status = EngineResultStatus.ERROR
            answer.error_code = "PROVIDER_REFUSED"
            return answer

        incomplete = classify_stop(finish, GEMINI_STOPS)
        if incomplete is not None:
            answer.status, answer.error_code = incomplete
            return answer

        parts = (candidate.get("content") or {}).get("parts") or []
        # Thought parts are marked `thought: true` and are never requested
        # here; excluded anyway, so a future `includeThoughts` cannot leak
        # reasoning into the answer digest.
        answer.text = "".join(
            str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought")
        )
        return answer


# The whole registry. Both literals must be edited to add an engine — the list
# is explicit, not derived from the Engine enum, because the enum names every
# engine this product might ever measure while this dict names the ones that
# actually have an adapter behind them. Whether a KEY is behind it is a
# per-deployment fact `configured_engines` reads at run time.
ENGINE_REGISTRY: dict[Engine, EngineAdapter] = {
    Engine.CLAUDE: ClaudeParametricAdapter(),
    Engine.CLAUDE_SEARCH: ClaudeSearchAdapter(),
    Engine.CHATGPT: ChatGptAdapter(),
    Engine.PERPLEXITY: PerplexityAdapter(),
    Engine.GEMINI: GeminiAdapter(),
}

# The five this product measures — Epic 21. Order is the order results are
# gathered and reported in; parametric engines before grounded ones within a
# vendor, vendors in the order they arrived.
DEFAULT_ENGINES: tuple[Engine, ...] = (
    Engine.CLAUDE,
    Engine.CLAUDE_SEARCH,
    Engine.CHATGPT,
    Engine.PERPLEXITY,
    Engine.GEMINI,
)


def configured_engines(settings: Settings | None = None) -> tuple[Engine, ...]:
    """The default engines that actually have a key behind them — Epic 21.

    An engine without a key is not live: every call would fail inside
    `ask()` as PROVIDER_ERROR and be persisted as 24 non-answers a scan,
    dragging coverage down for a vendor nobody had provisioned. So a scan
    that names no engines runs THESE, not `DEFAULT_ENGINES`, and a scan that
    names an unkeyed engine explicitly is refused at the door
    (`routers/scans.py`). The order is DEFAULT_ENGINES' order.

    Read from `Settings` at call time rather than fixed at import, because
    a key is a deployment fact, and the test suite's settings are not the
    developer's `.env`.
    """
    settings = settings or get_settings()
    return tuple(
        e
        for e in DEFAULT_ENGINES
        if getattr(settings, ENGINE_REGISTRY[e].key_setting, None) is not None
    )


# One gate per gated engine, made on first use so it binds to the running
# loop. Bounds THIS ENGINE's calls across every slot of a scan and every
# ad-hoc prompt run in the process; the runner's PROMPT_CONCURRENCY bounds
# slots and knows nothing about vendors. Two shapes, because vendors limit
# two different things (Epic 21.1): a semaphore for how many are in flight,
# and a pacer for how often one may START.
_GATES: dict[Engine, asyncio.Semaphore] = {}
_PACERS: dict[Engine, _Pacer] = {}


class _Pacer:
    """Admits one start per `interval` seconds, in arrival order.

    A lock plus a timestamp, not a token bucket: the vendor's bucket holds one
    token, so the only thing worth modelling is the spacing. Waiting happens
    INSIDE the lock so concurrent arrivals queue rather than all computing the
    same "next allowed" instant and starting together.
    """

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def admit(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self.interval


def _gate(engine: Engine) -> asyncio.Semaphore | None:
    limit = ENGINE_REGISTRY[engine].max_in_flight
    if limit is None:
        return None
    gate = _GATES.get(engine)
    if gate is None:
        gate = _GATES[engine] = asyncio.Semaphore(limit)
    return gate


def _pacer(engine: Engine) -> _Pacer | None:
    interval = ENGINE_REGISTRY[engine].min_start_interval
    if interval is None:
        return None
    pacer = _PACERS.get(engine)
    if pacer is None:
        pacer = _PACERS[engine] = _Pacer(interval)
    return pacer


async def _ask_gated(engine: Engine, prompt: str, settings: Settings) -> EngineAnswer:
    gate = _gate(engine)
    pacer = _pacer(engine)
    if gate is None:
        if pacer is not None:
            await pacer.admit()
        return await ENGINE_REGISTRY[engine].ask(prompt, settings=settings)
    async with gate:
        if pacer is not None:
            await pacer.admit()
        return await ENGINE_REGISTRY[engine].ask(prompt, settings=settings)


async def ask_all(
    prompt: str,
    *,
    engines: tuple[Engine, ...] = DEFAULT_ENGINES,
    settings: Settings | None = None,
) -> list[EngineAnswer]:
    """Ask one prompt of every engine, concurrently — gated per vendor."""
    settings = settings or get_settings()
    return list(
        await asyncio.gather(*(_ask_gated(e, prompt, settings) for e in engines))
    )

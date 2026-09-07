"""Engine adapters — §5.4 step 4, "run each prompt against each AI engine".

=============================================================================
IP-SAFETY BOUNDARY (ip-safety.md #7)
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
    are publisher copy (ip-safety.md #7). Only the URL and its registrable
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
    # Order matters, exactly as on the Claude path: TimeoutException subclasses
    # TransportError, and so does ConnectError.
    if isinstance(exc, httpx.TimeoutException):
        return EngineResultStatus.TIMEOUT, "TIMEOUT"
    if isinstance(exc, httpx.TransportError):
        return EngineResultStatus.ERROR, "PROVIDER_UNREACHABLE"
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return EngineResultStatus.TIMEOUT, "TIMEOUT"
    return EngineResultStatus.ERROR, "PROVIDER_ERROR"


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


# The whole registry. Both literals must be edited to add an engine — the list
# is explicit, not derived from the Engine enum, because the enum names every
# engine this product might ever measure while this dict names the ones that
# actually have an adapter and a key behind them.
ENGINE_REGISTRY: dict[Engine, EngineAdapter] = {
    Engine.CLAUDE: ClaudeParametricAdapter(),
    Engine.CLAUDE_SEARCH: ClaudeSearchAdapter(),
    Engine.CHATGPT: ChatGptAdapter(),
}

DEFAULT_ENGINES: tuple[Engine, ...] = (
    Engine.CLAUDE,
    Engine.CLAUDE_SEARCH,
    Engine.CHATGPT,
)


async def ask_all(
    prompt: str,
    *,
    engines: tuple[Engine, ...] = DEFAULT_ENGINES,
    settings: Settings | None = None,
) -> list[EngineAnswer]:
    """Ask one prompt of every engine, concurrently."""
    settings = settings or get_settings()
    return list(
        await asyncio.gather(
            *(ENGINE_REGISTRY[e].ask(prompt, settings=settings) for e in engines)
        )
    )

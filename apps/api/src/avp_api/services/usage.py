"""What every paid call used, and what a scan used in all — U1, 2026-10-04.

Until this module nothing under `src` read `usage`: every provider tells us how many tokens a
call cost and the answer was thrown away, so what a scan cost was a rate multiplied by a guess
(`scripts/verify_e2e.py` measured two dev scans, 2026-09-11 and 09-12, and nothing since).
Two log lines fix that, and both carry COUNTS ONLY. Never a prompt, an answer, a brand, a
domain, an exception's text, a key, or a dollar figure: turning counts into money is
`scripts/verify_e2e.py --price-usage`, which holds each rate with the date it was read.

`llm.call` — one line for each REQUEST a paid call makes
-------------------------------------------------------
    scan_id         the scan it was made for, or None outside one
    provider        anthropic, openai, perplexity or google
    model           the model the RESPONSE says served it; the one we asked for when the
                    response names none (Gemini's body may not, and an unanswered request
                    has no response at all)
    purpose         engine, sentiment, prompt_generation, fixes, detection, classification
    engine          the engine's id for an engine call, else None
    input_tokens, output_tokens
                    as the provider reports them. Reasoning is INSIDE the output count
                    (Anthropic bills thinking as output, OpenAI counts reasoning in
                    `completion_tokens`, Gemini's `thoughtsTokenCount` is added to its
                    candidate tokens here), so the two are never added together.
    reasoning_tokens        the share of the output that was reasoning, where one is reported
    cache_read_tokens, cache_write_tokens
                    as reported. Anthropic's input count EXCLUDES them and OpenAI's and
                    Google's INCLUDE the cached part; nothing this product sends is cacheable
                    yet, so they are zero today and the pricing mode says so when they are not
    search_invocations      web searches or grounding queries the call made
    retry           this request was a re-send of an earlier one for the same call
    ok              a response came back and its usage was read
    error           why not: `http_<status>` for an error status, else the exception's CLASS
                    NAME. Never its message, which can carry a vendor's text, a host or a key.

A request that got no response logs `ok=False` and zeros. A call that was answered and then
failed as a measurement (a truncated answer, a refusal, a blocked reply) logs the usage it was
billed for: it is `ok`, and its status is the engine result's business.

`scan.usage` — one line when a scan ends
----------------------------------------
Flat `key=value` counts, so a pasted line can be read back (`verify_e2e.py --price-usage`) and a
grep finds one scan. For each of `engines` (every engine together), `engine_<id>` (each engine
that was asked), `sentiment`, `prompt_generation`, `fixes` and `detection`:

    <line>_calls            requests made, retries included
    <line>_unanswered       of those, how many got no usage back
    <line>_retries          of those, how many were a re-send
    <line>_input_tokens, _output_tokens, _reasoning_tokens (inside the output),
    _cache_read_tokens, _cache_write_tokens, _search_invocations
    <line>_model            the model or models that served it, `+`-joined. Only a request that
                            was answered names one: an unanswered request carries the model that
                            was ASKED for, often an alias of what answers (`gpt-5.5` for
                            `gpt-5.5-2026-04-23`), and would make one model look like two.

A counter that is zero is left out, except `_calls`, `_input_tokens` and `_output_tokens`, which
every line carries; so is a `_model` nobody named. READ AN ABSENT COUNTER AS ZERO. (Written out in
full the line of a four-engine scan is four kilobytes; this way it is about one and a half.)
Then SerpApi, four counts that are always present: `serpapi_attempted` (HTTP requests, retries
included), `serpapi_ok` (answered; billed unless SerpApi served it from its cache),
`serpapi_error` (answered with an error; not billed) and `serpapi_no_answer` (nobody can say).
And `serpapi_billed_or_unknown`, which is `serpapi_ok` + `serpapi_no_answer`: an UPPER BOUND on
what SerpApi billed, since an answered search is billed unless SerpApi served it from its cache,
an error answer is not billed, and a search nobody answered may have been. It is left out when it
is zero, as every counter above that is. Classification happens at intake and is not part of a
scan, so it has its own `llm.call` lines and is never in this one.

The total is built from the same records the per-call lines are, so the two cannot disagree.

How a call is counted
---------------------
`track(...)` wraps ONE paid call; the call site reads the response into it and goes on exactly
as before. The scan chain runs inside `scan_scope(scan_id)`, which makes an accumulator the
current one in a context variable: tasks started inside the scope (`asyncio.gather`,
`create_task`) copy the context and share the accumulator by reference, so a call made anywhere
in the chain adds to the scan, and a call made outside one (intake's classification, the detect
route, the fixes route, a prompt run) only logs.

RETRIES THE SDK MAKES ITSELF. `messages.create` and `messages.parse` retry a 408, 409, 429, 5xx
or a dropped connection inside the one call and tell the caller nothing, so a retried call
would read as one request, and a first attempt that timed out and may have been billed would be
invisible. The pinned SDK's client-level middleware runs once per HTTP attempt, inside that retry
loop (`anthropic/_middleware.py`), and `count_attempts` is the one that reports each attempt to
the call being tracked. It is passed only to a client whose SDK takes one
(`sdk_client_options`). Perplexity's own retry after a 429 is reported by the adapter.

On the pinned SDK a client with middleware also sends the SDK's raw-response header
(`X-Stainless-Raw-Response: raw`, the header `.with_raw_response` sends too; it names a mode and
carries no secret) and takes the SDK's middleware code path. Retries, timeouts, idempotency and
response parsing are unchanged: `tests/test_llm_call_lines.py` holds that against the SDK.

WHAT THIS CANNOT SEE, stated so nobody has to find it out
---------------------------------------------------------
* `messages.parse` validates the answer inside the SDK. When it does not fit the schema the
  `ValidationError` carries neither the response nor its usage, and the provider billed the
  call. The line says `ok=False error=ValidationError` with zero counts: a run of those in a
  scan's `unanswered` is the sign.
* An attempt that timed out before any response may have been billed (`call_bounds.py` says
  so). It is a line with `ok=False`, and its cost is not known.
* Counting is best effort by construction: a reader that raises, a logger that fails or a
  usage block of an unexpected shape leaves the call's own outcome exactly as it was.

Apart from that one header, nothing here changes what a call sends, how long it may take, how
often it is retried or what it returns.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import TracebackType
from typing import Any, Literal

import anthropic
import structlog

logger = structlog.get_logger(__name__)

ANTHROPIC = "anthropic"
OPENAI = "openai"
PERPLEXITY = "perplexity"
GOOGLE = "google"


class Purpose(StrEnum):
    """What a paid call was for. Every one is a line of its own in a scan's total, except
    classification, which is not part of a scan."""

    ENGINE = "engine"
    SENTIMENT = "sentiment"
    PROMPT_GENERATION = "prompt_generation"
    FIXES = "fixes"
    DETECTION = "detection"
    CLASSIFICATION = "classification"


# The lines of a scan's total that are always present, in the order a reader meets them. The
# engines come as `engines` (all together) and as `engine_<id>` for each that was asked.
FIXED_LINES: tuple[str, ...] = (
    "engines", Purpose.SENTIMENT, Purpose.PROMPT_GENERATION, Purpose.FIXES, Purpose.DETECTION,
)
COUNTERS: tuple[str, ...] = (
    "calls", "unanswered", "retries", "input_tokens", "output_tokens", "reasoning_tokens",
    "cache_read_tokens", "cache_write_tokens", "search_invocations",
)
# The counters a line of the total always carries, zero or not; the others are left out when zero.
ALWAYS_PRESENT: frozenset[str] = frozenset({"calls", "input_tokens", "output_tokens"})


# ---------------------------------------------------------------------------
# reading a provider's usage block
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Counts:
    """What one response reported, in the units a line carries."""

    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    search_invocations: int = 0


def _count(value: object) -> int:
    """A token count: a whole number of at least zero, and zero for anything else.

    A bool is not a count (`True` is an `int` to Python), and neither is a negative, a NaN or a
    string: a provider that changes its block's shape costs a line its numbers, never a call.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float) and math.isfinite(value):
        return max(int(value), 0)
    return 0


def _dig(node: object, *path: str) -> object:
    """`node.a.b` or `node["a"]["b"]`, whichever it is, and None at the first thing missing."""
    for key in path:
        if node is None:
            return None
        node = node.get(key) if isinstance(node, dict) else getattr(node, key, None)
    return node


# What a model's id looks like: letters, digits and `. : / _ -`, up to a hundred of them. Anything
# else in a response's `model` (a space, a newline, a `+`, a paragraph) is not a model name, and a
# line must never be a place a provider's text can land. `+` is left out on purpose: it joins the
# models of a scan's line, so a name may not contain one.
_MODEL_ID = re.compile(r"[A-Za-z0-9._:/-]{1,100}")


def _model(value: object) -> str | None:
    return value if isinstance(value, str) and _MODEL_ID.fullmatch(value) else None


def counts_from_anthropic(message: object) -> Counts:
    """From a Messages response, typed or not: `usage` and `model`.

    `usage.input_tokens` and `.output_tokens`; `.output_tokens_details.thinking_tokens` (the
    reasoning inside the output); `.cache_read_input_tokens` and `.cache_creation_input_tokens`;
    `.server_tool_use.web_search_requests`. A web FETCH is not counted: this product never asks
    for one. All attested by the pinned SDK's own types (`anthropic/types/usage.py`).
    """
    usage = _dig(message, "usage")
    return Counts(
        model=_model(_dig(message, "model")),
        input_tokens=_count(_dig(usage, "input_tokens")),
        output_tokens=_count(_dig(usage, "output_tokens")),
        reasoning_tokens=_count(_dig(usage, "output_tokens_details", "thinking_tokens")),
        cache_read_tokens=_count(_dig(usage, "cache_read_input_tokens")),
        cache_write_tokens=_count(_dig(usage, "cache_creation_input_tokens")),
        search_invocations=_count(_dig(usage, "server_tool_use", "web_search_requests")),
    )


def counts_from_openai(payload: object) -> Counts:
    """From a Chat Completions body: `model`, and `usage.prompt_tokens`, `.completion_tokens`
    (which already includes the reasoning), `.completion_tokens_details.reasoning_tokens` and
    `.prompt_tokens_details.cached_tokens`. The adapter sends no tool, and Chat Completions
    reports no invocation count, so there is no search to read."""
    usage = _dig(payload, "usage")
    return Counts(
        model=_model(_dig(payload, "model")),
        input_tokens=_count(_dig(usage, "prompt_tokens")),
        output_tokens=_count(_dig(usage, "completion_tokens")),
        reasoning_tokens=_count(_dig(usage, "completion_tokens_details", "reasoning_tokens")),
        cache_read_tokens=_count(_dig(usage, "prompt_tokens_details", "cached_tokens")),
    )


def counts_from_perplexity(payload: object) -> Counts:
    """From an Agent API body: `model`, and `usage.input_tokens`, `.output_tokens`,
    `.input_tokens_details.cache_read_input_tokens` and
    `.tool_calls_details.web_search.invocation`: the fields `verify_e2e.py`'s meter has read
    off the real API since Epic 21. The vendor also reports a figure of its own for the call
    (`usage.cost`); it is a dollar amount and this module never reads it."""
    usage = _dig(payload, "usage")
    return Counts(
        model=_model(_dig(payload, "model")),
        input_tokens=_count(_dig(usage, "input_tokens")),
        output_tokens=_count(_dig(usage, "output_tokens")),
        cache_read_tokens=_count(_dig(usage, "input_tokens_details", "cache_read_input_tokens")),
        search_invocations=_count(_dig(usage, "tool_calls_details", "web_search", "invocation")),
    )


def counts_from_gemini(payload: object) -> Counts:
    """From a `generateContent` body: `usageMetadata.promptTokenCount`, `.candidatesTokenCount`
    PLUS `.thoughtsTokenCount` (Gemini reports thoughts beside the answer and bills them as
    output), `.cachedContentTokenCount`; the model from `modelVersion` when the body carries
    one; and the number of grounding queries, `candidates[0].groundingMetadata.
    webSearchQueries`. The queries themselves are never read into anything."""
    usage = _dig(payload, "usageMetadata")
    thoughts = _count(_dig(usage, "thoughtsTokenCount"))
    candidates = _dig(payload, "candidates")
    first = candidates[0] if isinstance(candidates, list) and candidates else None
    queries = _dig(first, "groundingMetadata", "webSearchQueries")
    return Counts(
        model=_model(_dig(payload, "modelVersion")),
        input_tokens=_count(_dig(usage, "promptTokenCount")),
        output_tokens=_count(_dig(usage, "candidatesTokenCount")) + thoughts,
        reasoning_tokens=thoughts,
        cache_read_tokens=_count(_dig(usage, "cachedContentTokenCount")),
        search_invocations=len(queries) if isinstance(queries, list) else 0,
    )


def error_label(exc: BaseException) -> str:
    """Why a request failed, as one short word: `http_<status>` or the exception's class name.

    Never `str(exc)`: an SDK's or httpx's message can carry a vendor's text, a host or a key.
    Anything with a response carrying a status (an httpx `HTTPStatusError`, the SDK's
    `APIStatusError`) is named by that status, which is what a reader wants to know.
    """
    status = _dig(exc, "response", "status_code")
    if isinstance(status, int) and not isinstance(status, bool):
        return f"http_{status}"
    return type(exc).__name__


# ---------------------------------------------------------------------------
# one request, as a line
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallUsage:
    """One request to a paid provider, as the `llm.call` line carries it."""

    provider: str
    model: str | None
    purpose: Purpose
    engine: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    search_invocations: int = 0
    retry: bool = False
    ok: bool = True
    error: str | None = None
    scan_id: str | None = None

    def log_fields(self) -> dict[str, object]:
        """Exactly the fields the line carries, and nothing else."""
        return {
            "scan_id": self.scan_id,
            "provider": self.provider,
            "model": self.model,
            "purpose": self.purpose.value,
            "engine": self.engine,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "search_invocations": self.search_invocations,
            "retry": self.retry,
            "ok": self.ok,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# a scan's total
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Totals:
    calls: int = 0
    unanswered: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    search_invocations: int = 0
    models: set[str] = field(default_factory=set)

    def add(self, call: CallUsage) -> None:
        self.calls += 1
        self.unanswered += 0 if call.ok else 1
        self.retries += 1 if call.retry else 0
        self.input_tokens += call.input_tokens
        self.output_tokens += call.output_tokens
        self.reasoning_tokens += call.reasoning_tokens
        self.cache_read_tokens += call.cache_read_tokens
        self.cache_write_tokens += call.cache_write_tokens
        self.search_invocations += call.search_invocations
        # Only a request that was answered says which model served it. One that was not carries
        # the model we ASKED for, which is often an alias of what the provider reports back
        # (`gpt-5.5` for `gpt-5.5-2026-04-23`), and would make a line look served by two.
        if call.ok and call.model:
            self.models.add(call.model)

    def fields(self, prefix: str) -> dict[str, object]:
        """This line's fields, with the zeros that say nothing left out: a counter that is zero
        is absent (read it as zero) unless it is one every line has, and so is a model nobody
        named. It keeps a scan's one line to about two kilobytes instead of four."""
        out: dict[str, object] = {}
        for name in COUNTERS:
            value = getattr(self, name)
            if value or name in ALWAYS_PRESENT:
                out[f"{prefix}_{name}"] = value
        model = "+".join(sorted(self.models))
        if model:
            out[f"{prefix}_model"] = model
        return out


class ScanUsage:
    """Everything a scan's chain has paid for so far.

    One per scan, shared by reference by every task the chain starts. Single event loop and no
    threads, so nothing here is locked: an addition is a handful of integer increments that
    cannot be interleaved.
    """

    def __init__(self, scan_id: str) -> None:
        self.scan_id = scan_id
        self._lines: dict[str, _Totals] = {str(name): _Totals() for name in FIXED_LINES}
        self._engines: dict[str, _Totals] = {}
        self._serp_attempted = 0
        self._serp_ok = 0
        self._serp_error = 0
        self._serp_no_answer = 0

    def add(self, call: CallUsage) -> None:
        """Add one request. Classification is not part of a scan and is ignored."""
        if call.purpose is Purpose.CLASSIFICATION:
            return
        if call.purpose is Purpose.ENGINE:
            self._lines["engines"].add(call)
            self._engines.setdefault(call.engine or "unknown", _Totals()).add(call)
            return
        self._lines[call.purpose.value].add(call)

    def add_serp(
        self, *, attempts: int, no_answer: int, answer: Literal["ok", "error"] | None
    ) -> None:
        """Add one SerpApi query: its requests, how many of them got no answer, and what the
        one that did answer said (`None` when none did)."""
        self._serp_attempted += attempts
        self._serp_no_answer += no_answer
        if answer == "ok":
            self._serp_ok += 1
        elif answer == "error":
            self._serp_error += 1

    def log_fields(self) -> dict[str, object]:
        """The `scan.usage` line: flat, so it is one line a reader can take apart again."""
        out: dict[str, object] = {"scan_id": self.scan_id}
        for name, totals in self._lines.items():
            out.update(totals.fields(name))
        for engine, totals in self._engines.items():
            out.update(totals.fields(f"engine_{engine}"))
        out["serpapi_attempted"] = self._serp_attempted
        out["serpapi_ok"] = self._serp_ok
        out["serpapi_error"] = self._serp_error
        out["serpapi_no_answer"] = self._serp_no_answer
        # What SerpApi may have billed, at most: the answered searches (billed unless SerpApi
        # served them from its cache) and the ones nobody answered (which may have been). An error
        # answer is not billed. Left out when zero, like every counter that is.
        billed_or_unknown = self._serp_ok + self._serp_no_answer
        if billed_or_unknown:
            out["serpapi_billed_or_unknown"] = billed_or_unknown
        return out


# The scan whose chain the current task is running in, and the call the current task is making.
# Both are read by code that was never handed them: the first by every paid call anywhere in the
# chain, the second by the SDK's per-attempt middleware.
_SCAN: ContextVar[ScanUsage | None] = ContextVar("avp_scan_usage", default=None)
_CURRENT: ContextVar[CallTracker | None] = ContextVar("avp_current_call", default=None)


def _leave_scan(token: Token[ScanUsage | None]) -> None:
    """Put the current scan back as it was. A token from another context cannot be used (a scope
    finished in a different task than it began in); the variable is cleared instead, which is
    what leaving was for."""
    try:
        _SCAN.reset(token)
    except ValueError:
        _SCAN.set(None)


def _leave_call(token: Token[CallTracker | None]) -> None:
    """The same for the call being tracked."""
    try:
        _CURRENT.reset(token)
    except ValueError:
        _CURRENT.set(None)


@contextmanager
def scan_scope(scan_id: str) -> Iterator[ScanUsage]:
    """The scope a scan's chain runs in; one `scan.usage` line when it ends, however it ends.

    A scan that finishes, ends PARTIAL or FAILED, raises, is cancelled or runs out of time all
    leave this scope, so all of them log. The scope never swallows what is raised through it.
    """
    scan = ScanUsage(scan_id)
    token = _SCAN.set(scan)
    try:
        yield scan
    finally:
        _leave_scan(token)
        # A line about a scan must never break it, or hide what was raised through this scope.
        with suppress(Exception):
            logger.info("scan.usage", **scan.log_fields())


def record_serp_search(
    *, attempts: int, no_answer: int, answer: Literal["ok", "error"] | None
) -> None:
    """One SerpApi query's requests into the current scan, if there is one.

    Never raises: it runs inside `serp.search`, whose answer is not usage's to change, and counting
    follows the one rule the middleware and the tracker follow.
    """
    with suppress(Exception):
        scan = _SCAN.get()
        if scan is not None:
            scan.add_serp(attempts=attempts, no_answer=no_answer, answer=answer)


# ---------------------------------------------------------------------------
# one call
# ---------------------------------------------------------------------------


class CallTracker:
    """One paid call: how many requests it made, which failed, and what the answering one used.

    Used as a context manager around the call. Leaving it logs one `llm.call` line for each
    request and adds each to the current scan. It never raises and never swallows: the call's
    own exception goes on as it was.
    """

    def __init__(self, provider: str, purpose: Purpose, model: str, engine: str | None) -> None:
        self._provider = provider
        self._purpose = purpose
        self._asked_model = model
        self._engine = engine
        self._attempts = 0
        self._failed: list[str] = []
        self._counts: Counts | None = None
        self._token: Token[CallTracker | None] | None = None

    # -- what the call site reports -------------------------------------------------

    def begin_attempt(self) -> None:
        """A request is about to be sent. Reported by whoever retries: the SDK's middleware for
        an Anthropic call, the adapter for Perplexity's. A call that never says is one request."""
        self._attempts += 1

    def attempt_failed(self, label: str) -> None:
        """The request that was begun got no usable answer, for this reason (`error_label`)."""
        self._failed.append(label)

    def read_anthropic(self, message: object) -> None:
        self._read(counts_from_anthropic, message)

    def read_openai(self, payload: object) -> None:
        self._read(counts_from_openai, payload)

    def read_perplexity(self, payload: object) -> None:
        self._read(counts_from_perplexity, payload)

    def read_gemini(self, payload: object) -> None:
        self._read(counts_from_gemini, payload)

    def _read(self, reader: Callable[[object], Counts], body: object) -> None:
        # A reader that raises leaves the call unanswered and unharmed.
        try:
            self._counts = reader(body)
        except Exception:  # noqa: BLE001 - see above
            self._counts = None

    # -- the scope ------------------------------------------------------------------

    def __enter__(self) -> CallTracker:
        self._token = _CURRENT.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _leave_call(self._token)
        scan = _SCAN.get()
        try:
            calls = self._calls(exc, scan.scan_id if scan is not None else None)
        except Exception:  # noqa: BLE001 - counting never decides how a call ends
            return
        for call in calls:
            with suppress(Exception):  # nor does the log
                logger.info("llm.call", **call.log_fields())
            if scan is not None:
                with suppress(Exception):  # nor does the accumulator
                    scan.add(call)

    def _calls(self, exc: BaseException | None, scan_id: str | None) -> list[CallUsage]:
        """A line for each request: the ones that failed first, then the one that ended the call."""
        attempts = max(self._attempts, 1)

        def failed(index: int) -> CallUsage:
            return CallUsage(
                provider=self._provider, model=self._asked_model, purpose=self._purpose,
                engine=self._engine, retry=index > 0, ok=False,
                error=self._failed[index] if index < len(self._failed) else None,
                scan_id=scan_id,
            )

        calls = [failed(index) for index in range(attempts - 1)]
        counts = self._counts
        if counts is None:
            # The last request got no usable answer. If whoever reported its attempts also
            # reported why, that is the reason; otherwise it is whatever the call raised.
            last = failed(attempts - 1)
            if last.error is None and exc is not None:
                last = replace(last, error=error_label(exc))
            calls.append(last)
            return calls
        calls.append(CallUsage(
            provider=self._provider, model=counts.model or self._asked_model,
            purpose=self._purpose, engine=self._engine,
            input_tokens=counts.input_tokens, output_tokens=counts.output_tokens,
            reasoning_tokens=counts.reasoning_tokens, cache_read_tokens=counts.cache_read_tokens,
            cache_write_tokens=counts.cache_write_tokens,
            search_invocations=counts.search_invocations,
            retry=attempts > 1, ok=True,
            error=error_label(exc) if exc is not None else None,
            scan_id=scan_id,
        ))
        return calls


def track(provider: str, purpose: Purpose, *, model: str, engine: str | None = None) -> CallTracker:
    """Track one paid call: `with track(ANTHROPIC, Purpose.SENTIMENT, model=MODEL) as call:`.

    `model` is the one the call asks for: the line names it only when the response does not
    say which model served the request, or when no response came.
    """
    return CallTracker(provider, purpose, model, engine)


# ---------------------------------------------------------------------------
# the Anthropic SDK's own retries
# ---------------------------------------------------------------------------


async def count_attempts(
    request: object, call_next: Callable[[object], Awaitable[object]]
) -> object:
    """SDK middleware: tell the call being tracked about each HTTP attempt it makes.

    It runs once per attempt, inside the SDK's retry loop, and passes the request and the response
    on as it found them. A call that is not being tracked passes straight through. Bookkeeping
    that fails is dropped on the floor: this sits on the path of every paid Anthropic call. A
    cancellation (the call's own deadline, a lease lost) passes through untouched, and the
    tracker logs it as the call's last request.

    On the pinned SDK a client with middleware also sends the SDK's raw-response header
    (`X-Stainless-Raw-Response: raw`, the header `.with_raw_response` sends too; it names a mode
    and carries no secret) and takes the SDK's middleware code path. Retries, timeouts,
    idempotency and response parsing are unchanged.
    """
    call = _CURRENT.get()
    if call is None:
        return await call_next(request)
    with suppress(Exception):
        call.begin_attempt()
    try:
        response = await call_next(request)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        with suppress(Exception):
            call.attempt_failed(error_label(exc))
        raise
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool) and status >= 400:
        with suppress(Exception):
            call.attempt_failed(f"http_{status}")
    return response


def _sdk_takes_middleware() -> bool:
    try:
        return "middleware" in inspect.signature(anthropic.AsyncAnthropic.__init__).parameters
    except (TypeError, ValueError):
        return False


_SDK_TAKES_MIDDLEWARE = _sdk_takes_middleware()


def sdk_client_options() -> dict[str, Any]:
    """Keyword arguments for `anthropic.AsyncAnthropic` that report its attempts, if it can.

    `pyproject.toml` allows `anthropic>=0.40,<1` and only recent releases have middleware, so an
    SDK without it gets none and its retries go uncounted rather than its client failing to
    build. The lockfile pins one that has it. On it, a client with middleware also sends the
    SDK's raw-response header and takes the SDK's middleware code path (see `count_attempts`).
    """
    return {"middleware": [count_attempts]} if _SDK_TAKES_MIDDLEWARE else {}

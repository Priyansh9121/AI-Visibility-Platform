"""Every paid call logs what it used, and only what it used — U1, 2026-10-04.

Production threw away the usage every provider sends back: no file under `src` read
`usage`, so what a scan cost was a guess multiplied by a rate. `llm.call` is the line that
fixes it: one for each request a paid call makes, with the provider, the model the response
says served it, the purpose, the engine, the token counts (reasoning included in the output
count, and also shown on its own), the cache counts, the search or grounding invocations,
whether it was a retry of an earlier attempt, and whether any usage came back.

What is pinned here, each by a test that fails without it:

* for every provider's own response shape, a fake response with KNOWN usage gives a line
  carrying exactly those counts (Anthropic with thinking, cache and web search; OpenAI with
  reasoning tokens; Perplexity with its search invocations; Gemini with thoughts and
  grounding queries), and the model it names is the one the response says, not the one that
  was asked for;
* a call that failed after the provider answered (a truncated answer, a refusal) still logs
  the usage the provider reported, and one that got no response logs `ok=False` and zeros;
* a retry is marked, for Perplexity's own and for the Anthropic SDK's, which retries inside
  one `messages.create` and tells the caller nothing;
* the line carries exactly its fields, and no prompt, answer, brand, domain, exception text,
  key or vendor-reported dollar figure is in any of them.

Nothing here reaches a provider. The Anthropic client under test is the real one with only
its HTTP transport faked, as in `test_call_bounds.py`, and the httpx engines are faked at
their transport as in `test_openai_engine.py`: so the response shapes are what each SDK and
adapter genuinely parse.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import anthropic
import httpx
import pytest
import structlog

from avp_api.config import Settings
from avp_api.models.engine_result import Engine, EngineResultStatus
from avp_api.services import (
    classify,
    cocitation,
    engines,
    extraction,
    fix_generator,
    prompts,
    usage,
)
from avp_api.services.call_bounds import CallBound
from avp_api.services.crawl import CrawlResult, CrawlSignals
from avp_api.services.engines import (
    ChatGptAdapter,
    ClaudeParametricAdapter,
    ClaudeSearchAdapter,
    EngineAnswer,
    GeminiAdapter,
    PerplexityAdapter,
)

# conftest's autouse `stub_chain_externals` replaces `AsyncMessages.parse` for fix sets with a
# canned response, one level ABOVE the transport these tests fake. Opted out so the fixes
# site reaches the transport like the rest.
pytestmark = pytest.mark.real_parse

# What a prompt, an answer, a brand, a domain, an exception's message, a vendor's own dollar
# figure and a grounding query look like when each is recognisable, so that its absence from a
# log line can be asserted.
PROMPT_SENTINEL = "PROMPTSENTINEL-1f3a"
ANSWER_SENTINEL = "ANSWERSENTINEL-9c2e"
BRAND_SENTINEL = "BRANDSENTINEL-44d0"
ERROR_SENTINEL = "ERRORSENTINEL-7b61"
GROUNDING_SENTINEL = "GROUNDINGSENTINEL-5e18"
KEY_SENTINEL = "KEYSENTINEL-a93f"
VENDOR_COST = 0.0123

# Exactly the fields of the line, and nothing else; structlog adds `event` and `log_level`.
LINE_FIELDS = {
    "scan_id", "provider", "model", "purpose", "engine",
    "input_tokens", "output_tokens", "reasoning_tokens",
    "cache_read_tokens", "cache_write_tokens", "search_invocations",
    "retry", "ok", "error",
}


@pytest.fixture
def keyed() -> Settings:
    """A key for every provider, recognisable so that its absence from a line can be asserted."""
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        anthropic_api_key=f"{KEY_SENTINEL}-anthropic",
        openai_api_key=f"{KEY_SENTINEL}-openai",
        perplexity_api_key=f"{KEY_SENTINEL}-perplexity",
        google_ai_api_key=f"{KEY_SENTINEL}-google",
    )


@pytest.fixture(autouse=True)
def _no_pacing_and_fresh_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Perplexity's start pacer and Gemini's gate are per process; keep each test its own."""
    monkeypatch.setattr(engines, "_GATES", {})
    monkeypatch.setattr(engines, "_PACERS", {})
    # On the CLASS, which monkeypatch restores exactly. On the registered instance it would put
    # the class's own value back as an instance attribute, which shadows the class from then on
    # and silently defeats every later test that sets it there (the Perplexity gate test does).
    monkeypatch.setattr(engines.PerplexityAdapter, "min_start_interval", None)


# ---------------------------------------------------------------------------
# the fakes
# ---------------------------------------------------------------------------

_REAL_HTTPX_CLIENT = httpx.AsyncClient


def routed_client(handler: Callable[[httpx.Request], httpx.Response]) -> type[httpx.AsyncClient]:
    """A stand-in for `httpx.AsyncClient` that talks to `handler` unless it is handed a transport.

    A SUBCLASS and not a function, because the Anthropic SDK checks `isinstance(http_client,
    httpx.AsyncClient)` against whatever that name is at the time, and a test that fakes both
    the SDK's transport and the engines' would otherwise break the SDK's own check.
    """

    class Routed(_REAL_HTTPX_CLIENT):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx.MockTransport(handler))
            super().__init__(*args, **kwargs)

    return Routed


def install_anthropic(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[dict[str, Any]]:
    """Point every Anthropic client at `handler`; returns the request bodies it was sent."""
    sent: list[dict[str, Any]] = []
    real_cls = anthropic.AsyncAnthropic

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return handler(request)

    def factory(**kwargs: Any) -> anthropic.AsyncAnthropic:
        # `httpx.AsyncClient` is looked up now: it is the stand-in when a test has installed one.
        return real_cls(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(recording)),
        )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", factory)
    return sent


def install_httpx(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    """Point every httpx client the engines open at `handler`; returns the requests."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(httpx, "AsyncClient", routed_client(recording))
    return seen


def message(
    content: list[dict[str, Any]] | None = None,
    *,
    model: str = "claude-opus-5",
    stop: str = "end_turn",
    **usage: Any,
) -> dict[str, Any]:
    """An Anthropic Messages response, with the usage the caller asks for."""
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": content if content is not None else [{"type": "text", "text": ANSWER_SENTINEL}],
        "stop_reason": stop, "stop_sequence": None,
        "usage": usage or {"input_tokens": 1, "output_tokens": 1},
    }


def structured(model_json: dict[str, Any], *, model: str, **usage: Any) -> dict[str, Any]:
    """An Anthropic response whose one text block is the JSON `messages.parse` validates."""
    return message([{"type": "text", "text": json.dumps(model_json)}], model=model, **usage)


def lines(events: list[dict[str, Any]], name: str = "llm.call") -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == name]


def facts(line: dict[str, Any]) -> dict[str, Any]:
    """The line without structlog's own keys."""
    return {k: v for k, v in line.items() if k not in ("event", "log_level")}


def expected(**overrides: Any) -> dict[str, Any]:
    """A line with every field at its default, for `overrides` to change."""
    base: dict[str, Any] = {
        "scan_id": None, "provider": None, "model": None, "purpose": None, "engine": None,
        "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0, "search_invocations": 0,
        "retry": False, "ok": True, "error": None,
    }
    assert set(overrides) <= LINE_FIELDS, set(overrides) - LINE_FIELDS
    return {**base, **overrides}


def assert_counts_only(events: list[dict[str, Any]], *forbidden: str) -> None:
    """Nothing but counts in either of the two lines this change adds.

    Every other line a run logs is somebody else's (a debug line of the classifier's holds its
    rationale, which is why production runs at INFO); these two must hold none of the
    sentinels, in any value or key.
    """
    ours = [e for e in events if e["event"] in ("llm.call", "scan.usage")]
    blob = json.dumps(ours, default=str)
    for sentinel in (
        PROMPT_SENTINEL, ANSWER_SENTINEL, BRAND_SENTINEL, ERROR_SENTINEL, GROUNDING_SENTINEL,
        KEY_SENTINEL, str(VENDOR_COST), *forbidden,
    ):
        assert sentinel not in blob, f"{sentinel} reached the log: {blob[:400]}"
    for line in lines(events):
        assert set(facts(line)) == LINE_FIELDS, set(facts(line)) ^ LINE_FIELDS
        assert line["log_level"] == "info"
        for key, value in facts(line).items():
            assert isinstance(value, str | int | bool | None), (key, value)
        for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens",
                    "cache_write_tokens", "search_invocations"):
            assert type(line[key]) is int and line[key] >= 0, (key, line[key])
        assert "cost" not in "".join(line), "no dollar figure belongs in a usage line"


# ---------------------------------------------------------------------------
# Anthropic: the two Claude engines
# ---------------------------------------------------------------------------


class TestAnthropicEngines:
    async def test_the_parametric_engine_logs_its_counts_with_thinking_and_cache(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=message(
            model="claude-opus-5-20261001",
            input_tokens=1200, output_tokens=3400,
            cache_read_input_tokens=50, cache_creation_input_tokens=70,
            output_tokens_details={"thinking_tokens": 900},
        )))

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.OK
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic",
            # What the response says served it, not `claude-opus-5`, which was asked for.
            model="claude-opus-5-20261001",
            purpose="engine", engine="claude",
            input_tokens=1200,
            # Thinking is billed as output, so it is INSIDE the 3,400, and shown on its own.
            output_tokens=3400, reasoning_tokens=900,
            cache_read_tokens=50, cache_write_tokens=70,
        )
        assert_counts_only(events)

    async def test_the_grounded_engine_logs_its_web_searches(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=message(
            [
                {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                 "input": {"query": f"{GROUNDING_SENTINEL} help desk"}},
                {"type": "text", "text": f"{ANSWER_SENTINEL} Zendesk."},
            ],
            input_tokens=21_000, output_tokens=800,
            server_tool_use={"web_search_requests": 3, "web_fetch_requests": 0},
        )))

        with structlog.testing.capture_logs() as events:
            await ClaudeSearchAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-opus-5", purpose="engine", engine="claude_search",
            input_tokens=21_000, output_tokens=800, search_invocations=3,
        )
        assert_counts_only(events)

    async def test_a_truncated_answer_still_logs_the_usage_the_provider_reported(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The call failed as a measurement (TRUNCATED) and was billed all the same."""
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=message(
            stop="max_tokens", input_tokens=30, output_tokens=4000,
            output_tokens_details={"thinking_tokens": 4000},
        )))

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.TRUNCATED
        (line,) = lines(events)
        assert (line["ok"], line["error"]) == (True, None)
        assert (line["input_tokens"], line["output_tokens"], line["reasoning_tokens"]) == (
            30, 4000, 4000
        )

    async def test_a_refusal_still_logs_the_usage_the_provider_reported(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=message(
            stop="refusal", input_tokens=40, output_tokens=2)))

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.error_code == "PROVIDER_REFUSED"
        (line,) = lines(events)
        assert (line["ok"], line["input_tokens"], line["output_tokens"]) == (True, 40, 2)

    async def test_a_response_with_no_usage_logs_zeros_and_the_model_that_was_asked_for(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = message()
        del body["usage"], body["model"]
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=body))

        with structlog.testing.capture_logs() as events:
            await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model=engines.ANSWER_MODEL, purpose="engine", engine="claude",
        )


class TestAnthropicFailures:
    async def test_a_call_that_got_no_response_logs_zeros_and_says_why(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={
                "type": "error",
                "error": {"type": "authentication_error", "message": ERROR_SENTINEL},
            })

        install_anthropic(monkeypatch, refuse)

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.error_code == "PROVIDER_AUTH_FAILED"
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model=engines.ANSWER_MODEL, purpose="engine", engine="claude",
            ok=False, error="http_401",
        )
        # The vendor's message is in the exception, and never reaches a line.
        assert_counts_only(events)

    async def test_the_sdks_own_retry_is_a_second_line_marked_as_a_retry(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`messages.create` retries a 529 inside one call and tells its caller nothing.

        Two requests reached Anthropic, so two lines: the one that was refused (no usage came
        back) and the retry that answered. `calls` is requests made and `retries` how many
        were a second attempt, which is what a scan's total needs to say.
        """
        answered = {"n": 0}

        def overloaded_then_answers(request: httpx.Request) -> httpx.Response:
            answered["n"] += 1
            if answered["n"] == 1:
                return httpx.Response(
                    529, headers={"retry-after-ms": "1"},
                    json={"type": "error", "error": {"type": "overloaded_error",
                                                     "message": ERROR_SENTINEL}},
                )
            return httpx.Response(200, json=message(input_tokens=11, output_tokens=22))

        install_anthropic(monkeypatch, overloaded_then_answers)

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.OK
        assert answered["n"] == 2
        first, second = lines(events)
        assert facts(first) == expected(
            provider="anthropic", model=engines.ANSWER_MODEL, purpose="engine", engine="claude",
            ok=False, error="http_529", retry=False,
        )
        assert facts(second) == expected(
            provider="anthropic", model="claude-opus-5", purpose="engine", engine="claude",
            input_tokens=11, output_tokens=22, retry=True,
        )
        assert_counts_only(events)

    async def test_every_attempt_timing_out_is_two_lines_and_the_second_is_a_retry(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Epic 9.1 shape: a timed-out attempt may have been billed, and no usage says so."""
        attempts = {"n": 0}

        def times_out(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            raise httpx.ReadTimeout(ERROR_SENTINEL, request=request)

        install_anthropic(monkeypatch, times_out)

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.error_code == "TIMEOUT"
        assert attempts["n"] == engines.MAX_RETRIES + 1 == 2
        first, second = lines(events)
        assert (first["ok"], first["error"], first["retry"]) == (False, "APITimeoutError", False)
        assert (second["ok"], second["error"], second["retry"]) == (False, "APITimeoutError", True)
        assert_counts_only(events)

    async def test_a_call_cut_by_its_own_deadline_is_one_line_naming_the_timeout(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def never(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        install_anthropic(monkeypatch, never)  # type: ignore[arg-type]
        monkeypatch.setattr(engines, "ENGINE_CALL_CEILING", 0.2)

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.TIMEOUT
        (line,) = lines(events)
        assert (line["ok"], line["error"], line["retry"]) == (False, "TimeoutError", False)
        assert line["input_tokens"] == line["output_tokens"] == 0


# ---------------------------------------------------------------------------
# the httpx engines
# ---------------------------------------------------------------------------


def openai_payload(**usage: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1", "model": "gpt-5.5-2026-04-23",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": ANSWER_SENTINEL}}],
        "usage": usage,
    }


def perplexity_payload(**usage: Any) -> dict[str, Any]:
    return {
        "id": "r", "status": "completed", "model": "perplexity/sonar",
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": ANSWER_SENTINEL,
                                 "annotations": []}]}],
        "usage": usage,
    }


def gemini_payload(*, grounding: bool = False, model_version: str | None = None,
                   finish: str = "STOP", **usage: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "content": {"role": "model", "parts": [{"text": ANSWER_SENTINEL}]},
        "finishReason": finish,
    }
    if grounding:
        candidate["groundingMetadata"] = {
            "webSearchQueries": [f"{GROUNDING_SENTINEL} one", f"{GROUNDING_SENTINEL} two"],
        }
    body: dict[str, Any] = {"candidates": [candidate], "usageMetadata": usage}
    if model_version is not None:
        body["modelVersion"] = model_version
    return body


class TestOpenAI:
    async def test_it_logs_reasoning_and_cached_tokens(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(200, json=openai_payload(
            prompt_tokens=111, completion_tokens=222, total_tokens=333,
            prompt_tokens_details={"cached_tokens": 11},
            completion_tokens_details={"reasoning_tokens": 77},
        )))

        with structlog.testing.capture_logs() as events:
            answer = await ChatGptAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.OK
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="openai", model="gpt-5.5-2026-04-23", purpose="engine", engine="chatgpt",
            input_tokens=111,
            # `completion_tokens` already counts the reasoning; `reasoning_tokens` is its share.
            output_tokens=222, reasoning_tokens=77, cache_read_tokens=11,
        )
        assert_counts_only(events)

    async def test_it_sends_no_tool_so_it_reports_no_search(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = install_httpx(monkeypatch, lambda r: httpx.Response(200, json=openai_payload(
            prompt_tokens=1, completion_tokens=1)))

        with structlog.testing.capture_logs() as events:
            await ChatGptAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert "tools" not in json.loads(sent[0].content)
        (line,) = lines(events)
        assert line["search_invocations"] == 0

    async def test_a_server_error_is_one_unanswered_line_naming_the_status(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(500, json={
            "error": {"message": ERROR_SENTINEL}}))

        with structlog.testing.capture_logs() as events:
            answer = await ChatGptAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.error_code == "PROVIDER_ERROR"
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="openai", model=engines.OPENAI_ANSWER_MODEL, purpose="engine",
            engine="chatgpt", ok=False, error="http_500",
        )
        assert_counts_only(events)

    async def test_a_truncated_answer_still_logs_what_was_billed(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = openai_payload(prompt_tokens=20, completion_tokens=4000,
                              completion_tokens_details={"reasoning_tokens": 4000})
        body["choices"][0]["finish_reason"] = "length"
        body["choices"][0]["message"]["content"] = ""
        install_httpx(monkeypatch, lambda r: httpx.Response(200, json=body))

        with structlog.testing.capture_logs() as events:
            answer = await ChatGptAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.TRUNCATED
        (line,) = lines(events)
        assert (line["ok"], line["output_tokens"], line["reasoning_tokens"]) == (True, 4000, 4000)


class TestPerplexity:
    async def test_it_logs_its_search_invocations_and_never_its_own_dollar_figure(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(200, json=perplexity_payload(
            input_tokens=1758, output_tokens=432,
            input_tokens_details={"cache_read_input_tokens": 5},
            tool_calls_details={"search_web": {"invocation": 2}},
            cost={"total_cost": VENDOR_COST},
        )))

        with structlog.testing.capture_logs() as events:
            answer = await PerplexityAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.OK
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="perplexity", model="perplexity/sonar", purpose="engine",
            engine="perplexity", input_tokens=1758, output_tokens=432,
            cache_read_tokens=5, search_invocations=2,
        )
        # `assert_counts_only` also fails on the vendor's 0.0123 anywhere in the line.
        assert_counts_only(events)

    async def test_a_429_then_an_answer_is_two_lines_and_the_second_is_the_retry(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def no_wait(seconds: float) -> None:
            return None

        monkeypatch.setattr(engines.asyncio, "sleep", no_wait)
        sent = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            sent["n"] += 1
            if sent["n"] == 1:
                return httpx.Response(429, json={}, headers={"Retry-After": "1"})
            return httpx.Response(200, json=perplexity_payload(
                input_tokens=100, output_tokens=50,
                tool_calls_details={"search_web": {"invocation": 1}}))

        install_httpx(monkeypatch, handler)

        with structlog.testing.capture_logs() as events:
            answer = await PerplexityAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.OK
        assert sent["n"] == 2
        first, second = lines(events)
        assert facts(first) == expected(
            provider="perplexity", model=engines.PERPLEXITY_ANSWER_MODEL, purpose="engine",
            engine="perplexity", ok=False, error="http_429", retry=False,
        )
        assert facts(second) == expected(
            provider="perplexity", model="perplexity/sonar", purpose="engine",
            engine="perplexity", input_tokens=100, output_tokens=50, search_invocations=1,
            retry=True,
        )

    async def test_a_second_429_is_two_unanswered_lines_and_one_retry(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def no_wait(seconds: float) -> None:
            return None

        monkeypatch.setattr(engines.asyncio, "sleep", no_wait)
        install_httpx(monkeypatch, lambda r: httpx.Response(
            429, json={}, headers={"Retry-After": "1"}))

        with structlog.testing.capture_logs() as events:
            answer = await PerplexityAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.RATE_LIMITED
        first, second = lines(events)
        assert [(c["ok"], c["error"], c["retry"]) for c in (first, second)] == [
            (False, "http_429", False), (False, "http_429", True),
        ]

    async def test_a_429_that_names_no_wait_is_not_retried_and_is_one_line(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(429, json={}))

        with structlog.testing.capture_logs() as events:
            await PerplexityAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        (line,) = lines(events)
        assert (line["ok"], line["error"], line["retry"]) == (False, "http_429", False)

    async def test_a_failed_run_on_a_200_still_logs_the_usage_it_carries(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = perplexity_payload(input_tokens=900, output_tokens=0)
        body["status"] = "failed"
        install_httpx(monkeypatch, lambda r: httpx.Response(200, json=body))

        with structlog.testing.capture_logs() as events:
            answer = await PerplexityAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.ERROR
        (line,) = lines(events)
        assert (line["ok"], line["input_tokens"]) == (True, 900)


class TestGemini:
    async def test_it_logs_thoughts_inside_the_output_count_and_the_grounding_queries(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(200, json=gemini_payload(
            grounding=True, model_version="gemini-3.8-flash-001",
            promptTokenCount=20, candidatesTokenCount=30, thoughtsTokenCount=5,
            cachedContentTokenCount=4,
        )))

        with structlog.testing.capture_logs() as events:
            answer = await GeminiAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.OK
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="google", model="gemini-3.8-flash-001", purpose="engine", engine="gemini",
            input_tokens=20,
            # Gemini reports thoughts BESIDE the candidate tokens and bills them as output.
            output_tokens=35, reasoning_tokens=5, cache_read_tokens=4,
            # The COUNT of grounding queries; the queries themselves are never read into a line.
            search_invocations=2,
        )
        assert_counts_only(events)

    async def test_a_body_that_names_no_model_gets_the_one_in_the_request(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(200, json=gemini_payload(
            promptTokenCount=3, candidatesTokenCount=4)))

        with structlog.testing.capture_logs() as events:
            await GeminiAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        (line,) = lines(events)
        assert line["model"] == engines.GEMINI_ANSWER_MODEL
        assert (line["input_tokens"], line["output_tokens"], line["reasoning_tokens"]) == (3, 4, 0)

    async def test_a_blocked_answer_still_logs_the_usage_it_reported(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(200, json=gemini_payload(
            finish="SAFETY", promptTokenCount=8, candidatesTokenCount=0)))

        with structlog.testing.capture_logs() as events:
            answer = await GeminiAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.error_code == "PROVIDER_REFUSED"
        (line,) = lines(events)
        assert (line["ok"], line["input_tokens"]) == (True, 8)

    async def test_a_rate_limit_is_one_unanswered_line_and_never_the_key_or_the_url(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_httpx(monkeypatch, lambda r: httpx.Response(429, json={
            "error": {"status": "RESOURCE_EXHAUSTED", "message": ERROR_SENTINEL}}))

        with structlog.testing.capture_logs() as events:
            await GeminiAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        (line,) = lines(events)
        assert facts(line) == expected(
            provider="google", model=engines.GEMINI_ANSWER_MODEL, purpose="engine",
            engine="gemini", ok=False, error="http_429",
        )
        assert_counts_only(events)


# ---------------------------------------------------------------------------
# the five helper calls, all on `messages.parse`
# ---------------------------------------------------------------------------


def classifiable_crawl() -> CrawlResult:
    text = f"{BRAND_SENTINEL} " + "word " * 200
    signals = CrawlSignals(
        final_url="https://northaven-dental.example",
        registrable_domain="northaven-dental.example",
        word_count=len(text.split()),
    )
    return CrawlResult(signals=signals, text_extract=text, ok=True)


def one_fix_for_every_candidate(request: httpx.Request) -> dict[str, Any]:
    """A valid fix set for whatever candidates the prompt lists, as conftest's stub builds it."""
    content = json.loads(request.content)["messages"][0]["content"]
    keys = [
        line.split(" — ")[0].removeprefix("- ")
        for line in content.splitlines()
        if line.startswith("- gap:") or line.startswith("- audit:")
    ]
    return {"fixes": [
        {"candidate_id": key, "title": f"Improve the site signal number {i}",
         "detail": "Add the missing markup to the page and confirm it validates.",
         "priority": "medium", "effort": "M",
         "priority_reason": "It moves a weighted dimension.",
         "effort_reason": "A template edit and a redeploy."}
        for i, key in enumerate(keys, start=1)
    ]}


def fix_inputs() -> tuple[fix_generator.FixFacts, list[fix_generator.FixCandidate]]:
    weight, subscore = Decimal("25"), Decimal("30")
    dimension = fix_generator.DimensionFact(
        key="share_of_voice", weight=weight, subscore=subscore,
        gap=(weight * (Decimal(100) - subscore) / Decimal(100)).quantize(Decimal("0.01")),
    )
    facts_ = fix_generator.FixFacts(
        domain="helpscout.com", brand_name="Help Scout", dimensions=[dimension]
    )
    return facts_, fix_generator.build_candidates([dimension], [])


class TestTheFiveHelperCalls:
    """Each logs its purpose, the model the response names, and the counts it reported."""

    async def test_sentiment(self, keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=structured(
            {"sentiment": "positive", "confidence": 0.9},
            model="claude-haiku-4-5-20251001", input_tokens=410, output_tokens=33)))
        answer = EngineAnswer(
            engine=Engine.CLAUDE, engine_version="t", prompt_text=PROMPT_SENTINEL,
            text=f"{ANSWER_SENTINEL} {BRAND_SENTINEL} is a strong option.",
        )

        with structlog.testing.capture_logs() as events:
            result = await extraction.classify_sentiment(
                answer, subject_name=BRAND_SENTINEL, settings=keyed)

        assert result[0] is not None
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-haiku-4-5-20251001", purpose="sentiment",
            input_tokens=410, output_tokens=33,
        )
        assert_counts_only(events)

    async def test_prompt_generation(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=structured(
            {"prompts": [{"text": f"{PROMPT_SENTINEL} who is best", "intent": "awareness"}]},
            model="claude-opus-5", input_tokens=600, output_tokens=2500,
            output_tokens_details={"thinking_tokens": 1800})))

        with structlog.testing.capture_logs() as events:
            _, generated_by = await prompts.generate_prompts(
                brand_name=BRAND_SENTINEL, domain="brand-sentinel.example",
                industry="help desk software", niche=None, settings=keyed)

        assert generated_by.startswith("claude-opus-5")
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-opus-5", purpose="prompt_generation",
            input_tokens=600, output_tokens=2500, reasoning_tokens=1800,
        )
        assert_counts_only(events, "brand-sentinel.example")

    async def test_fixes(self, keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=structured(
            one_fix_for_every_candidate(r),
            model="claude-opus-5", input_tokens=2200, output_tokens=5100,
            output_tokens_details={"thinking_tokens": 3000})))

        with structlog.testing.capture_logs() as events:
            outcome = await fix_generator.generate_fixes(*fix_inputs(), settings=keyed)

        assert outcome.status == "generated"
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-opus-5", purpose="fixes",
            input_tokens=2200, output_tokens=5100, reasoning_tokens=3000,
        )

    async def test_detection(self, keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=structured(
            {"brands": [{"name": f"{BRAND_SENTINEL} Rival", "domain": "front.com"}],
             "subject_named": False},
            model="claude-opus-5", input_tokens=300, output_tokens=140)))

        with structlog.testing.capture_logs() as events:
            result = await cocitation.run_seed_prompt(
                f"{PROMPT_SENTINEL} alternatives?", subject_brand="Help Scout", settings=keyed)

        assert result.ok
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-opus-5", purpose="detection",
            input_tokens=300, output_tokens=140,
        )
        assert_counts_only(events)

    async def test_classification_has_its_own_line_and_the_scan_total_never_counts_it(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=structured(
            {"is_classifiable": True, "industry": "dental practice", "niche": None,
             "brand_name": "Northaven Dental", "confidence": "high", "confidence_score": 0.95,
             "rationale": ERROR_SENTINEL},
            model="claude-opus-5", input_tokens=900, output_tokens=210,
            output_tokens_details={"thinking_tokens": 150})))

        with structlog.testing.capture_logs() as events:
            outcome = await classify.classify(classifiable_crawl(), settings=keyed)

        assert outcome.status == "classified"
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-opus-5", purpose="classification",
            input_tokens=900, output_tokens=210, reasoning_tokens=150,
        )
        assert_counts_only(events, "northaven-dental.example")
        # Outside a scan there is nothing to total.
        assert lines(events, "scan.usage") == []

    async def test_a_response_the_sdk_cannot_parse_logs_zeros_and_names_the_failure(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE KNOWN LIMIT, pinned so that lifting it is a decision and not a surprise.

        `messages.parse` validates the answer inside the SDK and raises a `ValidationError`
        that carries neither the response nor its usage. The provider billed that call, so
        the line says what it can: `ok=False`, zero counts, and the class of the failure. A
        run of these in a scan's `unanswered` count is the sign.
        """
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=structured(
            {"fixes": [{"candidate_id": "gap:x", "title": "t" * 500, "detail": "d",
                        "priority": "high", "effort": "S",
                        "priority_reason": "r", "effort_reason": "r"}]},
            model="claude-opus-5", input_tokens=2000, output_tokens=3000)))

        with structlog.testing.capture_logs() as events:
            outcome = await fix_generator.generate_fixes(*fix_inputs(), settings=keyed)

        assert outcome.reason_code == "PROVIDER_SCHEMA_VIOLATION"
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-opus-5", purpose="fixes",
            ok=False, error="ValidationError",
        )

    async def test_a_helper_call_that_is_retried_by_the_sdk_marks_the_retry(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = {"n": 0}

        def flaky(request: httpx.Request) -> httpx.Response:
            seen["n"] += 1
            if seen["n"] == 1:
                return httpx.Response(
                    529, headers={"retry-after-ms": "1"},
                    json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
            return httpx.Response(200, json=structured(
                {"sentiment": "neutral", "confidence": 0.5},
                model="claude-haiku-4-5-20251001", input_tokens=7, output_tokens=3))

        install_anthropic(monkeypatch, flaky)
        answer = EngineAnswer(engine=Engine.CLAUDE, engine_version="t", prompt_text="q", text="x")

        with structlog.testing.capture_logs() as events:
            await extraction.classify_sentiment(answer, subject_name="Help Scout", settings=keyed)

        first, second = lines(events)
        assert [(c["ok"], c["retry"], c["purpose"]) for c in (first, second)] == [
            (False, False, "sentiment"), (True, True, "sentiment"),
        ]
        assert (second["input_tokens"], second["output_tokens"]) == (7, 3)

    async def test_the_sdk_clients_keep_the_retry_and_timeout_they_were_given(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counting a call changes nothing about how it is made: the bound is still passed."""
        captured: dict[str, Any] = {}
        real_cls = anthropic.AsyncAnthropic

        def factory(**kwargs: Any) -> anthropic.AsyncAnthropic:
            captured.update(kwargs)
            return real_cls(**kwargs, http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=structured(
                    {"sentiment": "neutral", "confidence": 0.5}, model="m")))))

        monkeypatch.setattr(anthropic, "AsyncAnthropic", factory)
        bound = CallBound(timeout=7.0, max_retries=1)
        client = bound.client(keyed)

        assert captured["timeout"] == 7.0
        assert captured["max_retries"] == 1
        assert client.max_retries == 1


class TestAnEngineCallInsideAWholeScanOfCalls:
    async def test_two_engines_asked_together_each_log_their_own_line(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ask_all` gathers the engines as tasks; each task's line is its own engine's."""
        install_anthropic(monkeypatch, lambda r: httpx.Response(200, json=message(
            input_tokens=5, output_tokens=6)))

        def openai_or_gemini(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.openai.com":
                return httpx.Response(200, json=openai_payload(
                    prompt_tokens=7, completion_tokens=8))
            return httpx.Response(200, json=gemini_payload(
                promptTokenCount=9, candidatesTokenCount=10))

        install_httpx(monkeypatch, openai_or_gemini)

        with structlog.testing.capture_logs() as events:
            answers = await engines.ask_all(
                PROMPT_SENTINEL,
                engines=(Engine.CLAUDE, Engine.CHATGPT, Engine.GEMINI),
                settings=keyed,
            )

        assert all(a.ok for a in answers)
        by_engine = {c["engine"]: c for c in lines(events)}
        assert set(by_engine) == {"claude", "chatgpt", "gemini"}
        wanted = {"claude": (5, 6), "chatgpt": (7, 8), "gemini": (9, 10)}
        for engine, counts in wanted.items():
            line = by_engine[engine]
            assert (line["input_tokens"], line["output_tokens"]) == counts, engine
        assert_counts_only(events)


# ---------------------------------------------------------------------------
# what the SDK's middleware does, and what happens on an SDK that has none
# ---------------------------------------------------------------------------


class TestWithoutTheSdkMiddleware:
    """`sdk_client_options` offers the middleware only to an SDK that takes one. An SDK without it
    (`pyproject.toml` allows `anthropic>=0.40`) must still make every call, and log it."""

    async def test_a_client_built_without_it_still_logs_one_line_and_cannot_see_the_sdks_retry(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(usage, "_SDK_TAKES_MIDDLEWARE", False)
        built: dict[str, Any] = {}
        real_cls = anthropic.AsyncAnthropic
        asked = {"n": 0}

        def overloaded_then_answers(request: httpx.Request) -> httpx.Response:
            asked["n"] += 1
            if asked["n"] == 1:
                return httpx.Response(
                    529, headers={"retry-after-ms": "1"},
                    json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
            return httpx.Response(200, json=message(input_tokens=11, output_tokens=22))

        def factory(**kwargs: Any) -> anthropic.AsyncAnthropic:
            built.update(kwargs)
            return real_cls(**kwargs, http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(overloaded_then_answers)))

        monkeypatch.setattr(anthropic, "AsyncAnthropic", factory)

        with structlog.testing.capture_logs() as events:
            answer = await ClaudeParametricAdapter().ask(PROMPT_SENTINEL, settings=keyed)

        assert answer.status is EngineResultStatus.OK
        assert "middleware" not in built
        # The SDK did retry, inside the one call, and nobody was told: one line, not a retry.
        assert asked["n"] == 2
        (line,) = lines(events)
        assert facts(line) == expected(
            provider="anthropic", model="claude-opus-5", purpose="engine", engine="claude",
            input_tokens=11, output_tokens=22, retry=False,
        )

    async def test_helper_clients_lose_the_middleware_too(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bound = CallBound(timeout=5.0, max_retries=1)
        assert bound.client(keyed).middleware == (usage.count_attempts,)

        monkeypatch.setattr(usage, "_SDK_TAKES_MIDDLEWARE", False)
        assert bound.client(keyed).middleware == ()


class TestTheMiddlewareChangesOneHeaderAndNothingElse:
    """What `usage.py` and `call_bounds.py` say about the middleware, held to the pinned SDK.

    On it, a client with middleware also sends the SDK's raw-response header, and takes the SDK's
    middleware code path. Retries, timeouts, idempotency and parsing are unchanged. Said by
    comparing the requests a client with it and a client without it send for the same call, a
    529 and then an answer through `messages.parse`, and what each returns. An SDK upgrade that
    changes any of it fails here, and the two docstrings get reworded.
    """

    async def _parse_through(
        self, **client_options: Any
    ) -> tuple[list[httpx.Request], Any]:
        seen: list[httpx.Request] = []

        def overloaded_then_answers(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(
                    529, headers={"retry-after-ms": "1"},
                    json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
            return httpx.Response(200, json=structured(
                {"sentiment": "neutral", "confidence": 0.5},
                model="claude-haiku-4-5-20251001", input_tokens=7, output_tokens=3))

        client = anthropic.AsyncAnthropic(
            api_key="not-a-real-key", timeout=7.0, max_retries=1,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(overloaded_then_answers)),
            **client_options,
        )
        result = await client.messages.parse(
            model="claude-haiku-4-5-20251001", max_tokens=50, system="s",
            messages=[{"role": "user", "content": "q"}],
            output_format=extraction.SentimentJudgement,
        )
        return seen, result

    async def test_the_two_clients_send_the_same_requests_apart_from_the_raw_response_header(
        self,
    ) -> None:
        plain_seen, _ = await self._parse_through()
        counted_seen, _ = await self._parse_through(**usage.sdk_client_options())

        # Retries: the same two attempts.
        assert len(plain_seen) == len(counted_seen) == 2
        for before, after in zip(plain_seen, counted_seen, strict=True):
            assert (before.method, str(before.url), before.content) == (
                after.method, str(after.url), after.content)
            plain_headers, counted_headers = dict(before.headers), dict(after.headers)
            # The header is the SDK's own raw-response mode flag. It names a mode: no secret.
            assert "x-stainless-raw-response" not in plain_headers
            assert counted_headers.pop("x-stainless-raw-response") == "raw"
            # Everything else is the same: the key, the retry count, the user agent and any
            # idempotency header the SDK sends.
            assert counted_headers == plain_headers
            # Timeouts: the same per-request budget.
            assert before.extensions["timeout"] == after.extensions["timeout"]
        assert [r.headers["x-stainless-retry-count"] for r in counted_seen] == ["0", "1"]

    async def test_the_two_clients_return_the_same_parsed_answer(self) -> None:
        _, plain = await self._parse_through()
        _, counted = await self._parse_through(**usage.sdk_client_options())

        assert counted.parsed_output == plain.parsed_output
        assert (counted.model, counted.usage, counted.stop_reason) == (
            plain.model, plain.usage, plain.stop_reason)

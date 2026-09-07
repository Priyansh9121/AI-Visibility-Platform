"""The ChatGPT adapter — Epic 9.13, the third engine and the first second vendor.

Epic 4.2 closed with the limitation this file exists to retire: "**They are
still one vendor and one model**, so this does not test cross-vendor variance."

The property that matters most here is NOT that the adapter works. It is that a
failure from OpenAI lands on the **same statuses and the same error codes** a
failure from Anthropic does. `EngineResult` rows from both vendors are read by
one scoring path and rendered by one report, so a vendor that invented its own
vocabulary would either break the report or force a translation layer nobody
maintains.

No network. The error mapper is pure and is tested directly; the adapter is
exercised over a faked `httpx` transport, so the code under test is the request
`engines.py` genuinely builds.
"""

from __future__ import annotations

import asyncio

import anthropic
import httpx
import pytest

from avp_api.config import Settings
from avp_api.models.engine_result import Engine, EngineResultStatus
from avp_api.services import engines
from avp_api.services.engines import (
    CLAUDE_STOPS,
    OPENAI_STOPS,
    ChatGptAdapter,
    _map_error,
    _map_openai_error,
    classify_stop,
)


@pytest.fixture
def engine_settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        openai_api_key="test-key-never-sent-anywhere",
    )


def _status_error(code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(code, json=body if body is not None else {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestErrorMappingMirrorsClaude:
    """Same statuses, same codes — never a new vocabulary."""

    def test_every_code_it_emits_already_exists_on_the_claude_path(self) -> None:
        # The real guard. Read the codes Claude's mapper can produce straight
        # out of the Anthropic exception hierarchy, then assert OpenAI's mapper
        # never emits one that is not in that set. A new code invented here
        # would reach the report as an unrecognised string.
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        claude_codes = {
            _map_error(exc)[1]
            for exc in (
                anthropic.RateLimitError(
                    "x", response=httpx.Response(429, request=req), body=None
                ),
                anthropic.AuthenticationError(
                    "x", response=httpx.Response(401, request=req), body=None
                ),
                anthropic.BadRequestError(
                    "x", response=httpx.Response(400, request=req), body=None
                ),
                anthropic.BadRequestError(
                    "your credit balance is too low",
                    response=httpx.Response(400, request=req),
                    body=None,
                ),
                anthropic.APITimeoutError(request=req),
                anthropic.APIConnectionError(request=req),
                RuntimeError("unmapped"),
            )
        }
        # PROVIDER_REFUSED is set outside the mapper, on both paths.
        claude_codes.add("PROVIDER_REFUSED")
        # So are the stop-reason codes — by `classify_stop`, which both paths
        # share. Derived from the Claude vocabulary here and the OpenAI one
        # below, so the assertion still catches a vocabulary that drifts.
        claude_codes |= {
            classify_stop(reason, CLAUDE_STOPS)[1]
            for reason in ("max_tokens", "pause_turn", "never-seen")
        }

        openai_codes = {
            _map_openai_error(exc)[1]
            for exc in (
                _status_error(429),
                _status_error(401),
                _status_error(403),
                _status_error(400),
                _status_error(400, {"error": {"code": "insufficient_quota"}}),
                _status_error(402),
                _status_error(500),
                httpx.ReadTimeout("t", request=None),
                httpx.ConnectError("c", request=None),
                RuntimeError("unmapped"),
            )
        }
        openai_codes |= {
            classify_stop(reason, OPENAI_STOPS)[1]
            for reason in ("length", "tool_calls", "function_call", "never-seen")
        }

        assert openai_codes <= claude_codes, (
            f"invented codes: {sorted(openai_codes - claude_codes)}"
        )

    @pytest.mark.parametrize(
        ("exc", "status", "code"),
        [
            (_status_error(429), EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED"),
            (_status_error(401), EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"),
            (_status_error(403), EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"),
            (_status_error(400), EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"),
            (_status_error(500), EngineResultStatus.ERROR, "PROVIDER_ERROR"),
            (_status_error(402), EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"),
        ],
    )
    def test_http_status_codes_map_as_claude_maps_them(
        self, exc: Exception, status: EngineResultStatus, code: str
    ) -> None:
        assert _map_openai_error(exc) == (status, code)

    def test_an_exhausted_balance_is_quota_not_a_bad_request(self) -> None:
        # Anthropic reports this as a 400 whose MESSAGE contains "credit
        # balance"; OpenAI reports it as a 400 with a typed `code`. Both must
        # land on PROVIDER_QUOTA_EXHAUSTED, or an operator sees "bad request"
        # when the real problem is billing.
        for code in ("insufficient_quota", "billing_hard_limit_reached"):
            exc = _status_error(400, {"error": {"code": code}})
            assert _map_openai_error(exc) == (
                EngineResultStatus.ERROR,
                "PROVIDER_QUOTA_EXHAUSTED",
            )

    def test_a_non_json_400_is_still_a_clean_bad_request(self) -> None:
        # The quota probe reads the response body. A 400 that is not JSON must
        # not raise out of the error mapper — an error handler that throws is
        # worse than the error it was handling.
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(400, text="<html>gateway</html>", request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _map_openai_error(exc) == (
            EngineResultStatus.ERROR,
            "PROVIDER_BAD_REQUEST",
        )

    def test_timeout_is_checked_before_transport(self) -> None:
        # The Epic 9.2 lesson, re-applied: `TimeoutException` subclasses
        # `TransportError`, so testing transport first would make TIMEOUT
        # unreachable and report every timeout as PROVIDER_UNREACHABLE — which
        # is exactly the defect Epic 9.1 spent 271.6s discovering on the
        # Anthropic path.
        assert issubclass(httpx.TimeoutException, httpx.TransportError)
        assert _map_openai_error(httpx.ReadTimeout("t", request=None)) == (
            EngineResultStatus.TIMEOUT,
            "TIMEOUT",
        )
        assert _map_openai_error(httpx.ConnectError("c", request=None)) == (
            EngineResultStatus.ERROR,
            "PROVIDER_UNREACHABLE",
        )

    def test_an_asyncio_timeout_is_a_timeout(self) -> None:
        # The outer ENGINE_CALL_CEILING deadline raises this, not httpx.
        assert _map_openai_error(TimeoutError()) == (
            EngineResultStatus.TIMEOUT,
            "TIMEOUT",
        )

    def test_an_unknown_exception_is_a_status_not_a_crash(self) -> None:
        assert _map_openai_error(RuntimeError("who knows")) == (
            EngineResultStatus.ERROR,
            "PROVIDER_ERROR",
        )


class TestTheAdapterItself:
    def test_it_is_registered_and_run_by_default(self) -> None:
        # The registry is explicit, not derived from the Engine enum, so an
        # adapter that exists but is not in BOTH literals is dead code. This is
        # the assertion that would have caught forgetting DEFAULT_ENGINES.
        assert engines.ENGINE_REGISTRY[Engine.CHATGPT].__class__ is ChatGptAdapter
        assert Engine.CHATGPT in engines.DEFAULT_ENGINES
        assert len(engines.DEFAULT_ENGINES) == 3

    def test_every_default_engine_has_an_adapter(self) -> None:
        # `ask_all` subscripts ENGINE_REGISTRY bare, so a default engine with no
        # adapter is a KeyError in a background task rather than a mapped status.
        for engine in engines.DEFAULT_ENGINES:
            assert engine in engines.ENGINE_REGISTRY

    def test_it_conforms_to_the_adapter_contract(self) -> None:
        adapter = ChatGptAdapter()
        assert adapter.engine is Engine.CHATGPT
        # `<model>/<mode>` — the convention both Claude adapters use, and what
        # lands in Scan.engine_versions for provenance.
        assert adapter.version == f"{engines.OPENAI_ANSWER_MODEL}/parametric"
        assert "/" in adapter.version

    def test_it_is_the_parametric_analogue_of_claude_not_of_claude_search(self) -> None:
        # Epic 4.2's reasoning applied rather than replaced: holding the MODE
        # constant is what makes a disagreement between engines a statement
        # about the vendors instead of about browsing.
        assert engines.ENGINE_REGISTRY[Engine.CLAUDE].version.endswith("/parametric")
        assert engines.ENGINE_REGISTRY[Engine.CHATGPT].version.endswith("/parametric")
        assert not engines.ENGINE_REGISTRY[Engine.CLAUDE_SEARCH].version.endswith(
            "/parametric"
        )

    async def test_a_good_answer_produces_text_and_no_citations(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "Zendesk and Help Scout."},
                        }
                    ]
                },
            )

        _install(monkeypatch, httpx.MockTransport(handler))
        answer = await ChatGptAdapter().ask("who?", settings=engine_settings)

        assert answer.status is EngineResultStatus.OK
        assert answer.text == "Zendesk and Help Scout."
        # Parametric: it sends no tools, so there is nothing retrieved to cite.
        # An empty list, never a fabricated one.
        assert answer.citations == []
        assert answer.digest() is not None

    async def test_every_budget_is_sent_not_inherited(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Claude-side lesson (test_engine_timeout.py) applied to this vendor:
        a dropped kwarg silently restores a provider default, so guard the
        request body itself. gpt-5.5 defaults to MEDIUM reasoning effort and
        bills reasoning tokens against `max_completion_tokens`; unpinned, that
        is a 4,000-token budget spent thinking before a visible token exists,
        which is how an empty `finish_reason: length` answer is produced."""
        import json

        sent: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            sent.update(json.loads(request.content))
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": {"content": "x"}}]}
            )

        _install(monkeypatch, httpx.MockTransport(handler))
        await ChatGptAdapter().ask("q", settings=engine_settings)

        assert sent["model"] == engines.OPENAI_ANSWER_MODEL
        assert sent["max_completion_tokens"] == engines.OPENAI_MAX_TOKENS
        assert sent["reasoning_effort"] == engines.OPENAI_REASONING_EFFORT == "low"
        # Same footing as the Claude parametric engine it is compared against.
        assert engines.OPENAI_REASONING_EFFORT == engines.ANSWER_EFFORT

    async def test_a_refusal_maps_to_the_shared_refused_code(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same code `_ClaudeBase` sets on `stop_reason == "refusal"`.
        for payload in (
            {"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]},
            {"choices": [{"finish_reason": "stop", "message": {"refusal": "no"}}]},
        ):
            _install(
                monkeypatch,
                httpx.MockTransport(lambda r, p=payload: httpx.Response(200, json=p)),
            )
            answer = await ChatGptAdapter().ask("x", settings=engine_settings)
            assert answer.status is EngineResultStatus.ERROR
            assert answer.error_code == "PROVIDER_REFUSED"

    async def test_a_provider_failure_returns_a_status_and_never_raises(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `ask_all` gathers adapters concurrently; one raising would fail the
        # whole prompt rather than degrading one cell of the grid.
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(429, json={})))
        answer = await ChatGptAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.RATE_LIMITED
        assert answer.error_code == "PROVIDER_RATE_LIMITED"
        assert answer.text == ""

    async def test_the_answer_text_never_reaches_a_log_view(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ip-safety.md #7 — the same boundary the Claude adapters hold.
        secret = "PROPRIETARY THIRD PARTY PROSE"
        _install(
            monkeypatch,
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={"choices": [{"finish_reason": "stop", "message": {"content": secret}}]},
                )
            ),
        )
        answer = await ChatGptAdapter().ask("x", settings=engine_settings)
        assert secret not in str(answer.redacted())
        assert secret not in str(answer.digest())

    async def test_a_hanging_call_returns_inside_the_ceiling(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The ceiling is enforced by the outer deadline, so it holds for this
        # adapter by the same mechanism as for Claude — asserted, not assumed.
        class _Hanging(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                await asyncio.sleep(3600)
                raise AssertionError("unreachable")

        monkeypatch.setattr(engines, "ENGINE_CALL_CEILING", 0.25)
        _install(monkeypatch, _Hanging())
        answer = await ChatGptAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.TIMEOUT
        assert answer.error_code == "TIMEOUT"


def _install(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    """Force every AsyncClient the adapter builds onto a faked transport.

    The adapter constructs its own client, which is the point — the request
    under test is the one `engines.py` genuinely builds, headers and body
    included, rather than one the test re-declares.
    """
    real = httpx.AsyncClient

    def factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(engines.httpx, "AsyncClient", factory)

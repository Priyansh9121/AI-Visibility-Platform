"""The Gemini adapter — Epic 21, the fifth engine and the third parametric vendor.

Same discipline as the OpenAI and Perplexity tests, and the same property: a
Gemini failure lands on the codes Claude's do. The two Google shapes that
would otherwise land wrong — an invalid key on a 400, the per-day quota on a
429 — are tested by name, because they are the reason the mapper reads the
body at all. No GOOGLE_AI_API_KEY existed when this was written; the shapes
are the documented ones (api/generate-content and docs/api-errors, read
2026-09-11), and build-log Epic 21 says so.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from avp_api.config import Settings
from avp_api.models.engine_result import Engine, EngineResultStatus
from avp_api.services import engines
from avp_api.services.engines import (
    CLAUDE_STOPS,
    GEMINI_BLOCKED,
    GEMINI_STOPS,
    GeminiAdapter,
    _map_error,
    _map_gemini_error,
    classify_stop,
    configured_engines,
)


@pytest.fixture
def engine_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        google_ai_api_key="test-key-never-sent-anywhere",
    )


URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"


def _status_error(code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", URL)
    response = httpx.Response(code, json=body if body is not None else {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# Captured ONCE, at import. `_install` is called several times inside one
# test; reading `httpx.AsyncClient` at each call would capture the previous
# factory and nest it, and the first transport would win every time.
_REAL_CLIENT = httpx.AsyncClient


def _install(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    def factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["transport"] = transport
        return _REAL_CLIENT(*args, **kwargs)

    monkeypatch.setattr(engines.httpx, "AsyncClient", factory)


def _candidate(text: str, finish: str = "STOP", *, parts: list | None = None) -> dict:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts or [{"text": text}]},
                "finishReason": finish,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 30,
            "thoughtsTokenCount": 5,
        },
    }


class TestErrorMappingMirrorsClaude:
    def test_every_code_it_emits_already_exists_on_the_claude_path(self) -> None:
        import anthropic

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        claude_codes = {
            _map_error(exc)[1]
            for exc in (
                anthropic.RateLimitError("x", response=httpx.Response(429, request=req), body=None),
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
        claude_codes.add("PROVIDER_REFUSED")
        claude_codes |= {
            classify_stop(r, CLAUDE_STOPS)[1] for r in ("max_tokens", "pause_turn", "never-seen")
        }
        gemini_codes = {
            _map_gemini_error(exc)[1]
            for exc in (
                _status_error(429),
                _status_error(
                    429, {"error": {"code": "quota_exceeded", "status": "RESOURCE_EXHAUSTED"}}
                ),
                _status_error(401),
                _status_error(403),
                _status_error(400),
                _status_error(
                    400,
                    {
                        "error": {
                            "status": "INVALID_ARGUMENT",
                            "details": [{"reason": "API_KEY_INVALID"}],
                        }
                    },
                ),
                _status_error(400, {"error": {"code": "failed_precondition"}}),
                _status_error(404),
                _status_error(500),
                _status_error(503),
                httpx.ReadTimeout("t", request=None),
                httpx.ConnectError("c", request=None),
                TimeoutError(),
                RuntimeError("unmapped"),
            )
        }
        gemini_codes |= {classify_stop(r, GEMINI_STOPS)[1] for r in ("MAX_TOKENS", "never-seen")}
        assert gemini_codes <= claude_codes, (
            f"invented codes: {sorted(gemini_codes - claude_codes)}"
        )

    def test_a_per_minute_429_passes_and_a_per_day_429_is_quota(self) -> None:
        # The novel shape the brief asked about: Google reports both with 429
        # RESOURCE_EXHAUSTED and the same "check your plan and billing"
        # sentence; the quota id is what tells them apart.
        minute = _status_error(
            429,
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "You exceeded your current quota, please check your plan.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}
                            ],
                        }
                    ],
                }
            },
        )
        day = _status_error(
            429,
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "You exceeded your current quota, please check your plan.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                            ],
                        }
                    ],
                }
            },
        )
        assert _map_gemini_error(minute) == (
            EngineResultStatus.RATE_LIMITED,
            "PROVIDER_RATE_LIMITED",
        )
        assert _map_gemini_error(day) == (EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED")
        assert _map_gemini_error(_status_error(429, {"error": {"code": "quota_exceeded"}})) == (
            EngineResultStatus.ERROR,
            "PROVIDER_QUOTA_EXHAUSTED",
        )

    def test_an_invalid_api_key_is_auth_even_though_google_sends_a_400(self) -> None:
        for body in (
            {
                "error": {
                    "code": 400,
                    "status": "INVALID_ARGUMENT",
                    "message": "API key not valid.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "API_KEY_INVALID",
                        }
                    ],
                }
            },
            {
                "error": {
                    "code": "authentication",
                    "message": "The API key is missing, invalid, or expired",
                }
            },
            {"error": {"status": "UNAUTHENTICATED"}},
        ):
            assert _map_gemini_error(_status_error(400, body)) == (
                EngineResultStatus.ERROR,
                "PROVIDER_AUTH_FAILED",
            )
        assert _map_gemini_error(
            _status_error(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad field"}})
        ) == (
            EngineResultStatus.ERROR,
            "PROVIDER_BAD_REQUEST",
        )

    def test_a_billing_precondition_is_quota_not_a_bad_request(self) -> None:
        assert _map_gemini_error(
            _status_error(400, {"error": {"code": "failed_precondition"}})
        ) == (
            EngineResultStatus.ERROR,
            "PROVIDER_QUOTA_EXHAUSTED",
        )

    @pytest.mark.parametrize(
        ("exc", "status", "code"),
        [
            (_status_error(401), EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"),
            (_status_error(403), EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"),
            (_status_error(404), EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"),
            (_status_error(500), EngineResultStatus.ERROR, "PROVIDER_ERROR"),
            (_status_error(503), EngineResultStatus.ERROR, "PROVIDER_ERROR"),
            (_status_error(504), EngineResultStatus.ERROR, "PROVIDER_ERROR"),
        ],
    )
    def test_http_status_codes_map_as_claude_maps_them(
        self, exc: Exception, status: EngineResultStatus, code: str
    ) -> None:
        assert _map_gemini_error(exc) == (status, code)

    def test_a_non_json_body_is_still_a_clean_mapping(self) -> None:
        request = httpx.Request("POST", URL)
        response = httpx.Response(429, text="<html>gateway</html>", request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        assert _map_gemini_error(exc) == (EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED")

    def test_timeout_is_checked_before_transport(self) -> None:
        assert _map_gemini_error(httpx.ReadTimeout("t", request=None)) == (
            EngineResultStatus.TIMEOUT,
            "TIMEOUT",
        )
        assert _map_gemini_error(httpx.ConnectError("c", request=None)) == (
            EngineResultStatus.ERROR,
            "PROVIDER_UNREACHABLE",
        )
        assert _map_gemini_error(TimeoutError()) == (EngineResultStatus.TIMEOUT, "TIMEOUT")


class TestTheAdapterItself:
    def test_it_is_registered_parametric_and_gated(self) -> None:
        adapter = engines.ENGINE_REGISTRY[Engine.GEMINI]
        assert adapter.__class__ is GeminiAdapter
        assert Engine.GEMINI in engines.DEFAULT_ENGINES
        assert adapter.version == f"{engines.GEMINI_ANSWER_MODEL}/parametric"
        assert adapter.version.endswith("/parametric")
        assert adapter.key_setting == "google_ai_api_key"
        assert adapter.max_in_flight == engines.GEMINI_MAX_IN_FLIGHT
        assert adapter.min_start_interval is None

    def test_its_per_attempt_bound_fits_under_the_shared_ceiling(self) -> None:
        assert engines.GEMINI_TIMEOUT <= engines.DEFAULT_TIMEOUT
        assert engines.GEMINI_TIMEOUT < engines.ENGINE_CALL_CEILING

    def test_it_is_not_live_without_a_key(self) -> None:
        base = dict(
            _env_file=None,
            environment="test",
            database_url="postgresql+asyncpg://unused/unused",
            redis_url="redis://unused",
            app_secret="test-secret-not-used-in-any-real-environment",
            anthropic_api_key="k",
        )
        assert Engine.GEMINI not in configured_engines(Settings(**base))
        assert Engine.GEMINI in configured_engines(Settings(**base, google_ai_api_key="k"))
        # Order is DEFAULT_ENGINES' order, whichever keys are present.
        assert configured_engines(Settings(**base, google_ai_api_key="k")) == (
            Engine.CLAUDE,
            Engine.CLAUDE_SEARCH,
            Engine.GEMINI,
        )

    async def test_every_budget_is_sent_not_inherited_and_the_key_rides_in_a_header(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: dict = {}
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            sent.update(json.loads(request.content))
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json=_candidate("x"))

        _install(monkeypatch, httpx.MockTransport(handler))
        await GeminiAdapter().ask("q", settings=engine_settings)

        assert seen["url"] == URL
        # In the header, never in the query string, where it would be logged.
        assert "key=" not in seen["url"]
        assert seen["headers"]["x-goog-api-key"] == "test-key-never-sent-anywhere"
        assert sent["contents"] == [{"role": "user", "parts": [{"text": "q"}]}]
        config = sent["generationConfig"]
        assert config["maxOutputTokens"] == engines.GEMINI_MAX_OUTPUT_TOKENS
        # thinkingLevel for 3.x, and never alongside the 2.5-era budget.
        assert config["thinkingConfig"] == {"thinkingLevel": engines.GEMINI_THINKING_LEVEL}
        assert engines.GEMINI_THINKING_LEVEL == "low" == engines.ANSWER_EFFORT
        assert "thinkingBudget" not in json.dumps(sent)
        # Parametric: no grounding tool.
        assert "tools" not in sent

    async def test_a_good_answer_produces_text_and_no_citations(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            httpx.MockTransport(
                lambda r: httpx.Response(200, json=_candidate("Zendesk and Help Scout."))
            ),
        )
        answer = await GeminiAdapter().ask("who?", settings=engine_settings)
        assert answer.status is EngineResultStatus.OK
        assert answer.text == "Zendesk and Help Scout."
        assert answer.citations == []
        assert answer.digest() is not None

    async def test_thought_parts_never_reach_the_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _candidate(
            "", parts=[{"text": "let me think", "thought": True}, {"text": "Help Scout."}]
        )
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        answer = await GeminiAdapter().ask("x", settings=engine_settings)
        assert answer.text == "Help Scout."

    async def test_a_blocked_prompt_and_a_blocked_answer_are_both_refusals(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payloads = [{"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}]
        payloads += [_candidate("", reason) for reason in sorted(GEMINI_BLOCKED)]
        for payload in payloads:
            _install(
                monkeypatch, httpx.MockTransport(lambda r, p=payload: httpx.Response(200, json=p))
            )
            answer = await GeminiAdapter().ask("x", settings=engine_settings)
            assert answer.status is EngineResultStatus.ERROR
            assert answer.error_code == "PROVIDER_REFUSED", payload

    async def test_max_tokens_is_truncated_and_an_unnamed_stop_is_not_an_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            httpx.MockTransport(
                lambda r: httpx.Response(200, json=_candidate("partial", "MAX_TOKENS"))
            ),
        )
        answer = await GeminiAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.TRUNCATED
        assert answer.text == ""

        for reason in ("OTHER", "LANGUAGE", "MALFORMED_FUNCTION_CALL"):
            _install(
                monkeypatch,
                httpx.MockTransport(
                    lambda r, f=reason: httpx.Response(200, json=_candidate("x", f))
                ),
            )
            answer = await GeminiAdapter().ask("x", settings=engine_settings)
            assert answer.error_code == engines.STOP_REASON_UNKNOWN, reason

    async def test_no_candidates_and_no_block_is_not_an_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json={"candidates": []}))
        )
        answer = await GeminiAdapter().ask("x", settings=engine_settings)
        assert answer.error_code == engines.STOP_REASON_UNKNOWN

    async def test_a_provider_failure_returns_a_status_and_never_raises(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(429, json={})))
        answer = await GeminiAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.RATE_LIMITED

    async def test_the_answer_text_never_reaches_a_log_view(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "PROPRIETARY THIRD PARTY PROSE"
        _install(
            monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json=_candidate(secret)))
        )
        answer = await GeminiAdapter().ask("x", settings=engine_settings)
        assert secret not in str(answer.redacted())
        assert secret not in str(answer.digest())

    async def test_a_hanging_call_returns_inside_the_ceiling(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Hanging(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                await asyncio.sleep(3600)
                raise AssertionError("unreachable")

        monkeypatch.setattr(engines, "ENGINE_CALL_CEILING", 0.25)
        _install(monkeypatch, _Hanging())
        answer = await GeminiAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.TIMEOUT

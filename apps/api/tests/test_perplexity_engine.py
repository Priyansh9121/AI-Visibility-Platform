"""The Perplexity adapter — Epic 21, the fourth engine and the second that cites.

Same discipline as test_openai_engine.py: no network, the error mapper pure
and tested directly, the adapter exercised over a faked `httpx` transport so
the request under test is the one `engines.py` genuinely builds. The property
that matters most is the same one too — a Perplexity failure lands on the
statuses and codes Claude's do, never on a vocabulary of its own.

What is NOT here: a real call. No PERPLEXITY_API_KEY existed when this was
written; the response shapes below are the Agent API's documented ones
(api-reference/agent-post, read 2026-09-11), and build-log Epic 21 says so.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC

import httpx
import pytest

from avp_api.config import Settings
from avp_api.models.engine_result import Engine, EngineResultStatus
from avp_api.services import engines
from avp_api.services.engines import (
    CLAUDE_STOPS,
    PERPLEXITY_STOPS,
    PerplexityAdapter,
    _map_error,
    _map_perplexity_error,
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
        perplexity_api_key="test-key-never-sent-anywhere",
    )


def _status_error(code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.perplexity.ai/v1/agent")
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


def _completed(text: str, *, annotations: list | None = None, results: list | None = None) -> dict:
    output = []
    if results is not None:
        output.append({"type": "search_results", "queries": ["q"], "results": results})
    output.append(
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": annotations or []}],
        }
    )
    return {"id": "r", "status": "completed", "model": "perplexity/sonar", "output": output}


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
        perplexity_codes = {
            _map_perplexity_error(exc)[1]
            for exc in (
                _status_error(429),
                _status_error(401),
                _status_error(403),
                _status_error(400),
                _status_error(422),
                _status_error(402),
                _status_error(500),
                httpx.ReadTimeout("t", request=None),
                httpx.ConnectError("c", request=None),
                TimeoutError(),
                RuntimeError("unmapped"),
            )
        }
        perplexity_codes |= {
            classify_stop(r, PERPLEXITY_STOPS)[1]
            for r in ("incomplete", "in_progress", "queued", "never-seen")
        }
        assert perplexity_codes <= claude_codes, (
            f"invented codes: {sorted(perplexity_codes - claude_codes)}"
        )

    @pytest.mark.parametrize(
        ("exc", "status", "code"),
        [
            (_status_error(429), EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED"),
            (_status_error(401), EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"),
            (_status_error(403), EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"),
            (_status_error(400), EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"),
            (_status_error(422), EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"),
            (_status_error(402), EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"),
            (_status_error(503), EngineResultStatus.ERROR, "PROVIDER_ERROR"),
        ],
    )
    def test_http_status_codes_map_as_claude_maps_them(
        self, exc: Exception, status: EngineResultStatus, code: str
    ) -> None:
        assert _map_perplexity_error(exc) == (status, code)

    def test_timeout_is_checked_before_transport(self) -> None:
        # Epic 9.2's lesson, now in ONE shared tail for every httpx adapter.
        assert issubclass(httpx.TimeoutException, httpx.TransportError)
        assert _map_perplexity_error(httpx.ReadTimeout("t", request=None)) == (
            EngineResultStatus.TIMEOUT,
            "TIMEOUT",
        )
        assert _map_perplexity_error(httpx.ConnectError("c", request=None)) == (
            EngineResultStatus.ERROR,
            "PROVIDER_UNREACHABLE",
        )
        assert _map_perplexity_error(TimeoutError()) == (EngineResultStatus.TIMEOUT, "TIMEOUT")


class TestTheAdapterItself:
    def test_it_is_registered_grounded_and_gated(self) -> None:
        adapter = engines.ENGINE_REGISTRY[Engine.PERPLEXITY]
        assert adapter.__class__ is PerplexityAdapter
        assert Engine.PERPLEXITY in engines.DEFAULT_ENGINES
        # Grounded, like claude_search and unlike the three parametric engines.
        assert adapter.version == f"{engines.PERPLEXITY_ANSWER_MODEL}/web_search"
        assert adapter.key_setting == "perplexity_api_key"
        # A start-rate pacer, not an in-flight cap — Epic 21.1's finding.
        assert adapter.max_in_flight is None
        assert adapter.min_start_interval == engines.PERPLEXITY_MIN_START_INTERVAL

    def test_its_per_attempt_bound_fits_under_the_shared_ceiling(self) -> None:
        # The ceiling is the module's one guarantee; a per-attempt timeout
        # above it would make the ceiling a lie for this engine.
        assert engines.PERPLEXITY_TIMEOUT <= engines.DEFAULT_TIMEOUT
        assert engines.PERPLEXITY_TIMEOUT < engines.ENGINE_CALL_CEILING

    def test_it_is_not_live_without_a_key(self) -> None:
        keyed = engine_settings_with(anthropic=True, openai=True, perplexity=False)
        assert Engine.PERPLEXITY not in configured_engines(keyed)
        assert Engine.PERPLEXITY in configured_engines(
            engine_settings_with(anthropic=True, openai=True, perplexity=True)
        )

    async def test_the_request_is_perplexitys_own_model_with_search_and_no_storage(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: dict = {}
        headers: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            sent.update(json.loads(request.content))
            headers.update(request.headers)
            assert request.url.path == "/v1/agent"
            return httpx.Response(200, json=_completed("x"))

        _install(monkeypatch, httpx.MockTransport(handler))
        await PerplexityAdapter().ask("q", settings=engine_settings)

        assert headers["authorization"] == "Bearer test-key-never-sent-anywhere"
        assert sent["model"] == engines.PERPLEXITY_ANSWER_MODEL == "perplexity/sonar"
        assert sent["input"] == "q"
        assert sent["max_output_tokens"] == engines.PERPLEXITY_MAX_OUTPUT_TOKENS
        assert sent["store"] is False
        assert sent["tools"] == [
            {
                "type": "web_search",
                "search_context_size": engines.PERPLEXITY_SEARCH_CONTEXT,
                "max_results": engines.PERPLEXITY_MAX_RESULTS,
            }
        ]
        # Not a preset: `fast` is an OpenAI model behind a Perplexity search.
        assert "preset" not in sent

    async def test_a_good_answer_produces_text_and_cited_sources(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _completed(
            "Zendesk [1] and Help Scout [2].",
            annotations=[
                {"type": "url_citation", "url": "https://www.zendesk.com/pricing", "title": "T"},
                {"type": "url_citation", "url": "https://www.helpscout.com/", "title": "T"},
                {"type": "url_citation", "url": "https://www.zendesk.com/pricing", "title": "dup"},
            ],
            results=[
                {"id": 1, "url": "https://www.zendesk.com/pricing", "title": "x", "snippet": "y"},
                {"id": 2, "url": "https://www.helpscout.com/", "title": "x", "snippet": "y"},
                {"id": 3, "url": "https://example.org/unused", "title": "x", "snippet": "y"},
            ],
        )
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        answer = await PerplexityAdapter().ask("who?", settings=engine_settings)

        assert answer.status is EngineResultStatus.OK
        assert answer.text == "Zendesk [1] and Help Scout [2]."
        # What the answer CITED, deduplicated, in order — not everything it
        # retrieved: the third result was never cited.
        assert [c.url for c in answer.citations] == [
            "https://www.zendesk.com/pricing",
            "https://www.helpscout.com/",
        ]
        assert [c.domain for c in answer.citations] == ["zendesk.com", "helpscout.com"]
        assert [c.position for c in answer.citations] == [1, 2]
        # Titles and snippets are publisher copy and never survive.
        assert not any(hasattr(c, "title") for c in answer.citations)

    async def test_retrieved_sources_stand_in_when_nothing_was_cited_inline(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _completed(
            "Nobody in particular.",
            results=[{"id": 1, "url": "https://www.g2.com/categories/help-desk", "title": "x"}],
        )
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        answer = await PerplexityAdapter().ask("who?", settings=engine_settings)
        assert [c.domain for c in answer.citations] == ["g2.com"]

    async def test_an_incomplete_run_is_truncated_not_an_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _completed("partial…")
        payload["status"] = "incomplete"
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        answer = await PerplexityAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.TRUNCATED
        assert answer.error_code == engines.ANSWER_TRUNCATED
        assert answer.text == ""  # a digest of a prefix would lie on re-scan

    async def test_a_failed_run_on_a_200_is_a_status_never_an_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for error, expected in (
            ({"type": "server_error", "code": "upstream_failed", "message": "x"}, "PROVIDER_ERROR"),
            (
                {"type": "rate_limit", "code": "rate_limited", "message": "x"},
                "PROVIDER_RATE_LIMITED",
            ),
        ):
            payload = {"id": "r", "status": "failed", "output": [], "error": error}
            _install(
                monkeypatch, httpx.MockTransport(lambda r, p=payload: httpx.Response(200, json=p))
            )
            answer = await PerplexityAdapter().ask("x", settings=engine_settings)
            assert answer.error_code == expected
            assert answer.text == ""

    async def test_a_status_it_has_never_seen_is_not_an_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _completed("x")
        payload["status"] = "something_new"
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        answer = await PerplexityAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.ERROR
        assert answer.error_code == engines.STOP_REASON_UNKNOWN

    async def test_a_provider_failure_returns_a_status_and_never_raises(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(429, json={})))
        answer = await PerplexityAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.RATE_LIMITED
        assert answer.text == ""

    async def test_the_answer_text_never_reaches_a_log_view(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "PROPRIETARY THIRD PARTY PROSE"
        _install(
            monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json=_completed(secret)))
        )
        answer = await PerplexityAdapter().ask("x", settings=engine_settings)
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
        answer = await PerplexityAdapter().ask("x", settings=engine_settings)
        assert answer.status is EngineResultStatus.TIMEOUT


class TestTheGate:
    async def test_perplexity_starts_are_spaced_and_calls_still_overlap(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pacer — Epic 21.1. Six concurrent asks: starts at least the
        interval apart, yet several in flight at once, because the vendor's
        bucket limits the start rate and nothing else."""
        import time

        starts: list[float] = []
        in_flight = 0
        peak = 0

        class _Timing(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                nonlocal in_flight, peak
                starts.append(time.monotonic())
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0.2)
                in_flight -= 1
                return httpx.Response(200, json=_completed("x"))

        _install(monkeypatch, _Timing())
        monkeypatch.setattr(engines, "_PACERS", {})
        monkeypatch.setattr(engines, "PERPLEXITY_MIN_START_INTERVAL", 0.05)
        monkeypatch.setattr(engines.PerplexityAdapter, "min_start_interval", 0.05)
        await asyncio.gather(
            *(
                engines.ask_all("q", engines=(Engine.PERPLEXITY,), settings=engine_settings)
                for _ in range(6)
            )
        )
        ordered = sorted(starts)
        gaps = [b - a for a, b in zip(ordered[:-1], ordered[1:], strict=True)]
        assert min(gaps) >= 0.045, gaps
        assert peak > 1, "spacing starts must not serialise the calls"

    async def test_gemini_never_exceeds_its_in_flight_cap(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        in_flight = 0
        peak = 0

        class _Counting(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0.01)
                in_flight -= 1
                return httpx.Response(
                    200,
                    json={
                        "candidates": [
                            {"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}
                        ]
                    },
                )

        _install(monkeypatch, _Counting())
        monkeypatch.setattr(engines, "_GATES", {})
        settings = engine_settings.model_copy(update={"google_ai_api_key": "k"})
        await asyncio.gather(
            *(engines.ask_all("q", engines=(Engine.GEMINI,), settings=settings) for _ in range(20))
        )
        assert peak == engines.GEMINI_MAX_IN_FLIGHT

    def test_ungated_engines_have_no_gate(self) -> None:
        for engine in (Engine.CLAUDE, Engine.CLAUDE_SEARCH, Engine.CHATGPT):
            assert engines.ENGINE_REGISTRY[engine].max_in_flight is None
            assert engines.ENGINE_REGISTRY[engine].min_start_interval is None
            assert engines._gate(engine) is None
            assert engines._pacer(engine) is None


def engine_settings_with(*, anthropic: bool, openai: bool, perplexity: bool) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        anthropic_api_key="k" if anthropic else None,
        openai_api_key="k" if openai else None,
        perplexity_api_key="k" if perplexity else None,
    )


class TestRetryAfter:
    """One retry on a 429 that names its wait — 2026-09-12, the fix Epic
    21.1 deferred. Inside the ceiling, through the pacer, never twice."""

    def _sleeps(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        # Catches the retry's wait AND the pacer's spacing sleep, both of
        # which are `engines.asyncio.sleep`.
        monkeypatch.setattr(engines.asyncio, "sleep", fake_sleep)
        return slept

    async def test_a_429_with_a_wait_is_retried_once_and_then_answers(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept = self._sleeps(monkeypatch)
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, json={}, headers={"Retry-After": "1"})
            return httpx.Response(200, json=_completed("Zendesk."))

        _install(monkeypatch, httpx.MockTransport(handler))
        answer = await PerplexityAdapter().ask("q", settings=engine_settings)

        assert answer.status is EngineResultStatus.OK
        assert answer.text == "Zendesk."
        assert len(calls) == 2
        assert 1.0 in slept, "the vendor's own wait was honoured"

    async def test_a_second_429_is_recorded_not_retried_again(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._sleeps(monkeypatch)
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, json={}, headers={"Retry-After": "1"})

        _install(monkeypatch, httpx.MockTransport(handler))
        answer = await PerplexityAdapter().ask("q", settings=engine_settings)

        assert answer.status is EngineResultStatus.RATE_LIMITED
        assert answer.error_code == "PROVIDER_RATE_LIMITED"
        assert len(calls) == 2

    async def test_a_429_without_a_wait_is_recorded_at_once(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept = self._sleeps(monkeypatch)
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, json={})

        _install(monkeypatch, httpx.MockTransport(handler))
        answer = await PerplexityAdapter().ask("q", settings=engine_settings)

        assert answer.status is EngineResultStatus.RATE_LIMITED
        assert len(calls) == 1
        assert slept == []

    async def test_a_wait_that_cannot_fit_the_ceiling_is_not_taken(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept = self._sleeps(monkeypatch)
        calls: list[int] = []
        too_long = str(int(engines.PERPLEXITY_RETRY_AFTER_MAX) + 1)

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, json={}, headers={"Retry-After": too_long})

        _install(monkeypatch, httpx.MockTransport(handler))
        answer = await PerplexityAdapter().ask("q", settings=engine_settings)

        assert answer.status is EngineResultStatus.RATE_LIMITED
        assert len(calls) == 1
        assert slept == []

    def test_the_longest_wait_still_leaves_room_for_the_retry_itself(self) -> None:
        # wait + one more attempt at PERPLEXITY_TIMEOUT must fit the ceiling
        # from a call that already spent one attempt getting the 429.
        assert engines.PERPLEXITY_RETRY_AFTER_MAX + 2 * engines.PERPLEXITY_TIMEOUT <= (
            engines.ENGINE_CALL_CEILING
        )
        assert engines.PERPLEXITY_RETRY_AFTER_MAX > 17, "the observed 11-17s overload waits fit"

    def test_retry_after_is_read_as_seconds_or_a_date(self) -> None:
        from datetime import datetime, timedelta
        from email.utils import format_datetime

        assert engines._retry_after_seconds("1") == 1.0
        assert engines._retry_after_seconds(" 2.5 ") == 2.5
        assert engines._retry_after_seconds(None) is None
        assert engines._retry_after_seconds("soon") is None
        assert engines._retry_after_seconds("-3") is None
        later = format_datetime(datetime.now(UTC) + timedelta(seconds=30))
        parsed = engines._retry_after_seconds(later)
        assert parsed is not None and 25 < parsed <= 30

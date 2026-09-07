"""An answer the engine did not finish is not an answer.

Until the API key discipline audit (2026-09-07) both adapters read the
vendor's stop reason for exactly one value — "refusal" — and treated everything
else as a finished answer. A response cut off by the token budget therefore
reached `extract_facts` as OK; the brand was absent from the truncated prefix,
and the row was recorded ANSWERED_NO_MENTION, which scoring counts against the
mention rate. A billed call producing a fabricated absence, on a scan that
still read SUCCEEDED.

The property under test is the ALLOWLIST: a complete answer is one whose stop
reason is explicitly known to mean "finished", and everything else — including
a value this code has never seen — is not an answer. The classifier is pure
and is tested directly, including against the pinned SDK's own vocabulary so
an SDK bump that adds a stop reason fails here rather than in a report.

The Claude client under test is the real one with a faked transport, as in
test_engine_timeout.py. The OpenAI adapter is exercised over a faked httpx
transport, as in test_openai_engine.py. Both therefore run the code
`engines.py` genuinely builds.
"""

from __future__ import annotations

from typing import get_args

import anthropic
import httpx
import pytest
from anthropic.types import StopReason

from avp_api.config import Settings
from avp_api.models.engine_result import EngineResultStatus
from avp_api.services import engines
from avp_api.services.engines import (
    ANSWER_PAUSED,
    ANSWER_TRUNCATED,
    CLAUDE_STOPS,
    OPENAI_STOPS,
    STOP_REASON_UNKNOWN,
    ChatGptAdapter,
    ClaudeParametricAdapter,
    ClaudeSearchAdapter,
    classify_stop,
)

# What a truncated answer looks like: the prefix of a sentence that, finished,
# would have named the subject. The whole defect is that the brand is absent
# from THIS and present in the answer the engine was going to give.
PARTIAL = "Zendesk is popular for larger teams, while Help Sc"

# The five values openai-python's `chat_completion.py` documents for
# `finish_reason`, read on 2026-09-07. No SDK is pinned on that path, so this
# list is the test's own record of the contract the adapter was written to.
OPENAI_FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter", "function_call")


@pytest.fixture
def engine_settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        anthropic_api_key="test-key-never-sent-anywhere",
        openai_api_key="test-key-never-sent-anywhere",
    )


def _claude_message(stop_reason: str, *, content: list[dict] | None = None) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": engines.ANSWER_MODEL,
        "content": content if content is not None else [{"type": "text", "text": PARTIAL}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def _install_claude(monkeypatch: pytest.MonkeyPatch, body: dict) -> None:
    """Point the adapter's real Anthropic client at a transport returning `body`."""
    real_cls = anthropic.AsyncAnthropic

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    def factory(**kwargs):  # noqa: ANN003, ANN202
        transport = httpx.MockTransport(handler)
        return real_cls(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(engines.anthropic, "AsyncAnthropic", factory)


def _install_openai(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    real = httpx.AsyncClient

    def factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        return real(*args, **kwargs)

    monkeypatch.setattr(engines.httpx, "AsyncClient", factory)


class TestTheClassifierIsAnAllowlist:
    def test_every_stop_reason_the_pinned_sdk_knows_has_been_sorted(self) -> None:
        """The SDK-bump guard.

        Read the vocabulary from the installed SDK rather than restating it, so
        a new `StopReason` member appears here as a failure that names it — and
        somebody decides which bucket it belongs in, instead of it landing on
        STOP_REASON_UNKNOWN in production and being noticed in a report.
        """
        for reason in get_args(StopReason):
            if reason == "refusal":
                continue  # handled before classification, with its own code
            verdict = classify_stop(reason, CLAUDE_STOPS)
            assert verdict is None or verdict[1] != STOP_REASON_UNKNOWN, (
                f"the pinned SDK knows stop_reason {reason!r} and this module does not"
            )

    def test_every_documented_openai_finish_reason_has_been_sorted(self) -> None:
        for reason in OPENAI_FINISH_REASONS:
            if reason == "content_filter":
                continue  # the refusal signal, handled before classification
            verdict = classify_stop(reason, OPENAI_STOPS)
            assert verdict is None or verdict[1] != STOP_REASON_UNKNOWN, reason

    def test_the_allowlist_is_exactly_the_finished_answers(self) -> None:
        assert CLAUDE_STOPS.complete == {"end_turn", "stop_sequence"}
        assert OPENAI_STOPS.complete == {"stop"}
        for vocabulary in (CLAUDE_STOPS, OPENAI_STOPS):
            for reason in vocabulary.complete:
                assert classify_stop(reason, vocabulary) is None

    @pytest.mark.parametrize("reason", [None, "", "something_new", "END_TURN"])
    def test_a_value_it_has_never_seen_is_not_an_answer(self, reason: str | None) -> None:
        """The allowlist property. A blocklist would let all four through."""
        for vocabulary in (CLAUDE_STOPS, OPENAI_STOPS):
            assert classify_stop(reason, vocabulary) == (
                EngineResultStatus.ERROR,
                STOP_REASON_UNKNOWN,
            )

    def test_a_refusal_could_never_quietly_become_an_answer(self) -> None:
        """Both adapters catch refusals BEFORE classifying. This asserts what
        happens if that check were ever removed: the refusal signals are in no
        vocabulary, so they fall to "not an answer" rather than to OK."""
        assert classify_stop("refusal", CLAUDE_STOPS)[0] is EngineResultStatus.ERROR
        assert classify_stop("content_filter", OPENAI_STOPS)[0] is EngineResultStatus.ERROR

    def test_the_same_event_gets_the_same_code_from_both_vendors(self) -> None:
        # The property test_openai_engine.py guards for error codes, extended to
        # stop reasons: one vocabulary, read by one scoring path.
        assert classify_stop("max_tokens", CLAUDE_STOPS) == classify_stop("length", OPENAI_STOPS)
        for claude_reason, openai_reason in (
            ("pause_turn", "tool_calls"),
            ("tool_use", "function_call"),
        ):
            assert classify_stop(claude_reason, CLAUDE_STOPS) == classify_stop(
                openai_reason, OPENAI_STOPS
            )

    def test_the_search_cap_is_not_a_stop_reason(self) -> None:
        """`max_uses_exceeded` is a tool-result error code, not a stop reason.

        Hitting SEARCH_MAX_USES yields a complete, less-grounded answer with
        `stop_reason == "end_turn"`. It must never be sorted into truncation —
        pinned here so the distinction cannot be lost in a future edit of the
        vocabularies.
        """
        from anthropic.types import WebSearchToolResultErrorCode

        assert "max_uses_exceeded" in get_args(WebSearchToolResultErrorCode)
        assert "max_uses_exceeded" not in get_args(StopReason)
        for bucket in (CLAUDE_STOPS.truncated, CLAUDE_STOPS.paused, CLAUDE_STOPS.complete):
            assert "max_uses_exceeded" not in bucket


class TestClaudeAdapters:
    @pytest.mark.parametrize(
        ("stop_reason", "status", "code"),
        [
            ("end_turn", EngineResultStatus.OK, None),
            ("stop_sequence", EngineResultStatus.OK, None),
            ("max_tokens", EngineResultStatus.TRUNCATED, ANSWER_TRUNCATED),
            ("model_context_window_exceeded", EngineResultStatus.TRUNCATED, ANSWER_TRUNCATED),
            ("pause_turn", EngineResultStatus.PAUSED, ANSWER_PAUSED),
            ("tool_use", EngineResultStatus.PAUSED, ANSWER_PAUSED),
            ("refusal", EngineResultStatus.ERROR, "PROVIDER_REFUSED"),
            ("something_new", EngineResultStatus.ERROR, STOP_REASON_UNKNOWN),
        ],
    )
    @pytest.mark.parametrize("adapter_cls", [ClaudeParametricAdapter, ClaudeSearchAdapter])
    async def test_the_stop_reason_decides_the_status(
        self,
        adapter_cls: type,
        stop_reason: str,
        status: EngineResultStatus,
        code: str | None,
        engine_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_claude(monkeypatch, _claude_message(stop_reason))

        answer = await adapter_cls().ask("who is best?", settings=engine_settings)

        assert answer.status is status
        assert answer.error_code == code
        if status is EngineResultStatus.OK:
            assert answer.text == PARTIAL
            assert answer.ok
        else:
            # Not an answer: no text to extract from, no digest to compare
            # against next scan, and `ok` is False so extraction stops at the
            # door rather than recording an absence.
            assert answer.text == ""
            assert answer.digest() is None
            assert not answer.ok

    async def test_the_partial_text_never_leaks_through_a_log_view(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_claude(monkeypatch, _claude_message("max_tokens"))
        answer = await ClaudeParametricAdapter().ask("q", settings=engine_settings)
        assert PARTIAL not in str(answer.redacted())
        assert answer.redacted()["status"] == "truncated"
        assert answer.redacted()["chars"] == 0

    async def test_hitting_the_search_cap_is_still_a_complete_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The grounded engine exhausting SEARCH_MAX_USES returns `end_turn`
        with an error block among its tool results. That is a finished, less
        grounded answer — OK, with the error block skipped — and must not be
        read as truncation. Locks the decision test_the_search_cap_is_not_a_stop_reason
        pins at the vocabulary level, at the adapter."""
        body = _claude_message(
            "end_turn",
            content=[
                {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                 "input": {"query": "help desk software"}},
                {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                 "content": {"type": "web_search_tool_result_error",
                             "error_code": "max_uses_exceeded"}},
                {"type": "text", "text": "Zendesk and Help Scout are the usual picks."},
            ],
        )
        _install_claude(monkeypatch, body)

        answer = await ClaudeSearchAdapter().ask("q", settings=engine_settings)

        assert answer.status is EngineResultStatus.OK
        assert answer.error_code is None
        assert "Help Scout" in answer.text
        assert answer.citations == []


class TestChatGptAdapter:
    @pytest.mark.parametrize(
        ("finish_reason", "status", "code"),
        [
            ("stop", EngineResultStatus.OK, None),
            ("length", EngineResultStatus.TRUNCATED, ANSWER_TRUNCATED),
            ("tool_calls", EngineResultStatus.PAUSED, ANSWER_PAUSED),
            ("function_call", EngineResultStatus.PAUSED, ANSWER_PAUSED),
            ("content_filter", EngineResultStatus.ERROR, "PROVIDER_REFUSED"),
            ("something_new", EngineResultStatus.ERROR, STOP_REASON_UNKNOWN),
            (None, EngineResultStatus.ERROR, STOP_REASON_UNKNOWN),
        ],
    )
    async def test_the_finish_reason_decides_the_status(
        self,
        finish_reason: str | None,
        status: EngineResultStatus,
        code: str | None,
        engine_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        choice: dict = {"message": {"role": "assistant", "content": PARTIAL}}
        if finish_reason is not None:
            choice["finish_reason"] = finish_reason
        _install_openai(monkeypatch, {"choices": [choice]})

        answer = await ChatGptAdapter().ask("who is best?", settings=engine_settings)

        assert answer.status is status
        assert answer.error_code == code
        if status is EngineResultStatus.OK:
            assert answer.text == PARTIAL
        else:
            assert answer.text == ""
            assert answer.digest() is None
            assert not answer.ok

    async def test_the_exact_defect_the_audit_found(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`finish_reason: length` with an EMPTY body — a reasoning model that
        spent the whole `max_completion_tokens` budget before writing a visible
        token. This used to come back OK with empty text, which `extract_facts`
        turned into ANSWERED_NO_MENTION: a billed call recorded as "ChatGPT
        answered and did not name you"."""
        _install_openai(
            monkeypatch,
            {"choices": [{"finish_reason": "length", "message": {"content": ""}}]},
        )
        answer = await ChatGptAdapter().ask("q", settings=engine_settings)
        assert answer.status is EngineResultStatus.TRUNCATED
        assert answer.error_code == ANSWER_TRUNCATED
        assert not answer.ok

    async def test_an_empty_choices_list_is_not_an_answer(
        self, engine_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_openai(monkeypatch, {"choices": []})
        answer = await ChatGptAdapter().ask("q", settings=engine_settings)
        assert answer.status is EngineResultStatus.ERROR
        assert answer.error_code == STOP_REASON_UNKNOWN
        assert answer.text == ""

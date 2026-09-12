"""The grounded Claude engine's citations mean what the answer cited —
scoring-spec v2.2, 2026-09-12.

The adapter calls search directly (`allowed_callers: ["direct"]`), which is
the only path on which the answer's own `web_search_result_location`
citations come back; `_extract_citations` reads those first and falls back
to the retrieved result blocks only when nothing was cited inline —
`_read_agent_output`'s rule for Perplexity, applied to Claude. The version
string carries `/direct` so a stored row says which definition it holds.
"""

from __future__ import annotations

import json

import anthropic
import httpx
import pytest

from avp_api.config import Settings
from avp_api.models.engine_result import EngineResultStatus
from avp_api.services import engines
from avp_api.services.engines import ClaudeSearchAdapter


def _message(content: list[dict]) -> dict:
    return {
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": engines.ANSWER_MODEL, "content": content,
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def _install(monkeypatch: pytest.MonkeyPatch, body: dict, sent: dict | None = None) -> None:
    real_cls = anthropic.AsyncAnthropic

    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.update(json.loads(request.content))
        return httpx.Response(200, json=body)

    def factory(**kwargs):  # noqa: ANN003, ANN202
        return real_cls(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(engines.anthropic, "AsyncAnthropic", factory)


def _result_block(*urls: str) -> dict:
    return {
        "type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
        "content": [
            {"type": "web_search_result", "url": u, "title": "publisher copy",
             "encrypted_content": "x", "page_age": None}
            for u in urls
        ],
    }


def _cited_text(text: str, *urls: str) -> dict:
    return {
        "type": "text", "text": text,
        "citations": [
            {"type": "web_search_result_location", "url": u, "title": "publisher copy",
             "encrypted_index": "x", "cited_text": "publisher copy"}
            for u in urls
        ],
    }


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None, environment="test",
        database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        anthropic_api_key="test-key-never-sent-anywhere",
    )


class TestTheRequest:
    async def test_search_is_called_directly_and_the_version_says_so(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: dict = {}
        _install(monkeypatch, _message([{"type": "text", "text": "x"}]), sent)
        answer = await ClaudeSearchAdapter().ask("q", settings=settings)

        (tool,) = sent["tools"]
        assert tool["type"] == "web_search_20260209"
        assert tool["allowed_callers"] == ["direct"]
        assert tool["max_uses"] == engines.SEARCH_MAX_USES
        assert "response_inclusion" not in tool
        assert answer.engine_version.endswith("/web_search_20260209/direct")


class TestWhatACitationIs:
    async def test_the_answers_own_citations_come_first_not_everything_retrieved(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = _message([
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
             "input": {"query": "help desk"}},
            _result_block("https://www.zendesk.com/a", "https://www.helpscout.com/b",
                          "https://example.org/never-cited"),
            _cited_text("Zendesk ", "https://www.zendesk.com/a"),
            {"type": "text", "text": "and "},
            _cited_text("Help Scout.", "https://www.helpscout.com/b", "https://www.zendesk.com/a"),
        ])
        _install(monkeypatch, body)
        answer = await ClaudeSearchAdapter().ask("q", settings=settings)

        assert answer.status is EngineResultStatus.OK
        assert [c.url for c in answer.citations] == [
            "https://www.zendesk.com/a", "https://www.helpscout.com/b",
        ]
        assert [c.position for c in answer.citations] == [1, 2]
        assert {c.domain for c in answer.citations} == {"zendesk.com", "helpscout.com"}

    async def test_retrieved_results_stand_in_only_when_nothing_was_cited_inline(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = _message([
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
             "input": {"query": "help desk"}},
            _result_block("https://www.zendesk.com/a", "https://www.helpscout.com/b",
                          "https://www.zendesk.com/a"),
            {"type": "text", "text": "Zendesk and Help Scout, from memory of the results."},
        ])
        _install(monkeypatch, body)
        answer = await ClaudeSearchAdapter().ask("q", settings=settings)

        assert [c.url for c in answer.citations] == [
            "https://www.zendesk.com/a", "https://www.helpscout.com/b",
        ]

    async def test_an_answer_that_searched_nothing_cites_nothing(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _message([{"type": "text", "text": "From memory."}]))
        answer = await ClaudeSearchAdapter().ask("q", settings=settings)
        assert answer.citations == []

    def test_neither_source_reads_publisher_copy(self) -> None:
        """The facts-only rule, checked at the source of all three readers."""
        import inspect

        for fn in (engines._extract_citations, engines._urls_cited_inline, engines._urls_retrieved):
            source = inspect.getsource(fn)
            for forbidden in ('"title"', "'title'", ".title", "page_age", "encrypted",
                              "cited_text"):
                assert forbidden not in source, f"{fn.__name__} reads {forbidden}"

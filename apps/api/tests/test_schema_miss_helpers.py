"""A schema miss from `messages.parse` is a failed call, not a crashed scan (checklist 3.76).

`messages.parse` validates the model's whole answer inside the SDK and raises pydantic's
`ValidationError`, which is not an `anthropic.APIError`. `classify` and `fix_generator` catch it;
`classify_sentiment`, `generate_prompts` and `run_seed_prompt` did not, so one malformed answer
escaped them and failed a scan that had already been paid for. Each now takes the path a provider
error takes: sentiment `(None, None)`, the deterministic prompt set, a failed seed result.

The log line names the field, the error type and the length, never the model's text.
"""

from __future__ import annotations

import json

import anthropic
import pytest
import structlog
from pydantic import TypeAdapter

from avp_api.models.engine_result import Engine
from avp_api.services import cocitation, extraction, prompts
from avp_api.services.engines import EngineAnswer

MODEL_TEXT = "MODEL-AUTHORED-PROSE-THAT-MUST-NEVER-BE-LOGGED " * 10


def parse_that_misses(model: type, payload: dict) -> None:  # noqa: ANN001
    """Make `messages.parse` do what the SDK does: validate the whole document, and fail."""

    async def fake(**kwargs):  # noqa: ANN003, ARG001
        TypeAdapter(model).validate_json(json.dumps(payload))
        raise AssertionError("the schema should have rejected that payload")

    return fake


def install(monkeypatch: pytest.MonkeyPatch, fake) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        anthropic.resources.messages.AsyncMessages, "parse", lambda self, **kw: fake(**kw)
    )


def assert_one_miss_logged(events: list[dict], event: str, field: str) -> None:
    (miss,) = [e for e in events if e["event"] == event]
    (error,) = miss["errors"]
    assert error["field"] == field
    assert error["length"] == len(MODEL_TEXT)
    # No log line of the call carries what the model wrote.
    assert MODEL_TEXT.strip() not in repr(events)
    # The call was still paid for: its `llm.call` line says it got no usable answer.
    (call,) = [e for e in events if e["event"] == "llm.call"]
    assert call["ok"] is False
    assert call["error"] == "ValidationError"


class TestSentiment:
    async def test_a_schema_miss_is_no_sentiment_not_a_crash(self, settings, monkeypatch) -> None:  # noqa: ANN001
        install(
            monkeypatch,
            parse_that_misses(
                extraction.SentimentJudgement, {"sentiment": MODEL_TEXT, "confidence": 0.5}
            ),
        )
        answer = EngineAnswer(
            engine=Engine.CLAUDE, engine_version="t", prompt_text="p", text="Acme is fine."
        )

        with structlog.testing.capture_logs() as events:
            result = await extraction.classify_sentiment(
                answer, subject_name="Acme", settings=settings
            )

        # The same outcome as a provider error: the answer is kept, sentiment is not set.
        assert result == (None, None)
        assert_one_miss_logged(events, "extraction.schema_violation", "sentiment")


class TestPromptGeneration:
    async def test_a_schema_miss_is_an_unavailable_prompt_phase_not_a_template_set(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        # A bad intent is still a schema miss (an over-long question no longer is: F5).
        install(
            monkeypatch,
            parse_that_misses(
                prompts.GeneratedPromptSet,
                {"prompts": [{"text": "who is best", "intent": MODEL_TEXT * 3}]},
            ),
        )
        with structlog.testing.capture_logs() as events:
            result = await prompts.generate_prompts(
                brand_name="Acme", domain="acme.example", industry="widgets", niche=None,
                settings=settings,
            )

        assert result.unavailable == "schema_violation"
        assert result.prompts == []
        (miss,) = [e for e in events if e["event"] == "prompts.schema_violation"]
        assert miss["errors"][0]["field"] == "prompts.0.intent"
        assert miss["errors"][0]["length"] == len(MODEL_TEXT * 3)
        assert MODEL_TEXT.strip() not in repr(events)
        (call,) = [e for e in events if e["event"] == "llm.call"]
        assert (call["ok"], call["error"]) == (False, "ValidationError")


class TestSeedPrompt:
    async def test_a_schema_miss_is_a_failed_result_not_a_crash(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        install(
            monkeypatch,
            parse_that_misses(
                cocitation.CoCitationAnswer,
                {"brands": [{"name": MODEL_TEXT * 3}], "subject_named": False},
            ),
        )
        with structlog.testing.capture_logs() as events:
            result = await cocitation.run_seed_prompt(
                "who competes with Acme?", subject_brand="Acme", settings=settings
            )

        # Same shape as a provider error: not ok, a code, no hits.
        assert result.ok is False
        assert result.error_code == "PROVIDER_SCHEMA_VIOLATION"
        assert result.hits == []
        (miss,) = [e for e in events if e["event"] == "cocitation.schema_violation"]
        assert miss["errors"][0]["field"] == "brands.0.name"
        assert miss["errors"][0]["length"] == len(MODEL_TEXT * 3)
        assert MODEL_TEXT.strip() not in repr(events)
        (call,) = [e for e in events if e["event"] == "llm.call"]
        assert (call["ok"], call["error"]) == (False, "ValidationError")

    async def test_one_miss_among_the_seed_prompts_leaves_the_others_answered(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """The detection phase runs the seed prompts together: one miss must not raise out."""
        bad = {"brands": [{"name": MODEL_TEXT * 3}], "subject_named": False}
        good = {"brands": [{"name": "Rival", "domain": "rival.example"}], "subject_named": True}

        async def fake(**kwargs):  # noqa: ANN003
            question = kwargs["messages"][0]["content"]
            payload = bad if "second" in question else good
            adapter = TypeAdapter(cocitation.CoCitationAnswer)
            return _Parsed(adapter.validate_json(json.dumps(payload)))

        # `conftest.py` fakes `run_seed_prompts` for every test; this one needs the real gather.
        monkeypatch.undo()
        install(monkeypatch, fake)
        results = await cocitation.run_seed_prompts(
            ["first question", "second question", "third question"],
            subject_brand="Acme", settings=settings,
        )

        assert [r.ok for r in results] == [True, False, True]
        assert results[1].error_code == "PROVIDER_SCHEMA_VIOLATION"
        assert [h.name for h in results[0].hits] == ["Rival"]


class _Parsed:
    """The part of the SDK's `ParsedMessage` the helper reads."""

    stop_reason = "end_turn"
    usage = None
    model = None
    content: list = []  # noqa: RUF012

    def __init__(self, parsed_output) -> None:  # noqa: ANN001
        self.parsed_output = parsed_output

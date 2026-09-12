"""The internal analysis calls are not the product — the cost brief, 2026-09-11.

A report that says "here's what Claude tells your buyers" has to mean the
model a buyer would talk to, so the five answer-engine adapters' models and
their search budget are the product and do not move for cost. Sentiment,
classification and fix generation are analysis the pipeline runs on top of
those answers, and the cost brief moved sentiment — the highest-volume paid
call in a scan — to the cheapest current model after a held-out comparison
(build log, the cost brief entry). These tests keep the two categories apart.
"""

from __future__ import annotations

from decimal import Decimal

import anthropic

from avp_api.models.engine_result import Engine, Sentiment
from avp_api.services import engines, extraction
from avp_api.services.engines import EngineAnswer
from avp_api.services.extraction import SentimentJudgement


class TestTheProductLineDoesNotMove:
    def test_the_answer_engines_keep_their_models(self) -> None:
        """Pinned as strings, deliberately: a cost pass must not touch these."""
        assert engines.ANSWER_MODEL == "claude-opus-5"
        assert engines.OPENAI_ANSWER_MODEL == "gpt-5.5"
        assert engines.PERPLEXITY_ANSWER_MODEL == "perplexity/sonar"
        assert engines.GEMINI_ANSWER_MODEL == "gemini-3.8-flash"
        assert engines.SEARCH_MAX_USES == 4

    def test_sentiment_is_not_graded_by_the_engine_it_grades(self) -> None:
        assert extraction.SENTIMENT_MODEL == "claude-haiku-4-5-20251001"
        assert extraction.SENTIMENT_MODEL != engines.ANSWER_MODEL


class TestSentimentRequestShape:
    async def test_the_request_carries_no_effort_and_no_thinking(
        self, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """Haiku 4.5 answers 400 to `output_config.effort`; the request must
        not carry the Opus-era effort setting, and asks for no thinking."""
        captured: dict[str, object] = {}

        class _Fake:
            stop_reason = "end_turn"
            parsed_output = SentimentJudgement(sentiment=Sentiment.NEGATIVE, confidence=0.85)

        async def fake(self, **kwargs):  # noqa: ANN001, ANN003
            captured.update(kwargs)
            return _Fake()

        monkeypatch.setattr(anthropic.resources.messages.AsyncMessages, "parse", fake)
        answer = EngineAnswer(
            engine=Engine.CLAUDE, engine_version="test", prompt_text="q",
            text="Help Scout's reporting is thin; teams needing SLAs should look elsewhere.",
        )

        result = await extraction.classify_sentiment(
            answer, subject_name="Help Scout", settings=settings
        )

        assert captured["model"] == extraction.SENTIMENT_MODEL
        assert "output_config" not in captured
        assert "thinking" not in captured
        assert captured["output_format"] is SentimentJudgement
        assert captured["system"] == extraction.SENTIMENT_SYSTEM
        assert result == (Sentiment.NEGATIVE, Decimal("0.850"))

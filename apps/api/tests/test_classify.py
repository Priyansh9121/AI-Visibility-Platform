"""Classification policy.

`decide()` is the branch that keeps a guess from being stored as a result, so
it is tested exhaustively and without a network call. The live end-to-end check
against real sites is a separate, manual verification (see docs/build-log.md).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from avp_api.services.classify import (
    CONFIDENCE_THRESHOLD,
    MIN_WORDS_FOR_CLASSIFICATION,
    ClassificationOutcome,
    IndustryClassification,
    build_prompt,
    classify,
    decide,
)
from avp_api.services.crawl import CrawlResult, CrawlSignals


def _parsed(**overrides) -> IndustryClassification:  # noqa: ANN003
    base = {
        "is_classifiable": True,
        "industry": "dental practice",
        "niche": "cosmetic and implant dentistry",
        "brand_name": "Northaven Dental",
        "confidence": "high",
        "confidence_score": 0.92,
        "rationale": "The site describes clinical dental services and appointments.",
    }
    base.update(overrides)
    return IndustryClassification(**base)


def _crawl(text: str = "word " * 200, ok: bool = True, error: str | None = None) -> CrawlResult:
    signals = CrawlSignals(
        final_url="https://northaven-dental.example",
        registrable_domain="northaven-dental.example",
        word_count=len(text.split()),
    )
    return CrawlResult(signals=signals, text_extract=text, ok=ok, error_code=error)


class TestDecide:
    def test_high_confidence_is_classified(self) -> None:
        out = decide(_parsed())
        assert out.status == "classified"
        assert out.industry == "dental practice"
        assert out.niche == "cosmetic and implant dentistry"
        assert out.brand_name == "Northaven Dental"
        assert out.confidence_score == Decimal("0.920")

    def test_below_threshold_stores_no_industry(self) -> None:
        """The rule the whole epic hinges on.

        A wrong industry silently poisons Epic 3 and Epic 4, and neither can
        detect that its input was wrong. So an uncertain call stores nothing.
        """
        out = decide(_parsed(confidence_score=0.55, confidence="low"))
        assert out.status == "ambiguous"
        assert out.industry is None
        assert out.niche is None
        assert out.reason_code == "LOW_CONFIDENCE"

    def test_ambiguous_still_keeps_brand_name_and_score(self) -> None:
        """Only the industry was uncertain — the rest is still useful."""
        out = decide(_parsed(confidence_score=0.4))
        assert out.brand_name == "Northaven Dental"
        assert out.confidence_score == Decimal("0.400")

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.699, "ambiguous"),
            (0.70, "classified"),
            (0.701, "classified"),
        ],
    )
    def test_threshold_boundary_is_exact(self, score: float, expected: str) -> None:
        assert float(CONFIDENCE_THRESHOLD) == 0.70
        assert decide(_parsed(confidence_score=score)).status == expected

    def test_not_a_business_site_is_unclassifiable(self) -> None:
        out = decide(_parsed(is_classifiable=False, industry=None, confidence_score=0.95))
        assert out.status == "unclassifiable"
        assert out.reason_code == "NOT_A_BUSINESS_SITE"
        assert out.industry is None

    def test_classifiable_true_but_no_industry_is_still_refused(self) -> None:
        """Guards against a contradictory response being stored as a result."""
        out = decide(_parsed(industry=None, confidence_score=0.99))
        assert out.status == "unclassifiable"
        assert out.industry is None

    def test_industry_is_normalised(self) -> None:
        out = decide(_parsed(industry="  Dental Practice  "))
        assert out.industry == "dental practice"

    def test_score_quantises_to_three_places(self) -> None:
        """Must round-trip exactly against NUMERIC(4,3)."""
        out = decide(_parsed(confidence_score=0.8266666))
        assert out.confidence_score == Decimal("0.827")
        assert str(out.confidence_score) == "0.827"

    def test_schema_rejects_an_over_long_industry(self) -> None:
        """First line of defence: the structured-output schema itself."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _parsed(industry="x" * 200)

    def test_decide_truncates_defensively(self) -> None:
        """Second line: `decide` bounds it again.

        Constructed without validation to simulate the schema being relaxed or
        bypassed — the column is String(120) and an over-long value would be a
        write error at commit time, far from the cause.
        """
        unvalidated = IndustryClassification.model_construct(
            is_classifiable=True,
            industry="x" * 200,
            niche=None,
            brand_name="B",
            confidence="high",
            confidence_score=0.9,
            rationale="r",
        )
        out = decide(unvalidated)
        assert out.industry is not None
        assert len(out.industry) == 120


class TestClassifyGuards:
    """Paths that must never reach the model."""

    async def test_failed_crawl_short_circuits(self) -> None:
        out = await classify(_crawl(ok=False, error="FETCH_FAILED"))
        assert out.status == "unclassifiable"
        assert out.reason_code == "FETCH_FAILED"

    async def test_thin_page_short_circuits(self) -> None:
        """A parked domain must not yield a confident hallucination."""
        out = await classify(_crawl(text="hello world"))
        assert out.status == "unclassifiable"
        assert out.reason_code == "INSUFFICIENT_CONTENT"

    async def test_word_threshold_boundary(self) -> None:
        just_under = _crawl(text="word " * (MIN_WORDS_FOR_CLASSIFICATION - 1))
        out = await classify(just_under)
        assert out.reason_code == "INSUFFICIENT_CONTENT"


class TestPrompt:
    def test_prompt_includes_signals_and_text(self) -> None:
        crawl = _crawl()
        crawl.signals.schema_types = ["LocalBusiness", "Dentist"]
        crawl.signals.urls_fetched = ["https://x.example/", "https://x.example/about"]
        crawl.titles = ["Northaven Dental — Home"]
        prompt = build_prompt(crawl)
        assert "northaven-dental.example" in prompt
        assert "LocalBusiness, Dentist" in prompt
        assert "Northaven Dental — Home" in prompt
        assert "Page text:" in prompt

    def test_prompt_is_deterministic(self) -> None:
        """Same crawl, same prompt — so a re-run is comparable."""
        crawl = _crawl()
        assert build_prompt(crawl) == build_prompt(crawl)


class TestOutcomeShape:
    def test_outcome_has_no_rationale_field(self) -> None:
        """facts-only: model-authored prose is a debug aid, never persisted."""
        assert "rationale" not in ClassificationOutcome.model_fields

    def test_default_model_is_recorded(self) -> None:
        """Provenance: a stored classification must name the model."""
        assert decide(_parsed()).model == "claude-opus-5"


class TestCrawlErrorCodes:
    """A site that answers with an HTTP error is not the same as one that
    does not resolve — the reason code must let an operator tell them apart."""

    async def test_http_error_reason_code_reaches_the_outcome(self) -> None:
        crawl = CrawlResult(
            signals=CrawlSignals(
                final_url="https://blocked.example",
                registrable_domain="blocked.example",
                http_status=404,
            ),
            text_extract="",
            ok=False,
            error_code="HTTP_404",
        )
        out = await classify(crawl)
        assert out.status == "unclassifiable"
        assert out.reason_code == "HTTP_404"

    async def test_unresolvable_site_is_distinguishable(self) -> None:
        crawl = CrawlResult(
            signals=CrawlSignals(final_url="https://nx.example", registrable_domain="nx.example"),
            text_extract="",
            ok=False,
            error_code="FETCH_FAILED",
        )
        out = await classify(crawl)
        assert out.reason_code == "FETCH_FAILED"

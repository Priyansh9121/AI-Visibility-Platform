"""Where the engines disagree — Epic 9.23.

Pure functions over the facts scoring already loads, so these tests need no
database and no model call. The weight is on the two rules that make the
reading honest rather than merely computable:

* an engine that did not answer has no opinion, and must never be read as
  "this engine did not mention you";
* "every engine agreed" and "there was nothing to compare" are different
  claims, and a scan with one engine down must make the second one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from avp_api.models.engine_result import EngineResultStatus, Sentiment
from avp_api.services import divergence
from avp_api.services.scoring import ResultFacts, mention_rate, sentiment_score


def fact(
    prompt: str,
    engine: str,
    *,
    mentioned: bool = False,
    status: EngineResultStatus = EngineResultStatus.OK,
    sentiment: Sentiment | None = None,
) -> ResultFacts:
    return ResultFacts(
        result_id=f"{prompt}:{engine}",
        prompt_id=prompt,
        engine=engine,
        status=status if mentioned else _absent_status(status, mentioned),
        mentioned=mentioned,
        sentiment=sentiment,
    )


def _absent_status(
    status: EngineResultStatus, mentioned: bool
) -> EngineResultStatus:
    """An answered-but-absent result carries ANSWERED_NO_MENTION, as Epic 4 stores it."""
    if status is EngineResultStatus.OK and not mentioned:
        return EngineResultStatus.ANSWERED_NO_MENTION
    return status


class TestAnEngineThatDidNotAnswerHasNoOpinion:
    """The rule that keeps an outage from being reported as a finding."""

    @pytest.mark.parametrize(
        "status",
        [
            EngineResultStatus.ERROR,
            EngineResultStatus.TIMEOUT,
            EngineResultStatus.RATE_LIMITED,
            EngineResultStatus.TRUNCATED,
            EngineResultStatus.PAUSED,
        ],
    )
    def test_a_failed_engine_is_not_a_disagreement(
        self, status: EngineResultStatus
    ) -> None:
        """Claude named them; ChatGPT fell over. That is not a split.

        Counting it as one would turn every provider outage into a headline
        about cross-engine visibility, which is the failure ANSWERED_NO_MENTION
        exists to distinguish from a real absence.
        """
        results = [
            fact("p1", "claude", mentioned=True),
            fact("p1", "chatgpt", status=status),
        ]

        splits, comparable = divergence.split_prompts(results)

        assert splits == ()
        assert comparable == 0, "one answering engine is not a comparison"

    def test_a_failed_engine_does_not_dilute_another_engine_s_rate(self) -> None:
        results = [
            fact("p1", "chatgpt", mentioned=True),
            fact("p2", "chatgpt", status=EngineResultStatus.TIMEOUT),
        ]

        (standing,) = divergence.engine_standings(results)

        assert standing.answered == 1
        assert standing.mentioned == 1
        assert standing.mention_rate == Decimal("100")

    def test_an_engine_that_answered_nothing_reports_zero_not_none(self) -> None:
        """It answered nothing, so it has no rate to report and no sentiment.

        `mention_rate` returns 0 for an empty population by its own contract;
        what must NOT happen is a sentiment invented for a brand that engine
        never named.
        """
        results = [fact("p1", "chatgpt", status=EngineResultStatus.ERROR)]

        (standing,) = divergence.engine_standings(results)

        assert standing.answered == 0
        assert standing.sentiment is None


class TestTheSplitIsTheFinding:
    def test_one_engine_names_them_and_another_does_not(self) -> None:
        results = [
            fact("p1", "claude", mentioned=True),
            fact("p1", "chatgpt", mentioned=False),
            fact("p1", "claude_search", mentioned=True),
        ]

        splits, comparable = divergence.split_prompts(results)

        assert comparable == 1
        assert len(splits) == 1
        assert splits[0].named_by == ("claude", "claude_search")
        assert splits[0].missed_by == ("chatgpt",)

    def test_unanimous_prompts_are_comparable_but_not_splits(self) -> None:
        results = [
            fact("p1", "claude", mentioned=True),
            fact("p1", "chatgpt", mentioned=True),
            fact("p2", "claude", mentioned=False),
            fact("p2", "chatgpt", mentioned=False),
        ]

        splits, comparable = divergence.split_prompts(results)

        assert splits == ()
        assert comparable == 2, "agreeing on absence is still agreeing"

    def test_splits_are_ordered_deterministically(self) -> None:
        """The same scan must render the same way twice.

        A list that reshuffles between two reads of one scan reads as a change
        when nothing changed.
        """
        results = [
            fact(p, e, mentioned=(e == "claude"))
            for p in ("p3", "p1", "p2")
            for e in ("chatgpt", "claude")
        ]

        first, _ = divergence.split_prompts(results)
        second, _ = divergence.split_prompts(list(reversed(results)))

        assert [s.prompt_id for s in first] == ["p1", "p2", "p3"]
        assert first == second


class TestAgreementRateSaysWhichKindOfNothing:
    def test_nothing_comparable_is_none_and_never_a_hundred(self) -> None:
        """The distinction this property exists for.

        A single-engine scan agrees with itself trivially. Reporting that as
        100% consensus would be the strongest possible claim made from the
        weakest possible evidence.
        """
        results = [fact("p1", "claude", mentioned=True)]

        assert divergence.analyse(results).agreement_rate is None

    def test_total_agreement_is_a_hundred(self) -> None:
        results = [
            fact("p1", "claude", mentioned=True),
            fact("p1", "chatgpt", mentioned=True),
        ]

        assert divergence.analyse(results).agreement_rate == Decimal("100.00")

    def test_a_half_split_scan_reports_half(self) -> None:
        results = [
            fact("p1", "claude", mentioned=True),
            fact("p1", "chatgpt", mentioned=False),
            fact("p2", "claude", mentioned=True),
            fact("p2", "chatgpt", mentioned=True),
        ]

        assert divergence.analyse(results).agreement_rate == Decimal("50.00")


class TestTheNumbersAreNotRecomputedHere:
    """One source per number, or the report eventually contradicts itself."""

    def test_per_engine_figures_match_the_scoring_functions_on_the_same_slice(
        self,
    ) -> None:
        results = [
            fact("p1", "claude", mentioned=True, sentiment=Sentiment.POSITIVE),
            fact("p2", "claude", mentioned=False),
            fact("p1", "chatgpt", mentioned=True, sentiment=Sentiment.NEGATIVE),
            fact("p2", "chatgpt", mentioned=True, sentiment=Sentiment.NEUTRAL),
        ]

        for standing in divergence.engine_standings(results):
            own = [r for r in results if r.engine == standing.engine]
            assert standing.mention_rate == mention_rate(own)
            assert standing.sentiment == sentiment_score(own)

    def test_a_brand_no_engine_named_has_no_sentiment_anywhere(self) -> None:
        """No mention means no sentiment — excluded, never scored zero.

        Scoring redistributes the weight rather than punishing the same absence
        twice, and a per-engine reading must not undo that by reporting a 0.
        """
        results = [
            fact("p1", "claude", mentioned=False),
            fact("p1", "chatgpt", mentioned=False),
        ]

        for standing in divergence.engine_standings(results):
            assert standing.sentiment is None


class TestAnEmptyScan:
    def test_no_results_produces_an_empty_reading_rather_than_raising(self) -> None:
        result = divergence.analyse([])

        assert result.standings == ()
        assert result.splits == ()
        assert result.comparable_prompts == 0
        assert result.agreement_rate is None

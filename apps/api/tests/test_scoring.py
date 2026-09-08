"""Scoring engine — §6 formula, determinism, and every scoring-spec.md edge case.

Pure functions over value objects, so no database and no network. The live run
against real persisted scan data is scripts/verify_scoring.py.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from avp_api.models.competitor import DetectionStatus
from avp_api.models.engine_result import EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services.scoring import (
    FORMULA_VERSION,
    WEIGHTS,
    CompetitorFacts,
    Dimension,
    ResultFacts,
    SubScoreOutOfRangeError,
    awareness_only,
    citation_strength,
    compare_competitors,
    compute_inputs_digest,
    compute_score,
    mention_rate,
    sentiment_score,
    share_of_voice,
    weighted_composite,
)


def res(
    i: int, *, mentioned: bool = False, sentiment: Sentiment | None = None,
    brands: tuple[tuple[str, bool], ...] = (),
    citations: tuple[tuple[str, bool], ...] = (),
    status: EngineResultStatus = EngineResultStatus.OK,
    engine: str = "claude",
    intent: PromptIntent = PromptIntent.AWARENESS,
) -> ResultFacts:
    """One result. **`intent` defaults to AWARENESS here and NOWHERE ELSE.**

    `ResultFacts.intent` defaults to None in production, which excludes a
    result from Mention Rate and Share of Voice and raises
    `NO_AWARENESS_POPULATION` — a loud, visible failure rather than a silently
    inflated score. That is the right default for the code and the wrong one
    for a fixture, where every pre-v2 test means "an ordinary scored result".
    Tests about the intent itself pass it explicitly.
    """
    return ResultFacts(
        result_id=f"eres_{i:04d}", engine=engine, status=status, mentioned=mentioned,
        sentiment=sentiment, brands=brands, citations=citations, intent=intent,
    )


SUBJECT = ("Help Scout", True)


def comp(n: int, name: str, domain: str | None = None) -> CompetitorFacts:
    return CompetitorFacts(competitor_id=f"comp_{n:04d}", name=name, domain=domain)


class TestFormulaConstants:
    def test_weights_match_section_6_exactly(self) -> None:
        assert {
            Dimension.MENTION_RATE: Decimal("30"),
            Dimension.SHARE_OF_VOICE: Decimal("25"),
            Dimension.CITATION_STRENGTH: Decimal("20"),
            Dimension.SENTIMENT: Decimal("15"),
            Dimension.TECHNICAL_FOUNDATION: Decimal("10"),
        } == WEIGHTS

    def test_weights_sum_to_one_hundred(self) -> None:
        assert sum(WEIGHTS.values()) == Decimal("100")

    def test_formula_version_is_recorded(self) -> None:
        # Pinned so a formula change cannot ship without a deliberate bump.
        # v1.1 bumped when no-competitor Share of Voice changed behaviour; v2
        # when Mention Rate and Share of Voice moved to the awareness-only
        # population. Rows carrying an earlier version were computed under an
        # earlier definition and are not comparable across the bump — which is
        # what rule 5 exists to keep true. v2.1 when Citation Strength was
        # excluded under NO_AUTHORITY_DATA: a stand-in no brand could score
        # above 1/N on left the composite, and its weight was redistributed.
        assert FORMULA_VERSION == "v2.1"


class TestMentionRate:
    def test_basic_rate(self) -> None:
        results = [res(1, mentioned=True), res(2, mentioned=False),
                   res(3, mentioned=True), res(4, mentioned=True)]
        assert mention_rate(results) == Decimal("75")

    def test_failed_calls_are_excluded_from_the_denominator(self) -> None:
        """An outage is missing data, not evidence of absence.

        Counting a timeout as a non-mention turns a rate-limited engine into a
        low score — exactly what the PARTIAL scan status exists to prevent.
        """
        results = [
            res(1, mentioned=True),
            res(2, status=EngineResultStatus.TIMEOUT),
            res(3, status=EngineResultStatus.RATE_LIMITED),
            res(4, status=EngineResultStatus.ERROR),
            # An answer the engine did not finish is missing data too — the
            # brand may well be named in the part that was never generated.
            res(5, status=EngineResultStatus.TRUNCATED),
            res(6, status=EngineResultStatus.PAUSED),
        ]
        assert mention_rate(results) == Decimal("100"), "1 of 1 answered, not 1 of 6"

    def test_answered_no_mention_counts_in_the_denominator(self) -> None:
        """An engine that answered and did not name you IS evidence of absence."""
        results = [res(1, mentioned=True),
                   res(2, status=EngineResultStatus.ANSWERED_NO_MENTION)]
        assert mention_rate(results) == Decimal("50")

    def test_no_answered_results_is_zero(self) -> None:
        assert mention_rate([res(1, status=EngineResultStatus.TIMEOUT)]) == Decimal("0")


class TestShareOfVoice:
    def test_counts_every_appearance_not_just_presence(self) -> None:
        results = [
            res(1, mentioned=True, brands=(SUBJECT, ("Zendesk", False))),
            res(2, mentioned=False, brands=(("Zendesk", False),)),
        ]
        assert share_of_voice(results) == Decimal("1") / Decimal("3") * Decimal("100")

    def test_all_competitors_tied_is_exactly_one_over_n_plus_one(self) -> None:
        """scoring-spec.md: 'All competitors tied' -> 100/(1+n), exactly."""
        for n in (1, 2, 3, 4):
            brands = (SUBJECT, *[(f"Rival {i}", False) for i in range(n)])
            results = [res(1, mentioned=True, brands=brands)]
            expected = Decimal("100") / Decimal(1 + n)
            assert share_of_voice(results) == expected

    def test_no_brands_at_all_is_zero(self) -> None:
        assert share_of_voice([res(1, mentioned=False)]) == Decimal("0")

    def test_subject_only_is_one_hundred(self) -> None:
        assert share_of_voice([res(1, mentioned=True, brands=(SUBJECT,))]) == Decimal("100")


class TestSentiment:
    def test_neutral_sits_at_the_midpoint(self) -> None:
        """Being listed without evaluation beats being warned against.

        Collapsing neutral to 0 would make this dimension a near-duplicate of
        Mention Rate.
        """
        results = [res(1, mentioned=True, sentiment=Sentiment.NEUTRAL)]
        assert sentiment_score(results) == Decimal("50")

    def test_mean_across_labelled_results(self) -> None:
        results = [
            res(1, mentioned=True, sentiment=Sentiment.POSITIVE),
            res(2, mentioned=True, sentiment=Sentiment.NEGATIVE),
        ]
        assert sentiment_score(results) == Decimal("50")

    def test_no_population_returns_none_not_zero(self) -> None:
        """A brand nobody mentions has not been spoken of badly."""
        assert sentiment_score([res(1, mentioned=False)]) is None

    def test_unmentioned_results_do_not_dilute(self) -> None:
        results = [
            res(1, mentioned=True, sentiment=Sentiment.POSITIVE),
            res(2, mentioned=False),
        ]
        assert sentiment_score(results) == Decimal("100")


class TestCitationStrength:
    def test_normalised_against_the_best_cited_in_the_scan(self) -> None:
        results = [res(1, mentioned=True, citations=(
            ("helpscout.com", True), ("g2.com", False), ("capterra.com", False)))]
        value, flags = citation_strength(results)
        # subject 1 distinct domain, others 2 -> 1/2
        assert value == Decimal("50")
        assert "NO_AUTHORITY_DATA" in flags

    def test_always_flags_missing_authority_data(self) -> None:
        """§6 asks for 'number AND authority'. No authority source exists."""
        _, flags = citation_strength([res(1, citations=(("x.com", True),))])
        assert "NO_AUTHORITY_DATA" in flags

    def test_no_citations_anywhere_is_flagged_distinctly(self) -> None:
        """'Nobody cited anything' differs from 'cited others, not you'."""
        value, flags = citation_strength([res(1, mentioned=True)])
        assert value == Decimal("0")
        assert "NO_CITATIONS_IN_SCAN" in flags

    def test_distinct_domains_not_raw_citation_count(self) -> None:
        results = [res(1, citations=(("a.com", True), ("a.com", True), ("b.com", True)))]
        value, _ = citation_strength(results)
        assert value == Decimal("100")


class TestCitationStrengthIsExcluded:
    """v2.1 — a dimension nobody can earn is left out, not scored.

    The stand-in `citation_strength` computes divides the subject's own domain
    (0 or 1) by every distinct third-party domain in the scan, so its ceiling
    is 1/N. On the second pilot dry run's 24-prompt scan N was 152: the
    subject and all five rivals scored 0.66, and across every stored score the
    maximum was 3.70. Scoring that kept depressing every composite by up to 20
    points while the pitch beat promised those points back.
    """

    @staticmethod
    def _scored():  # noqa: ANN205
        results = [
            res(1, mentioned=True, sentiment=Sentiment.POSITIVE,
                brands=(("Help Scout", True), ("Zendesk", False)),
                citations=(("helpscout.com", True), ("g2.com", False), ("capterra.com", False))),
            res(2, mentioned=False, brands=(("Zendesk", False),),
                citations=(("g2.com", False), ("zendesk.com", False))),
        ]
        return compute_score(
            results, [comp(1, "Zendesk", "zendesk.com")],
            competitor_set_status=DetectionStatus.OK, technical_foundation=Decimal("80"),
        )

    def test_excluded_with_the_reason_that_names_the_missing_input(self) -> None:
        out = self._scored()
        assert out.excluded_dimensions["citation_strength"] == "NO_AUTHORITY_DATA"
        assert out.value(Dimension.CITATION_STRENGTH) is None
        assert not out.sub_scores[Dimension.CITATION_STRENGTH].included
        # Not NOT_YET_MEASURED: that says a capability is on its way.
        assert "NOT_YET_MEASURED" not in out.excluded_dimensions.values()

    def test_its_weight_is_redistributed_and_the_composite_re_sums(self) -> None:
        out = self._scored()
        included = {d: s for d, s in out.sub_scores.items() if s.included}
        assert Dimension.CITATION_STRENGTH not in included
        assert set(included) == {
            Dimension.MENTION_RATE, Dimension.SHARE_OF_VOICE,
            Dimension.SENTIMENT, Dimension.TECHNICAL_FOUNDATION,
        }
        assert sum(s.weight for s in included.values()) == Decimal("100.00")
        rebuilt = sum(s.weight * s.value for s in included.values()) / Decimal(100)
        assert rebuilt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == out.composite

    def test_no_authority_data_is_the_exclusion_and_not_also_a_degradation(self) -> None:
        """A dimension that is left out is not "rougher". One fact, said once."""
        out = self._scored()
        assert "NO_AUTHORITY_DATA" not in out.degradation_flags

    def test_no_citations_in_scan_is_still_a_fact_about_the_scan(self) -> None:
        out = compute_score([res(1, mentioned=True)], [], competitor_set_status=None)
        assert "NO_CITATIONS_IN_SCAN" in out.degradation_flags
        assert out.excluded_dimensions["citation_strength"] == "NO_AUTHORITY_DATA"

    def test_rivals_carry_no_citation_strength_while_the_subject_does_not(self) -> None:
        """A column means one thing — `compare_competitors`'s own rule."""
        out = self._scored()
        assert out.competitors
        assert all(c.citation_strength is None for c in out.competitors)
        assert all(c.mention_rate is not None and c.share_of_voice is not None
                   for c in out.competitors)

    def test_the_stand_in_is_still_computed_for_the_day_an_authority_source_exists(self) -> None:
        # Not asserted in the composite, but not thrown away either: the
        # function and its flags are what a real source plugs into.
        value, flags = citation_strength([res(1, citations=(("x.com", True), ("y.com", False)))])
        assert value == Decimal("100") and "NO_AUTHORITY_DATA" in flags

    def test_the_dry_run_scan_would_have_read_33_93_rather_than_27_28(self) -> None:
        """pirsch.io's stored v2 sub-scores, re-summed over the four that remain.

        Effective weights are the §6 weights over the 80 points that survive:
        30/80, 25/80, 15/80, 10/80 — exact at two places, so no rounding
        question arises in the re-sum.
        """
        dims = [
            Dimension.MENTION_RATE, Dimension.SHARE_OF_VOICE,
            Dimension.SENTIMENT, Dimension.TECHNICAL_FOUNDATION,
        ]
        weights = dict(zip(dims, [
            Decimal("37.50"), Decimal("31.25"), Decimal("18.75"), Decimal("12.50"),
        ], strict=True))
        values = dict(zip(dims, [
            Decimal("30.56"), Decimal("9.91"), Decimal("76.67"), Decimal("40.00"),
        ], strict=True))
        got = weighted_composite(weights, values)
        assert got.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("33.93")


class TestEdgeCasesFromSpec:
    """The table in scoring-spec.md, case by case."""

    def test_zero_mentions_anywhere(self) -> None:
        results = [res(i, mentioned=False, brands=(("Zendesk", False),)) for i in range(1, 4)]
        out = compute_score(results, [comp(1, "Zendesk")],
                            competitor_set_status=DetectionStatus.OK)
        assert out.value(Dimension.MENTION_RATE) == Decimal("0")
        assert out.value(Dimension.SHARE_OF_VOICE) == Decimal("0")
        # Sentiment excluded, not zeroed.
        assert out.value(Dimension.SENTIMENT) is None
        assert out.excluded_dimensions[Dimension.SENTIMENT.value] == "NO_POPULATION"
        assert out.status == "scored"

    def test_zero_prompts_is_null_not_zero(self) -> None:
        """An unrunnable scan must never render as a bad score."""
        out = compute_score([], [], competitor_set_status=None)
        assert out.composite is None
        assert out.status == "insufficient_data"
        assert out.reason_code == "INSUFFICIENT_DATA"

    def test_all_engine_calls_failed_is_insufficient_data(self) -> None:
        results = [res(i, status=EngineResultStatus.TIMEOUT) for i in range(1, 5)]
        out = compute_score(results, [], competitor_set_status=DetectionStatus.OK)
        assert out.composite is None
        assert out.reason_code == "INSUFFICIENT_DATA"

    def test_no_competitors_excludes_share_of_voice(self) -> None:
        """Deviation from scoring-spec v1, recorded as v1.1.

        v1 said Share of Voice = 100 when no competitors were detected. That
        awards a quarter of the composite for a DETECTION FAILURE. Excluded and
        redistributed instead, matching the treatment of sentiment with no
        population.
        """
        results = [res(1, mentioned=True, brands=(SUBJECT,))]
        out = compute_score(results, [], competitor_set_status=DetectionStatus.NO_SIGNAL)
        assert out.value(Dimension.SHARE_OF_VOICE) is None
        assert out.excluded_dimensions[Dimension.SHARE_OF_VOICE.value] == "NO_COMPETITOR_SET"
        assert "NO_COMPETITOR_SET" in out.degradation_flags

    def test_weak_competitor_set_is_flagged_but_still_scored(self) -> None:
        """Epic 3.5 measured SERP-only precision at ~58%."""
        results = [res(1, mentioned=True, brands=(SUBJECT, ("Rival", False)))]
        out = compute_score(results, [comp(1, "Rival")],
                            competitor_set_status=DetectionStatus.WEAK_SIGNAL)
        assert out.value(Dimension.SHARE_OF_VOICE) == Decimal("50")
        assert "WEAK_COMPETITOR_SET" in out.degradation_flags

    def test_subscore_above_one_hundred_clamps_and_raises(self) -> None:
        """scoring-spec.md: clamp AND raise — a silent clamp hides defects."""
        results = [res(1, mentioned=True, brands=(SUBJECT,))]
        with pytest.raises(SubScoreOutOfRangeError) as excinfo:
            compute_score(results, [comp(1, "R")],
                          competitor_set_status=DetectionStatus.OK,
                          technical_foundation=Decimal("150"))
        assert excinfo.value.clamped == Decimal("100")
        assert excinfo.value.raw == Decimal("150")
        assert excinfo.value.dimension is Dimension.TECHNICAL_FOUNDATION


class TestTechnicalFoundationExclusion:
    def test_excluded_with_a_distinct_reason_from_no_population(self) -> None:
        """'We haven't checked' is not 'there's nothing to find'.

        Epic 7's report must word those differently, so they cannot share a flag.
        """
        results = [res(1, mentioned=True, sentiment=Sentiment.POSITIVE, brands=(SUBJECT,))]
        out = compute_score(results, [comp(1, "R")],
                            competitor_set_status=DetectionStatus.OK)
        assert out.excluded_dimensions[Dimension.TECHNICAL_FOUNDATION.value] == "NOT_YET_MEASURED"
        assert "NO_POPULATION" not in out.excluded_dimensions.values()

    def test_supplying_a_value_includes_the_dimension(self) -> None:
        """Epic 6 will pass a real value; nothing else needs to change."""
        results = [res(1, mentioned=True, sentiment=Sentiment.POSITIVE, brands=(SUBJECT,))]
        out = compute_score(results, [comp(1, "R")],
                            competitor_set_status=DetectionStatus.OK,
                            technical_foundation=Decimal("80"))
        assert out.value(Dimension.TECHNICAL_FOUNDATION) == Decimal("80")
        assert Dimension.TECHNICAL_FOUNDATION.value not in out.excluded_dimensions

    def test_effective_weights_are_rounded_for_auditability(self) -> None:
        """Stored weights must be re-addable by a person reading the report."""
        results = [res(1, mentioned=True, sentiment=Sentiment.POSITIVE, brands=(SUBJECT,))]
        out = compute_score(results, [comp(1, "R")], competitor_set_status=DetectionStatus.OK)
        for sub in out.sub_scores.values():
            if sub.included:
                assert sub.weight == sub.weight.quantize(Decimal("0.01")), (
                    f"{sub.dimension.value} weight {sub.weight} is not 2dp"
                )

    def test_breakdown_re_sums_exactly_against_its_own_weights(self) -> None:
        """Rule 2, stated precisely: weight x value must reproduce the total."""
        results = [
            res(1, mentioned=True, sentiment=Sentiment.POSITIVE,
                brands=(SUBJECT, ("R", False)), citations=(("a.com", True),)),
            res(2, mentioned=False, brands=(("R", False),)),
        ]
        out = compute_score(results, [comp(1, "R", "r.com")],
                            competitor_set_status=DetectionStatus.OK)
        rebuilt = sum(
            (s.weight * s.value for s in out.sub_scores.values() if s.included),
            Decimal("0"),
        ) / Decimal("100")
        assert rebuilt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == out.composite

    def test_effective_weights_always_sum_to_one_hundred(self) -> None:
        """Redistribution must keep the composite on a 0-100 axis."""
        results = [res(1, mentioned=True, sentiment=Sentiment.POSITIVE, brands=(SUBJECT,))]
        for status, competitors in (
            (DetectionStatus.OK, [comp(1, "R")]),
            (DetectionStatus.NO_SIGNAL, []),
        ):
            out = compute_score(results, competitors, competitor_set_status=status)
            total = sum(
                (s.weight for s in out.sub_scores.values() if s.included), Decimal("0")
            )
            assert abs(total - Decimal("100")) < Decimal("0.0001")


class TestComposite:
    def test_composite_re_sums_from_the_stored_subscores(self) -> None:
        """scoring-spec.md rule 2 — a displayed breakdown must re-sum."""
        results = [
            res(1, mentioned=True, sentiment=Sentiment.POSITIVE,
                brands=(SUBJECT, ("R", False)), citations=(("a.com", True),)),
            res(2, mentioned=False, brands=(("R", False),)),
        ]
        out = compute_score(results, [comp(1, "R", "r.com")],
                            competitor_set_status=DetectionStatus.OK)
        rebuilt = sum(
            (s.weight * s.value for s in out.sub_scores.values() if s.included),
            Decimal("0"),
        ) / Decimal("100")
        assert abs(rebuilt - out.composite) < Decimal("0.01")

    def test_perfect_inputs_score_one_hundred(self) -> None:
        results = [res(1, mentioned=True, sentiment=Sentiment.POSITIVE,
                       brands=(SUBJECT,), citations=(("a.com", True),))]
        out = compute_score(results, [comp(1, "R")],
                            competitor_set_status=DetectionStatus.OK,
                            technical_foundation=Decimal("100"))
        assert out.composite == Decimal("100.00")

    def test_total_absence_scores_zero_not_null(self) -> None:
        """Absent-but-measured is a real zero; unmeasurable is null."""
        results = [res(i, mentioned=False, brands=(("R", False),)) for i in range(1, 4)]
        out = compute_score(results, [comp(1, "R")],
                            competitor_set_status=DetectionStatus.OK,
                            technical_foundation=Decimal("0"))
        assert out.composite == Decimal("0.00")
        assert out.status == "scored"

    def test_composite_is_rounded_to_two_places(self) -> None:
        results = [res(i, mentioned=(i % 3 == 0), brands=(SUBJECT,)) for i in range(1, 8)]
        out = compute_score(results, [comp(1, "R")],
                            competitor_set_status=DetectionStatus.OK)
        assert out.composite == out.composite.quantize(Decimal("0.01"))


class TestDeterminism:
    """The acceptance criterion: same EngineResult set in, same score out."""

    def _fixture(self) -> tuple[list[ResultFacts], list[CompetitorFacts]]:
        results = [
            res(1, mentioned=True, sentiment=Sentiment.POSITIVE,
                brands=(SUBJECT, ("Zendesk", False)),
                citations=(("helpscout.com", True), ("g2.com", False))),
            res(2, mentioned=False, brands=(("Zendesk", False), ("Front", False))),
            res(3, mentioned=True, sentiment=Sentiment.NEUTRAL, brands=(SUBJECT,),
                citations=(("capterra.com", False),)),
            res(4, mentioned=True, sentiment=Sentiment.NEGATIVE,
                brands=(SUBJECT, ("Front", False)), engine="claude_search"),
        ]
        return results, [comp(1, "Zendesk", "zendesk.com"), comp(2, "Front", "front.com")]

    def test_same_inputs_produce_an_identical_composite(self) -> None:
        results, competitors = self._fixture()
        first = compute_score(results, competitors, competitor_set_status=DetectionStatus.OK)
        for _ in range(25):
            again = compute_score(results, competitors, competitor_set_status=DetectionStatus.OK)
            assert again.composite == first.composite
            assert {d: s.value for d, s in again.sub_scores.items()} == {
                d: s.value for d, s in first.sub_scores.items()
            }

    def test_same_inputs_produce_an_identical_digest(self) -> None:
        results, competitors = self._fixture()
        first = compute_inputs_digest(results, competitors)
        for _ in range(25):
            assert compute_inputs_digest(results, competitors) == first
        assert len(first) == 64

    def test_digest_ignores_input_ordering(self) -> None:
        """Row order from the database must not change the fingerprint."""
        results, competitors = self._fixture()
        assert compute_inputs_digest(results, competitors) == compute_inputs_digest(
            list(reversed(results)), list(reversed(competitors))
        )

    def test_score_ignores_input_ordering(self) -> None:
        results, competitors = self._fixture()
        a = compute_score(results, competitors, competitor_set_status=DetectionStatus.OK)
        b = compute_score(list(reversed(results)), list(reversed(competitors)),
                          competitor_set_status=DetectionStatus.OK)
        assert a.composite == b.composite

    def test_digest_changes_when_any_scored_input_changes(self) -> None:
        """A changed score must be attributable to changed inputs or formula.

        "Any scored input" is three families, not two. This test covered
        `results` and `competitors` from Epic 5; Epic 6 made
        `technical_foundation` the fifth dimension and nobody widened it, so
        for four epics a re-audit could move the composite under an unchanged
        digest AND an unchanged formula_version — precisely the ambiguity the
        docstring promises cannot happen. Measured before the fix: identical
        results and competitors scored 79.50 with technical_foundation=20 and
        87.00 with 95, both under one digest. Found in Epic 3.10.
        """
        results, competitors = self._fixture()
        base = compute_inputs_digest(results, competitors)

        flipped = [*results]
        flipped[1] = res(2, mentioned=True, brands=(("Zendesk", False),))
        assert compute_inputs_digest(flipped, competitors) != base

        fewer = compute_inputs_digest(results, competitors[:1])
        assert fewer != base

        audited = compute_inputs_digest(results, competitors, Decimal("87.50"))
        assert audited != base, (
            "technical_foundation is a scored input and must move the digest"
        )
        rescored = compute_inputs_digest(results, competitors, Decimal("20.00"))
        assert rescored != audited, (
            "a re-audit that changes technical_foundation must move the digest"
        )

    def test_a_changed_audit_cannot_move_the_score_under_one_digest(self) -> None:
        """The end-to-end form of the guard above, asserted on the score itself.

        The digest test can be satisfied by a digest that changes for the wrong
        reason. This one takes the path an operator actually travels — re-audit
        a site, re-score the scan — and asserts the two numbers move together.
        """
        results, competitors = self._fixture()
        low = compute_score(
            results, competitors,
            competitor_set_status=DetectionStatus.OK,
            technical_foundation=Decimal("20.00"),
        )
        high = compute_score(
            results, competitors,
            competitor_set_status=DetectionStatus.OK,
            technical_foundation=Decimal("95.00"),
        )
        assert low.composite != high.composite, "the fixture must actually be sensitive"
        assert low.formula_version == high.formula_version
        assert low.inputs_digest != high.inputs_digest, (
            "the composite moved while the digest and formula_version did not — "
            "the change is unattributable, which scoring-spec.md forbids"
        )

    def test_every_stored_value_is_a_decimal(self) -> None:
        """Rule 3 — binary floats are platform-fragile at rounding boundaries."""
        results, competitors = self._fixture()
        out = compute_score(results, competitors, competitor_set_status=DetectionStatus.OK)
        assert isinstance(out.composite, Decimal)
        for sub in out.sub_scores.values():
            assert sub.value is None or isinstance(sub.value, Decimal)
            assert isinstance(sub.weight, Decimal)
            assert not isinstance(sub.value, float)

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            # Cases where binary float and Decimal disagree after half-up
            # rounding to 2dp. Found by searching the weight/value grid; each
            # is a one-point swing in a client-facing score from arithmetic
            # alone. These exist because a float injected into the weighted sum
            # passed all 42 original tests undetected — see build-log Epic 5.1.
            ((Decimal("67.13"), Decimal("77.75"), Decimal("77.55"), Decimal("50.33")),
             Decimal("69.60")),
            ((Decimal("76.72"), Decimal("52.87"), Decimal("67.21"), Decimal("86.28")),
             Decimal("69.58")),
            ((Decimal("79.46"), Decimal("6.22"), Decimal("5.17"), Decimal("28.76")),
             Decimal("34.16")),
        ],
    )
    def test_weighted_sum_is_decimal_exact_at_rounding_boundaries(
        self, values: tuple[Decimal, ...], expected: Decimal
    ) -> None:
        """scoring-spec.md rule 3, made falsifiable.

        Binary float gives an answer one hundredth lower on each of these. The
        weights are the real effective weights when Technical Foundation is
        excluded.
        """
        dims = [
            Dimension.MENTION_RATE, Dimension.SHARE_OF_VOICE,
            Dimension.CITATION_STRENGTH, Dimension.SENTIMENT,
        ]
        weights = dict(zip(dims, [
            Decimal("33.33"), Decimal("27.78"), Decimal("22.22"), Decimal("16.67"),
        ], strict=True))
        got = weighted_composite(weights, dict(zip(dims, values, strict=True)))
        assert got.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == expected

    def test_scoring_module_uses_no_float_arithmetic(self) -> None:
        """Rule 3 as a source-level guard.

        The boundary cases above only catch a float that happens to land on a
        rounding edge. This catches any float entering the module at all, which
        is the rule scoring-spec.md actually states.
        """
        import inspect

        from avp_api.services import scoring as scoring_module

        source = inspect.getsource(scoring_module)
        offenders = [
            line.strip()
            for line in source.splitlines()
            if ("float(" in line or " / 100.0" in line or "0.0," in line)
            and not line.strip().startswith("#")
            and "not isinstance" not in line
        ]
        assert not offenders, f"float arithmetic in the scoring path: {offenders}"

    def test_no_model_call_is_reachable_from_scoring(self) -> None:
        """Rule 4 — the LLM is never in the scoring path.

        Asserted by source inspection: importing anthropic here at all would
        mean re-scoring could re-classify sentiment and stop being reproducible.
        """
        import inspect

        from avp_api.services import scoring as scoring_module

        source = inspect.getsource(scoring_module)
        for forbidden in ("anthropic", "messages.create", "messages.parse", "httpx"):
            assert forbidden not in source, f"scoring imports/calls {forbidden}"


class TestCompetitorComparison:
    def test_reports_the_three_measurable_dimensions_only(self) -> None:
        """No competitor composite — 25% of the weight has no per-competitor input."""
        results = [
            res(1, mentioned=True, brands=(SUBJECT, ("Zendesk", False))),
            res(2, mentioned=False, brands=(("Zendesk", False),)),
        ]
        rows = compare_competitors(results, [comp(1, "Zendesk", "zendesk.com")])
        assert len(rows) == 1
        assert rows[0].mention_rate == Decimal("100.00")
        assert not hasattr(rows[0], "composite")

    def test_empty_without_competitors(self) -> None:
        assert compare_competitors([res(1, mentioned=True)], []) == []

    def test_ordering_is_deterministic(self) -> None:
        results = [res(1, mentioned=True, brands=(SUBJECT, ("B", False), ("A", False)))]
        competitors = [comp(2, "B"), comp(1, "A")]
        first = [c.competitor_id for c in compare_competitors(results, competitors)]
        for _ in range(5):
            assert [
                c.competitor_id for c in compare_competitors(results, list(reversed(competitors)))
            ] == first


class TestTheScoringPopulationIsAwarenessOnly:
    """v2 — Mention Rate and Share of Voice count only unprompted discovery.

    `prompts.py`'s generator names the subject brand in `comparison` and
    `bottom_funnel` questions by instruction, and its own system prompt gives
    the reason: a question that names the brand can only confirm the brand
    exists, it cannot reveal whether the brand gets discovered. A mention is a
    text match, so those questions register a mention almost regardless of what
    the engine knows — measured at 255 of 256 across every stored scan.

    `product-spec.md`'s Epic 0 states the product's first job as "prove to a
    stranger they are invisible in AI answers". Invisibility is an unprompted
    property. These tests are that requirement, executable.
    """

    def test_a_comparison_mention_does_not_count_toward_mention_rate(self) -> None:
        """The shape the diagnostic proved matters.

        Named in the comparison question, absent from the awareness one. The
        honest answer is 0%: nobody discovers this brand.
        """
        results = [
            res(1, mentioned=True, intent=PromptIntent.COMPARISON),
            res(2, mentioned=True, intent=PromptIntent.BOTTOM_FUNNEL),
            res(3, mentioned=False, intent=PromptIntent.AWARENESS),
        ]

        score = compute_score(
            results, [comp(1, "Zendesk", "zendesk.com")],
            competitor_set_status=DetectionStatus.OK,
        )

        assert score.sub_scores[Dimension.MENTION_RATE].value == Decimal("0.00")
        # And under the retired definition it would have been two thirds.
        assert mention_rate(results).quantize(Decimal("0.01")) == Decimal("66.67")

    def test_an_engine_denying_all_knowledge_does_not_inflate_the_rate(self) -> None:
        """The Zorblex case, as a regression.

        Asked "is zorblex inbox worth it or should i just pay for front",
        Claude answered "I don't have any knowledge of a product called Zorblex
        Inbox" — and because a mention is a text match, the pipeline recorded a
        mention at position 1. That row is a comparison row, so v2 must not let
        it near Mention Rate.
        """
        denial = res(1, mentioned=True, intent=PromptIntent.COMPARISON,
                     brands=(("Zorblex Inbox", True),))
        genuinely_absent = res(2, mentioned=False, intent=PromptIntent.AWARENESS)

        score = compute_score(
            [denial, genuinely_absent], [comp(1, "Front", "front.com")],
            competitor_set_status=DetectionStatus.OK,
        )

        assert score.sub_scores[Dimension.MENTION_RATE].value == Decimal("0.00")

    def test_share_of_voice_uses_the_same_population(self) -> None:
        """A comparison prompt names two parties and guarantees both a hit.

        That pulls the ratio toward 1/(1+named) whatever the real standing, so
        the numerator is inflated in a specific direction rather than neutrally.
        """
        results = [
            # Comparison: subject and one rival, both named because both were asked about.
            res(1, mentioned=True, intent=PromptIntent.COMPARISON,
                brands=(SUBJECT, ("Zendesk", False))),
            # Awareness: the engine volunteers three rivals and not the subject.
            res(2, mentioned=False, intent=PromptIntent.AWARENESS,
                brands=(("Zendesk", False), ("Intercom", False), ("Front", False))),
        ]

        score = compute_score(
            results, [comp(1, "Zendesk", "zendesk.com")],
            competitor_set_status=DetectionStatus.OK,
        )

        assert score.sub_scores[Dimension.SHARE_OF_VOICE].value == Decimal("0.00")
        # The retired definition counted the tautological pair and reported 20%.
        assert share_of_voice(results) == Decimal("20.00")

    def test_competitors_are_measured_on_the_same_population_as_the_subject(self) -> None:
        """One column of the report table means one thing.

        Epic 7 renders `score.mentionRate` and each `competitor.mentionRate` in
        the same column. Scoping only the subject would make the client look
        worse than its rivals through an accounting mismatch rather than
        through anything a scan found.
        """
        results = [
            res(1, mentioned=True, intent=PromptIntent.COMPARISON,
                brands=(SUBJECT, ("Zendesk", False))),
            res(2, mentioned=False, intent=PromptIntent.AWARENESS,
                brands=(("Zendesk", False),)),
        ]

        score = compute_score(
            results, [comp(1, "Zendesk", "zendesk.com")],
            competitor_set_status=DetectionStatus.OK,
        )
        (row,) = compare_competitors(results, [comp(1, "Zendesk", "zendesk.com")])

        # Zendesk was named in the one awareness answer: 100% of that population.
        assert row.mention_rate == Decimal("100.00")
        # The subject was named in none of it. Both figures over 1 awareness row.
        assert score.sub_scores[Dimension.MENTION_RATE].value == Decimal("0.00")

    def test_the_other_dimensions_keep_every_answered_result(self) -> None:
        """Citation Strength and Sentiment are not about discovery.

        Sentiment asks how an answer PORTRAYS the brand, which is a real signal
        whether the buyer named it or not. Scoping it would be a separate
        decision with its own evidence, and it is deliberately not made here.
        """
        results = [
            res(1, mentioned=True, sentiment=Sentiment.POSITIVE,
                intent=PromptIntent.COMPARISON, brands=(SUBJECT,),
                citations=(("helpscout.com", True),)),
            res(2, mentioned=False, intent=PromptIntent.AWARENESS),
        ]

        score = compute_score(
            results, [comp(1, "Zendesk", "zendesk.com")],
            competitor_set_status=DetectionStatus.OK,
        )

        # The comparison row's sentiment still counts — its population is
        # "results that mentioned", not "results that discovered".
        assert score.sub_scores[Dimension.SENTIMENT].value == Decimal("100.00")
        assert score.sub_scores[Dimension.SENTIMENT].included

    def test_no_awareness_prompts_excludes_rather_than_scores_zero(self) -> None:
        """A prompt set with nothing to measure is not a client with no visibility.

        The same treatment the spec already gives sentiment with no population
        and Share of Voice with no competitor set: exclude and redistribute,
        never score the absence.
        """
        results = [
            res(1, mentioned=True, intent=PromptIntent.COMPARISON, brands=(SUBJECT,)),
            res(2, mentioned=True, intent=PromptIntent.BOTTOM_FUNNEL, brands=(SUBJECT,)),
        ]

        score = compute_score(
            results, [comp(1, "Zendesk", "zendesk.com")],
            competitor_set_status=DetectionStatus.OK,
        )

        mr = score.sub_scores[Dimension.MENTION_RATE]
        assert mr.included is False
        assert mr.value is None
        assert score.excluded_dimensions[Dimension.MENTION_RATE.value] == (
            "NO_AWARENESS_POPULATION"
        )
        assert "NO_AWARENESS_POPULATION" in score.degradation_flags

    def test_the_filter_is_a_population_choice_not_a_rate_change(self) -> None:
        """`mention_rate` still answers "what fraction of THESE mention it".

        Kept apart on purpose: `services/divergence.py` calls `mention_rate` on
        its own per-engine slices, and folding the population into the rate
        would have re-scoped the cross-engine reading as a side effect of a
        scoring change.
        """
        mixed = [
            res(1, mentioned=True, intent=PromptIntent.COMPARISON),
            res(2, mentioned=False, intent=PromptIntent.AWARENESS),
        ]

        assert mention_rate(mixed) == Decimal("50.00")
        assert [r.result_id for r in awareness_only(mixed)] == ["eres_0002"]
        assert mention_rate(awareness_only(mixed)) == Decimal("0")

    def test_the_intent_is_in_the_digest(self) -> None:
        """Rule 1 — no hidden inputs.

        The intent now selects the scoring population, so two otherwise
        identical result sets that differ only by intent must not share a
        fingerprint; a changed score has to stay attributable.
        """
        a = [res(1, mentioned=True, intent=PromptIntent.AWARENESS)]
        b = [res(1, mentioned=True, intent=PromptIntent.COMPARISON)]

        assert compute_inputs_digest(a, []) != compute_inputs_digest(b, [])

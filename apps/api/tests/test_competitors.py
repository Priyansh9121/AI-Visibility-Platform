"""Competitor dedup, ranking and confidence policy.

Pure functions tested without network access. The live ≥80% accuracy check
across 10 URLs is a separate manual verification — scripts/verify_competitors.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from avp_api.models import DetectionSource, DetectionStatus
from avp_api.services import competitors as detection
from avp_api.services.cocitation import CoCitationHit, CoCitationResult, build_seed_prompts
from avp_api.services.competitors import (
    MIN_COMPETITORS_FOR_OK,
    Candidate,
    decide_detection,
    display_name_from_domain,
    merge_candidates,
    score_candidates,
    slugify,
)
from avp_api.services.serp import (
    NON_COMPETITOR_DOMAINS,
    SerpHit,
    SerpResult,
    build_queries,
    is_plausible_competitor,
)


def serp(query: str, domains: list[str]) -> SerpResult:
    return SerpResult(
        query=query,
        hits=[SerpHit(domain=d, position=i, query=query) for i, d in enumerate(domains, 1)],
    )


def cocit(prompt: str, brands: list[tuple[str, str | None]]) -> CoCitationResult:
    return CoCitationResult(
        prompt=prompt,
        hits=[
            CoCitationHit(name=n, domain=d, position=i, prompt=prompt)
            for i, (n, d) in enumerate(brands, 1)
        ],
    )


class TestQuerySeeding:
    def test_brand_anchored_queries_do_not_depend_on_industry(self) -> None:
        """The hedge against uncalibrated classification.

        Industry confidence is uncalibrated (Finding 2, open). Brand-anchored
        queries must work with no industry at all, so a mis-classification
        degrades recall rather than corrupting the result.
        """
        with_industry = build_queries(
            brand_name="Help Scout", domain="helpscout.com",
            industry="customer support software", niche=None,
        )
        without = build_queries(
            brand_name="Help Scout", domain="helpscout.com", industry=None, niche=None,
        )
        assert without, "brand-anchored queries must survive a missing industry"
        assert all(q in with_industry for q in without)
        assert any("Help Scout" in q for q in without)

    def test_brand_queries_come_first(self) -> None:
        """They survive truncation, and they are the classification-independent ones."""
        queries = build_queries(
            brand_name="Acme", domain="acme.com", industry="widgets", niche=None
        )
        assert "Acme" in queries[0]

    def test_falls_back_to_domain_without_a_brand_name(self) -> None:
        queries = build_queries(
            brand_name=None, domain="acme.com", industry=None, niche=None
        )
        assert any("acme.com" in q for q in queries)

    def test_niche_is_preferred_over_industry_as_the_seed(self) -> None:
        queries = build_queries(
            brand_name="X", domain="x.com",
            industry="dental practice", niche="cosmetic dentistry",
        )
        assert any("cosmetic dentistry" in q for q in queries)
        assert not any("dental practice" in q for q in queries)

    def test_no_industry_keyed_template_dictionary_exists(self) -> None:
        """Guards the agreed design decision against reintroduction.

        A dict keyed on industry would launder a bad classification into a
        confident-looking competitor set. Query shapes must stay generic.
        """
        import avp_api.services.serp as serp_module

        for name, value in vars(serp_module).items():
            if name.isupper() and isinstance(value, dict):
                assert not any(
                    isinstance(k, str) and " " in k for k in value
                ), f"{name} looks like an industry-keyed template dict"

    def test_seed_prompts_follow_the_same_rule(self) -> None:
        without = build_seed_prompts(
            brand_name="Help Scout", domain="helpscout.com", industry=None, niche=None
        )
        assert without
        assert all("Help Scout" in p for p in without)


class TestPublisherFiltering:
    @pytest.mark.parametrize(
        "domain", ["reddit.com", "g2.com", "wikipedia.org", "forbes.com", "yelp.com"]
    )
    def test_publishers_are_not_competitors(self, domain: str) -> None:
        assert not is_plausible_competitor(domain, subject_domain="acme.com")

    def test_the_subject_is_not_its_own_competitor(self) -> None:
        assert not is_plausible_competitor("acme.com", subject_domain="acme.com")

    def test_real_businesses_pass(self) -> None:
        for domain in ["zendesk.com", "front.com", "kayako.com"]:
            assert is_plausible_competitor(domain, subject_domain="helpscout.com")

    def test_exclusion_list_is_keyed_on_registrable_domains(self) -> None:
        """Entries must be eTLD+1, or matching silently fails."""
        for domain in NON_COMPETITOR_DOMAINS:
            assert "/" not in domain and not domain.startswith("www.")
            assert "." in domain


class TestSlugAndNames:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("Zendesk", "zendesk"), ("Zendesk Inc.", "zendesk"), ("Zoho Corp", "zoho"),
         ("Help  Scout", "helpscout"), ("Front", "front")],
    )
    def test_slugify_strips_noise_and_legal_suffixes(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    def test_short_names_are_not_eaten_by_suffix_stripping(self) -> None:
        """'Co' is a legal suffix, but 'Coco' must not become 'Co'."""
        assert slugify("Inc") == "inc"
        assert slugify("Coco") == "coco"

    def test_display_name_from_domain(self) -> None:
        assert display_name_from_domain("front.com") == "Front"
        assert display_name_from_domain("missive-app.com") == "Missive App"


class TestMerging:
    def test_same_domain_from_both_signals_is_one_candidate(self) -> None:
        merged = merge_candidates(
            [serp("q", ["front.com", "kayako.com"])],
            [cocit("p", [("Front", "front.com")])],
            subject_domain="helpscout.com", subject_name="Help Scout",
        )
        fronts = [c for c in merged if c.domain == "front.com"]
        assert len(fronts) == 1
        assert fronts[0].corroborated
        assert fronts[0].name == "Front"

    def test_name_only_cocitation_merges_with_a_serp_domain(self) -> None:
        """'Front' from an answer must match front.com from search."""
        merged = merge_candidates(
            [serp("q", ["front.com"])],
            [cocit("p", [("Front", None)])],
            subject_domain="x.com", subject_name="X",
        )
        assert len(merged) == 1
        assert merged[0].corroborated

    def test_legal_suffix_variants_do_not_split(self) -> None:
        merged = merge_candidates(
            [], [cocit("p1", [("Zendesk", None)]), cocit("p2", [("Zendesk Inc.", None)])],
            subject_domain="x.com", subject_name="X",
        )
        assert len(merged) == 1
        assert merged[0].co_citation_mentions == 2

    def test_subject_is_excluded_by_domain_and_by_name(self) -> None:
        merged = merge_candidates(
            [serp("q", ["helpscout.com", "front.com"])],
            [cocit("p", [("Help Scout", None), ("Front", "front.com")])],
            subject_domain="helpscout.com", subject_name="Help Scout",
        )
        assert {c.domain for c in merged} == {"front.com"}

    def test_publishers_from_model_output_are_filtered_too(self) -> None:
        """Models name G2 and Reddit as readily as search does."""
        merged = merge_candidates(
            [], [cocit("p", [("G2", "g2.com"), ("Zendesk", "zendesk.com")])],
            subject_domain="x.com", subject_name="X",
        )
        assert {c.domain for c in merged} == {"zendesk.com"}

    def test_failed_results_contribute_nothing(self) -> None:
        merged = merge_candidates(
            [SerpResult(query="q", ok=False, error_code="SERP_TIMEOUT")],
            [CoCitationResult(prompt="p", ok=False, error_code="PROVIDER_ERROR")],
            subject_domain="x.com", subject_name="X",
        )
        assert merged == []


class TestScoring:
    def test_corroborated_outranks_a_higher_volume_single_signal(self) -> None:
        """The central ranking claim.

        Agreement between two methods that fail differently beats volume within
        either one.
        """
        merged = merge_candidates(
            [serp("q1", ["loud.com"]), serp("q2", ["loud.com"]), serp("q3", ["loud.com"]),
             serp("q4", ["both.com"])],
            [cocit("p", [("Both", "both.com")])],
            subject_domain="x.com", subject_name="X",
        )
        ranked = score_candidates(merged)
        assert ranked[0].domain == "both.com"
        assert ranked[0].corroborated
        assert not ranked[1].corroborated

    def test_position_one_beats_position_ten(self) -> None:
        merged = merge_candidates(
            [serp("q", ["first.com"] + [f"f{i}.com" for i in range(2, 10)] + ["last.com"])],
            [], subject_domain="x.com", subject_name="X",
        )
        ranked = score_candidates(merged)
        assert ranked[0].domain == "first.com"
        assert ranked[-1].domain == "last.com"

    def test_neither_signal_dominates_by_hit_volume(self) -> None:
        """Each signal is normalised against its own max before combining.

        Six SERP queries return far more hits than four seed prompts; without
        normalisation that configuration artefact would decide the ranking.
        """
        merged = merge_candidates(
            [serp(f"q{i}", [f"s{j}.com" for j in range(8)]) for i in range(6)],
            [cocit("p", [("Solo", "solo.com")])],
            subject_domain="x.com", subject_name="X",
        )
        ranked = score_candidates(merged)
        solo = next(c for c in ranked if c.domain == "solo.com")
        top_serp = next(c for c in ranked if c.domain == "s0.com")
        assert solo.score >= top_serp.score * 0.8

    def test_ranking_is_deterministic(self) -> None:
        """Two reports of the same client must not swap competitor order."""
        def build() -> list[Candidate]:
            return score_candidates(
                merge_candidates(
                    [serp("q", ["a.com", "b.com", "c.com"])],
                    [cocit("p", [("A", "a.com"), ("C", "c.com")])],
                    subject_domain="x.com", subject_name="X",
                )
            )
        first = [(c.domain, c.score) for c in build()]
        for _ in range(5):
            assert [(c.domain, c.score) for c in build()] == first

    def test_source_reflects_which_signals_fired(self) -> None:
        ranked = score_candidates(
            merge_candidates(
                [serp("q", ["serponly.com", "both.com"])],
                [cocit("p", [("Both", "both.com"), ("CoOnly", "coonly.com")])],
                subject_domain="x.com", subject_name="X",
            )
        )
        by_domain = {c.domain: c.source for c in ranked}
        assert by_domain["both.com"] is DetectionSource.BOTH
        assert by_domain["serponly.com"] is DetectionSource.SERP
        assert by_domain["coonly.com"] is DetectionSource.CO_CITATION


class TestDetectionConfidence:
    def _ranked(self, serp_domains: list[str], cocit_brands: list[tuple[str, str | None]]):
        # TWO distinct queries, because production runs 3-6 and the SERP gate
        # (Epic 3.5) requires uncorroborated candidates to appear in at least
        # two. A single-query fixture would be gated out entirely and these
        # tests would silently stop exercising confidence and limit semantics.
        # The single-query case is covered deliberately in TestSerpGateRecallImpact.
        return score_candidates(
            merge_candidates(
                [serp("q1", serp_domains), serp("q2", serp_domains)],
                [cocit("p", cocit_brands)],
                subject_domain="x.com", subject_name="X",
            )
        )

    def test_full_agreement_is_one(self) -> None:
        ranked = self._ranked(
            ["a.com", "b.com", "c.com"],
            [("A", "a.com"), ("B", "b.com"), ("C", "c.com")],
        )
        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=1, used_industry_seed=True)
        assert outcome.detection_confidence == Decimal("1.000")
        assert outcome.status is DetectionStatus.OK

    def test_partial_agreement_is_a_fraction(self) -> None:
        ranked = self._ranked(
            ["a.com", "b.com", "c.com", "d.com"], [("A", "a.com"), ("Z", "z.com")]
        )
        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=1, used_industry_seed=True)
        assert outcome.detection_confidence is not None
        assert Decimal("0") < outcome.detection_confidence < Decimal("1")

    def test_one_signal_only_is_null_not_zero(self) -> None:
        """The distinction the whole field turns on.

        Zero would claim two signals looked and disagreed. In fact only one ran,
        so agreement is unmeasurable — and must be reported as such.
        """
        ranked = self._ranked(["a.com", "b.com", "c.com"], [])
        outcome = decide_detection(ranked, serp_ok=6, co_citation_ok=0, used_industry_seed=True)
        assert outcome.detection_confidence is None
        assert outcome.status is DetectionStatus.WEAK_SIGNAL

    def test_no_candidates_is_no_signal(self) -> None:
        outcome = decide_detection([], serp_ok=6, co_citation_ok=4, used_industry_seed=True)
        assert outcome.status is DetectionStatus.NO_SIGNAL
        assert outcome.detection_confidence is None
        assert outcome.candidates == []

    def test_zero_corroboration_downgrades_to_weak(self) -> None:
        """Both signals ran and agreed on nothing — an operator should look."""
        ranked = self._ranked(["a.com", "b.com", "c.com"], [("Z", "z.com"), ("Y", "y.com")])
        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=1, used_industry_seed=True)
        assert outcome.detection_confidence == Decimal("0.000")
        assert outcome.status is DetectionStatus.WEAK_SIGNAL

    def test_too_few_competitors_is_weak(self) -> None:
        ranked = self._ranked(["a.com"], [("A", "a.com")])
        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=1, used_industry_seed=True)
        assert outcome.status is DetectionStatus.WEAK_SIGNAL

    def test_returns_at_most_five(self) -> None:
        ranked = self._ranked([f"d{i}.com" for i in range(12)], [])
        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=1, used_industry_seed=True)
        assert len(outcome.candidates) == 5
        assert outcome.candidates_considered == 12

    def test_industry_seed_usage_is_recorded(self) -> None:
        ranked = self._ranked(["a.com"], [])
        assert not decide_detection(
            ranked, serp_ok=1, co_citation_ok=1, used_industry_seed=False
        ).used_industry_seed


class TestSerpGate:
    """The SERP-only gate (build-log Epic 3.5).

    A candidate seen by only one signal, in only one query, is the profile that
    measured 45% precision in the 10-URL verification. Corroborated and
    model-named candidates both measured 100% and are exempt.
    """

    def _candidates(  # noqa: ANN202
        self,
        serp_queries: list[tuple[str, list[str]]],
        brands: list[tuple[str, str | None]] | None = None,
    ):
        return score_candidates(
            merge_candidates(
                [serp(q, domains) for q, domains in serp_queries],
                [cocit("p", brands)] if brands else [],
                subject_domain="x.com",
                subject_name="X",
            )
        )

    def test_threshold_is_two(self) -> None:
        """Pinned so a silent change to the constant fails loudly."""
        from avp_api.services.competitors import MIN_SERP_QUERIES_FOR_UNCORROBORATED

        assert MIN_SERP_QUERIES_FOR_UNCORROBORATED == 2

    def test_one_query_hit_is_excluded(self) -> None:
        # Three eligible candidates are required for the gate to be observable:
        # below MIN_COMPETITORS_FOR_OK the floor backfills gated candidates, and
        # the exclusion would be masked. The floor itself is tested in
        # TestSerpGateFloor.
        ranked = self._candidates([
            ("q1", ["a.com", "b.com", "c.com", "solo.com"]),
            ("q2", ["a.com", "b.com", "c.com"]),
        ])
        solo = next(c for c in ranked if c.domain == "solo.com")
        assert solo.distinct_serp_queries == 1
        assert not solo.passes_serp_gate()

        outcome = decide_detection(ranked, serp_ok=2, co_citation_ok=1, used_industry_seed=True)
        assert "solo.com" not in {c.domain for c in outcome.candidates}
        assert len(outcome.candidates) == 3

    def test_two_query_hits_are_included(self) -> None:
        """The exact boundary — one more query flips it."""
        ranked = self._candidates([("q1", ["repeat.com"]), ("q2", ["repeat.com"])])
        repeat = next(c for c in ranked if c.domain == "repeat.com")
        assert repeat.distinct_serp_queries == 2
        assert repeat.passes_serp_gate()

        outcome = decide_detection(ranked, serp_ok=2, co_citation_ok=1, used_industry_seed=True)
        assert "repeat.com" in {c.domain for c in outcome.candidates}

    def test_same_query_twice_does_not_count_twice(self) -> None:
        """Two hits from ONE query is still one query's worth of evidence.

        Guards the difference between `len(serp_positions)` (hit count) and
        `distinct_serp_queries` (independent-query count) — using the former
        would let a single query that lists a domain twice satisfy the gate.
        """
        ranked = score_candidates(
            merge_candidates(
                [SerpResult(query="q1", hits=[
                    SerpHit(domain="dupe.com", position=1, query="q1"),
                    SerpHit(domain="dupe.com", position=7, query="q1"),
                ])],
                [], subject_domain="x.com", subject_name="X",
            )
        )
        dupe = ranked[0]
        assert len(dupe.serp_positions) == 2
        assert dupe.distinct_serp_queries == 1
        assert not dupe.passes_serp_gate()

    def test_corroborated_candidates_are_exempt(self) -> None:
        """100% measured precision — the gate must not touch them."""
        ranked = self._candidates([("q1", ["both.com"])], brands=[("Both", "both.com")])
        both = next(c for c in ranked if c.domain == "both.com")
        assert both.distinct_serp_queries == 1
        assert both.corroborated
        assert both.passes_serp_gate()

    def test_co_citation_only_candidates_are_exempt(self) -> None:
        """Also 100% measured — a model naming a brand is its own evidence."""
        ranked = self._candidates(
            [("q1", ["ignored.com"])], brands=[("Named", "named.com")]
        )
        named = next(c for c in ranked if c.domain == "named.com")
        assert named.distinct_serp_queries == 0
        assert named.passes_serp_gate()

    def test_gating_frees_the_slot_rather_than_shortening_the_set(self) -> None:
        """A gated candidate must not cost the set a rank.

        Filtering happens before truncation, so an eligible sixth candidate is
        promoted rather than the set coming back one short.
        """
        serp_queries = [
            ("q1", ["a.com", "b.com", "c.com", "d.com", "e.com", "f.com"]),
            ("q2", ["a.com", "b.com", "c.com", "d.com", "e.com", "f.com"]),
        ]
        ranked = self._candidates([*serp_queries, ("q3", ["oneoff.com"])])
        outcome = decide_detection(ranked, serp_ok=3, co_citation_ok=1, used_industry_seed=True)
        assert len(outcome.candidates) == 5
        assert "oneoff.com" not in {c.domain for c in outcome.candidates}

    def test_candidates_considered_still_counts_everything(self) -> None:
        """Provenance must reflect what was examined, not what was returned."""
        ranked = self._candidates([
            ("q1", ["a.com", "b.com", "c.com", "solo.com"]),
            ("q2", ["a.com", "b.com", "c.com"]),
        ])
        outcome = decide_detection(ranked, serp_ok=2, co_citation_ok=1, used_industry_seed=True)
        assert outcome.candidates_considered == 4, "the gated candidate is still evidence"
        assert len(outcome.candidates) == 3

    def test_gate_does_not_break_determinism(self) -> None:
        def build():  # noqa: ANN202
            ranked = self._candidates(
                [("q1", ["a.com", "b.com", "solo.com"]), ("q2", ["a.com", "b.com"])],
                brands=[("A", "a.com")],
            )
            return decide_detection(
                ranked, serp_ok=2, co_citation_ok=1, used_industry_seed=True
            )

        first = [(c.domain, c.score) for c in build().candidates]
        for _ in range(8):
            assert [(c.domain, c.score) for c in build().candidates] == first


class TestSerpGateRecallImpact:
    """Thin-data behaviour, now protected by the floor (Epic 3.5 addendum).

    When co-citation is unavailable every candidate is SERP-only and the gate is
    the only filter left. Without a floor the gate could empty such a set
    entirely; the floor backfills the highest-scoring gated candidates up to
    MIN_COMPETITORS_FOR_OK so an operator gets something to correct.
    """

    def test_single_signal_thin_result_is_backfilled_not_emptied(self) -> None:
        """One query, three domains, no co-citation.

        Every candidate fails the gate. Before the floor this returned
        NO_SIGNAL with an empty set; it now returns three backfilled rivals
        flagged WEAK_SIGNAL. Showing an operator three weakly-evidenced names
        they can correct beats showing them nothing.
        """
        ranked = score_candidates(
            merge_candidates(
                [serp("only-query", ["a.com", "b.com", "c.com"])],
                [], subject_domain="x.com", subject_name="X",
            )
        )
        assert not any(c.passes_serp_gate() for c in ranked), "all gated"

        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=0, used_industry_seed=True)
        assert outcome.status is DetectionStatus.WEAK_SIGNAL
        assert len(outcome.candidates) == MIN_COMPETITORS_FOR_OK
        assert outcome.detection_confidence is None, "one signal — agreement unmeasurable"
        assert outcome.candidates_considered == 3

    def test_backfill_takes_the_highest_scoring_gated_candidates(self) -> None:
        """Order among backfilled rows still follows score."""
        ranked = score_candidates(
            merge_candidates(
                [serp("only-query", ["top.com", "mid.com", "low.com", "lowest.com"])],
                [], subject_domain="x.com", subject_name="X",
            )
        )
        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=0, used_industry_seed=True)
        # Position 1 scores highest, so the first three by SERP position win.
        assert [c.domain for c in outcome.candidates] == ["top.com", "mid.com", "low.com"]

    def test_single_signal_with_realistic_query_count_keeps_the_real_one_first(self) -> None:
        """Production runs 3-6 queries, not one.

        `real.com` appears in all three and clears the gate on its own merits;
        the single-query noise domains are gated but backfilled to reach the
        floor. The eligible candidate must still rank FIRST — backfilled rows are
        appended after eligible ones, because passing the gate is stronger
        evidence than a raw score.
        """
        ranked = score_candidates(
            merge_candidates(
                [
                    serp("q1", ["real.com", "noise1.com"]),
                    serp("q2", ["real.com", "noise2.com"]),
                    serp("q3", ["real.com", "noise3.com"]),
                ],
                [], subject_domain="x.com", subject_name="X",
            )
        )
        outcome = decide_detection(ranked, serp_ok=3, co_citation_ok=0, used_industry_seed=True)
        assert outcome.candidates[0].domain == "real.com"
        assert len(outcome.candidates) == MIN_COMPETITORS_FOR_OK
        assert outcome.status is DetectionStatus.WEAK_SIGNAL
        assert outcome.detection_confidence is None


class TestSerpGateFloor:
    """The floor's exact boundary."""

    def test_three_eligible_does_not_trigger_backfill(self) -> None:
        """At the floor exactly — the gated candidate stays out."""
        ranked = score_candidates(
            merge_candidates(
                [
                    serp("q1", ["a.com", "b.com", "c.com", "gated.com"]),
                    serp("q2", ["a.com", "b.com", "c.com"]),
                ],
                [], subject_domain="x.com", subject_name="X",
            )
        )
        assert sum(1 for c in ranked if c.passes_serp_gate()) == 3
        outcome = decide_detection(ranked, serp_ok=2, co_citation_ok=1, used_industry_seed=True)
        assert len(outcome.candidates) == 3
        assert "gated.com" not in {c.domain for c in outcome.candidates}

    def test_two_eligible_triggers_backfill_of_exactly_one(self) -> None:
        """One below the floor — exactly one gated candidate is promoted.

        The boundary that matters: backfill tops up to the floor and stops. It
        must not readmit every gated candidate.
        """
        ranked = score_candidates(
            merge_candidates(
                [
                    serp("q1", ["a.com", "b.com", "gated1.com", "gated2.com"]),
                    serp("q2", ["a.com", "b.com"]),
                ],
                [], subject_domain="x.com", subject_name="X",
            )
        )
        assert sum(1 for c in ranked if c.passes_serp_gate()) == 2
        outcome = decide_detection(ranked, serp_ok=2, co_citation_ok=1, used_industry_seed=True)

        domains = [c.domain for c in outcome.candidates]
        assert len(domains) == 3, "topped up to the floor, not beyond"
        assert domains[:2] == ["a.com", "b.com"], "eligible candidates rank first"
        assert domains[2] in {"gated1.com", "gated2.com"}
        assert not (set(domains) >= {"gated1.com", "gated2.com"}), "only one promoted"

    def test_backfilled_sets_are_never_reported_as_ok(self) -> None:
        """A set containing rows that failed the evidence bar needs an operator.

        Uses the existing WEAK_SIGNAL flag rather than a new status.
        """
        ranked = score_candidates(
            merge_candidates(
                [
                    serp("q1", ["a.com", "b.com", "gated.com"]),
                    serp("q2", ["a.com", "b.com"]),
                ],
                [cocit("p", [("A", "a.com"), ("B", "b.com")])],
                subject_domain="x.com", subject_name="X",
            )
        )
        outcome = decide_detection(ranked, serp_ok=2, co_citation_ok=1, used_industry_seed=True)
        assert "gated.com" in {c.domain for c in outcome.candidates}
        # Both corroborated candidates are present, so without the backfill this
        # would have been OK with confidence 1.000.
        assert outcome.status is DetectionStatus.WEAK_SIGNAL
        assert outcome.detection_confidence == Decimal("0.667")

    def test_floor_cannot_invent_candidates(self) -> None:
        """Nothing to backfill from means nothing is returned."""
        outcome = decide_detection([], serp_ok=3, co_citation_ok=2, used_industry_seed=True)
        assert outcome.status is DetectionStatus.NO_SIGNAL
        assert outcome.candidates == []

    def test_floor_stops_when_gated_candidates_run_out(self) -> None:
        """Two gated candidates and none eligible -> a set of two, not three."""
        ranked = score_candidates(
            merge_candidates(
                [serp("q1", ["a.com", "b.com"])],
                [], subject_domain="x.com", subject_name="X",
            )
        )
        outcome = decide_detection(ranked, serp_ok=1, co_citation_ok=0, used_industry_seed=True)
        assert len(outcome.candidates) == 2
        assert outcome.status is DetectionStatus.WEAK_SIGNAL

    def test_floor_is_deterministic(self) -> None:
        def build():  # noqa: ANN202
            ranked = score_candidates(
                merge_candidates(
                    [
                        serp("q1", ["a.com", "b.com", "g1.com", "g2.com", "g3.com"]),
                        serp("q2", ["a.com", "b.com"]),
                    ],
                    [], subject_domain="x.com", subject_name="X",
                )
            )
            return decide_detection(
                ranked, serp_ok=2, co_citation_ok=0, used_industry_seed=True
            )

        first = [c.domain for c in build().candidates]
        for _ in range(8):
            assert [c.domain for c in build().candidates] == first


class TestFloorIsANoOpAboveTheFloor:
    """The floor cannot have changed Epic 3.5's live numbers.

    Every one of the ten sites in that run returned a full set of five, so at
    least five candidates passed the gate in each case — comfortably above
    MIN_COMPETITORS_FOR_OK. This proves the property directly rather than
    re-running the paid verification script: when enough candidates pass the
    gate, the floor is inert and the outcome is byte-identical to pre-floor
    behaviour.
    """

    def _outcome(self, serp_queries, brands=None, **kw):  # noqa: ANN001, ANN003, ANN202
        ranked = score_candidates(
            merge_candidates(
                [serp(q, domains) for q, domains in serp_queries],
                [cocit("p", brands)] if brands else [],
                subject_domain="x.com", subject_name="X",
            )
        )
        return ranked, decide_detection(ranked, used_industry_seed=True, **kw)

    def test_no_backfill_occurs_when_five_candidates_pass(self) -> None:
        five = ["a.com", "b.com", "c.com", "d.com", "e.com"]
        ranked, outcome = self._outcome(
            [("q1", [*five, "gated.com"]), ("q2", five)],
            serp_ok=2, co_citation_ok=1,
        )
        eligible = [c for c in ranked if c.passes_serp_gate()]
        assert len(eligible) == 5

        returned = [c.domain for c in outcome.candidates]
        # Identical to simply taking the top five eligible — no backfill, and
        # no reordering.
        assert returned == [c.domain for c in eligible[:5]]
        assert "gated.com" not in returned

    def test_status_and_confidence_are_untouched_above_the_floor(self) -> None:
        """A fully corroborated set stays OK with confidence 1.000.

        If the floor leaked into this path it would force WEAK_SIGNAL, which is
        exactly the regression that would have altered Epic 3.5's table.
        """
        domains = ["a.com", "b.com", "c.com"]
        _, outcome = self._outcome(
            [("q1", [*domains, "gated.com"]), ("q2", domains)],
            brands=[("A", "a.com"), ("B", "b.com"), ("C", "c.com")],
            serp_ok=2, co_citation_ok=1,
        )
        assert outcome.status is DetectionStatus.OK
        assert outcome.detection_confidence == Decimal("1.000")
        assert len(outcome.candidates) == 3

    def test_exactly_at_the_floor_is_still_inert(self) -> None:
        """Three eligible is the boundary — inert, not backfilled."""
        domains = ["a.com", "b.com", "c.com"]
        ranked, outcome = self._outcome(
            [("q1", [*domains, "g1.com", "g2.com"]), ("q2", domains)],
            serp_ok=2, co_citation_ok=1,
        )
        assert len([c for c in ranked if c.passes_serp_gate()]) == 3
        assert {c.domain for c in outcome.candidates} == set(domains)


class TestDetectionHasOneImplementation:
    """Epic 3.11 / Finding 5: the script and the API must run the same code.

    `scripts/verify_competitors.py` is the live check for §7 Epic 3's
    acceptance criterion and holds no database connection by design, so it
    could not call `detect_for_client` and had reassembled the pipeline from
    its parts instead. The two were equivalent when measured, so the precision
    figure it reported was sound — but nothing would have caught them drifting,
    and a verification script that measures a copy is the trap Epic 4.0 named
    and this codebase has now hit three times.

    `detect_from_facts` is that code, taking the six scalars detection actually
    needs. These assert the wrapper adds nothing, so the script calling one and
    the API calling the other cannot diverge.
    """

    @staticmethod
    def _stub(monkeypatch, domains, brands):  # noqa: ANN001, ANN205
        async def fake_search_many(queries, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return [
                SerpResult(
                    query=q,
                    hits=[SerpHit(domain=d, position=i, query=q)
                          for i, d in enumerate(domains, 1)],
                )
                for q in queries[:2]
            ]

        async def fake_run_prompts(prompts, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return [
                CoCitationResult(
                    prompt=prompts[0],
                    hits=[CoCitationHit(name=n, domain=d, position=i, prompt=prompts[0])
                          for i, (n, d) in enumerate(brands, 1)],
                )
            ]

        monkeypatch.setattr(detection.serp_service, "search_many", fake_search_many)
        monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", fake_run_prompts)

    async def test_the_wrapper_returns_exactly_what_the_core_returns(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        """A Client and its six scalars must produce the identical outcome.

        If `detect_for_client` ever grows logic of its own, the script stops
        verifying what the API runs and Finding 5 is reopened silently. This is
        what makes that impossible to do by accident.
        """
        from avp_api.models import Client
        from avp_api.models.client import ClassificationStatus, ClientKind

        self._stub(
            monkeypatch,
            ["rival-one.example", "rival-two.example"],
            [("Rival One", "rival-one.example"), ("Rival Two", "rival-two.example")],
        )

        client = Client(
            id="clnt_test", agency_id="agcy_test",
            name="Subject Co", brand_name="Subject", domain="subject.example",
            kind=ClientKind.PROSPECT,
            classification_status=ClassificationStatus.CLASSIFIED,
            industry="widget supply", industry_niche="regional widgets",
        )

        via_client = await detection.detect_for_client(client)
        via_facts = await detection.detect_from_facts(
            brand_name="Subject", domain="subject.example",
            industry="widget supply", niche="regional widgets", name="Subject Co",
        )

        assert via_client.status is via_facts.status
        assert via_client.detection_confidence == via_facts.detection_confidence
        assert via_client.candidates_considered == via_facts.candidates_considered
        assert via_client.used_industry_seed == via_facts.used_industry_seed
        assert [
            (c.resolved_name(), c.domain, c.source, c.corroborated)
            for c in via_client.candidates
        ] == [
            (c.resolved_name(), c.domain, c.source, c.corroborated)
            for c in via_facts.candidates
        ]
        assert via_client.candidates, "the fixture must actually detect something"

    async def test_the_core_needs_no_client_and_no_database(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        """The property that lets the verification script call it at all.

        `verify_competitors.py` is one of three scripts that cannot touch a
        database by construction, and that is worth keeping. If detection ever
        acquires a session or a query, this fails before the script does.
        """
        import inspect

        self._stub(monkeypatch, ["a.example"], [("A", "a.example")])
        outcome = await detection.detect_from_facts(
            brand_name="Subject", domain="subject.example"
        )
        assert outcome.candidates_considered >= 0

        source = inspect.getsource(detection.detect_from_facts)
        for forbidden in ("session", "select(", "commit", "execute("):
            assert forbidden not in source, (
                f"detect_from_facts touches {forbidden!r} — the verification "
                "script holds no database connection and could no longer call it"
            )

    def test_the_wrapper_holds_no_logic(self) -> None:
        """`detect_for_client` must stay a pure unpack.

        Asserted at source level because the failure is silent: logic added
        here runs in production and not in the script, which is exactly the
        divergence Finding 5 was about.
        """
        import inspect

        source = inspect.getsource(detection.detect_for_client)
        body = source.split('"""')[-1]
        assert "detect_from_facts(" in body
        for forbidden in ("build_queries", "merge_candidates", "decide_detection",
                          "score_candidates", "search_many"):
            assert forbidden not in body, (
                f"detect_for_client calls {forbidden} directly — it must delegate"
            )

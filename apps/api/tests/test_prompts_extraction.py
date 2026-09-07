"""Prompt generation policy and fact extraction.

Pure functions, no network. The live scan against real engines is a separate
verification — scripts/verify_scan.py.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from avp_api.models.engine_result import CitationType, Engine, EngineResultStatus
from avp_api.models.prompt import PromptIntent
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.extraction import (
    classify_citation,
    extract_facts,
    find_brand,
)
from avp_api.services.prompts import (
    MAX_PROMPTS,
    MIN_PROMPTS,
    GeneratedPrompt,
    build_generation_input,
    enforce_intent_mix,
    fallback_prompts,
)


def answer(text: str, *, citations=None, status=EngineResultStatus.OK) -> EngineAnswer:  # noqa: ANN001
    return EngineAnswer(
        engine=Engine.CLAUDE, engine_version="test", prompt_text="q",
        text=text, citations=citations or [], status=status,
    )


class TestPromptSeeding:
    def test_generation_input_survives_a_missing_industry(self) -> None:
        """Finding 2 hedge: an unclassified client still gets a usable brief."""
        text = build_generation_input(
            brand_name="Help Scout", domain="helpscout.com", industry=None, niche=None
        )
        assert "Help Scout" in text and "helpscout.com" in text

    def test_industry_is_labelled_as_possibly_imprecise(self) -> None:
        """The generator is told not to trust the label too hard."""
        text = build_generation_input(
            brand_name="X", domain="x.com", industry="dental practice", niche=None
        )
        assert "may be imprecise" in text

    def test_fallback_is_brand_anchored_and_needs_no_industry(self) -> None:
        prompts = fallback_prompts(brand_name="Help Scout", domain="helpscout.com", industry=None)
        assert prompts
        assert any("Help Scout" in p.text for p in prompts)
        assert {p.intent for p in prompts} == set(PromptIntent)

    def test_fallback_is_deterministic(self) -> None:
        a = fallback_prompts(brand_name="X", domain="x.com", industry="widgets")
        b = fallback_prompts(brand_name="X", domain="x.com", industry="widgets")
        assert [(p.text, p.intent) for p in a] == [(p.text, p.intent) for p in b]

    def test_no_industry_keyed_template_dictionary_exists(self) -> None:
        """Extends the Epic 3 guard to prompt generation.

        A dict keyed on industry would turn an uncalibrated classification into
        a confident-looking scan whose prompt set nothing downstream can audit.
        """
        import avp_api.services.prompts as prompts_module

        for name, value in vars(prompts_module).items():
            if name.isupper() and isinstance(value, dict):
                assert not any(
                    isinstance(k, str) and " " in k for k in value
                ), f"{name} looks like an industry-keyed template dict"


class TestIntentMix:
    def _generated(self, counts: dict[PromptIntent, int]) -> list[GeneratedPrompt]:
        out = []
        for intent, n in counts.items():
            out.extend(
                GeneratedPrompt(text=f"{intent.value} question {i}", intent=intent)
                for i in range(n)
            )
        return out

    def test_caps_at_the_maximum(self) -> None:
        kept = enforce_intent_mix(self._generated({i: 40 for i in PromptIntent}))
        assert len(kept) <= MAX_PROMPTS

    def test_enforces_the_quota_rather_than_trusting_the_model(self) -> None:
        """A model asked for 'a mix' returns whatever mix it likes.

        The mix decides what the score measures, so it is applied in code.
        """
        kept = enforce_intent_mix(
            self._generated({
                PromptIntent.AWARENESS: 2,
                PromptIntent.COMPARISON: 40,
                PromptIntent.BOTTOM_FUNNEL: 2,
            }),
            target=20,
        )
        mix = Counter(p.intent for p in kept)
        assert mix[PromptIntent.COMPARISON] < 40, "the skew must be trimmed"
        assert mix[PromptIntent.AWARENESS] == 2, "under-supply is not padded"

    def test_deduplicates_case_insensitively(self) -> None:
        kept = enforce_intent_mix([
            GeneratedPrompt(text="Best CRM software", intent=PromptIntent.AWARENESS),
            GeneratedPrompt(text="best crm software", intent=PromptIntent.AWARENESS),
            GeneratedPrompt(text="  best   crm software  ", intent=PromptIntent.AWARENESS),
        ])
        assert len(kept) == 1

    def test_never_pads_with_near_duplicates(self) -> None:
        """Padding would inflate the denominator of every rate scoring computes."""
        kept = enforce_intent_mix(self._generated({PromptIntent.AWARENESS: 3}), target=20)
        assert len(kept) == 3

    def test_is_deterministic(self) -> None:
        generated = self._generated({i: 12 for i in PromptIntent})
        first = [(p.text, p.intent) for p in enforce_intent_mix(generated)]
        for _ in range(5):
            assert [(p.text, p.intent) for p in enforce_intent_mix(generated)] == first

    def test_min_and_max_bracket_the_spec_range(self) -> None:
        assert (MIN_PROMPTS, MAX_PROMPTS) == (20, 30)

    def test_prompts_are_interleaved_so_any_prefix_is_representative(self) -> None:
        """Order matters as much as ratio.

        `position` follows this order, and anything taking a PREFIX — the API's
        promptLimit, a run cut short by a rate limit — would otherwise measure
        awareness only, while reporting a mention rate that looks whole. Found
        by the Epic 4 live run, where all six executed prompts were awareness.
        """
        kept = enforce_intent_mix(
            self._generated({
                PromptIntent.AWARENESS: 12,
                PromptIntent.COMPARISON: 8,
                PromptIntent.BOTTOM_FUNNEL: 4,
            })
        )
        # One of each in the first three, and every intent present by six.
        assert len(set(p.intent for p in kept[:3])) == 3
        assert set(p.intent for p in kept[:6]) == set(PromptIntent)

    def test_interleaving_preserves_the_overall_quota(self) -> None:
        """Reordering only — the totals must not shift."""
        kept = enforce_intent_mix(
            self._generated({
                PromptIntent.AWARENESS: 12,
                PromptIntent.COMPARISON: 8,
                PromptIntent.BOTTOM_FUNNEL: 4,
            })
        )
        mix = Counter(p.intent for p in kept)
        assert mix[PromptIntent.AWARENESS] > mix[PromptIntent.COMPARISON]
        assert mix[PromptIntent.COMPARISON] > mix[PromptIntent.BOTTOM_FUNNEL]

    def test_interleaving_is_deterministic(self) -> None:
        """Same generated set, same persisted order — positions must be stable."""
        generated = self._generated({
            PromptIntent.AWARENESS: 9,
            PromptIntent.COMPARISON: 7,
            PromptIntent.BOTTOM_FUNNEL: 5,
        })
        first = [(p.text, p.intent) for p in enforce_intent_mix(generated)]
        for _ in range(6):
            assert [(p.text, p.intent) for p in enforce_intent_mix(generated)] == first

    def test_a_single_intent_set_still_works(self) -> None:
        """Interleaving must not stall when only one bucket has entries."""
        kept = enforce_intent_mix(self._generated({PromptIntent.AWARENESS: 8}))
        assert len(kept) == 8
        assert all(p.intent is PromptIntent.AWARENESS for p in kept)


class TestBrandMatching:
    def test_finds_a_brand_by_name(self) -> None:
        assert find_brand("I recommend Zendesk for this.", name="Zendesk", domain=None) is not None

    def test_finds_a_brand_by_domain(self) -> None:
        assert (
            find_brand("See zendesk.com for details.", name="Nope", domain="zendesk.com")
            is not None
        )

    def test_does_not_match_inside_a_longer_word(self) -> None:
        """Word boundaries — 'Front' must not match 'Frontier' or 'confront'."""
        assert find_brand("Frontier Airlines confronted it", name="Front", domain=None) is None

    def test_short_names_are_matched_case_sensitively(self) -> None:
        """'On' the running brand must not match the preposition 'on'.

        Short names are the main false-positive source in mention detection, and
        a false mention inflates the headline metric of the entire product.
        """
        assert find_brand("put it on the shelf", name="On", domain=None) is None
        assert find_brand("On makes running shoes", name="On", domain=None) is not None

    def test_longer_names_are_case_insensitive(self) -> None:
        assert find_brand("we use zendesk daily", name="Zendesk", domain=None) is not None

    def test_absent_brand_returns_none(self) -> None:
        assert find_brand("nothing relevant here", name="Zendesk", domain="zendesk.com") is None


class TestExtraction:
    SUBJECT = {"subject_name": "Help Scout", "subject_domain": "helpscout.com"}
    COMPS = [("Zendesk", "zendesk.com"), ("Front", "front.com"), ("Intercom", "intercom.com")]

    def test_detects_the_subject_and_orders_brands_by_appearance(self) -> None:
        facts = extract_facts(
            answer("Zendesk is popular. Help Scout is simpler. Intercom is pricey."),
            competitors=self.COMPS, **self.SUBJECT,
        )
        assert facts.mentioned
        assert facts.brands_mentioned == 3
        assert [h.name for h in facts.brand_hits] == ["Zendesk", "Help Scout", "Intercom"]
        assert facts.position == 2, "position is the subject's rank by first appearance"

    def test_unmentioned_subject_is_answered_no_mention(self) -> None:
        facts = extract_facts(
            answer("Zendesk and Intercom are the leaders."),
            competitors=self.COMPS, **self.SUBJECT,
        )
        assert not facts.mentioned
        assert facts.position is None
        assert facts.status is EngineResultStatus.ANSWERED_NO_MENTION
        assert facts.brands_mentioned == 2

    def test_prominence_is_higher_when_named_earlier(self) -> None:
        early = extract_facts(
            answer("Help Scout leads. " + "filler text. " * 40),
            competitors=[], **self.SUBJECT,
        )
        late = extract_facts(
            answer("filler text. " * 40 + "Help Scout is also an option."),
            competitors=[], **self.SUBJECT,
        )
        assert early.prominence is not None and late.prominence is not None
        assert early.prominence > late.prominence
        assert Decimal("0") <= late.prominence <= Decimal("1")

    def test_extraction_is_deterministic(self) -> None:
        text = "Zendesk, then Help Scout, then Front."
        first = extract_facts(answer(text), competitors=self.COMPS, **self.SUBJECT)
        for _ in range(5):
            again = extract_facts(answer(text), competitors=self.COMPS, **self.SUBJECT)
            assert (again.position, again.prominence, again.brands_mentioned) == (
                first.position, first.prominence, first.brands_mentioned
            )

    def test_failed_answer_yields_no_facts(self) -> None:
        facts = extract_facts(
            answer("", status=EngineResultStatus.TIMEOUT),
            competitors=self.COMPS, **self.SUBJECT,
        )
        assert facts.status is EngineResultStatus.TIMEOUT
        assert not facts.mentioned
        assert facts.brands_mentioned == 0

    @pytest.mark.parametrize(
        "status", [EngineResultStatus.TRUNCATED, EngineResultStatus.PAUSED]
    )
    def test_an_incomplete_answer_yields_no_facts_even_when_text_is_present(
        self, status: EngineResultStatus
    ) -> None:
        """The adapters blank the text of an incomplete answer, but extraction
        must not depend on that: `ok` is False, so it stops at the door. The
        text here names a competitor and not the subject — exactly the shape
        that used to be recorded as ANSWERED_NO_MENTION."""
        facts = extract_facts(
            answer("Zendesk is popular for larger teams, while Help Sc", status=status),
            competitors=self.COMPS, **self.SUBJECT,
        )
        assert facts.status is status
        assert not facts.mentioned
        assert facts.brands_mentioned == 0
        assert facts.citations == []

    def test_sentiment_is_not_set_by_pure_extraction(self) -> None:
        """Sentiment needs a model call and is applied by the runner, only when
        the subject was actually mentioned."""
        facts = extract_facts(answer("Help Scout is great"), competitors=[], **self.SUBJECT)
        assert facts.sentiment is None


class TestCitations:
    def test_citations_are_typed(self) -> None:
        facts = extract_facts(
            answer("Some answer.", citations=[
                CitedSource(url="https://helpscout.com/a", domain="helpscout.com", position=1),
                CitedSource(url="https://zendesk.com/b", domain="zendesk.com", position=2),
                CitedSource(url="https://g2.com/c", domain="g2.com", position=3),
                CitedSource(url="https://reddit.com/d", domain="reddit.com", position=4),
                CitedSource(url="https://forbes.com/e", domain="forbes.com", position=5),
                CitedSource(
                    url="https://unknown-blog.com/f", domain="unknown-blog.com", position=6
                ),
            ]),
            subject_name="Help Scout", subject_domain="helpscout.com",
            competitors=[("Zendesk", "zendesk.com")],
        )
        by_domain = {c.domain: c for c in facts.citations}
        assert by_domain["helpscout.com"].source_type is CitationType.OWNED
        assert by_domain["helpscout.com"].cites_subject is True
        assert by_domain["zendesk.com"].source_type is CitationType.COMPETITOR
        assert by_domain["g2.com"].source_type is CitationType.REVIEW
        assert by_domain["reddit.com"].source_type is CitationType.SOCIAL
        assert by_domain["forbes.com"].source_type is CitationType.EDITORIAL
        assert by_domain["unknown-blog.com"].source_type is CitationType.OTHER

    @pytest.mark.parametrize(
        ("domain", "expected"),
        [("helpscout.com", CitationType.OWNED), ("zendesk.com", CitationType.COMPETITOR),
         ("yelp.com", CitationType.REVIEW), ("crunchbase.com", CitationType.DIRECTORY)],
    )
    def test_classify_citation(self, domain: str, expected: CitationType) -> None:
        source_type, _ = classify_citation(
            domain, subject_domain="helpscout.com", competitor_domains={"zendesk.com"}
        )
        assert source_type is expected


class TestDigest:
    def test_digest_is_stable_across_whitespace_changes(self) -> None:
        """Trivial reformatting must not read as a substantive answer change."""
        a = answer("Help  Scout   is\ngood")
        b = answer("Help Scout is good")
        assert a.digest() == b.digest()

    def test_digest_changes_with_content(self) -> None:
        assert answer("one").digest() != answer("two").digest()

    def test_digest_is_sha256_length(self) -> None:
        digest = answer("x").digest()
        assert digest is not None and len(digest) == 64

    def test_empty_answer_has_no_digest(self) -> None:
        assert answer("").digest() is None

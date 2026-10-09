"""Probable rivals: the rule, as pure functions — F2 (2026-10-09), the founder's decision.

A domain is a PROBABLE RIVAL of a client only if (a) it has the client's own restricted ending
(edu.au for rmit.edu.au, and likewise the other restricted endings of the 14 markets) AND (b) the
scan's answers named a brand whose name matches its label ("Deakin University" for
deakin.edu.au). This file holds the pieces of that rule that need no database:

* the table of restricted endings, which covers every one of the 14 markets;
* `restricted_ending`, which says whether a domain has one (the SAME suffix as the client: a
  school under vic.edu.au is not under edu.au, and deakin.com is not under anything restricted);
* `rival_name`, the name a suggestion is added with ("UNSW", "Deakin");
* `named_domains`, which counts, for every cited domain with the client's ending, the answers
  that named its label. It counts ANSWERS and not mentions, ignores an answer that failed, and is
  total over odd input, because it runs inside a scan after every answer has been paid for;
* `domains_named_in_answers`, which reads the stored fact back.

The matcher is `extraction.find_brand`, untouched (MATCHER_VERSION does not move).
"""

from __future__ import annotations

import hashlib
import inspect
import random
import re
from typing import Any

import pytest

from avp_api.models.engine_result import (
    ANSWERED_STATUSES,
    CitationType,
    Engine,
    EngineResultStatus,
)
from avp_api.services import competitors, extraction, probable_rivals
from avp_api.services.crawl import registrable_domain
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.extraction import CitationFact, ExtractedFacts, find_brand
from avp_api.services.intake import dns_form
from avp_api.services.probable_rivals import (
    RESTRICTED_ENDINGS,
    claimed_short_forms,
    domains_named_in_answers,
    named_domains,
    restricted_ending,
    rival_name,
)
from avp_api.services.serp import SEARCH_COUNTRY_BY_TLD

RMIT = ("RMIT University", "rmit.edu.au")


def answer(
    text: str, *cited: str, status: EngineResultStatus = EngineResultStatus.OK
) -> tuple[Engine, ExtractedFacts, EngineAnswer]:
    """One engine's answer through the real extractor, as `run_prompt` returns it."""
    engine_answer = EngineAnswer(
        engine=Engine.CLAUDE_SEARCH, engine_version="stub", prompt_text="p", text=text,
        citations=[
            CitedSource(url=f"https://{domain}/page", domain=domain, position=n)
            for n, domain in enumerate(cited, start=1)
        ],
        status=status,
    )
    facts = extraction.extract_facts(
        engine_answer, subject_name=RMIT[0], subject_domain=RMIT[1], competitors=[]
    )
    return Engine.CLAUDE_SEARCH, facts, engine_answer


def count(results: list[Any], **kwargs: Any) -> dict[str, int]:
    kwargs.setdefault("client_domain", RMIT[1])
    return named_domains(results, **kwargs)


# --- the table ----------------------------------------------------------------------------------


class TestTheRestrictedEndingsCoverTheFourteenMarkets:
    def test_every_market_has_a_row_and_no_row_is_for_another_market(self) -> None:
        assert set(RESTRICTED_ENDINGS) == set(SEARCH_COUNTRY_BY_TLD)
        assert len(RESTRICTED_ENDINGS) == 14

    def test_every_market_names_at_least_one_ending(self) -> None:
        for market, endings in RESTRICTED_ENDINGS.items():
            assert endings, market

    def test_every_ending_belongs_to_the_market_it_is_filed_under(self) -> None:
        for market, endings in RESTRICTED_ENDINGS.items():
            for ending in endings:
                if market == "us":
                    # The US sector endings are top-level domains of their own.
                    assert ending in {"edu", "gov", "mil"}
                else:
                    assert ending.endswith(f".{market}"), (market, ending)

    def test_the_table_is_the_founders(self) -> None:
        # A change to this table is a decision, so it fails here until the decision is on record.
        assert {market: set(endings) for market, endings in RESTRICTED_ENDINGS.items()} == {
            "au": {"edu.au", "gov.au"},
            "nz": {"ac.nz", "cri.nz", "govt.nz", "health.nz", "iwi.nz", "mil.nz", "parliament.nz"},
            "uk": {"ac.uk", "gov.uk", "mod.uk", "nhs.uk", "police.uk", "sch.uk"},
            "ie": {"gov.ie"},
            "ca": {"gc.ca"},
            "us": {"edu", "gov", "mil"},
            "za": {"ac.za", "gov.za", "mil.za"},
            "in": {"ac.in", "edu.in", "gov.in", "mil.in", "res.in"},
            "sg": {"edu.sg", "gov.sg"},
            "my": {"edu.my", "gov.my", "mil.my"},
            "ph": {"edu.ph", "gov.ph", "mil.ph"},
            "hk": {"edu.hk", "gov.hk"},
            "id": {"ac.id", "go.id", "mil.id", "sch.id"},
            "jp": {"ac.jp", "ed.jp", "go.jp", "lg.jp"},
        }

    @pytest.mark.parametrize(
        ("domain", "ending"),
        [
            ("deakin.edu.au", "edu.au"),
            ("dfat.gov.au", "gov.au"),
            ("auckland.ac.nz", "ac.nz"),
            ("ox.ac.uk", "ac.uk"),
            ("royalfree.nhs.uk", "nhs.uk"),
            ("dfa.gov.ie", "gov.ie"),
            ("canada.gc.ca", "gc.ca"),
            ("mit.edu", "edu"),
            ("nih.gov", "gov"),
            ("army.mil", "mil"),
            ("uct.ac.za", "ac.za"),
            ("iitb.ac.in", "ac.in"),
            ("nus.edu.sg", "edu.sg"),
            ("um.edu.my", "edu.my"),
            ("up.edu.ph", "edu.ph"),
            ("cuhk.edu.hk", "edu.hk"),
            ("ui.ac.id", "ac.id"),
            ("u-tokyo.ac.jp", "ac.jp"),
        ],
    )
    def test_a_domain_in_each_market_has_its_ending(self, domain: str, ending: str) -> None:
        market = next(m for m, endings in RESTRICTED_ENDINGS.items() if ending in endings)
        assert restricted_ending(domain) == ending
        assert ending in RESTRICTED_ENDINGS[market]


# --- restricted_ending --------------------------------------------------------------------------


class TestRestrictedEnding:
    def test_a_university_has_edu_au(self) -> None:
        assert restricted_ending("rmit.edu.au") == "edu.au"
        assert restricted_ending("deakin.edu.au") == "edu.au"

    def test_the_host_of_a_site_is_read_as_its_domain(self) -> None:
        assert restricted_ending("www.deakin.edu.au") == "edu.au"

    def test_case_does_not_matter(self) -> None:
        assert restricted_ending("RMIT.EDU.AU") == "edu.au"

    def test_a_com_has_none(self) -> None:
        assert restricted_ending("deakin.com") is None
        assert restricted_ending("example.com.au") is None
        assert restricted_ending("helpscout.com") is None

    def test_a_school_under_a_state_suffix_is_not_under_edu_au(self) -> None:
        # The Public Suffix List makes `vic.edu.au` a suffix of its own: the SAME ending means the
        # same suffix, and this is another one.
        assert restricted_ending("scotch.vic.edu.au") is None

    def test_a_private_suffix_is_never_restricted(self) -> None:
        assert restricted_ending("rmit.github.io") is None

    @pytest.mark.parametrize("odd", ["", " ", "localhost", "192.168.0.1", "edu.au", ".", "a..b"])
    def test_what_is_not_a_registrable_domain_has_none(self, odd: str) -> None:
        # A bare suffix has no name, so it is nobody's domain.
        assert restricted_ending(odd) is None

    def test_an_internationalised_name_is_read_in_dns_form(self) -> None:
        assert restricted_ending("bücher.edu.au") == "edu.au"
        assert restricted_ending("xn--bcher-kva.edu.au") == "edu.au"


# --- the name a suggestion is added with ---------------------------------------------------------


class TestRivalName:
    @pytest.mark.parametrize(
        ("domain", "name"),
        [
            ("unsw.edu.au", "UNSW"),
            ("uts.edu.au", "UTS"),
            ("anu.edu.au", "ANU"),
            ("vu.edu.au", "VU"),
            ("uac.edu.au", "UAC"),
            ("deakin.edu.au", "Deakin"),
            ("monash.edu", "Monash"),
            ("latrobe.edu.au", "Latrobe"),
            ("curtin.edu.au", "Curtin"),
            ("la-trobe.edu.au", "La-trobe"),
            ("u.edu.au", "U"),
        ],
    )
    def test_capitals_for_two_to_four_letters_else_capitalised(
        self, domain: str, name: str
    ) -> None:
        assert rival_name(domain) == name

    def test_the_host_of_a_site_names_the_domain_not_the_subdomain(self) -> None:
        assert rival_name("www.deakin.edu.au") == "Deakin"

    def test_five_letters_are_not_capitals(self) -> None:
        assert rival_name("rmitx.edu.au") == "Rmitx"

    def test_a_name_with_a_digit_is_capitalised_not_upper_cased(self) -> None:
        assert rival_name("u3a.edu.au") == "U3a"

    def test_nothing_to_name_gives_the_domain_back(self) -> None:
        assert rival_name("") == ""
        assert rival_name("localhost") == "localhost"


# --- named_domains ------------------------------------------------------------------------------


class TestWhichDomainsAreCandidates:
    def test_a_cited_domain_with_the_clients_ending_is_a_candidate_though_never_named(
        self,
    ) -> None:
        # Measured, and not named: the fact is 0, not an absence.
        found = count([answer("RMIT University is a good choice.", "uac.edu.au", "qilt.edu.au")])
        assert found == {"uac.edu.au": 0, "qilt.edu.au": 0}

    def test_a_domain_with_another_ending_is_not(self) -> None:
        found = count(
            [answer("Deakin University and Monash University.", "deakin.com", "monash.edu")]
        )
        assert found == {}

    def test_the_clients_own_domain_is_not(self) -> None:
        found = count([answer("RMIT University is a good choice.", "rmit.edu.au", "deakin.edu.au")])
        assert found == {"deakin.edu.au": 0}

    def test_the_clients_own_domain_is_not_whatever_its_spelling(self) -> None:
        found = count(
            [answer("RMIT University.", "rmit.edu.au", "deakin.edu.au")],
            client_domain="RMIT.edu.au",
        )
        assert found == {"deakin.edu.au": 0}

    def test_a_school_under_a_state_suffix_is_not_a_candidate(self) -> None:
        found = count([answer("Scotch College is a school.", "scotch.vic.edu.au", "deakin.edu.au")])
        assert found == {"deakin.edu.au": 0}

    def test_a_client_with_no_restricted_ending_has_none_ever(self) -> None:
        results = [answer("Deakin University and Help Scout.", "deakin.edu.au", "deakin.com")]
        assert count(results, client_domain="helpscout.com") == {}
        assert count(results, client_domain="deakin.com") == {}

    def test_a_client_under_a_state_suffix_has_none(self) -> None:
        results = [answer("Deakin University.", "deakin.edu.au", "other.vic.edu.au")]
        assert count(results, client_domain="scotch.vic.edu.au") == {}

    def test_the_candidates_are_every_cited_domain_of_the_scan_not_one_answers(self) -> None:
        found = count(
            [
                answer("An answer.", "deakin.edu.au"),
                answer("Another answer.", "qilt.edu.au"),
            ]
        )
        assert found == {"deakin.edu.au": 0, "qilt.edu.au": 0}

    def test_a_citation_of_a_failed_answer_is_not_a_cited_domain(self) -> None:
        failed_answer = EngineAnswer(
            engine=Engine.CHATGPT, engine_version="stub", prompt_text="p", text="Deakin University",
            status=EngineResultStatus.ERROR, error_code="PROVIDER_ERROR",
        )
        failed_facts = ExtractedFacts(
            status=EngineResultStatus.ERROR,
            citations=[
                CitationFact(
                    url="https://deakin.edu.au/x", domain="deakin.edu.au",
                    source_type=CitationType.OTHER, position=1, cites_subject=False,
                )
            ],
        )
        assert count([(Engine.CHATGPT, failed_facts, failed_answer)]) == {}


class TestAnswersAreCountedNotMentions:
    def test_one_answer_naming_a_brand_three_times_counts_once(self) -> None:
        text = "Deakin University is large. Deakin University has sites. Choose Deakin University"
        assert count([answer(text, "deakin.edu.au")]) == {"deakin.edu.au": 1}

    def test_two_answers_count_twice(self) -> None:
        found = count(
            [
                answer("Deakin University is large.", "deakin.edu.au"),
                answer("Consider Deakin University.", "deakin.edu.au"),
                answer("RMIT University only.", "deakin.edu.au"),
            ]
        )
        assert found == {"deakin.edu.au": 2}

    def test_an_answer_that_names_a_brand_without_citing_it_counts_toward_a_domain_cited_elsewhere(
        self,
    ) -> None:
        found = count(
            [
                answer("An answer that cites it and does not name it.", "deakin.edu.au"),
                answer("Deakin University is also an option."),
            ]
        )
        assert found == {"deakin.edu.au": 1}

    def test_each_domain_is_counted_on_its_own(self) -> None:
        found = count(
            [
                answer(
                    "Deakin University and Swinburne University are options.",
                    "deakin.edu.au", "swinburne.edu.au", "qilt.edu.au",
                ),
                answer("Deakin University again.", "deakin.edu.au"),
            ]
        )
        assert found == {"deakin.edu.au": 2, "swinburne.edu.au": 1, "qilt.edu.au": 0}

    def test_the_founders_pair_is_never_named(self) -> None:
        """qilt.edu.au and uac.edu.au are edu.au and cited, and the answers never name them."""
        found = count(
            [
                answer(
                    "Deakin University and Monash University are options.",
                    "deakin.edu.au", "qilt.edu.au", "uac.edu.au",
                )
            ]
        )
        assert found["deakin.edu.au"] == 1
        assert found["qilt.edu.au"] == 0 and found["uac.edu.au"] == 0


class TestOnlyAnsweredAnswersAreRead:
    def test_a_failed_answer_is_ignored_even_if_it_carries_text(self) -> None:
        failed = EngineAnswer(
            engine=Engine.CHATGPT, engine_version="stub", prompt_text="p",
            text="Deakin University is the best.", status=EngineResultStatus.ERROR,
            error_code="PROVIDER_ERROR",
        )
        failed_facts = ExtractedFacts(status=EngineResultStatus.ERROR)
        found = count(
            [
                (Engine.CHATGPT, failed_facts, failed),
                answer("Plain answer.", "deakin.edu.au"),
            ]
        )
        assert found == {"deakin.edu.au": 0}

    def test_an_answer_with_no_mention_of_the_client_is_still_an_answer(self) -> None:
        engine, facts, ans = answer("Deakin University is a good choice.", "deakin.edu.au")
        assert facts.status is EngineResultStatus.ANSWERED_NO_MENTION
        assert count([(engine, facts, ans)]) == {"deakin.edu.au": 1}

    def test_failed_answers_only_gives_nothing(self) -> None:
        failed = EngineAnswer(
            engine=Engine.GEMINI, engine_version="stub", prompt_text="p", text="",
            status=EngineResultStatus.TIMEOUT, error_code="TIMEOUT",
        )
        facts = extraction.extract_facts(
            failed, subject_name=RMIT[0], subject_domain=RMIT[1], competitors=[]
        )
        assert facts.status is EngineResultStatus.TIMEOUT
        assert count([(Engine.GEMINI, facts, failed)] * 3) == {}

    def test_an_answered_status_with_no_text_names_nothing(self) -> None:
        empty = EngineAnswer(engine=Engine.CLAUDE, engine_version="stub", prompt_text="p", text="")
        facts = ExtractedFacts(
            status=EngineResultStatus.ANSWERED_NO_MENTION,
            citations=[
                CitationFact(
                    url="https://deakin.edu.au/x", domain="deakin.edu.au",
                    source_type=CitationType.OTHER, position=1, cites_subject=False,
                )
            ],
        )
        assert count([(Engine.CLAUDE, facts, empty)]) == {"deakin.edu.au": 0}


class TestHowALabelIsNamed:
    """`extraction.find_brand` with the domain alone: the matcher the scan itself uses."""

    def test_a_label_of_five_or_more_letters_is_found_as_a_word_in_any_case(self) -> None:
        assert count([answer("go to deakin for it", "deakin.edu.au")]) == {"deakin.edu.au": 1}

    def test_a_five_letter_label_is_not_found_inside_another_word(self) -> None:
        results = [answer("Astonishing results from the study.", "aston.ac.uk")]
        assert count(results, client_domain="ox.ac.uk") == {"aston.ac.uk": 0}
        named = [answer("Aston University is in Birmingham.", "aston.ac.uk")]
        assert count(named, client_domain="ox.ac.uk") == {"aston.ac.uk": 1}

    def test_a_label_of_six_or_more_letters_is_also_found_inside_a_longer_word(self) -> None:
        """A limit of the matcher, recorded: its separator-insensitive pass has no word boundary.

        `find_brand` reads "Deakinesis" as naming deakin (a slug of six letters or more is looked
        for in the answer with its spacing and punctuation removed). F2 uses the matcher as it is.
        """
        found = count([answer("Deakinesis is not a university.", "deakin.edu.au")])
        assert found == {"deakin.edu.au": 1}

    def test_a_separator_insensitive_name_is_found(self) -> None:
        # "La Trobe University" names latrobe.edu.au.
        assert count([answer("La Trobe University is in Bundoora.", "latrobe.edu.au")]) == {
            "latrobe.edu.au": 1
        }

    def test_a_short_label_is_found_in_capitals_only(self) -> None:
        assert count([answer("UNSW is large.", "unsw.edu.au")]) == {"unsw.edu.au": 1}
        assert count([answer("unsw is large. Unsw too.", "unsw.edu.au")]) == {"unsw.edu.au": 0}

    def test_the_full_domain_in_an_answer_names_it(self) -> None:
        assert count([answer("See deakin.edu.au for courses.", "deakin.edu.au")]) == {
            "deakin.edu.au": 1
        }

    def test_a_short_form_the_client_or_a_rival_already_owns_is_not_counted_for_the_candidate(
        self,
    ) -> None:
        results = [answer("UNSW is large.", "unsw.edu.au")]
        assert count(results) == {"unsw.edu.au": 1}
        assert count(results, claimed_forms=frozenset({"UNSW"})) == {"unsw.edu.au": 0}

    def test_the_matcher_is_unchanged(self) -> None:
        # F2 reads `find_brand`; it must not move it.
        assert extraction.MATCHER_VERSION == "v8"

    def test_the_prefilter_is_revisited_whenever_the_matcher_changes(self) -> None:
        # `needles` and `may_name` skip a pair only when `find_brand` cannot match it, and that is
        # proved against the matcher's code as it stands today. The slug rules it borrows from
        # `competitors` can change without moving MATCHER_VERSION, so the code itself is pinned:
        # any change to it fails here first (the security review of F2's fixes, 2026-10-10).
        parts = [
            inspect.getsource(fn)
            for fn in (
                extraction.find_brand,
                extraction._slug_index,
                extraction.short_forms,
                extraction._name_pattern,
                extraction._short_form_pattern,
                competitors.slugify,
            )
        ]
        parts += [competitors._SLUG.pattern, repr(competitors._LEGAL_SUFFIXES)]
        digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        assert digest == "65f0b59ca0566587f051c855624e0716992e94a3b596194b788253eb5131b5bb", (
            "the matcher changed: revisit probable_rivals.needles and may_name, then re-pin"
        )


class TestTheClaimedForms:
    def test_the_subjects_and_every_rivals_short_forms_are_claimed(self) -> None:
        forms = claimed_short_forms(
            "RMIT University", "rmit.edu.au",
            [("UNSW Sydney", "unsw.edu.au"), ("Monash University", "monash.edu")],
        )
        assert forms == frozenset({"RMIT", "UNSW"})

    def test_a_rival_with_no_domain_is_read_by_its_name(self) -> None:
        assert claimed_short_forms("RMIT University", "rmit.edu.au", [("UTS", None)]) == frozenset(
            {"RMIT", "UTS"}
        )

    def test_a_stop_listed_form_is_not_claimed(self) -> None:
        assert claimed_short_forms("It Partners", "it.com.au", []) == frozenset()

    def test_every_brand_the_scan_matches_by_short_form_is_claimed(self) -> None:
        """The brands `extract_facts` finds by short form from one list are the forms claimed."""
        competitors = [("UNSW Sydney", "unsw.edu.au"), ("UTS", "uts.edu.au")]
        engine_answer = EngineAnswer(
            engine=Engine.CLAUDE, engine_version="stub", prompt_text="p",
            text="RMIT and UNSW and UTS.",
        )
        facts = extraction.extract_facts(
            engine_answer, subject_name=RMIT[0], subject_domain=RMIT[1], competitors=competitors
        )
        assert {hit.name for hit in facts.brand_hits} == {"RMIT University", "UNSW Sydney", "UTS"}
        assert claimed_short_forms(RMIT[0], RMIT[1], competitors) == frozenset(
            {"RMIT", "UNSW", "UTS"}
        )


class TestTotalOverOddInput:
    """It runs inside a scan after every answer has been paid for: it never raises."""

    def test_no_results_at_all(self) -> None:
        assert count([]) == {}
        assert named_domains((), client_domain=RMIT[1]) == {}

    def test_a_generator_is_read_once(self) -> None:
        gen = (r for r in [answer("Deakin University.", "deakin.edu.au")])
        assert named_domains(gen, client_domain=RMIT[1]) == {"deakin.edu.au": 1}

    @pytest.mark.parametrize("client_domain", ["", " ", "localhost", "192.168.0.1", "edu.au"])
    def test_a_client_domain_that_is_not_a_domain_gives_nothing(self, client_domain: str) -> None:
        results = [answer("Deakin University.", "deakin.edu.au")]
        assert count(results, client_domain=client_domain) == {}

    def test_a_citation_with_no_domain_or_a_strange_one_is_skipped(self) -> None:
        engine, facts, ans = answer("Deakin University.", "deakin.edu.au")
        for odd in ("", " ", "not a domain", "edu.au", None, 7):
            facts.citations.append(
                CitationFact(
                    url="u", domain=odd,  # type: ignore[arg-type]
                    source_type=CitationType.OTHER, position=9, cites_subject=False,
                )
            )
        found = named_domains([(engine, facts, ans)], client_domain=RMIT[1])
        assert found == {"deakin.edu.au": 1}

    def test_a_cited_domain_keeps_the_spelling_it_was_cited_in(self) -> None:
        # The report finds a domain in this map by its DNS form, so the key is what was cited.
        found = count([answer("Deakin University is large.", "Deakin.EDU.AU")])
        assert found == {"Deakin.EDU.AU": 1}

    def test_an_answer_whose_text_is_not_text_is_skipped(self) -> None:
        engine, facts, ans = answer("Deakin University.", "deakin.edu.au")
        ans.text = None  # type: ignore[assignment]
        # Cited, and there is no text to name it: measured, and 0. It does not raise.
        assert named_domains([(engine, facts, ans)], client_domain=RMIT[1]) == {"deakin.edu.au": 0}

    def test_a_very_long_answer_does_not_fail(self) -> None:
        text = "Deakin University. " + "x" * 200_000
        assert count([answer(text, "deakin.edu.au")]) == {"deakin.edu.au": 1}

    def test_the_answer_is_not_changed(self) -> None:
        engine, facts, ans = answer("Deakin University.", "deakin.edu.au")
        before = (ans.text, list(facts.citations), facts.status)
        named_domains([(engine, facts, ans)], client_domain=RMIT[1])
        assert (ans.text, list(facts.citations), facts.status) == before

    def test_it_returns_only_domains_and_counts(self) -> None:
        found = count([answer("Deakin University.", "deakin.edu.au")])
        assert all(type(k) is str and type(v) is int and v >= 0 for k, v in found.items())

    def test_the_same_input_gives_the_same_output(self) -> None:
        results = [
            answer("Deakin University and UNSW.", "deakin.edu.au", "unsw.edu.au"),
            answer("Deakin University.", "deakin.edu.au"),
        ]
        assert count(results) == count(list(reversed(results)))


# --- reading the stored fact back ---------------------------------------------------------------


class TestDomainsNamedInAnswers:
    def test_a_domain_named_in_at_least_one_answer_is_a_probable_rival(self) -> None:
        stored = {"deakin.edu.au": 4, "swinburne.edu.au": 1, "qilt.edu.au": 0}
        assert domains_named_in_answers(stored) == frozenset({"deakin.edu.au", "swinburne.edu.au"})

    @pytest.mark.parametrize("stored", [None, {}, [], "x", 3, [("deakin.edu.au", 2)]])
    def test_nothing_measured_or_nothing_qualifying_names_nobody(self, stored: Any) -> None:
        assert domains_named_in_answers(stored) == frozenset()

    def test_an_entry_that_is_not_a_count_is_ignored(self) -> None:
        stored = {"a.edu.au": True, "b.edu.au": "3", "c.edu.au": -1, "d.edu.au": 2.0, "e.edu.au": 2}
        assert domains_named_in_answers(stored) == frozenset({"e.edu.au"})

    def test_it_is_a_frozenset_so_a_report_cannot_change_the_scan(self) -> None:
        assert isinstance(domains_named_in_answers({"a.edu.au": 1}), frozenset)

    def test_the_stored_value_is_not_changed(self) -> None:
        stored = {"a.edu.au": 1, "b.edu.au": 0}
        domains_named_in_answers(stored)
        assert stored == {"a.edu.au": 1, "b.edu.au": 0}


def test_the_module_adds_no_dependency() -> None:
    """Pure: it imports the repo's own matcher and suffix list and nothing from outside."""
    import inspect

    source = inspect.getsource(probable_rivals)
    for forbidden in ("httpx", "anthropic", "sqlalchemy", "requests", "openai"):
        assert f"import {forbidden}" not in source and f"from {forbidden}" not in source


# --- the pre-filter -------------------------------------------------------------------------------
# The security review of 2026-10-10, should-fix 1, part 4. `named_domains` called `find_brand` once
# per (candidate domain, answered answer): 2,574 ms for 96 answers of ~5.4k characters over 60
# candidates. Before each call it now asks `may_name` whether the answer, flattened ONCE to
# lower-case ASCII letters and digits, holds the domain's label flattened the same way (or its slug,
# which the matcher's last pass looks for). That is a NECESSARY condition of every way `find_brand`
# can find a domain alone, so it only skips pairs the matcher would reject and changes no count.
# What follows holds that claim: the exact Unicode facts it rests on, crafted cases for each way the
# matcher finds a domain, a fuzz over an alphabet chosen to break it, and the counts themselves,
# equal to the matcher's own on fixtures and on a seeded corpus.


class TestFlatten:
    def test_it_keeps_only_lower_case_ascii_letters_and_digits(self) -> None:
        text = "Deakin University, 2nd-floor (B2)!"
        assert probable_rivals.flatten(text) == "deakinuniversity2ndfloorb2"

    def test_it_folds_accents_ligatures_and_wide_letters(self) -> None:
        assert probable_rivals.flatten("Zürich") == "zurich"
        assert probable_rivals.flatten("Ångström") == "angstrom"
        assert probable_rivals.flatten("ﬁnance") == "finance"
        assert probable_rivals.flatten("ＤＥＡＫＩＮ") == "deakin"
        assert probable_rivals.flatten("Straße") == "strasse"

    @pytest.mark.parametrize(
        ("char", "letter"), [("İ", "i"), ("ı", "i"), ("ſ", "s"), ("K", "k")]
    )
    def test_a_letter_the_regex_engine_reads_as_ascii_is_that_letter(
        self, char: str, letter: str
    ) -> None:
        """`re.IGNORECASE` equates these four with an ASCII letter (the dotted capital I, the
        dotless i, the long s, the Kelvin sign), and no single Unicode folding gives all four."""
        assert re.fullmatch(letter, char, re.IGNORECASE)
        assert probable_rivals.flatten(char) == letter

    def test_only_four_non_ascii_characters_stand_for_an_ascii_letter_or_digit(self) -> None:
        """Every code point against `[a-z0-9]` under IGNORECASE: the regex engine's own equalities.

        If a Python upgrade adds one, this fails first and says which, before a count could change.
        """
        ascii_alnum = re.compile(r"[a-z0-9]", re.IGNORECASE)
        stand_for = {
            char
            for char in map(chr, range(0x110000))
            if not char.isascii() and ascii_alnum.fullmatch(char)
        }
        assert stand_for == {"İ", "ı", "ſ", "K"}

    def test_a_character_that_is_not_a_letter_or_digit_adds_nothing(self) -> None:
        """Every code point `str.isalnum` refuses, at once. The matcher's slug pass skips each of
        them, so the flattened text must not gain a letter from one: a trade mark sign is not
        "tm", and must not come between the letters of a label."""
        refused = "".join(char for char in map(chr, range(0x110000)) if not char.isalnum())
        assert probable_rivals.flatten(refused) == ""
        assert probable_rivals.flatten("a" + refused + "b") == "ab"
        assert probable_rivals.flatten("deak™in") == "deakin"

    def test_case_variants_of_a_character_flatten_alike(self) -> None:
        """The regex engine equates a character with its case variants, so a text may spell a
        label's letter in any of them. Every cased code point, against all of its variants."""
        cased = [
            char
            for char in map(chr, range(0x110000))
            if char.lower() != char or char.upper() != char
        ]
        assert len(cased) > 2000
        for char in cased:
            variants = (char.lower(), char.upper(), char.swapcase(), char.casefold(), char.title())
            flat = probable_rivals.flatten(char)
            assert [v for v in variants if probable_rivals.flatten(v) != flat] == [], hex(ord(char))

    def test_it_is_total_over_odd_text(self) -> None:
        for odd in ("", " ", "\x00", "\ud800", "\U0001f600", "é", "x" * 200_000):
            assert isinstance(probable_rivals.flatten(odd), str)
        assert probable_rivals.flatten("") == ""


class TestNeedles:
    """What an answer has to hold, flattened, for the matcher to find a domain by its label."""

    def test_a_label_is_flattened(self) -> None:
        assert probable_rivals.needles("deakin.edu.au") == ("deakin",)
        assert probable_rivals.needles("Deakin.EDU.AU") == ("deakin",)
        assert probable_rivals.needles("la-trobe.edu.au") == ("latrobe",)
        assert probable_rivals.needles("xn--bcher-kva.edu.au") == ("xnbcherkva",)

    def test_the_slug_is_a_second_needle_when_it_differs_from_the_flattened_label(self) -> None:
        """The matcher's slug drops every character that is not an ASCII letter or digit (without
        folding it first) and a legal suffix, so it can be found where the label is not."""
        assert probable_rivals.needles("marketco.edu.au") == ("marketco", "market")
        assert probable_rivals.needles("ébcdefg.edu.au") == ("ebcdefg", "bcdefg")

    def test_a_slug_the_matcher_would_not_look_for_is_not_a_needle(self) -> None:
        # Under `MIN_SLUG_MATCH_CHARS`, `find_brand` does not run its slug pass at all.
        assert probable_rivals.needles("zürich.edu.au") == ("zurich",)
        assert probable_rivals.needles("unsw.edu.au") == ("unsw",)

    def test_the_label_is_what_find_brand_calls_the_label(self) -> None:
        # The part before the first dot, as the cited domain was spelled.
        assert probable_rivals.needles("www.deakin.edu.au") == ("www",)
        assert probable_rivals.needles("") == ("",)


# Each way `find_brand` finds a domain alone (name=None), and the pre-filter has to pass every one.
MATCHED = [
    pytest.param("deakin.edu.au", "See deakin.edu.au for courses.", id="the full domain"),
    pytest.param("deakin.edu.au", "see DEAKIN.EDU.AU for courses", id="the full domain, capitals"),
    pytest.param("unsw.edu.au", "UNSW is large.", id="the capitals short form"),
    pytest.param("uts.edu.au", "Study at UTS this year", id="a three-letter short form"),
    pytest.param("deakin.edu.au", "Deakin University is large.", id="the label as a word"),
    pytest.param("deakin.edu.au", "go to deakin for it", id="the label as a word, lower case"),
    pytest.param("deakin.edu.au", "DEAKIN", id="the label as a word, capitals"),
    pytest.param("deakin.edu.au", "dEaKiN is odd", id="the label, mixed case"),
    pytest.param("latrobe.edu.au", "La Trobe University is in Bundoora.", id="slug: spaced name"),
    pytest.param("latrobe.edu.au", "la-trobe", id="slug: hyphen"),
    pytest.param("latrobe.edu.au", "LA_TROBE", id="slug: underscore"),
    pytest.param("latrobe.edu.au", "La  Trobe", id="slug: two spaces"),
    pytest.param("la-trobe.edu.au", "La Trobe University", id="a hyphen in the label"),
    pytest.param("deakin.edu.au", "D.E.A.K.I.N", id="slug: dots between the letters"),
    pytest.param("deakin.edu.au", "deak™in", id="slug: a sign the slug pass skips"),
    pytest.param("deakin.edu.au", "Deakinesis is not a university.", id="inside a longer word"),
    pytest.param("deakin.edu.au", "undeakinable", id="inside a longer word, both sides"),
    pytest.param("zürich.edu.au", "Zürich University", id="an accented label"),
    pytest.param("bücher.edu.au", "Bücher is a bookshop", id="an internationalised label"),
    pytest.param(
        "xn--bcher-kva.edu.au", "see xn--bcher-kva.edu.au", id="a punycode label in prose"
    ),
    pytest.param("xn--bcher-kva.edu.au", "xn bcher kva", id="slug: a punycode label"),
    pytest.param("deakin.edu.au", "Deakın University", id="dotless i in the text"),
    pytest.param("deakin.edu.au", "DeaKin University", id="the Kelvin sign in the text"),
    pytest.param("deakin.edu.au", "DeakİN University", id="dotted capital I in the text"),
    pytest.param("swinburne.edu.au", "ſwinburne University", id="long s in the text"),
    pytest.param("marketco.edu.au", "Market is a word", id="slug shorter than the label: suffix"),
    pytest.param(
        "ébcdefg.edu.au", "Abcdefg is a thing", id="slug shorter than the label: a non-ASCII edge"
    ),
    pytest.param("a1b2c3d.edu.au", "A1B2C3D", id="digits in the label"),
    pytest.param("Deakin.EDU.AU", "deakin university", id="the domain as it was cited"),
]


class TestWhatTheMatcherFindsPassesThePrefilter:
    @pytest.mark.parametrize(("domain", "text"), MATCHED)
    def test_a_match_passes(self, domain: str, text: str) -> None:
        assert find_brand(text, name=None, domain=domain) is not None, "the case is a real match"
        assert probable_rivals.may_name(domain, probable_rivals.flatten(text))

    def test_a_text_with_none_of_the_label_is_rejected(self) -> None:
        text = "Monash University and RMIT University are options."
        assert find_brand(text, name=None, domain="deakin.edu.au") is None
        assert not probable_rivals.may_name("deakin.edu.au", probable_rivals.flatten(text))

    @pytest.mark.parametrize(
        ("domain", "text"),
        [
            pytest.param("zürich.edu.au", "Zurich University", id="the accent is missing"),
            pytest.param("deakin.edu.au", "Ｄｅａｋｉｎ University", id="full-width letters"),
            pytest.param("unsw.edu.au", "unsw is large", id="the short form is not in capitals"),
        ],
    )
    def test_a_pass_is_not_a_match(self, domain: str, text: str) -> None:
        """Necessary, not sufficient: the matcher still decides every pair that passes."""
        assert find_brand(text, name=None, domain=domain) is None
        assert probable_rivals.may_name(domain, probable_rivals.flatten(text))

    def test_a_label_with_nothing_to_look_for_is_never_skipped(self) -> None:
        """Cyrillic letters have no ASCII form, so a wholly Cyrillic label flattens to nothing and
        every answer passes: the matcher decides, as it always did."""
        assert probable_rivals.needles("пример.edu.au") == ("",)
        assert probable_rivals.may_name("пример.edu.au", "any text") is True
        assert probable_rivals.may_name("", "any text") is True

    def test_only_the_ascii_part_of_a_mixed_label_is_looked_for(self) -> None:
        domain = "дeakin.edu.au"
        assert probable_rivals.may_name(domain, probable_rivals.flatten("Дeakin University"))
        assert not probable_rivals.may_name(domain, probable_rivals.flatten("any text"))


# A fuzz over an alphabet chosen to break the claim: the four letters the regex engine reads as
# ASCII, signs the slug pass skips, accents, the sharp s, ligatures, wide letters and combining
# marks. Labels are built from it, with and without a legal suffix, and the text spells each
# letter any way the engine equates, with separators between.
_STAND_FOR = {"i": ["i", "I", "İ", "ı"], "k": ["k", "K", "K"], "s": ["s", "S", "ſ"]}
_SEPARATORS = ["", "", "", " ", "-", ".", "_", "™", "­", "‍", "  "]
_NOISE = list("abdeiknsuw0129 -._,;") + [
    "é", "ü", "ß", "ẞ", "ﬁ", "Ａ", "ǅ", "Ω", "Å", "̇", "™",
]
_LABEL_CHARS = list("abdeiknsuw0129-") + ["é", "ü", "ß", "ı", "İ", "ſ", "K", "ï", "Ａ"]
_LEGAL_SUFFIXES = ["co", "inc", "ltd", "pty", "ag", "limited"]


def _spelled(rng: random.Random, text: str, *, separators: bool = True) -> str:
    out: list[str] = []
    for char in text:
        out.append(rng.choice(_STAND_FOR.get(char.lower(), [char, char.upper(), char.lower()])))
        if separators and rng.random() < 0.15:
            out.append(rng.choice(_SEPARATORS))
    return "".join(out)


def _noise(rng: random.Random) -> str:
    return "".join(rng.choice(_NOISE) for _ in range(rng.randint(0, 8)))


def _fuzz_case(rng: random.Random) -> tuple[str, str]:
    label = "".join(rng.choice(_LABEL_CHARS) for _ in range(rng.randint(2, 9)))
    if rng.random() < 0.2:
        label += rng.choice(_LEGAL_SUFFIXES)
    domain = rng.choice([label, label.upper(), label.capitalize()]) + ".edu.au"
    kind = rng.random()
    if kind < 0.65:
        body = _spelled(rng, label)
    elif kind < 0.8:
        body = _spelled(rng, label + ".edu.au", separators=False)
    elif kind < 0.9:
        body = _spelled(rng, label[1:] if len(label) > 3 else label)
    else:
        body = "".join(rng.choice(_NOISE) for _ in range(rng.randint(0, 6)))
    return domain, _noise(rng) + body + _noise(rng)


class TestTheFuzz:
    def test_over_an_alphabet_chosen_to_break_it_a_match_always_passes(self) -> None:
        rng = random.Random(20261010)
        matched = 0
        counterexamples = []
        for _ in range(12_000):
            domain, text = _fuzz_case(rng)
            forms = frozenset({domain.split(".")[0].upper()}) if rng.random() < 0.2 else frozenset()
            found = find_brand(text, name=None, domain=domain, claimed_forms=forms) is not None
            if found:
                matched += 1
                if not probable_rivals.may_name(domain, probable_rivals.flatten(text)):
                    counterexamples.append((domain, text))
        assert matched > 500, "the fuzz must produce real matches for the property to mean anything"
        assert counterexamples == [], counterexamples[:5]


# --- the counts: the same, with and without the pre-filter ----------------------------------------


def unfiltered_count(
    results: list[Any], *, client_domain: str, claimed_forms: frozenset[str] = frozenset()
) -> dict[str, int]:
    """`named_domains` as it was before the pre-filter: `find_brand` once per (domain, answer)."""
    ending = restricted_ending(client_domain)
    if ending is None:
        return {}
    own = dns_form(registrable_domain(client_domain.strip()))
    answered = [
        (facts, ans) for _engine, facts, ans in results if facts.status in ANSWERED_STATUSES
    ]
    keys = {
        cited.domain
        for facts, _ans in answered
        for cited in facts.citations
        if restricted_ending(cited.domain) == ending and dns_form(cited.domain.strip()) != own
    }
    texts = [ans.text for _facts, ans in answered if isinstance(ans.text, str) and ans.text]
    return dict(
        sorted(
            (
                key,
                sum(
                    find_brand(text, name=None, domain=key, claimed_forms=claimed_forms) is not None
                    for text in texts
                ),
            )
            for key in keys
        )
    )


# The answers the tests above use, with the domains they cite, in one scan.
FIXTURE_ANSWERS = [
    ("Deakin University and Monash University are both good choices for design.",
     ("deakin.edu.au", "monash.edu.au", "qilt.edu.au")),
    ("RMIT University is strong in engineering.", ("qilt.edu.au",)),
    ("UNSW is large and so is UTS.", ("unsw.edu.au", "uts.edu.au")),
    ("unsw is large. Unsw too.", ("unsw.edu.au",)),
    ("ANU leads research; VU is smaller.", ("anu.edu.au", "vu.edu.au")),
    ("La Trobe University is in Bundoora.", ("latrobe.edu.au",)),
    ("Swinburne University of Technology offers design.", ("swinburne.edu.au",)),
    ("Astonishing results from the study.", ("aston.edu.au",)),
    ("Aston University is in Birmingham.", ("aston.edu.au",)),
    ("go to deakin for it", ("deakin.edu.au",)),
    ("See deakin.edu.au for courses.", ("deakin.edu.au", "uac.edu.au")),
    ("Deakinesis is not a university.", ("deakin.edu.au",)),
    ("Consider Curtin University or Griffith University.", ("curtin.edu.au", "griffith.edu.au")),
    ("QILT publishes the Graduate Outcomes Survey.", ("qilt.edu.au",)),
    ("UAC handles applications for NSW and the ACT.", ("uac.edu.au",)),
    ("University of Newcastle is a university in a city.", ("newcastle.edu.au",)),
    ("Zürich University and Bücher House are not Australian.", ("zürich.edu.au", "bücher.edu.au")),
    ("Deakın University, DeaKin University and DeakİN University.", ("deakin.edu.au",)),
    ("An answer with no brand in it at all, about study and careers.",
     ("bond.edu.au", "anu.edu.au")),
    ("D.E.A.K.I.N and deak™in and De akin.", ("deakin.edu.au",)),
]


class TestTheFilteredCountIsTheUnfilteredCount:
    def test_on_the_fixtures_the_answers_above_use(self) -> None:
        results = [answer(body, *cited) for body, cited in FIXTURE_ANSWERS]
        found = count(results)
        assert found == unfiltered_count(results, client_domain=RMIT[1])
        assert sum(found.values()) > 10, "the fixtures must name something to mean anything"
        assert found["qilt.edu.au"] == 1 and found["uac.edu.au"] == 1  # named in capitals once each

    def test_with_the_short_forms_the_client_and_its_rivals_own(self) -> None:
        results = [answer(body, *cited) for body, cited in FIXTURE_ANSWERS]
        claimed = frozenset({"UNSW", "UTS", "VU"})
        assert count(results, claimed_forms=claimed) == unfiltered_count(
            results, client_domain=RMIT[1], claimed_forms=claimed
        )

    def test_on_a_seeded_random_corpus(self) -> None:
        rng = random.Random(20261010)
        labels = [
            "deakin", "monash", "unsw", "uts", "anu", "vu", "latrobe", "la-trobe", "swinburne",
            "curtin", "bond", "usyd", "unimelb", "newcastle", "wollongong", "federation", "qilt",
            "uac", "u3a", "zürich", "bücher", "ébcdefg", "marketco", "xn--bcher-kva", "a1b2c3d",
            "ışık", "griffith", "uow",
        ]
        words = [
            "university", "course", "study", "degree", "campus", "student", "research",
            "engineering", "design", "teaching", "ranking", "Melbourne", "Sydney", "Australia",
            "online", "career", "program", "faculty",
        ]
        domains = [f"{label}.edu.au" for label in labels]
        results = []
        for _ in range(100):
            parts = [rng.choice(words) for _ in range(rng.randint(5, 40))]
            for _ in range(rng.randint(1, 4)):
                label = rng.choice(labels)
                rendering = rng.choice([
                    label.capitalize() + " University", label.upper(), label,
                    label + ".edu.au", _spelled(rng, label), "Uni of " + label.capitalize(),
                ])
                parts.insert(rng.randint(0, len(parts)), rendering)
            results.append(answer(" ".join(parts), *rng.sample(domains, rng.randint(1, 12))))
        found = count(results)
        assert found == unfiltered_count(results, client_domain=RMIT[1])
        assert sum(found.values()) > 100 and any(v == 0 for v in found.values())

    def test_the_filter_really_does_skip_pairs_in_that_corpus(self) -> None:
        results = [answer(body, *cited) for body, cited in FIXTURE_ANSWERS]
        texts = [ans.text for _engine, _facts, ans in results]
        flats = [probable_rivals.flatten(text) for text in texts]
        pairs = [(d, f) for d in count(results) for f in flats]
        assert sum(not probable_rivals.may_name(d, f) for d, f in pairs) > len(pairs) // 2


class TestAnAnswerThatCannotNameTheDomainNeverReachesTheMatcher:
    def test_the_matcher_is_called_only_for_the_pairs_that_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, str | None]] = []
        real = probable_rivals.find_brand

        def spy(text: str, **kwargs: Any) -> int | None:
            calls.append((text, kwargs.get("domain")))
            return real(text, **kwargs)

        monkeypatch.setattr(probable_rivals, "find_brand", spy)
        results = [
            answer(f"Answer number {n} about study options.", "deakin.edu.au", "qilt.edu.au")
            for n in range(30)
        ]
        results.append(answer("Deakin University is large.", "deakin.edu.au"))

        assert count(results) == {"deakin.edu.au": 1, "qilt.edu.au": 0}
        # Before the pre-filter this was 31 answers x 2 domains = 62 calls.
        assert calls == [("Deakin University is large.", "deakin.edu.au")]

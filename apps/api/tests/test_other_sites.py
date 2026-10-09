"""A brand's other sites: the rule, and what is proposed under it (M1, task 1.1).

The report counts a cited domain as the client's own only on EXACT equality, so a client's own
site under another public suffix (RMIT University's rmit.edu.au and RMIT Vietnam's rmit.edu.vn) is
cited as an independent third party. The plan: a domain with the brand's name under another public
suffix is PROPOSED as the brand's other site, and counts as the brand's own only once the agency
CONFIRMS it. These tests hold the pure halves of that: the rule (`same_brand_elsewhere`, over
the Public Suffix List's own split, `crawl.name_and_suffix`), `proposals()`, which turns one
scan's cited domains into what the agency is asked about, and the checks of the PUT
(`checked_replacement`, `rival_sites_differ`, `write_rival_sites`). Two properties of those are
held here: the caps are checked on the lists as sent, before any entry is read, and every
comparison is made on the DNS form, so one host in two spellings (`bücher.de`,
`xn--bcher-kva.de`; `пример.рф`, `пример.xn--p1ai`) is one host while storage keeps the
spelling given. Nothing here reads a database or the network, and nothing here confirms
anything: a proposal changes nothing.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Any

import pytest

from avp_api.errors import ValidationProblem
from avp_api.models import Competitor, CompetitorSet
from avp_api.services import intake, other_sites
from avp_api.services.crawl import name_and_icann_suffix, name_and_suffix, registrable_domain
from avp_api.services.other_sites import (
    Proposal,
    Replacement,
    checked_replacement,
    proposals,
    rival_sites_differ,
    same_brand_elsewhere,
    write_rival_sites,
)

CLIENT = "rmit.edu.au"
CLIENT_NAME = "RMIT University"

# Twenty suffixes of the Public Suffix List, none of them `edu.au` or `com`: `rmit.<suffix>` is the
# name `rmit` under another ending, twenty ways.
SUFFIXES = [
    "edu.vn", "edu.my", "edu.sg", "edu.hk", "edu.cn", "edu.in", "edu.ph", "edu.tw", "ac.uk",
    "ac.nz", "com.sg", "com.hk", "com.vn", "co.uk", "co.nz", "de", "fr", "nl", "ac.th", "edu.pk",
]


class TestNameAndSuffix:
    """`crawl.name_and_suffix`: the label left of the public suffix, and the suffix."""

    @pytest.mark.parametrize(
        ("domain", "expected"),
        [
            ("rmit.edu.vn", ("rmit", "edu.vn")),
            ("rmit.edu.au", ("rmit", "edu.au")),
            ("monash.edu", ("monash", "edu")),
            ("example.co.uk", ("example", "co.uk")),
            # Lower case, whatever came in.
            ("RMIT.EDU.AU", ("rmit", "edu.au")),
            # A subdomain, a scheme and a path are not part of either half.
            ("shop.example.com", ("example", "com")),
            ("https://www.rmit.edu.au/about", ("rmit", "edu.au")),
            # The private section of the list is in, as `registrable_domain` has it.
            ("practice.github.io", ("practice", "github.io")),
            # An internationalised suffix is found in either spelling (the list holds `рф`;
            # `xn--p1ai` is the same suffix as DNS carries it), and each label comes back as it
            # was given. So the two spellings of one host split to different strings, which is
            # why the rule splits the DNS form (`TestTheRule`) and not what it was handed.
            ("пример.рф", ("пример", "рф")),
            ("пример.xn--p1ai", ("пример", "xn--p1ai")),
            ("xn--e1afmkfd.xn--p1ai", ("xn--e1afmkfd", "xn--p1ai")),
            ("xn--e1afmkfd.рф", ("xn--e1afmkfd", "рф")),
            ("bücher.at", ("bücher", "at")),
            ("xn--bcher-kva.at", ("xn--bcher-kva", "at")),
        ],
    )
    def test_it_splits_a_domain_at_its_public_suffix(
        self, domain: str, expected: tuple[str, str]
    ) -> None:
        assert name_and_suffix(domain) == expected

    @pytest.mark.parametrize(
        "domain", ["", "localhost", "192.168.0.1", "co.uk", "com", "intranet", "not a host"]
    )
    def test_it_returns_two_empty_strings_when_either_half_is_missing(self, domain: str) -> None:
        """An IP address, `localhost`, a bare suffix or a bare word has no name and a suffix."""
        assert name_and_suffix(domain) == ("", "")

    @pytest.mark.parametrize(
        "domain",
        [
            "rmit.edu.vn", "monash.edu", "example.co.uk", "practice.github.io",
            "https://www.rmit.edu.au/about", "localhost", "192.168.0.1", "co.uk", "",
        ],
    )
    def test_it_splits_where_registrable_domain_does(self, domain: str) -> None:
        """One extractor, one answer: the two halves joined are the registrable domain."""
        name, suffix = name_and_suffix(domain)
        assert (f"{name}.{suffix}" if name else "") == registrable_domain(domain)


class TestNameAndIcannSuffix:
    """`crawl.name_and_icann_suffix`: the same split, only under the list's ICANN section."""

    @pytest.mark.parametrize(
        ("domain", "expected"),
        [
            ("rmit.edu.vn", ("rmit", "edu.vn")),
            ("monash.edu", ("monash", "edu")),
            ("bücher.at", ("bücher", "at")),
            ("xn--bcher-kva.at", ("xn--bcher-kva", "at")),
            # The private section is not an ICANN suffix: no name, no suffix.
            ("rmit.github.io", ("", "")),
            ("rmit.blogspot.com", ("", "")),
            ("rmit.vercel.app", ("", "")),
            ("shop.rmit.github.io", ("", "")),
            # What `name_and_suffix` has no split for, this has none for either.
            ("", ("", "")),
            ("localhost", ("", "")),
            ("co.uk", ("", "")),
        ],
    )
    def test_it_splits_only_under_an_icann_suffix(
        self, domain: str, expected: tuple[str, str]
    ) -> None:
        assert name_and_icann_suffix(domain) == expected

    def test_name_and_suffix_still_reads_the_private_section(self) -> None:
        """`registrable_domain` needs it for every cited domain: it must not have changed."""
        assert name_and_suffix("rmit.github.io") == ("rmit", "github.io")
        assert registrable_domain("rmit.github.io") == "rmit.github.io"


class TestTheRule:
    """`same_brand_elsewhere(domain, brand_domain)`: the brand's name under another suffix."""

    @pytest.mark.parametrize(
        ("domain", "brand_domain", "expected"),
        [
            # The pinned table.
            ("rmit.edu.vn", "rmit.edu.au", True),
            ("monash.edu.my", "monash.edu", True),
            ("rmit-online.com", "rmit.edu.au", False),
            ("pineapple.com.vn", "apple.com.au", False),
            # The same suffix is not "elsewhere".
            ("rmit.edu.au", "rmit.edu.au", False),
            # Nothing to compare: empty, an IP address, `localhost`.
            ("", "rmit.edu.au", False),
            ("rmit.edu.vn", "", False),
            ("", "", False),
            ("192.168.0.1", "rmit.edu.au", False),
            ("rmit.edu.au", "192.168.0.1", False),
            ("192.168.0.1", "192.168.0.1", False),
            ("localhost", "rmit.edu.au", False),
            ("rmit.edu.au", "localhost", False),
            ("localhost", "localhost", False),
            # Not a name at all: a bare suffix.
            ("co.uk", "co.uk", False),
            # Neither side's spelling matters.
            ("RMIT.EDU.VN", "rmit.edu.au", True),
            ("rmit.edu.vn", "RMIT.EDU.AU", True),
            # One host in two spellings is one host (the DNS form: `пример.рф` is
            # `xn--e1afmkfd.xn--p1ai`), so it is not "elsewhere" of itself, in any mix of them.
            ("пример.xn--p1ai", "пример.рф", False),
            ("пример.рф", "пример.xn--p1ai", False),
            ("xn--e1afmkfd.xn--p1ai", "пример.рф", False),
            ("пример.рф", "xn--e1afmkfd.xn--p1ai", False),
            ("xn--bcher-kva.de", "bücher.de", False),
            ("BÜCHER.DE", "xn--bcher-kva.de", False),
            # The brand's name under another suffix is found in whichever spelling either side
            # was given.
            ("bücher.at", "xn--bcher-kva.de", True),
            ("xn--bcher-kva.at", "bücher.de", True),
            ("bücher.at", "bücher.de", True),
            ("xn--bcher-kva.at", "xn--bcher-kva.de", True),
            ("BÜCHER.AT", "xn--bcher-kva.de", True),
            ("пример.com", "пример.xn--p1ai", True),
            ("xn--e1afmkfd.com", "пример.рф", True),
            # A name that cannot be put in DNS form (an underscore is not a hostname character)
            # is compared as it was given, as it always was.
            ("a_b.edu.vn", "a_b.edu.au", True),
            ("a_b.edu.au", "a_b.edu.au", False),
            # Only under the Public Suffix List's ICANN section (the founder, 2026-10-09): an
            # address on a service anyone can sign up to is nobody's other site.
            ("rmit.github.io", "rmit.edu.au", False),
            ("rmit.blogspot.com", "rmit.edu.au", False),
            ("rmit.vercel.app", "rmit.edu.au", False),
            ("rmit.edu.vn", "rmit.edu.au", True),
            ("monash.edu.my", "monash.edu", True),
            # Either side: a brand whose own domain is under a private suffix has no other sites.
            ("acme.edu.vn", "acme.github.io", False),
            ("acme.github.io", "acme.edu.vn", False),
            ("acme.vercel.app", "acme.github.io", False),
            ("acme.com", "acme.github.io", False),
        ],
    )
    def test_the_table(self, domain: str, brand_domain: str, expected: bool) -> None:
        assert same_brand_elsewhere(domain, brand_domain) is expected

    @pytest.mark.parametrize(
        ("one", "other"),
        [
            ("rmit.edu.vn", "rmit.edu.au"),
            ("monash.edu.my", "monash.edu"),
            ("rmit-online.com", "rmit.edu.au"),
            ("rmit.edu.au", "rmit.edu.au"),
            ("bücher.at", "xn--bcher-kva.de"),
            ("пример.xn--p1ai", "пример.рф"),
            ("xn--e1afmkfd.com", "пример.рф"),
        ],
    )
    def test_it_does_not_depend_on_which_is_which(self, one: str, other: str) -> None:
        assert same_brand_elsewhere(one, other) is same_brand_elsewhere(other, one)

    def test_a_longer_name_that_contains_the_brands_is_not_the_brand(self) -> None:
        """The label is compared whole: containing it, or being contained by it, is not equal."""
        assert not same_brand_elsewhere("rmit.edu.vn", "rm.edu.au")
        assert not same_brand_elsewhere("rm.edu.vn", "rmit.edu.au")
        assert not same_brand_elsewhere("myrmit.edu.vn", "rmit.edu.au")


class TestProposals:
    """`proposals(cited, client_domain, rivals, confirmed, dismissed)`: pure, no side effects."""

    RIVALS = [("Monash University", "monash.edu"), ("Deakin University", "deakin.edu.au")]

    def _propose(
        self,
        cited: dict[str, int],
        *,
        rivals: list[tuple[str, str]] | None = None,
        client_domain: str = CLIENT,
        confirmed: set[str] | None = None,
        dismissed: set[str] | None = None,
    ) -> list[Proposal]:
        return proposals(
            cited,
            client_domain,
            self.RIVALS if rivals is None else rivals,
            confirmed or set(),
            dismissed or set(),
            client_name=CLIENT_NAME,
        )

    def test_a_sibling_of_the_client_is_proposed_for_the_client(self) -> None:
        got = self._propose({"rmit.edu.vn": 4, "wikipedia.org": 9})

        assert got == [
            Proposal(
                domain="rmit.edu.vn", citations=4, brand_kind="client",
                brand_name=CLIENT_NAME, brand_domain=CLIENT,
            )
        ]

    def test_an_address_on_a_shared_hosting_service_is_proposed_for_nobody(self) -> None:
        """The founder's decision of 2026-10-09: only under the ICANN section of the list."""
        got = self._propose(
            {"rmit.github.io": 5, "rmit.blogspot.com": 4, "rmit.vercel.app": 3, "rmit.edu.vn": 2}
        )

        assert [p.domain for p in got] == ["rmit.edu.vn"]

    def test_a_brand_on_a_shared_hosting_service_has_no_other_sites(self) -> None:
        got = self._propose({"acme.edu.vn": 5, "acme.com": 2}, client_domain="acme.github.io")

        assert got == []

    def test_a_sibling_of_a_rival_is_proposed_for_that_rival(self) -> None:
        got = self._propose({"monash.edu.my": 3, "deakin.edu.my": 1})

        assert got == [
            Proposal(
                domain="monash.edu.my", citations=3, brand_kind="rival",
                brand_name="Monash University", brand_domain="monash.edu",
            ),
            Proposal(
                domain="deakin.edu.my", citations=1, brand_kind="rival",
                brand_name="Deakin University", brand_domain="deakin.edu.au",
            ),
        ]

    def test_the_first_rival_in_rank_order_gets_a_domain_two_of_them_could_have(self) -> None:
        """`acme.com.au` is the name of both rivals; the one ranked higher is asked about."""
        rivals = [("Acme Inc", "acme.com"), ("Acme Ltd", "acme.co.uk")]

        first = self._propose({"acme.com.au": 2}, rivals=rivals)
        flipped = self._propose({"acme.com.au": 2}, rivals=list(reversed(rivals)))

        assert [(p.brand_name, p.brand_domain) for p in first] == [("Acme Inc", "acme.com")]
        assert [(p.brand_name, p.brand_domain) for p in flipped] == [("Acme Ltd", "acme.co.uk")]
        assert len(first) == len(flipped) == 1

    def test_the_client_comes_before_any_rival(self) -> None:
        got = self._propose(
            {"acme.co.nz": 5},
            client_domain="acme.com.au",
            rivals=[("Acme Inc", "acme.com")],
        )

        assert [(p.brand_kind, p.brand_domain) for p in got] == [("client", "acme.com.au")]

    def test_the_clients_own_domain_is_never_proposed(self) -> None:
        """Not for the client (same suffix), and not for a rival that happens to share its name."""
        got = self._propose(
            {"acme.com": 10},
            client_domain="acme.com",
            rivals=[("Acme UK", "acme.co.uk")],
        )

        assert got == []

    def test_a_rivals_own_domain_is_never_proposed(self) -> None:
        # RMIT Vietnam is a rival here: its domain matches the client's name, and is its own.
        by_client = self._propose(
            {"rmit.edu.vn": 4}, rivals=[("RMIT Vietnam", "rmit.edu.vn")]
        )
        # And the second of two rivals with one name is not offered to the first.
        by_rival = self._propose(
            {"acme.de": 3}, rivals=[("Acme Inc", "acme.com"), ("Acme GmbH", "acme.de")]
        )

        assert by_client == []
        assert by_rival == []

    def test_a_confirmed_domain_is_never_proposed_again(self) -> None:
        got = self._propose({"rmit.edu.vn": 4, "rmit.edu.my": 2}, confirmed={"rmit.edu.vn"})

        assert [p.domain for p in got] == ["rmit.edu.my"]

    def test_a_dismissed_domain_is_never_proposed_again(self) -> None:
        got = self._propose(
            {"rmit.edu.vn": 4, "monash.edu.my": 2}, dismissed={"rmit.edu.vn", "monash.edu.my"}
        )

        assert got == []

    def test_a_domain_that_is_nobodys_name_elsewhere_is_not_proposed(self) -> None:
        got = self._propose(
            {"wikipedia.org": 9, "rmit-online.com": 5, "rmit.edu.au": 7, "pineapple.com.au": 1}
        )

        assert got == []

    def test_sorted_by_citations_then_domain(self) -> None:
        got = self._propose(
            {"rmit.edu.vn": 4, "rmit.edu.my": 4, "rmit.com.sg": 7, "monash.edu.my": 7}
        )

        assert [(p.domain, p.citations) for p in got] == [
            ("monash.edu.my", 7),
            ("rmit.com.sg", 7),
            ("rmit.edu.my", 4),
            ("rmit.edu.vn", 4),
        ]

    def test_nothing_cited_proposes_nothing(self) -> None:
        assert self._propose({}) == []
        assert self._propose({"rmit.edu.vn": 4}, rivals=[], client_domain="") == []

    def test_a_proposal_is_a_frozen_fact_and_the_inputs_are_left_alone(self) -> None:
        cited = {"rmit.edu.vn": 4}
        confirmed: set[str] = set()
        dismissed: set[str] = set()

        [only] = proposals(cited, CLIENT, [], confirmed, dismissed, client_name=CLIENT_NAME)

        with pytest.raises(dataclasses.FrozenInstanceError):
            only.citations = 99  # type: ignore[misc]
        assert (cited, confirmed, dismissed) == ({"rmit.edu.vn": 4}, set(), set())

    def test_the_clients_name_is_optional_and_falls_back_to_its_domain(self) -> None:
        [only] = proposals({"rmit.edu.vn": 4}, CLIENT, [], set(), set())

        assert (only.brand_kind, only.brand_name, only.brand_domain) == ("client", CLIENT, CLIENT)

    def test_the_same_host_in_the_other_spelling_is_never_proposed(self) -> None:
        """A cited domain keeps the spelling the engine gave: `пример.xn--p1ai` is `пример.рф`."""
        got = self._propose(
            {"пример.xn--p1ai": 3, "xn--e1afmkfd.xn--p1ai": 2, "пример.com": 1},
            client_domain="пример.рф",
            rivals=[],
        )

        assert [p.domain for p in got] == ["пример.com"]

    @pytest.mark.parametrize(
        ("client_domain", "rivals", "cited"),
        [
            # Not for the rival it belongs to, stored either way.
            pytest.param(
                CLIENT, [("Пример", "пример.рф")], "пример.xn--p1ai", id="a-rival-stored-unicode"
            ),
            pytest.param(
                CLIENT, [("Пример", "xn--e1afmkfd.xn--p1ai")], "пример.рф",
                id="a-rival-stored-punycode",
            ),
            # Not for another brand that carries its name: it is the rival's own host.
            pytest.param(
                "bücher.de", [("Bücher Austria", "bücher.at")], "xn--bcher-kva.at",
                id="another-brand-with-the-name-stored-unicode",
            ),
            pytest.param(
                "bücher.de", [("Bücher Austria", "xn--bcher-kva.at")], "bücher.at",
                id="another-brand-with-the-name-stored-punycode",
            ),
            # The client's own host is no one's other site, whichever rival carries its name.
            pytest.param(
                "bücher.de", [("Bücher Verlag", "xn--bcher-kva.com")], "xn--bcher-kva.de",
                id="the-clients-own-host-stored-unicode",
            ),
            pytest.param(
                "xn--bcher-kva.de", [("Bücher Verlag", "bücher.com")], "bücher.de",
                id="the-clients-own-host-stored-punycode",
            ),
        ],
    )
    def test_a_brands_own_host_is_never_proposed_whichever_way_it_is_spelled(
        self, client_domain: str, rivals: list[tuple[str, str]], cited: str
    ) -> None:
        assert self._propose({cited: 4}, client_domain=client_domain, rivals=rivals) == []

    def test_a_confirmed_or_dismissed_host_is_matched_in_either_spelling(self) -> None:
        """Each list holds one host spelled as punycode and one as Unicode; both are cited the
        other way round, and the one host nothing was said about is the only one proposed."""
        got = self._propose(
            {
                "bücher.at": 6, "xn--bcher-kva.be": 5,  # confirmed as punycode, as Unicode
                "bücher.nl": 4, "xn--bcher-kva.fr": 3,  # dismissed as punycode, as Unicode
                "bücher.se": 1,
            },
            client_domain="bücher.de",
            rivals=[],
            confirmed={"xn--bcher-kva.at", "bücher.be"},
            dismissed={"xn--bcher-kva.nl", "bücher.fr"},
        )

        assert [p.domain for p in got] == ["bücher.se"]

    def test_a_sibling_in_the_other_spelling_is_proposed_as_it_was_cited(self) -> None:
        """The proposal keeps the spelling the engine gave, and the brand's as it is stored."""
        got = self._propose(
            {"bücher.at": 4, "xn--bcher-kva.nl": 2}, client_domain="xn--bcher-kva.de", rivals=[]
        )

        assert got == [
            Proposal(
                domain="bücher.at", citations=4, brand_kind="client",
                brand_name=CLIENT_NAME, brand_domain="xn--bcher-kva.de",
            ),
            Proposal(
                domain="xn--bcher-kva.nl", citations=2, brand_kind="client",
                brand_name=CLIENT_NAME, brand_domain="xn--bcher-kva.de",
            ),
        ]

    def test_each_brands_split_is_made_once_and_not_again_for_every_cited_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The brands do not change while the cited domains are read, so each is split once."""
        calls: list[str] = []
        real = other_sites.name_and_icann_suffix

        def counting(domain: str) -> tuple[str, str]:
            calls.append(domain)
            return real(domain)

        monkeypatch.setattr(other_sites, "name_and_icann_suffix", counting)
        cited = {f"site-{n}.com": n for n in range(1, 41)}

        got = self._propose(cited)

        assert got == []
        brands = [CLIENT, *(domain for _name, domain in self.RIVALS)]
        assert Counter(calls) == Counter([*brands, *cited])


# --- the PUT's checks ---------------------------------------------------------------------------


@pytest.fixture
def normalised(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every string `other_sites` hands to `intake.rival_domain` while the test runs.

    `rival_domain` is the work that costs (urlparse, the Public Suffix List, IDNA), so it is what
    is counted: a refusal that has not called it has read no entry.
    """
    seen: list[str] = []
    real = other_sites.rival_domain

    def counting(raw: str) -> str:
        seen.append(raw)
        return real(raw)

    monkeypatch.setattr(other_sites, "rival_domain", counting)
    return seen


def _check(
    *,
    client_domain: str = CLIENT,
    rival_domains: list[str] | None = None,
    client_sites: list[str] | None = None,
    rival_sites: list[tuple[str, list[str]]] | None = None,
    dismissed: list[str] | None = None,
) -> Replacement:
    return checked_replacement(
        client_domain=client_domain,
        rival_domains=rival_domains or [],
        client_sites=client_sites or [],
        rival_sites=rival_sites or [],
        dismissed=dismissed or [],
    )


def _refused(**given: Any) -> dict[str, str]:
    """The one error of the 422 that `_check` raises: {field, message, type}.

    The same sentence is the problem's `detail`, so a screen that shows only that shows it.
    """
    with pytest.raises(ValidationProblem) as caught:
        _check(**given)
    [error] = caught.value.extra["errors"]
    assert error["message"] == caught.value.detail
    return error


class TestTheCapsComeBeforeAnyEntryIsRead:
    """`checked_replacement` refuses a list over its cap having read none of its entries.

    The schema lets a list carry 1,000 domains and `intake.rival_domain` parses, splits and
    IDNA-encodes each one, so one request could make ~12,000 domains of synchronous work before
    it was refused. The rivals' own domains (at most 10) are read first, because the sentence
    for a rival's list names the rival; then the lengths AS SENT are checked; only then is a
    site read.
    """

    RIVALS = ["monash.edu", "deakin.edu.au"]
    # What `rival_domain` would refuse, so a list that was read first would be refused for this.
    UNREAD = [f"https://x{n}.example/page" for n in range(201)]

    @pytest.mark.parametrize(
        ("lists", "expected"),
        [
            pytest.param(
                {"client_sites": UNREAD[:21]},
                {
                    "field": "client", "type": "too_many_sites",
                    "message": "rmit.edu.au can have at most 20 other sites.",
                },
                id="the-clients-sites",
            ),
            pytest.param(
                {"rival_sites": [("WWW.Monash.edu", UNREAD[:21]), ("deakin.edu.au", [])]},
                {
                    "field": "rivals.0.sites", "type": "too_many_sites",
                    "message": "monash.edu can have at most 20 other sites.",
                },
                id="the-first-rivals-sites",
            ),
            pytest.param(
                {"rival_sites": [("WWW.Monash.edu", []), ("deakin.edu.au", UNREAD[:21])]},
                {
                    "field": "rivals.1.sites", "type": "too_many_sites",
                    "message": "deakin.edu.au can have at most 20 other sites.",
                },
                id="the-second-rivals-sites",
            ),
            pytest.param(
                {"dismissed": UNREAD[:201]},
                {
                    "field": "dismissed", "type": "too_many_dismissed",
                    "message": "At most 200 domains can be dismissed.",
                },
                id="the-dismissed-domains",
            ),
        ],
    )
    def test_an_over_cap_list_is_refused_before_any_site_is_read(
        self, lists: dict[str, Any], expected: dict[str, str], normalised: list[str]
    ) -> None:
        given = {"rival_sites": [("WWW.Monash.edu", []), ("deakin.edu.au", [])], **lists}

        error = _refused(rival_domains=self.RIVALS, **given)

        assert error == expected
        # Only the rivals' own domains were read, as typed: no site and no dismissed domain.
        assert normalised == ["WWW.Monash.edu", "deakin.edu.au"]

    def test_a_rival_that_cannot_be_read_is_reported_before_a_list_over_its_cap(
        self, normalised: list[str]
    ) -> None:
        """The rivals' own domains are the first thing read, so they are the first thing refused."""
        error = _refused(client_sites=self.UNREAD[:21], rival_sites=[("not a domain", [])])

        assert (error["field"], error["type"]) == ("rivals.0.rivalDomain", "invalid_domain")
        assert normalised == ["not a domain"]

    def test_a_list_over_its_cap_is_reported_before_a_rival_that_is_not_on_the_list(self) -> None:
        error = _refused(
            rival_domains=self.RIVALS, client_sites=self.UNREAD[:21],
            rival_sites=[("zendesk.com", [])],
        )

        assert (error["field"], error["type"]) == ("client", "too_many_sites")

    def test_a_request_at_the_schemas_limits_reads_only_the_rivals_domains(
        self, normalised: list[str]
    ) -> None:
        """1,000 valid domains in each of 12 lists: ~12,000 to read, and ten are."""
        many = [f"site-{n}.com" for n in range(1000)]
        rivals = [(f"rival{n}.com", many) for n in range(10)]

        error = _refused(
            rival_domains=[domain for domain, _sites in rivals],
            client_sites=many, rival_sites=rivals, dismissed=many,
        )

        assert (error["field"], error["type"]) == ("client", "too_many_sites")
        assert normalised == [domain for domain, _sites in rivals]

    def test_entries_are_counted_as_sent_so_a_list_of_21_with_a_repeat_is_refused(self) -> None:
        """20 distinct domains and one more entry: refused, as the entries were not read to see
        how many are distinct."""
        own = [f"rmit.{suffix}" for suffix in SUFFIXES]
        theirs = [f"monash.{suffix}" for suffix in SUFFIXES]
        dismissed = [f"site-{n}.com" for n in range(200)]

        client_error = _refused(client_sites=[*own, own[0]])
        rival_error = _refused(
            rival_domains=["monash.edu"],
            rival_sites=[("monash.edu", [*theirs, theirs[0].upper()])],
        )
        dismissed_error = _refused(dismissed=[*dismissed, dismissed[0]])

        assert (client_error["field"], client_error["type"]) == ("client", "too_many_sites")
        assert (rival_error["field"], rival_error["type"]) == ("rivals.0.sites", "too_many_sites")
        assert (dismissed_error["field"], dismissed_error["type"]) == (
            "dismissed", "too_many_dismissed",
        )

    def test_a_request_exactly_at_the_caps_passes_and_is_the_most_work_one_can_make(
        self, normalised: list[str]
    ) -> None:
        rivals = [f"brand{n}.com" for n in range(10)]

        got = _check(
            rival_domains=rivals,
            client_sites=[f"rmit.{suffix}" for suffix in SUFFIXES],
            rival_sites=[
                (rival, [f"{rival.split('.')[0]}.{suffix}" for suffix in SUFFIXES])
                for rival in rivals
            ],
            dismissed=[f"site-{n}.com" for n in range(200)],
        )

        assert len(got.client) == 20 and len(got.dismissed) == 200
        assert [len(sites) for sites in got.rivals.values()] == [20] * 10
        # 10 rivals + 20 + 10 x 20 + 200.
        assert len(normalised) == 430


class TestEveryComparisonIsOnTheDnsForm:
    """One host in two spellings is one host, and the DNS form is what is stored.

    A cited domain keeps the spelling the engine gave and `intake.rival_domain` the spelling
    typed (`bücher.de` or `xn--bcher-kva.de`), so the checks compare `intake.dns_form`, and what
    `checked_replacement` returns for storage is that form too: lower case ASCII, so a list holds
    the same string for a host however it was typed, and holds it in any database encoding.
    """

    @pytest.mark.parametrize(
        ("client_domain", "rival_domains", "site"),
        [
            pytest.param("пример.рф", [], "пример.xn--p1ai", id="an-idn-suffix-as-punycode"),
            pytest.param("пример.xn--p1ai", [], "пример.рф", id="and-the-reverse"),
            pytest.param("пример.рф", [], "xn--e1afmkfd.xn--p1ai", id="all-punycode"),
            pytest.param("xn--bcher-kva.de", [], "bücher.de", id="punycode-as-unicode"),
            pytest.param(CLIENT, ["bücher.com"], "xn--bcher-kva.com", id="a-rivals-own-host"),
        ],
    )
    def test_a_brands_own_host_in_the_other_spelling_is_refused_as_its_own(
        self, client_domain: str, rival_domains: list[str], site: str
    ) -> None:
        error = _refused(
            client_domain=client_domain, rival_domains=rival_domains, client_sites=[site]
        )

        assert error == {
            "field": "client.0", "type": "a_brands_own_domain",
            "message": f"{site} is a brand's own domain here, so it cannot be confirmed as "
            "another site.",
        }

    def test_the_clients_own_host_in_the_other_spelling_is_refused_for_a_rival_too(self) -> None:
        error = _refused(
            client_domain="пример.рф", rival_domains=["monash.edu"],
            rival_sites=[("monash.edu", ["пример.xn--p1ai"])],
        )

        assert (error["field"], error["type"]) == ("rivals.0.sites.0", "a_brands_own_domain")

    @pytest.mark.parametrize(
        ("client_domain", "site", "stored"),
        [
            ("xn--bcher-kva.de", "bücher.at", "xn--bcher-kva.at"),
            ("bücher.de", "xn--bcher-kva.at", "xn--bcher-kva.at"),
            ("пример.рф", "пример.com", "xn--e1afmkfd.com"),
            ("пример.xn--p1ai", "xn--e1afmkfd.com", "xn--e1afmkfd.com"),
            # However else it was typed: upper case, `www.`, a root dot.
            ("bücher.de", "WWW.BÜCHER.AT.", "xn--bcher-kva.at"),
            (CLIENT, "RMIT.Edu.VN", "rmit.edu.vn"),
        ],
    )
    def test_the_brands_name_under_another_suffix_is_accepted_and_stored_in_the_dns_form(
        self, client_domain: str, site: str, stored: str
    ) -> None:
        got = _check(client_domain=client_domain, client_sites=[site])

        assert got.client == [stored]

    @pytest.mark.parametrize(
        "client_sites",
        [
            ["bücher.at", "xn--bcher-kva.at", "BÜCHER.AT"],
            ["xn--bcher-kva.at", "bücher.at"],
        ],
    )
    def test_one_host_named_twice_in_two_spellings_is_one_host_stored_once_in_the_dns_form(
        self, client_sites: list[str]
    ) -> None:
        """Whichever spelling came first, what is stored is the one string."""
        got = _check(
            client_domain="bücher.de",
            rival_domains=["xn--bcher-kva.com"],
            client_sites=client_sites,
            rival_sites=[("xn--bcher-kva.com", ["bücher.nl", "xn--bcher-kva.nl"])],
        )

        assert got.client == ["xn--bcher-kva.at"]
        assert got.rivals == {"xn--bcher-kva.com": ["xn--bcher-kva.nl"]}

    def test_dismissed_domains_are_each_once_and_stored_in_the_dns_form(self) -> None:
        got = _check(
            dismissed=["bücher.nl", "xn--bcher-kva.nl", "wikipedia.org", "WWW.Wikipedia.ORG"]
        )

        assert got.dismissed == ["xn--bcher-kva.nl", "wikipedia.org"]

    def test_everything_returned_for_storage_is_ascii_and_a_fixed_point_of_the_dns_form(
        self,
    ) -> None:
        """The client's sites, a rival's and the dismissed: one string per host, in any encoding."""
        got = _check(
            client_domain="bücher.de",
            rival_domains=["bücher.com"],
            client_sites=["WWW.BÜCHER.AT", "bücher.nl."],
            rival_sites=[("BÜCHER.com", ["bücher.fr"])],
            dismissed=["日本語.jp", "Straße.de", "wikipedia.org"],
        )

        assert got.client == ["xn--bcher-kva.at", "xn--bcher-kva.nl"]
        assert got.rivals == {"xn--bcher-kva.com": ["xn--bcher-kva.fr"]}
        assert got.dismissed == ["xn--wgv71a119e.jp", "xn--strae-oqa.de", "wikipedia.org"]
        everything = [
            *got.client, *got.dismissed, *got.rivals,
            *(site for sites in got.rivals.values() for site in sites),
        ]
        assert all(
            domain.isascii() and intake.dns_form(domain) == domain for domain in everything
        )

    @pytest.mark.parametrize(
        ("confirmed", "dismissed"),
        [("bücher.at", "xn--bcher-kva.at"), ("xn--bcher-kva.at", "bücher.at")],
    )
    def test_a_host_confirmed_in_one_spelling_and_dismissed_in_the_other(
        self, confirmed: str, dismissed: str
    ) -> None:
        error = _refused(
            client_domain="bücher.de", client_sites=[confirmed], dismissed=[dismissed]
        )

        assert error == {
            "field": "dismissed.0", "type": "confirmed_and_dismissed",
            "message": f"{dismissed} cannot be both confirmed and dismissed.",
        }

    def test_the_same_host_in_two_spellings_for_two_brands(self) -> None:
        error = _refused(
            client_domain="bücher.de",
            rival_domains=["xn--bcher-kva.com"],
            client_sites=["bücher.at"],
            rival_sites=[("xn--bcher-kva.com", ["xn--bcher-kva.at"])],
        )

        assert error == {
            "field": "rivals.0.sites.0", "type": "one_brand_only",
            "message": "xn--bcher-kva.at can be confirmed for one brand only.",
        }

    @pytest.mark.parametrize(
        ("row", "named"),
        [("bücher.com", "xn--bcher-kva.com"), ("xn--bcher-kva.com", "BÜCHER.com")],
    )
    def test_a_rival_named_in_the_other_spelling_from_its_row_is_that_rival(
        self, row: str, named: str
    ) -> None:
        """Keyed by the DNS form of the rival, like the sites; the row keeps its own domain."""
        got = _check(rival_domains=[row], rival_sites=[(named, ["bücher.at"])])

        assert got.rivals == {"xn--bcher-kva.com": ["xn--bcher-kva.at"]}

    def test_a_rival_named_twice_in_two_spellings_is_named_twice(self) -> None:
        error = _refused(
            rival_domains=["bücher.com"],
            rival_sites=[("bücher.com", []), ("xn--bcher-kva.com", [])],
        )

        assert error == {
            "field": "rivals.1.rivalDomain", "type": "repeated_rival",
            "message": "xn--bcher-kva.com is listed more than once.",
        }

    def test_a_rival_that_is_not_on_the_list_is_unknown_in_any_spelling(self) -> None:
        error = _refused(
            rival_domains=["monash.edu"], rival_sites=[("xn--bcher-kva.com", [])]
        )

        assert (error["field"], error["type"]) == ("rivals.0.rivalDomain", "unknown_rival")


def _list_of(*rows: tuple[str, list[str]]) -> CompetitorSet:
    """An unsaved list of rivals, (domain, other sites) in rank order: no database is touched."""
    return CompetitorSet(
        competitors=[
            Competitor(
                id=f"cmp_{rank}", name=f"Rival {rank}", domain=domain, rank=rank,
                other_sites=list(sites), is_suppressed=False,
            )
            for rank, (domain, sites) in enumerate(rows, start=1)
        ]
    )


class TestARivalIsMatchedToItsRowOnTheDnsForm:
    """`rival_sites_differ` and `write_rival_sites` find the row a request names by its host.

    The rival is matched on the DNS form. The sites a request holds are in the DNS form too
    (`checked_replacement` returns them so), and so are a row's, so a site respelled is the same
    stored string and no difference. A row that holds a site in another spelling (a list written
    by hand, or by a save from before the DNS form was what is stored) does differ from the
    request that names it, and is rewritten in the DNS form.
    """

    def test_a_rival_named_in_the_other_spelling_holding_the_same_sites_differs_in_nothing(
        self,
    ) -> None:
        current = _list_of(("bücher.com", ["xn--bcher-kva.at"]))

        assert not rival_sites_differ(current, {"xn--bcher-kva.com": ["xn--bcher-kva.at"]})

    def test_other_sites_are_a_difference_whichever_spelling_names_the_rival(self) -> None:
        current = _list_of(("bücher.com", ["xn--bcher-kva.at"]))

        assert rival_sites_differ(current, {"xn--bcher-kva.com": ["xn--bcher-kva.nl"]})
        assert rival_sites_differ(current, {"xn--bcher-kva.com": []})
        # A rival the request does not name wants none.
        assert rival_sites_differ(current, {})
        assert not rival_sites_differ(_list_of(("bücher.com", [])), {})

    @pytest.mark.parametrize(
        "spelling", ["bücher.at", "xn--bcher-kva.at", "BÜCHER.AT", "www.bücher.at"]
    )
    def test_a_site_respelled_is_no_difference_because_it_is_stored_as_the_same_string(
        self, spelling: str
    ) -> None:
        """The request is checked first, and what that returns is what is compared and stored."""
        current = _list_of(("bücher.com", ["xn--bcher-kva.at"]))
        wanted = _check(
            rival_domains=["bücher.com"], rival_sites=[("bücher.com", [spelling])]
        ).rivals

        assert not rival_sites_differ(current, wanted)

    def test_a_row_holding_a_site_in_a_spelling_that_is_no_longer_stored_is_rewritten(
        self,
    ) -> None:
        """A list written by hand, or before the DNS form was stored: it differs, and heals."""
        current = _list_of(("bücher.com", ["bücher.at"]))
        wanted = _check(
            rival_domains=["bücher.com"], rival_sites=[("bücher.com", ["bücher.at"])]
        ).rivals

        assert rival_sites_differ(current, wanted)
        write_rival_sites(current, wanted)
        assert current.competitors[0].other_sites == ["xn--bcher-kva.at"]
        assert not rival_sites_differ(current, wanted)

    def test_the_sites_are_written_to_the_row_named_in_the_other_spelling(self) -> None:
        current = _list_of(("bücher.com", []), ("monash.edu", ["monash.edu.my"]))

        write_rival_sites(current, {"xn--bcher-kva.com": ["xn--bcher-kva.at"]})

        # The rival the request does not name is cleared: the request is the whole state.
        assert [row.other_sites for row in current.competitors] == [["xn--bcher-kva.at"], []]

    def test_a_rival_not_on_the_set_in_any_spelling_is_refused_and_nothing_is_written(
        self,
    ) -> None:
        current = _list_of(("bücher.com", ["xn--bcher-kva.at"]))

        with pytest.raises(ValidationProblem) as caught:
            write_rival_sites(
                current, {"xn--bcher-kva.com": ["xn--bcher-kva.nl"], "zendesk.com": ["zendesk.nl"]}
            )

        assert caught.value.extra["errors"] == [
            {
                "field": "rivals.1.rivalDomain", "type": "unknown_rival",
                "message": "zendesk.com is not one of this client's rivals.",
            }
        ]
        assert current.competitors[0].other_sites == ["xn--bcher-kva.at"]


class TestTheSharedHostingRefusal:
    """`checked_replacement` and a site under a private suffix: one rule, so no disagreement."""

    def test_a_site_on_a_shared_hosting_service_is_refused_with_its_own_sentence(self) -> None:
        error = _refused(client_sites=["rmit.github.io"])

        assert error == {
            "field": "client.0",
            "message": (
                "rmit.github.io is an address on a service anyone can sign up to, so it "
                "cannot be confirmed as one of rmit.edu.au's other sites."
            ),
            "type": "shared_hosting",
        }

    def test_a_brand_on_shared_hosting_refuses_any_site_as_not_the_same_name(self) -> None:
        """The site's own suffix is ordinary, so the sentence is the ordinary one."""
        error = _refused(client_domain="acme.github.io", client_sites=["acme.edu.vn"])

        assert error["type"] == "not_the_same_name"
        assert error["message"] == (
            "acme.edu.vn is not the same name as acme.github.io with a different ending, so it "
            "cannot be confirmed as one of its other sites."
        )

    @pytest.mark.parametrize("site", ["wikipedia.org", "rmit-online.com", "rmit.edu.au"])
    def test_a_site_on_an_ordinary_suffix_keeps_its_other_refusals(self, site: str) -> None:
        assert _refused(client_sites=[site])["type"] != "shared_hosting"

    def test_a_rival_site_on_a_shared_hosting_service_names_the_rivals_field(self) -> None:
        error = _refused(
            rival_domains=["monash.edu"], rival_sites=[("monash.edu", ["monash.vercel.app"])]
        )

        assert (error["field"], error["type"]) == ("rivals.0.sites.0", "shared_hosting")

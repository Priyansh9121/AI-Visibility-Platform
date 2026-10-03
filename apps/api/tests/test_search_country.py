"""Detection searches, and asks the model, in the client's own market — 2026-10-03.

Production on 2026-10-03, two new clients: an Indian business (a .in domain) logged
`competitors.detected confidence=0.000 status=weak_signal returned=5`, and an Australian
one (.com.au, one query timed out) `confidence=0.000`. Confidence is the share of the
returned rivals that BOTH signals found, so 0.000 is five rivals and not one found by search
and by the model. Reproduced through this code path, with real searches and the same
co-citation answers in both arms, varying only `gl`: the .com.au client under gl=us made 2
queries and got 17 result domains, none a brand the model had named, 0.000; under gl=au the
same 2 queries returned one rival's own domain, that rival was corroborated, 0.200. And the
model was never told the domain: for the .in client's brand its brand-anchored prompts named
US packaged-waffle brands, and with the brand and its domain as the subject all four named
Indian dessert chains.

So `serp.search_country` reads the client's market off its domain's country-code TLD,
`search` sends it as `gl`, and `detect_from_facts` passes it to the search and names the
domain to the model. The scoring formula does not change; detection's inputs do.

Nothing here reaches SerpApi or the model: `search` runs against a `MockTransport`, and
`detect_from_facts` against fakes for `search_many` and `run_seed_prompts`. (The SerpApi
account is a free plan of 250 searches a month, shared with production.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import structlog

from avp_api.config import Settings
from avp_api.models import Client
from avp_api.models.client import ClassificationStatus, ClientKind
from avp_api.services import cocitation as cocitation_service
from avp_api.services import competitors as detection
from avp_api.services import serp
from avp_api.services.cocitation import CoCitationResult
from avp_api.services.serp import SerpResult, search, search_many

# `search` and `search_many` are imported here, by name, on purpose: conftest's autouse
# `stub_chain_externals` replaces `serp.search_many` for every test with a fake that never
# searches. The name bound at import is the real one (as in test_serp_search.py).

API_KEY = "SERPKEYSENTINEL-4d1e8a02c6b97f31"

# The briefed list and nothing else: a registrable domain's last label -> Google's `gl`.
# The United Kingdom's is "uk" in both columns: Google's `gl` for it is not "gb".
LISTED_MARKETS: dict[str, str] = {
    "au": "au", "nz": "nz", "uk": "uk", "ie": "ie", "ca": "ca", "us": "us", "za": "za",
    "in": "in", "sg": "sg", "my": "my", "ph": "ph", "hk": "hk", "id": "id", "jp": "jp",
}


@pytest.fixture
def serp_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        serpapi_key=API_KEY,
    )


class RecordingSerpApi:
    """SerpApi as `search` sees it: every request is kept, and answered with a result page."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        organic = [{"position": 1, "link": "https://front.com/page", "title": "Title"}]
        return httpx.Response(200, json={"organic_results": organic})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> RecordingSerpApi:
        """Every client `search` opens talks to `handler`, over a transport that never leaves."""
        real = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(self.handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(serp.httpx, "AsyncClient", factory)
        return self


def sent_countries(fake: RecordingSerpApi) -> list[list[str]]:
    """The `gl` of each request, as a list per request, so a doubled parameter shows."""
    return [request.url.params.get_list("gl") for request in fake.requests]


# ---------------------------------------------------------------------------
# which country a domain names
# ---------------------------------------------------------------------------


class TestSearchCountry:
    @pytest.mark.parametrize(
        ("domain", "country"),
        [
            ("example-bank.com.au", "au"),
            ("example-cafe.in", "in"),
            ("example.co.uk", "uk"),
            ("example.co.nz", "nz"),
            ("example.com", "au"),
            ("example.io", "au"),
            ("example.co", "au"),
            ("EXAMPLE.COM.AU.", "au"),
            ("example.ca", "ca"),
        ],
    )
    def test_a_country_code_tld_names_the_market_and_anything_else_the_default(
        self, domain: str, country: str
    ) -> None:
        assert serp.search_country(domain) == country

    @pytest.mark.parametrize(("tld", "gl"), sorted(LISTED_MARKETS.items()))
    def test_every_listed_country_code_maps_to_its_own_gl(self, tld: str, gl: str) -> None:
        assert serp.search_country(f"example.{tld}") == gl
        # Behind a second-level suffix too, as `.com.au` and `.co.nz` are: the last label.
        assert serp.search_country(f"example.co.{tld}") == gl

    def test_the_list_is_exactly_the_briefed_fourteen(self) -> None:
        assert dict(serp.SEARCH_COUNTRY_BY_TLD) == LISTED_MARKETS

    def test_the_default_is_australia_because_the_pilots_are_australian_agencies(self) -> None:
        assert serp.DEFAULT_SEARCH_COUNTRY == "au"

    @pytest.mark.parametrize(
        "label",
        ["com", "org", "net", "io", "ai", "co", "dev", "app", "info",
         "de", "fr", "nl", "es", "br", "mx", "ae", "kr", "cn", "gb"],
    )
    def test_a_label_that_is_not_listed_searches_the_default(self, label: str) -> None:
        """`gb` included: the United Kingdom's own country-code TLD is `.uk`."""
        assert serp.search_country(f"example.{label}") == "au"

    def test_it_is_the_last_label_that_decides(self) -> None:
        assert serp.search_country("uk.example.com") == "au"
        assert serp.search_country("in.example.com") == "au"
        assert serp.search_country("jp.example.io") == "au"
        assert serp.search_country("example.com.in") == "in"

    def test_case_whitespace_and_a_trailing_dot_do_not_matter(self) -> None:
        assert serp.search_country("EXAMPLE.CO.NZ") == "nz"
        assert serp.search_country("  Example.In  ") == "in"
        assert serp.search_country("example.in.") == "in"
        assert serp.search_country("EXAMPLE.COM.AU.") == serp.search_country("example.com.au")

    def test_a_name_with_no_dot_has_no_tld_and_gets_the_default(self) -> None:
        for not_a_domain in ("", ".", "in", "uk", "nz"):
            assert serp.search_country(not_a_domain) == "au", not_a_domain


# ---------------------------------------------------------------------------
# `search` and `search_many` send it
# ---------------------------------------------------------------------------


class TestSearchSendsTheMarketAsGl:
    async def test_the_country_it_is_given_is_sent_as_gl(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = RecordingSerpApi().install(monkeypatch)

        result = await search("acme alternatives", settings=serp_settings, country="in")

        assert result.ok
        assert sent_countries(fake) == [["in"]]

    async def test_every_listed_market_is_sent_as_it_is(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = RecordingSerpApi().install(monkeypatch)
        markets = sorted(set(LISTED_MARKETS.values()))

        for market in markets:
            await search("q", settings=serp_settings, country=market)

        assert sent_countries(fake) == [[market] for market in markets]

    async def test_without_a_country_it_searches_australia_and_not_the_united_states(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = RecordingSerpApi().install(monkeypatch)

        await search("q", settings=serp_settings)

        assert sent_countries(fake) == [["au"]]

    async def test_the_rest_of_the_request_is_as_it_was(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """The language stays English whatever the market, and nothing else is added."""
        fake = RecordingSerpApi().install(monkeypatch)

        await search("acme alternatives", settings=serp_settings, country="jp")

        (request,) = fake.requests
        assert dict(request.url.params) == {
            "engine": "google",
            "q": "acme alternatives",
            "api_key": API_KEY,
            "num": "10",
            "hl": "en",
            "gl": "jp",
        }

    @pytest.mark.parametrize(
        "value",
        ["zz", "de", "", "IN", "united states", "in&gl=us", "us\r\nHost: evil.example"],
        ids=["unknown", "real-but-unlisted", "empty", "upper-case", "words", "param", "header"],
    )
    async def test_a_country_that_is_not_a_listed_one_is_sent_as_au(
        self, value: str, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """Never an arbitrary string as `gl`: only what `search_country` can return."""
        fake = RecordingSerpApi().install(monkeypatch)

        await search("q", settings=serp_settings, country=value)

        assert sent_countries(fake) == [["au"]]
        (request,) = fake.requests
        assert request.url.params["hl"] == "en"


class TestSearchManyCarriesTheMarket:
    async def test_every_query_is_searched_in_the_country_it_is_given(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = RecordingSerpApi().install(monkeypatch)
        queries = [f"q{i}" for i in range(4)]

        results = await search_many(queries, settings=serp_settings, country="in")

        assert all(result.ok for result in results)
        assert sorted(request.url.params["q"] for request in fake.requests) == queries
        assert sent_countries(fake) == [["in"]] * 4

    async def test_without_a_country_every_query_is_searched_in_australia(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = RecordingSerpApi().install(monkeypatch)

        await search_many(["a", "b", "c", "d"], settings=serp_settings)

        assert sent_countries(fake) == [["au"]] * 4

    async def test_a_country_that_is_not_listed_is_replaced_for_every_query(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = RecordingSerpApi().install(monkeypatch)

        await search_many(["a", "b"], settings=serp_settings, country="zz")

        assert sent_countries(fake) == [["au"]] * 2


# ---------------------------------------------------------------------------
# detection uses it, and tells the model whose market this is
# ---------------------------------------------------------------------------


@dataclass
class Seen:
    """What `detect_from_facts` handed to the two discovery signals."""

    searches: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)
    prompts: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> Seen:
    """Both signals faked at the boundary, each keeping the arguments it was called with."""
    record = Seen()

    async def fake_search_many(queries: list[str], **kwargs: Any) -> list[SerpResult]:
        record.searches.append((list(queries), kwargs))
        return [SerpResult(query=q) for q in queries]

    async def fake_run_seed_prompts(prompts: list[str], **kwargs: Any) -> list[CoCitationResult]:
        record.prompts.append((list(prompts), kwargs))
        return [CoCitationResult(prompt=p) for p in prompts]

    monkeypatch.setattr(detection.serp_service, "search_many", fake_search_many)
    monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", fake_run_seed_prompts)
    return record


class TestDetectionSearchesAndAsksInTheClientsMarket:
    @pytest.mark.parametrize(
        ("brand", "domain", "country", "subject"),
        [
            ("Example Cafe", "example-cafe.in", "in", "Example Cafe (example-cafe.in)"),
            (
                "Example Bank", "example-bank.com.au", "au",
                "Example Bank (example-bank.com.au)",
            ),
            ("Example", "example.com", "au", "Example (example.com)"),
        ],
        ids=[".in", ".com.au", ".com"],
    )
    async def test_the_search_runs_in_the_domains_country_and_the_model_is_told_the_domain(
        self, brand: str, domain: str, country: str, subject: str,
        seen: Seen, serp_settings: Settings,
    ) -> None:
        await detection.detect_from_facts(
            brand_name=brand, domain=domain, industry="dessert cafes", settings=serp_settings
        )

        ((_, search_kwargs),) = seen.searches
        assert search_kwargs["country"] == country
        ((_, prompt_kwargs),) = seen.prompts
        assert prompt_kwargs["subject_brand"] == subject

    async def test_a_name_with_no_brand_is_named_with_the_domain_too(
        self, seen: Seen, serp_settings: Settings
    ) -> None:
        await detection.detect_from_facts(
            brand_name=None, name="Example Holdings", domain="example-cafe.in",
            settings=serp_settings,
        )

        ((_, prompt_kwargs),) = seen.prompts
        assert prompt_kwargs["subject_brand"] == "Example Holdings (example-cafe.in)"

    async def test_the_brand_is_preferred_to_the_name(
        self, seen: Seen, serp_settings: Settings
    ) -> None:
        await detection.detect_from_facts(
            brand_name="Example Cafe", name="Example Holdings", domain="example-cafe.in",
            settings=serp_settings,
        )

        ((_, prompt_kwargs),) = seen.prompts
        assert prompt_kwargs["subject_brand"] == "Example Cafe (example-cafe.in)"

    async def test_with_neither_brand_nor_name_the_subject_is_the_domain_alone(
        self, seen: Seen, serp_settings: Settings
    ) -> None:
        await detection.detect_from_facts(
            brand_name=None, name=None, domain="example-cafe.in", settings=serp_settings
        )

        ((_, search_kwargs),) = seen.searches
        assert search_kwargs["country"] == "in"
        ((_, prompt_kwargs),) = seen.prompts
        assert prompt_kwargs["subject_brand"] == "example-cafe.in"

    async def test_what_is_asked_is_what_it_was_and_only_where_changes(
        self, seen: Seen, serp_settings: Settings
    ) -> None:
        """Query shapes and seed prompts are untouched; settings still reach both signals."""
        await detection.detect_from_facts(
            brand_name="Example Cafe", domain="example-cafe.in",
            industry="dessert cafes", niche="waffles and shakes", settings=serp_settings,
        )

        facts = {
            "brand_name": "Example Cafe", "domain": "example-cafe.in",
            "industry": "dessert cafes", "niche": "waffles and shakes",
        }
        ((queries, search_kwargs),) = seen.searches
        assert queries == serp.build_queries(**facts)
        assert search_kwargs["settings"] is serp_settings
        ((prompts, prompt_kwargs),) = seen.prompts
        assert prompts == cocitation_service.build_seed_prompts(**facts)
        assert prompt_kwargs["settings"] is serp_settings

    async def test_the_clients_own_domain_decides_through_detect_for_client(
        self, seen: Seen, serp_settings: Settings
    ) -> None:
        """The entry point the endpoint and the scan chain call, not only the core under it."""
        client = Client(
            id="clnt_test", agency_id="agcy_test",
            name="Example Holdings", brand_name="Example Cafe", domain="example-cafe.in",
            kind=ClientKind.PROSPECT,
            classification_status=ClassificationStatus.CLASSIFIED,
            industry="dessert cafes", industry_niche=None,
        )

        await detection.detect_for_client(client, settings=serp_settings)

        ((_, search_kwargs),) = seen.searches
        assert search_kwargs["country"] == "in"
        ((_, prompt_kwargs),) = seen.prompts
        assert prompt_kwargs["subject_brand"] == "Example Cafe (example-cafe.in)"


@pytest.mark.usefixtures("seen")
class TestTheDetectedLineSaysWhichMarket:
    @pytest.mark.parametrize(
        ("domain", "country"),
        [("example-cafe.in", "in"), ("example-bank.com.au", "au"), ("example.com", "au")],
    )
    async def test_the_line_carries_the_country_and_the_rest_it_always_had(
        self, domain: str, country: str, serp_settings: Settings
    ) -> None:
        with structlog.testing.capture_logs() as events:
            await detection.detect_from_facts(
                brand_name="Acme", domain=domain, client_id="clnt_test", settings=serp_settings
            )

        (line,) = [event for event in events if event["event"] == "competitors.detected"]
        assert line["country"] == country
        assert set(line) - {"event", "log_level"} == {
            "client_id", "domain", "status", "confidence", "returned", "considered", "country",
        }
        assert line["domain"] == domain

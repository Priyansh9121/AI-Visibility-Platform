"""The crawler obeys robots.txt — 2026-09-29.

Until this date it read robots.txt only to report on it and fetched pages
whether or not the file allowed them. Now both fetchers — the intake crawl when
a site is added, the technical audit with each scan — read robots.txt first and
check every page before requesting it (`crawl.RobotsGate`), through the one
RFC 9309 parser (`ai_crawlers.crawl_policy`).

No network and no browser. `robots_web` (conftest) answers the gate's HTTP from
memory and records every request; `FakeChromium` below stands in for
Playwright and records every launch and navigation — so "not fetched" is
asserted on what was actually requested.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from httpx import AsyncClient

from avp_api.config import Settings
from avp_api.models.competitor import DetectionStatus
from avp_api.models.engine_result import EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import crawl, technical_audit
from avp_api.services.ai_crawlers import AccessVerdict, PolicyBasis, crawl_policy
from avp_api.services.classify import classify
from avp_api.services.scoring import (
    FORMULA_VERSION,
    CompetitorFacts,
    Dimension,
    ResultFacts,
    compute_inputs_digest,
    compute_score,
)

BASE = "/api/v1"
SITE = "https://prospect-site.com"
TOKEN = crawl.CRAWLER_TOKEN


def allows(body: str, path: str, *, status: int | None = 200) -> bool:
    return crawl_policy(status, body, token=TOKEN).allows(path)


# ---------------------------------------------------------------------------
# the parser, for our own crawler
# ---------------------------------------------------------------------------


class TestWhichRulesApply:
    def test_a_group_naming_avpscanbot_beats_the_star_group(self) -> None:
        body = "User-agent: *\nDisallow: /\n\nUser-agent: AVPScanBot\nAllow: /\n"
        assert allows(body, "/")
        body = "User-agent: *\nAllow: /\n\nUser-agent: AVPScanBot\nDisallow: /\n"
        assert not allows(body, "/")

    def test_the_token_matches_whatever_its_case(self) -> None:
        assert not allows("User-agent: avpscanbot\nDisallow: /\n", "/")

    def test_the_star_group_applies_when_nothing_names_it(self) -> None:
        body = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private\n"
        assert allows(body, "/")
        assert not allows(body, "/private/page")

    def test_no_applicable_group_allows_everything(self) -> None:
        assert allows("User-agent: GPTBot\nDisallow: /\n", "/")
        assert allows("", "/")

    def test_an_html_page_served_as_robots_txt_allows_everything(self) -> None:
        # A single-page app answers /robots.txt with its index page. The RFC
        # parses any 2xx body; this one has no groups, so nothing is forbidden.
        assert allows("<!doctype html><html><body>app</body></html>", "/")

    def test_groups_naming_the_same_agent_are_combined(self) -> None:
        body = "User-agent: *\nDisallow: /a\n\nUser-agent: *\nDisallow: /b\n"
        assert not allows(body, "/a/1")
        assert not allows(body, "/b/1")
        assert allows(body, "/c")

    def test_the_path_is_matched_with_its_query(self) -> None:
        body = "User-agent: *\nDisallow: /*?session=\n"
        assert not allows(body, "/cart?session=1")
        assert allows(body, "/cart")


class TestLongestMatch:
    def test_the_longest_matching_rule_wins(self) -> None:
        body = "User-agent: AVPScanBot\nDisallow: /shop\nAllow: /shop/about\n"
        assert allows(body, "/shop/about/team")
        assert not allows(body, "/shop/cart")

    def test_an_allow_overrides_a_shorter_disallow(self) -> None:
        body = "User-agent: *\nDisallow: /\nAllow: /about\n"
        assert allows(body, "/about")
        assert not allows(body, "/")

    def test_a_longer_disallow_overrides_a_shorter_allow(self) -> None:
        body = "User-agent: *\nAllow: /\nDisallow: /about\n"
        assert not allows(body, "/about")
        assert allows(body, "/")

    def test_allow_wins_a_tie(self) -> None:
        body = "User-agent: *\nDisallow: /page\nAllow: /page\n"
        assert allows(body, "/page")


class TestWhenTheFileCannotBeRead:
    @pytest.mark.parametrize("status", [404, 410, 403, 401])
    def test_a_4xx_means_there_is_no_file_so_everything_is_allowed(self, status: int) -> None:
        policy = crawl_policy(status, "User-agent: *\nDisallow: /\n", token=TOKEN)
        assert policy.basis is PolicyBasis.UNAVAILABLE
        assert policy.allows("/")

    @pytest.mark.parametrize("status", [500, 502, 503, 429])
    def test_a_5xx_or_429_means_nothing_is_fetched_this_run(self, status: int) -> None:
        policy = crawl_policy(status, "", token=TOKEN)
        assert policy.basis is PolicyBasis.UNREACHABLE
        assert not policy.allows("/")

    def test_no_response_at_all_means_nothing_is_fetched(self) -> None:
        assert not crawl_policy(None, "", token=TOKEN).allows("/")


# ---------------------------------------------------------------------------
# a Chromium stand-in
# ---------------------------------------------------------------------------


class FakeChromium:
    """`async_playwright()` for the crawls: pages from a dict, every navigation recorded.

    `landings` maps a URL to where the browser ends up — a redirect only the
    browser is given.
    """

    def __init__(
        self, pages: dict[str, dict[str, Any]] | None = None,
        landings: dict[str, str] | None = None,
    ) -> None:
        self.pages = pages or {}
        self.landings = landings or {}
        self.launches = 0
        self.navigations: list[str] = []

    def __call__(self) -> _Playwright:
        return _Playwright(self)


class _Playwright:
    def __init__(self, web: FakeChromium) -> None:
        self.web = web
        self.chromium = self

    async def __aenter__(self) -> _Playwright:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def launch(self, **kwargs: Any) -> _Browser:  # noqa: ARG002
        self.web.launches += 1
        return _Browser(self.web)


class _Browser:
    def __init__(self, web: FakeChromium) -> None:
        self.web = web

    async def new_context(self, **kwargs: Any) -> _Browser:  # noqa: ARG002
        return self

    async def route(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def new_page(self) -> _Page:
        return _Page(self.web)

    async def close(self) -> None:
        return None


class _Page:
    def __init__(self, web: FakeChromium) -> None:
        self.web = web
        self.url = "about:blank"

    def set_default_timeout(self, ms: int) -> None:
        return None

    async def goto(self, url: str, **kwargs: Any) -> SimpleNamespace:  # noqa: ARG002
        # As Chromium does: an address with no path is its root.
        if not urlsplit(url).path:
            url += "/"
        self.web.navigations.append(url)
        self.url = self.web.landings.get(url, url)
        page = self.web.pages.get(self.url)
        return SimpleNamespace(status=page["status"] if page else 404, headers={})

    async def evaluate(self, script: str) -> dict[str, Any]:  # noqa: ARG002
        page = self.web.pages.get(self.url, {})
        return {
            "text": page.get("text", "A business that does a thing, described at length."),
            "title": page.get("title", "A page"),
            "hasMetaDescription": True, "lang": "en", "h1": 1, "h2": 0,
            "jsonLd": "", "microdata": [], "links": page.get("links", []),
        }


def page(*links: str) -> dict[str, Any]:
    return {"status": 200, "links": list(links)}


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


class TestTheGate:
    async def test_robots_txt_is_fetched_once_per_host_with_our_user_agent(
        self, robots_web: Any
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: *\nDisallow: /x\n")
        gate = crawl.RobotsGate(user_agent="AVPScanBot-test")
        assert await gate.allows(f"{SITE}/")
        assert not await gate.allows(f"{SITE}/x/1")
        assert await gate.allows(f"{SITE}/about")
        assert robots_web.requests == [("GET", f"{SITE}/robots.txt", "AVPScanBot-test")]

    async def test_a_redirect_to_a_host_that_disallows_it_is_never_requested(
        self, robots_web: Any
    ) -> None:
        robots_web.serve(f"{SITE}/", status=301, headers={"location": "https://www.prospect-site.com/"})
        robots_web.serve(
            "https://www.prospect-site.com/robots.txt", body="User-agent: AVPScanBot\nDisallow: /\n"
        )
        gate = crawl.RobotsGate(user_agent="ua")

        assert await gate.resolve(f"{SITE}/") is None
        # www's robots.txt was read; www's homepage was not.
        assert "https://www.prospect-site.com/" not in robots_web.fetched()

    async def test_an_allowed_redirect_is_followed_to_where_it_leads(self, robots_web: Any) -> None:
        robots_web.serve(f"{SITE}/", status=301, headers={"location": "https://www.prospect-site.com/"})
        robots_web.serve("https://www.prospect-site.com/", status=200)
        gate = crawl.RobotsGate(user_agent="ua")
        assert await gate.resolve(f"{SITE}/") == "https://www.prospect-site.com/"
        # Asked with HEAD: headers, never a page.
        assert set(robots_web.fetched("GET")) == {
            f"{SITE}/robots.txt", "https://www.prospect-site.com/robots.txt"
        }

    async def test_a_robots_txt_that_times_out_disallows_everything(self, robots_web: Any) -> None:
        robots_web.fail(f"{SITE}/robots.txt", httpx.ConnectTimeout("slow"))
        gate = crawl.RobotsGate(user_agent="ua")
        assert await gate.resolve(f"{SITE}/") is None
        assert robots_web.fetched() == [f"{SITE}/robots.txt"]


# ---------------------------------------------------------------------------
# intake: adding a client
# ---------------------------------------------------------------------------


class TestIntake:
    async def test_a_disallowed_homepage_means_no_page_is_fetched(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: AVPScanBot\nDisallow: /\n")
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert (result.ok, result.error_code) == (False, "ROBOTS_DISALLOWED")
        # Zero page fetches: no browser, not even a HEAD — only robots.txt.
        assert chromium.launches == 0 and chromium.navigations == []
        assert robots_web.fetched() == [f"{SITE}/robots.txt"]
        assert result.signals.pages_fetched == 0 and result.text_extract == ""

        # The existing "couldn't classify" path, with the new reason.
        outcome = await classify(result, settings=Settings(_env_file=None))
        assert (outcome.status, outcome.reason_code) == ("unclassifiable", "ROBOTS_DISALLOWED")

    async def test_the_client_is_still_created_with_the_reason_recorded(
        self, client: AsyncClient, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: *\nDisallow: /\n")
        chromium = FakeChromium()
        monkeypatch.setattr(crawl, "async_playwright", chromium)
        await client.post(
            f"{BASE}/auth/sign-up",
            json={"agencyName": "Robots Agency", "fullName": "Op", "email": "robots@test.example",
                  "password": "correct-horse-battery-staple"},
        )

        resp = await client.post(f"{BASE}/clients", json={"url": SITE, "classify": True})

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ROBOTS_DISALLOWED"
        assert body["industry"] is None
        assert chromium.launches == 0

    async def test_a_disallowed_candidate_is_skipped_and_the_others_are_fetched(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: *\nDisallow: /services\n")
        chromium = FakeChromium(pages={
            f"{SITE}/": page("/about", "/services", "/products"),
            f"{SITE}/about": page(),
            f"{SITE}/services": page(),
            f"{SITE}/products": page(),
        })
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com", max_pages=3)

        assert result.ok
        assert f"{SITE}/services" not in chromium.navigations
        # /services took no slot: the next candidate allowed was read instead.
        assert chromium.navigations == [f"{SITE}/", f"{SITE}/about", f"{SITE}/products"]
        assert result.signals.pages_fetched == 3

    async def test_a_page_the_browser_lands_on_elsewhere_is_checked_too(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A redirect only the browser sees, to a host whose robots.txt says no.
        robots_web.serve(
            "https://elsewhere-host.com/robots.txt", body="User-agent: *\nDisallow: /\n"
        )
        chromium = FakeChromium(
            pages={"https://elsewhere-host.com/": page()},
            landings={f"{SITE}/": "https://elsewhere-host.com/"},
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert result.error_code == "ROBOTS_DISALLOWED"
        assert result.text_extract == "" and result.signals.pages_fetched == 0

    @pytest.mark.parametrize(("status", "crawled"), [(404, True), (410, True), (503, False)])
    async def test_a_missing_file_allows_and_a_server_error_does_not(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, status: int, crawled: bool
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", status=status)
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert (chromium.launches == 1) is crawled
        assert result.ok is crawled
        if not crawled:
            assert result.error_code == "ROBOTS_DISALLOWED"


# ---------------------------------------------------------------------------
# the audit: each scan
# ---------------------------------------------------------------------------

BLANKET = f"User-agent: *\nDisallow: /\nSitemap: {SITE}/sitemap.xml\n"


class TestAudit:
    async def test_a_disallowed_homepage_launches_no_browser_and_keeps_the_robots_facts(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body=BLANKET)
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        signals = await technical_audit.audit_site(SITE)

        assert chromium.launches == 0 and chromium.navigations == []
        assert robots_web.fetched() == [f"{SITE}/robots.txt"]
        assert (signals.ok, signals.error_code) == (False, "ROBOTS_DISALLOWED")
        # What robots.txt itself says is recorded, from the file we may read.
        assert signals.robots_txt_present is True
        assert signals.robots_allows_crawl is False
        assert signals.has_sitemap is True
        assert {a.verdict for a in signals.ai_crawler_access} == {AccessVerdict.BLOCKED}

        checks = {c.key: c for c in technical_audit.build_checks(signals)}
        # The blanket block stays the finding it always was.
        assert (checks["indexable"].status, checks["indexable"].detail_code) == (
            "fail", "ROBOTS_TXT_DISALLOW"
        )
        assert checks["robots_txt_present"].status == "pass"
        assert checks["sitemap_present"].status == "pass"
        for key in technical_audit.PAGE_CHECKS:
            assert (checks[key].status, checks[key].detail_code) == (
                "not_applicable", "ROBOTS_DISALLOWED"
            ), key
        assert not [c for c in checks.values() if c.status == "error"]

        outcome = technical_audit.score_audit(signals)
        assert (outcome.scored, outcome.score) == (False, None)

    async def test_a_block_on_avpscanbot_alone_is_not_an_indexing_finding(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: AVPScanBot\nDisallow: /\n")
        monkeypatch.setattr(technical_audit, "async_playwright", FakeChromium())

        signals = await technical_audit.audit_site(SITE)

        checks = {c.key: c for c in technical_audit.build_checks(signals)}
        assert signals.error_code == "ROBOTS_DISALLOWED"
        assert (checks["indexable"].status, checks["indexable"].detail_code) == (
            "not_applicable", "ROBOTS_DISALLOWED"
        )

    async def test_a_disallowed_sitemap_is_not_fetched(self, robots_web: Any) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: *\nDisallow: /sitemap.xml\n")
        robots_web.serve(f"{SITE}/sitemap.xml", body="<?xml version='1.0'?><urlset/>")
        gate = crawl.RobotsGate(user_agent="ua")

        side = await technical_audit._fetch_side_files(gate, SITE)  # noqa: SLF001

        assert f"{SITE}/sitemap.xml" not in robots_web.fetched()
        assert side.sitemap_disallowed is True and side.sitemap_present is False
        signals = technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=True, sitemap_disallowed=True
        )
        sitemap = next(
            c for c in technical_audit.build_checks(signals) if c.key == "sitemap_present"
        )
        # Unknown, so not the NO_SITEMAP_XML finding.
        assert (sitemap.status, sitemap.detail_code) == ("not_applicable", "ROBOTS_DISALLOWED")

    async def test_an_allowed_sitemap_is_still_fetched(self, robots_web: Any) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: *\nDisallow: /private\n")
        robots_web.serve(
            f"{SITE}/sitemap.xml", body="<?xml version='1.0'?><urlset/>",
            headers={"content-type": "application/xml"},
        )
        side = await technical_audit._fetch_side_files(  # noqa: SLF001
            crawl.RobotsGate(user_agent="ua"), SITE
        )
        assert f"{SITE}/sitemap.xml" in robots_web.fetched()
        assert side.sitemap_present is True and side.sitemap_disallowed is False


# ---------------------------------------------------------------------------
# scoring: excluded, never a zero — and the formula unchanged
# ---------------------------------------------------------------------------

RESULTS = [
    ResultFacts(
        result_id=f"eres_{i:04d}", engine="claude", status=EngineResultStatus.OK,
        mentioned=i % 2 == 0,
        sentiment=Sentiment.POSITIVE if i % 2 == 0 else None,
        brands=(("Help Scout", True), ("Front", False)) if i % 2 == 0 else (("Front", False),),
        citations=(("g2.com", False),),
        intent=PromptIntent.AWARENESS,
    )
    for i in range(6)
]
COMPETITORS = [CompetitorFacts(competitor_id="comp_0001", name="Front", domain="front.com")]


def _score(**kwargs: Any) -> Any:
    return compute_score(
        RESULTS, COMPETITORS, competitor_set_status=DetectionStatus.OK, **kwargs
    )


class TestScoring:
    def test_a_disallowed_site_scores_as_if_technical_foundation_were_excluded(self) -> None:
        disallowed = _score(
            technical_foundation=None, technical_foundation_exclusion="CRAWL_DISALLOWED"
        )
        excluded = _score(technical_foundation=None)
        zero = _score(technical_foundation=Decimal("0"))

        assert disallowed.composite == excluded.composite
        assert disallowed.composite != zero.composite
        # Same redistribution, dimension for dimension.
        assert {d: s.weight for d, s in disallowed.sub_scores.items() if s.included} == {
            d: s.weight for d, s in excluded.sub_scores.items() if s.included
        }
        assert FORMULA_VERSION == "v5"

    def test_it_is_excluded_under_its_own_reason(self) -> None:
        disallowed = _score(
            technical_foundation=None, technical_foundation_exclusion="CRAWL_DISALLOWED"
        )
        tf = Dimension.TECHNICAL_FOUNDATION.value
        assert disallowed.excluded_dimensions[tf] == "CRAWL_DISALLOWED"
        # "had not been audited" would be false: the audit ran.
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in disallowed.degradation_flags
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" in _score().degradation_flags
        assert _score().excluded_dimensions[tf] == "NOT_YET_MEASURED"

    def test_every_existing_digest_is_unchanged(self) -> None:
        plain = compute_inputs_digest(RESULTS, COMPETITORS, None)
        assert compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion=None
        ) == plain
        assert compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion="CRAWL_DISALLOWED"
        ) != plain

    def test_a_measured_value_ignores_an_exclusion_reason(self) -> None:
        measured = _score(technical_foundation=Decimal("80"))
        assert _score(
            technical_foundation=Decimal("80"), technical_foundation_exclusion="CRAWL_DISALLOWED"
        ).inputs_digest == measured.inputs_digest

    def test_an_unknown_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown Technical Foundation exclusion"):
            _score(technical_foundation=None, technical_foundation_exclusion="NOPE")


def test_the_product_token_is_the_one_in_the_user_agent() -> None:
    assert f"(compatible; {crawl.CRAWLER_TOKEN}/" in crawl.USER_AGENT_TEMPLATE

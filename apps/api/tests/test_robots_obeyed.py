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

import asyncio
import codecs
import gzip
import tracemalloc
import zlib
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from httpx import AsyncClient
from playwright.async_api import Error as PlaywrightError

from avp_api.config import Settings
from avp_api.models.competitor import DetectionStatus
from avp_api.models.engine_result import EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import crawl, egress, technical_audit
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
    browser is given. `chains` maps a URL to the hops that led to it
    (`redirected_from`, nearest first). `subresources` maps a page to the URLs
    it asks for when it loads: each goes through the registered routes the way
    Playwright runs them — the most recently registered first, `fallback()`
    passing to the next, and a request nothing handled going to the network
    (`loaded`). Everything the egress guard installs is recorded.

    `listener_delays` maps a response's URL to how long its `server_addr()` takes
    (None: never answers); such a response reaches the `response` listeners as a
    background task, as Playwright's events do, instead of being awaited inline.
    A page dict may carry an `"evaluate"` dict that overrides what `evaluate`
    returns, so a page can have facts for the audit to read. `no_response` lists
    URLs whose navigation yields no response (`goto` returns None), and
    `close_error` makes closing the browser fail.
    """

    def __init__(
        self, pages: dict[str, dict[str, Any]] | None = None,
        landings: dict[str, str] | None = None,
        subresources: dict[str, list[str]] | None = None,
        iframes: dict[str, list[str]] | None = None,
        chains: dict[str, list[str]] | None = None,
        drifts: dict[str, str] | None = None,
        server_addrs: dict[str, Any] | None = None,
        listener_delays: dict[str, float | None] | None = None,
        no_response: set[str] | None = None,
        close_error: bool = False,
    ) -> None:
        self.pages = pages or {}
        self.landings = landings or {}
        self.subresources = subresources or {}
        self.iframes = iframes or {}
        self.chains = chains or {}
        # url -> the address the page is at once `evaluate` has run.
        self.drifts = drifts or {}
        # url -> what `response.server_addr()` says; a URL not listed says None.
        self.server_addrs = server_addrs or {}
        self.listener_delays = listener_delays or {}
        self.no_response = no_response or set()
        self.close_error = close_error
        self.listeners: list[tuple[str, Any]] = []
        self.init_scripts: list[str] = []
        self.launches = 0
        self.launch_args: list[list[str]] = []
        self.evaluations = 0
        self.cancelled: list[str] = []
        self.navigations: list[str] = []
        self.context_kwargs: list[dict[str, Any]] = []
        self.routes: list[tuple[Any, Any]] = []
        self.web_socket_routes: list[tuple[Any, Any]] = []
        self.aborted: list[tuple[str, str | None]] = []
        self.loaded: list[str] = []

    def __call__(self) -> _Playwright:
        return _Playwright(self)

    async def request(self, url: str, kind: str = "main") -> bool:
        """One request through the routes. True if it reached the network.

        `kind` is what Playwright's request would say: "main" is the main frame's
        navigation, "iframe" a sub-frame's navigation, "resource" anything else.
        """
        matching = [
            handler for pattern, handler in reversed(self.routes) if _matches(pattern, url)
        ]
        before = len(self.loaded)
        await _Route(self, url, matching, kind).fallback()
        return len(self.loaded) > before


    async def respond(self, url: str) -> None:
        """Tell the context's `response` listeners a response arrived."""
        addr = self.server_addrs.get(url)
        slow = url in self.listener_delays
        delay = self.listener_delays.get(url)

        async def server_addr() -> Any:
            if slow:
                try:
                    if delay is None:
                        await asyncio.Event().wait()  # never answers
                    else:
                        await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    self.cancelled.append(url)
                    raise
            return addr

        response = SimpleNamespace(url=url, server_addr=server_addr)
        for event, handler in self.listeners:
            if event == "response":
                result = handler(response)
                if slow and hasattr(result, "__await__"):
                    asyncio.ensure_future(result)  # a background task, as Playwright does
                elif hasattr(result, "__await__"):
                    await result


def _matches(pattern: Any, url: str) -> bool:
    return pattern == "**/*" if isinstance(pattern, str) else bool(pattern.search(url))


class _Route:
    def __init__(
        self, web: FakeChromium, url: str, handlers: list[Any], kind: str = "main"
    ) -> None:
        self.web = web
        main_frame = SimpleNamespace(parent_frame=None)
        frame = SimpleNamespace(parent_frame=main_frame) if kind == "iframe" else main_frame
        self.request = SimpleNamespace(
            url=url, frame=frame, is_navigation_request=lambda: kind in ("main", "iframe")
        )
        self._handlers = handlers

    async def abort(self, reason: str | None = None) -> None:
        self.web.aborted.append((self.request.url, reason))

    async def fallback(self) -> None:
        if not self._handlers:
            self.web.loaded.append(self.request.url)  # no handler left: the network
            return
        handler = self._handlers.pop(0)
        result = handler(self)
        if hasattr(result, "__await__"):
            await result


class _Request:
    def __init__(self, url: str, redirected_from: _Request | None = None) -> None:
        self.url = url
        self.redirected_from = redirected_from


class _Playwright:
    def __init__(self, web: FakeChromium) -> None:
        self.web = web
        self.chromium = self

    async def __aenter__(self) -> _Playwright:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def launch(self, **kwargs: Any) -> _Browser:
        self.web.launches += 1
        self.web.launch_args.append(list(kwargs.get("args") or []))
        return _Browser(self.web)


class _Browser:
    def __init__(self, web: FakeChromium) -> None:
        self.web = web

    async def new_context(self, **kwargs: Any) -> _Browser:
        self.web.context_kwargs.append(kwargs)
        return self

    async def route(self, pattern: Any, handler: Any, **kwargs: Any) -> None:  # noqa: ARG002
        self.web.routes.append((pattern, handler))

    async def route_web_socket(self, pattern: Any, handler: Any) -> None:
        self.web.web_socket_routes.append((pattern, handler))

    def on(self, event: str, handler: Any) -> None:
        self.web.listeners.append((event, handler))

    async def add_init_script(self, script: str) -> None:
        self.web.init_scripts.append(script)

    async def new_page(self) -> _Page:
        return _Page(self.web)

    async def close(self) -> None:
        if self.web.close_error:
            raise PlaywrightError("Target page, context or browser has been closed")


class _Page:
    def __init__(self, web: FakeChromium) -> None:
        self.web = web
        self.url = "about:blank"

    def set_default_timeout(self, ms: int) -> None:
        return None

    async def add_init_script(self, script: str) -> None:  # noqa: ARG002
        return None

    async def wait_for_timeout(self, ms: int) -> None:  # noqa: ARG002
        return None

    async def close(self) -> None:
        # The fetchers close the page they opened (M1); closing the browser, which
        # stands in for the context here too, is what `close_error` makes fail.
        return None

    async def goto(self, url: str, **kwargs: Any) -> SimpleNamespace:  # noqa: ARG002
        # As Chromium does: an address with no path is its root.
        if not urlsplit(url).path:
            url += "/"
        self.web.navigations.append(url)
        # The navigation itself goes through the routes; an aborted one fails.
        if not await self.web.request(url):
            raise PlaywrightError("net::ERR_BLOCKED_BY_CLIENT")
        self.url = self.web.landings.get(url, url)
        await self.web.respond(self.url)
        for resource in self.web.subresources.get(self.url, []):
            if await self.web.request(resource, "resource"):
                await self.web.respond(resource)
        for frame_url in self.web.iframes.get(self.url, []):
            if await self.web.request(frame_url, "iframe"):
                await self.web.respond(frame_url)
        request = _Request(self.url)
        for hop in self.web.chains.get(self.url, []):
            # nearest hop first: each is the one before the request so far
            tail = request
            while tail.redirected_from is not None:
                tail = tail.redirected_from
            tail.redirected_from = _Request(hop)
        if url in self.web.no_response:
            return None
        page = self.web.pages.get(self.url)
        return SimpleNamespace(
            status=page["status"] if page else 404, headers={}, request=request
        )

    async def evaluate(self, script: str) -> dict[str, Any]:  # noqa: ARG002
        self.web.evaluations += 1
        data = await self._evaluate()
        # Where the page is once evaluate has run; nothing from a page that is not public is kept.
        self.url = self.web.drifts.get(self.url, self.url)
        return data

    async def _evaluate(self) -> dict[str, Any]:
        page = self.web.pages.get(self.url, {})
        return {
            "text": page.get("text", "A business that does a thing, described at length."),
            "title": page.get("title", "A page"),
            "hasMetaDescription": True, "lang": "en", "h1": 1, "h2": 0,
            "jsonLd": "", "microdata": [], "links": page.get("links", []),
            **page.get("evaluate", {}),
        }


def page(*links: str) -> dict[str, Any]:
    return {"status": 200, "links": list(links)}


def rich_page(*links: str) -> dict[str, Any]:
    """A page with facts for the audit to read: schema, words, a title, vitals, a date."""
    return {
        "status": 200, "links": list(links),
        "evaluate": {
            "jsonLd": '{"@type": "Organization", "dateModified": "2026-01-01"}',
            "words": 640, "hasTitle": True, "canonical": True, "ogCount": 3,
            "vitals": {"lcp": 1800, "cls": 0.02},
        },
    }


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

        assert (await gate.resolve(f"{SITE}/")).refusal == "ROBOTS_DISALLOWED"
        # www's robots.txt was read; www's homepage was not.
        assert "https://www.prospect-site.com/" not in robots_web.fetched()

    async def test_an_allowed_redirect_is_followed_to_where_it_leads(self, robots_web: Any) -> None:
        robots_web.serve(f"{SITE}/", status=301, headers={"location": "https://www.prospect-site.com/"})
        robots_web.serve("https://www.prospect-site.com/", status=200)
        gate = crawl.RobotsGate(user_agent="ua")
        followed = await gate.resolve(f"{SITE}/")
        assert (followed.url, followed.refusal) == ("https://www.prospect-site.com/", None)
        # Asked with HEAD: headers, never a page.
        assert set(robots_web.fetched("GET")) == {
            f"{SITE}/robots.txt", "https://www.prospect-site.com/robots.txt"
        }

    async def test_a_robots_txt_that_times_out_stops_everything_as_unreachable(
        self, robots_web: Any
    ) -> None:
        robots_web.fail(f"{SITE}/robots.txt", httpx.ConnectTimeout("slow"))
        gate = crawl.RobotsGate(user_agent="ua")
        # Nothing is fetched (the policy forbids all, with no file to read), and the
        # site is not said to have forbidden it (launch checklist 3.41).
        assert (await gate.resolve(f"{SITE}/")).refusal == "ROBOTS_UNREACHABLE"
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
            assert result.error_code == "ROBOTS_UNREACHABLE"


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
        assert side.sitemap_refusal == "ROBOTS_DISALLOWED" and side.sitemap_present is False
        signals = technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=True, sitemap_refusal="ROBOTS_DISALLOWED"
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
        assert side.sitemap_present is True and side.sitemap_refusal is None


# Digests of RESULTS/COMPETITORS below, computed before ADDRESS_NOT_PUBLIC existed.
PINNED_DIGESTS: dict[str, str] = {
    "none": "1326be29c2fcf015b5053b7a7862caca93a05a252c3d100a88f8007d67953778",
    "not_yet_measured": "43b9782e5ec5d182842149f8290648a0bb124e748ccf62fb7855ffc35f02c7b7",
    "crawl_disallowed": "91c56f3607d7d3da41a69e5f9d802d709d52792c0a5246e34268900ef5788419",
}

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

    def test_an_address_that_is_not_public_scores_as_if_technical_foundation_were_excluded(
        self,
    ) -> None:
        refused = _score(
            technical_foundation=None, technical_foundation_exclusion="CRAWL_ADDRESS_NOT_PUBLIC"
        )
        not_measured = _score(technical_foundation=None)
        disallowed = _score(
            technical_foundation=None, technical_foundation_exclusion="CRAWL_DISALLOWED"
        )
        tf = Dimension.TECHNICAL_FOUNDATION.value

        assert refused.composite == not_measured.composite == disallowed.composite
        assert refused.excluded_dimensions[tf] == "CRAWL_ADDRESS_NOT_PUBLIC"
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in refused.degradation_flags
        assert {d: s.weight for d, s in refused.sub_scores.items() if s.included} == {
            d: s.weight for d, s in not_measured.sub_scores.items() if s.included
        }
        # No re-scoring of history: a new reason is not a formula change.
        assert FORMULA_VERSION == "v5"

    def test_digests_computed_before_the_new_reason_are_unchanged(self) -> None:
        # Pinned from the code as it stood before ADDRESS_NOT_PUBLIC existed.
        plain = compute_inputs_digest(RESULTS, COMPETITORS, None)
        assert plain == PINNED_DIGESTS["none"]
        assert compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion="NOT_YET_MEASURED"
        ) == PINNED_DIGESTS["not_yet_measured"]
        assert compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion="CRAWL_DISALLOWED"
        ) == PINNED_DIGESTS["crawl_disallowed"]
        # The new reason enters the digest only when it is set.
        assert compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion="CRAWL_ADDRESS_NOT_PUBLIC"
        ) not in PINNED_DIGESTS.values()

    def test_the_report_has_words_for_it(self) -> None:
        from avp_api.services.report_narrative import EXCLUSION_DETAIL, EXCLUSION_REASON

        assert EXCLUSION_REASON["CRAWL_ADDRESS_NOT_PUBLIC"] == "Address not public"
        assert EXCLUSION_DETAIL["CRAWL_ADDRESS_NOT_PUBLIC"] == (
            "The site’s address, or an address the site sent the crawler to, is on a private or "
            "reserved network, so the crawler did not read the site. Technical Foundation is "
            "left out of the score."
        )

    async def test_a_refused_audit_excludes_technical_foundation_under_its_reason(self) -> None:
        from avp_api.models.technical_audit import AuditStatus
        from avp_api.services import scoring_runner

        audit = SimpleNamespace(
            status=AuditStatus.FAILED, error_code="ADDRESS_NOT_PUBLIC", technical_foundation=None
        )

        class _Result:
            def scalar_one_or_none(self) -> Any:
                return audit

        class _Session:
            async def execute(self, *_a: Any, **_k: Any) -> _Result:
                return _Result()

        assert await scoring_runner.load_technical_foundation(_Session(), "scan_x") == (
            None, "CRAWL_ADDRESS_NOT_PUBLIC"
        )
        audit.error_code = "BROWSER_ERROR"  # any other failure: AUDIT_FAILED (2026-10-05)
        assert await scoring_runner.load_technical_foundation(_Session(), "scan_x") == (
            None, "AUDIT_FAILED"
        )

    def test_a_measured_value_ignores_an_exclusion_reason(self) -> None:
        measured = _score(technical_foundation=Decimal("80"))
        assert _score(
            technical_foundation=Decimal("80"), technical_foundation_exclusion="CRAWL_DISALLOWED"
        ).inputs_digest == measured.inputs_digest

    def test_an_unknown_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown Technical Foundation exclusion"):
            _score(technical_foundation=None, technical_foundation_exclusion="NOPE")


# ---------------------------------------------------------------------------
# the egress guard (2026-10-01): a site whose address is not a public one
# ---------------------------------------------------------------------------

INTERNAL = "https://internal.test"
METADATA = "http://169.254.169.254/latest/meta-data/"


TYPED = "https://prospect-site.com"  # what `crawl_site("prospect-site.com")` starts from


def _refused(result: Any) -> bool:
    """True if the crawl ended refused — after checking that it kept nothing it read.

    A refusal can arrive after a page's facts were read; the result must be a
    fresh one, as if nothing had been (`crawl._address_not_public`).
    """
    assert result.signals == crawl.CrawlSignals(
        final_url=TYPED, registrable_domain="prospect-site.com"
    )
    assert result.text_extract == "" and result.titles == []
    return (result.ok, result.error_code) == (False, "ADDRESS_NOT_PUBLIC")


class TestACrawlThatIsRefused:
    async def test_a_homepage_redirecting_into_a_private_name_is_never_requested(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(SITE, status=301, headers={"location": f"{INTERNAL}/"})
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(pages={f"{SITE}/": page(), f"{INTERNAL}/": page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert _refused(result)
        assert chromium.launches == 0 and chromium.navigations == []
        # Not even its robots.txt: the private host was never asked anything.
        assert all(url.startswith(SITE) for url in robots_web.fetched())
        assert result.signals.pages_fetched == 0 and result.text_extract == ""

    async def test_a_homepage_redirecting_to_the_metadata_address_is_never_requested(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(SITE, status=302, headers={"location": METADATA})
        chromium = FakeChromium()
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert _refused(result)
        assert not [u for u in robots_web.fetched() if "169.254" in u]
        assert chromium.launches == 0

    async def test_a_start_name_that_is_private_is_refused_before_any_request(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("prospect-site.com", "192.168.1.10")
        chromium = FakeChromium()
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert _refused(result)
        assert robots_web.requests == [] and chromium.launches == 0

    async def test_a_robots_txt_that_redirects_into_a_private_address_is_refused(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", status=301, headers={"location": METADATA})
        monkeypatch.setattr(crawl, "async_playwright", FakeChromium())

        result = await crawl.crawl_site("prospect-site.com")

        assert _refused(result)
        assert robots_web.fetched() == [f"{SITE}/robots.txt"]

    async def test_robots_txt_redirects_are_still_followed_by_hand_when_public(
        self, robots_web: Any
    ) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt", status=301,
            headers={"location": "https://www.prospect-site.com/robots.txt"},
        )
        robots_web.serve(
            "https://www.prospect-site.com/robots.txt", body="User-agent: *\nDisallow: /x\n"
        )
        robots = await crawl.RobotsGate(user_agent="ua").file(SITE)
        assert (robots.status, robots.body) == (200, "User-agent: *\nDisallow: /x\n")

    async def test_endless_robots_txt_redirects_leave_no_answer(self, robots_web: Any) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt", status=301, headers={"location": f"{SITE}/robots.txt"}
        )
        assert (await crawl.RobotsGate(user_agent="ua").file(SITE)).status is None
        assert len(robots_web.fetched()) == crawl.MAX_REDIRECTS + 1

    async def test_a_name_that_will_not_resolve_is_no_answer_not_a_refusal(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.dns_fails("prospect-site.com")
        monkeypatch.setattr(crawl, "async_playwright", FakeChromium())

        result = await crawl.crawl_site("prospect-site.com")

        # No robots.txt answer: nothing is fetched, as before, and it is not a refusal
        # by the site (launch checklist 3.41).
        assert result.error_code == "ROBOTS_UNREACHABLE"
        assert robots_web.requests == []

    async def test_a_subresource_to_a_private_address_is_aborted_and_the_crawl_continues(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": page()},
            subresources={
                f"{SITE}/": [f"{INTERNAL}/api.json?token=abc", f"{INTERNAL}/b.js", f"{SITE}/app.js"]
            },
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        with capture_logs() as logs:
            result = await crawl.crawl_site("prospect-site.com")

        assert result.ok and result.error_code is None
        assert result.signals.pages_fetched == 1 and result.signals.word_count > 0
        assert (f"{INTERNAL}/api.json?token=abc", "blockedbyclient") in chromium.aborted
        assert f"{INTERNAL}/api.json?token=abc" not in chromium.loaded
        assert f"{INTERNAL}/b.js" not in chromium.loaded
        assert f"{SITE}/app.js" in chromium.loaded  # the rest of the page still loads
        # Logged once per host, with the host and nothing else: no URL, no query.
        events = [e for e in logs if e["event"] == "egress.request_refused"]
        assert len(events) == 1
        assert set(events[0]) == {"event", "log_level", "host"}
        assert events[0]["host"] == "internal.test" and events[0]["log_level"] == "info"
        assert "token=abc" not in str(logs) and "/api.json" not in str(logs)

    async def test_an_iframe_to_a_private_address_is_aborted_and_the_crawl_continues(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": page()}, iframes={f"{SITE}/": [f"{INTERNAL}/frame"]}
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert result.ok and result.signals.pages_fetched == 1
        assert (f"{INTERNAL}/frame", "blockedbyclient") in chromium.aborted
        assert f"{INTERNAL}/frame" not in chromium.loaded

    async def test_a_public_subresource_continues_and_images_are_still_blocked(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chromium = FakeChromium(
            pages={f"{SITE}/": page()},
            subresources={f"{SITE}/": [f"{SITE}/app.js", f"{SITE}/hero.png", f"{SITE}/f.woff2"]},
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert result.ok
        assert f"{SITE}/app.js" in chromium.loaded
        assert f"{SITE}/hero.png" not in chromium.loaded
        assert {u for u, _ in chromium.aborted} == {f"{SITE}/hero.png", f"{SITE}/f.woff2"}

    async def test_a_subresource_that_will_not_resolve_is_aborted_but_not_a_refusal(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.dns_fails("cdn.gone.test")
        chromium = FakeChromium(
            pages={f"{SITE}/": page()}, subresources={f"{SITE}/": ["https://cdn.gone.test/a.js"]}
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert result.ok
        assert ("https://cdn.gone.test/a.js", "namenotresolved") in chromium.aborted

    async def test_a_navigation_whose_redirect_chain_passed_a_private_host_is_refused(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": page()}, chains={f"{SITE}/": [f"{INTERNAL}/hop"]}
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert _refused(result)
        assert result.text_extract == "" and result.signals.pages_fetched == 0

    async def test_a_page_the_browser_lands_on_at_a_private_address_is_refused(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{INTERNAL}/": page()}, landings={f"{SITE}/": f"{INTERNAL}/"}
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        assert _refused(await crawl.crawl_site("prospect-site.com"))

    async def test_the_browser_blocks_service_workers_and_websockets_and_is_routed(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        await crawl.crawl_site("prospect-site.com")

        assert chromium.context_kwargs[0]["service_workers"] == "block"
        assert "**/*" in [pattern for pattern, _ in chromium.routes]
        # The guard registered last, so it runs first (Playwright's order).
        assert chromium.routes[-1][0] == "**/*"
        await _a_websocket_is_closed_never_connected(chromium)

    async def test_the_refusal_is_logged_with_the_host_and_no_page_text(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        robots_web.resolve_to("prospect-site.com", "10.0.0.5")
        monkeypatch.setattr(crawl, "async_playwright", FakeChromium())
        with capture_logs() as logs:
            await crawl.crawl_site("prospect-site.com")
        entry = next(e for e in logs if e["event"] == "crawl.address_not_public")
        assert set(entry) == {"event", "log_level", "url", "host"}
        assert entry["host"] == "prospect-site.com"


async def _a_websocket_is_closed_never_connected(chromium: FakeChromium) -> None:
    assert len(chromium.web_socket_routes) == 1
    _pattern, handler = chromium.web_socket_routes[0]
    closed: list[bool] = []

    class _Socket:
        async def close(self, **kwargs: Any) -> None:  # noqa: ARG002
            closed.append(True)

        async def connect_to_server(self) -> None:
            raise AssertionError("a WebSocket was connected to the server")

    await handler(_Socket())
    assert closed == [True]


class TestAnAuditThatIsRefused:
    async def _audit(self, monkeypatch: pytest.MonkeyPatch, chromium: FakeChromium) -> Any:
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)
        return await technical_audit.audit_site(SITE)

    @staticmethod
    def _assert_nothing_read(signals: Any) -> None:
        assert (signals.ok, signals.error_code) == (False, "ADDRESS_NOT_PUBLIC")
        # Fresh signals: nothing read before the refusal is kept — not the page's
        # facts, not what robots.txt said, not the AI crawler table.
        assert signals == technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=False, error_code="ADDRESS_NOT_PUBLIC"
        )
        assert signals.schema_types == [] and signals.ai_crawler_access == []
        assert (signals.word_count, signals.h1_count, signals.open_graph_tag_count) == (0, 0, 0)
        assert not (signals.has_title or signals.has_meta_description or signals.canonical_present)
        assert (signals.lcp_ms, signals.cls, signals.inp_ms) == (None, None, None)
        assert (signals.content_age_days, signals.http_status) == (None, None)
        checks = technical_audit.build_checks(signals)
        assert {c.key for c in checks} >= {"indexable", "sitemap_present", "meta_title"}
        assert all(
            (c.status, c.detail_code) == ("not_applicable", "ADDRESS_NOT_PUBLIC") for c in checks
        )
        outcome = technical_audit.score_audit(signals)
        assert (outcome.scored, outcome.score) == (False, None)

    async def test_a_start_name_that_is_private(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("prospect-site.com", "10.1.2.3")
        chromium = FakeChromium()
        signals = await self._audit(monkeypatch, chromium)
        self._assert_nothing_read(signals)
        assert robots_web.requests == [] and chromium.launches == 0

    async def test_a_homepage_redirect_into_a_private_name_is_never_requested(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(SITE, status=301, headers={"location": f"{INTERNAL}/"})
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium()
        signals = await self._audit(monkeypatch, chromium)
        self._assert_nothing_read(signals)
        assert all(url.startswith(SITE) for url in robots_web.fetched())
        assert chromium.launches == 0

    async def test_a_homepage_redirect_to_the_metadata_address(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(SITE, status=302, headers={"location": METADATA})
        signals = await self._audit(monkeypatch, FakeChromium())
        self._assert_nothing_read(signals)
        assert not [u for u in robots_web.fetched() if "169.254" in u]

    async def test_a_robots_txt_redirect_is_not_swallowed_by_the_side_file_handler(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", status=301, headers={"location": METADATA})
        chromium = FakeChromium()
        signals = await self._audit(monkeypatch, chromium)
        self._assert_nothing_read(signals)
        assert chromium.launches == 0

    async def test_a_sitemap_redirect_into_a_private_address_is_skipped_and_the_audit_continues(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        robots_web.resolve_to("internal.test", "10.0.0.5")
        robots_web.serve(
            f"{SITE}/sitemap.xml", status=301, headers={"location": f"{INTERNAL}/s.xml?k=1"}
        )
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})
        with capture_logs() as logs:
            signals = await self._audit(monkeypatch, chromium)
        # Treated as a sitemap that got no answer: not found, not disallowed, audit kept.
        assert signals.ok and signals.error_code is None
        assert signals.has_sitemap is False and signals.sitemap_refusal is None
        assert signals.schema_types == ["Organization"] and signals.word_count == 640
        assert not [u for u in robots_web.fetched() if "internal.test" in u]
        events = [e for e in logs if e["event"] == "egress.request_refused"]
        assert [(e["host"], set(e)) for e in events] == [
            ("internal.test", {"event", "log_level", "host"})
        ]
        assert "k=1" not in str(logs)

    async def test_a_private_subresource_is_aborted_and_the_audit_continues(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": rich_page()},
            subresources={f"{SITE}/": [f"{INTERNAL}/x.js?k=1", f"{SITE}/app.js"]},
        )
        with capture_logs() as logs:
            signals = await self._audit(monkeypatch, chromium)
        assert signals.ok and signals.error_code is None
        assert signals.schema_types == ["Organization"] and signals.word_count == 640
        assert (f"{INTERNAL}/x.js?k=1", "blockedbyclient") in chromium.aborted
        assert f"{INTERNAL}/x.js?k=1" not in chromium.loaded
        events = [e for e in logs if e["event"] == "egress.request_refused"]
        assert [(e["host"], set(e)) for e in events] == [
            ("internal.test", {"event", "log_level", "host"})
        ]
        assert "k=1" not in str(logs)

    async def test_an_iframe_to_a_private_address_is_aborted_and_the_audit_continues(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": rich_page()}, iframes={f"{SITE}/": [f"{INTERNAL}/frame"]}
        )
        signals = await self._audit(monkeypatch, chromium)
        assert signals.ok and signals.word_count == 640
        assert (f"{INTERNAL}/frame", "blockedbyclient") in chromium.aborted

    async def test_a_public_subresource_and_an_image_still_load_in_the_audit(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chromium = FakeChromium(
            pages={f"{SITE}/": page()},
            subresources={f"{SITE}/": [f"{SITE}/app.js", f"{SITE}/hero.png"]},
        )
        signals = await self._audit(monkeypatch, chromium)
        assert signals.ok
        # No resource blocking in the audit: LCP needs the images.
        assert chromium.aborted == []
        assert {f"{SITE}/app.js", f"{SITE}/hero.png"} <= set(chromium.loaded)

    async def test_a_redirect_chain_through_a_private_host(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": page()}, chains={f"{SITE}/": [f"{INTERNAL}/hop"]}
        )
        self._assert_nothing_read(await self._audit(monkeypatch, chromium))

    async def test_the_audit_browser_is_guarded_like_the_crawl(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        await self._audit(monkeypatch, chromium)
        assert chromium.context_kwargs[0]["service_workers"] == "block"
        assert chromium.routes[-1][0] == "**/*"
        await _a_websocket_is_closed_never_connected(chromium)

    async def test_the_refusal_is_logged_with_the_domain_only(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        robots_web.resolve_to("prospect-site.com", "10.1.2.3")
        with capture_logs() as logs:
            await self._audit(monkeypatch, FakeChromium())
        entry = next(e for e in logs if e["event"] == "audit.address_not_public")
        assert set(entry) == {"event", "log_level", "domain"}


class TestAPageThatEndsAtANonPublicAddress:
    """A page at an address that is not public once its facts are read: nothing is kept."""

    async def test_the_crawl_keeps_nothing_from_it(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": page(), f"{INTERNAL}/": page()},
            drifts={f"{SITE}/": f"{INTERNAL}/"},
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert _refused(result)
        assert result.text_extract == "" and result.signals.pages_fetched == 0

    async def test_the_audit_keeps_nothing_from_it(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = FakeChromium(
            pages={f"{SITE}/": page(), f"{INTERNAL}/": page()},
            drifts={f"{SITE}/": f"{INTERNAL}/"},
        )
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        signals = await technical_audit.audit_site(SITE)

        TestAnAuditThatIsRefused._assert_nothing_read(signals)  # noqa: SLF001
        assert signals.h1_count == 0 and signals.word_count == 0 and not signals.has_title


class TestTheAddressChromiumConnectedTo:
    def _fake(self, addr: Any, **kwargs: Any) -> FakeChromium:
        return FakeChromium(
            pages={f"{SITE}/": page()}, server_addrs={f"{SITE}/": addr}, **kwargs
        )

    @pytest.mark.parametrize(
        "addr",
        [{"ipAddress": "10.0.0.5", "port": 443}, {"ipAddress": "[::1]", "port": 443},
         {"ipAddress": "[fe80::1%eth0]", "port": 443}],
    )
    async def test_a_non_public_address_ends_the_crawl_and_the_audit(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, addr: dict[str, Any]
    ) -> None:
        monkeypatch.setattr(crawl, "async_playwright", self._fake(addr))
        result = await crawl.crawl_site("prospect-site.com")
        assert _refused(result) and result.text_extract == ""

        monkeypatch.setattr(technical_audit, "async_playwright", self._fake(addr))
        TestAnAuditThatIsRefused._assert_nothing_read(  # noqa: SLF001
            await technical_audit.audit_site(SITE)
        )

    @pytest.mark.parametrize(
        "addr",
        [{"ipAddress": "93.184.216.34", "port": 443},
         {"ipAddress": "[2606:4700:10::ac42:93f3]", "port": 443},
         None, {}, {"ipAddress": None}, {"ipAddress": "not an address"},
         {"ipAddress": 12}, "10.0.0.5"],
    )
    async def test_a_public_missing_or_unreadable_address_is_ignored(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, addr: Any
    ) -> None:
        monkeypatch.setattr(crawl, "async_playwright", self._fake(addr))
        assert (await crawl.crawl_site("prospect-site.com")).ok

        monkeypatch.setattr(technical_audit, "async_playwright", self._fake(addr))
        assert (await technical_audit.audit_site(SITE)).ok

    async def test_a_subresource_connected_to_a_private_address_is_caught(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A connection to a non-public address ends the run, and nothing is kept.
        chromium = FakeChromium(
            pages={f"{SITE}/": page()},
            subresources={f"{SITE}/": [f"{SITE}/app.js"]},
            server_addrs={f"{SITE}/app.js": {"ipAddress": "127.0.0.1", "port": 443}},
        )
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)
        TestAnAuditThatIsRefused._assert_nothing_read(  # noqa: SLF001
            await technical_audit.audit_site(SITE)
        )

    async def test_a_listener_still_reading_when_the_page_is_done_is_waited_for(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        late = FakeChromium(pages={f"{SITE}/": page()})

        async def slow_respond(url: str) -> None:
            async def server_addr() -> Any:
                await asyncio.sleep(0.05)
                return {"ipAddress": "10.0.0.5", "port": 443}

            response = SimpleNamespace(url=url, server_addr=server_addr)
            for event, handler in late.listeners:
                if event == "response":
                    asyncio.ensure_future(handler(response))  # as Playwright does

        late.respond = slow_respond  # type: ignore[method-assign]
        monkeypatch.setattr(technical_audit, "async_playwright", late)
        TestAnAuditThatIsRefused._assert_nothing_read(  # noqa: SLF001
            await technical_audit.audit_site(SITE)
        )


class TestWebRtcMeasures:
    async def test_both_browsers_register_the_init_script(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for module, run in (
            (crawl, lambda: crawl.crawl_site("prospect-site.com")),
            (technical_audit, lambda: technical_audit.audit_site(SITE)),
        ):
            chromium = FakeChromium(pages={f"{SITE}/": page()})
            monkeypatch.setattr(module, "async_playwright", chromium)
            await run()
            joined = " ".join(chromium.init_scripts)
            assert "window.RTCPeerConnection = undefined" in joined
            assert "window.webkitRTCPeerConnection = undefined" in joined

    async def test_both_launches_pass_the_chromium_webrtc_policy(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flag = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
        for module, run in (
            (crawl, lambda: crawl.crawl_site("prospect-site.com")),
            (technical_audit, lambda: technical_audit.audit_site(SITE)),
        ):
            chromium = FakeChromium(pages={f"{SITE}/": page()})
            monkeypatch.setattr(module, "async_playwright", chromium)
            await run()
            (args,) = chromium.launch_args
            assert flag in args
            # What was already passed is still passed.
            assert {"--no-sandbox", "--disable-dev-shm-usage"} <= set(args)


class TestAMalformedRedirectIsNoAnswer:
    async def test_a_robots_txt_redirect_with_a_bad_port(self, robots_web: Any) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt", status=301,
            headers={"location": "https://prospect-site.com:notaport/robots.txt"},
        )
        assert (await crawl.RobotsGate(user_agent="ua").file(SITE)).status is None

    async def test_a_robots_txt_redirect_that_is_not_a_url(self, robots_web: Any) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt", status=301, headers={"location": "http://[::1/robots.txt"}
        )
        assert (await crawl.RobotsGate(user_agent="ua").file(SITE)).status is None

    async def test_a_page_redirect_with_a_bad_port(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(
            SITE, status=301, headers={"location": "https://prospect-site.com:notaport/x"}
        )
        gate = crawl.RobotsGate(user_agent="ua")

        async def allows(url: str) -> bool:  # robots.txt is not what is under test
            return True

        monkeypatch.setattr(gate, "allows", allows)
        followed = await gate.follow("HEAD", SITE)
        assert followed.response is None and followed.refusal is None

    async def test_a_page_redirect_that_is_not_a_url(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(SITE, status=301, headers={"location": "http://[::1/x"})
        gate = crawl.RobotsGate(user_agent="ua")

        async def allows(url: str) -> bool:
            return True

        monkeypatch.setattr(gate, "allows", allows)
        followed = await gate.follow("HEAD", SITE)
        assert followed.response is None


@pytest.fixture
def quick_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Intermediate settles give up at once; the final one waits for a slow listener."""
    monkeypatch.setattr(egress, "SETTLE_TIMEOUT_S", 0.001)
    monkeypatch.setattr(egress, "FINAL_SETTLE_TIMEOUT_S", 5.0)


PRIVATE_ADDR = {"ipAddress": "10.0.0.5", "port": 443}


def _site_with_about(**extra: Any) -> FakeChromium:
    """A homepage with facts, linking to /about."""
    return FakeChromium(
        pages={f"{SITE}/": page("/about"), f"{SITE}/about": page()}, **extra
    )


class TestARefusalKeepsNothingAlreadyRead:
    """A refusal can arrive after a page's facts were read. None of them is kept.

    Each test first shows, on the same arrangement without the refusal, that
    there were facts to keep, so "nothing kept" is not true of an empty page.
    """

    @pytest.mark.parametrize("how", ["connected_address", "redirect_chain"])
    async def test_a_secondary_page_refused_after_the_home_page_was_read(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, how: str
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        monkeypatch.setattr(crawl, "async_playwright", _site_with_about())
        clean = await crawl.crawl_site("prospect-site.com")
        # The control: the home page and /about were read, and left facts.
        assert clean.ok and clean.signals.pages_fetched == 2
        assert clean.signals.word_count > 0 and clean.signals.has_title and clean.titles

        extra: dict[str, Any] = {
            "connected_address": {"server_addrs": {f"{SITE}/about": PRIVATE_ADDR}},
            "redirect_chain": {"chains": {f"{SITE}/about": [f"{INTERNAL}/hop"]}},
        }[how]
        chromium = _site_with_about(**extra)
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert chromium.navigations == [f"{SITE}/", f"{SITE}/about"]  # home was read first
        assert _refused(result)
        assert result.signals.urls_fetched == [] and result.signals.pages_fetched == 0

    async def test_a_main_frame_navigation_refused_after_the_home_page_was_read(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The second navigation's host is refused when the route handler asks (as if
        # it now resolved to a private address): the main frame's own navigation, so
        # the run ends, and the home page's facts are not kept.
        routed: list[str] = []

        class Flipping(crawl.BrowserGuard):
            async def handle(self, route: Any) -> None:
                routed.append(route.request.url)
                self.flip = len(routed) >= 2
                await super().handle(route)

            async def _verdict(self, host: str, port: int) -> None:
                if getattr(self, "flip", False):
                    raise crawl.EgressRefused(host, "10.0.0.5")
                await super()._verdict(host, port)

        monkeypatch.setattr(crawl, "BrowserGuard", Flipping)
        chromium = _site_with_about()
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert chromium.navigations == [f"{SITE}/", f"{SITE}/about"]  # home was read first
        assert (f"{SITE}/about", "blockedbyclient") in chromium.aborted
        assert _refused(result)
        assert result.signals.urls_fetched == [] and result.signals.pages_fetched == 0

    async def test_a_crawl_refused_at_the_final_settle(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, quick_settle: None
    ) -> None:
        chromium = _site_with_about(
            server_addrs={f"{SITE}/about": PRIVATE_ADDR}, listener_delays={f"{SITE}/about": 0.2}
        )
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        # Both pages were read before the listener answered; none of it is kept.
        assert chromium.navigations == [f"{SITE}/", f"{SITE}/about"]
        assert chromium.evaluations == 2
        assert _refused(result)

    async def test_an_audit_refused_at_the_final_settle(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, quick_settle: None
    ) -> None:
        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: GPTBot\nDisallow: /\n")
        monkeypatch.setattr(
            technical_audit, "async_playwright", FakeChromium(pages={f"{SITE}/": rich_page()})
        )
        clean = await technical_audit.audit_site(SITE)
        # The control: facts of every kind the audit stores.
        assert clean.ok and clean.schema_types == ["Organization"]
        assert (clean.word_count, clean.h1_count, clean.lcp_ms) == (640, 1, 1800)
        assert clean.has_title and clean.cls is not None and clean.content_age_days is not None
        assert any(a.verdict is AccessVerdict.BLOCKED for a in clean.ai_crawler_access)

        chromium = FakeChromium(
            pages={f"{SITE}/": rich_page()},
            server_addrs={f"{SITE}/": PRIVATE_ADDR}, listener_delays={f"{SITE}/": 0.2},
        )
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        signals = await technical_audit.audit_site(SITE)

        assert chromium.evaluations == 1  # the page was read first
        TestAnAuditThatIsRefused._assert_nothing_read(signals)  # noqa: SLF001

    async def test_the_stored_audit_row_holds_nothing_read(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, quick_settle: None
    ) -> None:
        from avp_api.models.technical_audit import AuditStatus, TechnicalAudit
        from avp_api.services import audit_runner

        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: GPTBot\nDisallow: /\n")
        chromium = FakeChromium(
            pages={f"{SITE}/": rich_page()},
            server_addrs={f"{SITE}/": PRIVATE_ADDR}, listener_delays={f"{SITE}/": 0.2},
        )
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)
        signals = await technical_audit.audit_site(SITE)

        row = TechnicalAudit()
        audit_runner._apply(row, signals, technical_audit.score_audit(signals))  # noqa: SLF001

        assert (row.status, row.error_code, row.pages_crawled) == (
            AuditStatus.FAILED, "ADDRESS_NOT_PUBLIC", 0
        )
        assert row.schema_types == [] and (row.word_count, row.h1_count) == (0, 0)
        assert (row.lcp_ms, row.cls, row.content_age_days) == (None, None, None)
        assert row.technical_foundation is None
        assert not (row.has_organization_schema or row.canonical_present or row.has_sitemap)


class TestAListenerThatNeverAnswers:
    """A connected address that was never read is not known to be public: fail closed.

    Not as `ADDRESS_NOT_PUBLIC` — nothing says the address is not public — but as
    the plain browser failure, with nothing kept.
    """

    @pytest.fixture(autouse=True)
    def impatient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(egress, "SETTLE_TIMEOUT_S", 0.001)
        monkeypatch.setattr(egress, "FINAL_SETTLE_TIMEOUT_S", 0.05)

    async def test_the_crawl_ends_as_a_browser_error_and_keeps_nothing(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        chromium = _site_with_about(listener_delays={f"{SITE}/about": None})
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        with capture_logs() as logs:
            result = await crawl.crawl_site("prospect-site.com")

        assert (result.ok, result.error_code) == (False, "BROWSER_ERROR")
        assert result.signals == crawl.CrawlSignals(
            final_url=TYPED, registrable_domain="prospect-site.com"
        )
        assert result.text_extract == "" and result.titles == []
        assert chromium.evaluations == 2  # both pages were read, and nothing is kept
        assert chromium.cancelled == [f"{SITE}/about"]  # the leftover listener was cancelled
        entry = next(e for e in logs if e["event"] == "crawl.egress_unverified")
        assert set(entry) == {"event", "log_level", "url"}
        assert not [e for e in logs if e["event"] == "crawl.address_not_public"]

    async def test_the_audit_fails_as_a_browser_error_and_keeps_nothing(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        robots_web.serve(f"{SITE}/robots.txt", body="User-agent: GPTBot\nDisallow: /\n")
        chromium = FakeChromium(
            pages={f"{SITE}/": rich_page()}, listener_delays={f"{SITE}/": None}
        )
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        with capture_logs() as logs:
            signals = await technical_audit.audit_site(SITE)

        assert signals == technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=False, error_code="BROWSER_ERROR"
        )
        assert chromium.evaluations == 1 and chromium.cancelled == [f"{SITE}/"]
        # An unknown is not a finding about the site: the plain failure check.
        (check,) = technical_audit.build_checks(signals)
        assert (check.key, check.status, check.detail_code) == (
            "site_reachable", "error", "BROWSER_ERROR"
        )
        entry = next(e for e in logs if e["event"] == "audit.egress_unverified")
        assert set(entry) == {"event", "log_level", "domain"}
        assert not [e for e in logs if e["event"] == "audit.address_not_public"]

    async def test_a_listener_that_answers_in_time_is_not_a_failure(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chromium = FakeChromium(
            pages={f"{SITE}/": rich_page()},
            server_addrs={f"{SITE}/": {"ipAddress": "93.184.216.34", "port": 443}},
            listener_delays={f"{SITE}/": 0.01},
        )
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)
        assert (await technical_audit.audit_site(SITE)).ok


def _assert_fresh(kind: str, result: Any, code: str) -> None:
    """The run ended as `code` and kept nothing it had read: a crawl result or audit signals."""
    if kind == "crawl":
        assert (result.ok, result.error_code) == (False, code)
        assert result.signals == crawl.CrawlSignals(
            final_url=TYPED, registrable_domain="prospect-site.com"
        )
        assert result.text_extract == "" and result.titles == []
    else:
        assert result == technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=False, error_code=code
        )


async def _run(
    kind: str, monkeypatch: pytest.MonkeyPatch, chromium: FakeChromium
) -> Any:
    if kind == "crawl":
        monkeypatch.setattr(crawl, "async_playwright", chromium)
        return await crawl.crawl_site("prospect-site.com")
    monkeypatch.setattr(technical_audit, "async_playwright", chromium)
    return await technical_audit.audit_site(SITE)


def _a_site_with_facts(kind: str, **extra: Any) -> FakeChromium:
    return FakeChromium(
        pages={f"{SITE}/": rich_page() if kind == "audit" else page()}, **extra
    )


def _impatient(monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    monkeypatch.setattr(egress, "SETTLE_TIMEOUT_S", 0.001)
    monkeypatch.setattr(
        egress, "FINAL_SETTLE_TIMEOUT_S", 0.05 if how == "never_answers" else 5.0
    )


def _listener_trouble(how: str, url: str) -> dict[str, Any]:
    """`FakeChromium` arguments: the response at `url` is read late, as private, or never."""
    if how == "never_answers":
        return {"listener_delays": {url: None}}
    return {"server_addrs": {url: PRIVATE_ADDR}, "listener_delays": {url: 0.1}}


EXPECTED_AFTER = {"late_refusal": "ADDRESS_NOT_PUBLIC", "never_answers": "BROWSER_ERROR"}


class TestAFailingCloseDoesNotMaskTheEgressOutcome:
    """A browser that fails to close, while an egress outcome is on its way out, replaces it.

    The outcome stands, and nothing that was read is kept.
    """

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    @pytest.mark.parametrize("how", ["late_refusal", "never_answers"])
    async def test_a_listener_outcome_stands(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str, how: str
    ) -> None:
        _impatient(monkeypatch, how)
        chromium = _a_site_with_facts(
            kind, close_error=True, **_listener_trouble(how, f"{SITE}/")
        )

        result = await _run(kind, monkeypatch, chromium)

        assert chromium.evaluations == 1  # the page was read, and then it was not kept
        _assert_fresh(kind, result, EXPECTED_AFTER[how])

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_refusal_the_gate_raised_stands(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        real = crawl.RobotsGate.allows

        async def allows(self: Any, url: str) -> bool:
            if url == f"{SITE}/":  # where the browser landed: the gate's own verdict
                raise egress.EgressRefused("prospect-site.com", "10.0.0.5")
            return await real(self, url)

        monkeypatch.setattr(crawl.RobotsGate, "allows", allows)
        chromium = _a_site_with_facts(kind, close_error=True)

        result = await _run(kind, monkeypatch, chromium)

        _assert_fresh(kind, result, "ADDRESS_NOT_PUBLIC")

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_close_that_fails_with_nothing_wrong_is_still_a_browser_error(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        result = await _run(kind, monkeypatch, _a_site_with_facts(kind, close_error=True))
        assert result.error_code == "BROWSER_ERROR"  # as before: nothing egress changed here


class TestEveryWayOutOfTheBrowserIsChecked:
    """No result leaves a browser run without the final check — the early returns included.

    Each test runs the arrangement twice: once plain, to show which return it
    reaches and what it keeps, and once with a response whose address is read late
    as private, or never.
    """

    # path -> (the plain outcome's error code, the URL the response listener sees)
    PATHS: dict[str, tuple[str, str]] = {
        "http_error": ("HTTP_404", f"{SITE}/"),
        "no_page": ("FETCH_FAILED", f"{SITE}/"),
        "robots_landing": ("ROBOTS_DISALLOWED", "https://elsewhere-host.com/"),
    }

    @staticmethod
    def _arrangement(kind: str, path: str, robots_web: Any, **extra: Any) -> FakeChromium:
        if path == "http_error":
            return FakeChromium(pages={f"{SITE}/": {"status": 404, "links": []}}, **extra)
        if path == "no_page":
            return FakeChromium(pages={f"{SITE}/": page()}, no_response={f"{SITE}/"}, **extra)
        robots_web.serve(
            "https://elsewhere-host.com/robots.txt", body="User-agent: *\nDisallow: /\n"
        )
        return FakeChromium(
            pages={"https://elsewhere-host.com/": page()},
            landings={f"{SITE}/": "https://elsewhere-host.com/"}, **extra,
        )

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    @pytest.mark.parametrize("path", list(PATHS))
    @pytest.mark.parametrize("how", ["late_refusal", "never_answers"])
    async def test_a_response_read_late_or_never_ends_the_run(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str, path: str, how: str
    ) -> None:
        plain_code, listener_url = self.PATHS[path]

        plain = await _run(kind, monkeypatch, self._arrangement(kind, path, robots_web))
        # The control: this is the return the arrangement reaches when nothing is wrong.
        assert plain.error_code == plain_code
        if path == "http_error":
            # ... and it kept the browser's status, which is what the final check guards.
            assert (plain.signals if kind == "crawl" else plain).http_status == 404

        _impatient(monkeypatch, how)
        chromium = self._arrangement(
            kind, path, robots_web, **_listener_trouble(how, listener_url)
        )
        result = await _run(kind, monkeypatch, chromium)

        _assert_fresh(kind, result, EXPECTED_AFTER[how])


class TestTheClientIsStillCreated:
    async def test_a_refused_site_is_unclassifiable_with_the_reason(
        self, client: AsyncClient, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("prospect-site.com", "10.0.0.5")
        chromium = FakeChromium()
        monkeypatch.setattr(crawl, "async_playwright", chromium)
        await client.post(
            f"{BASE}/auth/sign-up",
            json={"agencyName": "Egress Agency", "fullName": "Op", "email": "egress@test.example",
                  "password": "correct-horse-battery-staple"},
        )

        resp = await client.post(f"{BASE}/clients", json={"url": SITE, "classify": True})

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ADDRESS_NOT_PUBLIC"
        assert body["industry"] is None
        assert chromium.launches == 0 and robots_web.requests == []
        assert body["crawl"] == EMPTY_CRAWL_SUMMARY

    async def test_a_site_refused_after_its_home_page_was_read_returns_no_facts(
        self, client: AsyncClient, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.resolve_to("internal.test", "10.0.0.5")
        chromium = _site_with_about(server_addrs={f"{SITE}/about": PRIVATE_ADDR})
        monkeypatch.setattr(crawl, "async_playwright", chromium)
        await _sign_up(client, "egress2@test.example")

        resp = await client.post(f"{BASE}/clients", json={"url": SITE, "classify": True})

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert chromium.navigations == [f"{SITE}/", f"{SITE}/about"]  # the home page was read
        assert body["classificationReasonCode"] == "ADDRESS_NOT_PUBLIC"
        # What the response says about the crawl: none of what was read.
        assert body["crawl"] == EMPTY_CRAWL_SUMMARY


class TestTheStoredAuditOfARefusedSite:
    async def test_the_audit_endpoint_returns_and_stores_nothing_that_was_read(
        self, client: AsyncClient, session: Any, robots_web: Any,
        monkeypatch: pytest.MonkeyPatch, quick_settle: None,
    ) -> None:
        from avp_api import ids
        from avp_api.models import Client, Scan
        from avp_api.models.scan import ScanStatus
        from avp_api.services import audit_runner

        site = "https://helpscout.com"
        robots_web.serve(f"{site}/robots.txt", body="User-agent: GPTBot\nDisallow: /\n")
        chromium = FakeChromium(
            pages={f"{site}/": rich_page()},
            server_addrs={f"{site}/": PRIVATE_ADDR}, listener_delays={f"{site}/": 0.2},
        )
        monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)
        await _sign_up(client, "egress3@test.example")
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        row = await session.get(Client, cid)
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=cid, agency_id=row.agency_id,
            status=ScanStatus.QUEUED,
        )
        session.add(scan)
        await session.commit()

        resp = await client.post(f"{BASE}/scans/{scan.id}/audit")

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert chromium.evaluations == 1  # the page was read, then the refusal came
        assert (body["status"], body["errorCode"], body["pagesCrawled"]) == (
            "failed", "ADDRESS_NOT_PUBLIC", 0
        )
        assert body["schemaTypes"] == [] and (body["wordCount"], body["h1Count"]) == (0, 0)
        assert (body["lcpMs"], body["cls"], body["contentAgeDays"]) == (None, None, None)
        assert body["technicalFoundation"] is None
        assert body["checks"] and all(
            (c["status"], c["detailCode"]) == ("not_applicable", "ADDRESS_NOT_PUBLIC")
            for c in body["checks"]
        )
        # No AI crawler table was stored, so the Crawler screen has nothing to offer.
        access = await client.get(f"{BASE}/clients/{cid}/ai-crawler-access")
        assert access.status_code == 200 and access.json() is None


async def _sign_up(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Egress Agency", "fullName": "Op", "email": email,
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


EMPTY_CRAWL_SUMMARY = {
    "pagesFetched": 0, "urlsFetched": [], "schemaTypes": [], "wordCount": 0, "h1Count": 0,
    "hasTitle": False, "hasMetaDescription": False, "detectedLanguage": None,
}


def test_the_product_token_is_the_one_in_the_user_agent() -> None:
    assert f"(compatible; {crawl.CRAWLER_TOKEN}/" in crawl.USER_AGENT_TEMPLATE


# ---------------------------------------------------------------------------
# robots.txt that could not be read — launch checklist 3.41
#
# Until 2026-10-03 every case below was stored as ROBOTS_DISALLOWED, and the
# screens said the site "asks crawlers like ours to stay out" for a site that
# never answered. What the crawler DOES is the policy's and is unchanged: with no
# file to read it fetches nothing (RFC 9309 §2.3.1.4, `PolicyBasis.UNREACHABLE`).
# Only the code differs, and a robots.txt that really forbids the page keeps
# ROBOTS_DISALLOWED.
# ---------------------------------------------------------------------------

ROBOTS = f"{SITE}/robots.txt"

# Each way robots.txt can fail to be read, as a call on the fake web.
UNREADABLE: dict[str, Callable[[Any], None]] = {
    "no answer, a connect timeout": lambda web: web.fail(ROBOTS, httpx.ConnectTimeout("slow")),
    "no answer, a read timeout": lambda web: web.fail(ROBOTS, httpx.ReadTimeout("slow")),
    "no answer, a refused connection": lambda web: web.fail(ROBOTS, httpx.ConnectError("no")),
    "a name that does not resolve": lambda web: web.dns_fails("prospect-site.com"),
    "a 500": lambda web: web.serve(ROBOTS, status=500),
    "a 502": lambda web: web.serve(ROBOTS, status=502),
    "a 503": lambda web: web.serve(ROBOTS, status=503),
    "a 429": lambda web: web.serve(ROBOTS, status=429),
    "a redirect loop": lambda web: web.serve(ROBOTS, status=301, headers={"location": ROBOTS}),
    "a redirect to something that is not a URL": lambda web: web.serve(
        ROBOTS, status=301, headers={"location": "http://[::1/robots.txt"}
    ),
    "a 304 with nowhere to go": lambda web: web.serve(ROBOTS, status=304),
}
UNREADABLE_ARRANGEMENTS = pytest.mark.parametrize(
    "arrange", list(UNREADABLE.values()), ids=list(UNREADABLE)
)


class TestARobotsTxtThatCannotBeRead:
    @UNREADABLE_ARRANGEMENTS
    async def test_the_intake_crawl_reads_nothing_and_says_the_file_was_unreachable(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch,
        arrange: Callable[[Any], None],
    ) -> None:
        arrange(robots_web)
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        result = await crawl.crawl_site("prospect-site.com")

        assert (result.ok, result.error_code) == (False, "ROBOTS_UNREACHABLE")
        # What it does is the policy's, as before: no browser, no page, not even a HEAD.
        assert chromium.launches == 0 and chromium.navigations == []
        assert set(robots_web.fetched()) <= {ROBOTS}
        assert result.signals.pages_fetched == 0 and result.text_extract == ""
        # The classifier records it as the reason: not a refusal by the site.
        outcome = await classify(result, settings=Settings(_env_file=None))
        assert (outcome.status, outcome.reason_code) == ("unclassifiable", "ROBOTS_UNREACHABLE")

    @UNREADABLE_ARRANGEMENTS
    async def test_the_audit_reads_nothing_and_reports_no_finding_about_the_site(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch,
        arrange: Callable[[Any], None],
    ) -> None:
        arrange(robots_web)
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        signals = await technical_audit.audit_site(SITE)

        assert (signals.ok, signals.error_code) == (False, "ROBOTS_UNREACHABLE")
        assert signals.robots_unreadable is True  # the site's OWN file is the one that failed
        assert chromium.launches == 0 and chromium.navigations == []
        # Not even the sitemap was asked for: with no robots.txt, nothing is fetched.
        assert set(robots_web.fetched()) <= {ROBOTS}
        # The Crawler screen's table: "not readable" for every agent, never blocked.
        assert signals.ai_crawler_access
        assert {a.verdict for a in signals.ai_crawler_access} == {AccessVerdict.UNKNOWN}

        checks = {c.key: c for c in technical_audit.build_checks(signals)}
        # Nothing was read, so nothing is a finding: no "publish a robots.txt" for a
        # file that may well exist, and no "robots.txt forbids" for one nobody read.
        assert not [c for c in checks.values() if c.status in ("warn", "fail", "error")]
        for key in ("indexable", "robots_txt_present", "sitemap_present",
                    *technical_audit.PAGE_CHECKS):
            assert (checks[key].status, checks[key].detail_code) == (
                "not_applicable", "ROBOTS_UNREACHABLE"
            ), key
        assert checks["cwv_inp"].detail_code == "FIELD_METRIC_REQUIRES_REAL_USER_DATA"
        outcome = technical_audit.score_audit(signals)
        assert (outcome.scored, outcome.score) == (False, None)

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    @pytest.mark.parametrize(
        "body", ["User-agent: *\nDisallow: /\n", "User-agent: AVPScanBot\nDisallow: /\n"]
    )
    async def test_a_robots_txt_that_forbids_the_page_is_still_a_refusal_by_the_site(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str, body: str
    ) -> None:
        robots_web.serve(ROBOTS, body=body)
        chromium = FakeChromium(pages={f"{SITE}/": page()})

        result = await _run(kind, monkeypatch, chromium)

        assert result.error_code == "ROBOTS_DISALLOWED"
        assert chromium.launches == 0 and chromium.navigations == []

    @pytest.mark.parametrize("status", [404, 410, 403])
    async def test_a_missing_file_is_still_a_missing_file_and_the_site_is_read(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        robots_web.serve(ROBOTS, status=status)
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        signals = await technical_audit.audit_site(SITE)

        assert signals.ok and signals.error_code is None and chromium.launches == 1
        assert signals.robots_unreadable is False  # a 4xx is an answer
        checks = {c.key: c for c in technical_audit.build_checks(signals)}
        # The answer came back and said there is no file: that stays a finding.
        assert (checks["robots_txt_present"].status, checks["robots_txt_present"].detail_code) == (
            "warn", "NO_ROBOTS_TXT"
        )

    async def test_each_outcome_is_logged_under_its_own_event(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from structlog.testing import capture_logs

        monkeypatch.setattr(crawl, "async_playwright", FakeChromium())
        monkeypatch.setattr(technical_audit, "async_playwright", FakeChromium())
        robots_web.serve(ROBOTS, status=503)
        with capture_logs() as unreadable:
            await crawl.crawl_site("prospect-site.com")
            await technical_audit.audit_site(SITE)
        robots_web.serve(ROBOTS, body="User-agent: *\nDisallow: /\n")
        with capture_logs() as forbidden:
            await crawl.crawl_site("prospect-site.com")
            await technical_audit.audit_site(SITE)

        assert {e["event"] for e in unreadable if "robots" in e["event"]} == {
            "crawl.robots_unreachable", "audit.robots_unreachable"
        }
        assert {e["event"] for e in forbidden if "robots" in e["event"]} == {
            "crawl.robots_disallowed", "audit.robots_disallowed"
        }

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_landing_on_a_host_whose_robots_txt_cannot_be_read_is_the_same(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        # A redirect only the browser is given: the site's own file was read and allows it.
        robots_web.serve(ROBOTS, body="User-agent: *\nDisallow: /private\n")
        robots_web.serve("https://elsewhere-host.com/robots.txt", status=503)
        chromium = FakeChromium(
            pages={"https://elsewhere-host.com/": page() if kind == "crawl" else rich_page()},
            landings={f"{SITE}/": "https://elsewhere-host.com/"},
        )

        result = await _run(kind, monkeypatch, chromium)

        assert result.error_code == "ROBOTS_UNREACHABLE"
        if kind == "crawl":
            assert result.text_extract == "" and result.signals.pages_fetched == 0
            return
        assert result.robots_unreadable is False  # the site's own file was read
        checks = {c.key: c for c in technical_audit.build_checks(result)}
        # What the site's own robots.txt said was read, and stays read. Its sitemap was
        # asked for, as its own file allowed, and was not there: that is a finding about
        # the site, whatever happened to the host the browser landed on afterwards.
        assert checks["robots_txt_present"].status == "pass"
        assert (checks["sitemap_present"].status, checks["sitemap_present"].detail_code) == (
            "warn", "NO_SITEMAP_XML"
        )
        # The page was not read, so nothing that needed it is a finding.
        assert (checks["indexable"].status, checks["indexable"].detail_code) == (
            "not_applicable", "ROBOTS_UNREACHABLE"
        )
        assert [c.key for c in checks.values() if c.status in ("warn", "fail", "error")] == [
            "sitemap_present"
        ]

    async def test_a_missing_own_robots_txt_stays_a_finding_when_the_landing_host_cannot_be_read(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The site's own file answered 404 (a true `NO_ROBOTS_TXT`); the host a script sent
        # the browser to did not answer at all. The audit ends as unreachable, but it must
        # not blank what the site's own file said: the contract says the site's own checks
        # stay what they read (checklist 3.41, round 2).
        robots_web.serve(ROBOTS, status=404)
        robots_web.serve("https://elsewhere-host.com/robots.txt", status=503)
        chromium = FakeChromium(
            pages={"https://elsewhere-host.com/": rich_page()},
            landings={f"{SITE}/": "https://elsewhere-host.com/"},
        )

        signals = await _run("audit", monkeypatch, chromium)

        assert (signals.ok, signals.error_code) == (False, "ROBOTS_UNREACHABLE")
        assert signals.robots_unreadable is False and signals.robots_txt_present is False
        checks = {c.key: c for c in technical_audit.build_checks(signals)}
        assert (checks["robots_txt_present"].status, checks["robots_txt_present"].detail_code) == (
            "warn", "NO_ROBOTS_TXT"
        )
        assert (checks["sitemap_present"].status, checks["sitemap_present"].detail_code) == (
            "warn", "NO_SITEMAP_XML"
        )
        for key in ("indexable", *technical_audit.PAGE_CHECKS):
            assert (checks[key].status, checks[key].detail_code) == (
                "not_applicable", "ROBOTS_UNREACHABLE"
            ), key
        # Exactly the two findings the site's own answers support, in the order the report sorts.
        assert sorted(c.key for c in checks.values() if c.status in ("warn", "fail", "error")) == [
            "robots_txt_present", "sitemap_present"
        ]

    async def test_a_sitemap_that_redirects_to_an_unreadable_host_is_unknown_not_forbidden(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(ROBOTS, body="User-agent: *\nDisallow: /private\n")
        robots_web.serve(
            f"{SITE}/sitemap.xml", status=301, headers={"location": "https://cdn-host.com/s.xml"}
        )
        robots_web.serve("https://cdn-host.com/robots.txt", status=503)
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        signals = await technical_audit.audit_site(SITE)

        # The audit goes on (the site's own robots.txt allows its page) and keeps its facts.
        assert signals.ok and signals.error_code is None and chromium.launches == 1
        assert signals.has_sitemap is False and signals.sitemap_refusal == "ROBOTS_UNREACHABLE"
        assert "https://cdn-host.com/s.xml" not in robots_web.fetched()
        sitemap = next(
            c for c in technical_audit.build_checks(signals) if c.key == "sitemap_present"
        )
        assert (sitemap.status, sitemap.detail_code) == ("not_applicable", "ROBOTS_UNREACHABLE")


class TestTheGateSaysWhyItRefused:
    @pytest.mark.parametrize(
        ("arrange", "code"),
        [
            (lambda web: web.serve(ROBOTS, status=500), "ROBOTS_UNREACHABLE"),
            (lambda web: web.serve(ROBOTS, status=503), "ROBOTS_UNREACHABLE"),
            (lambda web: web.serve(ROBOTS, status=429), "ROBOTS_UNREACHABLE"),
            (lambda web: web.fail(ROBOTS, httpx.ConnectTimeout("slow")), "ROBOTS_UNREACHABLE"),
            (lambda web: web.dns_fails("prospect-site.com"), "ROBOTS_UNREACHABLE"),
            (
                lambda web: web.serve(ROBOTS, body="User-agent: *\nDisallow: /\n"),
                "ROBOTS_DISALLOWED",
            ),
            (
                lambda web: web.serve(ROBOTS, body="User-agent: AVPScanBot\nDisallow: /a\n"),
                "ROBOTS_DISALLOWED",
            ),
        ],
        ids=["500", "503", "429", "timeout", "no DNS", "forbids all", "forbids /a"],
    )
    async def test_the_code_follows_whether_the_file_could_be_read(
        self, robots_web: Any, arrange: Callable[[Any], None], code: str
    ) -> None:
        arrange(robots_web)
        gate = crawl.RobotsGate(user_agent="ua")
        url = f"{SITE}/a/page"
        assert not await gate.allows(url)
        assert await gate.refusal_code(url) == code
        followed = await gate.follow("GET", url)
        assert (followed.response, followed.refusal) == (None, code)

    async def test_a_redirect_to_a_host_whose_robots_txt_cannot_be_read_is_never_requested(
        self, robots_web: Any
    ) -> None:
        robots_web.serve(
            f"{SITE}/", status=301, headers={"location": "https://www.prospect-site.com/"}
        )
        robots_web.serve("https://www.prospect-site.com/robots.txt", status=503)
        gate = crawl.RobotsGate(user_agent="ua")

        followed = await gate.resolve(f"{SITE}/")

        # The refusal is the host that stopped it: the bare domain's file was fine.
        assert (followed.url, followed.refusal) == (
            "https://www.prospect-site.com/", "ROBOTS_UNREACHABLE"
        )
        assert "https://www.prospect-site.com/" not in robots_web.fetched()

    async def test_a_body_past_the_size_limit_is_cut_and_still_read_as_rules(
        self, robots_web: Any
    ) -> None:
        early = "User-agent: *\nDisallow: /private\n"
        late = "\nUser-agent: *\nDisallow: /\n"
        robots_web.serve(ROBOTS, body=early + "#" * crawl.ROBOTS_MAX_CHARS + late)
        gate = crawl.RobotsGate(user_agent="ua")
        # The first 512,000 characters count and the rest do not: neither fails the file.
        assert (await gate.file(SITE)).status == 200
        assert await gate.allows(f"{SITE}/")
        assert not await gate.allows(f"{SITE}/private/x")

    @pytest.mark.parametrize(
        ("content_type", "body"),
        [
            ("text/html", "<!doctype html><html><body>app</body></html>"),
            ("application/json", '{"error": "not found"}'),
            ("application/octet-stream", "\x00\x01\x02 not a robots file"),
        ],
    )
    async def test_a_2xx_body_that_is_not_a_robots_file_is_read_as_one_with_no_rules(
        self, robots_web: Any, content_type: str, body: str
    ) -> None:
        robots_web.serve(ROBOTS, body=body, headers={"content-type": content_type})
        gate = crawl.RobotsGate(user_agent="ua")
        assert await gate.allows(f"{SITE}/")
        assert (await gate.policy(SITE)).basis is PolicyBasis.RULES


# ---------------------------------------------------------------------------
# launch checklist 3.41, round 2: the gate reads little, and waits briefly
#
# Its robots.txt, HEAD-hop and sitemap requests used to read the whole body before
# cutting it, and had httpx's per-operation timeout and nothing else: a server that
# sent a byte every few seconds held the request, and the agency's read slot and
# database connection behind it, for as long as it liked. Now each request ends by a
# total deadline and reads no more than it needs.
# ---------------------------------------------------------------------------

CHUNK = 65_536
TEN_MB = 10 * 1024 * 1024


def _trickle(first: bytes = b"") -> Callable[[], AsyncIterator[bytes]]:
    """A body that never ends: `first`, then a byte every few milliseconds, for ever."""

    async def chunks() -> AsyncIterator[bytes]:
        if first:
            yield first
        while True:
            await asyncio.sleep(0.005)
            yield b"#"

    return chunks


def _long(first: bytes, sent: list[int], total: int = TEN_MB) -> Callable[[], AsyncIterator[bytes]]:
    """A long body that counts what was pulled from it: `sent[0]` bytes so far."""

    async def chunks() -> AsyncIterator[bytes]:
        sent[0] += len(first)
        yield first
        while sent[0] < total:
            sent[0] += CHUNK
            yield b"#" * CHUNK

    return chunks


class TestTheGateReadsLittleAndWaitsBriefly:
    @pytest.mark.parametrize("how", ["a body that trickles", "headers that never come"])
    async def test_a_robots_txt_that_never_finishes_is_no_answer_by_the_deadline(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, how: str
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 0.25)
        if how == "a body that trickles":
            robots_web.serve_stream(ROBOTS, _trickle(b"User-agent: *\n"))
        else:
            robots_web.hang(ROBOTS)
        gate = crawl.RobotsGate(user_agent="ua")

        # The net is not what is under test: nothing but the deadline can end either.
        async with asyncio.timeout(5):
            robots = await gate.file(SITE)

        assert (robots.status, robots.body) == (None, "")
        assert not await gate.allows(f"{SITE}/")
        assert await gate.refusal_code(f"{SITE}/") == "ROBOTS_UNREACHABLE"

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    @pytest.mark.parametrize("how", ["a body that trickles", "headers that never come"])
    async def test_a_robots_txt_that_never_finishes_ends_the_run_as_unreachable(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str, how: str
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 0.25)
        monkeypatch.setattr(technical_audit, "SIDE_FETCH_DEADLINE_S", 0.25)
        if how == "a body that trickles":
            robots_web.serve_stream(ROBOTS, _trickle(b"User-agent: *\n"))
        else:
            robots_web.hang(ROBOTS)
        chromium = FakeChromium(pages={f"{SITE}/": page() if kind == "crawl" else rich_page()})

        async with asyncio.timeout(5):
            result = await _run(kind, monkeypatch, chromium)

        # What the crawler does with a robots.txt it cannot read is unchanged: nothing.
        assert result.error_code == "ROBOTS_UNREACHABLE"
        assert chromium.launches == 0 and chromium.navigations == []
        if kind == "audit":
            assert result.robots_unreadable is True

    async def test_a_hop_that_never_answers_is_no_answer_and_not_a_refusal(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 0.25)
        robots_web.serve(ROBOTS, status=404)  # no file: allowed
        robots_web.hang(f"{SITE}/")
        gate = crawl.RobotsGate(user_agent="ua")

        async with asyncio.timeout(5):
            followed = await gate.resolve(f"{SITE}/")

        # The address is left as it is, as for any server that does not answer a HEAD.
        assert (followed.url, followed.response, followed.refusal) == (f"{SITE}/", None, None)

    async def test_a_10_mb_robots_txt_is_read_only_up_to_the_cap(self, robots_web: Any) -> None:
        sent = [0]
        first = b"User-agent: *\nDisallow: /private\n"
        robots_web.serve_stream(ROBOTS, _long(first, sent))
        gate = crawl.RobotsGate(user_agent="ua")

        robots = await gate.file(SITE)

        assert robots.status == 200 and len(robots.body) == crawl.ROBOTS_MAX_CHARS
        # It stopped pulling at the cap, within a chunk: the other 9.5 MB were never downloaded.
        assert crawl.ROBOTS_MAX_CHARS <= sent[0] <= crawl.ROBOTS_MAX_CHARS + 2 * CHUNK < TEN_MB
        # And what lies inside the cap is read as rules, as it always was.
        assert not await gate.allows(f"{SITE}/private/x") and await gate.allows(f"{SITE}/")

    @pytest.mark.parametrize(
        ("content_type", "first", "present"),
        [
            ("text/plain", b'<?xml version="1.0"?><urlset>', True),
            ("application/xml", b"<urlset>", True),
            ("text/plain", b"not a sitemap at all", False),
        ],
        ids=["an XML declaration", "an XML content type", "neither"],
    )
    async def test_the_sitemap_is_judged_by_its_first_characters_and_no_more_is_read(
        self, robots_web: Any, content_type: str, first: bytes, present: bool
    ) -> None:
        robots_web.serve(ROBOTS, status=404)  # no file: allowed
        sent = [0]
        robots_web.serve_stream(
            f"{SITE}/sitemap.xml", _long(first, sent), headers={"content-type": content_type}
        )

        side = await technical_audit._fetch_side_files(  # noqa: SLF001
            crawl.RobotsGate(user_agent="ua"), SITE
        )

        assert side.sitemap_present is present
        # Never the 10 MB on offer: the cap, and the chunk that crossed it.
        assert sent[0] <= technical_audit.SITEMAP_MAX_CHARS + 2 * CHUNK < TEN_MB

    async def test_a_sitemap_that_never_finishes_is_no_sitemap_and_the_audit_goes_on(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(technical_audit, "SIDE_FETCH_DEADLINE_S", 0.25)
        robots_web.serve(ROBOTS, body="User-agent: *\nDisallow: /private\n")
        robots_web.serve_stream(f"{SITE}/sitemap.xml", _trickle(b"<?xml"))
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})

        async with asyncio.timeout(5):
            signals = await _run("audit", monkeypatch, chromium)

        # Its existing failure handling: no answer reads as no sitemap, the audit is kept.
        assert signals.ok and signals.error_code is None and chromium.launches == 1
        assert signals.has_sitemap is False and signals.sitemap_refusal is None

    async def test_a_slow_robots_txt_that_finishes_inside_the_deadline_is_read_in_full(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 2.0)

        async def slow() -> AsyncIterator[bytes]:
            for _ in range(20):
                await asyncio.sleep(0.005)
                yield b"#"
            yield b"\nUser-agent: *\nDisallow: /private\n"

        robots_web.serve_stream(ROBOTS, slow)
        gate = crawl.RobotsGate(user_agent="ua")

        robots = await gate.file(SITE)

        # The deadline is a ceiling on a request, not a speed limit on a slow one.
        assert robots.status == 200 and robots.body.endswith("Disallow: /private\n")
        assert not await gate.allows(f"{SITE}/private/x")

    async def test_an_outer_cap_still_cuts_a_request_the_deadline_has_not_reached(
        self, robots_web: Any
    ) -> None:
        # The browser runs sit under `crawl_run_cap_s` / `audit_run_cap_s`, and a landing
        # host's robots.txt is read inside them. The gate's own `except TimeoutError` must
        # not swallow the cap's: it is the cap that ends the run.
        robots_web.hang(ROBOTS)
        gate = crawl.RobotsGate(user_agent="ua")  # the default deadline: 10 s

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                await gate.file(SITE)

    async def test_follow_reads_no_more_of_a_body_than_it_is_asked_for(
        self, robots_web: Any
    ) -> None:
        robots_web.serve(ROBOTS, status=404)
        robots_web.serve(f"{SITE}/page", body="0123456789")
        gate = crawl.RobotsGate(user_agent="ua")

        assert (await gate.follow("GET", f"{SITE}/page", max_chars=4)).response == crawl.Reply(
            200, "text/plain", "0123"
        )
        # Asked for nothing (the default, and a HEAD's), it reads nothing.
        assert (await gate.follow("GET", f"{SITE}/page")).response == crawl.Reply(
            200, "text/plain", ""
        )

    async def test_refusal_code_sends_nothing_of_its_own(self, robots_web: Any) -> None:
        robots_web.serve(ROBOTS, status=503)
        gate = crawl.RobotsGate(user_agent="ua")
        url = f"{SITE}/a"
        assert not await gate.allows(url)
        sent, looked_up = list(robots_web.requests), list(robots_web.lookups)

        assert await gate.refusal_code(url) == "ROBOTS_UNREACHABLE"
        assert await gate.readable(SITE) is False

        # It read the fetch `allows` had just made, and sent and looked up nothing.
        assert robots_web.requests == sent and robots_web.lookups == looked_up
        # The control: for a host `allows` never asked about it has to fetch, so the
        # equality above is the cache's doing and not an accident of the arrangement.
        robots_web.serve("https://other-host.test/robots.txt", status=503)
        assert await gate.refusal_code("https://other-host.test/a") == "ROBOTS_UNREACHABLE"
        assert robots_web.requests != sent

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_page_whose_url_moves_between_two_reads_cannot_name_another_origin(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        # The browser lands on A, whose robots.txt cannot be read. The moment the gate has
        # answered for A, the page's url moves to B, whose robots.txt forbids everything.
        # The landing is read once, so the run ends as A's outcome and B is never asked.
        a, b = "https://landing-a.test/", "https://landing-b.test/"
        robots_web.serve(ROBOTS, body="User-agent: *\nDisallow: /private\n")
        robots_web.serve(f"{a}robots.txt", status=503)
        robots_web.serve(f"{b}robots.txt", body="User-agent: *\nDisallow: /\n")
        opened: list[Any] = []
        real_new_page = _Browser.new_page

        async def new_page(self: Any) -> Any:
            opened.append(await real_new_page(self))
            return opened[-1]

        real_allows = crawl.RobotsGate.allows

        async def allows(self: Any, url: str) -> bool:
            answer = await real_allows(self, url)
            if url == a and opened:
                opened[0].url = b  # the page moves on
            return answer

        monkeypatch.setattr(_Browser, "new_page", new_page)
        monkeypatch.setattr(crawl.RobotsGate, "allows", allows)
        chromium = FakeChromium(
            pages={a: page() if kind == "crawl" else rich_page()}, landings={f"{SITE}/": a}
        )

        result = await _run(kind, monkeypatch, chromium)

        assert result.error_code == "ROBOTS_UNREACHABLE"  # A's, not B's "ROBOTS_DISALLOWED"
        assert f"{b}robots.txt" not in robots_web.fetched()
        if kind == "crawl":
            assert result.text_extract == "" and result.signals.pages_fetched == 0


# ---------------------------------------------------------------------------
# launch checklist 3.41, round 3
#
# A compressed body used to be inflated by httpx's decoders with no limit on what they
# produce, a chunk at a time on the event loop, before the character cap was applied;
# the gate reads the raw stream now and inflates it through ONE bounded decompressor. The
# reads BEFORE a browser starts, each under its own deadline, have one budget over all of
# them. A body it cannot decode, a hop that is not http, a last redirect nobody asked
# robots.txt about, and a 404 page that is slow, are each one outcome and no more.
# ---------------------------------------------------------------------------

MIB = 1024 * 1024
RULES = b"User-agent: *\nDisallow: /private\n"
GZIP = {"content-type": "text/plain", "content-encoding": "gzip"}


def _deflate_raw(data: bytes) -> bytes:
    """What many servers send as "deflate": the stream without the zlib wrapper."""
    z = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return z.compress(data) + z.flush()


def _bomb(head: bytes, expanded: int) -> bytes:
    """A gzip stream of `head` and then zeros, `expanded` bytes in all, in tens of kilobytes."""
    z = zlib.compressobj(wbits=zlib.MAX_WBITS | 16)
    out = bytearray(z.compress(head))
    zeros = bytes(MIB)
    for _ in range((expanded - len(head)) // MIB):
        out += z.compress(zeros)
    return bytes(out + z.flush())


def _served(
    data: bytes, pulled: list[int] | None = None, size: int = 16_384
) -> Callable[[], AsyncIterator[bytes]]:
    """`data` in network-sized pieces; `pulled[0]` is how many bytes of it were asked for."""

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(data), size):
            piece = data[start : start + size]
            if pulled is not None:
                pulled[0] += len(piece)
            yield piece

    return chunks


class _CountingInflater:
    """A zlib decompressor that counts what it was asked for and what it gave back."""

    def __init__(self, real: Any, ledger: dict[str, Any]) -> None:
        self._real = real
        self._ledger = ledger

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        out = self._real.decompress(data, max_length)
        self._ledger["limits"].append(max_length)
        self._ledger["inflated"] += len(out)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest.fixture
def inflated(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """`{"inflated": bytes zlib produced, "limits": the max_length of every call}`."""
    ledger: dict[str, Any] = {"limits": [], "inflated": 0}
    real = zlib.decompressobj
    monkeypatch.setattr(
        zlib, "decompressobj", lambda *a, **k: _CountingInflater(real(*a, **k), ledger)
    )
    return ledger


class TestACompressedBodyIsInflatedNoFurtherThanTheCap:
    async def test_a_gzip_bomb_is_inflated_up_to_the_cap_and_no_further(
        self, robots_web: Any, inflated: dict[str, Any]
    ) -> None:
        bomb = _bomb(RULES, 64 * MIB)  # tens of kilobytes of gzip that inflate to 64 MB
        assert len(bomb) < 200_000
        pulled = [0]
        robots_web.serve_stream(ROBOTS, _served(bomb, pulled), headers=GZIP)
        gate = crawl.RobotsGate(user_agent="ua")

        tracemalloc.start()
        try:
            robots = await gate.file(SITE)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert robots.status == 200 and len(robots.body) == crawl.ROBOTS_MAX_CHARS
        # The cap, a step at a time: never the 64 MB, and never a call to zlib without a limit.
        assert (
            crawl.ROBOTS_MAX_CHARS
            <= inflated["inflated"]
            <= crawl.ROBOTS_MAX_CHARS + crawl._INFLATE_STEP  # noqa: SLF001
        )
        assert inflated["limits"] and all(
            0 < n <= crawl._INFLATE_STEP for n in inflated["limits"]  # noqa: SLF001
        )
        assert peak < 12 * MIB  # the expansion would have been 64 MB or more
        assert pulled[0] < len(bomb)  # and the rest of the stream was never pulled
        # What lies inside the cap is read as rules, as it always was.
        assert not await gate.allows(f"{SITE}/private/x") and await gate.allows(f"{SITE}/")

    async def test_a_gzip_bomb_as_a_sitemap_is_inflated_up_to_its_cap_and_no_further(
        self, robots_web: Any, inflated: dict[str, Any]
    ) -> None:
        robots_web.serve(ROBOTS, status=404)
        robots_web.serve_stream(
            f"{SITE}/sitemap.xml",
            _served(_bomb(b'<?xml version="1.0"?><urlset>', 32 * MIB)),
            headers=GZIP,  # text/plain: only its first characters say it is XML
        )

        side = await technical_audit._fetch_side_files(  # noqa: SLF001
            crawl.RobotsGate(user_agent="ua"), SITE
        )

        assert side.sitemap_present is True
        assert (
            technical_audit.SITEMAP_MAX_CHARS
            <= inflated["inflated"]
            <= technical_audit.SITEMAP_MAX_CHARS + crawl._INFLATE_STEP  # noqa: SLF001
        )

    @pytest.mark.parametrize(
        "coding",
        [
            "gzip, gzip", "gzip,deflate", "deflate, gzip", "gzip, identity, gzip",
            "br", "zstd", "compress", "foo", "gzip, br", "br, gzip",
        ],
    )
    async def test_stacked_or_unsupported_codings_are_no_answer(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, coding: str
    ) -> None:
        headers = {"content-type": "text/plain", "content-encoding": coding}
        robots_web.serve_stream(ROBOTS, _served(gzip.compress(RULES)), headers=headers)
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)

        # A robots.txt we do not decode is not "no rules": it is one we could not read.
        assert (await crawl.RobotsGate(user_agent="ua").file(SITE)).status is None
        result = await crawl.crawl_site("prospect-site.com")

        assert (result.ok, result.error_code) == (False, "ROBOTS_UNREACHABLE")
        assert chromium.launches == 0 and chromium.navigations == []

    async def test_a_sitemap_in_an_unsupported_coding_is_not_found_and_the_side_facts_are_kept(
        self, robots_web: Any
    ) -> None:
        robots_web.serve(ROBOTS, body=RULES.decode())
        robots_web.serve_stream(
            f"{SITE}/sitemap.xml", _served(b"x"),
            headers={"content-type": "application/xml", "content-encoding": "br"},
        )

        side = await technical_audit._fetch_side_files(  # noqa: SLF001
            crawl.RobotsGate(user_agent="ua"), SITE
        )

        assert side.robots_present is True and side.robots_unreadable is False
        assert side.sitemap_present is False and side.sitemap_refusal is None

    @pytest.mark.parametrize(
        ("coding", "payload"),
        [
            ("gzip", gzip.compress(RULES)),
            ("x-gzip", gzip.compress(RULES)),
            ("GZIP", gzip.compress(RULES)),
            ("deflate", zlib.compress(RULES)),
            ("deflate", _deflate_raw(RULES)),
            ("gzip, identity", gzip.compress(RULES)),
            ("identity", RULES),
            ("", RULES),
        ],
        ids=["gzip", "x-gzip", "upper case", "deflate", "raw deflate", "gzip + identity",
             "identity", "empty"],
    )
    async def test_a_gzip_or_deflate_robots_txt_is_read_as_it_always_was(
        self, robots_web: Any, coding: str, payload: bytes
    ) -> None:
        headers = {"content-type": "text/plain", "content-encoding": coding}
        robots_web.serve_stream(ROBOTS, _served(payload), headers=headers)
        gate = crawl.RobotsGate(user_agent="ua")

        robots = await gate.file(SITE)

        assert (robots.status, robots.body) == (200, RULES.decode())
        assert not await gate.allows(f"{SITE}/private/x") and await gate.allows(f"{SITE}/")

    async def test_a_corrupt_stream_is_no_answer(self, robots_web: Any) -> None:
        corrupt = b"\x1f\x8b" + b"not a gzip stream" * 5
        robots_web.serve_stream(ROBOTS, _served(corrupt), headers=GZIP)
        assert (await crawl.RobotsGate(user_agent="ua").file(SITE)).status is None

    async def test_four_byte_characters_are_not_cut_short_by_the_inflate_budget(
        self, robots_web: Any
    ) -> None:
        text = "\U0001f600" * (crawl.ROBOTS_MAX_CHARS + 1_000)
        robots_web.serve_stream(ROBOTS, _served(gzip.compress(text.encode())), headers=GZIP)

        robots = await crawl.RobotsGate(user_agent="ua").file(SITE)

        assert robots.body == text[: crawl.ROBOTS_MAX_CHARS]

    async def test_the_gate_asks_only_for_the_codings_it_decodes(self) -> None:
        client = crawl.RobotsGate(user_agent="ua")._client()  # noqa: SLF001
        try:
            offered = {c.strip() for c in client.headers["accept-encoding"].split(",")}
        finally:
            await client.aclose()
        assert offered == {"gzip", "deflate"}
        assert offered <= set(crawl._ZLIB_WBITS)  # noqa: SLF001


class TestABodyWhoseCharsetIsNotATextCodec:
    # The gate decodes a body in a charset on a LIST (`crawl._TEXT_CHARSETS`): UTF-8, UTF-16,
    # UTF-32, ASCII, ISO-8859-n and Windows-125x. httpx takes any codec `codecs.lookup`
    # knows, and the registry holds codecs that must not be run on a stranger's bytes. The
    # decoding is synchronous on the API's one event loop, so no request deadline and no
    # budget can interrupt it: what is not on the list is refused BEFORE a byte of the body
    # is read. A robots.txt that names such a charset is no answer (ROBOTS_UNREACHABLE,
    # nothing fetched); a sitemap that does is not found. What is on the list is read as it
    # says, under any spelling Python knows for it.

    # Each raises on decode (AssertionError, TypeError or UnicodeError) where a caller looks
    # for none.
    CODECS = ["hex", "rot13", "base64", "zlib", "bz2", "idna", "undefined"]
    # Each decodes without raising, which is what makes them dangerous: punycode rebuilds
    # the text for every code point it adds, and utf-7's buffered decoder re-decodes all it
    # holds on every call.
    COSTLY = ["punycode", "utf-7", "utf7"]
    # Not charsets at all: escape sequences, a table with no table, and the byte-to-byte
    # encodings.
    NOT_CHARSETS = ["unicode_escape", "raw_unicode_escape", "charmap", "quopri", "uu"]
    # Text charsets that are linear-time and not on the list. A choice, not a danger: a file
    # that names one is unreadable until the list takes it.
    OFF_THE_LIST = ["shift_jis", "euc-jp", "gbk", "big5", "euc-kr", "koi8-r", "cp437", "mac-roman"]
    REFUSED = [*CODECS, *COSTLY, *NOT_CHARSETS, *OFF_THE_LIST]

    @pytest.mark.parametrize("codec", REFUSED)
    async def test_a_robots_txt_with_one_is_no_answer_and_not_a_500(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, codec: str
    ) -> None:
        pulled = [0]
        robots_web.serve_stream(
            ROBOTS, _served(b"User-agent: *\nDisallow: /\n", pulled),
            headers={"content-type": f"text/plain; charset={codec}"},
        )
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)

        assert (await crawl.RobotsGate(user_agent="ua").file(SITE)).status is None
        # Through the whole of each run, with nothing raised: no 500 on the intake request.
        crawled = await crawl.crawl_site("prospect-site.com")
        audited = await technical_audit.audit_site(SITE)

        assert (crawled.ok, crawled.error_code) == (False, "ROBOTS_UNREACHABLE")
        assert (audited.ok, audited.error_code) == (False, "ROBOTS_UNREACHABLE")
        assert chromium.launches == 0
        # Refused by its name: not a byte of the body was read, let alone decoded.
        assert pulled[0] == 0

    @pytest.mark.parametrize("codec", ["hex", "punycode", "utf-7", "shift_jis"])
    async def test_a_sitemap_with_one_is_not_found_and_the_audit_is_kept(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, codec: str
    ) -> None:
        pulled = [0]
        robots_web.serve(ROBOTS, body=RULES.decode())
        robots_web.serve_stream(
            f"{SITE}/sitemap.xml", _served(b"<urlset/>", pulled),
            headers={"content-type": f"application/xml; charset={codec}"},
        )
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})

        signals = await _run("audit", monkeypatch, chromium)

        assert signals.ok and signals.error_code is None and chromium.launches == 1
        assert signals.robots_txt_present is True  # the side facts that were read are kept
        assert signals.has_sitemap is False and signals.sitemap_refusal is None
        assert pulled[0] == 0

    # (the charset as a server writes it, the codec the bytes are really in, text it holds)
    READ_AS_THEY_SAY = [
        ("utf-8", "utf-8", "café €"),
        ("UTF8", "utf-8", "café €"),
        ("utf-8-sig", "utf-8-sig", "café €"),
        ("utf-16", "utf-16", "café €"),
        ("utf-16le", "utf-16-le", "café €"),
        ("utf-16be", "utf-16-be", "café €"),
        ("utf-32", "utf-32", "café €"),
        ("utf-32le", "utf-32-le", "café €"),
        ("utf-32be", "utf-32-be", "café €"),
        ("ascii", "ascii", "plain"),
        ("us-ascii", "ascii", "plain"),
        ("latin-1", "latin-1", "café"),
        ("ISO-8859-1", "latin-1", "café"),
        ("iso-8859-2", "iso-8859-2", "łódź"),
        ("iso-8859-15", "iso-8859-15", "café €"),
        ("windows-1252", "cp1252", "café €"),
        ("cp1251", "cp1251", "кафе"),
        ("windows-1250", "cp1250", "łódź"),
    ]

    @pytest.mark.parametrize(
        ("label", "real", "sample"), READ_AS_THEY_SAY, ids=[row[0] for row in READ_AS_THEY_SAY]
    )
    async def test_a_charset_on_the_list_is_read_as_it_says(
        self, robots_web: Any, label: str, real: str, sample: str
    ) -> None:
        text = f"# {sample}\nUser-agent: *\nDisallow: /private\n"
        # In pieces of three bytes: no character of any of them falls whole into one.
        robots_web.serve_stream(
            ROBOTS, _served(text.encode(real), size=3),
            headers={"content-type": f"text/plain; charset={label}"},
        )
        gate = crawl.RobotsGate(user_agent="ua")

        robots = await gate.file(SITE)

        assert (robots.status, robots.body) == (200, text)
        assert not await gate.allows(f"{SITE}/private/x") and await gate.allows(f"{SITE}/")

    @pytest.mark.parametrize(
        "headers",
        [
            {"content-type": "text/plain"},
            {"x-served-by": "fake"},
            {"content-type": "text/plain; charset=klingon"},
        ],
        ids=["no charset", "no content type", "a charset nobody knows"],
    )
    async def test_a_body_with_no_charset_or_one_nobody_knows_is_utf_8_as_it_was(
        self, robots_web: Any, headers: dict[str, str]
    ) -> None:
        text = "# café €\nUser-agent: *\nDisallow: /private\n"
        robots_web.serve_stream(ROBOTS, _served(text.encode(), size=3), headers=headers)
        gate = crawl.RobotsGate(user_agent="ua")

        robots = await gate.file(SITE)

        assert (robots.status, robots.body) == (200, text)
        assert not await gate.allows(f"{SITE}/private/x") and await gate.allows(f"{SITE}/")

    @pytest.mark.parametrize("codec", ["utf-16", "utf-32"])
    async def test_utf_16_and_utf_32_with_no_byte_order_mark_are_no_answer(
        self, robots_web: Any, codec: str
    ) -> None:
        # Python's codecs raise for these whatever `errors` says: a file that cannot be read
        # as it says it is, so no answer and not a 500.
        robots_web.serve_stream(
            ROBOTS, _served(RULES.decode().encode(f"{codec}-le")),
            headers={"content-type": f"text/plain; charset={codec}"},
        )
        gate = crawl.RobotsGate(user_agent="ua")

        assert (await gate.file(SITE)).status is None
        assert (await gate.policy(SITE)).basis is PolicyBasis.UNREACHABLE

    @pytest.mark.parametrize("codec", CODECS)
    async def test_a_codec_that_got_onto_the_list_by_mistake_is_still_no_answer(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, codec: str
    ) -> None:
        # The list is the first line. Were a codec that raises ever added to it, the handlers
        # around the decoder are the second: no answer and not a 500, as they always were.
        monkeypatch.setattr(
            crawl, "_TEXT_CHARSETS",
            crawl._TEXT_CHARSETS | {codecs.lookup(codec).name},  # noqa: SLF001
        )
        robots_web.serve(
            ROBOTS, body=RULES.decode(),
            headers={"content-type": f"text/plain; charset={codec}"},
        )

        assert (await crawl.RobotsGate(user_agent="ua").file(SITE)).status is None

    @pytest.mark.parametrize(
        "label",
        [
            "UTF8", "utf_8", "u8", "utf_8_sig", "UTF16", "U16", "UTF-16LE", "utf_16_be",
            "UTF32", "utf-32le", "utf_32_be", "US-ASCII", "646", "latin1", "L1", "ISO_8859-1",
            "iso8859-9", "ISO-8859-11", "iso-8859-16", "Windows-1253", "windows-1258",
            "CP1250",
        ],
    )
    def test_a_spelling_python_knows_for_a_listed_charset_is_a_listed_charset(
        self, label: str
    ) -> None:
        response = httpx.Response(200, headers={"content-type": f"text/plain; charset={label}"})

        decoder = crawl._text_decoder(response)  # noqa: SLF001

        assert isinstance(decoder, codecs.IncrementalDecoder)

    @pytest.mark.parametrize(
        "label", ["UTF-7", "utf7", "u7", "Punycode", "IDNA", "rot_13", "ISO-2022-JP", "SJIS"]
    )
    def test_a_spelling_python_knows_for_an_unlisted_charset_is_an_unlisted_charset(
        self, label: str
    ) -> None:
        response = httpx.Response(200, headers={"content-type": f"text/plain; charset={label}"})

        with pytest.raises(crawl._UnreadableBodyError):  # noqa: SLF001
            crawl._text_decoder(response)  # noqa: SLF001

    async def test_a_body_that_is_not_read_may_name_any_charset(self, robots_web: Any) -> None:
        # The list applies where a body is read: a robots.txt or a sitemap that answers 2xx.
        # A homepage asked with HEAD and a 404 page are not read, so no charset of theirs is
        # a reason to refuse them (or to stop crawling a site that has no robots.txt).
        robots_web.serve(ROBOTS, status=404, headers={"content-type": "text/html; charset=utf-7"})
        robots_web.serve(f"{SITE}/", headers={"content-type": "text/html; charset=punycode"})
        gate = crawl.RobotsGate(user_agent="ua")

        followed = await gate.resolve(f"{SITE}/")

        assert followed.refusal is None
        assert followed.response is not None and followed.response.status == 200
        assert (await gate.file(SITE)).status == 404
        assert (await gate.policy(SITE)).basis is PolicyBasis.UNAVAILABLE


def _slow_chain(web: Any, hosts: list[str], seconds: float) -> None:
    """Hosts that each answer, each answer after `seconds`: no robots.txt, a homepage that
    redirects to the next host. Slow, but every request finishes inside its own deadline."""
    for index, host in enumerate(hosts):
        home = f"https://{host}"
        later = hosts[index + 1] if index + 1 < len(hosts) else None
        for url in (home, f"{home}/"):
            if later:
                web.serve(url, status=301, headers={"location": f"https://{later}/"})
            else:
                web.serve(url, status=200)
            web.slow(url, seconds)
        web.slow(f"{home}/robots.txt", seconds)


class TestThePreBrowserBudget:
    HOSTS = ["prospect-site.com", "h1.test", "h2.test", "h3.test", "h4.test"]

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_chain_of_slow_but_finishing_hosts_ends_by_the_budget_as_a_timeout(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        monkeypatch.setattr(crawl, "PRE_BROWSER_BUDGET_S", 0.5)
        monkeypatch.setattr(technical_audit, "PRE_BROWSER_BUDGET_S", 0.5)
        _slow_chain(robots_web, self.HOSTS, 0.15)  # ten requests, 1.5 s: each well inside 10 s
        end = f"https://{self.HOSTS[-1]}/"
        chromium = FakeChromium(pages={end: page() if kind == "crawl" else rich_page()})

        async with asyncio.timeout(10):
            result = await _run(kind, monkeypatch, chromium)

        assert (result.ok, result.error_code) == (False, "TIMEOUT")
        assert chromium.launches == 0 and chromium.navigations == []
        assert end not in robots_web.fetched()  # the chain was cut, not walked to its end
        if kind == "crawl":
            assert result.signals == crawl.CrawlSignals(
                final_url=TYPED, registrable_domain="prospect-site.com"
            )

    async def test_a_budget_that_runs_out_after_the_side_files_keeps_what_they_read(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(technical_audit, "PRE_BROWSER_BUDGET_S", 0.5)
        robots_web.serve(ROBOTS, body=RULES.decode())  # the site's own files are quick
        for home in (SITE, f"{SITE}/"):
            robots_web.serve(home, status=301, headers={"location": "https://h1.test/"})
        _slow_chain(robots_web, self.HOSTS[1:], 0.3)  # then its redirects are not
        chromium = FakeChromium()

        async with asyncio.timeout(10):
            signals = await _run("audit", monkeypatch, chromium)

        assert (signals.ok, signals.error_code) == (False, "TIMEOUT")
        assert chromium.launches == 0
        assert signals.robots_txt_present is True and signals.ai_crawler_access
        assert f"https://{self.HOSTS[-1]}/" not in robots_web.fetched()

    async def test_a_budget_that_runs_out_inside_the_side_files_is_not_swallowed_by_them(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The side files may fail without failing the audit (a broad `except` says so); the
        # budget running out is not one of those failures, and must not let the audit carry
        # on to the homepage and the browser.
        monkeypatch.setattr(technical_audit, "PRE_BROWSER_BUDGET_S", 0.5)
        robots_web.serve(ROBOTS, body=RULES.decode())
        robots_web.slow(ROBOTS, 0.3)
        robots_web.serve(
            f"{SITE}/sitemap.xml", body="<?xml version='1.0'?><urlset/>",
            headers={"content-type": "application/xml"},
        )
        robots_web.slow(f"{SITE}/sitemap.xml", 0.3)  # robots.txt took 0.3, the sitemap another 0.3
        chromium = FakeChromium(pages={f"{SITE}/": rich_page()})

        async with asyncio.timeout(10):
            signals = await _run("audit", monkeypatch, chromium)

        assert (signals.ok, signals.error_code) == (False, "TIMEOUT")
        assert chromium.launches == 0 and robots_web.fetched("HEAD") == []
        # The side files never returned, so nothing of theirs was handed over: not measured.
        assert signals.robots_txt_present is False and signals.ai_crawler_access == []

    async def test_the_budget_is_one_worst_case_robots_txt_chain(self) -> None:
        assert crawl.PRE_BROWSER_BUDGET_S == (
            (crawl.MAX_REDIRECTS + 1) * crawl.ROBOTS_REQUEST_DEADLINE_S
        )
        assert technical_audit.PRE_BROWSER_BUDGET_S is crawl.PRE_BROWSER_BUDGET_S


FINAL_ROBOTS = "https://final.test/robots.txt"


class TestTheLastLocationWhenRedirectsRunOut:
    def _chain(self, web: Any, final: str) -> list[str]:
        """`MAX_REDIRECTS + 1` hosts that redirect in turn: the last Location is a seventh."""
        hosts = [f"hop{i}.test" for i in range(crawl.MAX_REDIRECTS + 1)]
        for index, host in enumerate(hosts):
            later = hosts[index + 1] if index + 1 < len(hosts) else final
            web.serve(f"https://{host}/", status=301, headers={"location": f"https://{later}/"})
        return hosts

    @pytest.mark.parametrize(
        ("arrange", "refusal"),
        [
            (lambda web: web.serve(FINAL_ROBOTS, body="User-agent: *\nDisallow: /\n"),
             "ROBOTS_DISALLOWED"),
            (lambda web: web.serve(FINAL_ROBOTS, status=503), "ROBOTS_UNREACHABLE"),
            (lambda web: web.fail(FINAL_ROBOTS, httpx.ConnectTimeout("slow")),
             "ROBOTS_UNREACHABLE"),
            (lambda web: None, None),
        ],
        ids=["forbids it", "a 503", "no answer", "no robots.txt: allowed"],
    )
    async def test_it_is_asked_about_before_it_is_handed_on(
        self, robots_web: Any, arrange: Callable[[Any], None], refusal: str | None
    ) -> None:
        hosts = self._chain(robots_web, "final.test")
        arrange(robots_web)
        gate = crawl.RobotsGate(user_agent="ua")

        followed = await gate.resolve(f"https://{hosts[0]}/")

        # The browser is given the last location only if its robots.txt was read and allows it.
        assert (followed.url, followed.response, followed.refusal) == (
            "https://final.test/", None, refusal
        )
        assert FINAL_ROBOTS in robots_web.fetched()
        assert "https://final.test/" not in robots_web.fetched()

    async def test_a_location_that_is_not_a_url_is_not_handed_on(self, robots_web: Any) -> None:
        robots_web.serve(SITE, status=301, headers={"location": "http://[::1/x"})
        gate = crawl.RobotsGate(user_agent="ua")

        followed = await gate.resolve(SITE)

        # The hop that sent it is the address robots.txt was asked about, so that is the one.
        assert (followed.url, followed.response, followed.refusal) == (SITE, None, None)


class TestOnlyAnHttpHopIsMade:
    @pytest.mark.parametrize(
        "url",
        [
            "ws://prospect-site.com/robots.txt",
            "wss://prospect-site.com/robots.txt",
            "ftp://prospect-site.com/robots.txt",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/plain,hi",
            "//prospect-site.com/robots.txt",
            "prospect-site.com/robots.txt",
        ],
    )
    async def test_a_hop_that_is_not_http_or_https_is_not_requested_or_looked_up(
        self, robots_web: Any, url: str
    ) -> None:
        # `egress.url_target` has no host to check for anything but http(s), so such a hop
        # would pass the gate's own address check untouched: it is not made at all.
        gate = crawl.RobotsGate(user_agent="ua")
        async with gate._client() as client:  # noqa: SLF001
            assert await gate._exchange(client, "GET", url, 1_000) is None  # noqa: SLF001
        assert robots_web.requests == [] and robots_web.lookups == []

    @pytest.mark.parametrize("location", ["ws://other.test/", "ftp://other.test/x", "file:///etc/passwd"])
    async def test_a_redirect_to_one_ends_as_unreadable_and_is_never_requested(
        self, robots_web: Any, location: str
    ) -> None:
        robots_web.serve(SITE, status=301, headers={"location": location})
        gate = crawl.RobotsGate(user_agent="ua")

        followed = await gate.resolve(SITE)

        assert (followed.url, followed.response, followed.refusal) == (
            location, None, "ROBOTS_UNREACHABLE"
        )
        assert robots_web.fetched() == [f"{SITE}/robots.txt", SITE]


class TestOnlyA2xxBodyIsRead:
    async def test_a_slow_404_is_still_no_file_and_the_crawl_goes_on(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 0.25)
        robots_web.serve_stream(
            ROBOTS, _trickle(b"<html>not found"), status=404, headers={"content-type": "text/html"}
        )
        chromium = FakeChromium(pages={f"{SITE}/": page()})
        monkeypatch.setattr(crawl, "async_playwright", chromium)
        gate = crawl.RobotsGate(user_agent="ua")

        async with asyncio.timeout(5):
            robots = await gate.file(SITE)
            result = await crawl.crawl_site("prospect-site.com")

        # The status was the answer: a page that goes on for ever after it is not read, so
        # it cannot turn "no file, crawl freely" into a robots.txt that never answered.
        assert (robots.status, robots.body) == (404, "")
        assert await gate.readable(SITE) and await gate.allows(f"{SITE}/")
        assert result.ok and chromium.launches == 1

    async def test_a_server_error_page_that_never_ends_is_unreachable_by_its_status(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 0.25)
        robots_web.serve_stream(ROBOTS, _trickle(b"oops"), status=503)

        async with asyncio.timeout(5):
            robots = await crawl.RobotsGate(user_agent="ua").file(SITE)

        assert (robots.status, robots.body) == (503, "")  # its status, not "no answer"

    async def test_a_redirect_whose_body_never_ends_is_followed(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 0.25)
        robots_web.serve(ROBOTS, status=404)
        robots_web.serve_stream(
            f"{SITE}/", _trickle(b"moved"), status=301,
            headers={"location": "https://www.prospect-site.com/"},
        )
        robots_web.serve("https://www.prospect-site.com/", status=200)
        gate = crawl.RobotsGate(user_agent="ua")

        async with asyncio.timeout(5):
            followed = await gate.resolve(f"{SITE}/")

        assert (followed.url, followed.refusal) == ("https://www.prospect-site.com/", None)

    async def test_an_address_check_slower_than_the_deadline_is_no_answer_and_nothing_is_sent(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl, "ROBOTS_REQUEST_DEADLINE_S", 0.25)
        gate = crawl.RobotsGate(user_agent="ua")

        async def never(url: str) -> bool:
            await asyncio.Event().wait()
            return True

        monkeypatch.setattr(gate, "_egress_ok", never)

        async with asyncio.timeout(5):
            robots = await gate.file(SITE)

        assert robots.status is None and robots_web.requests == []


class TestAnUnreadableRobotsTxtInTheScore:
    TF = Dimension.TECHNICAL_FOUNDATION.value

    def test_it_scores_as_if_technical_foundation_were_excluded_and_the_formula_stands(
        self,
    ) -> None:
        unreadable = _score(
            technical_foundation=None, technical_foundation_exclusion="CRAWL_POLICY_UNREADABLE"
        )
        others = [
            _score(technical_foundation=None),
            _score(technical_foundation=None, technical_foundation_exclusion="CRAWL_DISALLOWED"),
            _score(
                technical_foundation=None,
                technical_foundation_exclusion="CRAWL_ADDRESS_NOT_PUBLIC",
            ),
        ]

        for other in others:
            assert unreadable.composite == other.composite
            # The same dimensions are excluded, with the same weights: only the reason differs.
            assert set(unreadable.excluded_dimensions) == set(other.excluded_dimensions)
            assert {d: s.weight for d, s in unreadable.sub_scores.items() if s.included} == {
                d: s.weight for d, s in other.sub_scores.items() if s.included
            }
        assert unreadable.excluded_dimensions[self.TF] == "CRAWL_POLICY_UNREADABLE"
        assert unreadable.composite != _score(technical_foundation=Decimal("0")).composite
        # "had not been audited" would be false: the audit ran.
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in unreadable.degradation_flags
        assert FORMULA_VERSION == "v5"

    def test_every_digest_computed_before_it_is_unchanged_and_it_has_its_own(self) -> None:
        # The pins are the existing ones (test_digests_computed_before_the_new_reason...).
        assert compute_inputs_digest(RESULTS, COMPETITORS, None) == PINNED_DIGESTS["none"]
        assert compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion="CRAWL_DISALLOWED"
        ) == PINNED_DIGESTS["crawl_disallowed"]
        new = compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion="CRAWL_POLICY_UNREADABLE"
        )
        refused = compute_inputs_digest(
            RESULTS, COMPETITORS, None, technical_foundation_exclusion="CRAWL_ADDRESS_NOT_PUBLIC"
        )
        assert new not in PINNED_DIGESTS.values() and new != refused
        # A measured value ignores the reason, as for the others.
        assert _score(
            technical_foundation=Decimal("80"),
            technical_foundation_exclusion="CRAWL_POLICY_UNREADABLE",
        ).inputs_digest == _score(technical_foundation=Decimal("80")).inputs_digest

    @pytest.mark.parametrize(
        ("error_code", "reason"),
        [
            ("ROBOTS_DISALLOWED", "CRAWL_DISALLOWED"),
            ("ROBOTS_UNREACHABLE", "CRAWL_POLICY_UNREADABLE"),
            ("ADDRESS_NOT_PUBLIC", "CRAWL_ADDRESS_NOT_PUBLIC"),
            ("BROWSER_ERROR", "AUDIT_FAILED"),
            (None, "AUDIT_FAILED"),
        ],
    )
    async def test_a_failed_audit_excludes_technical_foundation_under_its_own_reason(
        self, error_code: str | None, reason: str | None
    ) -> None:
        from avp_api.models.technical_audit import AuditStatus
        from avp_api.services import scoring_runner

        audit = SimpleNamespace(
            status=AuditStatus.FAILED, error_code=error_code, technical_foundation=None
        )

        class _Result:
            def scalar_one_or_none(self) -> Any:
                return audit

        class _Session:
            async def execute(self, *_a: Any, **_k: Any) -> _Result:
                return _Result()

        assert await scoring_runner.load_technical_foundation(_Session(), "scan_x") == (
            None, reason
        )

    def test_the_report_and_the_pdf_have_words_for_it_and_none_of_them_says_stay_out(
        self,
    ) -> None:
        from avp_api.services.report_narrative import EXCLUSION_DETAIL, EXCLUSION_REASON
        from avp_api.services.scoring import TECHNICAL_FOUNDATION_EXCLUSIONS

        # Every reason the engine can record has a label and a sentence (the PDF would
        # print the raw code for one that has none).
        for code in TECHNICAL_FOUNDATION_EXCLUSIONS:
            if code != "NOT_YET_MEASURED":
                assert EXCLUSION_REASON[code] and EXCLUSION_DETAIL[code], code
        label = EXCLUSION_REASON["CRAWL_POLICY_UNREADABLE"]
        detail = EXCLUSION_DETAIL["CRAWL_POLICY_UNREADABLE"]
        assert label == "Could not read robots.txt"
        assert detail == (
            "The crawler tried to read the robots.txt of this site, or of an address the site "
            "sent it to, and did not get a usable answer. It obeys robots.txt, so without that "
            "answer it did not read the site. Technical Foundation is left out of the score."
        )
        # True of every way the file can fail to be read, and it says nothing about what
        # the site asks of crawlers: the file was never read.
        for words in ("stay out", "asks crawlers", "forbid", "blocked", "disallow", "not to load"):
            assert words not in f"{label} {detail}".lower(), words
        assert "ROBOTS_UNREACHABLE" not in detail and "CRAWL_POLICY_UNREADABLE" not in detail
        # ...whereas a file that really forbids the page keeps its own words.
        assert "asks crawlers like ours not to load" in EXCLUSION_DETAIL["CRAWL_DISALLOWED"]


class TestAnUnreadableRobotsTxtEndToEnd:
    async def test_the_client_is_still_created_with_the_reason_recorded(
        self, client: AsyncClient, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(ROBOTS, status=503)
        chromium = FakeChromium()
        monkeypatch.setattr(crawl, "async_playwright", chromium)
        await _sign_up(client, "unreadable1@test.example")

        resp = await client.post(f"{BASE}/clients", json={"url": SITE, "classify": True})

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ROBOTS_UNREACHABLE"
        assert body["industry"] is None and chromium.launches == 0
        assert body["crawl"] == EMPTY_CRAWL_SUMMARY

    async def test_the_audit_endpoint_stores_the_code_and_no_finding(
        self, client: AsyncClient, session: Any, robots_web: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from avp_api import ids
        from avp_api.models import Client, Scan
        from avp_api.models.scan import ScanStatus
        from avp_api.services import audit_runner

        site = "https://helpscout.com"
        robots_web.serve(f"{site}/robots.txt", status=503)
        chromium = FakeChromium(pages={f"{site}/": rich_page()})
        monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)
        monkeypatch.setattr(technical_audit, "async_playwright", chromium)
        await _sign_up(client, "unreadable2@test.example")
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        row = await session.get(Client, cid)
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=cid, agency_id=row.agency_id,
            status=ScanStatus.QUEUED,
        )
        session.add(scan)
        await session.commit()

        resp = await client.post(f"{BASE}/scans/{scan.id}/audit")

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert chromium.launches == 0 and robots_web.fetched() == [f"{site}/robots.txt"]
        assert (body["status"], body["errorCode"], body["pagesCrawled"]) == (
            "failed", "ROBOTS_UNREACHABLE", 0
        )
        assert body["technicalFoundation"] is None
        by_key = {c["checkKey"]: (c["status"], c["detailCode"]) for c in body["checks"]}
        assert by_key["robots_txt_present"] == ("not_applicable", "ROBOTS_UNREACHABLE")
        assert by_key["meta_title"] == ("not_applicable", "ROBOTS_UNREACHABLE")
        assert not [s for s, _ in by_key.values() if s in ("warn", "fail", "error")]
        # The Crawler screen says "not readable", which is what happened.
        access = (await client.get(f"{BASE}/clients/{cid}/ai-crawler-access")).json()
        assert access["robotsReadable"] is False
        assert access["summary"]["unknown"] == access["summary"]["total"] > 0
        assert access["summary"]["blocked"] == 0

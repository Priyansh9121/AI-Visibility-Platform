"""The crawler's user agent points at the web app's /crawler page — 2026-09-29.

Until this date it pointed at a placeholder address. It is now built from
`PUBLIC_WEB_BASE_URL`, so a domain change moves it too, and these tests hold
every fetcher to it: the crawl when a site is added, and the technical audit's
browser and its robots.txt and sitemap.xml requests with each scan.

No browser and no network. Playwright is replaced by a fake that records the
context's user agent and then raises the error both fetchers already catch;
httpx by a transport that records each request's headers.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError

from avp_api.config import Settings
from avp_api.services import crawl, technical_audit

WEB = "https://ai-visibility-platform-1.onrender.com"
EXPECTED = (
    "Mozilla/5.0 (compatible; AVPScanBot/0.1; "
    "+https://ai-visibility-platform-1.onrender.com/crawler) Playwright/Chromium"
)


def _settings(web: str) -> Settings:
    return Settings(_env_file=None, public_web_base_url=web)


class TestTheString:
    def test_it_points_at_the_crawler_page_on_the_configured_web_address(self) -> None:
        assert crawl.crawler_user_agent(_settings(WEB)) == EXPECTED
        assert crawl.crawler_user_agent(_settings(f"{WEB}/")) == EXPECTED

    def test_a_new_domain_moves_it_with_no_code_change(self) -> None:
        agent = crawl.crawler_user_agent(_settings("https://visibility.example.com"))
        assert "+https://visibility.example.com/crawler)" in agent
        assert "onrender" not in agent

    def test_no_address_is_typed_into_the_template(self) -> None:
        assert "://" not in crawl.USER_AGENT_TEMPLATE
        assert crawl.USER_AGENT_TEMPLATE.count("{info_url}") == 1


class _Browser:
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    async def new_context(self, **kwargs: Any) -> None:
        self.seen.append(kwargs["user_agent"])
        raise PlaywrightError("stopped: the context's user agent is all this test reads")

    async def close(self) -> None:
        return None


class _Playwright:
    def __init__(self, seen: list[str]) -> None:
        browser = _Browser(seen)

        class _Chromium:
            async def launch(self, **kwargs: Any) -> _Browser:  # noqa: ARG002
                return browser

        self.chromium = _Chromium()

    async def __aenter__(self) -> _Playwright:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class TestEveryFetcherSendsIt:
    @pytest.fixture(autouse=True)
    def configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The service's own settings, as `get_settings()` hands them out.
        monkeypatch.setattr(crawl, "get_settings", lambda: _settings(WEB))

    async def test_the_crawl_when_a_site_is_added(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []
        monkeypatch.setattr(crawl, "async_playwright", lambda: _Playwright(seen))

        result = await crawl.crawl_site("https://client.example")

        assert result.error_code == "BROWSER_ERROR"  # the fake stopped it, as designed
        assert seen == [EXPECTED]

    async def test_the_audit_browser_and_its_robots_and_sitemap_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        requested: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append((request.url.path, request.headers["user-agent"]))
            return httpx.Response(404)

        real = httpx.AsyncClient

        def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(technical_audit.httpx, "AsyncClient", client)
        monkeypatch.setattr(technical_audit, "async_playwright", lambda: _Playwright(seen))

        signals = await technical_audit.audit_site("https://client.example")

        assert signals.error_code == "BROWSER_ERROR"
        assert requested == [("/robots.txt", EXPECTED), ("/sitemap.xml", EXPECTED)]
        assert seen == [EXPECTED]

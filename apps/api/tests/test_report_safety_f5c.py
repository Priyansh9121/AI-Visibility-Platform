"""F5c: three report safety fixes.

1. The PDF is rendered on a worker thread, not the event loop, at most
   `pdf_render_max_concurrency` at once, a wait past `pdf_render_queue_timeout_seconds` is a 503,
   and no database connection is held while it waits or renders.
2. `POST /reports/shared` and `/reports/shared/pdf` are limited per source IP (own budgets, one
   window), 429 with `Retry-After`, and FAIL OPEN when Redis cannot say.
3. A URL in the report (a cited page, the audited URL, the logo) is exposed only when its scheme
   is http or https, on the signed-in report, the shared report, the PDF's input and the CSV.
"""

# ruff: noqa: E501, F811  (long parametrised lines; fixtures imported from test_share_link)

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from redis.asyncio import Redis

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.deps import redis_client
from avp_api.errors import ServiceUnavailable
from avp_api.schemas.report import CitedDomainOut, ReportAgencyOut, ReportAuditOut
from avp_api.services import report_pdf as pdf_service
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.links import http_url_or_none
from test_share_link import _sign_up, stub_engines  # noqa: F401

BASE = "/api/v1"
_AUDIT = {"status": "ok", "passed": 0, "warned": 0, "failed": 0, "not_applicable": 0, "findings": []}
IP_HEADER = "X-Test-Client-IP"
IP = "203.0.113.77"


@pytest.fixture
def ip_header(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)


def _hit(client: AsyncClient, route: str, token: str = "x" * 32, ip: str = IP):  # noqa: ANN202
    return client.post(f"{BASE}{route}", json={"token": token}, headers={IP_HEADER: ip})


async def _shared_token(client: AsyncClient, stub_engines) -> tuple[str, str]:  # noqa: ANN001
    from test_share_link import _scored_scan

    await _sign_up(client, "Safety Agency", "safety@f5c.example")
    sid = await _scored_scan(client, stub_engines)
    token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
    return sid, token


# --- 1. PDF off the event loop, bounded ---------------------------------------------------------


class TestPdfRenderingIsOffTheLoopAndBounded:
    def test_defaults(self) -> None:
        s = Settings(_env_file=None)
        assert (s.pdf_render_max_concurrency, s.pdf_render_queue_timeout_seconds) == (1, 20.0)

    async def test_both_routes_render_on_a_thread_that_is_not_the_loops_and_hold_no_connection(
        self, client: AsyncClient, engine, stub_engines, monkeypatch  # noqa: ANN001
    ) -> None:
        loop_thread = threading.get_ident()
        seen: list[tuple[int, int]] = []
        done = threading.Event()

        def fake_render(report):  # noqa: ANN001, ANN202
            # Runs on the executor thread; the pool's check-out count is read there too.
            seen.append((threading.get_ident(), engine.pool.checkedout()))
            done.set()
            return b"%PDF-1.4 stub"

        monkeypatch.setattr(pdf_service, "render_report_pdf", fake_render)
        sid, token = await _shared_token(client, stub_engines)

        assert (await client.get(f"{BASE}/scans/{sid}/report.pdf")).status_code == 200
        client.cookies.clear()
        assert (await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})).status_code == 200

        assert len(seen) == 2
        for thread_id, checked_out in seen:
            assert thread_id != loop_thread
            assert checked_out == 0, "a database connection was held while the PDF rendered"

    async def test_at_most_the_configured_number_render_at_once(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "pdf_render_max_concurrency", 2)
        monkeypatch.setattr(settings, "pdf_render_queue_timeout_seconds", 30)
        lock = threading.Lock()
        state = {"now": 0, "peak": 0}

        def fake_render(report):  # noqa: ANN001, ANN202
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.15)
            with lock:
                state["now"] -= 1
            return b"%PDF"

        monkeypatch.setattr(pdf_service, "render_report_pdf", fake_render)
        out = await asyncio.gather(
            *(pdf_service.render_report_pdf_async(None, settings) for _ in range(6))  # type: ignore[arg-type]
        )
        assert out == [b"%PDF"] * 6
        assert state["peak"] == 2

    async def test_a_wait_past_the_timeout_is_a_503_and_the_slot_is_given_back(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "pdf_render_max_concurrency", 1)
        monkeypatch.setattr(settings, "pdf_render_queue_timeout_seconds", 0.2)
        release = threading.Event()

        def fake_render(report):  # noqa: ANN001, ANN202
            release.wait(5)
            return b"%PDF"

        monkeypatch.setattr(pdf_service, "render_report_pdf", fake_render)
        first = asyncio.create_task(pdf_service.render_report_pdf_async(None, settings))  # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        with pytest.raises(ServiceUnavailable):
            await pdf_service.render_report_pdf_async(None, settings)  # type: ignore[arg-type]
        release.set()
        assert await first == b"%PDF"
        assert await pdf_service.render_report_pdf_async(None, settings) == b"%PDF"  # type: ignore[arg-type]

    async def test_the_route_answers_503_when_every_slot_stays_taken(
        self, client: AsyncClient, settings: Settings, stub_engines, monkeypatch  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(settings, "pdf_render_max_concurrency", 1)
        monkeypatch.setattr(settings, "pdf_render_queue_timeout_seconds", 0.2)
        release = threading.Event()

        def fake_render(report):  # noqa: ANN001, ANN202
            release.wait(5)
            return b"%PDF"

        monkeypatch.setattr(pdf_service, "render_report_pdf", fake_render)
        sid, _ = await _shared_token(client, stub_engines)
        first = asyncio.create_task(client.get(f"{BASE}/scans/{sid}/report.pdf"))
        await asyncio.sleep(0.5)
        second = await client.get(f"{BASE}/scans/{sid}/report.pdf")
        release.set()
        assert second.status_code == 503, second.text
        assert (await first).status_code == 200


class TestPdfRequestsInFlightAreBounded:
    async def test_past_the_cap_the_next_request_is_a_503_before_any_lookup(
        self, client: AsyncClient, settings: Settings, ip_header, stub_engines, monkeypatch  # noqa: ANN001
    ) -> None:
        import contextlib

        from avp_api.services import report as report_service
        from avp_api.services import share as share_service

        monkeypatch.setattr(settings, "pdf_render_max_concurrency", 1)
        sid, token = await _shared_token(client, stub_engines)
        built: list[str] = []
        looked: list[str] = []
        real_build, real_lookup = report_service.build_report, share_service.scan_for_share_token

        async def build(*a, **k):  # noqa: ANN002, ANN003, ANN202
            built.append("x")
            return await real_build(*a, **k)

        async def lookup(*a, **k):  # noqa: ANN002, ANN003, ANN202
            looked.append("x")
            return await real_lookup(*a, **k)

        monkeypatch.setattr(report_service, "build_report", build)
        monkeypatch.setattr(share_service, "scan_for_share_token", lookup)
        cap = 1 * pdf_service.PDF_IN_FLIGHT_FACTOR
        async with contextlib.AsyncExitStack() as stack:
            for _ in range(cap):
                await stack.enter_async_context(pdf_service.pdf_in_flight(settings))
            signed = await client.get(f"{BASE}/scans/{sid}/report.pdf")
            shared = await _hit(client, "/reports/shared/pdf", token=token)
            assert (signed.status_code, shared.status_code) == (503, 503)
        assert built == [] and looked == []
        # Released: the next request is served.
        assert (await client.get(f"{BASE}/scans/{sid}/report.pdf")).status_code == 200

    async def test_a_request_that_fails_gives_its_place_back(
        self, client: AsyncClient, settings: Settings, ip_header  # noqa: ANN001
    ) -> None:
        assert pdf_service._in_flight == 0  # noqa: SLF001
        for _ in range(10):
            assert (await _hit(client, "/reports/shared/pdf")).status_code == 404
        assert pdf_service._in_flight == 0  # noqa: SLF001


# --- 2. A per-IP limit on the public share routes -----------------------------------------------


class TestSharedRoutesAreLimitedPerIp:
    def test_defaults(self) -> None:
        s = Settings(_env_file=None)
        assert (s.shared_report_limit_per_ip, s.shared_pdf_limit_per_ip, s.shared_window_seconds) == (
            60, 10, 600,
        )

    @pytest.mark.parametrize(
        ("route", "setting"),
        [
            ("/reports/shared", "shared_report_limit_per_ip"),
            ("/reports/shared/pdf", "shared_pdf_limit_per_ip"),
        ],
    )
    async def test_the_request_past_the_limit_is_a_429_with_retry_after(
        self, client: AsyncClient, settings: Settings, ip_header, monkeypatch, route, setting  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(settings, setting, 3)
        for i in range(3):
            assert (await _hit(client, route)).status_code == 404, i
        with structlog.testing.capture_logs() as logs:
            refused = await _hit(client, route)
        assert refused.status_code == 429, refused.text
        assert refused.json()["type"].endswith("/problems/rate-limited")
        assert 0 < int(refused.headers["Retry-After"]) <= settings.shared_window_seconds
        assert IP not in repr(logs)
        # Another source has its own budget.
        assert (await _hit(client, route, ip="203.0.113.78")).status_code == 404

    async def test_the_two_routes_have_separate_budgets(
        self, client: AsyncClient, settings: Settings, ip_header, monkeypatch  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(settings, "shared_pdf_limit_per_ip", 1)
        assert (await _hit(client, "/reports/shared/pdf")).status_code == 404
        assert (await _hit(client, "/reports/shared/pdf")).status_code == 429
        assert (await _hit(client, "/reports/shared")).status_code == 404

    async def test_a_refusal_comes_before_the_token_lookup(
        self, client: AsyncClient, settings: Settings, ip_header, monkeypatch  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(settings, "shared_report_limit_per_ip", 1)
        await _hit(client, "/reports/shared")
        calls: list[str] = []
        from avp_api.services import share as share_service

        real = share_service.scan_for_share_token

        async def spy(db, token):  # noqa: ANN001, ANN202
            calls.append(token)
            return await real(db, token)

        monkeypatch.setattr(share_service, "scan_for_share_token", spy)
        assert (await _hit(client, "/reports/shared")).status_code == 429
        assert calls == []

    async def test_only_the_ip_keys_are_written_and_each_has_the_window_as_its_ttl(
        self, client: AsyncClient, settings: Settings, ip_header  # noqa: ANN001
    ) -> None:
        redis = redis_module.get_redis(settings)
        await redis.flushdb()
        await _hit(client, "/reports/shared")
        await _hit(client, "/reports/shared/pdf")
        keys = sorted([k async for k in redis.scan_iter("rl:shared_*")])
        assert len(keys) == 2
        assert keys[0].startswith("rl:shared_pdf:ip:") and keys[1].startswith("rl:shared_report:ip:")
        for key in keys:
            assert 0 < await redis.pttl(key) <= settings.shared_window_seconds * 1000
            assert IP not in key

    async def test_the_pdf_route_fails_closed_when_redis_is_down(
        self, client: AsyncClient, ip_header  # noqa: ANN001
    ) -> None:
        app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
        app.dependency_overrides[redis_client] = lambda: dead
        try:
            resp = await _hit(client, "/reports/shared/pdf")
        finally:
            await dead.aclose()
        assert resp.status_code == 503, resp.text

    async def test_redis_down_fails_open_for_the_json_report_with_a_warning_and_no_address(
        self, client: AsyncClient, ip_header  # noqa: ANN001
    ) -> None:
        app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
        app.dependency_overrides[redis_client] = lambda: dead
        try:
            with structlog.testing.capture_logs() as logs:
                resp = await _hit(client, "/reports/shared")
        finally:
            await dead.aclose()
        assert resp.status_code == 404, resp.text  # past the limiter: an unknown token
        warned = [e for e in logs if e["event"] == "shared_report.rate_limit.unavailable"]
        assert warned == [
            {
                "event": "shared_report.rate_limit.unavailable",
                "log_level": "warning",
                "action": "shared_report",
            }
        ]
        assert IP not in repr(logs)


# --- 3. Report links only when they are http(s) -------------------------------------------------

REFUSED = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "DaTa:text/html,x",
    "vbscript:msgbox(1)",
    "VBScript:msgbox(1)",
    " javascript:alert(1)",
    "\tjavascript:alert(1)",
    "\x01javascript:alert(1)",
    "\x00http://example.com",
    " http://example.com",
    "\nhttps://example.com",
    "java\nscript:alert(1)",
    "http://exa mple.com",
    "http://example.com/a\nb",
    "http:///nohost",
    "https://",
    "//example.com/x",
    "/relative/path",
    "example.com",
    "ftp://example.com/x",
    "file:///etc/passwd",
    "",
    "   ",
    # L1/L4: a backslash anywhere, userinfo, a non-ASCII fold of the scheme, C1/format characters.
    "https://evil.example\\@www.g2.com/x",
    "https://g2.com/a\\b",
    "https://g2.com@evil.com/x",
    "https://user:pw@g2.com/x",
    "httpſ://x",
    "httpſs://x",
    "https://g2.com/a\u0085b",
    "https://g2.com/a\ufeffb",
    "https://g2.com/a\u00a0b",
    "https://g2.com/a\u2028b",
]
ACCEPTED = [
    "https://g2.com/a@b",
    "https://g2.com/é",
    "https://g2.com?next=a@b",
    "http://example.com",
    "https://example.com/a/b?q=1#frag",
    "HTTPS://Example.com/Path",
    "Http://example.com:8080/x",
]


class TestUrlsAreHttpOrNull:
    @pytest.mark.parametrize("value", REFUSED)
    def test_a_refused_value_is_none(self, value: str) -> None:
        assert http_url_or_none(value) is None

    @pytest.mark.parametrize("value", ACCEPTED)
    def test_http_and_https_pass_unchanged(self, value: str) -> None:
        assert http_url_or_none(value) == value

    def test_a_non_string_is_none(self) -> None:
        assert http_url_or_none(None) is None
        assert http_url_or_none(5) is None

    @pytest.mark.parametrize("value", REFUSED)
    def test_every_url_field_of_the_report_refuses_it(self, value: str) -> None:
        assert CitedDomainOut(domain="x.test", citations=1, cites_subject=False, sample_url=value).sample_url is None
        assert ReportAgencyOut(id="a", name="A", slug="a", logo_url=value).logo_url is None
        assert ReportAuditOut(**_AUDIT, url_audited=value).url_audited is None

    @pytest.mark.parametrize("value", ACCEPTED)
    def test_every_url_field_of_the_report_keeps_http(self, value: str) -> None:
        assert CitedDomainOut(domain="x.test", citations=1, cites_subject=False, sample_url=value).sample_url == value
        assert ReportAgencyOut(id="a", name="A", slug="a", logo_url=value).logo_url == value
        assert ReportAuditOut(**_AUDIT, url_audited=value).url_audited == value

    async def test_a_domain_row_links_the_first_citation_that_is_http_not_the_first_url(
        self, client: AsyncClient, stub_engines, monkeypatch  # noqa: ANN001
    ) -> None:
        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine, engine_version="stub", prompt_text=prompt,
                    text="Zendesk is popular. Help Scout is simpler.",
                    citations=[
                        CitedSource(url="javascript:alert(1)", domain="mixed.example", position=1),
                        CitedSource(url="https://mixed.example/ok", domain="mixed.example", position=2),
                    ],
                    latency_ms=5,
                )
                for engine in engines
            ]

        await _sign_up(client, "Safety Agency", "safety3@f5c.example")
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        stub_engines(n_prompts=4)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
        body = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        rows = [
            r for key in ("subjectCitedDomains", "competitorCitedDomains", "unclaimedCitedDomains")
            for r in body["proof"][key] if r["domain"] == "mixed.example"
        ]
        assert rows and all(r["sampleUrl"] == "https://mixed.example/ok" for r in rows)

    def test_the_signed_in_scan_citation_api_blanks_other_schemes_too(self) -> None:
        from avp_api.schemas.scan import CitationOut

        def make(url):  # noqa: ANN001, ANN202
            return CitationOut(
                id="c", source_domain="x.test", source_url=url, source_type="other",
                cites_subject=False,
            )

        assert make("javascript:alert(1)").source_url is None
        assert make("https://evil.example\\@g2.com/").source_url is None
        assert make(None).source_url is None
        assert make("https://g2.com/x").source_url == "https://g2.com/x"

    async def test_a_javascript_citation_reaches_no_report_surface_and_a_https_one_does(
        self, client: AsyncClient, settings: Settings, stub_engines, monkeypatch  # noqa: ANN001
    ) -> None:
        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            out = []
            for engine in engines:
                cites = [
                    CitedSource(url="javascript:alert(1)", domain="evil.example", position=1),
                    CitedSource(url="https://g2.com/x", domain="g2.com", position=2),
                ]
                out.append(EngineAnswer(
                    engine=engine, engine_version="stub", prompt_text=prompt,
                    text="Zendesk is popular. Help Scout is simpler.",
                    citations=cites, latency_ms=5))
            return out

        handed: list[Any] = []
        real_render = pdf_service.render_report_pdf

        def capture(report):  # noqa: ANN001, ANN202
            handed.append(report)
            return real_render(report)

        monkeypatch.setattr(settings, "report_csv_enabled", True)
        monkeypatch.setattr(pdf_service, "render_report_pdf", capture)
        await _sign_up(client, "Safety Agency", "safety2@f5c.example")
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        stub_engines(n_prompts=4)
        # The stub's answers carry no hostile link; replace them, after the install.
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        sid2 = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert (await client.post(f"{BASE}/scans/{sid2}/score")).status_code == 201
        token2 = (await client.post(f"{BASE}/scans/{sid2}/share")).json()["token"]

        signed = await client.get(f"{BASE}/scans/{sid2}/report")
        csv = await client.get(f"{BASE}/scans/{sid2}/report.csv")
        assert (await client.get(f"{BASE}/scans/{sid2}/report.pdf")).status_code == 200
        client.cookies.clear()
        shared = await client.post(f"{BASE}/reports/shared", json={"token": token2})
        assert (await client.post(f"{BASE}/reports/shared/pdf", json={"token": token2})).status_code == 200

        for resp in (signed, shared):
            assert resp.status_code == 200, resp.text
            assert "javascript:" not in resp.text.lower()
        assert csv.status_code == 200 and "javascript:" not in csv.text.lower()
        for report in handed:
            assert "javascript:" not in report.model_dump_json().lower()
        for body in (signed.json(), shared.json()):
            rows = [
                row
                for key in ("subjectCitedDomains", "competitorCitedDomains", "unclaimedCitedDomains")
                for row in body["proof"][key]
            ]
            evil = [row for row in rows if row["domain"] == "evil.example"]
            good = [row for row in rows if row["domain"] == "g2.com"]
            # The domain stays (a fact); only the link is gone. An https link is kept.
            assert evil and all(row["sampleUrl"] is None for row in evil), rows
            assert good and all(row["sampleUrl"] == "https://g2.com/x" for row in good)

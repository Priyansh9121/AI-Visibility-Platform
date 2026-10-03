"""One site read per agency, and four agencies, at a time on the request routes — M1 rounds 2, 3.

Three routes start a browser run inside the request: `POST /clients` with
classification on, `POST /clients/{clientId}/reclassify` and `POST /scans/{scanId}/audit`.
Each waiter holds a pooled database connection (the pool is 10 + 5), and the browser
is one per process (`browser_slot`), so one agency queueing runs, or enough agencies
at once, could block every route. An agency therefore has ONE run queued or running at
a time across the three, only `MAX_REQUEST_READS` agencies at once, and a further one is
`429 rate-limited` with `Retry-After`. What this file holds the routes to:

  * the second read for the same agency is refused, on every pairing of the three
    routes, while another agency's is not; the fifth agency is refused while four are
    inside, with the sentence that says nothing about any agency;
  * the agency check comes first: another agency's id stays a 404, busy or not;
  * a refused request creates nothing, and `classify: false` is not a read;
  * a read that waited its whole bound for the browser (round 3) WRITES NOTHING and
    answers the same retryable 429: no client is created, a reclassified client keeps
    every column it had, a hand-run audit leaves the stored audit as it was;
  * the agency is released on every way out: a return, an exception, a cancellation,
    and a read that was refused as busy;
  * a scan's own phase-6 audit is neither counted nor refused by either limit, asks the
    slot for the scan's longer bound where a request's audit asks for a request's, and
    still records `BROWSER_BUSY` when its wait runs out;
  * the slot's log lines carry the id of the client or scan the read was for.

The unit (`one_run_per_agency`) is in test_browser_slot.py. The crawl and the audit are
stood in for at the boundary the routes call (`intake.crawl_site`, `audit_runner.audit_site`)
where a test needs a read to be in flight; where it needs the real fetchers, the browser
slot is held by the test and Playwright is never reached.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload
from structlog.testing import capture_logs

from avp_api import ids
from avp_api.errors import RateLimited
from avp_api.models import ClassificationStatus, Client, Scan, ScanStatus, TechnicalAudit
from avp_api.models.technical_audit import AuditStatus
from avp_api.services import (
    audit_runner,
    browser_slot,
    crawl,
    technical_audit,
)
from avp_api.services import intake as intake_service
from avp_api.services.ai_crawlers import evaluate_robots
from avp_api.services.classify import ClassificationOutcome
from avp_api.services.crawl import CrawlResult, CrawlSignals
from avp_api.services.technical_audit import AuditSignals

BASE = "/api/v1"

# The two sentences a request can be refused with. Both are a `429 rate-limited` with
# `Retry-After: 60`; the first is about this agency, the second says nothing about any.
AGENCY_BUSY = "Your agency already has a site being read. Try again in a minute."
CRAWLER_BUSY = "The crawler is busy with other pages right now. Try again in a minute."

CLASSIFIED = ClassificationOutcome(
    status="classified",
    industry="dental practice",
    niche="cosmetic and implant dentistry",
    brand_name="Northaven Dental",
    confidence="high",
    confidence_score=Decimal("0.920"),
)


async def _sign_up(client: AsyncClient, email: str = "reads@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Page Reads Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@asynccontextmanager
async def _another_agency(client: AsyncClient, email: str) -> AsyncIterator[AsyncClient]:
    """A signed-in client of a second agency, over the same app and datastores."""
    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": client.headers["Origin"]},
    ) as other:
        await _sign_up(other, email)
        yield other


async def _make_client(client: AsyncClient, domain: str) -> str:
    """A client with no read: `classify: false` never starts a browser."""
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_scan(session, client_id: str) -> str:  # noqa: ANN001
    """A queued scan row for a client, which is all `POST /scans/{id}/audit` needs."""
    row = await session.get(Client, client_id)
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=row.agency_id,
        status=ScanStatus.QUEUED,
    )
    session.add(scan)
    await session.commit()
    return scan.id


# ---------------------------------------------------------------------------
# the reads, in flight on demand
# ---------------------------------------------------------------------------


class PageReads:
    """The page reads of one test, standing in for the crawl and the audit.

    A read of a domain that starts with "held" begins, says so (`began`), and waits for
    `release`: it is a run in flight. Any other read returns at once, so it is the
    request that is being tested and not the read. `explode` makes a read raise.
    `audit_waits` is what each audit was told about its wait for the browser, and
    `crawl_ids` / `audit_ids` the id each read was given for the browser slot's log lines.
    """

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self._began: dict[str, asyncio.Event] = {}
        self.explode: set[str] = set()
        self.audit_waits: list[Any] = []
        self.crawl_ids: list[Any] = []
        self.audit_ids: list[Any] = []

    def began(self, domain: str) -> asyncio.Event:
        return self._began.setdefault(domain, asyncio.Event())

    async def read(self, domain: str) -> None:
        self.began(domain).set()
        if domain.startswith("held"):
            await self.release.wait()
        if domain in self.explode:
            raise RuntimeError("the read blew up")


@pytest.fixture
def page_reads(monkeypatch: pytest.MonkeyPatch) -> PageReads:
    reads = PageReads()

    async def crawl_site(url: str, **kwargs: Any) -> CrawlResult:
        domain = url.split("://", 1)[-1]
        reads.crawl_ids.append(kwargs.get("client_id", "not passed"))
        await reads.read(domain)
        return CrawlResult(
            signals=CrawlSignals(
                final_url=url, registrable_domain=domain, word_count=250, pages_fetched=1,
                urls_fetched=[url],
            ),
            text_extract="stub page text",
        )

    async def classify(crawled: CrawlResult, **kwargs: Any) -> ClassificationOutcome:  # noqa: ARG001
        return CLASSIFIED

    async def audit_site(url: str, **kwargs: Any) -> AuditSignals:
        domain = url.split("://", 1)[-1]
        reads.audit_waits.append(kwargs.get("slot_wait_s", "not passed"))
        reads.audit_ids.append(kwargs.get("scan_id", "not passed"))
        await reads.read(domain)
        return AuditSignals(
            url=url, domain=domain, http_status=200, ok=True, schema_types=["Organization"],
            has_organization_schema=True, has_title=True, has_meta_description=True,
            canonical_present=True, robots_txt_present=True, robots_allows_crawl=True,
            has_sitemap=True, is_indexable=True, h1_count=1, word_count=900, content_age_days=10,
            ai_crawler_access=evaluate_robots("User-agent: *\nAllow: /\n"),
        )

    monkeypatch.setattr(intake_service, "crawl_site", crawl_site)
    monkeypatch.setattr(intake_service, "classify", classify)
    monkeypatch.setattr(audit_runner, "audit_site", audit_site)
    return reads


@asynccontextmanager
async def read_in_flight(
    reads: PageReads, domain: str, call: Callable[[], Awaitable[Any]]
) -> AsyncIterator[asyncio.Task[Any]]:
    """`call()` sent as a request that is inside its read of `domain`, for the body.

    Released and finished on the way out, whatever the body did, so no test leaves a
    request hanging. The request's outcome is the task's `result()` afterwards.
    """
    task = asyncio.ensure_future(call())
    try:
        await asyncio.wait_for(reads.began(domain).wait(), 10)
        yield task
    finally:
        reads.release.set()
        await asyncio.gather(task, return_exceptions=True)


def _is_a_429(resp: Any, detail: str) -> None:
    assert resp.status_code == 429, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers["retry-after"] == "60"
    body = resp.json()
    assert body["type"] == "/problems/rate-limited"
    assert body["status"] == 429
    assert body["detail"] == detail


def _is_the_agency_busy_429(resp: Any) -> None:
    _is_a_429(resp, AGENCY_BUSY)


def _is_the_crawler_busy_429(resp: Any) -> None:
    _is_a_429(resp, CRAWLER_BUSY)


# ---------------------------------------------------------------------------
# the three routes, as calls
# ---------------------------------------------------------------------------


class Routes:
    """What the three routes are called with: a held target and a free one for each."""

    client: AsyncClient
    held_client_id: str
    free_client_id: str
    held_scan_id: str
    free_scan_id: str

    def call(self, route: str, *, held: bool) -> Callable[[], Awaitable[Any]]:
        if route == "create":
            url = "held-new.com" if held else "free-new.com"
            return lambda: self.client.post(
                f"{BASE}/clients", json={"url": url, "classify": True}
            )
        if route == "reclassify":
            cid = self.held_client_id if held else self.free_client_id
            return lambda: self.client.post(f"{BASE}/clients/{cid}/reclassify")
        sid = self.held_scan_id if held else self.free_scan_id
        return lambda: self.client.post(f"{BASE}/scans/{sid}/audit")

    @staticmethod
    def held_domain(route: str) -> str:
        return "held-new.com" if route == "create" else "held-a.com"

    SUCCESS = {"create": 201, "reclassify": 200, "audit": 201}


@pytest_asyncio.fixture
async def routes(client: AsyncClient, session, page_reads: PageReads) -> Routes:  # noqa: ANN001
    await _sign_up(client)
    s = Routes()
    s.client = client
    s.held_client_id = await _make_client(client, "held-a.com")
    s.free_client_id = await _make_client(client, "free-b.com")
    s.held_scan_id = await _make_scan(session, s.held_client_id)
    s.free_scan_id = await _make_scan(session, s.free_client_id)
    return s


ROUTES = ["create", "reclassify", "audit"]


class TestOnePageReadPerAgency:
    @pytest.mark.parametrize("second", ROUTES)
    @pytest.mark.parametrize("first", ROUTES)
    async def test_a_second_read_for_the_agency_is_a_429_whichever_two_routes_they_are(
        self, routes: Routes, page_reads: PageReads, first: str, second: str
    ) -> None:
        async with read_in_flight(
            page_reads, Routes.held_domain(first), routes.call(first, held=True)
        ) as in_flight:
            refused = await routes.call(second, held=False)()

            _is_the_agency_busy_429(refused)  # the one count is shared by all three routes
            assert not in_flight.done()  # and the refusal did nothing to the read in flight

        assert in_flight.result().status_code == Routes.SUCCESS[first]

    @pytest.mark.parametrize("route", ROUTES)
    async def test_with_nothing_in_flight_the_same_request_is_served(
        self, routes: Routes, route: str
    ) -> None:
        """The control for the 429s above: the refusal is the count, not the request."""
        resp = await routes.call(route, held=False)()

        assert resp.status_code == Routes.SUCCESS[route], resp.text

    async def test_a_refused_request_creates_nothing(
        self, routes: Routes, page_reads: PageReads
    ) -> None:
        async with read_in_flight(
            page_reads, "held-new.com", routes.call("create", held=True)
        ):
            refused = await routes.call("create", held=False)()
            _is_the_agency_busy_429(refused)

        listed = (await routes.client.get(f"{BASE}/clients")).json()["data"]
        domains = {c["domain"] for c in listed}
        assert "free-new.com" not in domains  # refused before anything was made
        assert "held-new.com" in domains  # the read that was running finished normally

    async def test_another_agencys_read_is_not_refused(
        self, client: AsyncClient, routes: Routes, page_reads: PageReads
    ) -> None:
        async with (
            _another_agency(client, "other@test.example") as other,
            read_in_flight(page_reads, "held-new.com", routes.call("create", held=True)),
        ):
            created = await other.post(
                f"{BASE}/clients", json={"url": "other-new.com", "classify": True}
            )

            assert created.status_code == 201, created.text
            other_id = created.json()["id"]
            reclassified = await other.post(f"{BASE}/clients/{other_id}/reclassify")
            assert reclassified.status_code == 200, reclassified.text

    async def test_the_agency_check_comes_first_busy_or_not(
        self, client: AsyncClient, session, routes: Routes, page_reads: PageReads  # noqa: ANN001
    ) -> None:
        """Another agency's id is a 404, never a 429, while this agency is busy."""
        async with _another_agency(client, "other@test.example") as other:
            their_client = await _make_client(other, "theirs.com")
            their_scan = await _make_scan(session, their_client)
            async with read_in_flight(
                page_reads, "held-new.com", routes.call("create", held=True)
            ):
                assert (
                    await client.post(f"{BASE}/clients/{their_client}/reclassify")
                ).status_code == 404
                assert (await client.post(f"{BASE}/scans/{their_scan}/audit")).status_code == 404
                unknown = ids.new_id(ids.CLIENT)
                missing = await client.post(f"{BASE}/clients/{unknown}/reclassify")
                assert missing.status_code == 404
                # ... and this agency's own targets are the ones the 429 is for.
                _is_the_agency_busy_429(await routes.call("reclassify", held=False)())

    async def test_a_client_made_without_classification_is_not_a_read(
        self, routes: Routes, page_reads: PageReads
    ) -> None:
        async with read_in_flight(
            page_reads, "held-new.com", routes.call("create", held=True)
        ):
            created = await routes.client.post(
                f"{BASE}/clients", json={"url": "no-read.com", "classify": False}
            )

            assert created.status_code == 201, created.text
            # ... and being served, it did not take the agency's place either: the read
            # that is in flight is still the only one, so the next real read is refused.
            _is_the_agency_busy_429(await routes.call("create", held=False)())


class TestTheAgencyIsReleasedOnEveryWayOut:
    async def test_after_a_read_that_returned(self, routes: Routes) -> None:
        first = await routes.call("create", held=False)()
        second = await routes.client.post(
            f"{BASE}/clients", json={"url": "free-third.com", "classify": True}
        )

        assert (first.status_code, second.status_code) == (201, 201)

    async def test_after_a_read_that_blew_up(
        self, routes: Routes, page_reads: PageReads
    ) -> None:
        page_reads.explode.add("free-boom.com")

        with pytest.raises(RuntimeError, match="blew up"):
            await routes.client.post(
                f"{BASE}/clients", json={"url": "free-boom.com", "classify": True}
            )
        after = await routes.call("create", held=False)()

        assert after.status_code == 201, after.text  # not a 429

    async def test_after_a_request_that_was_cancelled_mid_read(
        self, routes: Routes, page_reads: PageReads
    ) -> None:
        """A caller who went away: the cancellation runs through the handler's `finally`."""
        task = asyncio.ensure_future(routes.call("create", held=True)())
        try:
            await asyncio.wait_for(page_reads.began("held-new.com").wait(), 10)
            refused = await routes.call("reclassify", held=False)()
            _is_the_agency_busy_429(refused)  # it counted while it was in flight

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            after = await routes.call("create", held=False)()
        finally:
            page_reads.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert after.status_code == 201, after.text


@pytest.fixture
def busy_browser(monkeypatch: pytest.MonkeyPatch, robots_web: Any) -> Callable[[], Any]:
    """The real fetchers, and a browser another run holds: `hold()` is that other run.

    A request's wait is cut to a tenth of a second, and Playwright, which a read that never
    got the slot must never reach, fails the test if it is. `audit_runner` gets the real
    `audit_site`; a test that wants a stub (the stored audit below) asks for `page_reads`
    AFTER this and puts the real one back itself. The holder is in the test's own task, so
    requests are sent under it through `sent`, held to a deadline: with no bound on the wait
    they would sit behind the holder for ever.
    """
    monkeypatch.setattr(browser_slot, "REQUEST_WAIT_S", 0.1)

    def launched(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        pytest.fail("a browser was started for a read that never got the slot")

    monkeypatch.setattr(crawl, "async_playwright", launched)
    monkeypatch.setattr(technical_audit, "async_playwright", launched)
    monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)
    return lambda: browser_slot.browser_slot("audit")


async def sent(client: AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    """One request, held to a deadline of the test's own (see `busy_browser`)."""
    return await asyncio.wait_for(client.request(method, f"{BASE}{path}", **kwargs), 10)


def _columns(row: Any) -> dict[str, Any]:
    """Every mapped column of a row, by attribute: what 'column by column' compares."""
    return {attr.key: getattr(row, attr.key) for attr in sa_inspect(type(row)).column_attrs}


def _fresh_session(engine) -> Any:  # noqa: ANN001
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()


async def _client_rows(engine) -> list[dict[str, Any]]:  # noqa: ANN001
    async with _fresh_session(engine) as s:
        return [_columns(r) for r in (await s.execute(select(Client))).scalars()]


async def _client_columns(engine, client_id: str) -> dict[str, Any]:  # noqa: ANN001
    async with _fresh_session(engine) as s:
        row = await s.get(Client, client_id)
        assert row is not None
        return _columns(row)


async def _classify_in_db(engine, client_id: str) -> None:  # noqa: ANN001
    """Give a client a real classification, as an earlier read would have."""
    async with _fresh_session(engine) as s:
        row = await s.get(Client, client_id)
        assert row is not None
        intake_service.apply_outcome(row, CLASSIFIED)
        await s.commit()


async def _audit_snapshot(engine, scan_id: str) -> Any:  # noqa: ANN001
    """The scan's stored audit, its checks and its crawler-access rows, by every column, or None."""
    async with _fresh_session(engine) as s:
        audit = (
            await s.execute(
                select(TechnicalAudit)
                .where(TechnicalAudit.scan_id == scan_id)
                .options(
                    selectinload(TechnicalAudit.checks),
                    selectinload(TechnicalAudit.ai_crawler_access),
                )
            )
        ).scalar_one_or_none()
        if audit is None:
            return None
        return (
            _columns(audit),
            sorted((tuple(_columns(c).items()) for c in audit.checks), key=str),
            sorted((tuple(_columns(a).items()) for a in audit.ai_crawler_access), key=str),
        )


class TestABusyBrowserWritesNothingOnARequest:
    """A read that waited its whole bound for the browser is refused, not recorded.

    A client left `unclassifiable`, or an audit stored `failed`, because the crawler
    happened to be busy would say something about the client's site that is not true of
    it, and would replace what was stored before. So the three routes answer the same
    retryable 429 and write nothing at all: `updated_at` is compared too, which an UPDATE
    that changed no other value would still move.
    """

    async def test_adding_a_client_creates_nothing(
        self, client: AsyncClient, engine, busy_browser: Callable[[], Any]  # noqa: ANN001
    ) -> None:
        await _sign_up(client)

        with capture_logs() as logs:
            async with busy_browser():
                refused = await sent(
                    client, "POST", "/clients", json={"url": "busy-site.com", "classify": True}
                )

        _is_the_crawler_busy_429(refused)
        # No client, by any road: not in the list, and not in the table.
        assert (await client.get(f"{BASE}/clients")).json()["data"] == []
        assert await _client_rows(engine) == []
        # The refusal is logged with the id of the client it was for, which never existed.
        (busy,) = [e for e in logs if e["event"] == "browser.slot_busy"]
        assert ids.is_valid(busy["client_id"], ids.CLIENT) and "scan_id" not in busy

    @pytest.mark.parametrize("prior", ["classified", "never_classified"])
    async def test_reclassifying_leaves_the_client_exactly_as_it_was(
        self, client: AsyncClient, engine, busy_browser: Callable[[], Any], prior: str  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "known-site.com")
        if prior == "classified":
            await _classify_in_db(engine, cid)
        before = await _client_columns(engine, cid)
        assert before["classification_status"] is (
            ClassificationStatus.CLASSIFIED if prior == "classified"
            else ClassificationStatus.PENDING
        )

        with capture_logs() as logs:
            async with busy_browser():
                refused = await sent(client, "POST", f"/clients/{cid}/reclassify")

        _is_the_crawler_busy_429(refused)
        assert await _client_columns(engine, cid) == before  # every column, updated_at too
        shown = (await client.get(f"{BASE}/clients/{cid}")).json()
        assert shown["classificationReasonCode"] is None  # never BROWSER_BUSY: nothing wrote it
        (busy,) = [e for e in logs if e["event"] == "browser.slot_busy"]
        assert busy["client_id"] == cid and "scan_id" not in busy

    @pytest.mark.parametrize("stored", [True, False])
    async def test_a_hand_run_audit_leaves_the_stored_audit_as_it_was(
        self, client: AsyncClient, session, engine, busy_browser: Callable[[], Any],  # noqa: ANN001
        page_reads: PageReads, monkeypatch: pytest.MonkeyPatch, stored: bool,
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "audited-site.com")
        sid = await _make_scan(session, cid)
        if stored:
            good = await client.post(f"{BASE}/scans/{sid}/audit")  # `page_reads`: a good audit
            assert good.status_code == 201 and good.json()["status"] != "failed", good.text
        before = await _audit_snapshot(engine, sid)
        assert (before is not None) is stored
        monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)  # real, now

        with capture_logs() as logs:
            async with busy_browser():
                refused = await sent(client, "POST", f"/scans/{sid}/audit")

        _is_the_crawler_busy_429(refused)
        assert await _audit_snapshot(engine, sid) == before  # nothing stored, or all as it was
        shown = await client.get(f"{BASE}/scans/{sid}/audit")
        assert shown.status_code == (200 if stored else 404), shown.text
        if stored:
            assert shown.json()["errorCode"] is None and shown.json()["status"] != "failed"
        (busy,) = [e for e in logs if e["event"] == "browser.slot_busy"]
        assert busy["scan_id"] == sid and "client_id" not in busy

    async def test_the_routes_give_the_same_answer_and_each_refusal_releases_the_agency(
        self, client: AsyncClient, session, busy_browser: Callable[[], Any]  # noqa: ANN001
    ) -> None:
        """Four reads in a row from one agency, all refused for want of the browser. Each is
        the crawler's 429: had a refusal kept the agency's place, the next would have been
        the agency's own."""
        await _sign_up(client)
        cid = await _make_client(client, "released-site.com")
        sid = await _make_scan(session, cid)

        async with busy_browser():
            for method, path, kwargs in (
                ("POST", "/clients", {"json": {"url": "busy-two.com", "classify": True}}),
                ("POST", f"/clients/{cid}/reclassify", {}),
                ("POST", f"/scans/{sid}/audit", {}),
                ("POST", "/clients", {"json": {"url": "busy-three.com", "classify": True}}),
            ):
                _is_the_crawler_busy_429(await sent(client, method, path, **kwargs))


class TestTheServicesRefuseBeforeTheyWrite:
    async def test_run_classification_raises_before_it_classifies_or_touches_the_client(
        self, client: AsyncClient, session, engine, settings,  # noqa: ANN001
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "prior-site.com")
        await _classify_in_db(engine, cid)
        row = await session.get(Client, cid)
        assert row is not None
        before = _columns(row)
        given: list[dict[str, Any]] = []

        async def never_got_the_browser(url: str, **kwargs: Any) -> CrawlResult:
            given.append(kwargs)
            return CrawlResult(
                signals=CrawlSignals(final_url=url, registrable_domain="prior-site.com"),
                text_extract="", ok=False, error_code="BROWSER_BUSY",
            )

        async def must_not_run(*args: Any, **kwargs: Any) -> ClassificationOutcome:
            raise AssertionError("classified a site that was never read")

        monkeypatch.setattr(intake_service, "crawl_site", never_got_the_browser)
        monkeypatch.setattr(intake_service, "classify", must_not_run)

        with pytest.raises(RateLimited) as refused:
            await intake_service.run_classification(session, row, settings=settings)

        assert refused.value.detail == CRAWLER_BUSY
        assert refused.value.headers == {"Retry-After": "60"}
        assert not session.dirty and not session.new and not session.deleted
        assert _columns(row) == before
        assert given[0]["client_id"] == cid  # the id the slot's log lines will carry

    async def test_run_audit_refuses_only_when_asked_and_a_scan_still_records_it(
        self, client: AsyncClient, session, engine,  # noqa: ANN001
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "service-audit.com")
        sid = await _make_scan(session, cid)
        scan, row = await session.get(Scan, sid), await session.get(Client, cid)
        assert scan is not None and row is not None
        given: list[dict[str, Any]] = []

        async def never_got_the_browser(url: str, **kwargs: Any) -> AuditSignals:
            given.append(kwargs)
            return AuditSignals(
                url=url, domain="service-audit.com", ok=False, error_code="BROWSER_BUSY"
            )

        monkeypatch.setattr(audit_runner, "audit_site", never_got_the_browser)

        # A request (`refuse_when_busy`): refused, and not a row written.
        with pytest.raises(RateLimited) as refused:
            await audit_runner.run_audit(session, scan, row, refuse_when_busy=True)
        assert refused.value.detail == CRAWLER_BUSY
        assert not session.new and not session.dirty
        assert await _audit_snapshot(engine, sid) is None

        # A scan's phase 6, which asks for the longer wait and says nothing about refusing:
        # the failed audit is recorded, and the scan goes on.
        audit, outcome = await audit_runner.run_audit(
            session, scan, row, slot_wait_s=browser_slot.SCAN_AUDIT_WAIT_S
        )
        await session.commit()
        assert (audit.status, audit.error_code) == (AuditStatus.FAILED, "BROWSER_BUSY")
        assert outcome.scored is False
        assert await _audit_snapshot(engine, sid) is not None
        assert [kwargs["scan_id"] for kwargs in given] == [sid, sid]


class TestAtMostFourAgenciesAtOnceOnTheRoutes:
    """`MAX_REQUEST_READS`: each read holds a pooled database connection while it waits."""

    @staticmethod
    async def _four_agencies_inside(
        stack: AsyncExitStack, client: AsyncClient, page_reads: PageReads
    ) -> None:
        """This test's own agency (`client`) and three more, each inside a read of its own."""
        inside = [(client, "held-0.com")]
        for number in (1, 2, 3):
            other = await stack.enter_async_context(
                _another_agency(client, f"inside{number}@test.example")
            )
            inside.append((other, f"held-{number}.com"))
        for who, domain in inside:
            await stack.enter_async_context(
                read_in_flight(
                    page_reads, domain,
                    lambda who=who, domain=domain: who.post(
                        f"{BASE}/clients", json={"url": domain, "classify": True}
                    ),
                )
            )

    async def test_the_fifth_agency_is_refused_on_every_route_while_four_are_inside(
        self, client: AsyncClient, session, routes: Routes, page_reads: PageReads  # noqa: ANN001
    ) -> None:
        async with _another_agency(client, "fifth@test.example") as fifth:
            theirs = await _make_client(fifth, "fifth-owned.com")
            their_scan = await _make_scan(session, theirs)

            async with AsyncExitStack() as stack:
                await self._four_agencies_inside(stack, client, page_reads)

                # The crawler is full, whatever the fifth agency asks: the sentence is about
                # the crawler, and nothing was created, changed or stored.
                _is_the_crawler_busy_429(
                    await fifth.post(
                        f"{BASE}/clients", json={"url": "fifth-new.com", "classify": True}
                    )
                )
                _is_the_crawler_busy_429(await fifth.post(f"{BASE}/clients/{theirs}/reclassify"))
                _is_the_crawler_busy_429(await fifth.post(f"{BASE}/scans/{their_scan}/audit"))
                listed = (await fifth.get(f"{BASE}/clients")).json()["data"]
                assert {c["domain"] for c in listed} == {"fifth-owned.com"}
                # A site made without classification is not a read, so it is not refused.
                made = await fifth.post(
                    f"{BASE}/clients", json={"url": "fifth-quiet.com", "classify": False}
                )
                assert made.status_code == 201, made.text
                # An agency that IS inside is told about its own read, whatever the cap.
                _is_the_agency_busy_429(
                    await client.post(
                        f"{BASE}/clients", json={"url": "free-again.com", "classify": True}
                    )
                )

            # The four have finished and each place came back: the fifth is served.
            served = await fifth.post(
                f"{BASE}/clients", json={"url": "fifth-new.com", "classify": True}
            )
            assert served.status_code == 201, served.text

    async def test_a_scan_is_not_refused_by_the_cap(
        self, client: AsyncClient, engine, routes: Routes, page_reads: PageReads, stub_engines  # noqa: ANN001
    ) -> None:
        """Four agencies' reads are inside, and a fifth agency's scan still runs its audit."""
        async with _another_agency(client, "fifth@test.example") as fifth:
            cid = await _make_client(fifth, "helpscout.com")
            stub_engines()

            async with AsyncExitStack() as stack:
                await self._four_agencies_inside(stack, client, page_reads)

                started = await fifth.post(f"{BASE}/clients/{cid}/scans", json={})  # runs inline
                assert started.status_code == 202, started.text
                scan, audit = await _scan_and_its_audit(engine, started.json()["id"])

                assert scan is not None and scan.status is ScanStatus.SUCCEEDED
                assert audit is not None and audit.status is not AuditStatus.FAILED
                assert audit.error_code is None


# ---------------------------------------------------------------------------
# a scan's own audit is not one of an agency's reads
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Deterministic prompt generation and engine answers, as every scan test has."""
    from avp_api.models.engine_result import EngineResultStatus, Sentiment
    from avp_api.models.prompt import PromptIntent
    from avp_api.services import scan_runner
    from avp_api.services.engines import CitedSource, EngineAnswer
    from avp_api.services.prompts import GeneratedPrompt

    def _install(n_prompts: int = 2):
        generated = [
            GeneratedPrompt(text=f"question number {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub-generator"

        text = "Zendesk is popular. Help Scout is simpler and well liked. Front is newer."

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine, engine_version="stub", prompt_text=prompt, text=text,
                    citations=(
                        [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                        if engine.value.endswith("search")
                        else []
                    ),
                    latency_ms=10, status=EngineResultStatus.OK,
                )
                for engine in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)

    return _install


async def _scan_and_its_audit(engine, scan_id: str):  # noqa: ANN001, ANN202
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        scan = await s.get(Scan, scan_id)
        audit = (
            await s.execute(
                select(TechnicalAudit)
                .where(TechnicalAudit.scan_id == scan_id)
                .options(selectinload(TechnicalAudit.checks))
            )
        ).scalar_one_or_none()
        return scan, audit


class TestAScansOwnAuditIsNotOneOfThem:
    async def test_a_scan_is_not_refused_while_the_agency_has_a_read_in_flight(
        self, client: AsyncClient, engine, routes: Routes, page_reads: PageReads, stub_engines  # noqa: ANN001
    ) -> None:
        cid = await _make_client(client, "helpscout.com")
        stub_engines()

        async with read_in_flight(
            page_reads, "held-new.com", routes.call("create", held=True)
        ) as in_flight:
            started = await client.post(f"{BASE}/clients/{cid}/scans", json={})  # runs inline
            assert started.status_code == 202, started.text
            scan, audit = await _scan_and_its_audit(engine, started.json()["id"])

            # Its phase 6 ran, was not refused, and the scan finished: the read that was
            # in flight on the request route was still in flight throughout.
            assert not in_flight.done()
            assert scan is not None and scan.status is ScanStatus.SUCCEEDED
            assert audit is not None and audit.status is not AuditStatus.FAILED
            assert audit.error_code is None

        assert in_flight.result().status_code == 201

    async def test_a_scans_audit_in_flight_is_not_counted_against_the_agency(
        self, client: AsyncClient, engine, routes: Routes, page_reads: PageReads, stub_engines  # noqa: ANN001
    ) -> None:
        cid = await _make_client(client, "held-scan.com")
        stub_engines()
        scan_request = asyncio.ensure_future(client.post(f"{BASE}/clients/{cid}/scans", json={}))
        try:
            await asyncio.wait_for(page_reads.began("held-scan.com").wait(), 30)  # in phase 6

            # With the scan's audit running, a request-route read of this agency is served...
            served = await routes.call("create", held=False)()
            assert served.status_code == 201, served.text
            # ... and when a request-route read IS in flight beside it, the next is refused
            # as ever: the scan's audit took no place of its own in the count.
            async with read_in_flight(
                page_reads, "held-new.com", routes.call("create", held=True)
            ):
                _is_the_agency_busy_429(await routes.call("reclassify", held=False)())
        finally:
            # Finished before the test ends, whatever happened above: a scan still running
            # holds locks that the next test's table clean-up would wait on for ever.
            page_reads.release.set()
            await asyncio.wait_for(asyncio.gather(scan_request, return_exceptions=True), 30)
        started = scan_request.result()
        scan, audit = await _scan_and_its_audit(engine, started.json()["id"])
        assert scan is not None and scan.status is ScanStatus.SUCCEEDED
        assert audit is not None and audit.status is not AuditStatus.FAILED
        assert audit.error_code is None

    async def test_a_scans_audit_asks_for_the_scans_bound_and_a_requests_for_a_requests(
        self, client: AsyncClient, routes: Routes, page_reads: PageReads, stub_engines
    ) -> None:
        cid = await _make_client(client, "helpscout.com")
        stub_engines()

        await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert page_reads.audit_waits == [120.0]  # the scan's own bound (checklist 3.35)

        page_reads.audit_waits.clear()
        resp = await routes.call("audit", held=False)()
        assert resp.status_code == 201, resp.text
        # Says nothing, so `browser_slot` gives it the request's (`REQUEST_WAIT_S`, 60 s).
        assert page_reads.audit_waits == [None]

    async def test_a_scan_whose_audit_cannot_get_the_browser_fails_that_phase_and_goes_on(
        self, client: AsyncClient, engine, stub_engines, robots_web: Any,  # noqa: ANN001
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The wait is the scan's own, and when it runs out the audit is a failed one.

        The scan's audit asks the slot for its real bound, 120 s (launch checklist 3.35),
        and the test records what it was asked for. Only the time the slot then waits is
        scaled down, to 0.3 s, so the test does not sit through two minutes.
        """
        from avp_api.models import ActionItem, Score

        monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)
        asked: list[float | None] = []
        real_slot = browser_slot.browser_slot

        def scaled_down(
            kind: browser_slot.BrowserKind, *, wait_s: float | None = None, **refs: str | None
        ) -> Any:
            asked.append(wait_s)
            return real_slot(kind, wait_s=0.3, **refs)

        monkeypatch.setattr(technical_audit, "browser_slot", scaled_down)

        def launched(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
            pytest.fail("a browser was started for an audit that never got the slot")

        monkeypatch.setattr(technical_audit, "async_playwright", launched)
        await _sign_up(client)
        cid = await _make_client(client, "helpscout.com")
        stub_engines()

        with capture_logs() as logs:
            async with browser_slot.browser_slot("crawl"):  # another run holds the browser
                started = await asyncio.wait_for(
                    client.post(f"{BASE}/clients/{cid}/scans", json={}), 20
                )
        sid = started.json()["id"]

        assert asked == [120.0]  # the scan's real bound: the 0.3 s above is only its copy
        scan, audit = await _scan_and_its_audit(engine, sid)
        assert audit is not None and audit.status is AuditStatus.FAILED
        assert (audit.error_code, audit.pages_crawled) == ("BROWSER_BUSY", 0)
        assert [(c.check_key, c.status.value, c.detail_code) for c in audit.checks] == [
            ("site_reachable", "error", "BROWSER_BUSY")
        ]
        (busy,) = [e for e in logs if e["event"] == "browser.slot_busy"]
        assert busy["kind"] == "audit" and busy["waited_ms"] >= 250
        assert busy["scan_id"] == sid and "client_id" not in busy  # the scan it was for
        # The chain went on: it was scored without Technical Foundation (excluded, never
        # a zero), the fix list was written, and the scan ended on its own account.
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            scores = list((await s.execute(select(Score).where(Score.scan_id == sid))).scalars())
            fixes = list(
                (await s.execute(select(ActionItem).where(ActionItem.scan_id == sid))).scalars()
            )
        assert scores and scores[0].composite is not None
        assert scores[0].technical_foundation is None
        assert fixes, "fix generation did not run"
        assert scan is not None and scan.status is ScanStatus.SUCCEEDED
        assert scan.error_code is None

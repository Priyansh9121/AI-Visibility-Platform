"""One browser per API process at a time, closed on every path, under a cap — M1, 2026-10-02.

Measured on the API image: one heavy page's audit peaks near 490 MB, and a
crawl and an audit running together reach about 586 MB — over a 512 MB limit.
Both fetchers launch Chromium (`crawl.crawl_site`, `technical_audit.audit_site`),
so `browser_slot` lets one browser run at a time per process, which makes a hang
inside a run expensive: it holds everyone else. These follow, and all are tested
here:

  * every run ends with its page, its context and its browser closed, on every
    way out of it, and gives the slot back;
  * a run that hangs is cut by a cap computed from the code's own timeouts, and
    comes out as the outcome a timeout already had, keeping nothing the page gave
    (round 2: that now includes a cap that falls before the final egress check);
  * a caller does not wait for the slot for ever (round 2): past its bound it gets
    `BrowserBusy`, never launches, and leaves the queue cleanly; both fetchers turn
    that into a failed result with the code `BROWSER_BUSY`;
  * an agency has one run queued or running at a time on a request route (round 2:
    `one_run_per_agency`; the routes themselves are in test_agency_page_reads.py), and
    only `MAX_REQUEST_READS` agencies at once (round 3);
  * the slot's log lines carry our own id for the scan or client the run was for, and
    never a URL (round 3);
  * a browser error keeps nothing the page gave, as a timeout does: no failed audit
    carries a page reading (round 3).

No browser and no network. `Fake` stands in for `async_playwright()` and counts
what is opened and closed; `robots_web` (conftest) answers the robots gate.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from structlog.testing import capture_logs

from avp_api import ids
from avp_api.errors import RateLimited
from avp_api.services import (
    browser_slot,
    crawl,
    egress,
    intake,
    memory_stats,
    scan_executor,
    technical_audit,
)
from avp_api.services.ai_crawlers import AccessVerdict
from avp_api.services.classify import classify

SITE = "https://prospect-site.com"
SECOND_SITE = "https://second-site.com"

# Everything either fetcher reads from a page. A superset, so one fake serves both.
FACTS: dict[str, Any] = {
    "text": "A business that does a thing, described at length.",
    "title": "A page", "hasMetaDescription": True, "lang": "en", "h1": 1, "h2": 0,
    "jsonLd": '{"@type": "Organization"}', "microdata": [], "links": [],
    "hasTitle": True, "canonical": True, "ogCount": 1, "robotsContent": "", "words": 300,
    "vitals": {"lcp": 1800, "cls": 0.02},
}


# ---------------------------------------------------------------------------
# a Playwright stand-in that counts
# ---------------------------------------------------------------------------


class Fake:
    """`async_playwright()` with no browser: it counts everything opened and closed.

    A browser is open from `launch` until its `close`; `max_open_browsers` is the
    most that were open at once. `step_s` makes each call yield for that long, so
    two runs that could overlap do. The rest say where a run goes wrong:

      launch, new_page, goto   "ok", or "timeout" (Playwright's own TimeoutError)
      goto                     also "hold_first": the first navigation anywhere waits
                               for `release`, announcing itself on `holding`
      evaluate                 "ok", "hang" (never answers), "raise" (RuntimeError),
                               "renderer_dies" (Playwright's error for a page whose
                               renderer crashed), or "private_then_hang" (a response
                               from a private address is reported to the context's
                               listeners, then it hangs)
      *_close                  "ok", "error" (Playwright's closed-target error, the
                               target being gone) or "hang" (never returns)

    A close is counted when it is CALLED, so a hung one still shows.
    """

    def __init__(
        self, *, step_s: float = 0.0, launch: str = "ok", new_page: str = "ok",
        goto: str = "ok", evaluate: str = "ok", browser_close: str = "ok",
        context_close: str = "ok", page_close: str = "ok",
    ) -> None:
        self.step_s = step_s
        self.launch_mode, self.new_page_mode, self.goto_mode = launch, new_page, goto
        self.evaluate_mode = evaluate
        self.close_modes = {
            "browser": browser_close, "context": context_close, "page": page_close,
        }
        self.driver_calls = 0  # `async_playwright()` called at all, even if never entered
        self.driver_starts = self.driver_stops = 0
        self.launches = self.browser_closes = 0
        self.contexts_opened = self.context_closes = 0
        self.pages_opened = self.page_closes = 0
        self.open_browsers = self.max_open_browsers = 0
        self.listeners: list[tuple[str, Any]] = []
        self.evaluating = asyncio.Event()
        self.holding = asyncio.Event()
        self.release = asyncio.Event()
        self._held = False

    def __call__(self) -> _Driver:
        self.driver_calls += 1
        return _Driver(self)

    async def step(self) -> None:
        await asyncio.sleep(self.step_s)

    async def closing(self, part: str) -> None:
        how = self.close_modes[part]
        await asyncio.sleep(0)
        if how == "hang":
            await asyncio.Event().wait()
        if how == "error":
            raise PlaywrightError("Target page, context or browser has been closed")


class _Driver:
    def __init__(self, fake: Fake) -> None:
        self.fake = fake
        self.chromium = self

    async def __aenter__(self) -> _Driver:
        self.fake.driver_starts += 1
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.fake.driver_stops += 1
        await asyncio.sleep(0)
        return False

    async def launch(self, **kwargs: Any) -> _Browser:  # noqa: ARG002
        fake = self.fake
        await fake.step()
        if fake.launch_mode == "timeout":
            raise PlaywrightTimeoutError("Timeout 30000ms exceeded.")
        fake.launches += 1
        fake.open_browsers += 1
        fake.max_open_browsers = max(fake.max_open_browsers, fake.open_browsers)
        return _Browser(fake)


class _Browser:
    def __init__(self, fake: Fake) -> None:
        self.fake = fake

    async def new_context(self, **kwargs: Any) -> _Context:  # noqa: ARG002
        await self.fake.step()
        self.fake.contexts_opened += 1
        return _Context(self.fake)

    async def close(self) -> None:
        fake = self.fake
        fake.browser_closes += 1
        how = fake.close_modes["browser"]
        await asyncio.sleep(0)
        if how == "hang":
            await asyncio.Event().wait()
        fake.open_browsers -= 1  # an "error" is a target that was already gone: closed
        if how == "error":
            raise PlaywrightError("Target page, context or browser has been closed")


class _Context:
    def __init__(self, fake: Fake) -> None:
        self.fake = fake

    async def route(self, pattern: Any, handler: Any, **kwargs: Any) -> None:
        return None

    async def route_web_socket(self, pattern: Any, handler: Any) -> None:
        return None

    def on(self, event: str, handler: Any) -> None:
        self.fake.listeners.append((event, handler))

    async def add_init_script(self, script: str) -> None:
        return None

    async def new_page(self) -> _Page:
        await self.fake.step()
        if self.fake.new_page_mode == "timeout":
            raise PlaywrightTimeoutError("Timeout 30000ms exceeded.")
        self.fake.pages_opened += 1
        return _Page(self.fake)

    async def close(self) -> None:
        self.fake.context_closes += 1
        await self.fake.closing("context")


class _Page:
    def __init__(self, fake: Fake) -> None:
        self.fake = fake
        self.url = "about:blank"

    def set_default_timeout(self, ms: int) -> None:
        return None

    async def add_init_script(self, script: str) -> None:
        return None

    async def wait_for_timeout(self, ms: int) -> None:
        await asyncio.sleep(0)

    async def goto(self, url: str, **kwargs: Any) -> SimpleNamespace:  # noqa: ARG002
        fake = self.fake
        await fake.step()
        if fake.goto_mode == "timeout":
            raise PlaywrightTimeoutError("Timeout 25000ms exceeded.")
        if fake.goto_mode == "hold_first" and not fake._held:  # noqa: SLF001
            fake._held = True  # noqa: SLF001
            fake.holding.set()
            await fake.release.wait()
        # As Chromium does: an address with no path is its root.
        self.url = url if urlsplit(url).path else url + "/"
        return SimpleNamespace(
            status=200, headers={}, request=SimpleNamespace(url=self.url, redirected_from=None)
        )

    async def evaluate(self, script: str) -> dict[str, Any]:  # noqa: ARG002
        fake = self.fake
        await fake.step()
        fake.evaluating.set()
        if fake.evaluate_mode == "raise":
            raise RuntimeError("boom")
        if fake.evaluate_mode == "renderer_dies":
            raise PlaywrightError("Page crashed")
        if fake.evaluate_mode == "private_then_hang":
            await self._a_private_response()
        if fake.evaluate_mode in ("hang", "private_then_hang"):
            await asyncio.Event().wait()
        return dict(FACTS)

    async def _a_private_response(self) -> None:
        """What Chromium reports when a response came from a private address."""

        async def server_addr() -> dict[str, Any]:
            return {"ipAddress": "10.0.0.5", "port": 443}

        response = SimpleNamespace(url=self.url, server_addr=server_addr)
        for event, handler in self.fake.listeners:
            if event == "response":
                await handler(response)

    async def close(self) -> None:
        self.fake.page_closes += 1
        await self.fake.closing("page")


async def run(kind: str, monkeypatch: pytest.MonkeyPatch, fake: Fake, **kwargs: Any) -> Any:
    """The crawl or the audit of `SITE`, with `fake` for Playwright. `kwargs` go to the fetcher."""
    if kind == "crawl":
        monkeypatch.setattr(crawl, "async_playwright", fake)
        return await crawl.crawl_site("prospect-site.com", **kwargs)
    monkeypatch.setattr(technical_audit, "async_playwright", fake)
    return await technical_audit.audit_site(SITE, **kwargs)


def short_cap(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.3) -> None:
    """Both caps, scaled down so a hang costs a fraction of a second in a test."""
    monkeypatch.setattr(crawl, "crawl_run_cap_s", lambda max_pages, timeout_ms: seconds)
    monkeypatch.setattr(technical_audit, "audit_run_cap_s", lambda timeout_ms: seconds)


@pytest.fixture(autouse=True)
def _a_failed_test_leaves_nothing_taken() -> Iterator[None]:
    """The slot and the per-agency count are process-wide, and the suite shares one event
    loop: a test that fails while it holds either would fail every test after it. They are
    reset after each one. (Each test asserts its own release before this runs.)"""
    yield
    browser_slot._slots.clear()  # noqa: SLF001
    browser_slot._agencies_with_a_run.clear()  # noqa: SLF001


async def assert_slot_free() -> None:
    """The slot can be taken now. A held one would time out here, not hang the suite."""
    async with asyncio.timeout(2), browser_slot.browser_slot("crawl"):
        pass


def assert_everything_closed(fake: Fake, opened: tuple[int, int, int], runs: int = 1) -> None:
    assert (fake.launches, fake.contexts_opened, fake.pages_opened) == opened
    assert fake.browser_closes == fake.launches
    assert fake.context_closes == fake.contexts_opened
    assert fake.page_closes == fake.pages_opened
    assert fake.open_browsers == 0
    assert fake.driver_starts == fake.driver_stops == runs


# ---------------------------------------------------------------------------
# the slot
# ---------------------------------------------------------------------------


class TestTheSlot:
    async def test_one_holder_at_a_time_and_the_next_comes_in_when_it_leaves(self) -> None:
        order: list[str] = []
        release_first = asyncio.Event()

        async def first() -> None:
            async with browser_slot.browser_slot("audit"):
                order.append("first in")
                await release_first.wait()
                order.append("first out")

        async def second() -> None:
            async with browser_slot.browser_slot("crawl"):
                order.append("second in")

        one = asyncio.create_task(first())
        await asyncio.sleep(0.01)
        two = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert order == ["first in"]  # the second is waiting

        release_first.set()
        await asyncio.wait_for(asyncio.gather(one, two), 5)

        assert order == ["first in", "first out", "second in"]

    async def test_callers_are_let_in_in_the_order_they_asked(self) -> None:
        entered: list[int] = []
        release = asyncio.Event()

        async def caller(number: int) -> None:
            async with browser_slot.browser_slot("crawl"):
                entered.append(number)
                await release.wait()

        tasks = []
        for number in range(4):
            tasks.append(asyncio.create_task(caller(number)))
            await asyncio.sleep(0.01)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)

        assert entered == [0, 1, 2, 3]

    async def test_a_caller_that_had_to_wait_logs_how_long_and_nothing_else(self) -> None:
        release = asyncio.Event()

        async def holder() -> None:
            async with browser_slot.browser_slot("audit"):
                await release.wait()

        async def waiter() -> None:
            async with browser_slot.browser_slot("crawl"):
                pass

        with capture_logs() as logs:
            one = asyncio.create_task(holder())
            await asyncio.sleep(0.01)
            two = asyncio.create_task(waiter())
            await asyncio.sleep(0.1)
            release.set()
            await asyncio.wait_for(asyncio.gather(one, two), 5)

        (entry,) = [e for e in logs if e["event"] == "browser.slot_waited"]
        assert set(entry) == {"event", "log_level", "kind", "waited_ms"}
        assert entry["kind"] == "crawl"  # the waiter's kind, not the holder's
        assert type(entry["waited_ms"]) is int
        assert entry["waited_ms"] >= 50  # it began waiting ~100 ms before the release

    async def test_a_caller_that_did_not_wait_logs_no_wait(self) -> None:
        with capture_logs() as logs:
            async with browser_slot.browser_slot("crawl"):
                pass
            async with browser_slot.browser_slot("audit"):
                pass

        assert not [e for e in logs if e["event"] == "browser.slot_waited"]

    async def test_it_is_given_back_when_the_body_raises(self) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with browser_slot.browser_slot("audit"):
                raise RuntimeError("boom")

        await assert_slot_free()

    async def test_it_is_given_back_when_the_holder_is_cancelled(self) -> None:
        inside = asyncio.Event()

        async def holder() -> None:
            async with browser_slot.browser_slot("audit"):
                inside.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(holder())
        await asyncio.wait_for(inside.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await assert_slot_free()

    async def test_a_waiter_that_is_cancelled_does_not_take_the_slot_with_it(self) -> None:
        release = asyncio.Event()

        async def holder() -> None:
            async with browser_slot.browser_slot("audit"):
                await release.wait()

        async def waiter() -> None:
            async with browser_slot.browser_slot("crawl"):
                pass

        one = asyncio.create_task(holder())
        await asyncio.sleep(0.01)
        two = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)
        two.cancel()
        with pytest.raises(asyncio.CancelledError):
            await two
        release.set()
        await asyncio.wait_for(one, 5)

        await assert_slot_free()

    async def test_each_event_loop_has_its_own_slot(self) -> None:
        """Held here, free there: another loop (a thread's, or another test's) is unaffected."""

        async def on_another_loop() -> str:
            async with asyncio.timeout(5), browser_slot.browser_slot("crawl"):
                return "entered"

        async with browser_slot.browser_slot("audit"):  # held on THIS loop
            assert await asyncio.to_thread(asyncio.run, on_another_loop()) == "entered"

        await assert_slot_free()


class TestMemoryAtTheEndOfARun:
    SNAPSHOT = {
        "process_tree_rss_mb": 140, "cgroup_current_mb": 190, "cgroup_anon_mb": 120,
        "cgroup_peak_mb": 480, "cgroup_limit_mb": 512,
    }

    async def test_the_run_logs_its_memory_as_numbers_when_it_ends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(memory_stats, "snapshot", lambda: dict(self.SNAPSHOT))

        with capture_logs() as logs:
            async with browser_slot.browser_slot("audit"):
                assert not [e for e in logs if e["event"] == "browser.memory"]  # not yet

        assert [e for e in logs if e["event"] == "browser.memory"] == [
            {"event": "browser.memory", "log_level": "info", "kind": "audit", **self.SNAPSHOT}
        ]

    async def test_it_is_logged_before_the_next_caller_comes_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Inside the slot's exit: the numbers are this run's, not the next one's."""
        monkeypatch.setattr(memory_stats, "snapshot", lambda: dict(self.SNAPSHOT))
        release = asyncio.Event()

        async def holder() -> None:
            async with browser_slot.browser_slot("audit"):
                await release.wait()

        async def waiter() -> None:
            async with browser_slot.browser_slot("crawl"):
                pass

        with capture_logs() as logs:
            one = asyncio.create_task(holder())
            await asyncio.sleep(0.01)
            two = asyncio.create_task(waiter())
            await asyncio.sleep(0.02)
            release.set()
            await asyncio.wait_for(asyncio.gather(one, two), 5)

        events = [e["event"] for e in logs if e["event"].startswith("browser.")]
        assert events == [
            "browser.memory",  # the holder's, as it leaves
            "browser.slot_waited",  # then the waiter, who could not enter before
            "browser.memory",  # and the waiter's own
        ]
        assert [e["kind"] for e in logs if e["event"] == "browser.memory"] == ["audit", "crawl"]

    async def test_a_snapshot_that_raises_costs_the_run_nothing_and_the_slot_is_freed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> dict[str, int | None]:
            raise RuntimeError("memory exploded")

        monkeypatch.setattr(memory_stats, "snapshot", boom)

        with capture_logs() as logs:
            async with browser_slot.browser_slot("audit"):
                result = "the run's own result"

        assert result == "the run's own result"
        assert not [e for e in logs if e["event"] == "browser.memory"]
        await assert_slot_free()

    async def test_a_snapshot_that_raises_does_not_mask_the_runs_own_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> dict[str, int | None]:
            raise RuntimeError("memory exploded")

        monkeypatch.setattr(memory_stats, "snapshot", boom)

        with pytest.raises(ValueError, match="the run's error"):
            async with browser_slot.browser_slot("audit"):
                raise ValueError("the run's error")

        await assert_slot_free()

    async def test_nothing_but_numbers_and_the_kind_is_logged(self) -> None:
        with capture_logs() as logs:
            async with browser_slot.browser_slot("crawl"):
                pass

        (entry,) = [e for e in logs if e["event"] == "browser.memory"]
        assert set(entry) == {
            "event", "log_level", "kind", "process_tree_rss_mb", "cgroup_current_mb",
            "cgroup_anon_mb", "cgroup_peak_mb", "cgroup_limit_mb",
        }
        assert entry["kind"] == "crawl"
        numbers = {k: v for k, v in entry.items() if k.endswith("_mb")}
        assert all(v is None or type(v) is int for v in numbers.values()), numbers


# ---------------------------------------------------------------------------
# one browser at a time, across both fetchers
# ---------------------------------------------------------------------------


class TestNeverTwoBrowsersOpen:
    async def test_two_audits_at_once_run_one_after_the_other(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = Fake(step_s=0.02)
        monkeypatch.setattr(technical_audit, "async_playwright", fake)

        first, second = await asyncio.wait_for(
            asyncio.gather(
                technical_audit.audit_site(SITE), technical_audit.audit_site(SECOND_SITE)
            ),
            20,
        )

        assert first.ok and second.ok
        assert fake.launches == 2  # both ran
        assert fake.max_open_browsers == 1  # never together
        assert_everything_closed(fake, (2, 2, 2), runs=2)

    async def test_an_audit_and_a_crawl_at_once_run_one_after_the_other(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = Fake(step_s=0.02)
        monkeypatch.setattr(technical_audit, "async_playwright", fake)
        monkeypatch.setattr(crawl, "async_playwright", fake)

        audit, crawled = await asyncio.wait_for(
            asyncio.gather(
                technical_audit.audit_site(SITE), crawl.crawl_site("second-site.com")
            ),
            20,
        )

        assert audit.ok and crawled.ok
        assert fake.launches == 2
        assert fake.max_open_browsers == 1
        assert_everything_closed(fake, (2, 2, 2), runs=2)

    async def test_the_second_waits_for_the_browser_and_only_for_the_browser(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = Fake(goto="hold_first")
        monkeypatch.setattr(technical_audit, "async_playwright", fake)

        with capture_logs() as logs:
            first = asyncio.create_task(technical_audit.audit_site(SITE))
            await asyncio.wait_for(fake.holding.wait(), 5)  # the first is inside the browser
            second = asyncio.create_task(technical_audit.audit_site(SECOND_SITE))
            await asyncio.sleep(0.3)
            try:
                # Its robots.txt and sitemap were fetched while the first held the
                # browser: the slot covers the browser's lifetime and nothing before it.
                assert f"{SECOND_SITE}/robots.txt" in robots_web.fetched()
                assert f"{SECOND_SITE}/sitemap.xml" in robots_web.fetched()
                # ... but it has not launched one.
                assert fake.launches == 1
                assert not second.done()
            finally:
                fake.release.set()
            first_signals, second_signals = await asyncio.wait_for(
                asyncio.gather(first, second), 10
            )

        # A second caller proceeds once the first has finished.
        assert first_signals.ok and second_signals.ok
        assert fake.launches == 2 and fake.max_open_browsers == 1
        (waited,) = [e for e in logs if e["event"] == "browser.slot_waited"]
        assert waited["kind"] == "audit" and waited["waited_ms"] >= 100


# ---------------------------------------------------------------------------
# no browser outlives its run
# ---------------------------------------------------------------------------


def _refuse_the_landing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(self: Any, response: Any, page_url: str) -> None:  # noqa: ARG001
        raise egress.EgressRefused("prospect-site.com", "10.0.0.5")

    monkeypatch.setattr(egress.BrowserGuard, "check_landing", refuse)


def _never_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unverified(self: Any) -> None:  # noqa: ARG001
        raise egress.EgressUnverified

    monkeypatch.setattr(egress.BrowserGuard, "finish", unverified)


# path -> (Fake arguments, what is opened: (browsers, contexts, pages),
#          the error code each fetcher ends with — None where it raises or is cancelled)
PATHS: dict[str, tuple[dict[str, Any], tuple[int, int, int], dict[str, str | None]]] = {
    "success": ({}, (1, 1, 1), {"crawl": None, "audit": None}),
    "egress_refused": (
        {}, (1, 1, 1), {"crawl": "ADDRESS_NOT_PUBLIC", "audit": "ADDRESS_NOT_PUBLIC"}
    ),
    "egress_unverified": ({}, (1, 1, 1), {"crawl": "BROWSER_ERROR", "audit": "BROWSER_ERROR"}),
    "launch_timeout": (
        {"launch": "timeout"}, (0, 0, 0), {"crawl": "BROWSER_ERROR", "audit": "BROWSER_ERROR"}
    ),
    "page_timeout": (
        {"new_page": "timeout"}, (1, 1, 0), {"crawl": "BROWSER_ERROR", "audit": "BROWSER_ERROR"}
    ),
    "navigation_timeout": (
        {"goto": "timeout"}, (1, 1, 1), {"crawl": "FETCH_FAILED", "audit": "BROWSER_ERROR"}
    ),
    "other_exception": ({"evaluate": "raise"}, (1, 1, 1), {"crawl": "raises", "audit": "raises"}),
    "the_cap": ({"evaluate": "hang"}, (1, 1, 1), {"crawl": "TIMEOUT", "audit": "TIMEOUT"}),
    "cancelled": ({"evaluate": "hang"}, (1, 1, 1), {"crawl": "cancelled", "audit": "cancelled"}),
}


class TestNoBrowserOutlivesItsRun:
    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    @pytest.mark.parametrize("path", list(PATHS))
    async def test_everything_opened_is_closed_and_the_slot_is_free(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str, path: str
    ) -> None:
        arguments, opened, expected = PATHS[path]
        fake = Fake(**arguments)
        outcome = expected[kind]
        if path == "egress_refused":
            _refuse_the_landing(monkeypatch)
        if path == "egress_unverified":
            _never_verify(monkeypatch)
        if path == "the_cap":
            short_cap(monkeypatch)

        if outcome == "raises":
            with pytest.raises(RuntimeError, match="boom"):
                await run(kind, monkeypatch, fake)
        elif outcome == "cancelled":
            task = asyncio.create_task(run(kind, monkeypatch, fake))
            await asyncio.wait_for(fake.evaluating.wait(), 5)  # inside the browser, waiting
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await asyncio.wait_for(run(kind, monkeypatch, fake), 10)
            assert (result.ok, result.error_code) == (outcome is None, outcome)

        assert_everything_closed(fake, opened)
        await assert_slot_free()

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_run_that_succeeds_opens_and_closes_one_of_each(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """The control for the table above: the counts are not zero because nothing ran."""
        fake = Fake()

        result = await run(kind, monkeypatch, fake)

        assert result.ok
        assert (fake.launches, fake.contexts_opened, fake.pages_opened) == (1, 1, 1)
        assert (fake.browser_closes, fake.context_closes, fake.page_closes) == (1, 1, 1)

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_the_page_and_the_context_are_closed_before_the_browser(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        order: list[str] = []
        fake = Fake()
        for part, cls in (("page", _Page), ("context", _Context), ("browser", _Browser)):
            original = cls.close

            async def close(self: Any, _part: str = part, _original: Any = original) -> None:
                order.append(_part)
                await _original(self)

            monkeypatch.setattr(cls, "close", close)

        await run(kind, monkeypatch, fake)

        assert order == ["page", "context", "browser"]

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    @pytest.mark.parametrize("part", ["page", "context"])
    async def test_a_page_or_context_that_will_not_close_does_not_keep_the_browser_open(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str, part: str
    ) -> None:
        fake = Fake(**{f"{part}_close": "error"})

        with capture_logs() as logs:
            result = await run(kind, monkeypatch, fake)

        assert result.ok  # the facts were read and checked; the failed close changes nothing
        assert_everything_closed(fake, (1, 1, 1))
        (entry,) = [e for e in logs if e["event"] == "browser.close_failed"]
        # The part and the error's class: never its message, which can carry a page's address.
        assert entry == {
            "event": "browser.close_failed", "log_level": "warning", "part": part,
            "error": "Error",
        }

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_page_that_hangs_on_close_is_given_up_on_and_the_browser_is_closed(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        monkeypatch.setattr(browser_slot, "CLOSE_TIMEOUT_S", 0.05)
        fake = Fake(page_close="hang")

        result = await asyncio.wait_for(run(kind, monkeypatch, fake), 10)

        assert result.ok
        assert (fake.page_closes, fake.context_closes, fake.browser_closes) == (1, 1, 1)
        assert fake.open_browsers == 0
        await assert_slot_free()

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_browser_that_hangs_on_close_does_not_hold_the_slot(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        monkeypatch.setattr(browser_slot, "CLOSE_TIMEOUT_S", 0.05)
        fake = Fake(browser_close="hang")
        started = time.monotonic()

        with capture_logs() as logs:
            result = await asyncio.wait_for(run(kind, monkeypatch, fake), 10)

        assert time.monotonic() - started < 5
        assert result.ok  # what was read was already checked; only the close was lost
        assert fake.browser_closes == 1
        assert fake.driver_stops == 1  # leaving the driver ends a browser that would not close
        (entry,) = [e for e in logs if e["event"] == "browser.close_failed"]
        assert (entry["part"], entry["error"]) == ("browser", "TimeoutError")
        await assert_slot_free()

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_browser_that_fails_to_close_is_still_a_browser_error(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """Unchanged: nothing about a close that RAISES was asked to move (test_robots_obeyed)."""
        fake = Fake(browser_close="error")

        result = await run(kind, monkeypatch, fake)

        assert (result.ok, result.error_code) == (False, "BROWSER_ERROR")
        assert fake.browser_closes == 1
        await assert_slot_free()

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_second_cancellation_while_closing_still_closes_the_browser(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """The scan deadline and a lost lease can both cancel a run; the second must not
        leave the browser open."""
        fake = Fake(evaluate="hang", page_close="hang")
        task = asyncio.create_task(run(kind, monkeypatch, fake))
        await asyncio.wait_for(fake.evaluating.wait(), 5)
        task.cancel()  # the first: the run is now in its closes, and the page's hangs
        async with asyncio.timeout(5):
            while fake.page_closes == 0:
                await asyncio.sleep(0.01)
        task.cancel()  # the second, in the middle of them

        with pytest.raises(asyncio.CancelledError):
            await task

        assert fake.browser_closes == 1  # reached anyway
        assert fake.open_browsers == 0
        assert fake.driver_stops == 1
        await assert_slot_free()


# ---------------------------------------------------------------------------
# the cap
# ---------------------------------------------------------------------------


class TestTheCapIsArithmeticFromTheCodesOwnTimeouts:
    """No legitimate run reaches it, and a hang does not wait for the scan deadline."""

    def test_the_audit_cap_clears_every_stage_at_its_own_ceiling(self) -> None:
        stages = (
            browser_slot.LAUNCH_ALLOWANCE_S
            + 2 * technical_audit.PAGE_TIMEOUT_MS / 1000  # the load, then the fallback
            + (crawl.MAX_REDIRECTS + 1) * technical_audit.SIDE_FETCH_TIMEOUT  # a robots.txt
            + 2 * egress.SETTLE_TIMEOUT_S  # the landing checks either side of the read
            + technical_audit.SETTLE_MS / 1000  # the vitals wait
            + browser_slot.EVALUATE_ALLOWANCE_S  # page.evaluate: no timeout of its own (3.20)
            + egress.FINAL_SETTLE_TIMEOUT_S + 1.0  # the final check, and its cancellations
            + browser_slot.TEARDOWN_ALLOWANCE_S
            + browser_slot.NAME_LOOKUP_ALLOWANCE_S
        )

        cap = technical_audit.audit_run_cap_s(technical_audit.PAGE_TIMEOUT_MS)

        assert cap > stages
        # And it is a backstop, not a second deadline: not much past the sum.
        assert cap < stages + 2 * browser_slot.RUN_MARGIN_S

    @pytest.mark.parametrize("max_pages", [1, 3, crawl.DEFAULT_MAX_PAGES])
    def test_the_crawl_cap_clears_every_stage_at_its_own_ceiling(self, max_pages: int) -> None:
        one_page = (
            crawl.DEFAULT_TIMEOUT_MS / 1000  # the navigation
            + 2 * egress.SETTLE_TIMEOUT_S  # the landing checks either side of the read
            + browser_slot.EVALUATE_ALLOWANCE_S  # page.evaluate
        )
        stages = (
            browser_slot.LAUNCH_ALLOWANCE_S
            + max_pages * one_page
            + (crawl.MAX_REDIRECTS + 1) * crawl.ROBOTS_TIMEOUT  # a landing host not read yet
            + egress.FINAL_SETTLE_TIMEOUT_S + 1.0
            + browser_slot.TEARDOWN_ALLOWANCE_S
            + browser_slot.NAME_LOOKUP_ALLOWANCE_S
        )

        cap = crawl.crawl_run_cap_s(max_pages, crawl.DEFAULT_TIMEOUT_MS)

        assert cap > stages
        assert cap < stages + 2 * browser_slot.RUN_MARGIN_S

    def test_the_crawl_cap_grows_with_the_pages_and_the_navigation_timeout(self) -> None:
        base = crawl.crawl_run_cap_s(3, 12_000)

        assert crawl.crawl_run_cap_s(4, 12_000) > base
        assert crawl.crawl_run_cap_s(3, 20_000) > base
        # A page is a page: the cap never goes below one, whatever it was asked for.
        assert crawl.crawl_run_cap_s(0, 12_000) == crawl.crawl_run_cap_s(1, 12_000)

    def test_the_audit_cap_grows_with_the_page_timeout(self) -> None:
        assert technical_audit.audit_run_cap_s(40_000) > technical_audit.audit_run_cap_s(25_000)

    def test_a_hang_costs_minutes_and_not_the_scan_deadline(self) -> None:
        """3.20: the audit step that never returned used to leave the scan to its deadline."""
        deadline = scan_executor.MAX_SCAN_DURATION.total_seconds()

        assert technical_audit.audit_run_cap_s() < deadline / 4
        assert crawl.crawl_run_cap_s(crawl.DEFAULT_MAX_PAGES, crawl.DEFAULT_TIMEOUT_MS) < (
            deadline / 4
        )

    def test_the_audit_default_is_the_page_timeout_constant(self) -> None:
        assert technical_audit.audit_run_cap_s() == technical_audit.audit_run_cap_s(
            technical_audit.PAGE_TIMEOUT_MS
        )


class TestAHangEndsAsTheTimeoutAlreadyDid:
    async def test_an_audit_whose_page_read_never_returns_is_a_failed_audit_with_its_robots_facts(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt",
            body="User-agent: *\nAllow: /\nSitemap: https://prospect-site.com/sitemap.xml\n",
        )
        short_cap(monkeypatch)
        fake = Fake(evaluate="hang")
        started = time.monotonic()

        signals = await asyncio.wait_for(run("audit", monkeypatch, fake), 10)

        assert time.monotonic() - started < 5  # the cap, not the 2,400 s scan deadline
        assert (signals.ok, signals.error_code) == (False, "TIMEOUT")
        # An ordinary failed audit: what robots.txt said is kept, as for any timeout.
        assert signals.robots_txt_present and signals.has_sitemap
        assert signals.ai_crawler_access  # a verdict per agent, not an empty list
        assert fake.evaluating.is_set()
        # ... and it reads as "we could not measure", never as a finding about the site.
        (check,) = technical_audit.build_checks(signals)
        assert (check.key, check.status, check.detail_code) == (
            "site_reachable", "error", "TIMEOUT"
        )

    async def test_a_crawl_whose_page_read_never_returns_is_the_timeout_failure(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        short_cap(monkeypatch)
        fake = Fake(evaluate="hang")

        result = await asyncio.wait_for(run("crawl", monkeypatch, fake), 10)

        assert (result.ok, result.error_code) == (False, "TIMEOUT")
        assert result.text_extract == ""

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_no_new_exception_leaves_the_fetcher(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        short_cap(monkeypatch, 0.1)

        # Returns, as an unreachable site always has; `TimeoutError` does not escape.
        result = await asyncio.wait_for(run(kind, monkeypatch, Fake(evaluate="hang")), 10)

        assert result.error_code == "TIMEOUT"

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_refusal_the_guard_had_already_recorded_outranks_the_cap(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """A response from a private address, read while the page read hangs: nothing is kept."""
        short_cap(monkeypatch)

        result = await asyncio.wait_for(
            run(kind, monkeypatch, Fake(evaluate="private_then_hang")), 10
        )

        assert (result.ok, result.error_code) == (False, "ADDRESS_NOT_PUBLIC")
        if kind == "crawl":
            assert result.signals == crawl.CrawlSignals(
                final_url=SITE, registrable_domain="prospect-site.com"
            )
            assert result.text_extract == "" and result.titles == []
        else:
            assert result == technical_audit.AuditSignals(
                url=SITE, domain="prospect-site.com", ok=False, error_code="ADDRESS_NOT_PUBLIC"
            )

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_a_run_inside_its_cap_is_not_cut(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """The cap is not on the way of a legitimate run: a slow one finishes."""
        short_cap(monkeypatch, 5.0)

        result = await run(kind, monkeypatch, Fake(step_s=0.05))

        assert result.ok

    async def test_the_cap_does_not_start_until_the_slot_is_held(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Time spent waiting behind another run is not time the run was allowed."""
        caps = iter([30.0, 0.5])  # the first run gets plenty; the second, half a second
        monkeypatch.setattr(technical_audit, "audit_run_cap_s", lambda timeout_ms: next(caps))
        fake = Fake(goto="hold_first")
        monkeypatch.setattr(technical_audit, "async_playwright", fake)

        first = asyncio.create_task(technical_audit.audit_site(SITE))
        await asyncio.wait_for(fake.holding.wait(), 5)
        second = asyncio.create_task(technical_audit.audit_site(SECOND_SITE))
        await asyncio.sleep(1.0)  # the second has now waited twice as long as its whole cap
        fake.release.set()
        first_signals, second_signals = await asyncio.wait_for(asyncio.gather(first, second), 10)

        assert first_signals.ok
        assert second_signals.ok  # its cap began when it got the browser, not when it asked


# ---------------------------------------------------------------------------
# M1 round 2 — a wait for the slot does not go on for ever
# ---------------------------------------------------------------------------


@asynccontextmanager
async def another_run_holds_the_slot() -> AsyncIterator[None]:
    """The one browser slot taken, as a run in another request or scan would hold it."""
    async with browser_slot.browser_slot("crawl", wait_s=5):
        yield


class TestAWaitForTheSlotIsBounded:
    """Every wait here is held to a test deadline of its own as well, so a missing bound
    fails a test instead of hanging the suite."""

    async def test_a_waiter_past_its_bound_gets_browser_busy_and_its_body_never_runs(self) -> None:
        entered: list[str] = []

        async def waiter() -> None:
            async with browser_slot.browser_slot("audit", wait_s=0.1):
                entered.append("the waiter's body")

        with capture_logs() as logs:
            async with another_run_holds_the_slot():
                started = time.monotonic()
                with pytest.raises(browser_slot.BrowserBusy) as busy:
                    await asyncio.wait_for(waiter(), 5)
                took = time.monotonic() - started

        assert entered == []
        assert 0.09 <= took < 2  # it waited its bound and no longer
        assert (busy.value.kind, type(busy.value.waited_ms)) == ("audit", int)
        assert busy.value.waited_ms >= 90
        # Numbers and the kind of the one that was sent away, and nothing else.
        (entry,) = [e for e in logs if e["event"] == "browser.slot_busy"]
        assert set(entry) == {"event", "log_level", "kind", "waited_ms"}
        assert entry["kind"] == "audit" and entry["waited_ms"] == busy.value.waited_ms
        # It never got in: no run of its own ended (the one `browser.memory` is the
        # holder's) and there was no wait that ended in a slot.
        assert [e["kind"] for e in logs if e["event"] == "browser.memory"] == ["crawl"]
        assert not [e for e in logs if e["event"] == "browser.slot_waited"]

    async def test_the_slot_is_free_afterwards_and_the_holder_is_not_disturbed(self) -> None:
        async with another_run_holds_the_slot(), asyncio.timeout(5):
            with pytest.raises(browser_slot.BrowserBusy):
                async with browser_slot.browser_slot("audit", wait_s=0.05):
                    pytest.fail("the waiter's body must not run")
            # Still the first one's: the waiter that gave up took nothing and freed nothing.
            with pytest.raises(browser_slot.BrowserBusy):
                async with browser_slot.browser_slot("audit", wait_s=0):
                    pytest.fail("the slot was given away while its holder was still in")

        await assert_slot_free()
        # Nothing is left queued for it either: a caller that will not wait at all gets in.
        async with browser_slot.browser_slot("audit", wait_s=0):
            pass

    async def test_a_waiter_that_gives_up_does_not_disturb_the_order_of_those_behind_it(
        self,
    ) -> None:
        entered: list[str] = []
        sent_away: list[str] = []
        release = asyncio.Event()

        async def holder() -> None:
            async with browser_slot.browser_slot("audit"):
                await release.wait()

        async def caller(name: str, wait_s: float) -> None:
            try:
                async with browser_slot.browser_slot("crawl", wait_s=wait_s):
                    entered.append(name)
            except browser_slot.BrowserBusy:
                sent_away.append(name)

        one = asyncio.create_task(holder())
        callers: list[asyncio.Task[None]] = []
        try:
            await asyncio.sleep(0.01)
            for name, wait_s in (("first", 5), ("middle", 0.1), ("last", 5)):
                callers.append(asyncio.create_task(caller(name, wait_s)))
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.3)  # the middle one's bound has passed; the holder is still in

            assert sent_away == ["middle"] and entered == []
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(one, *callers, return_exceptions=True), 5)

        assert entered == ["first", "last"]  # in the order they asked, the one that left aside
        await assert_slot_free()

    async def test_a_waiter_cancelled_as_the_slot_is_handed_to_it_passes_it_on(self) -> None:
        """The window an expiring bound falls in: the holder has left and the slot is the
        waiter's, but the waiter has not run yet. Its bound's expiry is a cancellation, so
        this is what it does to the caller behind it: nothing is lost, and it goes in."""
        entered: list[str] = []
        release = asyncio.Event()

        async def holder() -> None:
            async with browser_slot.browser_slot("audit"):
                await release.wait()

        async def waiter(name: str) -> None:
            async with browser_slot.browser_slot("crawl"):
                entered.append(name)

        one = asyncio.create_task(holder())
        tasks = [one]
        try:
            await asyncio.sleep(0.01)
            cancelled = asyncio.create_task(waiter("the cancelled one"))
            tasks.append(cancelled)
            await asyncio.sleep(0.01)
            behind = asyncio.create_task(waiter("the one behind it"))
            tasks.append(behind)
            await asyncio.sleep(0.01)
            release.set()
            await asyncio.sleep(0)  # the holder leaves: the slot is the second's, who has not run
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            await asyncio.wait_for(asyncio.gather(one, behind), 5)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        assert entered == ["the one behind it"]
        await assert_slot_free()

    async def test_a_wait_of_nothing_takes_a_free_slot_and_leaves_a_taken_one_at_once(self) -> None:
        async with browser_slot.browser_slot("crawl", wait_s=0), asyncio.timeout(5):  # free: in
            started = time.monotonic()
            with pytest.raises(browser_slot.BrowserBusy):
                async with browser_slot.browser_slot("audit", wait_s=0):  # taken: out, at once
                    pytest.fail("the slot is taken")
            assert time.monotonic() - started < 0.5

    async def test_no_bound_is_the_requests_and_it_is_read_when_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(browser_slot, "REQUEST_WAIT_S", 0.05)

        async with another_run_holds_the_slot(), asyncio.timeout(5):
            started = time.monotonic()
            with pytest.raises(browser_slot.BrowserBusy):
                async with browser_slot.browser_slot("audit"):  # says nothing: the safe one
                    pytest.fail("the slot is taken")

        assert time.monotonic() - started < 2

    async def test_an_outer_deadline_is_not_mistaken_for_the_bound(self) -> None:
        """The scan's own deadline ends a waiting audit as a cancellation, never as busy."""
        async with another_run_holds_the_slot():
            with pytest.raises(TimeoutError):  # the outer deadline's own conversion
                async with asyncio.timeout(0.1):
                    async with browser_slot.browser_slot("audit", wait_s=30):
                        pytest.fail("the slot is taken")

        await assert_slot_free()


class TestTheTwoBounds:
    def test_a_request_waits_a_minute_and_a_scans_audit_five(self) -> None:
        assert browser_slot.REQUEST_WAIT_S == 60.0
        assert browser_slot.SCAN_AUDIT_WAIT_S == 300.0

    def test_a_scans_wait_covers_the_longest_single_run_that_can_be_ahead_of_it(self) -> None:
        """One capped run ahead of it, and a margin; not a second cap beside it."""
        pages = inspect.signature(intake.run_classification).parameters["max_pages"].default
        longest = max(
            technical_audit.audit_run_cap_s(),
            crawl.crawl_run_cap_s(pages, crawl.DEFAULT_TIMEOUT_MS),
        )

        wait = browser_slot.SCAN_AUDIT_WAIT_S
        assert longest < wait < 2 * longest

    def test_a_request_gives_up_well_before_a_scan_would(self) -> None:
        assert browser_slot.REQUEST_WAIT_S < browser_slot.SCAN_AUDIT_WAIT_S / 2


@pytest.fixture
def waits_asked_for(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float | None]]:
    """What each browser run asked of the slot, as (kind, wait_s) in order. The slot is real."""
    asked: list[tuple[str, float | None]] = []
    real = browser_slot.browser_slot

    def recorder(
        kind: browser_slot.BrowserKind, *, wait_s: float | None = None, **refs: str | None
    ) -> Any:
        asked.append((kind, wait_s))
        return real(kind, wait_s=wait_s, **refs)

    monkeypatch.setattr(crawl, "browser_slot", recorder)
    monkeypatch.setattr(technical_audit, "browser_slot", recorder)
    return asked


class TestWhichBoundEachCallerAsksFor:
    async def test_a_crawl_and_an_audit_say_nothing_and_so_get_the_requests(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch,
        waits_asked_for: list[tuple[str, float | None]],
    ) -> None:
        fake = Fake()

        await run("crawl", monkeypatch, fake)
        await run("audit", monkeypatch, fake)

        assert waits_asked_for == [("crawl", None), ("audit", None)]  # None: the request's

    async def test_a_scans_audit_can_ask_for_the_longer_one(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch,
        waits_asked_for: list[tuple[str, float | None]],
    ) -> None:
        result = await run(
            "audit", monkeypatch, Fake(), slot_wait_s=browser_slot.SCAN_AUDIT_WAIT_S
        )

        assert result.ok
        assert waits_asked_for == [("audit", 300.0)]


async def run_while_the_slot_is_held(
    kind: str, monkeypatch: pytest.MonkeyPatch, fake: Fake, *, slot_wait_s: float = 0.05
) -> Any:
    """`run(kind, ...)` for a caller whose wait for the slot runs out, held to a deadline of
    the test's own: with no bound on the wait it would sit behind the holder for ever."""
    async with another_run_holds_the_slot():
        return await asyncio.wait_for(
            run(kind, monkeypatch, fake, slot_wait_s=slot_wait_s), 10
        )


class TestAWaiterPastItsBoundIsAFailedRead:
    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_it_ends_as_browser_busy_and_never_asks_for_a_browser(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        fake = Fake()

        with capture_logs() as logs:
            result = await run_while_the_slot_is_held(kind, monkeypatch, fake, slot_wait_s=0.1)

        assert (result.ok, result.error_code) == (False, "BROWSER_BUSY")
        assert fake.driver_calls == 0  # not so much as a driver: nothing was launched
        assert_everything_closed(fake, (0, 0, 0), runs=0)
        (entry,) = [e for e in logs if e["event"] == "browser.slot_busy"]
        assert entry["kind"] == kind
        await assert_slot_free()

    async def test_a_crawl_keeps_nothing(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await run_while_the_slot_is_held("crawl", monkeypatch, Fake())

        assert result.signals == crawl.CrawlSignals(
            final_url=SITE, registrable_domain="prospect-site.com"
        )
        assert result.text_extract == "" and result.titles == []

    async def test_an_audit_keeps_what_robots_txt_and_the_sitemap_said_and_nothing_else(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt",
            body=(
                "User-agent: *\nAllow: /\nSitemap: https://prospect-site.com/sitemap.xml\n\n"
                "User-agent: GPTBot\nDisallow: /\n"
            ),
        )

        signals = await run_while_the_slot_is_held("audit", monkeypatch, Fake())

        verdicts = {a.agent.token: a.verdict for a in signals.ai_crawler_access}
        assert verdicts["GPTBot"] is AccessVerdict.BLOCKED  # robots.txt was read, and is kept
        assert dataclasses.replace(signals, ai_crawler_access=[]) == technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=False, error_code="BROWSER_BUSY",
            robots_txt_present=True, robots_allows_crawl=True, has_sitemap=True,
        )

    async def test_the_classifier_records_it_as_the_reason_like_any_failed_read(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, settings: Any
    ) -> None:
        crawled = await run_while_the_slot_is_held("crawl", monkeypatch, Fake())

        outcome = await classify(crawled, settings=settings)  # a failed read costs no model call

        assert (outcome.status, outcome.reason_code, outcome.industry) == (
            "unclassifiable", "BROWSER_BUSY", None,
        )

    async def test_the_audit_reads_as_could_not_measure_and_is_never_a_finding_or_a_score(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        signals = await run_while_the_slot_is_held("audit", monkeypatch, Fake())

        (check,) = technical_audit.build_checks(signals)
        assert (check.key, check.status, check.detail_code) == (
            "site_reachable", "error", "BROWSER_BUSY"
        )
        outcome = technical_audit.score_audit(signals)
        assert (outcome.scored, outcome.score) == (False, None)  # excluded, not scored as zero


# ---------------------------------------------------------------------------
# M1 round 2 — a cap that falls before the final egress check keeps nothing the page gave
# ---------------------------------------------------------------------------


class TestACapThatFallsBeforeTheFinalEgressCheck:
    """`BrowserGuard.finish` is the last check before a page's facts are kept. A run cut
    before it returns has facts that never passed it, and a TIMEOUT that carried them
    would store them (`audit_runner` stores whatever it is handed)."""

    @staticmethod
    def _the_final_check_never_returns(monkeypatch: pytest.MonkeyPatch) -> None:
        async def never(self: Any) -> None:  # noqa: ARG001
            await asyncio.Event().wait()

        monkeypatch.setattr(egress.BrowserGuard, "finish", never)

    async def test_an_audit_keeps_what_robots_txt_said_and_nothing_the_page_gave(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt",
            body="User-agent: *\nAllow: /\nSitemap: https://prospect-site.com/sitemap.xml\n",
        )
        self._the_final_check_never_returns(monkeypatch)
        short_cap(monkeypatch)
        fake = Fake()

        signals = await asyncio.wait_for(run("audit", monkeypatch, fake), 10)

        # The page WAS read: its facts existed in the live signals when the cap fell.
        assert fake.evaluating.is_set()
        assert (signals.ok, signals.error_code) == (False, "TIMEOUT")
        assert signals.schema_types == [] and not signals.has_organization_schema
        assert (signals.h1_count, signals.word_count, signals.open_graph_tag_count) == (0, 0, 0)
        assert (signals.lcp_ms, signals.cls, signals.http_status, signals.content_age_days) == (
            None, None, None, None,
        )
        assert not (signals.has_title or signals.has_meta_description or signals.canonical_present)
        # What robots.txt and the sitemap said was read before the browser, and is kept.
        assert signals.robots_txt_present and signals.robots_allows_crawl and signals.has_sitemap
        assert signals.ai_crawler_access  # a verdict per agent, not an empty list
        # All of it at once: a fresh object holding those facts and nothing else.
        assert dataclasses.replace(signals, ai_crawler_access=[]) == technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=False, error_code="TIMEOUT",
            robots_txt_present=True, robots_allows_crawl=True, has_sitemap=True,
        )

    async def test_a_crawl_keeps_nothing(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._the_final_check_never_returns(monkeypatch)
        short_cap(monkeypatch)
        fake = Fake()

        result = await asyncio.wait_for(run("crawl", monkeypatch, fake), 10)

        assert fake.evaluating.is_set()
        assert (result.ok, result.error_code) == (False, "TIMEOUT")
        assert result.signals == crawl.CrawlSignals(
            final_url=SITE, registrable_domain="prospect-site.com"
        )
        assert result.text_extract == "" and result.titles == []

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [("refused", "ADDRESS_NOT_PUBLIC"), ("unverified", "BROWSER_ERROR")],
    )
    async def test_an_egress_outcome_the_cap_overtakes_is_still_the_outcome(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str, outcome: str,
        expected: str,
    ) -> None:
        """The run's own gate (not the guard, which records its own) refuses, or the final
        check gives up, and the browser then takes longer to close than the cap has left:
        what arrives is the cap's `TimeoutError`, with the outcome behind it on its chain."""
        fake = Fake(page_close="hang")
        if outcome == "refused":
            original = crawl.RobotsGate.allows

            async def refuses_once_the_browser_is_up(self: Any, url: str) -> bool:
                if fake.pages_opened:  # its reads before the browser started are untouched
                    raise egress.EgressRefused("prospect-site.com", "10.0.0.5")
                return await original(self, url)

            monkeypatch.setattr(crawl.RobotsGate, "allows", refuses_once_the_browser_is_up)
        else:
            _never_verify(monkeypatch)
        short_cap(monkeypatch)

        result = await asyncio.wait_for(run(kind, monkeypatch, fake), 10)

        assert (result.ok, result.error_code) == (False, expected)
        # The cap fell in the middle of the closes (the page's never returned), and the
        # browser's own close, which ends everything under it, still ran.
        assert (fake.page_closes, fake.browser_closes) == (1, 1)
        assert fake.open_browsers == 0 and fake.driver_starts == fake.driver_stops == 1
        if kind == "crawl":
            assert result.signals == crawl.CrawlSignals(
                final_url=SITE, registrable_domain="prospect-site.com"
            )
            assert result.text_extract == ""
        else:
            assert result == technical_audit.AuditSignals(
                url=SITE, domain="prospect-site.com", ok=False, error_code=expected
            )
        await assert_slot_free()

    def test_the_guard_keeps_an_outcome_the_run_raised_that_it_did_not_record_itself(self) -> None:
        guard = egress.BrowserGuard()
        first = egress.EgressRefused("a.example", "10.0.0.5")

        guard.record_outcome(first)
        guard.record_outcome(egress.EgressRefused("b.example", "10.0.0.6"))

        assert guard.refused is first  # the first refusal stands, as for the guard's own
        assert not guard.unverified

        other = egress.BrowserGuard()
        other.record_outcome(egress.EgressUnverified())

        assert other.unverified and other.refused is None


# ---------------------------------------------------------------------------
# M1 round 2 — one run per agency on the request routes
# ---------------------------------------------------------------------------


class TestOneRunPerAgency:
    """The unit; the three routes that use it are in test_agency_page_reads.py."""

    async def test_a_second_run_for_the_same_agency_is_a_429_with_retry_after(self) -> None:
        async with browser_slot.one_run_per_agency("agcy_one"):
            with pytest.raises(RateLimited) as refused:
                async with browser_slot.one_run_per_agency("agcy_one"):
                    pytest.fail("the second run must not start")

        assert refused.value.status_code == 429
        assert refused.value.problem_type == "rate-limited"
        assert refused.value.headers == {"Retry-After": "60"}
        assert refused.value.detail == (
            "Your agency already has a site being read. Try again in a minute."
        )

    async def test_another_agency_is_not_refused(self) -> None:
        async with (
            browser_slot.one_run_per_agency("agcy_one"),
            browser_slot.one_run_per_agency("agcy_two"),
        ):
            pass

    async def test_requests_in_the_same_moment_let_exactly_one_through(self) -> None:
        async def enter() -> str:
            try:
                async with browser_slot.one_run_per_agency("agcy_one"):
                    await asyncio.sleep(0.05)
                    return "ran"
            except RateLimited:
                return "refused"

        results = await asyncio.gather(enter(), enter(), enter())

        assert sorted(results) == ["ran", "refused", "refused"]

    @pytest.mark.parametrize("how", ["returns", "raises", "cancelled", "browser_busy"])
    async def test_it_is_released_on_every_way_out(self, how: str) -> None:
        inside, proceed = asyncio.Event(), asyncio.Event()

        async def the_run() -> None:
            async with browser_slot.one_run_per_agency("agcy_one"):
                inside.set()
                await proceed.wait()  # still inside: the next request finds it counted
                if how == "raises":
                    raise RuntimeError("boom")
                if how == "browser_busy":
                    async with browser_slot.browser_slot("crawl", wait_s=0.05):
                        pytest.fail("the slot is taken")

        expected = {
            "returns": None, "raises": RuntimeError,
            "cancelled": asyncio.CancelledError, "browser_busy": browser_slot.BrowserBusy,
        }[how]

        async with another_run_holds_the_slot():
            task = asyncio.create_task(the_run())
            try:
                await asyncio.wait_for(inside.wait(), 5)
                with pytest.raises(RateLimited):  # while it is inside, it counts
                    async with browser_slot.one_run_per_agency("agcy_one"):
                        pytest.fail("refused while the first is inside")
                if how == "cancelled":
                    task.cancel()
                else:
                    proceed.set()
                if expected is None:
                    await asyncio.wait_for(task, 5)
                else:
                    with pytest.raises(expected):
                        await asyncio.wait_for(task, 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        async with browser_slot.one_run_per_agency("agcy_one"):  # and once it is out, it is free
            pass

    async def test_a_refusal_changes_nothing_for_the_run_that_is_inside(self) -> None:
        async with browser_slot.one_run_per_agency("agcy_one"):
            for _ in range(3):
                with pytest.raises(RateLimited):
                    async with browser_slot.one_run_per_agency("agcy_one"):
                        pytest.fail("refused")
            with pytest.raises(RateLimited):  # still counted: the refusals did not release it
                async with browser_slot.one_run_per_agency("agcy_one"):
                    pytest.fail("refused")

        async with browser_slot.one_run_per_agency("agcy_one"):
            pass


# ---------------------------------------------------------------------------
# M1 round 3 — at most four agencies at once on the request routes
# ---------------------------------------------------------------------------

AGENCY_BUSY = "Your agency already has a site being read. Try again in a minute."
CRAWLER_BUSY = "The crawler is busy with other pages right now. Try again in a minute."


class TestAtMostFourAgenciesAtOnce:
    """One per agency bounds an agency, not the total: left alone, the reads waiting at once
    would be as many as the agencies asking, each holding a pooled database connection."""

    @staticmethod
    async def four_inside(stack: AsyncExitStack, count: int | None = None) -> None:
        for number in range(browser_slot.MAX_REQUEST_READS if count is None else count):
            await stack.enter_async_context(
                browser_slot.one_run_per_agency(f"agcy_inside_{number}")
            )

    async def test_the_fifth_agency_is_refused_while_four_are_inside(self) -> None:
        async with AsyncExitStack() as stack:
            await self.four_inside(stack)

            with pytest.raises(RateLimited) as refused:
                async with browser_slot.one_run_per_agency("agcy_fifth"):
                    pytest.fail("a fifth agency must not get in")

        assert refused.value.status_code == 429
        assert refused.value.problem_type == "rate-limited"
        assert refused.value.headers == {"Retry-After": "60"}
        # The sentence is about the crawler, not about any agency.
        assert refused.value.detail == CRAWLER_BUSY
        assert "agency" not in refused.value.detail.lower()

    async def test_the_fifth_gets_in_as_soon_as_one_of_the_four_leaves(self) -> None:
        async with AsyncExitStack() as outer:
            await self.four_inside(outer, browser_slot.MAX_REQUEST_READS - 1)
            async with browser_slot.one_run_per_agency("agcy_fourth"):
                with pytest.raises(RateLimited):
                    async with browser_slot.one_run_per_agency("agcy_fifth"):
                        pytest.fail("four are inside")
            # the fourth has left
            async with browser_slot.one_run_per_agency("agcy_fifth"):
                pass

    async def test_an_agency_already_inside_is_told_about_its_own_read_whatever_the_cap(
        self,
    ) -> None:
        async with AsyncExitStack() as stack:
            await self.four_inside(stack)

            with pytest.raises(RateLimited) as refused:
                async with browser_slot.one_run_per_agency("agcy_inside_0"):
                    pytest.fail("this agency is already inside")

        assert refused.value.detail == AGENCY_BUSY  # the more specific of the two

    @pytest.mark.parametrize("how", ["returns", "raises", "cancelled", "refused_as_busy"])
    async def test_a_place_comes_back_on_every_way_out(self, how: str) -> None:
        inside, proceed = asyncio.Event(), asyncio.Event()

        async def the_leaver() -> None:
            async with browser_slot.one_run_per_agency("agcy_leaver"):
                inside.set()
                await proceed.wait()
                if how == "raises":
                    raise RuntimeError("boom")
                if how == "refused_as_busy":
                    # What `run_classification` and `run_audit(refuse_when_busy=True)` raise
                    # for a read that waited its whole bound for the browser.
                    raise browser_slot.crawler_busy()

        expected = {
            "returns": None, "raises": RuntimeError, "cancelled": asyncio.CancelledError,
            "refused_as_busy": RateLimited,
        }[how]

        async with AsyncExitStack() as stack:
            await self.four_inside(stack, browser_slot.MAX_REQUEST_READS - 1)
            task = asyncio.create_task(the_leaver())
            try:
                await asyncio.wait_for(inside.wait(), 5)
                with pytest.raises(RateLimited):  # four inside now: the fifth is refused
                    async with browser_slot.one_run_per_agency("agcy_fifth"):
                        pytest.fail("four are inside")
                if how == "cancelled":
                    task.cancel()
                else:
                    proceed.set()
                if expected is None:
                    await asyncio.wait_for(task, 5)
                else:
                    with pytest.raises(expected):
                        await asyncio.wait_for(task, 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            async with browser_slot.one_run_per_agency("agcy_fifth"):  # the place came back
                pass

    def test_the_cap_is_four_and_leaves_most_of_the_pool_to_everything_else(
        self, settings: Any
    ) -> None:
        pool = settings.database_pool_size + settings.database_max_overflow

        assert browser_slot.MAX_REQUEST_READS == 4
        assert pool - browser_slot.MAX_REQUEST_READS >= 10


# ---------------------------------------------------------------------------
# M1 round 3 — the slot's log lines carry our own id
# ---------------------------------------------------------------------------


class TestTheSlotsLogLinesCarryOurOwnId:
    async def test_an_audits_three_lines_carry_its_scan_id(self) -> None:
        sid = ids.new_id(ids.SCAN)
        release = asyncio.Event()

        async def holder() -> None:
            async with browser_slot.browser_slot("crawl"):  # no id: a line without one
                await release.wait()

        async def waiter() -> None:
            async with browser_slot.browser_slot("audit", scan_id=sid):
                pass

        with capture_logs() as logs:
            one = asyncio.create_task(holder())
            await asyncio.sleep(0.01)
            with pytest.raises(browser_slot.BrowserBusy):
                async with browser_slot.browser_slot("audit", wait_s=0.05, scan_id=sid):
                    pytest.fail("the slot is taken")
            two = asyncio.create_task(waiter())
            await asyncio.sleep(0.05)
            release.set()
            await asyncio.wait_for(asyncio.gather(one, two), 5)

        lines = {
            event: [e for e in logs if e["event"] == event and e["kind"] == "audit"]
            for event in ("browser.slot_busy", "browser.slot_waited", "browser.memory")
        }
        for event, entries in lines.items():
            (entry,) = entries
            assert entry["scan_id"] == sid, event
            assert "client_id" not in entry, event
        assert set(lines["browser.slot_busy"][0]) == {
            "event", "log_level", "kind", "waited_ms", "scan_id"
        }
        assert set(lines["browser.slot_waited"][0]) == {
            "event", "log_level", "kind", "waited_ms", "scan_id"
        }
        # The holder gave no id, so its own line has none.
        (holders,) = [e for e in logs if e["event"] == "browser.memory" and e["kind"] == "crawl"]
        assert "scan_id" not in holders and "client_id" not in holders

    async def test_a_crawls_lines_carry_its_client_id(self) -> None:
        cid = ids.new_id(ids.CLIENT)

        with capture_logs() as logs:
            async with browser_slot.browser_slot("crawl", client_id=cid):
                pass

        (entry,) = [e for e in logs if e["event"] == "browser.memory"]
        assert entry["client_id"] == cid and "scan_id" not in entry

    async def test_only_an_id_of_ours_is_ever_logged(self) -> None:
        """A URL, a domain, or an id of the wrong kind handed in by mistake is dropped."""
        sid, cid = ids.new_id(ids.SCAN), ids.new_id(ids.CLIENT)

        with capture_logs() as logs:
            async with browser_slot.browser_slot(
                "audit", scan_id="https://prospect-site.com/page", client_id=sid
            ):
                pass
            async with browser_slot.browser_slot(
                "crawl", scan_id=cid, client_id="prospect-site.com"
            ):
                pass

        lines = [e for e in logs if e["event"] == "browser.memory"]
        assert len(lines) == 2
        for entry in lines:
            assert "scan_id" not in entry and "client_id" not in entry
            assert not any("prospect-site" in str(value) for value in entry.values())

    @pytest.mark.parametrize("kind", ["crawl", "audit"])
    async def test_the_fetchers_pass_their_id_down_and_never_a_url(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        sid, cid = ids.new_id(ids.SCAN), ids.new_id(ids.CLIENT)
        passed = {"scan_id": sid} if kind == "audit" else {"client_id": cid}

        with capture_logs() as logs:
            result = await run(kind, monkeypatch, Fake(), **passed)

        assert result.ok
        slot_lines = [e for e in logs if e["event"].startswith("browser.")]
        (entry,) = slot_lines
        assert (entry.get("scan_id"), entry.get("client_id")) == (
            (sid, None) if kind == "audit" else (None, cid)
        )
        assert not any("prospect-site" in str(v) for e in slot_lines for v in e.values())


# ---------------------------------------------------------------------------
# M1 round 3 — a browser error keeps nothing the page gave
# ---------------------------------------------------------------------------


def _page_facts(signals: Any) -> tuple[Any, ...]:
    """Every field of an audit that a PAGE gives, as against robots.txt and the sitemap."""
    return (
        signals.http_status, signals.last_modified, signals.load_event_reached,
        signals.schema_types, signals.has_organization_schema, signals.has_localbusiness_schema,
        signals.has_faq_schema, signals.has_product_schema, signals.has_title,
        signals.has_meta_description, signals.canonical_present, signals.open_graph_tag_count,
        signals.meta_robots_noindex, signals.h1_count, signals.word_count,
        signals.structured_date, signals.content_age_days, signals.lcp_ms, signals.cls,
        signals.inp_ms,
    )


class TestABrowserErrorKeepsNothingThePageGave:
    """`BROWSER_ERROR` used to return the live signals, with whatever the page had given by
    the time the browser failed. Those facts may never have passed the final egress check
    (`BrowserGuard.finish`), and what an audit is handed `audit_runner` stores, and what a
    crawl is handed its route returns."""

    @pytest.mark.parametrize(
        "path",
        [
            "egress_refused", "egress_unverified", "launch_timeout", "page_timeout",
            "navigation_timeout", "the_cap", "renderer_dies", "browser_close_error",
        ],
    )
    async def test_no_failed_audit_carries_a_page_reading(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """The invariant, over every way an audit fails after the browser has been asked for."""
        ways: dict[str, dict[str, str]] = {
            "egress_refused": {}, "egress_unverified": {},
            "launch_timeout": {"launch": "timeout"}, "page_timeout": {"new_page": "timeout"},
            "navigation_timeout": {"goto": "timeout"}, "the_cap": {"evaluate": "hang"},
            "renderer_dies": {"evaluate": "renderer_dies"},
            "browser_close_error": {"browser_close": "error"},
        }
        if path == "egress_refused":
            _refuse_the_landing(monkeypatch)
        if path == "egress_unverified":
            _never_verify(monkeypatch)
        if path == "the_cap":
            short_cap(monkeypatch)

        signals = await asyncio.wait_for(run("audit", monkeypatch, Fake(**ways[path])), 10)

        assert signals.ok is False
        nothing = technical_audit.AuditSignals(url=SITE, domain="prospect-site.com")
        assert _page_facts(signals) == _page_facts(nothing)

    async def test_an_audit_whose_renderer_dies_after_the_landing_check_keeps_no_page_fact(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        robots_web.serve(
            f"{SITE}/robots.txt",
            body="User-agent: *\nAllow: /\nSitemap: https://prospect-site.com/sitemap.xml\n",
        )
        fake = Fake(evaluate="renderer_dies")

        signals = await asyncio.wait_for(run("audit", monkeypatch, fake), 10)

        assert fake.evaluating.is_set()  # it got as far as reading the page
        assert (signals.ok, signals.error_code) == (False, "BROWSER_ERROR")
        # The page had answered before its renderer died; none of that is kept.
        assert signals.http_status is None
        # What robots.txt and the sitemap said was read before the browser, and is.
        assert signals.robots_txt_present and signals.robots_allows_crawl and signals.has_sitemap
        assert signals.ai_crawler_access
        assert dataclasses.replace(signals, ai_crawler_access=[]) == technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=False, error_code="BROWSER_ERROR",
            robots_txt_present=True, robots_allows_crawl=True, has_sitemap=True,
        )
        (check,) = technical_audit.build_checks(signals)
        assert (check.key, check.status, check.detail_code) == (
            "site_reachable", "error", "BROWSER_ERROR"
        )

    async def test_an_audit_whose_browser_will_not_close_after_the_read_keeps_no_page_fact(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = Fake(browser_close="error")

        signals = await asyncio.wait_for(run("audit", monkeypatch, fake), 10)

        assert fake.evaluating.is_set()  # the facts were read, then the close failed
        assert (signals.ok, signals.error_code) == (False, "BROWSER_ERROR")
        assert signals.schema_types == [] and not signals.has_organization_schema
        assert (signals.h1_count, signals.word_count, signals.open_graph_tag_count) == (0, 0, 0)
        assert (signals.lcp_ms, signals.cls, signals.http_status, signals.content_age_days) == (
            None, None, None, None,
        )
        assert dataclasses.replace(signals, ai_crawler_access=[]) == technical_audit.AuditSignals(
            url=SITE, domain="prospect-site.com", ok=False, error_code="BROWSER_ERROR",
        )

    async def test_a_crawl_whose_browser_will_not_close_after_its_pages_were_read_keeps_nothing(
        self, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = Fake(browser_close="error")

        result = await asyncio.wait_for(run("crawl", monkeypatch, fake), 10)

        assert fake.evaluating.is_set()
        assert (result.ok, result.error_code) == (False, "BROWSER_ERROR")
        assert result.signals == crawl.CrawlSignals(
            final_url=SITE, registrable_domain="prospect-site.com"
        )
        assert result.text_extract == "" and result.titles == []

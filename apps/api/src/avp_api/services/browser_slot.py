"""One browser per API process at a time — M1, 2026-10-02.

WHY ONE
-------
Chromium is launched in exactly two places: `crawl.crawl_site` (a client being
created with classification on) and `technical_audit.audit_site` (a scan's audit,
and `POST /scans/{id}/audit`). Measured on the API image, one heavy page's audit
peaks near 490 MB of process memory, a crawl near 310 MB, and a crawl and an audit
at the same time near 586 MB — over a 512 MB limit. Nothing stopped them
overlapping: a scan's audit while a client is created, two scans at once, or an
audit started by hand during a scan. `browser_slot` makes a second one wait for
the first, in the order they asked.

WHAT IT COVERS
--------------
The whole browser lifetime, from before `async_playwright()` starts to after it
exits, and nothing else. The robots.txt and sitemap fetches, the redirects the
gate follows, scoring and everything else a scan does run while another run holds
the slot; only a second browser waits. Callers say what they are (`kind`), so the
log can say who waited.

WHAT KEEPS IT FROM BEING HELD FOR EVER
--------------------------------------
Making runs take turns makes a hang expensive: one browser that never comes back
holds every run behind it. So each caller puts a cap on its own run, inside the
slot (`crawl.crawl_run_cap_s`, `technical_audit.audit_run_cap_s`), and closes what
it opened on every way out (`close_browser`, each close bounded by
`CLOSE_TIMEOUT_S`). The cap's arithmetic is in those two functions; the
allowances they share, for the steps that have no timeout of their own, are here.

WHAT KEEPS A CALLER FROM WAITING FOR EVER (M1 round 2)
------------------------------------------------------
The same turn-taking makes every wait for the slot a place a request can be held,
and three request routes start a browser run inside the request. So the wait is
bounded too: past its bound a caller gets `BrowserBusy`, never launches a browser
and leaves the queue (the others keep their places, in order; no slot is lost).
Both fetchers turn it into an ordinary failed result with the code `BROWSER_BUSY`,
nothing kept. The two bounds, `REQUEST_WAIT_S` and `SCAN_AUDIT_WAIT_S`, and their
arithmetic, are below.

WHAT A BUSY BROWSER MEANS FOR WHOEVER ASKED (M1 round 3)
--------------------------------------------------------
It depends on who asked. A scan's audit (phase 6) records the failed audit with
`BROWSER_BUSY` and the scan goes on: nobody is waiting on it, and a scan has a
report to finish. A REQUEST (a client added or reclassified, `POST /scans/{id}/audit`)
writes NOTHING and answers `429` with `Retry-After` (`crawler_busy`): a client left
`unclassifiable`, or an audit stored `failed`, because the crawler happened to be
busy would say something about the client's site that is not true of it, and would
replace what was stored before. `intake.run_classification` and
`audit_runner.run_audit(refuse_when_busy=True)` do it, before they touch a row.

WHO MAY QUEUE (M1 round 2, and 3)
---------------------------------
A bound alone leaves one agency free to queue any number of runs, each holding a
request and a pooled database connection (the pool is 10 + 5) until its bound runs
out. So on the three request routes (a client added with classification on,
`POST /clients/{id}/reclassify`, `POST /scans/{id}/audit`) an agency may have ONE
run queued or running at a time: a second is a 429 (`one_run_per_agency`). And only
`MAX_REQUEST_READS` agencies at once, however many there are: a further one is
refused with the 429 that says the crawler is busy, which says nothing about any
agency. A scan's own audit (phase 6) is not one of them: it is never counted and
never refused.

PER EVENT LOOP
--------------
The semaphore is kept per running loop. A semaphore binds to the loop that first
waits on it, so one shared by every loop would raise, or block, for a caller on
another: anything that starts a second loop (a thread running `asyncio.run`) must
not inherit another's holder. In production there is one loop, so there is one slot
per process, which is the rule. (The test suite runs every test on ONE session-wide
loop, so there the slot is shared by all of them: `test_browser_slot` resets it, and
the per-agency count, after each of its tests, so a test that fails while holding
either cannot fail the ones after it.)

WHAT IT LOGS
------------
  `browser.slot_waited`  `kind`, `waited_ms`: a caller had to wait, and then got in.
  `browser.slot_busy`    `kind`, `waited_ms`: a caller's wait ran out, and it was sent
                         away without a browser (`BrowserBusy`).
  `browser.memory`       `kind` and the numbers in `memory_stats`, as a run ends and
                         before the slot is given back, so the figures are this run's
                         and not the next one's.
  `browser.close_failed` `part`, `error` (the exception's class, never its message):
                         a page or a context would not close, or the browser did not
                         in time.
The first three also carry OUR OWN id for what the run was for (M1 round 3): `scan_id`
for an audit, `client_id` for a crawl, so a wait or a refusal can be tied to its scan
or client. Only an id of ours is ever logged (`_refs`): a URL or a domain handed in
by mistake is dropped. Otherwise numbers, names of our own parts and class names only.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, Protocol

import structlog

from .. import ids
from ..errors import RateLimited
from . import memory_stats

logger = structlog.get_logger(__name__)

BrowserKind = Literal["crawl", "audit"]

# The error code both fetchers give a run that never got the browser: its wait for the
# slot ran out. Stored as an audit's error code and as a client's classification reason.
BROWSER_BUSY = "BROWSER_BUSY"

# ---------------------------------------------------------------------------
# How long a caller waits for the slot before it gives up (`BrowserBusy`).
#
# Two bounds, because two kinds of caller wait.
#
# A REQUEST: a client added with classification on, a reclassify, `POST
# /scans/{id}/audit`. A person is at a screen, and the request holds one pooled
# database connection for as long as it waits (the pool is 10 + 5), so the wait has
# to end long before enough of them could hold every connection. The intake's own
# acceptance criterion is a classified industry within 30 s (product-spec §7), and a
# browser run takes a few seconds, so 60 s is twice that budget: a request that has
# waited this long has already used up the time a person is promised, and is told so
# instead of being kept waiting.
REQUEST_WAIT_S = 60.0
# A SCAN'S AUDIT (phase 6). Nobody is at a screen and it holds only the scan's own
# connection, and what giving up costs is the audit's findings: the scan goes on,
# scored without a Technical Foundation (excluded, never a zero). So it waits longer
# than a request does, but not for long: its wait is also time the scan's 2,400 s
# deadline has to clear (`scan_executor.MAX_SCAN_DURATION` works the sum out, and the
# deadline test in `test_scan_executor` pins it). 120 s is the founder's decision
# (launch checklist 3.35); it was 300 s.
#
# WHAT 120 s COVERS: a run ahead that finishes normally. Runs take seconds: the audits
# timed for M1 (2026-10-02, 29 runs on real sites and a local page, in and out of a
# container) took 42 s at the most, so a scan's audit gets its browser behind a run
# that is doing its work.
#
# WHAT IT NO LONGER COVERS: one capped run ahead. A run that hangs holds the slot until
# its own cap cuts it. The two kinds of run, each at its cap:
#
#     the intake's crawl, 3 pages         266   crawl.crawl_run_cap_s(3, 12 s)
#     another scan's audit              244.5   technical_audit.audit_run_cap_s()
#
# Both are longer than 120 s. So a scan's audit that queues behind a run that hangs, with
# more than 120 s of its cap still to run, now ends as `BROWSER_BUSY` after 120 s and the
# scan goes on without a Technical Foundation, as it does for any other failed audit. (At
# 300 s it waited such a run out, with 34 s over the longer of the two, and got its
# audit; the price was 180 s more in the worst case.)
SCAN_AUDIT_WAIT_S = 120.0

# ---------------------------------------------------------------------------
# What a run's cap allows for the steps the code gives no timeout of its own.
#
# The rest of each cap is the code's own timeouts, added up in `crawl_run_cap_s`
# and `audit_run_cap_s`. These are the terms those two share. Each is generous on
# purpose: a cap exists to catch a hang, and a legitimate run must never reach it.
# ---------------------------------------------------------------------------

# Starting the browser. Playwright bounds `launch` itself, at its own default of
# 30 s (`BrowserType.launch`, `timeout=30_000`); that is the figure.
LAUNCH_ALLOWANCE_S = 30.0
# `page.evaluate` takes no timeout and is outside `set_default_timeout`'s reach: it
# is the audit step launch checklist 3.20 is about. One call is a single read of the
# DOM and returns in milliseconds on a page that is alive. This is for a page whose
# main thread is busy, which is slow and not hung.
EVALUATE_ALLOWANCE_S = 15.0
# Each close of a page, a context or a browser, and so the most `close_browser` can
# add per part. A close is a local round trip that takes well under a second.
CLOSE_TIMEOUT_S = 10.0
# Three closes, each at its bound, and the driver's stop after them.
TEARDOWN_ALLOWANCE_S = 3 * CLOSE_TIMEOUT_S + 5.0
# The egress guard resolves the name of every host a navigation touches
# (`egress.resolve_public`), and a lookup has no timeout of ours: it ends when the
# resolver gives up, about 10 s per name server. A navigation touches a handful.
NAME_LOOKUP_ALLOWANCE_S = 30.0
# The driver starting, and the few calls that set a context up.
RUN_MARGIN_S = 15.0


# One semaphore per running loop. Weak, so a loop that is gone takes its slot with
# it. (A loop that ever had a waiter is held by its own semaphore and so lives as
# long as this entry does; there is one loop in production, and a test's is small.)
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _slots.get(loop)
    if slot is None:
        slot = _slots[loop] = asyncio.Semaphore(1)
    return slot


class BrowserBusy(Exception):  # noqa: N818 - named for what the caller was told, like EgressRefused
    """The slot stayed taken for the caller's whole wait: nothing was started, nothing kept.

    Not a failure of the page and not of the browser: another run held the one slot
    and the caller's bound (`wait_s`) ran out. Both fetchers catch it and return a
    failed result with the code `BROWSER_BUSY` (`crawl_site`, `audit_site`), so it
    never reaches a route. Carries numbers and the kind only.
    """

    def __init__(self, kind: BrowserKind, waited_ms: int) -> None:
        super().__init__(f"the browser slot stayed taken for {waited_ms} ms")
        self.kind = kind
        self.waited_ms = waited_ms


def _refs(scan_id: str | None, client_id: str | None) -> dict[str, str]:
    """The id the slot's log lines carry for what the run was for: ours, and nothing else.

    `scan_id` for an audit, `client_id` for a crawl, so a wait or a refusal can be tied to
    the scan or the client it was for. Only a value that is one of our own ids is kept: a
    URL or a domain handed in by mistake is dropped here and never reaches a log line.
    """
    found: dict[str, str] = {}
    if scan_id is not None and ids.is_valid(scan_id, ids.SCAN):
        found["scan_id"] = scan_id
    if client_id is not None and ids.is_valid(client_id, ids.CLIENT):
        found["client_id"] = client_id
    return found


@asynccontextmanager
async def browser_slot(
    kind: BrowserKind,
    *,
    wait_s: float | None = None,
    scan_id: str | None = None,
    client_id: str | None = None,
) -> AsyncIterator[None]:
    """Hold the one browser slot of this process for the body. Waits, in order, if it is taken.

    Wrap the whole browser lifetime, `async_playwright()` included. It is given back
    on every way out of the body: a return, an exception, a cancellation.

    **The wait is bounded by `wait_s`** (`SCAN_AUDIT_WAIT_S` for a scan's audit). Past
    it this raises `BrowserBusy` and the body never runs; the caller leaves the queue
    without taking the slot, and the callers behind it keep their places. `None`, the
    default, is a request's bound, `REQUEST_WAIT_S`, the shorter of the two, so a
    caller that does not say gets the safe one; it is read when called, so a test can
    shorten it. `wait_s=0` takes the slot if it is free and gives up at once if not.

    `scan_id` (an audit) or `client_id` (a crawl) is put on this call's log lines
    (`_refs`): our own id for what the run is for, and never anything else.
    """
    slot = _slot()
    refs = _refs(scan_id, client_id)
    waiting = slot.locked()  # taken, or others already queued for it: this call will wait
    began = time.monotonic()
    try:
        async with asyncio.timeout(REQUEST_WAIT_S if wait_s is None else wait_s):
            await slot.acquire()
    except TimeoutError:
        # Only this bound's own expiry: an outer deadline or a cancellation arrives as
        # `CancelledError`, which `asyncio.timeout` leaves alone. `Semaphore.acquire`
        # takes a cancelled waiter out of its queue (and hands a wake-up it was given
        # on to the next), so there is nothing to give back here.
        waited_ms = round((time.monotonic() - began) * 1000)
        logger.warning("browser.slot_busy", kind=kind, waited_ms=waited_ms, **refs)
        raise BrowserBusy(kind, waited_ms) from None
    try:
        if waiting:
            logger.info(
                "browser.slot_waited",
                kind=kind, waited_ms=round((time.monotonic() - began) * 1000), **refs,
            )
        yield
    finally:
        try:
            _log_memory(kind, refs)
        finally:
            slot.release()


# ---------------------------------------------------------------------------
# What a request is told when it cannot have the browser, and who may ask
# ---------------------------------------------------------------------------

# Both 429s ask for the same minute: a run is a few seconds, and a request's own wait for
# the slot is bounded at `REQUEST_WAIT_S`.
REQUEST_RETRY_AFTER_S = 60
# This agency already has a read queued or running on a request route.
AGENCY_BUSY_DETAIL = "Your agency already has a site being read. Try again in a minute."
# The crawler is full: the cap below, or a read that waited its whole bound for the
# browser. It says nothing about any agency, or about the site, so whoever asked may be
# shown it.
CRAWLER_BUSY_DETAIL = "The crawler is busy with other pages right now. Try again in a minute."

# How many agencies may have a read queued or running on the request routes at once. The
# rule of one per agency bounds an agency, not the total: left alone, the number of
# waiting reads would be the number of agencies asking, and each holds one pooled database
# connection (the pool is 10 + 5 = 15) for as long as it waits for the browser, up to
# `REQUEST_WAIT_S` (60 s), and then while it runs. The browser serves one read at a time,
# so a fifth could only wait behind four; four leave 11 connections for every other
# request, for scans and for their heartbeats.
MAX_REQUEST_READS = 4

# The agencies with a run queued or running on a request route. One process and one
# event loop, so a plain set: `one_run_per_agency` checks and adds with no `await`
# between the two, so two requests cannot both pass.
_agencies_with_a_run: set[str] = set()


class CrawlerBusy(RateLimited):
    """The 429 `crawler_busy` returns: a `RateLimited` that says nothing was launched.

    A type of its own so a route can tell it from any other 429 and give back the
    classification units it had taken (F3: the browser was never started, nothing was spent)."""


def crawler_busy() -> CrawlerBusy:
    """The 429 for a request that cannot have the browser, with `Retry-After`.

    Raised when the cap above is full, and by `intake.run_classification` and
    `audit_runner.run_audit(refuse_when_busy=True)` when their read waited its whole
    bound for the slot (`BROWSER_BUSY`): in that case nothing was read and nothing is
    written. A 429 and not a 5xx because it is retryable and the web shows a 4xx's own
    detail, where a 5xx would read as "we could not reach the server".
    """
    return CrawlerBusy(
        detail=CRAWLER_BUSY_DETAIL, headers={"Retry-After": str(REQUEST_RETRY_AFTER_S)}
    )


@asynccontextmanager
async def one_run_per_agency(agency_id: str) -> AsyncIterator[None]:
    """One browser run per agency, and `MAX_REQUEST_READS` agencies, on the request routes.

    Wrap what a request does that can start a browser run (the whole call into
    `intake.run_classification` or `audit_runner.run_audit`), AFTER the agency check
    that makes another agency's id a 404. Two refusals, both `RateLimited` (429,
    `Retry-After`), both before anything is done:

    - this agency already has a read inside: `AGENCY_BUSY_DETAIL`. Checked first, so an
      agency is told about its own read even when the cap is also full;
    - `MAX_REQUEST_READS` other agencies are inside: `CRAWLER_BUSY_DETAIL`.

    The check and the add have no `await` between them. The agency is released on every
    way out of the body: a return, an exception, a cancellation (a caller who went
    away), and a read that was refused as busy.

    **Not for a scan's own audit.** `scan_executor` calls `audit_runner.run_audit`
    directly and never comes through here, so a scan is never refused by an agency's
    request or by the cap, and its audit never counts against either.
    """
    if agency_id in _agencies_with_a_run:
        raise RateLimited(
            detail=AGENCY_BUSY_DETAIL, headers={"Retry-After": str(REQUEST_RETRY_AFTER_S)}
        )
    if len(_agencies_with_a_run) >= MAX_REQUEST_READS:
        raise crawler_busy()
    _agencies_with_a_run.add(agency_id)
    try:
        yield
    finally:
        _agencies_with_a_run.discard(agency_id)


def _log_memory(kind: BrowserKind, refs: dict[str, str]) -> None:
    try:
        logger.info("browser.memory", kind=kind, **refs, **memory_stats.snapshot())
    except Exception:  # noqa: BLE001 - a memory reading is never worth a run, or the slot
        return


class _Closable(Protocol):
    async def close(self) -> None: ...


async def close_browser(
    browser: _Closable, *, context: _Closable | None = None, page: _Closable | None = None
) -> None:
    """Close what a run opened, the page first and the browser last. Call it from a `finally`.

    `context` and `page` are None when the run never got as far as opening them.

    A page or a context that will not close is logged and left to the browser's own
    close, which tears down everything under it: the facts were already read and
    checked, and the browser is the thing that must not outlive the run. The browser
    is closed whatever happened to the others, a second cancellation included.

    Every close is bounded by `CLOSE_TIMEOUT_S`, so a browser that has stopped
    answering cannot hold the slot. The browser's close differs in one way: an
    error it RAISES is the caller's to handle, as it always was (a close that fails
    while an egress outcome is on its way out replaces it, and the callers' handlers
    read the outcome back; see `crawl_site`). Only its running out of time is
    logged and let go, because leaving the driver ends a browser that would not.
    """
    try:
        for part, opened in (("page", page), ("context", context)):
            if opened is None:
                continue
            try:
                await _close(opened)
            except Exception as exc:  # noqa: BLE001 - the browser's close below covers it
                _close_failed(part, exc)
    finally:
        try:
            await _close(browser)
        except TimeoutError as exc:
            _close_failed("browser", exc)


async def _close(thing: _Closable) -> None:
    async with asyncio.timeout(CLOSE_TIMEOUT_S):
        await thing.close()


def _close_failed(part: str, exc: BaseException) -> None:
    # The class, never the message: a Playwright error's text can carry a page's address.
    logger.warning("browser.close_failed", part=part, error=type(exc).__name__)

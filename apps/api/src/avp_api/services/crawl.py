"""Playwright crawl — §5.4 step 1, "crawl homepage/key pages".

=============================================================================
FACTS-ONLY BOUNDARY
=============================================================================

This module is where third-party page content enters the process, so the rule
lives here explicitly:

  Raw page text exists ONLY inside `CrawlResult`, which is an in-memory
  dataclass. It is passed to the classifier, used to derive facts, and then
  discarded when the request ends. It is never persisted, never returned by an
  API endpoint, and never rendered.

`CrawlResult` is deliberately NOT a SQLAlchemy model and has no `to_orm()`. The
only things that reach the database from a crawl are the derived facts on
`CrawlSignals` — counts, booleans, schema.org type names, and the client's own
brand name.

The distinction that makes this legitimate: we send the page text to a
classifier the way a human would read a page to decide what business it is.
What we keep is the conclusion, not the copy.
=============================================================================
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlsplit

import httpx
import structlog
import tldextract
from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from ..config import Settings, get_settings
from . import egress
from .ai_crawlers import CrawlPolicy, crawl_policy
from .browser_slot import (
    BROWSER_BUSY,
    EVALUATE_ALLOWANCE_S,
    LAUNCH_ALLOWANCE_S,
    NAME_LOOKUP_ALLOWANCE_S,
    RUN_MARGIN_S,
    TEARDOWN_ALLOWANCE_S,
    BrowserBusy,
    browser_slot,
    close_browser,
)
from .egress import ADDRESS_NOT_PUBLIC, BrowserGuard, EgressRefused, EgressUnverified

logger = structlog.get_logger(__name__)

# A classifier needs a page's gist, not its entire text. Capping the extract
# bounds token cost, keeps latency inside the 30s acceptance budget, and limits
# how much third-party text is in memory at once.
MAX_CHARS_PER_PAGE = 6_000
MAX_TOTAL_CHARS = 18_000

DEFAULT_TIMEOUT_MS = 12_000
DEFAULT_MAX_PAGES = 4

# Identify ourselves honestly rather than impersonating a browser, and say
# where a site owner can read about us: the web app's /crawler page. Until
# 2026-09-29 the address here was a placeholder. It is built from
# `PUBLIC_WEB_BASE_URL` rather than typed, so a domain change moves it too.
#
# ONE LITERAL, READ BY THE WEB. The /crawler page prints this template filled
# in with its own address (`apps/web/src/lib/legal/crawler.ts`), and a web test
# reads these two lines back from this file, so the page cannot show a user
# agent this crawler does not send. Keep each on one line.
USER_AGENT_TEMPLATE = "Mozilla/5.0 (compatible; AVPScanBot/0.1; +{info_url}) Playwright/Chromium"
CRAWLER_INFO_PATH = "/crawler"
# The product token in the user agent: the name a robots.txt group uses to
# address this crawler (`User-agent: AVPScanBot`), matched case-insensitively.
CRAWLER_TOKEN = "AVPScanBot"


def crawler_user_agent(settings: Settings | None = None) -> str:
    """The User-Agent header on every request to a client's site.

    Pages, the files they load, robots.txt and sitemap.xml alike — the crawl
    here and the technical audit (`technical_audit.py`) both send this.
    """
    base = (settings or get_settings()).public_web_base_url.rstrip("/")
    return USER_AGENT_TEMPLATE.format(info_url=f"{base}{CRAWLER_INFO_PATH}")


# ===========================================================================
# robots.txt, obeyed — 2026-09-29
#
# Until this date the crawler read robots.txt only to report on it, and
# fetched pages whether or not the file allowed them. Agencies point this tool
# at prospects' sites, whose owners never agreed to anything, so it now obeys
# the file the way every reputable crawler does: robots.txt first, then only
# what the group for AVPScanBot (or else `*`) allows. The crawl here and the
# technical audit (`technical_audit.py`) share this gate, and
# `ai_crawlers.crawl_policy` is the one reader of the rules.
# ===========================================================================

# Reason code for a site whose robots.txt forbids the page. The intake crawl
# returns it as its error code, so the classifier's "could not classify" path
# records it as the reason; the audit stores it as its error code, and the
# score excludes Technical Foundation as CRAWL_DISALLOWED.
ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
# `ADDRESS_NOT_PUBLIC` (imported from `egress`) is its sibling: the site's
# address, or a redirect's, is not a public one, so nothing was fetched.

ROBOTS_TIMEOUT = 10.0
# Redirects followed, for robots.txt (RFC 9309 §2.3.1.2 asks for at least
# five) and for a page, whose redirects the gate follows by hand.
MAX_REDIRECTS = 5
# RFC 9309 §2.5: a crawler must read at least the first 500 KiB.
ROBOTS_MAX_CHARS = 512_000


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def path_of(url: str) -> str:
    """What a robots.txt rule is matched against: the path, and the query if any."""
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


@dataclass(frozen=True, slots=True)
class RobotsFile:
    """One fetch of an origin's robots.txt, as it came back.

    `status` is None when nothing came back: a timeout, a refused connection,
    too many redirects. `body` is empty unless something did.
    """

    status: int | None
    content_type: str = ""
    body: str = ""


@dataclass(frozen=True, slots=True)
class Followed:
    """Where a request ended after the gate followed its redirects.

    `response` is None when the last hop was not sent (it was disallowed) or
    got no answer.
    """

    url: str
    response: httpx.Response | None
    disallowed: bool = False


class RobotsGate:
    """robots.txt for this crawler, one fetch per host, asked before every page.

    Every page the crawler fetches is checked first: the homepage, the linked
    pages, and /sitemap.xml. A redirect is followed HERE, by hand, so each hop
    is checked on its own host before it is requested. The browser cannot do
    that: Playwright's request routing never sees a redirect's second request
    (tried on 2026-09-29), so a browser handed a bare domain that redirects to
    its www host would load www before anyone read www's robots.txt. The crawls
    hand the browser the address this gate resolved instead, and still check
    where the browser actually landed.

    The files a loaded page asks for itself — scripts, stylesheets, images —
    are not checked one by one. No browser-based crawler does that, and the
    /crawler page says so.
    """

    def __init__(self, *, user_agent: str, timeout: float = ROBOTS_TIMEOUT) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        self._files: dict[str, RobotsFile] = {}

    def _client(self) -> httpx.AsyncClient:
        # A client per call rather than one held open: a crawl makes a handful
        # of these requests, and a gate that owns nothing open can be asked
        # from inside a browser session without either outliving the other.
        # `guarded_transport` re-checks the resolved address at connection time
        # and connects to the address it checked (`egress.py`).
        return httpx.AsyncClient(
            timeout=self._timeout,
            headers={"User-Agent": self._user_agent},
            max_redirects=MAX_REDIRECTS,
            transport=egress.guarded_transport(),
        )

    async def _egress_ok(self, url: str) -> bool:
        """Checked before EVERY request this gate sends, on every hop.

        Raises `EgressRefused` if the host is not public; False if it does not
        resolve (the caller treats that as a request that got no answer). The
        transport checks again underneath; this check is what a client that
        replaces the transport still goes through.
        """
        return await egress.check_url(url)

    async def file(self, origin: str) -> RobotsFile:
        """This origin's robots.txt, fetched once and remembered."""
        if origin not in self._files:
            self._files[origin] = await self._fetch(origin)
        return self._files[origin]

    async def _fetch(self, origin: str) -> RobotsFile:
        # Redirects are followed by hand so each hop is egress-checked before
        # it is requested. robots.txt itself is not checked against robots.txt.
        # A refusal here (the start address, or a redirect hop) deliberately ends
        # the run as `ADDRESS_NOT_PUBLIC`, unlike a sub-resource the browser is
        # refused: robots.txt is the permission check for the whole crawl, so if
        # it cannot be fetched the crawler cannot learn the site's rules and must
        # not crawl. The one exception: a robots.txt reached only through the
        # audit's sitemap redirect (`technical_audit._fetch_side_files`), which is
        # skipped like the sitemap, because it was refused before connecting.
        url = f"{origin}/robots.txt"
        async with self._client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                if not await self._egress_ok(url):
                    return RobotsFile(status=None)
                try:
                    response = await client.get(url, follow_redirects=False)
                except (httpx.HTTPError, httpx.InvalidURL):
                    return RobotsFile(status=None)
                location = response.headers.get("location")
                if not response.is_redirect or not location:
                    return RobotsFile(
                        status=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        body=response.text[:ROBOTS_MAX_CHARS],
                    )
                try:
                    url = urljoin(url, location)
                    urlsplit(url)
                except ValueError:
                    return RobotsFile(status=None)
        return RobotsFile(status=None)

    async def policy(self, origin: str) -> CrawlPolicy:
        robots = await self.file(origin)
        return crawl_policy(robots.status, robots.body, token=CRAWLER_TOKEN)

    async def allows(self, url: str) -> bool:
        return (await self.policy(origin_of(url))).allows(path_of(url))

    async def follow(self, method: str, url: str) -> Followed:
        """Request `url`, following redirects by hand and checking each hop first.

        Each hop is checked twice before it is sent: against robots.txt, and
        for a public address (`EgressRefused` propagates out).
        """
        async with self._client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                if not await self.allows(url):
                    return Followed(url, None, disallowed=True)
                if not await self._egress_ok(url):
                    return Followed(url, None)
                try:
                    response = await client.request(method, url, follow_redirects=False)
                except (httpx.HTTPError, httpx.InvalidURL):
                    return Followed(url, None)
                location = response.headers.get("location")
                if not response.is_redirect or not location:
                    return Followed(url, response)
                try:
                    url = urljoin(url, location)
                    urlsplit(url)
                except ValueError:
                    return Followed(url, None)
        return Followed(url, None)

    async def resolve(self, url: str) -> str | None:
        """Where the homepage's redirects lead, or None if robots.txt forbids a hop.

        Asked with HEAD, so a hop costs a response's headers and no page. A
        server that answers HEAD with no redirect (or not at all) leaves the
        address as it is, and the browser's own landing is checked after.
        """
        followed = await self.follow("HEAD", url)
        return None if followed.disallowed else followed.url


# Paths most likely to state what a business actually does. Ordered by value:
# an About page names the business, Services names the offering.
CANDIDATE_PATHS: tuple[str, ...] = (
    "/about",
    "/about-us",
    "/services",
    "/what-we-do",
    "/products",
)

_WHITESPACE = re.compile(r"\s+")

# Public Suffix List extractor, pinned to the snapshot bundled with tldextract.
#
# `suffix_list_urls=()` is deliberate. tldextract's default behaviour is to
# FETCH the live PSL on first use and cache it to disk — a network call on the
# request path, with a disk write, that fails closed in a sandboxed container
# and makes domain parsing non-deterministic between environments. The bundled
# snapshot changes rarely (new TLDs are infrequent) and updates with the
# dependency, which is the right cadence for this.
# `include_psl_private_domains=True` matters for this product specifically.
# Entries like `github.io`, `myshopify.com`, and `squarespace.com` live in the
# PSL's PRIVATE section, which tldextract excludes by default. Excluded, every
# GitHub Pages site collapses to `github.io` and every Shopify store to
# `myshopify.com` — so two different prospects on the same platform would
# collide on the (agency_id, domain) unique key and the second would be
# rejected as a duplicate. Agencies prospect small businesses; hosted-platform
# domains are common, not edge cases.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)


@dataclass(slots=True)
class CrawlSignals:
    """Derived FACTS about a site. Safe to persist.

    Every field is a count, a boolean, a URL, a schema.org type name, or the
    subject's own name — all explicitly permitted by the facts-only rule.
    """

    final_url: str
    registrable_domain: str
    http_status: int | None = None
    pages_fetched: int = 0
    urls_fetched: list[str] = field(default_factory=list)

    # Structural signals — also the seed for Epic 6's technical audit.
    schema_types: list[str] = field(default_factory=list)
    h1_count: int = 0
    h2_count: int = 0
    word_count: int = 0
    has_title: bool = False
    has_meta_description: bool = False
    internal_link_count: int = 0
    external_link_count: int = 0
    detected_language: str | None = None


@dataclass(slots=True)
class CrawlResult:
    """Crawl output. **Transient — never persist this object.**

    `text_extract` holds third-party page text for the sole purpose of handing
    it to the classifier in the same request. See the module docstring.
    """

    signals: CrawlSignals
    text_extract: str
    # Page <title> values, used only as classification input. Not persisted.
    titles: list[str] = field(default_factory=list)
    ok: bool = True
    error_code: str | None = None

    def redacted(self) -> dict[str, object]:
        """Log-safe view. Never includes page text."""
        return {
            "final_url": self.signals.final_url,
            "pages_fetched": self.signals.pages_fetched,
            "word_count": self.signals.word_count,
            "ok": self.ok,
            "error_code": self.error_code,
        }


def registrable_domain(url: str) -> str:
    """Normalise a URL to its registrable domain (eTLD+1).

    Uses the Public Suffix List rather than naive dot-splitting, which gets
    `example.co.uk` (two labels are the suffix) and `practice.github.io`
    (the suffix is `github.io`) wrong in opposite directions.

    Returns "" when the host has no public suffix — an IP address, `localhost`,
    an intranet name, or a reserved TLD. The caller treats that as invalid
    input, which is correct: none of those can be a prospect's website.
    """
    extracted = _EXTRACT(url)
    if not extracted.domain or not extracted.suffix:
        return ""
    return f"{extracted.domain}.{extracted.suffix}".lower()


def normalise_url(raw: str) -> str:
    """Accept what a person would type; return something fetchable."""
    candidate = raw.strip()
    if not candidate:
        raise ValueError("URL is empty")
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.netloc:
        raise ValueError(f"Could not parse a hostname from {raw!r}")
    return candidate


def crawl_run_cap_s(max_pages: int, timeout_ms: int) -> float:
    """The most one browser run of `crawl_site` may take: the driver starting to the browser gone.

    M1, 2026-10-02. The run holds the process's one browser slot (`browser_slot`), so
    a run that hangs holds every run behind it. Some steps have no timeout of their
    own: `page.evaluate` takes none (`_fetch_page`), and nor does a call to a browser
    that has stopped answering. The cap is on the whole run, and it is the SUM of
    every step at its own ceiling, so a run in which each step does only what its own
    timeout allows cannot reach it. Only a hang does, and it is then cut here and not
    left to the scan's 2,400 s deadline.

        browser launch                       30   Playwright's own launch timeout
        per page, `max_pages` of them
            the navigation                   12   `timeout_ms`
            the landing checks before and
              after the page is read          4   2 x egress.SETTLE_TIMEOUT_S
            the page read                    15   EVALUATE_ALLOWANCE_S
          a page                             31   x 4 pages = 124 (intake asks for 3: 93)
        robots.txt of a host the gate has
          not read (a redirect only the
          browser was given)                 60   (MAX_REDIRECTS + 1) x ROBOTS_TIMEOUT
        the final egress check                3   FINAL_SETTLE_TIMEOUT_S + 1
        closing page, context, browser       35   TEARDOWN_ALLOWANCE_S
        the guard's name lookups             30   NAME_LOOKUP_ALLOWANCE_S
        the driver starting, set-up calls    15   RUN_MARGIN_S
                                           ----
        297 s for the default 4 pages; 266 s for the 3 intake asks for.

    A cap that fires ends the run as `TIMEOUT`, the code a timeout already had here.
    """
    pages = max(max_pages, 1)  # the homepage is always read
    one_page = timeout_ms / 1000 + 2 * egress.SETTLE_TIMEOUT_S + EVALUATE_ALLOWANCE_S
    return (
        LAUNCH_ALLOWANCE_S
        + pages * one_page
        + (MAX_REDIRECTS + 1) * ROBOTS_TIMEOUT
        + egress.FINAL_SETTLE_TIMEOUT_S + 1.0
        + TEARDOWN_ALLOWANCE_S
        + NAME_LOOKUP_ALLOWANCE_S
        + RUN_MARGIN_S
    )


async def crawl_site(
    url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    slot_wait_s: float | None = None,
    client_id: str | None = None,
) -> CrawlResult:
    """Fetch the homepage plus a few key pages and extract classification input.

    Never raises for an unreachable site: a site that will not load is a normal
    outcome for a prospecting tool pointed at an arbitrary URL, and it must
    surface as `ok=False` with a reason code rather than a 500.

    **robots.txt first** (2026-09-29). Nothing the file forbids this crawler is
    fetched, and a forbidden homepage means no crawl at all: `ok=False` with
    `ROBOTS_DISALLOWED`, which the classifier records as its reason.

    **One browser at a time, under a cap** (M1, 2026-10-02). The browser run holds the
    process's one browser slot (`browser_slot`), and `crawl_run_cap_s` cuts it if
    something in it never returns: it ends as `TIMEOUT`, with nothing kept.

    **A wait for the slot that runs out is a failed read too** (M1 round 2): another
    run held the browser for `slot_wait_s` (None: a request's `REQUEST_WAIT_S`; every
    caller of this function in the API is a request), so no browser was started and
    the result is `ok=False` with `BROWSER_BUSY`, nothing kept. What the caller does
    with it is its own: `intake.run_classification` refuses the request and writes
    nothing (M1 round 3).

    `client_id` is our own id for the client being read, put on the browser slot's log
    lines (`browser_slot._refs`); it is never a URL.
    """
    start_url = normalise_url(url)
    domain = registrable_domain(start_url)
    signals = CrawlSignals(final_url=start_url, registrable_domain=domain)
    user_agent = crawler_user_agent()
    gate = RobotsGate(user_agent=user_agent)

    guard = BrowserGuard()
    try:
        home_url = await gate.resolve(start_url)
        if home_url is None:
            logger.info("crawl.robots_disallowed", url=start_url)
            return _disallowed(signals)

        return await _crawl(
            gate, home_url, signals,
            user_agent=user_agent, max_pages=max_pages, timeout_ms=timeout_ms,
            guard=guard, slot_wait_s=slot_wait_s, client_id=client_id,
        )
    except BrowserBusy:
        # The browser was never started: another run held the slot for the whole wait.
        # Nothing was read, so nothing is kept, and the classifier records the code as
        # its reason as it does any other failed read.
        return CrawlResult(
            signals=_nothing_kept(start_url), text_extract="", ok=False, error_code=BROWSER_BUSY
        )
    except EgressRefused as exc:
        return _address_not_public(start_url, exc)
    except EgressUnverified:
        return _egress_unverified(start_url)
    except PlaywrightError as exc:
        # A request the guard aborted fails the navigation that asked for it. And a
        # browser that fails to close while an egress outcome is on its way out
        # replaces it with this error: the outcome stands, and nothing is kept.
        refused = guard.refused or (
            exc.__context__ if isinstance(exc.__context__, EgressRefused) else None
        )
        if refused is not None:
            return _address_not_public(start_url, refused)
        if guard.unverified:
            return _egress_unverified(start_url)
        logger.warning("crawl.playwright_error", url=start_url, error=type(exc).__name__)
        # Nothing is kept: the error can arrive after pages were read and before the final
        # egress check (`guard.finish`) said what they showed may be kept, or in place of
        # that check, and the caller returns whatever `signals` holds (M1 round 3).
        return CrawlResult(
            signals=_nothing_kept(start_url), text_extract="", ok=False, error_code="BROWSER_ERROR"
        )
    except TimeoutError:
        # The run's cap (`crawl_run_cap_s`): something in the browser never returned.
        # What the guard had already decided outranks it, as for a browser error above.
        # That includes a refusal only the robots gate raised, when the cap fell while
        # the run was closing: `_crawl` records it on the guard before the closes
        # (`BrowserGuard.record_outcome`). Otherwise nothing is kept either: the final
        # egress check (`guard.finish`) never ran, so what the pages showed is not
        # verified.
        if guard.refused is not None:
            return _address_not_public(start_url, guard.refused)
        if guard.unverified:
            return _egress_unverified(start_url)
        return CrawlResult(
            signals=_nothing_kept(start_url), text_extract="", ok=False, error_code="TIMEOUT"
        )


def _nothing_kept(url: str) -> CrawlSignals:
    """Signals as if nothing had been read: what a crawl that stopped on its egress guard keeps.

    The refusal can arrive after a page's facts were read, and the caller stores
    and returns whatever `signals` holds, so the result carries a fresh object.
    """
    return CrawlSignals(final_url=url, registrable_domain=registrable_domain(url))


def _address_not_public(url: str, exc: EgressRefused) -> CrawlResult:
    """The result for a site whose address, or a redirect's, is not public: nothing kept."""
    logger.info("crawl.address_not_public", url=url, host=exc.host)
    return CrawlResult(
        signals=_nothing_kept(url), text_extract="", ok=False, error_code=ADDRESS_NOT_PUBLIC
    )


def _egress_unverified(url: str) -> CrawlResult:
    """A connected address was never read: not a refusal, so the plain browser failure."""
    logger.warning("crawl.egress_unverified", url=url)
    return CrawlResult(
        signals=_nothing_kept(url), text_extract="", ok=False, error_code="BROWSER_ERROR"
    )


def _disallowed(signals: CrawlSignals) -> CrawlResult:
    """The result for a site whose robots.txt forbids its homepage: nothing read."""
    return CrawlResult(signals=signals, text_extract="", ok=False, error_code=ROBOTS_DISALLOWED)


async def _crawl(
    gate: RobotsGate,
    home_url: str,
    signals: CrawlSignals,
    *,
    user_agent: str,
    max_pages: int,
    timeout_ms: int,
    guard: BrowserGuard,
    slot_wait_s: float | None,
    client_id: str | None,
) -> CrawlResult:
    """The browser half of `crawl_site`, from a homepage robots.txt allows."""
    domain = signals.registrable_domain
    # One browser per process at a time, and the run under its cap (`crawl_run_cap_s`).
    # Entered in this order and left in reverse: the slot is held first, so time spent
    # waiting for it is not time the run was allowed; the driver stops, and the cap
    # ends, before the slot is given back.
    async with (
        browser_slot("crawl", wait_s=slot_wait_s, client_id=client_id),
        asyncio.timeout(crawl_run_cap_s(max_pages, timeout_ms)),
        async_playwright() as pw,
    ):
        browser = await pw.chromium.launch(
            args=["--disable-dev-shm-usage", "--no-sandbox", egress.WEBRTC_LAUNCH_ARG]
        )
        context: BrowserContext | None = None
        page: Page | None = None
        try:
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1280, "height": 900},
                java_script_enabled=True,
                # Blocked, so the route guard sees the page's requests.
                service_workers="block",
            )
            # Images and fonts contribute nothing to classification and are
            # most of the bytes; blocking them roughly halves crawl time.
            await context.route(
                re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|mp4|webm)$"),
                lambda route: asyncio.ensure_future(route.abort()),
            )
            # Registered AFTER the image route: the most recent route runs
            # first, so the guard goes first and falls back to the image
            # block for the rest. It also blocks WebSockets and removes the
            # page's WebRTC constructors.
            await guard.install(context)
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            home = await _fetch_page(page, home_url, timeout_ms, guard)
            if home is None:
                await guard.finish()
                return CrawlResult(
                    signals=signals,
                    text_extract="",
                    ok=False,
                    error_code="FETCH_FAILED",
                )
            # The browser can still land somewhere the gate did not send it —
            # a redirect only a browser is given, or a script's. That host's
            # robots.txt decides whether anything from it is kept.
            if not await gate.allows(page.url):
                await guard.finish()
                logger.info("crawl.robots_disallowed", url=page.url)
                return _disallowed(signals)
            if home.get("blocked"):
                # Every way out of the browser run goes through the final check.
                await guard.finish()
                signals.http_status = home["status"]
                signals.final_url = page.url
                return CrawlResult(
                    signals=signals,
                    text_extract="",
                    ok=False,
                    error_code=f"HTTP_{home['status']}",
                )

            signals.final_url = page.url
            signals.registrable_domain = registrable_domain(page.url) or domain
            signals.http_status = home["status"]
            _apply_page_signals(signals, home, is_home=True)

            extracts = [home["text"][:MAX_CHARS_PER_PAGE]]
            titles = [home["title"]] if home["title"] else []
            signals.pages_fetched = 1
            signals.urls_fetched.append(page.url)

            # Every candidate, best first; a page robots.txt forbids is skipped
            # without taking a slot, so the next one allowed is read instead.
            # A page that fails to load still takes its slot, as it always has.
            wanted = max_pages - 1
            attempted = skipped = 0
            for target in _pick_secondary_urls(page.url, home["links"], len(home["links"])):
                if attempted >= wanted or sum(len(e) for e in extracts) >= MAX_TOTAL_CHARS:
                    break
                # Checked before it is requested.
                if not await gate.allows(target):
                    skipped += 1
                    continue
                attempted += 1
                sub = await _fetch_page(page, target, timeout_ms, guard)
                if sub is None or sub.get("blocked"):
                    continue
                if not await gate.allows(page.url):
                    skipped += 1
                    continue
                _apply_page_signals(signals, sub, is_home=False)
                extracts.append(sub["text"][:MAX_CHARS_PER_PAGE])
                if sub["title"]:
                    titles.append(sub["title"])
                signals.pages_fetched += 1
                signals.urls_fetched.append(target)
            if skipped:
                logger.info("crawl.robots_skipped_pages", url=signals.final_url, skipped=skipped)

            # The last check before anything read is kept: a refusal ends the
            # crawl as one, and a connected address never read fails it closed.
            await guard.finish()
            text = _WHITESPACE.sub(" ", "\n\n".join(extracts)).strip()[:MAX_TOTAL_CHARS]
            signals.word_count = len(text.split())

            return CrawlResult(signals=signals, text_extract=text, titles=titles)
        except (EgressRefused, EgressUnverified) as outcome:
            # Put on the guard BEFORE the closes below: the cap can fall while they
            # run, and what arrives then is a `TimeoutError` that has forgotten this.
            guard.record_outcome(outcome)
            raise
        finally:
            # On every way out: a return, an egress refusal, any exception, the cap, a
            # cancellation. Nothing the run opened outlives it.
            await close_browser(browser, context=context, page=page)


async def _fetch_page(page, url: str, timeout_ms: int, guard: BrowserGuard) -> dict | None:  # noqa: ANN001
    """Fetch one page and pull out text plus structural signals.

    Raises `EgressRefused` if the browser was sent, or redirected, anywhere
    that is not a public address.
    """
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightError:
        # An aborted request fails the navigation; say why, not "no page".
        guard.raise_if_refused()
        return None
    guard.raise_if_refused()
    if response is None:
        return None
    await guard.check_landing(response, page.url)
    status = response.status
    if status >= 400:
        # Return the status rather than a bare None. A site that answers with
        # 404 or 403 (bot protection, geo-blocking) is a different operational
        # problem from one that does not resolve at all, and an operator
        # debugging "why did this prospect fail" needs to tell them apart.
        return {"status": status, "blocked": True}

    try:
        # One evaluate call rather than many round-trips through the protocol.
        data = await page.evaluate(
            """() => {
                const txt = (document.body?.innerText || '').slice(0, 20000);
                const ld = Array.from(
                    document.querySelectorAll('script[type="application/ld+json"]')
                ).map(n => n.textContent || '').join('\\n');
                const micro = Array.from(document.querySelectorAll('[itemtype]'))
                    .map(n => n.getAttribute('itemtype') || '');
                const links = Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.getAttribute('href') || '')
                    .filter(Boolean)
                    .slice(0, 400);
                return {
                    text: txt,
                    title: document.title || '',
                    hasMetaDescription: !!document.querySelector('meta[name="description"]'),
                    lang: document.documentElement.getAttribute('lang'),
                    h1: document.querySelectorAll('h1').length,
                    h2: document.querySelectorAll('h2').length,
                    jsonLd: ld,
                    microdata: micro,
                    links,
                };
            }"""
        )
    except PlaywrightError:
        guard.raise_if_refused()
        return None
    # What evaluate read is kept only if the page is still somewhere public.
    await guard.check_landing(response, page.url)

    return {
        "status": status,
        "text": data.get("text", ""),
        "title": data.get("title", ""),
        "has_meta_description": bool(data.get("hasMetaDescription")),
        "lang": data.get("lang"),
        "h1": int(data.get("h1") or 0),
        "h2": int(data.get("h2") or 0),
        "schema_types": _schema_types(data.get("jsonLd", ""), data.get("microdata", [])),
        "links": data.get("links", []),
        "url": page.url,
    }


def _apply_page_signals(signals: CrawlSignals, page_data: dict, *, is_home: bool) -> None:
    signals.h1_count += page_data["h1"]
    signals.h2_count += page_data["h2"]
    for schema_type in page_data["schema_types"]:
        if schema_type not in signals.schema_types:
            signals.schema_types.append(schema_type)
    if is_home:
        signals.has_title = bool(page_data["title"])
        signals.has_meta_description = page_data["has_meta_description"]
        signals.detected_language = page_data["lang"]
        host = urlparse(signals.final_url).netloc
        for href in page_data["links"]:
            absolute = urljoin(signals.final_url, href)
            if urlparse(absolute).netloc == host:
                signals.internal_link_count += 1
            elif absolute.startswith("http"):
                signals.external_link_count += 1


def _schema_types(json_ld: str, microdata: list[str]) -> list[str]:
    """Extract schema.org TYPE NAMES.

    Type names are a structural signal (the facts-only rule permits "structural
    signals (schema.org types present, ...)"). The VALUES inside the JSON-LD —
    descriptions, addresses, review text — are content and are deliberately
    discarded here rather than parsed.
    """
    found: list[str] = []
    for match in re.finditer(r'"@type"\s*:\s*("([^"]+)"|\[([^\]]+)\])', json_ld):
        if match.group(2):
            found.append(match.group(2))
        elif match.group(3):
            found.extend(t.strip().strip('"') for t in match.group(3).split(","))
    for item in microdata:
        if "schema.org/" in item:
            found.append(item.rsplit("/", 1)[-1])
    # Bound it: a page with hundreds of @type entries is a feed, not a signal.
    seen: list[str] = []
    for name in found:
        clean = name.strip()
        if clean and clean.isascii() and len(clean) <= 80 and clean not in seen:
            seen.append(clean)
    return seen[:40]


def _pick_secondary_urls(base_url: str, links: list[str], limit: int) -> list[str]:
    """Choose which additional same-host pages are worth fetching."""
    if limit <= 0:
        return []
    host = urlparse(base_url).netloc
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for href in links:
        absolute = urljoin(base_url, href).split("#")[0].rstrip("/")
        parsed = urlparse(absolute)
        if parsed.netloc != host or not absolute.startswith("http"):
            continue
        if absolute in seen or absolute.rstrip("/") == base_url.rstrip("/"):
            continue
        path = parsed.path.lower()
        for rank, candidate in enumerate(CANDIDATE_PATHS):
            if path.startswith(candidate):
                seen.add(absolute)
                scored.append((rank, absolute))
                break

    scored.sort(key=lambda pair: pair[0])
    return [u for _, u in scored[:limit]]

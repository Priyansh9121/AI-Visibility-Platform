"""Playwright crawl — §5.4 step 1, "crawl homepage/key pages".

=============================================================================
IP-SAFETY BOUNDARY  (docs/ip-safety.md constraint 7)
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
from urllib.parse import urljoin, urlparse

import structlog
import tldextract
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

logger = structlog.get_logger(__name__)

# A classifier needs a page's gist, not its entire text. Capping the extract
# bounds token cost, keeps latency inside the 30s acceptance budget, and limits
# how much third-party text is in memory at once.
MAX_CHARS_PER_PAGE = 6_000
MAX_TOTAL_CHARS = 18_000

DEFAULT_TIMEOUT_MS = 12_000
DEFAULT_MAX_PAGES = 4

# Identify ourselves honestly rather than impersonating a browser.
USER_AGENT = (
    "Mozilla/5.0 (compatible; AVPScanBot/0.1; +https://example.invalid/bot) "
    "Playwright/Chromium"
)

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
    subject's own name — all explicitly permitted by ip-safety.md #7.
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


async def crawl_site(
    url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> CrawlResult:
    """Fetch the homepage plus a few key pages and extract classification input.

    Never raises for an unreachable site: a site that will not load is a normal
    outcome for a prospecting tool pointed at an arbitrary URL, and it must
    surface as `ok=False` with a reason code rather than a 500.
    """
    start_url = normalise_url(url)
    domain = registrable_domain(start_url)
    signals = CrawlSignals(final_url=start_url, registrable_domain=domain)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                args=["--disable-dev-shm-usage", "--no-sandbox"]
            )
            try:
                context = await browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 900},
                    java_script_enabled=True,
                )
                # Images and fonts contribute nothing to classification and are
                # most of the bytes; blocking them roughly halves crawl time.
                await context.route(
                    re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|mp4|webm)$"),
                    lambda route: asyncio.ensure_future(route.abort()),
                )
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)

                home = await _fetch_page(page, start_url, timeout_ms)
                if home is None:
                    return CrawlResult(
                        signals=signals,
                        text_extract="",
                        ok=False,
                        error_code="FETCH_FAILED",
                    )
                if home.get("blocked"):
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

                for target in _pick_secondary_urls(page.url, home["links"], max_pages - 1):
                    if sum(len(e) for e in extracts) >= MAX_TOTAL_CHARS:
                        break
                    sub = await _fetch_page(page, target, timeout_ms)
                    if sub is None or sub.get("blocked"):
                        continue
                    _apply_page_signals(signals, sub, is_home=False)
                    extracts.append(sub["text"][:MAX_CHARS_PER_PAGE])
                    if sub["title"]:
                        titles.append(sub["title"])
                    signals.pages_fetched += 1
                    signals.urls_fetched.append(target)

                text = _WHITESPACE.sub(" ", "\n\n".join(extracts)).strip()[:MAX_TOTAL_CHARS]
                signals.word_count = len(text.split())

                return CrawlResult(signals=signals, text_extract=text, titles=titles)
            finally:
                await browser.close()

    except PlaywrightError as exc:
        logger.warning("crawl.playwright_error", url=start_url, error=type(exc).__name__)
        return CrawlResult(
            signals=signals, text_extract="", ok=False, error_code="BROWSER_ERROR"
        )
    except TimeoutError:
        return CrawlResult(signals=signals, text_extract="", ok=False, error_code="TIMEOUT")


async def _fetch_page(page, url: str, timeout_ms: int) -> dict | None:  # noqa: ANN001
    """Fetch one page and pull out text plus structural signals."""
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightError:
        return None
    if response is None:
        return None
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
        return None

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

    Type names are a structural signal (ip-safety.md #7 permits "structural
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

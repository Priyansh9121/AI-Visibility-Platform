"""Technical SEO audit — §7 Epic 6, feeding §6's Technical Foundation.

=============================================================================
FACTS-ONLY RULE
=============================================================================
This module crawls the CLIENT'S OWN site, not a competitor's and not an engine
answer. The facts-only discipline still applies to what is persisted: schema.org
TYPE NAMES, presence booleans, counts, and dates. No page copy, no meta
description text, no OG tag values, no HTML.

`AuditSignals` is a plain dataclass with no SQLAlchemy mapping — the same
contract as `CrawlResult` (Epic 2), `SerpResult` (Epic 3) and `EngineAnswer`
(Epic 4).

=============================================================================
WHY NOT REUSE services/crawl.py
=============================================================================
Epic 2's crawler was examined first. Its URL normalisation and Public Suffix
List parsing ARE reused (imported below). Its page fetch is not, for two
reasons that are not stylistic:

1. **It blocks images, fonts and video at the router** to halve classification
   crawl time. Largest Contentful Paint is usually an image. Measuring LCP
   through a crawler that refuses to load images would produce a confidently
   wrong number.
2. It fetches several pages and extracts text for a classifier. An audit needs
   one page loaded completely, plus two side fetches (robots.txt, sitemap.xml),
   and no text at all.

=============================================================================
DETERMINISM
=============================================================================
The SCORED signals — schema presence, structured data, indexation, freshness —
are deterministic given the same page: presence booleans and type names, read
the same way every time.

Core Web Vitals are NOT deterministic (network and render timing vary run to
run) and are deliberately **not scored** — they are measured, stored, and
reported per §7, but excluded from the §6 sub-score. `content_age_days` is
computed against wall-clock at audit time and then STORED; normalisation reads
the stored integer, so scoring stays reproducible. Same pattern as sentiment in
Epic 4: a non-reproducible measurement is taken once, upstream, and persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from . import egress
from .ai_crawlers import AgentAccess, evaluate_robots, unknown_access
from .crawl import (
    ROBOTS_DISALLOWED,
    RobotsGate,
    crawler_user_agent,
    normalise_url,
    registrable_domain,
)
from .egress import ADDRESS_NOT_PUBLIC, BrowserGuard, EgressRefused, EgressUnverified

logger = structlog.get_logger(__name__)

PAGE_TIMEOUT_MS = 25_000
SIDE_FETCH_TIMEOUT = 10.0
# Time allowed after load for LCP/CLS observers to settle. LCP can still fire
# late on lazy-loaded heroes; this is a lab approximation, not field data.
SETTLE_MS = 2_500

# Schema.org types that matter to this product, checked individually because
# each answers a different question a report needs to ask.
TRACKED_SCHEMA = {
    "organization": ("Organization", "Corporation", "OnlineBusiness"),
    "localbusiness": ("LocalBusiness", "Dentist", "Restaurant", "Store", "MedicalBusiness"),
    "faq": ("FAQPage", "QAPage"),
    "product": ("Product", "Service", "Offer", "SoftwareApplication"),
}


@dataclass(slots=True)
class AuditSignals:
    """Structural facts about one site. **Transient — never persisted as-is.**

    Every field is a boolean, a count, a type name, or a duration. There is
    deliberately no field capable of holding page copy.
    """

    url: str
    domain: str
    http_status: int | None = None
    ok: bool = True
    error_code: str | None = None

    # --- schema / structured data ---
    schema_types: list[str] = field(default_factory=list)
    has_organization_schema: bool = False
    has_localbusiness_schema: bool = False
    has_faq_schema: bool = False
    has_product_schema: bool = False

    # --- meta / markup presence (presence only, never values) ---
    has_title: bool = False
    has_meta_description: bool = False
    canonical_present: bool = False
    open_graph_tag_count: int = 0
    meta_robots_noindex: bool = False

    # --- indexation / crawlability ---
    robots_txt_present: bool = False
    robots_allows_crawl: bool = True
    has_sitemap: bool = False
    # robots.txt forbids /sitemap.xml to this crawler (2026-09-29): unknown,
    # not absent. Transient — the sitemap check carries it.
    sitemap_disallowed: bool = False
    is_indexable: bool = True

    # --- AI crawler policy (Epic F) ---
    # One verdict per agent in `ai_crawlers.AGENTS`. Empty ONLY before the side
    # fetch has run; an unreadable robots.txt yields a full list of UNKNOWN
    # rather than an empty one, so "not measured yet" and "measured, and the
    # file was unreachable" never collapse into the same value.
    ai_crawler_access: list[AgentAccess] = field(default_factory=list)

    # --- content structure ---
    h1_count: int = 0
    word_count: int = 0

    # --- freshness ---
    last_modified: datetime | None = None
    structured_date: datetime | None = None
    content_age_days: int | None = None

    # --- Core Web Vitals (LAB approximations; INP is unmeasurable here) ---
    lcp_ms: int | None = None
    # False when the page was read after `domcontentloaded` because `load`
    # timed out. Everything that does not depend on subresources is still
    # measured; LCP is not. Recorded so a reader of the signals can tell a
    # site with no LCP from a page we stopped waiting for.
    load_event_reached: bool = True
    cls: Decimal | None = None
    inp_ms: int | None = None

    def redacted(self) -> dict[str, object]:
        """Log-safe view. Everything here is already a fact, but kept narrow."""
        return {
            "url": self.url, "ok": self.ok, "error_code": self.error_code,
            "http_status": self.http_status, "schema_types": len(self.schema_types),
            "indexable": self.is_indexable, "lcp_ms": self.lcp_ms,
        }


def _classify_schema(types: list[str]) -> dict[str, bool]:
    lowered = {t.lower() for t in types}
    return {
        key: any(candidate.lower() in lowered for candidate in candidates)
        for key, candidates in TRACKED_SCHEMA.items()
    }


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_ISO_DATE = re.compile(r'"date(?:Published|Modified)"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})')


def _extract_structured_date(json_ld: str) -> datetime | None:
    """Newest datePublished/dateModified in JSON-LD.

    Only the DATE is read — the surrounding article body, headline and author
    are content and are not touched.
    """
    dates: list[datetime] = []
    for match in _ISO_DATE.finditer(json_ld or ""):
        try:
            dates.append(datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=UTC))
        except ValueError:
            continue
    return max(dates) if dates else None


@dataclass(slots=True)
class _SideFiles:
    """What the two side fetches found.

    A dataclass rather than the tuple this used to return: Epic F added a
    fourth and a fifth value, and a five-tuple unpacked at the call site is
    where an ordering mistake goes unnoticed.
    """

    robots_present: bool = False
    robots_allows_crawl: bool = True
    sitemap_present: bool = False
    # 2026-09-29: robots.txt forbids /sitemap.xml to this crawler, so it was
    # not fetched and whether one exists is unknown — not "no sitemap".
    sitemap_disallowed: bool = False
    # Epic F. Per-agent AI crawler policy. Empty only if never evaluated.
    ai_access: list[AgentAccess] = field(default_factory=list)


async def _fetch_side_files(gate: RobotsGate, base: str) -> _SideFiles:
    """Read robots.txt and sitemap.xml, and both policies out of robots.

    **robots.txt is read three ways, on purpose — and fetched once.** The
    fetch is `gate`'s, the same one that decides what this crawler may load
    (2026-09-29), so what is reported and what is obeyed come from one file.

    1. The blanket `Disallow: /` under `User-agent: *` check, unchanged since
       Epic 6, feeding `robots_allows_crawl` -> `is_indexable` -> the §6
       Technical Foundation sub-score.
    2. Epic F's full RFC 9309 parse in `services/ai_crawlers.py`, feeding the
       AI crawler access screen and NOTHING else.
    3. The same parser applied to AVPScanBot (`gate.allows`), deciding what
       the audit fetches — sitemap.xml here, the homepage in `audit_site`.

    The first is deliberately NOT reimplemented on top of the second, even
    though the second is strictly more correct. `robots_allows_crawl` is a
    SCORED input: re-deriving it would move Technical Foundation for every
    client whose robots.txt the old reader got wrong, inside a diff about
    crawler policy, with no way to tell a fixed score from a regressed one.
    Epic 6's number stays Epic 6's number until something deliberately
    revisits it. Recorded in `build-log.md` Epic F rather than left to be
    rediscovered as duplication.
    """
    found = _SideFiles()

    robots = await gate.file(base)
    if robots.status == 200 and "text" in robots.content_type:
        found.robots_present = True
        body = robots.body[:20_000]
        found.ai_access = evaluate_robots(body)
        star_block = False
        for raw in body.splitlines():
            line = raw.split("#", 1)[0].strip().lower()
            if line.startswith("user-agent:"):
                star_block = line.split(":", 1)[1].strip() == "*"
            elif (
                star_block
                and line.startswith("disallow:")
                and line.split(":", 1)[1].strip() == "/"
            ):
                found.robots_allows_crawl = False
            if "sitemap:" in line:
                found.sitemap_present = True

    if not found.ai_access:
        # Non-200, wrong content type, or a transport error. The file was
        # not read, so the policy is UNKNOWN — never an allow. The
        # distinction is the whole point of `unknown_access()`; see
        # `ai_crawlers.py`.
        found.ai_access = unknown_access()

    if not found.sitemap_present:
        # Checked before it is requested, like every page (2026-09-29).
        sitemap = await gate.follow("GET", f"{base}/sitemap.xml")
        if sitemap.disallowed:
            found.sitemap_disallowed = True
        elif sitemap.response is not None:
            response = sitemap.response
            found.sitemap_present = response.status_code == 200 and (
                "xml" in response.headers.get("content-type", "")
                or response.text.lstrip().startswith("<?xml")
            )

    return found


def _robots_disallowed(signals: AuditSignals) -> AuditSignals:
    """The audit of a site whose robots.txt forbids its homepage to this crawler.

    Nothing is loaded. What robots.txt itself says — the AI crawler table,
    whether the file exists, the sitemap it names — is already on `signals`,
    read from the one file this crawler may always fetch. A blanket
    `Disallow: /` under `*` also settles indexing without the page, so that
    stays a finding (`build_checks`).
    """
    signals.ok = False
    signals.error_code = ROBOTS_DISALLOWED
    signals.is_indexable = signals.robots_allows_crawl
    logger.info("audit.robots_disallowed", domain=signals.domain)
    return signals


def _address_not_public(signals: AuditSignals) -> AuditSignals:
    """The audit of a site whose address, or a redirect's, is not a public one.

    Nothing is kept. The refusal can arrive after the page's facts, or
    robots.txt's, were read, and `audit_runner` stores whatever it is handed, so
    this is a FRESH object: no schema types, counts, vitals or AI crawler table.
    (`_robots_disallowed` keeps robots.txt's facts on purpose; this does not,
    because nothing read from an address that is not public is ours to keep.)
    Every check is `not_applicable` (`build_checks`) and Technical Foundation is
    excluded.
    """
    logger.info("audit.address_not_public", domain=signals.domain)
    return AuditSignals(
        url=signals.url, domain=signals.domain, ok=False, error_code=ADDRESS_NOT_PUBLIC
    )


def _egress_unverified(signals: AuditSignals) -> AuditSignals:
    """A connected address was never read: not a refusal, so the plain browser failure.

    Nothing is kept, for the same reason as `_address_not_public`.
    """
    logger.warning("audit.egress_unverified", domain=signals.domain)
    return AuditSignals(
        url=signals.url, domain=signals.domain, ok=False, error_code="BROWSER_ERROR"
    )


async def audit_site(url: str, *, timeout_ms: int = PAGE_TIMEOUT_MS) -> AuditSignals:
    """Crawl one site for structural signals. Never raises for an unreachable site."""
    start_url = normalise_url(url)
    domain = registrable_domain(start_url)
    signals = AuditSignals(url=start_url, domain=domain)

    origin_match = re.match(r"^(https?://[^/]+)", start_url)
    origin = origin_match.group(1) if origin_match else start_url
    user_agent = crawler_user_agent()
    gate = RobotsGate(user_agent=user_agent, timeout=SIDE_FETCH_TIMEOUT)

    try:
        side = await _fetch_side_files(gate, origin)
        signals.robots_txt_present = side.robots_present
        signals.robots_allows_crawl = side.robots_allows_crawl
        signals.has_sitemap = side.sitemap_present
        signals.sitemap_disallowed = side.sitemap_disallowed
        signals.ai_crawler_access = side.ai_access
    except EgressRefused:
        # Before the broad handler below: a refusal ends the audit.
        return _address_not_public(signals)
    except Exception:  # noqa: BLE001 - side files must never fail the audit
        logger.warning("audit.side_fetch_failed", domain=domain)
        # The fetch raised outside its own handlers, so nothing about the
        # policy is known. Say so explicitly rather than leaving the list
        # empty, which downstream would read as "no agents to report".
        signals.ai_crawler_access = unknown_access()

    # robots.txt decides whether the page is loaded at all — 2026-09-29. The
    # homepage's redirects are followed by the gate, each hop checked on its
    # own host, so the browser is handed an address robots.txt allows.
    try:
        home_url = await gate.resolve(start_url)
    except EgressRefused:
        return _address_not_public(signals)
    if home_url is None:
        return _robots_disallowed(signals)

    guard = BrowserGuard()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                args=["--disable-dev-shm-usage", "--no-sandbox", egress.WEBRTC_LAUNCH_ARG]
            )
            try:
                # NO resource blocking — see the module note. Images must load
                # or LCP is meaningless.
                context = await browser.new_context(
                    user_agent=user_agent, viewport={"width": 1280, "height": 900},
                    # Blocked, so the route guard sees the page's requests.
                    service_workers="block",
                )
                # Refuses non-public hosts and blocks WebSockets; images still load.
                await guard.install(context)
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)

                await page.add_init_script(
                    """
                    window.__avpVitals = { lcp: null, cls: 0 };
                    try {
                      new PerformanceObserver((l) => {
                        const es = l.getEntries();
                        if (es.length) window.__avpVitals.lcp = es[es.length - 1].startTime;
                      }).observe({ type: 'largest-contentful-paint', buffered: true });
                      new PerformanceObserver((l) => {
                        for (const e of l.getEntries()) {
                          if (!e.hadRecentInput) window.__avpVitals.cls += e.value;
                        }
                      }).observe({ type: 'layout-shift', buffered: true });
                    } catch (err) { /* observer unsupported */ }
                    """
                )

                # ONE SLOW LOAD MUST NOT COST THE WHOLE AUDIT — 2026-09-08.
                #
                # `load` waits for every subresource, which is what LCP needs
                # (see the module note: images must load or LCP is
                # meaningless). It is also the strictest thing this module
                # asks for, and on a real marketing site with third-party tags
                # it is the condition most likely to miss.
                #
                # The pilot dry run caught the consequence: `helpwise.io`
                # timed out mid-scan, the whole audit returned BROWSER_ERROR,
                # and seventeen checks that never needed `load` — schema
                # types, indexability, sitemap, title, meta description — were
                # lost along with the one that did. Standalone the same site
                # then passed six times out of six, median 11.9s against this
                # 25s ceiling, so the timeout was transient rather than a
                # property of the site. That is precisely the failure a
                # fallback is for: the retry costs one measurement instead of
                # seventeen.
                #
                # `domcontentloaded` is what `crawl.py` has always used and
                # what succeeded on this site during the same scan. LCP is
                # simply not observed on the fallback path, which
                # `build_checks` already renders as `cwv_lcp: not_applicable`
                # with `LCP_NOT_OBSERVED` — a case it handled before this
                # existed, so nothing downstream had to change.
                try:
                    response = await page.goto(
                        home_url, wait_until="load", timeout=timeout_ms
                    )
                except PlaywrightTimeoutError:
                    guard.raise_if_refused()
                    logger.info(
                        "audit.load_timeout_fell_back",
                        domain=domain, timeout_ms=timeout_ms,
                    )
                    signals.load_event_reached = False
                    response = await page.goto(
                        home_url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                guard.raise_if_refused()
                if response is None:
                    await guard.finish()
                    signals.ok = False
                    signals.error_code = "FETCH_FAILED"
                    return signals
                # Where the browser landed, if the gate did not send it there
                # (a redirect only a browser is given, or a script's): that
                # host's robots.txt decides whether anything is read from it.
                # Every hop that led here, and where it ended, must be public
                # before anything is read from it.
                await guard.check_landing(response, page.url)
                if not await gate.allows(page.url):
                    await guard.finish()
                    return _robots_disallowed(signals)

                signals.http_status = response.status
                signals.last_modified = _parse_http_date(
                    response.headers.get("last-modified")
                )
                if response.status >= 400:
                    # Every way out of the browser run goes through the final check.
                    await guard.finish()
                    signals.ok = False
                    signals.error_code = f"HTTP_{response.status}"
                    return signals

                await page.wait_for_timeout(SETTLE_MS)
                guard.raise_if_refused()

                data = await page.evaluate(
                    """() => {
                        const ld = Array.from(
                          document.querySelectorAll('script[type="application/ld+json"]')
                        ).map(n => n.textContent || '').join('\\n');
                        const micro = Array.from(document.querySelectorAll('[itemtype]'))
                          .map(n => n.getAttribute('itemtype') || '');
                        const robots = document.querySelector('meta[name="robots"]');
                        return {
                          jsonLd: ld,
                          microdata: micro,
                          hasTitle: !!(document.title || '').trim(),
                          hasMetaDescription: !!document.querySelector('meta[name="description"]'),
                          canonical: !!document.querySelector('link[rel="canonical"]'),
                          ogCount: document.querySelectorAll('meta[property^="og:"]').length,
                          robotsContent: robots ? (robots.getAttribute('content') || '') : '',
                          h1: document.querySelectorAll('h1').length,
                          words: ((document.body?.innerText || '').match(/\\S+/g) || []).length,
                          vitals: window.__avpVitals || {},
                        };
                    }"""
                )

                # What evaluate read is kept only if the page is still somewhere public.
                await guard.check_landing(response, page.url)
                json_ld = data.get("jsonLd", "")
                types: list[str] = []
                for match in re.finditer(r'"@type"\s*:\s*("([^"]+)"|\[([^\]]+)\])', json_ld):
                    if match.group(2):
                        types.append(match.group(2))
                    elif match.group(3):
                        types.extend(t.strip().strip('"') for t in match.group(3).split(","))
                for item in data.get("microdata", []):
                    if "schema.org/" in item:
                        types.append(item.rsplit("/", 1)[-1])

                seen: list[str] = []
                for name in types:
                    clean = name.strip()
                    if clean and clean.isascii() and len(clean) <= 80 and clean not in seen:
                        seen.append(clean)
                signals.schema_types = seen[:40]

                flags = _classify_schema(signals.schema_types)
                signals.has_organization_schema = flags["organization"]
                signals.has_localbusiness_schema = flags["localbusiness"]
                signals.has_faq_schema = flags["faq"]
                signals.has_product_schema = flags["product"]

                signals.has_title = bool(data.get("hasTitle"))
                signals.has_meta_description = bool(data.get("hasMetaDescription"))
                signals.canonical_present = bool(data.get("canonical"))
                signals.open_graph_tag_count = int(data.get("ogCount") or 0)
                signals.meta_robots_noindex = "noindex" in (
                    data.get("robotsContent") or ""
                ).lower()
                signals.h1_count = int(data.get("h1") or 0)
                signals.word_count = int(data.get("words") or 0)

                signals.is_indexable = (
                    not signals.meta_robots_noindex and signals.robots_allows_crawl
                )

                signals.structured_date = _extract_structured_date(json_ld)
                freshest = max(
                    [d for d in (signals.last_modified, signals.structured_date) if d],
                    default=None,
                )
                if freshest is not None:
                    signals.content_age_days = max(
                        0, (datetime.now(UTC) - freshest).days
                    )

                # WEB VITALS ARE DISCARDED ON THE FALLBACK PATH — 2026-09-08.
                #
                # Both are cumulative: LCP is the largest paint SO FAR and CLS
                # the shift SO FAR, so a page we stopped waiting for reports
                # better numbers than the same page fully loaded. Measured on
                # `helpwise.io` while building the fallback: 988ms after
                # `domcontentloaded` against 2,952ms after `load` — a third of
                # the real figure, and enough to flip `cwv_lcp` from `warn` to
                # `pass`.
                #
                # Reporting that would be the fallback quietly flattering
                # every slow site it rescues, which is worse than the outage
                # it was added to survive. `build_checks` already renders an
                # absent vital as `not_applicable` with `LCP_NOT_OBSERVED` /
                # `CLS_NOT_OBSERVED`, so dropping them here says "not
                # measured" rather than inventing a good number.
                vitals = (data.get("vitals") or {}) if signals.load_event_reached else {}
                lcp = vitals.get("lcp")
                if isinstance(lcp, (int, float)) and lcp > 0:
                    signals.lcp_ms = int(round(lcp))
                cls_value = vitals.get("cls")
                if isinstance(cls_value, (int, float)):
                    signals.cls = Decimal(str(round(cls_value, 3)))
                # INP measures real user interaction latency. It is a FIELD
                # metric; a crawler that never clicks anything cannot produce
                # one, and inventing a lab proxy would be worse than a null.
                signals.inp_ms = None

                # The last check before anything read is kept: a refusal ends the
                # audit as one, and a connected address never read fails it closed.
                await guard.finish()
                return signals
            finally:
                await browser.close()

    except EgressRefused:
        return _address_not_public(signals)
    except EgressUnverified:
        return _egress_unverified(signals)
    except PlaywrightError as exc:
        # A request the guard aborted fails the navigation that asked for it. And a
        # browser that fails to close while an egress outcome is on its way out
        # replaces it with this error: the outcome stands, and nothing is kept.
        if guard.refused is not None or isinstance(exc.__context__, EgressRefused):
            return _address_not_public(signals)
        if guard.unverified:
            return _egress_unverified(signals)
        logger.warning("audit.playwright_error", domain=domain, error=type(exc).__name__)
        signals.ok = False
        signals.error_code = "BROWSER_ERROR"
        return signals
    except TimeoutError:
        signals.ok = False
        signals.error_code = "TIMEOUT"
        return signals


# ===========================================================================
# Normalisation — signals to per-check verdicts and a 0-100 Decimal.
#
# §7's acceptance criterion is "audit returns pass/fail + detail for each
# check", so the per-check verdicts below are the primary deliverable. The
# 0-100 Decimal is what §6's Technical Foundation dimension consumes, and it is
# derived from those same verdicts rather than computed separately — one source
# of truth, so a check and the score can never disagree.
#
# Decimal throughout, matching services/scoring.py. This module is not in the
# scoring path, but its OUTPUT is, so float here would undermine Epic 5's
# determinism guarantees exactly as much as float inside scoring.py would.
# ===========================================================================

from .scoring import HUNDRED  # noqa: E402

TWO_PLACES = Decimal("0.01")

# Component weights inside the Technical Foundation sub-score.
#
# Indexation is heaviest because it is a precondition, not a nicety: a site
# telling crawlers to stay out has no technical foundation for AI visibility
# whatever its markup looks like. Schema presence and structured data are §6's
# two named inputs and are kept separate — "has any markup at all" and "has the
# RIGHT markup for this business" are different findings with different fixes.
#
# Core Web Vitals are measured and reported (§7) but carry NO weight: LCP and
# CLS here are single-cold-load lab numbers, and letting a noisy measurement
# move a client-facing score would make the score noisy too.
COMPONENT_WEIGHTS: dict[str, Decimal] = {
    "indexation": Decimal("30"),
    "schema_presence": Decimal("20"),
    "structured_data": Decimal("25"),
    "content_freshness": Decimal("25"),
}

# Freshness bands, in days. Graduated rather than linear because the difference
# between a page updated yesterday and one updated last week is immaterial,
# while the difference between last year and three years ago is not.
FRESHNESS_BANDS: tuple[tuple[int, Decimal], ...] = (
    (90, Decimal("100")),
    (180, Decimal("75")),
    (365, Decimal("50")),
    (730, Decimal("25")),
)

# Google's "good" thresholds, used only for the pass/warn/fail verdicts.
LCP_GOOD_MS, LCP_POOR_MS = 2500, 4000
CLS_GOOD, CLS_POOR = Decimal("0.1"), Decimal("0.25")


@dataclass(slots=True)
class CheckVerdict:
    key: str
    status: str  # pass | warn | fail | not_applicable | error
    value: Decimal | None = None
    detail_code: str | None = None


@dataclass(slots=True)
class AuditOutcome:
    checks: list[CheckVerdict]
    components: dict[str, Decimal]
    excluded_components: dict[str, str]
    score: Decimal | None
    scored: bool


def _round2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _verdict(passed: bool, *, warn: bool = False) -> str:
    if passed:
        return "pass"
    return "warn" if warn else "fail"


# Error codes that are the SERVER'S OWN ANSWER about itself, as against this
# module's report on its own attempt. An HTTP 4xx or 5xx is the site saying
# what it is; a browser timeout is us saying we stopped waiting. Only the
# first is evidence about a client's site — see `build_checks`.
SITE_ANSWERED_CODES = ("HTTP_",)


def build_checks(signals: AuditSignals) -> list[CheckVerdict]:
    """Per-check pass/fail with a detail code — §7's acceptance deliverable.

    **A FAILED AUDIT IS NOT AUTOMATICALLY A FINDING ABOUT THE SITE** —
    2026-09-08, and this cost a false claim on a real report before it was
    fixed.

    Every failure used to collapse into one `site_reachable: error`, which
    `fix_runner.audit_findings` promoted to a fix candidate, which the
    generator wrote up as *"Fix the crawl failure on helpwise.io so the site
    returns rendered HTML to automated visitors"* — the highest-priority item
    on a report about a site that answers in 1.13 seconds and that this
    module's own audit then passed six times out of six.

    So the two kinds are separated at the point they are first distinguishable:

    * **The server answered, and its answer was an error** (`HTTP_404`,
      `HTTP_503`). That is the site describing itself, and it is a genuine
      `fail` a client can act on.
    * **We could not complete the measurement** (`BROWSER_ERROR`, `TIMEOUT`,
      `FETCH_FAILED`). That is a report about our attempt. It stays `error`,
      which `audit_findings` no longer treats as a finding — the same
      distinction this codebase already draws between `NOT_YET_MEASURED` and
      `NO_POPULATION`, applied one layer down.

    Technical Foundation is excluded either way (`score_audit` returns
    `scored=False` whenever the signals are not ok), so neither kind silently
    scores a client down.

    **A FOURTH KIND: the site's address is not a public one** (`ADDRESS_NOT_PUBLIC`).
    Nothing was fetched, robots.txt included, so nothing is known and every
    check is `not_applicable` — never a finding.

    **A THIRD KIND, 2026-09-29: the site's robots.txt forbids the page.** Not
    a failure — this crawler obeys the file. What the file itself says is
    still reported (`_robots_disallowed_checks`), and everything that needs
    the page is `not_applicable` with `ROBOTS_DISALLOWED`, which is never a
    finding.
    """
    if not signals.ok and signals.error_code == ROBOTS_DISALLOWED:
        return _robots_disallowed_checks(signals)
    if not signals.ok and signals.error_code == ADDRESS_NOT_PUBLIC:
        return _address_not_public_checks()
    if not signals.ok:
        code = signals.error_code or "FETCH_FAILED"
        site_answered = code.startswith(SITE_ANSWERED_CODES)
        return [
            CheckVerdict(
                "site_reachable",
                "fail" if site_answered else "error",
                None,
                code,
            )
        ]

    checks: list[CheckVerdict] = [
        CheckVerdict("site_reachable", "pass", Decimal(signals.http_status or 200)),
    ]

    # --- indexation / crawlability -------------------------------------
    checks.append(CheckVerdict(
        "indexable", _verdict(signals.is_indexable), None,
        None if signals.is_indexable else (
            "META_ROBOTS_NOINDEX" if signals.meta_robots_noindex else "ROBOTS_TXT_DISALLOW"
        ),
    ))
    checks.extend(_robots_file_checks(signals))
    checks.append(CheckVerdict(
        "canonical_present", _verdict(signals.canonical_present, warn=True), None,
        None if signals.canonical_present else "NO_CANONICAL_LINK",
    ))

    # --- schema / structured data ---------------------------------------
    type_count = len(signals.schema_types)
    checks.append(CheckVerdict(
        "schema_present", _verdict(type_count > 0), Decimal(type_count),
        None if type_count else "NO_STRUCTURED_DATA",
    ))
    checks.append(CheckVerdict(
        "schema_business_entity",
        _verdict(signals.has_organization_schema or signals.has_localbusiness_schema),
        None,
        None if (signals.has_organization_schema or signals.has_localbusiness_schema)
        else "NO_ORGANIZATION_OR_LOCALBUSINESS",
    ))
    checks.append(CheckVerdict(
        "schema_faq", _verdict(signals.has_faq_schema, warn=True), None,
        None if signals.has_faq_schema else "NO_FAQ_SCHEMA",
    ))
    checks.append(CheckVerdict(
        "schema_product_or_service", _verdict(signals.has_product_schema, warn=True), None,
        None if signals.has_product_schema else "NO_PRODUCT_OR_SERVICE_SCHEMA",
    ))

    # --- meta presence ---------------------------------------------------
    checks.append(CheckVerdict("meta_title", _verdict(signals.has_title), None,
                               None if signals.has_title else "NO_TITLE"))
    checks.append(CheckVerdict(
        "meta_description", _verdict(signals.has_meta_description, warn=True), None,
        None if signals.has_meta_description else "NO_META_DESCRIPTION",
    ))
    checks.append(CheckVerdict(
        "open_graph_tags", _verdict(signals.open_graph_tag_count > 0, warn=True),
        Decimal(signals.open_graph_tag_count),
        None if signals.open_graph_tag_count else "NO_OPEN_GRAPH_TAGS",
    ))
    checks.append(CheckVerdict(
        "single_h1", _verdict(signals.h1_count == 1, warn=True), Decimal(signals.h1_count),
        None if signals.h1_count == 1 else ("NO_H1" if signals.h1_count == 0 else "MULTIPLE_H1"),
    ))

    # --- freshness -------------------------------------------------------
    if signals.content_age_days is None:
        checks.append(CheckVerdict(
            "content_freshness", "not_applicable", None, "NO_DATE_SIGNAL_AVAILABLE"
        ))
    else:
        age = signals.content_age_days
        checks.append(CheckVerdict(
            "content_freshness",
            "pass" if age <= 180 else ("warn" if age <= 365 else "fail"),
            Decimal(age), None if age <= 180 else "CONTENT_STALE",
        ))

    # --- Core Web Vitals (reported, not scored) --------------------------
    if signals.lcp_ms is None:
        checks.append(CheckVerdict("cwv_lcp", "not_applicable", None, "LCP_NOT_OBSERVED"))
    else:
        checks.append(CheckVerdict(
            "cwv_lcp",
            "pass" if signals.lcp_ms <= LCP_GOOD_MS
            else ("warn" if signals.lcp_ms <= LCP_POOR_MS else "fail"),
            Decimal(signals.lcp_ms), "LAB_MEASUREMENT_NOT_FIELD_DATA",
        ))
    if signals.cls is None:
        checks.append(CheckVerdict("cwv_cls", "not_applicable", None, "CLS_NOT_OBSERVED"))
    else:
        checks.append(CheckVerdict(
            "cwv_cls",
            "pass" if signals.cls <= CLS_GOOD
            else ("warn" if signals.cls <= CLS_POOR else "fail"),
            signals.cls, "LAB_MEASUREMENT_NOT_FIELD_DATA",
        ))
    # INP measures real user interaction latency. A crawler never interacts, so
    # there is nothing to measure — a lab proxy would be a fabricated number.
    checks.append(CheckVerdict(
        "cwv_inp", "not_applicable", None, "FIELD_METRIC_REQUIRES_REAL_USER_DATA"
    ))

    return checks


# The checks that need the page itself — every check but the two robots.txt
# settles, and INP, which no crawler can measure. Ordered as `build_checks`.
PAGE_CHECKS: tuple[str, ...] = (
    "site_reachable",
    "canonical_present",
    "schema_present",
    "schema_business_entity",
    "schema_faq",
    "schema_product_or_service",
    "meta_title",
    "meta_description",
    "open_graph_tags",
    "single_h1",
    "content_freshness",
    "cwv_lcp",
    "cwv_cls",
)


def _robots_file_checks(signals: AuditSignals) -> list[CheckVerdict]:
    """The two checks robots.txt answers on its own: the file, and the sitemap."""
    if signals.has_sitemap:
        sitemap = CheckVerdict("sitemap_present", "pass", None, None)
    elif signals.sitemap_disallowed:
        # Not fetched because robots.txt forbids it: unknown, so not a
        # finding. Telling a client to publish a sitemap it may well have
        # would be a claim this audit never checked.
        sitemap = CheckVerdict("sitemap_present", "not_applicable", None, ROBOTS_DISALLOWED)
    else:
        sitemap = CheckVerdict("sitemap_present", "warn", None, "NO_SITEMAP_XML")
    return [
        CheckVerdict(
            "robots_txt_present", _verdict(signals.robots_txt_present, warn=True), None,
            None if signals.robots_txt_present else "NO_ROBOTS_TXT",
        ),
        sitemap,
    ]


def _robots_disallowed_checks(signals: AuditSignals) -> list[CheckVerdict]:
    """What an audit barred from the page can still say, from robots.txt alone.

    A blanket `Disallow: /` under `*` is a site telling every crawler, AI
    ones included, to stay out. That is known from the file, and it stays the
    `indexable` finding (`ROBOTS_TXT_DISALLOW`) it has always been: being
    blocked from measuring is no reason to hide that the site blocks the
    crawlers that build AI answers. Anything else about indexing needs the
    page's own meta tags, so it is `not_applicable`.
    """
    if signals.robots_allows_crawl:
        indexable = CheckVerdict("indexable", "not_applicable", None, ROBOTS_DISALLOWED)
    else:
        indexable = CheckVerdict("indexable", "fail", None, "ROBOTS_TXT_DISALLOW")
    return [
        indexable,
        *_robots_file_checks(signals),
        *(CheckVerdict(key, "not_applicable", None, ROBOTS_DISALLOWED) for key in PAGE_CHECKS),
        CheckVerdict("cwv_inp", "not_applicable", None, "FIELD_METRIC_REQUIRES_REAL_USER_DATA"),
    ]


def _address_not_public_checks() -> list[CheckVerdict]:
    """Every check, `not_applicable`: the crawler refused to connect, so nothing was read."""
    keys = ("indexable", "robots_txt_present", "sitemap_present", *PAGE_CHECKS, "cwv_inp")
    return [CheckVerdict(key, "not_applicable", None, ADDRESS_NOT_PUBLIC) for key in keys]


def compute_components(signals: AuditSignals) -> tuple[dict[str, Decimal], dict[str, str]]:
    """The four scored components, each 0-100, plus any that were unmeasurable."""
    components: dict[str, Decimal] = {}
    excluded: dict[str, str] = {}

    # Indexation: being indexable dominates; sitemap and canonical are hygiene.
    indexation = Decimal("0")
    if signals.is_indexable:
        indexation += Decimal("60")
    if signals.has_sitemap:
        indexation += Decimal("20")
    if signals.canonical_present:
        indexation += Decimal("20")
    components["indexation"] = indexation

    # Schema presence: graduated, because one stray WebSite type is not the same
    # as a properly marked-up site, and a binary flag would call them equal.
    count = len(signals.schema_types)
    if count == 0:
        components["schema_presence"] = Decimal("0")
    elif count >= 3:
        components["schema_presence"] = Decimal("100")
    else:
        components["schema_presence"] = Decimal("60")

    # Structured data: the RIGHT markup for a business, not merely any markup.
    structured = Decimal("0")
    if signals.has_organization_schema or signals.has_localbusiness_schema:
        structured += Decimal("50")
    if signals.has_faq_schema:
        structured += Decimal("25")
    if signals.has_product_schema:
        structured += Decimal("25")
    components["structured_data"] = structured

    # Freshness: excluded when no date signal exists at all. Scoring it zero
    # would punish a site for not advertising a Last-Modified header, which is
    # a publishing convention rather than a visibility problem. Same discipline
    # as scoring-spec.md's treatment of sentiment with no population.
    if signals.content_age_days is None:
        excluded["content_freshness"] = "NO_DATE_SIGNAL_AVAILABLE"
    else:
        age = signals.content_age_days
        value = Decimal("0")
        for threshold, band in FRESHNESS_BANDS:
            if age <= threshold:
                value = band
                break
        components["content_freshness"] = value

    return components, excluded


def signals_from_row(audit: Any) -> AuditSignals:
    """Rebuild the scoring inputs from a STORED audit row — Epic 9.22.

    `compute_components` needs exactly nine facts, and every one of them is
    already a column on `technical_audits`: `is_indexable`, `has_sitemap`,
    `canonical_present`, `schema_types`, the four schema flags, and
    `content_age_days`. So the four component scores can be recomputed from a
    row without re-crawling anything and without a migration.

    This is NOT a general-purpose inverse of the crawl. The fields it leaves at
    their defaults — titles, meta description, Open Graph counts, robots.txt
    presence — are inputs to `build_checks`, never to `compute_components`, and
    the stored `TechnicalAuditCheck` rows already carry those verdicts. Calling
    `build_checks` on the result would produce wrong answers; calling
    `compute_components` produces the same answers the score was built from.
    """
    return AuditSignals(
        url=audit.url_audited,
        domain="",
        ok=True,
        schema_types=list(audit.schema_types or []),
        has_organization_schema=bool(audit.has_organization_schema),
        has_localbusiness_schema=bool(audit.has_localbusiness_schema),
        has_faq_schema=bool(audit.has_faq_schema),
        has_product_schema=bool(audit.has_product_schema),
        canonical_present=bool(audit.canonical_present),
        has_sitemap=bool(audit.has_sitemap),
        is_indexable=bool(audit.is_indexable),
        content_age_days=audit.content_age_days,
    )


def effective_weights(components: dict[str, Decimal]) -> dict[str, Decimal]:
    """The weight each component actually carried, after redistribution.

    Exposed alongside the components so a reader can see WHY an unmeasurable
    component did not simply score zero: its weight was shared out across the
    ones that could be measured, which is scoring-spec.md rule 2.
    """
    included = [k for k in COMPONENT_WEIGHTS if k in components]
    if not included:
        return {}
    total = sum((COMPONENT_WEIGHTS[k] for k in included), Decimal("0"))
    return {k: _round2(COMPONENT_WEIGHTS[k] / total * HUNDRED) for k in included}


def score_audit(signals: AuditSignals) -> AuditOutcome:
    """Turn signals into per-check verdicts and the §6 Technical Foundation value.

    Returns `score=None` when the site could not be read at all — an unreachable
    site is not a site with a bad technical foundation, and scoring it zero would
    be the same category error as scoring an unrunnable scan zero.
    """
    checks = build_checks(signals)

    if not signals.ok:
        return AuditOutcome(
            checks=checks, components={}, excluded_components={},
            score=None, scored=False,
        )

    components, excluded = compute_components(signals)
    included = [k for k in COMPONENT_WEIGHTS if k in components]
    if not included:
        return AuditOutcome(
            checks=checks, components=components, excluded_components=excluded,
            score=None, scored=False,
        )

    # Redistribute proportionally across measurable components, rounded once,
    # so the stored breakdown re-sums against its own weights — same rule as
    # scoring-spec.md rule 2.
    total_weight = sum((COMPONENT_WEIGHTS[k] for k in included), Decimal("0"))
    effective = {k: _round2(COMPONENT_WEIGHTS[k] / total_weight * HUNDRED) for k in included}
    score = sum(
        (effective[k] * components[k] for k in sorted(included)), Decimal("0")
    ) / HUNDRED

    return AuditOutcome(
        checks=checks, components=components, excluded_components=excluded,
        score=_round2(score), scored=True,
    )

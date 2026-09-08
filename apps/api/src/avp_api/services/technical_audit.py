"""Technical SEO audit — §7 Epic 6, feeding §6's Technical Foundation.

=============================================================================
IP-SAFETY (ip-safety.md #7)
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

import httpx
import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from .ai_crawlers import AgentAccess, evaluate_robots, unknown_access
from .crawl import USER_AGENT, normalise_url, registrable_domain

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
    # Epic F. Per-agent AI crawler policy. Empty only if never evaluated.
    ai_access: list[AgentAccess] = field(default_factory=list)


async def _fetch_side_files(base: str) -> _SideFiles:
    """Fetch robots.txt and sitemap.xml, and read both policies out of robots.

    **robots.txt is now read twice, on purpose.**

    1. The blanket `Disallow: /` under `User-agent: *` check, unchanged since
       Epic 6, feeding `robots_allows_crawl` -> `is_indexable` -> the §6
       Technical Foundation sub-score.
    2. Epic F's full RFC 9309 parse in `services/ai_crawlers.py`, feeding the
       AI crawler access screen and NOTHING else.

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

    async with httpx.AsyncClient(
        timeout=SIDE_FETCH_TIMEOUT, follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        try:
            resp = await client.get(f"{base}/robots.txt")
            if resp.status_code == 200 and "text" in resp.headers.get("content-type", ""):
                found.robots_present = True
                body = resp.text[:20_000]
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
        except httpx.HTTPError:
            pass

        if not found.ai_access:
            # Non-200, wrong content type, or a transport error. The file was
            # not read, so the policy is UNKNOWN — never an allow. The
            # distinction is the whole point of `unknown_access()`; see
            # `ai_crawlers.py`.
            found.ai_access = unknown_access()

        if not found.sitemap_present:
            try:
                resp = await client.get(f"{base}/sitemap.xml")
                found.sitemap_present = resp.status_code == 200 and (
                    "xml" in resp.headers.get("content-type", "")
                    or resp.text.lstrip().startswith("<?xml")
                )
            except httpx.HTTPError:
                pass

    return found


async def audit_site(url: str, *, timeout_ms: int = PAGE_TIMEOUT_MS) -> AuditSignals:
    """Crawl one site for structural signals. Never raises for an unreachable site."""
    start_url = normalise_url(url)
    domain = registrable_domain(start_url)
    signals = AuditSignals(url=start_url, domain=domain)

    origin_match = re.match(r"^(https?://[^/]+)", start_url)
    origin = origin_match.group(1) if origin_match else start_url

    try:
        side = await _fetch_side_files(origin)
        signals.robots_txt_present = side.robots_present
        signals.robots_allows_crawl = side.robots_allows_crawl
        signals.has_sitemap = side.sitemap_present
        signals.ai_crawler_access = side.ai_access
    except Exception:  # noqa: BLE001 - side files must never fail the audit
        logger.warning("audit.side_fetch_failed", domain=domain)
        # The fetch raised outside its own handlers, so nothing about the
        # policy is known. Say so explicitly rather than leaving the list
        # empty, which downstream would read as "no agents to report".
        signals.ai_crawler_access = unknown_access()

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--disable-dev-shm-usage", "--no-sandbox"])
            try:
                # NO resource blocking — see the module note. Images must load
                # or LCP is meaningless.
                context = await browser.new_context(
                    user_agent=USER_AGENT, viewport={"width": 1280, "height": 900}
                )
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
                        start_url, wait_until="load", timeout=timeout_ms
                    )
                except PlaywrightTimeoutError:
                    logger.info(
                        "audit.load_timeout_fell_back",
                        domain=domain, timeout_ms=timeout_ms,
                    )
                    signals.load_event_reached = False
                    response = await page.goto(
                        start_url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                if response is None:
                    signals.ok = False
                    signals.error_code = "FETCH_FAILED"
                    return signals

                signals.http_status = response.status
                signals.last_modified = _parse_http_date(
                    response.headers.get("last-modified")
                )
                if response.status >= 400:
                    signals.ok = False
                    signals.error_code = f"HTTP_{response.status}"
                    return signals

                await page.wait_for_timeout(SETTLE_MS)

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

                return signals
            finally:
                await browser.close()

    except PlaywrightError as exc:
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
    """
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
    checks.append(CheckVerdict(
        "robots_txt_present", _verdict(signals.robots_txt_present, warn=True), None,
        None if signals.robots_txt_present else "NO_ROBOTS_TXT",
    ))
    checks.append(CheckVerdict(
        "sitemap_present", _verdict(signals.has_sitemap, warn=True), None,
        None if signals.has_sitemap else "NO_SITEMAP_XML",
    ))
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

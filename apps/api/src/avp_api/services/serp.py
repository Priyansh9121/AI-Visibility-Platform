"""SerpApi search — §5.4 step 2, "query SERP for niche keywords".

No SerpApi SDK. Their API is a single authenticated GET returning JSON, and
`httpx` is already a vetted dependency (BSD-3-Clause), so the official client
would add a package, a sync-only call style inside an async service, and
another licence to audit — for one HTTP request.

=============================================================================
IP-SAFETY BOUNDARY (ip-safety.md #7)
=============================================================================
Search results are third-party content. Result titles and snippets are
publisher copy and are **never persisted**: they exist inside `SerpResult` in
memory, are used to derive facts, and are discarded with the request.

What leaves this module is domains, positions, and counts. `SerpResult` is a
plain dataclass with no SQLAlchemy mapping and no persistence path, exactly
like `CrawlResult` in services/crawl.py.
=============================================================================
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
import structlog

from ..config import Settings, get_settings
from .crawl import registrable_domain

logger = structlog.get_logger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
DEFAULT_TIMEOUT = 25.0
RESULTS_PER_QUERY = 10

# Domains that rank for "best X" queries but are never the competitor — they
# are publishers, marketplaces, directories and social platforms writing ABOUT
# the category.
#
# This list is a heuristic and is knowingly incomplete. It is deliberately
# biased toward false negatives: wrongly excluding a real competitor costs one
# slot in a ranked set an operator can edit, while wrongly *including*
# Wikipedia as a rival makes the whole report look unserious to the client.
# Judged by registrable domain, so regional variants (amazon.co.uk) match.
NON_COMPETITOR_DOMAINS: frozenset[str] = frozenset({
    # Encyclopaedic / reference
    "wikipedia.org", "wikimedia.org", "britannica.com", "fandom.com",
    # Social / UGC / forums
    "reddit.com", "quora.com", "x.com", "twitter.com", "facebook.com",
    "instagram.com", "linkedin.com", "tiktok.com", "pinterest.com",
    "youtube.com", "medium.com", "substack.com", "tumblr.com", "threads.net",
    # Review aggregators and software directories
    "g2.com", "capterra.com", "trustpilot.com", "trustradius.com",
    "getapp.com", "softwareadvice.com", "producthunt.com", "alternativeto.net",
    "sourceforge.net", "slant.co", "gartner.com", "forrester.com",
    # Local directories
    "yelp.com", "yellowpages.com", "tripadvisor.com", "opentable.com",
    "thumbtack.com", "angi.com", "houzz.com", "checkatrade.com", "bark.com",
    # Marketplaces / retail aggregators
    "amazon.com", "ebay.com", "etsy.com", "walmart.com", "target.com",
    "alibaba.com", "aliexpress.com",
    # Publishers and press
    "forbes.com", "techcrunch.com", "businessinsider.com", "nytimes.com",
    "theguardian.com", "bbc.co.uk", "bbc.com", "cnn.com", "wired.com",
    "theverge.com", "cnet.com", "zdnet.com", "pcmag.com", "wirecutter.com",
    "nerdwallet.com", "investopedia.com", "inc.com", "entrepreneur.com",
    # Job boards and company databases
    "indeed.com", "glassdoor.com", "crunchbase.com", "pitchbook.com",
    "zoominfo.com", "owler.com", "bloomberg.com",
    # Search engines / infrastructure that occasionally self-rank
    "google.com", "bing.com", "duckduckgo.com", "apple.com", "microsoft.com",
    "github.com", "gitlab.com",
})


@dataclass(slots=True)
class SerpHit:
    """One organic result, reduced to facts."""

    domain: str
    position: int
    query: str


@dataclass(slots=True)
class SerpResult:
    """Outcome of one SERP query. **Transient — never persist.**"""

    query: str
    hits: list[SerpHit] = field(default_factory=list)
    ok: bool = True
    error_code: str | None = None
    # Result titles, held ONLY to pass to the co-citation extractor in the same
    # request. Never stored, never returned by an endpoint.
    titles: list[str] = field(default_factory=list)

    def redacted(self) -> dict[str, object]:
        """Log-safe view — never includes titles."""
        return {
            "query": self.query,
            "hits": len(self.hits),
            "ok": self.ok,
            "error_code": self.error_code,
        }


def build_queries(
    *,
    brand_name: str | None,
    domain: str,
    industry: str | None,
    niche: str | None,
) -> list[str]:
    """Build SERP queries from what is known about the client.

    **There is deliberately no industry-keyed template dictionary here.**
    `Client.industry` confidence is uncalibrated (build-log Epic 2.6/2.8,
    Finding 2 — still open), so bespoke per-industry query sets would silently
    produce a plausible-but-wrong competitor set for a mis-classified client,
    with nothing downstream able to detect it. Instead:

    * Query shapes are **generic** and apply to any industry string.
    * **Brand-anchored queries are always included**, and do not depend on the
      classification at all. "<brand> alternatives" and "<brand> vs" surface
      real competitors even when the industry label is wrong — so a
      misclassification degrades the result rather than corrupting it.

    That asymmetry is the point: industry is a *soft* input that can improve
    recall, never a load-bearing one.
    """
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        cleaned = " ".join(q.split()).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            queries.append(cleaned)

    # Brand-anchored: independent of classification, so these still work when
    # the industry label is wrong. Listed first so they survive any truncation.
    anchor = (brand_name or "").strip() or domain
    add(f"{anchor} alternatives")
    add(f"{anchor} competitors")
    add(f"{anchor} vs")

    # Industry-seeded: better recall when the label is right, harmless noise
    # when it is not, because ranking requires corroboration to promote.
    seed = (niche or industry or "").strip()
    if seed:
        add(f"best {seed}")
        add(f"top {seed} companies")
        add(f"{seed} providers")

    return queries


async def search(
    query: str, *, settings: Settings | None = None, num: int = RESULTS_PER_QUERY
) -> SerpResult:
    """Run one Google search via SerpApi. Never raises for an upstream failure."""
    settings = settings or get_settings()
    api_key = settings.provider_key("serpapi_key")
    if not api_key:
        return SerpResult(query=query, ok=False, error_code="SERPAPI_KEY_MISSING")

    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": str(num),
        "hl": "en",
        "gl": "us",
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(SERPAPI_URL, params=params)
    except httpx.TimeoutException:
        return SerpResult(query=query, ok=False, error_code="SERP_TIMEOUT")
    except httpx.HTTPError:
        return SerpResult(query=query, ok=False, error_code="SERP_UNREACHABLE")

    if response.status_code == 401:
        logger.error("serp.auth_failed")
        return SerpResult(query=query, ok=False, error_code="SERP_AUTH_FAILED")
    if response.status_code == 429:
        return SerpResult(query=query, ok=False, error_code="SERP_RATE_LIMITED")
    if response.status_code != 200:
        # The response body is NOT logged: SerpApi echoes the request URL on
        # some errors, and the request URL contains the API key.
        logger.warning("serp.http_error", status=response.status_code)
        return SerpResult(query=query, ok=False, error_code=f"SERP_HTTP_{response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        return SerpResult(query=query, ok=False, error_code="SERP_BAD_JSON")

    if "error" in payload:
        return SerpResult(query=query, ok=False, error_code="SERP_API_ERROR")

    hits: list[SerpHit] = []
    titles: list[str] = []
    for index, item in enumerate(payload.get("organic_results") or [], start=1):
        link = item.get("link") or ""
        domain = registrable_domain(link)
        if not domain:
            continue
        hits.append(SerpHit(domain=domain, position=item.get("position") or index, query=query))
        title = item.get("title")
        if title:
            titles.append(str(title))

    return SerpResult(query=query, hits=hits, titles=titles)


async def search_many(
    queries: list[str], *, settings: Settings | None = None, concurrency: int = 3
) -> list[SerpResult]:
    """Run several queries concurrently, bounded.

    Bounded because SerpApi bills per search and rate-limits: firing an
    unbounded gather at a paid, rate-limited API is how a detection run turns
    into a bill and a wall of 429s.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def one(q: str) -> SerpResult:
        async with semaphore:
            return await search(q, settings=settings)

    return list(await asyncio.gather(*(one(q) for q in queries)))


def is_plausible_competitor(domain: str, *, subject_domain: str) -> bool:
    """Whether a domain could be a competitor rather than a publisher.

    Excludes the subject's own domain (a site is not its own rival) and the
    known non-competitor list above.
    """
    if not domain or domain == subject_domain:
        return False
    return domain not in NON_COMPETITOR_DOMAINS

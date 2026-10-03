"""SerpApi search — §5.4 step 2, "query SERP for niche keywords".

No SerpApi SDK. Their API is a single authenticated GET returning JSON, and
`httpx` is already a vetted dependency (BSD-3-Clause), so the official client
would add a package, a sync-only call style inside an async service, and
another licence to audit — for one HTTP request.

=============================================================================
FACTS-ONLY BOUNDARY
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
import time
from dataclasses import dataclass, field

import httpx
import structlog

from ..config import Settings, get_settings
from .crawl import registrable_domain

logger = structlog.get_logger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
RESULTS_PER_QUERY = 10

# --- The request's bound (2026-10-03) ----------------------------------------
# Production logged `competitors.serp_failed error_code=SERP_TIMEOUT` for a scan's
# SERP queries: each had waited out the 25 s timeout this module used to carry, and
# the scan lost its SERP signal. The measurement that followed ran this module's own
# `search` for real: 24 uncached searches (4 businesses x the 6 queries `build_queries`
# makes, three at a time as `search_many` runs them), all ok. Slowest 6.93 s, p95 2.54 s,
# median 1.54 s, none over 10 s; a business's six queries took 3.2-9.0 s in all.
#
# So 25 s was not too short and three at once is not too many. A provider that answers
# in 1.5 s does not take 25 s on several queries at once unless a connection hung, or
# SerpApi (or the path to it from Render) was briefly down. The fix is to stop one hung
# request from costing the signal, and to make the next failure say what kind it was:
#
#   * 10 s per attempt: about 1.4x the slowest search measured and 4x the p95. It is a
#     DEADLINE on the whole attempt (`asyncio.timeout`), because httpx's own budgets apply
#     to each network step (connect, write, read, pool) and not to the attempt: a response
#     that keeps arriving a byte at a time never runs one out. Under the deadline sit those
#     budgets: connect 5 s, because a TLS handshake to SerpApi is well under a second, and
#     write, read and pool 9 s. A second under, so that a connection that hangs, the
#     failure seen, ends on its own phase's timeout and the log line names the phase. Only
#     an attempt that no one phase accounts for ends on the deadline, and is `total`.
#   * ONE retry, two attempts in all, after a 1 s pause and on a NEW client, so on a new
#     connection: the retry must not wait on the connection that hung. Only where no
#     answer came, a timeout or a transport failure (refused, reset, DNS). Never for an
#     answer, whatever it says (401, 429, 5xx, bad JSON, an `error` payload): the
#     provider has spoken, and asking again would be billed or rate-limited again.
#     "No answer" does not mean "not billed": SerpApi may already have run, and billed,
#     the search whose answer never arrived, so a retry after a read failure can be a
#     second billed search. Only a failure to connect certainly was not. So every retry
#     is logged, `serp.retried` when one was needed and `serp.attempts_failed` when it
#     did not help, and the extra searches against the shared plan can be counted.
#   * The worst case for one query is 10 + 1 + 10 = 21 s of waiting (it was 25 s), plus
#     the fraction of a second it takes to open two clients. The deadline makes that a
#     ceiling, not an expectation. `search_many` runs three at a time, so a detection's
#     six queries are two waves: at most 42 s (it was 50 s), which is also all that
#     `POST /clients/{clientId}/competitors/detect` waits on SerpApi. The phase stays
#     bounded and is no longer than before. `test_scan_executor.py` adds `QUERY_CEILING_S`
#     into the scan's worst case.
ATTEMPT_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 5.0
PHASE_TIMEOUT_S = 9.0
PHASE_TIMEOUTS = httpx.Timeout(PHASE_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
MAX_ATTEMPTS = 2
RETRY_PAUSE_S = 1.0
QUERY_CEILING_S = MAX_ATTEMPTS * ATTEMPT_TIMEOUT_S + (MAX_ATTEMPTS - 1) * RETRY_PAUSE_S

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


_TIMEOUT_KINDS: tuple[tuple[type[httpx.TimeoutException], str], ...] = (
    (httpx.ConnectTimeout, "connect"),
    (httpx.ReadTimeout, "read"),
    (httpx.WriteTimeout, "write"),
    (httpx.PoolTimeout, "pool"),
)


def _failure_kind(exc: Exception) -> str:
    """What kind of failure `exc` was, from its class alone.

    A timeout is `connect`, `read`, `write` or `pool`: the phase that gave up is what
    tells a host that never answered from one that answered and then went quiet. An
    attempt that ran out its own deadline (the builtin `TimeoutError`, raised by
    `asyncio.timeout`) is `total`. Any other failure is its class name. **Never its
    message**: an httpx error's text can carry the request URL, and the URL carries the
    API key.
    """
    if isinstance(exc, TimeoutError):
        return "total"
    for timeout_class, kind in _TIMEOUT_KINDS:
        if isinstance(exc, timeout_class):
            return kind
    return type(exc).__name__


def _since_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _gave_up(query: str, exc: Exception, kinds: list[str], started: float) -> SerpResult:
    """The query that no attempt answered: one line of numbers and kinds, and the code."""
    # Numbers and kinds only. NEVER the exception's text, the request, the URL or the
    # params: the URL carries the API key.
    logger.warning(
        "serp.attempts_failed", attempts=len(kinds), elapsed_ms=_since_ms(started), kinds=kinds
    )
    timed_out = isinstance(exc, httpx.TimeoutException | TimeoutError)
    return SerpResult(
        query=query, ok=False, error_code="SERP_TIMEOUT" if timed_out else "SERP_UNREACHABLE"
    )


async def _get(query: str, params: dict[str, str]) -> httpx.Response | SerpResult:
    """The search request, tried again once if no answer came. A `SerpResult` means none did.

    Only a timeout, a transport failure (`httpx.TransportError`: refused, reset, DNS) or
    an attempt that ran out its deadline is tried again. That is not a promise that the
    first attempt cost nothing: a read failure may already have been processed, and
    billed, by SerpApi, so a retry can be a second billed search. Every retry is logged
    for that reason, `serp.retried` when one was needed and `serp.attempts_failed` when
    it did not help. Whatever answers is returned as it is and judged by the caller, and
    so is anything else httpx raises (a body it cannot decode is an answer too). The
    error codes are the ones this module has always had.
    """
    started = time.perf_counter()
    kinds: list[str] = []
    while True:
        try:
            # A new client for each attempt, so the retry never waits on the connection
            # that hung. The deadline is inside it, so that a cut attempt closes its
            # client in the ordinary way and not under a cancellation.
            async with httpx.AsyncClient(timeout=PHASE_TIMEOUTS) as client:
                async with asyncio.timeout(ATTEMPT_TIMEOUT_S):
                    response = await client.get(SERPAPI_URL, params=params)
        except (httpx.TransportError, TimeoutError) as exc:
            kinds.append(_failure_kind(exc))
            if len(kinds) < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_PAUSE_S)
                continue
            return _gave_up(query, exc, kinds, started)
        except httpx.HTTPError as exc:
            kinds.append(_failure_kind(exc))
            return _gave_up(query, exc, kinds, started)
        if kinds:
            # A retry was needed, and it was answered. Not a failure, and still a second
            # search against the plan. Numbers and kinds only, as above.
            logger.info(
                "serp.retried", attempts=len(kinds) + 1, kinds=kinds, elapsed_ms=_since_ms(started)
            )
        return response


async def search(
    query: str, *, settings: Settings | None = None, num: int = RESULTS_PER_QUERY
) -> SerpResult:
    """Run one Google search via SerpApi. Never raises for an upstream failure.

    A request that got no answer (a timeout, a refused or reset connection, an attempt
    that ran past its deadline) is made once more, after a pause; an answer never is.
    The bound is at the top of the module.
    """
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

    got = await _get(query, params)
    if isinstance(got, SerpResult):
        return got
    response = got

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

    A search that is tried again does so inside its slot, so the three never
    become four, and a detection's six queries are two waves of at most
    `QUERY_CEILING_S` each.
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

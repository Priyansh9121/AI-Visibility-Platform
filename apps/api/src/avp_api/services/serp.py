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
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field

import httpx
import structlog

from ..config import Settings, get_settings
from . import usage
from .concurrency import gather_cancelling
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
#     Whether an attempt that got no answer is billed is settled below. Every retry is
#     logged, `serp.retried` when one was needed and `serp.attempts_failed` when it did
#     not help, and so is every query that was answered, `serp.completed`, with how long
#     it took: the latency distribution of the searches Render makes. How to read the
#     three lines: docs/runbook.md, "Reading competitor-search lines".
#   * The worst case for one query is 10 + 1 + 10 = 21 s of waiting (it was 25 s), plus
#     the fraction of a second it takes to open two clients. The deadline makes that a
#     ceiling, not an expectation. `search_many` runs three at a time, so a detection's
#     six queries are two waves: at most 42 s (it was 50 s), which is also all that
#     `POST /clients/{clientId}/competitors/detect` waits on SerpApi. The phase stays
#     bounded and is no longer than before. `test_scan_executor.py` adds `QUERY_CEILING_S`
#     into the scan's worst case.
#
# Is an attempt that got no answer billed? Looked into on 2026-10-03.
#
# SerpApi's pricing page (read 2026-10-03): "Only successful searches are counted toward
# your monthly searches. Cached, errored, and failed searches are not." Its Search API page:
# "Cached searches are free, and are not counted towards your searches per month."
#
# Measured the same day from a development Mac: 12 requests for 10 queries, 2 of them
# retried after a read timeout. 8 were answered and 4 attempts timed out on the read. The
# account's month count read 66 before, 75 at 17:20:57, and 71 at 17:23:15 and at 17:27:25,
# so 5 searches were counted in all. The count rose by 4 more than it kept, the number of
# attempts that timed out, which fits those being counted for a moment and then dropped; and
# 5 is fewer than the 8 answered, which fits SerpApi's cache serving a repeat of a recent
# identical search free.
#
# So a failure to connect never reaches SerpApi and is never billed, a read failure was not
# billed when measured, and SerpApi bills only a search it records as successful. A search
# it completed after we stopped waiting would be a successful search, and the account's
# count is the only place that would show it: no line this module logs can.
#
# What the retry has done so far: of the 3 queries seen to time out on a read (production's
# .com.au client's query and 2 measured here), the retry timed out on a read too, `attempts=2
# ... kinds=['read', 'read']`, about 19.1 to 19.3 s for the query in all (a 9 s read, the 1 s
# pause, a 9 s read). The founder has decided to keep retrying a read failure; this records
# what was seen and changes nothing.
ATTEMPT_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 5.0
PHASE_TIMEOUT_S = 9.0
PHASE_TIMEOUTS = httpx.Timeout(PHASE_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
MAX_ATTEMPTS = 2
RETRY_PAUSE_S = 1.0
QUERY_CEILING_S = MAX_ATTEMPTS * ATTEMPT_TIMEOUT_S + (MAX_ATTEMPTS - 1) * RETRY_PAUSE_S

# --- The market a search is made in (2026-10-03) -----------------------------------------
# `search` used to send `gl=us` for every client. Production on 2026-10-03, two new clients:
# an Indian business (a .in domain) logged `competitors.serp_gate_applied considered=44
# eligible=27 gated_out=17` and `competitors.detected confidence=0.000 status=weak_signal
# returned=5`; an Australian one (.com.au), with one query timed out, `considered=44
# eligible=14 gated_out=30` and `confidence=0.000`. `decide_detection` takes confidence as the
# share of the returned rivals that BOTH signals found, so 0.000 is five rivals and not one
# found by search and the model.
#
# Reproduced the same day through this code path, with each client's own classification and
# real searches, varying only `gl` and keeping the same co-citation answers in both arms.
# The .in client under gl=us: 3 queries, 27 result domains, none a brand the model had
# named, so 0 corroborated and 0.000. The .com.au client under gl=us: 2 queries, 17 domains,
# none named, 0.000; under gl=au the same 2 queries returned one rival's own domain, that
# rival was corroborated and the confidence was 0.200, status ok. Searched from the United
# States, neither client's results named a rival the model named; searched from Australia,
# the Australian client's did. The market is not the whole of it: under gl=in two of the .in
# client's three queries got no answer (both read timeouts, twice each) and the third named
# no rival the model named either.
#
# So a search is made in the client's own market. Nothing in the data model records a country
# (no field on Client, Agency or the classification output), so the domain is the only source
# there is, and a country-code TLD names it. Anything else (.com, .org, .net, .io, .ai, .co,
# any label not listed) says nothing about where the business trades and searches the default,
# Australia, because the pilots are Australian agencies. The list is extended by adding a row.
# `hl` stays `en`: the queries are English. The scoring formula does not change (and so
# `FORMULA_VERSION` does not): what changes is the input detection is given.
DEFAULT_SEARCH_COUNTRY = "au"
SEARCH_COUNTRY_BY_TLD: Mapping[str, str] = {
    "au": "au",
    "nz": "nz",
    "uk": "uk",  # the United Kingdom's own TLD, and Google's `gl` for it (ISO's is "gb")
    "ie": "ie",
    "ca": "ca",
    "us": "us",
    "za": "za",
    "in": "in",
    "sg": "sg",
    "my": "my",
    "ph": "ph",
    "hk": "hk",
    "id": "id",
    "jp": "jp",
}
# The only values `search` will send as `gl`: one this module could itself have chosen.
_SEARCH_COUNTRIES = frozenset(SEARCH_COUNTRY_BY_TLD.values())

# --- What is left of the month (2026-10-04) ---------------------------------------------------
# The plan is 250 searches a month, shared with production (Free Plan, read from `account.json`:
# build log, the AI Overview investigation), and nothing said how many were left until someone
# opened SerpApi's account page. `quota_left` asks the account endpoint once for each detection
# (`competitors.detect_from_facts`, which says why where it does) and writes the answer.
#
#   * A free read: an `account.json` call does not count as a search (build log, the same
#     investigation). It is not a search and never goes through `search`.
#   * The field is `plan_searches_left`, what the plan still allows this month. It is what
#     `searches_per_month` is the size of, which is why the line carries both (`left=184
#     plan_searches=250`). `total_searches_left` is that plus any one-time extra credits. The two
#     are equal unless credits were bought (none are recorded for this account, a Free Plan), and
#     then a warning on the plan's figure alone fires while credits remain.
#   * It lags. SerpApi's counts trail the searches by a minute or two, and a search that failed
#     can be counted for a moment and then dropped (measured 2026-10-03: the account's month
#     count read 66, then 75, then 71, where it stayed). A figure read right after a detection is
#     therefore good to within that detection's own searches, and the next detection's line is the
#     settled one. It is a reading, not a ledger.
#   * The bound is 5 s for the whole read, a deadline (`asyncio.timeout`, as `_get`'s is), with
#     each httpx phase (connect, write, read, pool) at 4 s, a second under it so that a hung
#     connect ends on its own phase's timeout and the line names the phase. No retry: the figure
#     is a convenience, and a second wait would be a cost to the detection it is read in.
#   * A read that failed writes `serp.quota_unavailable` at INFO, the kind and nothing else, one
#     line for the detection. Not DEBUG: DEBUG is off in staging and production, and turning it on
#     there prints `classify.result` and `fixes.reasoning` as well (`logs.py`), so a read that had
#     stopped working would be invisible, or visible only at the price of printing the model's
#     reasoning about client sites.
#   * Under 50 is low: a detection makes up to six searches, so under 50 is at most eight more
#     detections.
#   * The body also carries the API key and the account owner's address. Two counts are taken
#     from it and the rest is dropped; never the body, the URL or an exception's text goes into a
#     line, for the reason `_failure_kind` gives.
ACCOUNT_URL = "https://serpapi.com/account.json"
QUOTA_TIMEOUT_S = 5.0
QUOTA_PHASE_TIMEOUTS = httpx.Timeout(4.0)
QUOTA_LOW_BELOW = 50

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


def search_country(domain: str) -> str:
    """The Google country (`gl`) to search in for a client's domain.

    The domain's last label if it is a country-code TLD in `SEARCH_COUNTRY_BY_TLD`, else
    the default: `example.com.au` is `au`, `example.in` is `in`, `example.com` is the
    default. Pure: no I/O, case does not matter, and a trailing dot is allowed.
    """
    host = domain.strip().lower().rstrip(".")
    if "." not in host:
        # A bare name has no TLD, whatever it is called.
        return DEFAULT_SEARCH_COUNTRY
    return SEARCH_COUNTRY_BY_TLD.get(host.rpartition(".")[2], DEFAULT_SEARCH_COUNTRY)


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
    # Into the scan's total, if this is one (`usage.py`): every request that got no answer, and
    # nothing that did. Whether such a request is billed is the question at the top of the module.
    usage.record_serp_search(attempts=len(kinds), no_answer=len(kinds), answer=None)
    timed_out = isinstance(exc, httpx.TimeoutException | TimeoutError)
    return SerpResult(
        query=query, ok=False, error_code="SERP_TIMEOUT" if timed_out else "SERP_UNREACHABLE"
    )


@dataclass(slots=True)
class _Answered:
    """What `_get` returns when SerpApi answered: the response, and what getting it took."""

    response: httpx.Response
    # 1, or 2 when the first attempt got no answer and the retry did.
    attempts: int
    # `time.perf_counter()` when the first attempt began, so that the caller can say how long
    # the query took from there to the end of parsing the answer.
    started: float


async def _get(query: str, params: dict[str, str]) -> _Answered | SerpResult:
    """The search request, tried again once if no answer came. A `SerpResult` means none did.

    Only a timeout, a transport failure (`httpx.TransportError`: refused, reset, DNS) or
    an attempt that ran out its deadline is tried again. A retry costs time, up to the pause
    and another attempt's deadline. Whether an attempt that got no answer is billed is
    settled at the top of the module: a failure to connect never was, and a read failure
    was not when measured. Every retry is logged, `serp.retried` when one was needed and
    `serp.attempts_failed` when it did not help. Whatever answers is returned as it is, with
    the number of attempts it took and when the first began, and judged by the caller, and
    so is anything else httpx raises (a body it cannot decode is an answer too). The error
    codes are the ones this module has always had.
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
            # request. Numbers and kinds only, as above.
            logger.info(
                "serp.retried", attempts=len(kinds) + 1, kinds=kinds, elapsed_ms=_since_ms(started)
            )
        return _Answered(response=response, attempts=len(kinds) + 1, started=started)


async def search(
    query: str,
    *,
    settings: Settings | None = None,
    num: int = RESULTS_PER_QUERY,
    country: str = DEFAULT_SEARCH_COUNTRY,
) -> SerpResult:
    """Run one Google search via SerpApi. Never raises for an upstream failure.

    The search is made in `country` (Google's `gl`; `search_country` picks it for a
    client's domain). A value that is not one of `SEARCH_COUNTRY_BY_TLD`'s values is sent
    as the default, so no arbitrary string ever reaches the request. `hl` stays `en`.

    A request that got no answer (a timeout, a refused or reset connection, an attempt
    that ran past its deadline) is made once more, after a pause; an answer never is.
    The bound is at the top of the module, and so is whether an attempt that got no answer
    is billed (a failure to connect never was, and a read failure was not when measured).

    One line is logged for each query that reached SerpApi: `serp.completed` when it was
    answered, whatever the answer said, and `serp.attempts_failed` (from `_get`) when it
    was not. A missing key, unset or empty, sends nothing, logs neither and is the result
    `SERPAPI_KEY_MISSING`; the detection counts it as a search that failed and goes on.
    """
    settings = settings or get_settings()
    try:
        api_key = settings.provider_key("serpapi_key")
    except RuntimeError:  # the key is unset: `provider_key` says so by raising
        api_key = ""
    if not api_key:
        return SerpResult(query=query, ok=False, error_code="SERPAPI_KEY_MISSING")

    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": str(num),
        "hl": "en",
        "gl": country if country in _SEARCH_COUNTRIES else DEFAULT_SEARCH_COUNTRY,
    }

    answered = await _get(query, params)
    if isinstance(answered, SerpResult):
        return answered

    result = _judge(query, answered.response)
    # The one line for a query SerpApi answered; the time is read after the answer is
    # parsed. Facts only: how long, how many attempts, how many hits, the HTTP status and
    # this module's code. NEVER the query, the market, the key, the URL, the body or an
    # exception's message: the URL carries the API key.
    logger.info(
        "serp.completed",
        elapsed_ms=_since_ms(answered.started),
        attempts=answered.attempts,
        hits=len(result.hits),
        status=answered.response.status_code,
        error_code=result.error_code,
    )
    # Into the scan's total, if this is one (`usage.py`): the requests that got no answer before
    # the one that did, and what that one said. An answer that is an error is not billed (the
    # pricing page, quoted at the top of this module), except a 200 whose body could not be read:
    # SerpApi sent it as a 200 and may have counted it, and it lands here as an error too. An `ok`
    # one is billed, unless SerpApi's cache served it.
    usage.record_serp_search(
        attempts=answered.attempts,
        no_answer=answered.attempts - 1,
        answer="ok" if result.ok else "error",
    )
    return result


# The highest rank a result is taken to have. Google answers at most 100 results to a request (the
# ceiling of `num`) and `search` asks for 10, so no real rank is near it. It is here so that an
# absurd number cannot get as far as scoring, which takes `1 / sqrt(position)`: that raises
# `OverflowError` for an integer of 309 digits or more, and a rank that high would weigh nothing.
MAX_POSITION = 1000


def _position(value: object) -> int | None:
    """A result's own `position`, if it is one: a whole number from 1 to `MAX_POSITION`, else None.

    JSON has one kind of number and Python two, so `2.0` is the whole number 2 and comes out as an
    `int`. A bool is an `int` to Python and is not a rank; nor is a string (`"1"`), a list, an
    object, a fraction, NaN, infinity, zero or a negative. `_judge` gives such an item its place
    on the page, which is what an item with no `position` has always had, so that
    `competitors._position_weight` is only ever handed an int that it can take.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    if isinstance(value, int) and 1 <= value <= MAX_POSITION:
        return value
    return None


def _judge(query: str, response: httpx.Response) -> SerpResult:
    """What an answer says: its hits, or this module's code for what went wrong.

    A 401, a 429, any other status that is not a 200, a body that is not a page of results and
    one that carries an `error` are each an error, and none of them is asked for twice. A body
    that is not a page of results is `SERP_BAD_JSON`: not JSON (or nested so deeply that the
    parser gives up), JSON that is not an object (a list, a string, a number, `true`, `false` or
    `null`), an `organic_results` that is not a list (absent or `null` is a page with nothing on
    it), or a list that is not empty and has no object in it. It is an answer like any other, so
    `search` logs and counts it as one.

    A list of results with SOME items that are not objects keeps the objects and skips the rest,
    as an item with no usable link has always been skipped: a hit is a fact taken from one item
    alone, and the search was answered, so one stray item does not throw away its page. A skipped
    item keeps its place in the page's numbering. But a list with no object in it at all is an
    error and not an `ok` search with no hits, because `decide_detection` counts the `ok`
    searches, and "could not be read" must not become "SerpApi ran and found nothing".

    Inside an item that is an object, a `link` that is not a string is no usable link and the item
    is skipped as one with none is, and a `position` that is not a whole number from 1 to
    `MAX_POSITION` is not a position and the item has its place on the page instead, as an item
    with no `position` always had.
    """
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
    except (ValueError, RecursionError):
        # Not JSON, or nested so deeply that the parser gives up (9,997 levels on Python 3.12):
        # `RecursionError` is a `RuntimeError`, not a `ValueError`, and would otherwise leave here.
        return SerpResult(query=query, ok=False, error_code="SERP_BAD_JSON")

    # JSON that parses is not always an object: a list, a string, a number, `true`, `false` and
    # `null` do too. This is asked before anything else is asked of the payload, because what
    # follows is answered, or raises, according to what it is: `"error" in payload` is a substring
    # test on a string, a membership test on a list (both true for the word `error`, which would
    # be a `SERP_API_ERROR` SerpApi never sent) and a `TypeError` on a number, a bool or `null`.
    if not isinstance(payload, dict):
        return SerpResult(query=query, ok=False, error_code="SERP_BAD_JSON")

    if "error" in payload:
        return SerpResult(query=query, ok=False, error_code="SERP_API_ERROR")

    # Absent or `null` is a page with nothing on it. Anything else has to be the list it is called:
    # not `or []`, which would also take `{}`, `""`, `0` and `false` for an empty page.
    results = payload.get("organic_results")
    if results is None:
        results = []
    if not isinstance(results, list):
        return SerpResult(query=query, ok=False, error_code="SERP_BAD_JSON")

    hits: list[SerpHit] = []
    titles: list[str] = []
    unreadable = 0
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            # Not a result at all. Skipped, as an item with no usable link is below, and still a
            # place in the page: a `position` that falls back to `index` does not shift for the
            # items after it.
            unreadable += 1
            continue
        link = item.get("link")
        # A `link` that is not a string is one nobody can read, which is no usable link: skipped
        # as an item with none is, and still a place in the page. `registrable_domain` takes a
        # string and raises `AttributeError` for a number, a list, an object or a bool.
        domain = registrable_domain(link) if isinstance(link, str) else ""
        if not domain:
            continue
        # The provider's own rank when it is one (a whole number from 1 to `MAX_POSITION`), else the
        # item's place on the page: the same number an item with no `position` always had.
        position = _position(item.get("position")) or index
        hits.append(SerpHit(domain=domain, position=position, query=query))
        title = item.get("title")
        if title:
            titles.append(str(title))

    if results and unreadable == len(results):
        # A list with things in it and not one of them an object is not a page of results, and an
        # `ok` search with no hits would say that it was. `decide_detection` counts the `ok`
        # searches, so that would be "SerpApi ran and found nothing" where the truth is that we
        # could not tell. One object, however empty, makes it a list of results; `[]` is a page
        # with nothing on it.
        return SerpResult(query=query, ok=False, error_code="SERP_BAD_JSON")

    return SerpResult(query=query, hits=hits, titles=titles)


async def search_many(
    queries: list[str],
    *,
    settings: Settings | None = None,
    concurrency: int = 3,
    country: str = DEFAULT_SEARCH_COUNTRY,
) -> list[SerpResult]:
    """Run several queries concurrently, bounded, all in the one `country`.

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
            return await search(q, settings=settings, country=country)

    return list(await gather_cancelling(*(one(q) for q in queries)))


def _whole_count(value: object) -> int | None:
    """A count from a JSON body: a whole number of at least zero, and None for anything else.

    A string (`"184"`), a float (`184.0`), a bool (`True` is an `int` to Python) and a negative
    are not counts. A provider that changes its body's shape costs a line, never a detection.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _quota_unavailable(kind: str) -> int | None:
    """What `quota_left` returns when it could not read: one info line, and None.

    The kind and nothing else, so that the line is as safe as `_failure_kind`'s word is. INFO,
    so that it is in the logs Render keeps (the reason is at the top of the module). The line is
    written inside `suppress`: a logger that fails must not turn a read that was never going to
    be used into an exception. (It is typed `int | None`, not `None`, so that `quota_left` can
    `return` it.)
    """
    with suppress(Exception):
        logger.info("serp.quota_unavailable", kind=kind)
    return None


async def quota_left(settings: Settings | None = None) -> int | None:
    """How many searches the plan still allows this month, or None when that could not be read.

    One request to SerpApi's account endpoint, which is free and is not a search; bounded by
    `QUOTA_TIMEOUT_S` and never retried. The bound, the field read and why are at the top of
    the module, with how far behind SerpApi's own count can be. Never raises for anything that
    can go wrong in the read: no key, a timeout, a transport failure, an answer that is not a
    200, a body that is not a JSON object, a count that is missing or not a whole number of at
    least zero. A cancellation is not one of those and goes on as it was, because a scan's
    deadline cancels what it is running.

    Written (the three lines are read in docs/runbook.md, "Reading competitor-search lines"):

    * `serp.quota_left`, info: `left`, and `plan_searches` (the plan's size, `searches_per_month`)
      when the account gives one;
    * `serp.quota_low`, warning, as well and with the same fields, when `left` is under
      `QUOTA_LOW_BELOW`. Both, not either: the info line is the reading, so a search for it finds
      every figure, the lowest included, and the warning is the alert;
    * `serp.quota_unavailable`, info, when None is returned, instead of the two lines above, so
      one line for the detection: `kind` and nothing else. `no_key`, `http_<status>`, `bad_json`
      (a body that is not a JSON object), `no_count`, the phase of a timeout (`connect`, `read`,
      `write`, `pool`, or `total` for the deadline), or the class name of any other error.

    Facts only, and no secret: two counts are taken from the body and the rest is dropped (it
    carries the API key and the owner's address), and NEVER the URL, the params, the body or an
    exception's text goes into a line: the URL carries the key.
    """
    try:
        settings = settings or get_settings()
        try:
            api_key = settings.provider_key("serpapi_key")
        except RuntimeError:  # the key is unset: `provider_key` says so by raising
            api_key = ""
        if not api_key:
            return _quota_unavailable("no_key")

        # The deadline is inside the client, so that a cut read closes its client in the
        # ordinary way and not under a cancellation (as `_get` does).
        async with httpx.AsyncClient(timeout=QUOTA_PHASE_TIMEOUTS) as client:
            async with asyncio.timeout(QUOTA_TIMEOUT_S):
                response = await client.get(ACCOUNT_URL, params={"api_key": api_key})
        if response.status_code != 200:
            return _quota_unavailable(f"http_{response.status_code}")

        try:
            body = response.json()
        except ValueError:
            return _quota_unavailable("bad_json")
        if not isinstance(body, dict):
            return _quota_unavailable("bad_json")
        left = _whole_count(body.get("plan_searches_left"))
        if left is None:
            return _quota_unavailable("no_count")

        line: dict[str, int] = {"left": left}
        plan = _whole_count(body.get("searches_per_month"))
        if plan is not None:
            line["plan_searches"] = plan
        logger.info("serp.quota_left", **line)
        if left < QUOTA_LOW_BELOW:
            logger.warning("serp.quota_low", **line)
        return left
    except Exception as exc:  # noqa: BLE001 - never raises; a cancellation is not an Exception
        return _quota_unavailable(_failure_kind(exc))


def is_plausible_competitor(domain: str, *, subject_domain: str) -> bool:
    """Whether a domain could be a competitor rather than a publisher.

    Excludes the subject's own domain (a site is not its own rival) and the
    known non-competitor list above.
    """
    if not domain or domain == subject_domain:
        return False
    return domain not in NON_COMPETITOR_DOMAINS

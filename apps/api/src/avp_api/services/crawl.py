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
import codecs
import re
import zlib
from collections.abc import AsyncGenerator, Iterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urljoin, urlparse, urlsplit

import httpx
import structlog
import tldextract
from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from ..config import Settings, get_settings
from . import egress
from .ai_crawlers import CrawlPolicy, PolicyBasis, crawl_policy
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
# Reason code for a site whose robots.txt could not be read at all (launch
# checklist 3.41): no answer (a timeout, a refused connection, a name that does
# not resolve), a 5xx, a 429, or redirects that never reach a file. Until
# 2026-10-03 these were stored as ROBOTS_DISALLOWED, so a site that never
# answered was reported as one that asks crawlers to stay out. What the crawler
# DOES is unchanged: with no file to read `CrawlPolicy` forbids everything
# (`PolicyBasis.UNREACHABLE`, RFC 9309 §2.3.1.4: "assume complete disallow"; a
# 429 is this service's own addition to that rule), so nothing is fetched, and
# honest crawling comes before a complete scan. Only the CODE differs: the intake
# crawl returns it as its error code (the classifier's reason), the audit stores
# it as its error code, and the score excludes Technical Foundation as
# CRAWL_POLICY_UNREADABLE.
ROBOTS_UNREACHABLE = "ROBOTS_UNREACHABLE"
# `ADDRESS_NOT_PUBLIC` (imported from `egress`) is its sibling: the site's
# address, or a redirect's, is not a public one, so nothing was fetched.

ROBOTS_TIMEOUT = 10.0
# The most ONE request of the gate may take, in total: its address check, the
# connection, the answer and every byte of the body it reads. `ROBOTS_TIMEOUT` is
# httpx's, and httpx applies it per operation (the connection, each read), so a
# server that sends a byte every few seconds never trips it: until 2026-10-03 such
# a response held the request open for as long as the server liked, with the
# request's agency read slot and database connection behind it. A request that has
# not finished by this is "no answer". For robots.txt that is `ROBOTS_UNREACHABLE`
# and nothing is fetched, as for every other file that cannot be read; for a HEAD
# hop and the sitemap it is the failure they already had. It equals
# `ROBOTS_TIMEOUT` on purpose: `crawl_run_cap_s` and `technical_audit.audit_run_cap_s`
# add up `(MAX_REDIRECTS + 1)` requests at that figure for a landing host's
# robots.txt, which was an assumption about a prompt server and is now the ceiling,
# so neither cap moves. Raising it by `d` seconds would raise each cap by `6 * d`.
ROBOTS_REQUEST_DEADLINE_S = 10.0
# Redirects followed, for robots.txt (RFC 9309 §2.3.1.2 asks for at least
# five) and for a page, whose redirects the gate follows by hand.
MAX_REDIRECTS = 5
# The most the gate's reads BEFORE THE BROWSER may take, all of them together (launch
# checklist 3.41, round 3). Each request ends by `ROBOTS_REQUEST_DEADLINE_S`, but a run
# makes many before it starts a browser: the start host's robots.txt; for every redirect
# hop of the homepage a HEAD and, for a host not yet read, its robots.txt (a chain of up
# to `MAX_REDIRECTS + 1` requests of its own); and, for the audit, the sitemap's hops. A
# chain of hosts that each answer just inside their deadline held a run for about 780 s
# with its agency's read slot and database connection behind it, and a scan's audit for as
# long. This is one budget over all of it. 60 s is `(MAX_REDIRECTS + 1)` requests at their
# full deadline, one whole worst-case robots.txt chain, and it clears the slow but honest
# site: robots.txt, the sitemap and a HEAD at 10 s each, plus a www redirect's own two, is
# 50 s. A run that has not finished reading by then ends `TIMEOUT` and starts no browser:
# a report about our attempt, never a finding about the site.
PRE_BROWSER_BUDGET_S = 60.0
# RFC 9309 §2.5: a crawler must read at least the first 500 KiB. It is also the
# MOST of a robots.txt that is read: the body is streamed and the connection is
# dropped at this many characters, so a long or endless one costs this much
# memory and its deadline, never its length. A compressed one costs no more: it is
# inflated no further than four bytes a character of it (`_read_text`).
ROBOTS_MAX_CHARS = 512_000
# What the gate asks servers to send, and so all it decodes: gzip and deflate, one
# coding at a time (`_coding_of`). httpx offers whatever decoders it finds installed, so a
# future `brotli` would have servers answering `br`, which `_coding_of` refuses: said here,
# once, rather than left to the library's default.
ACCEPT_ENCODING = "gzip, deflate"


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
    too many redirects, a response that had not finished by the request's deadline
    (`ROBOTS_REQUEST_DEADLINE_S`). `body` is empty unless something did, and is at most
    the first `ROBOTS_MAX_CHARS` characters of it.
    """

    status: int | None
    content_type: str = ""
    body: str = ""


@dataclass(frozen=True, slots=True)
class Reply:
    """What one request of the gate got back: the status, the content type, and the
    first characters of the body, as many as the caller asked to read (none for a HEAD,
    and none of a body that is not a 2xx: a 404 page is no part of an answer).

    Not an `httpx.Response`: the body is streamed and the connection dropped once
    the caller has what it asked for, so there is no response left to read from.
    """

    status: int
    content_type: str = ""
    body: str = ""


@dataclass(frozen=True, slots=True)
class Followed:
    """Where a request ended after the gate followed its redirects.

    `response` is None when the last hop was not sent (robots.txt stopped it) or
    got no answer (including no answer by `ROBOTS_REQUEST_DEADLINE_S`, and a body the
    gate would not read: see `_UnreadableBodyError`).

    `refusal` says why robots.txt stopped it: `ROBOTS_DISALLOWED` when the file
    was read and forbids the path, `ROBOTS_UNREACHABLE` when it could not be read
    (so nothing may be fetched). None when robots.txt did not stop it, whether
    the hop was sent or failed for any other reason.

    `url` is an address robots.txt has been asked about: the one that was sent, or the
    one it stopped, or, when the redirects ran out, the last Location, whose robots.txt
    is read before it is handed on (so a caller that loads it is loading a page the file
    allows).
    """

    url: str
    response: Reply | None
    refusal: str | None = None


class _UnreadableBodyError(Exception):
    """A body the gate will not turn into text, so the request got no usable answer.

    Two content codings stacked, one that is not gzip or deflate, a gzip or deflate stream
    that is corrupt, and a charset the gate does not decode (`_text_decoder`: anything off
    its allow-list). Never leaves `_exchange`: a robots.txt it cannot read is
    `ROBOTS_UNREACHABLE`, a sitemap it cannot read is not found.
    """


# The content codings the gate decodes, ONE at a time: gzip (which some servers still call
# x-gzip) and deflate. Each goes through a single `zlib.decompressobj` with a
# `max_length`, so no more than a cap's worth of a body is ever inflated. Anything else,
# or two codings stacked, is not decoded at all: httpx's own decoders chain one
# decompressor per listed coding with no limit on what each produces, and a gzip stream
# of a few hundred kilobytes inflates to gigabytes, all of it on the event loop.
_ZLIB_WBITS = {
    "gzip": zlib.MAX_WBITS | 16,
    "x-gzip": zlib.MAX_WBITS | 16,
    "deflate": zlib.MAX_WBITS,
}
# A character is at most four bytes in any charset on the allow-list below (UTF-8, UTF-16
# and UTF-32 take up to four, the rest one), so `n` characters never take more than `4 * n`
# bytes. It is the most that is ever inflated, or read of an uncompressed body, for a cap of
# `n` characters.
_MAX_BYTES_PER_CHAR = 4
# The charsets the gate decodes (launch checklist 3.41, round 4): UTF-8 (with and without a
# BOM), UTF-16 and UTF-32 in either byte order, ASCII, ISO-8859-n and Windows-125x.
# RFC 9309 asks for UTF-8 alone, and nearly every real file is plain ASCII. Names are
# compared as Python knows them (`codecs.lookup(name).name`, so `UTF8`, `latin1` and
# `windows-1252` are `utf-8`, `iso8859-1` and `cp1252`). A response that names no charset
# is UTF-8, as before.
#
# Why a list and not "whatever `codecs.lookup` knows": the decoding runs on the API's one
# event loop and never awaits, so neither a request's deadline nor the pre-browser budget
# can interrupt it, and the registry holds decoders that must not be run on a stranger's
# bytes. Some raise on `errors="replace"` or hand back bytes (hex, base64, zlib, bz2,
# rot13, idna, undefined). Some do not raise and cost more than their input: punycode's
# decode, in pure Python, grows faster than linearly with the piece it is given, and
# utf-7's buffered decoder re-decodes all it holds on every call. Every codec below is a
# linear-time C decoder (the UTF ones keep at most a few bytes between calls) that replaces
# what it cannot decode; `utf-16` and `utf-32` still raise for a stream with no byte-order
# mark, which `_decode` turns into the same refusal. Anything else is
# `_UnreadableBodyError`, before a byte of the body is read, so a robots.txt that names one
# fails closed as `ROBOTS_UNREACHABLE` and a sitemap that does is not found. (The East Asian
# multibyte codecs, KOI8 and the DOS code pages are linear too and could be added; they are
# not, so a file that names one is unreadable.)
_TEXT_CHARSETS = frozenset(
    {
        "utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le",
        "utf-32-be", "ascii",
    }
)
_TEXT_CHARSET_FAMILIES = re.compile(r"iso8859-\d{1,2}|cp125[0-8]")
# What one inflate call may produce. A call is synchronous, so a step this size holds the
# event loop for a fraction of a millisecond, and no call allocates more than this.
_INFLATE_STEP = 65_536


def _coding_of(response: httpx.Response) -> str | None:
    """The one content coding to undo, or None for a body that is not coded.

    `identity` and empty values are no coding. Two codings, or one that is not gzip or
    deflate (`br`, `zstd`, `compress`, one nobody has heard of), are refused: a coding we
    do not undo leaves bytes that are not text, and a robots.txt read as no rules at all
    is permission to crawl a site that may forbid it.
    """
    codings = [
        value
        for raw in response.headers.get_list("content-encoding", split_commas=True)
        if (value := raw.strip().lower()) and value != "identity"
    ]
    if not codings:
        return None
    if len(codings) > 1 or codings[0] not in _ZLIB_WBITS:
        raise _UnreadableBodyError(f"content-encoding {', '.join(codings)}")
    return codings[0]


class _Inflater:
    """One gzip or deflate stream, inflated a bounded step at a time.

    `feed` hands back the inflated bytes of what it is given, at most `room` of them in
    all and never more than `_INFLATE_STEP` from one call to zlib, so a stream that
    expands a thousandfold costs what the caller asked for and not what it expands to.
    """

    def __init__(self, coding: str) -> None:
        self._coding = coding
        self._z = zlib.decompressobj(_ZLIB_WBITS[coding])
        self._first = True
        self.finished = False

    def feed(self, raw: bytes, room: int) -> Iterator[bytes]:
        data = raw
        while room > 0 and not self.finished:
            step = min(_INFLATE_STEP, room)
            try:
                out = self._z.decompress(data, step)
            except zlib.error as exc:
                if self._coding == "deflate" and self._first:
                    # The RFC's "deflate" is zlib-wrapped and many servers send it raw:
                    # try raw on the first bytes, as httpx's decoder does.
                    self._first = False
                    self._z = zlib.decompressobj(-zlib.MAX_WBITS)
                    continue
                raise _UnreadableBodyError(f"corrupt {self._coding} stream") from exc
            self._first = False
            data = self._z.unconsumed_tail
            self.finished = self._z.eof
            room -= len(out)
            if out:
                yield out
            # A full step may leave more waiting inside zlib even with no input left (the
            # tail of a long match), so keep asking until a call comes back short.
            if len(out) < step and not data:
                break


def _text_decoder(response: httpx.Response) -> codecs.IncrementalDecoder:
    """The incremental decoder for the charset the response names, if the gate decodes it.

    The charset is httpx's (`Response.encoding`: the `charset=` of the content type when
    Python knows it, UTF-8 when there is none or nobody knows it), looked up and compared
    by the name Python gives it (`_TEXT_CHARSETS`). Anything else raises
    `_UnreadableBodyError` before a byte of the body is read: we do not guess at a file we
    cannot decode as it says it is, and the codecs we would have to run to try are the ones
    that must not be run on a stranger's bytes (see `_TEXT_CHARSETS`).
    """
    try:
        info = codecs.lookup(response.encoding or "utf-8")
    except (LookupError, ValueError, TypeError) as exc:
        raise _UnreadableBodyError("charset") from exc
    if info.name not in _TEXT_CHARSETS and not _TEXT_CHARSET_FAMILIES.fullmatch(info.name):
        raise _UnreadableBodyError(f"charset {info.name}")
    try:
        return info.incrementaldecoder(errors="replace")
    except (ValueError, TypeError, AssertionError) as exc:
        raise _UnreadableBodyError(f"charset {info.name}") from exc


def _decode(decoder: codecs.IncrementalDecoder, data: bytes, *, final: bool = False) -> str:
    """Text from bytes, or `_UnreadableBodyError` for a body its charset cannot read.

    The decoders that get here are the allow-list's (`_text_decoder`): linear-time codecs
    that replace what they cannot decode. The codecs that raise (hex, base64, zlib, bz2,
    rot13, idna, undefined) and the ones that never raise and cost more than their input
    (punycode, utf-7) were kept out before a byte was read, so nothing here depends on what
    they do. Two on the list still raise whatever `errors` says: `utf-16` and `utf-32` with
    no byte-order mark (`UnicodeError`, a ValueError), a file that is not readable as it
    says it is. That is no answer, and not a 500. The other handlers, and the check that
    what came back is text, are for a codec that is added to the list without a look at what
    it does.
    """
    try:
        text = decoder.decode(data, final)
    except (ValueError, LookupError, TypeError, AssertionError) as exc:
        # (UnicodeError is a ValueError.)
        raise _UnreadableBodyError("charset") from exc
    if not isinstance(text, str):
        raise _UnreadableBodyError("charset")
    return text


async def _read_text(response: httpx.Response, max_chars: int) -> str:
    """The first `max_chars` characters of a streamed body, and no more of it than that.

    `Response.text[:n]` needs the whole body first, and httpx's decoders inflate a coded
    one without limit. This reads the RAW stream (`aiter_raw`), undoes at most one gzip or
    deflate coding through a bounded inflater, decodes the text itself (a charset on the
    allow-list only, `_text_decoder`), and stops the moment it has `n` characters: a long,
    endless or compressed body costs `n` characters (and at most `4 * n` bytes inflated) and
    the time they take, never its length. The decoding is `Response.text`'s own (the same
    incremental codec, `errors="replace"`), so a body in a charset on the list that is
    shorter than `n` reads exactly as it always did.

    Raises `_UnreadableBodyError` for what it will not read (see there).
    """
    if max_chars <= 0:
        return ""
    coding = _coding_of(response)
    inflater = _Inflater(coding) if coding else None
    decoder = _text_decoder(response)
    parts: list[str] = []
    size = 0
    room = max_chars * _MAX_BYTES_PER_CHAR
    capped = False
    # httpx types `aiter_raw` as an AsyncIterator; at runtime it is an async generator,
    # which is what lets it be closed when this returns and not whenever the interpreter
    # gets to it (the stream underneath is dropped with the connection by the caller,
    # straight after).
    raw_chunks = cast("AsyncGenerator[bytes, None]", response.aiter_raw())
    async with aclosing(raw_chunks):
        async for raw in raw_chunks:
            pieces = inflater.feed(raw, room) if inflater else iter((raw[:room],))
            for piece in pieces:
                room -= len(piece)
                text = _decode(decoder, piece)
                parts.append(text)
                size += len(text)
                # Checked after EVERY piece, not every network chunk: one chunk of a
                # compressed body can stand for megabytes, and the next piece of it is
                # not inflated once there is enough.
                if size >= max_chars or room <= 0:
                    capped = True
                    break
            if capped:
                break
            if inflater is not None and inflater.finished:
                break
    if not capped:
        parts.append(_decode(decoder, b"", final=True))
    return "".join(parts)[:max_chars]


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

    **Each request is small and brief** (launch checklist 3.41, rounds 2 and 3). One
    request ends by `ROBOTS_REQUEST_DEADLINE_S` in total, and reads no more of a body than
    its caller asked for (`ROBOTS_MAX_CHARS` of a robots.txt; none of a HEAD's, a
    redirect's or an error page's): a server that sends a byte every few seconds, or a body
    of any length, compressed or not, no longer holds a run, its agency's read slot and its
    database connection open, or fills the process's memory. A robots.txt that has not
    finished by the deadline, or that the gate will not decode, is no answer, so
    `ROBOTS_UNREACHABLE`, and nothing is fetched, as for any file that cannot be read.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = ROBOTS_TIMEOUT,
        deadline: float | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        # Read when the gate is built, not when the module is: a caller (and a test)
        # that changes `ROBOTS_REQUEST_DEADLINE_S` is heard by the next gate.
        self._deadline = ROBOTS_REQUEST_DEADLINE_S if deadline is None else deadline
        self._files: dict[str, RobotsFile] = {}

    def _client(self) -> httpx.AsyncClient:
        # A client per call rather than one held open: a crawl makes a handful
        # of these requests, and a gate that owns nothing open can be asked
        # from inside a browser session without either outliving the other.
        # `guarded_transport` re-checks the resolved address at connection time
        # and connects to the address it checked (`egress.py`).
        return httpx.AsyncClient(
            timeout=self._timeout,
            headers={"User-Agent": self._user_agent, "Accept-Encoding": ACCEPT_ENCODING},
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

    async def _exchange(
        self, client: httpx.AsyncClient, method: str, url: str, max_chars: int
    ) -> tuple[Reply, str | None] | None:
        """One request of the gate, its address check to its last byte read, under ONE deadline.

        Returns what came back (the status, the content type, and up to `max_chars`
        of the body) with where it redirects to (None when it does not), or None
        when nothing did: a name that does not resolve, a refused connection, an
        error, no answer by `self._deadline`, a body it will not read
        (`_UnreadableBodyError`), or an address that is not http or https. A body is
        streamed and read no further than `max_chars`; **only a 2xx answer's body is read
        at all**: a redirect's, a 404's and an error page's are not, so a slow 404 is still
        "no file, crawl freely" and not a robots.txt that never answered. What is left
        unread is dropped with the connection. `EgressRefused` propagates.

        The deadline is the whole hop's, not an operation's: see
        `ROBOTS_REQUEST_DEADLINE_S`. It also bounds the address check, so a name
        that will not resolve in time is the same "no answer" as one that does not
        resolve at all.
        """
        # `egress.url_target` has no host to check for anything but http and https, so a
        # redirect to ws:, wss:, ftp: or file: would pass the gate's own address check
        # untouched and be left to the transport's. It is not a hop this gate makes.
        try:
            scheme = urlsplit(url).scheme
        except ValueError:
            return None
        if scheme not in ("http", "https"):
            return None
        try:
            async with asyncio.timeout(self._deadline):
                if not await self._egress_ok(url):
                    return None
                async with client.stream(method, url, follow_redirects=False) as response:
                    location = response.headers.get("location") if response.is_redirect else None
                    answered = location is None and 200 <= response.status_code < 300
                    reply = Reply(
                        response.status_code,
                        response.headers.get("content-type", ""),
                        await _read_text(response, max_chars) if answered else "",
                    )
                    return reply, location or None
        except (httpx.HTTPError, httpx.InvalidURL, TimeoutError, _UnreadableBodyError):
            return None

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
                got = await self._exchange(client, "GET", url, ROBOTS_MAX_CHARS)
                if got is None:
                    return RobotsFile(status=None)
                reply, location = got
                if location is None:
                    return RobotsFile(
                        status=reply.status, content_type=reply.content_type, body=reply.body
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

    async def readable(self, origin: str) -> bool:
        """False when this origin's robots.txt could not be read (`PolicyBasis.UNREACHABLE`).

        No answer by the deadline, a name that does not resolve, a timeout, a 5xx, a
        429, redirects that never reach a file: it is the file, not a rule in it,
        that stops the crawler. True for a file that was read and for one that is
        missing (a 404 is an answer).
        """
        return (await self.policy(origin)).basis is not PolicyBasis.UNREACHABLE

    async def refusal_code(self, url: str) -> str:
        """Why `allows` said no to `url`: the code the run ends with.

        `ROBOTS_UNREACHABLE` when the robots.txt that decided it could not be read
        (`readable`). The crawler fetches nothing then, as RFC 9309 §2.3.1.4 says,
        but the site said nothing. `ROBOTS_DISALLOWED` when the file was read and
        its rules forbid the path.

        Asked of a URL `allows` refused, so it reads the fetch `allows` just made
        (one per host, remembered) and sends nothing of its own; for a URL that is
        allowed the answer means nothing. Callers read the URL ONCE and pass the
        same value to `allows` and here: a page's `url` can move between two reads,
        and a code for another origin than the one that was refused is a wrong one.
        """
        return ROBOTS_DISALLOWED if await self.readable(origin_of(url)) else ROBOTS_UNREACHABLE

    async def follow(self, method: str, url: str, *, max_chars: int = 0) -> Followed:
        """Request `url`, following redirects by hand and checking each hop first.

        Each hop is checked twice before it is sent: against robots.txt, and
        for a public address (`EgressRefused` propagates out). Each hop is then
        one `_exchange`, under its own deadline.

        `max_chars` is how much of the final answer's body to read: none by
        default (a HEAD has none, and a caller that wants only the status asks for
        no body), and never more than that however long the body is.
        """
        async with self._client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                if not await self.allows(url):
                    return Followed(url, None, refusal=await self.refusal_code(url))
                got = await self._exchange(client, method, url, max_chars)
                if got is None:
                    return Followed(url, None)
                reply, location = got
                if location is None:
                    return Followed(url, reply)
                try:
                    following = urljoin(url, location)
                    urlsplit(following)
                except ValueError:
                    # Not a URL, so not an address to hand on: the hop that sent it is.
                    return Followed(url, None)
                url = following
        # Out of redirects. `url` is the last Location, which no hop has asked about: it
        # is handed to the caller as where to go (`resolve` gives it to the browser), so
        # robots.txt has its say first. Running out is not a failed fetch, and a longer
        # chain still works, but the browser is never sent where the file was not read.
        if not await self.allows(url):
            return Followed(url, None, refusal=await self.refusal_code(url))
        return Followed(url, None)

    async def resolve(self, url: str) -> Followed:
        """Where the homepage's redirects lead (`.url`), or why robots.txt stopped one (`.refusal`).

        Asked with HEAD, so a hop costs a response's headers and no page. A
        server that answers HEAD with no redirect (or not at all) leaves the
        address as it is, and the browser's own landing is checked after.

        A hop can be on another host than the one asked, so the refusal is the
        code of the host that stopped it: `www.` can forbid, or fail to answer
        for, what the bare domain allowed.
        """
        return await self.follow("HEAD", url)


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
          browser was given)                 60   (MAX_REDIRECTS + 1) x ROBOTS_REQUEST_DEADLINE_S
        the final egress check                3   FINAL_SETTLE_TIMEOUT_S + 1
        closing page, context, browser       35   TEARDOWN_ALLOWANCE_S
        the guard's name lookups             30   NAME_LOOKUP_ALLOWANCE_S
        the driver starting, set-up calls    15   RUN_MARGIN_S
                                           ----
        297 s for the default 4 pages; 266 s for the 3 intake asks for.

    The robots.txt line is a real ceiling since 2026-10-03: each request of the gate
    ends by `ROBOTS_REQUEST_DEADLINE_S` in total, where the figure used to be
    httpx's per-operation `ROBOTS_TIMEOUT`, which a slow server never reaches (so the
    sum was only ever true of a server that answered). The two are the same number,
    so the cap did not move.

    A cap that fires ends the run as `TIMEOUT`, the code a timeout already had here.
    """
    pages = max(max_pages, 1)  # the homepage is always read
    one_page = timeout_ms / 1000 + 2 * egress.SETTLE_TIMEOUT_S + EVALUATE_ALLOWANCE_S
    return (
        LAUNCH_ALLOWANCE_S
        + pages * one_page
        + (MAX_REDIRECTS + 1) * ROBOTS_REQUEST_DEADLINE_S
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
    `ROBOTS_DISALLOWED`, which the classifier records as its reason. A robots.txt
    that could not be read stops the crawl the same way (nothing may be fetched
    without it) but is its own outcome, `ROBOTS_UNREACHABLE`: the site did not
    forbid anything (launch checklist 3.41).

    **One browser at a time, under a cap** (M1, 2026-10-02). The browser run holds the
    process's one browser slot (`browser_slot`), and `crawl_run_cap_s` cuts it if
    something in it never returns: it ends as `TIMEOUT`, with nothing kept.

    **A budget for the reads before it** (launch checklist 3.41, round 3). The gate reads
    robots.txt and the homepage's redirects before any browser starts, each request under
    its own deadline, and `PRE_BROWSER_BUDGET_S` bounds all of them together. A run that
    spends it ends as `TIMEOUT` the same way, and starts no browser.

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
        try:
            async with asyncio.timeout(PRE_BROWSER_BUDGET_S):
                home = await gate.resolve(start_url)
        except TimeoutError:
            # Not a request's deadline (those end inside the gate as "no answer"): the budget
            # for all of them. A report about our attempt, never a finding about the site.
            logger.warning(
                "crawl.pre_browser_budget_spent", url=start_url, budget_s=PRE_BROWSER_BUDGET_S
            )
            return CrawlResult(
                signals=_nothing_kept(start_url), text_extract="", ok=False, error_code="TIMEOUT"
            )
        if home.refusal is not None:
            return _robots_refused(signals, home.refusal, start_url)

        return await _crawl(
            gate, home.url, signals,
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


def _robots_refused(signals: CrawlSignals, code: str, url: str) -> CrawlResult:
    """The result for a site whose robots.txt kept the crawler off its homepage: nothing read.

    `code` is `ROBOTS_DISALLOWED` (the file was read and forbids the page) or
    `ROBOTS_UNREACHABLE` (it could not be read). Each is logged under its own event,
    so the two can be counted apart; `url` is the site's own address, as before.
    """
    event = "crawl.robots_unreachable" if code == ROBOTS_UNREACHABLE else "crawl.robots_disallowed"
    logger.info(event, url=url)
    return CrawlResult(signals=signals, text_extract="", ok=False, error_code=code)


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
            landed = page.url  # read ONCE: the same address is asked about, named and logged
            if not await gate.allows(landed):
                refusal = await gate.refusal_code(landed)
                await guard.finish()
                return _robots_refused(signals, refusal, landed)
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

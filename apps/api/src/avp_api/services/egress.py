"""Where the crawler may connect — an app-level egress guard.

The crawler (`crawl.py`) and the technical audit (`technical_audit.py`) fetch
whatever address an agency types in, and follow whatever a site redirects to.
Without a check, that address could be one on our own network: a private or
loopback address, the cloud metadata address, an internal service. So every
outbound fetch resolves the host first and refuses any address that is not
public. The run ends with `ADDRESS_NOT_PUBLIC`, handled the way a robots.txt
refusal is (`crawl.ROBOTS_DISALLOWED`), when the site's own address, a
navigation's redirect hop or landing, or robots.txt is refused, or when Chromium
reports it connected to an address that is not public. A browser sub-resource or
iframe, and the audit's sitemap, refused before they are sent are skipped alone.

Three layers, because no one of them covers everything:

1. `RobotsGate` (`crawl.py`) calls `resolve_public` before EVERY request it
   sends, on every redirect hop it follows by hand.
2. `guarded_transport` — the gate's HTTP client — resolves again at connection
   time and connects to the very address it checked (`GuardedNetworkBackend`),
   so a name that resolves differently a moment later cannot swap the address.
3. `BrowserGuard` — the Playwright context. It routes the browser's requests,
   blocks WebSockets and service workers, checks where each navigation went,
   and reads the address Chromium actually connected to for each response (a
   `response` listener). UDP is disabled by a launch switch, and the page's
   WebRTC constructors are removed by an init script.

In the browser, the run ends, and nothing is kept, when the main frame's own
navigation is refused, when a redirect hop or landing of a navigation is not
public, or when Chromium reports it connected to an address that is not public.
A sub-resource or sub-frame request refused BEFORE it is sent never left this
machine: it is aborted on its own and the run goes on. A network-level egress
control in front of the browser, a founder item on the launch checklist
(`docs/launch-checklist.md`), is the next layer.

`EgressRefused` is deliberately NOT an `httpx.HTTPError` or a Playwright error:
the handlers that treat those as "no answer" must not swallow a refusal. Nor is
`EgressUnverified`, which is not a refusal at all: an address that could not be
read in time is not known to be public, and not known to be private.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import typing
from collections.abc import Iterable
from urllib.parse import urlsplit

import httpcore
import httpx
import structlog

logger = structlog.get_logger(__name__)

# The reason code a refused crawl or audit carries. Stored as the audit's error
# code and as the client's classification reason; scores exclude Technical
# Foundation as CRAWL_ADDRESS_NOT_PUBLIC.
ADDRESS_NOT_PUBLIC = "ADDRESS_NOT_PUBLIC"

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class EgressRefused(Exception):  # noqa: N818 - a refusal, named as the brief names it
    """The host resolves to an address that is not public. Carries both."""

    def __init__(self, host: str, address: str) -> None:
        super().__init__(f"{host} resolves to a non-public address")
        self.host = host
        self.address = address


class EgressUnverified(Exception):  # noqa: N818 - not an error a handler recovers from
    """A connected address was still being read when the run ended.

    Not a refusal: nothing says the address is not public, so the run is not
    reported as one. It fails closed all the same, as a plain browser failure
    with nothing kept (`crawl_site`, `audit_site`).
    """

    def __init__(self) -> None:
        super().__init__("a connected address was not read in time")


# Every network below is refused outright. `is_global` already refuses most of
# them; the list is kept explicit so a change in the standard library's tables
# between Python versions cannot quietly open one.
_REFUSED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, including cloud metadata
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved
        "255.255.255.255/32",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",  # deprecated site-local
        "ff00::/8",
        "100::/64",  # discard-only
        "2001:db8::/32",  # documentation
    )
)

# IPv6 forms that carry an IPv4 address inside them. Whatever the embedded
# address is, these are refused: a reader of the string cannot be trusted to
# agree with the network stack about which address it means.
_EMBEDDED_V4_NETWORKS: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "::/96",  # IPv4-compatible
        "2002::/16",  # 6to4
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # NAT64, local use
        "2001::/32",  # Teredo
    )
)


def is_public_address(ip: str | _IPAddress) -> bool:
    """True only for an address on the public internet. Anything doubtful is False."""
    try:
        addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return False
        if any(addr in net for net in _EMBEDDED_V4_NETWORKS):
            return False
    if any(addr in net for net in _REFUSED_NETWORKS):
        return False
    if (
        addr.is_multicast or addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_unspecified
        or getattr(addr, "is_site_local", False)
    ):
        return False
    return addr.is_global


def _clean_host(host: str) -> str:
    name = host.strip().lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name.rstrip(".")


async def _getaddrinfo(host: str, port: int) -> list[typing.Any]:
    """The one place a name is resolved. Tests replace it; nothing else may."""
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)


async def resolve_public(host: str, port: int) -> list[str]:
    """Resolve `host` and return its addresses, every one of them public.

    The RESOLVED addresses are checked, never the string, so a name that points
    at a private address is refused as an IP literal is. If any one address is
    not public the whole host is refused: a connection could be made to it.

    A name that does not resolve is not a refusal. It raises `OSError`, which
    the callers already treat as "no answer".
    """
    name = _clean_host(host)
    if not name:
        raise OSError("empty host")
    try:
        ipaddress.ip_address(name)
        found = [name]
    except ValueError:
        try:
            infos = await _getaddrinfo(name, port)
        except ValueError as exc:
            # The resolver's idna codec raises UnicodeError (a ValueError) for
            # an empty or over-long label. That is a name that cannot resolve.
            raise OSError(f"{name} is not a resolvable name") from exc
        found = []
        for info in infos:
            address = str(info[4][0])
            if address not in found:
                found.append(address)
    if not found:
        raise OSError(f"{name} did not resolve")
    for address in found:
        if not is_public_address(address):
            raise EgressRefused(name, address)
    return found


def url_target(url: str) -> tuple[str, int] | None:
    """(host, port) of an http(s) URL, or None for anything else (data:, about:, ...)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    return parts.hostname, port or (443 if parts.scheme == "https" else 80)


async def check_url(url: str) -> bool:
    """Raise `EgressRefused` if `url`'s host is not public; False if it will not resolve.

    True when the host is public (or the URL is not one a connection is made
    for). A DNS failure is False, not a refusal.
    """
    target = url_target(url)
    if target is None:
        return True
    try:
        await resolve_public(*target)
    except EgressRefused:
        raise
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# the HTTP client: connect to the address that was checked
# ---------------------------------------------------------------------------


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolves, refuses non-public addresses, and connects to the checked address.

    TLS is unaffected: httpcore hands the origin's host name to `start_tls` as
    the server name, so the certificate is still checked against the name and
    only the TCP connection goes to the pinned address.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await resolve_public(host, port)
        except EgressRefused:
            raise
        except OSError as exc:
            # httpx only maps httpcore's own errors, so a bare OSError would
            # escape the callers' `except httpx.HTTPError`.
            raise httpcore.ConnectError(str(exc)) from exc
        last: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout,
                    local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        assert last is not None  # resolve_public never returns an empty list
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise EgressRefused(path, "unix-socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _GuardedTransport(httpx.AsyncHTTPTransport):
    """`httpx.AsyncHTTPTransport` whose connections go through `GuardedNetworkBackend`.

    httpx 0.28 gives its transport no network-backend parameter, so the pool the
    parent built is replaced with one that has it. A test builds a real client
    and proves the swap, so a library upgrade that breaks it fails loudly.

    Proxies and unix sockets are refused: either would send the connection
    somewhere this guard never sees.
    """

    def __init__(
        self,
        *,
        verify: typing.Any = True,
        cert: typing.Any = None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: httpx.Limits | None = None,
        retries: int = 0,
        socket_options: Iterable[typing.Any] | None = None,
        local_address: str | None = None,
    ) -> None:
        limits = limits or httpx.Limits(
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0
        )
        super().__init__(
            verify=verify, cert=cert, trust_env=trust_env, http1=http1, http2=http2,
            limits=limits, retries=retries, socket_options=socket_options,
            local_address=local_address,
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify, cert=cert, trust_env=trust_env),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            retries=retries,
            local_address=local_address,
            socket_options=socket_options,
            network_backend=GuardedNetworkBackend(),
        )


def guarded_transport(**kwargs: typing.Any) -> httpx.AsyncHTTPTransport:
    return _GuardedTransport(**kwargs)


# ---------------------------------------------------------------------------
# the browser
# ---------------------------------------------------------------------------

# UDP disabled by the launch switch; the page's WebRTC constructors removed by the
# init script.
WEBRTC_LAUNCH_ARG = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
# SharedWorker is removed too: Playwright detaches such targets, so their requests
# would be neither routed through `handle` nor reported to `_on_response`.
_NO_WEBRTC = (
    "window.RTCPeerConnection = undefined; window.webkitRTCPeerConnection = undefined;"
    " window.SharedWorker = undefined;"
)

# Distinct hosts whose refused requests are logged by name; the rest are counted.
MAX_LOGGED_REFUSED_HOSTS = 10

# How long a settle waits for response listeners still reading an address. The
# ones between navigations give up and carry on; the FINAL one, before a run's
# facts are kept, fails closed (`BrowserGuard.finish`).
SETTLE_TIMEOUT_S = 2.0
FINAL_SETTLE_TIMEOUT_S = 2.0


async def _block_web_socket(ws: typing.Any) -> None:
    # Never connected to the server: a socket is a connection this guard cannot
    # follow, and nothing the crawler reads needs one.
    await ws.close()


def _server_ip(addr: typing.Any) -> _IPAddress | None:
    """The IP address in a Playwright `server_addr()` value, or None if it has none we can read.

    Playwright gives `{'ipAddress': '93.184.216.34', 'port': 443}`, an IPv6
    address in brackets. A bracket or a zone is stripped; anything else that
    does not parse is None, and never a reason to refuse.
    """
    raw = addr.get("ipAddress") if isinstance(addr, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return ipaddress.ip_address(_clean_host(raw).split("%", 1)[0])
    except ValueError:
        return None


def _safe_host(response: typing.Any) -> str:
    """The host of a response's URL, or "" — whatever the URL looks like. Never raises."""
    try:
        return urlsplit(str(response.url)).hostname or ""
    except Exception:  # noqa: BLE001 - a host for a log line, not worth a failure
        return ""


def _is_main_frame_navigation(request: typing.Any) -> bool:
    """True if `request` is the main frame's own navigation.

    Fails closed: if Playwright raises while reading the request's frame (it does
    for some requests, e.g. one from a service worker), it is treated as the main
    frame's navigation, so the run ends rather than carrying on.
    """
    try:
        in_main_frame = request.frame.parent_frame is None  # read first: it may raise
        return in_main_frame and bool(request.is_navigation_request())
    except Exception:  # noqa: BLE001 - cannot tell what it is: assume the worst
        return True


class BrowserGuard:
    """Egress control for one Playwright context.

    Holds a verdict per (host, port), so a page with hundreds of subresources
    resolves each host once, and `refused`, the first refusal that ends the run.
    Two things set `refused`:

    - a request refused BEFORE it is sent, when it is the main frame's own
      navigation (`handle`): the site, or a page the crawl goes to, is not
      public, and nothing from it was read;
    - a connected address found AFTER the fact (`_on_response`), or a redirect
      hop of a navigation (`check_landing`): a request did reach a non-public
      address, so the run's facts cannot be trusted.

    A sub-resource or sub-frame request refused before it is sent is aborted on
    its own and does not set `refused`: it never left this machine. The crawl
    checks `refused` after every navigation and evaluate.
    """

    def __init__(self) -> None:
        self.refused: EgressRefused | None = None
        # A refusal is cached as (host, address) and raised fresh each time, so a
        # host hit by hundreds of requests does not grow one traceback.
        self._verdicts: dict[tuple[str, int], tuple[str, str] | OSError | None] = {}
        self._pending: set[asyncio.Task[typing.Any]] = set()
        self._unparsed_logged = False
        # Hosts whose refused (not-sent) requests have been logged: once per host.
        self._refused_hosts_logged: set[str] = set()
        self._refused_not_logged = 0
        # Set when a FINAL settle gave up on a listener, before it raises, so a
        # handler that sees only a later error can still tell what was on its way.
        self.unverified = False

    async def _on_response(self, response: typing.Any) -> None:
        """The address Chromium actually connected to, for one response.

        Only an address that parses is judged. No address, or a shape we do not
        know, is ignored: refusing on what cannot be read would fail every real
        crawl the day the format moved. A shape we do not know is logged, once,
        so that day is noticed.
        """
        task = asyncio.current_task()
        if task is not None:
            self._pending.add(task)
        try:
            try:
                addr = await response.server_addr()
            except Exception:  # noqa: BLE001 - no address to read is not a refusal
                return
            if not addr:
                return
            ip = _server_ip(addr)
            if ip is None:
                if not self._unparsed_logged:
                    self._unparsed_logged = True
                    logger.warning("egress.server_addr_unparsed")
                return
            if not is_public_address(ip):
                # Recorded with nothing between the finding and the record that
                # could raise (`_safe_host` never does).
                self.refused = self.refused or EgressRefused(_safe_host(response), str(ip))
        finally:
            if task is not None:
                self._pending.discard(task)

    async def _on_request(self, request: typing.Any) -> None:
        """A redirect hop of any request, judged once it has been sent.

        Chromium follows a redirect without routing the new request through
        `handle`, and a hop into a private address that never answers with an
        HTTP response (refused, reset, not HTTP) never reaches `_on_response`.
        The hop was sent, so a refusal ends the run, as in `check_landing`. Kept
        in `_pending`, so `settle` and `finish` wait for it like a response.
        """
        task = asyncio.current_task()
        if task is not None:
            self._pending.add(task)
        try:
            try:
                is_hop = request.redirected_from is not None
                url = request.url
            except Exception:  # noqa: BLE001 - nothing readable to judge
                return
            if is_hop:
                try:
                    await self._check_hop(url)  # records the refusal before raising
                except EgressRefused:
                    return
        finally:
            if task is not None:
                self._pending.discard(task)

    async def settle(self, *, final: bool = False) -> None:
        """Wait for response listeners still reading an address.

        Bounded. Between navigations a listener still running after
        `SETTLE_TIMEOUT_S` is left to carry on. The FINAL settle fails closed:
        a listener still running after `FINAL_SETTLE_TIMEOUT_S` is cancelled and
        `EgressUnverified` is raised, because the run's facts are about to be
        kept and one of its connections was never checked.
        """
        await asyncio.sleep(0)  # let a listener that was just scheduled start
        pending = list(self._pending)
        if not pending:
            return
        _done, still = await asyncio.wait(
            pending, timeout=FINAL_SETTLE_TIMEOUT_S if final else SETTLE_TIMEOUT_S
        )
        if final and still:
            self.unverified = True
            for task in still:
                task.cancel()
            await asyncio.wait(still, timeout=1.0)  # let the cancellations land
            raise EgressUnverified

    async def finish(self) -> None:
        """The last check before a run's facts are kept.

        A refusal seen by now ends the run as a refusal, even if a listener is
        still out: what is known outranks what is not. Otherwise a listener that
        never answered ends it as `EgressUnverified`.
        """
        try:
            try:
                await self.settle(final=True)
            except EgressUnverified:
                self.raise_if_refused()
                raise
            self.raise_if_refused()
        finally:
            if self._refused_not_logged:
                # No hosts in it: the first ones were logged by name already.
                logger.info("egress.request_refused_more", requests=self._refused_not_logged)
                self._refused_not_logged = 0

    async def _verdict(self, host: str, port: int) -> None:
        key = (_clean_host(host), port)
        if key not in self._verdicts:
            try:
                await resolve_public(*key)
                self._verdicts[key] = None
            except EgressRefused as exc:
                self._verdicts[key] = (exc.host, exc.address)
            except OSError as exc:
                self._verdicts[key] = exc
        outcome = self._verdicts[key]
        if isinstance(outcome, tuple):
            raise EgressRefused(*outcome)
        if outcome is not None:
            raise outcome

    async def handle(self, route: typing.Any) -> None:
        """Playwright route handler: refuse non-public hosts, let the rest through.

        A request refused here is never sent, so nothing from a non-public
        network can have been read. What happens next depends on what it was:

        - the MAIN FRAME's navigation: recorded in `refused` and aborted, so
          the run ends as a refusal (the site itself is not public);
        - anything else (images, scripts, styles, fetch/XHR, fonts, a sub-frame's
          navigation): aborted on its own, not recorded, and logged once per host
          with the host only. The page and the crawl go on.

        `fallback()`, not `continue_()`: the most recently registered route
        runs first, and fallback hands the request to the next one (the crawl's
        image blocking) or, with none left, to the network.
        """
        target = url_target(route.request.url)
        if target is None:
            await route.fallback()
            return
        try:
            await self._verdict(*target)
        except EgressRefused as exc:
            if _is_main_frame_navigation(route.request):
                self.refused = self.refused or exc
                await route.abort("blockedbyclient")
            else:
                await route.abort("blockedbyclient")  # aborted first, then logged
                self._log_refused_request(exc.host)
            return
        except OSError:
            await route.abort("namenotresolved")
            return
        await route.fallback()

    def _log_refused_request(self, host: str) -> None:
        """Once per host per guard, the host only: never the URL, never a query."""
        if host in self._refused_hosts_logged:
            return
        if len(self._refused_hosts_logged) >= MAX_LOGGED_REFUSED_HOSTS:
            self._refused_not_logged += 1  # counted per request, logged once at `finish`
            return
        self._refused_hosts_logged.add(host)
        logger.info("egress.request_refused", host=host)

    async def install(self, context: typing.Any) -> None:
        """Register the guard on `context`. Call after any other route, so it runs first."""
        await context.route("**/*", self.handle)
        context.on("response", self._on_response)
        context.on("request", self._on_request)
        await context.add_init_script(_NO_WEBRTC)
        await context.route_web_socket(re.compile(".*"), _block_web_socket)

    def raise_if_refused(self) -> None:
        if self.refused is not None:
            raise self.refused

    def record_outcome(self, exc: EgressRefused | EgressUnverified) -> None:
        """Keep an egress outcome the run raised that the guard did not record itself.

        The robots gate raises `EgressRefused` on its own (`RobotsGate._egress_ok`), and
        the guard never sees it. A run calls this from an `except` clause on the way out
        of its body, BEFORE its closes begin, so that a time cap that falls during them
        finds the outcome in `refused` / `unverified` and does not lose it with the
        exception that carried it. (The exception chain is not a way to find it: a
        cancellation that arrives after a close has awaited is no longer linked to the
        error it interrupted, and the browser's close always awaits.)
        """
        if isinstance(exc, EgressRefused):
            self.refused = self.refused or exc
        else:
            self.unverified = True

    async def check_landing(self, response: typing.Any, page_url: str) -> None:
        """After a navigation: every hop that led here, and where it ended.

        These hops were followed by Chromium without going through `handle`, so
        they were already sent. Any hop or landing that is not public ends the
        run, and nothing is kept. (A sub-resource refused before it was sent does
        not come through here: `handle` aborted it on its own.)
        """
        await self.settle()
        self.raise_if_refused()
        request = getattr(response, "request", None)
        hops = 0
        while request is not None and hops < 50:
            await self._check_hop(request.url)
            request = request.redirected_from
            hops += 1
        await self._check_hop(page_url)
        self.raise_if_refused()

    async def _check_hop(self, url: str) -> None:
        target = url_target(url)
        if target is None:
            return
        try:
            await self._verdict(*target)
        except EgressRefused as exc:
            self.refused = self.refused or exc
            raise
        except OSError:
            return  # no answer is not a refusal

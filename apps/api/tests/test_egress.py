"""The crawler's egress guard — 2026-10-01.

The crawler fetches whatever address an agency types in and follows whatever it
redirects to. `services/egress.py` refuses any address that is not public, at
three layers; this file holds the address rules, the resolver, the connection
that is pinned to the address it checked, and the proof that the real client
`RobotsGate` builds goes through it. The crawls' own outcomes (a redirect into
a private address, a browser subresource) are in `test_robots_obeyed.py`, next
to the browser fake they need.

No real network: the resolver is replaced, and the connection underneath is a
stand-in that records what it was asked to connect to.
"""

from __future__ import annotations

import socket
from typing import Any

import httpcore
import httpx
import pytest

from avp_api.services import crawl, egress
from avp_api.services.egress import EgressRefused, is_public_address, resolve_public

REFUSED = [
    "10.0.0.1", "172.16.0.1", "192.168.1.1", "127.0.0.1", "::1",
    "169.254.169.254", "fe80::1", "100.64.0.1", "100.127.255.254",
    "224.0.0.1", "ff02::1", "240.0.0.1", "255.255.255.255", "0.0.0.0", "::",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:8.8.8.8",
    "fc00::1", "2002:a00:1::1", "64:ff9b::a00:1", "2001:db8::1", "192.0.2.1",
    # the rest of the families the brief names, one each
    "::a00:1", "64:ff9b:1::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    "fec0::1", "100::1", "198.18.0.1", "192.88.99.1",
]
ALLOWED = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"]


class TestAddressRules:
    @pytest.mark.parametrize("address", REFUSED)
    def test_a_non_public_address_is_refused(self, address: str) -> None:
        assert is_public_address(address) is False

    @pytest.mark.parametrize("address", ALLOWED)
    def test_a_public_address_is_allowed(self, address: str) -> None:
        assert is_public_address(address) is True

    def test_an_address_object_is_judged_like_its_string(self) -> None:
        import ipaddress

        assert is_public_address(ipaddress.ip_address("10.0.0.1")) is False
        assert is_public_address(ipaddress.ip_address("8.8.8.8")) is True

    def test_text_that_is_not_an_address_is_refused(self) -> None:
        assert is_public_address("not-an-ip") is False

    def test_the_explicit_list_alone_refuses_the_named_ranges(self) -> None:
        # Belt and braces: not left to the standard library's `is_global`.
        import ipaddress

        for address in ("100.64.0.1", "192.0.2.1", "198.51.100.1", "203.0.113.1", "fec0::1"):
            ip = ipaddress.ip_address(address)
            assert any(ip in net for net in egress._REFUSED_NETWORKS), address  # noqa: SLF001


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A resolver for these tests: `dns[host] = [addresses]` or an exception."""
    table: dict[str, Any] = {}

    async def fake(host: str, port: int) -> list[tuple]:
        table.setdefault("_asked", []).append((host, port))
        found = table[host]
        if isinstance(found, Exception):
            raise found
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in found
        ]

    monkeypatch.setattr(egress, "_getaddrinfo", fake)
    return table


class TestResolvePublic:
    async def test_a_name_that_resolves_to_a_private_address_is_refused(
        self, dns: dict[str, Any]
    ) -> None:
        dns["internal.test"] = ["10.0.0.5"]
        with pytest.raises(EgressRefused) as raised:
            await resolve_public("internal.test", 443)
        assert (raised.value.host, raised.value.address) == ("internal.test", "10.0.0.5")

    async def test_one_private_address_among_public_ones_refuses_the_host(
        self, dns: dict[str, Any]
    ) -> None:
        dns["mixed.test"] = ["93.184.216.34", "10.0.0.5"]
        with pytest.raises(EgressRefused):
            await resolve_public("mixed.test", 80)

    async def test_public_addresses_come_back_in_resolver_order_without_repeats(
        self, dns: dict[str, Any]
    ) -> None:
        dns["ok.test"] = ["93.184.216.34", "8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"]
        assert await resolve_public("ok.test", 443) == [
            "93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"
        ]

    async def test_an_ip_literal_is_checked_without_a_lookup(self, dns: dict[str, Any]) -> None:
        with pytest.raises(EgressRefused):
            await resolve_public("169.254.169.254", 80)
        with pytest.raises(EgressRefused):
            await resolve_public("[::1]", 80)
        assert await resolve_public("8.8.8.8", 80) == ["8.8.8.8"]
        assert "_asked" not in dns

    async def test_the_host_is_cleaned_before_it_is_resolved(self, dns: dict[str, Any]) -> None:
        dns["example.test"] = ["93.184.216.34"]
        await resolve_public("Example.TEST.", 443)
        assert dns["_asked"] == [("example.test", 443)]

    async def test_a_name_that_will_not_resolve_is_not_a_refusal(
        self, dns: dict[str, Any]
    ) -> None:
        dns["gone.test"] = socket.gaierror(socket.EAI_NONAME, "not found")
        with pytest.raises(OSError) as raised:
            await resolve_public("gone.test", 443)
        assert not isinstance(raised.value, EgressRefused)

    @pytest.mark.parametrize("name", ["a" * 64 + ".test", "a..b", ".leading.test"])
    async def test_a_name_the_resolver_cannot_encode_is_no_answer_not_a_crash(
        self, name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The REAL resolver: its idna codec raises UnicodeError for these, which
        # is a ValueError and not an OSError.
        async def real(host: str, port: int) -> list[Any]:
            import asyncio

            return await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )

        monkeypatch.setattr(egress, "_getaddrinfo", real)
        with pytest.raises(OSError) as raised:
            await resolve_public(name, 443)
        assert not isinstance(raised.value, EgressRefused)
        assert await egress.check_url(f"https://{name}/") is False

    async def test_an_empty_answer_is_not_a_refusal_either(self, dns: dict[str, Any]) -> None:
        dns["empty.test"] = []
        with pytest.raises(OSError):
            await resolve_public("empty.test", 443)


class TestARefusalIsNotAnHttpError:
    def test_it_is_not_swallowed_by_the_handlers_that_mean_no_answer(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        assert not issubclass(EgressRefused, httpx.HTTPError)
        assert not issubclass(EgressRefused, PlaywrightError)
        assert not issubclass(EgressRefused, OSError)


class _Stream(httpcore.AsyncNetworkStream):
    """What a connection returns: records the TLS server name, then refuses to talk."""

    def __init__(self, log: dict[str, Any]) -> None:
        self.log = log

    async def start_tls(self, ssl_context: Any, server_hostname: Any = None, timeout: Any = None):  # noqa: ANN201, ANN401, ARG002
        self.log["server_hostname"] = server_hostname
        raise httpcore.ConnectError("stand-in: no TLS here")

    async def aclose(self) -> None:
        return None


class _Inner(httpcore.AsyncNetworkBackend):
    def __init__(self, fail_first: int = 0) -> None:
        self.connected: list[str] = []
        self.fail_first = fail_first
        self.log: dict[str, Any] = {}

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ANN001, ANN201
        self.connected.append(host)
        if len(self.connected) <= self.fail_first:
            raise httpcore.ConnectError("stand-in: refused")
        return _Stream(self.log)

    async def sleep(self, seconds: float) -> None:
        return None


class TestGuardedNetworkBackend:
    async def test_a_refused_host_never_reaches_the_inner_backend(
        self, dns: dict[str, Any]
    ) -> None:
        dns["internal.test"] = ["10.0.0.5"]
        inner = _Inner()
        with pytest.raises(EgressRefused):
            await egress.GuardedNetworkBackend(inner).connect_tcp("internal.test", 80)
        assert inner.connected == []

    async def test_the_connection_goes_to_the_address_that_was_checked(
        self, dns: dict[str, Any]
    ) -> None:
        dns["ok.test"] = ["93.184.216.34"]
        inner = _Inner()
        await egress.GuardedNetworkBackend(inner).connect_tcp("ok.test", 443, timeout=3)
        # The name is not handed on: a second lookup could answer differently.
        assert inner.connected == ["93.184.216.34"]

    async def test_a_checked_address_that_will_not_connect_falls_to_the_next(
        self, dns: dict[str, Any]
    ) -> None:
        dns["two.test"] = ["2606:4700:4700::1111", "93.184.216.34"]
        inner = _Inner(fail_first=1)
        await egress.GuardedNetworkBackend(inner).connect_tcp("two.test", 443)
        assert inner.connected == ["2606:4700:4700::1111", "93.184.216.34"]

    async def test_a_name_that_will_not_resolve_is_a_connect_error_httpx_understands(
        self, dns: dict[str, Any]
    ) -> None:
        dns["gone.test"] = socket.gaierror(socket.EAI_NONAME, "not found")
        with pytest.raises(httpcore.ConnectError):
            await egress.GuardedNetworkBackend(_Inner()).connect_tcp("gone.test", 443)

    async def test_a_unix_socket_is_refused(self) -> None:
        inner = _Inner()
        with pytest.raises(EgressRefused):
            await egress.GuardedNetworkBackend(inner).connect_unix_socket("/var/run/x.sock")
        assert inner.connected == []


@pytest.mark.stubbed_connect
class TestTheClientTheGateBuildsIsReallyGuarded:
    """Pins the swap into httpx's transport.

    httpx 0.28 gives its transport no network-backend parameter, so
    `egress.guarded_transport` replaces the connection pool. If a library
    upgrade breaks that, these fail — rather than the client quietly
    connecting to anything.
    """

    @pytest.fixture
    def inner(self, monkeypatch: pytest.MonkeyPatch) -> _Inner:
        inner = _Inner()
        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", inner.connect_tcp)
        return inner

    async def test_a_request_to_a_private_address_raises_the_refusal_unchanged(
        self, dns: dict[str, Any], inner: _Inner
    ) -> None:
        dns["internal.test"] = ["10.0.0.5"]
        gate = crawl.RobotsGate(user_agent="ua")
        async with gate._client() as client:  # noqa: SLF001
            with pytest.raises(EgressRefused):
                await client.get("http://internal.test/")
        assert inner.connected == []

    async def test_a_refusal_at_connection_time_propagates_through_the_gates_exchange(
        self, dns: dict[str, Any], inner: _Inner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The gate's own address check is satisfied (it is told the address is fine) and the
        # connection-time check, which resolves again, is not: a name that answers differently
        # the second time. With the REAL client the refusal must come out of `_exchange` as
        # itself and end the run, never be read as a request that got no answer.
        dns["rebind.test"] = ["10.0.0.5"]
        gate = crawl.RobotsGate(user_agent="ua")

        async def passes(url: str) -> bool:
            return True

        monkeypatch.setattr(gate, "_egress_ok", passes)

        async with gate._client() as client:  # noqa: SLF001
            with pytest.raises(EgressRefused):
                await gate._exchange(client, "GET", "https://rebind.test/robots.txt", 1_000)  # noqa: SLF001
        # ...and so out of the gate's own fetch, which a run ends as `ADDRESS_NOT_PUBLIC` on.
        with pytest.raises(EgressRefused):
            await gate.file("https://rebind.test")
        assert inner.connected == []

    async def test_a_literal_private_address_in_the_url_is_refused_too(
        self, dns: dict[str, Any], inner: _Inner
    ) -> None:
        gate = crawl.RobotsGate(user_agent="ua")
        async with gate._client() as client:  # noqa: SLF001
            with pytest.raises(EgressRefused):
                await client.get("http://169.254.169.254/latest/meta-data/")
        assert inner.connected == []

    async def test_a_name_that_will_not_resolve_is_still_an_httpx_error(
        self, dns: dict[str, Any], inner: _Inner
    ) -> None:
        dns["gone.test"] = socket.gaierror(socket.EAI_NONAME, "not found")
        gate = crawl.RobotsGate(user_agent="ua")
        async with gate._client() as client:  # noqa: SLF001
            with pytest.raises(httpx.HTTPError):
                await client.get("http://gone.test/")

    async def test_tls_is_still_verified_against_the_name_not_the_address(
        self, dns: dict[str, Any], inner: _Inner
    ) -> None:
        dns["public.test"] = ["93.184.216.34"]
        gate = crawl.RobotsGate(user_agent="ua")
        async with gate._client() as client:  # noqa: SLF001
            with pytest.raises(httpx.HTTPError):  # the stand-in stream stops at TLS
                await client.get("https://public.test/")
        assert inner.connected == ["93.184.216.34"]
        assert inner.log["server_hostname"] == "public.test"

    def test_a_proxy_would_bypass_the_guard_so_it_is_not_accepted(self) -> None:
        with pytest.raises(TypeError):
            egress.guarded_transport(proxy="http://proxy.test:8080")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# the browser guard's own listener and settling
# ---------------------------------------------------------------------------


def _response(url: Any, addr: Any, *, never: bool = False) -> Any:
    """A Playwright response, as far as the guard reads one."""
    from types import SimpleNamespace

    async def server_addr() -> Any:
        if never:
            import asyncio

            await asyncio.Event().wait()
        return addr

    return SimpleNamespace(url=url, server_addr=server_addr)


class TestTheConnectedAddressListener:
    async def test_a_non_public_address_is_recorded_whatever_the_url_looks_like(self) -> None:
        # Recorded first: working out the host must not be able to lose it.
        for url in ("http://[::1/x", None, 42):
            guard = egress.BrowserGuard()
            await guard._on_response(  # noqa: SLF001
                _response(url, {"ipAddress": "10.0.0.5", "port": 80})
            )
            assert guard.refused is not None, url
            assert (guard.refused.host, guard.refused.address) == ("", "10.0.0.5")

    async def test_the_host_is_the_response_s_when_it_can_be_read(self) -> None:
        guard = egress.BrowserGuard()
        await guard._on_response(  # noqa: SLF001
            _response("https://cdn.test/a.js", {"ipAddress": "[::1]", "port": 443})
        )
        assert guard.refused is not None
        assert (guard.refused.host, guard.refused.address) == ("cdn.test", "::1")

    async def test_an_address_that_will_not_parse_is_logged_once_without_its_value(self) -> None:
        from structlog.testing import capture_logs

        guard = egress.BrowserGuard()
        with capture_logs() as logs:
            for addr in ({"ipAddress": "garbage-value-xyz"}, {"ipAddress": 7}, "10.0.0.5"):
                await guard._on_response(_response("https://x.test/", addr))  # noqa: SLF001
        events = [e for e in logs if e["event"] == "egress.server_addr_unparsed"]
        # Once per guard: a format change is noticed without a line per response.
        assert len(events) == 1
        assert set(events[0]) == {"event", "log_level"}
        assert "garbage-value-xyz" not in str(logs)
        assert guard.refused is None  # never refuse on what cannot be read

    async def test_no_address_at_all_and_a_public_one_are_silent(self) -> None:
        from structlog.testing import capture_logs

        guard = egress.BrowserGuard()
        with capture_logs() as logs:
            for addr in (None, {}, {"ipAddress": "93.184.216.34", "port": 443},
                         {"ipAddress": "[2606:4700:4700::1111]", "port": 443}):
                await guard._on_response(_response("https://x.test/", addr))  # noqa: SLF001
        assert logs == [] and guard.refused is None

    async def test_a_response_whose_address_cannot_be_asked_for_is_ignored(self) -> None:
        from types import SimpleNamespace

        async def broken() -> Any:
            raise RuntimeError("response is gone")

        guard = egress.BrowserGuard()
        await guard._on_response(SimpleNamespace(url="https://x.test/", server_addr=broken))  # noqa: SLF001
        assert guard.refused is None


class _FakeRequest:
    def __init__(
        self, url: str, *, navigation: bool, frame: Any, frame_raises: bool,
        navigation_raises: bool = False,
    ) -> None:
        self.url = url
        self._navigation_raises = navigation_raises
        self._navigation = navigation
        self._frame = frame
        self._frame_raises = frame_raises

    def is_navigation_request(self) -> bool:
        if self._navigation_raises:
            raise RuntimeError("cannot tell")
        return self._navigation

    @property
    def frame(self) -> Any:
        if self._frame_raises:
            raise RuntimeError("Service Worker requests have no frame")
        return self._frame


class _FakeRoute:
    """A Playwright route, as far as `BrowserGuard.handle` reads one."""

    def __init__(
        self, url: str, *, navigation: bool, parent: Any, frame_raises: bool = False,
        navigation_raises: bool = False,
    ):
        from types import SimpleNamespace

        self.actions: list[tuple[str, Any]] = []
        self.request = _FakeRequest(
            url, navigation=navigation, frame=SimpleNamespace(parent_frame=parent),
            frame_raises=frame_raises, navigation_raises=navigation_raises,
        )

    async def abort(self, reason: str | None = None) -> None:
        self.actions.append(("abort", reason))

    async def fallback(self) -> None:
        self.actions.append(("fallback", None))


class TestARefusedRequestBeforeItIsSent:
    """Only the main frame's own navigation ends the run; anything else is aborted alone."""

    @pytest.fixture(autouse=True)
    def private(self, dns: dict[str, Any]) -> None:
        dns["internal.test"] = ["10.0.0.5"]
        dns["ok.test"] = ["93.184.216.34"]

    async def test_a_main_frame_navigation_is_recorded_and_aborted(self) -> None:
        guard = egress.BrowserGuard()
        route = _FakeRoute("https://internal.test/", navigation=True, parent=None)
        await guard.handle(route)
        assert route.actions == [("abort", "blockedbyclient")]
        assert guard.refused is not None and guard.refused.host == "internal.test"

    async def test_a_sub_resource_is_aborted_and_not_recorded(self) -> None:
        from structlog.testing import capture_logs

        guard = egress.BrowserGuard()
        main = object()
        with capture_logs() as logs:
            for path in ("a.js", "b.js"):
                route = _FakeRoute(
                    f"https://internal.test/{path}?q=1", navigation=False, parent=main
                )
                await guard.handle(route)
                assert route.actions == [("abort", "blockedbyclient")]
        assert guard.refused is None
        guard.raise_if_refused()
        # Once per host, host only.
        assert [(e["event"], e["host"], e["log_level"]) for e in logs] == [
            ("egress.request_refused", "internal.test", "info")
        ]
        assert set(logs[0]) == {"event", "log_level", "host"}

    async def test_a_sub_resource_in_the_main_frame_is_not_a_navigation(self) -> None:
        guard = egress.BrowserGuard()
        route = _FakeRoute("https://internal.test/a.png", navigation=False, parent=None)
        await guard.handle(route)
        assert route.actions == [("abort", "blockedbyclient")]
        assert guard.refused is None

    async def test_a_sub_frame_navigation_is_aborted_and_not_recorded(self) -> None:
        guard = egress.BrowserGuard()
        route = _FakeRoute("https://internal.test/f", navigation=True, parent=object())
        await guard.handle(route)
        assert route.actions == [("abort", "blockedbyclient")]
        assert guard.refused is None

    async def test_a_request_whose_frame_cannot_be_read_fails_closed(self) -> None:
        # Playwright raises for some requests (a service worker's): treated as the
        # main frame's navigation, so the run ends rather than carrying on.
        guard = egress.BrowserGuard()
        route = _FakeRoute(
            "https://internal.test/sw", navigation=False, parent=None, frame_raises=True
        )
        await guard.handle(route)
        assert route.actions == [("abort", "blockedbyclient")]
        assert guard.refused is not None

    async def test_a_navigation_check_that_raises_fails_closed(self) -> None:
        guard = egress.BrowserGuard()
        route = _FakeRoute(
            "https://internal.test/x", navigation=False, parent=None, navigation_raises=True
        )
        await guard.handle(route)
        assert route.actions == [("abort", "blockedbyclient")]
        assert guard.refused is not None

    async def test_a_host_refused_as_a_sub_resource_still_ends_the_run_on_a_hop(self) -> None:
        from types import SimpleNamespace

        guard = egress.BrowserGuard()
        await guard.handle(
            _FakeRoute("https://internal.test/a.js", navigation=False, parent=object())
        )
        assert guard.refused is None
        response = SimpleNamespace(
            request=SimpleNamespace(url="https://ok.test/", redirected_from=None)
        )
        with pytest.raises(EgressRefused):
            await guard.check_landing(response, "https://internal.test/landed")
        assert guard.refused is not None

    async def test_a_refusal_is_raised_fresh_each_time(self) -> None:
        guard = egress.BrowserGuard()
        raised = []
        for _ in range(3):
            with pytest.raises(EgressRefused) as caught:
                await guard._verdict("internal.test", 443)  # noqa: SLF001
            raised.append(caught.value)
        assert len({id(e) for e in raised}) == 3
        assert all((e.host, e.address) == ("internal.test", "10.0.0.5") for e in raised)
        # Each traceback is its own and short: nothing accumulates across raises.
        assert all(len(list(_frames(e.__traceback__))) < 4 for e in raised)

    async def test_only_the_first_hosts_are_logged_by_name_and_the_rest_counted(
        self, dns: dict[str, Any]
    ) -> None:
        from structlog.testing import capture_logs

        guard = egress.BrowserGuard()
        with capture_logs() as logs:
            for i in range(13):
                dns[f"h{i}.test"] = ["10.0.0.5"]
                for _ in range(2):  # repeats of a named host are not logged again
                    await guard.handle(
                        _FakeRoute(f"https://h{i}.test/a", navigation=False, parent=object())
                    )
            await guard.finish()
            await guard.finish()  # reported once
        named = [e for e in logs if e["event"] == "egress.request_refused"]
        more = [e for e in logs if e["event"] == "egress.request_refused_more"]
        assert [e["host"] for e in named] == [f"h{i}.test" for i in range(10)]
        assert len(more) == 1 and set(more[0]) == {"event", "log_level", "requests"}
        assert more[0]["requests"] == 6  # 3 hosts x 2 requests, never named
        assert "h10.test" not in str(more)

    async def test_the_installed_script_disables_the_shared_worker(self) -> None:
        scripts: list[str] = []

        class Context:
            async def route(self, *a: Any, **k: Any) -> None: ...
            async def route_web_socket(self, *a: Any, **k: Any) -> None: ...
            def on(self, *a: Any, **k: Any) -> None: ...

            async def add_init_script(self, script: str) -> None:
                scripts.append(script)

        await egress.BrowserGuard().install(Context())
        assert any("window.SharedWorker = undefined" in s for s in scripts)
        assert any("window.RTCPeerConnection = undefined" in s for s in scripts)

    async def test_a_public_request_goes_on_whatever_it_is(self) -> None:
        guard = egress.BrowserGuard()
        route = _FakeRoute("https://ok.test/a.js", navigation=False, parent=object())
        await guard.handle(route)
        assert route.actions == [("fallback", None)]
        assert guard.refused is None


def _frames(tb: Any) -> Any:
    while tb is not None:
        yield tb
        tb = tb.tb_next


class TestARedirectHopThatNeverAnswers:
    """`request` listener: a hop sent into a private address is judged without a response."""

    @pytest.fixture(autouse=True)
    def names(self, dns: dict[str, Any]) -> None:
        dns["internal.test"] = ["10.0.0.5"]
        dns["ok.test"] = ["93.184.216.34"]

    @staticmethod
    def _request(url: str, hop: bool) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(url=url, redirected_from=object() if hop else None)

    async def test_a_hop_into_a_private_address_is_recorded(self) -> None:
        guard = egress.BrowserGuard()
        await guard._on_request(self._request("https://internal.test/x", True))  # noqa: SLF001
        assert guard.refused is not None and guard.refused.host == "internal.test"

    async def test_a_request_that_is_not_a_hop_and_a_public_hop_are_left_alone(self) -> None:
        guard = egress.BrowserGuard()
        await guard._on_request(self._request("https://internal.test/x", False))  # noqa: SLF001
        await guard._on_request(self._request("https://ok.test/x", True))  # noqa: SLF001
        assert guard.refused is None

    async def test_finish_waits_for_a_hop_still_being_judged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        async def slow(host: str, port: int) -> list[tuple]:
            await asyncio.sleep(0.05)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

        monkeypatch.setattr(egress, "_getaddrinfo", slow)
        guard = egress.BrowserGuard()
        task = asyncio.ensure_future(
            guard._on_request(self._request("https://slow.test/x", True))  # noqa: SLF001
        )
        await asyncio.sleep(0)
        with pytest.raises(EgressRefused):
            await guard.finish()
        await task

    async def test_install_listens_for_requests(self) -> None:
        events: list[str] = []

        class Context:
            async def route(self, *a: Any, **k: Any) -> None: ...
            async def route_web_socket(self, *a: Any, **k: Any) -> None: ...
            async def add_init_script(self, *a: Any, **k: Any) -> None: ...

            def on(self, event: str, handler: Any) -> None:
                events.append(event)

        await egress.BrowserGuard().install(Context())
        assert "request" in events and "response" in events


class TestARefusedHopIsRecorded:
    async def test_it_is_recorded_before_it_is_raised(self, dns: dict[str, Any]) -> None:
        from types import SimpleNamespace

        dns["internal.test"] = ["10.0.0.5"]
        dns["ok.test"] = ["93.184.216.34"]
        guard = egress.BrowserGuard()
        hop = SimpleNamespace(url="https://internal.test/hop", redirected_from=None)
        response = SimpleNamespace(
            request=SimpleNamespace(url="https://ok.test/", redirected_from=hop)
        )
        with pytest.raises(EgressRefused) as raised:
            await guard.check_landing(response, "https://ok.test/")
        assert guard.refused is raised.value


class TestSettling:
    @pytest.fixture(autouse=True)
    def impatient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(egress, "SETTLE_TIMEOUT_S", 0.01)
        monkeypatch.setattr(egress, "FINAL_SETTLE_TIMEOUT_S", 0.05)

    async def _pending_listener(self, guard: egress.BrowserGuard) -> Any:
        import asyncio

        task = asyncio.ensure_future(
            guard._on_response(_response("https://x.test/", None, never=True))  # noqa: SLF001
        )
        await asyncio.sleep(0)  # let it start
        return task

    async def test_an_intermediate_settle_gives_up_quietly_and_leaves_the_listener_running(
        self,
    ) -> None:
        guard = egress.BrowserGuard()
        task = await self._pending_listener(guard)
        await guard.settle()  # bounded, and no error: as before
        assert not task.done()
        task.cancel()

    async def test_the_final_settle_fails_closed_and_cancels_what_is_left(self) -> None:
        import asyncio

        guard = egress.BrowserGuard()
        task = await self._pending_listener(guard)
        with pytest.raises(egress.EgressUnverified):
            await guard.settle(final=True)
        await asyncio.sleep(0)
        assert task.cancelled()
        # Nothing is left for a later settle to wait for.
        await guard.settle(final=True)

    async def test_the_final_settle_records_that_it_is_unverified(self) -> None:
        # Recorded on the guard, so a handler that sees only a later error (a
        # browser that failed to close) can still tell what was on its way out.
        guard = egress.BrowserGuard()
        assert guard.unverified is False
        task = await self._pending_listener(guard)
        await guard.settle()  # between navigations: gives up, records nothing
        assert guard.unverified is False
        with pytest.raises(egress.EgressUnverified):
            await guard.settle(final=True)
        assert guard.unverified is True
        assert task.cancelled()

    async def test_nothing_is_recorded_when_every_listener_answered(self) -> None:
        guard = egress.BrowserGuard()
        await guard._on_response(  # noqa: SLF001
            _response("https://x.test/", {"ipAddress": "93.184.216.34", "port": 443})
        )
        await guard.settle(final=True)
        assert guard.unverified is False and guard.refused is None

    async def test_finish_raises_unverified_for_a_listener_that_never_answers(self) -> None:
        guard = egress.BrowserGuard()
        task = await self._pending_listener(guard)
        with pytest.raises(egress.EgressUnverified):
            await guard.finish()
        assert task.cancelled()

    async def test_a_known_refusal_outranks_an_unverified_listener(self) -> None:
        guard = egress.BrowserGuard()
        await guard._on_response(  # noqa: SLF001
            _response("https://x.test/", {"ipAddress": "10.0.0.5", "port": 80})
        )
        await self._pending_listener(guard)
        with pytest.raises(EgressRefused):
            await guard.finish()

    async def test_finish_is_quiet_when_every_listener_answered(self) -> None:
        guard = egress.BrowserGuard()
        await guard._on_response(  # noqa: SLF001
            _response("https://x.test/", {"ipAddress": "93.184.216.34", "port": 443})
        )
        await guard.finish()

    def test_unverified_is_neither_a_refusal_nor_an_error_a_handler_swallows(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        assert not issubclass(egress.EgressUnverified, EgressRefused)
        assert not issubclass(egress.EgressUnverified, httpx.HTTPError)
        assert not issubclass(egress.EgressUnverified, PlaywrightError)
        assert not issubclass(egress.EgressUnverified, OSError)

    def test_the_chromium_launch_switch_is_the_one_named(self) -> None:
        assert egress.WEBRTC_LAUNCH_ARG == (
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
        )

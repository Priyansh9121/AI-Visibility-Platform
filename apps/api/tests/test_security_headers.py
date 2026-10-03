"""Security headers on every response the API sends — launch checklist 3.1, 2026-10-03.

`avp_api/security_headers.py` puts six headers on every HTTP response, unless the
response already set that header itself. Before it the API sent none of them: it
set CORS headers only.

Three things are under test, and they are different.

**The values.** Each header, with its exact value, exactly once, on every kind of
response the API sends: a 200, a 404, a 401 problem, the origin check's 403, a CORS
preflight, a 429 and a PDF. The expected values are written out below, not read from
the code, so editing the code cannot quietly edit what is pinned.

**The placement.** The layer is OUTSIDE CORS (`main.py` says why): CORS answers a
preflight without calling what it wraps, so a layer inside it never sees one. The
preflight test is what fails if the layer is moved in. CORS itself must behave as it
did, and its headers are asserted beside ours.

**The layer itself.** A pure ASGI middleware that touches `http.response.start` and
nothing else: a header a response set is kept (whatever its case), a streamed body
still streams, and a `websocket` or `lifespan` scope passes straight through.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Response
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from avp_api.config import Settings
from avp_api.deps import scan_executor
from avp_api.main import create_app
from avp_api.origin_check import OriginCheckMiddleware
from avp_api.security_headers import (
    PERMISSIONS_POLICY,
    SECURITY_HEADERS,
    STRICT_TRANSPORT_SECURITY,
    SecurityHeadersMiddleware,
)

BASE = "/api/v1"
LOGIN = f"{BASE}/auth/login"
FOREIGN = "https://evil.example"
WEB_NEXT_CONFIG = Path(__file__).resolve().parents[3] / "apps" / "web" / "next.config.mjs"

# Written out, as a browser reads them, and not taken from the module.
EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "referrer-policy": "no-referrer",
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "permissions-policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()"
    ),
}


def _assert_all_six(resp: httpx.Response) -> None:
    """Every header, with its exact value, and only once."""
    for name, value in EXPECTED.items():
        assert resp.headers.get_list(name) == [value], f"{name}: {resp.headers.get_list(name)}"


# ---------------------------------------------------------------------------
# the values, on every kind of response
# ---------------------------------------------------------------------------


async def test_a_json_200_carries_every_header(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{BASE}/health")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.json()["status"] == "ok"
    _assert_all_six(resp)


async def test_a_404_problem_carries_every_header(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{BASE}/no-such-route")

    assert resp.status_code == 404
    assert resp.json()["type"] == "/problems/not-found"
    _assert_all_six(resp)


async def test_a_401_problem_carries_every_header(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{BASE}/auth/me")

    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "/problems/authentication-required"
    _assert_all_six(resp)


async def test_the_origin_checks_403_refusal_carries_every_header(
    client: httpx.AsyncClient,
) -> None:
    """The refusal is returned from inside the stack, and leaves through this layer."""
    resp = await client.post(LOGIN, json={}, headers={"Origin": FOREIGN})

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/origin-not-allowed"
    _assert_all_six(resp)


async def test_a_cors_preflight_carries_every_header_and_cors_still_answers_it(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """The reason the layer is outside CORS: the CORS middleware answers a preflight
    itself, without calling what it wraps. A layer inside it would never see one.
    And CORS answers exactly as it did: its own headers are asserted beside ours."""
    allowed = settings.cors_allow_origins[0]
    ask = {
        "Origin": allowed,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }

    resp = await client.options(f"{BASE}/clients", headers=ask)

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == allowed
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "POST" in resp.headers["access-control-allow-methods"]
    assert resp.text == "OK", "the body is CORS's own"
    _assert_all_six(resp)


async def test_a_refused_cors_preflight_carries_every_header_too(
    client: httpx.AsyncClient,
) -> None:
    ask = {
        "Origin": FOREIGN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }

    resp = await client.options(f"{BASE}/clients", headers=ask)

    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers
    _assert_all_six(resp)


async def test_a_429_carries_every_header_and_keeps_its_retry_after(
    client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_limit_per_email", 1)
    body = {"email": "limited@security.example", "password": "not-the-password-1234"}

    first = await client.post(LOGIN, json=body)
    assert first.status_code == 401, first.text

    resp = await client.post(LOGIN, json=body)

    assert resp.status_code == 429, resp.text
    assert resp.json()["type"] == "/problems/rate-limited"
    assert int(resp.headers["retry-after"]) > 0, "the route's own header must survive"
    _assert_all_six(resp)


async def _scan_for_report(client: httpx.AsyncClient) -> str:
    """Sign up, add a client, and open a scan that is never run. Spends nothing."""
    signed_up = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Headers Test Agency",
            "fullName": "Operator",
            "email": "headers@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert signed_up.status_code == 201, signed_up.text

    class _Deferred:
        async def submit(self, job: Any, *, settings: Settings) -> None:
            return None

    client._transport.app.dependency_overrides[scan_executor] = (  # noqa: SLF001
        lambda: _Deferred()
    )
    created = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert created.status_code == 201, created.text
    scan = await client.post(f"{BASE}/clients/{created.json()['id']}/scans", json={})
    assert scan.status_code == 202, scan.text
    return str(scan.json()["id"])


async def test_the_report_pdf_carries_every_header_and_keeps_its_own(
    client: httpx.AsyncClient,
) -> None:
    scan_id = await _scan_for_report(client)

    resp = await client.get(f"{BASE}/scans/{scan_id}/report.pdf")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")
    # The route's own headers are untouched.
    assert resp.headers["content-disposition"].startswith("attachment; filename=")
    assert resp.headers["cache-control"] == "private, no-store"
    _assert_all_six(resp)


def _routes(app: Any) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in sorted((getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}):
            if path.startswith(BASE):
                found.append((method, path))
    return found


async def test_every_route_answers_with_the_headers_whatever_it_answers(
    client: httpx.AsyncClient,
) -> None:
    """A sweep over every registered route, called with no session and no body: a
    401, a 422, a redirect, a page. The status is whatever the route gives; the
    headers are on all of them."""
    app = client._transport.app  # noqa: SLF001
    routes = _routes(app)
    assert len(routes) > 40, "the sweep found too few routes to mean anything"

    statuses: set[int] = set()
    for method, path in routes:
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        resp = await client.request(method, concrete, follow_redirects=False)
        statuses.add(resp.status_code)
        for name, value in EXPECTED.items():
            assert resp.headers.get_list(name) == [value], f"{method} {concrete} -> {name}"
    assert {401, 422} <= statuses, f"the sweep reached only {sorted(statuses)}"


# ---------------------------------------------------------------------------
# a header the response set itself is not overwritten
# ---------------------------------------------------------------------------


async def test_a_header_a_route_set_itself_is_not_overwritten(client: httpx.AsyncClient) -> None:
    app = client._transport.app  # noqa: SLF001

    @app.get(f"{BASE}/_own-headers")
    async def own_headers() -> Response:
        return Response(
            content="ok",
            headers={"Referrer-Policy": "same-origin", "X-Frame-Options": "SAMEORIGIN"},
        )

    resp = await client.get(f"{BASE}/_own-headers")

    assert resp.status_code == 200
    # The route's two stay as it sent them, once each ...
    assert resp.headers.get_list("referrer-policy") == ["same-origin"]
    assert resp.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    # ... and the other four are added.
    for name in (
        "x-content-type-options",
        "content-security-policy",
        "strict-transport-security",
        "permissions-policy",
    ):
        assert resp.headers.get_list(name) == [EXPECTED[name]], name


async def _run(inner: ASGIApp, scope_type: str = "http") -> list[Message]:
    """`inner`, behind the middleware alone, and every message it sent."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": scope_type, "method": "GET", "path": "/", "headers": []}
    await SecurityHeadersMiddleware(inner)(scope, receive, send)
    return sent


def _responding(headers: list[tuple[bytes, bytes]] | None, body: bytes = b"ok") -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        start: Message = {"type": "http.response.start", "status": 200}
        if headers is not None:
            start["headers"] = headers
        await send(start)
        await send({"type": "http.response.body", "body": body})

    return app


def _pairs(message: Message) -> list[tuple[str, str]]:
    return [(n.decode().lower(), v.decode()) for n, v in message["headers"]]


@pytest.mark.parametrize("spelling", [b"x-frame-options", b"X-Frame-Options", b"X-FRAME-OPTIONS"])
async def test_a_header_the_response_set_is_kept_whatever_the_case_of_its_name(
    spelling: bytes,
) -> None:
    sent = await _run(_responding([(spelling, b"SAMEORIGIN")]))

    pairs = _pairs(sent[0])
    assert [v for n, v in pairs if n == "x-frame-options"] == ["SAMEORIGIN"]
    for name, value in EXPECTED.items():
        if name != "x-frame-options":
            assert [v for n, v in pairs if n == name] == [value], name
    assert (spelling, b"SAMEORIGIN") in sent[0]["headers"], "the response's own pair, as sent"


async def test_a_response_that_set_all_six_is_left_exactly_as_it_was() -> None:
    own = [(name.lower().encode(), b"its own value") for name, _ in SECURITY_HEADERS]

    sent = await _run(_responding(list(own)))

    assert sent[0]["headers"] == own


@pytest.mark.parametrize("headers", [None, []], ids=["no-headers-key", "empty-list"])
async def test_a_response_with_no_headers_gets_all_six(
    headers: list[tuple[bytes, bytes]] | None,
) -> None:
    sent = await _run(_responding(headers))

    assert dict(_pairs(sent[0])) == EXPECTED
    assert len(sent[0]["headers"]) == 6


async def test_what_the_response_sent_is_kept_in_order_and_ours_come_after() -> None:
    own = [(b"content-type", b"text/plain"), (b"set-cookie", b"a=1"), (b"set-cookie", b"b=2")]

    sent = await _run(_responding(list(own)))

    assert sent[0]["headers"][:3] == own
    assert dict(_pairs(sent[0])[3:]) == EXPECTED
    assert sent[0]["status"] == 200


async def test_the_body_goes_through_untouched() -> None:
    sent = await _run(_responding([], body=b"the body"))

    assert sent[1] == {"type": "http.response.body", "body": b"the body"}


@pytest.mark.parametrize(
    ("scope_type", "message"),
    [
        ("websocket", {"type": "websocket.accept", "headers": [(b"x-own", b"1")]}),
        ("lifespan", {"type": "lifespan.startup.complete"}),
    ],
)
async def test_a_scope_that_is_not_http_passes_straight_through(
    scope_type: str, message: Message
) -> None:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send(dict(message))

    sent = await _run(inner, scope_type)

    assert sent == [message]


# ---------------------------------------------------------------------------
# a streamed response still streams
# ---------------------------------------------------------------------------


async def test_a_streaming_response_still_streams(client: httpx.AsyncClient) -> None:
    """Through the whole stack. The route's second chunk is not produced until the
    first has reached the server's `send`, so a layer that buffered the response
    until it ended could never get there: the route would wait for a chunk that is
    held back, and the wait here times out instead of passing."""
    app = client._transport.app  # noqa: SLF001
    first_chunk_sent = asyncio.Event()

    @app.get(f"{BASE}/_stream")
    async def stream() -> StreamingResponse:
        async def chunks() -> Any:
            yield b"first"
            await asyncio.wait_for(first_chunk_sent.wait(), timeout=3)
            yield b"second"

        return StreamingResponse(chunks(), media_type="text/plain")

    sent: list[Message] = []
    request_sent = False

    async def receive() -> Message:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # the client stays connected
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body") == b"first":
            first_chunk_sent.set()

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"{BASE}/_stream",
        "raw_path": f"{BASE}/_stream".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)

    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 200
    assert {n.decode(): v.decode() for n, v in start["headers"]}.items() >= EXPECTED.items()
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert [m["body"] for m in bodies if m["body"]] == [b"first", b"second"]
    assert bodies[0]["body"] == b"first" and bodies[0].get("more_body") is True, (
        "the first chunk went out while the response was still open"
    )


# ---------------------------------------------------------------------------
# the wiring, and the values shared with the web app
# ---------------------------------------------------------------------------


def test_the_layer_is_the_outermost_one_and_outside_cors(settings: Settings) -> None:
    """Outside CORS, because a preflight never reaches a layer inside it. The origin
    check stays inside CORS (`test_origin_check.py` holds that)."""
    order = [m.cls for m in create_app(settings).user_middleware]

    assert order[0] is SecurityHeadersMiddleware
    assert order.index(SecurityHeadersMiddleware) < order.index(CORSMiddleware)
    assert order.index(CORSMiddleware) < order.index(OriginCheckMiddleware)


def test_the_values_in_the_module_are_the_ones_pinned_here() -> None:
    assert {name.lower(): value for name, value in SECURITY_HEADERS} == EXPECTED
    assert len(SECURITY_HEADERS) == len(EXPECTED), "a header was added without a test"


@pytest.mark.parametrize(
    ("constant", "header", "api_value"),
    [
        pytest.param(
            "PERMISSIONS_POLICY", "permissions-policy", PERMISSIONS_POLICY, id="permissions-policy"
        ),
        pytest.param(
            "STRICT_TRANSPORT_SECURITY",
            "strict-transport-security",
            STRICT_TRANSPORT_SECURITY,
            id="hsts",
        ),
    ],
)
def test_a_value_the_web_app_also_sends_is_the_same_in_both(
    constant: str, header: str, api_value: str
) -> None:
    """One value, written twice: here and in `apps/web/next.config.mjs`, where it is a
    constant of the same name on one line. This reads the web app's line and holds the
    three equal (the web's, this module's, and the one pinned above), so neither side
    can drift from the other."""
    config = WEB_NEXT_CONFIG.read_text()
    found = re.findall(rf"^const {constant} = '([^']+)';$", config, re.M)

    assert len(found) == 1, f"could not find the web app's {constant} line"
    assert api_value == found[0] == EXPECTED[header]

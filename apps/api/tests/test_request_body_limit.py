"""A request body has a size limit and a deadline — A8b, 2026-10-11 (the 413 and the 408).

`avp_api/body_limit.py`. Before it the API set neither: a route that takes a JSON body had the
whole of it read and parsed on the one worker before the session or any rate limit was looked at,
however large and however slowly it came, on the unauthenticated routes too. Now a body over its
limit is refused `413 /problems/payload-too-large`, one not whole within the deadline `408
/problems/request-timeout`, and the route never runs.

The groups, and the first three go through the live app:

* **Declared** (`Content-Length` over the limit): refused before a byte is read. Exactly the limit
  passes. The handler is replaced by a spy, or the write it would make is looked for, so a refusal
  that still ran the route would fail.
* **Counted** (no `Content-Length`, a chunked body; or one that declares less than it sends):
  refused as soon as the count passes the limit, without reading the rest. This is the part FastAPI
  makes subtle: it reads a JSON body inside a `try` that turns any exception it does not know into
  a `400`, and re-raises only an `HTTPException`. The same 413 must come out of a JSON route and of
  the raw-body Stripe webhook.
* **The other-sites list**, the one route whose schema allows a large body, and the webhook's edge.
* **The deadline** (`REQUEST_BODY_TIMEOUT_SECONDS`): a body that stalls is answered 408 AT the
  deadline, by cutting the wait off and not by looking at the clock between chunks; a slow body
  that is steady passes; the clock starts at the first read and stops when the body is whole.
* **Where it sits.** Inside the security headers and CORS (the 413 carries both), outside the origin
  check. A preflight and a body-less GET pass.
* **The limits.** The default, the two overrides, what a setting changes, and what each setting
  may be: its own range, outside which the API does not start. Where the three disagree it does
  NOT refuse to start: the webhook's limit is raised to the default, and the other-sites one to the
  default or to the largest body its schema allows, with a warning when the app is built
  (`config.body_limit_raised`). A raised schema bound is covered at runtime by that, and CI still
  fails, because the shipped defaults are checked as configured.
* **Derived from the schemas.** For every route that takes a body, the largest body its request
  model allows is BUILT from the model's JSON schema, and the route's limit must cover it, so a
  schema that grows cannot silently outgrow its limit, and an override must be one the default
  cannot do without.
* **The pieces:** the `Content-Length` reader, the template matching, and the middleware alone,
  around hand-made ASGI apps, where "nothing was read" can be asserted.
* **The log line.**

`tests/` is not a package, so the few helpers the other API tests share (a spy on a route, a
signed-up session) are written out again here, as `test_request_schemas_forbid_extra.py` does.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Iterator
from inspect import iscoroutinefunction
from typing import Any

import pytest
import structlog
from email_validator.syntax import EMAIL_MAX_LENGTH
from fastapi import FastAPI
from fastapi import Response as AppResponse
from fastapi.routing import APIRoute
from httpx import AsyncClient
from httpx import Response as HttpResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from avp_api import ids, other_sites_bounds
from avp_api import main as api_main
from avp_api.body_limit import (
    MAX_LENGTH_DIGITS,
    OTHER_SITES_SETTING,
    WEBHOOK_SETTING,
    BodyLimitMiddleware,
    BodyLimits,
    MalformedContentLengthError,
    RouteLimit,
    configured_limits,
    declared_length,
    limits_from_settings,
    log_raised_limits,
    raised_limits,
)
from avp_api.config import (
    OTHER_SITES_BODY_LIMIT_CEILING,
    REQUEST_BODY_LIMIT_RANGE,
    REQUEST_BODY_TIMEOUT_RANGE,
    WEBHOOK_BODY_LIMIT_RANGE,
    Settings,
)
from avp_api.errors import InvalidWebhookSignature, RequestBodyTimeout, RequestBodyTooLarge
from avp_api.models import Agency, User
from avp_api.origin_check import OriginCheckMiddleware
from avp_api.other_sites_bounds import (
    MAX_DISMISSED_LIST,
    MAX_SITES_PER_BRAND_LIST,
    largest_plain_body_bytes,
)
from avp_api.schemas.agency import HEX_COLOR
from avp_api.security_headers import SECURITY_HEADERS, SecurityHeadersMiddleware
from avp_api.services import billing as billing_service
from avp_api.services.other_sites import MAX_DISMISSED, MAX_SITES_PER_BRAND

LIVE_APP: FastAPI = api_main.app
BASE = "/api/v1"
LOGIN = f"{BASE}/auth/login"
SIGN_UP = f"{BASE}/auth/sign-up"
WEBHOOK = f"{BASE}/billing/webhook"
OTHER_SITES = f"{BASE}/clients/{{clientId}}/other-sites"
JSON = {"Content-Type": "application/json"}

# Written out rather than read from the code, so editing the code cannot quietly edit what these
# tests pin.
TOO_LARGE = "This request is larger than this service accepts."
PROBLEM = "application/problem+json"
EVENT = "security.request_too_large"

# The default limit the tests that need a small one run with (a setting; the real default is
# 64 KiB). Set BEFORE the `client` fixture builds its app: see `small_default`.
SMALL = 2048

# The deadline the tests that need a short one run with (a setting; the real default is 30 s, and no
# test here waits longer than about a second). Set BEFORE `client` builds its app: `short_deadline`.
DEADLINE = 0.4
TIMEOUT_EVENT = "security.request_body_timeout"
TIMED_OUT = "This request took too long to arrive."
# A test that waits for something that must happen gives up after this long, so a regression fails
# the test instead of hanging the run (a stalled body is exactly what never returns on its own).
GIVE_UP = 5.0


def _document(path: str) -> dict[str, Any]:
    return {
        "type": "/problems/payload-too-large",
        "title": "Request too large",
        "status": 413,
        "detail": TOO_LARGE,
        "instance": path,
    }


def _assert_refused(resp: HttpResponse, path: str) -> None:
    """The whole refusal: status, content type, every member of the document, the six security
    headers (it is returned from inside the stack and leaves through the outermost layer)."""
    assert resp.status_code == 413, resp.text
    assert resp.headers["content-type"] == PROBLEM
    assert resp.json() == _document(path)
    for name, value in SECURITY_HEADERS:
        assert resp.headers.get_list(name.lower()) == [value], name


# ---------------------------------------------------------------------------
# helpers: bodies, streams, spies
# ---------------------------------------------------------------------------

_LOGIN_JSON = b'{"email":"a@b.co","password":"x"}'
_SIGN_UP_JSON = (
    b'{"agencyName":"Sized Agency","fullName":"Dana Whitfield",'
    b'"email":"dana@sized.example","password":"correct-horse-battery-staple"}'
)


def _padded(base: bytes, size: int) -> bytes:
    """`base`, then spaces, to exactly `size` bytes. Space after the last token is still the same
    JSON, so a body of any size can be a valid body: the size is tested and nothing else."""
    assert size >= len(base), f"{size} is below the {len(base)} bytes of the body itself"
    return base + b" " * (size - len(base))


async def _chunks(
    body: bytes, size: int = 500, pulled: list[int] | None = None
) -> AsyncIterator[bytes]:
    """`body` as a stream of `size`-byte chunks. httpx sends a stream of unknown length chunked,
    with no `Content-Length`. `pulled` records the offset of each chunk the server asked for."""
    for start in range(0, len(body), size):
        if pulled is not None:
            pulled.append(start)
        yield body[start : start + size]


def _all_routes(routes: Any) -> Iterator[Any]:
    for route in routes:
        yield route
        yield from _all_routes(getattr(route, "routes", None) or ())


def _api_routes(app: FastAPI) -> list[APIRoute]:
    return [route for route in _all_routes(app.routes) if isinstance(route, APIRoute)]


def _spy(app: FastAPI, method: str, template: str) -> list[int]:
    """Replace the handler of `method template` in THIS app with one that records each call and
    answers 204. Everything in front of it is the live route's own: the body validation, the
    dependencies, the error handlers. The recorded list is what the handler was reached with."""
    [route] = [r for r in _api_routes(app) if r.path == template and method in r.methods]
    calls: list[int] = []

    async def reached_async(**_: Any) -> AppResponse:
        calls.append(1)
        return AppResponse(status_code=204)

    def reached(**_: Any) -> AppResponse:
        calls.append(1)
        return AppResponse(status_code=204)

    original = route.dependant.call
    route.dependant.call = reached_async if iscoroutinefunction(original) else reached
    return calls


def _app(client: AsyncClient) -> FastAPI:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    assert isinstance(app, FastAPI)
    return app


def _limits_of(app: FastAPI) -> BodyLimits:
    [layer] = [m for m in app.user_middleware if m.cls is BodyLimitMiddleware]
    limits = layer.kwargs["limits"]
    assert isinstance(limits, BodyLimits)
    return limits


@pytest.fixture
def small_default(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> int:
    """The default limit, lowered to `SMALL`. It changes the `settings` the app is BUILT from, so a
    test lists it before `client`, which builds the app; `_limits_of` is how a test makes sure."""
    monkeypatch.setattr(settings, "max_request_body_bytes", SMALL)
    return SMALL


# An other-sites limit that is its own: above the default (64 KiB) and the largest body its schema
# allows (207,657), so the API applies it as it is. A lower one is RAISED, which is tested apart.
OWN_OTHER_SITES = 300_000


@pytest.fixture
def own_other_sites(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> int:
    """The other-sites limit, set to `OWN_OTHER_SITES`. List it before `client`, like the others."""
    monkeypatch.setattr(settings, "max_other_sites_body_bytes", OWN_OTHER_SITES)
    return OWN_OTHER_SITES


@pytest.fixture
def small_webhook(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> int:
    """The webhook limit, lowered to `SMALL`, with the default: a webhook limit under the default is
    raised to it, so to see a small one applied the default goes down with it. List it before
    `client`, as `small_default`."""
    monkeypatch.setattr(settings, "max_request_body_bytes", SMALL)
    monkeypatch.setattr(settings, "max_stripe_webhook_body_bytes", SMALL)
    return SMALL


@pytest.fixture
def raised_webhook(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """A webhook limit under the default: configured 2,048, default 8,192. The API applies 8,192."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 8192)
    monkeypatch.setattr(settings, "max_stripe_webhook_body_bytes", 2048)
    return 2048, 8192


@pytest.fixture
def raised_other_sites(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> int:
    """An other-sites limit under what its schema allows: configured 2,048. The API applies the
    largest body the schema allows (207,657)."""
    monkeypatch.setattr(settings, "max_other_sites_body_bytes", 2048)
    return 2048


@pytest.fixture
def short_deadline(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> float:
    """The body deadline, shortened to `DEADLINE` seconds. List it before `client`."""
    monkeypatch.setattr(settings, "request_body_timeout_seconds", DEADLINE)
    return DEADLINE


async def _sign_up(client: AsyncClient) -> None:
    """A real session, for the routes that sit behind one."""
    resp = await client.post(SIGN_UP, content=_SIGN_UP_JSON, headers=JSON)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# declared: a Content-Length over the limit is refused before anything is read
# ---------------------------------------------------------------------------


async def test_a_declared_length_over_the_limit_is_refused_and_the_route_never_runs(
    small_default: int, client: AsyncClient
) -> None:
    assert _limits_of(_app(client)).default == SMALL
    calls = _spy(_app(client), "POST", LOGIN)

    resp = await client.post(LOGIN, content=_padded(_LOGIN_JSON, SMALL + 1), headers=JSON)

    _assert_refused(resp, LOGIN)
    assert calls == [], "the route ran for a body over the limit"


async def test_a_body_of_exactly_the_limit_passes_and_the_route_gets_all_of_it(
    small_default: int, client: AsyncClient
) -> None:
    assert _limits_of(_app(client)).default == SMALL
    calls = _spy(_app(client), "POST", LOGIN)

    resp = await client.post(LOGIN, content=_padded(_LOGIN_JSON, SMALL), headers=JSON)

    assert resp.status_code == 204, resp.text
    assert calls == [1]


async def test_a_sign_up_over_the_limit_creates_nothing_and_one_at_the_limit_creates_the_account(
    small_default: int, client: AsyncClient, session: AsyncSession
) -> None:
    """The write that must not happen, on the real handler. The body is a valid sign-up either way:
    only its size, by trailing space, differs."""
    assert _limits_of(_app(client)).default == SMALL

    async def rows() -> tuple[int, int]:
        users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        agencies = (await session.execute(select(func.count()).select_from(Agency))).scalar_one()
        return users, agencies

    refused = await client.post(SIGN_UP, content=_padded(_SIGN_UP_JSON, SMALL + 1), headers=JSON)
    _assert_refused(refused, SIGN_UP)
    assert await rows() == (0, 0), "an account was made from a request that was refused"

    accepted = await client.post(SIGN_UP, content=_padded(_SIGN_UP_JSON, SMALL), headers=JSON)
    assert accepted.status_code == 201, accepted.text
    assert await rows() == (1, 1)


async def test_an_empty_body_is_for_the_route_to_judge_and_not_a_413(
    small_default: int, client: AsyncClient
) -> None:
    """Here a 422 for the missing fields."""
    assert _limits_of(_app(client)).default == SMALL

    resp = await client.post(LOGIN, content=b"", headers=JSON)

    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", LOGIN),
        ("PUT", f"{BASE}/clients/{ids.new_id(ids.CLIENT)}/competitors"),
        ("PATCH", f"{BASE}/agencies/{ids.new_id(ids.AGENCY)}/branding"),
        ("DELETE", f"{BASE}/users/{ids.new_id(ids.USER)}"),
        ("GET", f"{BASE}/health"),
        ("POST", f"{BASE}/no-such-route"),
    ],
    ids=["POST", "PUT", "PATCH", "DELETE", "GET", "an unknown path"],
)
async def test_every_method_and_even_an_unknown_path_is_refused_by_its_declared_length(
    small_default: int, client: AsyncClient, method: str, path: str
) -> None:
    """Nothing about the limit hangs on the method or on a route existing: it is measured before
    routing. (A DELETE or GET that carries a body is refused for it like any other request.)"""
    assert _limits_of(_app(client)).default == SMALL

    resp = await client.request(method, path, content=b" " * (SMALL + 1), headers=JSON)

    _assert_refused(resp, path)


async def test_the_refusal_carries_the_cors_headers_an_allowed_origin_gets(
    small_default: int, client: AsyncClient, settings: Settings
) -> None:
    """The layer is INSIDE CORS: a page on the web app's origin can read the refusal."""
    assert _limits_of(_app(client)).default == SMALL
    allowed = settings.cors_allow_origins[0]

    resp = await client.post(
        LOGIN, content=_padded(_LOGIN_JSON, SMALL + 1), headers={**JSON, "Origin": allowed}
    )

    _assert_refused(resp, LOGIN)
    assert resp.headers["access-control-allow-origin"] == allowed
    assert resp.headers["access-control-allow-credentials"] == "true"


# ---------------------------------------------------------------------------
# counted: no Content-Length, or a lying one
# ---------------------------------------------------------------------------


async def test_a_chunked_body_over_the_limit_is_refused_and_the_rest_is_never_read(
    small_default: int, client: AsyncClient
) -> None:
    assert _limits_of(_app(client)).default == SMALL
    calls = _spy(_app(client), "POST", LOGIN)
    body, size, pulled = _padded(_LOGIN_JSON, 10 * SMALL), 500, []

    resp = await client.post(LOGIN, content=_chunks(body, size, pulled), headers=JSON)

    _assert_refused(resp, LOGIN)
    assert calls == [], "the route ran for a body over the limit"
    # SMALL / 500 = 4.1: the fifth chunk is the one that passes the limit, and nothing after it
    # is asked for. A layer that read the stream to its end would have pulled all 41.
    assert len(pulled) == SMALL // size + 1, f"{len(pulled)} chunks were pulled"


async def test_a_chunked_body_under_the_limit_passes_and_a_body_at_it_does_too(
    small_default: int, client: AsyncClient
) -> None:
    assert _limits_of(_app(client)).default == SMALL
    calls = _spy(_app(client), "POST", LOGIN)

    for size in (SMALL - 1, SMALL):
        resp = await client.post(LOGIN, content=_chunks(_padded(_LOGIN_JSON, size)), headers=JSON)
        assert resp.status_code == 204, (size, resp.text)
    assert calls == [1, 1]

    over = await client.post(LOGIN, content=_chunks(_padded(_LOGIN_JSON, SMALL + 1)), headers=JSON)
    _assert_refused(over, LOGIN)
    assert calls == [1, 1], "the body one byte over the limit reached the route"


async def test_a_content_length_that_lies_is_counted_not_believed(
    small_default: int, client: AsyncClient
) -> None:
    """A small header over a big stream (an in-process client and some other servers let it
    through; uvicorn would not). The declared length is under the limit, so it is let in, and the
    count cuts it off."""
    assert _limits_of(_app(client)).default == SMALL
    calls = _spy(_app(client), "POST", LOGIN)
    pulled: list[int] = []

    resp = await client.post(
        LOGIN,
        content=_chunks(_padded(_LOGIN_JSON, 10 * SMALL), 500, pulled),
        headers={**JSON, "Content-Length": "40"},
    )

    _assert_refused(resp, LOGIN)
    assert calls == []
    assert len(pulled) == SMALL // 500 + 1


async def test_a_flood_of_keys_over_the_limit_is_a_413_and_one_under_it_is_still_a_422(
    client: AsyncClient,
) -> None:
    """A8's guard (`too_many_fields`) and this limit are two layers, in this order. 100,000 keys is
    1.6 MB: at the default 64 KiB it is refused by size, before it is parsed. 3,000 keys is about 40
    KB: under the limit, so it is parsed, and the guard answers it with its one small error."""
    keys = {**json.loads(_LOGIN_JSON)}
    over = {**keys, **{f"key{i}": i for i in range(100_000)}}
    under = {**keys, **{f"key{i}": i for i in range(3000)}}

    refused = await client.post(LOGIN, json=over)
    guarded = await client.post(LOGIN, json=under)

    _assert_refused(refused, LOGIN)
    assert guarded.status_code == 422, guarded.text
    assert [e["type"] for e in guarded.json()["errors"]] == ["too_many_fields"]
    assert len(json.dumps(under)) < 65_536 < len(json.dumps(over))


async def test_the_same_document_comes_from_a_declared_length_and_from_the_count(
    small_default: int, client: AsyncClient
) -> None:
    """Two code paths render it (the middleware, and the exception handlers for the mid-stream
    one). One document, byte for byte."""
    assert _limits_of(_app(client)).default == SMALL
    declared = await client.post(LOGIN, content=_padded(_LOGIN_JSON, SMALL + 1), headers=JSON)
    counted = await client.post(
        LOGIN, content=_chunks(_padded(_LOGIN_JSON, SMALL + 1)), headers=JSON
    )

    assert declared.content == counted.content
    assert declared.headers["content-type"] == counted.headers["content-type"] == PROBLEM


async def test_a_mid_stream_refusal_is_a_413_and_not_fastapis_400(
    small_default: int, client: AsyncClient
) -> None:
    """FastAPI turns an exception it does not know, raised while it reads the body, into 400 "There
    was an error parsing the body". The refusal must come out as the 413 it is."""
    assert _limits_of(_app(client)).default == SMALL

    resp = await client.post(LOGIN, content=_chunks(_padded(_LOGIN_JSON, 3 * SMALL)), headers=JSON)

    assert resp.status_code == 413
    assert "error parsing the body" not in resp.text


# ---------------------------------------------------------------------------
# the raw-body route: the Stripe webhook
# ---------------------------------------------------------------------------


@pytest.fixture
def webhook_reached(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """The payloads `verify_webhook` is called with. It refuses each as a bad signature, so
    nothing else of the route runs, and a test can tell reached (400) from refused (413)."""
    payloads: list[bytes] = []

    def refuse(payload: bytes, signature: str | None, *, settings: Settings) -> Any:
        payloads.append(payload)
        raise InvalidWebhookSignature(detail="stubbed")

    monkeypatch.setattr(billing_service, "verify_webhook", refuse)
    return payloads


async def test_the_webhook_has_the_limit_of_its_own_setting(
    client: AsyncClient, settings: Settings, webhook_reached: list[bytes]
) -> None:
    """The real default, 1 MiB: a body of exactly that reaches the route whole, one byte more does
    not. (Stripe retries a refused event for days, so the edge is worth pinning.)"""
    limit = settings.max_stripe_webhook_body_bytes
    assert limit == 1024 * 1024
    stripe_like = b'{"id":"evt_1","type":"customer.subscription.updated"}'

    at = await client.post(WEBHOOK, content=_padded(stripe_like, limit), headers=JSON)
    assert at.status_code == 400, at.text
    assert at.json()["type"] == "/problems/invalid-webhook-signature"
    assert [len(p) for p in webhook_reached] == [limit], "the route did not get the whole body"

    over = await client.post(WEBHOOK, content=_padded(stripe_like, limit + 1), headers=JSON)
    _assert_refused(over, WEBHOOK)
    assert len(webhook_reached) == 1


async def test_the_webhook_is_cut_off_mid_stream_through_its_raw_read(
    small_webhook: int, client: AsyncClient, webhook_reached: list[bytes]
) -> None:
    """The route reads `await request.body()` itself, outside FastAPI's body parsing. A chunked
    event over the limit must still be a 413, and `verify_webhook` is never called with it."""
    assert _limits_of(_app(client)).for_request("POST", WEBHOOK) == SMALL
    pulled: list[int] = []

    resp = await client.post(
        WEBHOOK, content=_chunks(b" " * (4 * SMALL), 256, pulled), headers=JSON
    )

    _assert_refused(resp, WEBHOOK)
    assert webhook_reached == []
    assert len(pulled) == SMALL // 256 + 1, f"{len(pulled)} chunks were pulled"


async def test_a_declared_webhook_body_over_its_limit_is_refused_before_the_route(
    small_webhook: int, client: AsyncClient, webhook_reached: list[bytes]
) -> None:
    assert _limits_of(_app(client)).for_request("POST", WEBHOOK) == SMALL

    resp = await client.post(WEBHOOK, content=b" " * (SMALL + 1), headers=JSON)

    _assert_refused(resp, WEBHOOK)
    assert webhook_reached == []


# ---------------------------------------------------------------------------
# the other-sites list: the one route whose schema allows a large body
# ---------------------------------------------------------------------------


def _other_sites_path() -> str:
    return OTHER_SITES.replace("{clientId}", ids.new_id(ids.CLIENT))


async def test_other_sites_takes_the_largest_body_its_schema_allows_and_refuses_one_over_its_limit(
    client: AsyncClient, settings: Settings
) -> None:
    """207,657 bytes: 430 domains of 253 characters (50 for the client, 50 for each of ten rivals,
    250 dismissed), built from the schema's own bounds. The request is valid by the schema (a
    domain's other checks are the service's, behind the spy)."""
    await _sign_up(client)
    calls = _spy(_app(client), "PUT", OTHER_SITES)
    path = _other_sites_path()
    largest = largest_body(_model_of("PUT", OTHER_SITES), "a", ensure_ascii=True)
    assert len(largest) == 207_657, "the body this test is about is not the largest one"
    limit = settings.max_other_sites_body_bytes
    assert limit == 512 * 1024

    resp = await client.put(path, content=largest, headers=JSON)
    assert resp.status_code == 204, resp.text
    assert calls == [1]

    resp = await client.put(path, content=_padded(largest, limit), headers=JSON)
    assert resp.status_code == 204, "a body of exactly its limit was refused"
    assert calls == [1, 1]

    resp = await client.put(path, content=_padded(largest, limit + 1), headers=JSON)
    _assert_refused(resp, path)
    assert calls == [1, 1], "a body over the limit reached the route"


async def test_the_same_body_is_refused_on_a_route_that_has_no_override(
    client: AsyncClient,
) -> None:
    path = _other_sites_path()
    body = largest_body(_model_of("PUT", OTHER_SITES), "a", ensure_ascii=True)

    for method, target in (
        ("PUT", f"{BASE}/clients/{ids.new_id(ids.CLIENT)}/competitors"),
        ("PATCH", path),
        ("POST", path),
        ("PUT", path + "/"),
    ):
        resp = await client.request(method, target, content=body, headers=JSON)
        _assert_refused(resp, target)


async def test_the_other_sites_limit_is_its_own_setting(
    own_other_sites: int, client: AsyncClient
) -> None:
    """Set to 300,000 bytes (above its floor, so applied as it is), the route is refused above that
    and the default is untouched."""
    limits = _limits_of(_app(client))
    assert limits.for_request("PUT", _concrete(OTHER_SITES)) == OWN_OTHER_SITES
    assert limits.default == 65_536
    await _sign_up(client)
    calls = _spy(_app(client), "PUT", OTHER_SITES)
    path = _other_sites_path()
    base = b'{"client":[],"rivals":[],"dismissed":[]}'

    at = await client.put(path, content=_padded(base, OWN_OTHER_SITES), headers=JSON)
    over = await client.put(path, content=_padded(base, OWN_OTHER_SITES + 1), headers=JSON)

    assert at.status_code == 204, at.text
    _assert_refused(over, path)
    assert calls == [1]


async def test_an_other_sites_limit_under_its_schema_is_applied_at_the_schema_not_refused(
    raised_other_sites: int, client: AsyncClient
) -> None:
    """Configured 2,048: the API starts and applies 207,657, the largest body its schema allows.
    That body passes and one byte more is a 413."""
    limits = _limits_of(_app(client))
    floor = largest_plain_body_bytes()
    assert limits.for_request("PUT", _concrete(OTHER_SITES)) == floor == 207_657
    await _sign_up(client)
    calls = _spy(_app(client), "PUT", OTHER_SITES)
    path = _other_sites_path()
    largest = largest_body(_model_of("PUT", OTHER_SITES), "a", ensure_ascii=True)

    at = await client.put(path, content=largest, headers=JSON)
    over = await client.put(path, content=_padded(largest, floor + 1), headers=JSON)

    assert at.status_code == 204, "the largest body the schema allows was refused"
    _assert_refused(over, path)
    assert calls == [1]


async def test_a_webhook_limit_under_the_default_is_applied_at_the_default_not_refused(
    raised_webhook: tuple[int, int], client: AsyncClient, webhook_reached: list[bytes]
) -> None:
    configured, default = raised_webhook
    assert _limits_of(_app(client)).for_request("POST", WEBHOOK) == default

    between = await client.post(WEBHOOK, content=b" " * (configured * 2), headers=JSON)
    at = await client.post(WEBHOOK, content=b" " * default, headers=JSON)
    over = await client.post(WEBHOOK, content=b" " * (default + 1), headers=JSON)

    assert between.status_code == 400 and at.status_code == 400  # past the size check: the stub's
    assert [len(p) for p in webhook_reached] == [configured * 2, default]
    _assert_refused(over, WEBHOOK)


# ---------------------------------------------------------------------------
# the other methods: a GET and a preflight pass, and the order of the layers
# ---------------------------------------------------------------------------


async def test_a_get_passes_untouched(small_default: int, client: AsyncClient) -> None:
    assert _limits_of(_app(client)).default == SMALL

    resp = await client.get(f"{BASE}/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_a_cors_preflight_passes_untouched(
    small_default: int, client: AsyncClient, settings: Settings
) -> None:
    assert _limits_of(_app(client)).default == SMALL
    allowed = settings.cors_allow_origins[0]

    resp = await client.options(
        f"{BASE}/clients",
        headers={
            "Origin": allowed,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert resp.status_code == 200
    assert resp.text == "OK", "the body is CORS's own"
    assert resp.headers["access-control-allow-origin"] == allowed


def test_the_layer_sits_inside_the_security_headers_and_cors_and_outside_the_origin_check(
    settings: Settings,
) -> None:
    """Starlette puts the LAST `add_middleware` outermost. The security headers stay outermost and
    CORS next (`test_security_headers.py`, `test_origin_check.py`), so the 413 carries both."""
    order = [m.cls for m in api_main.create_app(settings).user_middleware]

    assert order == [
        SecurityHeadersMiddleware,
        CORSMiddleware,
        BodyLimitMiddleware,
        OriginCheckMiddleware,
    ]


def test_the_layer_is_configured_from_the_settings(settings: Settings) -> None:
    app = api_main.create_app(settings)
    limits = _limits_of(app)
    [layer] = [m for m in app.user_middleware if m.cls is BodyLimitMiddleware]

    assert layer.kwargs["body_timeout_seconds"] == settings.request_body_timeout_seconds
    assert limits.default == settings.max_request_body_bytes
    assert limits.for_request("PUT", _other_sites_path()) == settings.max_other_sites_body_bytes
    assert limits.for_request("POST", WEBHOOK) == settings.max_stripe_webhook_body_bytes


# ---------------------------------------------------------------------------
# the limits are settings
# ---------------------------------------------------------------------------


def test_the_defaults_are_the_ones_the_docs_and_the_runbook_give() -> None:
    fresh = Settings(_env_file=None)

    assert (
        fresh.max_request_body_bytes,
        fresh.max_other_sites_body_bytes,
        fresh.max_stripe_webhook_body_bytes,
        fresh.request_body_timeout_seconds,
    ) == (65_536, 524_288, 1_048_576, 30.0)
    assert REQUEST_BODY_LIMIT_RANGE == (65_536, 1_048_576)
    assert WEBHOOK_BODY_LIMIT_RANGE == (262_144, 8_388_608)
    assert OTHER_SITES_BODY_LIMIT_CEILING == 8_388_608
    assert REQUEST_BODY_TIMEOUT_RANGE == (5, 120)


def _refusal(**values: Any) -> str:
    """What the API says when it will not start with these settings."""
    with pytest.raises(ValidationError) as refused:
        Settings(_env_file=None, **values)
    return str(refused.value)


@pytest.mark.parametrize(
    ("name", "low", "high", "company"),
    [
        # The default's highest value would pass the other-sites limit's floor, so that one is
        # raised with it: the point of the cross-check.
        pytest.param(
            "max_request_body_bytes", *REQUEST_BODY_LIMIT_RANGE,
            {"max_other_sites_body_bytes": REQUEST_BODY_LIMIT_RANGE[1]}, id="the default"),
        pytest.param(
            "max_stripe_webhook_body_bytes", *WEBHOOK_BODY_LIMIT_RANGE, {}, id="the webhook"
        ),
    ],
)
def test_a_limit_with_a_range_of_its_own_stops_the_start_outside_it_naming_the_setting(
    name: str, low: int, high: int, company: dict[str, int]
) -> None:
    for bad in (0, -1, 1024, low - 1, high + 1, 64 * 1024 * 1024):
        assert name in _refusal(**{name: bad}), (name, bad)
    for good in (low, high):
        assert getattr(Settings(_env_file=None, **{name: good}, **company), name) == good
    # Not a number, and nothing: a blank value is not a limit either.
    for junk in ("", "64KB", "lots"):
        assert name in _refusal(**{name: junk}), (name, junk)


def _raised(settings: Settings) -> list[tuple[str, int, int, str]]:
    """What the API raises, as plain tuples: (setting, configured, applied, which floor)."""
    return [(r.setting, r.configured, r.effective, r.reason) for r in raised_limits(settings)]


def test_a_webhook_limit_below_the_default_is_raised_to_it_and_the_start_goes_on() -> None:
    """Both in their own range, the default at its highest and the webhook at its lowest. Used to
    stop the start; now the API starts and applies the default to the webhook."""
    big = REQUEST_BODY_LIMIT_RANGE[1]
    small = WEBHOOK_BODY_LIMIT_RANGE[0]
    assert small < big

    settings = Settings(
        _env_file=None,
        max_request_body_bytes=big,
        max_other_sites_body_bytes=big,
        max_stripe_webhook_body_bytes=small,
    )

    assert _raised(settings) == [(WEBHOOK_SETTING, small, big, "below_default")]
    assert limits_from_settings(settings).for_request("POST", WEBHOOK) == big
    assert configured_limits(settings).for_request("POST", WEBHOOK) == small, "as it was set"
    # Equal is not below, and so is not raised.
    equal = Settings(
        _env_file=None,
        max_request_body_bytes=big,
        max_other_sites_body_bytes=big,
        max_stripe_webhook_body_bytes=big,
    )
    assert _raised(equal) == []


def test_raising_the_default_alone_raises_other_sites_with_it_and_the_start_goes_on() -> None:
    """The ordinary mistake the clamp is for: the default put up to its top, other-sites left at
    512 KiB. That used to lock the deploy, a hotfix included."""
    top = REQUEST_BODY_LIMIT_RANGE[1]

    settings = Settings(_env_file=None, max_request_body_bytes=top)

    assert _raised(settings) == [(OTHER_SITES_SETTING, 524_288, top, "below_default")]
    limits = limits_from_settings(settings)
    assert limits.default == top
    assert limits.for_request("PUT", _other_sites_path()) == top
    assert limits.for_request("POST", WEBHOOK) == 1_048_576, "the webhook's was not below it"


def test_an_other_sites_limit_under_the_largest_body_its_schema_allows_is_raised_to_it() -> None:
    """The floor is COMPUTED from the schema's bounds (`other_sites_bounds`), not typed here."""
    floor = largest_plain_body_bytes()
    assert floor > REQUEST_BODY_LIMIT_RANGE[0], "the schema's largest body is under the default"

    below = Settings(_env_file=None, max_other_sites_body_bytes=floor - 1)
    at = Settings(_env_file=None, max_other_sites_body_bytes=floor)

    assert _raised(below) == [(OTHER_SITES_SETTING, floor - 1, floor, "below_schema")]
    assert limits_from_settings(below).for_request("PUT", _other_sites_path()) == floor
    assert _raised(at) == [], "a limit AT the floor is not raised"
    # Nonsense is raised too, and starts: only a value outside the setting's own range does not.
    for junk in (0, -1, 1024):
        settings = Settings(_env_file=None, max_other_sites_body_bytes=junk)
        assert _raised(settings) == [(OTHER_SITES_SETTING, junk, floor, "below_schema")], junk


def test_the_reason_named_is_the_floor_that_set_the_applied_limit() -> None:
    """Under both floors, the larger one is what is applied, and is what is named."""
    floor = largest_plain_body_bytes()
    assert floor > REQUEST_BODY_LIMIT_RANGE[0] and floor < REQUEST_BODY_LIMIT_RANGE[1]

    default_is_the_floor = Settings(
        _env_file=None, max_request_body_bytes=REQUEST_BODY_LIMIT_RANGE[1],
        max_other_sites_body_bytes=1000,
    )
    schema_is_the_floor = Settings(_env_file=None, max_other_sites_body_bytes=1000)

    assert _raised(default_is_the_floor)[0][2:] == (REQUEST_BODY_LIMIT_RANGE[1], "below_default")
    assert _raised(schema_is_the_floor)[0][2:] == (floor, "below_schema")


def test_both_raised_at_once_are_two_entries_webhook_first() -> None:
    top = REQUEST_BODY_LIMIT_RANGE[1]

    settings = Settings(
        _env_file=None,
        max_request_body_bytes=top,
        max_other_sites_body_bytes=1000,
        max_stripe_webhook_body_bytes=WEBHOOK_BODY_LIMIT_RANGE[0],
    )

    assert _raised(settings) == [
        (WEBHOOK_SETTING, WEBHOOK_BODY_LIMIT_RANGE[0], top, "below_default"),
        (OTHER_SITES_SETTING, 1000, top, "below_default"),
    ]


def test_the_default_settings_raise_nothing() -> None:
    assert raised_limits(Settings(_env_file=None)) == []


def test_the_other_sites_limit_is_at_most_eight_mebibytes() -> None:
    assert "max_other_sites_body_bytes" in _refusal(
        max_other_sites_body_bytes=OTHER_SITES_BODY_LIMIT_CEILING + 1
    )
    ok = Settings(
        _env_file=None, max_other_sites_body_bytes=OTHER_SITES_BODY_LIMIT_CEILING
    )
    assert ok.max_other_sites_body_bytes == OTHER_SITES_BODY_LIMIT_CEILING


def test_the_deadline_has_a_range_and_is_a_real_number() -> None:
    low, high = REQUEST_BODY_TIMEOUT_RANGE
    name = "request_body_timeout_seconds"
    infinite = (float("inf"), float("-inf"), float("nan"))
    for bad in (0, -1, low - 0.01, high + 0.01, *infinite, "", "soon"):
        assert name in _refusal(**{name: bad}), bad
    for good in (low, 7.5, high):
        assert Settings(_env_file=None, **{name: good}).request_body_timeout_seconds == good


def test_the_start_refuses_a_value_outside_a_settings_own_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As a deploy sees it: the environment variable, and the message names the setting. These are
    the refusals that remain; a PAIR that disagrees is not one of them (next)."""
    for variable, value in (
        ("MAX_REQUEST_BODY_BYTES", "1000"),
        ("MAX_REQUEST_BODY_BYTES", "2097152"),
        ("MAX_STRIPE_WEBHOOK_BODY_BYTES", "100000"),
        ("MAX_STRIPE_WEBHOOK_BODY_BYTES", "9437184"),
        ("MAX_OTHER_SITES_BODY_BYTES", "9437184"),
        ("REQUEST_BODY_TIMEOUT_SECONDS", "1"),
        ("REQUEST_BODY_TIMEOUT_SECONDS", "soon"),
    ):
        monkeypatch.setenv(variable, value)
        said = _refusal()
        assert variable.lower() in said.lower(), (variable, said)
        monkeypatch.delenv(variable)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        pytest.param("MAX_REQUEST_BODY_BYTES", "1048576", id="the default at its top"),
        pytest.param("MAX_OTHER_SITES_BODY_BYTES", "65536", id="other-sites at the default"),
        pytest.param("MAX_OTHER_SITES_BODY_BYTES", "100000", id="other-sites under its schema"),
        pytest.param("MAX_OTHER_SITES_BODY_BYTES", "1", id="other-sites at nothing"),
    ],
)
def test_the_start_does_not_refuse_a_pair_that_disagrees(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """The deploy goes on; the API applies the floor and says so (`config.body_limit_raised`)."""
    monkeypatch.setenv(variable, value)

    settings = Settings(_env_file=None)

    assert _raised(settings), "nothing was raised, so this proves nothing"
    limits = limits_from_settings(settings)
    assert limits.for_request("PUT", _other_sites_path()) >= largest_plain_body_bytes()
    assert limits.for_request("POST", WEBHOOK) >= limits.default


def test_a_limit_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "131072")
    monkeypatch.setenv("MAX_OTHER_SITES_BODY_BYTES", "262144")
    monkeypatch.setenv("MAX_STRIPE_WEBHOOK_BODY_BYTES", "524288")
    monkeypatch.setenv("REQUEST_BODY_TIMEOUT_SECONDS", "45")

    settings = Settings(_env_file=None)
    limits = limits_from_settings(settings)

    assert limits.default == 131_072
    assert limits.for_request("PUT", _other_sites_path()) == 262_144
    assert limits.for_request("POST", WEBHOOK) == 524_288
    assert settings.request_body_timeout_seconds == 45


# ---------------------------------------------------------------------------
# a raised limit is said once, when the app is built
# ---------------------------------------------------------------------------

RAISED_EVENT = "config.body_limit_raised"


def test_each_raised_limit_is_one_warning_with_the_setting_the_numbers_and_the_floor() -> None:
    top = REQUEST_BODY_LIMIT_RANGE[1]
    settings = Settings(
        _env_file=None,
        max_request_body_bytes=top,
        max_other_sites_body_bytes=1000,
        max_stripe_webhook_body_bytes=WEBHOOK_BODY_LIMIT_RANGE[0],
    )

    with structlog.testing.capture_logs() as events:
        log_raised_limits(settings)

    assert [{k: v for k, v in e.items() if k != "log_level"} for e in events] == [
        {
            "event": RAISED_EVENT,
            "setting": WEBHOOK_SETTING,
            "configured": WEBHOOK_BODY_LIMIT_RANGE[0],
            "effective": top,
            "reason": "below_default",
        },
        {
            "event": RAISED_EVENT,
            "setting": OTHER_SITES_SETTING,
            "configured": 1000,
            "effective": top,
            "reason": "below_default",
        },
    ]
    assert {e["log_level"] for e in events} == {"warning"}


def test_nothing_is_logged_when_nothing_is_raised() -> None:
    with structlog.testing.capture_logs() as events:
        log_raised_limits(Settings(_env_file=None))

    assert events == []


def test_the_warning_names_the_schema_when_that_is_the_floor() -> None:
    with structlog.testing.capture_logs() as events:
        log_raised_limits(Settings(_env_file=None, max_other_sites_body_bytes=100_000))

    [line] = events
    assert (line["setting"], line["configured"], line["effective"], line["reason"]) == (
        OTHER_SITES_SETTING, 100_000, 207_657, "below_schema",
    )


async def test_building_the_app_says_so_once_and_a_request_says_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Through `create_app`, as it is printed: once at the build, never per request. (The build
    configures logging itself, so this reads what it printed and does not capture events.)"""
    from httpx import ASGITransport

    settings = Settings(_env_file=None, max_request_body_bytes=REQUEST_BODY_LIMIT_RANGE[1])
    capsys.readouterr()

    app = api_main.create_app(settings)
    built = capsys.readouterr().out
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        for _ in range(3):
            await c.get(f"{BASE}/health")
    served = capsys.readouterr().out

    assert built.count(RAISED_EVENT) == 1, built
    assert OTHER_SITES_SETTING in built and "below_default" in built
    assert RAISED_EVENT not in served
    assert _limits_of(app).for_request("PUT", _other_sites_path()) == REQUEST_BODY_LIMIT_RANGE[1]


def test_the_app_built_from_the_default_settings_says_nothing_of_the_kind(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()

    api_main.create_app(settings)

    assert RAISED_EVENT not in capsys.readouterr().out


def test_the_warning_carries_only_numbers_and_the_setting_name() -> None:
    settings = Settings(_env_file=None, max_other_sites_body_bytes=100_000)

    with structlog.testing.capture_logs() as events:
        log_raised_limits(settings)

    [line] = events
    assert set(line) == {"event", "log_level", "setting", "configured", "effective", "reason"}
    assert all(type(line[k]) is int for k in ("configured", "effective"))


# ---------------------------------------------------------------------------
# derived from the schemas: every route's limit covers the largest body its model allows
#
# The largest legitimate body is BUILT from the model's JSON schema (every property present, every
# string at its `maxLength`, every list at its `maxItems`, the longest of a union), then measured.
# Two spellings of the same characters: plain ASCII (1 byte each), and the worst a JSON text can do
# to one character, an astral code point written as an escaped surrogate pair, two six-character
# `\u` escapes (12 bytes).
# ---------------------------------------------------------------------------

# A string whose `maxLength` the schema does not carry, and how many characters the model lets it
# have instead. None else may exist: a field that is not bounded is bounded only by the limit.
FIXED_WIDTH_PATTERNS: dict[str, int] = {HEX_COLOR: 7}
EMAIL_WIDTH = EMAIL_MAX_LENGTH  # `EmailStr`'s validator refuses an address longer than this
ASTRAL = "\U0001f600"


class _Unbounded(Exception):  # noqa: N818 - a signal for the sweep, not an error
    """The schema has no upper bound for this part of the body."""


def _resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _text(value: Any, ensure_ascii: bool) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=ensure_ascii)


def _maximal(
    schema: dict[str, Any], defs: dict[str, Any], char: str, ensure_ascii: bool, where: str
) -> Any:
    """The largest JSON value `schema` allows, with `char` for every character."""
    schema = _resolve(schema, defs)
    for union in ("anyOf", "oneOf", "allOf"):
        if union in schema:
            options = [_maximal(s, defs, char, ensure_ascii, where) for s in schema[union]]
            return max(options, key=lambda value: len(_text(value, ensure_ascii)))
    if "enum" in schema:
        return max(schema["enum"], key=lambda value: len(_text(value, ensure_ascii)))
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "null":
        return None
    if kind == "boolean":
        return False  # `false` is longer than `true`
    if kind in ("integer", "number"):
        if "maximum" not in schema:
            raise _Unbounded(where)
        bounds = [schema["maximum"], schema.get("minimum", schema["maximum"])]
        return max(bounds, key=lambda value: len(str(value)))
    if kind == "string":
        if "maxLength" in schema:
            return char * schema["maxLength"]
        if schema.get("format") == "email":
            return char * EMAIL_WIDTH
        if schema.get("pattern") in FIXED_WIDTH_PATTERNS:
            return char * FIXED_WIDTH_PATTERNS[schema["pattern"]]
        raise _Unbounded(where)
    if kind == "array":
        if "maxItems" not in schema:
            raise _Unbounded(where)
        item = _maximal(schema["items"], defs, char, ensure_ascii, where + "[]")
        return [item] * schema["maxItems"]
    if kind == "object":
        return {
            name: _maximal(prop, defs, char, ensure_ascii, f"{where}.{name}")
            for name, prop in schema.get("properties", {}).items()
        }
    raise _Unbounded(f"{where} (schema type {kind!r})")


def largest_body(model: type[BaseModel], char: str, *, ensure_ascii: bool) -> bytes:
    """The compact JSON of the largest body `model` allows, in the spelling the wire uses."""
    schema = model.model_json_schema(by_alias=True, mode="validation")
    body = _maximal(schema, schema.get("$defs", {}), char, ensure_ascii, model.__name__)
    return _text(body, ensure_ascii).encode()


def _operations() -> list[tuple[str, APIRoute]]:
    return [
        (method, route)
        for route in _api_routes(LIVE_APP)
        if route.body_field is not None
        for method in sorted(route.methods)
    ]


OPERATIONS = _operations()
OPERATION_IDS = [f"{method} {route.path.removeprefix(BASE)}" for method, route in OPERATIONS]


def _model_of(method: str, template: str) -> type[BaseModel]:
    [route] = [
        r for r in _api_routes(LIVE_APP)
        if r.path == template and method in r.methods and r.body_field is not None
    ]
    model = route.body_field.type_  # type: ignore[union-attr]
    assert isinstance(model, type) and issubclass(model, BaseModel)
    return model


def _concrete(template: str) -> str:
    return re.sub(r"\{(\w+)\}", "x", template)


def test_the_sweep_finds_the_whole_request_surface() -> None:
    """20 operations take a body today. The webhook is not one: it reads its own raw body."""
    assert len(OPERATIONS) >= 20, f"only {len(OPERATIONS)} operations with a body were found"
    assert ("PUT", OTHER_SITES) in {(m, r.path) for m, r in OPERATIONS}


@pytest.mark.parametrize(("method", "route"), OPERATIONS, ids=OPERATION_IDS)
def test_the_limit_covers_the_largest_body_the_schema_allows(
    method: str, route: APIRoute, settings: Settings
) -> None:
    """`limit >= the largest body`, in plain characters, for every route. A route on the default
    limit must also cover the worst spelling of every character: no name in any script, written
    in any JSON escape, can be refused. If this fails after a schema change, the schema outgrew
    its limit: raise the limit (a setting, and an override if the default cannot) or bound the
    schema; do not drop the test."""
    model = route.body_field.type_  # type: ignore[union-attr]
    # As CONFIGURED, not as applied: the API raises a limit under its floor and goes on, which would
    # keep this green for a schema that has outgrown the shipped defaults. CI is what says so.
    limits = configured_limits(settings)
    limit = limits.for_request(method, _concrete(route.path))

    try:
        plain = len(largest_body(model, "a", ensure_ascii=True))
        escaped = (
            len(largest_body(model, ASTRAL, ensure_ascii=True)) if limit == limits.default else 0
        )
    except _Unbounded as unbounded:
        pytest.fail(
            f"{unbounded} has no upper bound in the schema, so the body of {method} {route.path} "
            "has none either. Give the field a max_length, a max_length on its list, or a maximum."
        )

    assert limit >= plain, f"{method} {route.path}: the limit {limit} < the largest body {plain}"
    if limit == limits.default:
        assert limit >= escaped, (
            f"{method} {route.path}: the default {limit} < {escaped}, the largest body when every "
            "character is written as a 12-byte escape"
        )


def test_an_override_exists_only_for_a_route_that_needs_one(settings: Settings) -> None:
    """A route with a limit of its own must have a body the default cannot hold: otherwise the
    override is only a larger door than the route needs."""
    limits = configured_limits(settings)
    overridden = [
        (method, route)
        for method, route in OPERATIONS
        if limits.for_request(method, _concrete(route.path)) != limits.default
    ]

    assert [(m, r.path) for m, r in overridden] == [("PUT", OTHER_SITES)]
    for _method, route in overridden:
        model = route.body_field.type_  # type: ignore[union-attr]
        assert len(largest_body(model, "a", ensure_ascii=True)) > limits.default


def test_every_override_names_a_route_that_exists(settings: Settings) -> None:
    """A template that matched no route would leave the route on the default, and the webhook
    would refuse every event over 64 KiB. Matched by method and exact template, as registered."""
    registered = {(m, r.path) for r in _api_routes(LIVE_APP) for m in r.methods}

    for route_limit in limits_from_settings(settings).routes:
        assert (route_limit.method, route_limit.path) in registered, route_limit


def test_the_webhook_reads_a_raw_body_and_has_its_own_limit(settings: Settings) -> None:
    """It takes no model, so the sweep above cannot see it: its limit is its own setting, and the
    only route that reads its body by hand (`test_request_schemas_forbid_extra.py`, guard f)."""
    [route] = [r for r in _api_routes(LIVE_APP) if r.path == WEBHOOK]
    limits = limits_from_settings(settings)

    assert route.body_field is None
    assert limits.for_request("POST", WEBHOOK) == settings.max_stripe_webhook_body_bytes
    assert settings.max_stripe_webhook_body_bytes > limits.default
    # The override is for POST alone, as the origin check's exemption is.
    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert limits.for_request(method, WEBHOOK) == limits.default


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param({"type": "string", "maxLength": 3}, '"aaa"', id="a string"),
        pytest.param({"type": "boolean"}, "false", id="a boolean is false, the longer"),
        pytest.param({"type": "integer", "minimum": 1, "maximum": 30}, "30", id="an integer"),
        pytest.param({"type": "integer", "minimum": -100, "maximum": 5}, "-100", id="a negative"),
        pytest.param(
            {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 1}},
            '["a","a"]',
            id="a list",
        ),
        pytest.param(
            {"anyOf": [{"type": "string", "maxLength": 3}, {"type": "null"}]},
            '"aaa"',
            id="the longer branch of a union",
        ),
        pytest.param(
            {"anyOf": [{"type": "string", "maxLength": 1}, {"type": "null"}]},
            "null",
            id="null when it is longer",
        ),
        pytest.param({"enum": ["ab", "abcd"]}, '"abcd"', id="the longest enum value"),
        pytest.param(
            {"type": "object", "properties": {"k": {"type": "boolean"}, "m": {"type": "null"}}},
            '{"k":false,"m":null}',
            id="every property",
        ),
        pytest.param(
            {"type": "string", "pattern": HEX_COLOR}, '"aaaaaaa"', id="a fixed-width pattern"
        ),
        pytest.param({"type": "string", "format": "email"}, f'"{"a" * 254}"', id="an email"),
    ],
)
def test_the_builder_makes_the_largest_value_a_schema_allows(
    schema: dict[str, Any], expected: str
) -> None:
    assert _text(_maximal(schema, {}, "a", True, "x"), True) == expected


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"type": "string", "pattern": "^x+$"},
        {"type": "array", "items": {"type": "boolean"}},
        {"type": "integer"},
        {"type": "number", "minimum": 0},
        {"type": "object", "properties": {"k": {"type": "string"}}},
        {"type": "array", "maxItems": 2, "items": {"type": "string"}},
        {"type": "weird"},
    ],
    ids=[
        "a string", "a pattern of unknown width", "a list", "an integer", "a number",
        "a string in an object", "a string in a list", "a type nobody wrote",
    ],
)
def test_the_builder_refuses_what_has_no_upper_bound(schema: dict[str, Any]) -> None:
    with pytest.raises(_Unbounded):
        _maximal(schema, {}, "a", True, "x")


def test_the_builder_agrees_with_the_number_the_docs_and_settings_give() -> None:
    """The figures `config.py`, `.env.example` and the runbook quote, from the real models."""
    [competitors] = [r for _, r in OPERATIONS if r.path.endswith("/competitors")]
    model = competitors.body_field.type_  # type: ignore[union-attr]

    assert len(largest_body(_model_of("PUT", OTHER_SITES), "a", ensure_ascii=True)) == 207_657
    assert len(largest_body(model, "a", ensure_ascii=True)) == 4_864
    assert len(largest_body(model, ASTRAL, ensure_ascii=True)) == 55_398


# ---------------------------------------------------------------------------
# the other-sites lists are bounded just above the service's caps, and the limit is sized to that
# ---------------------------------------------------------------------------


def test_the_computed_largest_body_is_the_one_built_from_the_schema() -> None:
    """`config.py` holds the other-sites limit to `largest_plain_body_bytes()` when the API starts.
    It is computed from the bounds in `other_sites_bounds`, with the keys written there; the size
    built from the request model's own JSON schema is the check that the two still say one thing
    (a key renamed in the model and not there, a bound changed in one place)."""
    model = _model_of("PUT", OTHER_SITES)

    assert largest_plain_body_bytes() == len(largest_body(model, "a", ensure_ascii=True)) == 207_657


def _assert_the_shipped_defaults_cover_the_schema() -> None:
    """What the API would silently RAISE at every start if it were false, so CI says it instead."""
    shipped = Settings(_env_file=None)
    raised = _raised(shipped)
    assert raised == [], f"the defaults are raised at every start: {raised}"
    assert shipped.max_other_sites_body_bytes >= largest_plain_body_bytes()


def test_the_shipped_defaults_cover_the_schema_so_the_api_raises_nothing_at_start() -> None:
    _assert_the_shipped_defaults_cover_the_schema()


@pytest.fixture
def raised_schema_bounds(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The schema's bounds put back to 1,000 a list, as they were before A8b. The size is computed
    (and cached) from `other_sites_bounds`, so the cache is cleared on the way in and out."""
    monkeypatch.setattr(other_sites_bounds, "MAX_SITES_PER_BRAND_LIST", 1000)
    monkeypatch.setattr(other_sites_bounds, "MAX_DISMISSED_LIST", 1000)
    largest_plain_body_bytes.cache_clear()
    try:
        yield
    finally:
        monkeypatch.undo()
        largest_plain_body_bytes.cache_clear()


def test_if_the_schema_bounds_are_raised_and_the_setting_is_not_the_limit_still_covers_them(
    raised_schema_bounds: None,
) -> None:
    """The floor holds AT RUNTIME. The setting is left at 512 KiB, the bounds are put up, and the
    API neither refuses to start nor refuses a body its schema accepts."""
    new_largest = largest_plain_body_bytes()
    assert new_largest == 3_074_857

    settings = Settings(_env_file=None)  # starts: the default is in its own range

    assert settings.max_other_sites_body_bytes == 524_288 < new_largest
    assert _raised(settings) == [(OTHER_SITES_SETTING, 524_288, new_largest, "below_schema")]
    limits = limits_from_settings(settings)
    assert limits.for_request("PUT", _other_sites_path()) >= new_largest
    assert configured_limits(settings).for_request("PUT", _other_sites_path()) < new_largest


def test_and_ci_still_fails_when_the_schema_bounds_are_raised_and_the_setting_is_not(
    raised_schema_bounds: None,
) -> None:
    """The shipped defaults are checked as configured, so a schema that has outgrown them is
    caught here though the running API would have raised the limit and gone on."""
    with pytest.raises(AssertionError, match="raised at every start"):
        _assert_the_shipped_defaults_cover_the_schema()


def test_the_other_sites_default_is_about_twice_the_largest_body_in_a_power_of_two_of_kib() -> None:
    limit = Settings(_env_file=None).max_other_sites_body_bytes
    largest = largest_plain_body_bytes()

    assert 2 * largest <= limit < 4 * largest, f"{limit} is not about twice {largest}"
    assert limit % 1024 == 0 and limit & (limit - 1) == 0, f"{limit} is not a power of two"
    assert limit >= Settings(_env_file=None).max_request_body_bytes


def test_the_schema_cuts_a_list_off_just_above_the_services_caps() -> None:
    """A little above, never at or below: the service answers a list of 21 to 50 (201 to 250
    dismissed) with its sentence naming the brand, and that is only reachable if the schema lets the
    list through. And not far above: that is the whole point of the bound."""
    assert MAX_SITES_PER_BRAND < MAX_SITES_PER_BRAND_LIST <= 3 * MAX_SITES_PER_BRAND
    assert MAX_DISMISSED < MAX_DISMISSED_LIST <= 3 * MAX_DISMISSED
    assert (MAX_SITES_PER_BRAND_LIST, MAX_DISMISSED_LIST) == (50, 250)


def test_the_openapi_document_carries_the_bounds() -> None:
    schemas = LIVE_APP.openapi()["components"]["schemas"]
    request = schemas["ReplaceOtherSitesRequest"]["properties"]

    assert request["client"]["maxItems"] == MAX_SITES_PER_BRAND_LIST == 50
    assert request["dismissed"]["maxItems"] == MAX_DISMISSED_LIST == 250
    assert request["rivals"]["maxItems"] == 10
    assert schemas["RivalSitesIn"]["properties"]["sites"]["maxItems"] == MAX_SITES_PER_BRAND_LIST


# ---------------------------------------------------------------------------
# the Content-Length reader
# ---------------------------------------------------------------------------


def _headers(*values: str) -> Headers:
    return Headers(raw=[(b"content-length", value.encode("latin-1")) for value in values])


def test_no_content_length_declares_nothing() -> None:
    assert declared_length(Headers(raw=[(b"host", b"x")])) is None


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("0",), 0),
        (("10",), 10),
        (("0010",), 10),
        ((" 10 ",), 10),
        (("\t10",), 10),
        (("9" * MAX_LENGTH_DIGITS,), int("9" * MAX_LENGTH_DIGITS)),
        (("10", "10"), 10),
        (("10", " 10"), 10),
    ],
    ids=["zero", "ten", "leading zeros", "padding", "tab", "the longest", "twice", "twice, padded"],
)
def test_a_length_is_digits_and_equal_copies_are_one_length(
    values: tuple[str, ...], expected: int
) -> None:
    assert declared_length(_headers(*values)) == expected


@pytest.mark.parametrize(
    "values",
    [
        ("",),
        ("   ",),
        ("abc",),
        ("-1",),
        ("+1",),
        ("1.5",),
        ("1e3",),
        ("0x10",),
        ("1 0",),
        ("10, 10",),
        ("\xb2",),
        ("9" * (MAX_LENGTH_DIGITS + 1),),
        ("9" * 5000,),
        ("10", "11"),
        ("10", "abc"),
    ],
    ids=[
        "empty", "blank", "letters", "negative", "plus", "decimal", "exponent", "hex", "space",
        "a list", "a superscript", "too many digits", "thousands of digits", "two values",
        "a value and a bad one",
    ],
)
def test_anything_else_is_not_a_length(values: tuple[str, ...]) -> None:
    with pytest.raises(MalformedContentLengthError):
        declared_length(_headers(*values))


async def test_a_malformed_content_length_is_a_400_and_the_route_never_runs(
    client: AsyncClient,
) -> None:
    """In-process, Starlette never reads the header and takes the request as it comes; uvicorn
    answers its own plain-text 400 before the app is called. Here, where a server passes one on, it
    is the API's `bad-request` problem. (The reader's cases are above.)"""
    calls = _spy(_app(client), "POST", LOGIN)

    resp = await client.post(
        LOGIN, content=_LOGIN_JSON, headers={**JSON, "Content-Length": "twelve"}
    )

    assert resp.status_code == 400, resp.text
    assert resp.headers["content-type"] == PROBLEM
    assert resp.json() == {
        "type": "/problems/bad-request",
        "title": "Bad request",
        "status": 400,
        "detail": "The Content-Length header of this request is not a valid length.",
        "instance": LOGIN,
    }
    for name, value in SECURITY_HEADERS:
        assert resp.headers.get_list(name.lower()) == [value], name
    assert calls == []


async def test_only_the_refusals_that_end_the_connection_say_so(
    short_deadline: float, client: AsyncClient, settings: Settings
) -> None:
    """RFC 9112 6.3: a request whose Content-Length cannot be trusted is answered 400 and the
    connection closed. RFC 9110 15.5.9: a 408 says the server has stopped waiting, so it closes
    too. The 413 is kept alive, on purpose: uvicorn drops the rest of an unread body once a
    response is complete, and a close can erase the answer for a client that is still sending."""
    bad_length = await client.post(
        LOGIN, content=_LOGIN_JSON, headers={**JSON, "Content-Length": "twelve"}
    )
    over = b" " * (settings.max_request_body_bytes + 1)
    too_large = await client.post(LOGIN, content=over, headers=JSON)
    counted = await client.post(LOGIN, content=_chunks(over, 8192), headers=JSON)
    stalled = await asyncio.wait_for(
        client.post(LOGIN, content=_stalls_after(b"{"), headers=JSON), GIVE_UP
    )

    assert (bad_length.status_code, too_large.status_code, counted.status_code) == (400, 413, 413)
    assert stalled.status_code == 408
    assert bad_length.headers.get_list("connection") == ["close"]
    assert stalled.headers.get_list("connection") == ["close"]
    assert "connection" not in too_large.headers, "the 413 of a declared length is kept alive"
    assert "connection" not in counted.headers, "the 413 of a counted one is kept alive"


# ---------------------------------------------------------------------------
# the matching of a route's template
# ---------------------------------------------------------------------------


def test_a_template_matches_the_paths_its_route_does_and_no_more() -> None:
    limits = BodyLimits(100, [RouteLimit("PUT", "/a/{id}/b.c", 500)])

    assert limits.for_request("PUT", "/a/x/b.c") == 500
    assert limits.for_request("put", "/a/x/b.c") == 500, "a method is matched in upper case"
    assert limits.for_request("PUT", "/a/ id /b.c") == 500, "a parameter is any one segment"
    for method, path in [
        ("POST", "/a/x/b.c"),  # another method
        ("PUT", "/a//b.c"),  # an empty segment
        ("PUT", "/a/x/y/b.c"),  # a parameter does not span a slash
        ("PUT", "/a/x/b.c/"),  # a trailing slash is another path
        ("PUT", "/a/x/bXc"),  # `.` is a dot, not "any character"
        ("PUT", "/b/x/b.c"),
        ("PUT", "/a/x/b.c?q=1"),  # a query is not part of a path
    ]:
        assert limits.for_request(method, path) == 100, (method, path)


def test_the_first_matching_override_wins_and_a_route_limit_is_not_a_default() -> None:
    limits = BodyLimits(
        10, [RouteLimit("POST", "/x", 20), RouteLimit("POST", "/x", 30), RouteLimit("GET", "/y", 5)]
    )

    assert limits.for_request("POST", "/x") == 20
    assert limits.for_request("GET", "/y") == 5, "an override may also be below the default"
    assert limits.for_request("GET", "/x") == 10
    assert limits.default == 10


# ---------------------------------------------------------------------------
# the middleware alone, around hand-made ASGI apps
# ---------------------------------------------------------------------------


def _scope(
    method: str = "POST", path: str = "/p", headers: tuple[tuple[str, str], ...] = ()
) -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "server": ("testserver", 80),
        "client": ("203.0.113.9", 4000),
    }


class Reader:
    """An ASGI app that reads its whole body, then answers 200. Records what it was given."""

    def __init__(self) -> None:
        self.calls = 0
        self.body = b""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            self.body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class Stream:
    """A client: hands out `chunks` one `receive` at a time, then waits for a disconnect."""

    def __init__(self, *chunks: bytes) -> None:
        self.queue = list(chunks)
        self.pulled = 0
        self.sent: list[Message] = []

    async def receive(self) -> Message:
        if not self.queue:
            return {"type": "http.disconnect"}
        self.pulled += 1
        body = self.queue.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(self.queue)}

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int | None:
        starts = [m for m in self.sent if m["type"] == "http.response.start"]
        return starts[0]["status"] if starts else None

    @property
    def document(self) -> dict[str, Any]:
        body = b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")
        return json.loads(body)


def _layer(
    inner: ASGIApp,
    default: int = 100,
    routes: tuple[RouteLimit, ...] = (),
    deadline: float = 30.0,
) -> BodyLimitMiddleware:
    return BodyLimitMiddleware(
        inner, limits=BodyLimits(default, routes), body_timeout_seconds=deadline
    )


async def test_a_declared_length_over_the_limit_reads_nothing_and_calls_nothing() -> None:
    inner = Reader()
    sent: list[Message] = []

    async def must_not_read() -> Message:
        raise AssertionError("the body was read")

    async def send(message: Message) -> None:
        sent.append(message)

    scope = _scope(headers=(("content-length", "101"),))
    await _layer(inner)(scope, must_not_read, send)

    assert inner.calls == 0
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == _document("/p")


async def test_a_declared_length_at_the_limit_is_let_through_with_its_messages_as_sent() -> None:
    inner = Reader()
    stream = Stream(b"x" * 60, b"y" * 40)

    await _layer(inner)(_scope(headers=(("content-length", "100"),)), stream.receive, stream.send)

    assert inner.calls == 1 and inner.body == b"x" * 60 + b"y" * 40
    assert stream.status == 200


async def test_a_stream_that_passes_the_limit_is_cut_at_the_chunk_that_did_it() -> None:
    inner = Reader()
    stream = Stream(b"x" * 60, b"y" * 41, b"never asked for")

    await _layer(inner)(_scope(), stream.receive, stream.send)

    assert stream.status == 413
    assert stream.document == _document("/p")
    assert stream.pulled == 2, "the layer read on after the count passed the limit"
    assert inner.body == b"x" * 60, "the app was handed the chunk that passed the limit"


async def test_the_count_is_of_the_bytes_and_a_chunk_that_ends_on_the_limit_passes() -> None:
    inner = Reader()
    stream = Stream(b"x" * 60, b"y" * 40)

    await _layer(inner)(_scope(), stream.receive, stream.send)

    assert stream.status == 200 and len(inner.body) == 100


async def test_a_lying_length_is_counted() -> None:
    inner = Reader()
    stream = Stream(b"x" * 200)

    await _layer(inner)(_scope(headers=(("content-length", "5"),)), stream.receive, stream.send)

    assert stream.status == 413


async def test_the_route_limit_is_the_one_a_request_is_measured_against() -> None:
    inner = Reader()
    big, small = Stream(b"x" * 300), Stream(b"x" * 300)
    layer = _layer(inner, 100, (RouteLimit("PUT", "/big/{id}", 400),))

    await layer(_scope("PUT", "/big/7", (("content-length", "300"),)), big.receive, big.send)
    await layer(_scope("PUT", "/small", (("content-length", "300"),)), small.receive, small.send)

    assert (big.status, small.status) == (200, 413)


async def test_a_malformed_length_is_a_400_and_reads_nothing() -> None:
    inner = Reader()
    sent: list[Message] = []

    async def must_not_read() -> Message:
        raise AssertionError("the body was read")

    async def send(message: Message) -> None:
        sent.append(message)

    await _layer(inner)(_scope(headers=(("content-length", "ten"),)), must_not_read, send)

    assert inner.calls == 0 and sent[0]["status"] == 400


async def test_messages_that_are_not_body_are_passed_on_and_not_counted() -> None:
    """A disconnect (what a streaming response waits for) is neither counted nor held back."""
    seen: list[Message] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(await receive())
        seen.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})

    sent: list[Message] = []
    queue: list[Message] = [
        {"type": "http.request", "body": b"abc", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await _layer(inner, 3)(_scope(), receive, send)

    assert seen == [
        {"type": "http.request", "body": b"abc", "more_body": False},
        {"type": "http.disconnect"},
    ]


@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_a_scope_that_is_not_http_passes_straight_through(scope_type: str) -> None:
    got: list[tuple[Scope, Receive, Send]] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        got.append((scope, receive, send))

    async def receive() -> Message:
        raise AssertionError("not for this test")

    async def send(message: Message) -> None:
        raise AssertionError("not for this test")

    scope: Scope = {"type": scope_type, "headers": [(b"content-length", b"999999")]}
    await _layer(inner, 10)(scope, receive, send)

    assert got == [(scope, receive, send)], "the scope, receive and send were wrapped"


async def test_an_app_that_lets_the_refusal_escape_is_answered_by_the_layer() -> None:
    """In FastAPI the exception handlers answer it. For an app that has none, nothing has been
    sent when it is raised, so the layer sends the 413 itself."""
    stream = Stream(b"x" * 101)

    async def raw_app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()  # raises; nothing handles it

    await _layer(raw_app)(_scope(), stream.receive, stream.send)

    assert stream.status == 413 and stream.document == _document("/p")


async def test_a_refusal_after_the_response_began_is_re_raised_not_answered_twice() -> None:
    stream = Stream(b"x" * 101)

    async def starts_then_reads(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()

    with pytest.raises(RequestBodyTooLarge):
        await _layer(starts_then_reads)(_scope(), stream.receive, stream.send)

    assert [m["type"] for m in stream.sent] == ["http.response.start"]


async def test_asked_again_after_the_refusal_the_app_is_told_the_body_is_gone() -> None:
    stream = Stream(b"x" * 101)
    asked: list[Message] = []

    async def asks_twice(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await receive()
        except RequestBodyTooLarge:
            asked.append(await receive())
            raise

    await _layer(asks_twice)(_scope(), stream.receive, stream.send)

    assert asked == [{"type": "http.disconnect"}]
    assert stream.status == 413


async def test_a_get_with_no_body_and_a_preflight_reach_the_app_as_they_came() -> None:
    for method in ("GET", "OPTIONS", "HEAD"):
        inner = Reader()
        stream = Stream(b"")

        await _layer(inner, 1)(_scope(method), stream.receive, stream.send)

        assert inner.calls == 1 and inner.body == b"" and stream.status == 200, method


# ---------------------------------------------------------------------------
# the deadline
#
# A body is given `REQUEST_BODY_TIMEOUT_SECONDS` from the first read of it. The tests that wait for
# it use a deadline of a fraction of a second, and give up after a few seconds of their own
# (`GIVE_UP`), so a regression that never answers a stalled body fails a test and does not hang the
# run. The wait must be CUT OFF at the deadline: the stalled client below never sends another byte,
# so a layer that only looked at the clock when a chunk arrived would wait for ever.
# ---------------------------------------------------------------------------


async def _stalls_after(
    *chunks: bytes, cancelled: list[bool] | None = None
) -> AsyncIterator[bytes]:
    """A client that sends `chunks` and then goes quiet, never sending or closing again.
    `cancelled` is told when the read that was waiting for it was cut off."""
    for chunk in chunks:
        yield chunk
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        if cancelled is not None:
            cancelled.append(True)
        raise


async def _drips(chunks: list[bytes], every: float) -> AsyncIterator[bytes]:
    """A slow client that keeps going: a chunk every `every` seconds."""
    for chunk in chunks:
        await asyncio.sleep(every)
        yield chunk


def _document_408(path: str) -> dict[str, Any]:
    return {
        "type": "/problems/request-timeout",
        "title": "Request timeout",
        "status": 408,
        "detail": TIMED_OUT,
        "instance": path,
    }


def _assert_timed_out(resp: HttpResponse, path: str) -> None:
    """The whole answer: the document, the six security headers, and the connection closing."""
    assert resp.status_code == 408, resp.text
    assert resp.headers["content-type"] == PROBLEM
    assert resp.json() == _document_408(path)
    assert resp.headers.get_list("connection") == ["close"]
    for name, value in SECURITY_HEADERS:
        assert resp.headers.get_list(name.lower()) == [value], name


async def test_a_body_that_stalls_is_answered_408_at_the_deadline_and_the_route_never_runs(
    short_deadline: float, client: AsyncClient
) -> None:
    calls = _spy(_app(client), "POST", LOGIN)
    cancelled: list[bool] = []
    started = time.monotonic()

    resp = await asyncio.wait_for(
        client.post(LOGIN, content=_stalls_after(b'{"email":', cancelled=cancelled), headers=JSON),
        GIVE_UP,
    )

    elapsed = time.monotonic() - started
    _assert_timed_out(resp, LOGIN)
    assert calls == [], "the route ran for a body that never arrived"
    assert 0.9 * DEADLINE <= elapsed < DEADLINE + 1.5, f"answered after {elapsed:.2f}s"
    assert cancelled == [True], "the read that was waiting was not cut off"


async def test_a_body_that_never_starts_is_answered_at_the_deadline_too(
    short_deadline: float, client: AsyncClient
) -> None:
    """The clock starts at the first read, not at the first byte: silence from the start counts."""
    calls = _spy(_app(client), "POST", LOGIN)

    resp = await asyncio.wait_for(
        client.post(LOGIN, content=_stalls_after(), headers=JSON), GIVE_UP
    )

    _assert_timed_out(resp, LOGIN)
    assert calls == []


async def test_a_body_that_stalls_with_a_declared_length_is_answered_408_not_waited_for(
    short_deadline: float, client: AsyncClient
) -> None:
    """It said 1,000 bytes and sent 9. The declared length is under the limit, so it is let in, and
    the deadline ends the wait for the rest."""
    calls = _spy(_app(client), "POST", LOGIN)

    resp = await asyncio.wait_for(
        client.post(
            LOGIN,
            content=_stalls_after(b'{"email":'),
            headers={**JSON, "Content-Length": "1000"},
        ),
        GIVE_UP,
    )

    _assert_timed_out(resp, LOGIN)
    assert calls == []


async def test_a_body_that_arrives_in_time_passes_with_a_short_deadline(
    short_deadline: float, client: AsyncClient
) -> None:
    calls = _spy(_app(client), "POST", LOGIN)

    resp = await asyncio.wait_for(
        client.post(LOGIN, content=_chunks(_padded(_LOGIN_JSON, 1500), 500), headers=JSON), GIVE_UP
    )

    assert resp.status_code == 204, resp.text
    assert calls == [1]


async def test_a_stalled_webhook_body_is_answered_408_through_its_raw_read(
    short_deadline: float, client: AsyncClient, webhook_reached: list[bytes]
) -> None:
    """The route reads `await request.body()` itself. The deadline applies to it as to any other."""
    resp = await asyncio.wait_for(
        client.post(WEBHOOK, content=_stalls_after(b'{"id":"evt_1",'), headers=JSON), GIVE_UP
    )

    _assert_timed_out(resp, WEBHOOK)
    assert webhook_reached == [], "verify_webhook was called with a body that never arrived"


async def test_a_get_and_a_preflight_and_an_empty_post_are_not_timed(
    short_deadline: float, client: AsyncClient, settings: Settings
) -> None:
    allowed = settings.cors_allow_origins[0]

    got = await asyncio.wait_for(client.get(f"{BASE}/health"), GIVE_UP)
    preflight = await asyncio.wait_for(
        client.options(
            f"{BASE}/clients",
            headers={
                "Origin": allowed,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        ),
        GIVE_UP,
    )
    empty = await asyncio.wait_for(client.post(LOGIN, content=b"", headers=JSON), GIVE_UP)

    assert got.status_code == 200
    assert preflight.status_code == 200 and preflight.text == "OK"
    assert empty.status_code == 422, "an empty body is whole at once: the route judges it"


async def test_the_deadline_is_the_setting_and_is_read_when_the_app_is_built(
    short_deadline: float, client: AsyncClient
) -> None:
    [layer] = [m for m in _app(client).user_middleware if m.cls is BodyLimitMiddleware]

    assert layer.kwargs["body_timeout_seconds"] == DEADLINE


class Staller:
    """A client for the layer alone: hands out `chunks` (each with more to come), then goes quiet
    until it is cut off. `pause` is how long it takes over each chunk. With `whole`, the last chunk
    ends the body, and a read after that is a wait to hear that the client has gone: it takes
    `wait_after` seconds, then the client disconnects."""

    def __init__(
        self, *chunks: bytes, pause: float = 0.0, whole: bool = False, wait_after: float = 0.0
    ) -> None:
        self.queue = list(chunks)
        self.pause = pause
        self.whole = whole
        self.wait_after = wait_after
        self.sent: list[Message] = []
        self.cut_off = False

    async def receive(self) -> Message:
        if self.queue:
            if self.pause:
                await asyncio.sleep(self.pause)
            body = self.queue.pop(0)
            more = bool(self.queue) or not self.whole
            return {"type": "http.request", "body": body, "more_body": more}
        if self.whole:
            await asyncio.sleep(self.wait_after)
            return {"type": "http.disconnect"}
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cut_off = True
            raise
        raise AssertionError("unreachable")

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int | None:
        starts = [m for m in self.sent if m["type"] == "http.response.start"]
        return starts[0]["status"] if starts else None

    @property
    def headers(self) -> dict[str, str]:
        starts = [m for m in self.sent if m["type"] == "http.response.start"]
        return {k.decode(): v.decode() for k, v in starts[0]["headers"]} if starts else {}

    @property
    def document(self) -> dict[str, Any]:
        body = b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")
        return json.loads(body)


async def _drive(layer: BodyLimitMiddleware, client: Staller, scope: Scope | None = None) -> None:
    """Run `layer` over `client`, giving up after `GIVE_UP`: a stall must not hang the run."""
    await asyncio.wait_for(layer(scope or _scope(), client.receive, client.send), GIVE_UP)


async def test_the_layer_alone_cuts_a_stalled_read_off_at_the_deadline() -> None:
    inner = Reader()
    client = Staller(b"x" * 10)
    started = time.monotonic()

    await _drive(_layer(inner, deadline=0.3), client)

    elapsed = time.monotonic() - started
    assert client.status == 408 and client.document == _document_408("/p")
    assert client.headers["connection"] == "close"
    assert client.cut_off, "the read that was waiting was not cut off"
    assert 0.27 <= elapsed < 1.5, f"answered after {elapsed:.2f}s"
    assert inner.body == b"x" * 10, "the app was handed what had arrived, and no more"


async def test_a_slow_body_that_keeps_coming_inside_the_deadline_passes() -> None:
    """Six chunks, 0.1 s apart: 0.6 s of body against a deadline of 1.5 s. Slow is not stalled."""
    inner = Reader()
    client = Staller(*[b"x" * 10] * 6, pause=0.1, whole=True)

    await _drive(_layer(inner, deadline=1.5), client)

    assert client.status == 200 and len(inner.body) == 60


async def test_a_body_that_keeps_coming_but_is_not_whole_by_the_deadline_is_cut_off() -> None:
    """The deadline is for the BODY, not for each chunk: a client that drips a chunk every 0.1 s
    forever is answered 408 at 0.35 s, though no single wait was longer than the deadline."""
    inner = Reader()
    client = Staller(*[b"x" * 10] * 50, pause=0.1)

    await _drive(_layer(inner, deadline=0.35), client)

    assert client.status == 408
    assert 0 < len(inner.body) < 500


async def test_the_clock_stops_when_the_body_is_whole() -> None:
    """After a whole body the app waits to hear the client has gone (a streaming response does).
    That wait is not timed: 0.4 s here, against a deadline of 0.15 s."""
    seen: list[Message] = []

    async def waits_for_the_client_to_go(scope: Scope, receive: Receive, send: Send) -> None:
        while (message := await receive())["type"] == "http.request":
            if not message.get("more_body"):
                break
        seen.append(await receive())  # the wait for the disconnect
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    client = Staller(b"x" * 10, whole=True, wait_after=0.4)

    await _drive(_layer(waits_for_the_client_to_go, deadline=0.15), client)

    assert client.status == 200
    assert seen == [{"type": "http.disconnect"}]


async def test_a_slow_app_does_not_use_up_the_time() -> None:
    """The clock starts at the first read of the body. An app that takes 0.4 s to ask for it, with
    a deadline of 0.15 s, finds the body there and whole."""
    inner = Reader()

    async def late(scope: Scope, receive: Receive, send: Send) -> None:
        await asyncio.sleep(0.4)
        await inner(scope, receive, send)

    client = Staller(b"x" * 10, whole=True)

    await _drive(_layer(late, deadline=0.15), client)

    assert client.status == 200 and inner.body == b"x" * 10


async def test_a_request_with_no_body_is_not_timed() -> None:
    inner = Reader()
    client = Staller(b"", whole=True)

    await _drive(_layer(inner, deadline=0.05), client, _scope("GET"))

    assert client.status == 200 and inner.calls == 1


async def test_asked_again_after_the_timeout_the_app_is_told_the_body_is_gone() -> None:
    client = Staller(b"x")
    asked: list[Message] = []

    async def asks_twice(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            while True:
                await receive()
        except RequestBodyTimeout:
            asked.append(await receive())
            raise

    await _drive(_layer(asks_twice, deadline=0.2), client)

    assert asked == [{"type": "http.disconnect"}]
    assert client.status == 408


async def test_a_timeout_after_the_response_began_is_re_raised_not_answered_twice() -> None:
    client = Staller(b"x")

    async def starts_then_reads(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            await receive()

    with pytest.raises(RequestBodyTimeout):
        await _drive(_layer(starts_then_reads, deadline=0.2), client)

    assert [m["type"] for m in client.sent] == ["http.response.start"]


# ---------------------------------------------------------------------------
# the log line
# ---------------------------------------------------------------------------


def _refusals(events: list[Any]) -> list[dict[str, Any]]:
    return [{k: v for k, v in e.items() if k != "log_level"} for e in events if e["event"] == EVENT]


async def test_a_refusal_is_logged_once_with_the_route_the_limit_and_the_declared_length(
    small_default: int, client: AsyncClient
) -> None:
    assert _limits_of(_app(client)).default == SMALL

    with structlog.testing.capture_logs() as events:
        await client.post(LOGIN, content=_padded(_LOGIN_JSON, SMALL + 1), headers=JSON)

    assert _refusals(events) == [
        {
            "event": EVENT,
            "method": "POST",
            "route": LOGIN,
            "limit": SMALL,
            "declared_length": SMALL + 1,
        }
    ]
    [line] = [e for e in events if e["event"] == EVENT]
    assert line["log_level"] == "warning"


async def test_a_counted_refusal_is_logged_once_and_declares_no_length(
    small_default: int, client: AsyncClient
) -> None:
    assert _limits_of(_app(client)).default == SMALL

    with structlog.testing.capture_logs() as events:
        await client.post(LOGIN, content=_chunks(_padded(_LOGIN_JSON, 5 * SMALL)), headers=JSON)

    assert _refusals(events) == [{"event": EVENT, "method": "POST", "route": LOGIN, "limit": SMALL}]


async def test_a_lying_length_is_logged_with_what_it_declared(
    small_default: int, client: AsyncClient
) -> None:
    assert _limits_of(_app(client)).default == SMALL

    with structlog.testing.capture_logs() as events:
        await client.post(
            LOGIN,
            content=_chunks(_padded(_LOGIN_JSON, 5 * SMALL)),
            headers={**JSON, "Content-Length": "40"},
        )

    assert _refusals(events) == [
        {"event": EVENT, "method": "POST", "route": LOGIN, "limit": SMALL, "declared_length": 40}
    ]


async def test_the_log_names_the_template_and_not_the_path_and_an_unknown_path_has_none(
    client: AsyncClient, settings: Settings
) -> None:
    client_id = ids.new_id(ids.CLIENT)
    limit = settings.max_request_body_bytes

    with structlog.testing.capture_logs() as events:
        await client.put(
            f"{BASE}/clients/{client_id}/competitors", content=b" " * (limit + 1), headers=JSON
        )
        await client.post(
            f"{BASE}/no-such-route/secret-looking-segment", content=b" " * (limit + 1)
        )

    first, second = _refusals(events)
    assert first["route"] == f"{BASE}/clients/{{clientId}}/competitors"
    assert second["route"] is None
    assert client_id not in json.dumps(events, default=str)
    assert "secret-looking-segment" not in json.dumps(events, default=str)


async def test_the_log_line_carries_no_body_no_header_and_no_address(
    small_default: int,
    client: AsyncClient,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert _limits_of(_app(client)).default == SMALL
    body = _padded(b'{"email":"body-sentinel@logs.example","password":"p"}', SMALL + 1)
    caplog.set_level("DEBUG")
    capsys.readouterr()

    with structlog.testing.capture_logs() as events:
        await client.post(
            LOGIN, content=body, headers={**JSON, "X-Forwarded-For": "198.51.100.77",
                                           "X-Sentinel": "header-sentinel"},
        )
    await client.post(LOGIN, content=body, headers={**JSON, "X-Sentinel": "header-sentinel"})

    printed = capsys.readouterr().out
    assert EVENT in printed, "nothing was printed, so this proves nothing"
    for text in (json.dumps(events, default=str), printed, caplog.text):
        for sentinel in ("body-sentinel", "header-sentinel", "198.51.100.77", "127.0.0.1"):
            assert sentinel not in text, sentinel


async def test_a_declared_length_too_long_to_be_one_is_not_logged_at_any_length(
    client: AsyncClient,
) -> None:
    """The 400 for a length that is not one is not logged, so a header the sender chose cannot
    make a log line of its own length."""
    with structlog.testing.capture_logs() as events:
        resp = await client.post(
            LOGIN, content=_LOGIN_JSON, headers={**JSON, "Content-Length": "9" * 5000}
        )

    assert resp.status_code == 400
    assert _refusals(events) == []
    assert "9" * 100 not in json.dumps(events, default=str)


def _timeouts(events: list[Any]) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in e.items() if k != "log_level"}
        for e in events
        if e["event"] == TIMEOUT_EVENT
    ]


async def test_a_timeout_is_logged_once_with_the_route_the_deadline_and_the_bytes_so_far(
    short_deadline: float, client: AsyncClient
) -> None:
    first = b'{"email":'

    with structlog.testing.capture_logs() as events:
        await asyncio.wait_for(
            client.post(LOGIN, content=_stalls_after(first), headers=JSON), GIVE_UP
        )

    assert _timeouts(events) == [
        {
            "event": TIMEOUT_EVENT,
            "method": "POST",
            "route": LOGIN,
            "deadline_seconds": DEADLINE,
            "bytes_received": len(first),
        }
    ]
    [line] = [e for e in events if e["event"] == TIMEOUT_EVENT]
    assert line["log_level"] == "warning"
    assert type(line["bytes_received"]) is int
    assert [e for e in events if e["event"] == EVENT] == [], "a 408 is not also a 413"


async def test_a_timeout_logs_the_length_the_request_declared(
    short_deadline: float, client: AsyncClient
) -> None:
    with structlog.testing.capture_logs() as events:
        await asyncio.wait_for(
            client.post(
                LOGIN,
                content=_stalls_after(b"{"),
                headers={**JSON, "Content-Length": "1000"},
            ),
            GIVE_UP,
        )

    assert _timeouts(events) == [
        {
            "event": TIMEOUT_EVENT,
            "method": "POST",
            "route": LOGIN,
            "deadline_seconds": DEADLINE,
            "bytes_received": 1,
            "declared_length": 1000,
        }
    ]


async def test_the_timeout_line_carries_no_body_no_header_and_no_address(
    short_deadline: float,
    client: AsyncClient,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = b'{"email":"body-sentinel@logs.example",'
    headers = {**JSON, "X-Forwarded-For": "198.51.100.77", "X-Sentinel": "header-sentinel"}
    caplog.set_level("DEBUG")
    capsys.readouterr()

    with structlog.testing.capture_logs() as events:
        await asyncio.wait_for(
            client.post(LOGIN, content=_stalls_after(first), headers=headers), GIVE_UP
        )
    await asyncio.wait_for(
        client.post(LOGIN, content=_stalls_after(first), headers=headers), GIVE_UP
    )

    printed = capsys.readouterr().out
    assert TIMEOUT_EVENT in printed, "nothing was printed, so this proves nothing"
    for text in (json.dumps(events, default=str), printed, caplog.text):
        for sentinel in ("body-sentinel", "header-sentinel", "198.51.100.77", "127.0.0.1"):
            assert sentinel not in text, sentinel


async def test_a_timeout_in_the_layer_alone_is_logged_with_its_own_numbers() -> None:
    client = Staller(b"x" * 7)

    with structlog.testing.capture_logs() as events:
        await _drive(_layer(Reader(), deadline=0.2), client)

    assert _timeouts(events) == [
        {
            "event": TIMEOUT_EVENT,
            "method": "POST",
            "route": None,
            "deadline_seconds": 0.2,
            "bytes_received": 7,
        }
    ]

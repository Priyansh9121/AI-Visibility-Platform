"""A write must come from the web app — 2026-10-01.

CORS decides which pages may READ an answer from this API. It does not stop a
page on another site from SENDING a write: the browser attaches the visitor's
cookies to any request aimed at this host, and the write has happened by the time
the page is told it may not look at the reply.

`origin_check.py` closes that. A POST, PUT, PATCH or DELETE is refused with
`403 /problems/origin-not-allowed` when

- its `Origin` header is PRESENT and is not exactly one of `CORS_ALLOW_ORIGINS`
  (`null` and an empty value included), with or without a session: a sign-in,
  sign-up, reset, invitation or Google completion sent by another site is a write
  too (founder decision, 2026-10-01); or
- it has NO `Origin` and carries the session cookie.

Not refused: a write with no `Origin` and no session cookie (Stripe, curl, a
server), and the Stripe webhook, whatever it carries.

Every behaviour here goes through `create_app`, on real routes, so the tests
fail when the middleware is not registered — not only when its logic is wrong.
The suite's own `client` sends the allowed origin (`conftest.TEST_ORIGIN`), as a
browser on the web app does; a second client with another origin, or none, is
built by `_page` (with the session) or `_visitor` (without).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from fastapi import FastAPI, WebSocket
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import WebSocketRoute

from avp_api import ids
from avp_api.config import Settings, get_settings
from avp_api.deps import db_session, redis_client
from avp_api.errors import PROBLEM_CONTENT_TYPE
from avp_api.main import create_app
from avp_api.models import Agency, Invitation, PasswordResetToken, User, UserStatus
from avp_api.redis_client import get_redis
from avp_api.security import token_digest
from avp_api.services.google_oauth import GoogleFlowStore, GoogleIdentity

BASE = "/api/v1"
LOGIN = f"{BASE}/auth/login"
WEBHOOK = f"{BASE}/billing/webhook"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
OWNER = "owner@origin.example"
EMAIL = "someone@origin.example"

FOREIGN = "https://evil.example"
# Stands for the allowed origin in a parametrize list, which is built before
# `settings` exists; the test swaps it for `settings.cors_allow_origins[0]`.
WEB = "<the web app's origin>"
SESSION = "avp_session=x"
REFUSED_TYPE = "/problems/origin-not-allowed"
REFUSED_DETAIL = "Requests that change data must come from the AVP web app."

# Written out rather than read from the code, so editing the code cannot quietly
# edit what these tests pin.
NEW_CLIENT = {"url": "helpscout.com", "classify": False}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _sign_up(client: AsyncClient, email: str = OWNER) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Origin Check Agency",
            "fullName": "Owner Person",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@asynccontextmanager
async def _page(client: AsyncClient, origin: str | None) -> AsyncIterator[AsyncClient]:
    """The same signed-in browser, on another page: it holds `client`'s cookies
    and sends `origin` with what it sends — or no Origin header at all."""
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app),  # noqa: SLF001
        base_url="http://testserver",
        cookies=client.cookies,
        headers={} if origin is None else {"Origin": origin},
    ) as page:
        yield page


@asynccontextmanager
async def _visitor(client: AsyncClient, origin: str | None) -> AsyncIterator[AsyncClient]:
    """A browser with no session: no cookies at all, and `origin` (or none)."""
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app),  # noqa: SLF001
        base_url="http://testserver",
        headers={} if origin is None else {"Origin": origin},
    ) as visitor:
        yield visitor


def _assert_refused(resp: Response, path: str) -> None:
    """The whole refusal: status, content type and every member of the document."""
    assert resp.status_code == 403, resp.text
    assert resp.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert resp.json() == {
        "type": REFUSED_TYPE,
        "title": "Origin not allowed",
        "status": 403,
        "detail": REFUSED_DETAIL,
        "instance": path,
    }


async def _clients(client: AsyncClient) -> list[dict[str, Any]]:
    return (await client.get(f"{BASE}/clients")).json()["data"]


async def _competitor_names(client: AsyncClient, client_id: str) -> list[str]:
    body = (await client.get(f"{BASE}/clients/{client_id}/competitors")).json()
    return [c["name"] for c in body["competitors"]]


async def _seats_used(client: AsyncClient) -> int:
    return (await client.get(f"{BASE}/auth/me")).json()["seats"]["used"]


def _sessions(engine) -> async_sessionmaker:  # noqa: ANN001
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _accent(engine, agency_id: str) -> str | None:  # noqa: ANN001
    """Read straight from Postgres, so this observes the state, not the route."""
    async with _sessions(engine)() as s:
        return (
            await s.execute(select(Agency.accent_color).where(Agency.id == agency_id))
        ).scalar_one()


async def _user(engine, email: str) -> User | None:  # noqa: ANN001
    async with _sessions(engine)() as s:
        return (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _live_sessions(settings: Settings, user_id: str) -> int:
    """Session records Redis holds for a user, read directly rather than through
    the session store. Through `get_redis(settings)`: the pool must be the TEST
    Redis whether or not the app has made a request yet."""
    redis = get_redis(settings)
    digests = await redis.smembers(f"user-sessions:{user_id}")
    live = {d for d in digests if await redis.exists(f"sess:{d}")}
    return len(live)


def _app(settings: Settings, **changes: Any) -> FastAPI:
    """The real app under changed settings, with no datastore behind it.

    The probe for these apps is `_login`: a login with an empty body, which the
    route answers `422` before it asks anything of Postgres or Redis. A request
    that gets that answer got past the origin check; a `403` is the check.
    """
    custom = settings.model_copy(update=changes)
    app = create_app(custom)
    app.dependency_overrides[get_settings] = lambda: custom
    app.dependency_overrides[db_session] = lambda: None
    app.dependency_overrides[redis_client] = lambda: None
    return app


async def _login(
    app: FastAPI,
    *,
    cookie: str | list[str] | None = SESSION,
    origin: str | None = None,
) -> Response:
    headers: list[tuple[str, str]] = []
    for line in [cookie] if isinstance(cookie, str) else cookie or []:
        headers.append(("Cookie", line))
    if origin is not None:
        headers.append(("Origin", origin))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        return await http.post(LOGIN, json={}, headers=headers)


def _reached_the_route(resp: Response) -> bool:
    return resp.status_code == 422 and resp.json()["type"] == "/problems/validation-failed"


async def _call(
    app: FastAPI, *, method: str, path: str, headers: dict[str, str]
) -> tuple[int, Any]:
    """The app called as a server calls it, with the method exactly as given.

    httpx upper-cases a method before it builds the scope, so a lowercase one can
    only be sent this way.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body)


# ---------------------------------------------------------------------------
# a refused write changes nothing; the same write from the web app runs
# ---------------------------------------------------------------------------


async def test_a_post_from_another_origin_creates_nothing(client: AsyncClient) -> None:
    await _sign_up(client)

    async with _page(client, FOREIGN) as page:
        refused = await page.post(f"{BASE}/clients", json=NEW_CLIENT)
    _assert_refused(refused, f"{BASE}/clients")
    assert await _clients(client) == []

    # The same request, on the same session, from the web app's origin.
    created = await client.post(f"{BASE}/clients", json=NEW_CLIENT)
    assert created.status_code == 201, created.text
    assert len(await _clients(client)) == 1


async def test_a_put_from_another_origin_replaces_nothing(client: AsyncClient) -> None:
    await _sign_up(client)
    cid = (await client.post(f"{BASE}/clients", json=NEW_CLIENT)).json()["id"]
    assert (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).status_code == 201
    before = await _competitor_names(client, cid)
    pick = {"competitors": [{"name": "Operator Pick", "domain": "operatorpick.example"}]}

    async with _page(client, FOREIGN) as page:
        refused = await page.put(f"{BASE}/clients/{cid}/competitors", json=pick)
    _assert_refused(refused, f"{BASE}/clients/{cid}/competitors")
    assert await _competitor_names(client, cid) == before
    assert "Operator Pick" not in before

    replaced = await client.put(f"{BASE}/clients/{cid}/competitors", json=pick)
    assert replaced.status_code == 200, replaced.text
    assert await _competitor_names(client, cid) == ["Operator Pick"]


async def test_a_patch_from_another_origin_sets_nothing(client: AsyncClient, engine) -> None:  # noqa: ANN001
    agency_id = (await _sign_up(client))["agency"]["id"]
    path = f"{BASE}/agencies/{agency_id}/branding"

    async with _page(client, FOREIGN) as page:
        refused = await page.patch(path, json={"accentColor": "#aa0000"})
    _assert_refused(refused, path)
    assert await _accent(engine, agency_id) is None

    patched = await client.patch(path, json={"accentColor": "#aa0000"})
    assert patched.status_code == 200, patched.text
    assert await _accent(engine, agency_id) == "#aa0000"


async def test_a_delete_from_another_origin_removes_nothing(client: AsyncClient) -> None:
    agency_id = (await _sign_up(client))["agency"]["id"]
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations",
        json={"email": "seat@origin.example", "role": "member"},
    )
    assert invited.status_code == 201, invited.text
    user_id = invited.json()["user"]["id"]
    assert await _seats_used(client) == 2

    async with _page(client, FOREIGN) as page:
        refused = await page.delete(f"{BASE}/users/{user_id}")
    _assert_refused(refused, f"{BASE}/users/{user_id}")
    assert await _seats_used(client) == 2

    assert (await client.delete(f"{BASE}/users/{user_id}")).status_code == 204
    assert await _seats_used(client) == 1


# ---------------------------------------------------------------------------
# the credential routes: no session needed to be refused — 2026-10-01
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """One credential route, set up so that a good request changes something."""

    path: str
    body: dict[str, Any]
    ok: int  # what the route answers a good request with
    changed: Callable[[], Awaitable[bool]]  # whether the write took effect


async def _sign_up_route(client: AsyncClient, engine, settings: Settings) -> Scenario:  # noqa: ANN001
    async def changed() -> bool:
        return await _user(engine, EMAIL) is not None

    body = {
        "agencyName": "Origin Check Agency",
        "fullName": "Some One",
        "email": EMAIL,
        "password": PASSWORD,
    }
    return Scenario(f"{BASE}/auth/sign-up", body, 201, changed)


async def _login_route(client: AsyncClient, engine, settings: Settings) -> Scenario:  # noqa: ANN001
    user_id = (await _sign_up(client, EMAIL))["user"]["id"]
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204

    async def changed() -> bool:
        return await _live_sessions(settings, user_id) > 0

    return Scenario(LOGIN, {"email": EMAIL, "password": PASSWORD}, 200, changed)


async def _google_complete_route(client: AsyncClient, engine, settings: Settings) -> Scenario:  # noqa: ANN001
    store = GoogleFlowStore(get_redis(settings), settings)
    ticket = await store.issue_ticket(
        GoogleIdentity(
            sub="google-sub-origin", email=EMAIL, email_verified=True, name="Some One",
            nonce="not-used-by-completion",
        )
    )

    async def changed() -> bool:
        # The agency and its owner exist, or the ticket has been spent.
        return await _user(engine, EMAIL) is not None or await store.peek_ticket(ticket) is None

    body = {"ticket": ticket, "agencyName": "Origin Check Agency", "fullName": "Some One"}
    return Scenario(f"{BASE}/auth/google/complete", body, 201, changed)


async def _reset_request_route(client: AsyncClient, engine, settings: Settings) -> Scenario:  # noqa: ANN001
    user_id = (await _sign_up(client, EMAIL))["user"]["id"]
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204

    async def changed() -> bool:
        async with _sessions(engine)() as s:
            rows = await s.scalar(
                select(func.count()).select_from(PasswordResetToken).where(
                    PasswordResetToken.user_id == user_id
                )
            )
        return bool(rows)

    return Scenario(f"{BASE}/auth/reset-password/request", {"email": EMAIL}, 200, changed)


async def _reset_confirm_route(client: AsyncClient, engine, settings: Settings) -> Scenario:  # noqa: ANN001
    user_id = (await _sign_up(client, EMAIL))["user"]["id"]
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204
    raw = "origin-check-reset-" + "x" * 32
    async with _sessions(engine)() as s:
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET),
                user_id=user_id,
                token_digest=token_digest(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()
    before = (await _user(engine, EMAIL)).password_hash

    async def changed() -> bool:
        return (await _user(engine, EMAIL)).password_hash != before

    body = {"token": raw, "newPassword": NEW_PASSWORD}
    return Scenario(f"{BASE}/auth/reset-password/confirm", body, 200, changed)


async def _accept_invitation_route(client: AsyncClient, engine, settings: Settings) -> Scenario:  # noqa: ANN001
    agency_id = (await _sign_up(client))["agency"]["id"]
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": EMAIL, "role": "member"}
    )
    assert invited.status_code == 201, invited.text
    # The raw token is never returned, so the test files its own digest.
    token = "origin-check-invite-" + "z" * 32
    async with _sessions(engine)() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == EMAIL,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(token)
        await s.commit()
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204

    async def changed() -> bool:
        return (await _user(engine, EMAIL)).status is not UserStatus.INVITED

    body = {"token": token, "fullName": "Jo Iner", "password": NEW_PASSWORD}
    return Scenario(f"{BASE}/auth/invitations/accept", body, 200, changed)


CREDENTIAL_ROUTES = {
    "sign-up": _sign_up_route,
    "login": _login_route,
    "google-complete": _google_complete_route,
    "reset-request": _reset_request_route,
    "reset-confirm": _reset_confirm_route,
    "invitation-accept": _accept_invitation_route,
}


@pytest.mark.parametrize("route", list(CREDENTIAL_ROUTES))
@pytest.mark.parametrize(
    ("origin", "works"),
    [
        pytest.param(FOREIGN, False, id="another-site"),
        pytest.param("null", False, id="null"),
        pytest.param(None, True, id="no-origin-as-before"),
        pytest.param(WEB, True, id="the-web-app"),
    ],
)
async def test_the_credential_routes_refuse_another_origin_with_no_session_at_all(
    client: AsyncClient,
    engine,  # noqa: ANN001
    settings: Settings,
    route: str,
    origin: str | None,
    works: bool,
) -> None:
    """Sign-in, sign-up, Google completion, both reset steps and invitation
    acceptance, from a browser holding no cookie: another site's Origin and
    `null` are refused and change nothing; no Origin at all, and the web app's,
    work exactly as they did."""
    scenario = await CREDENTIAL_ROUTES[route](client, engine, settings)
    assert not await scenario.changed(), "the scenario starts changed, so it proves nothing"
    if origin == WEB:
        origin = settings.cors_allow_origins[0]

    async with _visitor(client, origin) as visitor:
        resp = await visitor.post(scenario.path, json=scenario.body)

    if works:
        assert resp.status_code == scenario.ok, resp.text
        assert await scenario.changed()
    else:
        _assert_refused(resp, scenario.path)
        assert not await scenario.changed()


async def test_a_sign_in_from_another_origin_is_refused_with_nobody_signed_in(
    client: AsyncClient,
) -> None:
    """This used to be `test_a_write_with_no_session_is_not_refused`. It flips,
    on the founder's decision of 2026-10-01: a present, unlisted Origin is
    refused whether or not a session rides with it."""
    async with _visitor(client, FOREIGN) as visitor:
        resp = await visitor.post(
            LOGIN, json={"email": "nobody@origin.example", "password": PASSWORD}
        )

    _assert_refused(resp, LOGIN)


async def test_a_write_with_no_origin_and_no_session_is_not_refused(client: AsyncClient) -> None:
    """Stripe, curl and server-side callers send no Origin and no cookie."""
    async with _visitor(client, None) as visitor:
        resp = await visitor.post(
            LOGIN, json={"email": "nobody@origin.example", "password": PASSWORD}
        )

    assert resp.status_code == 401
    assert resp.json()["type"] == "/problems/invalid-credentials"


# ---------------------------------------------------------------------------
# what counts as the web app's origin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        pytest.param(None, id="no-origin-header"),
        pytest.param("null", id="null"),
        pytest.param("", id="empty"),
        pytest.param(FOREIGN, id="another-site"),
        pytest.param("http://localhost:3000.evil.example", id="the-allowed-host-as-a-prefix"),
        pytest.param("http://evil.example/http://localhost:3000", id="the-allowed-one-in-a-path"),
        pytest.param("https://localhost:3000", id="another-scheme"),
        pytest.param("http://localhost:3001", id="another-port"),
        pytest.param("http://localhost:3000/", id="a-trailing-slash-on-the-request"),
    ],
)
async def test_only_a_listed_origin_may_write(client: AsyncClient, origin: str | None) -> None:
    """With a session: exactly equal, or refused. A missing Origin, `null` and
    look-alikes included."""
    await _sign_up(client)

    async with _page(client, origin) as page:
        resp = await page.post(f"{BASE}/clients", json=NEW_CLIENT)

    _assert_refused(resp, f"{BASE}/clients")
    assert await _clients(client) == []


@pytest.mark.parametrize(
    ("cookie", "origin", "refused"),
    [
        pytest.param(SESSION, WEB, False, id="signed-in-on-the-web-app"),
        pytest.param(None, WEB, False, id="signed-out-on-the-web-app"),
        pytest.param(SESSION, FOREIGN, True, id="signed-in-on-another-site"),
        pytest.param(None, FOREIGN, True, id="signed-out-on-another-site"),
        pytest.param(SESSION, "null", True, id="signed-in-with-null"),
        pytest.param(None, "null", True, id="signed-out-with-null"),
        pytest.param(SESSION, None, True, id="a-session-and-no-origin"),
        pytest.param(None, None, False, id="no-session-and-no-origin"),
    ],
)
async def test_the_rule_in_full(
    settings: Settings, cookie: str | None, origin: str | None, refused: bool
) -> None:
    """An Origin that is present must be listed, session or not. One that is
    missing is refused only when the session cookie rides with the request."""
    if origin == WEB:
        origin = settings.cors_allow_origins[0]

    resp = await _login(_app(settings), cookie=cookie, origin=origin)

    if refused:
        _assert_refused(resp, LOGIN)
    else:
        assert _reached_the_route(resp), resp.text


async def test_the_refusal_is_the_same_problem_document_the_app_gives_elsewhere(
    client: AsyncClient,
) -> None:
    """Not a shape of the middleware's own: the members and the content type are
    the ones the app's exception handlers produce for any other error."""
    own = await client.post(f"{BASE}/clients", json=NEW_CLIENT)  # no session yet
    assert own.status_code == 401
    assert own.json()["type"] == "/problems/authentication-required"

    await _sign_up(client)
    async with _page(client, FOREIGN) as page:
        refusal = await page.post(f"{BASE}/clients", json=NEW_CLIENT)

    assert set(refusal.json()) == set(own.json())
    assert refusal.headers["content-type"] == own.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert refusal.json()["instance"] == own.json()["instance"] == f"{BASE}/clients"


@pytest.mark.parametrize(
    ("configured", "origin", "allowed"),
    [
        pytest.param(["https://app.example.com"], "https://app.example.com", True, id="listed"),
        pytest.param(
            ["https://app.example.com/"], "https://app.example.com", True,
            id="listed-with-a-trailing-slash",
        ),
        pytest.param(
            ["https://a.example", "https://b.example"], "https://b.example", True,
            id="any-of-several",
        ),
        pytest.param(
            ["https://a.example", "https://b.example"], "https://c.example", False,
            id="none-of-several",
        ),
        pytest.param([], "https://app.example.com", False, id="nothing-listed"),
        pytest.param([""], "", False, id="a-blank-entry-matches-no-origin-not-even-a-blank-one"),
        pytest.param(["*"], FOREIGN, False, id="a-wildcard-is-not-an-origin"),
        pytest.param(["null"], "null", False, id="null-is-never-an-origin-even-when-listed"),
        pytest.param(
            ["null", "https://app.example.com"], "https://app.example.com", True,
            id="the-rest-of-the-list-still-counts-beside-null",
        ),
    ],
)
async def test_the_allowed_origins_are_the_configured_ones(
    settings: Settings, configured: list[str], origin: str, allowed: bool
) -> None:
    resp = await _login(_app(settings, cors_allow_origins=configured), cookie=None, origin=origin)

    if allowed:
        assert _reached_the_route(resp), resp.text
    else:
        _assert_refused(resp, LOGIN)


# ---------------------------------------------------------------------------
# what makes a request a session's — it matters when the request names no Origin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cookie",
    [
        pytest.param("avp_session=abc", id="a-session-cookie"),
        pytest.param("avp_session=", id="with-no-value"),
        pytest.param("theme=dark; avp_session=abc; other=1", id="among-others"),
        pytest.param(["theme=dark", "avp_session=abc"], id="in-a-second-cookie-header"),
    ],
)
async def test_a_write_carrying_the_session_cookie_by_name_needs_an_origin(
    settings: Settings, cookie: str | list[str]
) -> None:
    """Present with any value counts: the check does not look the session up."""
    _assert_refused(await _login(_app(settings), cookie=cookie, origin=None), LOGIN)


@pytest.mark.parametrize(
    "cookie",
    [
        pytest.param(None, id="no-cookie-header"),
        pytest.param("theme=dark", id="another-cookie"),
        pytest.param("my_avp_session=abc", id="a-longer-name"),
        pytest.param("AVP_SESSION=abc", id="another-case"),
    ],
)
async def test_a_write_with_no_origin_and_no_session_cookie_is_not_refused(
    settings: Settings, cookie: str | None
) -> None:
    resp = await _login(_app(settings), cookie=cookie, origin=None)
    assert _reached_the_route(resp), resp.text


async def test_the_cookie_name_follows_the_setting(settings: Settings) -> None:
    app = _app(settings, session_cookie_name="custom_sid")

    _assert_refused(await _login(app, cookie="custom_sid=x", origin=None), LOGIN)
    # The default name means nothing to a deployment that renamed its cookie.
    assert _reached_the_route(await _login(app, cookie="avp_session=x", origin=None))


# ---------------------------------------------------------------------------
# what is never refused
# ---------------------------------------------------------------------------


async def test_a_read_with_a_session_from_another_origin_is_not_refused(
    client: AsyncClient,
) -> None:
    await _sign_up(client)

    async with _page(client, FOREIGN) as page:
        resp = await page.get(f"{BASE}/auth/me")

    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    ("signed_in", "origin"),
    [
        pytest.param(True, None, id="signed-in-and-no-origin"),
        pytest.param(True, FOREIGN, id="signed-in-and-another-origin"),
        pytest.param(False, FOREIGN, id="signed-out-and-another-origin"),
        pytest.param(False, None, id="signed-out-and-no-origin"),
    ],
)
async def test_the_stripe_webhook_is_exempt_whatever_it_carries(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    signed_in: bool,
    origin: str | None,
) -> None:
    """Every request reaches the signature check, which is what guards it."""
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_not_a_real_secret"))
    if signed_in:
        await _sign_up(client)

    async with _page(client, origin) as page:
        resp = await page.post(
            WEBHOOK, content=b"{}", headers={"Content-Type": "application/json"}
        )

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-webhook-signature"


async def test_a_preflight_is_answered_by_cors_not_refused(
    client: AsyncClient, settings: Settings
) -> None:
    """OPTIONS is no write. CORS stays outermost, so it answers a preflight —
    and refuses a foreign one — before the origin check is asked anything."""
    allowed = settings.cors_allow_origins[0]
    await _sign_up(client)
    ask = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }

    async with _page(client, allowed) as page:
        resp = await page.options(f"{BASE}/clients", headers=ask)
    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == allowed
    assert resp.headers["access-control-allow-credentials"] == "true"

    async with _page(client, FOREIGN) as page:
        resp = await page.options(f"{BASE}/clients", headers=ask)
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# the exemption is exact, and so is the method
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [f"{WEBHOOK}/", f"{WEBHOOK}x"], ids=["trailing-slash", "longer"])
@pytest.mark.parametrize(
    ("signed_in", "origin"),
    [
        pytest.param(False, FOREIGN, id="another-origin"),
        pytest.param(True, FOREIGN, id="signed-in-and-another-origin"),
        pytest.param(True, None, id="signed-in-and-no-origin"),
    ],
)
async def test_only_the_exact_webhook_path_is_exempt(
    client: AsyncClient, path: str, signed_in: bool, origin: str | None
) -> None:
    if signed_in:
        await _sign_up(client)

    async with _page(client, origin) as page:
        resp = await page.post(path, content=b"{}", headers={"Content-Type": "application/json"})

    _assert_refused(resp, path)


async def test_a_lowercase_method_is_checked_as_the_method_it_names(settings: Settings) -> None:
    """httpx upper-cases a method, and uvicorn's httptools parser (the one the
    container runs) rejects a lowercase one before ASGI sees it. Its h11 parser,
    the fallback, passes `post` through as sent, and Starlette's router does NOT
    normalise it: it answers 405 and no handler runs. The check upper-cases the
    method itself, so even a request the router would have refused is refused
    for the right reason. Called directly because no client can send one."""
    app = _app(settings)
    carrying_a_session = {"cookie": SESSION}

    status, body = await _call(
        app, method="post", path=f"{BASE}/clients",
        headers={**carrying_a_session, "origin": FOREIGN},
    )
    assert (status, body["type"]) == (403, REFUSED_TYPE)

    status, body = await _call(
        app, method="post", path=f"{BASE}/clients",
        headers={**carrying_a_session, "origin": settings.cors_allow_origins[0]},
    )
    assert (status, body["type"]) == (405, "/problems/method-not-allowed")


# ---------------------------------------------------------------------------
# the log line
# ---------------------------------------------------------------------------


async def test_a_refusal_is_logged_with_no_cookie_and_no_origin(client: AsyncClient) -> None:
    await _sign_up(client)
    cookie = client.cookies.get("avp_session")
    assert cookie, "the session cookie the refusal must not log"
    sentinel = "https://origin-sentinel.example"

    with structlog.testing.capture_logs() as events:
        async with _page(client, sentinel) as page:
            await page.post(f"{BASE}/clients", json=NEW_CLIENT)
        async with _page(client, None) as page:
            await page.delete(f"{BASE}/users/user_not_a_real_id")
        async with _visitor(client, sentinel) as visitor:
            await visitor.post(LOGIN, json={})

    refusals = [e for e in events if e["event"] == "security.origin_refused"]
    assert [{k: v for k, v in e.items() if k != "log_level"} for e in refusals] == [
        {
            "event": "security.origin_refused",
            "method": "POST",
            "path": f"{BASE}/clients",
            "origin_present": True,
        },
        {
            "event": "security.origin_refused",
            "method": "DELETE",
            "path": f"{BASE}/users/user_not_a_real_id",
            "origin_present": False,
        },
        {
            "event": "security.origin_refused",
            "method": "POST",
            "path": LOGIN,
            "origin_present": True,
        },
    ]
    everything = json.dumps(events, default=str)
    assert cookie not in everything
    assert sentinel not in everything


async def test_the_printed_refusal_carries_no_cookie_and_no_origin(
    client: AsyncClient, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The same, as it is rendered: the line structlog prints on stdout."""
    await _sign_up(client)
    cookie = client.cookies.get("avp_session")
    assert cookie
    sentinel = "https://origin-sentinel.example"
    caplog.set_level("DEBUG")
    capsys.readouterr()

    async with _page(client, sentinel) as page:
        await page.post(f"{BASE}/clients", json=NEW_CLIENT)

    printed = capsys.readouterr().out
    assert "security.origin_refused" in printed, "nothing was printed — the test proves nothing"
    for text in (printed, caplog.text):
        assert cookie not in text
        assert sentinel not in text


async def test_a_secret_in_a_refused_path_is_redacted_from_the_log(
    client: AsyncClient,
) -> None:
    """A stale client's POST to a legacy share-token path must not log the token."""
    await _sign_up(client)

    with structlog.testing.capture_logs() as events:
        async with _page(client, FOREIGN) as page:
            await page.post(f"{BASE}/reports/LEGACYSHARETOKENsentinel0123456789")

    [refusal] = [e for e in events if e["event"] == "security.origin_refused"]
    assert "LEGACYSHARETOKENsentinel" not in json.dumps(events, default=str)
    assert refusal["path"] == f"{BASE}/reports/…"


async def test_a_refused_path_reaches_the_log_percent_encoded(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """`scope["path"]` is percent-DECODED, so an escape, a NUL or a newline sent
    as `%1B`, `%00` or `%0A` would reach the log raw — and a newline could start
    a line of the requester's choosing. The access log quotes the path; so does
    this."""
    await _sign_up(client)
    sent = f"{BASE}/clients%1B%00%0Aforged-line"
    capsys.readouterr()

    with structlog.testing.capture_logs() as events:
        async with _page(client, FOREIGN) as page:
            await page.post(sent)
    [refusal] = [e for e in events if e["event"] == "security.origin_refused"]
    assert refusal["path"] == sent, "the logged path is not the quoted request path"

    # And as printed, outside `capture_logs`: one line, nothing raw in it.
    async with _page(client, FOREIGN) as page:
        await page.post(sent)
    printed = capsys.readouterr().out
    assert "\x00" not in printed
    assert "clients\x1b" not in printed
    lines = [line for line in printed.splitlines() if "forged-line" in line]
    assert len(lines) == 1 and "security.origin_refused" in lines[0]
    assert sent in lines[0]


# ---------------------------------------------------------------------------
# the wiring
# ---------------------------------------------------------------------------


def _middleware(app: FastAPI, cls: type) -> Any:
    return next(m for m in app.user_middleware if m.cls is cls)


def _all_routes(routes: Any) -> Iterator[Any]:
    for route in routes:
        yield route
        yield from _all_routes(getattr(route, "routes", None) or ())


def _websocket_paths(app: FastAPI) -> list[str]:
    return [route.path for route in _all_routes(app.routes) if isinstance(route, WebSocketRoute)]


def test_cors_stays_the_outermost_middleware(settings: Settings) -> None:
    """Starlette puts the LAST `add_middleware` outermost, so the origin check is
    added first. CORS must see every request before it — it answers preflights —
    and every response, the refusal's included, leaves through it."""
    from avp_api.origin_check import OriginCheckMiddleware

    order = [m.cls for m in create_app(settings).user_middleware]

    assert OriginCheckMiddleware in order, "the origin check is not registered"
    assert order[0] is CORSMiddleware
    assert order.index(CORSMiddleware) < order.index(OriginCheckMiddleware)


def test_the_check_is_configured_from_the_settings(settings: Settings) -> None:
    from avp_api.origin_check import OriginCheckMiddleware

    kwargs = _middleware(create_app(settings), OriginCheckMiddleware).kwargs

    assert list(kwargs["allowed_origins"]) == settings.cors_allow_origins
    assert kwargs["session_cookie_name"] == settings.session_cookie_name


def test_every_method_a_route_registers_is_either_read_only_or_checked(
    settings: Settings,
) -> None:
    """The twin of `test_cors.py`'s sweep. A route with a new method (a QUERY, say)
    must not slip past a check that only knows four of them. And no route may be
    a websocket: the check passes `websocket` scopes through, so a websocket route
    would be a way in that carries the session cookie past it."""
    from avp_api.origin_check import STATE_CHANGING_METHODS

    app = create_app(settings)
    registered = {
        method
        for route in _all_routes(app.routes)
        for method in getattr(route, "methods", None) or ()
    }
    assert {"GET", "POST", "PUT", "PATCH", "DELETE"} <= registered, "the sweep is vacuous"

    unchecked = registered - STATE_CHANGING_METHODS - {"GET", "HEAD", "OPTIONS"}
    assert not unchecked, f"{sorted(unchecked)} is registered on a route and not origin-checked"
    assert not _websocket_paths(app), (
        f"{_websocket_paths(app)} is a websocket route. The origin check does not look at "
        "websocket handshakes: check theirs first, then change this test."
    )


def test_the_websocket_sweep_would_notice_one(settings: Settings) -> None:
    app = create_app(settings)

    @app.websocket(f"{BASE}/ws")
    async def _socket(websocket: WebSocket) -> None:
        await websocket.close()

    assert _websocket_paths(app) == [f"{BASE}/ws"]


def test_the_one_exempt_path_is_the_stripe_webhook_route(settings: Settings) -> None:
    from avp_api.origin_check import OriginCheckMiddleware

    app = create_app(settings)
    exempt = set(_middleware(app, OriginCheckMiddleware).kwargs["exempt_paths"])
    post_routes = {
        route.path
        for route in app.routes
        if "POST" in (getattr(route, "methods", None) or ())
    }

    assert exempt == {WEBHOOK}
    assert exempt <= post_routes, "the exemption names a route that does not exist"

"""Google sign-in — Epic 20.

The provider is faked at the seam `deps.google_provider` opens, the same
"fake the provider, test our own logic" pattern the scan executor and the
billing tests use. Everything else runs for real: the state store in the
test Redis, the callback, the linking policy against Postgres, and the
cookie. What is NOT exercised here, and is said so in the build log, is the
exchange with Google itself and the verification of a real ID token —
`LiveGoogleProvider` needs a Google Cloud client that does not exist yet.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api.config import Settings, get_settings
from avp_api.deps import google_provider
from avp_api.models import Agency, Invitation, User, UserStatus
from avp_api.services.google_oauth import GoogleExchangeError, GoogleIdentity

BASE = "/api/v1"
WEB = "http://localhost:3000"


class FakeGoogleProvider:
    """Hands back whatever identity the test filed under a code.

    Records the state, nonce and challenge it was asked to send, so a test
    can assert the authorization URL carried them; and stamps the minted
    nonce into the identity it returns, the way a real ID token would echo
    the nonce the request carried — unless the test filed one deliberately
    wrong.
    """

    def __init__(self) -> None:
        self.identities: dict[str, GoogleIdentity] = {}
        self.last_state: str | None = None
        self.last_nonce: str | None = None
        self.last_challenge: str | None = None
        self.exchanges: list[tuple[str, str]] = []

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.last_state, self.last_nonce, self.last_challenge = state, nonce, code_challenge
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&fake=1"

    async def exchange(self, *, code: str, code_verifier: str) -> GoogleIdentity:
        self.exchanges.append((code, code_verifier))
        identity = self.identities.get(code)
        if identity is None:
            raise GoogleExchangeError
        if identity.nonce == "<minted>":
            return replace(identity, nonce=self.last_nonce)
        return identity


def identity(
    email: str = "dana@northlight.example",
    *,
    sub: str = "google-sub-dana",
    verified: bool = True,
    name: str = "Dana Whitfield",
    nonce: str = "<minted>",
) -> GoogleIdentity:
    return GoogleIdentity(sub=sub, email=email, email_verified=verified, name=name, nonce=nonce)


@pytest.fixture
def google(client: AsyncClient, settings: Settings) -> FakeGoogleProvider:
    """A configured deployment with a fake Google behind it."""
    app = client._transport.app  # noqa: SLF001 - the ASGI app under test
    fake = FakeGoogleProvider()
    configured = settings.model_copy(
        update={
            "google_oauth_client_id": "test-client-id.apps.googleusercontent.com",
            "google_oauth_client_secret": "not-a-real-secret",
            "google_oauth_redirect_url": "http://localhost:8000/api/v1/auth/google/callback",
        }
    )
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[google_provider] = lambda: fake
    return fake


async def start(client: AsyncClient) -> str:
    """Press the button. Returns the state Google would send back."""
    resp = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    return parse_qs(urlparse(location).query)["state"][0]


async def callback(client: AsyncClient, *, code: str | None, state: str | None, **extra: str):
    params = {k: v for k, v in {"code": code, "state": state, **extra}.items() if v is not None}
    return await client.get(f"{BASE}/auth/google/callback", params=params, follow_redirects=False)


def landed(resp, path: str) -> dict[str, list[str]]:  # noqa: ANN001
    """Assert a 302 to the web app at `path`; return its query."""
    assert resp.status_code == 302, resp.text
    url = urlparse(resp.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{WEB}{path}", resp.headers["location"]
    return parse_qs(url.query)


def landed_ticket(resp) -> str:  # noqa: ANN001
    """The ticket from the completion redirect: in the fragment, never the query."""
    assert resp.status_code == 302, resp.text
    url = urlparse(resp.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{WEB}/sign-up/google"
    assert url.query == "", "the ticket must not ride in the query string"
    fragment = parse_qs(url.fragment)
    assert list(fragment) == ["ticket"]
    (ticket,) = fragment["ticket"]
    return ticket


async def peek(client: AsyncClient, ticket: str):  # noqa: ANN201
    return await client.post(f"{BASE}/auth/google/pending", json={"ticket": ticket})


async def sign_up_with_password(client: AsyncClient, email: str) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Northlight Partners",
            "fullName": "Dana Whitfield",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    await client.post(f"{BASE}/auth/logout")
    return resp.json()["user"]["id"]


# --- the button's target ---------------------------------------------------


async def test_unconfigured_deployment_answers_503_not_a_dead_link(client: AsyncClient) -> None:
    resp = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert resp.status_code == 503
    body = resp.json()
    assert body["type"].endswith("/google-sign-in-not-configured")
    assert "GOOGLE_OAUTH_CLIENT_ID" in body["detail"]


async def test_start_sends_the_browser_to_google_with_state_nonce_and_pkce(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    state = await start(client)
    assert google.last_state == state
    assert google.last_nonce is not None and len(google.last_nonce) >= 24
    # An S256 challenge is 43 URL-safe characters with no padding.
    assert google.last_challenge is not None and len(google.last_challenge) == 43
    assert "=" not in google.last_challenge


async def test_two_starts_are_two_states(client: AsyncClient, google: FakeGoogleProvider) -> None:
    assert await start(client) != await start(client)


# --- the callback's guards ---------------------------------------------------


async def test_a_state_this_process_did_not_mint_is_refused(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity()
    query = landed(await callback(client, code="code-1", state="forged"), "/")
    assert query == {"google": ["error"], "reason": ["invalid-state"]}
    assert google.exchanges == [], "no code may be exchanged on a bad state"


async def test_a_state_is_single_use(client: AsyncClient, google: FakeGoogleProvider) -> None:
    google.identities["code-1"] = identity()
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/sign-up/google")
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["invalid-state"]


async def test_cancelling_on_googles_screen_is_denied_not_an_error(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    state = await start(client)
    query = landed(await callback(client, code=None, state=state, error="access_denied"), "/")
    assert query["reason"] == ["denied"]


async def test_a_code_google_refuses_is_exchange_failed(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    state = await start(client)
    query = landed(await callback(client, code="unknown-code", state=state), "/")
    assert query["reason"] == ["exchange-failed"]


async def test_the_tokens_nonce_must_be_the_one_minted(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity(nonce="somebody-elses-nonce")
    state = await start(client)
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["invalid-state"]
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


async def test_the_pkce_verifier_minted_at_start_is_the_one_exchanged(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity()
    state = await start(client)
    await callback(client, code="code-1", state=state)
    assert len(google.exchanges) == 1
    ((_, verifier),) = google.exchanges
    assert len(verifier) >= 43


async def test_an_unverified_google_email_proves_nothing(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity(verified=False)
    state = await start(client)
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["email-unverified"]


# --- a new person: the ticket and the completion --------------------------


async def test_a_new_address_creates_nothing_until_the_agency_is_named(
    client: AsyncClient, google: FakeGoogleProvider, engine, settings: Settings
) -> None:  # noqa: ANN001
    google.identities["code-1"] = identity("new@example.com", name="Dana Whitfield")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))
    assert len(ticket) >= 43

    # Nothing exists yet, and nobody is signed in.
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        assert (
            await s.execute(select(User).where(User.email == "new@example.com"))
        ).first() is None

    # The completion page can read who this is, more than once.
    for _ in range(2):
        pending = await peek(client, ticket)
        assert pending.status_code == 200, pending.text
        assert pending.json() == {"email": "new@example.com", "suggestedName": "Dana Whitfield"}

    done = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": ticket, "agencyName": "Northlight Partners", "fullName": "Dana W."},
    )
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["fullName"] == "Dana W."
    assert body["user"]["role"] == "owner"
    assert body["user"]["signInMethods"] == ["google"]
    assert body["agency"]["name"] == "Northlight Partners"
    assert body["seats"] == {"used": 1, "limit": 3}
    assert settings.session_cookie_key in done.cookies

    me = await client.get(f"{BASE}/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == body["user"]["id"]

    # The ticket is spent.
    again = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": ticket, "agencyName": "Second Agency", "fullName": "Dana W."},
    )
    assert again.status_code == 400
    assert again.json()["type"].endswith("/invalid-google-ticket")
    assert (await peek(client, ticket)).status_code == 400


async def test_completion_validates_the_agency_name_like_sign_up_does(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity("new@example.com")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))
    resp = await client.post(
        f"{BASE}/auth/google/complete", json={"ticket": ticket, "agencyName": "N", "fullName": "D"}
    )
    assert resp.status_code == 422
    # A rejected form does not spend the ticket.
    assert (await peek(client, ticket)).status_code == 200


async def test_an_unknown_ticket_is_one_refusal(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    resp = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": "not-a-ticket", "agencyName": "Northlight", "fullName": "Dana"},
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/invalid-google-ticket")


async def test_a_google_only_account_cannot_sign_in_with_a_password(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity("new@example.com")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))
    await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": ticket, "agencyName": "Northlight", "fullName": "Dana"},
    )
    await client.post(f"{BASE}/auth/logout")
    resp = await client.post(
        f"{BASE}/auth/login", json={"email": "new@example.com", "password": "anything-at-all-here"}
    )
    assert resp.status_code == 401


# --- an existing account: the linking policy -------------------------------


async def test_a_password_account_links_by_verified_email_and_signs_in(
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    user_id = await sign_up_with_password(client, "dana@northlight.example")
    google.identities["code-1"] = identity("Dana@Northlight.example", sub="sub-dana")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")

    me = await client.get(f"{BASE}/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == user_id
    # Linking removes the password (F2): Google is the only way in afterwards.
    assert me.json()["user"]["signInMethods"] == ["google"]

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        assert user.google_sub == "sub-dana"
        assert user.last_login_at is not None


async def test_the_callback_sets_the_host_prefixed_cookie_when_secure(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    """The callback sets the session cookie on whatever host serves it: first-party
    for the web app, `__Host-` prefixed, Lax, with no Domain."""
    await sign_up_with_password(client, "dana@northlight.example")
    google.identities["code-1"] = identity("dana@northlight.example", sub="sub-dana")
    # Swap in secure settings over the ones the `google` fixture left in place.
    app = client._transport.app  # noqa: SLF001
    configured = app.dependency_overrides[get_settings]()
    secure = configured.model_copy(update={"session_cookie_secure": True})
    app.dependency_overrides[get_settings] = lambda: secure

    # A Secure cookie is not sent back over this test's http, so the browser's
    # flow cookie is carried by hand (its own name, `__Host-` prefixed, is
    # asserted in the flow-cookie tests below).
    started = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    (flow_header,) = [h for h in started.headers.get_list("set-cookie") if "google_flow" in h]
    client.cookies.set("__Host-avp_google_flow", flow_header.split(";")[0].split("=", 1)[1])
    resp = await callback(client, code="code-1", state=state)
    landed(resp, "/dashboard")

    (header,) = [h for h in resp.headers.get_list("set-cookie") if "avp_session" in h]
    assert header.startswith("__Host-avp_session=")
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert {"secure", "httponly", "samesite=lax", "path=/"} <= attrs
    assert not any(a.startswith("domain") for a in attrs)


async def test_once_linked_the_sub_wins_over_a_changed_email(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    user_id = await sign_up_with_password(client, "dana@northlight.example")
    google.identities["code-1"] = identity("dana@northlight.example", sub="sub-dana")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")
    await client.post(f"{BASE}/auth/logout")

    # Same Google account, renamed address: still Dana.
    google.identities["code-2"] = identity("dana.whitfield@northlight.example", sub="sub-dana")
    state = await start(client)
    landed(await callback(client, code="code-2", state=state), "/dashboard")
    assert (await client.get(f"{BASE}/auth/me")).json()["user"]["id"] == user_id


async def test_a_different_google_account_on_a_linked_address_is_refused(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    await sign_up_with_password(client, "dana@northlight.example")
    google.identities["code-1"] = identity("dana@northlight.example", sub="sub-dana")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")
    await client.post(f"{BASE}/auth/logout")

    google.identities["code-2"] = identity("dana@northlight.example", sub="sub-someone-else")
    state = await start(client)
    query = landed(await callback(client, code="code-2", state=state), "/")
    # Distinct since 2026-09-21: the person holds this address, so they may be
    # told it is linked to a different Google account than the one they used.
    assert query["reason"] == ["email-claimed"]
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


async def test_a_suspended_account_is_unavailable_however_it_signs_in(
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    user_id = await sign_up_with_password(client, "dana@northlight.example")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        user.status = UserStatus.SUSPENDED
        await s.commit()
    google.identities["code-1"] = identity("dana@northlight.example")
    state = await start(client)
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["account-suspended"]


async def test_a_deleted_account_is_unavailable_and_says_no_more(
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    """The one refusal that stays coarse: a removed seat is not the person's
    to be told about, and the reset flow is the honest next step either way."""
    user_id = await sign_up_with_password(client, "dana@northlight.example")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        user.deleted_at = datetime.now(UTC)
        await s.commit()
    google.identities["code-1"] = identity("dana@northlight.example")
    state = await start(client)
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["account-unavailable"]
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


# --- a link takes the old credentials with it (F2) ---------------------------

PASSWORD = "correct-horse-battery-staple"


def session_cookie_of(client: AsyncClient) -> tuple[str, str]:
    """(name, value) of the session cookie the client holds right now."""
    held = [(c.name, c.value) for c in client.cookies.jar if "session" in c.name.lower()]
    assert len(held) == 1, held
    return held[0]


def hold(client: AsyncClient, cookie: tuple[str, str]) -> None:
    """Make `cookie` the only cookie the client sends."""
    client.cookies.clear()
    client.cookies.set(*cookie)


async def password_login(client: AsyncClient, email: str, password: str):  # noqa: ANN201
    client.cookies.clear()
    return await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


async def user_row(engine, email: str) -> User:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        return (await s.execute(select(User).where(User.email == email))).scalar_one()


async def test_linking_a_password_row_clears_its_password_and_kills_its_sessions(
    client: AsyncClient, google: FakeGoogleProvider, engine  # noqa: ANN001
) -> None:
    """The attack: somebody registered the victim's address with a password
    (nothing verifies it). The victim proves the address through Google; the
    squatter's password and cookie must stop working."""
    email = "victim@example.com"
    await sign_up_with_password(client, email)
    login = await password_login(client, email, PASSWORD)
    assert login.status_code == 200, login.text
    cookie_a = session_cookie_of(client)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200

    google.identities["code-1"] = identity(email, sub="sub-victim")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")
    cookie_b = session_cookie_of(client)
    assert cookie_b != cookie_a
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200  # the new session works

    hold(client, cookie_a)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    wrong = await password_login(client, email, "not-the-password-at-all")
    old = await password_login(client, email, PASSWORD)
    assert old.status_code == 401
    assert wrong.status_code == 401
    assert old.json() == wrong.json()

    row = await user_row(engine, email)
    assert row.password_hash is None
    assert row.google_sub == "sub-victim"

    hold(client, cookie_b)
    assert (await client.get(f"{BASE}/auth/me")).json()["user"]["signInMethods"] == ["google"]


async def test_activating_an_invited_seat_revokes_the_sessions_it_already_holds(
    client: AsyncClient, google: FakeGoogleProvider, engine, settings: Settings  # noqa: ANN001
) -> None:
    from avp_api import redis_client as redis_module
    from avp_api.sessions import SessionStore

    await sign_up_with_password(client, "owner@northlight.example")
    await password_login(client, "owner@northlight.example", PASSWORD)
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": "rae@northlight.example"}
    )
    assert invited.status_code == 201, invited.text
    rae = await user_row(engine, "rae@northlight.example")
    assert rae.status is UserStatus.INVITED

    store = SessionStore(redis_module.get_redis(settings), settings)
    stale, _ = await store.create(user_id=rae.id, agency_id=agency_id)
    assert await store.get(stale) is not None

    client.cookies.clear()
    google.identities["code-1"] = identity("rae@northlight.example", sub="sub-rae")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")

    assert await store.get(stale) is None
    row = await user_row(engine, "rae@northlight.example")
    assert row.status is UserStatus.ACTIVE
    assert row.google_sub == "sub-rae"
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200


async def test_a_repeat_google_sign_in_leaves_other_sessions_alone(
    client: AsyncClient, google: FakeGoogleProvider, engine, settings: Settings  # noqa: ANN001
) -> None:
    from avp_api import redis_client as redis_module
    from avp_api.sessions import SessionStore

    google.identities["code-1"] = identity("new@example.com", sub="sub-new")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))
    done = await complete(client, ticket)
    assert done.status_code == 201, done.text
    user_id = done.json()["user"]["id"]
    first = session_cookie_of(client)

    google.identities["code-2"] = identity("new@example.com", sub="sub-new")
    state = await start(client)
    landed(await callback(client, code="code-2", state=state), "/dashboard")
    assert session_cookie_of(client) != first

    store = SessionStore(redis_module.get_redis(settings), settings)
    assert await store.get(first[1]) is not None
    hold(client, first)
    assert (await client.get(f"{BASE}/auth/me")).json()["user"]["id"] == user_id


async def test_redis_failing_during_the_revoke_fails_the_sign_in_closed(
    client: AsyncClient,
    google: FakeGoogleProvider,
    engine,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    from avp_api.sessions import SessionStore

    email = "victim@example.com"
    await sign_up_with_password(client, email)
    assert (await password_login(client, email, PASSWORD)).status_code == 200
    cookie_a = session_cookie_of(client)

    async def broken(self, user_id):  # noqa: ANN001, ANN202, ARG001
        raise RedisConnectionError("redis is away")

    monkeypatch.setattr(SessionStore, "revoke_all_for_user", broken)
    google.identities["code-1"] = identity(email, sub="sub-victim")
    client.cookies.clear()
    state = await start(client)
    resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["unavailable"]
    assert not [h for h in resp.headers.get_list("set-cookie") if "session" in h.lower()]
    monkeypatch.undo()

    # Nothing was linked: the account is exactly as it was, and no new session exists.
    row = await user_row(engine, email)
    assert row.google_sub is None
    assert row.password_hash is not None
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
    hold(client, cookie_a)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200


async def test_the_linked_log_line_carries_counts_and_no_email(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    import json

    import structlog

    email = "victim@example.com"
    user_id = await sign_up_with_password(client, email)
    assert (await password_login(client, email, PASSWORD)).status_code == 200
    google.identities["code-1"] = identity(email, sub="sub-victim")
    state = await start(client)
    with structlog.testing.capture_logs() as events:
        landed(await callback(client, code="code-1", state=state), "/dashboard")
    (linked,) = [e for e in events if e["event"] == "google.linked"]
    assert linked["user_id"] == user_id
    assert linked["password_cleared"] is True
    assert linked["sessions_revoked"] >= 1
    assert email not in json.dumps(events, default=str)


async def test_an_invited_seat_is_accepted_by_signing_in_with_google(
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    await sign_up_with_password(client, "owner@northlight.example")
    # Sign back in as the owner to invite.
    await client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@northlight.example", "password": "correct-horse-battery-staple"},
    )
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": "rae@northlight.example"}
    )
    assert invited.status_code == 201, invited.text
    await client.post(f"{BASE}/auth/logout")

    google.identities["code-1"] = identity("rae@northlight.example", sub="sub-rae", name="Rae Park")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")
    me = (await client.get(f"{BASE}/auth/me")).json()
    assert me["user"]["email"] == "rae@northlight.example"
    assert me["user"]["status"] == "active"
    assert me["user"]["fullName"] == "Rae Park"
    assert me["user"]["role"] == "member"
    assert me["user"]["signInMethods"] == ["google"]
    assert me["agency"]["id"] == agency_id
    assert me["seats"] == {"used": 2, "limit": 3}

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        inv = (
            await s.execute(select(Invitation).where(Invitation.email == "rae@northlight.example"))
        ).scalar_one()
        assert inv.accepted_at is not None


async def test_an_invited_seat_whose_invitation_has_expired_is_refused(
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    await sign_up_with_password(client, "owner@northlight.example")
    await client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@northlight.example", "password": "correct-horse-battery-staple"},
    )
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": "rae@northlight.example"}
    )
    await client.post(f"{BASE}/auth/logout")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        inv = (
            await s.execute(select(Invitation).where(Invitation.email == "rae@northlight.example"))
        ).scalar_one()
        inv.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()

    google.identities["code-1"] = identity("rae@northlight.example", sub="sub-rae")
    state = await start(client)
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["invitation-expired"]
    async with factory() as s:
        user = (
            await s.execute(select(User).where(User.email == "rae@northlight.example"))
        ).scalar_one()
        assert user.status is UserStatus.INVITED
        assert user.google_sub is None


async def test_a_revoked_invitation_is_refused_like_an_expired_one(
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    """Revoked-but-unexpired had no test; `revoked_at` is its own column."""
    await sign_up_with_password(client, "owner@northlight.example")
    await client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@northlight.example", "password": "correct-horse-battery-staple"},
    )
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": "rae@northlight.example"}
    )
    await client.post(f"{BASE}/auth/logout")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        inv = (
            await s.execute(select(Invitation).where(Invitation.email == "rae@northlight.example"))
        ).scalar_one()
        inv.revoked_at = datetime.now(UTC)
        await s.commit()

    google.identities["code-1"] = identity("rae@northlight.example", sub="sub-rae")
    state = await start(client)
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["invitation-expired"]


async def test_re_sending_an_invitation_clears_a_google_link_on_the_seat(
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    """The rule is unconditional: a re-issued seat carries no Google link,
    whichever branch of `invite` handled it. Until 2026-09-21 only the
    revive branch cleared it; the INVITED re-issue branch did not."""
    await sign_up_with_password(client, "owner@northlight.example")
    await client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@northlight.example", "password": "correct-horse-battery-staple"},
    )
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": "rae@northlight.example"}
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        user = (
            await s.execute(select(User).where(User.email == "rae@northlight.example"))
        ).scalar_one()
        assert user.status is UserStatus.INVITED
        user.google_sub = "sub-left-over"  # nothing writes this today; the rule still holds
        await s.commit()

    resp = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": "rae@northlight.example"}
    )
    assert resp.status_code in (200, 201), resp.text
    async with factory() as s:
        user = (
            await s.execute(select(User).where(User.email == "rae@northlight.example"))
        ).scalar_one()
        assert user.status is UserStatus.INVITED
        assert user.google_sub is None


async def test_a_blank_google_setting_is_unset_not_configured(
    client: AsyncClient, settings: Settings
) -> None:
    """`cp .env.example .env` leaves GOOGLE_OAUTH_CLIENT_ID= present and empty.
    Until 2026-09-21 that counted as configured and the button sent the
    browser to Google with an empty client id; it is the 503 now."""
    app = client._transport.app  # noqa: SLF001 - the ASGI app under test
    blank = settings.model_copy(
        update={
            "google_oauth_client_id": "",
            "google_oauth_client_secret": "not-a-real-secret",
            "google_oauth_redirect_url": "http://localhost:8000/api/v1/auth/google/callback",
        }
    )
    assert Settings.model_validate(blank.model_dump()).google_sign_in_configured is False
    app.dependency_overrides[get_settings] = lambda: Settings.model_validate(blank.model_dump())
    resp = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert resp.status_code == 503, resp.text
    assert "GOOGLE_OAUTH_CLIENT_ID" in resp.json()["detail"]


async def test_an_infrastructure_failure_in_the_callback_is_a_redirect_not_a_500(
    client: AsyncClient, google: FakeGoogleProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person is standing at the callback. Redis or Postgres failing under
    it must not show them a raw error page (2026-09-21)."""
    from avp_api.services import google_oauth

    google.identities["code-1"] = identity("dana@northlight.example")
    state = await start(client)

    async def broken(self, key):  # noqa: ANN001, ANN202, ARG001
        raise ConnectionError("redis is away")

    monkeypatch.setattr(google_oauth.GoogleFlowStore, "_take", broken)
    query = landed(await callback(client, code="code-1", state=state), "/")
    assert query["reason"] == ["unavailable"]
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


async def test_a_ticket_expires_with_the_configured_ttl(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings
) -> None:
    """Expiry was inferred from `ex=` until 2026-09-21; now it is exercised:
    the key carries the configured TTL, and once it lapses /pending and
    /complete both answer 400."""
    import asyncio

    app = client._transport.app  # noqa: SLF001 - the ASGI app under test
    short = app.dependency_overrides[get_settings]().model_copy(
        update={"google_sign_in_ttl_seconds": 1}
    )
    app.dependency_overrides[get_settings] = lambda: short
    google.identities["code-1"] = identity("new-person@example.com", sub="sub-new")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))

    first = await peek(client, ticket)
    assert first.status_code == 200, first.text
    await asyncio.sleep(1.2)
    second = await peek(client, ticket)
    assert second.status_code == 400, second.text
    done = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": ticket, "agencyName": "Late Agency", "fullName": "New Person"},
    )
    assert done.status_code == 400, done.text


async def test_the_failure_redirect_carries_no_email_and_no_token(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity("secret-person@example.com", verified=False)
    state = await start(client)
    resp = await callback(client, code="code-1", state=state)
    location = resp.headers["location"]
    assert "secret-person" not in location
    assert "code-1" not in location
    assert state not in location


async def test_the_pending_peek_is_a_post_and_a_query_ticket_is_refused(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    """The ticket is a bearer credential: it travels in a body, never a query.

    GET is 405 (the path exists, the method does not), so an old client that
    still sends `?ticket=` learns nothing and its ticket reaches no handler.
    """
    google.identities["code-1"] = identity("new@example.com")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))
    old = await client.get(f"{BASE}/auth/google/pending", params={"ticket": ticket})
    assert old.status_code == 405, old.text
    assert "new@example.com" not in old.text
    # A POST with the ticket in a query and no body is a validation error, not a peek.
    assert (
        await client.post(f"{BASE}/auth/google/pending", params={"ticket": ticket})
    ).status_code == 422
    # Unknown ticket in the body: the existing problem.
    unknown = await peek(client, "not-a-ticket")
    assert unknown.status_code == 400
    assert unknown.json()["type"].endswith("/invalid-google-ticket")


# Covers app/structlog logs over ASGITransport, not uvicorn's access log; that
# path is covered by the empty-query assertions, the 405 test and
# test_logging_config.py.
async def test_the_ticket_reaches_no_app_log_line(
    client: AsyncClient, google: FakeGoogleProvider, capsys, caplog  # noqa: ANN001
) -> None:
    import json

    import structlog

    caplog.set_level("DEBUG")
    google.identities["code-1"] = identity("logcheck@example.com")
    with structlog.testing.capture_logs() as events:
        state = await start(client)
        ticket = landed_ticket(await callback(client, code="code-1", state=state))
        assert (await peek(client, ticket)).status_code == 200
        done = await client.post(
            f"{BASE}/auth/google/complete",
            json={"ticket": ticket, "agencyName": "Log Agency", "fullName": "Dana"},
        )
        assert done.status_code == 201, done.text
    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    assert ticket not in everything
    assert "logcheck@example.com" not in everything


# --- the flow cookie: the state is bound to the browser that started it ------
#
# Without it, someone could start a flow in their own browser and get a
# victim's browser to finish it (login CSRF); the callback is a GET, which the
# Origin check does not cover. 2026-10-02, launch-checklist 3.22.

FLOW_COOKIE = "avp_google_flow"


def set_cookie_headers(resp) -> list[str]:  # noqa: ANN001
    return resp.headers.get_list("set-cookie")


def flow_cookie_header(resp) -> str:  # noqa: ANN001
    (header,) = [h for h in set_cookie_headers(resp) if h.startswith(f"{FLOW_COOKIE}=")]
    return header


def assert_flow_cookie_cleared(resp) -> None:  # noqa: ANN001
    header = flow_cookie_header(resp)
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert header.startswith(f'{FLOW_COOKIE}="";') or header.startswith(f"{FLOW_COOKIE}=;")
    assert "max-age=0" in attrs
    assert "path=/" in attrs


async def start_with_cookie(client: AsyncClient) -> tuple[str, str]:
    """Press the button. Returns (state, the flow cookie's value)."""
    client.cookies.delete(FLOW_COOKIE)
    resp = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    return state, client.cookies[FLOW_COOKIE]


def use_cookie(client: AsyncClient, value: str | None) -> None:
    """Make the next callback arrive carrying exactly this flow cookie (or none)."""
    client.cookies.delete(FLOW_COOKIE)
    if value is not None:
        client.cookies.set(FLOW_COOKIE, value)


async def test_start_sets_the_flow_cookie(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings
) -> None:
    resp = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    header = flow_cookie_header(resp)
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert {"httponly", "samesite=lax", "path=/"} <= attrs
    assert f"max-age={settings.google_sign_in_ttl_seconds + 60}" in attrs
    assert "secure" not in attrs  # local http
    assert not any(a.startswith("domain") for a in attrs)
    value = header.split(";")[0].split("=", 1)[1]
    assert len(value) >= 40
    # The value is the browser's secret: it is not in the URL sent to Google.
    assert value not in resp.headers["location"]


async def test_start_flow_cookie_is_host_prefixed_and_secure_when_secure(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    app = client._transport.app  # noqa: SLF001
    configured = app.dependency_overrides[get_settings]()
    secure = configured.model_copy(update={"session_cookie_secure": True})
    app.dependency_overrides[get_settings] = lambda: secure

    resp = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    (header,) = [h for h in set_cookie_headers(resp) if "avp_google_flow" in h]
    assert header.startswith("__Host-avp_google_flow=")
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert {"secure", "httponly", "samesite=lax", "path=/"} <= attrs
    assert f"max-age={secure.google_sign_in_ttl_seconds + 60}" in attrs
    assert not any(a.startswith("domain") for a in attrs)


async def test_the_cookie_that_started_the_flow_finishes_it_and_is_cleared(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    # A brand-new address: the ticket path.
    google.identities["code-new"] = identity("new-person@example.com", sub="sub-new")
    state, _ = await start_with_cookie(client)
    resp = await callback(client, code="code-new", state=state)
    landed_ticket(resp)
    # The ticket is bound to this browser, so the cookie is kept (renewed)
    # for /pending and /complete, and cleared there.
    assert not any(
        h.startswith(f"{FLOW_COOKIE}=") and "Max-Age=0" in h for h in set_cookie_headers(resp)
    )

    # An existing account: the session path, with the session cookie alongside.
    await sign_up_with_password(client, "dana@northlight.example")
    google.identities["code-1"] = identity("dana@northlight.example", sub="sub-dana")
    state, _ = await start_with_cookie(client)
    resp = await callback(client, code="code-1", state=state)
    landed(resp, "/dashboard")
    assert_flow_cookie_cleared(resp)
    assert any(h.startswith("avp_session=") for h in set_cookie_headers(resp))
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200


async def test_another_browsers_flow_cookie_is_browser_mismatch(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    await sign_up_with_password(client, "dana@northlight.example")
    google.identities["code-1"] = identity("dana@northlight.example", sub="sub-dana")
    state, mine = await start_with_cookie(client)
    _, theirs = await start_with_cookie(client)
    assert mine != theirs
    use_cookie(client, theirs)

    resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/") == {"google": ["error"], "reason": ["browser-mismatch"]}
    assert not any(h.startswith("avp_session=") for h in set_cookie_headers(resp))
    assert_flow_cookie_cleared(resp)
    assert google.exchanges == [], "no code may be exchanged for the wrong browser"
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    # The state was taken before it was compared: the right cookie cannot retry it.
    use_cookie(client, mine)
    retry = await callback(client, code="code-1", state=state)
    assert landed(retry, "/")["reason"] == ["invalid-state"]
    assert google.exchanges == []


async def test_a_missing_flow_cookie_is_browser_mismatch(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    import structlog

    google.identities["code-1"] = identity()
    state, mine = await start_with_cookie(client)
    use_cookie(client, None)
    with structlog.testing.capture_logs() as events:
        resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["browser-mismatch"]
    assert_flow_cookie_cleared(resp)
    assert google.exchanges == []
    (event,) = [e for e in events if e["event"] == "google.callback.browser_mismatch"]
    assert event["cause"] == "missing"
    use_cookie(client, mine)
    assert landed(await callback(client, code="code-1", state=state), "/")["reason"] == [
        "invalid-state"
    ]


async def test_a_different_cookie_is_logged_as_different(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    import json

    import structlog

    google.identities["code-1"] = identity()
    state, mine = await start_with_cookie(client)
    use_cookie(client, "somebody-elses-binding-value-0123456789abcdef")
    with structlog.testing.capture_logs() as events:
        resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["browser-mismatch"]
    (event,) = [e for e in events if e["event"] == "google.callback.browser_mismatch"]
    assert event["cause"] == "different"
    blob = json.dumps(events, default=str)
    assert mine not in blob and "somebody-elses-binding-value" not in blob


async def test_a_replayed_callback_is_invalid_state_and_signs_in_nobody_twice(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    await sign_up_with_password(client, "dana@northlight.example")
    google.identities["code-1"] = identity("dana@northlight.example", sub="sub-dana")
    state, mine = await start_with_cookie(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")
    assert len(google.exchanges) == 1
    await client.post(f"{BASE}/auth/logout")

    # Same state, and the right cookie again (the first reply cleared it).
    use_cookie(client, mine)
    again = await callback(client, code="code-1", state=state)
    assert landed(again, "/")["reason"] == ["invalid-state"]
    assert not any(h.startswith("avp_session=") for h in set_cookie_headers(again))
    assert_flow_cookie_cleared(again)
    assert len(google.exchanges) == 1
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


async def test_an_expired_state_is_invalid_state_even_with_the_right_cookie(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    import asyncio

    app = client._transport.app  # noqa: SLF001
    short = app.dependency_overrides[get_settings]().model_copy(
        update={"google_sign_in_ttl_seconds": 1}
    )
    app.dependency_overrides[get_settings] = lambda: short
    google.identities["code-1"] = identity()
    state, _ = await start_with_cookie(client)
    await asyncio.sleep(1.2)
    # The cookie outlives the Redis record by 60 s, so this reads as expired.
    assert FLOW_COOKIE in client.cookies
    resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["invalid-state"]
    assert_flow_cookie_cleared(resp)
    assert google.exchanges == []


async def test_a_flow_parked_before_the_deploy_has_no_binding_and_fails_closed(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings
) -> None:
    import json

    import structlog

    from avp_api import redis_client as redis_module
    from avp_api.security import token_digest
    from avp_api.services.google_oauth import STATE_PREFIX

    google.identities["code-1"] = identity()
    state, _ = await start_with_cookie(client)
    redis = redis_module.get_redis(settings)
    key = f"{STATE_PREFIX}{token_digest(state)}"
    d = json.loads(await redis.get(key))
    assert d.pop("binding_digest")  # the record carries a digest of the cookie, not the cookie
    await redis.set(key, json.dumps(d), ex=60)

    with structlog.testing.capture_logs() as events:
        resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["browser-mismatch"]
    (event,) = [e for e in events if e["event"] == "google.callback.browser_mismatch"]
    assert event["cause"] == "unbound"
    assert google.exchanges == []


async def test_access_denied_clears_the_flow_cookie_too(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    state, _ = await start_with_cookie(client)
    resp = await callback(client, code=None, state=state, error="access_denied")
    assert landed(resp, "/")["reason"] == ["denied"]
    assert_flow_cookie_cleared(resp)


async def test_the_bad_state_and_unconfigured_paths_clear_the_flow_cookie_too(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    await start_with_cookie(client)
    resp = await callback(client, code="code-1", state="forged")
    assert landed(resp, "/")["reason"] == ["invalid-state"]
    assert_flow_cookie_cleared(resp)
    resp = await callback(client, code="code-1", state=None)
    assert landed(resp, "/")["reason"] == ["invalid-state"]
    assert_flow_cookie_cleared(resp)


async def test_the_unconfigured_callback_clears_the_flow_cookie(client: AsyncClient) -> None:
    resp = await callback(client, code="code-1", state="x")
    assert landed(resp, "/")["reason"] == ["not-configured"]
    assert_flow_cookie_cleared(resp)


async def test_the_binding_value_reaches_no_log_line_and_no_failure_url(
    client: AsyncClient, google: FakeGoogleProvider, capsys, caplog  # noqa: ANN001
) -> None:
    import json

    import structlog

    caplog.set_level("DEBUG")
    google.identities["code-1"] = identity("logcheck@example.com")
    forged = "forged-binding-value-" + "x" * 22
    with structlog.testing.capture_logs() as events:
        state, first = await start_with_cookie(client)
        use_cookie(client, forged)
        bad = await callback(client, code="code-1", state=state)
        state, second = await start_with_cookie(client)
        good = await callback(client, code="code-1", state=state)
    landed_ticket(good)
    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    for value in (first, second, forged):
        assert value not in everything
        assert value not in bad.headers["location"] and value not in good.headers["location"]


async def test_the_clearing_header_in_secure_mode_names_the_host_cookie(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    app = client._transport.app  # noqa: SLF001
    configured = app.dependency_overrides[get_settings]()
    secure = configured.model_copy(update={"session_cookie_secure": True})
    app.dependency_overrides[get_settings] = lambda: secure
    resp = await callback(client, code=None, state="forged")
    (header,) = [h for h in set_cookie_headers(resp) if "avp_google_flow" in h]
    assert header.startswith("__Host-avp_google_flow=")
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert {"secure", "path=/", "max-age=0"} <= attrs


async def test_the_unavailable_path_clears_the_flow_cookie_too(
    client: AsyncClient, google: FakeGoogleProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from avp_api.services import google_oauth

    state, _ = await start_with_cookie(client)

    async def broken(self, key):  # noqa: ANN001, ANN202, ARG001
        raise ConnectionError("redis is away")

    monkeypatch.setattr(google_oauth.GoogleFlowStore, "_take", broken)
    resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["unavailable"]
    assert_flow_cookie_cleared(resp)


# --- the new-address ticket is bound to the same browser ----------------------


async def new_address_ticket(client: AsyncClient, google: FakeGoogleProvider):  # noqa: ANN201
    """Returns (ticket, the flow cookie value, the callback response)."""
    google.identities["code-new"] = identity("new-person@example.com", sub="sub-new")
    state, mine = await start_with_cookie(client)
    resp = await callback(client, code="code-new", state=state)
    return landed_ticket(resp), mine, resp


async def no_user_for(engine, email: str) -> bool:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        return (await s.execute(select(User).where(User.email == email))).first() is None


async def complete(client: AsyncClient, ticket: str):  # noqa: ANN201
    return await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": ticket, "agencyName": "Bound Agency", "fullName": "New Person"},
    )


async def test_the_new_address_callback_keeps_the_flow_cookie_for_the_ticket(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings
) -> None:
    _, mine, resp = await new_address_ticket(client, google)
    header = flow_cookie_header(resp)
    assert header.split(";")[0] == f"{FLOW_COOKIE}={mine}"
    attrs = {part.strip().lower() for part in header.split(";")[1:]}
    assert {"httponly", "samesite=lax", "path=/"} <= attrs
    assert f"max-age={settings.google_sign_in_ttl_seconds + 60}" in attrs


@pytest.mark.parametrize("who", ["nobody", "another-browser"])
async def test_a_ticket_is_not_redeemable_from_another_browser(
    client: AsyncClient, google: FakeGoogleProvider, engine, who: str  # noqa: ANN001
) -> None:
    import structlog

    ticket, mine, _ = await new_address_ticket(client, google)
    use_cookie(client, None if who == "nobody" else "another-browsers-binding-" + "y" * 20)

    with structlog.testing.capture_logs() as events:
        pending = await peek(client, ticket)
        done = await complete(client, ticket)
    assert pending.status_code == 400, pending.text
    assert pending.json()["type"].endswith("/invalid-google-ticket")
    assert done.status_code == 400, done.text
    assert done.json()["type"].endswith("/invalid-google-ticket")
    assert FLOW_COOKIE in "".join(done.headers.get_list("set-cookie"))
    assert_flow_cookie_cleared(done)
    assert await no_user_for(engine, "new-person@example.com")
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
    causes = [e["cause"] for e in events if e["event"] == "google.ticket.browser_mismatch"]
    expected = "missing" if who == "nobody" else "different"
    assert causes == [expected, expected]
    assert mine not in str(events)


async def test_a_refused_read_does_not_spend_the_ticket(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    ticket, mine, _ = await new_address_ticket(client, google)
    use_cookie(client, None)
    assert (await peek(client, ticket)).status_code == 400
    use_cookie(client, mine)
    assert (await peek(client, ticket)).status_code == 200


async def test_the_same_browser_completes_and_the_flow_cookie_is_cleared(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    ticket, _, _ = await new_address_ticket(client, google)
    assert (await peek(client, ticket)).status_code == 200
    done = await complete(client, ticket)
    assert done.status_code == 201, done.text
    assert_flow_cookie_cleared(done)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200


async def test_a_ticket_parked_without_a_binding_fails_closed(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings, engine  # noqa: ANN001
) -> None:
    import json

    from avp_api import redis_client as redis_module
    from avp_api.security import token_digest
    from avp_api.services.google_oauth import TICKET_PREFIX

    ticket, _, _ = await new_address_ticket(client, google)
    redis = redis_module.get_redis(settings)
    key = f"{TICKET_PREFIX}{token_digest(ticket)}"
    d = json.loads(await redis.get(key))
    assert d.pop("binding_digest")
    await redis.set(key, json.dumps(d), ex=60)
    assert (await peek(client, ticket)).status_code == 400
    assert (await complete(client, ticket)).status_code == 400
    assert await no_user_for(engine, "new-person@example.com")


# --- the link racing other writers, and what it leaves behind (F2 review) -----


async def add_reset_token(engine, user_id: str, raw: str) -> None:  # noqa: ANN001
    from avp_api import ids
    from avp_api.models import PasswordResetToken
    from avp_api.security import token_digest

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET),
                user_id=user_id,
                token_digest=token_digest(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()


async def clear_hash_elsewhere(engine, email: str) -> None:  # noqa: ANN001
    """What a committed Google link does to the row, from another connection."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(select(User).where(User.email == email))).scalar_one()
        row.google_sub = "sub-raced"
        row.password_hash = None
        await s.commit()


class LinkLandsBeforeTheWrite:
    """A session whose first UPDATE is preceded by a committed Google link.

    The request has verified the password it loaded; the link commits from
    another connection; only then does the write run. Everything else passes
    straight through.
    """

    def __init__(self, inner, engine, email: str) -> None:  # noqa: ANN001
        self.inner, self.engine, self.email, self.fired = inner, engine, email, False

    async def execute(self, stmt, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if not self.fired and getattr(stmt, "is_update", False):
            self.fired = True
            await clear_hash_elsewhere(self.engine, self.email)
        return await self.inner.execute(stmt, *args, **kwargs)

    def __getattr__(self, name):  # noqa: ANN001, ANN202
        return getattr(self.inner, name)


async def test_a_change_password_racing_the_link_cannot_write_the_password_back(
    client: AsyncClient, engine, settings: Settings  # noqa: ANN001
) -> None:
    from avp_api.errors import InvalidCredentials
    from avp_api.services import auth as auth_service

    email = "victim@example.com"
    await sign_up_with_password(client, email)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        loaded = (await s.execute(select(User).where(User.email == email))).scalar_one()
        racing = LinkLandsBeforeTheWrite(s, engine, email)
        with pytest.raises(InvalidCredentials):
            await auth_service.change_password(
                racing,  # type: ignore[arg-type]
                user=loaded,
                current_password=PASSWORD,
                new_password="a-brand-new-passphrase-1",
                settings=settings,
            )
        assert racing.fired
        await s.rollback()
    row = await user_row(engine, email)
    assert row.password_hash is None
    assert row.google_sub == "sub-raced"


async def test_a_change_password_that_loses_the_race_after_minting_gets_no_session(
    client: AsyncClient, engine, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    """Fix (b): the link commits after the write; the session just minted dies."""
    from avp_api.sessions import SessionStore

    email = "victim@example.com"
    await sign_up_with_password(client, email)
    assert (await password_login(client, email, PASSWORD)).status_code == 200
    real_create = SessionStore.create
    minted: list[str] = []

    async def create_then_link(self, **kw):  # noqa: ANN001, ANN003, ANN202
        token_and_rest = await real_create(self, **kw)
        if not minted:  # only the change-password session, not the later ones
            minted.append(token_and_rest[0])
            await clear_hash_elsewhere(engine, email)
        return token_and_rest

    monkeypatch.setattr(SessionStore, "create", create_then_link)
    resp = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": "a-brand-new-passphrase-1"},
    )
    assert resp.status_code == 401, resp.text
    assert not [h for h in resp.headers.get_list("set-cookie") if "session" in h.lower()]
    from avp_api import redis_client as redis_module

    store = SessionStore(redis_module.get_redis(get_settings()), get_settings())
    assert await store.get(minted[0]) is None


async def test_a_link_landing_right_after_the_change_password_commit_gets_no_session(
    client: AsyncClient, engine, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    """The link commits between the write's commit and its read-back (review B1).

    The hash compared is the one written inside the transaction, so a NULL read
    after the commit is a lost race: no cookie, and the attempt is not given back.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from avp_api import redis_client as redis_module

    email = "victim@example.com"
    await sign_up_with_password(client, email)
    assert (await password_login(client, email, PASSWORD)).status_code == 200
    real_commit = AsyncSession.commit
    fired: list[bool] = []

    async def commit_then_link(self):  # noqa: ANN001, ANN202
        await real_commit(self)
        if not fired:
            fired.append(True)
            await clear_hash_elsewhere(engine, email)

    monkeypatch.setattr(AsyncSession, "commit", commit_then_link)
    resp = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": "a-brand-new-passphrase-1"},
    )
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    assert fired
    assert resp.status_code == 401, resp.text
    assert not [h for h in resp.headers.get_list("set-cookie") if "session" in h.lower()]
    row = await user_row(engine, email)
    assert row.password_hash is None
    # The refused attempt was not refunded: the account's change-password bucket
    # still holds it.
    redis = redis_module.get_redis(get_settings())
    keys = [k async for k in redis.scan_iter(match="rl:change_password:email:*")]
    assert len(keys) == 1
    assert int(await redis.get(keys[0])) == 1


async def test_a_login_rehash_racing_the_link_is_refused_and_writes_nothing(
    client: AsyncClient, engine, settings: Settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    from avp_api.errors import InvalidCredentials
    from avp_api.services import auth as auth_service

    email = "victim@example.com"
    await sign_up_with_password(client, email)
    monkeypatch.setattr(auth_service, "needs_rehash", lambda h, st: True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        racing = LinkLandsBeforeTheWrite(s, engine, email)
        with pytest.raises(InvalidCredentials):
            await auth_service.authenticate(
                racing,  # type: ignore[arg-type]
                email=email,
                password=PASSWORD,
                settings=settings,
            )
        assert racing.fired
        await s.rollback()
    assert (await user_row(engine, email)).password_hash is None


async def test_a_reset_token_minted_before_the_link_is_refused_after_it(
    client: AsyncClient, google: FakeGoogleProvider, engine  # noqa: ANN001
) -> None:
    email = "victim@example.com"
    user_id = await sign_up_with_password(client, email)
    await add_reset_token(engine, user_id, "tok-" + "x" * 40)
    google.identities["code-1"] = identity(email, sub="sub-victim")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")

    from avp_api.models import PasswordResetToken

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        stamped = (
            await s.execute(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        ).scalar_one()
        assert stamped.used_at is not None  # retired by the link itself

    body = {"token": "tok-" + "x" * 40, "newPassword": "a-brand-new-passphrase-1"}
    refused = await client.post(f"{BASE}/auth/reset-password/confirm", json=body)
    unknown = await client.post(
        f"{BASE}/auth/reset-password/confirm", json={**body, "token": "tok-" + "y" * 40}
    )
    assert refused.status_code == unknown.status_code == 400
    # The Google link retired the link (`used_at` is stamped above), so it answers as a used one
    # does; status, type and title are the unknown token's.
    assert refused.json()["detail"] == (
        "This link has already been used. If you just set a new password, sign in with it. "
        "Otherwise, request a new link."
    )
    for key in ("type", "title"):
        assert refused.json()[key] == unknown.json()[key]
    assert (await user_row(engine, email)).password_hash is None


async def test_the_link_leaves_an_expired_unused_reset_link_unstamped(
    client: AsyncClient, google: FakeGoogleProvider, engine  # noqa: ANN001
) -> None:
    from sqlalchemy import update

    from avp_api.models import PasswordResetToken
    from avp_api.security import token_digest

    email = "victim@example.com"
    user_id = await sign_up_with_password(client, email)
    raw = "tok-" + "e" * 40
    await add_reset_token(engine, user_id, raw)
    this_link = PasswordResetToken.token_digest == token_digest(raw)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            update(PasswordResetToken)
            .where(this_link)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    google.identities["code-1"] = identity(email, sub="sub-victim")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")

    async with factory() as s:
        row = (await s.execute(select(PasswordResetToken).where(this_link))).scalar_one()
        assert row.used_at is None  # expired, never used: not stamped by the link

    body = {"token": raw, "newPassword": "a-brand-new-passphrase-1"}
    refused = await client.post(f"{BASE}/auth/reset-password/confirm", json=body)
    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        "That reset link is not valid. Links work once and expire after an hour."
    )


async def test_confirm_reset_refuses_a_row_with_no_password(
    client: AsyncClient, google: FakeGoogleProvider, engine  # noqa: ANN001
) -> None:
    google.identities["code-1"] = identity("new@example.com", sub="sub-new")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))
    user_id = (await complete(client, ticket)).json()["user"]["id"]
    await add_reset_token(engine, user_id, "tok-" + "z" * 40)  # one that bypassed request_reset
    resp = await client.post(
        f"{BASE}/auth/reset-password/confirm",
        json={"token": "tok-" + "z" * 40, "newPassword": "a-brand-new-passphrase-1"},
    )
    assert resp.status_code == 400
    assert (await user_row(engine, "new@example.com")).password_hash is None


async def test_the_link_revokes_the_invitations_that_user_sent_and_only_those(
    client: AsyncClient, google: FakeGoogleProvider, engine  # noqa: ANN001
) -> None:
    email = "victim@example.com"
    await sign_up_with_password(client, email)
    await password_login(client, email, PASSWORD)
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    sent = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": "mine@northlight.example"}
    )
    assert sent.status_code == 201, sent.text
    # Somebody else's invitation, from another account in another agency.
    client.cookies.clear()
    await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Other Agency",
            "fullName": "Other Owner",
            "email": "other@example.com",
            "password": PASSWORD,
        },
    )
    other_agency = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    theirs = await client.post(
        f"{BASE}/agencies/{other_agency}/invitations", json={"email": "theirs@example.com"}
    )
    assert theirs.status_code == 201, theirs.text
    client.cookies.clear()

    google.identities["code-1"] = identity(email, sub="sub-victim")
    state = await start(client)
    landed(await callback(client, code="code-1", state=state), "/dashboard")

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        rows = {i.email: i for i in (await s.execute(select(Invitation))).scalars()}
    assert rows["mine@northlight.example"].revoked_at is not None
    assert rows["theirs@example.com"].revoked_at is None


async def test_a_post_commit_revoke_failure_is_logged_and_leaves_the_link_committed(
    client: AsyncClient,
    google: FakeGoogleProvider,
    engine,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import structlog
    from redis.exceptions import ConnectionError as RedisConnectionError

    from avp_api.sessions import SessionStore

    email = "victim@example.com"
    user_id = await sign_up_with_password(client, email)
    real = SessionStore.revoke_all_for_user
    calls: list[int] = []

    async def second_fails(self, uid):  # noqa: ANN001, ANN202
        calls.append(1)
        if len(calls) == 2:
            raise RedisConnectionError("redis is away")
        return await real(self, uid)

    monkeypatch.setattr(SessionStore, "revoke_all_for_user", second_fails)
    google.identities["code-1"] = identity(email, sub="sub-victim")
    state = await start(client)
    with structlog.testing.capture_logs() as events:
        resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["unavailable"]
    names = [e["event"] for e in events]
    assert names.index("google.linked") < names.index("google.link.post_commit_revoke_failed")
    failed = next(e for e in events if e["event"] == "google.link.post_commit_revoke_failed")
    assert failed["user_id"] == user_id
    assert email not in str(events)
    row = await user_row(engine, email)
    assert row.google_sub == "sub-victim"
    assert row.password_hash is None


# --- the complete step racing a password sign-up for the same address (A1 review, L4) ----------


async def test_the_complete_step_losing_a_race_for_the_address_is_the_409_and_logs_no_address(
    client: AsyncClient, google: FakeGoogleProvider, engine, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    capsys, caplog,  # noqa: ANN001
) -> None:
    """The address is free when the ticket is minted and when the step checks it, and taken by the
    time the insert runs. The unique index answers, and the answer is the step's existing 409."""
    import structlog

    from avp_api.services import auth as auth_service

    email = "race@example.com"
    google.identities["code-1"] = identity(email)
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-1", state=state))
    await sign_up_with_password(client, email)  # somebody registers it after the ticket

    async def free(session, address):  # noqa: ANN001, ANN202
        return False  # the check ran before that sign-up committed

    monkeypatch.setattr(auth_service, "email_exists", free)
    caplog.set_level("DEBUG")
    with structlog.testing.capture_logs() as events:
        done = await client.post(
            f"{BASE}/auth/google/complete",
            json={"ticket": ticket, "agencyName": "Race Agency", "fullName": "Dana"},
        )

    assert done.status_code == 409, done.text
    assert done.json()["type"] == "/problems/email-already-registered"
    assert email not in done.text
    everything = str(events) + capsys.readouterr().out + caplog.text
    assert email not in everything, "the address reached a log"
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        names = (await s.execute(select(Agency.name))).scalars().all()
    assert "Race Agency" not in names

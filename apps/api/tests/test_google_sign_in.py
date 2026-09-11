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
from avp_api.models import Invitation, User, UserStatus
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
    client: AsyncClient, google: FakeGoogleProvider, engine
) -> None:  # noqa: ANN001
    google.identities["code-1"] = identity("new@example.com", name="Dana Whitfield")
    state = await start(client)
    query = landed(await callback(client, code="code-1", state=state), "/sign-up/google")
    (ticket,) = query["ticket"]
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
        pending = await client.get(f"{BASE}/auth/google/pending", params={"ticket": ticket})
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
    assert "avp_session" in done.cookies

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
    assert (
        await client.get(f"{BASE}/auth/google/pending", params={"ticket": ticket})
    ).status_code == 400


async def test_completion_validates_the_agency_name_like_sign_up_does(
    client: AsyncClient, google: FakeGoogleProvider
) -> None:
    google.identities["code-1"] = identity("new@example.com")
    state = await start(client)
    (ticket,) = landed(await callback(client, code="code-1", state=state), "/sign-up/google")[
        "ticket"
    ]
    resp = await client.post(
        f"{BASE}/auth/google/complete", json={"ticket": ticket, "agencyName": "N", "fullName": "D"}
    )
    assert resp.status_code == 422
    # A rejected form does not spend the ticket.
    assert (
        await client.get(f"{BASE}/auth/google/pending", params={"ticket": ticket})
    ).status_code == 200


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
    (ticket,) = landed(await callback(client, code="code-1", state=state), "/sign-up/google")[
        "ticket"
    ]
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
    assert me.json()["user"]["signInMethods"] == ["email", "google"]

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        assert user.google_sub == "sub-dana"
        assert user.last_login_at is not None


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
    assert query["reason"] == ["account-unavailable"]
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
    assert query["reason"] == ["account-unavailable"]


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
    assert query["reason"] == ["account-unavailable"]
    async with factory() as s:
        user = (
            await s.execute(select(User).where(User.email == "rae@northlight.example"))
        ).scalar_one()
        assert user.status is UserStatus.INVITED
        assert user.google_sub is None


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

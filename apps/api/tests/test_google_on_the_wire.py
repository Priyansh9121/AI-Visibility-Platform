"""The real `LiveGoogleProvider` behind the real routes, Google replaced at the wire — A6 part 1.

`test_google_sign_in.py` fakes the provider at the seam `deps.google_provider` opens, so the address
the product sends the browser to and the request it makes of Google's token endpoint never meet the
routes there; `test_google_live_provider.py` drives the provider without the routes. Here the two
meet. The routes call the real provider, and the only things replaced are the two places Google is
reached: the token endpoint (an httpx `MockTransport`, which keeps the request the provider really
built) and the key set (`google_oauth._jwks`, so PyJWT opens no connection). So what is proved is
what Google would receive, and that the routes accept what the real provider hands back.

1. **The redirect URI is the configured one however the request is made.** The authorization URL
   carries the setting `GOOGLE_OAUTH_REDIRECT_URL` and so does the token request, though both
   requests to the API name another host (`Host`, `X-Forwarded-Host`) and a `redirect_uri` of their
   own in the query. Google refuses a code whose redirect URI is not the one the authorization
   named, so a mismatch would be a broken sign-in; one taken from the request would be an open
   redirect.
2. **PKCE at the wire.** The verifier Google receives at the token endpoint hashes (S256, worked out
   here and not by the product) to the challenge in the authorization URL, and the browser is sent
   it nowhere.
3. **`email_verified` as a string.** The claim is the one the linking policy turns into the right to
   take a password account over, so a token whose claim is the string "false" must end at
   `email-unverified` with the account untouched, through the code production runs.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from httpx import AsyncClient, Response
from pydantic import SecretStr

from avp_api.config import Settings, get_settings
from avp_api.deps import SettingsDep, google_provider
from avp_api.services import google_oauth as g
from test_google_sign_in import (
    BASE,
    PASSWORD,
    landed,
    landed_ticket,
    password_login,
    s256,
    sign_up_with_password,
    user_row,
)

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
# Unlike the redirect URI of the other Google tests, this one is derived from nothing the request
# says and equals no other setting, so equality with it means it was read from the setting.
CONFIGURED_REDIRECT = "https://api.configured.example/api/v1/auth/google/callback"
FOREIGN_HOST = "evil.example"


@dataclass(frozen=True)
class _SigningKey:
    key: Any


class GoogleOnTheWire:
    """Everything Google would be, for one test: its signing key, its token endpoint, and a record
    of what reached each."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.kid = "wire-kid"
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # Read from the authorization URL, as Google reads it, and put in the token it issues.
        self.nonce: str | None = None
        # The person the token it issues is about; a test sets them before the callback.
        self.email = "wire-person@example.com"
        self.email_verified: Any = True
        self.token_requests: list[httpx.Request] = []
        # What each request the API served looked like where the provider was chosen.
        self.seen: list[dict[str, Any]] = []

    # The stand-in for the key set `google_oauth._jwks()` returns.
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        assert jwt.get_unverified_header(token)["kid"] == self.kid
        return _SigningKey(self.private.public_key())

    def token_endpoint(self, request: httpx.Request) -> httpx.Response:
        self.token_requests.append(request)
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": self.settings.google_oauth_client_id,
            "sub": "sub-wire",
            "email": self.email,
            "email_verified": self.email_verified,
            "name": "Wire Person",
            "nonce": self.nonce,
            "iat": now,
            "exp": now + 300,
        }
        id_token = jwt.encode(
            {k: v for k, v in claims.items() if v is not None},
            self.private,
            algorithm="RS256",
            headers={"kid": self.kid},
        )
        return httpx.Response(200, json={"id_token": id_token})


@pytest_asyncio.fixture
async def wire(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[GoogleOnTheWire]:
    """A configured deployment whose provider is the real one, talking to `GoogleOnTheWire`."""
    app = client._transport.app  # noqa: SLF001 - the ASGI app under test
    configured = settings.model_copy(
        update={
            "google_oauth_client_id": CLIENT_ID,
            # `model_copy(update=...)` does not validate, so the secret is wrapped here as the
            # settings wrap it: the real provider reads it with `get_secret_value()`.
            "google_oauth_client_secret": SecretStr("not-a-real-secret"),
            "google_oauth_redirect_url": CONFIGURED_REDIRECT,
        }
    )
    google = GoogleOnTheWire(configured)
    http = httpx.AsyncClient(transport=httpx.MockTransport(google.token_endpoint))

    def real_provider(request: Request, settings_: SettingsDep) -> g.LiveGoogleProvider:
        google.seen.append(
            {
                "host": request.headers.get("host"),
                "forwarded_host": request.headers.get("x-forwarded-host"),
                "query": dict(request.query_params),
            }
        )
        return g.LiveGoogleProvider(settings_, http=http)

    monkeypatch.setattr(g, "_jwks", lambda: google)
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[google_provider] = real_provider
    yield google
    await http.aclose()


async def through_the_wire(
    client: AsyncClient,
    wire: GoogleOnTheWire,
    *,
    headers: dict[str, str] | None = None,
    stray: dict[str, str] | None = None,
) -> tuple[Response, dict[str, list[str]], Response]:
    """Press the button and come back from Google with a code, as the browser does: the start,
    then the callback with the state from the authorization URL, each carrying `headers` and the
    `stray` query parameters. Returns the start's response, the authorization URL's query, and the
    callback's response."""
    stray = stray or {}
    started = await client.get(
        f"{BASE}/auth/google/start", params=stray, headers=headers, follow_redirects=False
    )
    assert started.status_code == 302, started.text
    authorization = parse_qs(urlparse(started.headers["location"]).query)
    wire.nonce = authorization["nonce"][0]
    resp = await client.get(
        f"{BASE}/auth/google/callback",
        params={"code": "code-from-google", "state": authorization["state"][0], **stray},
        headers=headers,
        follow_redirects=False,
    )
    return started, authorization, resp


async def test_the_redirect_uri_is_the_configured_one_whatever_the_request_says(
    client: AsyncClient, wire: GoogleOnTheWire
) -> None:
    stray = {"redirect_uri": "https://evil.example/take-the-code"}
    foreign = {
        "Host": FOREIGN_HOST,
        "X-Forwarded-Host": FOREIGN_HOST,
        "X-Forwarded-Proto": "https",
    }
    started, authorization, resp = await through_the_wire(
        client, wire, headers=foreign, stray=stray
    )

    # Both requests said otherwise, and the API heard it: the equalities below are not an accident
    # of a header that never arrived.
    assert started.status_code == 302
    assert len(wire.seen) == 2
    for seen in wire.seen:
        assert seen["host"] == FOREIGN_HOST
        assert seen["forwarded_host"] == FOREIGN_HOST
        assert seen["query"]["redirect_uri"] == stray["redirect_uri"]

    # The authorization URL names the setting, once.
    assert authorization["redirect_uri"] == [CONFIGURED_REDIRECT]
    # The callback went on to the token request (so the real provider built and sent it, and the
    # token it got back verified), and that request names the setting too.
    landed_ticket(resp)
    (token_request,) = wire.token_requests
    assert str(token_request.url) == g.TOKEN_ENDPOINT
    assert parse_qs(token_request.content.decode())["redirect_uri"] == [CONFIGURED_REDIRECT]


async def test_google_is_sent_a_verifier_that_hashes_to_the_challenge_it_was_given(
    client: AsyncClient, wire: GoogleOnTheWire
) -> None:
    started, authorization, resp = await through_the_wire(client, wire)

    landed_ticket(resp)
    (token_request,) = wire.token_requests
    verifier = parse_qs(token_request.content.decode())["code_verifier"][0]
    assert authorization["code_challenge_method"] == ["S256"]
    assert authorization["code_challenge"] == [s256(verifier)]
    # The verifier is the server's alone: nothing the browser was sent holds it.
    for sent in (started, resp):
        for name, value in sent.headers.multi_items():
            assert verifier not in value, name


async def test_a_token_whose_email_verified_is_the_string_false_links_nothing(
    client: AsyncClient, wire: GoogleOnTheWire, engine  # noqa: ANN001
) -> None:
    """The route-level case for the claim read as `bool()`: `bool("false")` is True, and a verified
    address that matches a password account removes its password and ends its sessions. Here the
    real provider reads the token and the sign-in ends at `email-unverified`."""
    email = "victim@example.com"
    await sign_up_with_password(client, email)
    wire.email, wire.email_verified = email, "false"
    _, _, resp = await through_the_wire(client, wire)

    assert landed(resp, "/") == {"google": ["error"], "reason": ["email-unverified"]}
    assert len(wire.token_requests) == 1  # the code was exchanged and the token verified
    row = await user_row(engine, email)
    assert row.google_sub is None
    assert row.password_hash is not None
    assert (await password_login(client, email, PASSWORD)).status_code == 200


async def test_a_token_whose_email_verified_is_the_boolean_true_links_the_same_account(
    client: AsyncClient, wire: GoogleOnTheWire, engine  # noqa: ANN001
) -> None:
    """The control for the test above: the same account, flow and harness, and a real boolean. It
    links, so the refusal above is the claim's and not a harness that cannot sign anybody in."""
    email = "victim@example.com"
    await sign_up_with_password(client, email)
    wire.email, wire.email_verified = email, True
    _, _, resp = await through_the_wire(client, wire)

    landed(resp, "/dashboard")
    row = await user_row(engine, email)
    assert row.google_sub == "sub-wire"
    assert row.password_hash is None

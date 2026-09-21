"""`LiveGoogleProvider`, the half the suite's fake stands in for — 2026-09-21.

Until this file the exchange with Google and the verification of an ID
token ran against nothing at all (the Epic 20 build-log entry says so).
Here the token endpoint is a fake transport and the JWKS is a key this
test minted, so every branch of `verify_id_token` and `exchange` is
exercised without a network: the RS256 pin, the audience, the issuer, the
expiry, the required claims, the missing `id_token`, the refused exchange
and the transport failure. `scripts/verify_google_live.py` is the other
half — the same code against Google's real discovery document, JWKS and
token endpoint.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from avp_api.config import Settings
from avp_api.services import google_oauth as g

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
REDIRECT = "http://localhost:8000/api/v1/auth/google/callback"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None, environment="test",
        database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        google_oauth_client_id=CLIENT_ID,
        google_oauth_client_secret="GOCSPX-not-a-real-secret",
        google_oauth_redirect_url=REDIRECT,
    )


class _Key:
    """A signing key and a stand-in for the JWKS client that serves it."""

    def __init__(self) -> None:
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-kid"

    def get_signing_key_from_jwt(self, token: str):  # noqa: ANN201 - PyJWKClient's shape
        header = jwt.get_unverified_header(token)
        if header.get("kid") != self.kid:
            raise jwt.PyJWKClientError("kid not found")

        class _Signing:
            key = self.private.public_key()

        return _Signing()

    def token(self, **overrides: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "sub-dana",
            "email": "dana@northlight.example", "email_verified": True,
            "name": "Dana Whitfield", "nonce": "nonce-1", "iat": now, "exp": now + 300,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> _Key:
    k = _Key()
    monkeypatch.setattr(g, "_jwks", lambda: k)
    return k


class TestTheAuthorizationUrl:
    def test_it_carries_everything_the_flow_needs(self, settings: Settings) -> None:
        url = g.LiveGoogleProvider(settings).authorization_url(
            state="s1", nonce="n1", code_challenge="c1"
        )
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert f"{u.scheme}://{u.netloc}{u.path}" == g.AUTHORIZATION_ENDPOINT
        assert q["client_id"] == CLIENT_ID
        assert q["redirect_uri"] == REDIRECT
        assert q["response_type"] == "code"
        assert q["scope"] == "openid email profile"
        assert q["state"] == "s1" and q["nonce"] == "n1"
        assert q["code_challenge"] == "c1" and q["code_challenge_method"] == "S256"
        assert q["prompt"] == "select_account"


class TestVerifyIdToken:
    def test_a_good_token_yields_the_identity_and_its_nonce(
        self, settings: Settings, key: _Key
    ) -> None:
        identity = g.LiveGoogleProvider(settings).verify_id_token(key.token())
        assert identity == g.GoogleIdentity(
            sub="sub-dana", email="dana@northlight.example", email_verified=True,
            name="Dana Whitfield", nonce="nonce-1",
        )

    def test_the_legacy_issuer_form_is_accepted(self, settings: Settings, key: _Key) -> None:
        identity = g.LiveGoogleProvider(settings).verify_id_token(
            key.token(iss="accounts.google.com")
        )
        assert identity.sub == "sub-dana"

    @pytest.mark.parametrize(
        ("overrides", "why"),
        [
            ({"aud": "someone-else.apps.googleusercontent.com"}, "audience"),
            ({"iss": "https://evil.example"}, "issuer"),
            ({"exp": int(time.time()) - 60}, "expired"),
            ({"sub": None}, "no sub"),
            ({"exp": None}, "no exp"),
        ],
    )
    def test_a_bad_claim_is_refused(
        self, settings: Settings, key: _Key, overrides: dict, why: str
    ) -> None:
        with pytest.raises(g.GoogleExchangeError):
            g.LiveGoogleProvider(settings).verify_id_token(key.token(**overrides))

    def test_a_token_signed_by_a_key_google_did_not_issue_is_refused(
        self, settings: Settings, key: _Key
    ) -> None:
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "x",
             "iat": int(time.time()), "exp": int(time.time()) + 300},
            stranger, algorithm="RS256", headers={"kid": key.kid},
        )
        with pytest.raises(g.GoogleExchangeError):
            g.LiveGoogleProvider(settings).verify_id_token(forged)

    def test_hmac_over_the_public_key_and_alg_none_are_refused(
        self, settings: Settings, key: _Key
    ) -> None:
        """The RS256 pin: neither an HS256 token keyed with the public key nor
        an unsigned one gets as far as a claim. Both are built by hand —
        PyJWT's own encoder refuses to HMAC with an asymmetric key, which is
        the same guard, one layer down."""
        import base64
        import hashlib
        import hmac
        import json

        from cryptography.hazmat.primitives import serialization

        def b64(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

        claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "x",
                  "iat": int(time.time()), "exp": int(time.time()) + 300}
        body = b64(json.dumps(claims).encode())

        unsigned = f"{b64(json.dumps({'alg': 'none', 'kid': key.kid}).encode())}.{body}."
        with pytest.raises(g.GoogleExchangeError):
            g.LiveGoogleProvider(settings).verify_id_token(unsigned)

        pem = key.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": key.kid}).encode())
        signing_input = f"{head}.{body}".encode()
        sig = b64(hmac.new(pem, signing_input, hashlib.sha256).digest())
        with pytest.raises(g.GoogleExchangeError):
            g.LiveGoogleProvider(settings).verify_id_token(f"{head}.{body}.{sig}")


class TestExchange:
    def _provider(self, settings: Settings, handler, sent: dict | None = None):  # noqa: ANN001, ANN202
        def wrapped(request: httpx.Request) -> httpx.Response:
            if sent is not None:
                sent["url"] = str(request.url)
                sent["form"] = dict(parse_qs(request.content.decode()))
            return handler(request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
        return g.LiveGoogleProvider(settings, http=http)

    async def test_the_code_and_verifier_are_exchanged_and_the_token_verified(
        self, settings: Settings, key: _Key
    ) -> None:
        sent: dict = {}
        provider = self._provider(
            settings, lambda r: httpx.Response(200, json={"id_token": key.token()}), sent
        )
        identity = await provider.exchange(code="code-1", code_verifier="verifier-1")
        assert identity.email == "dana@northlight.example" and identity.nonce == "nonce-1"
        assert sent["url"] == g.TOKEN_ENDPOINT
        form = {k: v[0] for k, v in sent["form"].items()}
        assert form["grant_type"] == "authorization_code"
        assert form["code"] == "code-1" and form["code_verifier"] == "verifier-1"
        assert form["client_id"] == CLIENT_ID and form["redirect_uri"] == REDIRECT
        assert form["client_secret"] == "GOCSPX-not-a-real-secret"

    async def test_a_refused_exchange_is_an_exchange_error(
        self, settings: Settings, key: _Key
    ) -> None:
        provider = self._provider(
            settings, lambda r: httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(g.GoogleExchangeError):
            await provider.exchange(code="bad", code_verifier="v")

    async def test_a_200_without_an_id_token_is_an_exchange_error(
        self, settings: Settings, key: _Key
    ) -> None:
        provider = self._provider(
            settings, lambda r: httpx.Response(200, json={"access_token": "x"})
        )
        with pytest.raises(g.GoogleExchangeError):
            await provider.exchange(code="c", code_verifier="v")

    async def test_a_transport_failure_is_an_exchange_error(
        self, settings: Settings, key: _Key
    ) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        provider = self._provider(settings, down)
        with pytest.raises(g.GoogleExchangeError):
            await provider.exchange(code="c", code_verifier="v")

    async def test_a_bad_token_from_a_good_exchange_is_an_exchange_error(
        self, settings: Settings, key: _Key
    ) -> None:
        provider = self._provider(
            settings,
            lambda r: httpx.Response(200, json={"id_token": key.token(aud="other-client")}),
        )
        with pytest.raises(g.GoogleExchangeError):
            await provider.exchange(code="c", code_verifier="v")

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
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import structlog
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from avp_api.config import GOOGLE_ID_TOKEN_LEEWAY_RANGE, Settings
from avp_api.services import google_oauth as g

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
REDIRECT = "http://localhost:8000/api/v1/auth/google/callback"


def _rejections(events: list[dict[str, Any]]) -> list[object]:
    """The reasons `verify_id_token` logged for the tokens it refused."""
    return [e["reason"] for e in events if e["event"] == "google.id_token.rejected"]


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
        ("claim", "verified"),
        [
            (True, True),
            ("true", True),  # the form Google's documentation also shows
            (False, False),
            ("false", False),
            (1, False),  # `1 == True` in Python: only `is True` keeps it out
            ("1", False),
            ("True", False),
            ("yes", False),
            (0, False),
            (None, False),  # absent: `key.token` drops a claim given as None
        ],
        ids=[
            "true", "string-true", "false", "string-false", "one", "string-one",
            "string-True", "yes", "zero", "absent",
        ],
    )
    def test_email_verified_counts_only_when_it_is_exactly_true(
        self, settings: Settings, key: _Key, claim: object, verified: bool
    ) -> None:
        """Only Google's JSON boolean `true` (or the string "true") vouches for the address.
        `bool()` read the string "false" and the number 1 as yes, and the linking policy turns
        this one claim into the right to take a password account over. Anything else, and a token
        that leaves the claim out, is an address nobody has vouched for."""
        identity = g.LiveGoogleProvider(settings).verify_id_token(
            key.token(email_verified=claim)
        )
        assert identity.email_verified is verified

    def test_a_token_with_no_nonce_yields_no_nonce(self, settings: Settings, key: _Key) -> None:
        """The other half of "a missing nonce is refused": a token that carries none reaches the
        callback as None, which `test_google_sign_in.py` proves is refused, and not as a value."""
        identity = g.LiveGoogleProvider(settings).verify_id_token(key.token(nonce=None))
        assert identity.nonce is None

    @pytest.mark.parametrize(
        ("overrides", "why"),
        [
            ({"aud": "someone-else.apps.googleusercontent.com"}, "audience"),
            ({"iss": "https://evil.example"}, "issuer"),
            # Ten minutes ago: past the largest allowance the setting can be given (300 s), so the
            # case does not sit on the edge of whatever the allowance is.
            ({"exp": int(time.time()) - 600}, "expired"),
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


class TestTheClockAllowance:
    """`iat` and `exp` are checked with `google_id_token_leeway_seconds` of allowance (the security
    review's L2). `iat` is Google's second, floored, so with none a host clock a second behind
    Google's refuses a token issued just now, and a Google-only account has no other way in.

    The allowance is passed in explicitly (a minute, the default), so no test depends on the
    environment, and the times keep 30 s of margin either side of it, so none depends on which side
    of a second it happens to run."""

    def _provider(self, settings: Settings, seconds: int = 60) -> g.LiveGoogleProvider:
        return g.LiveGoogleProvider(
            settings.model_copy(update={"google_id_token_leeway_seconds": seconds})
        )

    def test_a_token_issued_30_seconds_ahead_of_our_clock_verifies(
        self, settings: Settings, key: _Key
    ) -> None:
        now = int(time.time())
        identity = self._provider(settings).verify_id_token(key.token(iat=now + 30, exp=now + 330))
        assert identity.sub == "sub-dana"

    def test_a_token_issued_past_the_allowance_ahead_is_refused(
        self, settings: Settings, key: _Key
    ) -> None:
        now = int(time.time())
        with structlog.testing.capture_logs() as events, pytest.raises(g.GoogleExchangeError):
            self._provider(settings).verify_id_token(key.token(iat=now + 90, exp=now + 390))
        assert _rejections(events) == ["ImmatureSignatureError"]

    def test_a_token_that_expired_30_seconds_ago_verifies(
        self, settings: Settings, key: _Key
    ) -> None:
        now = int(time.time())
        identity = self._provider(settings).verify_id_token(key.token(iat=now - 330, exp=now - 30))
        assert identity.sub == "sub-dana"

    def test_a_token_that_expired_past_the_allowance_is_refused(
        self, settings: Settings, key: _Key
    ) -> None:
        now = int(time.time())
        with structlog.testing.capture_logs() as events, pytest.raises(g.GoogleExchangeError):
            self._provider(settings).verify_id_token(key.token(iat=now - 390, exp=now - 90))
        assert _rejections(events) == ["ExpiredSignatureError"]

    def test_with_no_allowance_a_token_seconds_ahead_is_refused(
        self, settings: Settings, key: _Key
    ) -> None:
        """Zero is PyJWT's own behaviour. The token is ten seconds ahead, not one or two: `iat` is a
        whole second, and a loaded runner can stall for a second or more between making the token
        and checking it, which would let a token only just ahead pass with no allowance at all."""
        now = int(time.time())
        ahead = key.token(iat=now + 10, exp=now + 310)
        with structlog.testing.capture_logs() as events, pytest.raises(g.GoogleExchangeError):
            self._provider(settings, seconds=0).verify_id_token(ahead)
        assert _rejections(events) == ["ImmatureSignatureError"]
        # The very same token, under a minute's allowance.
        assert self._provider(settings).verify_id_token(ahead).sub == "sub-dana"


class TestTheLeewaySetting:
    def test_the_default_is_a_minute(self) -> None:
        assert Settings.model_fields["google_id_token_leeway_seconds"].default == 60

    def test_the_range_is_nought_to_five_minutes(self) -> None:
        assert GOOGLE_ID_TOKEN_LEEWAY_RANGE == (0, 300)

    def test_each_bound_is_accepted_and_one_past_it_is_refused_naming_the_setting(self) -> None:
        for good in (0, 300):
            built = Settings(_env_file=None, google_id_token_leeway_seconds=good)
            assert built.google_id_token_leeway_seconds == good
        for bad in (-1, 301):
            with pytest.raises(ValidationError) as refused:
                Settings(_env_file=None, google_id_token_leeway_seconds=bad)
            assert "google_id_token_leeway_seconds" in str(refused.value), bad

    def test_the_variable_sets_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_ID_TOKEN_LEEWAY_SECONDS", "5")
        assert Settings(_env_file=None).google_id_token_leeway_seconds == 5


class _GoogleKeySet(jwt.PyJWKClient):
    """The real `jwt.PyJWKClient`, built as `google_oauth._jwks()` builds it, with the one method
    that opens a connection answered by the test.

    `_Key` above stands in for the whole client and raises the errors its author chose. This is
    PyJWT's own client: its lookup by `kid`, the one forced refresh when a `kid` is not in the set,
    and its errors, so a token naming a key the set does not hold fails here as it fails in
    production. `fetch_data` is what would call Google; it answers from the test instead, and so
    skips the key-set cache it would also fill. That cache is not exercised here."""

    def __init__(self, answer: Callable[[], Any]) -> None:
        super().__init__(g.JWKS_URI, cache_keys=True)
        self._answer = answer
        self.fetches = 0

    def fetch_data(self) -> Any:
        self.fetches += 1
        return self._answer()


class TestTheKeySetIsPyJWTs:
    def _jwk(self, key: _Key) -> dict[str, Any]:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(key.private.public_key(), as_dict=True)
        return {**public, "kid": key.kid, "use": "sig", "alg": "RS256"}

    def _signed(self, key: _Key, kid: str | None) -> str:
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "sub-dana",
            "iat": now, "exp": now + 300,
        }
        headers = None if kid is None else {"kid": kid}
        return jwt.encode(claims, key.private, algorithm="RS256", headers=headers)

    def test_a_kid_the_set_holds_verifies_through_the_real_client(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control: the same client, key and token, and the set holds the kid. So the refusals
        below are the kid's and the fetch's, and not a harness that cannot verify anything."""
        key = _Key()
        keyset = _GoogleKeySet(lambda: {"keys": [self._jwk(key)]})
        monkeypatch.setattr(g, "_jwks", lambda: keyset)
        identity = g.LiveGoogleProvider(settings).verify_id_token(self._signed(key, key.kid))
        assert identity.sub == "sub-dana"
        assert keyset.fetches == 1

    @pytest.mark.parametrize(
        "kid", ["a-kid-google-never-published", None], ids=["unknown-kid", "no-kid"]
    )
    def test_a_token_naming_no_key_the_set_holds_is_refused(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch, kid: str | None
    ) -> None:
        """PyJWT answers a `kid` it cannot find, after one refresh, with `PyJWKClientError`: a
        `PyJWTError`, but not an `InvalidTokenError`. Only a refusal that catches the base class
        turns it into the exchange's one error, so that the browser is told `exchange-failed`;
        narrower, it escapes as an unhandled error and the callback's catch-all answers
        `unavailable`, with a logged traceback, for what is a token Google did not sign for us."""
        key = _Key()
        keyset = _GoogleKeySet(lambda: {"keys": [self._jwk(key)]})
        monkeypatch.setattr(g, "_jwks", lambda: keyset)
        with structlog.testing.capture_logs() as events, pytest.raises(g.GoogleExchangeError):
            g.LiveGoogleProvider(settings).verify_id_token(self._signed(key, kid))
        assert _rejections(events) == ["PyJWKClientError"]
        assert keyset.fetches == 2  # the set, then PyJWT's one forced refresh before it gives up

    @pytest.mark.parametrize(
        "failure",
        [URLError("no route to host"), TimeoutError("timed out")],
        ids=["url-error", "timeout"],
    )
    def test_a_key_set_that_cannot_be_fetched_is_refused(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch, failure: Exception
    ) -> None:
        """Google's key endpoint unreachable. PyJWT's OWN `fetch_data` runs (nothing here stands in
        for it) and only the connection beneath it fails: it turns `URLError` and `TimeoutError`
        into `PyJWKClientConnectionError`, another `PyJWTError` that is not an `InvalidTokenError`,
        and `verify_id_token` into the exchange's one error. A token that could not be checked is
        refused as one that failed the check is."""
        key = _Key()
        asked: list[str] = []

        def unreachable(request: urllib.request.Request, *args: object, **kwargs: object) -> None:
            asked.append(request.full_url)
            raise failure

        monkeypatch.setattr(urllib.request, "urlopen", unreachable)
        keyset = jwt.PyJWKClient(g.JWKS_URI, cache_keys=True)
        monkeypatch.setattr(g, "_jwks", lambda: keyset)
        with structlog.testing.capture_logs() as events, pytest.raises(g.GoogleExchangeError):
            g.LiveGoogleProvider(settings).verify_id_token(self._signed(key, key.kid))
        assert _rejections(events) == ["PyJWKClientConnectionError"]
        assert asked == [g.JWKS_URI]


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

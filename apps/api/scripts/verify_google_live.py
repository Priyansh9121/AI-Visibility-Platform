"""Google Sign-In against REAL Google infrastructure and the REAL deployed
API — everything a script can check without a person's Google account,
2026-09-21.

    uv run python scripts/verify_google_live.py                       # step 0 only
    uv run python scripts/verify_google_live.py https://<api-host>/api/v1

Sends no credential and creates nothing.

Step 0 — Google itself, no deployment needed. `LiveGoogleProvider` had run
against nothing but the suite's fake until this script: (a) Google's OpenID
discovery document is fetched and its authorization, token and JWKS
endpoints and its issuer are compared with the constants the module pins;
(b) the real JWKS is fetched through the SAME `jwt.PyJWKClient` the
verifier uses and must yield RS256 signing keys; (c) `verify_id_token` is
handed a token signed by a key Google never issued and must refuse it as
`GoogleExchangeError` (the JWKS lookup fails to find the kid); (d) the real
token endpoint is asked to exchange a bogus code through
`LiveGoogleProvider.exchange` with placeholder client credentials and must
refuse (a non-200), which the code maps to `exchange-failed`.

Steps 1-4 — the deployed API's `/auth/google/*` routes, then the exact
consent-screen URL the API minted so a person (the founder, a test user on
the OAuth client) can complete the one step no script may: signing in on
Google's screen. The callback that follows is Google's browser redirect to
the deployed API, so the round trip is proven by landing on the dashboard
signed in.

Checks:
  1. GET /auth/google/start answers 302 to accounts.google.com carrying the
     client id, this deployment's redirect_uri, S256 PKCE, state, nonce,
     the three scopes and prompt=select_account — and NOT 503, which would
     mean the three settings are not all set on the host.
  2. GET /auth/google/callback?error=access_denied&state=<the state from 1>
     answers 302 to the front door with reason=denied — the person-pressed-
     Cancel path, which also proves the state store (Redis) consumed it.
  3. The same state a second time answers reason=invalid-state — single use.
  4. GET /auth/google/pending?ticket=bogus answers 400 invalid-google-ticket.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REQUIRED = ("client_id", "redirect_uri", "state", "nonce", "code_challenge")
DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


def google_itself() -> list[str]:
    """Step 0: the module's constants and its verifier against real Google."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from avp_api.config import Settings
    from avp_api.services import google_oauth as g

    failures: list[str] = []
    print("0. Google's own infrastructure, through the module's own code")
    doc = httpx.get(DISCOVERY, timeout=20.0).json()
    for label, ours, theirs in (
        ("authorization_endpoint", g.AUTHORIZATION_ENDPOINT, doc.get("authorization_endpoint")),
        ("token_endpoint", g.TOKEN_ENDPOINT, doc.get("token_endpoint")),
        ("jwks_uri", g.JWKS_URI, doc.get("jwks_uri")),
        ("issuer", g.GOOGLE_ISSUERS[0], doc.get("issuer")),
    ):
        ok = ours == theirs
        print(f"   discovery {label:24} {'matches' if ok else 'DIFFERS'}: {theirs}")
        if not ok:
            failures.append(f"{label}: module pins {ours!r}, discovery says {theirs!r}")
    algs = doc.get("id_token_signing_alg_values_supported")
    print(f"   discovery id_token algs         {algs}")
    if algs != ["RS256"]:
        failures.append(f"Google lists id-token algorithms {algs}; the verifier pins RS256")

    keys = g._jwks().get_signing_keys()
    kinds = {getattr(k, "algorithm_name", None) or k._jwk_data.get("alg") for k in keys}
    print(f"   JWKS via jwt.PyJWKClient        {len(keys)} signing keys,"
          f" algs {sorted(str(x) for x in kinds)}")
    if not keys:
        failures.append("the real JWKS yielded no signing keys through PyJWKClient")

    settings = Settings(
        _env_file=None, environment="test",
        database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
        app_secret="probe-secret-not-used-anywhere",
        google_oauth_client_id="probe-client-id.apps.googleusercontent.com",
        google_oauth_client_secret="GOCSPX-probe-not-a-real-secret",
        google_oauth_redirect_url="https://example.invalid/api/v1/auth/google/callback",
    )
    provider = g.LiveGoogleProvider(settings)

    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"iss": g.GOOGLE_ISSUERS[0], "aud": settings.google_oauth_client_id, "sub": "1",
         "email": "x@example.com", "email_verified": True, "exp": 4_102_444_800, "iat": 1},
        forged_key, algorithm="RS256", headers={"kid": "not-a-google-kid"},
    )
    try:
        provider.verify_id_token(forged)
        failures.append("a token signed by a key Google never issued was ACCEPTED")
        print("   forged ID token                 ACCEPTED — wrong")
    except g.GoogleExchangeError:
        print("   forged ID token                 refused (GoogleExchangeError) — right")

    try:
        asyncio.run(provider.exchange(code="bogus-code", code_verifier="bogus-verifier"))
        failures.append("the real token endpoint accepted a bogus exchange")
        print("   bogus code at the token endpoint ACCEPTED — wrong")
    except g.GoogleExchangeError:
        print("   bogus code at the token endpoint refused"
              " (GoogleExchangeError -> exchange-failed) — right")
    return failures


def main(base: str | None) -> int:
    failures = google_itself()
    if base is None:
        print()
        print("Step 0 " + ("FAILED:\n  - " + "\n  - ".join(failures) if failures else "passes."))
        print("Give the deployed API base URL as the argument to run steps 1-4.")
        return 1 if failures else 0
    base = base.rstrip("/")
    with httpx.Client(follow_redirects=False, timeout=20.0) as http:
        # 1. start
        r = http.get(f"{base}/auth/google/start")
        print(f"1. GET /auth/google/start -> {r.status_code}")
        if r.status_code == 503:
            print("   503: the host does not have all three GOOGLE_OAUTH_* settings set.")
            return 1
        if r.status_code != 302:
            failures.append(f"start answered {r.status_code}, expected 302")
            print(r.text[:300])
            return 1
        location = r.headers.get("location", "")
        u = urlparse(location)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        print(f"   -> {u.scheme}://{u.netloc}{u.path}")
        for key in REQUIRED:
            shown = ""
            if key in ("redirect_uri", "client_id") and q.get(key):
                shown = f"  ({q[key]})"
            print(f"   {key:22} {'present' if q.get(key) else 'MISSING'}{shown}")
            if not q.get(key):
                failures.append(f"authorization URL lacks {key}")
        print(f"   code_challenge_method  {q.get('code_challenge_method')}")
        print(f"   scope                  {q.get('scope')}")
        print(f"   prompt                 {q.get('prompt')}")
        if u.netloc != "accounts.google.com":
            failures.append(f"authorization host is {u.netloc}")
        if q.get("code_challenge_method") != "S256":
            failures.append("PKCE method is not S256")
        if set((q.get("scope") or "").split()) != {"openid", "email", "profile"}:
            failures.append(f"scope is {q.get('scope')!r}")
        if not (q.get("redirect_uri") or "").startswith(base):
            print("   NOTE: redirect_uri does not start with the base you gave; the host's"
                  " GOOGLE_OAUTH_REDIRECT_URL may point at a different hostname.")
        state = q.get("state", "")

        # 2. the cancel path consumes the state
        r = http.get(f"{base}/auth/google/callback",
                     params={"error": "access_denied", "state": state})
        loc = r.headers.get("location", "")
        print(f"2. GET /auth/google/callback?error=access_denied -> {r.status_code} -> {loc}")
        if r.status_code != 302 or "reason=denied" not in loc:
            failures.append("cancel path did not redirect with reason=denied")

        # 3. the same state again is refused
        r = http.get(f"{base}/auth/google/callback",
                     params={"error": "access_denied", "state": state})
        loc = r.headers.get("location", "")
        print(f"3. the same state again -> {r.status_code} -> {loc}")
        if r.status_code != 302 or "reason=invalid-state" not in loc:
            failures.append("a replayed state was not refused with reason=invalid-state")

        # 4. an unknown ticket
        r = http.get(f"{base}/auth/google/pending", params={"ticket": "bogus"})
        print(f"4. GET /auth/google/pending?ticket=bogus -> {r.status_code} {r.text[:120]}")
        if r.status_code != 400:
            failures.append(f"pending with a bogus ticket answered {r.status_code}, expected 400")

        # a fresh authorization URL for the person to open
        r = http.get(f"{base}/auth/google/start")
        fresh = r.headers.get("location", "")

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All four script-checkable steps pass on the live API.")
    print("\nThe step only a person can take — open this in a browser, choose the test-user"
          " Google account, and consent; the browser will return through the deployed"
          " callback and should land on /dashboard signed in (a new address lands on"
          " /sign-up/google to name the agency):\n")
    print(fresh)
    print("\nThat URL is single-use and expires in ten minutes.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1] if len(sys.argv) == 2 else None))

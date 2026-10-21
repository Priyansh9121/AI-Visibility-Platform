"""The session cookie is first-party: SameSite=Lax, no Domain, `__Host-` when Secure.

The web app serves the API under its own origin (`/api/v1`), so the browser
never sends a cross-site request and the cookie needs no `SameSite=None` and no
`Domain`. A deployed environment forces Secure on, which is what makes the
`__Host-` prefix valid; local http development keeps the bare name.

A3 part 4 adds two proofs. The flags are asserted from the real `Set-Cookie` of a password sign-in
under settings that went THROUGH validation as a production start does (`deployed`, below):
`secure_client` turns Secure on with `model_copy`, which runs no validator, so it proves what the
route does with Secure on and not that a deployed environment turns it on. And logout is proved to
end the session on the server: the old cookie, carried by hand, is a 401, and Redis holds neither
the record nor its place in the person's list. And when Redis fails mid-logout the answer is the 503
and nothing changes: no Set-Cookie, and the session still works until a retry succeeds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from avp_api import redis_client as redis_module
from avp_api.config import Settings, get_settings
from avp_api.deps import redis_client
from avp_api.logs import configure_logging
from avp_api.main import create_app
from avp_api.security import token_digest
from avp_api.sessions import SESSION_PREFIX, USER_SESSIONS_PREFIX, SessionStore

BASE = "/api/v1"
DEPLOYED_NAME = "__Host-avp_session"
LOCAL_NAME = "avp_session"
# What an unknown, revoked or expired session is told (`deps.current_principal`); a request that
# carried no cookie at all is told something else, so this also proves the cookie WAS carried.
GONE = "Session is expired or invalid."
# 50 bytes: a deployed start refuses anything under 32 (`config.APP_SECRET_MIN_BYTES`).
DEPLOYED_SECRET = "a-real-secret-of-sufficient-length-set-by-the-host"
# The absolute lifetime at its default: 14 days, in seconds.
FOURTEEN_DAYS = 1_209_600


@pytest_asyncio.fixture
async def secure_client(client: AsyncClient, settings: Settings) -> AsyncIterator[AsyncClient]:
    """A client for an app with the Secure flag on, over https, on the test stores.

    Depends on `client` for the datastore wiring it does, and for the Origin it sends.
    """
    secure = settings.model_copy(update={"session_cookie_secure": True})
    app = create_app(secure)
    app.dependency_overrides[get_settings] = lambda: secure
    app.dependency_overrides[redis_client] = lambda: redis_module.get_redis(secure)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        headers={"Origin": client.headers["Origin"]},
    ) as c:
        yield c


@pytest.fixture
def deployed(client: AsyncClient, settings: Settings) -> Settings:
    """What a production start makes of the suite's settings: built, not copied.

    `model_copy(update=...)` runs no validator, so a test on its result cannot tell whether a
    deployed environment turns Secure on. This goes through `Settings(...)`: `environment` is
    `production`, `session_cookie_secure=False` is passed explicitly (as a host's
    `SESSION_COOKIE_SECURE=false` would be) and `_harden_deployed_environments` has to force it,
    the secret is a real one (32 bytes or more is required), and the one origin the suite's client
    sends is the one listed. Everything else is the suite's own, so the stores are the test ones.
    """
    return Settings(
        **{
            **settings.model_dump(),
            "_env_file": None,
            "environment": "production",
            "session_cookie_secure": False,
            "app_secret": DEPLOYED_SECRET,
            "cors_allow_origins": [client.headers["Origin"]],
        }
    )


@pytest_asyncio.fixture
async def deployed_client(
    client: AsyncClient, settings: Settings, deployed: Settings
) -> AsyncIterator[AsyncClient]:
    """A client for an app built from `deployed`, over https (an http client never sends a Secure
    cookie), on the test stores. Depends on `client` for the wiring it does and for the Origin."""
    app = create_app(deployed)
    app.dependency_overrides[get_settings] = lambda: deployed
    app.dependency_overrides[redis_client] = lambda: redis_module.get_redis(deployed)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as c:
            yield c
    finally:
        # `create_app` configures logging for the environment it is given (INFO, in production):
        # give the rest of the suite its own back.
        configure_logging(settings)


@pytest_asyncio.fixture
async def redis(client: AsyncClient, settings: Settings) -> Redis:
    """The suite's test Redis; `client` is asked for so that the app is wired to it."""
    return redis_module.get_redis(settings)


def _attributes(resp: Response) -> tuple[str, list[str]]:
    """The cookie's name and its lower-cased attributes, from the one Set-Cookie."""
    header = resp.headers["set-cookie"]
    first, *rest = [part.strip() for part in header.split(";")]
    return first.split("=", 1)[0], [a.lower() for a in rest]


def _assert_first_party(attrs: list[str], *, secure: bool, max_age: int) -> None:
    assert "httponly" in attrs
    assert "samesite=lax" in attrs
    assert "path=/" in attrs
    assert ("secure" in attrs) is secure
    assert not any(a.startswith("domain") for a in attrs)
    # The ABSOLUTE lifetime (`session_ttl_seconds`), not the idle window: the browser keeps the
    # cookie for the longest a session can live, and the server decides when it stops working.
    assert f"max-age={max_age}" in attrs


async def _alive(redis: Redis, token: str) -> bool:
    """Is there a record for this token in Redis? Read from Redis itself, not through the store."""
    return bool(await redis.exists(f"{SESSION_PREFIX}{token_digest(token)}"))


async def _indexed(redis: Redis, user_id: str, token: str) -> bool:
    """Is this token's digest in the person's list of sessions (`user-sessions:<user id>`)?"""
    return bool(await redis.sismember(f"{USER_SESSIONS_PREFIX}{user_id}", token_digest(token)))


def _carry(client: AsyncClient, settings: Settings, token: str) -> None:
    """Make the browser carry exactly this session cookie and no other, as a thief with a copy
    of it would, or the same browser after it cleared its own. On the domain a response's own
    cookie gets (the test server's), so a response that sets one replaces it."""
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_key, token, domain="testserver.local", path="/")


async def test_login_sets_the_host_prefixed_cookie_when_secure(
    secure_client: AsyncClient, settings: Settings, signup_payload: dict[str, str]
) -> None:
    resp = await secure_client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    await secure_client.post(f"{BASE}/auth/logout")
    resp = await secure_client.post(
        f"{BASE}/auth/login",
        json={"email": signup_payload["email"], "password": signup_payload["password"]},
    )
    assert resp.status_code == 200, resp.text
    name, attrs = _attributes(resp)
    assert name == DEPLOYED_NAME
    _assert_first_party(attrs, secure=True, max_age=settings.session_ttl_seconds)
    # The browser would send it back, and it signs the caller in.
    assert (await secure_client.get(f"{BASE}/auth/me")).status_code == 200


async def test_login_sets_the_bare_cookie_locally(
    client: AsyncClient, settings: Settings, signup_payload: dict[str, str]
) -> None:
    assert settings.session_cookie_secure is False
    assert settings.session_ttl_seconds == FOURTEEN_DAYS
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    name, attrs = _attributes(resp)
    assert name == LOCAL_NAME
    _assert_first_party(attrs, secure=False, max_age=settings.session_ttl_seconds)
    # And the answer to a password sign-in, the other route that sets it (A3 part 4).
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204
    resp = await client.post(
        f"{BASE}/auth/login",
        json={"email": signup_payload["email"], "password": signup_payload["password"]},
    )
    assert resp.status_code == 200, resp.text
    name, attrs = _attributes(resp)
    assert name == LOCAL_NAME
    _assert_first_party(attrs, secure=False, max_age=settings.session_ttl_seconds)


async def test_a_deployed_password_sign_in_sets_the_cookie_a_browser_will_keep(
    deployed_client: AsyncClient, deployed: Settings, signup_payload: dict[str, str]
) -> None:
    """A3 part 4. The settings went THROUGH validation: the fixture passed
    `session_cookie_secure=False` and only the deployed-environment check can have made it True."""
    assert deployed.environment == "production"
    assert deployed.session_cookie_secure is True
    assert deployed.session_ttl_seconds == FOURTEEN_DAYS

    resp = await deployed_client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    assert (await deployed_client.post(f"{BASE}/auth/logout")).status_code == 204
    resp = await deployed_client.post(
        f"{BASE}/auth/login",
        json={"email": signup_payload["email"], "password": signup_payload["password"]},
    )
    assert resp.status_code == 200, resp.text

    # The raw header, and only one cookie in it: the session's.
    assert len(resp.headers.get_list("set-cookie")) == 1
    name, attrs = _attributes(resp)
    assert name == "__Host-avp_session"
    _assert_first_party(attrs, secure=True, max_age=deployed.session_ttl_seconds)
    # The browser would send it back over https, and it signs the caller in.
    assert (await deployed_client.get(f"{BASE}/auth/me")).status_code == 200


# --- logout ends the session on the server (A3 part 4) --------------------------------------
# Already replayed elsewhere: `test_authorization_matrix.py`'s `world` fixture signs a session in
# and out, and `test_signed_out_a_protected_operation_answers_401` carries that old token to every
# protected operation (`POST /auth/logout` and `GET /auth/me` among them) and expects a 401, as
# `test_the_two_agencies_are_built_as_designed` does once more for `GET /auth/me`. Those prove the
# refusal across the whole API. They do not look at Redis, they cannot tell a cookie the server
# refused from a cookie that was never sent, and they never ask whether the same person's OTHER
# session lives. These do.


async def test_logout_ends_the_session_on_the_server_and_the_old_cookie_is_dead(
    client: AsyncClient,
    settings: Settings,
    redis: Redis,
    signup_payload: dict[str, str],
) -> None:
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    token = client.cookies.get(settings.session_cookie_key)
    assert token
    # Before: the record is in Redis and its digest is in the person's list, so the "not" below
    # means something.
    assert await _alive(redis, token)
    assert await _indexed(redis, user_id, token)

    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204

    # The server forgot it: Redis itself holds neither the record nor the digest.
    assert not await _alive(redis, token)
    assert not await _indexed(redis, user_id, token)
    # And exactly the old token, carried by hand (the answer cleared the browser's own copy), gets
    # the answer an unknown session gets, not the one for a request with no cookie.
    _carry(client, settings, token)
    me = await client.get(f"{BASE}/auth/me")
    assert (me.status_code, me.json()["detail"]) == (401, GONE)
    # A second sign-out with it is refused as well, and finds nothing to end.
    _carry(client, settings, token)
    again = await client.post(f"{BASE}/auth/logout")
    assert (again.status_code, again.json()["detail"]) == (401, GONE)
    assert not await _alive(redis, token)
    assert not await _indexed(redis, user_id, token)


async def test_logout_ends_only_its_own_session(
    client: AsyncClient,
    settings: Settings,
    redis: Redis,
    signup_payload: dict[str, str],
) -> None:
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    first = client.cookies.get(settings.session_cookie_key)
    assert first
    # The same person signs in from another browser: one that does NOT carry the first cookie (a
    # sign-in that carries one ends it, A3 part 3's rotation).
    client.cookies.clear()
    resp = await client.post(
        f"{BASE}/auth/login",
        json={"email": signup_payload["email"], "password": signup_payload["password"]},
    )
    assert resp.status_code == 200, resp.text
    second = client.cookies.get(settings.session_cookie_key)
    assert second
    assert second != first
    for token in (first, second):
        assert await _alive(redis, token)
        assert await _indexed(redis, user_id, token)

    # They sign out of the first.
    _carry(client, settings, first)
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204

    assert not await _alive(redis, first)
    assert not await _indexed(redis, user_id, first)
    # The second is untouched: its record, its place in the list, and the answer it gets.
    assert await _alive(redis, second)
    assert await _indexed(redis, user_id, second)
    _carry(client, settings, second)
    me = await client.get(f"{BASE}/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["id"] == user_id
    _carry(client, settings, first)
    gone = await client.get(f"{BASE}/auth/me")
    assert (gone.status_code, gone.json()["detail"]) == (401, GONE)


async def test_logout_when_redis_fails_changes_nothing_and_a_retry_signs_out(
    client: AsyncClient,
    settings: Settings,
    redis: Redis,
    signup_payload: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The security review's I5. The session is looked up fine and the revoke then fails. A 204
    that cleared the cookie would tell the person they are signed out while a copy of the cookie
    still works, so the answer is the existing Redis problem (503) and the cookie is left alone."""
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    token = client.cookies.get(settings.session_cookie_key)
    assert token

    async def refuse(self: SessionStore, token: str) -> None:
        raise RedisConnectionError("boom")

    with monkeypatch.context() as redis_fails:
        redis_fails.setattr(SessionStore, "revoke", refuse)
        out = await client.post(f"{BASE}/auth/logout")

    assert out.status_code == 503, out.text
    assert out.json()["type"] == "/problems/service-unavailable"
    # Nothing about the cookie was sent: not a deletion, not a new value.
    assert "set-cookie" not in out.headers
    assert client.cookies.get(settings.session_cookie_key) == token
    # Nothing was revoked: the record, its place in the person's list, and the answer it gets.
    assert await _alive(redis, token)
    assert await _indexed(redis, user_id, token)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200
    # Redis answers again, and signing out is a retry away.
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204
    assert not await _alive(redis, token)
    assert not await _indexed(redis, user_id, token)


@pytest.mark.parametrize("path", ["logout", "logout-all"])
async def test_logout_clears_the_host_prefixed_cookie_with_secure_and_path(
    secure_client: AsyncClient, signup_payload: dict[str, str], path: str
) -> None:
    resp = await secure_client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    resp = await secure_client.post(f"{BASE}/auth/{path}")
    assert resp.status_code == 204, resp.text
    name, attrs = _attributes(resp)
    # A browser ignores the deletion of a `__Host-` cookie that lacks Secure or Path=/.
    assert name == DEPLOYED_NAME
    assert "secure" in attrs
    assert "path=/" in attrs
    assert "max-age=0" in attrs
    assert not any(a.startswith("domain") for a in attrs)
    assert (await secure_client.get(f"{BASE}/auth/me")).status_code == 401


def test_stale_samesite_and_domain_settings_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".example.com")
    s = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql+asyncpg://avp:avp@db.internal:5432/avp",
        redis_url="redis://cache.internal:6379/0",
        app_secret="a-real-secret-of-sufficient-length-set-by-the-host",
        cors_allow_origins=["https://app.example.com"],
    )
    assert not hasattr(s, "session_cookie_samesite")
    assert not hasattr(s, "session_cookie_domain")
    assert s.session_cookie_key == DEPLOYED_NAME


def test_the_key_follows_the_secure_flag(settings: Settings) -> None:
    assert settings.model_copy(update={"session_cookie_secure": True}).session_cookie_key == (
        DEPLOYED_NAME
    )
    assert settings.model_copy(update={"session_cookie_secure": False}).session_cookie_key == (
        LOCAL_NAME
    )


@pytest.mark.parametrize("name", ["__Host-avp", "__sid", "has space", "semi;colon", "", "a=b"])
def test_a_session_cookie_name_that_is_not_a_plain_token_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="SESSION_COOKIE_NAME"):
        Settings(_env_file=None, session_cookie_name=name)


def test_a_plain_custom_name_is_accepted() -> None:
    s = Settings(_env_file=None, session_cookie_name="my-sid_2", session_cookie_secure=True)
    assert s.session_cookie_key == "__Host-my-sid_2"

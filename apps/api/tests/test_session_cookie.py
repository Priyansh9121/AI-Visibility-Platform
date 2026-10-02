"""The session cookie is first-party: SameSite=Lax, no Domain, `__Host-` when Secure.

The web app serves the API under its own origin (`/api/v1`), so the browser
never sends a cross-site request and the cookie needs no `SameSite=None` and no
`Domain`. A deployed environment forces Secure on, which is what makes the
`__Host-` prefix valid; local http development keeps the bare name.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response

from avp_api import redis_client as redis_module
from avp_api.config import Settings, get_settings
from avp_api.deps import redis_client
from avp_api.main import create_app

BASE = "/api/v1"
DEPLOYED_NAME = "__Host-avp_session"
LOCAL_NAME = "avp_session"


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


def _attributes(resp: Response) -> tuple[str, list[str]]:
    """The cookie's name and its lower-cased attributes, from the one Set-Cookie."""
    header = resp.headers["set-cookie"]
    first, *rest = [part.strip() for part in header.split(";")]
    return first.split("=", 1)[0], [a.lower() for a in rest]


def _assert_first_party(attrs: list[str], *, secure: bool) -> None:
    assert "httponly" in attrs
    assert "samesite=lax" in attrs
    assert "path=/" in attrs
    assert ("secure" in attrs) is secure
    assert not any(a.startswith("domain") for a in attrs)


async def test_login_sets_the_host_prefixed_cookie_when_secure(
    secure_client: AsyncClient, signup_payload: dict[str, str]
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
    _assert_first_party(attrs, secure=True)
    # The browser would send it back, and it signs the caller in.
    assert (await secure_client.get(f"{BASE}/auth/me")).status_code == 200


async def test_login_sets_the_bare_cookie_locally(
    client: AsyncClient, settings: Settings, signup_payload: dict[str, str]
) -> None:
    assert settings.session_cookie_secure is False
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    name, attrs = _attributes(resp)
    assert name == LOCAL_NAME
    _assert_first_party(attrs, secure=False)


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

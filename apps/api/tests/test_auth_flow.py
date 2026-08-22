"""End-to-end auth: the literal Epic 1 acceptance criterion.

"An agency can sign up, log in, and see an empty dashboard."
"""

from __future__ import annotations

from httpx import AsyncClient

BASE = "/api/v1"


async def test_sign_up_log_in_and_see_empty_dashboard(
    client: AsyncClient, signup_payload: dict[str, str]
) -> None:
    """The Epic 1 acceptance criterion, start to finish."""
    # --- sign up ---------------------------------------------------------
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["agency"]["name"] == "Meridian Search Partners"
    assert body["agency"]["slug"] == "meridian-search-partners"
    assert body["user"]["email"] == "dana@meridiansearch.example"
    # The first user of an agency is always the owner.
    assert body["user"]["role"] == "owner"
    assert body["seats"] == {"used": 1, "limit": 3}
    assert "avp_session" in resp.cookies

    # --- log out, so the login step is a genuine one ---------------------
    assert (await client.post(f"{BASE}/auth/logout")).status_code == 204
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    # --- log in ----------------------------------------------------------
    resp = await client.post(
        f"{BASE}/auth/login",
        json={"email": signup_payload["email"], "password": signup_payload["password"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["id"] == body["user"]["id"]

    # --- see an empty dashboard ------------------------------------------
    resp = await client.get(f"{BASE}/dashboard")
    assert resp.status_code == 200, resp.text
    dash = resp.json()
    assert dash["isEmpty"] is True
    assert dash["clientCount"] == 0
    assert dash["scanCount"] == 0
    assert dash["recentScans"] == []
    assert dash["agency"]["id"] == body["agency"]["id"]
    assert dash["seats"] == {"used": 1, "limit": 3}


async def test_response_body_is_camel_case(
    client: AsyncClient, signup_payload: dict[str, str]
) -> None:
    """api-contracts.md fixes camelCase on the wire."""
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    user = resp.json()["user"]
    assert "fullName" in user and "full_name" not in user
    assert "lastLoginAt" in user and "last_login_at" not in user
    assert "seatLimit" in resp.json()["agency"]


async def test_ids_are_prefixed_ulids(
    client: AsyncClient, signup_payload: dict[str, str]
) -> None:
    body = (await client.post(f"{BASE}/auth/sign-up", json=signup_payload)).json()
    assert body["user"]["id"].startswith("user_")
    assert body["agency"]["id"].startswith("agcy_")


async def test_password_is_never_returned(
    client: AsyncClient, signup_payload: dict[str, str]
) -> None:
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    text = resp.text.lower()
    assert signup_payload["password"] not in resp.text
    assert "password" not in text
    assert "argon2" not in text


async def test_session_cookie_is_http_only(
    client: AsyncClient, signup_payload: dict[str, str]
) -> None:
    """httpOnly is the whole reason this is a cookie and not a bearer token."""
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


async def test_duplicate_email_is_rejected(
    client: AsyncClient, signup_payload: dict[str, str]
) -> None:
    assert (await client.post(f"{BASE}/auth/sign-up", json=signup_payload)).status_code == 201
    resp = await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/email-already-registered"


async def test_email_is_normalised(client: AsyncClient, signup_payload: dict[str, str]) -> None:
    """Case and surrounding whitespace must not create a second account."""
    await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    await client.post(f"{BASE}/auth/logout")
    resp = await client.post(
        f"{BASE}/auth/login",
        json={"email": "  DANA@MeridianSearch.Example  ", "password": signup_payload["password"]},
    )
    assert resp.status_code == 200


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: AsyncClient, signup_payload: dict[str, str]
) -> None:
    """The login endpoint must not be a user-enumeration oracle."""
    await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    await client.post(f"{BASE}/auth/logout")

    wrong_password = await client.post(
        f"{BASE}/auth/login",
        json={"email": signup_payload["email"], "password": "not-the-right-password"},
    )
    unknown_email = await client.post(
        f"{BASE}/auth/login",
        json={"email": "nobody@nowhere.example", "password": "not-the-right-password"},
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_logout_all_revokes_every_session(
    client: AsyncClient, signup_payload: dict[str, str], settings
) -> None:
    """Seat removal depends on this working."""
    from httpx import ASGITransport
    from httpx import AsyncClient as SecondDevice

    await client.post(f"{BASE}/auth/sign-up", json=signup_payload)
    first_cookie = client.cookies.get("avp_session")

    # A second, independent client — a different browser/device.
    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    async with SecondDevice(transport=transport, base_url="http://testserver") as other:
        resp = await other.post(
            f"{BASE}/auth/login",
            json={"email": signup_payload["email"], "password": signup_payload["password"]},
        )
        assert resp.status_code == 200
        second_cookie = other.cookies.get("avp_session")
        assert second_cookie != first_cookie

        await client.post(f"{BASE}/auth/logout-all")

        # The other device is signed out too.
        assert (await other.get(f"{BASE}/auth/me")).status_code == 401


async def test_short_password_is_rejected_with_problem_json(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Test Agency",
            "fullName": "Test Person",
            "email": "test@example.com",
            "password": "short",
        },
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    problem = resp.json()
    assert problem["type"] == "/problems/validation-failed"
    assert any(err["field"] == "password" for err in problem["errors"])


async def test_dashboard_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get(f"{BASE}/dashboard")
    assert resp.status_code == 401
    assert resp.json()["type"] == "/problems/authentication-required"


async def test_slug_collision_gets_a_suffix(client: AsyncClient) -> None:
    """Two agencies may legitimately share a name."""
    for i, email in enumerate(["a@x.example", "b@x.example"]):
        resp = await client.post(
            f"{BASE}/auth/sign-up",
            json={
                "agencyName": "Northern Lights Media",
                "fullName": "Person",
                "email": email,
                "password": "correct-horse-battery-staple",
            },
        )
        assert resp.status_code == 201
        expected = "northern-lights-media" if i == 0 else "northern-lights-media-1"
        assert resp.json()["agency"]["slug"] == expected
        await client.post(f"{BASE}/auth/logout")

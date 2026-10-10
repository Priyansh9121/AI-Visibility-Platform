"""Seat invitation and removal — Epic 9.14.

The three pieces these endpoints stand on were built and tested in Epic 1 and
never called: `test_seats.py` proves the seat limit holds under concurrency,
`test_auth_flow.py` proves `logout-all` revokes sessions, and the `invitations`
table has been in the schema since the initial migration. What is asserted here
is the HTTP surface over them, plus the two questions the surface had to answer
that the service layer never had to: what a re-invite does, and whether removing
a seat ends a session or merely a subscription.

No email provider is configured in the suite. That is the same deliberate
choice `test_password_reset.py` makes — the unconfigured path is the one that
runs in development, so it is the one under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import ids
from avp_api.config import Settings
from avp_api.models import Invitation, User, UserRole, UserStatus
from avp_api.security import token_digest
from avp_api.services import invitations as invitation_service
from avp_api.services import seats as seat_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _sign_up(
    client: AsyncClient,
    *,
    email: str = "owner@seats.example",
    agency: str = "Seat Endpoints Agency",
) -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": agency,
            "fullName": "Owner Person",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _invite(client: AsyncClient, agency_id: str, email: str, role: str = "member"):  # noqa: ANN202
    return await client.post(
        f"{BASE}/agencies/{agency_id}/invitations",
        json={"email": email, "role": role},
    )


async def _live_token_for(engine, agency_id: str, email: str) -> str:  # noqa: ANN001
    """Mint a token whose digest matches the agency's live invitation row.

    The raw token is never returned by the API — that is the point of the
    endpoint's design — so a test that needs to redeem one writes its own
    digest onto the existing row. If the lookup ever stopped being
    digest-based, this would break, which is what it is for.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    token = f"test-token-{email}"
    async with factory() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(token)
        await s.commit()
    return token


async def _live_sessions(user_id: str) -> set[str]:
    """Session digests Redis still holds a live record for, for one user.

    Read straight out of Redis rather than through `SessionStore`, so this
    observes the STATE rather than re-running the code under test. Epic 9.13's
    review flagged exactly this shape of gap on the reset flow: a revocation
    test written through the store's own API can pass whether or not the
    revocation happened. Delete the `revoke_all_for_user` call from the delete
    route and this assertion fails; a `GET /auth/me` assertion would not,
    because `current_principal` 401s on a soft-deleted row regardless.

    Goes through `get_redis()` rather than building a client from
    `settings.redis_url`, because that is the pool the app itself used — the
    session store resolves its client through the same function.
    """
    from avp_api.redis_client import get_redis

    redis = get_redis()
    members = set(await redis.smembers(f"user-sessions:{user_id}"))
    return {d for d in members if await redis.exists(f"sess:{d}")}


# ---------------------------------------------------------------------------
# the seat limit, enforced server-side
# ---------------------------------------------------------------------------


async def test_inviting_up_to_the_limit_then_refusing(client: AsyncClient) -> None:
    """Three seats: the owner and two invitees. The fourth is refused."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    for i in range(2):
        resp = await _invite(client, agency_id, f"member{i}@seats.example")
        assert resp.status_code == 201, resp.text
        assert resp.json()["seatConsumed"] is True

    assert (await client.get(f"{BASE}/auth/me")).json()["seats"] == {"used": 3, "limit": 3}

    resp = await _invite(client, agency_id, "one-too-many@seats.example")
    assert resp.status_code == 409, resp.text
    problem = resp.json()
    assert problem["type"] == "/problems/seat-limit-reached"
    # The numbers travel with the refusal, so a screen can say what to do.
    assert problem["seatsUsed"] == 3
    assert problem["seatLimit"] == 3
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_a_refused_invite_creates_nothing(client: AsyncClient, engine) -> None:  # noqa: ANN001
    """Not silently allowed, and not half-applied either.

    The seat check runs inside the same transaction as the insert, so a refusal
    must leave no user row and no invitation row behind.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    for i in range(2):
        assert (await _invite(client, agency_id, f"m{i}@seats.example")).status_code == 201

    assert (await _invite(client, agency_id, "rejected@seats.example")).status_code == 409

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        assert (
            await s.execute(select(User).where(User.email == "rejected@seats.example"))
        ).scalar_one_or_none() is None
        assert (
            await s.execute(
                select(Invitation).where(Invitation.email == "rejected@seats.example")
            )
        ).scalar_one_or_none() is None
        assert await seat_service.count_occupied_seats(s, agency_id) == 3


async def test_an_invited_seat_is_occupied_before_it_is_accepted(
    client: AsyncClient,
) -> None:
    """Otherwise an agency could issue unlimited invitations and overshoot."""
    me = await _sign_up(client)
    await _invite(client, me["agency"]["id"], "pending@seats.example")
    assert (await client.get(f"{BASE}/auth/me")).json()["seats"]["used"] == 2


# ---------------------------------------------------------------------------
# what the invite response may and may not carry
# ---------------------------------------------------------------------------


async def test_with_email_configured_the_invite_response_never_carries_the_token(
    client: AsyncClient, settings, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """The link goes in one email. A response body is not that email.

    With email configured, that is. Since 2026-09-29, when this service cannot
    email at all, the link comes back ONCE, to the inviter, to send themselves
    — see `test_email_not_configured.py`. With a key set it never does.
    """
    from pydantic import SecretStr

    from avp_api.services import email as email_service

    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))

    async def quiet_send(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(email_service, "send_invitation", quiet_send)
    me = await _sign_up(client)
    resp = await _invite(client, me["agency"]["id"], "quiet@seats.example")
    assert resp.json()["acceptUrl"] is None
    body = resp.text
    assert "token" not in body.lower()
    assert "/invite#" not in body
    assert resp.json()["invitation"].keys() == {
        "id",
        "email",
        "role",
        "expiresAt",
        "createdAt",
    }


async def test_invite_body_is_camel_case(client: AsyncClient) -> None:
    me = await _sign_up(client)
    body = (await _invite(client, me["agency"]["id"], "camel@seats.example")).json()
    assert "seatConsumed" in body and "seat_consumed" not in body
    assert "expiresAt" in body["invitation"]


# ---------------------------------------------------------------------------
# re-inviting
# ---------------------------------------------------------------------------


async def test_reinviting_a_pending_address_consumes_no_second_seat(
    client: AsyncClient,
) -> None:
    """The decision stated in Part B, asserted.

    The row that occupies the seat IS the row being re-invited, so a second
    invitation must move the link and not the count.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    first = await _invite(client, agency_id, "pending@seats.example")
    assert first.status_code == 201
    assert first.json()["seatConsumed"] is True
    assert first.json()["seats"]["used"] == 2

    second = await _invite(client, agency_id, "pending@seats.example")
    assert second.status_code == 201, second.text
    assert second.json()["seatConsumed"] is False
    assert second.json()["seats"]["used"] == 2
    # A different invitation row, because a new link was genuinely minted.
    assert second.json()["invitation"]["id"] != first.json()["invitation"]["id"]


async def test_a_new_invitation_retires_the_previous_link(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """The rule `password_reset.request_reset` applies, for the same reason.

    An earlier email that was forwarded or leaked must stop working the moment
    a replacement is issued.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "pending@seats.example")
    stale = await _live_token_for(engine, agency_id, "pending@seats.example")

    await _invite(client, agency_id, "pending@seats.example")

    resp = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": stale, "fullName": "Too Late", "password": NEW_PASSWORD},
    )
    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/invalid-invitation"


async def test_inviting_a_live_account_is_refused(client: AsyncClient) -> None:
    me = await _sign_up(client)
    resp = await _invite(client, me["agency"]["id"], me["user"]["email"])
    assert resp.status_code == 409
    assert resp.json()["type"] == "/problems/email-already-registered"


async def test_inviting_someone_elses_user_is_refused(client: AsyncClient) -> None:
    """Email is globally unique by design — see the `User` model."""
    first = await _sign_up(client)
    await client.post(f"{BASE}/auth/logout")
    await _sign_up(client, email="other@elsewhere.example", agency="Another Agency")

    second_agency = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    resp = await _invite(client, second_agency, first["user"]["email"])
    assert resp.status_code == 409
    assert resp.json()["type"] == "/problems/email-already-registered"


# ---------------------------------------------------------------------------
# accepting
# ---------------------------------------------------------------------------


async def test_accepting_an_invitation_signs_the_new_seat_in(
    client: AsyncClient, engine, settings: Settings
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "joiner@seats.example")
    token = await _live_token_for(engine, agency_id, "joiner@seats.example")

    await client.post(f"{BASE}/auth/logout")
    resp = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Jo Iner", "password": NEW_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "joiner@seats.example"
    assert body["user"]["status"] == "active"
    assert body["user"]["fullName"] == "Jo Iner"
    assert body["agency"]["id"] == agency_id
    # The seat was already counted while pending; accepting does not move it.
    assert body["seats"] == {"used": 2, "limit": 3}
    assert settings.session_cookie_key in resp.cookies

    # Signed in, not merely acknowledged.
    assert (await client.get(f"{BASE}/auth/me")).json()["user"]["email"] == (
        "joiner@seats.example"
    )


async def test_an_accepted_link_cannot_be_used_twice(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "once@seats.example")
    token = await _live_token_for(engine, agency_id, "once@seats.example")

    assert (
        await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": token, "fullName": "First", "password": NEW_PASSWORD},
        )
    ).status_code == 200

    replay = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Second", "password": NEW_PASSWORD},
    )
    assert replay.status_code == 400


@pytest.mark.parametrize("case", ["unknown", "expired", "revoked", "seat-removed"])
async def test_every_bad_invitation_is_refused_identically(
    client: AsyncClient, engine, case: str
) -> None:  # noqa: ANN001
    """One class, one message, no branch that says which kind of wrong.

    The same discipline `test_password_reset.py` asserts for reset links and
    Epic 9.8's public report route applies to a share token.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    if case == "unknown":
        token = "a-token-that-was-never-minted"
    else:
        await _invite(client, agency_id, "target@seats.example")
        token = await _live_token_for(engine, agency_id, "target@seats.example")
        async with factory() as s:
            row = (
                await s.execute(
                    select(Invitation).where(Invitation.email == "target@seats.example")
                )
            ).scalar_one()
            if case == "expired":
                row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            elif case == "revoked":
                row.revoked_at = datetime.now(UTC)
            else:
                user = (
                    await s.execute(
                        select(User).where(User.email == "target@seats.example")
                    )
                ).scalar_one()
                user.deleted_at = datetime.now(UTC)
            await s.commit()

    resp = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Nobody", "password": NEW_PASSWORD},
    )
    assert resp.status_code == 400
    problem = resp.json()
    assert problem["type"] == "/problems/invalid-invitation"
    assert problem["detail"] == (
        "That invitation link is not valid. Links work once and expire "
        "after seven days. Ask whoever invited you to send a new one."
    )


async def test_accepting_applies_the_sign_up_password_rules(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """The same `Password` annotation, not a restatement of it."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "weak@seats.example")
    token = await _live_token_for(engine, agency_id, "weak@seats.example")

    short = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Weak", "password": "short"},
    )
    assert short.status_code == 422

    repetitive = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Weak", "password": "aaaaaaaaaaaaaaaa"},
    )
    assert repetitive.status_code == 422
    assert "repetitive" in repetitive.text


# ---------------------------------------------------------------------------
# removal — the decision stated in Part B
# ---------------------------------------------------------------------------


async def test_removing_a_seat_revokes_every_session_it_held(
    client: AsyncClient, engine, settings: Settings
) -> None:  # noqa: ANN001
    """The stated decision: the seat AND the sessions, immediately.

    `sessions.py` opens by saying this case is why the product uses a session
    store rather than JWTs. The assertion reads Redis directly rather than
    calling the store, so it fails if `revoke_all_for_user` stops being called
    — an assertion made through the code under test would not.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "leaver@seats.example")
    token = await _live_token_for(engine, agency_id, "leaver@seats.example")

    owner_cookie = client.cookies.get(settings.session_cookie_key)

    # Accept, then sign in a SECOND time, so there are two sessions to kill.
    accepted = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Lea Ver", "password": NEW_PASSWORD},
    )
    leaver_id = accepted.json()["user"]["id"]
    await client.post(
        f"{BASE}/auth/login",
        json={"email": "leaver@seats.example", "password": NEW_PASSWORD},
    )
    assert len(await _live_sessions(leaver_id)) == 2

    # Back to the owner.
    client.cookies.set(settings.session_cookie_key, owner_cookie)

    resp = await client.delete(f"{BASE}/users/{leaver_id}")
    assert resp.status_code == 204, resp.text

    assert await _live_sessions(leaver_id) == set()
    assert (await client.get(f"{BASE}/auth/me")).json()["seats"]["used"] == 1


async def test_a_removed_seat_cannot_sign_back_in(
    client: AsyncClient, engine, settings: Settings
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "gone@seats.example")
    token = await _live_token_for(engine, agency_id, "gone@seats.example")
    owner_cookie = client.cookies.get(settings.session_cookie_key)

    user_id = (
        await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": token, "fullName": "Gone", "password": NEW_PASSWORD},
        )
    ).json()["user"]["id"]

    client.cookies.set(settings.session_cookie_key, owner_cookie)
    assert (await client.delete(f"{BASE}/users/{user_id}")).status_code == 204

    resp = await client.post(
        f"{BASE}/auth/login",
        json={"email": "gone@seats.example", "password": NEW_PASSWORD},
    )
    # Identical to a wrong password. A removed seat is not a distinct answer.
    assert resp.status_code == 401
    assert resp.json()["type"] == "/problems/invalid-credentials"


async def test_removing_a_seat_frees_it_for_someone_else(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    for i in range(2):
        await _invite(client, agency_id, f"full{i}@seats.example")
    assert (await _invite(client, agency_id, "waiting@seats.example")).status_code == 409

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        victim = (
            await s.execute(select(User).where(User.email == "full0@seats.example"))
        ).scalar_one()
        victim_id = victim.id

    assert (await client.delete(f"{BASE}/users/{victim_id}")).status_code == 204
    assert (await _invite(client, agency_id, "waiting@seats.example")).status_code == 201


async def test_a_removed_address_can_be_invited_again(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """`uq_users_email` does not exclude soft-deleted rows.

    Without the revive path in `invite_seat`, removing a seat would ban that
    address from the product permanently — including from the agency that
    removed it by mistake thirty seconds earlier.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "boomerang@seats.example")

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        user_id = (
            await s.execute(select(User).where(User.email == "boomerang@seats.example"))
        ).scalar_one().id

    assert (await client.delete(f"{BASE}/users/{user_id}")).status_code == 204
    assert (await client.get(f"{BASE}/auth/me")).json()["seats"]["used"] == 1

    again = await _invite(client, agency_id, "boomerang@seats.example")
    assert again.status_code == 201, again.text
    # A seat is genuinely re-taken: the removal released it.
    assert again.json()["seatConsumed"] is True
    assert again.json()["seats"]["used"] == 2
    # The same row, so the person's scan history is not orphaned.
    assert again.json()["user"]["id"] == user_id


async def test_you_cannot_remove_your_own_seat(client: AsyncClient) -> None:
    me = await _sign_up(client)
    resp = await client.delete(f"{BASE}/users/{me['user']['id']}")
    assert resp.status_code == 409
    assert "your own seat" in resp.json()["detail"]


async def test_the_last_owner_cannot_be_removed(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """`UserRole`: an agency with no owner has nobody who can manage seats."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "admin@seats.example", role="admin")
    token = await _live_token_for(engine, agency_id, "admin@seats.example")
    owner_id = me["user"]["id"]

    await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Ad Min", "password": NEW_PASSWORD},
    )

    # The admin tries to remove the only owner. Refused on BOTH counts, and the
    # privilege check is the one that must fire first.
    resp = await client.delete(f"{BASE}/users/{owner_id}")
    assert resp.status_code in (403, 409)
    assert (await client.get(f"{BASE}/auth/me")).json()["user"]["role"] == "admin"


# ---------------------------------------------------------------------------
# authority and tenancy
# ---------------------------------------------------------------------------


async def test_a_member_cannot_manage_seats(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """A member holds a seat; they do not decide who else does."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "member@seats.example")
    token = await _live_token_for(engine, agency_id, "member@seats.example")
    owner_id = me["user"]["id"]

    await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Mem Ber", "password": NEW_PASSWORD},
    )

    assert (await _invite(client, agency_id, "third@seats.example")).status_code == 403
    assert (await client.delete(f"{BASE}/users/{owner_id}")).status_code == 403
    assert (await client.get(f"{BASE}/agencies/{agency_id}/seats")).status_code == 403


async def test_another_agencys_ids_are_404_not_403(client: AsyncClient) -> None:
    """Confirming an id exists is a cross-tenant leak, so it is never confirmed."""
    first = await _sign_up(client)
    await client.post(f"{BASE}/auth/logout")
    await _sign_up(client, email="second@elsewhere.example", agency="Second Agency")

    assert (
        await _invite(client, first["agency"]["id"], "x@seats.example")
    ).status_code == 404
    assert (
        await client.get(f"{BASE}/agencies/{first['agency']['id']}/seats")
    ).status_code == 404
    assert (await client.delete(f"{BASE}/users/{first['user']['id']}")).status_code == 404


async def test_seat_management_requires_a_session(client: AsyncClient) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await client.post(f"{BASE}/auth/logout")

    assert (await _invite(client, agency_id, "nobody@seats.example")).status_code == 401
    assert (await client.get(f"{BASE}/agencies/{agency_id}/seats")).status_code == 401
    assert (await client.delete(f"{BASE}/users/{me['user']['id']}")).status_code == 401


# ---------------------------------------------------------------------------
# the roster
# ---------------------------------------------------------------------------


async def test_the_seat_list_shows_holders_and_pending_links(
    client: AsyncClient,
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "pending@seats.example")

    body = (await client.get(f"{BASE}/agencies/{agency_id}/seats")).json()
    assert body["seats"] == {"used": 2, "limit": 3}

    emails = [m["email"] for m in body["members"]]
    # Owners first — the list answers "who can do what here".
    assert emails[0] == me["user"]["email"]
    assert "pending@seats.example" in emails

    pending = next(m for m in body["members"] if m["email"] == "pending@seats.example")
    assert pending["status"] == "invited"
    # A pending invitee is on BOTH lists: the seat list because they occupy a
    # seat, the invitation list because only that row knows the expiry.
    assert [i["email"] for i in body["invitations"]] == ["pending@seats.example"]
    assert body["invitations"][0]["expiresAt"] is not None


async def test_an_expired_invitation_leaves_the_pending_list_but_keeps_its_seat(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """The seat is what costs money, and it is held by the user row, not this one."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "stale@seats.example")

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (
            await s.execute(select(Invitation).where(Invitation.email == "stale@seats.example"))
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()

    body = (await client.get(f"{BASE}/agencies/{agency_id}/seats")).json()
    assert body["invitations"] == []
    assert body["seats"]["used"] == 2
    assert "stale@seats.example" in [m["email"] for m in body["members"]]


async def test_no_password_hash_reaches_the_seat_list(client: AsyncClient) -> None:
    me = await _sign_up(client)
    resp = await client.get(f"{BASE}/agencies/{me['agency']['id']}/seats")
    assert "password" not in resp.text.lower()


# ---------------------------------------------------------------------------
# the service-level invariant the endpoints inherit
# ---------------------------------------------------------------------------


async def test_invited_seat_holders_show_the_address_until_they_say_otherwise(
    session, settings
) -> None:  # noqa: ANN001
    """No display name is invented from an email's local part.

    Guessing "j.smith@" is "J Smith" puts a fabrication on a seat list, and the
    person it names is the one who would have to correct it.
    """
    from avp_api.services import auth as auth_service

    agency, owner = await auth_service.sign_up_agency(
        session,
        agency_name="Naming Agency",
        full_name="Owner",
        email="owner@naming.example",
        password=PASSWORD,
        settings=settings,
    )
    await session.commit()

    minted = await invitation_service.invite_seat(
        session,
        agency_id=agency.id,
        invited_by_user_id=owner.id,
        inviter_role=UserRole.OWNER,
        email="J.Smith@Naming.Example",
        role=UserRole.MEMBER,
        settings=settings,
    )
    await session.commit()

    assert minted.user.email == "j.smith@naming.example"
    assert minted.user.full_name == "j.smith@naming.example"
    assert minted.user.status is UserStatus.INVITED
    assert minted.user.password_hash is None
    assert minted.invitation.id.startswith(f"{ids.INVITATION}_")


# ---------------------------------------------------------------------------
# an admin must not be able to make an owner (hardening F1)
# ---------------------------------------------------------------------------

DETAIL = "Only an owner can invite someone as an owner."
ADMIN = "admin@owners.example"


async def _become_admin(client: AsyncClient, engine, me: dict) -> str:  # noqa: ANN001
    """Invite and accept an admin; the client is signed in as them afterwards."""
    agency_id = me["agency"]["id"]
    assert (await _invite(client, agency_id, ADMIN, role="admin")).status_code == 201
    token = await _live_token_for(engine, agency_id, ADMIN)
    resp = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Ad Min", "password": NEW_PASSWORD},
    )
    assert resp.status_code in (200, 201), resp.text
    assert (await client.get(f"{BASE}/auth/me")).json()["user"]["role"] == "admin"
    return agency_id


async def _counts(engine, email: str | None = None):  # noqa: ANN001, ANN202
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        users = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        invs = (
            await s.execute(select(func.count()).select_from(Invitation))
        ).scalar_one()
        user = (
            (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if email
            else None
        )
        live = (
            (
                await s.execute(
                    select(Invitation).where(
                        Invitation.email == email,
                        Invitation.accepted_at.is_(None),
                        Invitation.revoked_at.is_(None),
                    )
                )
            ).scalars().all()
            if email
            else []
        )
    return users, invs, user, live


async def test_admin_cannot_invite_a_new_address_as_owner(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email="owner@owners.example")
    agency_id = await _become_admin(client, engine, me)
    users0, invs0, _, _ = await _counts(engine)
    used0 = (await client.get(f"{BASE}/auth/me")).json()["seats"]["used"]

    resp = await _invite(client, agency_id, "new@owners.example", role="owner")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == DETAIL

    users1, invs1, user, _ = await _counts(engine, "new@owners.example")
    assert (users1, invs1) == (users0, invs0)
    assert user is None
    assert (await client.get(f"{BASE}/auth/me")).json()["seats"]["used"] == used0


async def test_admin_cannot_promote_a_pending_invitation_to_owner(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email="owner@owners.example")
    agency_id = await _become_admin(client, engine, me)
    assert (await _invite(client, agency_id, "pend@owners.example")).status_code == 201

    resp = await _invite(client, agency_id, "pend@owners.example", role="owner")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == DETAIL

    _, _, user, live = await _counts(engine, "pend@owners.example")
    assert user.role is UserRole.MEMBER
    assert user.status is UserStatus.INVITED
    assert len(live) == 1 and live[0].role is UserRole.MEMBER


async def test_admin_cannot_revive_a_removed_user_as_owner(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email="owner@owners.example")
    agency_id = await _become_admin(client, engine, me)
    assert (await _invite(client, agency_id, "gone@owners.example")).status_code == 201
    _, _, user, _ = await _counts(engine, "gone@owners.example")
    assert (await client.delete(f"{BASE}/users/{user.id}")).status_code == 204

    resp = await _invite(client, agency_id, "gone@owners.example", role="owner")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == DETAIL

    _, _, user, _ = await _counts(engine, "gone@owners.example")
    assert user.deleted_at is not None
    assert user.role is UserRole.MEMBER


async def test_owner_can_invite_as_owner(client: AsyncClient) -> None:
    me = await _sign_up(client, email="owner@owners.example")
    agency_id = me["agency"]["id"]
    first = await _invite(client, agency_id, "co@owners.example", role="owner")
    assert first.status_code == 201, first.text
    assert first.json()["user"]["role"] == "owner"

    # Re-invite of the INVITED row, as owner again.
    again = await _invite(client, agency_id, "co@owners.example", role="owner")
    assert again.status_code == 201, again.text
    assert again.json()["seatConsumed"] is False


@pytest.mark.parametrize("role", ["admin", "member"])
async def test_admin_can_invite_as_admin_or_member(
    client: AsyncClient, engine, role: str
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email="owner@owners.example")
    agency_id = await _become_admin(client, engine, me)
    resp = await _invite(client, agency_id, f"as-{role}@owners.example", role=role)
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["role"] == role


async def test_the_refusal_log_line_carries_no_email(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email="owner@owners.example")
    agency_id = await _become_admin(client, engine, me)
    with structlog.testing.capture_logs() as events:
        resp = await _invite(client, agency_id, "leak@owners.example", role="owner")
    assert resp.status_code == 403
    refused = [e for e in events if e.get("event") == "invitation.owner_refused"]
    assert len(refused) == 1
    assert refused[0]["agency_id"] == agency_id
    assert "@" not in repr(refused[0])


# ---------------------------------------------------------------------------
# F1 follow-ups: owner invitations, the last-owner guard, a leaver's links
# ---------------------------------------------------------------------------

REISSUE_DETAIL = "Only an owner can change an owner's invitation."
OWNER_EMAIL = "owner@owners.example"


async def _user(engine, email: str) -> User:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        return (await s.execute(select(User).where(User.email == email))).scalar_one()


@pytest.mark.parametrize("resend_role", ["owner", "member"])
async def test_admin_cannot_reissue_a_pending_owner_invitation(
    client: AsyncClient, engine, resend_role: str
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email=OWNER_EMAIL)
    agency_id = me["agency"]["id"]
    assert (await _invite(client, agency_id, "x@owners.example", role="owner")).status_code == 201
    await _become_admin(client, engine, me)

    resp = await _invite(client, agency_id, "x@owners.example", role=resend_role)
    assert resp.status_code == 403, resp.text
    # A role=owner re-issue is caught by the first check, a member one by the second.
    assert resp.json()["detail"] == (DETAIL if resend_role == "owner" else REISSUE_DETAIL)

    _, _, user, live = await _counts(engine, "x@owners.example")
    assert user.role is UserRole.OWNER
    assert len(live) == 1 and live[0].role is UserRole.OWNER


async def test_owner_can_reissue_a_pending_owner_invitation_as_member(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email=OWNER_EMAIL)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "x@owners.example", role="owner")
    resp = await _invite(client, agency_id, "x@owners.example", role="member")
    assert resp.status_code == 201, resp.text
    _, _, user, live = await _counts(engine, "x@owners.example")
    assert user.role is UserRole.MEMBER
    assert len(live) == 1


async def test_a_sole_owner_can_withdraw_a_pending_owner_invitation(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email=OWNER_EMAIL)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "x@owners.example", role="owner")
    pending = await _user(engine, "x@owners.example")
    assert (await client.delete(f"{BASE}/users/{pending.id}")).status_code == 204
    _, _, user, live = await _counts(engine, "x@owners.example")
    assert user.deleted_at is not None and live == []


async def test_the_last_active_owner_still_cannot_be_removed_with_an_owner_pending(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email=OWNER_EMAIL)
    agency_id = me["agency"]["id"]
    await _invite(client, agency_id, "x@owners.example", role="owner")
    await _become_admin(client, engine, me)
    resp = await client.delete(f"{BASE}/users/{me['user']['id']}")
    assert resp.status_code in (403, 409)
    assert (await _user(engine, OWNER_EMAIL)).deleted_at is None


async def test_removing_a_user_revokes_the_pending_invitations_they_sent(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client, email=OWNER_EMAIL)
    agency_id = me["agency"]["id"]
    await _become_admin(client, engine, me)
    admin = await _user(engine, ADMIN)

    # Rows are written directly: the plan's three seats leave no room to mint
    # this many through the API, and what is under test is the removal.
    def _row(email: str, by: str, *, accepted: bool = False) -> Invitation:
        return Invitation(
            id=ids.new_id(ids.INVITATION),
            agency_id=agency_id,
            invited_by_user_id=by,
            email=email,
            role=UserRole.MEMBER,
            token_digest=token_digest(f"tok-{email}"),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            accepted_at=datetime.now(UTC) if accepted else None,
        )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        s.add_all(
            [
                _row("bysoon@owners.example", admin.id),
                _row("accepted@owners.example", admin.id, accepted=True),
                _row("byowner@owners.example", me["user"]["id"]),
            ]
        )
        await s.commit()

    login = await client.post(
        f"{BASE}/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert (await client.delete(f"{BASE}/users/{admin.id}")).status_code == 204

    async with factory() as s:
        rows = {
            r.email: r
            for r in (
                await s.execute(
                    select(Invitation).where(Invitation.email.like("%@owners.example"))
                )
            ).scalars()
        }
    assert rows["bysoon@owners.example"].revoked_at is not None
    assert rows["byowner@owners.example"].revoked_at is None
    assert rows["accepted@owners.example"].revoked_at is None
    assert rows["accepted@owners.example"].accepted_at is not None

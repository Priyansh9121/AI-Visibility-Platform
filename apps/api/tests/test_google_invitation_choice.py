"""An invited address's Google sign-in asks first — A6 part 3, launch checklist 3.126.

Until this change a verified Google sign-in whose address matched an invited seat with a live
invitation ACTIVATED the seat at once, so whoever invited another person's address into their own
agency captured that person's first Google sign-in there. The founder's decision (2026-10-11): ask
first. "You've been invited to join {agency}. Join, or start your own agency?" Saying no revokes the
invitation and continues as a normal sign-up. Only offered when Google says the email is verified.

What these tests hold the build to:

* **The callback writes nothing for an invited address.** No activation, no Google link, no
  session, no cutoff; it parks a ticket (the new address's kind, with the invitation's three ids in
  it) and the peek names the agency. An unverified address, a dead invitation, a removed, suspended
  or claimed seat get no offer, as they were refused before.
* **Join** is the old activation made on the person's say-so: locks, second look, link, session.
  Everything that can have changed since the callback is refused with the one answer a spent or
  foreign ticket gets, and changes nothing.
* **Start your own agency** revokes the invitation and frees the address: a fresh invited row is
  deleted (and the inviter's agency shows neither it nor the invitation), a row with a history (a
  removed member who was invited again) or that anything refers to is kept and put back as removed,
  and a row that has become an active member's is never touched. The person is handed a ticket
  with no invitation in it, and the existing sign-up step makes the agency with them as its owner.
* **A ticket makes one choice**, in the browser that holds the flow cookie, for as long as the
  ticket lives, at exactly one of the three steps its kind belongs to.
* No log line carries an address, a ticket or the agency's name.

The old tests of the activation (`test_google_sign_in.py`, `test_session_cutoff.py`,
`test_auth_hardening_f5b.py`) were moved to the Join step by design, and say so.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from httpx import AsyncClient, Response
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings, get_settings
from avp_api.models import (
    Agency,
    Base,
    Client,
    Invitation,
    PromptRun,
    PromptRunStatus,
    Scan,
    User,
    UserRole,
    UserStatus,
)
from avp_api.security import token_digest
from avp_api.services import google_oauth
from avp_api.services import invitations as invitation_service
from avp_api.services.google_oauth import TICKET_PREFIX, GoogleIdentity, InvitationOffer
from avp_api.sessions import SESSION_PREFIX, SessionStore
from test_google_sign_in import (  # noqa: F401 - `google` is a fixture, used by name
    BASE,
    FLOW_COOKIE,
    PASSWORD,
    FakeGoogleProvider,
    assert_flow_cookie_cleared,
    callback,
    complete,
    decline,
    flow_cookie_header,
    google,
    identity,
    join,
    landed,
    landed_ticket,
    new_address_ticket,
    no_user_for,
    password_login,
    peek,
    session_cookie_of,
    sign_up_with_password,
    start,
    start_with_cookie,
    use_cookie,
)
from test_google_state_race import _MeetOnTheWire

OWNER = "owner@northlight.example"
INVITEE = "rae@northlight.example"
AGENCY = "Northlight Partners"  # what `sign_up_with_password` names the inviting agency
REFUSED = "invalid-google-ticket"


# --- the scene ---------------------------------------------------------------------------------


def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def invite(client: AsyncClient, email: str = INVITEE) -> tuple[str, str]:
    """The owner invites `email`. Returns (the agency's id, the invited seat's user id).

    The client ends signed out, as the person who will sign in with Google is.
    """
    await sign_up_with_password(client, OWNER)
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    invited = await client.post(f"{BASE}/agencies/{agency_id}/invitations", json={"email": email})
    assert invited.status_code == 201, invited.text
    await client.post(f"{BASE}/auth/logout")
    return agency_id, invited.json()["user"]["id"]


async def offered(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    *,
    email: str = INVITEE,
    sub: str = "sub-rae",
    name: str = "Rae Park",
    code: str = "code-1",
    verified: bool = True,
) -> tuple[str, str, Response]:
    """The invited person signs in with Google, in a browser of their own.

    Returns (the ticket, the flow cookie's value, the callback's answer).
    """
    google.identities[code] = identity(email, sub=sub, name=name, verified=verified)
    state, mine = await start_with_cookie(client)
    resp = await callback(client, code=code, state=state)
    return landed_ticket(resp), mine, resp


async def seat(engine: AsyncEngine, email: str = INVITEE) -> User | None:
    async with sessions(engine)() as s:
        return (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def invitations_of(engine: AsyncEngine, email: str = INVITEE) -> list[Invitation]:
    async with sessions(engine)() as s:
        return list(
            (
                await s.execute(
                    select(Invitation).where(Invitation.email == email).order_by(Invitation.id)
                )
            ).scalars()
        )


async def snapshot(engine: AsyncEngine) -> tuple[list[Any], list[Any], list[Any]]:
    """Every row the choice could touch, by the columns it could touch."""
    async with sessions(engine)() as s:
        users = (
            await s.execute(
                select(
                    User.id, User.status, User.google_sub, User.password_hash, User.full_name,
                    User.last_login_at, User.sessions_valid_after, User.deleted_at,
                ).order_by(User.id)
            )
        ).all()
        invites = (
            await s.execute(
                select(Invitation.id, Invitation.accepted_at, Invitation.revoked_at).order_by(
                    Invitation.id
                )
            )
        ).all()
        agencies = (await s.execute(select(Agency.id).order_by(Agency.id))).all()
    return list(users), list(invites), list(agencies)


async def ticket_keys(settings: Settings) -> list[str]:
    redis = redis_module.get_redis(settings)
    return [key async for key in redis.scan_iter(f"{TICKET_PREFIX}*")]


async def session_keys(settings: Settings) -> list[str]:
    redis = redis_module.get_redis(settings)
    return [key async for key in redis.scan_iter(f"{SESSION_PREFIX}*")]


def sets_a_session(resp: Response) -> bool:
    return any("avp_session" in h for h in resp.headers.get_list("set-cookie"))


def assert_refused(resp: Response) -> None:
    """The one answer for a ticket that cannot be used, with the flow cookie cleared."""
    assert resp.status_code == 400, resp.text
    assert resp.json()["type"].endswith(f"/{REFUSED}"), resp.text
    assert not sets_a_session(resp)
    assert_flow_cookie_cleared(resp)


def an_offer_for(agency_id: str, user_id: str, invitation: Invitation) -> InvitationOffer:
    return InvitationOffer(invitation_id=invitation.id, user_id=user_id, agency_id=agency_id)


# --- the callback asks, and writes nothing ----------------------------------------------------


async def test_an_invited_addresss_google_sign_in_writes_nothing_and_parks_an_offer(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    agency_id, user_id = await invite(client)
    before = await snapshot(engine)

    ticket, mine, resp = await offered(client, google)

    # Nobody is signed in: no session cookie, no session, nothing for `/auth/me` to answer.
    assert not sets_a_session(resp)
    assert await session_keys(settings) == []
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
    # Nothing was written: not the activation, not the Google link, not the cutoff, not the name.
    assert await snapshot(engine) == before
    rae = await seat(engine)
    assert rae is not None and rae.id == user_id
    assert rae.status is UserStatus.INVITED
    assert rae.google_sub is None and rae.password_hash is None
    assert rae.last_login_at is None and rae.sessions_valid_after is None
    assert rae.full_name == INVITEE
    (invitation,) = await invitations_of(engine)
    assert invitation.accepted_at is None and invitation.revoked_at is None
    # The ticket needs the browser's flow cookie to be redeemed, so the callback keeps it.
    assert flow_cookie_header(resp).split(";")[0] == f"{FLOW_COOKIE}={mine}"

    # The ticket is the new address's kind: the same key family and lifetime, and the record holds
    # the invitation's three ids, written by the server, beside the verified identity.
    redis = redis_module.get_redis(settings)
    key = f"{TICKET_PREFIX}{token_digest(ticket)}"
    assert 0 < await redis.ttl(key) <= settings.google_sign_in_ttl_seconds
    record = json.loads(await redis.get(key))
    assert record["invitation"] == {
        "invitation_id": invitation.id,
        "user_id": user_id,
        "agency_id": agency_id,
    }
    assert record["binding_digest"] == token_digest(mine)

    # And the page learns which agency invited the address.
    pending = await peek(client, ticket)
    assert pending.status_code == 200, pending.text
    assert pending.json() == {"email": INVITEE, "suggestedName": "Rae Park", "invitedTo": AGENCY}


async def test_a_new_addresss_ticket_record_is_what_it_always_was(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    settings: Settings,
) -> None:
    ticket, mine, _ = await new_address_ticket(client, google)
    redis = redis_module.get_redis(settings)
    record = json.loads(await redis.get(f"{TICKET_PREFIX}{token_digest(ticket)}"))
    assert set(record) == {"sub", "email", "email_verified", "name", "nonce", "binding_digest"}
    assert (await peek(client, ticket)).json()["invitedTo"] is None


async def test_an_unverified_invited_address_gets_no_offer(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    await invite(client)
    before = await snapshot(engine)
    google.identities["code-1"] = identity(INVITEE, sub="sub-rae", verified=False)
    state = await start(client)
    resp = await callback(client, code="code-1", state=state)

    assert landed(resp, "/") == {"google": ["error"], "reason": ["email-unverified"]}
    assert await ticket_keys(settings) == []
    assert not sets_a_session(resp)
    assert await snapshot(engine) == before


@pytest.mark.parametrize("how", ["expired", "revoked"])
async def test_a_dead_invitation_is_refused_with_no_offer(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
    how: str,
) -> None:
    await invite(client)
    async with sessions(engine)() as s:
        (invitation,) = (await s.execute(select(Invitation))).scalars().all()
        if how == "expired":
            invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            invitation.revoked_at = datetime.now(UTC)
        await s.commit()
    before = await snapshot(engine)

    google.identities["code-1"] = identity(INVITEE, sub="sub-rae")
    state = await start(client)
    resp = await callback(client, code="code-1", state=state)

    assert landed(resp, "/")["reason"] == ["invitation-expired"]
    assert await ticket_keys(settings) == []
    assert await snapshot(engine) == before


@pytest.mark.parametrize(
    ("how", "reason"),
    [
        ("removed", "account-unavailable"),
        ("suspended", "account-suspended"),
        ("claimed", "email-claimed"),
    ],
)
async def test_a_removed_suspended_or_claimed_seat_is_refused_with_no_offer(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
    how: str,
    reason: str,
) -> None:
    """The invitation is still live in all three, which is what makes them worth trying: the seat
    is what refuses, as it did when the sign-in activated it."""
    await invite(client)
    async with sessions(engine)() as s:
        rae = (await s.execute(select(User).where(User.email == INVITEE))).scalar_one()
        if how == "removed":
            rae.deleted_at = datetime.now(UTC)
        elif how == "suspended":
            rae.status = UserStatus.SUSPENDED
            rae.password_hash = "not-a-real-hash"  # a suspended row holds a credential (the CHECK)
        else:
            rae.google_sub = "sub-somebody-else"
        await s.commit()
    before = await snapshot(engine)

    google.identities["code-1"] = identity(INVITEE, sub="sub-rae")
    state = await start(client)
    resp = await callback(client, code="code-1", state=state)

    assert landed(resp, "/")["reason"] == [reason]
    assert await ticket_keys(settings) == []
    assert await snapshot(engine) == before


# --- Join --------------------------------------------------------------------------------------


async def test_join_activates_the_seat_and_signs_the_person_in(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    agency_id, user_id = await invite(client)
    ticket, _, _ = await offered(client, google)

    joined = await join(client, ticket)

    assert joined.status_code == 200, joined.text
    body = joined.json()
    assert body["user"]["id"] == user_id
    assert body["user"]["status"] == "active" and body["user"]["role"] == "member"
    assert body["user"]["fullName"] == "Rae Park"  # Google's name takes over the placeholder
    assert body["user"]["signInMethods"] == ["google"]
    assert body["agency"]["id"] == agency_id
    assert body["seats"] == {"used": 2, "limit": 3}
    assert "feedbackEmail" in body
    assert sets_a_session(joined)
    assert_flow_cookie_cleared(joined)
    # The same session answers for the person.
    me = await client.get(f"{BASE}/auth/me")
    assert me.status_code == 200 and me.json()["user"]["id"] == user_id
    rae = await seat(engine)
    assert rae is not None and rae.google_sub == "sub-rae" and rae.password_hash is None
    assert rae.last_login_at is not None and rae.sessions_valid_after is not None
    (invitation,) = await invitations_of(engine)
    assert invitation.accepted_at is not None and invitation.revoked_at is None
    assert await ticket_keys(settings) == []


async def test_the_emailed_link_is_unchanged_by_a_waiting_offer(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The password invitation link is not the Google flow and is not touched by it."""
    agency_id, _ = await invite(client)
    ticket, mine, _ = await offered(client, google)
    token = "choice-emailed-link-" + "m" * 32
    async with sessions(engine)() as s:
        (invitation,) = (await s.execute(select(Invitation))).scalars().all()
        invitation.token_digest = token_digest(token)
        await s.commit()
    assert (await peek(client, ticket)).status_code == 200  # the offer is waiting

    accepted = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Rae Park", "password": PASSWORD},
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["signInMethods"] == ["email"]
    assert accepted.json()["agency"]["id"] == agency_id
    # The offer that was waiting is no longer one: the seat is a member's. The ticket is still
    # this browser's (the flow cookie rides), so the refusal is the seat's and nothing else's.
    use_cookie(client, mine)
    refused = await peek(client, ticket)
    assert refused.status_code == 400, refused.text


async def test_the_offer_follows_the_invitation_when_it_is_sent_again(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    """The ticket names an invitation, but Join accepts the newest live one for the address in
    that agency, as the sign-in did: a re-sent invitation is not a reason to refuse."""
    agency_id, _ = await invite(client)
    owner = await seat(engine, OWNER)
    assert owner is not None
    ticket, _, _ = await offered(client, google)
    async with sessions(engine)() as s:
        await invitation_service.invite_seat(
            s, agency_id=agency_id, invited_by_user_id=owner.id, inviter_role=UserRole.OWNER,
            email=INVITEE, role=UserRole.MEMBER, settings=settings,
        )
        await s.commit()
    first, second = await invitations_of(engine)
    assert first.revoked_at is not None and second.revoked_at is None

    assert (await peek(client, ticket)).json()["invitedTo"] == AGENCY
    joined = await join(client, ticket)

    assert joined.status_code == 200, joined.text
    first, second = await invitations_of(engine)
    assert first.accepted_at is None and second.accepted_at is not None


async def test_join_after_the_emailed_link_won_links_the_account_and_removes_its_password(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The seat was activated by the emailed link between the sign-in and the press. Join does not
    activate it again; it links Google as the sign-in links any account, which removes the
    password the link set and ends the session it made (the callback's rule, unchanged)."""
    await invite(client)
    ticket, mine, _ = await offered(client, google)
    token = "choice-accept-first-" + "f" * 32
    async with sessions(engine)() as s:
        (invitation,) = (await s.execute(select(Invitation))).scalars().all()
        invitation.token_digest = token_digest(token)
        await s.commit()
    accepted = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Rae Park", "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    by_link = session_cookie_of(client)
    use_cookie(client, mine)

    joined = await join(client, ticket)

    assert joined.status_code == 200, joined.text
    assert joined.json()["user"]["signInMethods"] == ["google"]
    rae = await seat(engine)
    assert rae is not None and rae.password_hash is None and rae.google_sub == "sub-rae"
    assert (await password_login(client, INVITEE, PASSWORD)).status_code == 401
    client.cookies.clear()
    client.cookies.set(*by_link)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401  # the link's session ended


@pytest.mark.parametrize(
    ("what", "reason"),
    [
        ("revoked", "invitation-expired"),
        ("expired", "invitation-expired"),
        ("removed", "account-unavailable"),
        ("suspended", "account-suspended"),
        ("claimed", "email-claimed"),
    ],
)
async def test_join_is_refused_when_the_seat_or_its_invitation_stopped_being_good(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
    what: str,
    reason: str,
) -> None:
    """Between the sign-in and the press the invitation can be revoked, expire or be used, and the
    seat removed, suspended or claimed. Join looks again, under the locks, and changes nothing."""
    await invite(client)
    ticket, _, _ = await offered(client, google)
    async with sessions(engine)() as s:
        rae = (await s.execute(select(User).where(User.email == INVITEE))).scalar_one()
        (invitation,) = (await s.execute(select(Invitation))).scalars().all()
        if what == "revoked":
            invitation.revoked_at = datetime.now(UTC)
        elif what == "expired":
            invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif what == "removed":
            rae.deleted_at = datetime.now(UTC)
        elif what == "suspended":
            rae.status = UserStatus.SUSPENDED
            rae.password_hash = "not-a-real-hash"  # a suspended row holds a credential (the CHECK)
        else:
            rae.google_sub = "sub-somebody-else"
        await s.commit()
    before = await snapshot(engine)

    with structlog.testing.capture_logs() as events:
        joined = await join(client, ticket)

    assert_refused(joined)
    assert await snapshot(engine) == before
    assert await session_keys(settings) == []
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
    (refusal,) = [e for e in events if e["event"] == "google.invitation_choice.refused"]
    assert refusal["choice"] == "join" and refusal["reason"] == reason
    assert INVITEE not in json.dumps(events, default=str)


@pytest.mark.parametrize("what", ["revoked", "expired", "removed", "suspended", "claimed"])
async def test_the_peek_offers_nothing_that_join_would_refuse(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    what: str,
) -> None:
    """The page shows the choice only while "Join" could succeed; after that the ticket is the
    ticket that can no longer be used."""
    await invite(client)
    ticket, _, _ = await offered(client, google)
    async with sessions(engine)() as s:
        rae = (await s.execute(select(User).where(User.email == INVITEE))).scalar_one()
        (invitation,) = (await s.execute(select(Invitation))).scalars().all()
        if what == "revoked":
            invitation.revoked_at = datetime.now(UTC)
        elif what == "expired":
            invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif what == "removed":
            rae.deleted_at = datetime.now(UTC)
        elif what == "suspended":
            rae.status = UserStatus.SUSPENDED
            rae.password_hash = "not-a-real-hash"  # a suspended row holds a credential (the CHECK)
        else:
            rae.google_sub = "sub-somebody-else"
        await s.commit()

    pending = await peek(client, ticket)

    assert pending.status_code == 400, pending.text
    assert pending.json()["type"].endswith(f"/{REFUSED}")
    assert "invitedTo" not in pending.text and AGENCY not in pending.text


# --- a ticket makes one choice -----------------------------------------------------------------

ANSWERS: dict[str, Callable[[AsyncClient, str], Awaitable[Response]]] = {
    "join": join,
    "decline": decline,
}


@pytest.mark.parametrize("second", ["join", "decline"])
@pytest.mark.parametrize("first", ["join", "decline"])
async def test_one_ticket_makes_one_choice(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    first: str,
    second: str,
) -> None:
    await invite(client)
    ticket, mine, _ = await offered(client, google)

    one = await ANSWERS[first](client, ticket)
    assert one.status_code == 200, one.text
    after_one = await snapshot(engine)
    use_cookie(client, mine)  # the right browser again, so only the spent ticket can refuse it
    two = await ANSWERS[second](client, ticket)

    assert_refused(two)
    assert await snapshot(engine) == after_one
    use_cookie(client, mine)
    assert (await peek(client, ticket)).status_code == 400


@pytest.mark.parametrize("answer", ["join", "decline"])
async def test_an_expired_ticket_is_refused_at_both_steps(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    answer: str,
) -> None:
    app = client._transport.app  # noqa: SLF001 - the ASGI app under test
    short = app.dependency_overrides[get_settings]().model_copy(
        update={"google_sign_in_ttl_seconds": 1}
    )
    app.dependency_overrides[get_settings] = lambda: short
    await invite(client)
    ticket, _, _ = await offered(client, google)
    before = await snapshot(engine)
    await asyncio.sleep(1.2)

    assert_refused(await ANSWERS[answer](client, ticket))
    assert await snapshot(engine) == before


@pytest.mark.parametrize("answer", ["join", "decline"])
@pytest.mark.parametrize("who", ["nobody", "another-browser"])
async def test_another_browser_cannot_answer_for_the_person(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
    who: str,
    answer: str,
) -> None:
    await invite(client)
    ticket, mine, _ = await offered(client, google)
    before = await snapshot(engine)
    use_cookie(client, None if who == "nobody" else "another-browsers-binding-" + "y" * 20)

    with structlog.testing.capture_logs() as events:
        refused = await ANSWERS[answer](client, ticket)

    assert_refused(refused)
    assert await snapshot(engine) == before
    assert await session_keys(settings) == []
    causes = [e["cause"] for e in events if e["event"] == "google.ticket.browser_mismatch"]
    assert causes == ["missing" if who == "nobody" else "different"]
    assert mine not in str(events)


@pytest.mark.parametrize("answer", ["join", "decline"])
async def test_a_new_addresss_ticket_is_not_an_answer_to_an_invitation(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    answer: str,
) -> None:
    ticket, _, _ = await new_address_ticket(client, google)

    assert_refused(await ANSWERS[answer](client, ticket))
    assert await no_user_for(engine, "new-person@example.com")
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


async def test_an_invited_addresss_ticket_cannot_create_an_agency(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The address is held by the invited seat until the person says which. The sign-up step
    creates nothing from this ticket, whatever agency name it is given."""
    await invite(client)
    ticket, _, _ = await offered(client, google)
    before = await snapshot(engine)

    refused = await complete(client, ticket)

    assert_refused(refused)
    assert await snapshot(engine) == before
    async with sessions(engine)() as s:
        assert (await s.execute(select(Agency.name))).scalars().all() == [AGENCY]


# --- Start your own agency ---------------------------------------------------------------------


async def test_start_your_own_agency_revokes_frees_and_leads_to_an_agency_of_their_own(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    agency_id, user_id = await invite(client)
    ticket, mine, _ = await offered(client, google)
    redis = redis_module.get_redis(settings)

    declined = await decline(client, ticket)

    assert declined.status_code == 200, declined.text
    assert set(declined.json()) == {"ticket"}
    new_ticket = declined.json()["ticket"]
    assert new_ticket != ticket
    # Nobody is signed in, and the agency is not made yet. The flow cookie is kept, renewed, for
    # the next step.
    assert not sets_a_session(declined)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
    assert flow_cookie_header(declined).split(";")[0] == f"{FLOW_COOKIE}={mine}"
    async with sessions(engine)() as s:
        assert (await s.execute(select(Agency.name))).scalars().all() == [AGENCY]
    # The invitation is revoked, and the address is free: the fresh invited row is gone.
    assert await seat(engine) is None
    (invitation,) = await invitations_of(engine)
    assert invitation.revoked_at is not None and invitation.accepted_at is None
    # The old ticket is spent. The new one is the same verified identity in the same browser, and
    # names no invitation.
    assert (await peek(client, ticket)).status_code == 400
    record = json.loads(await redis.get(f"{TICKET_PREFIX}{token_digest(new_ticket)}"))
    assert "invitation" not in record
    assert record["binding_digest"] == token_digest(mine)
    assert record["sub"] == "sub-rae" and record["email_verified"] is True
    assert await redis.ttl(f"{TICKET_PREFIX}{token_digest(new_ticket)}") > 0
    pending = await peek(client, new_ticket)
    assert pending.json() == {"email": INVITEE, "suggestedName": "Rae Park", "invitedTo": None}

    # The existing step makes the agency, with this person as its owner.
    done = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": new_ticket, "agencyName": "Rae Park Studio", "fullName": "Rae Park"},
    )
    assert done.status_code == 201, done.text
    made = done.json()
    assert made["user"]["email"] == INVITEE and made["user"]["role"] == "owner"
    assert made["user"]["id"] != user_id
    assert made["agency"]["id"] != agency_id and made["agency"]["name"] == "Rae Park Studio"
    assert made["user"]["signInMethods"] == ["google"]

    # The inviter's agency gained no member, and shows neither the seat nor the invitation.
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    listed = (await client.get(f"{BASE}/agencies/{agency_id}/seats")).json()
    assert [m["email"] for m in listed["members"]] == [OWNER]
    assert listed["invitations"] == []
    assert listed["seats"] == {"used": 1, "limit": 3}


async def _revived_seat(client: AsyncClient, engine: AsyncEngine) -> tuple[str, str]:
    """Rae accepted by the emailed link, was removed, and was invited again: the same row, which
    has a history. Returns (the agency's id, the row's id). The client ends signed out."""
    agency_id, user_id = await invite(client)
    token = "choice-revived-" + "v" * 32
    async with sessions(engine)() as s:
        (invitation,) = (await s.execute(select(Invitation))).scalars().all()
        invitation.token_digest = token_digest(token)
        await s.commit()
    accepted = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Rae Park", "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    await client.post(f"{BASE}/auth/logout")
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    assert (await client.delete(f"{BASE}/users/{user_id}")).status_code == 204
    again = await client.post(f"{BASE}/agencies/{agency_id}/invitations", json={"email": INVITEE})
    assert again.status_code == 201, again.text
    assert again.json()["user"]["id"] == user_id  # the same row, revived
    await client.post(f"{BASE}/auth/logout")
    return agency_id, user_id


async def test_a_removed_member_invited_again_is_kept_and_the_sign_up_step_refuses_the_address(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The row has a history (what points at its id), so it is not deleted: it goes back to being
    removed. The address stays held, as it is for every address that has had a seat here, so the
    sign-up step's existing 409 is what the person meets. The invitation is revoked all the same."""
    agency_id, user_id = await _revived_seat(client, engine)
    revived = await seat(engine)
    assert revived is not None and revived.deleted_at is None
    assert revived.status is UserStatus.INVITED and revived.sessions_valid_after is not None
    ticket, _, _ = await offered(client, google)
    assert (await peek(client, ticket)).json()["invitedTo"] == AGENCY

    with structlog.testing.capture_logs() as events:
        declined = await decline(client, ticket)

    assert declined.status_code == 200, declined.text
    kept = await seat(engine)
    assert kept is not None and kept.id == user_id  # not deleted
    assert kept.deleted_at is not None and kept.status is UserStatus.INVITED
    assert kept.google_sub is None and kept.password_hash is None
    assert all(i.revoked_at is not None or i.accepted_at is not None
               for i in await invitations_of(engine))
    (line,) = [e for e in events if e["event"] == "invitation.declined"]
    assert line["row_deleted"] is False
    # The step that makes the agency refuses, as it refuses any address that has had a seat.
    done = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": declined.json()["ticket"], "agencyName": "Rae Studio", "fullName": "Rae"},
    )
    assert done.status_code == 409, done.text
    assert done.json()["type"].endswith("/email-already-registered")
    async with sessions(engine)() as s:
        assert (await s.execute(select(Agency.name))).scalars().all() == [AGENCY]
    # The inviting agency lists neither the seat nor an invitation.
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    listed = (await client.get(f"{BASE}/agencies/{agency_id}/seats")).json()
    assert [m["email"] for m in listed["members"]] == [OWNER]
    assert listed["invitations"] == []


async def _refer_to(engine: AsyncEngine, kind: str, agency_id: str, user_id: str) -> None:
    """Make one row that survives a user's deletion point at that user."""
    async with sessions(engine)() as s:
        client_row = Client(
            id=ids.new_id(ids.CLIENT), agency_id=agency_id, name="Referred", domain="ref.example"
        )
        s.add(client_row)
        await s.flush()
        if kind == "scan":
            s.add(
                Scan(
                    id=ids.new_id(ids.SCAN), client_id=client_row.id, agency_id=agency_id,
                    requested_by_user_id=user_id,
                )
            )
        elif kind == "prompt-run":
            s.add(
                PromptRun(
                    id=ids.new_id(ids.PROMPT_RUN), client_id=client_row.id, agency_id=agency_id,
                    asked_by_user_id=user_id, prompt_text="Which studio?",
                    status=PromptRunStatus.OK, subject_name="Referred",
                    subject_domain="ref.example",
                )
            )
        elif kind == "auto-rescan":
            client_row.auto_rescan_by_user_id = user_id
        else:
            s.add(
                Invitation(
                    id=ids.new_id(ids.INVITATION), agency_id=agency_id,
                    invited_by_user_id=user_id, email="someone-else@northlight.example",
                    role=UserRole.MEMBER, token_digest=token_digest("sent-by-the-seat"),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
        await s.commit()


@pytest.mark.parametrize("kind", ["scan", "prompt-run", "auto-rescan", "invitation-sent"])
async def test_a_seat_that_something_refers_to_is_kept_not_deleted(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    kind: str,
) -> None:
    """Deleting the row would not fail: every reference but the reset links is `ON DELETE SET
    NULL`, so it would silently detach the scan, the run, the rescan or the invitation from the
    person who made it. A row that anything names is put back as removed instead."""
    agency_id, user_id = await invite(client)
    await _refer_to(engine, kind, agency_id, user_id)
    ticket, _, _ = await offered(client, google)

    with structlog.testing.capture_logs() as events:
        declined = await decline(client, ticket)

    assert declined.status_code == 200, declined.text
    kept = await seat(engine)
    assert kept is not None and kept.id == user_id and kept.deleted_at is not None
    (line,) = [e for e in events if e["event"] == "invitation.declined"]
    assert line["row_deleted"] is False


async def test_a_seat_that_became_a_members_is_never_deleted(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Two tickets for one seat: "Join" on one, then "Start your own agency" on the other. The
    seat is a member's by then, and the second answer changes nothing."""
    agency_id, user_id = await invite(client)
    first, mine_first, _ = await offered(client, google, code="code-1")
    second, mine_second, _ = await offered(client, google, code="code-2")
    use_cookie(client, mine_first)
    assert (await join(client, first)).status_code == 200
    use_cookie(client, mine_second)  # the browser the second ticket was issued to
    before = await snapshot(engine)

    refused = await decline(client, second)

    assert_refused(refused)
    assert await snapshot(engine) == before
    rae = await seat(engine)
    assert rae is not None and rae.id == user_id and rae.deleted_at is None
    assert rae.status is UserStatus.ACTIVE and rae.google_sub == "sub-rae"
    assert rae.agency_id == agency_id


@pytest.mark.parametrize("how", ["active", "removed"])
async def test_only_an_invited_seat_that_is_still_there_can_be_freed(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    how: str,
) -> None:
    """The deleting helper refuses anything but an invited, not-removed row before it writes, so
    it cannot delete a member whatever calls it."""
    await invite(client)
    async with sessions(engine)() as s:
        rae = (await s.execute(select(User).where(User.email == INVITEE))).scalar_one()
        if how == "active":
            rae.status = UserStatus.ACTIVE
            rae.google_sub = "sub-rae"
        else:
            rae.deleted_at = datetime.now(UTC)
        await s.commit()
    before = await snapshot(engine)

    async with sessions(engine)() as s:
        rae = (await s.execute(select(User).where(User.email == INVITEE))).scalar_one()
        with pytest.raises(ValueError, match="invited seat"):
            await invitation_service.free_unactivated_seat(s, user=rae)
        await s.rollback()

    assert await snapshot(engine) == before


async def test_a_seat_already_gone_still_leads_to_the_form(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Two tickets for one seat, and "Start your own agency" on both. The first frees the address;
    nothing is left for the second to free, and it hands over its ticket all the same, because the
    person asked for their own agency and the sign-up step checks the address itself."""
    await invite(client)
    first, mine_first, _ = await offered(client, google, code="code-1")
    second, mine_second, _ = await offered(client, google, code="code-2")
    use_cookie(client, mine_first)
    one = await decline(client, first)
    use_cookie(client, mine_second)
    two = await decline(client, second)
    assert one.status_code == 200 and two.status_code == 200, (one.text, two.text)

    use_cookie(client, mine_second)
    made = await complete(client, two.json()["ticket"])
    assert made.status_code == 201, made.text
    use_cookie(client, mine_first)
    again = await complete(client, one.json()["ticket"])
    assert again.status_code == 409, again.text


# --- what "Start your own agency" is and is not refused for (the route's text) ------------------


def _withdraw(invitation: Invitation, how: str) -> None:
    if how == "revoked":
        invitation.revoked_at = datetime.now(UTC)
    else:
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)


@pytest.mark.parametrize("how", ["revoked", "expired"])
async def test_start_your_own_agency_is_not_refused_when_the_invitation_was_withdrawn_meanwhile(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    how: str,
) -> None:
    """Join is refused in this case (the invitation it would accept is gone); this is not. The seat
    is still an invited one and the person has asked for their own agency, so the address is freed,
    the invitation is stamped revoked (an expired one is stamped now), and the answer is a `200`
    with a new ticket, which makes the agency."""
    await invite(client)
    ticket, _, _ = await offered(client, google)
    async with sessions(engine)() as s:
        (invitation,) = (await s.execute(select(Invitation))).scalars().all()
        _withdraw(invitation, how)
        await s.commit()
    (before,) = await invitations_of(engine)
    assert (before.revoked_at is None) == (how == "expired")

    with structlog.testing.capture_logs() as events:
        declined = await decline(client, ticket)

    assert declined.status_code == 200, declined.text
    assert await seat(engine) is None  # a seat nobody ever used is deleted
    (after,) = await invitations_of(engine)
    assert after.revoked_at is not None and after.accepted_at is None
    (line,) = [e for e in events if e["event"] == "invitation.declined"]
    assert line["row_deleted"] is True
    done = await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": declined.json()["ticket"], "agencyName": "Rae Studio", "fullName": "Rae"},
    )
    assert done.status_code == 201, done.text
    assert done.json()["user"]["role"] == "owner"


async def test_start_your_own_agency_for_a_seat_removed_meanwhile_has_nothing_to_free(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The owner removes the seat between the sign-in and the press. Nothing is left to free and
    nothing is written (the removal revoked the invitation), and the answer is still a `200` with
    a new ticket. The removed seat holds the address, so the step that makes the agency gives its
    existing `409`."""
    _, user_id = await invite(client)
    ticket, mine, _ = await offered(client, google)
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    assert (await client.delete(f"{BASE}/users/{user_id}")).status_code == 204
    await client.post(f"{BASE}/auth/logout")
    use_cookie(client, mine)
    removed = await seat(engine)
    assert removed is not None and removed.deleted_at is not None
    (invitation,) = await invitations_of(engine)
    assert invitation.revoked_at is not None
    before = await snapshot(engine)

    with structlog.testing.capture_logs() as events:
        declined = await decline(client, ticket)

    assert declined.status_code == 200, declined.text
    assert await snapshot(engine) == before
    (line,) = [e for e in events if e["event"] == "invitation.declined"]
    assert line["row_deleted"] is False
    done = await complete(client, declined.json()["ticket"])
    assert done.status_code == 409, done.text
    assert done.json()["type"].endswith("/email-already-registered")


@pytest.mark.parametrize(
    ("how", "reason"), [("suspended", "account-suspended"), ("claimed", "email-claimed")]
)
async def test_start_your_own_agency_is_refused_for_a_suspended_or_claimed_seat(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    how: str,
    reason: str,
) -> None:
    """The refusals the route's text names besides an active seat (which
    `test_a_seat_that_became_a_members_is_never_deleted` covers): the one answer, and nothing is
    changed."""
    await invite(client)
    ticket, _, _ = await offered(client, google)
    async with sessions(engine)() as s:
        rae = (await s.execute(select(User).where(User.email == INVITEE))).scalar_one()
        if how == "suspended":
            rae.status = UserStatus.SUSPENDED
            rae.password_hash = "not-a-real-hash"  # a suspended row holds a credential (the CHECK)
        else:
            rae.google_sub = "sub-somebody-else"
        await s.commit()
    before = await snapshot(engine)

    with structlog.testing.capture_logs() as events:
        refused = await decline(client, ticket)

    assert_refused(refused)
    assert await snapshot(engine) == before
    (line,) = [e for e in events if e["event"] == "google.invitation_choice.refused"]
    assert line["choice"] == "decline" and line["reason"] == reason


async def test_a_second_ticket_for_a_seat_already_freed_leaves_a_newer_seats_invitation_alone(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Two tickets for one seat. The first answer deletes the row; the agency then invites the
    address again, which makes a NEW row with an invitation of its own. The second ticket was for
    the old row, which is gone: its answer has nothing to free, and it must not withdraw the new
    seat's invitation, which is another seat's."""
    agency_id, old_user_id = await invite(client)
    first, mine_first, _ = await offered(client, google, code="code-1")
    second, mine_second, _ = await offered(client, google, code="code-2")
    use_cookie(client, mine_first)
    assert (await decline(client, first)).status_code == 200
    assert await seat(engine) is None
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    again = await client.post(f"{BASE}/agencies/{agency_id}/invitations", json={"email": INVITEE})
    assert again.status_code == 201, again.text
    assert again.json()["user"]["id"] != old_user_id  # a new row
    await client.post(f"{BASE}/auth/logout")
    rows = await invitations_of(engine)
    assert len([i for i in rows if i.revoked_at is None and i.accepted_at is None]) == 1
    use_cookie(client, mine_second)
    before = await snapshot(engine)

    declined = await decline(client, second)

    assert declined.status_code == 200, declined.text
    assert await snapshot(engine) == before  # the new seat and its invitation are as they were
    newer = await seat(engine)
    assert newer is not None and newer.status is UserStatus.INVITED and newer.deleted_at is None


async def _traced(engine: AsyncEngine, trace: str, agency_id: str) -> None:
    """Leave exactly one trace of a life on the invited seat."""
    async with sessions(engine)() as s:
        rae = (await s.execute(select(User).where(User.email == INVITEE))).scalar_one()
        if trace == "last_login_at":
            rae.last_login_at = datetime.now(UTC) - timedelta(days=3)
        elif trace == "sessions_valid_after":
            rae.sessions_valid_after = datetime.now(UTC) - timedelta(days=3)
        elif trace == "password_hash":
            rae.password_hash = "not-a-real-hash"
        elif trace == "google_sub":
            rae.google_sub = "sub-rae"  # this person's own, so the seat is not "claimed"
        else:
            # An earlier invitation of this address in this agency was accepted by the emailed
            # link. That accept writes neither `last_login_at` nor `sessions_valid_after`.
            s.add(
                Invitation(
                    id=ids.new_id(ids.INVITATION), agency_id=agency_id, invited_by_user_id=None,
                    email=INVITEE, role=UserRole.MEMBER, token_digest=token_digest("accepted-once"),
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                    accepted_at=datetime.now(UTC) - timedelta(days=3),
                )
            )
        await s.commit()


@pytest.mark.parametrize(
    "trace",
    ["last_login_at", "sessions_valid_after", "password_hash", "google_sub", "accepted_invitation"],
)
async def test_a_seat_with_any_one_trace_of_a_life_is_kept_not_deleted(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    trace: str,
) -> None:
    """Each trace alone is enough, so each is pinned alone: removing any one clause of the check
    fails exactly its case. The last is the one a seat leaves that joined through the emailed link,
    never signed in again, and was removed and invited again before the credential version existed:
    all four columns empty, nothing pointing at the row, and an accepted invitation as the only
    record that it was a member."""
    agency_id, _ = await invite(client)
    await _traced(engine, trace, agency_id)
    ticket, _, _ = await offered(client, google)

    with structlog.testing.capture_logs() as events:
        declined = await decline(client, ticket)

    assert declined.status_code == 200, declined.text
    kept = await seat(engine)
    assert kept is not None and kept.deleted_at is not None  # put back as removed, not deleted
    (line,) = [e for e in events if e["event"] == "invitation.declined"]
    assert line["row_deleted"] is False


# --- the session store down: Join is a JSON route, so it is a 503 and not a redirect ------------


async def test_join_with_the_session_store_down_before_the_commit_changes_nothing(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await invite(client)
    ticket, _, _ = await offered(client, google)
    before = await snapshot(engine)

    async def away(self: SessionStore, user_id: str) -> int:
        raise RedisConnectionError("redis is away")

    with monkeypatch.context() as patched:
        patched.setattr(SessionStore, "revoke_all_for_user", away)
        joined = await join(client, ticket)

    assert joined.status_code == 503, joined.text
    assert joined.json()["type"].endswith("/service-unavailable")
    assert not sets_a_session(joined)
    assert await snapshot(engine) == before  # the seat is still invited, its invitation open
    assert await session_keys(settings) == []
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401
    # The ticket is spent, so the person signs in with Google again, and then Join works.
    again, _, _ = await offered(client, google, code="code-2")
    assert (await join(client, again)).status_code == 200


async def test_join_with_the_session_store_down_after_the_commit_leaves_the_join_standing(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await invite(client)
    ticket, _, _ = await offered(client, google)
    real = SessionStore.revoke_all_for_user
    calls: list[int] = []

    async def second_fails(self: SessionStore, user_id: str) -> int:
        calls.append(1)
        if len(calls) == 2:
            raise RedisConnectionError("redis is away")
        return await real(self, user_id)

    with monkeypatch.context() as patched:
        patched.setattr(SessionStore, "revoke_all_for_user", second_fails)
        with structlog.testing.capture_logs() as events:
            joined = await join(client, ticket)

    assert joined.status_code == 503, joined.text
    assert joined.json()["type"].endswith("/service-unavailable")
    assert not sets_a_session(joined)  # no session was made
    rae = await seat(engine)
    assert rae is not None and rae.status is UserStatus.ACTIVE and rae.google_sub == "sub-rae"
    (invitation,) = await invitations_of(engine)
    assert invitation.accepted_at is not None
    assert [e for e in events if e["event"] == "google.link.post_commit_revoke_failed"]
    assert INVITEE not in str(events)
    # The join stands, so the next Google sign-in signs the person in.
    google.identities["code-2"] = identity(INVITEE, sub="sub-rae", name="Rae Park")
    state = await start(client)
    landed(await callback(client, code="code-2", state=state), "/dashboard")
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200
    assert await session_keys(settings) != []


async def test_start_your_own_agency_with_redis_down_for_the_new_ticket_still_freed_the_address(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit is before the new ticket is made, so a Redis that fails there is a `503` with the
    invitation already revoked and the address already free. The person starts again from the
    sign-in page, and the address is then a new one."""
    await invite(client)
    ticket, _, _ = await offered(client, google)

    async def away(
        self: google_oauth.GoogleFlowStore,
        identity: GoogleIdentity,
        binding_digest: str | None,
        offer: InvitationOffer | None = None,
    ) -> str:
        raise RedisConnectionError("redis is away")

    with monkeypatch.context() as patched:
        patched.setattr(google_oauth.GoogleFlowStore, "issue_ticket", away)
        declined = await decline(client, ticket)

    assert declined.status_code == 503, declined.text
    assert declined.json()["type"].endswith("/service-unavailable")
    assert await seat(engine) is None
    (invitation,) = await invitations_of(engine)
    assert invitation.revoked_at is not None
    again, _, _ = await offered(client, google, code="code-2")
    made = await complete(client, again)
    assert made.status_code == 201, made.text


# --- two answers at once -----------------------------------------------------------------------


async def test_join_and_start_your_own_agency_pressed_at_once_make_exactly_one_choice(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One ticket, both buttons, one moment. The two requests are held at Redis until both have
    asked to take the ticket, so both really are in flight together, and exactly one choice
    happens. This shows the outcome, not which layer decides it: the single-use take and the row
    locks each give one winner. `test_one_ticket_makes_one_choice` pins single use, and the
    lock-wait tests below pin the locks."""
    await invite(client)
    ticket, _, _ = await offered(client, google)
    meeting = _MeetOnTheWire(monkeypatch, parties=2, prefix=TICKET_PREFIX)

    joined, declined = await asyncio.gather(
        asyncio.wait_for(join(client, ticket), timeout=30),
        asyncio.wait_for(decline(client, ticket), timeout=30),
    )

    assert meeting.released_with == [2, 2]  # both were sent before either was answered
    assert sorted([joined.status_code, declined.status_code]) == [200, 400]
    rae = await seat(engine)
    (invitation,) = await invitations_of(engine)
    if joined.status_code == 200:
        assert rae is not None and rae.status is UserStatus.ACTIVE
        assert invitation.accepted_at is not None and invitation.revoked_at is None
    else:
        assert rae is None
        assert invitation.revoked_at is not None and invitation.accepted_at is None


class _AnswerLandsFirst:
    """A session that, before its FIRST locking SELECT, lets another answer run to completion on a
    session of its own and commit: the interleaving two tickets for one seat can make."""

    def __init__(self, inner: Any, other: Callable[[], Awaitable[None]]) -> None:
        self.inner, self.other, self.fired = inner, other, False

    async def execute(self, stmt: Any, *a: Any, **k: Any) -> Any:
        if not self.fired and getattr(stmt, "_for_update_arg", None) is not None:
            self.fired = True
            await self.other()
        return await self.inner.execute(stmt, *a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


async def _offer_and_identity(
    engine: AsyncEngine, agency_id: str, user_id: str
) -> tuple[InvitationOffer, GoogleIdentity]:
    (invitation,) = await invitations_of(engine)
    return (
        an_offer_for(agency_id, user_id, invitation),
        GoogleIdentity(
            sub="sub-rae", email=INVITEE, email_verified=True, name="Rae Park", nonce="n"
        ),
    )


async def test_a_start_your_own_agency_that_lands_first_leaves_join_nothing_to_activate(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    agency_id, user_id = await invite(client)
    offer, who = await _offer_and_identity(engine, agency_id, user_id)
    factory = sessions(engine)

    async def decline_lands_first() -> None:
        async with factory() as other:
            await google_oauth.decline_invitation(other, who, offer)
            await other.commit()

    async with factory() as s:
        racing = _AnswerLandsFirst(s, decline_lands_first)
        with pytest.raises(google_oauth.GoogleSignInRefusedError) as refused:
            await google_oauth.join_invitation(racing, who, offer)
        await s.rollback()

    assert racing.fired and refused.value.reason == "account-unavailable"
    assert await seat(engine) is None
    (invitation,) = await invitations_of(engine)
    assert invitation.revoked_at is not None and invitation.accepted_at is None


async def test_a_join_that_lands_first_leaves_start_your_own_agency_nothing_to_delete(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    agency_id, user_id = await invite(client)
    offer, who = await _offer_and_identity(engine, agency_id, user_id)
    factory = sessions(engine)

    async def join_lands_first() -> None:
        async with factory() as other:
            await google_oauth.join_invitation(other, who, offer)
            await other.commit()

    async with factory() as s:
        racing = _AnswerLandsFirst(s, join_lands_first)
        with pytest.raises(google_oauth.GoogleSignInRefusedError) as refused:
            await google_oauth.decline_invitation(racing, who, offer)
        await s.rollback()

    assert racing.fired and refused.value.reason == "account-unavailable"
    rae = await seat(engine)
    assert rae is not None and rae.status is UserStatus.ACTIVE and rae.deleted_at is None
    (invitation,) = await invitations_of(engine)
    assert invitation.accepted_at is not None and invitation.revoked_at is None


@pytest.mark.parametrize(
    ("first", "second", "then"),
    [
        # The first answer made the seat a member's: a second Join finds that done and signs the
        # same person in; a second "Start your own agency" is refused.
        ("join", "join", "answered"),
        ("join", "decline", "account-unavailable"),
        # The first answer freed the address: a second Join has no seat; a second "Start your own
        # agency" has nothing left to free and answers all the same.
        ("decline", "join", "account-unavailable"),
        ("decline", "decline", "answered"),
    ],
)
async def test_one_answer_makes_the_other_wait_for_the_rows_and_then_decide_again(
    client: AsyncClient, engine: AsyncEngine, first: str, second: str, then: str
) -> None:
    """Two tickets for one seat, answered at the same moment. The answer that gets the invitation
    row first holds it (and then the user row) until it commits, so the other waits, and when it
    gets them it looks again at what the first left. Without the locks the second would read the
    seat before the first had written and act on a state that was already gone: it would not wait,
    and the test sees it finish while the first still holds the rows."""
    agency_id, user_id = await invite(client)
    offer, who = await _offer_and_identity(engine, agency_id, user_id)
    factory = sessions(engine)
    answers: dict[str, Callable[..., Awaitable[Any]]] = {
        "join": google_oauth.join_invitation,
        "decline": google_oauth.decline_invitation,
    }

    async def other_answer() -> str:
        async with factory() as other:
            try:
                await answers[second](other, who, offer)
                await other.commit()
            except google_oauth.GoogleSignInRefusedError as refused:
                await other.rollback()
                return refused.reason
            return "answered"

    async with factory() as holder:
        await answers[first](holder, who, offer)  # the rows are held from here, uncommitted
        waiting = asyncio.create_task(other_answer())
        done, _ = await asyncio.wait({waiting}, timeout=1.0)
        assert not done, "the second answer did not wait for the rows the first one holds"
        await holder.commit()
    assert await asyncio.wait_for(waiting, timeout=15) == then


# --- what the logs and the schema say ------------------------------------------------------------


async def test_no_log_line_carries_an_address_a_ticket_or_the_agencys_name(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    capsys,  # noqa: ANN001
    caplog,  # noqa: ANN001
) -> None:
    caplog.set_level("DEBUG")
    agency_id, user_id = await invite(client)
    with structlog.testing.capture_logs() as events:
        joining, _, _ = await offered(client, google, code="code-1")
        assert (await peek(client, joining)).status_code == 200
        assert (await join(client, joining)).status_code == 200
        await client.post(f"{BASE}/auth/logout")
    accepted = [e for e in events if e["event"] == "invitation.accepted"]
    assert len(accepted) == 1
    assert accepted[0]["via"] == "google" and accepted[0]["user_id"] == user_id
    assert accepted[0]["agency_id"] == agency_id
    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    for secret in (INVITEE, "Rae Park", AGENCY, joining):
        assert secret not in everything, secret


async def test_the_declined_line_is_ids_and_whether_the_row_was_deleted_and_nothing_else(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    capsys,  # noqa: ANN001
    caplog,  # noqa: ANN001
) -> None:
    caplog.set_level("DEBUG")
    agency_id, user_id = await invite(client)
    ticket, _, _ = await offered(client, google)
    with structlog.testing.capture_logs() as events:
        assert (await peek(client, ticket)).status_code == 200
        declined = await decline(client, ticket)
        assert (await peek(client, declined.json()["ticket"])).status_code == 200
        assert (await complete(client, declined.json()["ticket"])).status_code == 201
    (line,) = [e for e in events if e["event"] == "invitation.declined"]
    assert set(line) - {"event", "log_level"} == {"agency_id", "user_id", "row_deleted"}
    assert line["agency_id"] == agency_id and line["user_id"] == user_id
    assert line["row_deleted"] is True
    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    for secret in (INVITEE, "Rae Park", AGENCY, ticket, declined.json()["ticket"]):
        assert secret not in everything, secret


def test_every_reference_to_a_user_is_one_the_free_the_address_check_knows() -> None:
    """`free_unactivated_seat` deletes a row only when nothing refers to it
    (`invitations._is_named_by_anything`). A table that gains a foreign key to `users.id` must be
    looked at by whoever adds it: this fails until it is named here, and the check names it."""
    referring = {
        (fk.parent.table.name, fk.parent.name): fk.ondelete
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == "users" and fk.column.name == "id"
    }
    assert referring == {
        ("scans", "requested_by_user_id"): "SET NULL",
        ("prompt_runs", "asked_by_user_id"): "SET NULL",
        ("clients", "auto_rescan_by_user_id"): "SET NULL",
        ("invitations", "invited_by_user_id"): "SET NULL",
        # The one that goes with its user.
        ("password_reset_tokens", "user_id"): "CASCADE",
    }


async def test_the_session_of_the_browser_that_answers_is_replaced_not_kept(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
) -> None:
    """Join signs in like the callback's link does: whatever session the browser already presented
    is ended before the new one is made (A3 part 3)."""
    await invite(client)
    ticket, mine, _ = await offered(client, google)
    await sign_up_with_password(client, "someone-else@northlight.example")
    assert (
        await password_login(client, "someone-else@northlight.example", PASSWORD)
    ).status_code == 200
    stale = session_cookie_of(client)
    use_cookie(client, mine)

    joined = await join(client, ticket)

    assert joined.status_code == 200, joined.text
    assert session_cookie_of(client) != stale
    assert (await client.get(f"{BASE}/auth/me")).json()["user"]["email"] == INVITEE
    client.cookies.clear()
    client.cookies.set(*stale)
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401

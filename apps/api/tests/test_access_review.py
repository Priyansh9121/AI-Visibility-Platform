"""The one-time "Check who has access" review — A6 part 4, launch checklist 3.123.

A Google sign-in whose verified address already had a password account links to it and removes the
password. Whatever the earlier holder set up stays (the seats they added, the invitations they saw
accepted), so the person who proved the address is asked, once, about the people who were already in
the agency: Settings > Team lists them, each with Remove, above one "Keep these people" button. The
founder's decision of 2026-10-11, in the words of 2026-10-22.

What these tests hold the build to, and the negative control that breaks each (`nc/a6p4-*`):

* **The link time is written by a link that REPLACED a password, and by nothing else.** Not by a
  Google-first account, not by an invited seat that joins through Google (it had no password to
  replace), not by a repeat sign-in; and a link that fails writes nothing. It is the instant the
  review draws its line at.
* **The listed people are the other seat holders from before that instant:** never the holder, never
  someone who arrived after it (strictly: the instant itself is "after"), never someone removed. The
  response carries their ids and nothing else; names and addresses are the roster's own.
* **It is the holder's own, and an owner's or an admin's.** Another admin of the same agency never
  sees it, a member holder has none, and "Keep these people" closes only the caller's own review.
* **Keep ends it, once and for good.** So does removing everyone listed. A seat given a new life
  (a removed seat revived, an invited seat re-issued) starts without the old holder's review.
  Keep is the owner's or admin's alone (a member's call matches nothing), and a request on a
  session that a Google link is ending cannot close the notice of the person who took the account:
  it waits for the link and is refused.
* **The migration** adds two empty, nullable columns with no default, takes them away again, and a
  user from before it comes out with neither.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import structlog
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from avp_api import deps
from avp_api.main import app
from avp_api.models import Agency, Invitation, User, UserRole, UserStatus
from avp_api.security import token_digest
from avp_api.services import access_review
from avp_api.sessions import SessionStore
from test_google_sign_in import (  # noqa: F401 - `google` is a fixture, used by name
    BASE,
    PASSWORD,
    FakeGoogleProvider,
    callback,
    complete,
    google,
    hold,
    identity,
    join,
    landed,
    landed_ticket,
    password_login,
    session_cookie_of,
    sign_up_with_password,
    start,
    start_with_cookie,
    use_cookie,
)

OWNER = "owner@review.example"
ADMIN = "admin@review.example"
MEMBER = "member@review.example"
INVITED = "invited@review.example"  # invited as an admin and never answered
NEWCOMER = "newcomer@review.example"
KEEP = f"{BASE}/auth/me/access-review/keep"


# --- the scene ---------------------------------------------------------------------------------


def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def person(engine: AsyncEngine, user_id: str) -> User:
    async with sessions(engine)() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one()


@dataclass(frozen=True)
class Scene:
    agency_id: str
    owner_id: str
    admin_id: str
    member_id: str
    invited_id: str


async def accept(
    client: AsyncClient, engine: AsyncEngine, agency_id: str, email: str, name: str
) -> None:
    """The invitee redeems their invitation with a password of their own (and is signed in).

    The raw token is never returned, so the test writes its own digest onto the live invitation, as
    `test_seat_endpoints.py` does.
    """
    token = f"accept-token-for-{email}"
    async with sessions(engine)() as s:
        invitation = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        invitation.token_digest = token_digest(token)
        await s.commit()
    resp = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": name, "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text


async def build_agency(client: AsyncClient, engine: AsyncEngine) -> Scene:
    """An owner with a password who has let people in: an admin and a member who accepted (each with
    a password of their own), and an admin who was invited and has not answered.

    The client ends signed out. The agency has room for more than the suite's three seats.
    """
    owner_id = await sign_up_with_password(client, OWNER)
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    async with sessions(engine)() as s:
        await s.execute(update(Agency).where(Agency.id == agency_id).values(seat_limit=10))
        await s.commit()
    invited: dict[str, str] = {}
    for email, role in ((ADMIN, "admin"), (MEMBER, "member"), (INVITED, "admin")):
        resp = await client.post(
            f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": role}
        )
        assert resp.status_code == 201, resp.text
        invited[email] = resp.json()["user"]["id"]
    await client.post(f"{BASE}/auth/logout")
    await accept(client, engine, agency_id, ADMIN, "Sam Admin")
    await client.post(f"{BASE}/auth/logout")
    await accept(client, engine, agency_id, MEMBER, "Kit Member")
    await client.post(f"{BASE}/auth/logout")
    return Scene(
        agency_id=agency_id,
        owner_id=owner_id,
        admin_id=invited[ADMIN],
        member_id=invited[MEMBER],
        invited_id=invited[INVITED],
    )


async def sign_in_with_google(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    email: str,
    *,
    sub: str,
    code: str = "code-google",
) -> None:
    """The person signs in with Google in a browser of their own: for an address that holds a
    password account, this is the link that replaces the password."""
    google.identities[code] = identity(email, sub=sub, name="Google Name")
    client.cookies.clear()
    state = await start(client)
    landed(await callback(client, code=code, state=state), "/dashboard")


async def seat_list(client: AsyncClient, agency_id: str) -> dict:
    resp = await client.get(f"{BASE}/agencies/{agency_id}/seats")
    assert resp.status_code == 200, resp.text
    return resp.json()


def listed_ids(body: dict) -> list[str]:
    review = body["accessReview"]
    assert review is not None
    return review["userIds"]


def roster_order(body: dict, ids: list[str]) -> list[str]:
    """`ids` in the order the roster lists them."""
    return [member["id"] for member in body["members"] if member["id"] in set(ids)]


# --- the link time: written by a link that replaced a password, and by nothing else ------------


async def test_a_link_that_replaces_a_password_records_the_instant_it_was_made(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    before = datetime.now(UTC)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    after = datetime.now(UTC)

    owner = await person(engine, scene.owner_id)
    assert owner.password_hash is None  # the password is what the link replaced
    assert owner.google_linked_at is not None
    assert before <= owner.google_linked_at <= after
    assert owner.access_review_done_at is None
    # Nobody else's row was touched.
    for other in (scene.admin_id, scene.member_id, scene.invited_id):
        row = await person(engine, other)
        assert (row.google_linked_at, row.access_review_done_at) == (None, None)


async def test_a_repeat_google_sign_in_does_not_move_the_link_time(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """"Before you first signed in with Google": the first link is the line, and a later sign-in
    from the same Google account does not redraw it."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner", code="code-1")
    first = (await person(engine, scene.owner_id)).google_linked_at
    assert first is not None

    await sign_in_with_google(client, google, OWNER, sub="sub-owner", code="code-2")
    assert (await person(engine, scene.owner_id)).google_linked_at == first


async def test_a_google_first_account_has_no_link_time_and_no_review_however_often_it_signs_in(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """An owner created through Google never had a password to replace, so nobody in the agency
    "had access before" anything: not at the first sign-in, and not at the next."""
    google.identities["code-new"] = identity(OWNER, sub="sub-owner", name="Owner Person")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-new", state=state))
    created = await complete(client, ticket)
    assert created.status_code == 201, created.text
    owner_id = created.json()["user"]["id"]
    agency_id = created.json()["agency"]["id"]
    for email in (ADMIN, MEMBER):
        invited = await client.post(
            f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": "admin"}
        )
        assert invited.status_code == 201, invited.text

    await sign_in_with_google(client, google, OWNER, sub="sub-owner", code="code-again")

    owner = await person(engine, owner_id)
    assert owner.google_sub == "sub-owner"
    assert (owner.google_linked_at, owner.access_review_done_at) == (None, None)
    body = await seat_list(client, agency_id)
    assert len(body["members"]) == 3  # there ARE people who were there before
    assert body["accessReview"] is None


async def test_an_invited_seat_that_joins_through_google_has_no_link_time_and_no_review(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The invited admin says "Join" after signing in with Google. The seat had no password to
    replace, so there is no link to review: no link time and no notice, though the owner and two
    others were in the agency first."""
    scene = await build_agency(client, engine)
    google.identities["code-join"] = identity(INVITED, sub="sub-invited", name="Pat Ending")
    state = await start(client)
    ticket = landed_ticket(await callback(client, code="code-join", state=state))
    joined = await join(client, ticket)
    assert joined.status_code == 200, joined.text
    assert joined.json()["user"]["id"] == scene.invited_id

    row = await person(engine, scene.invited_id)
    assert row.status is UserStatus.ACTIVE and row.google_sub == "sub-invited"
    assert (row.google_linked_at, row.access_review_done_at) == (None, None)
    body = await seat_list(client, scene.agency_id)  # an admin: the seat list is theirs to read
    assert len(body["members"]) == 4
    assert body["accessReview"] is None


async def test_a_join_after_the_emailed_link_won_replaces_a_password_and_so_has_a_review(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The one way a Join replaces a password: the invited admin redeemed the emailed link between
    the Google sign-in and the press, so the seat holds a password when Join links Google over it.
    That IS a link that replaced a password, so it is recorded, and the admin is asked about the
    people who were in the agency before."""
    scene = await build_agency(client, engine)
    google.identities["code-join"] = identity(INVITED, sub="sub-invited", name="Pat Ending")
    state, mine = await start_with_cookie(client)
    ticket = landed_ticket(await callback(client, code="code-join", state=state))
    await accept(client, engine, scene.agency_id, INVITED, "Pat Ending")
    use_cookie(client, mine)

    joined = await join(client, ticket)
    assert joined.status_code == 200, joined.text

    row = await person(engine, scene.invited_id)
    assert row.password_hash is None and row.google_sub == "sub-invited"
    assert row.google_linked_at is not None
    body = await seat_list(client, scene.agency_id)
    assert set(listed_ids(body)) == {scene.owner_id, scene.admin_id, scene.member_id}


async def test_a_link_that_fails_writes_no_link_time(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The time is written in the link's own transaction. The session store being away fails the
    sign-in before the commit, so the password is still there, no Google account is linked, and
    there is no link time to draw a review from."""
    scene = await build_agency(client, engine)

    async def broken(self: SessionStore, user_id: str) -> int:
        raise RedisConnectionError("redis is away")

    monkeypatch.setattr(SessionStore, "revoke_all_for_user", broken)
    google.identities["code-1"] = identity(OWNER, sub="sub-owner")
    client.cookies.clear()
    state = await start(client)
    resp = await callback(client, code="code-1", state=state)
    assert landed(resp, "/")["reason"] == ["unavailable"]
    monkeypatch.undo()

    owner = await person(engine, scene.owner_id)
    assert owner.google_sub is None and owner.password_hash is not None
    assert (owner.google_linked_at, owner.access_review_done_at) == (None, None)


# --- who is shown the list, and who is on it ---------------------------------------------------


async def test_an_owner_whose_link_replaced_a_password_is_shown_the_people_from_before_it(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")

    body = await seat_list(client, scene.agency_id)
    ids = listed_ids(body)
    # Exactly the three other people: the two who accepted, and the invitee nobody has heard from
    # (the link revoked the invitation the owner sent, but the seat is still in the roster).
    assert set(ids) == {scene.admin_id, scene.member_id, scene.invited_id}
    assert len(ids) == 3
    assert scene.owner_id not in ids  # never the holder
    assert ids == roster_order(body, ids)  # in the roster's order
    # Every one of them is a person the same response shows, by name and address.
    assert {m["id"] for m in body["members"]} >= set(ids)


async def test_an_admin_whose_link_replaced_a_password_is_shown_the_people_from_before_it(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """An admin can set access up as well (invitations are theirs to make), and is a holder too.
    The owner is among the people they are asked about: the list is every other seat from before,
    whatever its rank (removing the owner is refused to an admin, as it is from the roster)."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, ADMIN, sub="sub-admin")

    body = await seat_list(client, scene.agency_id)
    ids = listed_ids(body)
    assert set(ids) == {scene.owner_id, scene.member_id, scene.invited_id}
    assert scene.admin_id not in ids
    assert ids == roster_order(body, ids)


async def test_people_who_arrive_after_the_link_are_not_listed(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, ADMIN, sub="sub-admin")
    holder_cookie = session_cookie_of(client)

    # The owner (still on a password) lets one more person in after the admin's link.
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    invited = await client.post(
        f"{BASE}/agencies/{scene.agency_id}/invitations", json={"email": NEWCOMER}
    )
    assert invited.status_code == 201, invited.text
    newcomer_id = invited.json()["user"]["id"]

    hold(client, holder_cookie)
    body = await seat_list(client, scene.agency_id)
    assert newcomer_id in {m["id"] for m in body["members"]}  # they are in the roster ...
    assert set(listed_ids(body)) == {scene.owner_id, scene.member_id, scene.invited_id}
    assert newcomer_id not in listed_ids(body)  # ... and not in the review


async def test_the_line_is_the_link_instant_and_a_seat_created_at_it_is_after_it(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Strictly before: a second earlier is listed; the very instant and a second later are not."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    linked_at = (await person(engine, scene.owner_id)).google_linked_at
    assert linked_at is not None
    async with sessions(engine)() as s:
        for user_id, created in (
            (scene.admin_id, linked_at - timedelta(seconds=1)),
            (scene.member_id, linked_at),
            (scene.invited_id, linked_at + timedelta(seconds=1)),
        ):
            await s.execute(update(User).where(User.id == user_id).values(created_at=created))
        await s.commit()

    assert (await seat_list(client, scene.agency_id))["accessReview"] == {
        "userIds": [scene.admin_id]
    }


async def test_a_person_removed_before_the_link_is_not_listed(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    removed = await client.delete(f"{BASE}/users/{scene.member_id}")
    assert removed.status_code == 204, removed.text
    await client.post(f"{BASE}/auth/logout")

    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    body = await seat_list(client, scene.agency_id)
    assert scene.member_id not in {m["id"] for m in body["members"]}
    assert set(listed_ids(body)) == {scene.admin_id, scene.invited_id}


async def test_a_suspended_seat_from_before_the_link_is_listed(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The roster shows a suspended seat (`seat_holders` filters on removal, not on status), and a
    suspended seat is still a seat the holder did not choose, so it is on the list."""
    scene = await build_agency(client, engine)
    async with sessions(engine)() as s:
        await s.execute(
            update(User).where(User.id == scene.member_id).values(status=UserStatus.SUSPENDED)
        )
        await s.commit()
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")

    body = await seat_list(client, scene.agency_id)
    suspended = [m["id"] for m in body["members"] if m["status"] == "suspended"]
    assert suspended == [scene.member_id]
    assert set(listed_ids(body)) == {scene.admin_id, scene.member_id, scene.invited_id}


async def test_a_holder_with_nobody_else_in_the_agency_has_no_review(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    owner_id = await sign_up_with_password(client, OWNER)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    assert (await person(engine, owner_id)).google_linked_at is not None  # the link was recorded
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    assert (await seat_list(client, agency_id))["accessReview"] is None


async def test_an_account_with_no_link_time_has_no_review_and_the_field_is_still_there(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """The usual case. `accessReview` is a key of the response, null, so a client can rely on it."""
    scene = await build_agency(client, engine)
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    body = await seat_list(client, scene.agency_id)
    assert set(body) == {"members", "invitations", "seats", "accessReview"}
    assert body["accessReview"] is None


async def test_the_review_carries_ids_and_nothing_else(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    body = await seat_list(client, scene.agency_id)

    review = body["accessReview"]
    assert set(review) == {"userIds"}
    assert all(isinstance(i, str) for i in review["userIds"])
    # No address, no name: the roster, in the same response, is where those are.
    wire = json.dumps(review)
    for text_ in (OWNER, ADMIN, MEMBER, INVITED, "Sam Admin", "Kit Member", "@"):
        assert text_ not in wire


async def test_a_member_holder_has_no_review(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """Only an owner's or an admin's account could have set access up, so a member has nothing of
    theirs to review. Their link IS recorded (it replaced their password); the seat list is
    refused them as it always was; and the review says no by itself, apart from that gate."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, MEMBER, sub="sub-member")
    member = await person(engine, scene.member_id)
    assert member.google_linked_at is not None

    refused = await client.get(f"{BASE}/agencies/{scene.agency_id}/seats")
    assert refused.status_code == 403
    assert "accessReview" not in refused.text

    others = (scene.owner_id, scene.admin_id, scene.invited_id)
    roster = [await person(engine, user_id) for user_id in others]
    assert all(other.created_at < member.google_linked_at for other in roster)
    assert access_review.people_to_review(member, roster) == []


async def test_another_admin_never_sees_the_holders_review(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The owner linked and has people to look at. The admin of the same agency, on the same seat
    list, is shown none: the review is read from the caller's own row."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    assert listed_ids(await seat_list(client, scene.agency_id))  # the owner's is there

    assert (await password_login(client, ADMIN, PASSWORD)).status_code == 200
    body = await seat_list(client, scene.agency_id)
    assert body["accessReview"] is None
    assert len(body["members"]) == 4  # the same roster, in full


def _in_memory_user(user_id: str, role: UserRole, created_at: datetime, **columns: object) -> User:
    return User(
        id=user_id,
        agency_id="agcy_in_memory",
        email=f"{user_id}@review.example",
        full_name=user_id,
        role=role,
        status=UserStatus.ACTIVE,
        created_at=created_at,
        **columns,
    )


@pytest.mark.parametrize(
    ("role", "shown"),
    [(UserRole.OWNER, True), (UserRole.ADMIN, True), (UserRole.MEMBER, False)],
)
def test_the_review_belongs_to_the_roles_that_manage_seats(role: UserRole, shown: bool) -> None:
    linked = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    holder = _in_memory_user("holder", role, linked - timedelta(days=9), google_linked_at=linked)
    earlier = _in_memory_user("earlier", UserRole.MEMBER, linked - timedelta(days=1))
    assert access_review.people_to_review(holder, [holder, earlier]) == (
        ["earlier"] if shown else []
    )


def test_the_roles_that_can_have_a_review_are_the_roles_that_manage_seats() -> None:
    """`deps` imports the services, so the service cannot import its role list: this keeps the two
    the same."""
    assert frozenset(deps.ADMIN_ROLES) == access_review.REVIEWING_ROLES


def test_a_review_that_is_done_or_was_never_made_lists_nobody() -> None:
    linked = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    earlier = _in_memory_user("earlier", UserRole.MEMBER, linked - timedelta(days=1))
    never = _in_memory_user("never", UserRole.OWNER, linked - timedelta(days=9))
    done = _in_memory_user(
        "done", UserRole.OWNER, linked - timedelta(days=9), google_linked_at=linked,
        access_review_done_at=linked + timedelta(minutes=5),
    )
    open_ = _in_memory_user(
        "open", UserRole.OWNER, linked - timedelta(days=9), google_linked_at=linked
    )
    assert access_review.people_to_review(never, [never, earlier]) == []
    assert access_review.people_to_review(done, [done, earlier]) == []
    assert access_review.people_to_review(open_, [open_, earlier]) == ["earlier"]


# --- "Keep these people" ----------------------------------------------------------------------


async def test_keep_ends_the_review_and_records_when(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    assert listed_ids(await seat_list(client, scene.agency_id))
    before = datetime.now(UTC)

    kept = await client.post(KEEP)
    assert kept.status_code == 204, kept.text
    assert kept.content == b""

    owner = await person(engine, scene.owner_id)
    assert owner.access_review_done_at is not None
    assert before <= owner.access_review_done_at <= datetime.now(UTC)
    # The link time stays: it is the fact the review was drawn from.
    assert owner.google_linked_at is not None
    body = await seat_list(client, scene.agency_id)
    assert body["accessReview"] is None
    assert len(body["members"]) == 4  # nobody was removed by keeping them


async def test_keep_is_idempotent_and_does_not_move_the_time_it_first_recorded(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    assert (await client.post(KEEP)).status_code == 204
    first = (await person(engine, scene.owner_id)).access_review_done_at
    assert first is not None

    for _ in range(2):  # a stale page, a double click
        assert (await client.post(KEEP)).status_code == 204
    assert (await person(engine, scene.owner_id)).access_review_done_at == first


async def test_keep_by_anyone_else_closes_nobody_elses_review(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The owner has a review open. The admin, another holder of the same agency with no review of
    their own, calls it: a 204, and nothing changes for either of them."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")

    assert (await password_login(client, ADMIN, PASSWORD)).status_code == 200
    assert (await client.post(KEEP)).status_code == 204

    owner = await person(engine, scene.owner_id)
    admin = await person(engine, scene.admin_id)
    assert owner.access_review_done_at is None
    assert (admin.google_linked_at, admin.access_review_done_at) == (None, None)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner", code="code-back")
    assert listed_ids(await seat_list(client, scene.agency_id))  # still open for the owner


async def test_a_members_keep_matches_nothing_logs_nothing_and_their_review_waits_for_a_promotion(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """A member whose link replaced a password has a link time and nothing to answer. Their call is
    a `204` that writes nothing and logs nothing, so a member who is promoted later (nothing does it
    today; a restore by hand would) finds the review where the link left it."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, MEMBER, sub="sub-member")
    assert (await person(engine, scene.member_id)).google_linked_at is not None

    with structlog.testing.capture_logs() as events:
        assert (await client.post(KEEP)).status_code == 204
    after = await person(engine, scene.member_id)
    assert after.access_review_done_at is None
    assert not [e for e in events if e["event"] == "access_review.kept"]

    async with sessions(engine)() as s:
        await s.execute(update(User).where(User.id == scene.member_id).values(role=UserRole.ADMIN))
        await s.commit()
    body = await seat_list(client, scene.agency_id)
    assert set(listed_ids(body)) == {scene.owner_id, scene.admin_id, scene.invited_id}


async def test_an_owner_with_nobody_to_list_who_keeps_is_recorded_harmlessly(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The one caller that is recorded without having had anything to answer: an owner whose link
    replaced a password and who is alone in the agency. Nothing was shown to them either way."""
    owner_id = await sign_up_with_password(client, OWNER)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    agency_id = (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]
    assert (await seat_list(client, agency_id))["accessReview"] is None

    with structlog.testing.capture_logs() as events:
        assert (await client.post(KEEP)).status_code == 204
    assert (await person(engine, owner_id)).access_review_done_at is not None
    assert len([e for e in events if e["event"] == "access_review.kept"]) == 1
    assert (await seat_list(client, agency_id))["accessReview"] is None


def test_keep_names_no_user_and_reads_no_body() -> None:
    """The row written is the caller's: the operation has no path parameter, no query parameter and
    no request body to put anybody else in."""
    operation = app.openapi()["paths"][KEEP]["post"]
    assert "parameters" not in operation
    assert "requestBody" not in operation
    assert "204" in operation["responses"]


async def test_keep_on_a_session_a_link_is_ending_waits_for_the_link_and_writes_nothing(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """The earlier password holder's Keep, let in before the Google link commits, runs its write
    after it. The link time is then set and the answer is empty, so a bare conditional UPDATE would
    record the answer for the person who has just taken the account, who has not seen the notice.

    The link is held open here as the callback holds it: its UPDATE of the row (the link time and
    the cutoff that ends the earlier sessions), not yet committed. Redis is left alone, so the
    session's record is alive and the cutoff alone is what refuses it. Keep passes
    `current_principal` (the commit it needs is not there yet), waits for the row, and once the
    link commits is refused as a dead session is, with nothing written and nothing logged."""
    scene = await build_agency(client, engine)
    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200  # the session at risk
    link_at = datetime.now(UTC)

    with structlog.testing.capture_logs() as events:
        async with sessions(engine)() as link:
            await link.execute(
                update(User)
                .where(User.id == scene.owner_id)
                .values(google_linked_at=link_at, sessions_valid_after=link_at)
            )
            keep = asyncio.create_task(client.post(KEEP))
            await asyncio.sleep(1)
            assert not keep.done(), "Keep did not wait for the link that holds the row"
            await link.commit()
        resp = await asyncio.wait_for(keep, timeout=15)

    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Session is expired or invalid."
    owner = await person(engine, scene.owner_id)
    assert owner.google_linked_at is not None  # the link is there ...
    assert owner.access_review_done_at is None  # ... and the notice nobody has seen is still open
    assert not [e for e in events if e["event"] == "access_review.kept"]


async def test_the_callers_row_is_held_against_a_link_but_not_against_a_row_that_names_them(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """What `lock_holder` takes, pinned apart from the route: the row is held, so an UPDATE of it
    (the link's) waits, and it is `FOR NO KEY UPDATE`, so a row that names the person as its author
    takes `FOR KEY SHARE` on it without waiting. `FOR UPDATE` would make Keep queue behind any such
    transaction, as `seats.lock_agency` explains for the agency."""
    scene = await build_agency(client, engine)
    user_id = scene.owner_id
    async with sessions(engine)() as holder:
        await access_review.lock_holder(holder, user_id)

        async with sessions(engine)() as link:
            await link.execute(text("SET LOCAL lock_timeout = '1s'"))
            with pytest.raises(DBAPIError) as waited:
                await link.execute(
                    update(User)
                    .where(User.id == user_id)
                    .values(google_linked_at=datetime.now(UTC))
                )
            assert "lock" in str(waited.value).lower()

        async with sessions(engine)() as author_of_a_child:
            shared = await author_of_a_child.execute(
                select(User.id)
                .where(User.id == user_id)
                .with_for_update(read=True, key_share=True, nowait=True)
            )
            assert shared.scalar_one() == user_id


async def test_keep_needs_a_session(client: AsyncClient) -> None:
    resp = await client.post(KEEP)
    assert resp.status_code == 401
    assert resp.json()["type"].endswith("/authentication-required")


async def test_keep_logs_ids_only_and_only_when_it_closes_something(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    with structlog.testing.capture_logs() as events:
        assert (await client.post(KEEP)).status_code == 204
        assert (await client.post(KEEP)).status_code == 204
    (kept,) = [e for e in events if e["event"] == "access_review.kept"]
    assert set(kept) == {"event", "log_level", "user_id", "agency_id"}
    assert kept["user_id"] == scene.owner_id and kept["agency_id"] == scene.agency_id
    dumped = json.dumps(events, default=str)
    for secret in (OWNER, ADMIN, MEMBER, INVITED):
        assert secret not in dumped


# --- removing the people listed ----------------------------------------------------------------


async def test_removing_one_person_leaves_the_others_listed(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    removed = await client.delete(f"{BASE}/users/{scene.admin_id}")
    assert removed.status_code == 204, removed.text

    body = await seat_list(client, scene.agency_id)
    assert set(listed_ids(body)) == {scene.member_id, scene.invited_id}


async def test_removing_everyone_listed_ends_the_review_without_a_keep(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")
    for user_id in (scene.admin_id, scene.member_id, scene.invited_id):
        assert (await client.delete(f"{BASE}/users/{user_id}")).status_code == 204

    body = await seat_list(client, scene.agency_id)
    assert [m["id"] for m in body["members"]] == [scene.owner_id]
    assert body["accessReview"] is None
    # Nobody pressed Keep: the review has simply no one left to show.
    assert (await person(engine, scene.owner_id)).access_review_done_at is None


# --- a seat given a new life starts without the old review -------------------------------------


async def test_a_revived_seat_starts_without_the_old_holders_review(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The admin linked (and kept) and was removed; inviting the address again revives the same
    row for a new person, who must not inherit the link time or the answer."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, ADMIN, sub="sub-admin")
    assert (await client.post(KEEP)).status_code == 204
    admin = await person(engine, scene.admin_id)
    assert admin.google_linked_at is not None and admin.access_review_done_at is not None

    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    assert (await client.delete(f"{BASE}/users/{scene.admin_id}")).status_code == 204
    revived = await client.post(
        f"{BASE}/agencies/{scene.agency_id}/invitations", json={"email": ADMIN, "role": "admin"}
    )
    assert revived.status_code == 201, revived.text
    assert revived.json()["user"]["id"] == scene.admin_id  # the same row

    row = await person(engine, scene.admin_id)
    assert row.google_sub is None and row.status is UserStatus.INVITED
    assert (row.google_linked_at, row.access_review_done_at) == (None, None)


async def test_a_re_issued_invitation_starts_without_a_review(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    """The re-issue branch: an invited row that somehow carries both is given neither when its link
    is sent again, alongside the Google link that goes with it."""
    scene = await build_agency(client, engine)
    stamp = datetime.now(UTC)
    async with sessions(engine)() as s:
        await s.execute(
            update(User)
            .where(User.id == scene.invited_id)
            .values(google_linked_at=stamp, access_review_done_at=stamp)
        )
        await s.commit()

    assert (await password_login(client, OWNER, PASSWORD)).status_code == 200
    again = await client.post(
        f"{BASE}/agencies/{scene.agency_id}/invitations", json={"email": INVITED, "role": "admin"}
    )
    assert again.status_code == 201, again.text
    assert again.json()["seatConsumed"] is False  # the same seat, its link sent again

    row = await person(engine, scene.invited_id)
    assert (row.google_linked_at, row.access_review_done_at) == (None, None)


# --- the wire ----------------------------------------------------------------------------------


async def test_neither_time_is_on_a_user_or_the_signed_in_context(
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    """The times are not facts about a person to show anyone: `UserOut` (the context's `user` and
    the roster's `members`) carries neither."""
    scene = await build_agency(client, engine)
    await sign_in_with_google(client, google, OWNER, sub="sub-owner")

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    me = (await client.get(f"{BASE}/auth/me")).json()
    members = (await seat_list(client, scene.agency_id))["members"]
    for name in keys(me) | keys(members):
        assert "linked" not in name.lower() and "review" not in name.lower(), name


def test_the_document_names_the_review_as_ids_only_and_optional() -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["AccessReviewOut"]["properties"]) == {"userIds"}
    assert schemas["AccessReviewOut"]["required"] == ["userIds"]
    seat_list_out = schemas["SeatListOut"]
    assert "accessReview" in seat_list_out["properties"]
    # Optional, so a client that has never heard of it is unaffected.
    assert "accessReview" not in seat_list_out["required"]


# --- the migration -----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_DIR = REPO_ROOT / "infra" / "db"
ALEMBIC = REPO_ROOT / "apps" / "api" / ".venv" / "bin" / "alembic"
BEFORE = "a3480a6d70d1"
REVISION = "d73a848787a9"
SCRATCH_PREFIX = "avp_access_review_"
COLUMNS = ("google_linked_at", "access_review_done_at")


def alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ALEMBIC), *args],
        cwd=DB_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_the_revision_adds_two_empty_columns_to_users_that_predate_it_and_takes_them_away(
    scratch_databases,  # noqa: ANN001
) -> None:
    """The real revision, up and down, over a database at the revision before it that already holds
    a user. The user comes out of the upgrade with no link time and no answer (nothing is guessed
    for an account linked earlier), the columns are nullable with no default so the old instance
    still serving during the deploy can insert a user without them, and the downgrade drops both
    columns and keeps the user."""
    agency, user, later = "agcy_migration", "user_migration", "user_migration_later"
    insert_user = text(
        "INSERT INTO users (id, agency_id, email, password_hash, full_name, role, status) "
        "VALUES (:id, :agency, :email, 'not-a-real-hash', 'Migration Person', 'owner', 'active')"
    )
    columns_of_users = text(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_name = 'users' AND column_name = ANY(:names) ORDER BY column_name"
    )
    async with scratch_databases.database(SCRATCH_PREFIX) as url:
        before = alembic(["upgrade", BEFORE], url)
        assert before.returncode == 0, f"{before.stdout}\n{before.stderr}"
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO agencies (id, name, slug, seat_limit) "
                        "VALUES (:id, 'Migration Agency', 'migration-agency', 3)"
                    ),
                    {"id": agency},
                )
                await conn.execute(
                    insert_user, {"id": user, "agency": agency, "email": "a@review.example"}
                )
                present = (await conn.execute(columns_of_users, {"names": list(COLUMNS)})).all()
            assert present == []

            up = alembic(["upgrade", REVISION], url)
            assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"
            assert f"Running upgrade {BEFORE} -> {REVISION}" in up.stderr

            async with engine.begin() as conn:
                # What the instance still serving during the deploy writes: neither column.
                await conn.execute(
                    insert_user, {"id": later, "agency": agency, "email": "b@review.example"}
                )
                found = (await conn.execute(columns_of_users, {"names": list(COLUMNS)})).all()
                values = (
                    await conn.execute(
                        text(
                            "SELECT id, google_linked_at IS NULL, access_review_done_at IS NULL "
                            "FROM users ORDER BY id"
                        )
                    )
                ).all()
            assert [tuple(r) for r in found] == [
                ("access_review_done_at", "timestamp with time zone", "YES", None),
                ("google_linked_at", "timestamp with time zone", "YES", None),
            ]
            assert [tuple(r) for r in values] == [(user, True, True), (later, True, True)]

            down = alembic(["downgrade", BEFORE], url)
            assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
            async with engine.begin() as conn:
                found = (await conn.execute(columns_of_users, {"names": list(COLUMNS)})).all()
                kept = (await conn.execute(text("SELECT count(*) FROM users"))).scalar_one()
            assert found == []
            assert kept == 2

            again = alembic(["upgrade", REVISION], url)
            assert again.returncode == 0, f"{again.stdout}\n{again.stderr}"
        finally:
            await engine.dispose()

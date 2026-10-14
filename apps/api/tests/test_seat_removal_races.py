"""Two seat removals at once (launch checklist 3.124).

`DELETE /users/{userId}` used to read its target, count the active owners and write, with nothing
between the count and the write. Two owners removing each other at the same moment each counted two
owners, each passed the last-owner check and each committed: the agency was left with no owner, and
nobody who could manage its seats or undo it. The same gap let a removed owner finish what they had
started: with owners A, B and C, A removing B while B removes C could leave only A, because B's
request was let in before B was gone.

The removal now takes the agency's row lock before it reads anything, and looks its caller up again
under that lock. These tests prove the lock, the second look and the order the rows are taken in.

`POST /agencies/{agencyId}/invitations` had the same gap and now closes it the same way (section 5):
it took the lock on two of its four paths only, and never asked its caller again, so an admin
removed while an invitation waited could still write one and, with email off, be handed the link.

HOW A RACE IS MADE HERE. A race test that passes without the code it tests proves nothing, so each
one below was run with that code taken out, and failed.

* The pool is warmed first (`_warm_the_pool`), as the other race tests do: run cold, the second
  request can wait to open a connection of its own while the first runs on. (The gate below is what
  makes these tests bite; they fail without the lock on a cold pool as well.)
* `gate` parks each request that has counted the active owners, with the number in hand, until the
  test lets it go. Without the lock both requests arrive and are let go together, and both act on
  a count that is no longer true. With it only one can arrive: the other is held at the lock, and
  the test lets go when it sees a request waiting for a lock in Postgres. `removal_gate` parks a
  removal that holds the lock and has read everything, just before it writes.
* `_agency_lock_held` is the test holding the agency's row itself, so a removal waits for it and the
  test changes the world under the waiting request, as a request that committed ahead of it would.
  The request must then be answered as it would be if it had arrived after the change.

Sections 6 to 9 are the security review's round: the lock's strength (6), the Google link's lock
order (7), a caller whose session was revoked while the request waited (8) and a re-sent invitation
the invitee accepts meanwhile (9).

`tests/` is not a package, so the helpers the other race tests share are written out again.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from avp_api import ids
from avp_api.config import Settings
from avp_api.models import Client, Invitation, PasswordResetToken, User, UserRole, UserStatus
from avp_api.security import token_digest
from avp_api.services import auth as auth_service
from avp_api.services import google_oauth
from avp_api.services import invitations as invitation_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NOT_ACTIVE = "This account is no longer active."
ONLY_AN_OWNER = "Only an owner can remove another owner's seat."


# --- the database -------------------------------------------------------------------------------


def _db(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _warm_the_pool(engine: AsyncEngine) -> None:
    """Open four connections at once and give them back, so a race starts with idle connections.

    Run cold, the second of two requests waits to open a connection of its own and the first is
    done by then (the reset-confirm race test passed with its row lock taken out until it did this).
    """

    async def hold_a_connection() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("select pg_sleep(0.3)"))

    await asyncio.gather(*(hold_a_connection() for _ in range(4)))


async def _active_owners(engine: AsyncEngine, agency_id: str) -> list[str]:
    async with _db(engine)() as s:
        rows = await s.execute(
            select(User.id).where(
                User.agency_id == agency_id,
                User.role == UserRole.OWNER,
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
        )
        return sorted(rows.scalars())


async def _is_removed(engine: AsyncEngine, user_id: str) -> bool:
    async with _db(engine)() as s:
        deleted_at = (
            await s.execute(select(User.deleted_at).where(User.id == user_id))
        ).scalar_one()
    return deleted_at is not None


async def _stored(engine: AsyncEngine, table: str, row_id: str) -> tuple[Any, ...]:
    """The whole stored row, every column: a second look shows whether anything wrote to it."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(text(f"SELECT * FROM {table} WHERE id = :id"), {"id": row_id})
        ).one()
    return tuple(row)


async def _seat_rows(
    engine: AsyncEngine, *, except_user: str
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Every invitation and every user but one, as stored: a second look shows what was written."""
    async with engine.connect() as conn:
        invitations = (await conn.execute(text("SELECT * FROM invitations ORDER BY id"))).all()
        users = (
            await conn.execute(
                text("SELECT * FROM users WHERE id <> :id ORDER BY id"), {"id": except_user}
            )
        ).all()
    return [tuple(row) for row in invitations], [tuple(row) for row in users]


async def _waiting_for_a_lock(engine: AsyncEngine) -> int:
    """How many backends of this database wait for a lock that another transaction holds."""
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        ).scalar_one()


@contextlib.asynccontextmanager
async def _agency_lock_held(engine: AsyncEngine, agency_id: str) -> AsyncIterator[AsyncConnection]:
    """The test holds the agency's row, as the request ahead of a removal would.

    `FOR NO KEY UPDATE`, the strength `seats.lock_agency` takes (a plain `FOR UPDATE` would also
    wait for every uncommitted child row of the agency; see section 6).

    What is run on the connection inside the block commits with the lock's release, in one step.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT 1 FROM agencies WHERE id = :id FOR NO KEY UPDATE"), {"id": agency_id}
        )
        yield conn


async def _until(
    what: str, check: Callable[[], bool | Awaitable[bool]], *, seconds: float = 10.0
) -> None:
    """Poll until `check` holds, failing the test (and not hanging it) after `seconds`."""
    try:
        async with asyncio.timeout(seconds):
            while True:
                answer = check()
                if inspect.isawaitable(answer):
                    answer = await answer
                if answer:
                    return
                await asyncio.sleep(0.01)
    except TimeoutError:
        pytest.fail(f"timed out waiting for {what}")


# --- an agency, each seat signed in through a client of its own ---------------------------------


@dataclass
class Seat:
    id: str
    email: str
    http: AsyncClient  # signed in as this seat: its own connection to the app and cookie jar

    async def removes(self, other: Seat) -> Response:
        return await self.http.delete(f"{BASE}/users/{other.id}")

    async def invites(self, agency_id: str, email: str, role: str = "member") -> Response:
        return await self.http.post(
            f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": role}
        )


@dataclass
class World:
    agency_id: str
    seats: list[Seat]


@contextlib.asynccontextmanager
async def _agency(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, *roles: UserRole
) -> AsyncIterator[World]:
    """An agency whose first seat is its owner and whose other seats hold `roles`, all active."""
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with _db(engine)() as s:
        agency, owner = await auth_service.sign_up_agency(
            s,
            agency_name="Removal Races",
            full_name="Seat 0",
            email="seat0@removals.example",
            password=PASSWORD,
            settings=settings,
        )
        users = [owner]
        for n, role in enumerate(roles, start=1):
            users.append(
                await auth_service.create_user_in_agency(
                    s,
                    agency_id=agency.id,
                    email=f"seat{n}@removals.example",
                    full_name=f"Seat {n}",
                    password=PASSWORD,
                    role=role,
                    settings=settings,
                )
            )
        await s.commit()
        agency_id, people = agency.id, [(u.id, u.email) for u in users]

    async with contextlib.AsyncExitStack() as stack:
        seats = []
        for user_id, email in people:
            http = await stack.enter_async_context(
                AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Origin": client.headers["Origin"]},
                )
            )
            login = await http.post(
                f"{BASE}/auth/login", json={"email": email, "password": PASSWORD}
            )
            assert login.status_code == 200, login.text
            seats.append(Seat(user_id, email, http))
        yield World(agency_id, seats)


# --- the gate: a request that has counted the owners waits with the number in hand ---------------


class Gate:
    """Parks every request that reaches it, until the test lets them go.

    `parked` counts the arrivals. Once let go, a request arriving later passes straight on.
    """

    def __init__(self) -> None:
        self.parked = 0
        self._go = asyncio.Event()

    async def wait(self) -> None:
        self.parked += 1
        await self._go.wait()

    def let_go(self) -> None:
        self._go.set()


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[Gate]:
    """Parks each removal after it has counted the active owners, the number still in hand.

    Counted first and parked after, so what a parked request holds is a count taken before the
    other request had changed anything.
    """
    parked = Gate()
    real_count = invitation_service.count_active_owners

    async def count_then_wait(session: AsyncSession, agency_id: str) -> int:
        counted = await real_count(session, agency_id)
        await parked.wait()
        return counted

    monkeypatch.setattr(invitation_service, "count_active_owners", count_then_wait)
    yield parked
    parked.let_go()  # a test that failed before it did leaves nothing waiting


@pytest.fixture
def removal_gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[Gate]:
    """Parks each removal that has passed every check, just before it writes.

    A removal parked here holds the agency's lock (the removal takes it first), so a request that
    needs the lock is made to wait behind it, in the order the test chooses.
    """
    parked = Gate()
    real_release = invitation_service.release_seat

    async def wait_then_release(session: AsyncSession, *, agency_id: str, user: User) -> None:
        await parked.wait()
        await real_release(session, agency_id=agency_id, user=user)

    monkeypatch.setattr(invitation_service, "release_seat", wait_then_release)
    yield parked
    parked.let_go()


# --- 1. two owners removing each other at the same moment ----------------------------------------


async def test_two_owners_removing_each_other_at_once_leave_exactly_one_owner(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, gate: Gate
) -> None:
    async with _agency(client, engine, settings, UserRole.OWNER) as agency:
        a, b = agency.seats
        await _warm_the_pool(engine)

        a_removes_b = asyncio.create_task(a.removes(b))
        b_removes_a = asyncio.create_task(b.removes(a))
        try:
            # Without the lock both requests count two owners and wait here together. With it one
            # of them is held at the lock, so the test also lets go when it sees one waiting.
            await _until(
                "both requests to have counted, or one to wait for the lock",
                lambda: gate.parked >= 2
                or a_removes_b.done()
                or b_removes_a.done()
                or _waiting_for_a_lock(engine),
            )
        finally:
            gate.let_go()
            # If anything above failed, both requests still end before the test does.
            await asyncio.gather(a_removes_b, b_removes_a, return_exceptions=True)

        # The point of it all: an agency is never left without an owner.
        owners = await _active_owners(engine, agency.agency_id)
        assert len(owners) == 1, f"the agency was left with {len(owners)} active owners"

        first, second = a_removes_b.result(), b_removes_a.result()
        assert sorted([first.status_code, second.status_code]) == [204, 401], (
            first.text,
            second.text,
        )
        winner, loser, refused = (a, b, second) if first.status_code == 204 else (b, a, first)
        # The loser was removed by the winner while it waited, and is told what its next request
        # would be told: its seat is gone. It never reached the count. (The lock alone would have
        # let it count, find one owner and answer 409; the second look under the lock comes first.)
        assert refused.json()["type"] == "/problems/authentication-required"
        assert refused.json()["detail"] == NOT_ACTIVE
        assert gate.parked == 1, f"{gate.parked} requests counted the owners at once"

        # The owner left is the one whose request won.
        assert owners == [winner.id]
        assert await _is_removed(engine, loser.id)
        assert (await winner.http.get(f"{BASE}/auth/me")).status_code == 200
        assert (await loser.http.get(f"{BASE}/auth/me")).status_code == 401


# --- 2. A removes B while B removes C ------------------------------------------------------------


async def test_an_owner_removed_while_removing_another_owner_cannot_finish_removing_them(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, gate: Gate
) -> None:
    async with _agency(client, engine, settings, UserRole.OWNER, UserRole.OWNER) as agency:
        a, b, c = agency.seats
        await _warm_the_pool(engine)

        a_removes_b = asyncio.create_task(a.removes(b))
        b_removes_c: asyncio.Task[Response] | None = None
        try:
            # A's request counts three owners and waits, holding the agency's lock when there is
            # one. B's removal has not committed, so B is a live owner as far as anything can see.
            await _until(
                "A's request to have counted", lambda: gate.parked >= 1 or a_removes_b.done()
            )
            b_removes_c = asyncio.create_task(b.removes(c))
            # B's request got in before B was gone. With the lock it now waits for A's; without
            # it, it counts three owners as well and waits at the gate.
            await _until(
                "B's request to wait for the lock, or to have counted",
                lambda: gate.parked >= 2
                or a_removes_b.done()
                or b_removes_c.done()
                or _waiting_for_a_lock(engine),
            )
        finally:
            gate.let_go()
            # If anything above failed, both requests still end before the test does.
            await asyncio.gather(
                *(task for task in (a_removes_b, b_removes_c) if task is not None),
                return_exceptions=True,
            )

        assert b_removes_c is not None
        assert a_removes_b.result().status_code == 204
        refused = b_removes_c.result()
        # B was removed by A's commit while this waited, so it removes nobody: C is untouched.
        assert refused.status_code == 401, refused.text
        assert refused.json()["detail"] == NOT_ACTIVE
        assert gate.parked == 1, f"{gate.parked} requests counted the owners at once"

        assert await _active_owners(engine, agency.agency_id) == sorted([a.id, c.id])
        assert await _is_removed(engine, b.id)
        assert not await _is_removed(engine, c.id)
        assert (await c.http.get(f"{BASE}/auth/me")).status_code == 200


# --- 3. a removal waits for the lock, and is answered as if it had arrived afterwards -------------


@pytest.mark.parametrize(
    ("roles", "actor", "target", "change", "status", "detail"),
    [
        pytest.param(
            (UserRole.ADMIN, UserRole.MEMBER), 1, 2, None, 204, None, id="nothing_changed"
        ),
        pytest.param(
            (UserRole.ADMIN, UserRole.MEMBER),
            1,
            2,
            "UPDATE users SET deleted_at = now() WHERE id = :id",
            401,
            NOT_ACTIVE,
            id="remover_removed",
        ),
        pytest.param(
            (UserRole.ADMIN, UserRole.MEMBER),
            1,
            2,
            "UPDATE users SET status = 'suspended' WHERE id = :id",
            401,
            NOT_ACTIVE,
            id="remover_suspended",
        ),
        pytest.param(
            (UserRole.ADMIN, UserRole.MEMBER),
            1,
            2,
            "UPDATE users SET role = 'member' WHERE id = :id",
            403,
            "This action requires one of: admin, owner. Your role is member.",
            id="remover_demoted_to_member",
        ),
        # The role the request began with is not the role it acts with: an owner who is an admin
        # by the time the lock is theirs removes no owner.
        pytest.param(
            (UserRole.OWNER, UserRole.OWNER),
            1,
            2,
            "UPDATE users SET role = 'admin' WHERE id = :id",
            403,
            ONLY_AN_OWNER,
            id="remover_demoted_from_owner_to_admin",
        ),
    ],
)
async def test_a_removal_waits_for_the_agency_lock_then_decides_on_what_is_true_by_then(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    roles: tuple[UserRole, ...],
    actor: int,
    target: int,
    change: str | None,
    status: int,
    detail: str | None,
) -> None:
    async with _agency(client, engine, settings, *roles) as agency:
        remover, removed = agency.seats[actor], agency.seats[target]
        target_before = await _stored(engine, "users", removed.id)

        async with _agency_lock_held(engine, agency.agency_id) as held:
            removal = asyncio.create_task(remover.removes(removed))
            try:
                await _until(
                    "the removal to wait for the agency's lock",
                    lambda: removal.done() or _waiting_for_a_lock(engine),
                )
                assert not removal.done(), "the removal did not wait for the agency's lock"
                if change is not None:
                    # What a request that committed ahead of it would have done to the remover.
                    await held.execute(text(change), {"id": remover.id})
            except BaseException:
                removal.cancel()
                raise
        # The lock is released and the change committed with it.
        response = await asyncio.wait_for(removal, 10)

        assert response.status_code == status, response.text
        if change is None:
            assert response.content == b""
            assert await _is_removed(engine, removed.id)
            return

        assert response.json()["detail"] == detail
        # Nothing was written, and the answer is the one the same request gets now.
        assert await _stored(engine, "users", removed.id) == target_before
        again = await remover.removes(removed)
        assert (again.status_code, again.json()) == (response.status_code, response.json())


# --- 4. the order the rows are taken in ----------------------------------------------------------
#
# The removal takes the agency's row first, as `invite_seat` does, then the invitation rows, then
# the reset links, and the user's row last. The accept and the Google sign-in take an invitation
# and then the user's row, and the reset confirm takes a link and then the user's row; none of them
# takes the agency's. The Google link to a password account (section 7) retires the user's sent
# invitations and then their reset links: the removal's own order. So no path holds a row that the
# removal waits for while it waits for one the removal holds: the orders that could deadlock are
# the removal holding the user's row while it waits for an invitation or a link, and a path that
# takes the links before the invitations.


def _is_a_lock(statement: str) -> bool:
    """A statement that takes a row lock, at either strength (`FOR UPDATE`, `FOR NO KEY UPDATE`)."""
    return " FOR UPDATE" in statement or " FOR NO KEY UPDATE" in statement


def _is_the_agency_lock(statement: str) -> bool:
    """`seats.lock_agency`: the agency's row, `FOR NO KEY UPDATE`, and nothing stronger."""
    return "FROM agencies" in statement and statement.endswith("FOR NO KEY UPDATE")


async def test_a_removal_locks_the_agency_before_it_reads_the_target_and_writes_the_user_last(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    async with _agency(client, engine, settings, UserRole.OWNER) as agency:
        a, b = agency.seats

        statements: list[str] = []

        def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            statements.append(" ".join(statement.split()))

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            removed = await a.removes(b)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
        assert removed.status_code == 204, removed.text

    locks = [i for i, s in enumerate(statements) if _is_a_lock(s)]
    assert len(locks) == 1 and _is_the_agency_lock(statements[locks[0]]), statements
    before, after = statements[: locks[0]], statements[locks[0] + 1 :]

    # Before the lock: only the caller's own look (by id), no write, and neither the target nor
    # the owners of the agency (both are read `WHERE users.agency_id = ...`), nor anything of an
    # invitation or a link.
    assert not [s for s in before if s.startswith(("UPDATE", "INSERT", "DELETE"))], before
    assert not [s for s in before if "users.agency_id = " in s], before
    assert not [s for s in before if "invitations" in s or "password_reset_tokens" in s], before
    # After it: the target and the owners are read, and the user's row is the last thing written.
    assert [s for s in after if "users.agency_id = " in s], after
    tables = [s.split()[1] for s in after if s.startswith("UPDATE")]
    assert tables and tables[-1] == "users" and tables.count("users") == 1, tables
    # The invitations before the reset links: the order the Google link to a password account takes
    # them in too (section 7), so the two cannot each wait for the row the other holds.
    assert tables.index("invitations") < tables.index("password_reset_tokens"), tables


@pytest.mark.parametrize("held", ["invitation", "reset_link"])
async def test_a_removal_holds_no_user_row_while_it_waits_for_an_invitation_or_a_link(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, held: str
) -> None:
    """The path most likely to take the rows the other way round, played by the test.

    An accept or a Google sign-in locks the invitation, then the seat's user row; a reset confirm
    locks the link, then the user row. Here the test holds the invitation (or the link) of the seat
    being removed, and once the removal is waiting for it asks for the user row, as those paths do.
    If the removal held the user row while it waited, the two would wait for each other.
    """
    async with _agency(client, engine, settings, UserRole.MEMBER) as agency:
        owner, member = agency.seats
        invitation_id, link_id = ids.new_id(ids.INVITATION), ids.new_id(ids.PASSWORD_RESET)
        async with _db(engine)() as s:
            s.add(
                Invitation(
                    id=invitation_id,
                    agency_id=agency.agency_id,
                    invited_by_user_id=owner.id,
                    email=member.email,
                    role=UserRole.MEMBER,
                    token_digest=token_digest("held-invitation"),
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
            s.add(
                PasswordResetToken(
                    id=link_id,
                    user_id=member.id,
                    token_digest=token_digest("held-link"),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await s.commit()
        table, row_id = (
            ("invitations", invitation_id)
            if held == "invitation"
            else ("password_reset_tokens", link_id)
        )

        removal: asyncio.Task[Response] | None = None
        try:
            async with engine.begin() as other:
                # A lock timeout, so a user row the removal held would fail this test in two
                # seconds and not hang it (Postgres would otherwise settle the wait as a deadlock).
                await other.execute(text("SET LOCAL lock_timeout = '2s'"))
                await other.execute(
                    text(f"SELECT 1 FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id}
                )
                removal = asyncio.create_task(owner.removes(member))
                await _until(
                    "the removal to wait for the row",
                    lambda: removal.done() or _waiting_for_a_lock(engine),
                )
                assert not removal.done(), "the removal did not wait for the row"
                # Now the user row, as the other path would ask for it next. Granted at once.
                await other.execute(
                    text("SELECT 1 FROM users WHERE id = :id FOR UPDATE"), {"id": member.id}
                )
        finally:
            if removal is not None:
                await asyncio.gather(removal, return_exceptions=True)

        assert removal is not None
        assert removal.result().status_code == 204
        assert await _is_removed(engine, member.id)


# --- 5. the invitation route: the same lock, the same second look --------------------------------
#
# `POST /agencies/{agencyId}/invitations` took the agency's lock only where a seat is counted (a new
# address and a revived seat) and never asked its caller again. All four of its ways through (those
# two, the re-issue of a pending invitation, and a refusal) now take the row first, once, and the
# caller is asked again under it, as the removal's is.

BRANCHES = ["new_address", "revived_seat", "reissued_invitation"]
PENDING = "pending@invites.example"


def _roles_for(branch: str) -> tuple[UserRole, ...]:
    """The seats after the owner's: an admin who invites, and a member to revive or to refuse."""
    if branch in ("revived_seat", "live_account"):
        return (UserRole.ADMIN, UserRole.MEMBER)
    return (UserRole.ADMIN,)


async def _ready_for(branch: str, agency: World, engine: AsyncEngine) -> str:
    """Put the agency in the state a branch needs, and name the address the admin will invite."""
    if branch == "new_address":
        return "new@invites.example"
    if branch == "live_account":
        return agency.seats[2].email  # an active colleague: inviting them is refused
    if branch == "revived_seat":
        member = agency.seats[2]
        # A removed member's row is kept, soft-deleted, and a new invitation revives it.
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET deleted_at = now() WHERE id = :id"), {"id": member.id}
            )
        return member.email
    invited = await agency.seats[0].invites(agency.agency_id, PENDING)
    assert invited.status_code == 201, invited.text
    return PENDING


async def _live_invitation_ids(engine: AsyncEngine, email: str) -> list[str]:
    async with _db(engine)() as s:
        rows = await s.execute(
            select(Invitation.id).where(
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
        )
        return sorted(rows.scalars())


async def _invite_across_a_change(
    agency: World, engine: AsyncEngine, inviter: Seat, email: str, role: str, change: str
) -> Response:
    """The inviter asks for an invitation while the test holds the agency's lock.

    The request waits for it. `change` is run against the inviter, as a request that committed
    ahead of this one would have, and commits with the lock's release; the answer is what the
    request then decides.
    """
    async with _agency_lock_held(engine, agency.agency_id) as held:
        invitation = asyncio.create_task(inviter.invites(agency.agency_id, email, role))
        try:
            await _until(
                "the invitation to wait for the agency's lock",
                lambda: invitation.done() or _waiting_for_a_lock(engine),
            )
            assert not invitation.done(), "the invitation did not wait for the agency's lock"
            await held.execute(text(change), {"id": inviter.id})
        except BaseException:
            invitation.cancel()
            raise
    return await asyncio.wait_for(invitation, 10)


@pytest.mark.parametrize("branch", BRANCHES)
async def test_an_inviter_removed_while_the_invitation_waits_writes_nothing_and_is_told_so(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, branch: str
) -> None:
    async with _agency(client, engine, settings, *_roles_for(branch)) as agency:
        admin = agency.seats[1]
        email = await _ready_for(branch, agency, engine)
        before = await _seat_rows(engine, except_user=admin.id)

        removed = "UPDATE users SET deleted_at = now() WHERE id = :id"
        response = await _invite_across_a_change(agency, engine, admin, email, "member", removed)

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == NOT_ACTIVE
        # No link comes back (an invitation made with email off answers with one), and no user or
        # invitation row was written, whichever way the request would have gone.
        assert "acceptUrl" not in response.text
        assert await _seat_rows(engine, except_user=admin.id) == before
        # And it is the answer the same request gets now.
        again = await admin.invites(agency.agency_id, email)
        assert (again.status_code, again.json()) == (response.status_code, response.json())


@pytest.mark.parametrize(
    ("roles", "asks", "change", "status", "detail"),
    [
        pytest.param(
            (UserRole.ADMIN,),
            "member",
            "UPDATE users SET role = 'member' WHERE id = :id",
            403,
            "This action requires one of: admin, owner. Your role is member.",
            id="admin_demoted_to_member",
        ),
        pytest.param(
            (UserRole.ADMIN,),
            "member",
            "UPDATE users SET status = 'suspended' WHERE id = :id",
            401,
            NOT_ACTIVE,
            id="admin_suspended",
        ),
        # The role the request began with is not the role it is held to: an owner who is an admin
        # by the time the lock is theirs invites no owner.
        pytest.param(
            (UserRole.OWNER,),
            "owner",
            "UPDATE users SET role = 'admin' WHERE id = :id",
            403,
            "Only an owner can invite someone as an owner.",
            id="owner_demoted_to_admin_asks_for_an_owner",
        ),
    ],
)
async def test_an_inviter_changed_while_the_invitation_waits_is_asked_again(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    roles: tuple[UserRole, ...],
    asks: str,
    change: str,
    status: int,
    detail: str,
) -> None:
    async with _agency(client, engine, settings, *roles) as agency:
        inviter = agency.seats[1]
        email = "new@invites.example"
        before = await _seat_rows(engine, except_user=inviter.id)

        response = await _invite_across_a_change(agency, engine, inviter, email, asks, change)

        assert response.status_code == status, response.text
        assert response.json()["detail"] == detail
        assert "acceptUrl" not in response.text
        assert await _seat_rows(engine, except_user=inviter.id) == before
        again = await inviter.invites(agency.agency_id, email, asks)
        assert (again.status_code, again.json()) == (response.status_code, response.json())


@pytest.mark.parametrize("removed", ["the_inviter", "the_invitee"])
async def test_a_resent_invitation_is_serialised_against_a_removal(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    removal_gate: Gate,
    removed: str,
) -> None:
    """An admin re-sends a pending invitation while the owner removes a seat, the removal first.

    The re-send took no agency lock at all, and ran beside the removal. It now waits for it and
    decides on what the removal left.

    * The seat removed is the admin's own: the re-send is refused as their next request is, and
      writes nothing; the pending seat and its one live link are as they were.
    * The seat removed is the pending invitee's: the re-send finds a removed seat and revives it
      (so a seat is consumed), and the link it leaves is the one live link.
    """
    async with _agency(client, engine, settings, UserRole.ADMIN) as agency:
        owner, admin = agency.seats
        invited = await owner.invites(agency.agency_id, PENDING)
        assert invited.status_code == 201, invited.text
        pending_id, original_id = invited.json()["user"]["id"], invited.json()["invitation"]["id"]
        target = admin.id if removed == "the_inviter" else pending_id
        before = await _seat_rows(engine, except_user=admin.id)
        await _warm_the_pool(engine)

        removal = asyncio.create_task(owner.http.delete(f"{BASE}/users/{target}"))
        resend: asyncio.Task[Response] | None = None
        try:
            # The removal has passed every check and holds the lock, about to write.
            await _until(
                "the removal to hold the lock", lambda: removal_gate.parked >= 1 or removal.done()
            )
            resend = asyncio.create_task(admin.invites(agency.agency_id, PENDING))
            await _until(
                "the re-send to wait for the lock",
                lambda: resend.done() or removal.done() or _waiting_for_a_lock(engine),
            )
        finally:
            removal_gate.let_go()
            # If anything above failed, both requests still end before the test does.
            await asyncio.gather(
                *(task for task in (removal, resend) if task is not None), return_exceptions=True
            )

        assert resend is not None
        assert removal.result().status_code == 204
        response = resend.result()
        if removed == "the_inviter":
            assert response.status_code == 401, response.text
            assert response.json()["detail"] == NOT_ACTIVE
            assert "acceptUrl" not in response.text
            assert await _seat_rows(engine, except_user=admin.id) == before
        else:
            assert response.status_code == 201, response.text
            assert response.json()["seatConsumed"] is True
            assert response.json()["user"]["id"] == pending_id
            live = await _live_invitation_ids(engine, PENDING)
            assert live == [response.json()["invitation"]["id"]] and original_id not in live
            assert not await _is_removed(engine, pending_id)


@pytest.mark.parametrize("branch", [*BRANCHES, "live_account"])
async def test_an_invitation_takes_the_agency_lock_first_and_only_ever_repeats_it(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, branch: str
) -> None:
    """What is asserted: the agency lock is the first lock statement and nothing is read or written
    before it (but the caller's own look, by id); every other agency-lock statement is the same
    `FOR NO KEY UPDATE` on the same row, a repeat in one transaction that waits for nothing (the
    seat check takes it again, so a new address and a revived seat show two and the other ways one);
    and the re-send of a pending invitation reads the invitee `FOR UPDATE` only after it has
    revoked the outstanding invitations (invitation, then user)."""
    async with _agency(client, engine, settings, *_roles_for(branch)) as agency:
        admin = agency.seats[1]
        email = await _ready_for(branch, agency, engine)

        statements: list[str] = []

        def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            statements.append(" ".join(statement.split()))

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            invited = await admin.invites(agency.agency_id, email)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
        if branch == "live_account":
            assert invited.status_code == 409, invited.text  # a refusal takes the lock too
        else:
            assert invited.status_code == 201, invited.text
            # A new address and a revived seat take a seat; a re-send takes none.
            assert invited.json()["seatConsumed"] is (branch != "reissued_invitation")

    agency_locks = [i for i, s in enumerate(statements) if _is_the_agency_lock(s)]
    locks = [i for i, s in enumerate(statements) if _is_a_lock(s)]
    assert agency_locks and locks[0] == agency_locks[0], statements
    # The repeats are the one statement again, and only where a seat is checked.
    assert len({statements[i] for i in agency_locks}) == 1, statements
    assert len(agency_locks) == (2 if branch in ("new_address", "revived_seat") else 1), statements
    first = agency_locks[0]
    before, after = statements[:first], statements[first + 1 :]

    # Before it: only the caller's own look (by id). No write, no look at the address, nothing of
    # an invitation or a reset link.
    assert not [s for s in before if s.startswith(("UPDATE", "INSERT", "DELETE"))], before
    assert not [s for s in before if "users.email = " in s], before
    assert not [s for s in before if "invitations" in s or "password_reset_tokens" in s], before
    # After it: the address is looked up and, unless it was refused, the invitation written.
    assert [s for s in after if "users.email = " in s], after
    written = [s for s in after if s.startswith(("UPDATE", "INSERT", "DELETE"))]
    assert bool(written) is (branch != "live_account"), after
    if branch != "live_account":
        assert [s for s in after if s.startswith("INSERT INTO invitations")], after

    user_locks = [i for i, s in enumerate(statements) if _is_a_lock(s) and "FROM users" in s]
    if branch == "reissued_invitation":
        (user_lock,) = user_locks
        revoke = next(i for i, s in enumerate(statements) if s.startswith("UPDATE invitations"))
        assert revoke < user_lock, statements
    else:
        assert user_locks == [], statements

    if branch == "revived_seat":
        # The invitations are revoked before the old reset links are retired: the order a removal
        # and the Google link take them in, so none of them waits for a row another holds.
        revoke = next(i for i, s in enumerate(statements) if s.startswith("UPDATE invitations"))
        retire = next(
            i for i, s in enumerate(statements) if s.startswith("UPDATE password_reset_tokens")
        )
        assert revoke < retire, statements


async def _user_id(engine: AsyncEngine, email: str) -> str:
    async with _db(engine)() as s:
        return (await s.execute(select(User.id).where(User.email == email))).scalar_one()


@pytest.mark.parametrize(
    ("branch", "held"),
    [
        ("reissued_invitation", "invitation"),
        ("revived_seat", "reset_link"),
        ("revived_seat", "invitation"),
    ],
)
async def test_an_invitation_holds_no_user_row_while_it_waits_for_an_invitation_or_a_link(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, branch: str, held: str
) -> None:
    """The re-send and the revival, against the paths that lock an invitation or a link first.

    An accept or a Google sign-in locks the invitation and then the seat's user row; a reset confirm
    locks the link and then the user row. Here the test holds the invitation (or the link) of the
    seat being invited, and once the invitation is waiting for it asks for the user row, as those
    paths do. If the invitation held the user row while it waited, the two would wait for each
    other: so it writes the invitations and the links first, the user row last.
    """
    async with _agency(client, engine, settings, *_roles_for(branch)) as agency:
        owner, admin = agency.seats[0], agency.seats[1]
        email = await _ready_for(branch, agency, engine)
        invitee_id = await _user_id(engine, email)
        link_id = ids.new_id(ids.PASSWORD_RESET)
        invitation_id = ids.new_id(ids.INVITATION)
        async with _db(engine)() as s:
            if held == "invitation" and branch == "revived_seat":
                # The removal revoked the live ones; a request in flight at the time can leave one.
                s.add(
                    Invitation(
                        id=invitation_id,
                        agency_id=agency.agency_id,
                        invited_by_user_id=owner.id,
                        email=email,
                        role=UserRole.MEMBER,
                        token_digest=token_digest("held-invitation"),
                        expires_at=datetime.now(UTC) + timedelta(days=7),
                    )
                )
            if held == "reset_link":
                s.add(
                    PasswordResetToken(
                        id=link_id,
                        user_id=invitee_id,
                        token_digest=token_digest("held-link"),
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
            await s.commit()
        if held == "invitation" and branch == "reissued_invitation":
            (invitation_id,) = await _live_invitation_ids(engine, email)
        table, row_id = (
            ("invitations", invitation_id)
            if held == "invitation"
            else ("password_reset_tokens", link_id)
        )

        invitation: asyncio.Task[Response] | None = None
        try:
            async with engine.begin() as other:
                # A lock timeout, so a user row the invitation held would fail this test in two
                # seconds and not hang it (Postgres would otherwise settle the wait as a deadlock).
                await other.execute(text("SET LOCAL lock_timeout = '2s'"))
                await other.execute(
                    text(f"SELECT 1 FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id}
                )
                # As an admin, so that the seat's row really changes (a re-send as the role it
                # already holds writes nothing to it, and there would be no row to hold).
                invitation = asyncio.create_task(admin.invites(agency.agency_id, email, "admin"))
                await _until(
                    "the invitation to wait for the row",
                    lambda: invitation.done() or _waiting_for_a_lock(engine),
                )
                assert not invitation.done(), "the invitation did not wait for the row"
                # Now the user row, as the other path would ask for it next. Granted at once.
                await other.execute(
                    text("SELECT 1 FROM users WHERE id = :id FOR UPDATE"), {"id": invitee_id}
                )
        finally:
            if invitation is not None:
                await asyncio.gather(invitation, return_exceptions=True)

        assert invitation is not None
        assert invitation.result().status_code == 201, invitation.result().text
        assert invitation.result().json()["user"]["role"] == "admin"
        assert not await _is_removed(engine, invitee_id)


# --- 6. the lock does not wait for a client being added ------------------------------------------
#
# Every child of the agency (a client, a scan, an alert) takes `FOR KEY SHARE` on the agency's row
# when it is inserted and holds it until it commits. `POST /clients` with `classify: true` flushes
# the client and then crawls and calls the model before it commits: a minute or more. A plain
# `FOR UPDATE` would make a removal or an invitation wait that long, with the removed member's
# sessions live and a pooled connection held. `FOR NO KEY UPDATE` does not conflict with it.


@pytest.mark.parametrize("request_", ["removal", "invitation"])
async def test_a_removal_and_an_invitation_do_not_wait_for_a_client_being_added(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, request_: str
) -> None:
    # Three seats fill the plan: the invitation needs a free one.
    roles = (UserRole.ADMIN, UserRole.MEMBER) if request_ == "removal" else (UserRole.ADMIN,)
    async with _agency(client, engine, settings, *roles) as agency:
        owner, member = agency.seats[0], agency.seats[-1]
        await _warm_the_pool(engine)

        async with _db(engine)() as adding:
            # A client row inserted and not committed: the agency's row is held `FOR KEY SHARE` by
            # the foreign-key check, as it is for the whole of a classifying `POST /clients`.
            adding.add(
                Client(
                    id=ids.new_id(ids.CLIENT),
                    agency_id=agency.agency_id,
                    name="Held",
                    domain="held.example",
                )
            )
            await adding.flush()

            if request_ == "removal":
                task = asyncio.create_task(owner.removes(member))
            else:
                task = asyncio.create_task(owner.invites(agency.agency_id, "new@invites.example"))
            try:
                done, _ = await asyncio.wait({task}, timeout=10)
                assert done, "the request waited for an uncommitted client"
                # Answered while the client is still uncommitted.
                assert task.result().status_code == (204 if request_ == "removal" else 201)
            finally:
                await adding.rollback()
                await asyncio.gather(task, return_exceptions=True)

        if request_ == "removal":
            assert await _is_removed(engine, member.id)


# --- 7. the Google link to a password account, against a removal ---------------------------------
#
# Linking Google to a password account retired the user's reset links and then revoked the
# invitations they sent. The removal does the invitations and then the links, so each could hold
# the row the other waited for. The link now takes them in the removal's order.


async def test_linking_google_and_removing_the_seat_at_once_do_not_deadlock(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _agency(client, engine, settings, UserRole.ADMIN) as agency:
        owner, admin = agency.seats
        # The admin has a pending invitation out and an unused reset link: the two kinds of row
        # both paths write.
        invited = await admin.invites(agency.agency_id, PENDING)
        assert invited.status_code == 201, invited.text
        async with _db(engine)() as s:
            s.add(
                PasswordResetToken(
                    id=ids.new_id(ids.PASSWORD_RESET),
                    user_id=admin.id,
                    token_digest=token_digest("unused-link"),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await s.commit()

        # The Google link runs on a connection of its own, in an engine of its own (NullPool), and
        # is parked just before it revokes the invitations. With the links written first, it holds
        # the admin's link by then; with the invitations first, it holds nothing yet.
        own_engine = create_async_engine(settings.database_url, poolclass=NullPool)
        between = Gate()
        real_revoke = google_oauth.revoke_pending_invitations_sent_by

        async def park_then_revoke(session: AsyncSession, user_id: str) -> int:
            await between.wait()
            return await real_revoke(session, user_id)

        monkeypatch.setattr(google_oauth, "revoke_pending_invitations_sent_by", park_then_revoke)
        written: list[str] = []

        def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            if statement.startswith("UPDATE"):
                written.append(statement.split()[1])

        event.listen(own_engine.sync_engine, "before_cursor_execute", record)

        async def link_google() -> bool:
            async with _db(own_engine)() as s:
                resolved = await google_oauth.resolve_identity(
                    s,
                    google_oauth.GoogleIdentity(
                        sub="google-sub-for-the-admin",
                        email=admin.email,
                        email_verified=True,
                        name="Seat 1",
                        nonce=None,
                    ),
                )
                await s.commit()
                return resolved is not None and resolved.linked

        linking = asyncio.create_task(link_google())
        removal: asyncio.Task[Response] | None = None
        try:
            await _until(
                "the link to reach its invitation revoke",
                lambda: between.parked >= 1 or linking.done(),
            )
            removal = asyncio.create_task(owner.removes(admin))
            # Either the removal finishes, or it waits for a row the link holds.
            # Then the link goes on.
            await _until(
                "the removal to finish or to wait for a row",
                lambda: removal.done() or linking.done() or _waiting_for_a_lock(engine),
            )
        finally:
            between.let_go()
            outcomes = await asyncio.gather(
                *(task for task in (linking, removal) if task is not None), return_exceptions=True
            )
            await own_engine.dispose()

        assert removal is not None
        # A deadlock ends one of the two in an error; neither ends in one.
        assert outcomes[0] is True, outcomes
        assert not isinstance(outcomes[1], BaseException), outcomes
        assert removal.result().status_code == 204, removal.result().text
        assert await _is_removed(engine, admin.id)
        # And the order the link wrote in is the removal's: the invitations, then the reset links.
        assert written.index("invitations") < written.index("password_reset_tokens"), written


# --- 8. a session revoked while the request waits ------------------------------------------------
#
# `reconfirm_principal` re-reads the seat, and now the session store too. A caller whose session was
# revoked while the request waited (sign out everywhere, a password change, a Google link replacing
# an earlier password holder) is refused as the next request would be. With email off, an invitation
# would otherwise hand an `acceptUrl` to the holder of a revoked session.

SESSION_GONE = "Session is expired or invalid."


@pytest.mark.parametrize("request_", ["removal", "invitation"])
async def test_a_session_revoked_while_the_request_waits_for_the_lock_is_refused(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, request_: str
) -> None:
    # An invitation needs a free seat (three fill the plan), or without the session check it would
    # answer 409 seat-limit-reached and not hand a link out.
    roles = (UserRole.ADMIN, UserRole.MEMBER) if request_ == "removal" else (UserRole.ADMIN,)
    async with _agency(client, engine, settings, *roles) as agency:
        admin, member = agency.seats[1], agency.seats[-1]
        before = await _seat_rows(engine, except_user=admin.id)
        app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

        async with _agency_lock_held(engine, agency.agency_id):
            if request_ == "removal":
                task = asyncio.create_task(admin.removes(member))
            else:
                task = asyncio.create_task(admin.invites(agency.agency_id, "new@invites.example"))
            try:
                await _until(
                    "the request to wait for the agency's lock",
                    lambda: task.done() or _waiting_for_a_lock(engine),
                )
                assert not task.done(), "the request did not wait for the agency's lock"
                # Sign out everywhere, from another sign-in of the same person.
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Origin": client.headers["Origin"]},
                ) as elsewhere:
                    login = await elsewhere.post(
                        f"{BASE}/auth/login", json={"email": admin.email, "password": PASSWORD}
                    )
                    assert login.status_code == 200, login.text
                    out = await elsewhere.post(f"{BASE}/auth/logout-all")
                    assert out.status_code == 204, out.text
            except BaseException:
                task.cancel()
                raise
        response = await asyncio.wait_for(task, 10)

        # The seat is untouched, active and still an admin: it is the session that is refused.
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == SESSION_GONE
        assert "acceptUrl" not in response.text
        assert await _seat_rows(engine, except_user=admin.id) == before
        assert not await _is_removed(engine, member.id)


@pytest.mark.parametrize("request_", ["removal", "invitation"])
async def test_a_caller_whose_seat_is_removed_and_sessions_revoked_hears_no_longer_active(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, request_: str
) -> None:
    """A removal commits and only then revokes the removed seat's sessions.

    A request that gets the lock at that commit may find the session still there or already
    gone. This is the second case, made certain: the seat is removed and the sessions are gone
    before the lock is released. The seat is asked about first, so the answer is the same as
    in the first case, never "Session is expired or invalid.".
    """
    roles = (UserRole.ADMIN, UserRole.MEMBER) if request_ == "removal" else (UserRole.ADMIN,)
    async with _agency(client, engine, settings, *roles) as agency:
        admin, member = agency.seats[1], agency.seats[-1]
        app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

        async with _agency_lock_held(engine, agency.agency_id) as held:
            if request_ == "removal":
                task = asyncio.create_task(admin.removes(member))
            else:
                task = asyncio.create_task(admin.invites(agency.agency_id, "new@invites.example"))
            try:
                await _until(
                    "the request to wait for the agency's lock",
                    lambda: task.done() or _waiting_for_a_lock(engine),
                )
                assert not task.done(), "the request did not wait for the agency's lock"
                # The sessions go first (sign out everywhere)...
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Origin": client.headers["Origin"]},
                ) as elsewhere:
                    login = await elsewhere.post(
                        f"{BASE}/auth/login", json={"email": admin.email, "password": PASSWORD}
                    )
                    assert login.status_code == 200, login.text
                    out = await elsewhere.post(f"{BASE}/auth/logout-all")
                    assert out.status_code == 204, out.text
                # ...and the seat's removal commits with the lock's release.
                await held.execute(
                    text("UPDATE users SET deleted_at = now() WHERE id = :id"), {"id": admin.id}
                )
            except BaseException:
                task.cancel()
                raise
        response = await asyncio.wait_for(task, 10)

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == NOT_ACTIVE
        assert "acceptUrl" not in response.text
        if request_ == "removal":
            assert not await _is_removed(engine, member.id)


# --- 9. a re-sent invitation the invitee accepts while it waits -----------------------------------
#
# The re-send decides on an unlocked read that the seat is still pending. An accept locks the
# invitation and then the seat's row, so the re-send, which revokes the invitation first, waits for
# the accept and must then read the seat again, locked: it was pending, and is not.


async def test_a_resent_invitation_the_invitee_accepts_meanwhile_is_refused_as_for_an_active_one(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    async with _agency(client, engine, settings, UserRole.ADMIN) as agency:
        owner, admin = agency.seats
        invited = await owner.invites(agency.agency_id, PENDING)
        assert invited.status_code == 201, invited.text
        pending_id = invited.json()["user"]["id"]
        (invitation_id,) = await _live_invitation_ids(engine, PENDING)
        await _warm_the_pool(engine)

        resend: asyncio.Task[Response] | None = None
        try:
            async with engine.begin() as accepting:
                # The accept has the invitation, as it takes it first.
                await accepting.execute(
                    text("SELECT 1 FROM invitations WHERE id = :id FOR UPDATE"),
                    {"id": invitation_id},
                )
                resend = asyncio.create_task(admin.invites(agency.agency_id, PENDING, "admin"))
                await _until(
                    "the re-send to wait for the invitation",
                    lambda: resend.done() or _waiting_for_a_lock(engine),
                )
                assert not resend.done(), "the re-send did not wait for the invitation"
                # The accept commits: the invitation is used and the seat is active.
                await accepting.execute(
                    text("UPDATE invitations SET accepted_at = now() WHERE id = :id"),
                    {"id": invitation_id},
                )
                await accepting.execute(
                    text(
                        "UPDATE users SET status = 'active', password_hash = 'accepted' "
                        "WHERE id = :id"
                    ),
                    {"id": pending_id},
                )
        finally:
            if resend is not None:
                await asyncio.gather(resend, return_exceptions=True)

        assert resend is not None
        response = resend.result()
        assert response.status_code == 409, response.text
        assert response.json()["type"] == "/problems/email-already-registered"
        assert "acceptUrl" not in response.text
        # The accepted seat is as the accept left it: still a member, no link left behind.
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT role, status, google_sub FROM users WHERE id = :id"),
                    {"id": pending_id},
                )
            ).one()
        assert tuple(row) == ("member", "active", None), tuple(row)
        assert await _live_invitation_ids(engine, PENDING) == []

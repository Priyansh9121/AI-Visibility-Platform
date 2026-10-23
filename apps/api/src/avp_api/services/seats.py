"""Seat accounting.

product-spec.md §5.3 nests `Agency -> User (seats)`, and Epic 1 requires
seat-based auth. The whole of seat enforcement is this module.

**Why the agency row is locked rather than simply counted.** The naive check —
`SELECT count(*) ... ; if count < limit: INSERT` — is a time-of-check /
time-of-use race. Two invitations submitted simultaneously both read
`count = 2` against a limit of 3, both pass, and the agency ends up with 4
seats it is not paying for. Taking `SELECT ... FOR NO KEY UPDATE` on the agency row
first serialises every seat mutation for that agency, so the count cannot move
between the check and the insert.

A database trigger would also close the race. This is done in the service layer
instead because seat limits are commercial policy, not a data invariant: the
rules will grow (grace seats, trials, per-role limits), and policy that lives
in a trigger is invisible to the people who change it.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import SeatLimitReached
from ..models import Agency, User, UserStatus


async def count_occupied_seats(session: AsyncSession, agency_id: str) -> int:
    """Seats currently consumed.

    Counts ACTIVE and INVITED users. Pending invitations occupy a seat on
    purpose: otherwise an agency could issue unlimited invitations and
    overshoot its plan the instant they were accepted.
    """
    stmt = (
        select(func.count())
        .select_from(User)
        .where(
            User.agency_id == agency_id,
            User.deleted_at.is_(None),
            User.status.in_([UserStatus.ACTIVE, UserStatus.INVITED]),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def lock_agency(session: AsyncSession, agency_id: str) -> Agency | None:
    """Take a row lock on the agency, serialising seat changes for it.

    `FOR NO KEY UPDATE`, not `FOR UPDATE`. Every child of `agencies` (a client, a scan, an alert)
    takes `FOR KEY SHARE` on its agency row when it is inserted, and holds it until its transaction
    commits. `FOR UPDATE` conflicts with that lock, so a removal or an invitation would wait,
    without limit, behind a member's `POST /clients` that crawls and calls the model before it
    commits (a minute or more), with the removed member's sessions live and a pooled connection
    held. `FOR NO KEY UPDATE` does not conflict with it, and still conflicts with itself: other
    removals, invitations, checkouts (`services/billing.py` takes the same lock), scan starts and
    webhooks that lock the row still take turns. Nothing here changes a key of the row.

    The row is read again in place (`populate_existing`), so `seat_limit` is the locked read's, not
    one the session loaded earlier. Taking the lock again in the same transaction waits for nothing.
    """
    stmt = (
        select(Agency)
        .where(Agency.id == agency_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def assert_seat_available(session: AsyncSession, agency_id: str) -> None:
    """Raise unless one more seat can be claimed. Caller must be in a transaction.

    Call this *inside* the same transaction as the INSERT that consumes the
    seat; the lock is only meaningful for as long as that transaction is open.

    It takes the agency's lock itself. A caller that has locked the agency already (the invite
    route does, first) loses nothing: the second `lock_agency` in one transaction is a no-op that
    returns the row, which is why there is no way to say "already locked" and so no way to skip it.
    """
    agency = await lock_agency(session, agency_id)
    if agency is None:
        raise SeatLimitReached(detail="Agency not found.")

    occupied = await count_occupied_seats(session, agency_id)
    if occupied >= agency.seat_limit:
        raise SeatLimitReached(
            detail=(
                f"This agency is using {occupied} of {agency.seat_limit} seats. "
                f"Remove a seat or upgrade the plan before adding another user."
            ),
            seatsUsed=occupied,
            seatLimit=agency.seat_limit,
        )


async def seat_usage(session: AsyncSession, agency_id: str) -> tuple[int, int]:
    """(occupied, limit) for display."""
    agency = (
        await session.execute(select(Agency).where(Agency.id == agency_id))
    ).scalar_one_or_none()
    if agency is None:
        return (0, 0)
    return (await count_occupied_seats(session, agency_id), agency.seat_limit)

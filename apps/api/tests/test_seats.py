"""Seat accounting — the commercial invariant behind seat-based billing."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from avp_api.errors import SeatLimitReached
from avp_api.models import UserRole, UserStatus
from avp_api.services import auth as auth_service
from avp_api.services import seats as seat_service


async def _agency_with_owner(session: AsyncSession, settings) -> str:  # noqa: ANN001
    agency, _ = await auth_service.sign_up_agency(
        session,
        agency_name="Seat Test Agency",
        full_name="Owner Person",
        email="owner@seattest.example",
        password="correct-horse-battery-staple",
        settings=settings,
    )
    await session.commit()
    return agency.id


async def test_owner_occupies_the_first_seat(session: AsyncSession, settings) -> None:  # noqa: ANN001
    agency_id = await _agency_with_owner(session, settings)
    assert await seat_service.count_occupied_seats(session, agency_id) == 1
    assert await seat_service.seat_usage(session, agency_id) == (1, 3)


async def test_seats_fill_to_the_limit_then_refuse(
    session: AsyncSession, settings
) -> None:  # noqa: ANN001
    agency_id = await _agency_with_owner(session, settings)

    for i in range(2):  # seats 2 and 3 of 3
        await auth_service.create_user_in_agency(
            session,
            agency_id=agency_id,
            email=f"member{i}@seattest.example",
            full_name=f"Member {i}",
            password="correct-horse-battery-staple",
            settings=settings,
        )
        await session.commit()

    assert await seat_service.count_occupied_seats(session, agency_id) == 3

    with pytest.raises(SeatLimitReached) as excinfo:
        await auth_service.create_user_in_agency(
            session,
            agency_id=agency_id,
            email="one-too-many@seattest.example",
            full_name="Overflow",
            password="correct-horse-battery-staple",
            settings=settings,
        )
    await session.rollback()

    # The error carries the numbers, so the UI can say what to do about it.
    assert excinfo.value.extra["seatsUsed"] == 3
    assert excinfo.value.extra["seatLimit"] == 3


async def test_invited_users_occupy_seats(session: AsyncSession, settings) -> None:  # noqa: ANN001
    """Otherwise an agency could issue unlimited invitations and overshoot."""
    from avp_api import ids
    from avp_api.models import User

    agency_id = await _agency_with_owner(session, settings)
    session.add(
        User(
            id=ids.new_id(ids.USER),
            agency_id=agency_id,
            email="invited@seattest.example",
            password_hash=None,
            full_name="Invited Person",
            role=UserRole.MEMBER,
            status=UserStatus.INVITED,
        )
    )
    await session.commit()
    assert await seat_service.count_occupied_seats(session, agency_id) == 2


async def test_soft_deleted_users_release_their_seat(
    session: AsyncSession, settings
) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    from sqlalchemy import select

    from avp_api.models import User

    agency_id = await _agency_with_owner(session, settings)
    user = await auth_service.create_user_in_agency(
        session,
        agency_id=agency_id,
        email="leaver@seattest.example",
        full_name="Leaver",
        password="correct-horse-battery-staple",
        settings=settings,
    )
    await session.commit()
    assert await seat_service.count_occupied_seats(session, agency_id) == 2

    fetched = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
    fetched.deleted_at = datetime.now(UTC)
    await session.commit()

    assert await seat_service.count_occupied_seats(session, agency_id) == 1


async def test_concurrent_signups_cannot_exceed_the_limit(
    engine, settings
) -> None:  # noqa: ANN001
    """The race the row lock exists to prevent.

    Without `SELECT ... FOR UPDATE` on the agency row, two concurrent requests
    both read `occupied = 1` against a limit of 3, both pass the check, and the
    agency ends up with more seats than it pays for. This test runs four
    genuinely concurrent transactions against a 3-seat agency and asserts that
    exactly two succeed.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with factory() as setup:
        agency_id = await _agency_with_owner(setup, settings)  # 1 of 3 used

    async def try_add(index: int) -> str:
        async with factory() as s:
            try:
                await auth_service.create_user_in_agency(
                    s,
                    agency_id=agency_id,
                    email=f"racer{index}@seattest.example",
                    full_name=f"Racer {index}",
                    password="correct-horse-battery-staple",
                    settings=settings,
                )
                await s.commit()
                return "created"
            except SeatLimitReached:
                await s.rollback()
                return "refused"

    results = await asyncio.gather(*(try_add(i) for i in range(4)))

    assert results.count("created") == 2, f"expected exactly 2 seats granted, got {results}"
    assert results.count("refused") == 2

    async with factory() as check:
        assert await seat_service.count_occupied_seats(check, agency_id) == 3

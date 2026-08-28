"""Seat invitations — Epic 9.14.

This module is the HTTP surface's missing half, not new domain logic. Every
piece it stands on already existed and was already tested: the `invitations`
table has been in the schema since the initial migration, `services/seats.py`
owns the transactional seat check, and `SessionStore.revoke_all_for_user` has
been the answer to "a seat was removed" since Epic 1. What was never built was
anything that called them.

THE TOKEN FOLLOWS THE RESET RULE, NOT THE SHARE RULE
----------------------------------------------------
Only the SHA-256 digest is stored, exactly as `PasswordResetToken` does and for
the same reason its docstring gives: an invitation link is minted, put in one
email, and never shown back to anybody. Nobody needs it read back, so a dump of
this table must hand an attacker nothing. `Scan.share_token` is the one
clear-text token in this system and it is clear-text because an operator has to
be able to copy that URL again — a requirement this credential does not have.

WHY A RE-INVITE IS ALLOWED, AND WHAT IT COSTS
---------------------------------------------
An address that was invited and never signed up already holds a `User` row with
`status = INVITED`, and `count_occupied_seats` counts it. So the seat is already
paid for, and the only thing a second invitation needs to do is put a working
link in front of the person again — the first may have expired, gone to spam, or
been sent to a colleague who has since left.

Refusing that (which `email_exists` would have done, with
`409 email-already-registered`) leaves an agency with a seat consumed, nobody in
it, and no way out except removing the seat and starting over. So a re-invite
re-mints instead: outstanding invitations for the address are revoked first, so
the new link retires the old one — the same rule `password_reset.request_reset`
applies for the same reason. **No second seat is consumed**, because the row
that occupies it is the row being re-invited.

WHY A REMOVED SEAT'S ROW IS REVIVED RATHER THAN RE-CREATED
-----------------------------------------------------------
`uq_users_email` is unconditional — it does not exclude soft-deleted rows — so
after a seat is removed, that address cannot be inserted again. Left alone, that
turns "remove a seat by mistake" into a permanent ban on the person's email.

Re-inviting a soft-deleted user therefore clears `deleted_at` and puts the row
back to `INVITED` with a null password. This is also the better answer on the
merits: `scans.requested_by_user_id` and `invitations.invited_by_user_id` point
at that id, and a new row would orphan the history while the old one kept it.
Reviving DOES consume a seat, because a soft-deleted row does not occupy one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings
from ..errors import EmailAlreadyRegistered
from ..models import Invitation, User, UserRole, UserStatus
from ..security import hash_password, new_session_token, token_digest
from . import seats
from .auth import normalise_email

logger = structlog.get_logger(__name__)


class InvitationTokenError(Exception):
    """The token does not resolve to a live, unaccepted, unexpired invitation.

    **One class for every reason**, carrying no detail — unknown, expired,
    already accepted, revoked, and pointing at a seat that has since been
    removed all raise this. The router has exactly one thing to say and no
    branch that could tell a holder which kind of wrong their guess was, which
    is the discipline `password_reset.ResetTokenError` states at length and
    Epic 9.8's public report route applies to a bad share token.
    """


class MintedInvitation(NamedTuple):
    """What the router needs to build a URL and answer accurately."""

    user: User
    invitation: Invitation
    # The clear-text token. Goes in exactly one email and is never persisted,
    # returned in a response body, or logged.
    token: str
    # True when this invitation consumed a seat. False when it merely re-issued
    # a link for a seat the invited address already held.
    seat_consumed: bool


async def _user_by_email(session: AsyncSession, email: str) -> User | None:
    """Look up by address across every agency, INCLUDING soft-deleted rows.

    Deliberately not `auth.email_exists`, which filters nothing and answers a
    boolean. Both the cross-tenant refusal and the revive path need the row
    itself, and the revive path specifically needs to see rows `deleted_at` has
    hidden — the unique constraint on email can see them, so this must too.
    """
    return (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()


async def _revoke_outstanding(session: AsyncSession, *, agency_id: str, email: str) -> None:
    """Retire every live invitation for this address in this agency.

    Called before minting a new one and again when a seat is removed. A link
    that still works after it has been superseded — or after the seat it leads
    to is gone — is a second key to a door somebody has already changed.
    """
    await session.execute(
        update(Invitation)
        .where(
            Invitation.agency_id == agency_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def invite_seat(
    session: AsyncSession,
    *,
    agency_id: str,
    invited_by_user_id: str,
    email: str,
    role: UserRole,
    settings: Settings,
) -> MintedInvitation:
    """Invite an address to a seat, or re-issue the link for one it already holds.

    Raises `EmailAlreadyRegistered` when the address belongs to a live account
    — in this agency or another — and `SeatLimitReached` when the agency is
    full. The seat check runs inside the caller's transaction, which is the
    only place it means anything (see `services/seats.py`).
    """
    email = normalise_email(email)
    existing = await _user_by_email(session, email)

    if existing is None:
        # A genuinely new address. This is the only path that inserts a row,
        # and the seat check must precede the insert and share its transaction.
        await seats.assert_seat_available(session, agency_id)
        user = User(
            id=ids.new_id(ids.USER),
            agency_id=agency_id,
            email=email,
            # Null until acceptance. The `active_user_has_password` CHECK
            # constraint permits exactly this pairing and no other.
            password_hash=None,
            # The invitee has not told us their name yet. The address is what
            # we know, so it is what is shown until they say otherwise —
            # inventing a display name from the local part would be a guess
            # presented as a fact on a seat list.
            full_name=email,
            role=role,
            status=UserStatus.INVITED,
        )
        session.add(user)
        seat_consumed = True

    elif existing.agency_id != agency_id:
        # Email is globally unique by design (see the User model). Refused
        # explicitly rather than vaguely, for the reason `sign_up_agency`
        # gives: an attacker learns the same fact by trying to register, and a
        # coy error here only makes a real operator retry.
        raise EmailAlreadyRegistered(
            detail="That email address is already associated with an account."
        )

    elif existing.deleted_at is not None:
        # A seat that was removed. Revive rather than insert — see the module
        # docstring. The seat was released on removal, so it must be re-taken.
        await seats.assert_seat_available(session, agency_id)
        existing.deleted_at = None
        existing.status = UserStatus.INVITED
        # Required by `active_user_has_password`, and right on the merits: a
        # re-invited person sets a new password rather than resuming an old one.
        existing.password_hash = None
        existing.role = role
        user = existing
        seat_consumed = True

    elif existing.status is UserStatus.INVITED:
        # The seat is already occupied by this very row. Re-issue only.
        existing.role = role
        user = existing
        seat_consumed = False

    else:
        # ACTIVE or SUSPENDED. Inviting an active colleague is a no-op the
        # operator should see as one; un-suspending is a different action with
        # different authority, and quietly doing it from an invite form would
        # be a privilege change disguised as an invitation.
        raise EmailAlreadyRegistered(
            detail="That email address is already associated with an account."
        )

    await session.flush()

    await _revoke_outstanding(session, agency_id=agency_id, email=email)

    # Same generator and same 256 bits as a session token. This is a bearer
    # credential that grants a seat; it has no business being weaker.
    token = new_session_token()
    invitation = Invitation(
        id=ids.new_id(ids.INVITATION),
        agency_id=agency_id,
        invited_by_user_id=invited_by_user_id,
        email=email,
        role=role,
        token_digest=token_digest(token),
        expires_at=datetime.now(UTC)
        + timedelta(seconds=settings.invitation_ttl_seconds),
    )
    session.add(invitation)
    await session.flush()

    # The token is never logged. It is the credential.
    logger.info(
        "invitation.minted",
        agency_id=agency_id,
        user_id=user.id,
        seat_consumed=seat_consumed,
    )
    return MintedInvitation(
        user=user, invitation=invitation, token=token, seat_consumed=seat_consumed
    )


async def accept_invitation(
    session: AsyncSession,
    *,
    token: str,
    full_name: str,
    password: str,
    settings: Settings,
) -> User:
    """Redeem an invitation and turn the seat into a working account.

    Raises `InvitationTokenError` for every failure. Single use is enforced
    here and not merely documented: `accepted_at` is stamped in the same
    transaction as the password write, so a link cannot open the door twice
    even if it is clicked twice.
    """
    invitation = (
        await session.execute(
            select(Invitation).where(Invitation.token_digest == token_digest(token))
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        # Deliberately one branch and one exception.
        raise InvitationTokenError

    user = (
        await session.execute(
            select(User).where(
                User.agency_id == invitation.agency_id,
                User.email == invitation.email,
                User.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    # A live token whose seat has since been removed is refused identically to
    # a forged one. The seat is gone; there is nothing to accept, and saying
    # which of the two happened tells a stranger about the agency's roster.
    if user is None or user.status is not UserStatus.INVITED:
        raise InvitationTokenError

    user.password_hash = hash_password(password, settings)
    user.status = UserStatus.ACTIVE
    name = full_name.strip()
    if name:
        user.full_name = name
    invitation.accepted_at = now
    await session.flush()

    logger.info("invitation.accepted", agency_id=user.agency_id, user_id=user.id)
    return user


async def pending_invitations(session: AsyncSession, agency_id: str) -> list[Invitation]:
    """Invitations that are still live, newest first.

    Expired ones are excluded rather than shown greyed out: the seat they hold
    is still consumed (the `INVITED` user row is what counts, not this row), so
    the useful surface is the seat list, and listing a dead link beside it
    invites someone to wait for it to be used.
    """
    rows = (
        (
            await session.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                    Invitation.expires_at > datetime.now(UTC),
                )
            )
        )
        .scalars()
        .all()
    )
    # ULIDs are creation-ordered, so id DESC is newest-first and is a total
    # order — two reads of identical data never disagree.
    return sorted(rows, key=lambda r: r.id, reverse=True)


async def seat_holders(session: AsyncSession, agency_id: str) -> list[User]:
    """Everyone occupying a seat, owners first.

    Ordered by role then id rather than by name: the question a seat list
    answers is "who can do what here", and a list that opens with the owners
    answers it without being read in full.
    """
    rows = (
        (
            await session.execute(
                select(User).where(
                    User.agency_id == agency_id,
                    User.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    rank = {UserRole.OWNER: 0, UserRole.ADMIN: 1, UserRole.MEMBER: 2}
    return sorted(rows, key=lambda u: (rank.get(u.role, 3), u.id))


async def count_active_owners(session: AsyncSession, agency_id: str) -> int:
    """Live owners. The last one cannot be removed — see `UserRole`."""
    rows = await seat_holders(session, agency_id)
    return sum(
        1 for u in rows if u.role is UserRole.OWNER and u.status is UserStatus.ACTIVE
    )


async def release_seat(session: AsyncSession, *, agency_id: str, user: User) -> None:
    """Soft-delete a seat holder and retire any invitation pointing at them.

    **Soft, not hard.** `deleted_at` releases the seat (`count_occupied_seats`
    filters on it) while leaving `scans.requested_by_user_id` and every other
    reference intact, which is the rule `models/base.py` states for the whole
    schema: an agency removing someone must not cascade-destroy the history
    their historical reports are built from.

    Session revocation is NOT done here. It needs the Redis-backed
    `SessionStore`, which is a request-scoped dependency rather than something
    this module should reach for — so the router does it, immediately after
    committing this.
    """
    user.deleted_at = datetime.now(UTC)
    await _revoke_outstanding(session, agency_id=agency_id, email=user.email)
    await session.flush()
    logger.info("seat.released", agency_id=agency_id, user_id=user.id)

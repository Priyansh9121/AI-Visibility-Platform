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

A REVIVED ROW IS THE SAME USER ID THE OLD SESSIONS NAME (A3 part 3, the security review's M1)
-----------------------------------------------------------------------------------------------
While the row is removed, `current_principal` refuses its sessions because `deleted_at` is set. Once
it is revived that is no longer true, and a session the removal's Redis revoke missed (the revoke
runs after the commit and can fail, or find a list Redis evicted) would answer again as soon as the
new invitation is accepted. So the credential version covers both ends: `release_seat` sets
`users.sessions_valid_after` with the removal, and the revive branch of `invite_seat` sets it again
with the revival, which also ends a session that was signed in after the removal's cutoff. Both
write it as part of the one UPDATE of the user row (`session_cutoff_value`), after the invitations
and the reset links, so the row is still written last and no lock order changes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import structlog
from redis.asyncio import Redis
from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings
from ..errors import EmailAlreadyRegistered, PermissionDenied
from ..models import (
    Client,
    Invitation,
    PasswordResetToken,
    PromptRun,
    Scan,
    User,
    UserRole,
    UserStatus,
)
from ..security import hash_password_async, new_session_token, token_digest
from . import breached_passwords, rate_limit, seats
from .auth import normalise_email, session_cutoff_value
from .password_reset import retire_live_tokens

logger = structlog.get_logger(__name__)

# How high a seat sits, 0 the highest. The one map for "who is listed first" (`seat_holders`) and
# "who may hand a role to whom" (`outranks`), so the two cannot disagree. A role added to `UserRole`
# is placed here: until it is, `outranks` puts it above every other, so nobody can grant it, and
# `tests/test_seat_endpoints.py` fails until it is placed.
ROLE_RANK: dict[UserRole, int] = {UserRole.OWNER: 0, UserRole.ADMIN: 1, UserRole.MEMBER: 2}

# The sentences of a refusal (hardening F1, and the rank rule that generalises it). The owner's keep
# the words they have always had; any other role above the inviter's gets the general one.
OWNER_INVITE_REFUSED = "Only an owner can invite someone as an owner."
OWNER_REISSUE_REFUSED = "Only an owner can change an owner's invitation."
HIGHER_INVITE_REFUSED = "You cannot invite someone to a role above your own."
HIGHER_REISSUE_REFUSED = "You cannot change an invitation for a role above your own."
# And of a refused removal (checklist 3.124): the owner's sentence is the one the seat removal has
# always had; the general one is for a role above the remover's that is not the owner.
OWNER_REMOVE_REFUSED = "Only an owner can remove another owner's seat."
HIGHER_REMOVE_REFUSED = "You cannot remove someone whose role is above your own."


def outranks(role: UserRole, other: UserRole) -> bool:
    """True when `role` sits above `other`.

    A role with no place in `ROLE_RANK` counts as above everything when it is `role` and below
    everything when it is `other`: a role nobody has ranked can be granted by nobody and grants
    nothing.
    """
    return ROLE_RANK.get(role, -1) < ROLE_RANK.get(other, len(ROLE_RANK))


def refuse_removing_a_role_above(remover_role: UserRole, target_role: UserRole) -> None:
    """Refuse (403) when the seat to remove is held by a role above the remover's own.

    Reads and writes nothing. The same rank as the invitations use (`outranks`): a seat that the
    owner granted must not be usable to remove the owner who granted it, and a role ranked above
    admin later is removable by nobody below it. A seat of the same rank may remove another of it.
    """
    if not outranks(target_role, remover_role):
        return
    raise PermissionDenied(
        detail=OWNER_REMOVE_REFUSED if target_role is UserRole.OWNER else HIGHER_REMOVE_REFUSED
    )


def _refuse_a_role_above(
    role: UserRole,
    inviter_role: UserRole,
    *,
    agency_id: str,
    invited_by_user_id: str,
    reissue: bool,
) -> None:
    """Refuse (403) when `role` sits above the inviter's own. Reads and writes nothing.

    `reissue` is the second question: not the role asked for, but the role the pending invitation
    already holds. The owner's refusals keep their sentences and log events (`invitation.owner_*`);
    another role is logged as `invitation.role_*` with its code. Ids and codes only: no address.
    """
    if not outranks(role, inviter_role):
        return
    if role is UserRole.OWNER:
        logger.info(
            "invitation.owner_reissue_refused" if reissue else "invitation.owner_refused",
            agency_id=agency_id,
            invited_by_user_id=invited_by_user_id,
        )
        raise PermissionDenied(
            detail=OWNER_REISSUE_REFUSED if reissue else OWNER_INVITE_REFUSED
        )
    logger.info(
        "invitation.role_reissue_refused" if reissue else "invitation.role_refused",
        agency_id=agency_id,
        invited_by_user_id=invited_by_user_id,
        role=role.value,
    )
    raise PermissionDenied(detail=HIGHER_REISSUE_REFUSED if reissue else HIGHER_INVITE_REFUSED)


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
    inviter_role: UserRole,
    email: str,
    role: UserRole,
    settings: Settings,
) -> MintedInvitation:
    """Invite an address to a seat, or re-issue the link for one it already holds.

    Raises `EmailAlreadyRegistered` when the address belongs to a live account
    — in this agency or another — and `SeatLimitReached` when the agency is
    full. The seat check runs inside the caller's transaction, which is the
    only place it means anything (see `services/seats.py`).

    The seat checks take the agency's lock themselves. The invite route has taken it first, for
    every path below, and asked its own caller again under it; the second lock in one transaction
    waits for nothing.

    Raises `PermissionDenied` when `role` sits above the inviter's own (`outranks`, by
    `ROLE_RANK`): an admin may manage seats, but must not be able to mint the one role that can
    remove the real owner, and a role ranked above admin later can be granted by nobody below it.
    Checked first, before any read or write, so every path below (new row, revived row, re-issued
    row) is covered and a refused call writes nothing.
    """
    _refuse_a_role_above(
        role,
        inviter_role,
        agency_id=agency_id,
        invited_by_user_id=invited_by_user_id,
        reissue=False,
    )

    email = normalise_email(email)
    existing = await _user_by_email(session, email)
    reissued: User | None = None
    revived = False

    if existing is None:
        # A genuinely new address. This is the only path that inserts a row,
        # and the seat check must precede the insert and share its transaction.
        await seats.assert_seat_available(session, agency_id)
        user = User(
            id=ids.new_id(ids.USER),
            agency_id=agency_id,
            email=email,
            # Null until acceptance. The `active_user_has_credential` CHECK
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
        # explicitly, so the person inviting knows why nothing was sent.
        raise EmailAlreadyRegistered(
            detail="That email address is already associated with an account."
        )

    elif existing.deleted_at is not None:
        # A seat that was removed. Revive rather than insert — see the module
        # docstring. The seat was released on removal, so it must be re-taken.
        await seats.assert_seat_available(session, agency_id)
        existing.deleted_at = None
        existing.status = UserStatus.INVITED
        # Required by `active_user_has_credential`, and right on the merits: a
        # re-invited person sets a new password rather than resuming an old one.
        # The Google link goes for the same reason (Epic 20): the seat is being
        # re-issued, not resumed, and the new holder proves the address again.
        existing.password_hash = None
        existing.google_sub = None
        # The "Check who has access" review (A6 part 4, 3.123) was the old holder's: the person who
        # accepts this seat is a new one, and starts with none.
        existing.google_linked_at = None
        existing.access_review_done_at = None
        # `release_seat` retired the links that existed when the seat was removed. A request that
        # was in flight then can still commit one afterwards, and it would set the password of the
        # seat once this person accepts. Retired in this transaction, with the rest of the re-issue,
        # after the invitations are revoked below (invitations, then links, as a removal does).
        revived = True
        existing.role = role
        user = existing
        seat_consumed = True

    elif existing.status is UserStatus.INVITED:
        # The seat is already occupied by this very row. Re-issue only — and
        # the same rule as the revive branch above: a re-sent invitation is
        # not resumed, so a Google link the row carries goes with it. Nothing
        # writes a sub onto an INVITED row today (Google acceptance activates
        # it in the same statement), but the rule is "re-sending clears it",
        # unconditionally, and was only half implemented until 2026-09-21.
        #
        # Nobody may re-issue a pending invitation for a role above their own, with any role:
        # an admin re-sending an OWNER's as member would downgrade the seat and hand the new link
        # to the admin. Refused before any write.
        _refuse_a_role_above(
            existing.role,
            inviter_role,
            agency_id=agency_id,
            invited_by_user_id=invited_by_user_id,
            reissue=True,
        )
        # Nothing is written to the row yet: it is read again, locked, once the invitations are
        # out of the way (below), and what this decided on is only what that read still finds.
        reissued = existing
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

    # Invitations before the user row, the order accept locks them in (autoflush is off, so
    # the user changes above are not written until the flush below).
    await _revoke_outstanding(session, agency_id=agency_id, email=email)
    if revived:
        await retire_live_tokens(session, user.id)
        # The credential version (A3 part 3, M1): nothing the old holder signed in with works once
        # the seat is back, whether or not the removal's Redis revoke reached it, and a session
        # signed in after the removal's own cutoff ends here too. Written by the flush below with
        # the rest of the row, so the user row is still written after the invitations and the links.
        user.sessions_valid_after = session_cutoff_value()
    if reissued is not None:
        # The re-send decided on an unlocked read. The invitee may have accepted since: accepting
        # locks the invitation and then this row, so by now the revoke above has waited for that
        # accept and the row is either still INVITED or no longer. Read again, locked and in place,
        # in invitation-then-user order, and refuse a seat that is no longer pending as a re-send
        # to an active account is refused.
        current = (
            await session.execute(
                select(User)
                .where(User.id == reissued.id, User.deleted_at.is_(None))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if current is None or current.status is not UserStatus.INVITED:
            raise EmailAlreadyRegistered(
                detail="That email address is already associated with an account."
            )
        current.role = role
        current.google_sub = None
        # With the link goes the review that came of it (A6 part 4, 3.123), as in the revive branch.
        current.google_linked_at = None
        current.access_review_done_at = None
    await session.flush()

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


async def _acceptable(
    session: AsyncSession, digest: str, *, lock: bool
) -> tuple[Invitation, User]:
    """The invitation and the seat it leads to, or `InvitationTokenError` if it cannot be accepted.

    `accept_invitation` asks this twice, and it is the same question both times: before the
    password is hashed (to refuse a link that was never good without spending a hashing slot on
    it), and again after, under the row locks, on what is true by then. One function, so the
    second look cannot drift from the first. Both reads use `populate_existing`, so a row already
    in this session's identity map (the first look's, or a caller's) is re-read, not served stale.

    With `lock` the invitation row is taken `FOR UPDATE` and then the user row, which is the
    order every path that touches both keeps (`invite_seat` revokes invitations before it writes
    the user, `release_seat` and the Google sign-in likewise). A second concurrent accept waits
    for the first to commit, then sees `accepted_at` set and is refused.
    """
    stmt = select(Invitation).where(Invitation.token_digest == digest)
    if lock:
        stmt = stmt.with_for_update()
    invitation = (
        await session.execute(stmt.execution_options(populate_existing=True))
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

    user_stmt = select(User).where(
        User.agency_id == invitation.agency_id,
        User.email == invitation.email,
        User.deleted_at.is_(None),
    )
    if lock:
        user_stmt = user_stmt.with_for_update()
    user = (
        await session.execute(user_stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()

    # A live token whose seat has since been removed is refused identically to
    # a forged one. The seat is gone; there is nothing to accept, and saying
    # which of the two happened tells a stranger about the agency's roster.
    if user is None or user.status is not UserStatus.INVITED:
        raise InvitationTokenError
    return invitation, user


async def accept_invitation(
    session: AsyncSession,
    *,
    redis: Redis,
    token: str,
    full_name: str,
    password: str,
    settings: Settings,
) -> User:
    """Redeem an invitation and turn the seat into a working account.

    Call it with nothing pending: it commits the session it is given.

    Raises `InvitationTokenError` for every failure. Single use is enforced
    here and not merely documented: `accepted_at` is stamped in the same
    transaction as the password write, so a link cannot open the door twice
    even if it is clicked twice.

    THE HASH WAITS OUTSIDE THE LOCKS — 2026-10-11
    ---------------------------------------------
    The hash waits for a slot (`ARGON2_MAX_CONCURRENCY`, for up to
    `ARGON2_QUEUE_TIMEOUT_SECONDS`), and this used to wait with the invitation and user rows locked
    and a pooled connection checked out, so a burst of valid links could tie up the pool. It now
    does what `auth.authenticate` and `auth.change_password` do: read and decide without a lock,
    end the read transaction, hash, and only then lock the rows and decide again. A link that was
    never good is refused before the hash and costs no slot. One that was accepted, revoked or
    expired, or whose seat was removed or taken by a Google sign-in, while the hash waited is
    refused after it, and nothing is written.

    The lock order is the one F5b settled: the invitation row, then the user row.

    THE BREACHED-PASSWORD CHECK (A4) runs between the first look and the hash, while it is on (see
    `services/breached_passwords.py`). Nothing is written when it refuses, and the claim and the
    invitation are left as they were.

    ONE HASH PER LINK IN FLIGHT — 2026-10-11
    ----------------------------------------
    The route has no limit of its own, and every request that passed the first look went on to
    hash: N requests at once with one valid link took N hashing slots (shared with login and
    sign-up) for one password. The link is now claimed in Redis before anything is read
    (`rate_limit.claim_redemption`). The request that gets the claim goes on; any other is
    refused at once with the same `InvitationTokenError` as a used link, having read and hashed
    nothing. The claim is released when this function is done, however it ends. A Redis error is
    not caught: nothing has been read or written when the claim is taken.

    The claim limits hashing work and is not what makes a link single use: it has a lifetime,
    and a holder that outlives it meets a request with a claim of its own, and then the row locks
    and the decision under them are what refuse the second. Nor does it reach past this function:
    it is released before the route commits, so a request that arrives in those few
    milliseconds still gets the claim, can hash once, and is refused under the locks.
    """
    digest = token_digest(token)
    claim = await rate_limit.claim_redemption(redis, settings, kind="invite", digest=digest)
    if claim is None:
        # Another request is accepting this invitation right now. Deliberately the same exception
        # as every other refusal: this is what an accepted invitation would be a moment later.
        raise InvitationTokenError
    try:
        await _acceptable(session, digest, lock=False)

        # End the read transaction before the wait (`expire_on_commit=False`, as login relies on;
        # and nothing read above is used again: the decision below re-reads). The route's
        # rollback on a refusal and its commit on success both work on the transaction that
        # starts after this.
        await session.commit()
        # The breached-password check (A4, D10), while it is on: after the first look, so a link
        # that was never good is refused before any outside request is made, as it is before a
        # hash; before the hash; and with the read transaction ended, so the wait holds no
        # connection. A listed password raises `BreachedPasswordError` (a 422 on `password`); the
        # `finally` below gives the claim up and the invitation stays open, so it can be accepted
        # again with another password.
        await breached_passwords.refuse_if_breached(password, settings, field="password")
        password_hash = await hash_password_async(password, settings)

        invitation, user = await _acceptable(session, digest, lock=True)
        user.password_hash = password_hash
        user.status = UserStatus.ACTIVE
        name = full_name.strip()
        if name:
            user.full_name = name
        invitation.accepted_at = datetime.now(UTC)
        await session.flush()

        logger.info("invitation.accepted", agency_id=user.agency_id, user_id=user.id)
        return user
    finally:
        await rate_limit.release_redemption(
            redis, settings, kind="invite", digest=digest, claim=claim
        )


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
    return sorted(rows, key=lambda u: (ROLE_RANK.get(u.role, len(ROLE_RANK)), u.id))


async def count_active_owners(session: AsyncSession, agency_id: str) -> int:
    """Live owners. The last one cannot be removed — see `UserRole`."""
    rows = await seat_holders(session, agency_id)
    return sum(
        1 for u in rows if u.role is UserRole.OWNER and u.status is UserStatus.ACTIVE
    )


async def release_seat(session: AsyncSession, *, agency_id: str, user: User) -> None:
    """Soft-delete a seat holder and retire any invitation pointing at them
    and any pending invitation they sent.

    **Soft, not hard.** `deleted_at` releases the seat (`count_occupied_seats`
    filters on it) while leaving `scans.requested_by_user_id` and every other
    reference intact, which is the rule `models/base.py` states for the whole
    schema: an agency removing someone must not cascade-destroy the history
    their historical reports are built from.

    Session revocation in Redis is NOT done here. It needs the Redis-backed
    `SessionStore`, which is a request-scoped dependency rather than something
    this module should reach for — so the router does it, immediately after
    committing this. What IS done here, in the same transaction, is the credential version
    (A3 part 3, the security review's M1): `users.sessions_valid_after` is set with the removal,
    so a session that revoke misses stays refused after the seat is restored or re-issued
    (`invite_seat` revives this very row, and a restore done by hand in SQL brings it back too).
    """
    user.deleted_at = datetime.now(UTC)
    await _revoke_outstanding(session, agency_id=agency_id, email=user.email)
    # Links this person minted but nobody has used yet die with their seat: with
    # email off the link is in their hands, and they could otherwise come back
    # through it after removal. Accepted invitations are history and stay.
    await session.execute(
        update(Invitation)
        .where(
            Invitation.agency_id == agency_id,
            Invitation.invited_by_user_id == user.id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    # A reset link minted for this person before the removal must not set a password on the
    # seat once it is re-issued.
    # Live links only: an expired one nobody used stays "not valid" and is not stamped "used".
    stamp = datetime.now(UTC)
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > stamp,
        )
        .values(used_at=stamp)
    )
    # Last, so the one UPDATE of the user row (`deleted_at` and this) is still written after the
    # invitations and the links: autoflush is off, and the flush below is where the row is written.
    # Taking the row earlier would reverse the order an accept, a Google sign-in and a reset confirm
    # take it in (invitation or link first, then the user), which `test_seat_removal_races.py` pins.
    user.sessions_valid_after = session_cutoff_value()
    await session.flush()
    logger.info("seat.released", agency_id=agency_id, user_id=user.id)


async def _is_named_by_anything(session: AsyncSession, user_id: str) -> bool:
    """Whether any row that survives a user's deletion points at this user.

    Every foreign key to `users.id` but one is `ON DELETE SET NULL`: deleting the user would not
    fail, it would silently detach the scan, the prompt run, the client's monthly rescan or the
    invitation from the person who made it. The exception is the password reset link, which goes
    with its user (`ON DELETE CASCADE`). `tests/test_google_invitation_choice.py` fails when a
    table gains a reference to a user that is not one of these five.
    """
    named = or_(
        exists().where(Scan.requested_by_user_id == user_id),
        exists().where(PromptRun.asked_by_user_id == user_id),
        exists().where(Client.auto_rescan_by_user_id == user_id),
        exists().where(Invitation.invited_by_user_id == user_id),
    )
    return bool((await session.execute(select(named))).scalar())


async def _was_accepted_before(session: AsyncSession, user: User) -> bool:
    """Whether an invitation for this address in this agency was ever accepted.

    That is a life this row has led even when no other column says so. `last_login_at` is written
    only by a password sign-in and by Google, so a member who joined through the emailed link and
    never signed in again leaves it empty (a password accept and a password sign-up do not write
    it), and `sessions_valid_after` has no backfill, so a seat removed and invited again before the
    column existed leaves that empty too. The accepted invitation is what is left of that
    membership. One row per address, so "this address in this agency" is this row's own.
    """
    return bool(
        (
            await session.execute(
                select(
                    exists().where(
                        Invitation.agency_id == user.agency_id,
                        Invitation.email == user.email,
                        Invitation.accepted_at.is_not(None),
                    )
                )
            )
        ).scalar()
    )


async def free_unactivated_seat(session: AsyncSession, *, user: User) -> bool:
    """Free the address of an invited seat whose invitee turned the invitation down (A6 part 3).

    The invitation goes first: every live one for the address is revoked, as an admin's removal of
    the seat revokes them. Then the address is freed, which is `users.email`'s unique constraint
    holding it (a soft-deleted row still does):

    * **A seat that was never used is deleted.** The row was made by an invitation and has led no
      life: `last_login_at` and `sessions_valid_after` are both NULL, it holds no password or
      Google link, no invitation for its address in its agency was ever accepted
      (`_was_accepted_before`), and nothing points at it (`_is_named_by_anything`). Nothing it
      could have made survives, because it made nothing. Its reset links, if it somehow had any,
      go with it.
    * **Anything else is kept, and put back as removed.** That is a removed member who was invited
      again: `invite_seat` revived the same row so the history that names its id stays with it. It
      goes back to what it was, exactly as `release_seat` leaves a row. The address then stays
      held, as it is for every address that has had a seat here, and signing up with it is refused
      the way every such address is (`409 email-already-registered`).

    Returns True when the row was deleted, False when it was kept. The caller commits.

    Only an invited seat that has not been removed may be freed; any other row is a bug in the
    caller and raises `ValueError` before anything is written, so this can never delete a member.
    Called with the invitation and then the user row already locked (`google_oauth`), the order an
    accept takes them in.
    """
    if user.status is not UserStatus.INVITED or user.deleted_at is not None:
        raise ValueError("only an invited seat that has not been removed can be freed")
    had_a_life = (
        user.last_login_at is not None
        or user.sessions_valid_after is not None
        or user.password_hash is not None
        or user.google_sub is not None
        or await _was_accepted_before(session, user)
    )
    if had_a_life or await _is_named_by_anything(session, user.id):
        await release_seat(session, agency_id=user.agency_id, user=user)
        return False
    await _revoke_outstanding(session, agency_id=user.agency_id, email=user.email)
    await session.delete(user)
    await session.flush()
    return True

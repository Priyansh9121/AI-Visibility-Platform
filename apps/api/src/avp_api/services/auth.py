"""Signup, login, and password change. Seat invitations live in `invitations.py`."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import DateTime, func, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings, get_settings
from ..errors import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    SignUpNotPossible,
    ValidationProblem,
)
from ..models import Agency, User, UserRole, UserStatus
from ..security import (
    dummy_verify_async,
    hash_password_async,
    needs_rehash,
    verify_password_async,
)
from . import breached_passwords, seats

logger = structlog.get_logger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# The unique constraint on `users.email` (the initial schema's name, and the naming convention's).
USERS_EMAIL_UNIQUE = "uq_users_email"


AGENCIES_SLUG_UNIQUE = "uq_agencies_slug"


class SignUpFailedError(Exception):
    """A sign-up that failed for a reason that is not the caller's (an unexpected constraint).

    A plain exception with no message: the server answers 500 and nothing of the insert, the
    address included, travels with it.
    """


def integrity_details(exc: IntegrityError) -> tuple[str | None, str | None]:
    """The constraint's name and the SQLSTATE of an integrity error, and nothing of its message.

    The driver's message carries the offending values (an address), so callers log these two
    and drop the exception.
    """
    cause = getattr(exc.orig, "__cause__", None)
    return getattr(cause, "constraint_name", None), getattr(cause, "sqlstate", None)


def normalise_email(email: str) -> str:
    """Lower-case and strip.

    Deliberately does NOT strip dots or +tags. Those are Gmail conventions, not
    standards; applying them universally would merge distinct addresses at
    providers that treat them as distinct, locking people out of their own
    accounts.
    """
    return email.strip().lower()


def slugify(name: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    )
    slug = _SLUG_STRIP.sub("-", ascii_name).strip("-")
    return slug[:60] or "agency"


async def _unique_slug(session: AsyncSession, base: str) -> str:
    """Append a numeric suffix until the slug is free."""
    candidate = base
    for suffix in range(0, 100):
        if suffix:
            candidate = f"{base}-{suffix}"
        exists = (
            await session.execute(select(Agency.id).where(Agency.slug == candidate))
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    # Fall back to the agency ULID, which is unique by construction.
    return f"{base}-{ids.new_id(ids.AGENCY).split('_')[1][:8].lower()}"


async def email_exists(session: AsyncSession, email: str) -> bool:
    stmt = select(User.id).where(User.email == normalise_email(email))
    return (await session.execute(stmt)).scalar_one_or_none() is not None


class EmailTakenError(Exception):
    """The unique index on `users.email` refused the insert. Carries nothing."""


async def create_agency_with_owner(
    session: AsyncSession,
    *,
    settings: Settings,
    agency_name: str,
    user_fields: dict[str, Any],
) -> tuple[Agency, User]:
    """Insert an agency and its OWNER in a savepoint; the one place sign-up and Google do it.

    Raises `EmailTakenError` when the email's unique index refuses the user, and
    `SignUpFailedError` for any other integrity error or for slug retries spent. Nothing of the
    attempt is left in the session.
    """
    # No pre-check for an existing address. The insert is attempted and the unique index on the
    # email answers: a sign-up for an address that has an account then does the same statements as
    # one that does not (slug lookup, agency insert, user insert) up to the refusal, and two
    # sign-ups for one address at once cannot both pass a check and then both insert. The inserts
    # sit in a savepoint, so a refusal removes the attempted agency and leaves the session usable.
    #
    # A slug taken between the lookup and the insert (another sign-up with the same agency name,
    # committed in that gap) is retried: the savepoint is gone, the lookup now sees the other
    # row, and the next suffix is free. A few attempts, `signup_slug_attempts`.
    #
    # Nothing is raised from inside the `except`: the driver's message names the address, and an
    # exception raised there would carry it as `__context__` into any traceback that is logged.
    # A flag is set, and the answer is raised after the block.
    email_taken = False
    other: tuple[str | None, str | None] | None = None
    for _attempt in range(settings.signup_slug_attempts):
        slug_taken = False
        try:
            async with session.begin_nested():
                agency = Agency(
                    id=ids.new_id(ids.AGENCY),
                    name=agency_name.strip(),
                    slug=await _unique_slug(session, slugify(agency_name)),
                    seat_limit=settings.default_seat_limit,
                )
                session.add(agency)
                # Flush so the FK target exists before the user insert, without committing.
                await session.flush()

                user = User(
                    id=ids.new_id(ids.USER),
                    agency_id=agency.id,
                    role=UserRole.OWNER,
                    status=UserStatus.ACTIVE,
                    **user_fields,
                )
                session.add(user)
                await session.flush()
        except IntegrityError as exc:
            constraint, sqlstate = integrity_details(exc)
            if constraint == USERS_EMAIL_UNIQUE:
                email_taken = True
            elif constraint == AGENCIES_SLUG_UNIQUE:
                slug_taken = True
            else:
                other = (constraint, sqlstate)
        else:
            return agency, user
        if not slug_taken:
            break
    if email_taken:
        raise EmailTakenError()
    if other is not None:
        # The constraint's name and the SQLSTATE: never the driver's message.
        logger.error("auth.sign_up.integrity_error", constraint=other[0], sqlstate=other[1])
    else:
        logger.error("auth.sign_up.slug_attempts_spent", attempts=settings.signup_slug_attempts)
    raise SignUpFailedError()


async def sign_up_agency(
    session: AsyncSession,
    *,
    agency_name: str,
    full_name: str,
    email: str,
    password: str,
    settings: Settings | None = None,
) -> tuple[Agency, User]:
    """Create an agency and its first user (the owner).

    The first user is always OWNER — an agency with no owner has nobody who can
    manage seats or billing. Seat checking is skipped for this one insert
    because the agency is being created in the same transaction and its first
    seat is definitionally available.

    The password's length and repetitiveness were decided by the request schema (`Password`);
    whether it is on the breached-password list is decided here, first (see below), and raises
    `BreachedPasswordError`, a 422 on the `password` field, while that check is on.
    """
    settings = settings or get_settings()
    email = normalise_email(email)
    # The breached-password check (A4, D10), while it is on: on the password alone, on every
    # attempt that reaches this function (it has passed the per-address limit and the schema),
    # before anything that depends on WHETHER THE ADDRESS HAS AN ACCOUNT. A new address and a taken
    # one then make the same one request, and a listed password is this one refusal (a 422 on the
    # field) for both, so the neutral 409 below cannot be told from a success with it. It waits for
    # the outside service with no connection held: nothing has been read or written yet.
    await breached_passwords.refuse_if_breached(password, settings, field="password")
    # Hashed before the first statement: a sign-up waiting for the pool holds no connection.
    password_hash = await hash_password_async(password, settings)

    # The insert is attempted with no pre-check (see `create_agency_with_owner`).
    try:
        return await create_agency_with_owner(
            session,
            settings=settings,
            agency_name=agency_name,
            user_fields={
                "email": email,
                "password_hash": password_hash,
                "full_name": full_name.strip(),
            },
        )
    except EmailTakenError:
        pass
    # Raised outside the `except`, so the answer carries no `__context__`.
    raise SignUpNotPossible()


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    slowed: bool,
    settings: Settings | None = None,
) -> User:
    """Verify credentials, or raise InvalidCredentials.

    Failure is a single error regardless of cause — unknown email, wrong
    password, or suspended account all produce the same message, so the login
    endpoint is not a user-enumeration oracle. `dummy_verify` equalises timing
    for the unknown-email path, which would otherwise return without doing any
    Argon2 work and be measurably faster.

    `slowed` is the limiter's verdict that this address is in a wait (A2): the attempt is
    refused WITHOUT its password being checked, even if it is right. It is not a shortcut. It
    runs the same single lookup and the same commit as any other attempt, then the same ONE
    Argon2 operation at the configured cost (the dummy verify, as for an unknown address), and
    refuses with the matched user's id for the server's log line, so a refusal during a wait
    sends the database the same statements and takes the same work as a wrong password.

    `slowed` is REQUIRED, with no default: a caller that leaves it out is a `TypeError`, not a
    sign-in that quietly skips the slow-down. The sign-in route passes the verdict
    (`rate_limit.enforce_login`); a caller that is not behind the limiter says `slowed=False`
    and so says it was not.
    """
    settings = settings or get_settings()
    email = normalise_email(email)

    stmt = select(User).where(User.email == email, User.deleted_at.is_(None))
    user = (await session.execute(stmt)).scalar_one_or_none()

    # End the read transaction before any wait for Argon2: a login queued for a hash slot
    # holds no pooled connection. (`expire_on_commit=False`, so `user` stays readable; the
    # conditional rehash UPDATE below and the route's read-back guard what changes meanwhile.)
    await session.commit()

    matched_id = user.id if user is not None else None

    def refuse() -> InvalidCredentials:
        exc = InvalidCredentials()
        exc.user_id = matched_id  # for the server's log line; never sent to the client
        return exc

    # A wait, no such user, or an account with no password to check (Google-only): the password
    # is not looked at, and the one Argon2 operation is the dummy at the configured cost.
    if slowed or user is None or user.password_hash is None:
        await dummy_verify_async(settings)
        raise refuse()

    if not await verify_password_async(password, user.password_hash, settings):
        raise refuse()

    if user.status is not UserStatus.ACTIVE:
        raise refuse()

    # Transparent cost-factor migration: if the stored hash predates a raise in
    # Argon2 parameters, upgrade it now that the plaintext is briefly in hand.
    # Written only if the hash is still the one just verified: a Google link
    # that cleared it in between must not be overwritten (0 rows = refused).
    if needs_rehash(user.password_hash, settings):
        # Hashed before the statement is built: the thread work is awaited, never inside it.
        new_hash = await hash_password_async(password, settings)
        rehashed = (
            await session.execute(
                update(User)
                .where(User.id == user.id, User.password_hash == user.password_hash)
                .values(password_hash=new_hash)
                .returning(User.id)
                .execution_options(synchronize_session=False)
            )
        ).all()
        if len(rehashed) != 1:
            raise refuse()
        await session.refresh(user)

    user.last_login_at = datetime.now(UTC)
    await session.flush()
    return user


async def create_user_in_agency(
    session: AsyncSession,
    *,
    agency_id: str,
    email: str,
    full_name: str,
    password: str,
    role: UserRole = UserRole.MEMBER,
    settings: Settings | None = None,
) -> User:
    """Add a seat holder. Enforces the seat limit inside the transaction.

    **No route calls this, and it takes no request schema:** only tests do, to make a user with a
    known password quickly. So it applies none of the password rules (length, repetitiveness, the
    breached-password check): those belong to the four places a person SETS a password, which are
    `sign_up_agency`, `change_password`, `password_reset.confirm_reset` and
    `invitations.accept_invitation`. A caller that is ever not a test must apply them itself.
    `tests/test_password_policy_a4.py` lists every function in the API that hashes a password, so
    a new one cannot arrive unnoticed.
    """
    settings = settings or get_settings()
    email = normalise_email(email)

    if await email_exists(session, email):
        raise EmailAlreadyRegistered(
            detail="That email address is already associated with an account."
        )

    # Must precede the insert and share its transaction — see services/seats.py.
    await seats.assert_seat_available(session, agency_id)

    user = User(
        id=ids.new_id(ids.USER),
        agency_id=agency_id,
        email=email,
        password_hash=await hash_password_async(password, settings),
        full_name=full_name.strip(),
        role=role,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user


class SamePasswordError(ValidationProblem):
    """The new password is the current one.

    A `422` on the field, not a silent success. Silently accepting it would
    revoke every other session — which is what a change does — for a change
    that did not happen, and the person would have no way to tell.

    This leaks nothing: the caller has already proved they know the current
    password, so being told it matches tells them something they supplied.
    """

    def __init__(self) -> None:
        super().__init__(
            detail="One or more fields were invalid.",
            errors=[
                {
                    "field": "newPassword",
                    "message": "That is already your password. Choose a different one.",
                    "type": "value_error",
                }
            ],
        )


async def change_password(
    session: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
    settings: Settings | None = None,
) -> User:
    """Re-verify the current password, then set the new one.

    **The current password is re-verified server-side even though the caller
    holds a valid session.** A session proves someone got in once; it does not
    prove they are still the account holder. An unlocked laptop, a borrowed
    phone, or a stolen cookie all present a valid session, and without this
    check any of them silently becomes permanent ownership of the account.

    NO DUMMY HASH HERE, AND THAT IS DELIBERATE
    ------------------------------------------
    `authenticate` burns a throwaway Argon2 verification on the unknown-email
    path because login latency would otherwise reveal whether an address has an
    account. There is nothing equivalent to hide here: there is exactly one user
    — the authenticated caller — and no branch that depends on a fact the caller
    does not already know. A wrong current password does return faster than a
    right one, because success goes on to hash the new password and write it.
    That difference tells an observer only that a guess against a session they
    already control was wrong, which the response status says outright. Adding a
    dummy hash would be copying a defence to a place with nothing to defend.

    Raises `InvalidCredentials` for a wrong current password, `SamePasswordError` when nothing
    would change, and `BreachedPasswordError` (a 422 on `newPassword`) when the breached-password
    check is on and the new password is listed. That check comes last of the three, so a caller who
    has not proved the current password, or who sends the current one again, makes no outside
    request.
    """
    settings = settings or get_settings()

    # End the read transaction before waiting for Argon2 (as `authenticate` does): the
    # conditional UPDATE below and the route's read-back guard the write.
    await session.commit()

    if user.password_hash is None or not await verify_password_async(
        current_password, user.password_hash, settings
    ):
        # The same class login uses, so there is one vocabulary for "those
        # credentials are wrong" rather than a second one to learn. The detail
        # names the field and says nothing further — there is no "no such user"
        # to leak, because the caller is authenticated.
        raise InvalidCredentials(detail="That is not your current password.")

    if await verify_password_async(new_password, user.password_hash, settings):
        raise SamePasswordError()

    # The breached-password check (A4, D10), while it is on: on the new password alone, after the
    # caller has proved the current one and the new one is a different password, and before it is
    # hashed. The read transaction was ended above, so the wait holds no connection.
    await breached_passwords.refuse_if_breached(new_password, settings, field="newPassword")

    # Hashed first (off the loop), then the conditional write: if a Google link cleared
    # the hash since this request loaded the row, the write must not bring it back.
    new_hash = await hash_password_async(new_password, settings)
    # A reset link asked for before this change must not take the account after it: whoever holds
    # a leaked one could redeem it within the hour and set a password of their own over the one
    # the owner just chose. Retired in this transaction, so the new password and the dead links
    # land together; a failure below rolls both back.
    #
    # BEFORE the write, and not after it, for lock order: `confirm_reset` locks the token row and
    # then writes the user row, and retiring after this write took the user row first. A change
    # racing the redemption of a link then deadlocked (Postgres aborted the change after a
    # second, and the redemption won). In this order the change waits for the redemption, or
    # the redemption for the change, and the loser is refused cleanly.
    #
    # (Imported here: `password_reset` imports this module for `normalise_email`, so a
    # module-level import would be a cycle.)
    from .password_reset import retire_live_tokens

    await retire_live_tokens(session, user.id)
    written = (
        await session.execute(
            update(User)
            .where(User.id == user.id, User.password_hash == user.password_hash)
            .values(password_hash=new_hash)
            .returning(User.id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    if len(written) != 1:
        raise InvalidCredentials(detail="That is not your current password.")
    await session.refresh(user)
    return user


def session_cutoff_value() -> Any:
    """The value `users.sessions_valid_after` takes when a change ends a person's sessions.

    A3 part 3 (launch checklist 3.125). `deps.current_principal` and `deps.reconfirm_principal`
    check the column on every request: a session whose `created_at` is BEFORE it is refused (`401`)
    and, on an ordinary request, revoked. It is the backstop under the Redis revokes, which every
    caller keeps: those can fail, can miss a list that was evicted, and can run a moment before a
    session is minted. The cutoff does not depend on any of that.

    **One clock.** The instant is the app's `datetime.now(UTC)`, the clock `SessionStore.create`
    stamps `created_at` with, taken when this is called and not from the database (`now()` is the
    START of the transaction, which can be long before the commit, and a session minted since would
    count as older). A session is refused only when its `created_at` is strictly before the cutoff,
    so the replacement a password change mints after its commit is never touched by its own cutoff.

    **It only moves forward.** The value is `GREATEST(the column, that instant)`, worked out by the
    database when the statement runs (a NULL is no cutoff), so two changes that overlap, or a change
    written by an instance whose clock runs behind, can never bring an ended session back by writing
    an earlier instant. Skew between instances can make a session be refused that should have
    lived, which costs its holder a sign-in.

    An SQL expression and not a datetime, to be written in one of two ways: `set_session_cutoff`
    (its own UPDATE), or an assignment to the mapped attribute, `user.sessions_valid_after =
    session_cutoff_value()`, which the next flush writes as part of the UPDATE of the row the caller
    is already changing (`invitations.release_seat` and the revive branch of `invite_seat`). The
    second adds no statement and no lock order of its own: the user row is written once, last, as
    those paths already write it. The attribute is expired by that flush and must be refreshed
    before it is read.
    """
    now = literal(datetime.now(UTC), DateTime(timezone=True))
    return func.greatest(User.sessions_valid_after, now)


async def set_session_cutoff(session: AsyncSession, user_id: str) -> None:
    """End every session `user_id` signed in before this moment, whatever Redis still holds.

    Writes `session_cutoff_value()` with an UPDATE of its own, for the places that make no other
    write to the user row (sign out everywhere, the password change's and the reset's and the
    Google link's commit, the operator's reset script).

    **Called last, inside the transaction of the change, so the cutoff commits with it.** Callers
    call this immediately before `commit()` and after their Redis revoke, because a session minted
    between the two is older than the cutoff and dies with it, and one minted after the cutoff is
    not touched by it.

    Not caught by a cutoff, and covered elsewhere: (a) a session minted after it by a sign-in that
    checked the old credentials before the change committed. The read-back that a password sign-in
    makes after minting (`login`, `change_password`, `accept_invitation`) takes a share lock on the
    user row, so it waits for a change in flight and sees what it did; the Redis revoke after the
    commit covers it too. (b) A session minted by an instance whose clock runs ahead of the one that
    wrote the cutoff, by more than the time between the two (the Redis revokes, which every caller
    keeps).
    """
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(sessions_valid_after=session_cutoff_value())
        .execution_options(synchronize_session=False)
    )

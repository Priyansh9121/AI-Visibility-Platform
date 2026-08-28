"""Signup, login, and password change. Seat invitations live in `invitations.py`."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings, get_settings
from ..errors import EmailAlreadyRegistered, InvalidCredentials, ValidationProblem
from ..models import Agency, User, UserRole, UserStatus
from ..security import dummy_verify, hash_password, needs_rehash, verify_password
from . import seats

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


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
    """
    settings = settings or get_settings()
    email = normalise_email(email)

    if await email_exists(session, email):
        # Deliberately explicit. Signup is not a place where email enumeration
        # matters much — an attacker can discover the same fact by attempting
        # to register — and a vague error here just makes real users retry.
        raise EmailAlreadyRegistered(
            detail="An account with that email already exists. Try signing in instead."
        )

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
        email=email,
        password_hash=hash_password(password, settings),
        full_name=full_name.strip(),
        role=UserRole.OWNER,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return agency, user


async def authenticate(
    session: AsyncSession, *, email: str, password: str, settings: Settings | None = None
) -> User:
    """Verify credentials, or raise InvalidCredentials.

    Failure is a single error regardless of cause — unknown email, wrong
    password, or suspended account all produce the same message, so the login
    endpoint is not a user-enumeration oracle. `dummy_verify` equalises timing
    for the unknown-email path, which would otherwise return without doing any
    Argon2 work and be measurably faster.
    """
    settings = settings or get_settings()
    email = normalise_email(email)

    stmt = select(User).where(User.email == email, User.deleted_at.is_(None))
    user = (await session.execute(stmt)).scalar_one_or_none()

    if user is None or user.password_hash is None:
        dummy_verify(settings)
        raise InvalidCredentials()

    if not verify_password(password, user.password_hash, settings):
        raise InvalidCredentials()

    if user.status is not UserStatus.ACTIVE:
        raise InvalidCredentials()

    # Transparent cost-factor migration: if the stored hash predates a raise in
    # Argon2 parameters, upgrade it now that the plaintext is briefly in hand.
    if needs_rehash(user.password_hash, settings):
        user.password_hash = hash_password(password, settings)

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
    """Add a seat holder. Enforces the seat limit inside the transaction."""
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
        password_hash=hash_password(password, settings),
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

    Raises `InvalidCredentials` for a wrong current password and
    `SamePasswordError` when nothing would change.
    """
    settings = settings or get_settings()

    if user.password_hash is None or not verify_password(
        current_password, user.password_hash, settings
    ):
        # The same class login uses, so there is one vocabulary for "those
        # credentials are wrong" rather than a second one to learn. The detail
        # names the field and says nothing further — there is no "no such user"
        # to leak, because the caller is authenticated.
        raise InvalidCredentials(detail="That is not your current password.")

    if verify_password(new_password, user.password_hash, settings):
        raise SamePasswordError()

    user.password_hash = hash_password(new_password, settings)
    await session.flush()
    return user

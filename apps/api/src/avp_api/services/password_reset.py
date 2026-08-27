"""Password reset — Epic 9.13.

THE ONE PROPERTY THIS MODULE EXISTS TO HOLD
-------------------------------------------
`request_reset` does the same observable thing for an address that exists and
one that does not. No different return, no different timing class, no exception
on the unknown path. `services/auth.authenticate` already takes this seriously
in the other direction — it burns a dummy Argon2 verification on an unknown
email so login latency does not leak account existence — and a reset endpoint
that answered "no such user" faster would hand back exactly what that defends.

The token is minted here and returned to the CALLER (the router) so it can be
put in a URL. It is never returned to the client, and only its digest is
persisted — see `models/password_reset.py` for why this follows the session
rule rather than the share-token rule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings
from ..models import PasswordResetToken, User, UserStatus
from ..security import hash_password, new_session_token, token_digest
from .auth import normalise_email

logger = structlog.get_logger(__name__)


class ResetTokenError(Exception):
    """The token does not resolve to a live, unused, unexpired row.

    **One exception for every reason.** Unknown, expired, already used and
    belonging-to-a-suspended-user all raise this same class carrying no detail,
    so the router has exactly one thing to say and no branch that could tell an
    attacker which kind of wrong their guess was — the same discipline Epic
    9.8's public report route applies to a bad share token.
    """


async def request_reset(
    session: AsyncSession, *, email: str, settings: Settings
) -> tuple[User, str] | None:
    """Mint a reset token for `email`, or return None if there is nobody to mint for.

    **The None is for the ROUTER's benefit, not the caller's response.** The
    router must answer identically either way; returning None rather than
    raising keeps the "nothing to do" path from looking like an error anywhere
    in the stack, including in logs and traces.
    """
    normalised = normalise_email(email)
    user = (
        await session.execute(
            select(User).where(User.email == normalised, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()

    if user is None or user.status is not UserStatus.ACTIVE:
        # Logged without the address at warning level would be useless for
        # support, and logged AT the address is fine: this is our own log, and
        # the response the caller gets is unchanged.
        logger.info("password_reset.request.no_account", email=normalised)
        return None

    # Invalidate every outstanding token for this user first. Requesting a
    # second link should retire the first — otherwise a forwarded or leaked
    # earlier email stays live for the rest of its hour.
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )

    # Same generator and same 256 bits as a session token. A reset token is a
    # bearer credential for one action; it has no business being weaker.
    token = new_session_token()
    session.add(
        PasswordResetToken(
            id=ids.new_id(ids.PASSWORD_RESET),
            user_id=user.id,
            token_digest=token_digest(token),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.password_reset_ttl_seconds),
        )
    )
    await session.flush()
    # The token is never logged. It is the credential.
    logger.info("password_reset.request.minted", user_id=user.id)
    return user, token


async def confirm_reset(
    session: AsyncSession, *, token: str, new_password: str, settings: Settings
) -> User:
    """Redeem a token and set the new password. Raises `ResetTokenError`.

    Single use is enforced HERE and not merely documented: `used_at` is stamped
    in the same transaction as the password change, so a link cannot open the
    door twice even if it is clicked twice.
    """
    row = (
        await session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_digest == token_digest(token)
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at <= now:
        # Deliberately one branch and one exception — see ResetTokenError.
        raise ResetTokenError

    user = (
        await session.execute(
            select(User).where(User.id == row.user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if user is None or user.status is not UserStatus.ACTIVE:
        raise ResetTokenError

    user.password_hash = hash_password(new_password, settings)
    row.used_at = now
    await session.flush()
    logger.info("password_reset.confirm.succeeded", user_id=user.id)
    return user

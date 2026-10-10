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
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings
from ..models import PasswordResetToken, User, UserStatus
from ..security import hash_password_async, new_session_token, token_digest
from .auth import normalise_email
from .links import reset_link

logger = structlog.get_logger(__name__)


class ResetTokenError(Exception):
    """The token does not resolve to a live, unused, unexpired row.

    **One exception for every reason.** Unknown, expired, already used and
    belonging-to-a-suspended-user all raise this same class carrying no detail,
    so the router has exactly one thing to say and no branch that could tell an
    attacker which kind of wrong their guess was — the same discipline Epic
    9.8's public report route applies to a bad share token.
    """


async def retire_live_tokens(
    session: AsyncSession, user_id: str, *, now: datetime | None = None
) -> None:
    """Retire every reset link of `user_id` that nobody has used, in the caller's transaction.

    The one statement for the places that must kill a user's outstanding links: asking for a new
    one (`request_reset` below), changing the password while signed in (`auth.change_password`),
    and re-issuing a removed seat (`invitations.invite_seat`). `release_seat` and the Google link
    run the same statement inline. Committing is the caller's.
    """
    await session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now or datetime.now(UTC))
    )


async def request_reset(
    session: AsyncSession, *, email: str, settings: Settings
) -> tuple[User, str] | None:
    """Mint a reset token for `email`, or return None if there is nobody to mint for.

    **The None is for the ROUTER's benefit, not the caller's response.** The
    router must answer identically either way; returning None rather than
    raising keeps the "nothing to do" path from looking like an error anywhere
    in the stack, including in logs and traces.

    It is also None when another request for the same user is minting right now:
    that request's link is the live one, and this one sends nothing (see the lock
    below).
    """
    normalised = normalise_email(email)
    user = (
        await session.execute(
            select(User).where(User.email == normalised, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()

    if user is None or user.status is not UserStatus.ACTIVE:
        # The event alone. Until 2026-09-29 this logged the address, which
        # meant every address anybody typed into the reset form — customer or
        # not — ended up in logs the host keeps. No log line carries an email
        # address now (`services/email.py` says why for the rest).
        logger.info("password_reset.request.no_account")
        return None

    if user.password_hash is None:
        # NO PASSWORD, NOTHING TO RESET — 2026-09-29. An account with no
        # password signs in with Google (the users table's check constraint
        # allows nothing else for an active one), and a reset link would ADD a
        # password to it: a second way in, opened by whoever reads the inbox.
        # So it goes the "no account" way, minting nothing, and the caller
        # answers byte for byte as it does for a stranger. The event is its
        # own, named by id and never by address.
        logger.info("password_reset.request.no_password", user_id=user.id)
        return None

    # ONE LIVE LINK PER USER, EVEN FOR TWO REQUESTS AT ONCE — 2026-10-11. The retire below and
    # the insert after it are separate statements, and a row one request has inserted is
    # invisible to the other until it commits. Two requests for one address that ran side by side
    # each retired nothing of the other's, each inserted, and both links stayed live: redeeming
    # one left the other redeemable for the rest of its hour. A transaction-level advisory lock
    # keyed on the user stops that, and a request that finds it taken does NOT wait for it. The
    # request holding it is minting this address's live link and sends it, so this one returns
    # None and the route answers as it does for a stranger, with nothing sent. Not waiting is the
    # point: a flood for one known address (the Redis rate limit fails open) cannot park pool
    # connections on the lock. Requests that do not overlap get the lock in turn, and the later
    # one's retire sees the earlier link and retires it.
    #
    # This relies on READ COMMITTED, the default (nothing in the app sets another): the retire
    # takes its snapshot after the lock, so it sees every link committed before the lock was
    # granted.
    #
    # Advisory, and not `FOR UPDATE` on the user row: `confirm_reset` locks a token row and then
    # writes the user row, so taking the user row first and the token rows second here could
    # deadlock against a confirm. This takes no row lock at all. The route commits straight after
    # (so does `scripts/issue_password_reset.py`), and that commit, or a rollback, releases it.
    # The auto-rescan wake takes its lock the same way (`services/auto_rescan.py`,
    # `WAKE_LOCK_KEY`); this key is derived from the user id, so only one user's requests meet.
    #
    # It adds nothing an observer can newly see: only a known address reaches this line, and a
    # known address already writes before the response (the retire, the insert, the commit) while
    # an unknown one does not. The route says so, and hardening item A1 is to close that.
    granted = (
        await session.execute(
            select(
                func.pg_try_advisory_xact_lock(
                    func.hashtextextended(f"password_reset:{user.id}", 0)
                )
            )
        )
    ).scalar_one()
    if not granted:
        return None

    # Invalidate every outstanding token for this user first. Requesting a
    # second link should retire the first — otherwise a forwarded or leaked
    # earlier email stays live for the rest of its hour.
    await retire_live_tokens(session, user.id)

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


def reset_url(settings: Settings, token: str) -> str:
    """The web page that redeems `token` — the one place this link is built.

    Shared by the forgot-password route and the operator's
    `scripts/issue_password_reset.py` (2026-09-29), so the two cannot hand out
    different links. It carries a live credential: send it or print it to the
    operator, and never log it.
    """
    return reset_link(settings.public_web_base_url, token)


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
            # Row lock: a second concurrent confirm waits for the first to
            # commit, then sees `used_at` set and is refused.
            .with_for_update()
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
    # No password, no reset: a Google-only row (including one a Google link
    # just cleared) takes no new password from a link issued before.
    if user is None or user.status is not UserStatus.ACTIVE or user.password_hash is None:
        raise ResetTokenError

    user.password_hash = await hash_password_async(new_password, settings)
    row.used_at = now
    await session.flush()
    return user

"""Signup, login, logout, and the authenticated-context endpoint."""

from __future__ import annotations

import contextlib
import hmac
from typing import Any, Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from ..config import Settings
from ..db import get_sessionmaker
from ..deps import (
    DbDep,
    GoogleFlowStoreDep,
    GoogleProviderDep,
    PrincipalDep,
    RedisDep,
    SessionStoreDep,
    SettingsDep,
)
from ..errors import (
    AuthenticationRequired,
    GoogleSignInNotConfigured,
    InvalidCredentials,
    InvalidGoogleTicket,
    InvalidInvitation,
    InvalidResetToken,
    ProblemError,
    _problem_response,
)
from ..models import Agency, User, UserStatus
from ..schemas.auth import (
    AcceptInvitationRequest,
    AgencyOut,
    ChangePasswordRequest,
    CompleteGoogleSignUpRequest,
    GoogleChoiceRequest,
    GooglePendingOut,
    GooglePendingRequest,
    GoogleTicketOut,
    LoginRequest,
    MeOut,
    ResetPasswordConfirm,
    ResetPasswordRequest,
    ResetRequestOut,
    ScanEmailsOut,
    ScanEmailsRequest,
    SeatUsageOut,
    SignUpRequest,
    UserOut,
)
from ..security import token_digest
from ..services import auth as auth_service
from ..services import email as email_service
from ..services import google_oauth, rate_limit
from ..services import invitations as invitation_service
from ..services import password_reset as reset_service
from ..services import seats as seat_service
from ..sessions import SessionStore

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# The founder's sentence (A5 part 2) for a reset link that is already used or being redeemed.
RESET_LINK_USED = (
    "This link has already been used. If you just set a new password, sign in with it. "
    "Otherwise, request a new link."
)


# What the reset request answers, for every address (A1, D9). With email off the detail is today's
# sentence; with it on, the founder's.
RESET_EMAIL_OFF_DETAIL = (
    "Password reset by email is not available on this service yet, so no link was sent."
)
RESET_EMAIL_ON_DETAIL = "If an account exists for that email, we've sent a link."


def _scan_emails_for(user: User, settings: Settings) -> ScanEmailsOut:
    """The scan-finished email switch as this user sees it (F1)."""
    return ScanEmailsOut(available=settings.scan_emails_available, enabled=user.scan_emails)


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_key,
        value=token,
        # The ABSOLUTE lifetime (14 days): the longest a session can live. The server decides
        # when it stops working, and that can be sooner (72 hours unused, `sessions.py`), so the
        # browser may hold a cookie the server no longer honours; a request that carries one is
        # the usual 401. The cookie is not renewed by use: the browser's copy ends where the
        # session's absolute end does.
        max_age=settings.session_ttl_seconds,
        # httpOnly: XSS cannot read this cookie. The single most valuable
        # property of the session-cookie approach over a token in localStorage.
        httponly=True,
        secure=settings.session_cookie_secure,
        # First-party (the web app serves this API under its own origin), so Lax.
        # Path=/ and no Domain are what the `__Host-` prefix requires.
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    # A browser ignores the deletion of a `__Host-` cookie unless it carries the
    # same Secure and Path=/ as the cookie it removes.
    response.delete_cookie(
        key=settings.session_cookie_key,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


async def _end_presented_session(store: SessionStore, token: str | None) -> None:
    """Rotation at sign-in (A3 part 3): end the session this browser already presents, if any.

    Called by the sign-ins that follow a person's credentials (password login and both steps of
    Google sign-in), AFTER the credentials were accepted and just BEFORE the new session is minted,
    so no older session survives on that browser: the new one is the only one it holds. Any user's
    session is ended, not only the one signing in: the cookie belongs to the browser, and the
    browser is being handed another. Before the mint, not after, so that the session being replaced
    does not count against the cap (`session_max_per_user`) and a sign-in does not end somebody's
    other, older device for it.

    `revoke` is a no-op for a token that has no session (expired, already revoked, made up), so
    there is nothing to check first. The sign-ins that make an account (sign-up, an invitation
    accept) do not call this: the Set-Cookie of their answer replaces the browser's cookie, but the
    session it carried stays valid on the server until it ends by itself or is revoked.
    """
    if token:
        await store.revoke(token)


@router.post("/sign-up", response_model=MeOut, status_code=status.HTTP_201_CREATED)
async def sign_up(
    payload: SignUpRequest,
    request: Request,
    redis: RedisDep,
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Any:
    """Create an agency and its owner, then sign them straight in.

    Signing in as part of signup is deliberate: requiring a fresh login
    immediately after choosing a password is friction with no security value.

    **Errors:** `409 sign-up-not-possible` when the address already has an account (or another
    sign-up for it wins at the same moment). Its title and detail are one neutral sentence that
    does not say which, and the work behind it matches a real sign-up as closely as it can.
    `422 validation-failed`, `429 rate-limited`.

    **A password on the breached-password list is a `422` on the `password` field** with the
    sentence "This password has appeared in a data breach. Choose a different one.", while that
    check is switched on (`BREACHED_PASSWORD_CHECK_ENABLED`, off by default). It runs on every
    attempt that passes the per-address limit and the schema, before anything that depends on
    whether the address has an account, so a taken address gets this `422` too, and never the
    `409`, for a listed password.
    """
    # Before the 409 for an address that has an account, so that answer cannot be probed
    # faster than the limit.
    await rate_limit.enforce(redis, settings, request, action="signup", email=payload.email)
    agency, user = await auth_service.sign_up_agency(
        db,
        agency_name=payload.agency_name,
        full_name=payload.full_name,
        email=payload.email,
        password=payload.password,
        settings=settings,
    )
    await db.commit()
    await db.refresh(agency)
    await db.refresh(user)

    token, _ = await store.create(user_id=user.id, agency_id=agency.id)
    _set_session_cookie(response, token, settings)

    used, limit = await seat_service.seat_usage(db, agency.id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
        feedback_email=settings.feedback_email,
        scan_emails=_scan_emails_for(user, settings),
    )


@router.post("/login", response_model=MeOut)
async def login(
    payload: LoginRequest,
    request: Request,
    redis: RedisDep,
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Any:
    # Before Argon2: a limited attempt costs nothing. Over the per-IP limit this is the 429. Under
    # it, one Redis step either finds the address in a wait (A2) or counts this attempt as a
    # failure in advance, so a burst cannot outrun the slow-down; a success below clears it.
    attempt = await rate_limit.enforce_login(redis, settings, request, email=payload.email)
    try:
        user = await auth_service.authenticate(
            db,
            email=payload.email,
            password=payload.password,
            settings=settings,
            slowed=attempt.slowed,
        )
    except InvalidCredentials as refused:
        # Ids and counts only: the matched live user's id, else null. Never the address, the
        # digest or the IP. A refusal during a wait is the same 401 as a wrong password and logs
        # one event of its own, not also `auth.login.failed`.
        if attempt.slowed:
            logger.info(
                "auth.login.slowed",
                user_id=refused.user_id,
                wait_left_seconds=attempt.wait_left_seconds,
                failures=attempt.failures,
            )
        else:
            logger.info(
                "auth.login.failed",
                user_id=refused.user_id,
                reason="invalid_credentials",
            )
        raise
    verified_hash = user.password_hash
    await db.commit()
    await db.refresh(user)

    agency = (
        await db.execute(select(Agency).where(Agency.id == user.agency_id))
    ).scalar_one()

    # The credentials were accepted (a refused attempt raised above and touched nothing): the
    # browser's older session ends now, before the new one is made (A3 part 3).
    await _end_presented_session(store, request.cookies.get(settings.session_cookie_key))
    token, _ = await store.create(user_id=user.id, agency_id=user.agency_id)
    # A password change that committed after the hash was verified has already
    # run its revoke, so this session would outlive it. Re-read the hash; if it
    # moved, kill the session just minted and refuse (counted, not refunded).
    # Also refused: a seat removed (or suspended) while this waited for the hash pool.
    #
    # The re-read takes a SHARE lock on the user row (A3 part 3, the review's L1). A credential
    # change holds that row from its write until its commit, and writes its cutoff in between. A
    # plain read during that time still sees the old hash, so a session minted after the change took
    # its cutoff would pass, and the cutoff (older than the session) would not catch it either. The
    # locked read waits for the commit and sees the new hash. A re-read that comes before the
    # change's write means this session was minted before the cutoff, which ends it. The route
    # holds no other row lock when it takes this one and asks for none after it, so it can wait
    # for a change but no change can wait for it in a cycle.
    current = (
        await db.execute(
            select(User.password_hash, User.status, User.deleted_at)
            .where(User.id == user.id)
            .with_for_update(read=True)
        )
    ).one_or_none()
    if (
        current is None
        or current.password_hash != verified_hash
        or current.status is not UserStatus.ACTIVE
        or current.deleted_at is not None
    ):
        await store.revoke(token)
        logger.info("auth.login.failed", user_id=user.id, reason="invalid_credentials")
        raise InvalidCredentials()
    # A failed login raised above and stays counted; a success is given back.
    await rate_limit.release_login(redis, settings, request, email=payload.email)
    logger.info("auth.login.succeeded", user_id=user.id, agency_id=user.agency_id)
    _set_session_cookie(response, token, settings)

    used, limit = await seat_service.seat_usage(db, user.agency_id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
        feedback_email=settings.feedback_email,
        scan_emails=_scan_emails_for(user, settings),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    principal: PrincipalDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Response:
    await store.revoke(principal.session_token)
    # Clear it on the Response that is returned: FastAPI does not merge the
    # injected `response`'s headers into one the route builds itself, so a
    # cookie cleared only on that one never reaches the browser.
    done = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(done, settings)
    return done


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    principal: PrincipalDep,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Response:
    """Revoke every session for the caller, on every device."""
    await store.revoke_all_for_user(principal.user_id)
    # And the cutoff (A3 part 3), so this does not depend on the Redis list the line above reads: a
    # session that list missed (evicted, or made between two revokes) is refused at its next
    # request. This session ends by it as well as by the revoke.
    await auth_service.set_session_cutoff(db, principal.user_id)
    await db.commit()
    # Clear it on the Response that is returned: FastAPI does not merge the
    # injected `response`'s headers into one the route builds itself, so a
    # cookie cleared only on that one never reaches the browser.
    done = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(done, settings)
    return done


@router.post("/change-password", response_model=MeOut)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    redis: RedisDep,
    response: Response,
    principal: PrincipalDep,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Any:
    """Change your own password while signed in. **Auth required.**

    The current password is re-verified server-side. A session proves somebody
    got in once; it does not prove they are still the account holder, and an
    unlocked laptop or a stolen cookie presents a perfectly valid one.

    EVERY OTHER SESSION IS REVOKED. THIS ONE IS NOT.
    ------------------------------------------------
    **This is not the reset flow's answer copied over.** `reset-password/confirm`
    revokes everything and leaves the caller signed OUT, because the person
    holding a reset link proved control of an inbox rather than knowledge of a
    password — they may be recovering from a compromise, they might not be the
    account holder at all, and making them sign in once with the new password
    confirms they hold it.

    Neither of those applies here. The caller just demonstrated knowledge of the
    current password, so the caller is not the suspect and signing them out of
    the session they are actively using would be friction with no security
    value — the same argument api-contracts.md already makes for signing a user
    in at sign-up.

    The OTHER sessions are a different question, and the answer is still revoke.
    The commonest reason someone changes a password while signed in is that they
    think somebody else has it; a change that left every other device alive
    would fail at the one job the user believed they were doing. `logout-all`
    exists for the explicit version, but requiring two deliberate actions to
    accomplish the obvious intent of one is a trap.

    So: `revoke_all_for_user`, then a FRESH session for this caller and a new
    cookie. Every other device is signed out; this one keeps working. The new
    token is minted rather than the old one spared, because "spare this digest"
    is a special case in the revocation path and a bulk revoke with an exception
    in it is the kind of code that later fails to revoke.

    **Unused reset links are retired too** (2026-10-11): a link asked for before the
    change no longer works, so one that leaked cannot undo it within its hour.

    **Errors:** `401 authentication-required` (no session), `401
    invalid-credentials` (wrong current password — no "no such user" branch
    exists, because the caller is authenticated), `422 validation-failed` for a
    new password that fails the SIGN-UP rules, that is the current one, or, while the
    breached-password check is on (`BREACHED_PASSWORD_CHECK_ENABLED`, off by default), that is on
    the list ("This password has appeared in a data breach. Choose a different one.", on the
    `newPassword` field, after the current password is proved), `429
    rate-limited` (10 failed attempts per account and 30 per IP per 15 minutes,
    counted before the password is verified; a success gives its attempt back),
    `503` (Redis down).
    """
    # Before Argon2: a limited attempt costs nothing, is not a timing oracle and
    # revokes nothing. Keyed on the signed-in account's email, HMAC-hashed. Safe
    # today because no route changes users.email; if an email-change route is
    # ever added, key on user.id instead or the budget resets with the address.
    await rate_limit.enforce(
        redis, settings, request, action="change_password", email=principal.user.email
    )
    user = await auth_service.change_password(
        db,
        user=principal.user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        settings=settings,
    )
    # The hash this request wrote, read inside its own transaction (the service
    # refreshed the row after its conditional UPDATE). Read after the commit, it
    # could already be a Google link's NULL, and the check below would pass.
    written_hash = user.password_hash
    # Revoke BEFORE the commit as well as after it (see below). The current
    # password is already verified here, so a wrong guess never reaches this
    # line. If Redis fails now the request is a 503 and nothing has changed.
    await store.revoke_all_for_user(principal.user_id)
    # The credential version (A3 part 3), last before the commit and from the clock the sessions
    # are stamped with: every session signed in before this instant ends with the password, even
    # one that both revokes miss. The replacement minted below is after the commit, so after it.
    await auth_service.set_session_cutoff(db, user.id)
    await db.commit()
    await db.refresh(user)

    agency = (
        await db.execute(select(Agency).where(Agency.id == user.agency_id))
    ).scalar_one()

    # Revoked twice on purpose. The pre-commit revoke means a Redis failure
    # leaves the password unchanged (503, retry works); if the commit then
    # fails, devices are signed out over an unchanged password, the safe
    # direction. This post-commit revoke catches a session minted in between;
    # the cutoff set above is the backstop that makes a failed or racing
    # revoke harmless for any session signed in before it.
    await store.revoke_all_for_user(user.id)
    token, _ = await store.create(user_id=user.id, agency_id=user.agency_id)
    # A Google link that committed after the write has cleared the hash; the
    # session just minted would belong to the earlier password-holder. Re-read
    # and, if the hash is no longer the one written, kill it and refuse. A SHARE lock, as in
    # `login`: a link in flight holds the row until it commits, and this waits for it.
    current_hash = (
        await db.execute(
            select(User.password_hash).where(User.id == user.id).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if current_hash is None or current_hash != written_hash:
        await store.revoke(token)
        raise InvalidCredentials(detail="That is not your current password.")
    # Only now, after the commit and the read-back, does a success give its
    # attempt back: a failed commit or a lost race refunds nothing.
    await rate_limit.release_change_password(
        redis, settings, request, email=principal.user.email
    )
    _set_session_cookie(response, token, settings)

    used, limit = await seat_service.seat_usage(db, user.agency_id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
        feedback_email=settings.feedback_email,
        scan_emails=_scan_emails_for(user, settings),
    )


@router.post(
    "/reset-password/request", response_model=ResetRequestOut, status_code=status.HTTP_200_OK
)
async def request_password_reset(
    payload: ResetPasswordRequest,
    request: Request,
    redis: RedisDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> Any:
    """Ask for a reset link. **Always `200`, and the same body for every address.**

    The response is byte-identical whether the address has an account, has a
    suspended one, or has never been seen. Any observable difference is an
    oracle for enumerating who banks here, which is precisely what
    `authenticate` already burns a dummy Argon2 hash to avoid on the login path.

    **It does differ by deployment, honestly** (2026-09-29): `emailConfigured`
    says whether this service can send email at all, and the `detail` says
    what that means. Neither field depends on the address.

    - **Email not configured:** nothing is looked up, minted or written, for any address, and
      nothing is sent. A link could not be delivered, and minting one would retire a live link the
      operator issued by hand (`scripts/issue_password_reset.py`). The `detail` is "Password reset
      by email is not available on this service yet, so no link was sent."
    - **Email configured:** the response is built from the request alone, so the work before it is
      the same for every address. The lookup, the mint and the send run in one background task
      after the response, in a session of its own. The `detail` is "If an account exists for that
      email, we've sent a link."

    The send is best-effort by construction: neither it nor the mint can change the status code.

    **Errors:** `429` when the per-email or per-IP limit is spent. The per-email
    limit trips identically for every address, so it reveals nothing about
    accounts. `422` if the body is not an email address.
    """
    await rate_limit.enforce(redis, settings, request, action="reset", email=payload.email)

    if not settings.email_configured:
        return ResetRequestOut(
            status="accepted",
            detail=RESET_EMAIL_OFF_DETAIL,
            email_configured=False,
        )

    # Everything that depends on the address happens after the response is sent.
    background.add_task(_mint_and_send_reset, payload.email, settings)
    return ResetRequestOut(
        status="accepted",
        detail=RESET_EMAIL_ON_DETAIL,
        email_configured=True,
    )


async def _mint_and_send_reset(email: str, settings: Settings) -> None:
    """The reset work for one address, run after the response, in its own session.

    The service call is the one `scripts/issue_password_reset.py` makes, so the retire of live
    links only (`expires_at > now`) and the per-user advisory try-lock are the same here. The
    commit comes before the send: a token that reaches an inbox but not the database is a link
    that fails. Nothing here raises into the server and nothing logs the address.
    """
    try:
        factory = get_sessionmaker(settings)
        async with factory() as session:
            minted = await reset_service.request_reset(session, email=email, settings=settings)
            await session.commit()
        # The connection is back in the pool before the provider is called, so a slow provider
        # holds no database connection. (`expire_on_commit=False`: `user` stays readable.)
        if minted is None:
            return
        user, token = minted
        await email_service.send_password_reset(
            user.email,
            reset_service.reset_url(settings, token),
            user_id=user.id,
            settings=settings,
        )
    except Exception as exc:
        # The class only: a driver message can carry bound values.
        logger.warning("password_reset.request.background_failed", error=type(exc).__name__)


@router.post("/reset-password/confirm", status_code=status.HTTP_200_OK)
async def confirm_password_reset(
    payload: ResetPasswordConfirm,
    response: Response,
    redis: RedisDep,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> dict[str, str]:
    """Redeem a reset link and set the new password.

    **Every existing session is revoked.** A reset is what someone does when
    they believe the account is compromised, so leaving the attacker's session
    alive would defeat the exercise. `logout-all` already exists for the
    deliberate version of this; here it is not optional.

    The caller is NOT signed in afterwards, deliberately — unlike sign-up. The
    person holding this link proved control of an inbox, not knowledge of the
    old password, and making them sign in once with the new one confirms they
    have it.

    **Errors:** `400 invalid-reset-token`, always the same status, type and title. Its
    `detail` is "This link has already been used. If you just set a new password, sign in with
    it. Otherwise, request a new link." for a link that was redeemed, retired by a newer
    request, a password change, a Google link or a seat removal, or is being redeemed by
    another request right now; for an unknown, expired or belonging-to-an-inactive-user link it
    is "That reset link is not valid. Links work once and expire after an hour."

    `422 validation-failed` for a new password that fails the sign-up rules or, while the
    breached-password check is on (`BREACHED_PASSWORD_CHECK_ENABLED`, off by default), is on the
    list ("This password has appeared in a data breach. Choose a different one.", on the
    `newPassword` field). The list is checked only for a link that is still good, and a refusal
    leaves the link unused.
    """
    try:
        user = await reset_service.confirm_reset(
            db,
            redis=redis,
            token=payload.token,
            new_password=payload.new_password,
            settings=settings,
        )
    except reset_service.ResetLinkUsedError:
        await db.rollback()
        raise InvalidResetToken(detail=RESET_LINK_USED) from None
    except reset_service.ResetTokenError:
        await db.rollback()
        raise InvalidResetToken(
            detail="That reset link is not valid. Links work once and expire after an hour."
        ) from None

    # Revoke before the commit as well as after it: if Redis fails here the
    # request is a 503 with the password unchanged and the reset link not yet
    # consumed (the session_scope rollback discards it), so the retry works.
    # The post-commit revoke catches a session minted in between. The cutoff
    # (A3 part 3), set last before the commit, ends every session signed in
    # before it even if both revokes miss one; the caller is signed out too.
    await store.revoke_all_for_user(user.id)
    await auth_service.set_session_cutoff(db, user.id)
    await db.commit()
    await store.revoke_all_for_user(user.id)
    logger.info("password_reset.confirm.succeeded", user_id=user.id)
    # Best effort: the person proved control of the inbox, so forget the failed
    # logins that may have locked them out. Never changes the reply.
    await rate_limit.clear_login(redis, settings, email=user.email)
    _clear_session_cookie(response, settings)
    return {
        "status": "reset",
        "detail": "Your password has been changed. Sign in with the new one.",
    }


@router.post("/invitations/accept", response_model=MeOut)
async def accept_invitation(
    payload: AcceptInvitationRequest,
    response: Response,
    redis: RedisDep,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Any:
    """Redeem a seat invitation, set a password, and sign in. **No auth.**

    Unauthenticated by necessity: the person holding this link has no account
    to authenticate with yet. That is what the link is for.

    **This signs them in — unlike the reset path, and for the reason sign-up
    gives.** `reset-password/confirm` deliberately leaves the caller signed out
    because they proved control of an inbox rather than knowledge of a
    password, and one deliberate sign-in confirms they hold the new one. There
    is no prior state to protect here: this is a first password on a seat that
    has not been used since it was issued, exactly the situation api-contracts.md
    already calls "friction with no security value" on the sign-up path. (A seat
    that was removed and invited again is the same user row, but whatever its
    earlier holder signed in with was ended when it was removed and again when it
    was invited, so none of it survives this accept.)

    **`200`, not `201`.** Nothing is created. The `User` row was inserted when
    the invitation was sent and has occupied a seat ever since; this fills it.

    **Errors:** `400 invalid-invitation` for unknown, expired, already-accepted,
    revoked, and pointing-at-a-seat-that-has-since-been-removed alike — one
    response, no branch that says which. `422` for a password that fails the
    same rules sign-up applies or, while the breached-password check is on
    (`BREACHED_PASSWORD_CHECK_ENABLED`, off by default), is on the list ("This password has
    appeared in a data breach. Choose a different one.", on the `password` field). The list is
    checked only for an invitation that is still good, and a refusal leaves it open.
    """
    try:
        user = await invitation_service.accept_invitation(
            db,
            redis=redis,
            token=payload.token,
            full_name=payload.full_name,
            password=payload.password,
            settings=settings,
        )
    except invitation_service.InvitationTokenError:
        await db.rollback()
        raise InvalidInvitation(
            detail=(
                "That invitation link is not valid. Links work once and expire "
                "after seven days. Ask whoever invited you to send a new one."
            )
        ) from None

    # The hash this accept wrote, read inside its own transaction (see change_password).
    written_hash = user.password_hash
    await db.commit()
    await db.refresh(user)

    agency = (
        await db.execute(select(Agency).where(Agency.id == user.agency_id))
    ).scalar_one()

    token, _ = await store.create(user_id=user.id, agency_id=user.agency_id)
    # A Google link that committed after this accept has cleared the password; the session
    # just minted would belong to the earlier password-holder. Re-read and refuse. A SHARE lock, as
    # in `login`: a link in flight holds the row until it commits, and this waits for it.
    current = (
        await db.execute(
            select(User.password_hash, User.google_sub)
            .where(User.id == user.id)
            .with_for_update(read=True)
        )
    ).one_or_none()
    if current is None or current.google_sub is not None or current.password_hash != written_hash:
        await store.revoke(token)
        raise InvalidInvitation(
            detail=(
                "That invitation link is not valid. Links work once and expire "
                "after seven days. Ask whoever invited you to send a new one."
            )
        )
    _set_session_cookie(response, token, settings)

    used, limit = await seat_service.seat_usage(db, user.agency_id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
        feedback_email=settings.feedback_email,
        scan_emails=_scan_emails_for(user, settings),
    )


# --- Google sign-in — Epic 20 ------------------------------------------------
#
# Four routes. The two GETs are browser navigations, not API calls: the button
# is an `<a href>` to /start, Google sends the browser back to /callback, and
# both answer with a 302 rather than JSON because there is no script on the
# other end to read one. The cookie rides on the callback's redirect, which
# is how the web app finds itself signed in when it lands.
#
# The policy — what happens when the email is known — lives in
# `services/google_oauth.py`, not here. These routes move the browser and set
# the cookie.


def _google_failure(settings: Settings, reason: str) -> RedirectResponse:
    """Send the browser back to the front door with one word about why.

    Every failure lands on the same page with a `reason` the web app maps to
    a sentence. No token, no email, and no Google error text in the URL: a
    reason is a word this product chose, and the log carries the rest.
    """
    base = settings.public_web_base_url.rstrip("/")
    return RedirectResponse(f"{base}/?google=error&reason={reason}", status_code=302)


# Why a request to `/google/start` is not a person's own navigation (hardening A6 part 2, fix
# round, security review M1). A closed set: the log carries one of these words and never a
# header's raw value.
NotNavigationCause = Literal["cross-site", "not-navigate", "not-document"]


def _not_a_navigation(request: Request) -> NotNavigationCause | None:
    """Why this request is NOT a person's top-level navigation, by the browser's own headers.

    A browser attaches `Sec-Fetch-Site`, `Sec-Fetch-Mode` and `Sec-Fetch-Dest` to every request it
    makes, and a page's script can neither set nor change them (the `Sec-` prefix is forbidden to
    it), so they say how the request came about whatever the page that caused it wanted. Pressing
    the button is a top-level navigation from one of the web app's own pages, or to an address
    typed or bookmarked: `Sec-Fetch-Site` is `same-origin`, `same-site` or `none`, the mode is
    `navigate` and the destination is `document`. Anything else (another site's page, an image, a
    `fetch`, a frame) is not, and without this check it would spend the ceiling on Google sign-in
    starts for everyone. Checked in this order, so one request has one cause: from another site;
    not a navigation; not into a document.

    A header that is absent or empty is no evidence either way (an older browser, a script): such
    a request is treated as it was before this check existed.
    """

    def header(name: str) -> str:
        return request.headers.get(name, "").strip().lower()

    if header("sec-fetch-site") == "cross-site":
        return "cross-site"
    mode = header("sec-fetch-mode")
    if mode and mode != "navigate":
        return "not-navigate"
    dest = header("sec-fetch-dest")
    if dest and dest != "document":
        return "not-document"
    return None


def _front_door(settings: Settings) -> RedirectResponse:
    """Back to the front door with nothing in the address: no reason word, so no sentence."""
    return RedirectResponse(f"{settings.public_web_base_url.rstrip('/')}/", status_code=302)


_TICKET_REFUSAL = "That Google sign-in can no longer be used. Start again from the sign-in page."


def _ticket_bound_to(cookie: str | None, binding_digest: str | None) -> bool:
    """Is this the browser the ticket was issued to? Logs the cause, never a value."""
    if cookie is None:
        cause = "missing"
    elif binding_digest is None:
        cause = "unbound"
    elif not hmac.compare_digest(token_digest(cookie), binding_digest):
        cause = "different"
    else:
        return True
    logger.warning("google.ticket.browser_mismatch", cause=cause)
    return False


def _sets_flow_cookie(response: Response, settings: Settings) -> bool:
    prefix = f"{settings.google_flow_cookie_key}="
    return any(h.startswith(prefix) for h in response.headers.getlist("set-cookie"))


def _set_google_flow_cookie(response: Response, binding: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.google_flow_cookie_key,
        value=binding,
        # The Redis state expires first, so an expired flow reads as expired,
        # not as a mismatch.
        max_age=settings.google_sign_in_ttl_seconds + 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        # Lax: it must ride on the top-level GET back from accounts.google.com.
        samesite="lax",
        path="/",
    )


def _clear_google_flow_cookie(response: Response, settings: Settings) -> None:
    # Same Secure and Path=/ as the cookie it removes, or a browser ignores a
    # `__Host-` deletion.
    response.delete_cookie(
        key=settings.google_flow_cookie_key,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.get("/google/start", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
async def google_start(
    request: Request,
    settings: SettingsDep,
    redis: RedisDep,
    flow: GoogleFlowStoreDep,
    google: GoogleProviderDep,
) -> RedirectResponse:
    """Send the browser to Google. **No auth.** A navigation, not an API call.

    Mints a `state`, a `nonce` and a PKCE verifier, parks them in Redis for
    ten minutes, and redirects to Google's authorization endpoint with the
    state, the nonce and the S256 challenge. The state is looked up
    server-side; the flow cookie set here, whose digest is parked with it,
    ties it to this browser and is compared at the callback.

    **Errors:** `503 google-sign-in-not-configured` when the deployment has
    no OAuth client. The web app shows the button regardless; this is what
    it gets until the founder creates one.

    **Only a person's own navigation is counted.** A request whose Fetch
    Metadata headers show that it is not a top-level navigation (it is
    cross-site, or its mode is not `navigate`, or its destination is not
    `document`) is answered with a plain 302 to `/` before anything is counted,
    stored or set, and a request without those headers is treated as before.

    **Rate limited per IP, and for the whole deployment, before anything is
    stored.** Past the per-address limit (30 starts in 15 minutes by default)
    the answer is a 302 to `/?google=error&reason=rate-limited`. Past the
    ceiling on starts admitted from all addresses together (300 in 15 minutes
    by default) it is a 302 to `reason=busy`; that ceiling is checked first, so
    a refusal there stores nothing and sets no cookie. When Redis cannot say,
    it is `reason=unavailable`. These are redirects, like the callback's
    refusals, because a person is navigating here and not a script. Password
    sign-in is not affected by either limit.
    """
    if not settings.google_sign_in_configured:
        raise GoogleSignInNotConfigured(
            detail=(
                "Google sign-in needs GOOGLE_OAUTH_CLIENT_ID, "
                "GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URL. "
                "Sign in with your email and password instead."
            )
        )
    cause = _not_a_navigation(request)
    if cause is not None:
        logger.warning("auth.google_start.not_navigation", cause=cause)
        return _front_door(settings)
    verdict = await rate_limit.hit_google_start(redis, settings, request)
    if verdict == "busy":
        return _google_failure(settings, "busy")
    if verdict == "limited":
        return _google_failure(settings, "rate-limited")
    if verdict == "unavailable":
        return _google_failure(settings, "unavailable")
    state, nonce, challenge, binding = await flow.begin()
    redirect = RedirectResponse(
        google.authorization_url(state=state, nonce=nonce, code_challenge=challenge),
        status_code=status.HTTP_302_FOUND,
    )
    _set_google_flow_cookie(redirect, binding, settings)
    return redirect


@router.get(
    "/google/callback", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse
)
async def google_callback(
    request: Request,
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
    flow: GoogleFlowStoreDep,
    google: GoogleProviderDep,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Where Google sends the browser back. **No auth.** Always a 302.

    In order: the state must be one this process minted and not yet used;
    the flow cookie must be the one `/start` set in this browser (else
    `browser-mismatch`); the code is exchanged and the ID token verified (signature, audience,
    issuer, expiry); the token's nonce must be the state's; then the
    linking policy decides. Four destinations:

    - an existing account → the session cookie is set and the browser goes
      to `/dashboard`. When this sign-in LINKS a password account to Google
      for the first time, that account's password is removed and every
      session it already held is revoked first. If the session store cannot
      be reached before the link commits, the sign-in fails as `unavailable`
      and nothing is changed; if it fails after the commit, the link stands
      (the password is gone and the earlier sessions were revoked before the
      commit), the warning `google.link.post_commit_revoke_failed` is logged,
      and the sign-in still ends as `unavailable`;
    - a new address → a ten-minute ticket is minted and the browser goes to
      `/sign-up/google#ticket=…` (the fragment, never the query, so it is in
      no request log and no Referer) to name the agency;
    - an address with an invited seat and a live invitation → **nothing is activated, linked
      or signed in, and nothing is written.** The same kind of ticket is minted, bound to this
      browser like a new address's, and it also names the invitation; the browser goes to the
      same page, which asks whether to join that agency or start the person's own
      (`POST /auth/google/join`, `POST /auth/google/decline`). Only an address Google vouched for
      gets this, and an invited seat whose invitation has expired or been revoked is refused
      `invitation-expired` with no offer;
    - anything else → `/?google=error&reason=…` with one of `not-configured`,
      `denied`, `invalid-state`, `browser-mismatch`, `exchange-failed`, `email-unverified`,
      `account-suspended`, `email-claimed`, `invitation-expired`,
      `account-unavailable`, or `unavailable` when something this route
      depends on — Redis, Postgres, the session store — failed underneath it
      (2026-09-21; until then that was the one way this route showed a raw
      500 page).

    **Errors:** none as status codes. A person arriving here is not a script
    and gets a page, not a problem document.
    """
    # Every answer from here clears the flow cookie, on the response actually
    # returned (headers set on the injected `response` are not merged into one
    # the route builds itself).
    result: RedirectResponse
    if not settings.google_sign_in_configured:
        result = _google_failure(settings, "not-configured")
    elif state is None:
        result = _google_failure(settings, "invalid-state")
    else:
        try:
            result = await _google_callback(
                response,
                db,
                store,
                settings,
                flow,
                google,
                state=state,
                code=code,
                error=error,
                flow_cookie=request.cookies.get(settings.google_flow_cookie_key),
                session_cookie=request.cookies.get(settings.session_cookie_key),
            )
        except Exception:  # noqa: BLE001 - a person is standing here, not a script
            logger.exception("google.callback.failed")
            with contextlib.suppress(Exception):  # the connection may be what failed
                await db.rollback()
            result = _google_failure(settings, "unavailable")
    if not _sets_flow_cookie(result, settings):
        _clear_google_flow_cookie(result, settings)
    return result


async def _google_callback(
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: Settings,
    flow: GoogleFlowStoreDep,
    google: GoogleProviderDep,
    *,
    state: str,
    code: str | None,
    error: str | None,
    flow_cookie: str | None,
    session_cookie: str | None,
) -> RedirectResponse:
    """The callback proper; `google_callback` wraps it so nothing escapes as a 500.

    `session_cookie` is the session cookie the browser sent, if any: the older session this
    sign-in ends before it makes the new one (`_end_presented_session`).
    """
    parked = await flow.consume_state(state)
    if parked is None:
        return _google_failure(settings, "invalid-state")
    # The state is spent; now it must have been started by THIS browser. A
    # record parked before the binding existed fails closed.
    cause: str | None = None
    if flow_cookie is None:
        cause = "missing"
    elif parked.binding_digest is None:
        cause = "unbound"
    elif not hmac.compare_digest(token_digest(flow_cookie), parked.binding_digest):
        cause = "different"
    if cause is not None:
        logger.warning("google.callback.browser_mismatch", cause=cause)
        return _google_failure(settings, "browser-mismatch")
    if error is not None or code is None:
        # `access_denied` is the person pressing Cancel on Google's screen.
        return _google_failure(settings, "denied")

    try:
        identity = await google.exchange(code=code, code_verifier=parked.code_verifier)
    except google_oauth.GoogleExchangeError:
        return _google_failure(settings, "exchange-failed")
    if identity.nonce is None or identity.nonce != parked.nonce:
        return _google_failure(settings, "invalid-state")

    try:
        resolved = await google_oauth.resolve_identity(db, identity, settings=settings)
    except google_oauth.GoogleSignInRefusedError as refused:
        await db.rollback()
        return _google_failure(settings, refused.reason)

    base = settings.public_web_base_url.rstrip("/")
    if resolved is None or resolved.offer is not None:
        # A new address is asked for an agency's name; an invited one is asked whether to join the
        # agency that invited it (3.126). Either way NOTHING has been written and nobody is signed
        # in: the identity waits in the ticket, and the person's answer is the next request.
        ticket = await flow.issue_ticket(
            identity, parked.binding_digest, None if resolved is None else resolved.offer
        )
        # A fragment is never sent to a server: the ticket stays out of the web
        # host's request log, browser history as a query, and every Referer.
        to_sign_up = RedirectResponse(f"{base}/sign-up/google#ticket={ticket}", status_code=302)
        # The ticket is bound to this browser: keep the flow cookie (renewed)
        # for the completion calls, which clear it.
        assert flow_cookie is not None  # the binding was compared above
        _set_google_flow_cookie(to_sign_up, flow_cookie, settings)
        return to_sign_up

    token = await _sign_in_resolved(db, store, resolved, session_cookie=session_cookie)
    redirect = RedirectResponse(f"{base}/dashboard", status_code=status.HTTP_302_FOUND)
    _set_session_cookie(redirect, token, settings)
    return redirect


async def _sign_in_resolved(
    db: DbDep,
    store: SessionStoreDep,
    resolved: google_oauth.ResolvedIdentity,
    *,
    session_cookie: str | None,
) -> str:
    """Commit what `resolved` changed, end what a link ends, and mint the session: its token.

    The one place a Google sign-in of an existing account is finished, whichever way it got here:
    the callback (an account that is already active) and the Join step (an invited seat whose
    person said yes). The caller turns the token into its cookie and its answer.
    """
    user = resolved.user
    revoked = 0
    if resolved.linked:
        # The link removed the password; every session the row already holds
        # goes too, before the commit and again after it (the change-password
        # pattern). A Redis failure here raises, and the caller rolls back and
        # tells the person it did not work: nothing is committed and no new
        # session exists.
        revoked = await store.revoke_all_for_user(user.id)
        # The credential version (A3 part 3, 3.125), last before the commit: a session minted
        # between this revoke and the commit is older than it, so it ends even if the revoke after
        # the commit fails. Set for every link, an invited seat's included, where it ends nothing.
        await auth_service.set_session_cutoff(db, user.id)
    user_id, agency_id = user.id, user.agency_id
    await db.commit()
    if resolved.linked:
        # Logged first: the link is committed whatever happens next. Counts are
        # those of the pre-commit pass.
        logger.info(
            "google.linked",
            user_id=user_id,
            password_cleared=resolved.password_cleared,
            sessions_revoked=revoked,
            invitations_revoked=resolved.invitations_revoked,
        )
        try:
            await store.revoke_all_for_user(user_id)
            if resolved.password_cleared:
                # An in-flight request on an old session may have sent one since.
                await google_oauth.revoke_pending_invitations_sent_by(db, user_id)
                await db.commit()
        except Exception:
            logger.warning("google.link.post_commit_revoke_failed", user_id=user_id)
            raise
    await _end_presented_session(store, session_cookie)
    token, _ = await store.create(user_id=user_id, agency_id=agency_id)
    return token


@router.post("/google/pending", response_model=GooglePendingOut)
async def google_pending(
    payload: GooglePendingRequest,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    flow: GoogleFlowStoreDep,
) -> Any:
    """What the completion page shows before asking for an agency name. **No auth.**

    A POST so the ticket rides in the body: it is a bearer credential with a
    session token's entropy, and a URL is logged. It is not a mutation: the
    ticket is read without being consumed, so asking twice is not a second
    sign-in. The web page removes the ticket from the address bar once read
    (like invite and reset), so reloading that page does not bring it back and
    the person starts again. `GET` with a `?ticket=` is `405`.

    The ticket is bound to the browser that signed in: the flow cookie must
    come with it.

    **`invitedTo`** is the name of the agency whose invitation is waiting for this address, and
    null for a new address. When it is set, the page asks whether to join that agency
    (`POST /auth/google/join`) or start the person's own (`POST /auth/google/decline`) instead of
    asking for an agency's name. It is given only while the invitation can still be accepted (the
    seat is still an invited one and not removed, and the invitation is neither used, revoked nor
    expired); after that the ticket is refused like any other that can no longer be used.

    **Errors:** `400 invalid-google-ticket` for unknown, used, expired and
    another browser's alike.
    """
    peeked = await flow.peek_ticket(payload.ticket)
    if peeked is None or not _ticket_bound_to(
        request.cookies.get(settings.google_flow_cookie_key), peeked.binding_digest
    ):
        raise InvalidGoogleTicket(detail=_TICKET_REFUSAL)
    invited_to: str | None = None
    if peeked.offer is not None:
        invited_to = await google_oauth.invitation_agency_name(db, peeked.identity, peeked.offer)
        if invited_to is None:
            raise InvalidGoogleTicket(detail=_TICKET_REFUSAL)
    return GooglePendingOut(
        email=peeked.identity.email, suggested_name=peeked.identity.name, invited_to=invited_to
    )


async def _consume_offer_ticket(
    request: Request, settings: Settings, flow: GoogleFlowStoreDep, ticket: str
) -> tuple[google_oauth.Ticket, google_oauth.InvitationOffer]:
    """Spend a ticket at one of the two answers to "Join, or start your own agency?" (3.126).

    Spent first, as ever, and then it must be this browser's and must carry an invitation: a new
    address's ticket has nothing to join and is refused here, as an invited address's is refused by
    `/complete`. One refusal for all of it. The invitation is the ticket's own, written by the
    callback; nothing in the request names one.
    """
    consumed = await flow.consume_ticket(ticket)
    if (
        consumed is None
        or consumed.offer is None
        or not _ticket_bound_to(
            request.cookies.get(settings.google_flow_cookie_key), consumed.binding_digest
        )
    ):
        raise InvalidGoogleTicket(detail=_TICKET_REFUSAL)
    return consumed, consumed.offer


@router.post("/google/join", response_model=MeOut)
async def google_join(
    payload: GoogleChoiceRequest,
    request: Request,
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
    flow: GoogleFlowStoreDep,
) -> Any:
    """Join the agency whose invitation is waiting, and sign in. **No auth.**

    The answer "Join" to "You've been invited to join {agency}. Join, or start your own agency?".
    The ticket is the one the callback parked for an invited address (the peek says which agency),
    spent here, so one ticket makes one choice. Its invitation is the ticket's own; nothing in the
    request names one.

    This is the activation the callback made before the person was asked, made now on their say-so
    and under the same locks (the invitation row, then the user row) and the same second look at
    everything: the seat is made active, the invitation stamped accepted, Google's name takes over
    the placeholder, and Google is linked. A seat a password accept activated first is not activated
    again and is linked as any account is, which removes its password. A link ends every session
    the seat already holds before the new one is made, as the callback's link does; the session
    cookie is set and the flow cookie cleared. `200`, not `201`: the seat already existed.

    **Errors:** `400 invalid-google-ticket` for a ticket that is unknown, used, expired, another
    browser's or a new address's (which has nothing to join), and also when the seat cannot be
    joined any more: its invitation was revoked (and not sent again) or has expired, or the seat
    was removed, suspended or linked to another Google account, since the person signed in. One
    answer for all of them, and signing in with Google again says which. Nothing is changed in
    any of those cases. A seat that is already active is not refused: a password accept landed
    first, or the person joined in another tab, and it is linked as any account is (`200`).
    `503 service-unavailable` when the session store cannot be reached: before the commit
    nothing is changed (an invited seat is still invited), and after it the join stands, no
    session is made, and the next Google sign-in signs the person in. `422`.
    """
    try:
        consumed, offer = await _consume_offer_ticket(request, settings, flow, payload.ticket)
        try:
            resolved = await google_oauth.join_invitation(
                db, consumed.identity, offer, settings=settings
            )
        except google_oauth.GoogleSignInRefusedError as refused:
            logger.info("google.invitation_choice.refused", choice="join", reason=refused.reason)
            raise InvalidGoogleTicket(detail=_TICKET_REFUSAL) from None
    except ProblemError as problem:
        await db.rollback()
        refusal = _problem_response(request, problem)
        _clear_google_flow_cookie(refusal, settings)
        return refusal

    token = await _sign_in_resolved(
        db, store, resolved, session_cookie=request.cookies.get(settings.session_cookie_key)
    )
    user = resolved.user
    await db.refresh(user)
    agency = (await db.execute(select(Agency).where(Agency.id == user.agency_id))).scalar_one()
    _set_session_cookie(response, token, settings)
    _clear_google_flow_cookie(response, settings)

    used, limit = await seat_service.seat_usage(db, agency.id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
        feedback_email=settings.feedback_email,
        scan_emails=_scan_emails_for(user, settings),
    )


@router.post("/google/decline", response_model=GoogleTicketOut)
async def google_decline(
    payload: GoogleChoiceRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    flow: GoogleFlowStoreDep,
) -> Any:
    """Turn the invitation down and go on to start the person's own agency. **No auth.**

    The answer "Start your own agency" to "You've been invited to join {agency}. Join, or start
    your own agency?". The ticket is the one the callback parked for an invited address, spent
    here, so one ticket makes one choice. Nobody is signed in and no agency is made.

    **The invitation is revoked and the address is freed**, under the same locks as joining.
    A seat that was never used is deleted, so nothing remains of it: it is gone from the inviting
    agency's seat list and its invitation list, and the agency has gained no member. "Never used"
    means nothing records a life for it: no sign-in is recorded, it holds no password or Google
    link, no invitation for its address in the agency was ever accepted, and no scan, prompt run,
    client rescan or invitation refers to it. A seat that belonged to a member who was removed and
    invited again is not deleted, because their history points at it: it goes back to being
    removed. The address is then still held, as every address that has had a seat here is, and
    the new-address step refuses it with the `409` it gives any such address.

    The answer is a NEW ticket for the same verified identity and the same browser, with no
    invitation in it. The page keeps the flow cookie and goes on to ask for the agency's name, and
    `POST /auth/google/complete` creates the agency with this person as its owner.

    **Errors:** `400 invalid-google-ticket` for a ticket that is unknown, used, expired, another
    browser's or a new address's (which has nothing to withdraw), and also when the seat is no
    longer an invited one: it has become an active member's (a password accept landed first, or the
    person joined in another tab), or it is suspended or linked to another Google account. Nothing
    is changed in any of those cases. It is not refused when the invitation was revoked or has
    expired since the person signed in (the seat is freed and the invitation stamped revoked all
    the same), nor when the seat was removed or deleted (nothing is left to free, nothing is
    written, and the answer is still `200` with a new ticket). `503 service-unavailable` when
    Redis cannot be reached: before the commit nothing is changed; after it what the decline
    decided stands, as described above, no new ticket is made, and the person starts again from the
    sign-in page. `422`.
    """
    try:
        consumed, offer = await _consume_offer_ticket(request, settings, flow, payload.ticket)
        try:
            row_deleted = await google_oauth.decline_invitation(
                db, consumed.identity, offer, settings=settings
            )
        except google_oauth.GoogleSignInRefusedError as refused:
            logger.info("google.invitation_choice.refused", choice="decline", reason=refused.reason)
            raise InvalidGoogleTicket(detail=_TICKET_REFUSAL) from None
        await db.commit()
    except ProblemError as problem:
        await db.rollback()
        refusal = _problem_response(request, problem)
        _clear_google_flow_cookie(refusal, settings)
        return refusal

    logger.info(
        "invitation.declined",
        agency_id=offer.agency_id,
        user_id=offer.user_id,
        row_deleted=row_deleted,
    )
    ticket = await flow.issue_ticket(consumed.identity, consumed.binding_digest)
    # The new ticket is bound to this browser like the one it replaces, and the step that spends it
    # needs the flow cookie: keep it, renewed, as the callback does for a new address.
    flow_cookie = request.cookies.get(settings.google_flow_cookie_key)
    assert flow_cookie is not None  # the binding was compared above
    _set_google_flow_cookie(response, flow_cookie, settings)
    return GoogleTicketOut(ticket=ticket)


@router.post(
    "/google/complete", response_model=MeOut, status_code=status.HTTP_201_CREATED
)
async def google_complete(
    payload: CompleteGoogleSignUpRequest,
    request: Request,
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
    flow: GoogleFlowStoreDep,
) -> Any:
    """Create the agency and its owner from a verified Google identity. **No auth.**

    The Google half of `/sign-up`: same agency, same owner seat, same
    sign-in-on-creation, no password. The ticket is consumed here, so the
    page it came from cannot create two agencies.

    **Errors:** `400 invalid-google-ticket` (also for an invited address's ticket, which is answered
    at `/join` or `/decline`), `409 email-already-registered`
    (the address was registered in the minutes since the ticket was minted, or it has had a seat
    here), `422`.
    """
    # The ticket is spent first, as ever; then it must be this browser's. A
    # refusal is built here as a problem document so the flow cookie can be
    # cleared on the response actually sent.
    try:
        consumed = await flow.consume_ticket(payload.ticket)
        if (
            consumed is None
            # An invited address's ticket is answered at `/join` or `/decline`: its address is
            # held by the invited seat until the person has said which, and this creates nothing
            # from it.
            or consumed.offer is not None
            or not _ticket_bound_to(
                request.cookies.get(settings.google_flow_cookie_key), consumed.binding_digest
            )
        ):
            raise InvalidGoogleTicket(detail=_TICKET_REFUSAL)
        agency, user = await google_oauth.complete_sign_up(
            db,
            consumed.identity,
            agency_name=payload.agency_name,
            full_name=payload.full_name,
            settings=settings,
        )
        await db.commit()
    except ProblemError as problem:
        await db.rollback()
        refused = _problem_response(request, problem)
        _clear_google_flow_cookie(refused, settings)
        return refused
    await db.refresh(agency)
    await db.refresh(user)

    await _end_presented_session(store, request.cookies.get(settings.session_cookie_key))
    token, _ = await store.create(user_id=user.id, agency_id=agency.id)
    _set_session_cookie(response, token, settings)
    _clear_google_flow_cookie(response, settings)

    used, limit = await seat_service.seat_usage(db, agency.id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
        feedback_email=settings.feedback_email,
        scan_emails=_scan_emails_for(user, settings),
    )


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, db: DbDep, settings: SettingsDep) -> Any:
    """The signed-in context, in one call. **Auth required.**

    `feedbackEmail` (2026-10-06) is `FEEDBACK_EMAIL`, null when it is unset: the address the
    shell's "Send feedback" link writes to. It is on this response, and on the other responses
    that return the same context to someone who has just signed in, and on no other.
    """
    used, limit = await seat_service.seat_usage(db, principal.agency_id)
    return MeOut(
        user=UserOut.model_validate(principal.user),
        agency=AgencyOut.model_validate(principal.agency),
        seats=SeatUsageOut(used=used, limit=limit),
        feedback_email=settings.feedback_email,
        scan_emails=_scan_emails_for(principal.user, settings),
    )


@router.put("/me/scan-emails", response_model=ScanEmailsOut)
async def set_scan_emails(
    payload: ScanEmailsRequest,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
) -> Any:
    """Turn the emails sent when a scan you started finishes on or off. **Auth required.**

    **Only your own choice.** There is no user id anywhere in the request: the row written is the
    caller's, matched by user id AND agency id, and a body that names anything but `enabled` is a
    `422`. Accepted whether or not the feature is on (`available` in the answer says), because it
    writes one boolean nothing reads while the feature is off. Idempotent.

    **Errors:** `401 authentication-required`, `422 validation-failed` (not a JSON true or false).
    """
    row = (
        await db.execute(
            update(User)
            .where(User.id == principal.user_id, User.agency_id == principal.agency_id)
            .values(scan_emails=payload.enabled)
            .returning(User.scan_emails)
        )
    ).scalar_one_or_none()
    await db.commit()
    if row is None:
        raise AuthenticationRequired(detail="This account is no longer active.")
    return ScanEmailsOut(available=settings.scan_emails_available, enabled=row)

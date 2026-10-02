"""Signup, login, logout, and the authenticated-context endpoint."""

from __future__ import annotations

import contextlib
import hmac
from typing import Any

import structlog
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..config import Settings
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
    GoogleSignInNotConfigured,
    InvalidCredentials,
    InvalidGoogleTicket,
    InvalidInvitation,
    InvalidResetToken,
    ProblemError,
    _problem_response,
)
from ..models import Agency, User
from ..schemas.auth import (
    AcceptInvitationRequest,
    AgencyOut,
    ChangePasswordRequest,
    CompleteGoogleSignUpRequest,
    GooglePendingOut,
    GooglePendingRequest,
    LoginRequest,
    MeOut,
    ResetPasswordConfirm,
    ResetPasswordRequest,
    ResetRequestOut,
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

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_key,
        value=token,
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
    """
    # Before the "already registered" 409, so that answer cannot be probed
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
    # Before Argon2: a limited attempt costs nothing.
    await rate_limit.enforce(redis, settings, request, action="login", email=payload.email)
    user = await auth_service.authenticate(
        db, email=payload.email, password=payload.password, settings=settings
    )
    verified_hash = user.password_hash
    await db.commit()
    await db.refresh(user)

    agency = (
        await db.execute(select(Agency).where(Agency.id == user.agency_id))
    ).scalar_one()

    token, _ = await store.create(user_id=user.id, agency_id=user.agency_id)
    # A password change that committed after the hash was verified has already
    # run its revoke, so this session would outlive it. Re-read the hash; if it
    # moved, kill the session just minted and refuse (counted, not refunded).
    current_hash = (
        await db.execute(select(User.password_hash).where(User.id == user.id))
    ).scalar_one_or_none()
    if current_hash != verified_hash:
        await store.revoke(token)
        raise InvalidCredentials()
    # A failed login raised above and stays counted; a success is given back.
    await rate_limit.release_login(redis, settings, request, email=payload.email)
    _set_session_cookie(response, token, settings)

    used, limit = await seat_service.seat_usage(db, user.agency_id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
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
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Response:
    """Revoke every session for the caller, on every device."""
    await store.revoke_all_for_user(principal.user_id)
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

    **Errors:** `401 authentication-required` (no session), `401
    invalid-credentials` (wrong current password — no "no such user" branch
    exists, because the caller is authenticated), `422 validation-failed` for a
    new password that fails the SIGN-UP rules or that is the current one, `429
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
    # Revoke BEFORE the commit as well as after it (see below). The current
    # password is already verified here, so a wrong guess never reaches this
    # line. If Redis fails now the request is a 503 and nothing has changed.
    await store.revoke_all_for_user(principal.user_id)
    await db.commit()
    # After the commit, so a failed commit does not refund an attempt.
    await rate_limit.release_change_password(
        redis, settings, request, email=principal.user.email
    )
    await db.refresh(user)

    agency = (
        await db.execute(select(Agency).where(Agency.id == user.agency_id))
    ).scalar_one()

    # Revoked twice on purpose. The pre-commit revoke means a Redis failure
    # leaves the password unchanged (503, retry works); if the commit then
    # fails, devices are signed out over an unchanged password, the safe
    # direction. This post-commit revoke catches a session minted in between.
    # The durable fix would be a `users.sessions_valid_after` column checked by
    # `current_principal`, which needs no Redis at write time; not implemented.
    await store.revoke_all_for_user(user.id)
    token, _ = await store.create(user_id=user.id, agency_id=user.agency_id)
    _set_session_cookie(response, token, settings)

    used, limit = await seat_service.seat_usage(db, user.agency_id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
    )


@router.post(
    "/reset-password/request", response_model=ResetRequestOut, status_code=status.HTTP_200_OK
)
async def request_password_reset(
    payload: ResetPasswordRequest,
    request: Request,
    redis: RedisDep,
    db: DbDep,
    settings: SettingsDep,
) -> Any:
    """Ask for a reset link. **Always `200`, and the same body for every address.**

    The response is byte-identical whether the address has an account, has a
    suspended one, or has never been seen. Any observable difference is an
    oracle for enumerating who banks here, which is precisely what
    `authenticate` already burns a dummy Argon2 hash to avoid on the login path.

    **It does differ by deployment, honestly** (2026-09-29): `emailConfigured`
    says whether this service can send email at all, and the `detail` says
    what that means. With no `RESEND_API_KEY` nothing is sent — and nothing is
    logged in its place any more, because the link it used to log is a live
    credential. Neither field depends on the address.

    The email is best-effort by construction: `send_password_reset` never raises
    and never reports its outcome, so a provider outage cannot become a
    different status code.

    **Errors:** `429` when the per-email or per-IP limit is spent. The per-email
    limit trips identically for every address, so it reveals nothing about
    accounts. `422` if the body is not an email address.
    """
    await rate_limit.enforce(redis, settings, request, action="reset", email=payload.email)
    minted = await reset_service.request_reset(
        db, email=payload.email, settings=settings
    )
    # Commit before sending. A token that reaches an inbox but not the database
    # is a link that 404s; the reverse merely wastes a row.
    await db.commit()

    if minted is not None:
        user, token = minted
        await email_service.send_password_reset(
            user.email,
            reset_service.reset_url(settings, token),
            user_id=user.id,
            settings=settings,
        )

    if not settings.email_configured:
        return ResetRequestOut(
            status="accepted",
            detail=(
                "Password reset by email is not available on this service yet, "
                "so no link was sent."
            ),
            email_configured=False,
        )
    return ResetRequestOut(
        status="accepted",
        detail=(
            "If that address has an account, a reset link is on its way. "
            "The link works once and expires in an hour."
        ),
        email_configured=True,
    )


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

    **Errors:** `400 invalid-reset-token` for unknown, expired, already-used,
    and belonging-to-an-inactive-user alike — one response, no branch that says
    which.
    """
    try:
        user = await reset_service.confirm_reset(
            db, token=payload.token, new_password=payload.new_password, settings=settings
        )
    except reset_service.ResetTokenError:
        await db.rollback()
        raise InvalidResetToken(
            detail="That reset link is not valid. Links work once and expire after an hour."
        ) from None

    # Revoke before the commit as well as after it: if Redis fails here the
    # request is a 503 with the password unchanged and the reset link not yet
    # consumed (the session_scope rollback discards it), so the retry works.
    # The post-commit revoke catches a session minted in between. Durable fix,
    # not implemented: a `users.sessions_valid_after` column checked by
    # `current_principal`.
    await store.revoke_all_for_user(user.id)
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
    has never been used, exactly the situation api-contracts.md already calls
    "friction with no security value" on the sign-up path.

    **`200`, not `201`.** Nothing is created. The `User` row was inserted when
    the invitation was sent and has occupied a seat ever since; this fills it.

    **Errors:** `400 invalid-invitation` for unknown, expired, already-accepted,
    revoked, and pointing-at-a-seat-that-has-since-been-removed alike — one
    response, no branch that says which. `422` for a password that fails the
    same rules sign-up applies.
    """
    try:
        user = await invitation_service.accept_invitation(
            db,
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

    await db.commit()
    await db.refresh(user)

    agency = (
        await db.execute(select(Agency).where(Agency.id == user.agency_id))
    ).scalar_one()

    token, _ = await store.create(user_id=user.id, agency_id=user.agency_id)
    _set_session_cookie(response, token, settings)

    used, limit = await seat_service.seat_usage(db, user.agency_id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
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
    settings: SettingsDep, flow: GoogleFlowStoreDep, google: GoogleProviderDep
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
    """
    if not settings.google_sign_in_configured:
        raise GoogleSignInNotConfigured(
            detail=(
                "Google sign-in needs GOOGLE_OAUTH_CLIENT_ID, "
                "GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URL. "
                "Sign in with your email and password instead."
            )
        )
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
    linking policy decides. Three destinations:

    - an existing account → the session cookie is set and the browser goes
      to `/dashboard`;
    - a new address → a ten-minute ticket is minted and the browser goes to
      `/sign-up/google#ticket=…` (the fragment, never the query, so it is in
      no request log and no Referer) to name the agency;
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
) -> RedirectResponse:
    """The callback proper; `google_callback` wraps it so nothing escapes as a 500."""
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
        user = await google_oauth.resolve_identity(db, identity, settings=settings)
    except google_oauth.GoogleSignInRefusedError as refused:
        await db.rollback()
        return _google_failure(settings, refused.reason)

    base = settings.public_web_base_url.rstrip("/")
    if user is None:
        ticket = await flow.issue_ticket(identity, parked.binding_digest)
        # A fragment is never sent to a server: the ticket stays out of the web
        # host's request log, browser history as a query, and every Referer.
        to_sign_up = RedirectResponse(f"{base}/sign-up/google#ticket={ticket}", status_code=302)
        # The ticket is bound to this browser: keep the flow cookie (renewed)
        # for the completion calls, which clear it.
        assert flow_cookie is not None  # the binding was compared above
        _set_google_flow_cookie(to_sign_up, flow_cookie, settings)
        return to_sign_up

    await db.commit()
    token, _ = await store.create(user_id=user.id, agency_id=user.agency_id)
    redirect = RedirectResponse(f"{base}/dashboard", status_code=status.HTTP_302_FOUND)
    _set_session_cookie(redirect, token, settings)
    return redirect


@router.post("/google/pending", response_model=GooglePendingOut)
async def google_pending(
    payload: GooglePendingRequest, request: Request, settings: SettingsDep, flow: GoogleFlowStoreDep
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

    **Errors:** `400 invalid-google-ticket` for unknown, used, expired and
    another browser's alike.
    """
    peeked = await flow.peek_ticket(payload.ticket)
    if peeked is None or not _ticket_bound_to(
        request.cookies.get(settings.google_flow_cookie_key), peeked[1]
    ):
        raise InvalidGoogleTicket(detail=_TICKET_REFUSAL)
    identity = peeked[0]
    return GooglePendingOut(email=identity.email, suggested_name=identity.name)


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

    **Errors:** `400 invalid-google-ticket`, `409 email-already-registered`
    (the address was registered in the minutes since the ticket was minted),
    `422`.
    """
    # The ticket is spent first, as ever; then it must be this browser's. A
    # refusal is built here as a problem document so the flow cookie can be
    # cleared on the response actually sent.
    try:
        consumed = await flow.consume_ticket(payload.ticket)
        if consumed is None or not _ticket_bound_to(
            request.cookies.get(settings.google_flow_cookie_key), consumed[1]
        ):
            raise InvalidGoogleTicket(detail=_TICKET_REFUSAL)
        agency, user = await google_oauth.complete_sign_up(
            db,
            consumed[0],
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

    token, _ = await store.create(user_id=user.id, agency_id=agency.id)
    _set_session_cookie(response, token, settings)
    _clear_google_flow_cookie(response, settings)

    used, limit = await seat_service.seat_usage(db, agency.id)
    return MeOut(
        user=UserOut.model_validate(user),
        agency=AgencyOut.model_validate(agency),
        seats=SeatUsageOut(used=used, limit=limit),
    )


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, db: DbDep) -> Any:
    used, limit = await seat_service.seat_usage(db, principal.agency_id)
    return MeOut(
        user=UserOut.model_validate(principal.user),
        agency=AgencyOut.model_validate(principal.agency),
        seats=SeatUsageOut(used=used, limit=limit),
    )

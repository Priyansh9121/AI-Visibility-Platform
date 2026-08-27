"""Signup, login, logout, and the authenticated-context endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from ..config import Settings
from ..deps import DbDep, PrincipalDep, SessionStoreDep, SettingsDep
from ..errors import InvalidResetToken
from ..models import Agency
from ..schemas.auth import (
    AgencyOut,
    LoginRequest,
    MeOut,
    ResetPasswordConfirm,
    ResetPasswordRequest,
    SeatUsageOut,
    SignUpRequest,
    UserOut,
)
from ..services import auth as auth_service
from ..services import email as email_service
from ..services import password_reset as reset_service
from ..services import seats as seat_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        # httpOnly: XSS cannot read this cookie. The single most valuable
        # property of the session-cookie approach over a token in localStorage.
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
        path="/",
    )


@router.post("/sign-up", response_model=MeOut, status_code=status.HTTP_201_CREATED)
async def sign_up(
    payload: SignUpRequest,
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Any:
    """Create an agency and its owner, then sign them straight in.

    Signing in as part of signup is deliberate: requiring a fresh login
    immediately after choosing a password is friction with no security value.
    """
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
    response: Response,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Any:
    user = await auth_service.authenticate(
        db, email=payload.email, password=payload.password, settings=settings
    )
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


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: PrincipalDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Response:
    await store.revoke(principal.session_token)
    _clear_session_cookie(response, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    response: Response,
    principal: PrincipalDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Response:
    """Revoke every session for the caller, on every device."""
    await store.revoke_all_for_user(principal.user_id)
    _clear_session_cookie(response, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reset-password/request", status_code=status.HTTP_200_OK)
async def request_password_reset(
    payload: ResetPasswordRequest,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, str]:
    """Ask for a reset link. **Always `200`, always this body.**

    The response is byte-identical whether the address has an account, has a
    suspended one, or has never been seen — and identical again whether or not
    an email provider is configured. Any observable difference is an oracle for
    enumerating who banks here, which is precisely what `authenticate` already
    burns a dummy Argon2 hash to avoid on the login path.

    The email is best-effort by construction: `send_password_reset` never raises
    and never reports its outcome, so a provider outage cannot become a
    different status code. With no `RESEND_API_KEY` the link is logged instead
    of sent — a supported development mode, not a failure.

    **Errors:** none. `422` only if the body is not an email address.
    """
    minted = await reset_service.request_reset(
        db, email=payload.email, settings=settings
    )
    # Commit before sending. A token that reaches an inbox but not the database
    # is a link that 404s; the reverse merely wastes a row.
    await db.commit()

    if minted is not None:
        user, token = minted
        reset_url = f"{settings.public_web_base_url.rstrip('/')}/reset-password/{token}"
        await email_service.send_password_reset(
            user.email, reset_url, settings=settings
        )

    return {
        "status": "accepted",
        "detail": (
            "If that address has an account, a reset link is on its way. "
            "The link works once and expires in an hour."
        ),
    }


@router.post("/reset-password/confirm", status_code=status.HTTP_200_OK)
async def confirm_password_reset(
    payload: ResetPasswordConfirm,
    response: Response,
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

    await db.commit()
    await store.revoke_all_for_user(user.id)
    _clear_session_cookie(response, settings)
    return {
        "status": "reset",
        "detail": "Your password has been changed. Sign in with the new one.",
    }


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, db: DbDep) -> Any:
    used, limit = await seat_service.seat_usage(db, principal.agency_id)
    return MeOut(
        user=UserOut.model_validate(principal.user),
        agency=AgencyOut.model_validate(principal.agency),
        seats=SeatUsageOut(used=used, limit=limit),
    )

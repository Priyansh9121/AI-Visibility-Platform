"""Signup, login, logout, and the authenticated-context endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from ..config import Settings
from ..deps import DbDep, PrincipalDep, SessionStoreDep, SettingsDep
from ..models import Agency
from ..schemas.auth import (
    AgencyOut,
    LoginRequest,
    MeOut,
    SeatUsageOut,
    SignUpRequest,
    UserOut,
)
from ..services import auth as auth_service
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


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, db: DbDep) -> Any:
    used, limit = await seat_service.seat_usage(db, principal.agency_id)
    return MeOut(
        user=UserOut.model_validate(principal.user),
        agency=AgencyOut.model_validate(principal.agency),
        seats=SeatUsageOut(used=used, limit=limit),
    )

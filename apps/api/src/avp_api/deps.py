"""FastAPI dependencies: settings, database, Redis, and the current user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import session_scope
from .errors import AuthenticationRequired, PermissionDenied
from .models import Agency, User, UserRole, UserStatus
from .redis_client import get_redis
from .sessions import SessionStore

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in session_scope():
        yield session


DbDep = Annotated[AsyncSession, Depends(db_session)]


def redis_client() -> Redis:
    return get_redis()


RedisDep = Annotated[Redis, Depends(redis_client)]


def session_store(redis: RedisDep, settings: SettingsDep) -> SessionStore:
    return SessionStore(redis, settings)


SessionStoreDep = Annotated[SessionStore, Depends(session_store)]


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, resolved once per request."""

    user: User
    agency: Agency
    session_token: str

    @property
    def agency_id(self) -> str:
        return self.agency.id

    @property
    def user_id(self) -> str:
        return self.user.id


async def current_principal(
    request: Request,
    db: DbDep,
    store: SessionStoreDep,
    settings: SettingsDep,
) -> Principal:
    """Resolve the caller from the session cookie, or raise 401.

    Re-reads the user and agency from Postgres on every request rather than
    trusting the session payload. That is the point of a server-side session:
    a suspended user or a soft-deleted agency loses access on their next
    request, not whenever a token expires.
    """
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise AuthenticationRequired(detail="No session cookie was supplied.")

    record = await store.get(token)
    if record is None:
        raise AuthenticationRequired(detail="Session is expired or invalid.")

    user = (
        await db.execute(
            select(User).where(User.id == record.user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if user is None or user.status is not UserStatus.ACTIVE:
        await store.revoke(token)
        raise AuthenticationRequired(detail="This account is no longer active.")

    agency = (
        await db.execute(
            select(Agency).where(Agency.id == user.agency_id, Agency.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if agency is None:
        await store.revoke(token)
        raise AuthenticationRequired(detail="This agency is no longer active.")

    return Principal(user=user, agency=agency, session_token=token)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require_role(*allowed: UserRole):  # noqa: ANN201 - returns a dependency
    """Dependency factory gating a route on seat role."""

    async def _guard(principal: PrincipalDep) -> Principal:
        if principal.user.role not in allowed:
            raise PermissionDenied(
                detail=(
                    "This action requires one of: "
                    + ", ".join(sorted(r.value for r in allowed))
                    + f". Your role is {principal.user.role.value}."
                )
            )
        return principal

    return _guard


RequireOwner = Annotated[Principal, Depends(require_role(UserRole.OWNER))]
RequireAdmin = Annotated[Principal, Depends(require_role(UserRole.OWNER, UserRole.ADMIN))]

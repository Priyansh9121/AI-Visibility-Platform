"""FastAPI dependencies: settings, database, Redis, and the current user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import session_scope
from .errors import AuthenticationRequired, NotFound, PermissionDenied
from .models import Agency, User, UserRole, UserStatus
from .redis_client import get_redis
from .services.google_oauth import GoogleFlowStore, GoogleProvider, LiveGoogleProvider
from .services.scan_executor import BackgroundScanExecutor, ScanExecutor
from .sessions import SessionStore

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in session_scope():
        yield session


DbDep = Annotated[AsyncSession, Depends(db_session)]


def redis_client() -> Redis:
    return get_redis()


RedisDep = Annotated[Redis, Depends(redis_client)]


def scan_executor(background: BackgroundTasks) -> ScanExecutor:
    """How a queued scan gets run — Epic 9.5.

    A dependency rather than a module-level singleton for two reasons.
    `BackgroundTasks` is per-request, so there is nothing to hold onto between
    requests. And it is the seam tests override with `InlineScanExecutor`, which
    keeps "the scan is finished once the POST returns" true in the suite without
    the endpoint pretending to be synchronous in production.
    """
    return BackgroundScanExecutor(background)


ScanExecutorDep = Annotated[ScanExecutor, Depends(scan_executor)]


def session_store(redis: RedisDep, settings: SettingsDep) -> SessionStore:
    return SessionStore(redis, settings)


SessionStoreDep = Annotated[SessionStore, Depends(session_store)]


def google_provider(settings: SettingsDep) -> GoogleProvider:
    """Who answers Google's side of the sign-in — Epic 20.

    The seam the suite overrides with a fake, exactly as `scan_executor` is
    overridden with an inline executor: the routes, the state store, the
    account-linking policy and the cookie are all exercised against a
    provider that never leaves the process. Nothing about the real one is
    reachable from a test.
    """
    return LiveGoogleProvider(settings)


GoogleProviderDep = Annotated[GoogleProvider, Depends(google_provider)]


def google_flow_store(redis: RedisDep, settings: SettingsDep) -> GoogleFlowStore:
    return GoogleFlowStore(redis, settings)


GoogleFlowStoreDep = Annotated[GoogleFlowStore, Depends(google_flow_store)]


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

    Use also refreshes the session's idle clock (A3, D8), once the caller is let in: a session
    ends 72 hours after it was last used, or 14 days after sign-in, whichever is first. The
    refresh is `store.touch`, which writes only when the last refresh is older than
    `session_touch_interval_seconds`, so most requests make no write, and which cannot bring back
    a session revoked meanwhile.
    """
    token = request.cookies.get(settings.session_cookie_key)
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

    # Last, so that only a caller who is actually let in refreshes the clock. Not
    # `reconfirm_principal`, which only reads.
    await store.touch(token, record)
    return Principal(user=user, agency=agency, session_token=token)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def assert_own_agency(principal_agency_id: str, agency_id: str) -> None:
    """The agency id in a path must be the caller's own.

    **404, not 403.** Answering "you may not touch that agency" confirms the
    agency exists, which is the cross-tenant leak every scoped route in this
    service already refuses to make. An id that is not yours is an id that does
    not exist, as far as this API is concerned.

    Lives here rather than in a router because Epic 9.15 gave it a second
    caller. It was a private helper in `routers/agencies.py`; a tenant-isolation
    rule with two copies is a rule with two chances to be relaxed, and the one
    that gets relaxed is never the one under test.
    """
    if principal_agency_id != agency_id:
        raise NotFound(detail="No agency with that identifier.")


def _refuse_unless_role(role: UserRole, allowed: tuple[UserRole, ...]) -> None:
    """The role gate: `403` naming the roles that may, unless `role` is one of them."""
    if role not in allowed:
        raise PermissionDenied(
            detail=(
                "This action requires one of: "
                + ", ".join(sorted(r.value for r in allowed))
                + f". Your role is {role.value}."
            )
        )


def require_role(*allowed: UserRole):  # noqa: ANN201 - returns a dependency
    """Dependency factory gating a route on seat role."""

    async def _guard(principal: PrincipalDep) -> Principal:
        _refuse_unless_role(principal.user.role, allowed)
        return principal

    return _guard


# The roles that manage seats: what `RequireAdmin` gates on, and what a request that waited for a
# lock asks again (`reconfirm_principal`), so the two cannot name different seats.
ADMIN_ROLES = (UserRole.OWNER, UserRole.ADMIN)

RequireOwner = Annotated[Principal, Depends(require_role(UserRole.OWNER))]
RequireAdmin = Annotated[Principal, Depends(require_role(*ADMIN_ROLES))]


async def reconfirm_principal(
    db: AsyncSession, store: SessionStore, principal: Principal, *allowed: UserRole
) -> None:
    """Ask `current_principal` and `require_role` again, of the database and the session store as
    they are now.

    A request is let in at its start. One that then waits for a row lock decides on what it knew at
    its start, which the request ahead of it may have changed: a removal that waited for the
    agency's lock would act for a seat that request has removed, and an invitation would hand a
    link to a session that was revoked meanwhile (sign out everywhere, a password change, a Google
    link replacing an earlier password holder). Called under the lock, this refuses as the
    dependencies would on the caller's next request, in this order: `401` for a seat that is gone or
    is not active; then `401` for a session that no longer exists (the body `current_principal`
    gives for an expired or invalid one); then the role gate's own `403` for a role, on the row just
    loaded, that has dropped out of `allowed`.

    The role is the one on the row just loaded. The row is refreshed in place, so `principal.user`
    is the database's now too and a caller that reads `principal.user.role` afterwards reads the
    current role, not the one the request began with. It is not locked: an active seat stays as it
    was read while the caller holds the agency's lock, because the only thing that changes one (a
    removal) takes the same lock. The agency row is not re-read, because nothing soft-deletes one.
    """
    user = (
        await db.execute(
            select(User)
            .where(User.id == principal.user_id, User.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if user is None or user.status is not UserStatus.ACTIVE:
        raise AuthenticationRequired(detail="This account is no longer active.")
    # After the seat, not before: a removal commits, and only then revokes the removed seat's
    # sessions, so a waiter that gets the lock at that commit may find the session still there or
    # already gone. Asked in this order, a removed caller hears "no longer active" either way.
    if await store.get(principal.session_token) is None:
        raise AuthenticationRequired(detail="Session is expired or invalid.")
    _refuse_unless_role(user.role, allowed)

"""Seat management — Epic 9.14.

Recorded in docs/api-contracts.md.

WHY THIS IS A NEW ROUTER AND NOT MORE OF `auth.py`
--------------------------------------------------
Every router in this service is identified by its path prefix — `clients`,
`competitors`, `scans`, `scores` — and `auth.py` carries `prefix="/auth"`.
Neither path here lives under `/auth`: they are `/agencies/{id}/...` and
`/users/{id}`. Adding them to `auth.py` would mean dropping that router's prefix
and hand-writing full paths on some routes but not others, which nothing else in
the codebase does.

They also are not the same KIND of endpoint. `auth.py` is the unauthenticated
edge plus the caller's own session — sign-up, login, logout, "who am I".
Everything here acts on somebody ELSE's seat and is gated on role. `report.py`
is the precedent for a router spanning two path families (`/scans/{id}/report`
and `/reports/{token}`) with no prefix at all, so that is the shape used.

**Acceptance lives in `auth.py`, not here**, because it is unauthenticated: a
person redeeming an invitation has no session yet, which is the whole point.

ROLE GATE
---------
Owner or admin. A member holds a seat; they do not decide who else does.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Response, status
from sqlalchemy import select

from ..deps import DbDep, RequireAdmin, SessionStoreDep, SettingsDep, assert_own_agency
from ..errors import Conflict, NotFound, PermissionDenied
from ..models import Agency, User
from ..schemas.agency import (
    BrandingOut,
    BrandingRequest,
    InviteSeatRequest,
    InviteSeatResponse,
    PendingInvitationOut,
    SeatListOut,
)
from ..schemas.auth import SeatUsageOut, UserOut
from ..services import email as email_service
from ..services import invitations as invitation_service
from ..services import seats as seat_service

router = APIRouter(tags=["agencies"])


@router.get("/agencies/{agencyId}/seats", response_model=SeatListOut)
async def list_seats(
    principal: RequireAdmin,
    db: DbDep,
    agency_id: str = Path(alias="agencyId"),
) -> Any:
    """Who holds a seat, and which invitations are still live.

    `GET /auth/me` already carries `seats: {used, limit}` — the COUNT. It has
    never carried the roster, because the shell needs a number and not a list,
    and a management screen needs the list. This is that list.

    **Errors:** `401`, `403` (member role), `404` (another agency's id).
    """
    assert_own_agency(principal.agency_id, agency_id)

    members = await invitation_service.seat_holders(db, agency_id)
    pending = await invitation_service.pending_invitations(db, agency_id)
    used, limit = await seat_service.seat_usage(db, agency_id)

    return SeatListOut(
        members=[UserOut.model_validate(u) for u in members],
        invitations=[PendingInvitationOut.model_validate(i) for i in pending],
        seats=SeatUsageOut(used=used, limit=limit),
    )


@router.post(
    "/agencies/{agencyId}/invitations",
    response_model=InviteSeatResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_seat(
    payload: InviteSeatRequest,
    principal: RequireAdmin,
    db: DbDep,
    settings: SettingsDep,
    agency_id: str = Path(alias="agencyId"),
) -> Any:
    """Invite an address to a seat.

    **The seat limit is enforced here, server-side, in the same transaction as
    the insert.** `GET /auth/me`'s `seats` object lets a screen grey out the
    form when the agency is full, but that is a courtesy — the limit is
    commercial policy and a client-side check is a suggestion. Refused with
    `409 /problems/seat-limit-reached`, carrying `seatsUsed` and `seatLimit` so
    the screen can say what to do about it rather than just that it failed.

    **Re-inviting an address that already holds an INVITED seat is allowed** and
    consumes no second seat: it revokes the outstanding link and mints a new
    one. `seatConsumed: false` in the response says that is what happened. See
    `services/invitations.py` for why refusing it would be a trap.

    **`201` in both cases.** An invitation row is genuinely created either way —
    that is the resource this endpoint makes. Whether a SEAT was newly consumed
    is a different question and it is answered in the body, not in the status.

    **Errors:** `401`, `403`, `404` (another agency's id), `409
    seat-limit-reached`, `409 email-already-registered` (a live account, here or
    elsewhere), `422`.
    """
    assert_own_agency(principal.agency_id, agency_id)

    minted = await invitation_service.invite_seat(
        db,
        agency_id=agency_id,
        invited_by_user_id=principal.user_id,
        email=payload.email,
        role=payload.role,
        settings=settings,
    )
    used, limit = await seat_service.seat_usage(db, agency_id)

    # Commit before sending, for the reason the reset endpoint gives: a token
    # that reaches an inbox but not the database is a link that 404s, whereas
    # the reverse merely wastes a row somebody can re-issue.
    await db.commit()
    await db.refresh(minted.user)
    await db.refresh(minted.invitation)

    invite_url = (
        f"{settings.public_web_base_url.rstrip('/')}/invite/{minted.token}"
    )
    await email_service.send_invitation(
        minted.user.email,
        invite_url,
        agency_name=principal.agency.name,
        inviter_name=principal.user.full_name,
        settings=settings,
    )

    return InviteSeatResponse(
        invitation=PendingInvitationOut.model_validate(minted.invitation),
        user=UserOut.model_validate(minted.user),
        seat_consumed=minted.seat_consumed,
        seats=SeatUsageOut(used=used, limit=limit),
    )


@router.delete("/users/{userId}", status_code=status.HTTP_204_NO_CONTENT)
async def release_seat(
    principal: RequireAdmin,
    db: DbDep,
    store: SessionStoreDep,
    user_id: str = Path(alias="userId"),
) -> Response:
    """Remove a seat. **Every session that seat holds dies immediately.**

    Not merely the seat. `sessions.py` opens by saying this is why the product
    has a server-side session store at all rather than JWTs: "when an agency
    removes a seat, that person's access has to end *now* — not whenever their
    access token happens to expire". This endpoint is the case that argument was
    made for, so it calls `revoke_all_for_user` rather than relying on the
    fallback.

    There IS a fallback, and it is not the mechanism: `deps.current_principal`
    re-reads the user on every request and 401s on a soft-deleted row, so access
    would end on the removed user's next call even if revocation were skipped.
    That closes the door within one request. Revocation closes it within zero,
    and the difference is a request that is already in flight.

    The row is soft-deleted, so the seat is released while `scans.requested_by_
    user_id` and the rest of that person's history stay intact — the rule
    `models/base.py` states for the whole schema.

    **Two refusals, both `409`:**

    * Removing yourself. An owner who deletes their own row is authenticated by
      a session that is about to be revoked, mid-request. `logout-all` is the
      deliberate version of signing yourself out.
    * Removing the last active owner. `UserRole` says why in the model: an
      agency with no owner has nobody who can manage seats — including nobody
      who can undo this.

    **Errors:** `401`, `403`, `404` (unknown user, or another agency's — never
    `403`, which would confirm the id), `409`.
    """
    user = (
        await db.execute(
            select(User).where(
                User.id == user_id,
                User.agency_id == principal.agency_id,
                User.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFound(detail="No user with that identifier.")

    if user.id == principal.user_id:
        raise Conflict(
            detail=(
                "You cannot remove your own seat. Ask another owner or admin to "
                "do it, or use sign out everywhere if you meant to end your sessions."
            )
        )

    if user.role.value == "owner" and await invitation_service.count_active_owners(
        db, principal.agency_id
    ) <= 1:
        raise Conflict(
            detail=(
                "This is the only owner on the account. Make someone else an "
                "owner before removing this seat."
            )
        )

    # An admin removing an owner is a privilege inversion: it would let a seat
    # the owner granted be used to remove the owner who granted it.
    if user.role.value == "owner" and principal.user.role.value != "owner":
        raise PermissionDenied(detail="Only an owner can remove another owner's seat.")

    await invitation_service.release_seat(db, agency_id=principal.agency_id, user=user)
    await db.commit()

    # AFTER the commit, deliberately. Revoking first and then failing to commit
    # would sign someone out of a seat they still hold.
    await store.revoke_all_for_user(user.id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/agencies/{agencyId}/branding", response_model=BrandingOut)
async def update_branding(
    principal: RequireAdmin,
    db: DbDep,
    payload: BrandingRequest,
    agency_id: str = Path(alias="agencyId"),
) -> Any:
    """Set this agency's logo and accent colour — Epic 9.22.

    **What may be changed is a logo and ONE colour, and the colour only reaches
    chrome.** The report's palette is notation rather than decoration: the
    visibility ramp encodes the score, the competitor series is neutral by
    design, the beacon marks the subject being scanned, and the semantic four
    say a scan failed. `BrandingRequest` has no field that can reach any of
    them — the guarantee is the type, not this docstring.

    **Admin or owner, not any member.** How every report an agency sends is
    branded is an agency-level decision with an external audience, which is the
    same bar `PATCH`-ing seats sits behind.

    **PATCH, and null means remove.** An agency that set the wrong logo needs a
    way back to unbranded; a route that can only ever set is a one-way door.
    Both fields are independent, so sending one does not clear the other —
    which is why the body is read field by field rather than assigned wholesale.

    **`404` for another agency's id**, never `403`: answering "you may not
    touch that agency" confirms it exists.

    **Errors:** `401`, `403` (member seat), `404` (another agency's id), `422`
    (a logo URL that is not `https://`, or a colour that is not `#rrggbb`).
    """
    assert_own_agency(principal.agency_id, agency_id)

    agency = (
        await db.execute(select(Agency).where(Agency.id == agency_id))
    ).scalar_one_or_none()
    if agency is None:
        raise NotFound(detail="No agency with that identifier.")

    # Field by field, and only what was sent. `model_fields_set` is what makes
    # "clear the logo" (an explicit null) different from "leave the logo alone"
    # (absent) — assigning the whole model would collapse those into one, and
    # an agency updating only its colour would silently lose its logo.
    sent = payload.model_fields_set
    if "logo_url" in sent:
        agency.logo_url = payload.logo_url
    if "accent_color" in sent:
        agency.accent_color = payload.accent_color
    await db.commit()

    return BrandingOut(
        agency_id=agency.id,
        logo_url=agency.logo_url,
        accent_color=agency.accent_color,
    )

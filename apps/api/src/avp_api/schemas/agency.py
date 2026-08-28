"""Seat-management schemas — Epic 9.14.

The invite response deliberately carries NO token and no invitation URL. The
link goes in exactly one email, and a response body is a place it would be
logged by a proxy, kept in a browser's network panel, and read by anyone who
can see the operator's screen — none of which the email is. That is the same
reasoning `PasswordResetToken` gives for storing only a digest, applied to the
other end of the same credential's life.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr

from ..models import UserRole
from .auth import SeatUsageOut, UserOut
from .common import ApiModel


class InviteSeatRequest(ApiModel):
    """Invite an address to a seat.

    Role defaults to MEMBER, the least-privileged seat there is. An invite form
    that defaults to ADMIN is how an agency ends up with five owners.
    """

    email: EmailStr
    role: UserRole = UserRole.MEMBER


class PendingInvitationOut(ApiModel):
    """An invitation that has not been accepted, revoked, or expired.

    No token and no URL — see the module docstring.
    """

    id: str
    email: str
    role: UserRole
    expires_at: datetime
    created_at: datetime


class InviteSeatResponse(ApiModel):
    """What the invite produced, and what the agency's seat position now is.

    `seat_consumed` is false when the address already held an `INVITED` seat and
    this call only re-issued its link. The distinction is the difference between
    "Invited" and "A new link is on its way", and a screen that cannot tell them
    apart will report a seat as newly taken when the count did not move.

    `seats` is returned so a caller does not have to re-read `GET /auth/me` to
    find out how much room is left after acting.
    """

    invitation: PendingInvitationOut
    user: UserOut
    seat_consumed: bool
    seats: SeatUsageOut


class SeatListOut(ApiModel):
    """Everyone occupying a seat, plus the invitations still outstanding.

    Both lists, not one merged list. A pending invitee appears in `members`
    with `status: "invited"` because they occupy a seat and that is what the
    seat count is about; they appear again in `invitations` because only that
    row knows when the link expires. Merging them would force one of those two
    facts to be dropped.
    """

    members: list[UserOut]
    invitations: list[PendingInvitationOut]
    seats: SeatUsageOut

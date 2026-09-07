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

from pydantic import EmailStr, Field, field_validator

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


# --- white-label branding (Epic 9.22) ----------------------------------------
# `#rrggbb`, lower or upper case. Anchored at both ends, because an unanchored
# pattern accepts `#abcdef; } body { display:none` and this value is written
# into a CSS custom property.
HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class BrandingRequest(ApiModel):
    """What an agency may change about how its reports look — Epic 9.22.

    **The shortness of this model is the decision, not an omission.** §7 line 2
    asked for "logo, custom domain, colours"; what an agency may actually
    change is a logo and ONE colour used only on chrome.

    The report's palette is notation. `--avp-vis-*` encodes the score on a
    monotonic lightness ramp that survives greyscale and colour-vision
    deficiency; `--avp-competitor-{1..5}` is neutral so no rival reads as
    endorsed or attacked; `--avp-beacon-*` marks the subject being scanned, who
    is the prospect and not the agency; the semantic four say a scan failed or
    a quota is low. There is no field here that can reach any of them, which is
    the guarantee — enforced by the type rather than by a reviewer noticing,
    the same way `FixFacts` has no field able to hold page copy.

    **No `custom_domain`.** Deferred rather than forgotten: it needs DNS
    verification, certificate issuance and routing before it does anything, and
    a field that stores a value nothing honours is a field that looks built.

    **Both values are explicitly nullable, and null means "remove it".** An
    agency that uploaded the wrong logo needs a way back to unbranded, and a
    PATCH that can only ever set is a one-way door.
    """

    logo_url: str | None = Field(default=None, max_length=2048)
    accent_color: str | None = Field(default=None, pattern=HEX_COLOR)

    @field_validator("logo_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        """**`https://` and nothing else**, and the reason is the share page.

        This URL is rendered as an `<img src>` on an UNAUTHENTICATED page that
        anyone with a link can open. `javascript:` in that position is script
        execution against every reader; `data:` is the same problem carrying
        its own payload. `http://` is not an attack but it is a mixed-content
        block, so the logo silently fails to render — a support ticket rather
        than a hole, and still not worth allowing.

        The URL is never fetched server-side, which is what keeps it from being
        an SSRF vector as well. That is a property of the PDF decision (the PDF
        does not carry a logo) and is recorded here because the day that
        changes, this validator is not enough on its own.
        """
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not cleaned.startswith("https://"):
            raise ValueError("logoUrl must be an absolute https:// URL.")
        return cleaned


class BrandingOut(ApiModel):
    """An agency's branding as stored. Null on both fields is unbranded."""

    agency_id: str
    logo_url: str | None = None
    accent_color: str | None = None

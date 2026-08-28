"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, EmailStr, Field

from ..models import UserRole, UserStatus
from .common import ApiModel

# NIST SP 800-63B: length is what matters; composition rules push users toward
# predictable substitutions and are explicitly discouraged. So: a real minimum,
# a generous maximum, and no character-class theatre.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256

# How few distinct characters make a string not a password. `aaaaaaaaaaaa`
# clears twelve characters and is not one.
MIN_DISTINCT_CHARACTERS = 5

REPETITIVE_PASSWORD_MESSAGE = (
    "Password is too repetitive. Use a longer, more varied passphrase."
)


def reject_repetitive(value: str) -> str:
    """A single repeated character clears a length check but is not a password.

    Cheap to reject; catches the worst real-world input.
    """
    if len(set(value)) < MIN_DISTINCT_CHARACTERS:
        raise ValueError(REPETITIVE_PASSWORD_MESSAGE)
    return value


# THE password rule. One definition, three doors.
#
# Sign-up, reset-confirm and the authenticated change each used to restate the
# bounds and re-declare their own `_reject_trivial` validator. Three copies of
# one policy is three chances for them to drift, and the direction drift takes
# is always the same: a recovery or change path ends up laxer than registration,
# which makes it the way in rather than the way back. Epic 9.13 already said so
# in prose on `ResetPasswordConfirm` ("the SIGN-UP rules, reused rather than
# restated") while still copying the code; this is that sentence made true.
Password = Annotated[
    str,
    Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH),
    AfterValidator(reject_repetitive),
]


class SignUpRequest(ApiModel):
    agency_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: Password


class ResetPasswordRequest(ApiModel):
    """Ask for a reset link. The response is identical whoever this is."""

    email: EmailStr


class ResetPasswordConfirm(ApiModel):
    """Redeem a link and set a new password.

    The password rules are the SIGN-UP rules, reused rather than restated —
    same length bounds and the same repetitiveness check. A reset path with
    weaker rules than registration is a way in, not a convenience.
    """

    token: str = Field(min_length=1, max_length=512)
    new_password: Password


class LoginRequest(ApiModel):
    email: EmailStr
    # Deliberately NOT `Password`. This is a credential being presented, not
    # one being set: applying the minimum-length and repetitiveness rules here
    # would 422 on a password that predates a rule change, turning "your rules
    # got stricter" into "you cannot sign in", and would leak which submitted
    # strings are even eligible to be somebody's password.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordRequest(ApiModel):
    """Change your own password while signed in — Epic 9.14.

    `current_password` is presented, not set, so it carries the login bounds
    for the reason `LoginRequest` gives. `new_password` is set, so it carries
    the full rule — the same `Password` sign-up and reset-confirm use.
    """

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: Password


class AcceptInvitationRequest(ApiModel):
    """Redeem a seat invitation and choose a password — Epic 9.14.

    `full_name` is required here and not on the invitation itself: the person
    who sent the invite knows an email address, not how the recipient writes
    their own name. Until this is submitted the seat list shows the address,
    which is the only fact anybody has.
    """

    token: str = Field(min_length=1, max_length=512)
    full_name: str = Field(min_length=1, max_length=200)
    password: Password


class UserOut(ApiModel):
    id: str
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    last_login_at: datetime | None = None
    created_at: datetime


class AgencyOut(ApiModel):
    id: str
    name: str
    slug: str
    seat_limit: int
    created_at: datetime


class SeatUsageOut(ApiModel):
    used: int
    limit: int

    @property
    def available(self) -> int:
        return max(0, self.limit - self.used)


class MeOut(ApiModel):
    """The whole authenticated context in one call.

    The web app needs user, agency, and seat usage to render its shell. Serving
    them together avoids three round trips on every page load.
    """

    user: UserOut
    agency: AgencyOut
    seats: SeatUsageOut

"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from ..models import UserRole, UserStatus
from .common import ApiModel

# NIST SP 800-63B: length is what matters; composition rules push users toward
# predictable substitutions and are explicitly discouraged. So: a real minimum,
# a generous maximum, and no character-class theatre.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256


class SignUpRequest(ApiModel):
    agency_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def _reject_trivial(cls, value: str) -> str:
        # A single repeated character clears a length check but is not a
        # password. Cheap to reject; catches the worst real-world input.
        if len(set(value)) < 5:
            raise ValueError("Password is too repetitive. Use a longer, more varied passphrase.")
        return value


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


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

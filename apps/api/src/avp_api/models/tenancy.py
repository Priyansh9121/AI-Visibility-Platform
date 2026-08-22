"""Agency, User, Invitation — the seat-based tenancy model.

product-spec.md §5.3 nests these as `Agency -> User (seats) -> Client/Prospect`.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    enum_column,
    fk_column,
    id_column,
)

if TYPE_CHECKING:
    from .client import Client


class UserRole(str, enum.Enum):
    """Seat roles.

    OWNER is the billing contact and cannot be removed while it is the last
    owner — an agency with no owner has nobody who can manage seats.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class Agency(Base, TimestampMixin, SoftDeleteMixin):
    """The tenant. Everything else in the system hangs off an agency."""

    __tablename__ = "agencies"

    id: Mapped[str] = id_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Used for white-label report URLs from Epic 7.
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)

    # Seat-based billing: the number of ACTIVE + INVITED users allowed.
    # Enforced transactionally in services/seats.py, not by a trigger — see
    # that module for why.
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    users: Mapped[list[User]] = relationship(back_populates="agency")
    clients: Mapped[list[Client]] = relationship(back_populates="agency")

    __table_args__ = (
        CheckConstraint("seat_limit >= 1", name="seat_limit_positive"),
        CheckConstraint("length(slug) >= 2", name="slug_min_length"),
    )


class User(Base, TimestampMixin, SoftDeleteMixin):
    """A seat holder within an agency.

    Email is globally unique, not unique-per-agency. That makes login a single
    lookup (email -> user -> agency) with no tenant disambiguation step. The
    trade-off is that one human cannot hold seats at two agencies with the same
    address; if that becomes a real requirement it needs a join table and a
    tenant picker at login, which is a deliberate future change rather than
    something to half-build now.
    """

    __tablename__ = "users"

    id: Mapped[str] = id_column()
    agency_id: Mapped[str] = fk_column("agencies.id")

    # Stored lower-cased and stripped; see services/auth.py:normalise_email.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    role: Mapped[UserRole] = enum_column(UserRole, name="user_role", default=UserRole.MEMBER)
    status: Mapped[UserStatus] = enum_column(
        UserStatus, name="user_status", default=UserStatus.ACTIVE
    )

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agency: Mapped[Agency] = relationship(back_populates="users")

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        # Every seat-count query filters on exactly these three columns.
        Index("ix_users_agency_status", "agency_id", "status", "deleted_at"),
        # An invited user has no password yet; an active one must have one.
        CheckConstraint(
            "(status = 'invited' AND password_hash IS NULL) OR password_hash IS NOT NULL",
            name="active_user_has_password",
        ),
    )

    @property
    def occupies_seat(self) -> bool:
        """Whether this row counts against the agency's seat limit.

        Invited-but-not-yet-accepted users DO occupy a seat. Otherwise an agency
        could issue unlimited invitations and overshoot its plan the moment they
        were all accepted.
        """
        return self.deleted_at is None and self.status in (
            UserStatus.ACTIVE,
            UserStatus.INVITED,
        )


class Invitation(Base, TimestampMixin):
    """A pending seat invitation.

    Only the token DIGEST is stored, for the same reason session tokens are
    digested: a database dump must not yield working invitation links.
    """

    __tablename__ = "invitations"

    id: Mapped[str] = id_column()
    agency_id: Mapped[str] = fk_column("agencies.id")
    invited_by_user_id: Mapped[str] = fk_column("users.id", ondelete="SET NULL", nullable=True)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[UserRole] = enum_column(UserRole, name="invitation_role", default=UserRole.MEMBER)

    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_invitations_agency_email", "agency_id", "email"),
    )

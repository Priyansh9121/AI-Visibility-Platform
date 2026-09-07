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

    # --- subscription (Epic 9.15) ------------------------------------------
    #
    # Four nullable columns, and NULL is the correct, expected state for every
    # agency that exists today. north-star.md §5.4 row 1 asked for a `Plan` /
    # `Subscription` model; this is deliberately less than that, because there
    # is one plan. A `plans` table with one row in it, joined through a
    # `subscriptions` table with one row per agency, would be a schema modelling
    # a product decision nobody has made. When a second plan exists, that is the
    # work; until then these columns say everything there is to say.
    #
    # NOTHING IN THE PRODUCT IS GATED ON ANY OF THEM. An agency with all four
    # NULL has exactly the access it had before this epic — scanning, scoring,
    # reporting, seats. These record whether an agency PAYS, not what it may do.
    # A future brief that wants a paywall is proposing a product decision and
    # must say so out loud.

    # The Stripe Customer this agency is. Written once, then reused forever —
    # `services/billing.py` looks here before creating one, so a second visit to
    # checkout does not leave a duplicate customer holding a duplicate card.
    #
    # UNIQUE: one Stripe customer belongs to one agency. Without the constraint,
    # a mis-scoped write could point two agencies at one customer, and the
    # webhook — which resolves an agency BY this column — would then update
    # whichever it found first. Postgres permits many NULLs under a unique
    # index, which is what makes it usable on a column almost every row leaves
    # empty.
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )

    # The current Subscription. Also unique, for the same reason and a sharper
    # one: `customer.subscription.deleted` arrives carrying a subscription id
    # and nothing else useful, so that id is a lookup key, and a lookup key that
    # can match two rows is a cancellation applied to the wrong agency.
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )

    # Stripe's own status string, stored VERBATIM and not mapped to an enum of
    # ours. Every other status in this schema is an `enum_column`, so this is a
    # deliberate exception rather than an oversight.
    #
    # The values are Stripe's to define and to extend: active, trialing,
    # past_due, canceled, unpaid, incomplete, incomplete_expired, paused. A
    # Postgres enum listing them is a constraint on a vocabulary we do not own,
    # and the failure mode is the worst kind — Stripe adds a status, the webhook
    # raises on write, the event is retried and then abandoned, and the database
    # quietly holds a status that stopped being true days ago. Storing the
    # string means an unrecognised status is recorded accurately and rendered
    # honestly rather than lost.
    #
    # `services/billing.py` owns the one interpretation that matters — which
    # statuses count as ACTIVE — in one place, so this column never has to be
    # read as a boolean at the call site.
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # When the paid-up period ends — the "renews 5 October" in Settings.
    #
    # A fourth column beyond the three the brief named, and it is here because
    # two of the brief's own requirements need it together: Settings must say
    # "Active — renews <date>", and the status endpoint must never call Stripe
    # on page load. Both cannot hold unless the date is written by the webhook
    # alongside the status, so it is.
    subscription_current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- white-label branding (Epic 9.22) -----------------------------------
    #
    # Two columns, and the SHORTNESS of that list is the decision. §7 line 2
    # asked for "logo, custom domain, colours"; what an agency may actually
    # change is a logo and ONE colour, used only on chrome.
    #
    # WHY NOT MORE COLOUR. The report's palette is not decoration, it is
    # notation. `--avp-vis-*` encodes the score itself on a monotonic lightness
    # ramp that survives greyscale and colour-vision deficiency;
    # `--avp-competitor-{1..5}` is deliberately neutral so no rival reads as
    # endorsed or attacked; `--avp-beacon-*` marks the subject BEING scanned,
    # which is the prospect and not the agency; and the semantic four say a
    # scan failed or a quota is low. An agency free to recolour any of those
    # changes what the document MEANS, not how it looks. So the agency's colour
    # and the report's encoded colour are kept in disjoint sets, the same
    # disjointness the semantics/visibility split already enforces for a
    # different reason, and a test asserts the accent never renders adjacent to
    # an encoded token.
    #
    # NO `custom_domain`, and that is deferred rather than forgotten. A custom
    # domain needs DNS verification, certificate issuance and request routing
    # before it does anything at all; a nullable column with none of that
    # behind it is a field that looks built and is not.

    # An absolute https:// URL, never an upload — there is no asset store in
    # this product and inventing one for a logo would be the larger decision
    # smuggled inside the smaller. Rendered as an `<img src>` by the browser
    # and NEVER fetched server-side, which is what keeps an agency-supplied URL
    # from being an SSRF vector; `schemas/agency.py` rejects every scheme but
    # https for the matching reason on the other side (`javascript:` and
    # `data:` in an `src` are script execution on an unauthenticated page).
    logo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # `#rrggbb`, validated at the schema and constrained again here. It reaches
    # the browser as a CSS custom property value, and an unvalidated string in
    # that position closes the declaration and opens another — so the format is
    # narrow on purpose and checked in both places rather than trusted from one.
    accent_color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    users: Mapped[list[User]] = relationship(back_populates="agency")
    clients: Mapped[list[Client]] = relationship(back_populates="agency")

    __table_args__ = (
        CheckConstraint("seat_limit >= 1", name="seat_limit_positive"),
        CheckConstraint("length(slug) >= 2", name="slug_min_length"),
        # BRANDING IS VALIDATED TWICE, AT THE SCHEMA AND HERE — Epic 9.22.
        #
        # Both values are agency-supplied and both end up on an
        # UNAUTHENTICATED page: the logo as an `<img src>`, the accent as a CSS
        # custom property value. `schemas/agency.py` is where a bad one is
        # rejected with a readable message; these are where a bad one cannot be
        # stored at all, however it arrived — a script, a fixture, a future
        # endpoint that forgets. Neither is redundant with the other, because
        # they fail at different times for different audiences.
        CheckConstraint(
            "accent_color IS NULL OR accent_color ~ '^#[0-9a-fA-F]{6}$'",
            name="accent_color_is_hex",
        ),
        CheckConstraint(
            "logo_url IS NULL OR logo_url LIKE 'https://%'",
            name="logo_url_is_https",
        ),
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

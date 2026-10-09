"""Client / Prospect — the domain an agency runs scans against."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, SoftDeleteMixin, TimestampMixin, enum_column, fk_column, id_column

if TYPE_CHECKING:
    from .scan import Scan
    from .tenancy import Agency


class ClientKind(str, enum.Enum):
    """A prospect becomes a client when the deal closes.

    The same row transitions rather than being recreated, so the scan history
    gathered while prospecting becomes the "before" half of the before/after
    ROI report in Epic 11.
    """

    PROSPECT = "prospect"
    CLIENT = "client"
    CHURNED = "churned"


class ClassificationStatus(str, enum.Enum):
    """Outcome of Epic 2's industry classification.

    Deliberately mirrors Score.status (scoring-spec.md): a result we could not
    determine is recorded as such, never as a confident-looking guess.
    """

    PENDING = "pending"
    CLASSIFIED = "classified"
    # The crawl succeeded but the evidence did not support a confident call.
    # `industry` stays NULL — see the module note on Client.industry.
    AMBIGUOUS = "ambiguous"
    # The site could not be fetched or carried no usable signal.
    UNCLASSIFIABLE = "unclassifiable"


class Client(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "clients"

    id: Mapped[str] = id_column()
    agency_id: Mapped[str] = fk_column("agencies.id")

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Registrable domain, normalised (lower-case, no scheme, no www, no path).
    domain: Mapped[str] = mapped_column(String(253), nullable=False)

    kind: Mapped[ClientKind] = enum_column(
        ClientKind, name="client_kind", default=ClientKind.PROSPECT
    )

    # --- classification (Epic 2) -----------------------------------------
    #
    # `industry` is NULL unless classification_status is CLASSIFIED. An
    # ambiguous result stores NULL plus a reason code rather than a
    # low-confidence guess: a wrong industry silently poisons Epic 3's
    # competitor detection and Epic 4's prompt generation, and neither has any
    # way to detect that its input was wrong. Same reasoning as Score's
    # INSUFFICIENT_DATA in scoring-spec.md — an undeterminable result must be
    # representable as undeterminable.
    #
    # Free-form string, not an enum: §6 defers per-industry weight tuning until
    # real data exists, and a closed enum decided now would constrain that
    # tuning to categories chosen before seeing a single scan.
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Narrower niche within the industry, e.g. "cosmetic dentistry" under
    # "dental practice". Feeds Epic 3's seed keywords.
    industry_niche: Mapped[str | None] = mapped_column(String(160), nullable=True)

    classification_status: Mapped[ClassificationStatus] = enum_column(
        ClassificationStatus,
        name="classification_status",
        default=ClassificationStatus.PENDING,
    )
    # Machine-readable cause when status is AMBIGUOUS or UNCLASSIFIABLE, e.g.
    # 'FETCH_FAILED', 'INSUFFICIENT_CONTENT', 'LOW_CONFIDENCE'.
    classification_reason_code: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Coarse label the model reports. Kept as the primary signal because an
    # LLM's self-reported confidence is not calibrated, and a label is honest
    # about that where a number implies precision it does not have.
    industry_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Numeric companion, nullable. Added now so a future calibrated threshold
    # can be applied without a migration; nothing reads it yet.
    industry_confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )

    # Provenance: which model was asked to classify the client, and when. Both are NULL until a
    # model has been asked, and for a client whose latest classification asked none (its crawl
    # was refused or failed, or the page had too little text). Written together, never one
    # without the other (`intake.apply_outcome`, launch checklist 3.71).
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classifier_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The brand entity to match against in engine answers. A FACT about the
    # client (their own name), not scraped third-party content.
    brand_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # --- the brand's other sites (M1 task 1.1) ---------------------------------------------
    #
    # A domain with the client's name under another public suffix (rmit.edu.vn for rmit.edu.au) is
    # PROPOSED as its other site (`services/other_sites.py`) and counts as the client's own only
    # once the agency CONFIRMS it; a proposal changes nothing. Both lists hold registrable
    # domains in the DNS FORM (`intake.dns_form`: lower case ASCII, an internationalised label
    # IDNA-encoded, so `bücher.at` is stored as `xn--bcher-kva.at`), written by
    # `PUT /clients/{clientId}/other-sites` and nowhere else: one string per host, and ASCII,
    # which JSONB holds in a database of any encoding. A cited domain keeps the spelling the
    # engine gave, so whatever reads these lists compares through `dns_form`. Nothing reads them
    # yet to type a citation or to score (task 1.2). Every client that exists has confirmed
    # nothing.
    #
    # The domains the agency confirmed as this client's own other sites. A rival's confirmed
    # sites are on its own row (`Competitor.other_sites`): a scan's competitor set is a frozen
    # copy of the client's list, so that is where they have to live.
    other_sites: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # The domains the agency said are NOT any brand's own, the client's or a rival's: one list
    # for the client and all its rivals, and a domain on it is never proposed again.
    dismissed_other_sites: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # --- automatic monthly rescans (F2, 2026-10-06) ----------------------------------------
    # OFF for every client until an owner or admin switches it on (and, in the API, only while
    # `AUTO_RESCAN_ENABLED` is). `services/auto_rescan.py` reads and writes all four columns.
    auto_rescan: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # The user who switched it on: the scan runs as them (their sign-in is the allowance's, and
    # F1 emails them). SET NULL on delete, so a removed user leaves a client that skips with
    # `USER_NOT_ADMIN` and is never scanned on behalf of nobody.
    auto_rescan_by_user_id: Mapped[str | None] = fk_column(
        "users.id", ondelete="SET NULL", nullable=True
    )
    # When the loop last took this client up: the claim (one conditional UPDATE on this column,
    # so only one API instance starts a due client) and the time of a skip.
    auto_rescan_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why the last attempt started no scan (`auto_rescan.SKIP_*`), our own code and never a
    # vendor's words. NULL when the last attempt started a scan, or none was made yet.
    auto_rescan_skip_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    agency: Mapped[Agency] = relationship(back_populates="clients")
    scans: Mapped[list[Scan]] = relationship(back_populates="client")

    __table_args__ = (
        # One row per domain per agency. Two agencies may both prospect the
        # same domain — that is normal and must not collide.
        UniqueConstraint("agency_id", "domain", name="uq_clients_agency_id_domain"),
        Index("ix_clients_agency_kind", "agency_id", "kind", "deleted_at"),
        CheckConstraint(
            "industry_confidence_score IS NULL OR "
            "(industry_confidence_score >= 0 AND industry_confidence_score <= 1)",
            name="industry_confidence_score_range",
        ),
        # The invariant that keeps a guess from masquerading as a result.
        CheckConstraint(
            "(classification_status = 'classified' AND industry IS NOT NULL) OR "
            "(classification_status <> 'classified' AND industry IS NULL)",
            name="industry_matches_classification_status",
        ),
    )

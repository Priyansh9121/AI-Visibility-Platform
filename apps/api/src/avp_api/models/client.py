"""Client / Prospect — the domain an agency runs scans against."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
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

    # Provenance: which model produced the classification, and when.
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classifier_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The brand entity to match against in engine answers. A FACT about the
    # client (their own name), not scraped third-party content.
    brand_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

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

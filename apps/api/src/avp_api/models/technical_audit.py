"""TechnicalAudit — the crawl-based half of the score (§5.3, §5.4 step 6).

Facts-only note: this table records STRUCTURAL SIGNALS about a page, never
its content. Schema.org types present, Core Web Vitals numbers, indexability
booleans, heading and word counts. There is no column for page copy, meta
description text, or rendered HTML. `detail_code` is a machine-readable
enumeration member, not free prose.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column, fk_column, id_column

if TYPE_CHECKING:
    # Import-time only. `ai_crawler_access` names TechnicalAudit as a string in
    # its own relationship, so importing it here at runtime would be a cycle
    # for no gain — SQLAlchemy resolves both ends from the registry.
    from .ai_crawler_access import AiCrawlerAccess


class AuditStatus(str, enum.Enum):
    """Outcome of an audit run.

    Added in Epic 6. Without it a failed crawl and a genuinely bare site are
    indistinguishable — both would be a row of nulls — and the Technical
    Foundation sub-score must never treat "we could not read the site" as
    "the site has no markup". Same discipline as ClassificationStatus,
    DetectionStatus and ScoreStatus.
    """

    OK = "ok"
    # Read the page, but some signals were unavailable (e.g. no date signal).
    PARTIAL = "partial"
    # Could not read the site at all. `technical_foundation` stays NULL.
    FAILED = "failed"


class CheckStatus(str, enum.Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class TechnicalAudit(Base, TimestampMixin):
    __tablename__ = "technical_audits"

    id: Mapped[str] = id_column()
    scan_id: Mapped[str] = fk_column("scans.id")

    url_audited: Mapped[str] = mapped_column(String(2048), nullable=False)

    status: Mapped[AuditStatus] = enum_column(
        AuditStatus, name="audit_status", default=AuditStatus.OK
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # The §6 Technical Foundation sub-score, 0-100, or NULL when the site could
    # not be read. Stored rather than recomputed on read: the full AuditSignals
    # object is transient (the facts-only rule — it is derived from the client's
    # pages and is not persisted in full), so there is nothing to recompute
    # from. Storing the number also makes it auditable at the time it was taken.
    technical_foundation: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    # {component: reason} for components that could not be measured, mirroring
    # Score.excluded_dimensions.
    excluded_components: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    audited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pages_crawled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- Core Web Vitals (§7 Epic 6) -------------------------------------
    lcp_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inp_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cls: Mapped[float | None] = mapped_column(Numeric(5, 3), nullable=True)

    # --- structured data --------------------------------------------------
    # Schema.org TYPE NAMES only (e.g. {'Organization','LocalBusiness'}).
    # Type names are a structural signal, not the page's content.
    schema_types: Mapped[list[str]] = mapped_column(
        ARRAY(String(80)), nullable=False, default=list, server_default="{}"
    )
    has_organization_schema: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_localbusiness_schema: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_faq_schema: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_product_schema: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- indexation / crawlability ---------------------------------------
    is_indexable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    robots_allows_crawl: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_sitemap: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    canonical_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- content structure signals (counts, never copy) -------------------
    h1_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Days since the most recent dateModified/article date found.
    content_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    checks: Mapped[list[TechnicalAuditCheck]] = relationship(
        back_populates="audit", cascade="all, delete-orphan"
    )
    # Epic F. One verdict per AI crawler in `services/ai_crawlers.AGENTS`,
    # read from the same robots.txt fetch that produced `robots_allows_crawl`.
    # Deliberately NOT an input to `technical_foundation` — see the audit
    # service's note on why the scored path was left alone.
    ai_crawler_access: Mapped[list[AiCrawlerAccess]] = relationship(
        back_populates="audit", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("scan_id", name="uq_technical_audits_scan_id"),
        CheckConstraint("cls IS NULL OR cls >= 0", name="cls_non_negative"),
        CheckConstraint("lcp_ms IS NULL OR lcp_ms >= 0", name="lcp_non_negative"),
        CheckConstraint("inp_ms IS NULL OR inp_ms >= 0", name="inp_non_negative"),
    )


class TechnicalAuditCheck(Base, TimestampMixin):
    """One named pass/fail check.

    Epic 6's acceptance is "audit returns pass/fail + detail for each check".
    `detail_code` is an enumerated machine code (e.g. 'MISSING_LOCALBUSINESS'),
    resolved to human copy in the frontend from OUR OWN string table. Storing
    a code rather than a sentence keeps the table facts-only and makes the
    report translatable and white-labelable later.
    """

    __tablename__ = "technical_audit_checks"

    id: Mapped[str] = id_column()
    audit_id: Mapped[str] = fk_column("technical_audits.id")

    check_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[CheckStatus] = enum_column(CheckStatus, name="check_status")
    # Measured value where the check is quantitative (e.g. LCP milliseconds).
    value: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    detail_code: Mapped[str | None] = mapped_column(String(80), nullable=True)

    audit: Mapped[TechnicalAudit] = relationship(back_populates="checks")

    __table_args__ = (
        UniqueConstraint("audit_id", "check_key", name="uq_audit_checks_audit_key"),
        Index("ix_audit_checks_audit_status", "audit_id", "status"),
    )

"""Scan — one execution of the Phase 1 pipeline (product-spec.md §5.4)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column, fk_column, id_column

if TYPE_CHECKING:
    from .client import Client


class ScanStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    # PARTIAL: some engines returned, others failed. A scan where one of three
    # engines was down is still worth reporting on, but the report must be able
    # to say so rather than quietly presenting a depressed score as fact.
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScanTrigger(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"  # Epic 11
    BULK = "bulk"  # Epic 10


class Scan(Base, TimestampMixin):
    __tablename__ = "scans"

    id: Mapped[str] = id_column()
    client_id: Mapped[str] = fk_column("clients.id")
    # Denormalised from client. Every tenant-scoped query filters on it, and
    # carrying it here avoids a join on the hottest read path in the product.
    agency_id: Mapped[str] = fk_column("agencies.id")
    requested_by_user_id: Mapped[str | None] = fk_column(
        "users.id", ondelete="SET NULL", nullable=True
    )

    status: Mapped[ScanStatus] = enum_column(
        ScanStatus, name="scan_status", default=ScanStatus.QUEUED
    )
    trigger: Mapped[ScanTrigger] = enum_column(
        ScanTrigger, name="scan_trigger", default=ScanTrigger.MANUAL
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Machine-readable code plus a SAFE detail string. `error_detail` carries
    # our own diagnostics only — never a vendor response body, which could
    # contain third-party content (ip-safety.md #7).
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance, so a score can always be explained after the fact.
    # scoring-spec.md rule 5: changing weights creates a new version rather
    # than silently altering history.
    formula_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    # {"chatgpt": "gpt-4o-2024-08-06", "perplexity": "sonar-pro"} — identifiers
    # only. Recorded so a score shift can be attributed to an engine change.
    engine_versions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    prompt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engine_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    client: Mapped[Client] = relationship(back_populates="scans")

    __table_args__ = (
        # The dashboard list: this agency's scans, newest first. `id` is a
        # ULID, so it sorts by creation time and doubles as the cursor.
        Index("ix_scans_agency_created", "agency_id", "id"),
        Index("ix_scans_client_created", "client_id", "id"),
        Index("ix_scans_status", "status"),
    )

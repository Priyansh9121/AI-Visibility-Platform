"""Scan — one execution of the Phase 1 pipeline (product-spec.md §5.4)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Index, Integer, String, Text, text
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
    # THE LEASE — API key discipline audit, 2026-09-07.
    #
    # When the executor holding this scan is presumed gone unless it has since
    # said otherwise. Stamped by the claim, renewed by the executor while it
    # works, read by the reaper — which keys off this column and not off
    # `started_at`, so a scan is reaped for going quiet rather than for taking
    # long. NULL means "not held under a lease": QUEUED, finished, or driven
    # directly through `scan_runner.run_scan`, which holds none. The reaper's
    # predicate is strict, so NULL is never "expired". `services/scan_executor.py`
    # has the mechanism in full.
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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

    # THE PUBLIC SHARE LINK — Epic 9.8.
    #
    # Nullable and minted on demand, never at scan creation. Most scans are
    # never shared, and a token that exists is a URL that works: minting one
    # for every scan would create a live public link for every scan ever run
    # and then rely on nobody learning it. Absent by default is the safer
    # state, and it makes "is this shared?" answerable by reading the column.
    #
    # NOT the scan's own ULID. `id` is already handed to the browser, appears
    # in the authenticated URL and is logged; reusing it would mean anyone who
    # ever saw a scan id could read that report forever. This is an independent
    # 256-bit secret (`security.new_share_token`, the same generator and the
    # same entropy as a session token).
    #
    # DELIBERATELY NOT BUILT, and this is a known gap rather than an oversight:
    # there is NO EXPIRY and NO REVOCATION. Once minted, the link works until
    # the row is deleted. That is an accepted risk for a pilot conversation and
    # is NOT acceptable as a permanent design — the first agency that shares a
    # report with the wrong prospect has no way to take it back. Revocation
    # (clearing the column) and expiry (a `share_expires_at`) are the next two
    # columns this table should grow. See build-log Epic 9.8.
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    client: Mapped[Client] = relationship(back_populates="scans")

    __table_args__ = (
        # The dashboard list: this agency's scans, newest first. `id` is a
        # ULID, so it sorts by creation time and doubles as the cursor.
        Index("ix_scans_agency_created", "agency_id", "id"),
        Index("ix_scans_client_created", "client_id", "id"),
        Index("ix_scans_status", "status"),
        # AT MOST ONE OPEN SCAN PER CLIENT — Epic 9.6.
        #
        # `get_or_create_scan` has always intended this: it looks for an open
        # scan and reuses it rather than starting a second. But a SELECT
        # followed by an INSERT is a check-then-act, and two concurrent
        # requests can both look, both find nothing, and both insert. Epic 9.5
        # narrowed that window from ~303s to milliseconds by committing early;
        # only the database can close it.
        #
        # A losing INSERT now blocks until the winner commits and then raises a
        # unique violation, which `get_or_create_scan` treats as "someone else
        # got there first" and resolves by returning the winner's row.
        #
        # Partial, because the invariant is about OPEN scans only: a client
        # accumulates any number of finished ones. `status` is VARCHAR-backed
        # with a CHECK constraint (models/base.py, `native_enum=False`), so the
        # predicate is a plain string comparison and needs no enum casting.
        Index(
            "uq_scans_one_open_per_client",
            "client_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        # Unique, and the lookup index for GET /reports/{token} — Epic 9.8.
        #
        # Partial on NOT NULL for both halves of that. Postgres treats NULLs as
        # distinct so a plain unique index would already permit many unshared
        # scans, but saying so explicitly keeps the index off every row that
        # has no token — which is most of them — and makes the intent readable.
        #
        # Independent of uq_scans_one_open_per_client above: that one constrains
        # `client_id` over open scans, this one constrains `share_token` over
        # shared scans. Different column, different predicate, no interaction.
        Index(
            "uq_scans_share_token",
            "share_token",
            unique=True,
            postgresql_where=text("share_token IS NOT NULL"),
        ),
    )

"""CompetitorSet and Competitor — the auto-detected rival set for a scan.

Facts-only note: a competitor is stored as NAME + DOMAIN +
provenance, and nothing else. There is deliberately no description, tagline,
summary, or positioning column. Entity names and domains are facts and are
explicitly permitted; a competitor's marketing copy is theirs and is not stored
anywhere in this system.
"""

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
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column, fk_column, id_column

if TYPE_CHECKING:
    pass


class DetectionSource(str, enum.Enum):
    SERP = "serp"  # SerpApi organic results only
    CO_CITATION = "co_citation"  # co-mentioned in AI engine answers only
    BOTH = "both"  # surfaced independently by SERP *and* co-citation
    MANUAL = "manual"  # agency override


class DetectionStatus(str, enum.Enum):
    """Outcome of a competitor-detection run.

    Mirrors ClassificationStatus and Score's INSUFFICIENT_DATA: a detection we
    could not make confidently must be representable as such, rather than
    surfacing a thin or contradictory set as though it were a finding.
    """

    OK = "ok"
    # Ran, but few candidates and little cross-signal agreement. The set is
    # shown with a warning and is the prime candidate for manual override.
    WEAK_SIGNAL = "weak_signal"
    # Nothing usable came back from either signal.
    NO_SIGNAL = "no_signal"


class CompetitorSet(Base, TimestampMixin):
    __tablename__ = "competitor_sets"

    id: Mapped[str] = id_column()
    scan_id: Mapped[str] = fk_column("scans.id")

    status: Mapped[DetectionStatus] = enum_column(
        DetectionStatus, name="detection_status", default=DetectionStatus.OK
    )

    # Cross-signal agreement, 0-1: the share of the returned set that SERP and
    # co-citation surfaced INDEPENDENTLY of each other.
    #
    # This is the number Epic 5 should consult before presenting a Share of
    # Voice comparison as authoritative. A composite score computed against a
    # competitor set that only one signal ever saw is a weaker claim than one
    # computed against a set both signals agreed on, and the report should be
    # able to say which it is.
    #
    # Deliberately NOT a measure of whether the competitors are "correct" —
    # nothing here can know that. It measures agreement, which is the only
    # thing actually observed.
    detection_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )

    # Provenance, so a surprising set can be explained after the fact.
    serp_queries_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    co_citation_prompts_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_considered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Whether Client.industry was available to seed SERP queries.
    #
    # Recorded because industry classification confidence is uncalibrated
    # (build-log Epic 2.6/2.8, Finding 2 — still open). When an operator reports
    # a bad competitor set, the first question is whether it was seeded from a
    # bad industry label, and this makes that answerable without re-running.
    used_industry_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    competitors: Mapped[list[Competitor]] = relationship(
        back_populates="competitor_set", cascade="all, delete-orphan"
    )

    @property
    def detected_competitors(self) -> list[Competitor]:
        """The active rows `detection_confidence` is a statement about.

        The figure is the share of the returned set that SERP and co-citation
        surfaced INDEPENDENTLY of each other, so it is computed over the rows
        detection produced — never over rows an operator set by hand, which no
        automated signal corroborated.

        Once a set is mixed, presenting the figure without saying how much of
        the set it describes is the defect Finding 3 recorded. Derived here
        rather than stored so it cannot go stale against the rows it counts.
        """
        return [c for c in self.active_competitors if not c.is_manual_override]

    @property
    def active_competitors(self) -> list[Competitor]:
        """The set as everything downstream should see it.

        `competitors` is the raw collection and includes suppressed tombstones —
        rows kept only so re-detection knows not to re-offer a rival an operator
        struck. Every read path (scoring, the report, prompt seeding, fix
        generation, the API) must go through here instead, so "what the set is"
        has ONE definition rather than seven copies of a filter that will
        eventually disagree.

        Returned in rank order: the relationship has no ordering guarantee, and
        a comparison table that reshuffles between requests reads as a bug.
        """
        return sorted(
            (c for c in self.competitors if not c.is_suppressed),
            key=lambda c: (c.rank, c.id),
        )

    __table_args__ = (
        UniqueConstraint("scan_id", name="uq_competitor_sets_scan_id"),
        CheckConstraint(
            "detection_confidence IS NULL OR "
            "(detection_confidence >= 0 AND detection_confidence <= 1)",
            name="detection_confidence_range",
        ),
    )


class Competitor(Base, TimestampMixin):
    __tablename__ = "competitors"

    id: Mapped[str] = id_column()
    competitor_set_id: Mapped[str] = fk_column("competitor_sets.id")

    # Facts only.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)

    # 1 = strongest rival. §7 Epic 3 ranks the top 3-5.
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    detection_source: Mapped[DetectionSource] = enum_column(
        DetectionSource, name="detection_source"
    )
    # How many independent signals surfaced this competitor. Used for ranking
    # confidence, not shown as a metric.
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Per-signal evidence counts, kept so a ranking can be audited rather than
    # taken on trust. Counts are facts; the search results and engine answers
    # they were derived from are not stored (the facts-only rule).
    serp_mentions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    co_citation_mentions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Surfaced independently by both signals. The strongest evidence available
    # that this is a real competitor rather than an artefact of one query shape.
    corroborated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # The ranking score this competitor was ordered by. Stored so a set can be
    # explained ("why is X above Y?") without re-running detection.
    score: Mapped[Decimal | None] = mapped_column(Numeric(7, 3), nullable=True)
    # True when an operator added or kept this competitor by hand (Epic 3's
    # manual override UI). Protects it from being dropped on re-detection.
    is_manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True when an operator STRUCK this competitor. The row is kept rather than
    # deleted, because a deleted row records nothing: re-detection would surface
    # the same rival again, match no override, and reinstate it. Epic 3 shipped
    # `is_manual_override` and preserved additions correctly, but had no way to
    # express a removal, so half of "correcting a set" silently reverted on the
    # next run. Found by scripts/verify_competitor_override.py against real
    # data; see build-log Epic 3.6.
    #
    # A suppressed row is a tombstone, not a competitor. It is excluded from
    # every read path — scoring, the report, prompt seeding and fix generation
    # all filter it — and exists only so `persist_detection` knows not to offer
    # this rival again.
    is_suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    competitor_set: Mapped[CompetitorSet] = relationship(back_populates="competitors")

    __table_args__ = (
        UniqueConstraint("competitor_set_id", "name", name="uq_competitors_set_name"),
        CheckConstraint("rank >= 1", name="rank_positive"),
        Index("ix_competitors_set_rank", "competitor_set_id", "rank"),
    )

"""ActionItem — the prioritised fix list (§5.3, generated in Epic 8).

`title` and `detail` hold free text, and that is correct: these are OUR OWN
generated recommendations, not scraped material (ip-safety.md #7).

`points_upside` ties each fix back to the ledger gap calculation in
docs/scoring-spec.md, so the report never asserts that a fix matters without
saying how much it is worth.

Epic 8 additions
----------------
`source` + `source_key` are the row's identity. Together with `scan_id` they
reproduce the business key the report client already mints for every candidate
fix — `gap:<dimension>` / `audit:<check_key>`, see apps/web/src/lib/report/
derive.ts — which is what lets a regenerated fix land back on the row it
replaces instead of duplicating it. Before Epic 8 the table had no unique key
at all, so nothing prevented two rank-1 rows on one scan.

`generated_by` records which model wrote the row, following the
`prompt_sets.generated_by` precedent rather than the sentiment path, which
records nothing and leaves a changed judge model unattributable.
"""

from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, enum_column, fk_column, id_column


class Priority(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Effort(str, enum.Enum):
    S = "S"
    M = "M"
    L = "L"


class ActionItemSource(str, enum.Enum):
    """Which Epic 7 candidate produced this fix.

    Deliberately only two members. An LLM-authored fix is not a third source —
    it is the same measured gap or audit finding, worded better, and modelling
    it as its own source would let a generated recommendation exist without a
    measurement behind it.
    """

    GAP = "gap"
    AUDIT = "audit"


class ActionItemStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DISMISSED = "dismissed"


class ActionItem(Base, TimestampMixin):
    __tablename__ = "action_items"

    id: Mapped[str] = id_column()
    scan_id: Mapped[str] = fk_column("scans.id")

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    priority: Mapped[Priority] = enum_column(Priority, name="action_priority")
    effort: Mapped[Effort] = enum_column(Effort, name="action_effort")
    status: Mapped[ActionItemStatus] = enum_column(
        ActionItemStatus, name="action_item_status", default=ActionItemStatus.OPEN
    )

    source: Mapped[ActionItemSource] = enum_column(ActionItemSource, name="action_item_source")
    # The identity of the candidate within its source: a dimension key for a
    # gap fix, a check_key for an audit fix. Never prefixed — the prefix is
    # `source`, and storing it twice invites the two disagreeing.
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)

    # Which §6 dimension this fix targets. One of score.DIMENSION_KEYS.
    dimension_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Recoverable points, from the ledger gap maths.
    points_upside: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # Model that authored `title`/`detail`/`priority`/`effort`, or NULL for a
    # row written from the deterministic string table alone.
    generated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    __table_args__ = (
        # One row per candidate per scan. Regeneration refreshes in place; see
        # services/fix_generator.persist_fixes for why that is the right shape
        # for an entity carrying operator `status`.
        UniqueConstraint("scan_id", "source", "source_key", name="uq_action_items_scan_source_key"),
        Index("ix_action_items_scan_rank", "scan_id", "rank"),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint(
            "points_upside IS NULL OR (points_upside >= 0 AND points_upside <= 100)",
            name="points_upside_range",
        ),
    )

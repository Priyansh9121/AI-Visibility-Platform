"""Score — the composite and its sub-scores (§5.3, formula in §6).

The scoring ENGINE is Epic 5. This table exists now because the determinism
guarantees in docs/scoring-spec.md are schema-level commitments, and retrofitting
them onto rows already written is far harder than starting with them:

  * **rule 3 — Decimal, not float.** Every score column is `NUMERIC(5,2)`,
    which round-trips to Python `Decimal`. A `double precision` column would
    reintroduce binary-float error at the storage boundary even if the
    calculation itself used Decimal.
  * **rule 5 — version the formula.** `formula_version` is NOT NULL on every
    row, and the uniqueness constraint is (scan_id, formula_version). Re-scoring
    a scan under new weights therefore INSERTS a new row rather than mutating
    the old one, so the before/after ROI reporting in Epic 11 compares like with
    like.
  * **rule 1 — no hidden inputs.** `inputs_digest` fingerprints the
    EngineResult set the score was computed from. Recomputing and getting a
    different digest means the inputs changed, not that scoring is
    non-deterministic — which is exactly the distinction needed when a client
    disputes a number.
  * **the INSUFFICIENT_DATA case.** `composite` is nullable and `status`
    distinguishes a genuine zero from an unscoreable scan. A scan with no
    prompts must never render to a client as a score of 0.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    TimestampMixin,
    enum_column,
    fk_column,
    id_column,
    score_column,
    score_range_check,
)

# The five §6 dimensions, as stable keys. The scoring engine and the frontend
# both reference these; defining them once stops the two drifting apart.
DIMENSION_KEYS: tuple[str, ...] = (
    "mention_rate",
    "share_of_voice",
    "citation_strength",
    "sentiment",
    "technical_foundation",
)

# §6 default weights. Per-industry tuning is deferred until real data exists;
# when it arrives it becomes a new formula_version, not an edit to these.
DEFAULT_WEIGHTS: dict[str, int] = {
    "mention_rate": 30,
    "share_of_voice": 25,
    "citation_strength": 20,
    "sentiment": 15,
    "technical_foundation": 10,
}


class ScoreStatus(str, enum.Enum):
    SCORED = "scored"
    # Not zero. There was not enough data to compute a score at all.
    INSUFFICIENT_DATA = "insufficient_data"


class Score(Base, TimestampMixin):
    __tablename__ = "scores"

    id: Mapped[str] = id_column()
    scan_id: Mapped[str] = fk_column("scans.id")

    status: Mapped[ScoreStatus] = enum_column(
        ScoreStatus, name="score_status", default=ScoreStatus.SCORED
    )
    # NULL when status is INSUFFICIENT_DATA.
    composite: Mapped[Decimal | None] = score_column()

    # --- §6 sub-scores, each normalised 0-100 ----------------------------
    mention_rate: Mapped[Decimal | None] = score_column()
    share_of_voice: Mapped[Decimal | None] = score_column()
    citation_strength: Mapped[Decimal | None] = score_column()
    sentiment: Mapped[Decimal | None] = score_column()
    technical_foundation: Mapped[Decimal | None] = score_column()

    # --- provenance -------------------------------------------------------
    # 40 chars, not 16: §6 anticipates per-industry tuning, so real version
    # strings look like "v2-industry-dental", not just "v1".
    formula_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    # The weights actually applied, so a stored score is self-describing even
    # after DEFAULT_WEIGHTS changes.
    weights: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=lambda: dict(DEFAULT_WEIGHTS)
    )
    # Dimensions excluded and had their weight redistributed. scoring-spec.md:
    # a brand with zero mentions has no sentiment, and scoring that 0 would
    # double-punish the same absence.
    # {dimension: reason_code}, e.g.
    #   {"sentiment": "NO_POPULATION",
    #    "technical_foundation": "NOT_YET_MEASURED",
    #    "share_of_voice": "NO_COMPETITOR_SET"}
    #
    # A dict rather than a list of names, because the REASON is what a report
    # has to act on: "we have not checked this yet" (capability missing) and
    # "there was nothing to measure" (no data for this brand) must be worded
    # differently to a client, and a bare list of excluded names cannot tell
    # them apart. Epic 5 changed this from ARRAY(String) for that reason.
    excluded_dimensions: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # e.g. {'citation_authority_unavailable'} — records that a sub-score used a
    # documented fallback path, so a depressed number can be explained.
    degradation_flags: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)), nullable=False, default=list, server_default="{}"
    )
    reason_code: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # SHA-256 over the ordered EngineResult + audit inputs. See module docstring.
    inputs_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Re-scoring under a new formula version inserts; it never overwrites.
        UniqueConstraint("scan_id", "formula_version", name="uq_scores_scan_formula"),
        Index("ix_scores_scan", "scan_id"),
        score_range_check("composite"),
        score_range_check("mention_rate"),
        score_range_check("share_of_voice"),
        score_range_check("citation_strength"),
        score_range_check("sentiment"),
        score_range_check("technical_foundation"),
        CheckConstraint(
            "inputs_digest IS NULL OR length(inputs_digest) = 64",
            name="inputs_digest_is_sha256",
        ),
        # A scored row must have a composite; an insufficient-data row must not.
        CheckConstraint(
            "(status = 'scored' AND composite IS NOT NULL) OR "
            "(status = 'insufficient_data' AND composite IS NULL)",
            name="composite_matches_status",
        ),
    )

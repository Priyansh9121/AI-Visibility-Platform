"""Score schemas — Epic 5.

ip-safety.md #7: every field is a number, a label, a flag, or a digest. Scoring
reads persisted facts and produces arithmetic; there is no path by which
third-party content could reach this surface.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from ..models.score import ScoreStatus
from .common import ApiModel


class CompetitorScoreOut(ApiModel):
    """Per-competitor figures for the three measurable dimensions.

    **There is deliberately no `composite`.** Sentiment is classified toward the
    subject only (Epic 4.3) and Technical Foundation arrives in Epic 6, so 25%
    of the weight has no per-competitor input. A composite computed over a
    different weight basis would not be comparable to the subject's, which is
    the entire purpose of a comparison.
    """

    competitor_id: str
    name: str
    mention_rate: Decimal
    share_of_voice: Decimal
    citation_strength: Decimal


class ScoreOut(ApiModel):
    id: str
    scan_id: str
    status: ScoreStatus

    # Null when status is insufficient_data. An unrunnable scan is never a zero.
    composite: Decimal | None = None

    mention_rate: Decimal | None = None
    share_of_voice: Decimal | None = None
    citation_strength: Decimal | None = None
    sentiment: Decimal | None = None
    technical_foundation: Decimal | None = None

    formula_version: str
    # Other formula versions this scan has ALSO been scored under, oldest
    # first — empty for a scan scored once, which is most of them.
    #
    # Present so a changed number can be explained. Rule 5 keeps every version's
    # row, so re-scoring under v2 leaves the v1.1 row intact and the reader sees
    # a different composite than they saw last week. Without this the only
    # honest reading available to them is "the score dropped", which is a claim
    # about their business; the true one is "the definition changed", which is
    # a claim about ours. The report says which.
    previous_formula_versions: list[str] = []
    # The EFFECTIVE weights used, after any exclusions — so the breakdown
    # re-sums against its own weights.
    weights: dict = Field(default_factory=dict)
    # dimension -> reason. `NOT_YET_MEASURED` (capability missing) is distinct
    # from `NO_POPULATION` (nothing to measure for this brand).
    excluded_dimensions: dict = Field(default_factory=dict)
    degradation_flags: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    # SHA-256 of the exact inputs. Two scores with the same digest were computed
    # from identical data.
    inputs_digest: str | None = None
    computed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScoreDetailOut(ScoreOut):
    competitors: list[CompetitorScoreOut] = Field(default_factory=list)

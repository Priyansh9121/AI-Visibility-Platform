"""Competitor detection schemas.

ip-safety.md #7: every field here is a name, a domain, a count, or a score.
There is no field capable of carrying a search-result snippet or an engine
answer, and that is enforced by the type rather than by review.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from ..models import DetectionSource, DetectionStatus
from .common import ApiModel


class CompetitorOut(ApiModel):
    id: str
    name: str
    domain: str | None = None
    rank: int
    detection_source: DetectionSource
    # Per-signal evidence, so a ranking can be audited rather than trusted.
    serp_mentions: int
    co_citation_mentions: int
    corroborated: bool
    signal_count: int
    score: Decimal | None = None
    is_manual_override: bool


class CompetitorSetOut(ApiModel):
    id: str
    scan_id: str
    status: DetectionStatus

    # Cross-signal agreement, 0-1, or null when it could not be measured
    # because only one signal ran. Null is NOT zero — see the model docstring.
    detection_confidence: Decimal | None = None

    serp_queries_run: int
    co_citation_prompts_run: int
    candidates_considered: int
    used_industry_seed: bool
    detected_at: datetime | None = None

    competitors: list[CompetitorOut] = Field(default_factory=list)


class CompetitorInput(ApiModel):
    """One competitor in an operator's replacement set."""

    name: str = Field(min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=253)


class ReplaceCompetitorsRequest(ApiModel):
    """Operator override for a detected competitor set.

    Replaces the set wholesale rather than patching individual rows: an operator
    correcting a bad detection is expressing "these are the rivals", and
    reconciling that against auto-detected rows one at a time invites a
    half-applied state where the correction is neither in force nor discarded.

    Every competitor supplied here is marked `isManualOverride`, which protects
    it from being removed by a later re-detection.
    """

    competitors: list[CompetitorInput] = Field(default_factory=list, max_length=10)

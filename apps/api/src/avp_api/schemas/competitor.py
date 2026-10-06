"""Competitor detection schemas.

The facts-only rule: every field here is a name, a domain, a count, or a score.
There is no field capable of carrying a search-result snippet or an engine
answer, and that is enforced by the type rather than by review.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from ..models import DetectionSource, DetectionStatus
from ..services.intake import InvalidUrl, rival_domain
from ..services.serp import is_non_competitor_domain
from .common import ApiModel

# The refusal of a platform or publisher typed as a rival (launch checklist 3.90). The list is
# detection's own (`serp.NON_COMPETITOR_DOMAINS`) and holds publishers, marketplaces, review sites
# and directories, social networks, wikis, job boards, search engines and the big platforms, so
# the sentence says "platform or publisher". No domain in it: nothing typed is echoed.
A_PLATFORM_NOT_A_RIVAL = "That is a platform or publisher, so it cannot be added as a rival."


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
    # The scan this set belongs to, or null for the client's NEXT LIST: the list a save or the
    # button's detection wrote, which belongs to no scan until the next scan adopts it. A scan's
    # own set never changes once the scan has started; the next list is the only one that does.
    scan_id: str | None = None
    status: DetectionStatus

    # Cross-signal agreement, 0-1, or null when it could not be measured
    # because only one signal ran. Null is NOT zero — see the model docstring.
    detection_confidence: Decimal | None = None
    # How many of the rows below came from DETECTION rather than an operator —
    # i.e. how many of them `detectionConfidence` is a statement about.
    #
    # The figure measures agreement between two automated signals. Rows an
    # operator set by hand were corroborated by neither, so on a mixed set a
    # lone confidence number is presented for more rows than it describes.
    # That was Finding 3. Publishing the scope alongside it lets a reader see
    # the gap instead of guessing at it: "0.80 across 4 of these 6" is honest
    # where a bare "0.80" is not. Equals the row count when nothing was
    # overridden.
    #
    # It is NOT a proxy for `detectionConfidence` being present. A set where
    # only one signal ran carries a null confidence and a full complement of
    # detected rows, and a set where re-detection re-found nothing but rivals
    # the operator had already named carries a real confidence and zero of
    # them. Read the two fields together; neither implies the other.
    #
    # Not quite the figure's own denominator, and deliberately not claimed to
    # be: detection computes it over every candidate it ranked, including any
    # the operator had already named or struck by hand, which are then held
    # back from the set. So this is the count of rows the reader can actually
    # see it apply to, which is the number the report needs.
    confidence_covers: int = 0

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

    @field_validator("domain")
    @classmethod
    def _domain_is_the_rivals_own(cls, value: str | None) -> str | None:
        """The rival's own domain, checked as a client's is and never reduced (checklist 3.81),
        and not a platform or publisher (3.90).

        Blank is no domain, as it always was. Anything else goes through `rival_domain`: a page, a
        path or a subdomain is refused and not cut down to another site's domain. What is left
        must not be on detection's own list (`serp.is_non_competitor_domain`), so a typed rival
        obeys the rule a detected one does. `rival_domain` itself does not check the list, on
        purpose: a model's domain goes through it too, and a refusal there would keep the brand
        with no domain, where detection drops a platform it names whole. Each error is a
        `PydanticCustomError`, so `errors[].message` is the plain sentence and not pydantic's
        "Value error, " in front of it.
        """
        if value is None or not value.strip():
            return None
        try:
            domain = rival_domain(value)
        except InvalidUrl as exc:
            raise PydanticCustomError("invalid_domain", exc.detail) from exc
        if is_non_competitor_domain(domain):
            raise PydanticCustomError("invalid_domain", A_PLATFORM_NOT_A_RIVAL)
        return domain


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

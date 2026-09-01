"""Technical audit schemas — Epic 6.

ip-safety.md #7: every field is a boolean, a count, a schema TYPE NAME, a
duration, or a day count. There is deliberately no field capable of carrying
page copy, a meta description's text, or an OG tag's value.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from ..models.technical_audit import AuditStatus, CheckStatus
from .common import ApiModel


class AuditCheckOut(ApiModel):
    """One check's verdict — §7's "pass/fail + detail for each check"."""

    id: str
    check_key: str
    status: CheckStatus
    # Numeric evidence where a check has one (LCP ms, CLS, schema type count,
    # content age in days). Null for purely boolean checks.
    value: Decimal | None = None
    # Machine-readable cause, e.g. NO_STRUCTURED_DATA, META_ROBOTS_NOINDEX,
    # FIELD_METRIC_REQUIRES_REAL_USER_DATA. Never prose.
    detail_code: str | None = None


class TechnicalAuditOut(ApiModel):
    id: str
    scan_id: str
    url_audited: str
    status: AuditStatus
    error_code: str | None = None
    pages_crawled: int

    # The §6 Technical Foundation sub-score, 0-100. Null when the site could
    # not be read — an unreachable site is not a site with a bad foundation.
    technical_foundation: Decimal | None = None
    excluded_components: dict = Field(default_factory=dict)

    # The four weighted parts the sub-score is made of, each 0-100 — Epic 9.22.
    #
    # **Derived on read, never stored**, exactly like `CompetitorComparison`
    # (`scoring_runner.score_scan`: "a pure function of rows already persisted,
    # so storing it would create a second copy that can fall out of step").
    # `compute_components` reads only columns this row already carries —
    # `is_indexable`, `has_sitemap`, `canonical_present`, `schema_types`, the
    # four schema flags and `content_age_days` — so recomputing costs a
    # dictionary and re-crawls nothing.
    #
    # Exposed because the sub-score is a WEIGHTED SUM of these, which makes it
    # drawable as a Luminance Ledger whose total lit height IS the Technical
    # Foundation score. Without them the screen could only show one number.
    components: dict[str, Decimal] = Field(default_factory=dict)
    # The weight actually applied to each component after redistribution, so a
    # reader can see why an excluded component did not simply score zero.
    component_weights: dict[str, Decimal] = Field(default_factory=dict)

    # --- Core Web Vitals -------------------------------------------------
    # LAB measurements from a single cold load, NOT field data. They are
    # reported per §7 but carry no weight in the sub-score: letting a noisy
    # measurement move a client-facing number would make the number noisy.
    # `inpMs` is always null — INP measures real user interaction latency and
    # cannot be produced by a crawler that never interacts.
    lcp_ms: int | None = None
    inp_ms: int | None = None
    cls: Decimal | None = None

    # --- schema / structured data ---
    schema_types: list[str] = Field(default_factory=list)
    has_organization_schema: bool
    has_localbusiness_schema: bool
    has_faq_schema: bool
    has_product_schema: bool

    # --- indexation / crawlability ---
    is_indexable: bool
    robots_allows_crawl: bool
    has_sitemap: bool
    canonical_present: bool

    # --- structure / freshness ---
    h1_count: int
    word_count: int
    content_age_days: int | None = None

    audited_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    checks: list[AuditCheckOut] = Field(default_factory=list)

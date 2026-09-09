"""AiCrawlerAccess — what a site's robots.txt asks one AI crawler to do. Epic F.

Facts-only note: this table records a VERDICT and a COUNT per agent. There
is no column capable of holding a path, a rule body, or any part of the
client's robots.txt file. `matched_token` is the user-agent token that decided
the verdict — a product name like `gptbot` or the literal `*`, never a URL.

=============================================================================
WHY THE VENDOR AND PURPOSE ARE STORED RATHER THAN LOOKED UP
=============================================================================
Both are derivable from `services/ai_crawlers.AGENTS`, so storing them is
denormalisation, and it is deliberate for the reason `technical_audit.py`
gives for storing `content_age_days` and `technical_foundation`: a scan is a
point-in-time claim, and it must still read correctly after the thing it was
derived from changes.

The roster WILL change — vendors ship new crawlers and reclassify existing
ones. When `Google-Extended` is reclassified, or an agent is retired from the
roster entirely, every historical row must keep saying what this product
claimed at the time it was measured. A join to a constant that has since moved
would silently rewrite the past.

=============================================================================
WHY THIS HANGS OFF THE AUDIT AND NOT OFF THE SCAN
=============================================================================
The rows are a product of the audit's robots.txt fetch — the same fetch, on
the same request, in `technical_audit._fetch_side_files`. Hanging them off
`technical_audits` keeps that provenance in the schema rather than in a
comment, and makes the cascade correct for free: re-auditing a scan replaces
its policy rows the same way it replaces its checks.

It also gets the failure case right without extra work. The audit's side fetch
runs BEFORE the page render and has its own error handling, so a site whose
homepage will not render can still produce a full set of policy rows — and a
site whose robots.txt was unreachable produces a full set of `UNKNOWN` rows
rather than none at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..services.ai_crawlers import AccessVerdict, AgentPurpose, RuleSource
from .base import Base, TimestampMixin, enum_column, fk_column, id_column

if TYPE_CHECKING:
    # Import-time only, and mirrored by the same guard in `technical_audit.py`.
    # Both ends name the other as a string in their relationship, so SQLAlchemy
    # resolves them from the registry and neither import exists at runtime.
    from .technical_audit import TechnicalAudit

__all__ = ["AiCrawlerAccess"]


class AiCrawlerAccess(Base, TimestampMixin):
    """One (audit x AI crawler) policy verdict.

    The enums live in `services/ai_crawlers.py` rather than here, against this
    package's usual convention, because they are the PARSER'S vocabulary and
    the parser is pure and database-free by design. Defining them here would
    make a module that must be testable without SQLAlchemy import SQLAlchemy.
    """

    __tablename__ = "ai_crawler_access"

    id: Mapped[str] = id_column()
    audit_id: Mapped[str] = fk_column("technical_audits.id")

    # The agent's product token, in the vendor's documented spelling.
    agent_token: Mapped[str] = mapped_column(String(80), nullable=False)
    vendor: Mapped[str] = mapped_column(String(80), nullable=False)
    purpose: Mapped[AgentPurpose] = enum_column(AgentPurpose, name="agent_purpose")

    verdict: Mapped[AccessVerdict] = enum_column(AccessVerdict, name="access_verdict")
    rule_source: Mapped[RuleSource] = enum_column(RuleSource, name="rule_source")

    # The user-agent token of the group that decided it, lowercased: a product
    # name or the literal '*'. NULL when no group applied.
    matched_token: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # How many Disallow rules the deciding group carries. A COUNT, never the
    # paths — see the module note. Qualifies ALLOWED; it is not a verdict of
    # its own, and `services/ai_crawlers.AccessVerdict` records why not.
    disallow_rules: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    audit: Mapped[TechnicalAudit] = relationship(
        "TechnicalAudit", back_populates="ai_crawler_access"
    )

    __table_args__ = (
        # One verdict per agent per audit. Without it a re-audit that failed to
        # clear its previous rows would double every count the screen shows.
        UniqueConstraint("audit_id", "agent_token", name="uq_ai_crawler_access_audit_agent"),
        Index("ix_ai_crawler_access_audit_verdict", "audit_id", "verdict"),
        CheckConstraint("disallow_rules >= 0", name="disallow_rules_non_negative"),
        # A verdict of UNSPECIFIED or UNKNOWN means no group applied, so there
        # is nothing for a matched token to name. Enforced rather than trusted:
        # the two states differ only by which constructor produced them, and a
        # stray token here would be the visible symptom of them being merged.
        CheckConstraint(
            "(verdict IN ('unspecified', 'unknown') AND matched_token IS NULL) "
            "OR (verdict IN ('allowed', 'blocked') AND matched_token IS NOT NULL)",
            name="matched_token_matches_verdict",
        ),
    )

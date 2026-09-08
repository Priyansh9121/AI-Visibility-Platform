"""EngineResult, Citation, BrandMention — what each AI engine actually returned.

=============================================================================
IP-SAFETY CRITICAL TABLE  (docs/ip-safety.md constraint 7)
=============================================================================

The rule: "Scraped data from AI engines or competitor pages is for FACTS ONLY
(mention counts, citation presence, schema presence, structural signals). Never
store, render, or republish a competitor's actual copyrighted text."

This schema is designed so that rule cannot be violated by accident. There is
**no column anywhere in this module capable of holding an engine's answer
text**: no `raw_response`, no `answer`, no `snippet`, no `excerpt`, no
`context`, no generic JSONB payload column.

What IS stored, and why each is a fact rather than content:
  * `mentioned`          - boolean presence
  * `position`           - ordinal ("named 3rd")
  * `prominence`         - normalised 0-1 derived from position
  * `sentiment`          - a classification LABEL, not the text classified
  * `brands_mentioned`   - a count
  * `response_digest`    - SHA-256 of the answer. Lets Epic 12 detect that an
                           answer CHANGED without retaining what it said.
  * citations            - domain + URL + type. Explicitly permitted.
  * brand mentions       - entity NAMES. Explicitly permitted.

The raw answer exists only transiently inside a worker process while these
facts are extracted, and is discarded. A test in tests/test_ip_safety.py
asserts this module introduces no text-bearing column, so adding one later
fails CI rather than passing review unnoticed.

If a future feature appears to require the raw text, that is a STOP-AND-FLAG
moment, not a migration.
=============================================================================
"""

from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column, fk_column, id_column


class Engine(str, enum.Enum):
    """AI answer engines. §7 Epic 4 starts with two, behind a common interface."""

    CHATGPT = "chatgpt"
    PERPLEXITY = "perplexity"
    GOOGLE_AI_OVERVIEW = "google_ai_overview"
    GEMINI = "gemini"
    CLAUDE = "claude"
    # Claude with the server-side web_search tool. Modelled as a SEPARATE engine
    # rather than a flag on CLAUDE because it answers differently in the way
    # this product measures: parametric recall names brands from training,
    # grounded search names brands it just retrieved and returns real cited
    # URLs. A scan must be able to say "you are absent from grounded answers
    # but present in parametric ones", which needs two rows, not one.
    CLAUDE_SEARCH = "claude_search"
    COPILOT = "copilot"


class Sentiment(str, enum.Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class EngineResultStatus(str, enum.Enum):
    OK = "ok"
    # The engine answered but the brand was absent. Distinct from ERROR: a
    # confirmed absence is a valid, scoreable data point; an error is not.
    ANSWERED_NO_MENTION = "answered_no_mention"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    TIMEOUT = "timeout"
    # --- Incomplete answers (API key discipline audit, 2026-09-07) ----------
    #
    # Neither of these is an answer, and neither is a provider failure. Before
    # they existed an engine that stopped early was read as if it had finished:
    # the adapters checked `stop_reason` for "refusal" and nothing else, so a
    # response cut off by the token budget flowed into `extract_facts` as OK,
    # and a brand absent from the truncated prefix was recorded as
    # ANSWERED_NO_MENTION — "the engine answered and did not name you" — which
    # scoring then counted against the mention rate. A billed call producing a
    # fabricated negative measurement, on a scan that still read SUCCEEDED.
    #
    # Kept as TWO members rather than one "incomplete", because they have
    # different futures. TRUNCATED has no fix but a bigger budget or a shorter
    # prompt. PAUSED could one day be fixed by actually resuming the call.
    # Collapsing them would hide that behind an `error_code` string nothing
    # queries.
    #
    # TRUNCATED — the engine ran out of room. Claude `max_tokens` and
    # `model_context_window_exceeded`; OpenAI `finish_reason == "length"`.
    TRUNCATED = "truncated"
    # PAUSED — the engine stopped to hand control back and nothing resumed it.
    # Claude `pause_turn` (a server-side tool turn the API paused) and
    # `tool_use`; OpenAI `finish_reason` `tool_calls` or `function_call`.
    #
    # Today only Claude's `pause_turn` can occur, on the grounded engine's
    # web_search. The other three are unreachable, and unreachable for a
    # reason that must not be confused with a dead guard: each fires the day a
    # client-side tool is added — on the Claude side, an entry in
    # `kwargs["tools"]` in `services/engines.py` that is not a server tool; on
    # the OpenAI side, a `tools` or `functions` entry in the ChatGPT adapter's
    # request payload. `serp.py`'s SERPAPI_KEY_MISSING is unreachable because
    # a guard elsewhere makes it redundant — a deletion candidate. These are
    # unreachable because a feature does not exist yet, and they are the
    # status that feature will need on its first day.
    PAUSED = "paused"


# The two statuses that are ANSWERS. `OK` is "answered and named the subject";
# `ANSWERED_NO_MENTION` is "answered and did not" — a finding, not a failure,
# and every population that counts answers has to count both.
#
# Defined once, beside the enum, because the rule already had three readers
# and the third had it wrong. `report.py` got it right in Epic 7.1, and
# `scan_runner` counts failures against it; `fix_runner.collect_facts`
# filtered on `OK` alone, so the generator was told that every answer it was
# shown named the brand — "All 30 of 30 answers named Pirsch" on a scan where
# 41 of 71 did not, and "3 of 123 citations" beside a proof beat that counted
# 323 (build-log, second pilot dry run, 2026-09-08). A rule with one home
# cannot be half-remembered at its third use.
ANSWERED_STATUSES: tuple[EngineResultStatus, ...] = (
    EngineResultStatus.OK,
    EngineResultStatus.ANSWERED_NO_MENTION,
)


class CitationType(str, enum.Enum):
    OWNED = "owned"  # the subject's own domain
    COMPETITOR = "competitor"
    DIRECTORY = "directory"
    REVIEW = "review"
    EDITORIAL = "editorial"
    SOCIAL = "social"
    OTHER = "other"


class EngineResult(Base, TimestampMixin):
    """One (prompt x engine) execution. See the module docstring before editing."""

    __tablename__ = "engine_results"

    id: Mapped[str] = id_column()
    scan_id: Mapped[str] = fk_column("scans.id")
    prompt_id: Mapped[str] = fk_column("prompts.id")

    engine: Mapped[Engine] = enum_column(Engine, name="engine")
    # Engine build/model identifier, so a score change can be attributed.
    engine_version: Mapped[str | None] = mapped_column(String(120), nullable=True)

    status: Mapped[EngineResultStatus] = enum_column(
        EngineResultStatus, name="engine_result_status"
    )

    # --- the facts (§5.3) ------------------------------------------------
    mentioned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 1-based ordinal of the subject brand among brands named. NULL when absent.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 0.000-1.000. Derived from position and answer structure.
    prominence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    sentiment: Mapped[Sentiment | None] = enum_column(
        Sentiment, name="sentiment", nullable=True
    )
    sentiment_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    # Total distinct brands named — the denominator for Share of Voice.
    brands_mentioned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # SHA-256 hex of the answer text. Change detection without retention.
    response_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Our own diagnostic code. NOT a vendor error body.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    citations: Mapped[list[Citation]] = relationship(
        back_populates="engine_result", cascade="all, delete-orphan"
    )
    brand_mentions: Mapped[list[BrandMention]] = relationship(
        back_populates="engine_result", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("prompt_id", "engine", name="uq_engine_results_prompt_engine"),
        Index("ix_engine_results_scan_engine", "scan_id", "engine"),
        Index("ix_engine_results_scan_mentioned", "scan_id", "mentioned"),
        CheckConstraint("position IS NULL OR position >= 1", name="position_positive"),
        CheckConstraint(
            "prominence IS NULL OR (prominence >= 0 AND prominence <= 1)",
            name="prominence_range",
        ),
        CheckConstraint(
            "sentiment_confidence IS NULL OR "
            "(sentiment_confidence >= 0 AND sentiment_confidence <= 1)",
            name="sentiment_confidence_range",
        ),
        CheckConstraint("brands_mentioned >= 0", name="brands_mentioned_non_negative"),
        # A result flagged as mentioned must carry a position, and vice versa.
        # Prevents a half-parsed row from silently inflating Mention Rate.
        CheckConstraint(
            "(mentioned = false AND position IS NULL) OR (mentioned = true)",
            name="unmentioned_has_no_position",
        ),
        CheckConstraint(
            "response_digest IS NULL OR length(response_digest) = 64",
            name="response_digest_is_sha256",
        ),
    )


class Citation(Base, TimestampMixin):
    """A source the engine cited.

    Domain and URL only. There is deliberately NO `title` or `snippet` column:
    a page title is the publisher's words, and ip-safety.md #7 permits "URLs and
    domains that were cited", not their copy. The UI renders the domain and
    links out.
    """

    __tablename__ = "engine_result_citations"

    id: Mapped[str] = id_column()
    engine_result_id: Mapped[str] = fk_column("engine_results.id")

    source_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[CitationType] = enum_column(
        CitationType, name="citation_type", default=CitationType.OTHER
    )
    # Order the citation appeared in.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether this citation points at the scanned brand's own property.
    cites_subject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    competitor_id: Mapped[str | None] = fk_column(
        "competitors.id", ondelete="SET NULL", nullable=True
    )

    engine_result: Mapped[EngineResult] = relationship(back_populates="citations")

    __table_args__ = (
        Index("ix_citations_result_domain", "engine_result_id", "source_domain"),
        Index("ix_citations_domain", "source_domain"),
    )


class BrandMention(Base, TimestampMixin):
    """A brand named in an answer. Entity names are facts (ip-safety.md #7).

    Populated for the subject AND every competitor named, which is what makes
    Share of Voice computable: brand mentions / total mentions.
    """

    __tablename__ = "engine_result_brand_mentions"

    id: Mapped[str] = id_column()
    engine_result_id: Mapped[str] = fk_column("engine_results.id")

    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    is_subject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    competitor_id: Mapped[str | None] = fk_column(
        "competitors.id", ondelete="SET NULL", nullable=True
    )
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    engine_result: Mapped[EngineResult] = relationship(back_populates="brand_mentions")

    __table_args__ = (
        UniqueConstraint(
            "engine_result_id", "entity_name", name="uq_brand_mentions_result_entity"
        ),
        Index("ix_brand_mentions_result_subject", "engine_result_id", "is_subject"),
    )

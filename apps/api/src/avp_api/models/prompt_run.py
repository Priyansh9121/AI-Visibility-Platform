"""PromptRun — an operator's ad-hoc question, asked of the engines on demand.

WHAT THIS IS, AND WHY IT IS NOT A SCAN
--------------------------------------
A scan generates ~24 prompts from a client's industry, runs each against every
engine, extracts facts, scores the result and produces a report. It takes about
six minutes and costs 72 engine calls. An operator who wants to know "does this
one question name my client?" has, until now, had to buy all of that.

This is that question on its own: one prompt the operator typed, run against the
same engines through the same `ask_all` and the same `extract_facts` the scan
uses, persisted so the answer is still there tomorrow. It deliberately produces
NO score, NO report and NO `Scan` row — a run is not a measurement of the client
and must never appear in a trend, a dashboard figure, or a client's scan history
where it would be read as one.

=============================================================================
IP-SAFETY (docs/ip-safety.md constraint 7) — the same rule, applied again
=============================================================================
`prompt_runs.prompt_text` holds full text and that is deliberate, for exactly
the reason `prompts.text` does: it is OUR side of the exchange. An operator typed
it; no engine returned it and no third party wrote it.

Everything in the three child tables is a fact derived from an answer, and there
is **no column anywhere below capable of holding an engine's answer**: no
`answer`, no `text`, no `snippet`, no generic JSONB payload. What survives a run
is what survives a scan — a boolean, an ordinal, a count, a digest, entity names
and cited URLs. The answer itself lives only inside the request that produced it.

`test_ip_safety.py` lists the three child tables in `FACTS_ONLY_MODELS`, so a
text-bearing column added here later fails CI rather than passing review.
=============================================================================
"""

from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import Boolean, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column, fk_column, id_column
from .engine_result import CitationType, Engine, EngineResultStatus


class PromptRunStatus(str, enum.Enum):
    """A run's overall verdict, from its per-engine outcomes.

    The same three-way shape `terminal_status_for` gives a scan, and for the
    same reason: PARTIAL exists so the screen can say "one engine was down"
    rather than presenting a narrower result as if it were the whole picture.
    """

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class PromptRun(Base, TimestampMixin):
    """One ad-hoc prompt, asked once, against every engine."""

    __tablename__ = "prompt_runs"

    id: Mapped[str] = id_column()
    client_id: Mapped[str] = fk_column("clients.id")
    # Denormalised from the client so tenancy can be enforced with one predicate
    # rather than a join. Every other agency-scoped read in this codebase does
    # the same; a history endpoint that had to join to check ownership is a
    # history endpoint somebody will eventually forget to join in.
    agency_id: Mapped[str] = fk_column("agencies.id")
    # Who asked. Nullable because a user may be removed from the agency while
    # their runs stay in the client's history — the run is a fact about the
    # client, not about the seat.
    asked_by_user_id: Mapped[str | None] = fk_column(
        "users.id", nullable=True, ondelete="SET NULL"
    )

    # OURS. See the module note. Length-capped in the schema layer, not here,
    # because the cap is a product decision about what is worth running.
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[PromptRunStatus] = enum_column(
        PromptRunStatus, name="prompt_run_status"
    )
    # The subject as it was resolved AT RUN TIME. A client's brand name can be
    # corrected later, and a run must keep reading the way it read when it ran —
    # otherwise a stored "not mentioned" silently becomes wrong.
    subject_name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_domain: Mapped[str] = mapped_column(String(253), nullable=False)

    results: Mapped[list[PromptRunResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # The history query: this client's runs, newest first. IDs are ULIDs, so
        # id DESC is creation order and this index serves the ordering too.
        Index("ix_prompt_runs_client_id_id", "client_id", "id"),
    )


class PromptRunResult(Base, TimestampMixin):
    """What ONE engine did with the prompt. Facts only."""

    __tablename__ = "prompt_run_results"

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = fk_column("prompt_runs.id")

    engine: Mapped[Engine] = enum_column(Engine, name="prompt_run_engine")
    engine_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[EngineResultStatus] = enum_column(
        EngineResultStatus, name="prompt_run_result_status"
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    mentioned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Ordinal among the brands this answer named. NULL when not named — never 0,
    # which would sort as "first" and read as a position nobody measured.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prominence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    brands_mentioned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # SHA-256 of the answer. Lets a re-ask detect "the answer changed" without
    # retaining what it said — the same trade `EngineResult` makes.
    response_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)

    run: Mapped[PromptRun] = relationship(back_populates="results")
    brands: Mapped[list[PromptRunBrand]] = relationship(
        back_populates="result", cascade="all, delete-orphan", lazy="selectin"
    )
    citations: Mapped[list[PromptRunCitation]] = relationship(
        back_populates="result", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # One row per engine per run. A retry must update, not accumulate.
        UniqueConstraint("run_id", "engine", name="uq_prompt_run_results_run_id_engine"),
    )


class PromptRunBrand(Base, TimestampMixin):
    """A brand this answer named. An entity name and where it appeared."""

    __tablename__ = "prompt_run_brands"

    id: Mapped[str] = id_column()
    result_id: Mapped[str] = fk_column("prompt_run_results.id")

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    is_subject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    result: Mapped[PromptRunResult] = relationship(back_populates="brands")

    __table_args__ = (
        Index("ix_prompt_run_brands_result_id_position", "result_id", "position"),
    )


class PromptRunCitation(Base, TimestampMixin):
    """A source this answer cited. Location, never content."""

    __tablename__ = "prompt_run_citations"

    id: Mapped[str] = id_column()
    result_id: Mapped[str] = fk_column("prompt_run_results.id")

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    source_type: Mapped[CitationType] = enum_column(
        CitationType, name="prompt_run_citation_type"
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    cites_subject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    result: Mapped[PromptRunResult] = relationship(back_populates="citations")

    __table_args__ = (
        Index("ix_prompt_run_citations_result_id_position", "result_id", "position"),
    )

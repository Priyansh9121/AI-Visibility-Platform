"""PromptSet and Prompt — the questions a scan asks the engines.

Storage note: `Prompt.text` holds full prompt text, and that is correct under
ip-safety.md #7. These strings are OUR OWN generated content (Epic 4 generates
them with an LLM from the client's industry), not scraped third-party material.
This is the only place in the schema where free-form text is stored, and it is
ours.
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column, fk_column, id_column


class PromptIntent(str, enum.Enum):
    """Buyer-journey stage. §7 Epic 4 requires intent tagging."""

    AWARENESS = "awareness"
    COMPARISON = "comparison"
    BOTTOM_FUNNEL = "bottom_funnel"


class PromptSet(Base, TimestampMixin):
    __tablename__ = "prompt_sets"

    id: Mapped[str] = id_column()
    scan_id: Mapped[str] = fk_column("scans.id")

    # Model identifier that generated the set, e.g. "claude-opus-5". Provenance
    # for reproducing or explaining a prompt set later.
    generated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    prompts: Mapped[list[Prompt]] = relationship(
        back_populates="prompt_set", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("scan_id", name="uq_prompt_sets_scan_id"),)


class Prompt(Base, TimestampMixin):
    __tablename__ = "prompts"

    id: Mapped[str] = id_column()
    prompt_set_id: Mapped[str] = fk_column("prompt_sets.id")

    text: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[PromptIntent] = enum_column(PromptIntent, name="prompt_intent")
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    prompt_set: Mapped[PromptSet] = relationship(back_populates="prompts")

    __table_args__ = (
        UniqueConstraint("prompt_set_id", "position", name="uq_prompts_set_position"),
        Index("ix_prompts_set_intent", "prompt_set_id", "intent"),
    )

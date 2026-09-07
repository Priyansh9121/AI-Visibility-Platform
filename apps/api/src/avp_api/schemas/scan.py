"""Scan, prompt-set and engine-result schemas — Epic 4.

ip-safety.md #7: `PromptOut.text` is our OWN generated question, the deliberate
exception. Nothing here can carry an engine's answer — EngineResultOut exposes
booleans, ordinals, a sentiment label, counts, cited domains, and a digest.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator

from ..models.engine_result import (
    CitationType,
    Engine,
    EngineResultStatus,
    Sentiment,
)
from ..models.prompt import PromptIntent
from ..models.scan import ScanStatus, ScanTrigger
from .common import ApiModel


class RunScanRequest(ApiModel):
    """Start a scan.

    `promptLimit` caps the generated set. It exists for cost control during
    verification — a full run is 20-30 prompts across every engine, and the
    grounded engine can take 100s per prompt. Omit it for a real scan.

    `engines` is de-duplicated BEFORE validation and bounded by the enum's own
    size — API key discipline audit, 2026-09-07. Neither alone bounds spend: a
    bare `max_length` still lets `["claude", "claude", "claude"]` through as
    three billed calls per prompt that then die on
    `uq_engine_results_prompt_engine` after the money is spent, and
    de-duplication is a ceiling only because the enum is finite. Together they
    make "one call per engine per prompt" true by construction. Whether each
    engine has an ADAPTER is the router's check, against `ENGINE_REGISTRY`:
    the enum names what this product might ever measure, the registry what it
    can measure today.
    """

    prompt_limit: int | None = Field(default=None, ge=1, le=30)
    engines: list[Engine] | None = Field(default=None, max_length=len(Engine))

    @field_validator("engines", mode="before")
    @classmethod
    def _collapse_duplicates(cls, value: object) -> object:
        # On the raw values, before enum validation, so `max_length` above
        # bounds DISTINCT engines: eight copies of "claude" is one engine, not
        # a rejected request and not eight billed calls. Order is kept — it is
        # the order `ask_all` gathers in and `engine_versions` records.
        if isinstance(value, list):
            try:
                return list(dict.fromkeys(value))
            except TypeError:
                return value  # unhashable junk; enum validation rejects it
        return value


class PromptOut(ApiModel):
    id: str
    text: str
    intent: PromptIntent
    position: int


class PromptSetOut(ApiModel):
    id: str
    scan_id: str
    generated_by: str | None = None
    generation_params: dict = Field(default_factory=dict)
    prompts: list[PromptOut] = Field(default_factory=list)


class CitationOut(ApiModel):
    id: str
    source_domain: str
    source_url: str | None = None
    source_type: CitationType
    position: int | None = None
    cites_subject: bool
    competitor_id: str | None = None


class BrandMentionOut(ApiModel):
    id: str
    entity_name: str
    entity_domain: str | None = None
    is_subject: bool
    position: int | None = None
    competitor_id: str | None = None


class EngineResultOut(ApiModel):
    id: str
    prompt_id: str
    engine: Engine
    engine_version: str | None = None
    status: EngineResultStatus

    mentioned: bool
    position: int | None = None
    prominence: Decimal | None = None
    sentiment: Sentiment | None = None
    sentiment_confidence: Decimal | None = None
    brands_mentioned: int

    # SHA-256 of the answer. Change-detection without retaining the answer.
    response_digest: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None

    brand_mentions: list[BrandMentionOut] = Field(default_factory=list)
    citations: list[CitationOut] = Field(default_factory=list)


class ScanOut(ApiModel):
    id: str
    client_id: str
    agency_id: str
    status: ScanStatus
    trigger: ScanTrigger
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    formula_version: str
    engine_versions: dict = Field(default_factory=dict)
    prompt_count: int
    engine_result_count: int
    created_at: datetime
    updated_at: datetime


class ScanDetailOut(ScanOut):
    prompt_set: PromptSetOut | None = None
    results: list[EngineResultOut] = Field(default_factory=list)

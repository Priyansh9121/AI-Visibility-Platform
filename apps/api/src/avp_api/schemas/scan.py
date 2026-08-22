"""Scan, prompt-set and engine-result schemas — Epic 4.

ip-safety.md #7: `PromptOut.text` is our OWN generated question, the deliberate
exception. Nothing here can carry an engine's answer — EngineResultOut exposes
booleans, ordinals, a sentiment label, counts, cited domains, and a digest.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

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
    """

    prompt_limit: int | None = Field(default=None, ge=1, le=30)
    engines: list[Engine] | None = None


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

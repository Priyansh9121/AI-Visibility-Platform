"""Client intake and read schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from ..models import ClassificationStatus, ClientKind
from .common import ApiModel


class CreateClientRequest(ApiModel):
    """Intake payload.

    `url` is intentionally permissive — an operator pastes whatever they have,
    including a bare domain. Normalisation and validation happen server-side in
    services/intake.py:parse_domain so the browser and the API cannot disagree
    about what counts as a valid address.
    """

    url: str = Field(min_length=3, max_length=2048)
    # Optional operator-supplied name. When absent the classifier's extracted
    # brand name is used, falling back to the domain.
    name: str | None = Field(default=None, max_length=200)
    # Allows creating the record without spending an LLM call — used by tests
    # and by Epic 10's bulk upload, which classifies out of band.
    classify: bool = True


class CrawlSummaryOut(ApiModel):
    """FACTS from the crawl. Never page content — see services/crawl.py."""

    pages_fetched: int
    urls_fetched: list[str]
    schema_types: list[str]
    word_count: int
    h1_count: int
    has_title: bool
    has_meta_description: bool
    detected_language: str | None = None


class ClientOut(ApiModel):
    id: str
    agency_id: str
    name: str
    domain: str
    kind: ClientKind

    # Null unless classificationStatus is 'classified'. The UI must render null
    # as "not classified", never as an empty industry.
    industry: str | None = None
    industry_niche: str | None = None
    brand_name: str | None = None

    classification_status: ClassificationStatus
    classification_reason_code: str | None = None
    industry_confidence: str | None = None
    # String-encoded decimal on the wire, like every NUMERIC in this API.
    industry_confidence_score: Decimal | None = None
    classified_at: datetime | None = None
    classifier_model: str | None = None

    created_at: datetime
    updated_at: datetime


class ClientDetailOut(ClientOut):
    """A client plus the crawl facts from its most recent classification."""

    crawl: CrawlSummaryOut | None = None

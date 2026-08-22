"""Intake — §5.4 step 1 end to end.

    URL submitted -> crawl homepage/key pages -> LLM classifies industry/niche

Runs synchronously inside the request. The §7 acceptance criterion is
"submitting a URL returns a correctly classified industry within 30 seconds",
and the measured path is a few seconds, so a synchronous call satisfies it
directly and keeps the intake screen a single round trip.

`run_classification` is deliberately independent of the HTTP layer, so Epic 4's
worker can call it for bulk and scheduled scans without going through the API.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ids
from ..config import Settings, get_settings
from ..errors import Conflict, NotFound
from ..models import ClassificationStatus, Client, ClientKind
from .classify import ClassificationOutcome, classify
from .crawl import CrawlResult, crawl_site, normalise_url, registrable_domain

logger = structlog.get_logger(__name__)

_STATUS_MAP = {
    "classified": ClassificationStatus.CLASSIFIED,
    "ambiguous": ClassificationStatus.AMBIGUOUS,
    "unclassifiable": ClassificationStatus.UNCLASSIFIABLE,
}


class InvalidUrl(Conflict):
    problem_type = "invalid-url"
    title = "That does not look like a website address"


def parse_domain(raw_url: str) -> tuple[str, str]:
    """Validate a submitted URL and return (normalised_url, registrable_domain).

    Rejects anything that does not resolve to a registrable domain — an IP
    address, `localhost`, or a bare word. Those are not prospects, and letting
    one through creates a client row that can never be scanned.
    """
    try:
        url = normalise_url(raw_url)
    except ValueError as exc:
        raise InvalidUrl(detail=str(exc)) from exc

    domain = registrable_domain(url)
    if not domain or "." not in domain:
        raise InvalidUrl(
            detail=(
                f"{raw_url!r} does not contain a public domain name. "
                "Enter a website address such as example.com."
            )
        )
    return url, domain


async def create_client_from_url(
    session: AsyncSession,
    *,
    agency_id: str,
    raw_url: str,
    name: str | None = None,
) -> Client:
    """Create the client record. Classification is a separate step."""
    url, domain = parse_domain(raw_url)

    existing = (
        await session.execute(
            select(Client).where(
                Client.agency_id == agency_id,
                Client.domain == domain,
                Client.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(
            detail=f"{domain} is already tracked by this agency.",
            clientId=existing.id,
        )

    client = Client(
        id=ids.new_id(ids.CLIENT),
        agency_id=agency_id,
        # Provisional. Replaced by the extracted brand name once classified —
        # the domain is a placeholder, not a claim about the business's name.
        name=(name or domain).strip()[:200],
        domain=domain,
        kind=ClientKind.PROSPECT,
        classification_status=ClassificationStatus.PENDING,
    )
    session.add(client)
    await session.flush()
    return client


def apply_outcome(client: Client, outcome: ClassificationOutcome) -> Client:
    """Write a classification outcome onto a client row.

    The `industry` assignment is conditional on purpose: the model's brand name
    and confidence are still useful when the industry call was uncertain, but
    the industry itself is withheld unless the outcome is CLASSIFIED. The
    database enforces the same rule via
    `ck_clients_industry_matches_classification_status`, so a bug here fails
    loudly rather than storing a guess.
    """
    client.classification_status = _STATUS_MAP[outcome.status]
    client.classification_reason_code = outcome.reason_code
    client.industry_confidence = outcome.confidence
    client.industry_confidence_score = outcome.confidence_score
    client.classified_at = datetime.now(UTC)
    client.classifier_model = outcome.model

    if outcome.status == "classified":
        client.industry = outcome.industry
        client.industry_niche = outcome.niche
    else:
        client.industry = None
        client.industry_niche = None

    if outcome.brand_name:
        client.brand_name = outcome.brand_name.strip()[:200]
        # Promote the real business name over the domain placeholder, but never
        # overwrite a name an operator typed in themselves.
        if client.name == client.domain:
            client.name = client.brand_name
    return client


async def run_classification(
    session: AsyncSession,
    client: Client,
    *,
    settings: Settings | None = None,
    max_pages: int = 3,
) -> tuple[Client, CrawlResult]:
    """Crawl and classify an existing client. Persists the outcome.

    Returns the crawl result alongside the client so the caller can surface
    crawl FACTS (pages fetched, schema types) without this function having to
    persist them — Epic 6's technical audit owns that table.
    """
    settings = settings or get_settings()

    crawl = await crawl_site(f"https://{client.domain}", max_pages=max_pages)
    outcome = await classify(crawl, settings=settings)
    apply_outcome(client, outcome)
    await session.flush()

    logger.info(
        "intake.classified",
        client_id=client.id,
        domain=client.domain,
        status=outcome.status,
        industry=outcome.industry,
        reason=outcome.reason_code,
        **crawl.redacted(),
    )
    return client, crawl


async def get_client(session: AsyncSession, *, agency_id: str, client_id: str) -> Client:
    """Fetch one client, scoped to the caller's agency.

    A client belonging to another agency returns 404, not 403 — confirming that
    an id exists is itself a leak across a tenant boundary.
    """
    if not ids.is_valid(client_id, ids.CLIENT):
        raise NotFound(detail="No client with that identifier.")

    client = (
        await session.execute(
            select(Client).where(
                Client.id == client_id,
                Client.agency_id == agency_id,
                Client.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if client is None:
        raise NotFound(detail="No client with that identifier.")
    return client

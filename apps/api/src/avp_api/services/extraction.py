"""Fact extraction from engine answers — §5.4 step 4, "capture citations/mentions".

=============================================================================
IP-SAFETY BOUNDARY (ip-safety.md #7)
=============================================================================
This module is where a live engine answer is converted into the facts that get
stored. Everything it returns is a boolean, a count, an ordinal, a normalised
score, an entity NAME, or a cited URL/domain. The answer text itself reaches no
return value and no log line.
=============================================================================

Determinism
-----------
Mention detection, position, prominence and citation typing are **pure string
and set operations** over the answer text — the same answer always yields the
same facts. This matters because scoring-spec.md requires the score to
recompute identically from a stored EngineResult set.

Sentiment is the one exception: it needs a model. Per scoring-spec.md rule 4 it
is classified ONCE here, upstream of scoring, and persisted. Scoring reads the
stored label and never re-invokes a model, so re-scoring an existing scan stays
deterministic even though the original classification was not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

import anthropic
import structlog
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..models.engine_result import CitationType, EngineResultStatus, Sentiment
from .competitors import slugify
from .engines import EngineAnswer
from .serp import NON_COMPETITOR_DOMAINS

logger = structlog.get_logger(__name__)

SENTIMENT_MODEL = "claude-opus-5"
SENTIMENT_EFFORT = "low"
SENTIMENT_MAX_TOKENS = 1_500

# Brand names shorter than this are matched case-sensitively. "On" (the running
# brand) and "Front" would otherwise match "on" and "front" in ordinary prose
# and report a mention on almost every answer.
SHORT_NAME_CHARS = 5

# Minimum slug length for the separator-insensitive pass below. Short slugs
# match inside unrelated words once separators are stripped ("on" inside
# "onward"), so the fallback is restricted to names long enough for an
# accidental collision to be unlikely.
MIN_SLUG_MATCH_CHARS = 6

# Publisher/aggregator domains reused from Epic 3, extended with the citation
# taxonomy the Citation model expects.
_REVIEW_DOMAINS = frozenset({
    "g2.com", "capterra.com", "trustpilot.com", "trustradius.com", "getapp.com",
    "softwareadvice.com", "yelp.com", "tripadvisor.com", "producthunt.com",
})
_SOCIAL_DOMAINS = frozenset({
    "reddit.com", "x.com", "twitter.com", "facebook.com", "instagram.com",
    "linkedin.com", "tiktok.com", "youtube.com", "quora.com", "threads.net",
})
_DIRECTORY_DOMAINS = frozenset({
    "yellowpages.com", "angi.com", "thumbtack.com", "houzz.com", "checkatrade.com",
    "bark.com", "crunchbase.com", "alternativeto.net", "sourceforge.net",
})


@dataclass(slots=True)
class BrandHit:
    name: str
    domain: str | None
    is_subject: bool
    position: int
    first_index: int


@dataclass(slots=True)
class CitationFact:
    url: str
    domain: str
    source_type: CitationType
    position: int
    cites_subject: bool


@dataclass(slots=True)
class ExtractedFacts:
    """Everything that may be persisted from one engine answer."""

    mentioned: bool = False
    position: int | None = None
    prominence: Decimal | None = None
    brands_mentioned: int = 0
    sentiment: Sentiment | None = None
    sentiment_confidence: Decimal | None = None
    brand_hits: list[BrandHit] = field(default_factory=list)
    citations: list[CitationFact] = field(default_factory=list)
    status: EngineResultStatus = EngineResultStatus.OK


def _name_pattern(name: str) -> re.Pattern[str] | None:
    """Word-boundary matcher for a brand name."""
    cleaned = name.strip()
    if not cleaned:
        return None
    flags = 0 if len(cleaned) < SHORT_NAME_CHARS else re.IGNORECASE
    return re.compile(rf"(?<![\w-]){re.escape(cleaned)}(?![\w-])", flags)


def _slug_index(text: str, slug: str) -> int | None:
    """Find `slug` in `text` ignoring spacing and punctuation.

    Needed because a brand is written many ways: a client whose classification
    did not run has no `brand_name`, so the subject is its DOMAIN
    ("helpscout.com"), while every engine answer writes "Help Scout". Without
    this pass such a client scores 0% mention rate on every scan — a false
    negative on the product's headline metric, and one that looks like a
    finding rather than a bug.

    Returns the index in the ORIGINAL text so brand ordering stays meaningful.
    """
    if len(slug) < MIN_SLUG_MATCH_CHARS:
        return None
    flat: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if char.isalnum():
            flat.append(char.lower())
            origin.append(index)
    found = "".join(flat).find(slug)
    return origin[found] if found != -1 else None


def find_brand(text: str, *, name: str | None, domain: str | None) -> int | None:
    """First character index at which a brand is referenced, or None.

    Three passes, cheapest and most precise first:

    1. The name with word boundaries.
    2. The bare domain, and its registrable label ("front.com" -> "Front").
    3. A separator-insensitive slug match, so "helpscout.com" or "HelpScout"
       finds "Help Scout".

    Returns an index rather than a bool so callers can order brands by where
    they appear in the answer.
    """
    best: int | None = None

    def consider(index: int | None) -> None:
        nonlocal best
        if index is not None and (best is None or index < best):
            best = index

    for candidate in (name, domain):
        if not candidate:
            continue
        pattern = _name_pattern(candidate)
        if pattern is not None:
            match = pattern.search(text)
            if match:
                consider(match.start())

    label = domain.split(".")[0] if domain else None
    if best is None and label and len(label) >= SHORT_NAME_CHARS:
        pattern = _name_pattern(label)
        if pattern is not None:
            match = pattern.search(text)
            if match:
                consider(match.start())

    if best is None:
        for candidate in (name, label):
            if candidate:
                consider(_slug_index(text, slugify(candidate)))

    return best


def classify_citation(
    domain: str, *, subject_domain: str, competitor_domains: set[str]
) -> tuple[CitationType, bool]:
    """Type a cited source, and say whether it cites the subject."""
    if domain == subject_domain:
        return CitationType.OWNED, True
    if domain in competitor_domains:
        return CitationType.COMPETITOR, False
    if domain in _REVIEW_DOMAINS:
        return CitationType.REVIEW, False
    if domain in _SOCIAL_DOMAINS:
        return CitationType.SOCIAL, False
    if domain in _DIRECTORY_DOMAINS:
        return CitationType.DIRECTORY, False
    if domain in NON_COMPETITOR_DOMAINS:
        # Everything else on the Epic 3 exclusion list is a publisher.
        return CitationType.EDITORIAL, False
    return CitationType.OTHER, False


def extract_facts(
    answer: EngineAnswer,
    *,
    subject_name: str,
    subject_domain: str,
    competitors: list[tuple[str, str | None]],
) -> ExtractedFacts:
    """Derive persistable facts from one engine answer. Pure and deterministic."""
    facts = ExtractedFacts(status=answer.status)

    if not answer.ok or not answer.text:
        facts.status = answer.status if not answer.ok else EngineResultStatus.ANSWERED_NO_MENTION
        return facts

    text = answer.text

    hits: list[BrandHit] = []
    subject_index = find_brand(text, name=subject_name, domain=subject_domain)
    if subject_index is not None:
        hits.append(
            BrandHit(name=subject_name, domain=subject_domain, is_subject=True,
                     position=0, first_index=subject_index)
        )
    seen_slugs = {slugify(subject_name)}
    for comp_name, comp_domain in competitors:
        slug = slugify(comp_name)
        if slug in seen_slugs:
            continue
        index = find_brand(text, name=comp_name, domain=comp_domain)
        if index is not None:
            seen_slugs.add(slug)
            hits.append(
                BrandHit(name=comp_name, domain=comp_domain, is_subject=False,
                         position=0, first_index=index)
            )

    # Order by where each brand first appears; that ordering IS the position.
    hits.sort(key=lambda h: (h.first_index, h.name.lower()))
    for rank, hit in enumerate(hits, start=1):
        hit.position = rank

    facts.brand_hits = hits
    facts.brands_mentioned = len(hits)

    subject_hit = next((h for h in hits if h.is_subject), None)
    if subject_hit is not None:
        facts.mentioned = True
        facts.position = subject_hit.position
        # Prominence: how early in the answer the subject first appears, 1.0 at
        # the very start and approaching 0 at the end. A deliberately simple,
        # deterministic proxy — being named in the opening sentence is worth
        # more than a footnote, and this captures that without pretending to
        # measure emphasis.
        fraction = subject_hit.first_index / max(1, len(text))
        facts.prominence = Decimal(str(round(1.0 - min(1.0, fraction), 3)))
    else:
        facts.status = EngineResultStatus.ANSWERED_NO_MENTION

    competitor_domains = {d for _, d in competitors if d}
    for cited in answer.citations:
        source_type, cites_subject = classify_citation(
            cited.domain, subject_domain=subject_domain, competitor_domains=competitor_domains
        )
        facts.citations.append(
            CitationFact(
                url=cited.url, domain=cited.domain, source_type=source_type,
                position=cited.position, cites_subject=cites_subject,
            )
        )

    return facts


class SentimentJudgement(BaseModel):
    """Structured sentiment. No field can hold answer text."""

    sentiment: Sentiment = Field(
        description=(
            "How the answer portrays the subject brand specifically. "
            "positive: recommended or praised. neutral: named without "
            "evaluation. negative: cautioned against or unfavourably compared."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)


SENTIMENT_SYSTEM = """\
You judge how an AI assistant's answer portrays ONE named brand.

Rules:
- Judge only the treatment of the subject brand. Praise for a competitor is not \
negative sentiment toward the subject unless the answer draws the comparison.
- A brand listed among options without evaluation is `neutral`. Most mentions \
are neutral; do not read enthusiasm into a plain listing.
- `negative` means the answer actively steers a reader away — caveats, \
limitations presented as reasons not to choose it, or an unfavourable direct \
comparison."""


async def classify_sentiment(
    answer: EngineAnswer, *, subject_name: str, settings: Settings | None = None
) -> tuple[Sentiment | None, Decimal | None]:
    """Classify sentiment toward the subject. Returns (None, None) on failure.

    Called ONLY when the subject was actually mentioned. Sentiment toward a
    brand that does not appear is meaningless, and scoring-spec.md excludes it
    from the composite rather than scoring it zero — so spending a model call on
    it would be both wasteful and misleading.
    """
    settings = settings or get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.provider_key("anthropic_api_key"))
    try:
        response = await client.messages.parse(
            model=SENTIMENT_MODEL,
            max_tokens=SENTIMENT_MAX_TOKENS,
            output_config={"effort": SENTIMENT_EFFORT},
            system=SENTIMENT_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": f"Subject brand: {subject_name}\n\nAnswer:\n{answer.text}",
                }
            ],
            output_format=SentimentJudgement,
        )
    except anthropic.APIError as exc:
        logger.warning("extraction.sentiment_failed", error=type(exc).__name__)
        return None, None

    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None, None

    parsed = response.parsed_output
    return parsed.sentiment, Decimal(str(parsed.confidence)).quantize(Decimal("0.001"))

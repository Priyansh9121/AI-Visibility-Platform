"""LLM industry classification — §5.4 step 1, "LLM classifies industry/niche".

Design notes worth reading before changing this file.

**Free-form industry label, not an enum.** §6 defers per-industry weight tuning
until real data exists. A closed enum decided now would lock that tuning to
categories chosen before a single scan had been run, and would force every
unanticipated business into "other". The model returns a short noun phrase;
normalisation into tunable buckets happens later, when there is data to cluster.

**The model reports confidence; this module decides status.** Asking an LLM to
self-assess "am I sure enough?" makes the threshold invisible and unauditable.
The model returns a score, and `CONFIDENCE_THRESHOLD` here — one constant, in
one place — decides CLASSIFIED vs AMBIGUOUS.

**Ambiguous stores NULL, never a guess.** A wrong industry silently poisons
Epic 3's competitor detection and Epic 4's prompt generation, and neither has
any way to detect that its input was wrong. Same reasoning as Score's
INSUFFICIENT_DATA in scoring-spec.md: a result we could not determine must be
representable as undetermined.

**Facts only.** Page text reaches this module only as a transient argument
and is sent to the model the way a human would read a page to decide what
business it is. What comes back and is persisted is the conclusion — an
industry label, a niche, the subject's own brand name, a confidence number —
not the copy. The model's `rationale` is deliberately NOT persisted and NOT
returned by any endpoint; it exists for debug logging while tuning the prompt.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import anthropic
import structlog
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from .call_bounds import CallBound
from .crawl import CrawlResult

logger = structlog.get_logger(__name__)

# Opus 5. Classification quality here determines the input quality of three
# downstream epics, so this is not a place to economise on model tier.
CLASSIFIER_MODEL = "claude-opus-5"

# Classification from page text is a simple extraction task. Low effort keeps
# the call comfortably inside the 30-second end-to-end acceptance budget, which
# the crawl already spends several seconds of.
CLASSIFIER_EFFORT = "low"

# Thinking is on by default on Opus 5; low effort keeps it short. Headroom is
# for thinking tokens, which count toward max_tokens — the structured payload
# itself is a few hundred tokens.
CLASSIFIER_MAX_TOKENS = 4_000

# --- The call's bound (API key discipline audit, 2026-09-07) -----------------
# Inherited both SDK defaults until this audit — a 600s read timeout across
# three attempts — in a request path whose own docstring promises a result
# within thirty seconds. The measurement is the whole endpoint: `POST /clients`,
# crawl AND classify, at 8.3s (build log, Epic 9.4's latency table), so twelve
# seconds per attempt is headroom over both together, not just this call. One
# retry, because a 529 or a dropped connection in the first second is far more
# common than a slow-but-alive classification, and without it each of those
# becomes an `unclassifiable` client. Two attempts plus the SDK's backoff must
# still fit inside the promise: 12 x 2 + 2 = 26s, the largest ceiling that
# does. `test_call_bounds.py` asserts it stays under 30.
CLASSIFIER_BOUND = CallBound(timeout=12.0, max_retries=1)  # ceiling: 26.0s

# Below this, the result is recorded as AMBIGUOUS with industry = NULL.
# Tunable in one place; deliberately not a per-call argument.
CONFIDENCE_THRESHOLD = Decimal("0.70")

# A page with almost no text cannot support a classification. Guards against
# a parked domain or a JS shell yielding a confident hallucination.
MIN_WORDS_FOR_CLASSIFICATION = 40

ConfidenceLabel = Literal["high", "medium", "low"]


class IndustryClassification(BaseModel):
    """Structured output contract for the classifier."""

    is_classifiable: bool = Field(
        description=(
            "False when the page does not identify a real operating business — "
            "a parked domain, a login wall, an error page, or pure boilerplate."
        )
    )
    industry: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Short lower-case noun phrase for the business's primary industry, "
            "e.g. 'dental practice', 'commercial law firm', 'b2b saas'. "
            "Null when is_classifiable is false."
        ),
    )
    niche: str | None = Field(
        default=None,
        max_length=160,
        description=(
            "Narrower specialisation within the industry, e.g. 'cosmetic and "
            "implant dentistry'. Null if the site does not indicate one."
        ),
    )
    brand_name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "The business's own name as it presents itself, e.g. 'Northaven "
            "Dental'. Not the legal entity name unless that is what is used."
        ),
    )
    confidence: ConfidenceLabel = Field(
        description="Coarse confidence in the industry call."
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Calibrated confidence 0-1. Use the full range: below 0.7 means a "
            "reader could reasonably disagree with this classification."
        ),
    )
    rationale: str = Field(
        max_length=300,
        description=(
            "One sentence, in your own words, on what led to this call. "
            "Do not quote the page."
        ),
    )


# The explicit "never its business model" rule and its banned-answer list are
# load-bearing, not stylistic. Without them the model reliably identified the
# specific business in its rationale and then wrote a broader category into the
# label: Basecamp, SavvyCal and Help Scout all returned "b2b saas", and Y
# Combinator returned "venture capital". Measured with scripts/tune_prompt.py
# across nine sites and two runs — see docs/build-log.md Epic 2.8.
#
# Note that fixing the misleading exemplar alone ("b2b logistics software", which
# modelled the very generalisation to avoid) changed 1 of 9 labels and fixed
# none of the failures. The negative is what works.
SYSTEM_PROMPT = """\
You classify what industry a business operates in, from the text of its own \
website. Your output is the input to a competitive-analysis pipeline, so a \
confident wrong answer is far more costly than an admitted uncertainty.

Rules:
- `industry` is a short, lower-case noun phrase a practitioner would recognise \
("dental practice", "independent bookshop", "warehouse management software"). \
Not a sentence, not a marketing category, not the company name.
- Name what the business SELLS or DOES, never its business model or delivery \
channel. "b2b saas", "saas", "e-commerce", "marketplace", "technology company", \
"home services" and "consumer goods" are never acceptable answers — if one is \
your first instinct, go one level more specific and name the actual product or \
service.
- `niche` narrows it only when the site clearly specialises. Otherwise null.
- `brand_name` is what the business calls itself.
- Set `is_classifiable` false for parked domains, error pages, login walls, \
directory listings, and pages with no indication of an operating business.
- `confidence_score` must be calibrated. Score below 0.7 whenever the site is \
vague, serves several unrelated lines of business, or is mostly navigation. \
A holding page for a conglomerate is a low-confidence input, not a high- \
confidence "conglomerate".
- Never infer the industry from the domain name alone. If the page text does \
not support the call, say so through a low score.
- Do not quote the page in `rationale`. Describe your reasoning in your own \
words."""


class ClassificationOutcome(BaseModel):
    """What the caller persists. Note `rationale` is absent by design."""

    status: Literal["classified", "ambiguous", "unclassifiable"]
    industry: str | None = None
    niche: str | None = None
    brand_name: str | None = None
    confidence: str | None = None
    confidence_score: Decimal | None = None
    reason_code: str | None = None
    model: str = CLASSIFIER_MODEL


def build_prompt(crawl: CrawlResult) -> str:
    """Assemble the classification input from crawl output."""
    parts = [
        f"Website: {crawl.signals.registrable_domain}",
        f"Pages read: {', '.join(crawl.signals.urls_fetched) or 'homepage only'}",
    ]
    if crawl.titles:
        parts.append("Page titles: " + " | ".join(t.strip() for t in crawl.titles if t.strip()))
    if crawl.signals.schema_types:
        # Structured-data type names are a strong, cheap signal — a
        # `LocalBusiness`/`Dentist` type resolves cases the prose leaves vague.
        parts.append("Schema.org types declared: " + ", ".join(crawl.signals.schema_types[:20]))
    parts.append("\nPage text:\n" + crawl.text_extract)
    return "\n".join(parts)


async def classify(
    crawl: CrawlResult,
    *,
    settings: Settings | None = None,
    system_prompt: str | None = None,
) -> ClassificationOutcome:
    """Classify a crawled site. Never raises for an unclassifiable input.

    `system_prompt` overrides SYSTEM_PROMPT. It exists for the prompt-tuning
    harness (scripts/tune_prompt.py), which compares variants against a fixed
    set of crawls. Production always passes None and uses SYSTEM_PROMPT — an
    override reaching a request path would mean a scan was scored under an
    unrecorded prompt, which `classifier_model` alone would not reveal.
    """
    settings = settings or get_settings()

    if not crawl.ok:
        return ClassificationOutcome(
            status="unclassifiable", reason_code=crawl.error_code or "FETCH_FAILED"
        )

    if crawl.signals.word_count < MIN_WORDS_FOR_CLASSIFICATION:
        return ClassificationOutcome(
            status="unclassifiable", reason_code="INSUFFICIENT_CONTENT"
        )

    try:
        async with CLASSIFIER_BOUND.deadline():
            response = await CLASSIFIER_BOUND.client(settings).messages.parse(
                model=CLASSIFIER_MODEL,
                max_tokens=CLASSIFIER_MAX_TOKENS,
                output_config={"effort": CLASSIFIER_EFFORT},
                system=system_prompt or SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(crawl)}],
                output_format=IndustryClassification,
            )
    except anthropic.AuthenticationError:
        logger.error("classify.auth_failed")
        return ClassificationOutcome(status="unclassifiable", reason_code="PROVIDER_AUTH_FAILED")
    except anthropic.RateLimitError:
        logger.warning("classify.rate_limited")
        return ClassificationOutcome(status="unclassifiable", reason_code="PROVIDER_RATE_LIMITED")
    except anthropic.BadRequestError as exc:
        # The API returns 400 for an exhausted credit balance, which is an
        # operational problem, not a malformed request. Conflating the two
        # sends an operator hunting for a payload bug when the fix is billing,
        # so it gets its own reason code. Matching on the message is the only
        # available signal — the error type and status are identical.
        message = str(exc)
        if "credit balance" in message.lower():
            logger.error("classify.quota_exhausted")
            return ClassificationOutcome(
                status="unclassifiable", reason_code="PROVIDER_QUOTA_EXHAUSTED"
            )
        logger.error("classify.bad_request", error=message[:200])
        return ClassificationOutcome(status="unclassifiable", reason_code="PROVIDER_BAD_REQUEST")
    except (anthropic.APITimeoutError, TimeoutError):
        # BEFORE APIConnectionError, which APITimeoutError subclasses — the
        # other order reports every timeout as an unreachable provider. The
        # builtin TimeoutError is the outer deadline (`CallBound.deadline`).
        logger.warning("classify.timeout")
        return ClassificationOutcome(status="unclassifiable", reason_code="TIMEOUT")
    except anthropic.APIConnectionError:
        logger.warning("classify.connection_error")
        return ClassificationOutcome(status="unclassifiable", reason_code="PROVIDER_UNREACHABLE")
    except anthropic.APIError as exc:
        logger.error("classify.api_error", status=getattr(exc, "status_code", None))
        return ClassificationOutcome(status="unclassifiable", reason_code="PROVIDER_ERROR")

    # A refusal is not a classification. Surfacing it as one would store an
    # empty industry as though it were a finding.
    if response.stop_reason == "refusal":
        logger.warning("classify.refused")
        return ClassificationOutcome(status="unclassifiable", reason_code="PROVIDER_REFUSED")

    parsed = response.parsed_output
    if parsed is None:
        return ClassificationOutcome(status="unclassifiable", reason_code="PROVIDER_NO_OUTPUT")

    # Debug-only. Model-authored reasoning, never persisted, never returned.
    logger.debug(
        "classify.result",
        domain=crawl.signals.registrable_domain,
        industry=parsed.industry,
        score=parsed.confidence_score,
        rationale=parsed.rationale,
    )

    return decide(parsed)


def decide(parsed: IndustryClassification) -> ClassificationOutcome:
    """Turn a model response into a persistable outcome.

    Split out from `classify` so the threshold policy is unit-testable without
    a network call — the branch that matters most is the one that refuses to
    store a guess.
    """
    # Quantised so the stored value round-trips exactly against NUMERIC(4,3).
    score = Decimal(str(parsed.confidence_score)).quantize(Decimal("0.001"))

    if not parsed.is_classifiable or not parsed.industry:
        return ClassificationOutcome(
            status="unclassifiable",
            brand_name=parsed.brand_name,
            confidence=parsed.confidence,
            confidence_score=score,
            reason_code="NOT_A_BUSINESS_SITE",
        )

    if score < CONFIDENCE_THRESHOLD:
        # Brand name and score are still kept — they are useful and were not
        # what we were unsure about. Only `industry` is withheld.
        return ClassificationOutcome(
            status="ambiguous",
            brand_name=parsed.brand_name,
            confidence=parsed.confidence,
            confidence_score=score,
            reason_code="LOW_CONFIDENCE",
        )

    return ClassificationOutcome(
        status="classified",
        industry=parsed.industry.strip().lower()[:120],
        niche=(parsed.niche or None),
        brand_name=parsed.brand_name,
        confidence=parsed.confidence,
        confidence_score=score,
    )

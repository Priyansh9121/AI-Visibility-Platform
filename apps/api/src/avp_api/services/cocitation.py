"""AI co-citation discovery — §5.4 step 2, second half.

    "run seed prompts through AI engines, extract co-occurring brand mentions"

The signal: when a buyer asks an AI assistant a purchase question in this
category, which brands does it name? Anything named alongside — or instead of —
the subject is a competitor by the only definition that matters to this product,
namely *the set of brands an AI answer puts in front of a buyer*.

=============================================================================
IP-SAFETY BOUNDARY (ip-safety.md #7)
=============================================================================
Only **entity names and domains** are extracted and persisted. The engine's
prose is never stored, never returned by an endpoint, and never rendered. The
structured-output schema below has no field capable of holding answer text —
the constraint is enforced by the type, not by a reviewer noticing.
=============================================================================

**Scope note.** This is deliberately NOT Epic 4's engine runner. Epic 4 executes
a generated prompt set across multiple real engines and parses mentions,
positions and citations from the raw responses. Here a single model is asked one
structured question per seed prompt, purely to discover *who the rivals are*.
Keeping them separate means Epic 3 does not have to wait on the engine
abstraction, and Epic 4 is free to replace this signal without touching ranking.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import anthropic
import structlog
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from .call_bounds import CallBound
from .crawl import registrable_domain

logger = structlog.get_logger(__name__)

CO_CITATION_MODEL = "claude-opus-5"
# Discovery, not analysis — low effort keeps a multi-prompt run affordable.
CO_CITATION_EFFORT = "low"
CO_CITATION_MAX_TOKENS = 3_000
MAX_BRANDS_PER_PROMPT = 10

# --- The call's bound (API key discipline audit, 2026-09-07) -----------------
# Inherited both SDK defaults until this audit — a 600s read timeout across
# three attempts — while fanned four seed prompts wide at concurrency three,
# so one hung call held a slot for thirty minutes inside the competitor phase
# that the engine loop waits on. The measurement is the whole endpoint:
# `POST /clients/{id}/competitors`, six SerpApi searches AND these four calls,
# at 20.9s (build log, Epic 9.4's latency table). Thirty seconds per attempt
# is headroom over the endpoint itself, not just this call; the one retry is
# `engines.py`'s, kept for its reason.
CO_CITATION_BOUND = CallBound(timeout=30.0, max_retries=1)  # ceiling: 62.0s


class CoCitedBrand(BaseModel):
    """One brand named in an answer. Facts only — there is no text field."""

    name: str = Field(max_length=200, description="The brand or company name, as commonly written.")
    domain: str | None = Field(
        default=None,
        max_length=253,
        description=(
            "The brand's primary website domain if you know it, e.g. 'stripe.com'. "
            "Null if unsure — do not guess."
        ),
    )


class CoCitationAnswer(BaseModel):
    brands: list[CoCitedBrand] = Field(
        default_factory=list,
        description=(
            "The brands you would name in answer to this question, most prominent "
            "first. Real companies only — never categories, directories, review "
            "sites, or publications."
        ),
    )
    subject_named: bool = Field(
        description="Whether the subject brand would appear in your answer at all."
    )


SYSTEM_PROMPT = """\
You answer buyer research questions by naming real companies, the way a \
shopping assistant would.

Rules:
- Name only real, currently operating companies that a buyer could actually \
choose between. Never name review sites, directories, marketplaces, \
publications, or subreddits — they are places people read about the category, \
not options within it.
- Order by how prominently you would mention them, most prominent first.
- Give a domain only when you actually know it. A wrong domain is worse than \
null, because it will be matched against real search results.
- Do not include the subject brand in `brands`; report it via `subject_named`.
- If you do not know enough about this category to name real companies, return \
an empty list rather than plausible-sounding inventions."""


@dataclass(slots=True)
class CoCitationHit:
    name: str
    domain: str | None
    position: int
    prompt: str


@dataclass(slots=True)
class CoCitationResult:
    """Outcome of one seed prompt. **Transient — never persist.**"""

    prompt: str
    hits: list[CoCitationHit] = field(default_factory=list)
    subject_named: bool = False
    ok: bool = True
    error_code: str | None = None

    def redacted(self) -> dict[str, object]:
        return {
            "prompt": self.prompt,
            "hits": len(self.hits),
            "subject_named": self.subject_named,
            "ok": self.ok,
            "error_code": self.error_code,
        }


def build_seed_prompts(
    *, brand_name: str | None, domain: str, industry: str | None, niche: str | None
) -> list[str]:
    """Seed prompts for co-citation discovery.

    Same discipline as `serp.build_queries`, and for the same reason: there is
    no industry-keyed prompt dictionary. Brand-anchored prompts do not depend on
    `Client.industry` at all, so an uncalibrated or wrong classification
    (build-log Epic 2.6/2.8, Finding 2 — open) degrades recall rather than
    silently producing a confident, wrong competitor set.
    """
    prompts: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        cleaned = " ".join(p.split()).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            prompts.append(cleaned)

    anchor = (brand_name or "").strip() or domain

    # Independent of classification.
    add(f"What are the best alternatives to {anchor}?")
    add(f"I'm evaluating {anchor}. Which similar companies should I compare it against?")

    # Classification-seeded: improves recall when right, noise when wrong.
    seed = (niche or industry or "").strip()
    if seed:
        add(f"Who are the leading providers of {seed}?")
        add(f"I'm choosing a {seed}. Which companies should I be looking at?")

    return prompts


async def run_seed_prompt(
    prompt: str,
    *,
    subject_brand: str,
    settings: Settings | None = None,
) -> CoCitationResult:
    """Ask one seed prompt and extract the brands named. Never raises."""
    settings = settings or get_settings()
    client = CO_CITATION_BOUND.client(settings)

    try:
        async with CO_CITATION_BOUND.deadline():
            response = await client.messages.parse(
                model=CO_CITATION_MODEL,
                max_tokens=CO_CITATION_MAX_TOKENS,
                output_config={"effort": CO_CITATION_EFFORT},
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Subject brand: {subject_brand}\n\nQuestion: {prompt}",
                    }
                ],
                output_format=CoCitationAnswer,
            )
    except anthropic.AuthenticationError:
        return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_AUTH_FAILED")
    except anthropic.RateLimitError:
        return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_RATE_LIMITED")
    except anthropic.BadRequestError as exc:
        if "credit balance" in str(exc).lower():
            return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_QUOTA_EXHAUSTED")
        return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_BAD_REQUEST")
    except (anthropic.APITimeoutError, TimeoutError):
        # Before APIConnectionError, which APITimeoutError subclasses; the
        # builtin TimeoutError is the outer deadline (`CallBound.deadline`).
        return CoCitationResult(prompt=prompt, ok=False, error_code="TIMEOUT")
    except anthropic.APIConnectionError:
        return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_UNREACHABLE")
    except anthropic.APIError:
        return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_ERROR")

    if response.stop_reason == "refusal":
        return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_REFUSED")

    parsed = response.parsed_output
    if parsed is None:
        return CoCitationResult(prompt=prompt, ok=False, error_code="PROVIDER_NO_OUTPUT")

    hits: list[CoCitationHit] = []
    for position, brand in enumerate(parsed.brands[:MAX_BRANDS_PER_PROMPT], start=1):
        name = brand.name.strip()
        if not name:
            continue
        # Normalise a returned domain through the Public Suffix List so it can
        # be compared with SERP domains. A model may return "www.Stripe.com/"
        # or a full URL; SERP returns a registrable domain.
        domain = registrable_domain(brand.domain) if brand.domain else None
        hits.append(
            CoCitationHit(name=name, domain=domain or None, position=position, prompt=prompt)
        )

    return CoCitationResult(prompt=prompt, hits=hits, subject_named=parsed.subject_named)


async def run_seed_prompts(
    prompts: list[str],
    *,
    subject_brand: str,
    settings: Settings | None = None,
    concurrency: int = 3,
) -> list[CoCitationResult]:
    """Run seed prompts concurrently, bounded to stay inside rate limits."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(p: str) -> CoCitationResult:
        async with semaphore:
            return await run_seed_prompt(p, subject_brand=subject_brand, settings=settings)

    return list(await asyncio.gather(*(one(p) for p in prompts)))

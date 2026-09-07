"""Prompt generation — §5.4 step 3.

    "LLM generates prompt set based on industry + buyer journey stages"

The prompt set is the measuring instrument. Every number this product reports —
mention rate, share of voice, sentiment — is a statement about *these* prompts,
so their construction determines what the score actually means.

Design notes worth reading before changing this file.

**No industry-keyed template dictionary.** Same discipline as Epic 3's
`serp.build_queries` and `cocitation.build_seed_prompts`, and for the same
reason: `Client.industry` confidence is uncalibrated (build-log Epic 2.6/2.8,
Finding 2 — still open). Bespoke per-industry prompt sets would turn a bad
classification into a confident-looking scan, with nothing downstream able to
tell. Generation is one generic instruction parameterised by whatever the label
says, plus a deterministic **brand-anchored fallback** that does not depend on
the classification at all.

**Intent distribution is enforced in code, not requested in the prompt.** A
model asked for "a mix" returns whatever mix it feels like, and the mix decides
what the score measures — a set skewed to bottom-funnel prompts flatters a brand
with strong branded search and says nothing about discovery. The quotas below
are applied after generation.
"""

from __future__ import annotations

import anthropic
import structlog
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..models.prompt import PromptIntent
from .call_bounds import CallBound

logger = structlog.get_logger(__name__)

GENERATOR_MODEL = "claude-opus-5"
GENERATOR_EFFORT = "medium"
GENERATOR_MAX_TOKENS = 8_000

# --- The call's bound (API key discipline audit, 2026-09-07) -----------------
# Inherited both SDK defaults until this audit — a 600s read timeout across
# three attempts — and this call sits in front of the ENTIRE engine loop, so
# a hung generation was thirty minutes before the first engine call, with the
# scan reading "running" throughout. Measured at 14.6s on a 24-prompt run
# (build log, Epic 9.2); sixty seconds per attempt is four times that one
# measurement, and medium effort with an 8,000-token budget is what earns the
# headroom over the low-effort calls elsewhere. One retry, `engines.py`'s.
# A timeout here is not a failed scan: `fallback_prompts` is the deterministic
# floor, which is what lets the ceiling be this tight.
GENERATOR_BOUND = CallBound(timeout=60.0, max_retries=1)  # ceiling: 122.0s

# §7: "20-30 prompts per scan".
MIN_PROMPTS = 20
MAX_PROMPTS = 30
TARGET_PROMPTS = 24

# Buyer-journey mix, as a share of the set. Awareness is weighted highest
# because that is where invisibility actually costs a business: a buyer who
# already types the brand name has been reached by other means.
INTENT_QUOTA: dict[PromptIntent, float] = {
    PromptIntent.AWARENESS: 0.45,
    PromptIntent.COMPARISON: 0.35,
    PromptIntent.BOTTOM_FUNNEL: 0.20,
}

MAX_PROMPT_CHARS = 300


class GeneratedPrompt(BaseModel):
    text: str = Field(
        max_length=MAX_PROMPT_CHARS,
        description=(
            "The question, phrased exactly as a real buyer would type it into "
            "an AI assistant. Lower-case, conversational, no marketing language."
        ),
    )
    intent: PromptIntent = Field(
        description=(
            "awareness: buyer does not yet know who the players are. "
            "comparison: buyer is weighing named options against each other. "
            "bottom_funnel: buyer is close to choosing and wants specifics "
            "(pricing, availability, booking, migration)."
        )
    )


class GeneratedPromptSet(BaseModel):
    prompts: list[GeneratedPrompt] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You write the questions real buyers type into AI assistants when researching a \
purchase. These questions are a measuring instrument: they will be run against \
several AI engines to find out whether a specific business ever gets named.

Rules:
- Write what a BUYER would type, not what a marketer would write. "who does \
same-day crowns near me", not "leading provider of restorative dentistry".
- Most questions must NOT contain the subject brand's name. A question that \
names the brand can only confirm the brand exists; it cannot reveal whether the \
brand gets discovered. Name the brand only in comparison and bottom-funnel \
questions where a buyer plausibly would.
- Cover the buyer journey: questions from someone who does not yet know the \
players, questions weighing named options, and questions from someone close to \
deciding.
- Vary the phrasing genuinely. Ten wordings of the same question measure one \
thing ten times and make the score look more robust than it is.
- No question should presuppose an answer or lead toward any particular company.
- Keep each question under 300 characters."""


def build_generation_input(
    *,
    brand_name: str | None,
    domain: str,
    industry: str | None,
    niche: str | None,
    competitors: list[str] | None = None,
) -> str:
    """Assemble what the generator is told about the business.

    Industry and niche are supplied as *context*, never as a template key. When
    both are absent — an unclassified or ambiguous client — the generator still
    receives the brand and domain and produces a usable, if broader, set.
    """
    lines = [f"Subject brand: {brand_name or domain}", f"Website: {domain}"]
    if industry:
        lines.append(f"Industry (classified automatically, may be imprecise): {industry}")
    if niche:
        lines.append(f"Niche: {niche}")
    if competitors:
        lines.append("Known competitors: " + ", ".join(competitors[:5]))
    lines.append(
        f"\nWrite {TARGET_PROMPTS} questions. Aim for roughly "
        f"{int(INTENT_QUOTA[PromptIntent.AWARENESS] * 100)}% awareness, "
        f"{int(INTENT_QUOTA[PromptIntent.COMPARISON] * 100)}% comparison, "
        f"{int(INTENT_QUOTA[PromptIntent.BOTTOM_FUNNEL] * 100)}% bottom-funnel."
    )
    return "\n".join(lines)


def fallback_prompts(
    *, brand_name: str | None, domain: str, industry: str | None
) -> list[GeneratedPrompt]:
    """Deterministic prompt set for when generation fails.

    Not a substitute for a generated set — it is narrower and more mechanical.
    It exists so a provider outage degrades the scan rather than aborting it,
    and so a scan always has *some* instrument rather than silently none.

    Brand-anchored, so it works with no industry label at all.
    """
    anchor = (brand_name or "").strip() or domain
    seed = (industry or "").strip()
    subject = seed or "this kind of business"

    shapes: list[tuple[str, PromptIntent]] = [
        (f"who are the best providers of {subject}", PromptIntent.AWARENESS),
        (f"how do i choose a {subject}", PromptIntent.AWARENESS),
        (f"what should i look for in a {subject}", PromptIntent.AWARENESS),
        (f"who are the top rated options for {subject}", PromptIntent.AWARENESS),
        (f"what are the most recommended {subject} companies", PromptIntent.AWARENESS),
        (f"best alternatives to {anchor}", PromptIntent.COMPARISON),
        (f"{anchor} vs competitors", PromptIntent.COMPARISON),
        (f"how does {anchor} compare to other options", PromptIntent.COMPARISON),
        (f"is {anchor} a good choice", PromptIntent.COMPARISON),
        (f"how much does {subject} cost", PromptIntent.BOTTOM_FUNNEL),
        (f"how do i get started with {anchor}", PromptIntent.BOTTOM_FUNNEL),
    ]
    return [GeneratedPrompt(text=t[:MAX_PROMPT_CHARS], intent=i) for t, i in shapes]


def enforce_intent_mix(
    prompts: list[GeneratedPrompt], *, target: int = TARGET_PROMPTS
) -> list[GeneratedPrompt]:
    """Trim toward the quota and cap the set size.

    Applied in code rather than trusted to the model, because the mix decides
    what the score measures. Deterministic: prompts keep their generated order
    within each intent, so the same generated set always yields the same
    persisted set.

    Never pads. If the model under-produced one intent, the set is smaller and
    honest about it, rather than being topped up with near-duplicates that would
    inflate the denominator of every rate the score computes.
    """
    target = max(MIN_PROMPTS, min(MAX_PROMPTS, target))
    by_intent: dict[PromptIntent, list[GeneratedPrompt]] = {i: [] for i in PromptIntent}
    seen: set[str] = set()

    for prompt in prompts:
        text = " ".join(prompt.text.split()).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        by_intent[prompt.intent].append(GeneratedPrompt(text=text, intent=prompt.intent))

    quotas = {
        intent: max(1, round(target * share)) for intent, share in INTENT_QUOTA.items()
    }

    selected: dict[PromptIntent, list[GeneratedPrompt]] = {
        intent: by_intent[intent][: quotas.get(intent, 0)] for intent in PromptIntent
    }

    # Backfill from whatever is left over, so an over-productive intent can top
    # the set up toward the target.
    shortfall = target - sum(len(v) for v in selected.values())
    if shortfall > 0:
        for intent in PromptIntent:
            if shortfall <= 0:
                break
            leftover = by_intent[intent][quotas.get(intent, 0) :][:shortfall]
            selected[intent].extend(leftover)
            shortfall -= len(leftover)

    # INTERLEAVE by intent, round-robin.
    #
    # Grouping by intent looks harmless and is not: `position` follows this
    # order, and anything that takes a PREFIX of the set — the API's
    # `promptLimit`, a scan cut short by a rate limit, a partial re-run — then
    # measures awareness only. A six-prompt scan would report a mention rate
    # covering one third of the buyer journey while looking like a normal scan,
    # and nothing downstream could tell.
    #
    # Found by the Epic 4 live verification, where all six executed prompts came
    # back `awareness`. The unit test checked the ratio of the whole set, which
    # was correct, and said nothing about its order.
    #
    # Round-robin is deterministic: same generated set, same persisted order.
    interleaved: list[GeneratedPrompt] = []
    cursors = dict.fromkeys(PromptIntent, 0)
    while len(interleaved) < target:
        advanced = False
        for intent in PromptIntent:
            index = cursors[intent]
            bucket = selected[intent]
            if index < len(bucket):
                interleaved.append(bucket[index])
                cursors[intent] = index + 1
                advanced = True
                if len(interleaved) >= target:
                    break
        if not advanced:
            break

    return interleaved[:MAX_PROMPTS]


async def generate_prompts(
    *,
    brand_name: str | None,
    domain: str,
    industry: str | None,
    niche: str | None,
    competitors: list[str] | None = None,
    settings: Settings | None = None,
) -> tuple[list[GeneratedPrompt], str]:
    """Generate a prompt set. Returns (prompts, generated_by).

    Never raises: a provider failure falls back to the deterministic set, and
    `generated_by` records which path produced the result so a scan can always
    be explained after the fact.
    """
    settings = settings or get_settings()
    client = GENERATOR_BOUND.client(settings)

    try:
        # The outer deadline is what makes the ceiling a guarantee. Its
        # TimeoutError is not an APIError, hence the second clause below.
        async with GENERATOR_BOUND.deadline():
            response = await client.messages.parse(
                model=GENERATOR_MODEL,
                max_tokens=GENERATOR_MAX_TOKENS,
                output_config={"effort": GENERATOR_EFFORT},
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": build_generation_input(
                            brand_name=brand_name,
                            domain=domain,
                            industry=industry,
                            niche=niche,
                            competitors=competitors,
                        ),
                    }
                ],
                output_format=GeneratedPromptSet,
            )
    except (anthropic.APIError, TimeoutError) as exc:
        logger.warning("prompts.generation_failed", error=type(exc).__name__)
        return fallback_prompts(
            brand_name=brand_name, domain=domain, industry=industry
        ), "fallback"

    if response.stop_reason == "refusal" or response.parsed_output is None:
        logger.warning("prompts.generation_unusable", stop_reason=response.stop_reason)
        return fallback_prompts(
            brand_name=brand_name, domain=domain, industry=industry
        ), "fallback"

    prompts = enforce_intent_mix(response.parsed_output.prompts)
    if len(prompts) < MIN_PROMPTS:
        # Too thin to be a credible instrument. Top up from the deterministic
        # set rather than reporting rates over a handful of questions.
        logger.warning("prompts.under_target", generated=len(prompts))
        extra = [
            p
            for p in fallback_prompts(brand_name=brand_name, domain=domain, industry=industry)
            if p.text.lower() not in {q.text.lower() for q in prompts}
        ]
        prompts = (prompts + extra)[:MIN_PROMPTS]
        return prompts, f"{GENERATOR_MODEL}+fallback"

    return prompts, GENERATOR_MODEL

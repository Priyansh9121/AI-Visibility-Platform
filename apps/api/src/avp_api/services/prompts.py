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
says. The deterministic templates (`fallback_prompts`) top up a set that is a few
questions short, and only when the client has both a sensible category noun and a
market read from its domain; they are never a scan's whole instrument (F5).

**Intent distribution is enforced in code, not requested in the prompt.** A
model asked for "a mix" returns whatever mix it feels like, and the mix decides
what the score measures — a set skewed to bottom-funnel prompts flatters a brand
with strong branded search and says nothing about discovery. The quotas below
are applied after generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import anthropic
import structlog
from pydantic import BaseModel, Field, ValidationError

from ..config import Settings, get_settings
from ..models.prompt import PromptIntent
from . import usage
from .call_bounds import CallBound

logger = structlog.get_logger(__name__)

# Opus 5, measured against cheaper models under the last-lever brief
# (2026-09-11) and kept. `scripts/verify_prompt_models.py` generated sets for
# four subjects with this model, Sonnet 5 and Haiku 4.5, twice each, through
# the production prompt, schema and `enforce_intent_mix`, and read them
# against three rules the schema cannot check. Haiku 4.5: awareness questions
# a company could never be named in ("how do i fix a clogged drain in my
# bathroom", "how long do dental implants last"), which sit in the population
# Mention Rate is scored on and can only lower it; discovery questions
# labelled `comparison` ("24 hour emergency drain cleaning near me", "best
# drain cleaning company in my area" — four in one set), which moves them
# OUT of that population; one set with 7 of 24 awareness where the quota
# wants 11; one set naming the brand in 14 of 24 where the rule says most
# must not; and keyword strings rather than questions ("buy ooni pizza oven
# online", "all on four implants explained"). Sonnet 5 was closer but named
# the brand in 11–13 of 24 on four of eight sets and put knowledge questions
# in awareness too. Neither is zero loss on the instrument every score is a
# statement about. One call a scan, and the saving on offer was a fraction
# of it (figures in the build log).
GENERATOR_MODEL = "claude-opus-5"
GENERATOR_EFFORT = "medium"
GENERATOR_MAX_TOKENS = 8_000

# --- The call's bound (API key discipline audit, 2026-09-07) -----------------
# Inherited both SDK defaults until this audit — a 600s read timeout across
# three attempts — and this call sits in front of the ENTIRE engine loop, so
# a hung generation was thirty minutes before the first engine call, with the
# scan reading "running" throughout. Measured live on 2026-10-09 (US$0.17, founder-approved,
# RMIT University, five rivals): seven production-shaped single-attempt calls each returned
# exactly 20 questions, `end_turn`, 690-757 output tokens, 0 thinking tokens (Opus 5's default is
# adaptive at medium effort), 10.8-13.6 s, nothing over 300 characters; two more with thinking
# disabled explicitly were identical (696 and 751 tokens, 11-14 s). So sixty seconds per attempt
# is about five times the normal call and 8,000 tokens about ten times. A short production set
# was therefore most likely a failed call (a timeout, or an overloaded or 5xx answer that outlived
# the retry), which is why there are now two retries; the SDK retries timeouts, 408/409/429 and
# 5xx. Thinking is set explicitly because Opus 5 has no thinking token budget (`budget_tokens` is
# a 400 on it); `disabled` is the explicit form, accepted at effort high or below.
# A timeout here DOES fail the scan (F5, 2026-10-09): there is no template floor to fall back on
# any more, so a generation that times out ends the prompt phase as unavailable and the scan as
# FAILED / PROMPTS_UNAVAILABLE before any engine is called. An outage like this timeout never
# counts against the allowance; a failure caused by the model's answer is free three times per
# agency per rolling 24 hours and counts after that (`scan_executor._prompts_failure_code`).
# The tight ceiling is what keeps that failure fast.
# Three attempts of at most 60 s plus the backoff allowance. The scan deadline's sum counts this
# ceiling (`test_scan_executor.py`): it assumes every call in a scan times out at its ceiling.
GENERATOR_BOUND = CallBound(timeout=60.0, max_retries=2)  # ceiling: 182.0s

# §7: "20-30 prompts per scan".
MIN_PROMPTS = 20
MAX_PROMPTS = 30
# 20 SINCE 2026-09-12 — THE FOUNDER'S DECISION, not a tuning. It was 24 from
# Epic 4.1 to then. The spec's floor and this target now coincide: the four
# default engines cost a measured amount a prompt (build log, "the cost split"),
# and the founder took the spec's own minimum rather than a number below it.
# What it changes for a customer is the sample: the 45/35/20 quotas of 20
# round to 9 awareness / 7 comparison / 4 bottom-funnel prompts (against
# 11 / 8 / 5 at 24), and scoring v2 computes Mention Rate and Share of Voice
# over the awareness prompts only — so the population behind the two
# headline numbers is 9 questions per engine, down from 11. The pricing
# card and the landing page were changed with this constant. Build log,
# "dropping grounded Claude and cutting to 20 prompts".
TARGET_PROMPTS = 20

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
    """One question in a prompt set we persist: our own text, at most `MAX_PROMPT_CHARS`."""

    text: str = Field(max_length=MAX_PROMPT_CHARS)
    intent: PromptIntent


class ModelPrompt(BaseModel):
    """One question as the MODEL returns it: no length limit in the schema.

    The SDK strips JSON-schema constraints the API does not support (string `maxLength` among
    them) and validates them client-side, so a limit here made ONE over-long question raise a
    ValidationError for the whole response and sink the set. The length is judged in code, per
    item (`usable_questions`), and the item alone is dropped.
    """

    text: str = Field(
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
    prompts: list[ModelPrompt] = Field(default_factory=list)


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


# --- The templates (F5, 2026-10-09) ------------------------------------------------------------
# Until then the templates pasted the industry label into sentences built for service
# businesses ("who are the best providers of university", "how much does university cost") and
# named no market, and about half of one real scan's questions came from them. They are now
# either true sentences about a stated category in a stated market, or nothing at all, and the
# prompt phase then fails visibly (`generate_prompts`).

# The country each country-code ending names, as a phrase that reads after "in". The keys are
# `serp.SEARCH_COUNTRY_BY_TLD`'s, the list detection uses (a test holds the two equal). A domain
# ending in .com, .org, .io names no country, and detection's search default is not a fact about
# the client, so there is no market for it. A city is not readable from a domain.
MARKET_BY_TLD: Final[dict[str, str]] = {
    "au": "australia",
    "nz": "new zealand",
    "uk": "the uk",
    "ie": "ireland",
    "ca": "canada",
    "us": "the us",
    "za": "south africa",
    "in": "india",
    "sg": "singapore",
    "my": "malaysia",
    "ph": "the philippines",
    "hk": "hong kong",
    "id": "indonesia",
    "jp": "japan",
}

# Labels the classifier uses when it knows nothing; never a category noun.
_PLACEHOLDER_LABELS: Final = frozenset(
    {"other", "unknown", "general", "business", "services", "company", "n/a"}
)
_MAX_NOUN_WORDS: Final = 4


def category_noun(industry: str | None) -> str | None:
    """The client's industry label as a noun the templates can use, or None.

    Stripped, lower-cased, inner white space collapsed. Accepted only when it is one to four
    words of letters, hyphens and "&" and not a placeholder.
    """
    noun = " ".join((industry or "").lower().split())
    words = noun.split(" ") if noun else []
    if not words or len(words) > _MAX_NOUN_WORDS or noun in _PLACEHOLDER_LABELS:
        return None
    if not any(c.isalpha() for c in noun):
        return None
    if any(not (c.isalpha() or c in " -&") for c in noun):
        return None
    return noun


def market_phrase(domain: str) -> str | None:
    """The country the domain's ending names, as a phrase that reads after "in", or None."""
    host = domain.strip().lower().rstrip(".")
    if "." not in host:
        return None
    return MARKET_BY_TLD.get(host.rpartition(".")[2])


def fallback_prompts(
    *, brand_name: str | None, domain: str, industry: str | None
) -> list[GeneratedPrompt]:
    """Template questions for topping up a nearly-complete generated set, or none.

    Returns templates only when it has BOTH a category noun (`category_noun`) and a market
    (`market_phrase`); with either missing it returns an empty list rather than a sentence that
    reads wrongly or means nothing. Each template is grammatical for a count noun
    ("university"), a person noun ("dentist") and a mass noun ("customer service software").
    Deterministic. Never a whole scan's questions: `generate_prompts` uses these to fill a
    shortfall and refuses to scan on them alone.
    """
    noun = category_noun(industry)
    market = market_phrase(domain)
    if noun is None or market is None:
        return []
    brand = (brand_name or "").strip() or domain

    shapes: list[tuple[str, PromptIntent]] = [
        (f"what is the best {noun} in {market}", PromptIntent.AWARENESS),
        (f"which {noun} is best in {market}", PromptIntent.AWARENESS),
        (f"how do i choose the right {noun} in {market}", PromptIntent.AWARENESS),
        (f"what matters most when choosing the right {noun} in {market}", PromptIntent.AWARENESS),
        (f"what are the best alternatives to {brand} in {market}", PromptIntent.COMPARISON),
        (
            f"how does {brand} compare with other {noun} options in {market}",
            PromptIntent.COMPARISON,
        ),
        (f"is {brand} a good choice in {market}", PromptIntent.COMPARISON),
        (f"how much does {brand} cost", PromptIntent.BOTTOM_FUNNEL),
        (f"what should i know before choosing {brand}", PromptIntent.BOTTOM_FUNNEL),
    ]
    return [GeneratedPrompt(text=t[:MAX_PROMPT_CHARS], intent=i) for t, i in shapes]


def usable_questions(items: list[ModelPrompt]) -> tuple[list[GeneratedPrompt], int]:
    """The model's questions that can be scanned, and how many were dropped.

    Blank ones and ones over `MAX_PROMPT_CHARS` (white space collapsed, as the set is) are
    dropped one by one; the rest are kept.
    """
    kept: list[GeneratedPrompt] = []
    dropped = 0
    for item in items:
        text = " ".join(item.text.split())
        if not text or len(text) > MAX_PROMPT_CHARS:
            dropped += 1
            continue
        kept.append(GeneratedPrompt(text=text, intent=item.intent))
    return kept, dropped


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


# The fewest generated questions a scan will run on (F5). Below this the prompt phase fails.
MIN_GENERATED: Final = 15


class PromptsUnavailable(Exception):  # noqa: N818 - named for the error code it becomes
    """The prompt phase could not produce a set worth scanning. Becomes PROMPTS_UNAVAILABLE."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PromptResult:
    """What `generate_prompts` decided, and why.

    `unavailable` is None for a usable set, else the reason the phase failed
    (`generation_failed`, `schema_violation`, `unusable_answer`, `too_few_questions`); `prompts`
    is then empty. `from_generator` and `from_fallback` are the two counts that go to the log, the
    prompt set's `generation_params` and the CSV; `dropped` is the generator's blank or over-long
    questions that were not kept.
    """

    prompts: list[GeneratedPrompt]
    generated_by: str
    from_generator: int = 0
    from_fallback: int = 0
    dropped: int = 0
    unavailable: str | None = None


def _unavailable(reason: str, *, dropped: int = 0) -> PromptResult:
    return PromptResult(prompts=[], generated_by="none", dropped=dropped, unavailable=reason)


async def generate_prompts(
    *,
    brand_name: str | None,
    domain: str,
    industry: str | None,
    niche: str | None,
    competitors: list[str] | None = None,
    settings: Settings | None = None,
) -> PromptResult:
    """Generate a prompt set and say how it was made.

    Never raises. The generator's usable questions decide what is scanned:

    * `MIN_PROMPTS` or more: they are the set.
    * `MIN_GENERATED` up to `MIN_PROMPTS`: topped up to `MIN_PROMPTS` from the templates when
      the client has a category noun and a market (`fallback_prompts`); with no templates the
      set is the generator's alone, smaller and honest, as `enforce_intent_mix` never pads.
    * fewer, or a failed, timed-out or unusable call: the phase is `unavailable` and no
      template-only set is ever made. The scan ends FAILED before any engine is called.
    """
    settings = settings or get_settings()
    client = GENERATOR_BOUND.client(settings)

    try:
        # Counted, not changed (`usage.py`): one `llm.call` line for each request.
        with usage.track(
            usage.ANTHROPIC, usage.Purpose.PROMPT_GENERATION, model=GENERATOR_MODEL
        ) as call:
            # The outer deadline is what makes the ceiling a guarantee. Its
            # TimeoutError is not an APIError, hence the second clause below.
            async with GENERATOR_BOUND.deadline():
                response = await client.messages.parse(
                    model=GENERATOR_MODEL,
                    max_tokens=GENERATOR_MAX_TOKENS,
                    output_config={"effort": GENERATOR_EFFORT},
                    thinking={"type": "disabled"},
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
            call.read_anthropic(response)
    except (anthropic.APIError, TimeoutError) as exc:
        # The class, and for an API status error the HTTP status; never any response text.
        if isinstance(exc, anthropic.APIStatusError):
            logger.warning(
                "prompts.generation_failed", error=type(exc).__name__, status=exc.status_code
            )
        else:
            logger.warning("prompts.generation_failed", error=type(exc).__name__)
        return _unavailable("generation_failed")
    except ValidationError as exc:
        # The model answered and its answer did not fit the schema: pydantic's error, raised
        # inside the SDK's `parse`, is not an `anthropic.APIError`, so the clause above never saw
        # it and it escaped into a scan that had been paid for (checklist 3.76). Same handling as
        # `classify.py` and `fix_generator.py`; the call is still counted (`llm.call`, `ok=False`).
        errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "type": err.get("type"),
                # The LENGTH of the offending value, never the value: it is model-authored.
                "length": len(v) if isinstance(v := err.get("input"), str) else None,
            }
            for err in exc.errors()[:5]
        ]
        logger.error("prompts.schema_violation", errors=errors)
        return _unavailable("schema_violation")

    if response.stop_reason == "refusal" or response.parsed_output is None:
        logger.warning("prompts.generation_unusable", stop_reason=response.stop_reason)
        return _unavailable("unusable_answer")

    usable, dropped = usable_questions(response.parsed_output.prompts)
    generated = enforce_intent_mix(usable)
    if len(generated) < MIN_GENERATED:
        logger.warning("prompts.under_minimum", generated=len(generated), dropped=dropped)
        return _unavailable("too_few_questions", dropped=dropped)

    if len(generated) >= MIN_PROMPTS:
        return PromptResult(
            prompts=generated,
            generated_by=GENERATOR_MODEL,
            from_generator=len(generated),
            dropped=dropped,
        )

    # Too thin to be a full instrument, but enough to be an honest one: top up from the
    # templates when there are any.
    logger.warning("prompts.under_target", generated=len(generated))
    taken = {q.text.lower() for q in generated}
    extra = [
        p
        for p in fallback_prompts(brand_name=brand_name, domain=domain, industry=industry)
        if p.text.lower() not in taken
    ][: MIN_PROMPTS - len(generated)]
    return PromptResult(
        prompts=generated + extra,
        generated_by=f"{GENERATOR_MODEL}+fallback" if extra else GENERATOR_MODEL,
        from_generator=len(generated),
        from_fallback=len(extra),
        dropped=dropped,
    )

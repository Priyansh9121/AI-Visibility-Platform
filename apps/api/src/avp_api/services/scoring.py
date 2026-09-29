"""AI Visibility Score — §6 / docs/scoring-spec.md.

Turns persisted EngineResult, BrandMention, Citation and Competitor rows into a
composite 0-100 with its sub-score breakdown.

=============================================================================
DETERMINISM (scoring-spec.md "Determinism requirements")
=============================================================================
1. No wall-clock, no RNG, no set/dict iteration-order dependence. Every
   collection is sorted by an explicit total key before aggregating.
2. Fixed rounding, applied once. Sub-scores are computed at full precision and
   rounded half-up to 2dp; the composite is then computed FROM THE ROUNDED
   sub-scores, so a displayed breakdown always re-sums to the displayed total.
3. `Decimal` throughout, never float.
4. The LLM is never in the scoring path. Sentiment is read from
   EngineResult.sentiment, which Epic 4 persisted. Re-scoring never re-invokes
   a model.
5. Every Score row carries `formula_version`. Re-scoring INSERTS a new row
   rather than overwriting, so before/after reporting (Epic 11) stays honest.

=============================================================================
FACTS-ONLY RULE
=============================================================================
Scoring reads stored FACTS only — booleans, ordinals, labels, domains, counts.
It never touches raw engine text; there is none to touch, because Epic 4 never
persisted any. This module makes no provider calls of any kind.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

import structlog

from ..models.competitor import DetectionStatus
from ..models.engine_result import EngineResultStatus, Sentiment
from ..models.prompt import PromptIntent
from .domain_authority import CURATED_ONLY, DomainAuthority, Tier, describe

logger = structlog.get_logger(__name__)

# Bumped from v1 when no-competitor Share of Voice changed from "score 100" to
# "exclude and redistribute" (scoring-spec.md changelog). The §6 weights are
# unchanged, but the composite a given EngineResult set produces is not — and
# rule 5 exists so that difference is attributable rather than silent. Two Score
# rows with different formula_version are not comparable; Epic 11's before/after
# reporting depends on being able to tell.
# v2 — Mention Rate and Share of Voice are scored on AWARENESS prompts only.
# The reasoning is on `awareness_only` below and in scoring-spec.md's changelog;
# the measurement that forced it is build-log "Mention Rate may be measuring the
# prompt, not the engine". Rows carrying v1.1 were computed under the old
# definition and are NOT re-scored — rule 5 exists so before/after reporting
# compares like with like, and silently restating history is the thing it
# forbids.
# v2.1 — Citation Strength is EXCLUDED under NO_AUTHORITY_DATA and its 20
# points redistributed, until there is a source of citing-domain authority to
# measure it with. The stand-in that filled its place since Epic 5 divided the
# subject's own domain (0 or 1) by every distinct third-party domain the
# engines cited, so no brand could score above 1/N: across all 33 stored
# scores the maximum was 3.70, every rival on a comparison table showed the
# identical figure, and the pitch beat promised about 20 of its recoverable
# points from a dimension nobody could earn (build-log, second pilot dry run,
# 2026-09-08). An interim, not a redesign — scoring-spec.md's changelog has the
# numbers. Rows carrying v2 keep the number they were scored with.
# v3 — Citation Strength is MEASURED again, from an authority input that did
# not exist before: `services/domain_authority.py`, a curated structural tier
# and a platform-frequency tier (2026-09-27). A brand's citing domains are the
# third-party sources cited in answers that name it; each carries its tier's
# value from AUTHORITY_VALUES; the subject's sum is normalised against the
# best-scoring rival in the same scan, which is what scoring-spec.md said this
# dimension should do and what the v1-v2 stand-in never did. NO_AUTHORITY_DATA
# now fires only when no cited domain in the scan has a tier at all. A major
# bump, like v2: every composite with a classifiable citation moves, because a
# dimension re-enters the weighting. Rows carrying v2.1 keep their numbers.
# v4 — Citation Strength is scored on AWARENESS prompts only, the population
# Mention Rate and Share of Voice have used since v2 (2026-09-27, the same
# day as v3, from v3's own measurement). v3 credited a brand every tiered
# source cited in any answer that named it, and a brand-named question
# manufactures that co-occurrence: 14 of 23 scored scans read 100 and
# helpwise.io read 90.63 with an awareness Mention Rate of 0. Rivals are
# scored on the same population, so the column keeps one meaning. Ships with
# domain_authority's market-participant exclusion from Tier B (a client's or
# a detected competitor's domain cannot be a platform-tier source), which
# changes tiers and is carried by the digest. A major bump, by v2's precedent:
# a population change moves every composite that has the dimension. Rows
# carrying v3 keep their numbers.
# v5 — Citation Strength asks "when an engine names the brand, does it back it
# with authoritative sources?", not "how often is it backed" (2026-09-29). A
# brand's value is the MEAN, over awareness answers that name it, of that
# answer's authority sum — v3-v4 summed one set of citing domains over every
# such answer, so it grew with how often the brand was named, which is Mention
# Rate's question on Mention Rate's population. Measured over the 26 scored
# scans before changing anything: Spearman rho 0.781 with zeros excluded (n =
# 83), against a threshold of 0.70 fixed before the numbers were seen. Under
# v5 the same measurement reads 0.177. Weights, tiers, AUTHORITY_VALUES, the
# normalisation to the scan's best and the three exclusions are unchanged; a
# brand named in no awareness answer still scores 0, never excluded. A major
# bump: it redefines a weighted dimension, and moved two stored composites by
# more than 10 points. Rows carrying v4 keep their numbers.
# A v6 — shrinking each brand's mean toward the scan's pooled mean — was
# measured the same day and rejected by the rule fixed before it ran; see the
# note on `authority_when_named`. v5 stands.
FORMULA_VERSION = "v5"

TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")


class Dimension(str, enum.Enum):
    MENTION_RATE = "mention_rate"
    SHARE_OF_VOICE = "share_of_voice"
    CITATION_STRENGTH = "citation_strength"
    SENTIMENT = "sentiment"
    TECHNICAL_FOUNDATION = "technical_foundation"


# §6. Fixed inputs for this epic — per-industry tuning is deferred until real
# data exists, and re-tuning is explicitly not this epic's job.
WEIGHTS: dict[Dimension, Decimal] = {
    Dimension.MENTION_RATE: Decimal("30"),
    Dimension.SHARE_OF_VOICE: Decimal("25"),
    Dimension.CITATION_STRENGTH: Decimal("20"),
    Dimension.SENTIMENT: Decimal("15"),
    Dimension.TECHNICAL_FOUNDATION: Decimal("10"),
}

# Sentiment labels mapped onto the 0-100 axis. Neutral sits at the midpoint
# rather than at zero: being listed without evaluation is a materially better
# outcome than being warned against, and collapsing the two would make the
# dimension a near-duplicate of Mention Rate.
SENTIMENT_VALUES: dict[Sentiment, Decimal] = {
    Sentiment.POSITIVE: Decimal("100"),
    Sentiment.NEUTRAL: Decimal("50"),
    Sentiment.NEGATIVE: Decimal("0"),
}

# What a citing domain is worth by authority tier — v3. Only the RATIOS matter,
# because the subject's sum is normalised against the best rival's in the same
# scan; the numbers are chosen to say exactly what the evidence supports and
# nothing more precise.
#
#   CURATED  100  The tier with a structural basis: a `.gov`, a university, a
#                 reference work, a wire service, a cross-industry review
#                 platform. It is the ceiling by construction — there is no
#                 stronger kind of evidence in this system.
#   PLATFORM  50  A domain engines cite across at least three client industries
#                 and five clients. That is real evidence of breadth, and it is
#                 measured through `Client.industry`, whose classification
#                 confidence is uncalibrated (build-log Epic 2.6/2.8, Finding
#                 2) and whose fine-grained labels already admit single-market
#                 vendors as "seven industries". The midpoint says the ordering
#                 — worth more than nothing, less than a structural fact — and
#                 makes no claim about where between the two it falls, in the
#                 same spirit as SENTIMENT_VALUES putting neutral at 50. Any
#                 other number would imply a measurement nobody has made.
#   None       0  Unclassified. No tier is no evidence, and no evidence
#                 contributes nothing — the same as its absence did before v3.
#                 A brand cited by forty domains none of which is classifiable
#                 scores zero on authority, which is the honest reading of
#                 "number AND authority" when only the number is known.
AUTHORITY_VALUES: dict[Tier | None, Decimal] = {
    Tier.CURATED: Decimal("100"),
    Tier.PLATFORM: Decimal("50"),
    None: Decimal("0"),
}

# Only these statuses mean "the engine answered". A rate-limited or timed-out
# call is MISSING DATA, not evidence of absence — counting it in the mention-rate
# denominator would turn an outage into a low score, which is precisely the
# failure the PARTIAL scan status exists to prevent.
ANSWERED = (EngineResultStatus.OK, EngineResultStatus.ANSWERED_NO_MENTION)


class SubScoreOutOfRangeError(Exception):
    """A sub-score computed outside 0-100.

    scoring-spec.md: "Clamp to 100 AND raise — a silent clamp hides pipeline
    defects." Every sub-score here is a ratio bounded by construction, so a
    value outside the range means an upstream bug (double-counted mentions, a
    competitor mis-linked to the subject). The clamped value is carried on the
    exception so the defect is debuggable, but scoring stops rather than
    emitting a plausible-looking number built on broken input.
    """

    def __init__(self, dimension: Dimension, raw: Decimal, clamped: Decimal) -> None:
        super().__init__(
            f"{dimension.value} computed {raw}, outside 0-100 (clamped to {clamped}). "
            "This indicates an upstream data defect, not a scoring bug."
        )
        self.dimension = dimension
        self.raw = raw
        self.clamped = clamped


@dataclass(frozen=True, slots=True)
class ResultFacts:
    """One EngineResult, reduced to what scoring reads.

    A plain value object rather than the ORM row, so the scoring functions are
    pure and testable without a database — and so it is obvious at a glance that
    nothing here is third-party text.
    """

    result_id: str
    engine: str
    status: EngineResultStatus
    mentioned: bool
    sentiment: Sentiment | None
    # What the prompt was TRYING to find out. Load-bearing since v2: Mention
    # Rate and Share of Voice are scored on awareness prompts only, and
    # `awareness_only` below is where that is applied and argued.
    intent: PromptIntent | None = None
    # Which prompt produced this, so the same facts can be regrouped BY PROMPT
    # rather than by engine — `services/divergence.py` needs that and scoring
    # does not. Carried here rather than in a parallel value object, because two
    # objects describing one row is two places for a field to be forgotten.
    # Defaulted so the scoring tests that predate it keep constructing facts
    # without it; nothing in this module reads it.
    prompt_id: str = ""

    # (entity_name, is_subject) per detected brand.
    brands: tuple[tuple[str, bool], ...] = ()
    # Distinct cited domains and whether each cites the subject.
    citations: tuple[tuple[str, bool], ...] = ()
    # WHERE in the answer the brands appeared — measured, not scored (v4).
    # `position` is the subject's 1-based ordinal among the brands named
    # (EngineResult.position; None when absent); `brand_positions` is the same
    # per named brand (BrandMention.position). Neither is in the inputs
    # digest, because neither moves a score: `average_position` reports them
    # the way the technical audit reports Core Web Vitals — shown, unweighted.
    position: int | None = None
    brand_positions: tuple[tuple[str, int], ...] = ()

    @property
    def answered(self) -> bool:
        return self.status in ANSWERED


@dataclass(frozen=True, slots=True)
class CompetitorFacts:
    competitor_id: str
    name: str
    domain: str | None


@dataclass(slots=True)
class SubScore:
    dimension: Dimension
    value: Decimal | None
    included: bool
    weight: Decimal
    reason: str | None = None


@dataclass(slots=True)
class CompetitorComparison:
    """The dimensions that are genuinely measurable per competitor.

    Deliberately NOT a composite. Sentiment is classified toward the subject
    only (Epic 4.3) and TechnicalAudit is Epic 6, so 25% of the weight has no
    per-competitor input. A competitor "composite" computed over a different
    weight basis would not be comparable to the subject's — which is the entire
    purpose of a comparison — so none is produced.
    """

    competitor_id: str
    name: str
    mention_rate: Decimal
    share_of_voice: Decimal
    # None when the subject's own Citation Strength is excluded: a column
    # means one thing (v2.1). Since v3 it is a number whenever the subject's
    # is — the rival's own figure (since v5 its mean authority per answer that
    # names it) against the same best-in-scan.
    citation_strength: Decimal | None
    # Mean ordinal among the brands named, over awareness answers that named
    # this rival, and how many answers that is. None / 0 when never named on
    # an awareness prompt — never 0.0, which would read as "first". Reported,
    # not scored: see `average_position`.
    average_position: Decimal | None = None
    position_count: int = 0


@dataclass(slots=True)
class ScoreResult:
    status: str
    composite: Decimal | None
    sub_scores: dict[Dimension, SubScore]
    weights: dict[str, str]
    excluded_dimensions: dict[str, str] = field(default_factory=dict)
    degradation_flags: list[str] = field(default_factory=list)
    reason_code: str | None = None
    inputs_digest: str = ""
    formula_version: str = FORMULA_VERSION
    competitors: list[CompetitorComparison] = field(default_factory=list)
    # Measured, not scored — the subject's `average_position` and the count
    # it is averaged over. Not a sub-score, not in `sub_scores`, not in the
    # weights, not on the Score row: derived on read like the comparison.
    average_position: Decimal | None = None
    position_count: int = 0

    def value(self, dimension: Dimension) -> Decimal | None:
        return self.sub_scores[dimension].value


def weighted_composite(
    effective_weights: dict[Dimension, Decimal], values: dict[Dimension, Decimal]
) -> Decimal:
    """Weighted sum of the included sub-scores, on a 0-100 axis.

    Extracted from `compute_score` so scoring-spec.md rule 3 ("Decimal, not
    float") is directly testable. It is not a style rule: at a 2dp rounding
    boundary the two disagree — e.g. weights 33.33/27.78/22.22/16.67 over values
    67.13/77.75/77.55/50.33 give 69.60 in Decimal and 69.59 in binary float.
    A one-point swing in a client-facing score, from arithmetic alone.

    Dimensions are iterated in a fixed order so the sum is associative in
    practice as well as in theory.
    """
    return sum(
        (
            effective_weights[d] * values[d]
            for d in sorted(effective_weights, key=lambda d: d.value)
            if values.get(d) is not None
        ),
        Decimal("0"),
    ) / HUNDRED


def _round2(value: Decimal) -> Decimal:
    """Half-up to 2dp. The only rounding applied to a sub-score."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _guard(dimension: Dimension, raw: Decimal) -> Decimal:
    """Clamp to 0-100 and raise if the raw value was outside it."""
    clamped = min(HUNDRED, max(Decimal("0"), raw))
    if raw != clamped:
        logger.error(
            "scoring.subscore_out_of_range",
            dimension=dimension.value, raw=str(raw), clamped=str(clamped),
        )
        raise SubScoreOutOfRangeError(dimension, raw, clamped)
    return clamped


def compute_inputs_digest(
    results: list[ResultFacts],
    competitors: list[CompetitorFacts],
    technical_foundation: Decimal | None = None,
    domain_tiers: dict[str, str] | None = None,
) -> str:
    """Fingerprint the exact inputs a score was computed from.

    Two scores with the same digest were computed from identical data, so a
    changed score can always be attributed to either changed inputs or a changed
    formula version — never left ambiguous.

    Everything is sorted before serialising; a set or dict iteration order
    leaking in here would make the digest unstable across runs and destroy its
    only purpose.

    `technical_foundation` is here because Epic 6 made it a scored input and
    this function was not widened to match. Until Epic 3.10 the digest covered
    `results` and `competitors` only, so re-auditing a site moved the composite
    while the digest and formula_version both stayed identical — the exact
    ambiguity the paragraph above promises is impossible. Demonstrated at the
    time: the same results and competitors with technical_foundation 20 vs 95
    scored 79.50 vs 87.00 under one unchanged digest.

    It is serialised as a string rather than a float for the reason
    scoring-spec.md rule 3 gives everywhere else: `str(Decimal)` is exact, and
    a binary float would make the fingerprint itself platform-fragile.

    `domain_tiers` — v3 — is the tier of every cited domain that has one, at
    the moment of scoring. Tier B is a frequency over the whole platform's
    stored scans, so it moves as scans accumulate: the same results re-scored
    a month later can carry a different Citation Strength under the same
    formula. That is an input change, and rule 1 says an input that can move
    the composite must be in the digest or the change is unattributable —
    the third time this function has been widened for that reason.

    v5 needed no widening, and a test holds that: its per-answer mean reads
    which answer cited which domain, and `results` already records every
    result's own citations, brands, `mentioned`, intent and status — so
    moving a source from one answer to another moves the digest.
    """
    payload = {
        "formula_version": FORMULA_VERSION,
        "results": [
            {
                "id": r.result_id,
                "engine": r.engine,
                "status": r.status.value,
                # v2: the intent selects the scoring population, so it is an
                # INPUT to the score. Rule 1 — a value that can move the
                # composite and is absent from the digest makes a changed score
                # unattributable, which is the ambiguity the digest exists to
                # prevent (see `technical_foundation` above, added for the same
                # reason after the same omission).
                "intent": r.intent.value if r.intent else None,
                "mentioned": r.mentioned,
                "sentiment": r.sentiment.value if r.sentiment else None,
                "brands": sorted(r.brands),
                "citations": sorted(r.citations),
            }
            for r in sorted(results, key=lambda r: r.result_id)
        ],
        "competitors": [
            {"id": c.competitor_id, "name": c.name, "domain": c.domain}
            for c in sorted(competitors, key=lambda c: c.competitor_id)
        ],
        "technical_foundation": (
            str(technical_foundation) if technical_foundation is not None else None
        ),
        "domain_tiers": dict(sorted((domain_tiers or {}).items())),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Sub-scores. Each is a pure function over stored facts.
# --------------------------------------------------------------------------


def awareness_only(results: list[ResultFacts]) -> list[ResultFacts]:
    """The results that can actually evidence discovery — v2.

    **WHY THIS EXISTS.** `prompts.py`'s generator is instructed to name the
    subject brand in `comparison` and `bottom_funnel` questions, and its own
    system prompt states the reason: *"A question that names the brand can only
    confirm the brand exists; it cannot reveal whether the brand gets
    discovered."* `INTENT_QUOTA` makes those 55% of every set. A mention is a
    text match (`extraction.find_brand`), so a question naming the brand very
    nearly guarantees the answer echoes it.

    That was measured across every scored scan in `avp_dev`: where the prompt
    named the brand, 255 of 256 answered rows registered a mention (99.61%);
    where it did not, 386 of 497 (77.67%). An engine answering *"I don't have
    any knowledge of a product called Zorblex Inbox"* was recorded as a mention
    at position 1.

    **WHY IT IS THE RIGHT POPULATION**, rather than one of several defensible
    ones. `product-spec.md`'s Epic 0 states the product's two jobs, and the
    first is *"prospecting — prove to a stranger they are invisible in AI
    answers."* Invisibility is an unprompted property. A question that names
    the brand cannot demonstrate it, whatever the answer says. The requirement
    and the prompt generator already agreed; only this module was out of step.

    **WHY IT IS A FUNCTION AND NOT A CHANGE TO `mention_rate`.** The rate
    functions answer "what fraction of THESE results mention the subject",
    which is a calculation. WHICH results belong in the population is a scoring
    POLICY, and it lives with the rest of the policy in `compute_score`.
    Keeping them apart also means `services/divergence.py`, which calls
    `mention_rate` on its own per-engine slices, is unaffected by this change
    rather than silently re-scoped by it.
    """
    return [r for r in results if r.intent is PromptIntent.AWARENESS]


def mention_rate(results: list[ResultFacts]) -> Decimal:
    """% of ANSWERED prompt x engine pairs naming the subject."""
    answered = [r for r in results if r.answered]
    if not answered:
        return Decimal("0")
    hits = sum(1 for r in answered if r.mentioned)
    return _guard(Dimension.MENTION_RATE, Decimal(hits) / Decimal(len(answered)) * HUNDRED)


def share_of_voice(results: list[ResultFacts]) -> Decimal:
    """Subject mentions ÷ total brand mentions (subject + competitors).

    Counts every brand appearance across answered results, so a competitor named
    in eight answers outweighs one named twice. When the subject and every
    competitor appear equally often this yields exactly 100/(1+n), which is the
    behaviour scoring-spec.md's "all competitors tied" case requires.
    """
    answered = [r for r in results if r.answered]
    subject_mentions = sum(1 for r in answered for _, is_subject in r.brands if is_subject)
    total_mentions = sum(len(r.brands) for r in answered)
    if total_mentions == 0:
        return Decimal("0")
    return _guard(
        Dimension.SHARE_OF_VOICE,
        Decimal(subject_mentions) / Decimal(total_mentions) * HUNDRED,
    )


def _cited_domains(results: list[ResultFacts]) -> set[str]:
    return {d for r in results if r.answered for d, _ in r.citations}


def _authority_credit(domains: set[str], tiers: dict[str, Tier]) -> Decimal:
    """Sum of tier values over the domains that have a tier. Sorted, Decimal."""
    return sum(
        (AUTHORITY_VALUES[tiers[d]] for d in sorted(domains) if d in tiers),
        Decimal("0"),
    )


def answer_authority(
    results: list[ResultFacts],
    competitors: list[CompetitorFacts],
    tiers: dict[str, Tier],
) -> tuple[list[Decimal], dict[str, list[Decimal]]]:
    """Each answer's authority sum, per brand, over the answered results that name it.

    An answer's authority sum for a brand is the AUTHORITY_VALUES of the
    distinct third-party domains it cites, less the brand's own domain (a
    brand is not a third party to itself). The same rule for the subject
    (`mentioned`, `cites_subject`) and for each rival (named in `brands`,
    `domain` from the competitor set). The length of a brand's list is how
    many answers name it. `authority_when_named` aggregates these; the
    measurement script reads them to find which brand sets a scan's scale.
    """
    answered = sorted((r for r in results if r.answered), key=lambda r: r.result_id)
    subject = [
        _authority_credit({d for d, cites_subject in r.citations if not cites_subject}, tiers)
        for r in answered if r.mentioned
    ]
    by_competitor: dict[str, list[Decimal]] = {}
    for competitor in sorted(competitors, key=lambda c: c.competitor_id):
        by_competitor[competitor.competitor_id] = [
            _authority_credit({d for d, _ in r.citations if d != competitor.domain}, tiers)
            for r in answered
            if any(name == competitor.name and not is_subject for name, is_subject in r.brands)
        ]
    return subject, by_competitor


def authority_when_named(
    results: list[ResultFacts],
    competitors: list[CompetitorFacts],
    tiers: dict[str, Tier],
) -> tuple[Decimal, dict[str, Decimal]]:
    """How strongly each brand is backed WHEN it is named — v5.

    For every answer that names a brand, that answer's authority sum
    (`answer_authority`). The brand's value is the MEAN of those sums over
    the answers that name it. A brand named in no answer is 0 — and still
    scored: an exclusion would hand its weight to the other dimensions and
    could RAISE an invisible brand's composite.

    **A mean lets a rarely named brand set the scale, and shrinkage was
    measured and rejected (v6, 2026-09-29).** Pulling each brand toward the
    scan's pooled mean by three answers' worth kept ρ against Mention Rate at
    0.301, but left the scans whose scale rests on fewer than five answers at
    3 of 19, the same three. The rule required that count to fall.
    scoring-spec.md, under v5, has both sets of numbers.

    **Why a mean, and not v3–v4's sum over every citing domain.** The sum
    grew with how often a brand was named: more answers naming it, more
    sources to credit, and from v4 both it and Mention Rate read the same
    awareness answers. Measured over the 26 scored scans in `avp_dev`
    (2026-09-29), the two sub-scores had Spearman ρ = 0.781 with zeros
    excluded (n = 83; 0.880 over all 132 pairs), so the composite was counting
    one fact twice, at 30 + 20 = 50% of its weight. Frequency is Mention
    Rate's question. This one is "when an engine names you, does it back you
    with authoritative sources?", and it is answered per answer.

    **Attribution is still by co-occurrence**: an answer that names two brands
    and cites three sources credits all three to both. That is the limit of
    what facts-only storage can support — nothing here knows what a cited page
    is ABOUT.

    **Which results.** Whatever it is handed; `citation_strength` and
    `compare_competitors` hand it the AWARENESS results (v4): a question that
    names the brand manufactures the co-occurrence, which is why helpwise.io
    read 90.63 under v3 on a scan where no unprompted question named it.
    """
    subject, rivals = answer_authority(results, competitors, tiers)

    def mean_credit(credits: list[Decimal]) -> Decimal:
        if not credits:
            return Decimal("0")
        return sum(credits, Decimal("0")) / Decimal(len(credits))

    return mean_credit(subject), {cid: mean_credit(c) for cid, c in rivals.items()}


def citation_strength(
    results: list[ResultFacts],
    competitors: list[CompetitorFacts],
    *,
    authority: DomainAuthority,
) -> tuple[Decimal, list[str]]:
    """Authority of the domains citing the subject, against the scan's best — v3.

    §6 asks for "number AND authority of domains citing the brand". Until v3
    **there was no authority data in this system** — no Domain Authority feed,
    no backlink source, nothing upstream — and the stand-in that filled the
    dimension divided the subject's own domain (0 or 1) by every distinct
    third-party domain the engines cited. That is 1/N for any brand cited at
    all: 3.70 was the most any of 33 stored scores reached, every rival on a
    comparison table showed the identical figure, and v2.1 excluded the
    dimension under NO_AUTHORITY_DATA rather than score it. The intent the
    old docstring stated — normalise against the best-cited brand in the same
    scan, so "how close is the subject to the most-cited player here" is the
    question — was never what its arithmetic did.

    **What replaced it.** `authority` (`services/domain_authority.py`) gives
    each cited domain a tier: curated-structural, platform-frequency, or none.
    Since v5 `authority_when_named` gives each brand the MEAN authority of the
    answers that name it — each answer's AUTHORITY_VALUES summed over its
    citing domains — and the subject's mean is normalised against the best
    mean in the scan, its own or a rival's, exactly as scoring-spec.md
    specifies. (v3-v4 summed one set of citing domains over all those
    answers, which grew with how often the brand was named — Mention Rate's
    question; `authority_when_named` has the measurement.) Normalising
    against the scan's best rather than an absolute target is the reason the
    old docstring gave and it still holds: "is 7 citing domains good" is not
    answerable from what we have; "how close is the subject to the
    best-backed player here" is.

    **Population — v4: awareness prompts only**, the same as Mention Rate and
    Share of Voice. scoring-spec.md closed this question for Citation
    Strength in v2 and said reopening it needed a reason of its own; v3's
    measurement is that reason, and `authority_when_named` states it. Sentiment
    keeps every answered result — portrayal is a signal whoever raised the
    brand — and the two dimensions now differ on purpose.

    **Flags.** NO_AWARENESS_POPULATION when the set has no awareness prompts:
    nothing can evidence discovery, the same exclusion the other two
    discovery dimensions get. NO_AUTHORITY_DATA when the awareness answers
    cite nothing tiered — no authority input to measure with, so
    `compute_score` excludes the dimension exactly as v2.1 did. Brand-named
    answers citing tiered sources do not rescue it: that is the co-occurrence
    the population change exists to stop counting. NO_CITATIONS_IN_SCAN stays
    a fact about the WHOLE scan. Whether a competitor set exists is
    `compute_score`'s call, not this function's: with no rivals the subject
    is compared with itself here, and the caller decides that is not a score.
    """
    flags: list[str] = []
    answered = [r for r in results if r.answered]
    if not _cited_domains(answered):
        # No engine in this scan cited anything at all — typically because no
        # grounded engine ran. Distinct from "cited others but not you".
        flags.append("NO_CITATIONS_IN_SCAN")

    aware = awareness_only(answered)
    if not aware:
        flags.append("NO_AWARENESS_POPULATION")
        return Decimal("0"), flags

    tiers = authority.tiers_for(_cited_domains(aware))
    if not tiers:
        flags.append("NO_AUTHORITY_DATA")
        return Decimal("0"), flags

    subject, rivals = authority_when_named(aware, competitors, tiers)
    best = max([subject, *rivals.values()])
    if best == 0:
        # Tiered sources were cited, but in answers that named nobody. There
        # is a scan-level input and no brand-level signal: 0, as Share of
        # Voice returns 0 when no brand at all was named.
        return Decimal("0"), flags
    return _guard(Dimension.CITATION_STRENGTH, subject / best * HUNDRED), flags


def average_position(
    results: list[ResultFacts], *, brand: str | None = None
) -> tuple[Decimal | None, int]:
    """Mean position of a brand across the answers that named it — measured, NOT scored.

    **What it is.** For the subject (`brand=None`): `EngineResult.position`,
    the 1-based ordinal of the subject among the brands the answer named,
    averaged over answered results where `mentioned` is true. For a rival:
    `BrandMention.position` for that name, averaged over the results that
    named it. Returned with the count it is averaged over, because "2.4" and
    "2.4 across 9 answers" are different claims and the report shows the
    second. Rounded half-up to 2dp, Decimal, like everything else here.

    **A brand never named has NO average position** — None and a count of 0.
    Never 0, which is a better position than first; never a large number,
    which is a position it did not hold. The report renders "not named".

    **Which answers.** The caller passes the population, and `compute_score`
    passes `awareness_only(answered)`, for the reason section 1 of the v4
    brief gives about co-occurrence and this dimension's own sharper form of
    it: a question that names the brand puts the brand at position 1 almost
    by construction. Measured before this was written: on the stored scans,
    694 of 931 subject mentions are at position 1 overall.

    **Why it is not in the composite.** The Core Web Vitals precedent
    (`technical_audit.py`): a measurement that varies between runs is stored
    and shown, and it moves no score. Measured on the re-run pairs in
    `avp_dev` before writing this: the subject's awareness average moved
    1.23 → 1.36 across three plausible.io runs and 1.08 → 1.12 across two
    notion.so runs — stable, at position one — while rivals on 9 to 22
    answers moved by up to 0.6 of a position (Airtable 3.17 → 2.56, PostHog
    3.31 → 3.89, Slite 2.85 → 2.40), and a rival named three times appeared
    at 5.00 in one run and not at all in the next. A mean over a handful of
    answers moves a lot between runs; whether to weight it is a decision for
    the founder, not for the pass that added the figure. It is deliberately
    absent from `compute_inputs_digest`, `Dimension`, `WEIGHTS` and the
    Score row, so nothing can mistake it for a sub-score.
    """
    if brand is None:
        positions = [
            r.position for r in results if r.answered and r.mentioned and r.position is not None
        ]
    else:
        positions = [
            position
            for r in results if r.answered
            for name, position in r.brand_positions
            if name == brand
        ]
    if not positions:
        return None, 0
    mean = sum((Decimal(p) for p in sorted(positions)), Decimal("0")) / Decimal(len(positions))
    return _round2(mean), len(positions)


def sentiment_score(results: list[ResultFacts]) -> Decimal | None:
    """Mean of stored sentiment labels across results that named the subject.

    Returns None when there is no population — no mention means no sentiment.
    scoring-spec.md requires that case be EXCLUDED and its weight redistributed,
    not scored 0: a brand nobody mentions has not been spoken of badly, and
    scoring it 0 would punish the same absence twice, once through Mention Rate
    and again here.

    Reads EngineResult.sentiment as persisted by Epic 4. No model call.
    """
    labelled = sorted(
        (r for r in results if r.answered and r.mentioned and r.sentiment is not None),
        key=lambda r: r.result_id,
    )
    if not labelled:
        return None
    total = sum((SENTIMENT_VALUES[r.sentiment] for r in labelled), Decimal("0"))
    return _guard(Dimension.SENTIMENT, total / Decimal(len(labelled)))


def compare_competitors(
    results: list[ResultFacts],
    competitors: list[CompetitorFacts],
    *,
    include_citation_strength: bool = True,
    authority: DomainAuthority = CURATED_ONLY,
) -> list[CompetitorComparison]:
    """Per-competitor figures for the three measurable dimensions.

    No composite — see CompetitorComparison's docstring.

    **Each figure uses the SAME population as the subject's own — v2.** Epic 7's
    report renders `score.mentionRate` and each `competitor.mentionRate` in one
    column of one table, so two populations in that column would be a
    comparison of different things presented as a comparison of brands. Worse,
    it would be biased in a specific direction: the subject's rate would be
    awareness-only while every rival's kept the inflated all-prompt figure, so
    the client would look worse than the competitors it is being measured
    against, on the strength of an accounting mismatch.

    So Mention Rate and Share of Voice are computed over awareness results here
    exactly as they are for the subject, and Citation Strength over every
    answered result exactly as the subject's is. The rule is that a column
    means one thing.

    **And when the subject's Citation Strength is excluded, the rivals' is
    None** — v2.1. The stand-in formula gives every single-domain brand the
    same 1/N, which is how a real report came to show 0.66 for the subject and
    all five rivals. A column of identical numbers under a dimension the score
    itself leaves out is the same accounting mismatch this docstring already
    refuses, from the other side.

    **When it is included — v3 — each rival's figure is its own authority
    against the SAME best-in-scan the subject was normalised against**, so
    the best-backed brand in the table reads 100 and everyone else reads their
    distance from it. One denominator for one column — and since v4 one
    population for it too: awareness results, exactly as the subject's. Since
    v5 that authority is the rival's mean per answer that names it, the same
    measure as the subject's.
    """
    answered = [r for r in results if r.answered]
    if not answered or not competitors:
        return []

    # Discovery dimensions, matching the subject. Empty when a prompt set has
    # no awareness questions — the case where the subject's own two are
    # excluded, so these fall to zero and the report has no comparison to draw.
    aware = awareness_only(answered)
    total_mentions = sum(len(r.brands) for r in aware)
    tiers = authority.tiers_for(_cited_domains(aware))
    subject_authority, rival_authority = authority_when_named(aware, competitors, tiers)
    best_authority = max([subject_authority, *rival_authority.values()])

    out: list[CompetitorComparison] = []
    for competitor in sorted(competitors, key=lambda c: c.competitor_id):
        appearances = sum(
            1 for r in aware for name, is_subject in r.brands
            if not is_subject and name == competitor.name
        )
        answered_with = sum(
            1 for r in aware
            if any(name == competitor.name and not is_subject for name, is_subject in r.brands)
        )
        rival_position, rival_position_count = average_position(aware, brand=competitor.name)
        out.append(
            CompetitorComparison(
                competitor_id=competitor.competitor_id,
                name=competitor.name,
                average_position=rival_position,
                position_count=rival_position_count,
                mention_rate=_round2(
                    Decimal(answered_with) / Decimal(len(aware)) * HUNDRED
                    if aware else Decimal("0")
                ),
                share_of_voice=_round2(
                    Decimal(appearances) / Decimal(total_mentions) * HUNDRED
                    if total_mentions else Decimal("0")
                ),
                citation_strength=(
                    _round2(
                        rival_authority[competitor.competitor_id] / best_authority * HUNDRED
                        if best_authority
                        else Decimal("0")
                    )
                    if include_citation_strength
                    else None
                ),
            )
        )
    return out


def compute_score(
    results: list[ResultFacts],
    competitors: list[CompetitorFacts],
    *,
    competitor_set_status: DetectionStatus | None,
    technical_foundation: Decimal | None = None,
    authority: DomainAuthority = CURATED_ONLY,
) -> ScoreResult:
    """Compute the composite and its breakdown.

    `technical_foundation` is a parameter rather than a computation because
    TechnicalAudit is Epic 6. Passing None excludes the dimension and
    redistributes its weight — see the exclusion logic below.

    `authority` — v3 — is the citing-domain tier lookup. The default carries
    the curated tier only, which is right for a unit test and wrong for
    production: `scoring_runner.score_scan` loads the platform tier from the
    database and passes it, and that is the only place this is called with
    real rows.
    """
    tiers = authority.tiers_for({d for r in results for d, _ in r.citations})
    digest = compute_inputs_digest(
        results, competitors, technical_foundation, domain_tiers=describe(tiers)
    )
    weights_recorded = {
        d.value: str(w) for d, w in sorted(WEIGHTS.items(), key=lambda kv: kv[0].value)
    }
    answered = [r for r in results if r.answered]

    # An unrunnable scan must never render as a bad score.
    if not answered:
        return ScoreResult(
            status="insufficient_data",
            composite=None,
            sub_scores={
                d: SubScore(
                    d, None, included=False, weight=WEIGHTS[d],
                    reason="NO_ANSWERED_RESULTS",
                )
                for d in Dimension
            },
            weights=weights_recorded,
            excluded_dimensions={d.value: "NO_ANSWERED_RESULTS" for d in Dimension},
            degradation_flags=["NO_ANSWERED_RESULTS"],
            reason_code="INSUFFICIENT_DATA",
            inputs_digest=digest,
        )

    flags: list[str] = []
    excluded: dict[str, str] = {}

    # --- the scoring population for the two DISCOVERY dimensions — v2 -------
    #
    # Mention Rate and Share of Voice both answer a question about being FOUND,
    # and `awareness_only` is where the argument for that population lives.
    # Citation Strength, Sentiment and Technical Foundation keep every answered
    # result on purpose: they are not about discovery. Sentiment in particular
    # asks how an answer PORTRAYS the brand, which is a real signal whether the
    # buyer named it or not — a deliberate distinction, with its own numbers, in
    # the build-log entry.
    aware = awareness_only(answered)

    # --- Mention Rate ------------------------------------------------------
    mr: Decimal | None
    if not aware:
        # No awareness prompts is no evidence about discovery, and scoring that
        # 0 would punish a client for the shape of a prompt set they did not
        # choose. Excluded and redistributed — the same treatment the spec
        # already prescribes for sentiment with no population and for
        # Share of Voice with no competitor set. Unreachable through the
        # generator (45% awareness quota) and through `fallback_prompts` (five
        # awareness shapes), so this guards a hand-built set rather than a
        # normal scan.
        mr = None
        excluded[Dimension.MENTION_RATE.value] = "NO_AWARENESS_POPULATION"
        flags.append("NO_AWARENESS_POPULATION")
    else:
        mr = _round2(mention_rate(aware))

    # --- Citation Strength — measured since v3; excluded on two conditions --
    #
    # §6 asks for "number AND authority of domains citing the brand". v2.1
    # excluded the dimension because there was no authority source and the
    # stand-in in its place could not be earned: the subject's own domain (0
    # or 1) over every distinct third-party domain cited, so 1/N — on a real
    # 24-prompt scan N was 152, the subject and all five rivals scored 0.66,
    # and across every stored score the maximum was 3.70. `citation_strength`
    # now reads tiers from `authority` and normalises the subject's authority
    # against the best in the scan; its docstring has the whole account.
    #
    # Since v4 it is measured on AWARENESS results, like the two discovery
    # dimensions, and since v5 a brand's authority is its mean per answer that
    # names it, so it no longer grows with how often it is named —
    # `authority_when_named` has both measurements.
    #
    # Three exclusions, different facts, in a fixed order of precedence:
    #
    # 1. NO_AWARENESS_POPULATION — the prompt set has no awareness questions.
    #    First because it is a fact about the POPULATION: with nothing in it,
    #    "did it cite anything tiered" and "is there a rival" have no subject
    #    to be asked of. The same code and the same reason Mention Rate and
    #    Share of Voice already use, so a report says one thing about a set
    #    that cannot evidence discovery.
    # 2. NO_AUTHORITY_DATA — the awareness answers cite no domain with a tier.
    #    Second because it is a fact about the INPUT: there is nothing to
    #    measure with, which is exactly what the code meant in v2.1, so a scan
    #    with nothing classifiable behaves precisely as it did then. Not
    #    NOT_YET_MEASURED: that says a capability is on its way; this says an
    #    input is absent for this scan. Still not ALSO a degradation flag: a
    #    dimension that is left out is not "rougher".
    # 3. NO_COMPETITOR_SET — tiered sources were cited but nobody was looked
    #    for to compare against. Last because it is a fact about the
    #    COMPARISON, which only arises once there is something to compare.
    #    The figure is the subject against the best in the scan, and with no
    #    rivals "the best in the scan" is the subject itself, which would
    #    score 100 for any classifiable citation at all. That is the v1 Share
    #    of Voice defect — a full mark for a detection failure — and it gets
    #    the v1.1 answer: excluded and redistributed, under the same code
    #    Share of Voice uses for the same absence.
    #
    # Population, then input, then comparison: each later question presumes
    # the earlier one had an answer. NO_CITATIONS_IN_SCAN remains a fact about
    # the whole scan and travels as a flag whichever way the dimension goes.
    no_competitors = (
        not competitors
        or competitor_set_status is None
        or competitor_set_status is DetectionStatus.NO_SIGNAL
    )
    cs_raw, citation_flags = citation_strength(results, competitors, authority=authority)
    cs: Decimal | None
    if "NO_AWARENESS_POPULATION" in citation_flags:
        cs = None
        excluded[Dimension.CITATION_STRENGTH.value] = "NO_AWARENESS_POPULATION"
        flags.extend(citation_flags)
    elif "NO_AUTHORITY_DATA" in citation_flags:
        cs = None
        excluded[Dimension.CITATION_STRENGTH.value] = "NO_AUTHORITY_DATA"
        flags.extend(f for f in citation_flags if f != "NO_AUTHORITY_DATA")
    elif no_competitors:
        cs = None
        excluded[Dimension.CITATION_STRENGTH.value] = "NO_COMPETITOR_SET"
        flags.extend(citation_flags)
    else:
        cs = _round2(cs_raw)
        flags.extend(citation_flags)

    # --- Share of Voice ---------------------------------------------------
    #
    # A competitor set that is absent, NO_SIGNAL, or empty gives Share of Voice
    # no population. scoring-spec.md v1 said "Share of Voice = 100, flag the
    # scan"; that awards a quarter of the composite for a DETECTION FAILURE,
    # which is a technically-computable but meaningless number. It is excluded
    # and redistributed instead — the same treatment the spec already prescribes
    # for sentiment with no population. Recorded in the spec's changelog as v1.1.
    #
    # v2 scopes it to awareness results for the reason Mention Rate is scoped:
    # the tautology inflates the NUMERATOR specifically. A comparison prompt
    # names the subject and usually one rival, so the subject scores a
    # guaranteed hit while the other four competitors appear only if the engine
    # volunteers them — which pulls the ratio toward 1/(1+named) regardless of
    # real standing. Measured: Share of Voice falls awareness-only on 12 of 14
    # stored scans, by 5 to 29 points.
    #
    # The counter-argument, considered and rejected on the code as it is: a
    # buyer weighing named options IS a competitive signal. But this function
    # counts PRESENCE, not airtime — one hit per brand however much the answer
    # says — so a comparison prompt contributes the fact that two named parties
    # were named, which is a fact about the question. An airtime-weighted Share
    # of Voice would deserve this argument re-opened; presence-counting does not.
    #
    # `no_competitors` is computed above, where Citation Strength first needs it.
    sov: Decimal | None
    if no_competitors:
        sov = None
        excluded[Dimension.SHARE_OF_VOICE.value] = "NO_COMPETITOR_SET"
        flags.append("NO_COMPETITOR_SET")
    elif not aware:
        sov = None
        excluded[Dimension.SHARE_OF_VOICE.value] = "NO_AWARENESS_POPULATION"
    else:
        sov = _round2(share_of_voice(aware))
        if competitor_set_status is DetectionStatus.WEAK_SIGNAL:
            # Epic 3.5 measured SERP-only competitor precision at ~58%. A weakly
            # corroborated set is a weaker denominator, and the report must be
            # able to say so.
            flags.append("WEAK_COMPETITOR_SET")

    # --- Sentiment --------------------------------------------------------
    sentiment_raw = sentiment_score(results)
    sentiment_value: Decimal | None
    if sentiment_raw is None:
        sentiment_value = None
        excluded[Dimension.SENTIMENT.value] = "NO_POPULATION"
        flags.append("NO_SENTIMENT_POPULATION")
    else:
        sentiment_value = _round2(sentiment_raw)

    # --- Technical Foundation --------------------------------------------
    #
    # Excluded with a DISTINCT reason from the others. "NOT_YET_MEASURED" means
    # the capability does not exist yet (Epic 6); "NO_POPULATION" means this
    # brand genuinely has no data for a dimension we can measure. Epic 7's report
    # must word those differently — "we haven't checked" is not "there's nothing
    # to find" — and collapsing them into one flag would lose that.
    tf_value: Decimal | None
    if technical_foundation is None:
        tf_value = None
        excluded[Dimension.TECHNICAL_FOUNDATION.value] = "NOT_YET_MEASURED"
        flags.append("TECHNICAL_FOUNDATION_NOT_MEASURED")
    else:
        tf_value = _round2(_guard(Dimension.TECHNICAL_FOUNDATION, technical_foundation))

    values: dict[Dimension, Decimal | None] = {
        Dimension.MENTION_RATE: mr,
        Dimension.SHARE_OF_VOICE: sov,
        Dimension.CITATION_STRENGTH: cs,
        Dimension.SENTIMENT: sentiment_value,
        Dimension.TECHNICAL_FOUNDATION: tf_value,
    }

    included = [d for d in Dimension if values[d] is not None]
    if not included:
        return ScoreResult(
            status="insufficient_data", composite=None,
            sub_scores={
                d: SubScore(
                    d, None, included=False, weight=WEIGHTS[d],
                    reason=excluded.get(d.value),
                )
                for d in Dimension
            },
            weights=weights_recorded, excluded_dimensions=excluded,
            degradation_flags=sorted(set(flags)), reason_code="INSUFFICIENT_DATA",
            inputs_digest=digest,
        )

    # Redistribute proportionally across the surviving dimensions, so the
    # effective weights still sum to 100 and the composite stays on a 0-100 axis.
    #
    # ROUNDED ONCE, here, and then used for BOTH the composite and the stored
    # breakdown. Rule 2 requires a displayed breakdown to re-sum to its
    # displayed total; computing the composite from full-precision weights while
    # storing 2dp ones breaks that by a hair, and a client re-adding the numbers
    # in the report would not get the total back. Found by the live run, whose
    # raw output showed weights like 33.33333333333333333333333333%.
    included_weight = sum((WEIGHTS[d] for d in included), Decimal("0"))
    effective = {
        d: _round2(WEIGHTS[d] / included_weight * HUNDRED) for d in included
    }

    # Composite from the ROUNDED sub-scores, so a displayed breakdown always
    # re-sums to the displayed total (scoring-spec.md rule 2).
    composite = weighted_composite(effective, {d: values[d] for d in included})

    sub_scores = {
        d: SubScore(
            dimension=d, value=values[d], included=values[d] is not None,
            weight=effective.get(d, Decimal("0")), reason=excluded.get(d.value),
        )
        for d in Dimension
    }

    subject_position, subject_position_count = average_position(aware)
    return ScoreResult(
        status="scored",
        composite=_round2(composite),
        sub_scores=sub_scores,
        weights=weights_recorded,
        excluded_dimensions=excluded,
        degradation_flags=sorted(set(flags)),
        reason_code=None,
        inputs_digest=digest,
        average_position=subject_position,
        position_count=subject_position_count,
        competitors=compare_competitors(
            results,
            competitors,
            include_citation_strength=Dimension.CITATION_STRENGTH.value not in excluded,
            authority=authority,
        ),
    )

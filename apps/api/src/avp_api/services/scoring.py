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
IP-SAFETY (ip-safety.md #7)
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

logger = structlog.get_logger(__name__)

# Bumped from v1 when no-competitor Share of Voice changed from "score 100" to
# "exclude and redistribute" (scoring-spec.md changelog). The §6 weights are
# unchanged, but the composite a given EngineResult set produces is not — and
# rule 5 exists so that difference is attributable rather than silent. Two Score
# rows with different formula_version are not comparable; Epic 11's before/after
# reporting depends on being able to tell.
FORMULA_VERSION = "v1.1"

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
    citation_strength: Decimal


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
    """
    payload = {
        "formula_version": FORMULA_VERSION,
        "results": [
            {
                "id": r.result_id,
                "engine": r.engine,
                "status": r.status.value,
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
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Sub-scores. Each is a pure function over stored facts.
# --------------------------------------------------------------------------


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


def citation_strength(results: list[ResultFacts]) -> tuple[Decimal, list[str]]:
    """Distinct domains citing the subject, normalised against the scan's best.

    §6 asks for "number AND authority of domains". **There is no authority data
    in this system** — no Domain Authority feed, no backlink source, and nothing
    upstream produces one. scoring-spec.md anticipates this: fall back to raw
    domain count normalised against the competitor maximum in the same scan, and
    record the degradation.

    Normalising against the best-cited brand in the same scan rather than an
    absolute target keeps the number meaningful: "how close is the subject to
    the most-cited player here" is answerable from what we have, whereas "is 7
    citing domains good" is not.
    """
    flags: list[str] = ["NO_AUTHORITY_DATA"]
    answered = [r for r in results if r.answered]

    subject_domains: set[str] = set()
    domains_by_brand: dict[str, set[str]] = {}
    for r in answered:
        for domain, cites_subject in r.citations:
            if cites_subject:
                subject_domains.add(domain)
        # Attribute non-subject citations to the scan as a whole; per-competitor
        # attribution needs Citation.competitor_id, handled in the comparison.
        for name, is_subject in r.brands:
            if not is_subject:
                domains_by_brand.setdefault(name, set())

    if not subject_domains and not any(r.citations for r in answered):
        # No engine in this scan cited anything at all — typically because no
        # grounded engine ran. Distinct from "cited others but not you".
        flags.append("NO_CITATIONS_IN_SCAN")
        return Decimal("0"), flags

    competitor_domain_counts = [
        len({d for r in answered for d, cites_subject in r.citations if not cites_subject})
    ]
    best = max([len(subject_domains), *competitor_domain_counts]) or 1
    raw = Decimal(len(subject_domains)) / Decimal(best) * HUNDRED
    return _guard(Dimension.CITATION_STRENGTH, raw), flags


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
    results: list[ResultFacts], competitors: list[CompetitorFacts]
) -> list[CompetitorComparison]:
    """Per-competitor figures for the three measurable dimensions.

    No composite — see CompetitorComparison's docstring.
    """
    answered = [r for r in results if r.answered]
    if not answered or not competitors:
        return []

    total_mentions = sum(len(r.brands) for r in answered)
    all_citation_domains = {
        d for r in answered for d, cites_subject in r.citations if not cites_subject
    }
    best_citations = max(
        len({d for r in answered for d, cs in r.citations if cs}),
        len(all_citation_domains),
        1,
    )

    out: list[CompetitorComparison] = []
    for competitor in sorted(competitors, key=lambda c: c.competitor_id):
        appearances = sum(
            1 for r in answered for name, is_subject in r.brands
            if not is_subject and name == competitor.name
        )
        answered_with = sum(
            1 for r in answered
            if any(name == competitor.name and not is_subject for name, is_subject in r.brands)
        )
        cited = len({
            d for r in answered for d, cs in r.citations
            if not cs and competitor.domain and d == competitor.domain
        })
        out.append(
            CompetitorComparison(
                competitor_id=competitor.competitor_id,
                name=competitor.name,
                mention_rate=_round2(
                    Decimal(answered_with) / Decimal(len(answered)) * HUNDRED
                ),
                share_of_voice=_round2(
                    Decimal(appearances) / Decimal(total_mentions) * HUNDRED
                    if total_mentions else Decimal("0")
                ),
                citation_strength=_round2(
                    Decimal(cited) / Decimal(best_citations) * HUNDRED
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
) -> ScoreResult:
    """Compute the composite and its breakdown.

    `technical_foundation` is a parameter rather than a computation because
    TechnicalAudit is Epic 6. Passing None excludes the dimension and
    redistributes its weight — see the exclusion logic below.
    """
    digest = compute_inputs_digest(results, competitors, technical_foundation)
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

    mr = _round2(mention_rate(results))
    cs_raw, citation_flags = citation_strength(results)
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
    no_competitors = (
        not competitors
        or competitor_set_status is None
        or competitor_set_status is DetectionStatus.NO_SIGNAL
    )
    sov: Decimal | None
    if no_competitors:
        sov = None
        excluded[Dimension.SHARE_OF_VOICE.value] = "NO_COMPETITOR_SET"
        flags.append("NO_COMPETITOR_SET")
    else:
        sov = _round2(share_of_voice(results))
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

    return ScoreResult(
        status="scored",
        composite=_round2(composite),
        sub_scores=sub_scores,
        weights=weights_recorded,
        excluded_dimensions=excluded,
        degradation_flags=sorted(set(flags)),
        reason_code=None,
        inputs_digest=digest,
        competitors=compare_competitors(results, competitors),
    )

"""Where the engines disagree — Epic 9.23, Layer 3.

    north-star.md §3.8: Layer 3 (Insight) is the differentiation bet.

WHAT THIS EXISTS TO SAY
-----------------------
A scan already runs every prompt against three engines across two vendors, and
stores a mention, a position and a sentiment for each. Until this module nothing
read them COMPARATIVELY: the report gave one mention rate, one sentiment
sub-score, and per-engine counts with nothing said about the difference between
them. A brand can be named by Claude on a question ChatGPT answers without it,
and that gap was in the database and on no page.

**`north-star.md` called this impossible and blocked by Layer 2.** It was
neither, and the correction is recorded in the build log: the cross-vendor axis
had been in the data since the ChatGPT adapter shipped.

TWO RULES, AND THE FIRST IS THE ONE THAT MAKES THIS HONEST
-----------------------------------------------------------
**Only engines that ANSWERED have an opinion.** A rate-limited, timed-out or
truncated call is missing data, not evidence of absence, and counting it as
"this engine did not mention you" would turn an outage into a divergence
finding — the exact failure `EngineResultStatus.ANSWERED_NO_MENTION` and
`ScanStatus.PARTIAL` exist to prevent. Every function here filters on
`ResultFacts.answered` before comparing anything, and a prompt where fewer than
two engines answered is not a disagreement, it is a gap in coverage.

**The numbers are not recomputed here.** Per-engine mention rate and sentiment
call `scoring.mention_rate` and `scoring.sentiment_score` on a filtered slice of
the same facts the composite is built from. A second implementation would
eventually disagree with the headline, and the disagreement would reach a
client — the same reasoning `fix_generator` gives for not re-deriving Epic 7's
gap formula in Python.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .scoring import ResultFacts, mention_rate, sentiment_score


@dataclass(frozen=True, slots=True)
class EngineStanding:
    """How one engine sees the subject, on the axes the composite already uses.

    `sentiment` is None when this engine never named the subject — no mention
    means no sentiment, the same exclusion `sentiment_score` makes for the
    scan as a whole rather than scoring the absence zero twice.
    """

    engine: str
    answered: int
    mentioned: int
    mention_rate: Decimal
    sentiment: Decimal | None


@dataclass(frozen=True, slots=True)
class SplitPrompt:
    """One buyer question the engines answered differently.

    Both lists hold engines that ANSWERED. An engine that failed on this prompt
    appears in neither, because it did not have an opinion to differ with.
    """

    prompt_id: str
    named_by: tuple[str, ...]
    missed_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Divergence:
    """The cross-engine reading of one scan.

    `comparable_prompts` is the denominator every claim here is honest about:
    prompts where at least two engines answered. A scan where one engine was
    down all run produces standings but no splits, and says so through this
    number rather than by reporting perfect agreement.
    """

    standings: tuple[EngineStanding, ...]
    splits: tuple[SplitPrompt, ...]
    comparable_prompts: int

    @property
    def agreement_rate(self) -> Decimal | None:
        """% of comparable prompts every answering engine agreed on.

        None when nothing was comparable — which is not 100%. "Every engine
        agreed" and "there was nothing to compare" are different claims, and
        collapsing them would report a single-engine scan as perfect consensus.
        """
        if self.comparable_prompts == 0:
            return None
        agreed = self.comparable_prompts - len(self.splits)
        return (
            Decimal(agreed) / Decimal(self.comparable_prompts) * Decimal("100")
        ).quantize(Decimal("0.01"))


def engine_standings(results: list[ResultFacts]) -> tuple[EngineStanding, ...]:
    """Each engine's own mention rate and sentiment, ordered by engine name.

    Ordered deterministically rather than by rank: a report whose engine list
    reshuffles between two scans of the same client reads as a change when
    nothing changed. Ranking is the reader's to do from the numbers.
    """
    by_engine: dict[str, list[ResultFacts]] = defaultdict(list)
    for fact in results:
        by_engine[fact.engine].append(fact)

    standings = []
    for engine in sorted(by_engine):
        slice_ = by_engine[engine]
        answered = [r for r in slice_ if r.answered]
        standings.append(
            EngineStanding(
                engine=engine,
                answered=len(answered),
                mentioned=sum(1 for r in answered if r.mentioned),
                # The SAME functions the composite is built from, on a slice.
                mention_rate=mention_rate(slice_),
                sentiment=sentiment_score(slice_),
            )
        )
    return tuple(standings)


def split_prompts(results: list[ResultFacts]) -> tuple[tuple[SplitPrompt, ...], int]:
    """Prompts the answering engines disagreed about, and how many were comparable.

    A split is a prompt where at least one answering engine named the subject
    and at least one did not. That is the finding this whole module is for: a
    specific buyer question where a brand is visible on one assistant and
    invisible on another, which no single-engine product can see and no
    aggregate mention rate reveals.

    A prompt with fewer than two answering engines is not comparable and is
    counted in neither number — one opinion cannot disagree with itself.
    """
    by_prompt: dict[str, list[ResultFacts]] = defaultdict(list)
    for fact in results:
        if fact.answered and fact.prompt_id:
            by_prompt[fact.prompt_id].append(fact)

    splits: list[SplitPrompt] = []
    comparable = 0
    for prompt_id in sorted(by_prompt):
        answering = by_prompt[prompt_id]
        if len(answering) < 2:
            continue
        comparable += 1
        named = tuple(sorted(r.engine for r in answering if r.mentioned))
        missed = tuple(sorted(r.engine for r in answering if not r.mentioned))
        if named and missed:
            splits.append(
                SplitPrompt(prompt_id=prompt_id, named_by=named, missed_by=missed)
            )
    return tuple(splits), comparable


def analyse(results: list[ResultFacts]) -> Divergence:
    """The cross-engine reading, from the facts scoring already loads."""
    splits, comparable = split_prompts(results)
    return Divergence(
        standings=engine_standings(results),
        splits=splits,
        comparable_prompts=comparable,
    )

"""What a FAILED scan never shows — checklist 3.24, extended by 3.30 and 3.37.

A scan that ends FAILED can still carry what its chain wrote before it failed: a
Score row, an audit whose Technical Foundation has a value, fix items worded from
the score. The founder's rule is that a FAILED scan's score is never shown or
counted, and neither is the sub-score, nor a fix the generator wrote. None of those
rows is changed or deleted (scoring-spec.md rule 5): each is read as it always was
and withheld on the way out. "Withheld" means not computed and not shown; the last
section says what it does not mean.

One place says which scans that is and what is withheld, so the report
(`services/report.py`), the fix list (`routers/action_items.py`), the audit routes
(`routers/audits.py`), the score routes (`routers/scores.py`) and alert generation
(`services/alerts.py`) cannot drift apart. The alert feed's own filter
(`routers/alerts.py`) is the same rule written as SQL, since it filters rows in the
query; a test holds it to this function for every scan status. The dashboard's two SQL
filters (`routers/dashboard.py`), the client history's plottable statuses
(`services/client_history.py`) and the share route's refusal (`routers/report.py`) apply
the 3.24 rule on their own and do not call this.

THE FIX GENERATOR
-----------------
The generator's one prompt carries the scan's composite and every dimension score
(`fix_generator.build_fix_prompt`), and it is told to quote this scan's own numbers
in each fix's `detail`, so what it writes can say "share of voice at 30 out of 100".
For a scan that is still running its input includes the score (the founder's
decision). If the scan then fails, nothing it wrote is served, on any route: not the
points (`points_upside` is arithmetic over the score) and not the wording, which can
quote it. Blanking one field would leave the rest, so the rule is the whole list:
`served_fixes` returns none for a FAILED scan. The rows are kept. The report's fix
beat then falls back to its own fixed wording, as it does for a scan with no fixes
generated.

THE PER-ENGINE STANDINGS (checklist 3.37)
-----------------------------------------
The report's cross-engine reading (`proof.crossEngine`) carries one standing per
engine: its mention rate and its sentiment, computed by `scoring.mention_rate` and
`scoring.sentiment_score` on that engine's slice of the answers. The sentiment one is
the Sentiment dimension's own function over the Sentiment dimension's own population
(every answered result), so the standings' sentiments, weighted by how many of each
engine's answers named the subject, add up to that dimension's sub-score: to within a
rounding when every such answer carries a label, and closely when one does not. Left in
a FAILED scan's report they would hand back the number this module exists to withhold,
so `withhold_score_like_proof` empties them. Everything else in the proof beat stays: it
is a fact about what was measured (a count, a boolean, a domain, a position as
observed), and none of it is computed by a scoring function.

WHAT "WITHHELD" MEANS, AND WHAT THIS DOES NOT COVER
---------------------------------------------------
This module withholds the score, the Technical Foundation sub-score and the component
scores that add up to it (on the audit route), the generated fixes, and the report's
per-engine standings.

Withheld means not computed and not shown. It does not mean that the figure cannot be
derived from what the scan stored: nothing here changes a stored row, or what the scan's
own routes serve. For a FAILED scan the signed-in routes still return, as stored:

* the inputs `scoring.compute_score` reads for Mention Rate, Share of Voice and
  Sentiment. `GET /scans/{id}` (`ScanDetailOut.prompt_set` and `.results`),
  `GET /scans/{id}/results` and `GET /scans/{id}/prompts` (`routers/scans.py`) serve each
  prompt's intent and each answer's status, mention flag, sentiment label, brand
  mentions and citations. Citation Strength reads the same citations and also the
  platform's authority tiers, which no route serves;
* every fact Technical Foundation is computed from. `GET /scans/{id}/audit`
  (`routers/audits.py`) serves the indexable, sitemap and canonical flags, the schema
  types and flags and the content age, with `componentWeights`.

Those routes apply none of this module. Whether they should is the founder's decision
(raised in the 3.37 review) and is not made here. Until it is, a FAILED scan has no score
computed or shown for it, and its stored inputs are not hidden.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models.scan import Scan, ScanStatus
from ..schemas.audit import TechnicalAuditOut
from ..schemas.report import ReportAuditOut, ReportProofOut


def score_is_withheld(scan: Scan) -> bool:
    """True when this scan's score, and what was written from it, may not be shown."""
    return scan.status is ScanStatus.FAILED


def served_fixes[T](scan: Scan, items: Iterable[T]) -> list[T]:
    """The fixes that may be shown for this scan: none, when its score is withheld.

    The fix generator was handed the score, and its wording can quote it, so for a
    FAILED scan nothing it wrote is served: no item, and so no points and no copy.
    Every route and the report read the fix list through this. The stored rows are
    not touched.
    """
    return [] if score_is_withheld(scan) else list(items)


def withhold_technical_foundation(audit: TechnicalAuditOut | ReportAuditOut) -> None:
    """Blank the Technical Foundation sub-score, and the components that add up to it.

    The sub-score is a score input, so a FAILED scan has none to show. The audit route
    also carries the four component scores whose weighted sum, with `component_weights`,
    IS that sub-score: left in, they would hand it back one sum away. They are emptied,
    as the report empties `dimensions` for a failed scan. `component_weights` stays. It
    is this audit's own weighting, the nominal weights with an unmeasured component's
    share redistributed (`technical_audit.effective_weights(components)`), which says how
    the audit was measured and is not a score. So does every fact about the site: the
    checks, the booleans, the counts, the dates, the schema types. The report's audit
    carries no components today, so for it this blanks the sub-score alone.

    The components are emptied on any audit that carries them, and not only on
    `TechnicalAuditOut`: a class that gains a `components` field (the report's audit, or a
    subclass of either) is covered without a change here.
    """
    audit.technical_foundation = None
    if hasattr(audit, "components"):
        audit.components = {}


def withhold_score_like_proof(proof: ReportProofOut) -> None:
    """Empty the proof beat's per-engine standings, the part of it built like a score.

    A standing is one engine's mention rate and sentiment, from the same two functions
    the Mention Rate and Sentiment dimensions use (`services/divergence.py`). The
    sentiment ones add up to the withheld Sentiment sub-score (see the module
    docstring), so a FAILED scan's report has no standings. The list is emptied, not
    blanked entry by entry: that is the shape the web already draws as "no reading"
    (`ReportView.tsx` draws no cross-engine section for an empty list, as for a scan
    with no answers), and no schema field has to become nullable.

    What stays is every fact the proof beat carries, and the rest of the cross-engine
    reading with it: `splits` and `comparablePrompts` record which engines named the
    subject on which questions, and `agreementRate` is only the share of comparable
    questions that were not splits. The per-engine counts of answers and of answers
    naming the subject are still in `engineCoverage`, the same numbers a standing carried.
    """
    proof.cross_engine.standings = []

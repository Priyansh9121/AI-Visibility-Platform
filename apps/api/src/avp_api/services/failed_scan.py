"""What a FAILED scan never shows — checklist 3.24, extended by 3.30.

A scan that ends FAILED can still carry what its chain wrote before it failed: a
Score row, an audit whose Technical Foundation has a value, fix items worded from
the score. The founder's rule is that a FAILED scan's score is never shown or
counted, and neither is the sub-score, nor a fix the generator wrote. None of those
rows is changed or deleted (scoring-spec.md rule 5): each is read as it always was
and withheld on the way out.

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

WHAT THIS DOES NOT COVER
------------------------
It withholds the score, the Technical Foundation sub-score and the component scores
that add up to it (on the audit route), and the generated fixes. It does not withhold the
report's per-engine standings (`proof.crossEngine.standings[]`), which carry each
engine's mention rate and sentiment, computed by the same functions as the Mention
Rate and Sentiment dimensions over one engine's answers. A FAILED scan's report still
carries them.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models.scan import Scan, ScanStatus
from ..schemas.audit import TechnicalAuditOut
from ..schemas.report import ReportAuditOut


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
    carries no components, so for it this blanks the sub-score alone.
    """
    audit.technical_foundation = None
    if isinstance(audit, TechnicalAuditOut):
        audit.components = {}

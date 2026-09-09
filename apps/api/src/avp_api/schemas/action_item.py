"""Action item schemas — Epic 8.

`title` and `detail` carry free text, and unlike every other response schema in
this API that is correct here: these are OUR OWN generated recommendations, not
scraped material (the facts-only rule, and the same justification recorded on
models/action_item.py). Nothing on this model is derived from a competitor's
page, an engine's answer, or a cited publisher's copy — services/fix_generator
cannot see any of those, which is what the Epic 8 prompt guards enforce.

Declared here rather than in schemas/report.py deliberately. That module is
swept field-by-field by test_facts_only.py's `test_report_projection_exposes_no_
third_party_prose`, whose forbidden set includes `title` — correctly, because
every other thing a report renders is somebody else's. The sweep skips classes
declared elsewhere and re-exported, which is the sanctioned way to hold an
exception without weakening the rule for everything around it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from ..models.action_item import ActionItemSource, ActionItemStatus, Effort, Priority
from .common import ApiModel


class ActionItemOut(ApiModel):
    """One generated fix — §5.3's prioritised action list."""

    id: str
    scan_id: str

    # Which Epic 7 candidate this enriches. `source` + `sourceKey` reproduce the
    # key the report client mints for the same candidate (`gap:citation_strength`,
    # `audit:schema_faq`), which is how generated copy merges onto the
    # deterministic list instead of replacing it wholesale.
    source: ActionItemSource
    source_key: str

    title: str
    detail: str | None = None

    priority: Priority
    effort: Effort
    status: ActionItemStatus

    # One of score.DIMENSION_KEYS. Present for every fix: an audit fix targets
    # Technical Foundation even though its individual contribution is not
    # separately measured.
    dimension_key: str | None = None
    # Recoverable composite points, from the ledger gap arithmetic. Null for an
    # audit fix, where this system does not measure how much a single check
    # moves the dimension — an invented number would be worse than none.
    points_upside: Decimal | None = None

    rank: int
    # The model that authored the copy above, so a change in wording quality is
    # attributable. Null for a row not written by a model.
    generated_by: str | None = None

    created_at: datetime
    updated_at: datetime


class ActionItemListOut(ApiModel):
    """The scan's fix list, plus what happened the last time it was generated."""

    scan_id: str
    # "generated" | "empty" | "failed". Distinct from an empty list: a scan with
    # nothing to fix and a scan whose generation failed are different things to
    # tell a viewer.
    status: str
    reason_code: str | None = None
    generated_by: str | None = None
    items: list[ActionItemOut] = Field(default_factory=list)

"""Answer gaps — where a rival is named and this client is not. Epic B.

WHAT A "GAP" IS HERE, AND THE THREE STATES THAT MUST NOT BE FOLDED TOGETHER
---------------------------------------------------------------------------
A prompt produced an answer from each engine. For this client, that answer is
in exactly one of three states, and they are different findings with different
fixes:

  * **named**      - the client appeared. No gap.
  * **absent**     - the client did not appear, but a rival did. THE gap: the
                     question has a commercial answer and somebody else is it.
  * **no brands**  - no brand appeared at all, from anyone. NOT a gap. The
                     question did not produce a brand answer, so there was
                     nothing to be absent from.

Folding `no brands` into `absent` would report a prompt nobody could have won
as a loss, and on the real `avp_dev` rows that is not a rounding error: one
scan carries 37 no-brand answers against 20 genuine absences. The same
discipline Epic A applied to `unclassified` sentiment, for the same reason.

WHY RECURRENCE IS COUNTED ACROSS ENGINES AND NOT ACROSS SCANS
-------------------------------------------------------------
The brief asked for gaps "sortable by how often the gap recurs", which assumed
prompts persist between scans. They do not. `build_prompt_set` calls
`generate_prompts` fresh for every scan, so prompt text is newly written each
time: across Plausible's three scans, 72 prompts carry 71 distinct texts, and
across Notion's two, 48 texts are 48 distinct. There is no prompt identity to
recur ON.

What does recur is the ENGINE. Every prompt is asked of every engine in the
scan, so a gap holding on 3 of 3 engines is a categorically stronger finding
than one holding on 1 of 3 — that is a real recurrence axis, it is measured
here as `absent_on` / `engines_answered`, and it is the default sort.

The other axis that survives between scans is the RIVAL, because competitors
are persistent rows. `AnswerGapRivalOut` counts those across the client's whole
history, which is the cross-scan half of the same question.

FACTS-ONLY RULE
--------------------------------
Every field below is a count, a boolean, an ordinal, an entity NAME or OUR OWN
prompt text. No engine answer text is read or returned — there is none stored
to read. `prompt.text` is ours: Epic 4 generated it.
"""

from __future__ import annotations

import enum
from datetime import datetime

from .common import ApiModel


class GapKind(str, enum.Enum):
    """One prompt's verdict for this client. Ordered by how bad it is.

    Precedence when several could apply is the declaration order below, worst
    first — a prompt that is both absent AND uncited is reported as `absent`,
    because being named at all comes before being cited.
    """

    # Every engine that answered named a rival and none named this client.
    ABSENT = "absent"
    # Some engines named this client and some did not. A gap with a foothold.
    PARTIAL = "partial"
    # Named by every engine that answered, but this client's own domain was
    # never cited for it — while the scan proves elsewhere that the domain IS
    # citable. Content exists; it is not the content answering this question.
    UNCITED = "uncited"
    # Named by every engine that answered.
    COVERED = "covered"
    # No brand at all was named, by anyone. Not a gap — see the module note.
    NO_BRANDS = "no_brands"
    # No engine produced a usable answer for this prompt.
    UNANSWERED = "unanswered"


class AnswerGapBrandOut(ApiModel):
    """One column of the grid: a brand, and how much of this scan it won."""

    name: str
    domain: str | None = None
    is_subject: bool
    # Answers (prompt x engine) that named this brand.
    answers_named: int
    # Prompts where at least one engine named it. The grid's column total.
    prompts_named: int


class AnswerGapCellOut(ApiModel):
    """One cell: how many of this prompt's engines named this brand.

    `named_on` is a count and not a boolean because the engines disagree, and
    that disagreement is the finding — a rival named by one engine of three is
    a different competitive position from one named by all three.
    """

    brand: str
    named_on: int


class AnswerGapRowOut(ApiModel):
    """One prompt, across every engine that answered it."""

    prompt_id: str
    # OURS — Epic 4 generated it. See the module note on the facts-only rule.
    text: str
    intent: str
    position: int

    engines_answered: int
    subject_named_on: int
    # Engines that named at least one rival. Distinguishes "a rival won this"
    # from "nobody was named", which `brands_named_on == 0` alone cannot.
    rivals_named_on: int
    # Engines that answered without naming ANY brand.
    no_brand_on: int

    # Was this client's own domain cited for this prompt, by any engine?
    subject_cited: bool

    kind: GapKind
    # Engines on which this client was absent. The recurrence measure, and the
    # default sort. Zero for a covered prompt.
    absent_on: int

    cells: list[AnswerGapCellOut]


class AnswerGapRivalOut(ApiModel):
    """A rival, counted across the client's WHOLE history.

    The cross-scan half of "how often does this recur". Competitors persist
    between scans where prompts do not, so this is the only axis on which a
    recurring gap can honestly be counted over time.
    """

    name: str
    domain: str | None = None
    # Answers where this rival was named and the client was not.
    answers_won: int
    # Distinct scans in which that happened at least once.
    scans_present: int


class AnswerGapsOut(ApiModel):
    """The Answer gaps read for one scan, plus the cross-scan rival rollup."""

    client_id: str
    name: str
    domain: str

    scan_id: str
    scanned_at: datetime
    # Every scan of this client that carries a grid, newest first, so the screen
    # can offer a picker without a second request.
    available_scan_ids: list[str]

    engines: list[str]
    brands: list[AnswerGapBrandOut]
    rows: list[AnswerGapRowOut]
    rivals: list[AnswerGapRivalOut]

    # --- totals, so the screen never has to re-derive them from `rows` ---
    prompts: int
    absent: int
    partial: int
    uncited: int
    covered: int
    no_brands: int
    unanswered: int
    # False when no answer in the scan cited this client's domain. When that is
    # so, `uncited` is NOT reported on any row: with no evidence the domain is
    # citable at all, "you have content but it wasn't cited" is a claim this
    # data cannot support. See `services/answer_gaps.py`.
    subject_citable: bool

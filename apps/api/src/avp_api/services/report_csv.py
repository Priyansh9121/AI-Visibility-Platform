"""The report's figures as one CSV file — F3, 2026-10-06 (`REPORT_CSV_ENABLED`, default off).

THE FILE
--------
One header row and six columns, `Section, Engine, Prompt, Item, Measure, Value`, and every row
below it is ONE fact. A long table, so the sections share the one header and the file opens in a
spreadsheet as it is, and a pivot over `Item` and `Measure` needs no clean-up. Every label is plain
words, and a figure carries its unit in its measure (`Prompts asked (count)`, `Sentiment (out of
100)`). Six sections, in this order:

- `Scan`: whose scan it is (the client's name, in `Item`), `Scan date` (ISO, UTC), `Scan status`
  (`Complete`, or the PDF cover's own sentence for a partial scan) and `Overall score (out of 100)`
  (the report's headline figure, or `Not scored`). A scan that ran out of time shrinks both of its
  counts, so only this section says that the figures behind it are partial.
- `Dimension scores`: each dimension's `Score (out of 100)`, or why it was `Left out of the score
  because`.
- `Share of voice`: the client and each competitor, as the report's "Rivals in the same answers"
  table lists them.
- `Answers`: per engine, what it returned for the whole scan (`Prompts asked (count)`, `Prompts
  listed below (count)`, `Answers received (count)`, `Answers naming the client (count)` and its
  `Sentiment (out of 100)` when it named the client at all); and per engine and prompt, whether it
  `Answered`, whether it `Named the client` and `Position when named`.
- `Cited domains`: each domain the answers cited, with its `Citations (count)` and who it is
  `Attributed to` (the client's name, a competitor's name or `Third party`), as the report's two
  citation tables list them.
- `Fixes`: each fix on the report's list, with its `Priority`, `Effort (S, M or L)` and `Points the
  score gains`.

THE CAPS ARE THE REPORT'S, AND THE FILE SAYS SO
-----------------------------------------------
The report lists the first 20 prompts of a scan (its Answer Shelf) and up to 12 domains in each of
its two citation tables. The file is built from that same projection, so it has the same caps. The
first is visible in the file: `Prompts listed below (count)` sits beside `Prompts asked (count)`
for each engine. The second is said by the button that offers the file.

BUILT FROM THE REPORT, AND NOTHING ELSE
---------------------------------------
`render_report_csv` takes a `ReportOut`, the projection `GET /scans/{id}/report` serves, and no
session, no model and no other query. It cannot carry anything the report does not, and it cannot
become a second assembly path that disagrees with the page it was downloaded from; that is the
argument `render_report_pdf` makes, and for the same reason. What the file leaves out, because
the brief did not name it: the weights, a rival's mention rate, citation strength and average
position, a citation's page address, the audit's checks and a fix's detail.

THE FIX LIST IS THE REPORT'S, NOT THE PROJECTION'S
--------------------------------------------------
The projection's `action_items` are only the fixes a model reworded. The report page derives
which fixes exist, their order and their points itself (`derive.ts`), and merges the reworded ones
on by key; the PDF does the same in Python (`report_narrative`). This takes that Python derivation
as it is, so a scan whose fixes were never generated still has the list its page shows, and the
points are the page's. `test_report_csv.py` holds it to what the TypeScript produced
(`expected-narrative.json`).

NOTHING INVENTED, NOTHING ZERO
------------------------------
A figure that does not exist is not a row: no position for a mention the engine gave no ordinal
for, no sentiment for an engine that never named the client, no points for an audit fix, no score
for an excluded dimension (a row naming the reason instead). A figure the report shows as a dash
is a row with a blank value, never a zero: a rival the scan could not compare is blank. An answer
that never came is `Answered = no` and nothing else, never an absence. A scan that was never
scored, or had too little data to be, says `Not scored` and never a zero.

Numbers are written as the report serves them (a decimal as stored, two places, never an
exponent), and a fix's points as the page shows them, one place. A decimal the web page rounds to
one place (its tables) is the more exact figure here, which is the PDF's too; the button says so.

EXCEL SAFETY
------------
- A UTF-8 byte-order mark at the start, so a spreadsheet reads the text as UTF-8 and not as the
  machine's code page.
- `guard_cell` puts a leading apostrophe on EVERY cell that begins with `=`, `+`, `-`, `@`, a tab,
  a carriage return or the full-width `＝＋－＠`, with or without white space in front of it, and
  on a formula that starts after a `;`, a tab, a carriage return or a line feed INSIDE a cell: a
  spreadsheet that splits on `;` or a tab starts a new cell there, and one in a `;` locale ends the
  record at a line break even inside quotes (it reads the quotes `csv` writes only at the start of
  a field), so what follows starts a record of its own. `csv` quotes a cell for a comma, a quote or
  a line break, and for neither a `;` nor a tab. The header and our own words go through it too, so
  a client's name, a competitor's, a prompt, a fix's title and a name a model wrote all arrive as
  text and never as a formula. Nothing this file writes is a negative number, so no figure is
  changed by it (`test_report_csv.py` pins that).
- Python's `csv` module does the quoting, with the standard line ending (`\\r\\n`).
- The file's name is built by the route (`_csv_filename`): the client, reduced to the characters
  a header can carry as the PDF's name is, then fixed words and `scan_date`. Nothing else an
  operator typed is in any header.

FACTS ONLY
----------
Every cell is a name (the client's, a competitor's), a domain, a count, an ordinal, a yes or a no,
a number, a date, one of our own words, our own generated prompt, or the title of one of our own
fixes. The prompt and the title are the two places this reads a field that carries prose, and
both are ours: `test_facts_only.py` holds the module to exactly those two.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC
from decimal import Decimal
from typing import Final

from ..models.engine_result import Engine
from ..models.scan import ScanStatus
from ..models.score import ScoreStatus
from ..schemas.report import ReportOut
from . import report_narrative as narrative

# The PDF cover's own sentence for a scan that is partial (and for one that failed, which is
# never exported). Taken as it is, so the file and the PDF cannot say it two ways.
from .report_pdf import _OUTCOME_LINE

CSV_COLUMNS: Final[tuple[str, ...]] = ("Section", "Engine", "Prompt", "Item", "Measure", "Value")

SCAN: Final = "Scan"
DIMENSION_SCORES: Final = "Dimension scores"
SHARE_OF_VOICE: Final = "Share of voice"
ANSWERS: Final = "Answers"
CITED_DOMAINS: Final = "Cited domains"
FIXES: Final = "Fixes"
SECTIONS: Final[tuple[str, ...]] = (
    SCAN,
    DIMENSION_SCORES,
    SHARE_OF_VOICE,
    ANSWERS,
    CITED_DOMAINS,
    FIXES,
)

# The byte-order mark a spreadsheet reads as "this is UTF-8".
BOM: Final = "\N{ZERO WIDTH NO-BREAK SPACE}"

# What a spreadsheet reads as the start of a formula: OWASP's list (`=`, `+`, `-`, `@`, a tab and
# a carriage return), and the full-width forms of the four that are visible (U+FF1D, U+FF0B,
# U+FF0D, U+FF20), which a spreadsheet in a Japanese or Chinese locale can read the same way. A
# tuple on purpose: `"" in "=+-@"` is True.
FORMULA_STARTS: Final[tuple[str, ...]] = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")

# The visible ones, for the inside of a cell: a formula that starts straight after a `;`, a tab, a
# carriage return or a line feed, with white space between or not. A spreadsheet that splits on `;`
# or a tab makes a new cell there, and `csv` quotes neither, so a name like `Acme;=1+1` would
# otherwise land in the file unguarded (it is not the first character of the cell, and it is the
# last column, so unquoted). A line break is the other kind: `csv` quotes the cell, and a reader in
# a `;` locale ends the record at the break all the same (it reads a quote only at the start of a
# field), so `Acme` and `=1+1;` on two lines is a record of its own that begins with a formula. The
# apostrophe goes after EACH of them, so `x;` then a line break then `=1` is `x;'`, the break and
# `'=1`: the line that follows the break begins with `'`.
_VISIBLE_STARTS: Final = "".join(c for c in FORMULA_STARTS if not c.isspace())
_AFTER_SEPARATOR: Final = re.compile(rf"(?<=[;\t\r\n])(?=\s*[{re.escape(_VISIBLE_STARTS)}])")

# The statuses a file is made for: finished, with figures to give. An allow-list, not "everything
# but failed, queued and running": a status the product adds later is refused until somebody says
# it should be exported. FAILED has no score to show (`failed_scan.score_is_withheld`, as for
# sharing), and QUEUED and RUNNING have no final figures. CANCELLED, which nothing writes today, is
# refused with them.
EXPORT_STATUSES: Final[frozenset[ScanStatus]] = frozenset(
    {ScanStatus.SUCCEEDED, ScanStatus.PARTIAL}
)

# One plain sentence for the person at the screen, per status the brief names. It names no code, no
# figure and nothing about the client, so a refusal says nothing a stranger could use.
_REFUSALS: Final[dict[ScanStatus, str]] = {
    ScanStatus.FAILED: "This scan failed, so its figures cannot be exported.",
    ScanStatus.QUEUED: (
        "This scan has not started yet, so there are no figures to export. "
        "Try again when it has finished."
    ),
    ScanStatus.RUNNING: (
        "This scan is still running, so its figures are not final. Try again when it has finished."
    ),
}
_REFUSAL_OTHERWISE: Final = "This scan did not finish, so its figures cannot be exported."

Row = tuple[str, str, str, str, str, str]


def is_available(enabled: bool, status: ScanStatus) -> bool:
    """Whether the report may offer the file for a scan: the setting is on and the status allows it.

    One rule for two readers: the route's gate and the report's `csvExportAvailable`, so the report
    never offers a link the route would refuse.
    """
    return enabled and status in EXPORT_STATUSES


def refusal_detail(status: ScanStatus) -> str | None:
    """The sentence for a scan that cannot be exported, or None for one that can."""
    if status in EXPORT_STATUSES:
        return None
    return _REFUSALS.get(status, _REFUSAL_OTHERWISE)


def guard_cell(value: str) -> str:
    """A cell that would start a formula, as text: apostrophes in front, nothing else changed.

    Two checks, both on every cell. The cell is judged by its first character that is not white
    space, so a tab, a space, a line feed or a no-break space in front of `=` does not hide it, and
    the guard does not lean on `ReportOut` having stripped the string already. And a formula that
    starts after a `;`, a tab, a carriage return or a line feed inside the cell gets an apostrophe
    straight after that character, so what a spreadsheet reads as a new cell or a new record
    begins with `'` and not with the formula. Only apostrophes are added, and a cell that is
    already safe is returned as it is, so the guard is idempotent.
    """
    if value[:1] in FORMULA_STARTS or value.lstrip()[:1] in FORMULA_STARTS:
        value = f"'{value}"
    return _AFTER_SEPARATOR.sub("'", value)


def scan_date(report: ReportOut) -> str:
    """The scan's date in UTC, ISO (`2026-08-21`); the day the report was made if it has none.

    The same date the PDF's cover prints, and the one in the file's name.
    """
    moment = report.scanned_at or report.generated_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date().isoformat()


def render_report_csv(report: ReportOut) -> bytes:
    """The whole file: the mark, the header and every row, each cell guarded, quoted by `csv`."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in (CSV_COLUMNS, *report_rows(report)):
        writer.writerow([guard_cell(cell) for cell in row])
    return f"{BOM}{buffer.getvalue()}".encode()


def report_rows(report: ReportOut) -> list[Row]:
    """Every row of every section, in the fixed order, before the guard and the quoting."""
    client = report.subject.brand_name or report.subject.name
    return [
        *_scan(report, client),
        *_dimension_scores(report),
        *_share_of_voice(report, client),
        *_answers(report, client),
        *_cited_domains(report, client),
        *_fixes(report),
    ]


# --------------------------------------------------------------------------
# the sections
# --------------------------------------------------------------------------


def _scan(report: ReportOut, client: str) -> list[Row]:
    return [
        (SCAN, "", "", client, "Scan date", scan_date(report)),
        (SCAN, "", "", client, "Scan status", _scan_status(report.scan_status)),
        (SCAN, "", "", client, "Overall score (out of 100)", _overall_score(report)),
    ]


def _scan_status(status: str) -> str:
    """`Complete`, or the sentence the PDF's cover gives a scan that did not complete."""
    if status == ScanStatus.SUCCEEDED.value:
        return "Complete"
    return _OUTCOME_LINE.get(status, status)


def _overall_score(report: ReportOut) -> str:
    """The headline figure as stored, or `Not scored`: never a zero, as the page and the PDF say."""
    score = report.score
    if score is not None and score.status is ScoreStatus.SCORED and score.composite is not None:
        return _number(score.composite)
    return "Not scored"


def _dimension_scores(report: ReportOut) -> list[Row]:
    rows: list[Row] = []
    for dimension in report.dimensions:
        label = narrative.DIMENSION_LABEL.get(dimension.key, dimension.key)
        if dimension.included and dimension.subscore is not None:
            rows.append(
                (DIMENSION_SCORES, "", "", label, "Score (out of 100)", _number(dimension.subscore))
            )
            continue
        # Left out is not scored zero: a row that says why, and no score.
        reason = dimension.exclusion_reason or "NOT_YET_MEASURED"
        why = narrative.EXCLUSION_REASON.get(reason, reason)
        rows.append((DIMENSION_SCORES, "", "", label, "Left out of the score because", why))
    return rows


def _share_of_voice(report: ReportOut, client: str) -> list[Row]:
    competitors = report.competitor_set.competitors if report.competitor_set else []
    if not competitors:
        # The report draws a card saying so where the table would be; there is no table to export.
        return []
    score = report.score
    measure = "Share of voice (out of 100)"
    rows: list[Row] = [
        (
            SHARE_OF_VOICE, "", "", client, measure,
            _optional(score.share_of_voice if score is not None else None),
        )
    ]
    for competitor in competitors:
        rows.append(
            (SHARE_OF_VOICE, "", "", competitor.name, measure, _optional(competitor.share_of_voice))
        )
    return rows


def _answers(report: ReportOut, client: str) -> list[Row]:
    proof = report.proof
    standings = {s.engine: s for s in proof.cross_engine.standings}
    listed: dict[Engine, int] = {}
    for shelf in proof.prompt_shelf:
        listed[shelf.engine] = listed.get(shelf.engine, 0) + 1
    rows: list[Row] = []

    for coverage in proof.engine_coverage:
        engine = _engine(coverage.engine)
        counts = (
            ("Prompts asked (count)", coverage.prompts_run),
            # The report lists the first 20 prompts, and these rows say how many are below.
            ("Prompts listed below (count)", listed.get(coverage.engine, 0)),
            ("Answers received (count)", coverage.answered),
            ("Answers naming the client (count)", coverage.mentioned),
        )
        for measure, count in counts:
            rows.append((ANSWERS, engine, "", client, measure, str(count)))
        standing = standings.get(coverage.engine)
        # No mention means no sentiment (`EngineStandingOut`): no row, never a zero.
        if standing is not None and standing.sentiment is not None:
            rows.append(
                (ANSWERS, engine, "", client, "Sentiment (out of 100)", _number(standing.sentiment))
            )

    for shelf in proof.prompt_shelf:
        engine = _engine(shelf.engine)
        text = shelf.prompt_text
        rows.append((ANSWERS, engine, text, client, "Answered (yes/no)", _yes_no(shelf.answered)))
        if not shelf.answered:
            # An answer that never came is not an absence: it says so and nothing else.
            continue
        named = _yes_no(shelf.subject_present)
        rows.append((ANSWERS, engine, text, client, "Named the client (yes/no)", named))
        # Stored ordinals only: a mention with no recorded position is a mention and no position.
        if shelf.subject_present and shelf.subject_position is not None:
            rows.append(
                (
                    ANSWERS, engine, text, client, "Position when named (1 = first)",
                    str(shelf.subject_position),
                )
            )
    return rows


def _cited_domains(report: ReportOut, client: str) -> list[Row]:
    proof = report.proof
    rows: list[Row] = []
    # The report's order: the tables it draws, the client's own pages first.
    for cited in (*proof.subject_cited_domains, *proof.competitor_cited_domains):
        rows.append(
            (CITED_DOMAINS, "", "", cited.domain, "Citations (count)", str(cited.citations))
        )
        who = cited.competitor_name or (client if cited.cites_subject else "Third party")
        rows.append((CITED_DOMAINS, "", "", cited.domain, "Attributed to", who))
    return rows


def _fixes(report: ReportOut) -> list[Row]:
    # The derivation the PDF runs on the same dump: which fixes exist, in what order, worth how
    # many points, with the generated wording laid on by key.
    story = narrative.derive_narrative(report.model_dump(by_alias=True, mode="json"))
    rows: list[Row] = []
    for fix in story.fixes:
        rows.append((FIXES, "", "", fix.title, "Priority", fix.priority))
        rows.append((FIXES, "", "", fix.title, "Effort (S, M or L)", fix.effort))
        # An audit fix carries no number: the system does not measure what one check moves.
        if fix.points_upside is not None:
            points = f"{fix.points_upside:.1f}"
            rows.append((FIXES, "", "", fix.title, "Points the score gains", points))
    return rows


# --------------------------------------------------------------------------
# cells
# --------------------------------------------------------------------------


def _engine(engine: Engine) -> str:
    """The engine as the report names it; its key if it has no name yet."""
    return narrative.ENGINE_LABEL.get(engine.value, engine.value)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _number(value: Decimal) -> str:
    """A decimal as served: `format(..., "f")`, so an exponent never reaches a spreadsheet."""
    return format(value, "f")


def _optional(value: Decimal | None) -> str:
    """A blank for a figure the report shows as a dash, never a zero."""
    return "" if value is None else _number(value)

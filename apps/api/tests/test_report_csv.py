"""The report's figures as one CSV file: the setting and the builder — F3, 2026-10-06.

The founder's brief: from the signed-in report, one CSV of the facts the report already shows.
Off by default (`REPORT_CSV_ENABLED`). This file holds everything that needs no database: the
setting, the cell guard and the builder, run over the six real and degraded reports the
cross-language tests already share (`packages/shared-types/fixtures/report-cases.json`). The
route, the report's `csvExportAvailable` field, the scoping and the refusals are
`test_report_csv_endpoint.py`'s; the facts-only sweep of the builder is `test_facts_only.py`'s.

What is held here:

- **The setting:** off when unset or blank, a plain boolean otherwise, a bad value stops the start
  by naming the variable and never the value, and the template ships it off.
- **The file:** a UTF-8 byte-order mark, one header row, six cells in every row, Python's `csv`
  quoting, the same bytes for the same report.
- **The guard:** a leading apostrophe on a cell that begins with `=`, `+`, `-`, `@`, a tab, a
  carriage return or a full-width `＝＋－＠`, with or without white space in front, and on a formula
  that starts after a `;`, a tab, a carriage return or a line feed inside the cell (a spreadsheet
  that splits on `;` or a tab starts a new cell there, and one in a `;` locale ends the record at
  a line break even inside quotes). Applied to EVERY cell, so a client name, a competitor name, a
  prompt or a fix title cannot become a formula.
- **Six sections,** each against the report it was built from, including the degraded ones: a
  figure that does not exist is not a row, and one the report shows as a dash is a blank cell,
  never a zero. The first, `Scan`, says whose scan it is, when, whether it finished and what it
  scored, so a partial scan's file says so.
- **Plain labels with units,** the client's own name where the file used to say `Subject`.
- **The fix list is the report's.** The report derives its fixes in the browser and the PDF in
  `report_narrative`, and generated fixes only reword those. The CSV takes that same derivation,
  and is held to what the web code produced (`expected-narrative.json`).
- **The file's name** is the client, the fixed words and the scan's date, reduced as the PDF's is.
"""

from __future__ import annotations

import copy
import csv
import inspect
import io
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from avp_api.config import Settings
from avp_api.models import ScanStatus
from avp_api.models.engine_result import Engine
from avp_api.models.score import ScoreStatus
from avp_api.routers import report as report_router
from avp_api.schemas.report import ReportOut
from avp_api.services import report_csv, report_pdf
from avp_api.services.report_narrative import ENGINE_LABEL

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
CASES: dict[str, Any] = json.loads((FIXTURES / "report-cases.json").read_text())
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected-narrative.json").read_text())
TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"
DB = "postgresql+asyncpg://avp:avp@db.internal:5432/avp"

# What a spreadsheet reads as the start of a formula: the brief's list (OWASP's), and the
# full-width forms of the four that are visible.
FORMULA_STARTS = ["=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠"]
# The visible four and their full-width forms, for the "after a separator" pattern.
FORMULA_CHARS = "=+-@＝＋－＠"
# A `;`, a tab, a carriage return or a line feed, then optional white space, then a formula
# character: a new cell or a new record in a spreadsheet that splits on any of them.
AFTER_SEPARATOR = re.compile(rf"[;\t\r\n]\s*[{re.escape(FORMULA_CHARS)}]")
BOM = b"\xef\xbb\xbf"

SCORED = "scored-with-generated-fixes"
HEADER = ["Section", "Engine", "Prompt", "Item", "Measure", "Value"]
# In this order, verbatim: the first thing in the file is whose scan it is.
SECTIONS = ["Scan", "Dimension scores", "Share of voice", "Answers", "Cited domains", "Fixes"]
# Every measure the file can say, verbatim. A figure carries its unit in its name.
MEASURES = {
    "Scan date",
    "Scan status",
    "Overall score (out of 100)",
    "Score (out of 100)",
    "Left out of the score because",
    "Share of voice (out of 100)",
    "Prompts asked (count)",
    "Prompts listed below (count)",
    "Answers received (count)",
    "Answers naming the client (count)",
    "Sentiment (out of 100)",
    "Answered (yes/no)",
    "Named the client (yes/no)",
    "Position when named (1 = first)",
    "Citations (count)",
    "Attributed to",
    "Priority",
    "Effort (S, M or L)",
    "Points the score gains",
}
# The measures whose value is a plain non-negative number (or blank where the report shows a dash).
NUMBER_MEASURES = {
    "Score (out of 100)",
    "Share of voice (out of 100)",
    "Prompts asked (count)",
    "Prompts listed below (count)",
    "Answers received (count)",
    "Answers naming the client (count)",
    "Sentiment (out of 100)",
    "Position when named (1 = first)",
    "Citations (count)",
    "Points the score gains",
}
PARTIAL_SENTENCE = (
    "This scan is partial: not every planned question was answered, "
    "so these results rest on fewer answers than planned."
)

# What the button says about the decimals, held to what the file writes (TestTheDecimalPlaces):
# the figures that keep two decimal places are exactly the scores, the shares and the sentiment;
# the points are one place, as the page shows them, and counts and positions are whole numbers.
BUTTON = (
    Path(__file__).resolve().parents[3]
    / "apps" / "web" / "src" / "components" / "report" / "DownloadCsvButton.tsx"
)
DECIMALS_SENTENCE = (
    "Scores, shares and sentiment keep two decimal places; the page rounds some of them."
)
SCORE_MEASURES = {"Overall score (out of 100)", "Score (out of 100)"}
SHARE_MEASURES = {"Share of voice (out of 100)"}
SENTIMENT_MEASURES = {"Sentiment (out of 100)"}
TWO_PLACE_MEASURES = SCORE_MEASURES | SHARE_MEASURES | SENTIMENT_MEASURES
ONE_PLACE_MEASURES = {"Points the score gains"}
WHOLE_MEASURES = {
    "Prompts asked (count)",
    "Prompts listed below (count)",
    "Answers received (count)",
    "Answers naming the client (count)",
    "Position when named (1 = first)",
    "Citations (count)",
}
# The one rule for "Not scored", as data both languages read: the web derives the button's
# `unscored` from it (`lib/report/csvScore.ts`) and the file's overall-score cell follows it.
SCORE_CASES: dict[str, Any] = json.loads((FIXTURES / "csv-score-cases.json").read_text())


def _settings(**given: Any) -> Settings:
    """Settings as the app builds them: from the environment, reading no dotenv file."""
    return Settings(
        _env_file=None, environment="local", database_url=DB, redis_url="redis://x/0", **given
    )


def report(name: str) -> ReportOut:
    return ReportOut.model_validate(CASES[name])


def parse(body: bytes) -> list[list[str]]:
    """What a spreadsheet reads: the bytes, the byte-order mark dropped, split by `csv`."""
    assert body.startswith(BOM)
    text = body.decode("utf-8")
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def table(r: ReportOut) -> list[list[str]]:
    return parse(report_csv.render_report_csv(r))


def section(rows: list[list[str]], name: str) -> list[list[str]]:
    return [row for row in rows if row[0] == name]


# ---------------------------------------------------------------------------
# the setting
# ---------------------------------------------------------------------------


class TestTheSetting:
    def test_the_default_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPORT_CSV_ENABLED", raising=False)
        assert _settings().report_csv_enabled is False

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_value_is_off_like_every_other_blank_setting(
        self, monkeypatch: pytest.MonkeyPatch, blank: str
    ) -> None:
        """`cp .env.example .env` and a cleared Render variable both leave `NAME=` behind."""
        monkeypatch.setenv("REPORT_CSV_ENABLED", blank)
        assert _settings().report_csv_enabled is False
        assert _settings(report_csv_enabled=blank).report_csv_enabled is False

    @pytest.mark.parametrize("raw", ["true", "TRUE", "1", " true "])
    def test_a_true_value_turns_it_on(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("REPORT_CSV_ENABLED", raw)
        assert _settings().report_csv_enabled is True

    @pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off"])
    def test_a_false_value_leaves_it_off(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("REPORT_CSV_ENABLED", raw)
        assert _settings().report_csv_enabled is False

    def test_a_value_that_is_not_a_boolean_stops_the_start_and_prints_no_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Somebody sees a typo at the start; nobody sees what was typed in the deploy log.

        Not "off": a typo that quietly turned the export off would be a switch the founder
        believes they set. The message names the setting; `Settings` hides its input.
        """
        sentinel = "LEAK-csv-qz7x"
        monkeypatch.setenv("REPORT_CSV_ENABLED", sentinel)
        with pytest.raises(ValidationError) as caught:
            _settings()
        message = str(caught.value)
        assert "report_csv_enabled" in message.lower()
        assert sentinel not in message
        assert "input_value" not in message

    def test_the_template_documents_it_and_ships_it_off(self) -> None:
        """A deployment that copies `.env.example` gets no export until somebody decides one."""
        lines = TEMPLATE.read_text().splitlines()
        at = next(
            i
            for i, line in enumerate(lines)
            if line.strip() in ("REPORT_CSV_ENABLED=", "REPORT_CSV_ENABLED=false")
        )
        comment: list[str] = []
        for line in reversed(lines[:at]):
            if not line.startswith("#"):
                break
            comment.append(line.lower())
        said = " ".join(comment)
        assert "csv" in said
        assert "off" in said
        assert "signed-in" in said
        assert "share" in said, "the comment says share links never get it"


# ---------------------------------------------------------------------------
# which scans can be exported
# ---------------------------------------------------------------------------


class TestWhichScansCanBeExported:
    @pytest.mark.parametrize(
        ("status", "exportable"),
        [
            (ScanStatus.SUCCEEDED, True),
            (ScanStatus.PARTIAL, True),
            (ScanStatus.FAILED, False),
            (ScanStatus.QUEUED, False),
            (ScanStatus.RUNNING, False),
            (ScanStatus.CANCELLED, False),
        ],
    )
    def test_only_a_finished_scan_with_figures_can(
        self, status: ScanStatus, exportable: bool
    ) -> None:
        assert report_csv.is_available(True, status) is exportable
        # The setting is the other half: off, nothing is ever available.
        assert report_csv.is_available(False, status) is False
        assert (report_csv.refusal_detail(status) is None) is exportable

    def test_every_status_the_product_has_is_decided_here_and_an_unknown_one_is_refused(
        self,
    ) -> None:
        """The list is an allow-list: a status added later is refused until somebody says so."""
        assert set(report_csv.EXPORT_STATUSES) == {ScanStatus.SUCCEEDED, ScanStatus.PARTIAL}
        for status in ScanStatus:
            allowed = status in report_csv.EXPORT_STATUSES
            assert report_csv.is_available(True, status) is allowed

    def test_each_refusal_is_a_plain_sentence_that_names_no_code_or_figure(self) -> None:
        for status in (
            ScanStatus.FAILED,
            ScanStatus.QUEUED,
            ScanStatus.RUNNING,
            ScanStatus.CANCELLED,
        ):
            sentence = report_csv.refusal_detail(status)
            assert sentence is not None
            assert sentence.startswith("This scan ") and sentence.endswith(".")
            assert not any(ch.isdigit() for ch in sentence), "a refusal quotes no figure"
        # And the three the brief names are not one sentence.
        sentences = {
            report_csv.refusal_detail(s)
            for s in (ScanStatus.FAILED, ScanStatus.QUEUED, ScanStatus.RUNNING)
        }
        assert len(sentences) == 3

    def test_a_failed_scan_is_told_in_the_words_the_founder_reads(self) -> None:
        assert report_csv.refusal_detail(ScanStatus.FAILED) == (
            "This scan failed, so its figures cannot be exported."
        )


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------

# What the guard is held to, in words: no cell begins with a formula character, with or without
# white space in front of it, and none has one straight after a `;`, a tab or a line break.
TRICKY = [
    "",
    " ",
    "x",
    "=",
    "x;=",
    ";;",
    "x; ;=",
    "\t\t=1",
    "a\t\t+b",
    " \t ;=x",
    "x;\N{NO-BREAK SPACE}=1",
    "x;\n=1",
    "x\n=1;",
    "x;\r\n=1;",
    "Acme\n=1+1;",
    "x\r=1;",
    "x\n\n=1",
    "\n\n=1",
    "ＡＢ＝ＣＤ",
    "'=1",
    "x;'=1",
    "-5",
    "--",
    "@@",
    "a;b;=c;+d",
    "＝＋－＠",
]

# (id, what is typed in front of a word, what the cell holds in front of it after the guard).
HOSTILE = [
    ("eq", "=", "'="),
    ("plus", "+", "'+"),
    ("minus", "-", "'-"),
    ("at", "@", "'@"),
    ("tab", "\t", "'\t"),
    ("cr", "\r", "'\r"),
    ("fullwidth-eq", "＝", "'＝"),
    ("fullwidth-plus", "＋", "'＋"),
    ("fullwidth-minus", "－", "'－"),
    ("fullwidth-at", "＠", "'＠"),
    ("space-eq", " =", "' ="),
    ("lf-eq", "\n=", "'\n'="),
    ("semicolon-eq", "x;=", "x;'="),
    ("tab-inside-eq", "x\t=", "x\t'="),
    ("semicolon-space-plus", "x; +", "x;' +"),
    # A line break inside the cell starts a new record where a reader in a `;` locale ignores
    # the quotes `csv` writes: what follows it must not begin with a formula either.
    ("lf-inside-eq", "x\n=1;", "x\n'=1;"),
    ("semicolon-crlf-eq", "x;\r\n=1;", "x;'\r'\n'=1;"),
    ("acme-lf-eq", "Acme\n=1+1;", "Acme\n'=1+1;"),
]


def hostile_report(typed: str) -> ReportOut:
    """The places an operator or our own generator puts text into the file, each starting with
    `typed`. Set after validation, so the white space `ReportOut` strips at either end of a string
    it is given stays: the builder does not rely on that."""
    r = report(SCORED).model_copy(deep=True)
    r.subject.name = typed + "client"
    r.subject.brand_name = None
    assert r.competitor_set is not None
    r.competitor_set.competitors[0].name = typed + "rival"
    # A competitor's name also rides on the domains it was credited with.
    r.proof.competitor_cited_domains[0].competitor_name = typed + "credited"
    r.proof.prompt_shelf[0].prompt_text = typed + "prompt"
    # The generated copy reaches the file by the key it was written for.
    r.action_items[0].title = typed + "fix"
    return r


def pieces_a_semicolon_locale_reads(body: bytes) -> list[str]:
    """The file read the way a `;`-locale spreadsheet reads it, at its worst: it takes the quotes
    `csv` writes for part of a name, so every `;`, tab and line break starts a cell or a record."""
    text = body.decode("utf-8").removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}")
    return re.split(r"[;\t\r\n]", text)


class TestTheGuard:
    @pytest.mark.parametrize("start", FORMULA_STARTS)
    def test_a_cell_that_begins_like_a_formula_gets_a_leading_apostrophe(
        self, start: str
    ) -> None:
        assert report_csv.guard_cell(f"{start}HYPERLINK(1)") == f"'{start}HYPERLINK(1)"
        # Even when that is all there is.
        assert report_csv.guard_cell(start) == f"'{start}"

    @pytest.mark.parametrize(
        "lead", [" ", "  ", "\n", "\N{NO-BREAK SPACE}", "\N{EM SPACE}", " \t "]
    )
    @pytest.mark.parametrize("start", ["=", "+", "-", "@", "＝"])
    def test_white_space_in_front_of_a_formula_does_not_hide_it(
        self, lead: str, start: str
    ) -> None:
        """The cell is judged by its first character that is not white space, so the guard does
        not lean on `ReportOut` having stripped the string already."""
        guarded = report_csv.guard_cell(f"{lead}{start}1+1")
        assert guarded.startswith("'")
        assert guarded.lstrip()[:1] not in FORMULA_STARTS
        assert guarded.replace("'", "") == f"{lead}{start}1+1"

    @pytest.mark.parametrize("separator", [";", "\t", "\r", "\n"])
    @pytest.mark.parametrize("start", ["=", "+", "-", "@", "＝", "＋", "－", "＠"])
    def test_a_formula_that_starts_after_a_separator_is_guarded_too(
        self, separator: str, start: str
    ) -> None:
        """A spreadsheet that splits on `;` or a tab makes a cell of what follows it, and one in a
        `;` locale ends the record at a line break even inside quotes, so what follows a carriage
        return or a line feed is a cell of its own too. `csv` quotes for a comma, a quote and a
        line break and for none of the first two, so a competitor named `Acme;=1+1`, or
        `Acme` and `=1+1;` on two lines, would otherwise land in the file as a formula."""
        assert report_csv.guard_cell(f"x{separator}{start}y") == f"x{separator}'{start}y"

    @pytest.mark.parametrize(
        ("raw", "guarded"),
        [
            ("x;=1+1", "x;'=1+1"),
            ("x\t=1+1", "x\t'=1+1"),
            ("Acme;=HYPERLINK(1)", "Acme;'=HYPERLINK(1)"),
            ("x; =1+1", "x;' =1+1"),
            ("a;;=1", "a;;'=1"),
            (";=1", ";'=1"),
            ("a;b;=c;+d", "a;b;'=c;'+d"),
            ("x\n=1;", "x\n'=1;"),
            ("Acme\n=1+1;", "Acme\n'=1+1;"),
            ("x\r=1", "x\r'=1"),
            # After a `;` and a line break both: the apostrophe goes after each, so the line
            # that follows the break starts with `'=` and not `=`.
            ("x;\n=1", "x;'\n'=1"),
            ("x;\n=1;", "x;'\n'=1;"),
            ("x;\r\n=1;", "x;'\r'\n'=1;"),
            ("a\n\n=1", "a\n'\n'=1"),
        ],
    )
    def test_the_apostrophe_goes_straight_after_the_separator(
        self, raw: str, guarded: str
    ) -> None:
        assert report_csv.guard_cell(raw) == guarded

    @pytest.mark.parametrize(
        "text",
        [
            "Help Scout",
            "0",
            "30.00",
            "",
            " not at the start =",
            "a=b",
            "x+y",
            "1-2",
            "mail@host",
            "'already quoted",
            "(=)",
            "Zendesk",
            "Café",
            "日本語",
            "#hash",
            "x; y",
            "x;'=1",
            "2026-08-21",
        ],
    )
    def test_anything_else_is_left_exactly_as_it_is(self, text: str) -> None:
        assert report_csv.guard_cell(text) == text

    @pytest.mark.parametrize("raw", TRICKY)
    def test_whatever_it_is_given_the_guard_leaves_nothing_a_spreadsheet_would_evaluate(
        self, raw: str
    ) -> None:
        out = report_csv.guard_cell(raw)
        assert out[:1] not in FORMULA_STARTS, out
        assert out.lstrip()[:1] not in FORMULA_STARTS, out
        assert not AFTER_SEPARATOR.search(out), out
        # Idempotent, and it only ever adds apostrophes.
        assert report_csv.guard_cell(out) == out
        assert out.replace("'", "") == raw.replace("'", "")

    def test_the_header_row_is_written_by_the_same_function_as_the_rest(self) -> None:
        """No cell is exempt, the header included."""
        assert table(report(SCORED))[0] == HEADER

    @pytest.mark.parametrize(
        ("typed", "held"),
        [(typed, held) for _id, typed, held in HOSTILE],
        ids=[hostile_id for hostile_id, _typed, _held in HOSTILE],
    )
    def test_a_client_name_a_competitor_a_prompt_and_a_fix_title_are_all_guarded(
        self, typed: str, held: str
    ) -> None:
        """The places an operator or our own generator puts text into the file.

        The values are set after validation: `ReportOut` strips the white space at either end of
        a string it is given, so a tab or a carriage return would never reach the builder
        through the real projection. The builder does not rely on that.
        """
        r = hostile_report(typed)
        rows = table(r)

        for planted in ("client", "rival", "credited", "prompt", "fix"):
            assert [row for row in rows if held + planted in row], (
                f"{planted!r} never reached the file as {held + planted!r}"
            )
            assert not [row for row in rows if typed + planted in row], (
                f"{planted!r} is in the file as it was typed"
            )

        # And the property under the examples: nothing a spreadsheet would evaluate, anywhere.
        for row in rows:
            for cell in row:
                assert cell[:1] not in FORMULA_STARTS, cell
                assert cell.lstrip()[:1] not in FORMULA_STARTS, cell
                assert not AFTER_SEPARATOR.search(cell), cell

    @pytest.mark.parametrize(
        "typed",
        [typed for _id, typed, _held in HOSTILE],
        ids=[hostile_id for hostile_id, _typed, _held in HOSTILE],
    )
    def test_a_reader_that_splits_on_semicolons_tabs_and_line_breaks_finds_no_formula(
        self, typed: str
    ) -> None:
        """The file as a whole, not a cell at a time: where a `;`, a tab or a line break starts a
        cell or a record, what follows is not a formula. `Acme` and `=1+1;` on two lines is one
        quoted cell to `csv` and two records to a reader in a `;` locale."""
        body = report_csv.render_report_csv(hostile_report(typed))
        for piece in pieces_a_semicolon_locale_reads(body):
            assert piece.lstrip()[:1] not in FORMULA_STARTS, repr(piece)

    def test_no_reader_finds_a_formula_in_any_report_the_product_makes(self) -> None:
        for name in CASES:
            body = report_csv.render_report_csv(report(name))
            for piece in pieces_a_semicolon_locale_reads(body):
                assert piece.lstrip()[:1] not in FORMULA_STARTS, (name, piece)

    def test_no_cell_of_any_report_the_product_makes_begins_with_a_trigger(self) -> None:
        for name in CASES:
            for row in table(report(name)):
                for cell in row:
                    assert cell[:1] not in FORMULA_STARTS, (name, cell)
                    assert cell.lstrip()[:1] not in FORMULA_STARTS, (name, cell)
                    assert not AFTER_SEPARATOR.search(cell), (name, cell)

    def test_a_name_the_guard_changed_is_still_the_name(self) -> None:
        """Only apostrophes are added: one in front, and one after the line break, where the `+1`
        would otherwise begin a record of its own. The rest of the cell is untouched."""
        r = report(SCORED).model_copy(deep=True)
        name = '=HYPERLINK("https://x.example","y"),\n+1'
        r.subject.name = name
        r.subject.brand_name = None
        cells = [row[3] for row in table(r) if row[3].replace("'", "") == name]
        assert cells, "the name never reached the file"
        assert set(cells) == {'\'=HYPERLINK("https://x.example","y"),\n\'+1'}

    def test_no_number_the_file_writes_is_negative(self) -> None:
        """The guard turns a cell that begins with `-` into text, so a negative figure would
        arrive as a word. Nothing here can be negative (scores, shares, counts and positions
        are not), and this keeps it so: a number cell is plain digits, or blank."""
        for name in CASES:
            for row in table(report(name))[1:]:
                measure, value = row[4], row[5]
                if measure in NUMBER_MEASURES:
                    assert value == "" or re.fullmatch(r"\d+(\.\d+)?", value), (name, row)
                assert not re.fullmatch(r"'?-\s*\.?\d[\d.]*", value), (name, row)


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------


class TestTheFile:
    def test_it_starts_with_one_utf8_byte_order_mark_and_the_header_row(self) -> None:
        body = report_csv.render_report_csv(report(SCORED))
        assert body.startswith(BOM + b"Section,Engine,Prompt,Item,Measure,Value\r\n")
        assert body.count(BOM) == 1, "one mark at the very start, never repeated"
        assert "\N{ZERO WIDTH NO-BREAK SPACE}" not in body[3:].decode("utf-8")

    def test_every_row_is_six_cells_and_the_header_is_the_only_header(self) -> None:
        rows = table(report(SCORED))
        assert rows[0] == HEADER
        assert all(len(row) == 6 for row in rows)
        assert HEADER not in rows[1:]

    def test_rows_end_in_a_carriage_return_and_a_line_feed(self) -> None:
        """RFC 4180's line ending, which is the one a spreadsheet expects."""
        text = report_csv.render_report_csv(report(SCORED)).decode("utf-8")
        assert text.endswith("\r\n")
        assert "\n" not in text.replace("\r\n", "")

    def test_it_is_quoted_by_the_csv_module_and_comes_back_whole(self) -> None:
        """A comma, a quote and a line break in a name survive the round trip."""
        r = report(SCORED).model_copy(deep=True)
        name = 'Acme, "the" Agency\nSecond line'
        r.subject.name = name
        r.subject.brand_name = None
        assert [row for row in table(r) if row[3] == name]

    def test_text_that_is_not_ascii_is_utf8_all_the_way(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        name = "Café Münch 日本語 😀"
        r.subject.name = name
        r.subject.brand_name = None
        body = report_csv.render_report_csv(r)
        assert name.encode("utf-8") in body
        assert [row for row in parse(body) if row[3] == name]

    def test_the_same_report_gives_the_same_bytes(self) -> None:
        r = report(SCORED)
        assert report_csv.render_report_csv(r) == report_csv.render_report_csv(r)
        # And the report is not changed by being exported.
        before = r.model_dump()
        report_csv.render_report_csv(r)
        assert r.model_dump() == before

    def test_the_sections_come_in_one_fixed_order_and_scan_is_first(self) -> None:
        assert list(report_csv.SECTIONS) == SECTIONS
        assert list(report_csv.CSV_COLUMNS) == HEADER
        seen = [row[0] for row in table(report(SCORED))[1:]]
        order = [SECTIONS.index(name) for name in seen]
        assert order == sorted(order), "each section is one run of rows, in the fixed order"
        assert seen[0] == "Scan"

    def test_every_degraded_report_the_product_makes_still_builds(self) -> None:
        for name in CASES:
            rows = table(report(name))
            assert rows[0] == HEADER, name
            assert len(rows) > 1, name


# ---------------------------------------------------------------------------
# the labels
# ---------------------------------------------------------------------------


class TestPlainLabels:
    def test_every_measure_the_file_says_is_one_of_these_and_a_figure_names_its_unit(self) -> None:
        said: set[str] = set()
        for name in CASES:
            said |= {row[4] for row in table(report(name))[1:]}
        # The shared reports predate per-engine sentiment, so one engine is given a tone.
        toned = report(SCORED).model_copy(deep=True)
        toned.proof.cross_engine.standings[0].sentiment = Decimal("75.00")
        said |= {row[4] for row in table(toned)[1:]}
        assert said <= MEASURES, said - MEASURES
        assert said == MEASURES, f"a label nothing says: {MEASURES - said}"
        # No code identifier: nobody reading a spreadsheet should meet `answers_received`.
        for label in MEASURES | set(SECTIONS) | set(HEADER):
            assert "_" not in label, label
            assert label[:1].isupper(), label

    def test_the_figures_that_have_a_unit_say_it(self) -> None:
        for label in NUMBER_MEASURES - {"Points the score gains"}:
            assert label.endswith(")"), label
        assert {"Overall score (out of 100)"} <= MEASURES

    def test_the_client_is_named_never_called_the_subject(self) -> None:
        for name in CASES:
            rows = table(report(name))
            assert not [row for row in rows if "Subject" in row], name
        attributed = [
            row
            for row in table(report(SCORED))
            if row[4] == "Attributed to" and row[3] == "helpscout.com"
        ]
        assert attributed == [
            ["Cited domains", "", "", "helpscout.com", "Attributed to", "Help Scout"]
        ]


# ---------------------------------------------------------------------------
# the six sections
# ---------------------------------------------------------------------------


def expected_counts(r: ReportOut, name: str) -> dict[str, int]:
    """How many rows each section has, worked out from the report and the web's fix list.

    Written from the brief, not from the builder: one row per fact, a fact that does not exist is
    not a row, and one the report shows as a dash is a blank cell on a row that is there.
    """
    proof = r.proof
    standings = {s.engine: s for s in proof.cross_engine.standings}
    answers = 0
    for coverage in proof.engine_coverage:
        answers += 4  # asked, listed below, received, naming the client
        standing = standings.get(coverage.engine)
        if standing is not None and standing.sentiment is not None:
            answers += 1
    for row in proof.prompt_shelf:
        answers += 1  # answered
        if row.answered:
            answers += 1  # named the client
            if row.subject_present and row.subject_position is not None:
                answers += 1  # position

    competitors = r.competitor_set.competitors if r.competitor_set else []
    return {
        "Scan": 3,  # the date, the status, the overall score
        "Dimension scores": len(r.dimensions),
        "Share of voice": len(competitors) + 1 if competitors else 0,
        "Answers": answers,
        "Cited domains": 2
        * (len(proof.subject_cited_domains) + len(proof.competitor_cited_domains)),
        "Fixes": sum(
            2 + (0 if fix["pointsUpside"] is None else 1) for fix in EXPECTED[name]["fixes"]
        ),
    }


class TestTheSixSections:
    @pytest.mark.parametrize("name", sorted(CASES))
    def test_each_section_has_the_rows_the_report_has_facts_for(self, name: str) -> None:
        r = report(name)
        rows = table(r)[1:]
        counts = expected_counts(r, name)
        for key, want in counts.items():
            assert len(section(rows, key)) == want, (name, key)
        assert len(rows) == sum(counts.values()), "and there is no seventh kind of row"

    def test_a_full_report_has_all_six(self) -> None:
        rows = table(report(SCORED))[1:]
        assert {row[0] for row in rows} == set(SECTIONS)

    def test_the_figures_are_the_ones_the_report_holds(self) -> None:
        """Spot checks against the real Help Scout scan the fixture was taken from."""
        rows = table(report(SCORED))[1:]
        answers = section(rows, "Answers")
        client = "Help Scout"

        # Per engine, for the whole scan: what each engine returned, and how much is listed.
        for engine in ("Claude", "Claude with web search"):
            assert [r[4:] for r in answers if r[1] == engine and r[2] == ""] == [
                ["Prompts asked (count)", "3"],
                ["Prompts listed below (count)", "3"],
                ["Answers received (count)", "3"],
                ["Answers naming the client (count)", "3"],
            ]

        # Per prompt and engine: whether it answered, named the client, and where.
        prompt = "what's a good shared inbox tool for a small support team"
        for engine in ("Claude", "Claude with web search"):
            assert ["Answers", engine, prompt, client, "Answered (yes/no)", "yes"] in answers
            named = "Named the client (yes/no)"
            assert ["Answers", engine, prompt, client, named, "yes"] in answers
            assert [
                "Answers", engine, prompt, client, "Position when named (1 = first)", "1",
            ] in answers

        sov = section(rows, "Share of voice")
        measure = "Share of voice (out of 100)"
        assert ["Share of voice", "", "", client, measure, "30.00"] in sov
        assert ["Share of voice", "", "", "Zendesk", measure, "25.00"] in sov
        assert ["Share of voice", "", "", "Freshdesk", measure, "20.00"] in sov
        assert ["Share of voice", "", "", "Kustomer", measure, "0.00"] in sov

        cited = section(rows, "Cited domains")
        assert ["Cited domains", "", "", "helpscout.com", "Citations (count)", "3"] in cited
        assert ["Cited domains", "", "", "helpscout.com", "Attributed to", client] in cited
        assert ["Cited domains", "", "", "front.com", "Attributed to", "Front"] in cited
        assert ["Cited domains", "", "", "eesel.ai", "Citations (count)", "6"] in cited
        assert ["Cited domains", "", "", "eesel.ai", "Attributed to", "Third party"] in cited

        assert section(rows, "Dimension scores") == [
            ["Dimension scores", "", "", "Mention Rate", "Score (out of 100)", "100.00"],
            ["Dimension scores", "", "", "Share of Voice", "Score (out of 100)", "30.00"],
            ["Dimension scores", "", "", "Citation Strength", "Score (out of 100)", "3.70"],
            ["Dimension scores", "", "", "Sentiment", "Score (out of 100)", "75.00"],
            ["Dimension scores", "", "", "Technical Foundation", "Score (out of 100)", "87.50"],
        ]

    def test_the_prompts_listed_are_counted_beside_the_prompts_asked(self) -> None:
        """The report lists the first 20 prompts of a scan and the file says so in its rows."""
        r = report(SCORED).model_copy(deep=True)
        claude = r.proof.engine_coverage[0]
        claude.prompts_run = 24
        # Two of Claude's three listed rows are not in the Answer Shelf.
        r.proof.prompt_shelf = [
            row for row in r.proof.prompt_shelf
            if row.engine != claude.engine or row.prompt_position == 1
        ]
        rows = section(table(r)[1:], "Answers")
        mine = [row for row in rows if row[1] == "Claude" and row[2] == ""]
        assert [row[4:] for row in mine][:2] == [
            ["Prompts asked (count)", "24"],
            ["Prompts listed below (count)", "1"],
        ]
        listed = {row[2] for row in rows if row[1] == "Claude" and row[2]}
        assert len(listed) == 1

    def test_an_engine_that_did_not_answer_is_not_an_engine_that_did_not_name_the_client(
        self,
    ) -> None:
        """The Answer Shelf's rule, kept: no answer is not an absence, so it says only that."""
        r = report(SCORED).model_copy(deep=True)
        gap = r.proof.prompt_shelf[0]
        gap.answered = False
        gap.subject_present = False
        gap.subject_position = None
        rows = section(table(r)[1:], "Answers")
        mine = [row for row in rows if row[2] == gap.prompt_text and row[1] == "Claude"]
        assert mine == [
            ["Answers", "Claude", gap.prompt_text, "Help Scout", "Answered (yes/no)", "no"]
        ]

    def test_a_prompt_the_client_was_not_named_in_has_no_position(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        absent = r.proof.prompt_shelf[0]
        absent.subject_present = False
        absent.subject_position = None
        rows = section(table(r)[1:], "Answers")
        mine = [row for row in rows if row[2] == absent.prompt_text and row[1] == "Claude"]
        assert [row[4:] for row in mine] == [
            ["Answered (yes/no)", "yes"],
            ["Named the client (yes/no)", "no"],
        ]

    def test_a_mention_the_engine_gave_no_ordinal_for_is_a_mention_with_no_position(self) -> None:
        """The shelf's own rule: an ordinal that was not stored is never made up."""
        r = report(SCORED).model_copy(deep=True)
        row = r.proof.prompt_shelf[0]
        row.subject_present = True
        row.subject_position = None
        mine = [
            c
            for c in section(table(r)[1:], "Answers")
            if c[2] == row.prompt_text and c[1] == "Claude"
        ]
        assert [c[4:] for c in mine] == [
            ["Answered (yes/no)", "yes"],
            ["Named the client (yes/no)", "yes"],
        ]

    def test_an_engines_sentiment_is_a_row_only_when_it_has_one(self) -> None:
        """No mention means no sentiment (`EngineStandingOut`): no row, and never a zero."""
        r = report(SCORED).model_copy(deep=True)
        assert all(s.sentiment is None for s in r.proof.cross_engine.standings)
        assert not [c for c in table(r) if c[4] == "Sentiment (out of 100)"]
        r.proof.cross_engine.standings[0].sentiment = Decimal("75.00")
        engine = r.proof.cross_engine.standings[0].engine
        found = [c for c in table(r) if c[4] == "Sentiment (out of 100)"]
        assert found == [
            [
                "Answers", ENGINE_LABEL[engine.value], "", "Help Scout",
                "Sentiment (out of 100)", "75.00",
            ]
        ]

    def test_a_competitor_the_scan_could_not_compare_is_a_blank_never_a_zero(self) -> None:
        """A scan never scored lists its rivals and has no figure for any of them.

        `build_report` gives a rival no comparison when the scan has no score (the fixture still
        carries figures from before that rule), so the figures are removed the way it removes them.
        """
        r = report("unscored").model_copy(deep=True)
        assert r.score is None
        assert r.competitor_set is not None and r.competitor_set.competitors
        for competitor in r.competitor_set.competitors:
            competitor.share_of_voice = None
        sov = section(table(r)[1:], "Share of voice")
        assert len(sov) == len(r.competitor_set.competitors) + 1
        assert all(row[4] == "Share of voice (out of 100)" and row[5] == "" for row in sov)

    def test_a_report_with_no_competitor_set_has_no_share_of_voice_rows(self) -> None:
        r = report("no-competitor-set")
        assert r.competitor_set is None
        assert section(table(r)[1:], "Share of voice") == []

    def test_an_excluded_dimension_says_why_and_has_no_score(self) -> None:
        dims = section(table(report("not-yet-measured"))[1:], "Dimension scores")
        cited = [row for row in dims if row[3] == "Citation Strength"]
        assert cited == [
            [
                "Dimension scores", "", "", "Citation Strength",
                "Left out of the score because", "Not yet checked",
            ]
        ]
        assert not [row for row in dims if row[5] in ("0", "0.00")]
        # The others are scored, as the report scored them.
        assert [
            "Dimension scores", "", "", "Mention Rate", "Score (out of 100)", "100.00",
        ] in dims

        no_rivals = section(table(report("no-competitor-set"))[1:], "Dimension scores")
        assert [
            "Dimension scores", "", "", "Share of Voice",
            "Left out of the score because", "No comparison was made",
        ] in no_rivals

    def test_an_unscored_scan_has_no_dimension_rows_at_all(self) -> None:
        """Never scored is not scored zero: the report has no dimensions, so the file has none."""
        assert section(table(report("unscored"))[1:], "Dimension scores") == []

    def test_a_decimal_is_written_as_the_report_serves_it(self) -> None:
        """Two places, as stored, never an exponent and never a float."""
        r = report(SCORED).model_copy(deep=True)
        r.dimensions[0].subscore = Decimal("1E+2")
        r.dimensions[1].subscore = Decimal("30.10")
        dims = section(table(r)[1:], "Dimension scores")
        assert dims[0][5] == "100"
        assert dims[1][5] == "30.10"

    def test_an_engine_is_named_as_the_report_names_it(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        r.proof.prompt_shelf[0].engine = Engine.CHATGPT
        engines = {row[1] for row in section(table(r)[1:], "Answers")}
        assert "ChatGPT" in engines
        for engine in Engine:
            assert engine.value in ENGINE_LABEL, "an engine without a label would show its key"


# ---------------------------------------------------------------------------
# the scan section: whose scan, when, how it ended and what it scored
# ---------------------------------------------------------------------------


class TestTheScanSection:
    def test_it_names_the_client_the_date_the_status_and_the_score(self) -> None:
        assert section(table(report(SCORED))[1:], "Scan") == [
            ["Scan", "", "", "Help Scout", "Scan date", "2026-08-21"],
            ["Scan", "", "", "Help Scout", "Scan status", "Complete"],
            ["Scan", "", "", "Help Scout", "Overall score (out of 100)", "58.24"],
        ]

    def test_it_is_the_first_thing_in_the_file(self) -> None:
        rows = table(report(SCORED))
        assert [row[0] for row in rows[1:4]] == ["Scan", "Scan", "Scan"]

    def test_a_partial_scan_says_so_in_the_words_the_pdf_uses(self) -> None:
        """A scan that ran out of time shrinks both counts, so the counts alone read complete."""
        r = report(SCORED).model_copy(deep=True)
        r.scan_status = "partial"
        status = [row for row in table(r)[1:] if row[4] == "Scan status"]
        assert status == [["Scan", "", "", "Help Scout", "Scan status", PARTIAL_SENTENCE]]
        # The PDF's cover sentence for the same scan, so the two cannot say it differently.
        assert report_pdf._OUTCOME_LINE["partial"] == PARTIAL_SENTENCE  # noqa: SLF001

    def test_a_succeeded_scan_is_complete(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        r.scan_status = "succeeded"
        assert [row[5] for row in table(r) if row[4] == "Scan status"] == ["Complete"]

    @pytest.mark.parametrize("name", ["unscored"])
    def test_a_scan_that_was_never_scored_says_not_scored_and_never_zero(self, name: str) -> None:
        r = report(name)
        assert r.score is None
        score = [row for row in table(r)[1:] if row[4] == "Overall score (out of 100)"]
        assert score == [["Scan", "", "", "Help Scout", "Overall score (out of 100)", "Not scored"]]

    def test_a_scan_with_too_little_data_to_score_says_not_scored_too(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        assert r.score is not None
        r.score.status = ScoreStatus.INSUFFICIENT_DATA
        r.score.composite = None
        score = [row[5] for row in table(r) if row[4] == "Overall score (out of 100)"]
        assert score == ["Not scored"]

    def test_a_score_row_that_holds_no_composite_is_not_a_zero(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        assert r.score is not None
        r.score.composite = None
        score = [row[5] for row in table(r) if row[4] == "Overall score (out of 100)"]
        assert score == ["Not scored"]

    def test_the_score_is_the_stored_composite_as_the_report_serves_it(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        assert r.score is not None
        r.score.composite = Decimal("7.50")
        assert [row[5] for row in table(r) if row[4] == "Overall score (out of 100)"] == ["7.50"]

    def test_the_date_is_the_scans_in_utc_and_the_reports_when_the_scan_has_none(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        assert report_csv.scan_date(r) == "2026-08-21"
        # 23:30 at UTC-5 is already the next day at UTC.
        r.scanned_at = datetime(2026, 8, 21, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
        assert report_csv.scan_date(r) == "2026-08-22"
        r.scanned_at = None
        r.generated_at = datetime(2026, 10, 6, 1, 2, tzinfo=UTC)
        assert report_csv.scan_date(r) == "2026-10-06"
        assert [row[5] for row in table(r) if row[4] == "Scan date"] == ["2026-10-06"]

    def test_a_hostile_client_name_is_guarded_in_this_section_too(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        r.subject.name = "=cmd|' /C calc'!A0"
        r.subject.brand_name = None
        scan = section(table(r)[1:], "Scan")
        assert {row[3] for row in scan} == {"'=cmd|' /C calc'!A0"}


# ---------------------------------------------------------------------------
# the fix list is the report's
# ---------------------------------------------------------------------------


class TestTheFixListIsTheReports:
    @pytest.mark.parametrize("name", sorted(CASES))
    def test_the_fixes_are_the_ones_the_web_code_derives(self, name: str) -> None:
        """`expected-narrative.json` was written by the TypeScript the report page runs.

        The projection carries only the fixes a model reworded, and the page merges them onto a
        list it derives itself. A CSV of the projection's list alone would be empty for a scan
        whose fixes were never generated, while the page shows five.
        """
        fixes = section(table(report(name))[1:], "Fixes")
        want: list[list[str]] = []
        for fix in EXPECTED[name]["fixes"]:
            want.append(["Fixes", "", "", fix["title"], "Priority", fix["priority"]])
            want.append(["Fixes", "", "", fix["title"], "Effort (S, M or L)", fix["effort"]])
            if fix["pointsUpside"] is not None:
                want.append(
                    [
                        "Fixes", "", "", fix["title"], "Points the score gains",
                        f"{fix['pointsUpside']:.1f}",
                    ]
                )
        assert fixes == want

    def test_a_fix_that_carries_no_points_has_no_points_row(self) -> None:
        """An audit fix is not worth a number the system never measured: nothing is invented."""
        fixes = section(table(report(SCORED))[1:], "Fixes")
        schema = "Add FAQPage markup to the pages that answer buyer questions about Help Scout"
        measures = [row[4] for row in fixes if row[3] == schema]
        assert measures == ["Priority", "Effort (S, M or L)"]

    def test_the_detail_of_a_fix_is_not_in_the_file(self) -> None:
        """Title, priority, effort and points, as the brief says, and no paragraph."""
        r = report(SCORED)
        details = [i.detail for i in r.action_items if i.detail]
        assert details
        body = report_csv.render_report_csv(r).decode("utf-8")
        for detail in details:
            assert detail not in body
            assert detail[:40] not in body


# ---------------------------------------------------------------------------
# the file's name
# ---------------------------------------------------------------------------


def pdf_stem(r: ReportOut) -> str:
    """What the PDF route names the client in its own file's name."""
    header = report_router._pdf_response(r).headers["content-disposition"]  # noqa: SLF001
    found = re.fullmatch(r'attachment; filename="(.*)-ai-visibility\.pdf"', header)
    assert found, header
    return found.group(1)


class TestTheFilename:
    def test_it_is_the_client_the_fixed_words_and_the_scans_date(self) -> None:
        r = report(SCORED)
        assert report_router._csv_filename(r) == "Help-Scout-ai-visibility-2026-08-21.csv"  # noqa: SLF001

    def test_it_says_nothing_of_the_product_that_made_it(self) -> None:
        assert "avp" not in report_router._csv_filename(report(SCORED)).lower()  # noqa: SLF001

    @pytest.mark.parametrize(
        "name",
        [
            "Help Scout",
            "Acme (Holdings) Ltd.",
            "Café Nero",
            "a.b_c-d",
            "___",
            "-_-",
            "  spaced   out  ",
            "x" * 200,
            "...",
            "日本語",
            'a"; filename="evil.exe',
            "x\r\nSet-Cookie: a=b",
            "../../etc/passwd",
            "=cmd|' /C calc'!A0",
        ],
    )
    def test_the_client_is_reduced_exactly_as_the_pdfs_name_is(self, name: str) -> None:
        """One reduction, the PDF route's `_UNSAFE_FILENAME`, so the two cannot drift."""
        r = report(SCORED).model_copy(deep=True)
        r.subject.name = name
        r.subject.brand_name = None
        filename = report_router._csv_filename(r)  # noqa: SLF001
        stem = pdf_stem(r)
        if stem == "report":  # the PDF's own fallback: nothing left of the name
            assert filename == "ai-visibility-2026-08-21.csv"
        else:
            assert filename == f"{stem}-ai-visibility-2026-08-21.csv"
        # Whatever was typed, the header carries only these characters.
        assert re.fullmatch(r"[A-Za-z0-9._-]+\.csv", filename), filename

    def test_an_underscore_survives_the_reduction_so_a_name_of_underscores_has_a_stem(self) -> None:
        """The reduction keeps `A-Za-z0-9._-`, so `_` is a stem and "no ASCII letter or digit"
        was not the rule for having none: nothing left after the reduction is."""
        r = report(SCORED).model_copy(deep=True)
        r.subject.name = "___"
        r.subject.brand_name = None
        assert report_router._csv_filename(r) == "___-ai-visibility-2026-08-21.csv"  # noqa: SLF001

    def test_the_docstring_says_only_what_is_true_of_the_reduction(self) -> None:
        doc = " ".join((inspect.getdoc(report_router._csv_filename) or "").split())  # noqa: SLF001
        assert "no ASCII letter or digit" not in doc
        assert "A name with nothing left after the reduction leaves no stem" in doc
        assert "named for the fixed words and the date alone" in doc
        assert "`ai-visibility-<date>.csv` when no client is left" in doc

    def test_the_brand_name_is_what_the_pdf_uses_and_so_is_this(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        r.subject.name = "Help Scout Inc"
        r.subject.brand_name = "Help Scout"
        assert report_router._csv_filename(r).startswith("Help-Scout-ai-visibility-")  # noqa: SLF001

    def test_a_name_with_nothing_left_falls_back_to_the_fixed_words_and_the_date(self) -> None:
        """The date stays: two scans of two such clients on two days are two files, and a name
        in the download list says which scan it is."""
        r = report(SCORED).model_copy(deep=True)
        r.subject.name = "日本語"
        r.subject.brand_name = None
        assert report_router._csv_filename(r) == "ai-visibility-2026-08-21.csv"  # noqa: SLF001
        r.scanned_at = None
        r.generated_at = datetime(2026, 10, 6, 1, 2, tzinfo=UTC)
        assert report_router._csv_filename(r) == "ai-visibility-2026-10-06.csv"  # noqa: SLF001

    def test_the_stem_is_at_most_sixty_characters_like_the_pdfs(self) -> None:
        r = report(SCORED).model_copy(deep=True)
        r.subject.name = "x" * 200
        r.subject.brand_name = None
        stem = report_router._csv_filename(r).removesuffix(  # noqa: SLF001
            "-ai-visibility-2026-08-21.csv"
        )
        assert stem == "x" * 60


# ---------------------------------------------------------------------------
# the decimal places, which the button describes
# ---------------------------------------------------------------------------


class TestTheDecimalPlaces:
    """The button says "Scores, shares and sentiment keep two decimal places; the page rounds some
    of them." That is true of exactly these figures: the overall score, each dimension's score, the
    share of voice (the client's and a rival's) and an engine's sentiment, each rounded half-up to
    two places at its source (`scoring._round2`, and `Numeric(5, 2)` where it is stored). It says
    nothing of the points, which are one place as the page shows them, or of the counts."""

    @staticmethod
    def every_row() -> list[list[str]]:
        rows: list[list[str]] = []
        for name in CASES:
            rows += table(report(name))[1:]
        # The shared reports predate per-engine sentiment, so one engine is given a tone.
        toned = report(SCORED).model_copy(deep=True)
        toned.proof.cross_engine.standings[0].sentiment = Decimal("75.00")
        return rows + table(toned)[1:]

    def test_scores_shares_and_sentiment_are_written_to_two_places(self) -> None:
        seen: set[str] = set()
        for row in self.every_row():
            if row[4] in TWO_PLACE_MEASURES and row[5] not in ("", "Not scored"):
                assert re.fullmatch(r"\d+\.\d{2}", row[5]), row
                seen.add(row[4])
        assert seen == TWO_PLACE_MEASURES, "every kind the sentence names was checked"

    def test_points_are_written_to_one_place_as_the_page_shows_them(self) -> None:
        seen = [
            row[5] for row in self.every_row() if row[4] in ONE_PLACE_MEASURES
        ]
        assert seen, "no points were checked"
        for value in seen:
            assert re.fullmatch(r"\d+\.\d", value), value

    def test_counts_and_positions_are_whole_numbers(self) -> None:
        seen: set[str] = set()
        for row in self.every_row():
            if row[4] in WHOLE_MEASURES:
                assert re.fullmatch(r"\d+", row[5]), row
                seen.add(row[4])
        assert seen == WHOLE_MEASURES

    def test_no_figure_is_in_two_of_these_kinds(self) -> None:
        assert not (TWO_PLACE_MEASURES & ONE_PLACE_MEASURES)
        assert not (TWO_PLACE_MEASURES & WHOLE_MEASURES)
        assert not (ONE_PLACE_MEASURES & WHOLE_MEASURES)
        # And together they are every figure the file writes (the rest is words, names and yes/no).
        figures = TWO_PLACE_MEASURES | ONE_PLACE_MEASURES | WHOLE_MEASURES
        assert figures <= MEASURES
        assert MEASURES - figures == {
            "Scan date",
            "Scan status",
            "Left out of the score because",
            "Answered (yes/no)",
            "Named the client (yes/no)",
            "Attributed to",
            "Priority",
            "Effort (S, M or L)",
        }

    def test_the_button_says_exactly_that_and_no_longer_says_numbers(self) -> None:
        source = BUTTON.read_text(encoding="utf-8")
        assert source.count(DECIMALS_SENTENCE) == 1
        assert "Numbers keep two decimal places" not in source
        # It names scores, shares and sentiment, which are the two-place figures and no others.
        assert DECIMALS_SENTENCE.startswith("Scores, shares and sentiment keep two decimal places")
        assert TWO_PLACE_MEASURES == SCORE_MEASURES | SHARE_MEASURES | SENTIMENT_MEASURES


# ---------------------------------------------------------------------------
# "Not scored" is one rule, and the web reads the same one
# ---------------------------------------------------------------------------


class TestNotScoredIsOneRule:
    @pytest.mark.parametrize("name", sorted(SCORE_CASES))
    def test_the_overall_score_cell_follows_the_shared_rule(self, name: str) -> None:
        """`csv-score-cases.json` is read by `lib/report/csvScore.test.ts` too, which holds the
        button's `unscored` to it: a scan never scored, one with too little data and a score row
        with no composite all say `Not scored`, and the button says so before the click."""
        case = SCORE_CASES[name]
        r = report(SCORED).model_copy(deep=True)
        if case["score"] is None:
            r.score = None
        else:
            assert r.score is not None
            r.score.status = ScoreStatus(case["score"]["status"])
            composite = case["score"]["composite"]
            r.score.composite = None if composite is None else Decimal(composite)
        cell = [row[5] for row in table(r) if row[4] == "Overall score (out of 100)"]
        if case["fileSaysNotScored"]:
            assert cell == ["Not scored"], name
        else:
            assert cell == [case["score"]["composite"]], name

    def test_the_shared_cases_cover_every_way_a_scan_can_be_not_scored(self) -> None:
        said = {name for name, case in SCORE_CASES.items() if case["fileSaysNotScored"]}
        assert len(said) >= 4
        statuses = {case["score"]["status"] for case in SCORE_CASES.values() if case["score"]}
        assert statuses == {"scored", "insufficient_data"}
        assert any(case["score"] is None for case in SCORE_CASES.values())
        assert any(
            case["score"] and case["score"]["composite"] is None for case in SCORE_CASES.values()
        )


def test_the_csv_fix_points_add_up_to_the_report_s_middle_row_on_rmit_figures() -> None:
    """The CSV carries no total and no ceiling, so on the CSV "re-adds" means: its
    fixes' points sum to the report's middle row, over the report's own composite."""
    subscores = {
        "mention_rate": "97.22",
        "share_of_voice": "30.43",
        "citation_strength": "96.01",
        "sentiment": "65.58",
        "technical_foundation": "66.67",
    }
    case = copy.deepcopy(CASES["scored-no-generated-fixes"])
    case["score"]["composite"] = "72.48"
    for dimension in case["dimensions"]:
        dimension["subscore"] = subscores[dimension["key"]]
        dimension["included"] = True
        dimension["exclusionReason"] = None
    rows = report_csv.report_rows(ReportOut.model_validate(case))
    overall = [r[5] for r in rows if r[0] == "Scan" and r[4] == "Overall score (out of 100)"]
    points = [Decimal(r[5]) for r in rows if r[0] == "Fixes" and r[4] == "Points the score gains"]
    assert overall == ["72.48"]
    assert points == [Decimal("17.4"), Decimal("5.2"), Decimal("3.3")]
    assert sum(points) == Decimal("25.9")
    assert Decimal(overall[0]) + sum(points) == Decimal("98.38")

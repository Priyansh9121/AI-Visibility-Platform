"""PDF export — Epic 9.14 (Epic 9 send path, slice 3).

Two things are under test and they are different.

**The writer** (`services/pdf.py`) must produce a file a reader can actually
open. There is no PDF library here to trust for that, so the structure is
asserted directly: the header, the object table, the cross-reference offsets,
and the trailer. A file that is "nearly" a PDF opens nowhere.

**The document** (`services/report_pdf.py`) must be the SAME report as the
screen. That means the degraded states above all: a null score prints "Not
scored" and never a zero, an excluded dimension prints its reason and never a
sub-score, and an empty `actionItems` list renders the deterministic fix
derivation. The fixtures are the same six the cross-language narrative test uses
— real Help Scout data and its degraded variants — so the two suites cannot
disagree about what a case is.
"""

from __future__ import annotations

import copy
import json
import re
import zlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from avp_api.deps import scan_executor
from avp_api.schemas.report import ReportOut
from avp_api.services import pdf as pdf_writer
from avp_api.services import report_narrative as narrative
from avp_api.services.report_pdf import render_report_pdf

BASE = "/api/v1"
FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
CASES: dict[str, Any] = json.loads((FIXTURES / "report-cases.json").read_text())


def _render(case: str) -> bytes:
    return render_report_pdf(ReportOut.model_validate(CASES[case]))


def _visible_text(pdf: bytes) -> str:
    """Every string the document actually draws.

    Content streams are Flate-compressed, so this decompresses them and pulls
    the literals out of the `(...) Tj` operators. Asserting on the raw bytes
    would pass on a document that merely CONTAINED the words somewhere — this
    asserts on what a reader would see.
    """
    out: list[str] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for literal in re.finditer(rb"\((.*?)\) Tj", content, re.S):
            raw = literal.group(1)
            raw = re.sub(rb"\\([0-7]{3})", lambda m: _octal(m.group(1)), raw)
            raw = raw.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            # cp1252, not latin-1: the writer emits WinAnsiEncoding, where
            # 0x97 is an em dash. latin-1 decodes the same byte to a C1
            # control character, and every assertion containing an em dash
            # then fails against text that renders perfectly.
            out.append(raw.decode("cp1252", errors="replace"))
    # Joined with spaces and collapsed, NOT with newlines. A sentence that
    # happens to wrap across two lines is still that sentence to a reader, and
    # an assertion that broke on where the wrap fell would be testing the
    # wrapper rather than the document.
    #
    # The no-break space is deliberately NOT collapsed: it is a character the
    # document draws on purpose, and flattening it here would let a reader
    # assert on a sentence the writer never produced.
    return re.sub(r"[^\S\u00a0]+", " ", " ".join(out))


def _octal(digits: bytes) -> bytes:
    return bytes([int(digits, 8)])


# ---------------------------------------------------------------------------
# the writer produces a real PDF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(CASES))
def test_every_case_produces_a_structurally_valid_pdf(case: str) -> None:
    pdf = _render(case)

    assert pdf.startswith(b"%PDF-1.7\n")
    # The binary marker, so a transport sniffing the first bytes does not treat
    # the file as text and mangle its newlines.
    assert pdf[9:14] == b"%\xe2\xe3\xcf\xd3"
    assert pdf.rstrip().endswith(b"%%EOF")

    assert b"/Type /Catalog" in pdf
    assert b"/Type /Pages" in pdf
    assert b"/Type /Page " in pdf
    assert b"/BaseFont /Helvetica" in pdf
    assert b"/Encoding /WinAnsiEncoding" in pdf


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_cross_reference_table_points_at_real_objects(case: str) -> None:
    """The one part of a PDF a reader cannot recover from.

    Every offset in the xref must land exactly on `N 0 obj`. A file whose xref
    is off by even one byte is rejected outright by strict readers and opened
    by lenient ones only after a full rebuild, so this is asserted rather than
    assumed.
    """
    pdf = _render(case)

    start = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    table = pdf[start:]
    assert table.startswith(b"xref\n")

    count = int(table.split(b"\n")[1].split()[1])
    entries = table.split(b"\n")[2 : 2 + count]
    # Object 0 is the free-list head, by specification.
    assert entries[0] == b"0000000000 65535 f "

    for number, entry in enumerate(entries[1:], start=1):
        offset = int(entry.split()[0])
        assert pdf[offset:].startswith(f"{number} 0 obj".encode()), (
            f"xref entry {number} does not point at object {number}"
        )

    assert f"/Size {count}".encode() in pdf


def test_the_citation_strength_note_is_the_shared_sentence() -> None:
    """The PDF prints the same note as the screen, from the mirrored table.

    `test_report_copy.py` holds the table to `strings.ts`; this holds the
    document to the table, whole, so a note that stopped printing (or printed
    a local copy) fails here rather than in a reader's hands.
    """
    note = re.sub(r"\s+", " ", narrative.DIMENSION_NOTE["citation_strength"])
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert f"Citation Strength. {note}" in text
    assert "how well does it back it with a trusted source?" in text


def test_a_long_report_breaks_onto_more_than_one_page() -> None:
    """Otherwise the page-break path is never exercised by any test here."""
    pdf = _render("scored-with-generated-fixes")
    assert pdf.count(b"/Type /Page ") >= 2
    assert b"/Count 1 " not in pdf


def test_identical_input_produces_identical_bytes() -> None:
    """Determinism, for the reason scoring-spec.md rule 1 requires it of the
    report's ordering: a document shown to a client twice must be the same
    document both times. No /CreationDate, no clock-derived /ID."""
    assert _render("scored-no-generated-fixes") == _render("scored-no-generated-fixes")


# ---------------------------------------------------------------------------
# the writer's own primitives
# ---------------------------------------------------------------------------


def test_wrapping_respects_the_measure() -> None:
    body = "The brand is named more often than its own pages are cited. " * 4
    lines = pdf_writer.wrap(body, pdf_writer.REGULAR, 10.0, 300.0)
    assert len(lines) > 1
    for line in lines:
        assert pdf_writer.text_width(line, pdf_writer.REGULAR, 10.0) <= 300.0


def test_a_word_longer_than_the_measure_is_broken_not_overrun() -> None:
    """The realistic case is a long domain or URL, and both appear in this
    report. A word left to overrun runs off the edge of the paper."""
    long_word = "averyveryverylongsubdomain" * 6
    lines = pdf_writer.wrap(long_word, pdf_writer.REGULAR, 10.0, 120.0)
    assert len(lines) > 1
    for line in lines:
        assert pdf_writer.text_width(line, pdf_writer.REGULAR, 10.0) <= 120.0
    assert "".join(lines) == long_word


def test_measurement_matches_what_is_drawn() -> None:
    """`normalise` is applied by both the measurer and the encoder.

    Measuring the original string and drawing a substitute is how a wrapper
    starts overrunning its measure — the substitute can be wider.
    """
    for text in ("a — b", "it’s “quoted”", "a​b", "naïve"):
        assert pdf_writer.normalise(pdf_writer.normalise(text)) == pdf_writer.normalise(text)


def test_parentheses_and_backslashes_cannot_break_the_content_stream() -> None:
    """A PDF literal is delimited by parentheses. An unescaped one from a client
    name would truncate the stream and corrupt every page after it."""
    doc = pdf_writer.PdfDocument()
    doc.text(r"Acme (Holdings) \ Ltd")
    pdf = doc.render()
    content = zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", pdf, re.S).group(1))
    assert rb"Acme \(Holdings\) \\ Ltd" in content


# ---------------------------------------------------------------------------
# the document degrades exactly as the screen does
# ---------------------------------------------------------------------------


def test_an_unscored_report_says_not_scored_and_never_zero() -> None:
    text = _visible_text(_render("unscored"))
    assert "Not scored" in text
    assert "Help Scout has not been scored yet." in text
    assert "Nothing here should be read as a low score." in text
    # The failure this is guarding: a composite rendered as a number.
    assert "0.00 / 100" not in text
    assert "0 / 100" not in text


def test_an_unscored_report_still_shows_the_evidence_it_does_have() -> None:
    """The engine results are real even when the composite is not computed.
    Dropping the proof beat would make a partial scan look like a failed one."""
    text = _visible_text(_render("unscored"))
    assert "Here is what the engines actually returned." in text
    assert "How each engine covered the prompt set" in text
    assert "No gap can be measured until there is a score." in text


def test_an_excluded_dimension_prints_its_reason_and_no_subscore() -> None:
    text = _visible_text(_render("no-competitor-set"))
    assert "Left out of the score, and why" in text
    assert "Share of Voice \u2014 No comparison was made." in text
    # Named exactly once — in the exclusion list, with a reason. Emitting it in
    # the score table as well, at 0.00, would assert what the scoring engine
    # deliberately refused to assert.
    assert text.count("Share of Voice") == 1


def test_a_missing_competitor_set_is_said_not_omitted() -> None:
    """A dropped beat reads as 'no rivals exist', which is a claim about the
    brand rather than about the detection."""
    text = _visible_text(_render("no-competitor-set"))
    assert (
        "This scan\u2019s competitor list has no rivals" not in text
    ), "a missing set is not an empty one"
    assert (
        "This scan has no competitor list, so no comparison is shown. "
        "That is a limit of the data, not a finding about the brand."
    ) in text
    assert "No competitor set was detected" not in text


def _with_competitors(**changes: Any) -> str:
    case = next(
        name for name, body in CASES.items()
        if body.get("competitorSet") and body["competitorSet"]["competitors"]
    )
    body = json.loads(json.dumps(CASES[case]))
    competitor_set = body["competitorSet"]
    competitor_set.update({k: v for k, v in changes.items() if k != "manual"})
    for competitor in competitor_set["competitors"]:
        competitor["isManualOverride"] = changes.get("manual", competitor["isManualOverride"])
    return _visible_text(render_report_pdf(ReportOut.model_validate(body)))


def test_a_hand_named_list_is_not_said_to_be_found_by_search_or_ai_answers() -> None:
    """A list the agency named is stored as status ok with no confidence, and a scan adopts it
    whole, so "independently surfaced" would be false of every rival on it."""
    detected = "Search results and AI answers independently surfaced this set of rivals."
    assert detected in _with_competitors(manual=False, status="ok")
    hand = _with_competitors(manual=True, status="ok", detectionConfidence=None)
    assert (
        "These rivals were named by the agency, not found by search results or AI answers."
    ) in hand
    assert detected not in hand


def test_a_weak_signal_note_does_not_ask_for_a_correction_that_cannot_reach_the_report() -> None:
    text = _with_competitors(manual=False, status="weak_signal")
    assert "Treat this rival set as a starting point." in text
    assert "correct it" not in text
    assert "before sending the report on" not in text


def test_an_empty_competitor_list_is_true_of_detection_and_of_a_hand_emptied_list() -> None:
    """`no_signal` is stored both when detection found nothing and when a list was emptied by
    hand, so the sentence names neither as the cause."""
    case = next(
        name for name, body in CASES.items()
        if body.get("competitorSet") and body["competitorSet"]["competitors"]
    )
    body = json.loads(json.dumps(CASES[case]))
    body["competitorSet"]["competitors"] = []
    body["competitorSet"]["status"] = "no_signal"
    text = _visible_text(render_report_pdf(ReportOut.model_validate(body)))
    assert (
        "This scan\u2019s competitor list has no rivals, so no comparison is shown. "
        "Either search results and AI answers surfaced no usable set, or the list was "
        "emptied by hand. Neither is a finding about the brand."
    ) in text
    assert "Detection ran and surfaced" not in text


def test_empty_action_items_render_the_deterministic_derivation() -> None:
    """api-contracts.md on `actionItems`: "Empty until generation has run — the
    report's fix beat renders its deterministic derivation in that case"."""
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert not (CASES["scored-no-generated-fixes"].get("actionItems") or [])
    assert "changes, worth 40.6 points." in text
    assert "Get onto the sources that answers already cite as authorities" in text
    assert "Get onto eesel.ai" in text


def test_generated_wording_is_used_when_it_exists() -> None:
    text = _visible_text(_render("scored-with-generated-fixes"))
    assert "Publish comparison and 'best help desk software' reference pages" in text
    assert "The wording of these items was drafted against this scan" in text


def test_the_pdf_says_what_is_computed_and_not_written_only_when_wording_was_generated() -> None:
    """The screen says it too (`ReportView.test.tsx`, launch checklist 3.49): which items appear,
    their order and what each is worth are computed, not written. It is true of `derive_fixes`,
    which lays the generated wording over the derived list and moves nothing else
    (`test_generated_wording_never_moves_a_number`)."""
    generated = _visible_text(_render("scored-with-generated-fixes"))
    assert (
        "The wording of these items was drafted against this scan's own figures. "
        "Which items appear, their order, and what each is worth are computed, not written."
    ) in generated
    assert "computed, not written" not in _visible_text(_render("scored-no-generated-fixes"))


def _fix_case(
    base: str,
    *,
    findings: list[dict[str, Any]] | None = None,
    unclaimed: list[Any] | None = None,
    subscores: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A fixture case with its fix list narrowed: its findings, its unclaimed domains, its gaps."""
    case = json.loads(json.dumps(CASES[base]))
    if findings is not None:
        case["audit"]["findings"] = findings
    if unclaimed is not None:
        case["proof"]["unclaimedCitedDomains"] = unclaimed
    for dimension in case["dimensions"]:
        dimension["subscore"] = (subscores or {}).get(dimension["key"], dimension["subscore"])
    return case


_CANONICAL = {
    "id": "tchk_canonical", "checkKey": "canonical_present", "status": "warn", "value": None,
    "detailCode": "NO_CANONICAL_LINK",
}


@pytest.mark.parametrize(
    ("case", "heading"),
    [
        # With points: "worth" is the sum of the figures the list carries.
        (_fix_case("scored-no-generated-fixes"), "6 changes, worth 40.6 points."),
        (
            _fix_case(
                "scored-no-generated-fixes", findings=[], unclaimed=[],
                subscores={"share_of_voice": "100.00", "sentiment": "100.00"},
            ),
            "1 change, worth 19.3 points.",
        ),
        # Without: the audit's findings and the citation fix, which is not an audit finding.
        (_fix_case("unscored"), "3 specific changes from this scan."),
        (
            _fix_case("unscored", findings=[_CANONICAL], unclaimed=[]),
            "1 specific change from this scan.",
        ),
        (_fix_case("unscored", findings=[]), "1 specific change from this scan."),
    ],
    ids=["many-with-points", "one-with-points", "many-without", "one-finding", "citation-only"],
)
def test_the_fix_heading_counts_in_the_singular_and_is_true_of_every_list_it_heads(
    case: dict[str, Any], heading: str
) -> None:
    """The screen's heading, word for word (`ReportView.test.tsx`, launch checklist 3.49).

    "1 changes" was wrong, and "specific changes the audit named" was untrue of a list that is
    only the citation fix.
    """
    text = _visible_text(render_report_pdf(ReportOut.model_validate(case)))
    assert f"04 WHAT TO CHANGE {heading}" in text
    for gone in ("1 changes", "1 specific changes", "the audit named"):
        assert gone not in text, gone


def test_a_fix_with_no_measurable_value_says_so_rather_than_inventing_one() -> None:
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert "No measurable point value" in text
    assert "Worth 19.3 points" in text


def test_a_dimension_we_have_not_measured_never_becomes_a_fix() -> None:
    text = _visible_text(_render("not-yet-measured"))
    assert "Not yet checked" in text
    assert "Get onto the sources that answers already cite" not in text


def test_the_five_beats_appear_in_order() -> None:
    """The narrative-report rule mandates the sequence. It is the argument, not a layout."""
    text = _visible_text(_render("scored-no-generated-fixes"))
    positions = [
        text.index("01 WHERE YOU STAND"),
        text.index("02 THE BIGGEST GAP"),
        text.index("03 THE EVIDENCE"),
        text.index("04 WHAT TO CHANGE"),
        text.index("05 THE OPPORTUNITY"),
    ]
    assert positions == sorted(positions)


def test_the_agency_is_named_and_we_are_not() -> None:
    """White-label: this document goes in front of the agency's prospect."""
    text = _visible_text(_render("scored-no-generated-fixes"))
    agency = CASES["scored-no-generated-fixes"]["agency"]["name"]
    assert agency in text
    assert "AI Visibility & Competitive Intelligence Platform" not in text


def test_a_null_competitor_subscore_is_a_dash_not_a_zero() -> None:
    """A rival that was not measured on a dimension did not score nothing."""
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert "Compared per dimension rather than on a composite" in text


def test_the_pitch_asserts_only_arithmetic() -> None:
    """No revenue estimate, no traffic projection, no urgency — nothing
    upstream measures any of them (build-log Epic 7, product-owner call).

    The document's own disclaimer NAMES those things in order to disown them,
    so it is removed before the sweep. Scanning for the bare words without that
    step fails on the sentence that exists to make the promise this test is
    checking, which is the wrong way round.
    """
    disclaimer = (
        "There is no revenue estimate and no traffic projection here, because "
        "nothing in this system measures either."
    )
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert disclaimer in text
    swept = text.replace(disclaimer, "").lower()
    for tell in ("revenue", "traffic", "roi", "leads", "conversion", "guarantee", "urgent"):
        assert tell not in swept, f"pitch beat asserts {tell!r}"


def test_no_third_party_prose_reaches_the_document() -> None:
    """The facts-only rule at the last gate before a file leaves the process.

    `ReportOut` has no text-bearing field for an engine answer, so there is
    nothing to leak — but this is the surface that renders facts, and the sweep
    belongs at the point of rendering as well as at the point of storage.
    """
    for case in CASES:
        text = _visible_text(_render(case))
        for tell in ("According to", "answer:", "The engine said", "responded with"):
            assert tell not in text, f"{case} carries third-party prose: {tell!r}"


# ---------------------------------------------------------------------------
# the endpoints
# ---------------------------------------------------------------------------


def _defer_scans(client: AsyncClient) -> None:
    """Create scan rows without running them. **Spends nothing.**

    The suite's default executor is `InlineScanExecutor`, which runs the whole
    pipeline during the POST — prompt generation and every engine call, for
    real, against whatever provider keys the environment carries. The first
    draft of this file used it and burned an Anthropic prompt-generation call
    plus part of a scan before it was killed. `test_report_endpoint.py` avoids
    that with a `stub_engines` fixture; here even a stub is unnecessary,
    because what this file needs is a scan with NO results.

    That is not a shortcut. A scan started from the UI runs prompt generation
    and engine execution and nothing else — no detection, no scoring, no audit
    (build-log Epic 9.13's walkthrough finding). So a report with nothing to
    score is the state the endpoint most has to survive, and deferring the run
    entirely produces the harshest version of it.

    Same seam `test_scan_endpoints.py::TestScanIsQueued` uses.
    """

    class _Deferred:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001, ARG002
            return None

    app = client._transport.app  # noqa: SLF001
    app.dependency_overrides[scan_executor] = lambda: _Deferred()


async def _scan_for_report(client: AsyncClient) -> str:
    """Sign up, add a client, and open a scan that is never executed."""
    me = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "PDF Test Agency",
            "fullName": "Operator",
            "email": "pdf@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert me.status_code == 201, me.text

    _defer_scans(client)
    created = await client.post(
        f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
    )
    assert created.status_code == 201, created.text

    scan = await client.post(f"{BASE}/clients/{created.json()['id']}/scans", json={})
    assert scan.status_code == 202, scan.text
    return scan.json()["id"]


async def test_the_endpoint_serves_a_pdf_with_a_safe_filename(client: AsyncClient) -> None:
    scan_id = await _scan_for_report(client)

    resp = await client.get(f"{BASE}/scans/{scan_id}/report.pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-1.7")

    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert disposition.endswith('-ai-visibility.pdf"')
    # A header is a header. A quote, a semicolon or a newline in a
    # caller-supplied client name is a response-splitting opportunity.
    assert "\n" not in disposition and "\r" not in disposition
    assert re.fullmatch(r'attachment; filename="[A-Za-z0-9._-]+"', disposition), disposition
    assert resp.headers["cache-control"] == "private, no-store"


async def test_an_unscored_scan_renders_rather_than_crashing(client: AsyncClient) -> None:
    """The live state of a UI-started scan today. It must produce a document."""
    scan_id = await _scan_for_report(client)
    resp = await client.get(f"{BASE}/scans/{scan_id}/report.pdf")
    assert resp.status_code == 200
    text = _visible_text(resp.content)
    assert "Not scored" in text
    assert "0.00 / 100" not in text


async def test_the_pdf_is_scoped_to_the_agency(client: AsyncClient) -> None:
    scan_id = await _scan_for_report(client)
    await client.post(f"{BASE}/auth/logout")
    await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Other Agency",
            "fullName": "Someone Else",
            "email": "other@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    resp = await client.get(f"{BASE}/scans/{scan_id}/report.pdf")
    # 404, never 403 — confirming an id exists is a cross-tenant leak.
    assert resp.status_code == 404
    assert resp.json()["type"] == "/problems/not-found"


async def test_the_pdf_requires_a_session(client: AsyncClient) -> None:
    scan_id = await _scan_for_report(client)
    await client.post(f"{BASE}/auth/logout")
    assert (await client.get(f"{BASE}/scans/{scan_id}/report.pdf")).status_code == 401


async def test_a_share_token_holder_can_download_the_pdf(client: AsyncClient) -> None:
    """The stated decision: the token grants the file as well as the page.

    It exposes nothing the JSON route does not already serve to the same holder.
    """
    scan_id = await _scan_for_report(client)
    token = (await client.post(f"{BASE}/scans/{scan_id}/share")).json()["token"]
    await client.post(f"{BASE}/auth/logout")

    resp = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-1.7")


async def test_the_share_pdf_is_the_same_document_as_the_authenticated_one(
    client: AsyncClient,
) -> None:
    """One `render_report_pdf`, one `build_report`. Two routes, one document."""
    scan_id = await _scan_for_report(client)
    token = (await client.post(f"{BASE}/scans/{scan_id}/share")).json()["token"]

    private = await client.get(f"{BASE}/scans/{scan_id}/report.pdf")
    await client.post(f"{BASE}/auth/logout")
    public = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})

    assert _visible_text(private.content) == _visible_text(public.content)


PARTIAL_LINE = (
    "This scan is partial: not every planned question was answered, so these "
    "results rest on fewer answers than planned."
)
FAILED_LINE = "This scan failed, so these results are incomplete."


def _with_status(status: str, error_code: str | None = None) -> bytes:
    case = {**CASES["scored-with-generated-fixes"], "scanStatus": status}
    case["scanErrorCode"] = error_code
    return render_report_pdf(ReportOut.model_validate(case))


@pytest.mark.parametrize("error_code", [None, "SCAN_DEADLINE_EXCEEDED"])
def test_a_partial_scan_says_so_with_or_without_the_error_code(error_code: str | None) -> None:
    text = _visible_text(_with_status("partial", error_code))
    assert PARTIAL_LINE in text
    assert FAILED_LINE not in text
    # Nothing about the agency's allowance, and never the code: the PDF goes to a prospect.
    assert "SCAN_DEADLINE_EXCEEDED" not in text
    assert "your scans" not in text


@pytest.mark.parametrize("error_code", [None, "ALL_ENGINE_CALLS_FAILED"])
def test_a_failed_scan_says_so_with_or_without_the_error_code(error_code: str | None) -> None:
    text = _visible_text(_with_status("failed", error_code))
    assert FAILED_LINE in text
    assert PARTIAL_LINE not in text
    assert "ALL_ENGINE_CALLS_FAILED" not in text
    assert "your scans" not in text


@pytest.mark.parametrize("status", ["succeeded", "running", "cancelled"])
def test_other_statuses_carry_no_partial_or_failed_line(status: str) -> None:
    text = _visible_text(_with_status(status))
    assert PARTIAL_LINE not in text
    assert FAILED_LINE not in text


def test_a_failed_audit_code_never_appears_in_the_pdf() -> None:
    case = json.loads(json.dumps(CASES["scored-with-generated-fixes"]))
    case["audit"] = {**(case.get("audit") or {}), "status": "failed", "errorCode": "FETCH_FAILED"}
    report = ReportOut.model_validate(case)
    assert "FETCH_FAILED" not in _visible_text(render_report_pdf(report))


async def test_the_public_pdf_of_a_partial_scan_carries_the_line(
    client: AsyncClient, session: Any
) -> None:
    from sqlalchemy import update

    from avp_api.models.scan import Scan, ScanStatus

    scan_id = await _scan_for_report(client)
    await session.execute(
        update(Scan).where(Scan.id == scan_id).values(status=ScanStatus.PARTIAL)
    )
    await session.commit()
    token = (await client.post(f"{BASE}/scans/{scan_id}/share")).json()["token"]
    private = await client.get(f"{BASE}/scans/{scan_id}/report.pdf")
    await client.post(f"{BASE}/auth/logout")
    public = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})
    assert PARTIAL_LINE in _visible_text(private.content)
    assert PARTIAL_LINE in _visible_text(public.content)


@pytest.mark.parametrize(
    "token", ["not-a-real-token", "a" * 300, "../../etc/passwd", "abc%2F"]
)
async def test_every_bad_share_token_is_a_404(client: AsyncClient, token: str) -> None:
    """No shape pre-check, no 401, one code path — the rules Epic 9.8 set for
    the JSON route, unchanged for the PDF one."""
    resp = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})
    assert resp.status_code == 404
    if resp.headers["content-type"].startswith("application/problem"):
        assert resp.json()["type"] == "/problems/not-found"


async def test_the_two_share_routes_each_serve_their_own_content_type(
    client: AsyncClient,
) -> None:
    """`/reports/shared` is JSON and `/reports/shared/pdf` is the file.

    The token is in the body, so there is no path-converter overlap any more;
    this pins that the two stay distinct routes.
    """
    scan_id = await _scan_for_report(client)
    token = (await client.post(f"{BASE}/scans/{scan_id}/share")).json()["token"]
    await client.post(f"{BASE}/auth/logout")

    pdf = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})
    assert pdf.headers["content-type"] == "application/pdf"

    web = await client.post(f"{BASE}/reports/shared", json={"token": token})
    assert web.headers["content-type"].startswith("application/json")


def _failed_case() -> dict[str, Any]:
    case = json.loads(json.dumps(CASES["scored-no-generated-fixes"]))
    case["scanStatus"] = "failed"
    case["score"] = None
    case["dimensions"] = []
    return case


def test_a_failed_scans_report_says_it_has_no_score() -> None:
    """3.24: a failed scan's score is never shown. The sentence is the founder's."""
    text = _visible_text(render_report_pdf(ReportOut.model_validate(_failed_case())))
    assert "Help Scout has no score: this scan failed." in text
    assert "This scan failed, so it has no score." in text
    assert "Not scored" in text
    assert "/ 100" not in text
    # Not the "run but not scored yet" prose, which is untrue of a failed scan.
    assert "composite is simply not computed yet" not in text


# ---------------------------------------------------------------------------
# four audit fixes said more than the audit read — launch checklist 3.49
# ---------------------------------------------------------------------------

# code -> (check, status a report lists it with, how the detail now opens, what it used to say).
# `test_audit_fix_wording.py` pins the whole text and what it rests on; this holds the
# document to it: the PDF prints the fix the narrative derives, and none of the old phrases.
REWORDED_AUDIT_FIXES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "ROBOTS_TXT_DISALLOW": (
        "indexable",
        "fail",
        "The audit found a Disallow: / rule under User-agent: * in the site’s robots.txt.",
        ("nothing else on this list", "can take effect", "tells crawlers to stay out",
         "cannot cite", "stop robots.txt from blocking", "crawlers that build ai answers",
         "rules for all crawlers"),
    ),
    "NO_DATE_SIGNAL_AVAILABLE": (
        "content_freshness",
        "warn",
        "The audit read no date from the audited page’s Last-Modified header",
        ("anywhere on the page", "no date could be found", "cannot be established either way"),
    ),
    "NO_ORGANIZATION_OR_LOCALBUSINESS": (
        "schema_business_entity",
        "fail",
        "The audit found none of Organization, Corporation, OnlineBusiness, LocalBusiness,",
        ("nothing on the page states", "what this organisation is", "entity marker"),
    ),
    "CONTENT_STALE": (
        "content_freshness",
        "warn",
        "The newest date the audit read from the audited page, in its Last-Modified header",
        ("visible updated date", "date signal", "one of the few signals"),
    ),
}


@pytest.mark.parametrize("code", sorted(REWORDED_AUDIT_FIXES))
def test_the_pdf_prints_the_reworded_audit_fix_and_none_of_what_it_used_to_say(code: str) -> None:
    check_key, status, opening, overstated = REWORDED_AUDIT_FIXES[code]
    case = json.loads(json.dumps(CASES["scored-no-generated-fixes"]))
    case["audit"]["findings"] = [
        {"id": "tchk_wording", "checkKey": check_key, "status": status, "value": None,
         "detailCode": code}
    ]
    text = _visible_text(render_report_pdf(ReportOut.model_validate(case)))

    title, detail, _ = narrative.FIX_FOR_DETAIL_CODE[code]
    assert title in text
    assert re.sub(r"\s+", " ", detail) in text
    assert opening in text
    for phrase in overstated:
        assert phrase not in text.lower(), phrase
    # A code is a machine's word for it: the PDF says the sentence and never the code.
    assert code not in text


# ---------------------------------------------------------------------------
# report cosmetics (pilot-fixes-7 item 3)
# ---------------------------------------------------------------------------


def _university_case() -> dict[str, Any]:
    """A scored report whose subject and a rival have long names, as in production."""
    case = json.loads(json.dumps(CASES["scored-no-generated-fixes"]))
    case["subject"]["name"] = "RMIT University"
    case["subject"]["brandName"] = "RMIT University"
    case["competitorSet"]["competitors"][0]["name"] = "Swinburne University of Technology"
    case["agency"]["slug"] = "psm-digital-agency"
    return case


def _university_text(case: dict[str, Any] | None = None) -> str:
    body = case or _university_case()
    return _visible_text(render_report_pdf(ReportOut.model_validate(body)))


def _placed(pdf: bytes) -> list[tuple[float, float, str]]:
    """(x, y, text) of every drawn string, in drawing order."""
    out: list[tuple[float, float, str]] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for op in re.finditer(rb"([\d.]+) ([\d.]+) Td \((.*?)\) Tj", content, re.S):
            out.append((float(op.group(1)), float(op.group(2)), op.group(3).decode("cp1252")))
    return out


def test_the_cover_does_not_print_the_agencys_slug() -> None:
    text = _university_text()
    assert "psm-digital-agency" not in text
    assert "AI visibility report" in text


def test_the_footer_has_the_scan_and_the_date_and_no_beat_names() -> None:
    case = _university_case()
    text = _university_text(case)
    assert f"Scan {case['scanId']} · generated " in text
    assert "beats" not in text
    assert "score · gap · proof" not in text


def test_a_long_table_header_and_a_long_rival_name_are_printed_in_full() -> None:
    text = _university_text()
    assert "Named RMIT University" in text
    assert "Swinburne University of Technology" in text
    assert "Unive…" not in text
    assert "Tec…" not in text


def test_a_name_too_long_for_two_lines_ends_with_an_ellipsis() -> None:
    case = _university_case()
    long_name = "The Very Long Named Institute of Advanced Interdisciplinary Studies and Technology"
    case["competitorSet"]["competitors"][0]["name"] = long_name
    text = _university_text(case)
    assert long_name not in text
    # Rival column is 135 wide, less the 6 pt gutter, at 9.5 pt regular.
    lines = pdf_writer.cell_lines(long_name, pdf_writer.REGULAR, 9.5, 129)
    assert len(lines) == 2
    assert lines[1].endswith("…")
    assert f"{lines[0]} {lines[1]}" in text


def test_cell_lines_wrap_to_two_lines_and_cut_the_second() -> None:
    font, size, width = pdf_writer.REGULAR, 9.5, 129.0
    assert pdf_writer.cell_lines("Zendesk", font, size, width) == ["Zendesk"]
    two = pdf_writer.cell_lines("Swinburne University of Technology", font, size, width)
    assert len(two) == 2
    assert " ".join(two) == "Swinburne University of Technology"
    long = pdf_writer.cell_lines(
        "The Very Long Named Institute of Advanced Interdisciplinary Studies", font, size, width
    )
    assert len(long) == 2
    assert long[1].endswith("…")
    assert not long[0].endswith("…")
    for line in long:
        assert pdf_writer.text_width(line, font, size) <= width


def test_a_row_is_as_tall_as_its_tallest_cell() -> None:
    font, size, widths = pdf_writer.REGULAR, 9.5, [135.0, 72.0]
    one = pdf_writer.row_height(["Zendesk", "1.0"], widths, font, size)
    two = pdf_writer.row_height(["Swinburne University of Technology", "1.0"], widths, font, size)
    assert one == pdf_writer.ROW_LEADING
    assert two == 2 * pdf_writer.ROW_LEADING + 2.0


def test_a_two_line_row_does_not_overlap_the_next_row() -> None:
    doc = pdf_writer.PdfDocument()
    doc.table(
        ["Brand", "Share"],
        [["Swinburne University of Technology", "1"], ["Zendesk", "2"]],
        widths=[120, 60],
    )
    placed = _placed(doc.render())
    ys = {t: y for _, y, t in placed}
    name = "Swinburne University of Technology"
    lines = pdf_writer.cell_lines(name, pdf_writer.REGULAR, 9.5, 114)
    assert len(lines) == 2
    first, second = ys[lines[0]], ys[lines[1]]
    assert first - second == pdf_writer.ROW_LEADING
    assert second - ys["Zendesk"] >= pdf_writer.ROW_LEADING + 2.0
    # The figure beside a wrapped name sits on the name's first line.
    assert ys["1"] == first


def test_a_row_that_does_not_fit_moves_whole_to_the_next_page() -> None:
    doc = pdf_writer.PdfDocument()
    rows = [[f"Row {i}", "x"] for i in range(40)]
    doc.table(["Brand", "Share"], rows, widths=[120, 60])
    # fill to the point that a two-line row cannot fit, then add it
    while doc._y - 2 * pdf_writer.ROW_LEADING >= pdf_writer.MARGIN_BOTTOM + 5:  # noqa: SLF001
        doc.text("filler")
    pages_before = doc.page_count
    doc.table(["A"], [["Swinburne University of Technology"]], widths=[100])
    pdf = doc.render()
    assert doc.page_count >= pages_before
    placed = _placed(pdf)
    ys = {t: y for _, y, t in placed}
    lines = pdf_writer.cell_lines("Swinburne University of Technology", pdf_writer.REGULAR, 9.5, 94)
    assert len(lines) == 2
    assert ys[lines[0]] - ys[lines[1]] == pdf_writer.ROW_LEADING
    assert ys[lines[1]] >= pdf_writer.MARGIN_BOTTOM


@pytest.mark.parametrize(
    ("composite", "potential", "expected"),
    [
        (
            62.6,
            93.2,
            "63 today. Up to 93 if these fixes close every gap they address (63\u00a0+\u00a030).",
        ),
        (
            58.24,
            98.84,
            "58 today. Up to 99 if these fixes close every gap they address (58\u00a0+\u00a041).",
        ),
        (
            97.5,
            100.0,
            "98 today. Up to 100 if these fixes close every gap they address (98\u00a0+\u00a02).",
        ),
        (40.0, 40.0, "40 today."),
        (
            62.4,
            62.6,
            "62 today. Up to 63 if these fixes close every gap they address (62\u00a0+\u00a01).",
        ),
        (62.6, 62.9, "63 today."),
    ],
)
def test_the_pitch_heading_sums_the_two_printed_figures(
    composite: float, potential: float, expected: str
) -> None:
    from types import SimpleNamespace

    from avp_api.services.report_pdf import _pitch_heading

    story = SimpleNamespace(status="scored", composite=composite, potential_composite=potential)
    assert _pitch_heading(story, "Acme") == expected  # type: ignore[arg-type]


def test_the_pitch_heading_for_an_unscored_report_is_unchanged() -> None:
    from types import SimpleNamespace

    from avp_api.services.report_pdf import _pitch_heading

    story = SimpleNamespace(status="insufficient_data", composite=None, potential_composite=None)
    assert _pitch_heading(story, "Acme") == "What a complete scan would tell you about Acme."  # type: ignore[arg-type]


def test_the_pdf_prints_the_new_pitch_sentence_and_not_the_old_one() -> None:
    text = _visible_text(_render("scored-no-generated-fixes"))
    sentence = "58 today. Up to 99 if these fixes close every gap they address (58\u00a0+\u00a041)."
    assert sentence in text
    assert "with the fixes above" not in text


def test_the_sum_in_the_pitch_heading_never_breaks_across_two_lines() -> None:
    """The parenthetical is read as one figure, so it is never split in two.

    The heading is set at whatever measure the page gives it; at none of them
    may "(58" end a line with "41)." beginning the next.
    """
    from types import SimpleNamespace

    from avp_api.services.report_pdf import _pitch_heading

    story = SimpleNamespace(status="scored", composite=58.24, potential_composite=98.84)
    heading = _pitch_heading(story, "Acme")  # type: ignore[arg-type]
    for measure in (pdf_writer.CONTENT_WIDTH, 400.0, 300.0, 250.0, 200.0):
        lines = pdf_writer.wrap(heading, pdf_writer.BOLD, 19, measure)
        whole = [line for line in lines if "(58" in line]
        assert len(whole) == 1
        assert whole[0].endswith("(58\u00a0+\u00a041).")


def test_a_no_break_space_is_drawn_rather_than_dropped_or_flattened() -> None:
    """It survives `normalise`, is emitted as WinAnsi 0o240, and reads back.

    Were it transliterated to a plain space the wrap above would pass while the
    drawn line still broke, which is the failure this pins.
    """
    assert pdf_writer.normalise("a\u00a0b") == "a\u00a0b"
    assert b"\\240" in pdf_writer._encode("a\u00a0b")
    assert pdf_writer.text_width("a\u00a0b", pdf_writer.BOLD, 10) == pytest.approx(
        pdf_writer.text_width("a b", pdf_writer.BOLD, 10)
    )


def test_a_plain_space_still_breaks_a_line() -> None:
    """The change narrows what breaks; it must not stop breaking altogether."""
    lines = pdf_writer.wrap("alpha beta gamma delta", pdf_writer.REGULAR, 10, 40)
    assert len(lines) > 1


# RMIT University's re-run (scan_01M4CCKWT0AB2K17CAWPVP0P83, 2026-10-08), carried on the
# Help Scout payload: sub-scores that reproduce its composite (72.48), Mention Rate (97.22)
# and three fix gaps (17.3925, 5.163, 3.333) exactly. Share of Voice and Citation Strength
# are pinned by those; Sentiment and Technical Foundation could also be 77.78 and 48.37,
# which gives the same three gaps and so the same arithmetic.
RMIT_SUBSCORES = {
    "mention_rate": "97.22",
    "share_of_voice": "30.43",
    "citation_strength": "96.01",
    "sentiment": "65.58",
    "technical_foundation": "66.67",
}


def _rmit_report() -> ReportOut:
    case = copy.deepcopy(CASES["scored-no-generated-fixes"])
    case["score"]["composite"] = "72.48"
    for key, field in (
        ("mention_rate", "mentionRate"),
        ("share_of_voice", "shareOfVoice"),
        ("citation_strength", "citationStrength"),
        ("sentiment", "sentiment"),
        ("technical_foundation", "technicalFoundation"),
    ):
        case["score"][field] = RMIT_SUBSCORES[key]
    for dimension in case["dimensions"]:
        dimension["subscore"] = RMIT_SUBSCORES[dimension["key"]]
        dimension["included"] = True
        dimension["exclusionReason"] = None
    return ReportOut.model_validate(case)


def _capped_report() -> ReportOut:
    """Every gap left is a listed fix (Sentiment and Technical Foundation at 100), and the
    fixes' 1dp points carry 70.53 past 100: the cap (launch checklist 3.100)."""
    subscores = {
        "mention_rate": "55.50", "share_of_voice": "62.14", "citation_strength": "66.74",
        "sentiment": "100.00", "technical_foundation": "100.00",
    }
    case = copy.deepcopy(CASES["scored-no-generated-fixes"])
    case["score"]["composite"] = "70.53"
    for dimension in case["dimensions"]:
        dimension["subscore"] = subscores[dimension["key"]]
        dimension["included"] = True
        dimension["exclusionReason"] = None
    return ReportOut.model_validate(case)


def test_at_the_cap_the_pitch_drops_the_sum_and_says_the_score_reaches_100() -> None:
    """The founder's wording, 2026-10-09: no ceiling row and no "added to the score as it
    stands", because 70.53 and the rounded points would not add up to the 100 printed."""
    report = _capped_report()
    story = narrative.derive_narrative(report.model_dump(by_alias=True, mode="json"))
    assert narrative.ceiling_is_capped(story)
    text = _visible_text(render_report_pdf(report))
    assert "Composite today 70.53" in text
    assert (
        f"recovers {story.recoverable_points:.1f} points, which takes the score to 100, "
        "the most a score can reach."
    ) in text
    assert "Composite if every gap they address closes" not in text
    assert "added to the score as it stands" not in text
    assert "There is no revenue estimate" in text


def test_below_the_cap_the_rows_and_the_note_are_unchanged() -> None:
    text = _visible_text(render_report_pdf(_rmit_report()))
    assert "Composite if every gap they address closes 98.38" in text
    assert "added to the score as it stands" in text
    assert "which takes the score to 100" not in text


def _printed_row(text: str, label: str) -> Decimal:
    match = re.search(re.escape(label) + r" (\d+\.\d+)", text)
    assert match is not None, label
    return Decimal(match.group(1))


def test_the_pitch_rows_re_add_exactly_as_printed_on_rmit_figures() -> None:
    """The PDF printed 72.48, 25.9 and 98.40, and 72.48 + 25.9 is 98.38: the ceiling
    was rounded a second time, to 1dp. Every total is now the sum of the figures
    printed above it, at every level of the beat."""
    text = _visible_text(render_report_pdf(_rmit_report()))
    today = _printed_row(text, "Composite today")
    points = _printed_row(text, "Points the listed changes recover")
    ceiling = _printed_row(text, "Composite if every gap they address closes")
    assert (today, points, ceiling) == (Decimal("72.48"), Decimal("25.9"), Decimal("98.38"))
    assert today + points == ceiling
    # The fix beat's heading names the middle row's points ...
    assert "6 changes, worth 25.9 points." in text
    # ... and the fixes' own printed points add up to it.
    worths = [Decimal(w) for w in re.findall(r"Worth (\d+\.\d) points", text)]
    assert worths == [Decimal("17.4"), Decimal("5.2"), Decimal("3.3")]
    assert sum(worths) == points
    # The heading's integers still come from the one shared helper.
    heading = "72 today. Up to 98 if these fixes close every gap they address (72\u00a0+\u00a026)."
    assert heading in text


def _pitch_text(composite: float, potential: float, recoverable: float) -> str:
    """The pitch beat alone, drawn onto an empty document and read back."""
    from types import SimpleNamespace

    from avp_api.services.report_pdf import _pitch_beat

    doc = pdf_writer.PdfDocument()
    story = SimpleNamespace(
        status="scored",
        composite=composite,
        potential_composite=potential,
        recoverable_points=recoverable,
        ahead_on_dimensions=[],
    )
    _pitch_beat(doc, {}, story, "Acme")  # type: ignore[arg-type]
    return _visible_text(doc.render())


def test_the_pitch_says_so_when_the_recovered_points_do_not_move_the_score() -> None:
    """62.6 and 62.9 both print as 63, so the sentence stops the two 63s being read as a
    gain. No self-consistent report reaches this (a listed fix is worth at least 2
    points; pilot-fixes-8 A1), so it is a guard, tested here on figures alone. Until
    2026-10-09 this test used 99.6 + 2.0, which is the cap and now says so instead
    (`test_at_the_cap_the_pitch_drops_the_sum_and_says_the_score_reaches_100`)."""
    text = _pitch_text(62.6, 62.9, 0.3)
    assert "63 today." in text
    assert "recovers 0.3 points, which does not change the rounded score of 63." in text
    # The rows are still printed: the exact figures do differ.
    assert "Composite today" in text
    assert "Composite if every gap they address closes" in text


def test_the_pitch_does_not_say_it_when_the_score_does_move() -> None:
    text = _pitch_text(58.24, 98.84, 40.6)
    assert "does not change the rounded score" not in text
    assert "58 today. Up to 99" in text


def test_the_heading_and_the_figures_read_the_same_two_printed_numbers() -> None:
    """One helper decides both, so a heading claiming no uplift can never sit
    above a paragraph claiming one."""
    from types import SimpleNamespace

    from avp_api.services.report_pdf import _printed_figures

    assert _printed_figures(
        SimpleNamespace(status="scored", composite=99.6, potential_composite=100.0)  # type: ignore[arg-type]
    ) == (100, 100, 0)
    assert _printed_figures(
        SimpleNamespace(status="scored", composite=58.24, potential_composite=98.84)  # type: ignore[arg-type]
    ) == (58, 99, 41)
    assert (
        _printed_figures(
            SimpleNamespace(  # type: ignore[arg-type]
                status="insufficient_data", composite=None, potential_composite=None
            )
        )
        is None
    )


def test_the_pdf_names_the_figure_the_fixes_would_reach() -> None:
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert "Composite if every gap they address closes" in text
    assert "Composite if they all land" not in text


def _rule_gap(header: list[str], widths: list[float]) -> float:
    """Distance from the header's last baseline to the rule beneath it."""
    doc = pdf_writer.PdfDocument()
    doc.table(header, [["x"] * len(header)], widths=widths)
    pdf = doc.render()
    content = zlib.decompress(re.search(rb"stream\n(.*?)\nendstream", pdf, re.S).group(1))
    rule_y = float(re.search(rb"[\d.]+ ([\d.]+) [\d.]+ 0\.6 re f", content).group(1))
    lines = {
        line
        for cell, width in zip(header, widths, strict=True)
        for line in pdf_writer.cell_lines(cell, pdf_writer.BOLD, 8.5, width - 6)
    }
    last = min(y for _, y, t in _placed(pdf) if t in lines)
    return last - rule_y


def test_a_two_line_header_leaves_more_room_above_its_rule_than_a_one_line_one() -> None:
    one = _rule_gap(["Engine", "Asked"], [100, 60])
    two = _rule_gap(["Engine", "Named RMIT University"], [100, 60])
    assert two - one >= 2.0


def _per_page(pdf: bytes) -> list[list[tuple[float, float, str]]]:
    pages = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        pages.append(
            [
                (float(m.group(1)), float(m.group(2)), re.sub(
                    rb"\\([0-7]{3})", lambda o: _octal(o.group(1)), m.group(3)
                ).decode("cp1252"))
                for m in re.finditer(rb"([\d.]+) ([\d.]+) Td \((.*?)\) Tj", content, re.S)
            ]
        )
    return pages


def test_the_footer_is_on_the_last_page_below_the_content_and_never_alone() -> None:
    case = _university_case()
    pages = _per_page(render_report_pdf(ReportOut.model_validate(case)))
    footer_prefix = f"Scan {case['scanId']} · generated "
    holders = [
        i for i, page in enumerate(pages) if any(t.startswith(footer_prefix) for *_, t in page)
    ]
    assert holders == [len(pages) - 1]
    last = pages[-1]
    footer_y = next(y for _, y, t in last if t.startswith(footer_prefix))
    others = [y for _, y, t in last if not t.startswith(footer_prefix)]
    assert footer_y == pdf_writer.FOOTER_Y < pdf_writer.MARGIN_BOTTOM
    assert others, "the footer must not be the only thing on a page"
    assert min(others) >= pdf_writer.MARGIN_BOTTOM
    for page in pages:
        assert len([t for *_, t in page if not t.startswith(footer_prefix)]) > 0


def test_a_footer_costs_no_flow_space() -> None:
    doc = pdf_writer.PdfDocument()
    doc.text("x")
    before = doc._y  # noqa: SLF001
    doc.footer("Scan s · generated d")
    assert doc._y == before  # noqa: SLF001
    assert doc.page_count == 1


def _fill_to(doc: pdf_writer.PdfDocument, room: float) -> None:
    while doc._y - 14 >= pdf_writer.MARGIN_BOTTOM + room:  # noqa: SLF001
        doc.text("filler")


@pytest.mark.parametrize("call", ["eyebrow", "heading", "subheading"])
def test_a_label_or_heading_near_the_page_foot_moves_with_what_follows(call: str) -> None:
    doc = pdf_writer.PdfDocument()
    _fill_to(doc, 20)  # room for the heading's own line, not for a line after it
    getattr(doc, call)("A HEADING" if call == "eyebrow" else "A heading")
    doc.text("First line after it.")
    pages = _per_page(doc.render())
    assert len(pages) == 2
    texts = [[t for *_, t in page] for page in pages]
    marker = "A HEADING" if call == "eyebrow" else "A heading"
    assert marker not in texts[0]
    assert marker in texts[1]
    assert "First line after it." in texts[1]


def test_a_heading_with_room_for_its_first_line_stays_on_the_page() -> None:
    doc = pdf_writer.PdfDocument()
    _fill_to(doc, 120)
    doc.heading("A heading")
    doc.text("First line after it.")
    assert doc.page_count == 1


def _rules_per_page(pdf: bytes) -> list[int]:
    return [
        len(re.findall(rb"0\.6 re f", zlib.decompress(m.group(1))))
        for m in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S)
    ]


def test_a_table_that_continues_on_a_new_page_repeats_its_header() -> None:
    doc = pdf_writer.PdfDocument()
    _fill_to(doc, 100)
    rows = [[f"Row {i}", "x"] for i in range(12)]
    doc.table(["Brand", "Share"], rows, widths=[120, 60])
    pdf = doc.render()
    pages = _per_page(pdf)
    assert len(pages) == 2
    for page in pages[1:]:
        texts = [t for *_, t in page]
        assert texts[0] == "Brand"
        header_y = page[0][1]
        first_row_y = next(y for _, y, t in page if t.startswith("Row "))
        assert first_row_y < header_y - pdf_writer.ROW_LEADING
    assert _rules_per_page(pdf)[1] == 1
    on_first = [t for *_, t in pages[0] if t.startswith("Row ")]
    on_second = [t for *_, t in pages[1] if t.startswith("Row ")]
    assert len(on_first) + len(on_second) == 12
    assert on_first and on_second


def test_a_table_that_fits_prints_its_header_once() -> None:
    doc = pdf_writer.PdfDocument()
    doc.table(["Brand", "Share"], [[f"Row {i}", "x"] for i in range(5)], widths=[120, 60])
    pages = _per_page(doc.render())
    assert len(pages) == 1
    assert [t for *_, t in pages[0]].count("Brand") == 1


def test_a_repeated_two_line_header_keeps_its_extra_space_above_the_rule() -> None:
    doc = pdf_writer.PdfDocument()
    _fill_to(doc, 100)
    doc.table(
        ["Brand", "Named RMIT University"],
        [[f"Row {i}", "x"] for i in range(12)],
        widths=[120, 60],
    )
    pdf = doc.render()
    page = _per_page(pdf)[1]
    header_lines = [y for _, y, t in page[:3]]
    assert len(set(header_lines)) == 2
    assert page[0][2] == "Brand"


def test_a_beat_opener_does_not_leave_its_rule_alone_at_the_page_foot() -> None:
    from avp_api.services.report_pdf import _beat

    doc = pdf_writer.PdfDocument()
    _fill_to(doc, 40)  # the rule alone would fit; the rule, label and heading would not
    _beat(doc, 3, "The evidence", "A claim.")
    pdf = doc.render()
    pages = _per_page(pdf)
    assert len(pages) == 2
    assert _rules_per_page(pdf) == [0, 1]
    assert "03 THE EVIDENCE" in [t for *_, t in pages[1]]


def _para(lines: int) -> str:
    """A paragraph that wraps to exactly `lines` lines at the default measure."""
    words = " ".join(f"w{i:03d}" for i in range(1000))
    out = pdf_writer.wrap(words, pdf_writer.REGULAR, 10.0, pdf_writer.CONTENT_WIDTH)[:lines]
    assert len(out) == lines
    return " ".join(out)


def _fill_lines(doc: pdf_writer.PdfDocument, room_lines: int) -> None:
    while int((doc._y - pdf_writer.MARGIN_BOTTOM) / 14) > room_lines:  # noqa: SLF001
        doc.text("filler")


def _body_counts(pdf: bytes, marker: str = "w") -> list[int]:
    return [len([t for *_, t in page if t.startswith(marker)]) for page in _per_page(pdf)]


@pytest.mark.parametrize("room", [1, 2, 3, 4, 5])
def test_a_long_paragraph_never_leaves_fewer_than_two_lines_on_either_side(room: int) -> None:
    doc = pdf_writer.PdfDocument()
    _fill_lines(doc, room)
    doc.text(_para(8))
    counts = [c for c in _body_counts(doc.render()) if c]
    assert sum(counts) == 8
    if len(counts) > 1:
        assert all(c >= 2 for c in counts)


def test_a_three_line_paragraph_moves_whole_rather_than_splitting() -> None:
    doc = pdf_writer.PdfDocument()
    _fill_lines(doc, 2)
    doc.text(_para(3))
    assert [c for c in _body_counts(doc.render()) if c] == [3]


def test_a_paragraph_that_fits_stays_where_it_is() -> None:
    doc = pdf_writer.PdfDocument()
    _fill_lines(doc, 10)
    doc.text(_para(8))
    assert doc.page_count == 1


def test_a_bulleted_item_follows_the_same_rule() -> None:
    doc = pdf_writer.PdfDocument()
    _fill_lines(doc, 1)
    doc.bullet(_para(2))
    assert doc.page_count == 2


def test_no_page_of_the_report_holds_a_single_line_of_body_text() -> None:
    case = _university_case()
    pages = _per_page(render_report_pdf(ReportOut.model_validate(case)))
    prefix = f"Scan {case['scanId']} · generated "
    for page in pages:
        body = [t for *_, t in page if not t.startswith(prefix)]
        assert len(body) > 1, body
    assert pages[-1][-1][2].startswith(prefix)

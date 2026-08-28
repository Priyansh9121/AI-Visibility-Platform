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

import json
import re
import zlib
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from avp_api.deps import scan_executor
from avp_api.schemas.report import ReportOut
from avp_api.services import pdf as pdf_writer
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
    return re.sub(r"\s+", " ", " ".join(out))


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
    assert "No competitor set was detected for this scan" in text
    assert "a limit of the detection, not a finding about the brand" in text


def test_empty_action_items_render_the_deterministic_derivation() -> None:
    """api-contracts.md on `actionItems`: "Empty until generation has run — the
    report's fix beat renders its deterministic derivation in that case"."""
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert not (CASES["scored-no-generated-fixes"].get("actionItems") or [])
    assert "changes, worth 40.6 points." in text
    assert "Make the site the source an answer cites, not just a name it mentions" in text
    assert "Get onto eesel.ai" in text


def test_generated_wording_is_used_when_it_exists() -> None:
    text = _visible_text(_render("scored-with-generated-fixes"))
    assert "Publish comparison and 'best help desk software' reference pages" in text
    assert "The wording of these items was drafted against this scan" in text


def test_a_fix_with_no_measurable_value_says_so_rather_than_inventing_one() -> None:
    text = _visible_text(_render("scored-no-generated-fixes"))
    assert "No measurable point value" in text
    assert "Worth 19.3 points" in text


def test_a_dimension_we_have_not_measured_never_becomes_a_fix() -> None:
    text = _visible_text(_render("not-yet-measured"))
    assert "Not yet checked" in text
    assert "Make the site the source an answer cites" not in text


def test_the_five_beats_appear_in_order() -> None:
    """ip-safety.md #3 mandates the sequence. It is the argument, not a layout."""
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
    """ip-safety.md #7 at the last gate before a file leaves the process.

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

    resp = await client.get(f"{BASE}/reports/{token}.pdf")
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
    public = await client.get(f"{BASE}/reports/{token}.pdf")

    assert _visible_text(private.content) == _visible_text(public.content)


@pytest.mark.parametrize(
    "token", ["not-a-real-token", "", "a" * 300, "../../etc/passwd", "abc%2F"]
)
async def test_every_bad_share_token_is_a_404(client: AsyncClient, token: str) -> None:
    """No shape pre-check, no 401, one code path — the rules Epic 9.8 set for
    the JSON route, unchanged for the PDF one."""
    resp = await client.get(f"{BASE}/reports/{token}.pdf")
    assert resp.status_code == 404
    if resp.headers["content-type"].startswith("application/problem"):
        assert resp.json()["type"] == "/problems/not-found"


async def test_the_pdf_route_is_not_swallowed_by_the_json_route(
    client: AsyncClient,
) -> None:
    """Starlette's path converter matches `.` too.

    `/reports/{token}` will happily match `/reports/abc.pdf` with a token of
    "abc.pdf", and routes are tried in registration order — so declared the
    wrong way round, every PDF request silently became a JSON request for a
    token nobody minted. Asserted here rather than left to the file's shape.
    """
    scan_id = await _scan_for_report(client)
    token = (await client.post(f"{BASE}/scans/{scan_id}/share")).json()["token"]
    await client.post(f"{BASE}/auth/logout")

    pdf = await client.get(f"{BASE}/reports/{token}.pdf")
    assert pdf.headers["content-type"] == "application/pdf"

    web = await client.get(f"{BASE}/reports/{token}")
    assert web.headers["content-type"].startswith("application/json")

"""The report, as a PDF — Epic 9.14 (Epic 9 send path, slice 3).

A SECOND RENDERING TARGET, NOT A SECOND REPORT
----------------------------------------------
This module renders exactly what `build_report` already assembles. It does not
query, aggregate, score, or decide anything: it takes `ReportOut`, dumps it to
the same camelCase JSON the browser receives, and lays it out. `report_narrative`
then derives the same argument the screen derives — and is held to that by a
cross-language fixture test, because a PDF that reasoned differently from the
page it was downloaded from would be the worst possible outcome of building one.

THE FIVE BEATS, IN ORDER
------------------------
score → gap → proof → fix → pitch, the sequence ip-safety.md #3 mandates and
`BEAT_SEQUENCE` fixes. The PDF is not a data dump with the same numbers in it;
it is the same argument on paper, so the order is not a display preference.

DEGRADED STATES ARE RENDERED, NOT SMOOTHED
------------------------------------------
Everything the screen refuses to fake, this refuses to fake:

  * a null composite prints **"Not scored"**, never 0 and never a dash that
    could read as a value
  * `insufficient_data` is its own sentence, distinct from never-scored
  * an excluded dimension prints its reason and no sub-score, never a zero
  * a null competitor set says detection did not run, rather than omitting the
    beat and letting the absence read as "no rivals exist"
  * an empty `actionItems` list renders the deterministic derivation, exactly
    as api-contracts.md's note on that field says the fix beat does

ip-safety.md #7: every value written here is a name, a domain, a URL, a count,
an ordinal, a machine code resolved through OUR OWN string table, or a number.
`ReportOut` has no field capable of carrying an engine's prose, so there is
nothing for this to leak even by accident — and `test_report_pdf.py` asserts it.
"""

from __future__ import annotations

from typing import Any

from ..schemas.report import ReportOut
from . import report_narrative as narrative
from .pdf import BOLD, ITALIC, REGULAR, PdfDocument


def render_report_pdf(report: ReportOut) -> bytes:
    """Render a report to PDF bytes.

    Takes the assembled `ReportOut` rather than a scan id, so this function has
    no database access and cannot become a second assembly path even by
    accident.
    """
    data: dict[str, Any] = report.model_dump(by_alias=True, mode="json")
    story = narrative.derive_narrative(data)

    subject = data["subject"]
    subject_name = subject.get("brandName") or subject["name"]

    doc = PdfDocument()
    _cover(doc, data, subject_name)
    _score_beat(doc, data, story, subject_name)
    _gap_beat(doc, story, subject_name)
    _proof_beat(doc, data, subject_name)
    _fix_beat(doc, story)
    _pitch_beat(doc, data, story, subject_name)
    _colophon(doc, data)
    return doc.render()


# ---------------------------------------------------------------------------
# cover
# ---------------------------------------------------------------------------


def _cover(doc: PdfDocument, data: dict[str, Any], subject_name: str) -> None:
    """The white-label surface. The AGENCY's identity, never ours.

    Name and slug only, which is what the data model holds today — Epic 7 shipped
    white-labelling limited to those two fields and logo/domain/colour injection
    is still blocked on a written token-override policy. So the PDF carries
    exactly what the web report carries, and nothing is invented to fill a
    letterhead.
    """
    agency = data["agency"]
    doc.text(agency["name"], font=BOLD, size=11, leading=15)
    doc.text(
        f"{agency['slug']} · AI visibility report",
        font=REGULAR,
        size=8,
        leading=12,
        grey=True,
    )
    doc.rule(space_above=8, space_below=14)

    doc.text(subject_name, font=BOLD, size=24, leading=30)
    doc.text(
        f"How {subject_name} appears when buyers ask AI assistants for a recommendation.",
        size=11,
        leading=16,
        space_after=6,
    )

    proof = data["proof"]
    meta = [
        subject["domain"] if (subject := data["subject"]) else "",
        subject.get("industry") or "Industry not determined",
        f"{proof['promptsRun']} prompts",
        f"{len(proof['engineCoverage'])} engines",
        f"Scanned {_date(data.get('scannedAt') or data['generatedAt'])}",
    ]
    doc.text(" · ".join(m for m in meta if m), size=8.5, leading=12, grey=True)
    doc.rule(space_above=10, space_below=8)


# ---------------------------------------------------------------------------
# 01 — SCORE
# ---------------------------------------------------------------------------


def _score_beat(
    doc: PdfDocument, data: dict[str, Any], story: narrative.Narrative, subject_name: str
) -> None:
    _beat(doc, 1, "Where you stand", narrative.score_heading(story, subject_name))

    if story.status == "scored" and story.composite is not None:
        doc.text(f"{story.composite:.2f} / 100", font=BOLD, size=30, leading=36)
        doc.text(_band(story.composite), font=BOLD, size=10, leading=14, space_after=4)
        doc.text(
            "The score is the weighted composite of the dimensions below. Each is "
            "measured from what the engines actually returned for this scan.",
            size=10,
            space_after=4,
        )
    else:
        # NEVER a zero, and never a bare dash that could read as a value.
        # scoring-spec.md: an unrunnable scan must not appear to a client as a
        # bad score, and the two reasons it can be unscored are different facts.
        doc.text("Not scored", font=BOLD, size=30, leading=36)
        if story.status == "insufficient_data":
            doc.text(
                "There was not enough data to compute a score for this scan — no engine "
                "returned an answer to measure. This is a scan that did not complete, "
                "not a brand that scored badly, and it is deliberately shown as no score "
                "rather than as a zero.",
                size=10,
                space_after=4,
            )
        else:
            doc.text(
                "This scan has run but has not been scored. The engine results below are "
                "real; the composite is simply not computed yet. Nothing here should be "
                "read as a low score.",
                size=10,
                space_after=4,
            )

    if story.segments:
        doc.subheading("What makes up the score")
        doc.table(
            ["Dimension", "Weight", "Sub-score", "Points left"],
            [
                [
                    s.label,
                    f"{s.weight:.0f}%",
                    f"{s.subscore:.2f}",
                    f"{s.gap:.1f}",
                ]
                for s in story.segments
            ],
            widths=[220, 80, 90, 93],
        )

    if story.exclusions:
        doc.subheading("Left out of the score, and why")
        for exclusion in story.exclusions:
            reason = narrative.EXCLUSION_REASON.get(
                exclusion["reason"], exclusion["reason"]
            )
            # An excluded dimension is named with its reason and NO sub-score.
            # Printing it as zero would assert what the scoring engine
            # deliberately refused to assert.
            doc.bullet(f"{exclusion['label']} — {reason}.")

    flags = (data.get("score") or {}).get("degradationFlags") or []
    if flags:
        doc.subheading("Why some numbers are rougher than others")
        for flag in flags:
            doc.bullet(_DEGRADATION.get(flag, flag))


# ---------------------------------------------------------------------------
# 02 — GAP
# ---------------------------------------------------------------------------


def _gap_beat(doc: PdfDocument, story: narrative.Narrative, subject_name: str) -> None:
    _beat(doc, 2, "The biggest gap", narrative.gap_heading(story))

    gap = story.biggest_gap
    if gap is None:
        doc.text(
            f"No dimension can be ranked for recovery until {subject_name} has a score. "
            "The evidence below is still real; there is simply no arithmetic to rank it "
            "with yet.",
            size=10,
        )
        return

    doc.text(
        f"{gap.label} carries {gap.weight:.0f}% of the composite and scored "
        f"{gap.subscore:.2f} out of 100, which leaves {gap.gap:.1f} points on the table "
        f"— more than any other dimension in this scan.",
        size=10,
        space_after=4,
    )

    runners = [s for s in story.segments if not s.is_biggest_gap and s.gap > 0]
    if runners:
        doc.subheading("The runners-up")
        for segment in sorted(runners, key=lambda s: (-s.gap, s.key))[:4]:
            doc.bullet(f"{segment.label} — {segment.gap:.1f} points.")


# ---------------------------------------------------------------------------
# 03 — PROOF
# ---------------------------------------------------------------------------


def _proof_beat(doc: PdfDocument, data: dict[str, Any], subject_name: str) -> None:
    proof = data["proof"]
    _beat(doc, 3, "The evidence", _proof_heading(proof, subject_name))

    doc.key_value("Prompts asked", str(proof["promptsRun"]))
    doc.key_value("Engine answers collected", str(proof["engineResults"]))
    doc.key_value("Answers that came back", str(proof["answeredResults"]))
    doc.key_value(f"Answers naming {subject_name}", str(proof["resultsMentioningSubject"]))
    doc.key_value("Sources cited across those answers", str(proof["totalCitations"]))
    doc.key_value(f"Citations pointing at {subject_name}", str(proof["subjectCitations"]))

    if proof["engineCoverage"]:
        doc.subheading("How each engine covered the prompt set")
        doc.table(
            ["Engine", "Asked", "Answered", f"Named {subject_name}"],
            [
                [
                    narrative.ENGINE_LABEL.get(row["engine"], row["engine"]),
                    str(row["promptsRun"]),
                    str(row["answered"]),
                    str(row["mentioned"]),
                ]
                for row in proof["engineCoverage"]
            ],
            widths=[220, 80, 90, 93],
        )

    competitor_set = data.get("competitorSet")
    doc.subheading("Who else these answers name")
    if competitor_set is None:
        # Said, not omitted. A missing beat reads as "no rivals exist", which is
        # a claim about the brand rather than about the detection.
        doc.text(
            "No competitor set was detected for this scan, so no comparison is shown. "
            "That is a limit of the detection, not a finding about the brand.",
            size=10,
        )
    elif not competitor_set["competitors"]:
        doc.text(
            "Detection ran and surfaced no usable rivals. Again: a limit of the "
            "detection, not a finding about the brand.",
            size=10,
        )
    else:
        doc.text(
            _DETECTION_STATUS.get(competitor_set["status"], ""),
            size=9.5,
            leading=13,
            grey=True,
            space_after=2,
        )
        doc.table(
            ["Brand", "Mention rate", "Share of voice", "Citation strength"],
            [
                [
                    competitor["name"],
                    _decimal(competitor.get("mentionRate")),
                    _decimal(competitor.get("shareOfVoice")),
                    _decimal(competitor.get("citationStrength")),
                ]
                for competitor in competitor_set["competitors"]
            ],
            widths=[200, 95, 95, 93],
        )
        doc.text(
            "Compared per dimension rather than on a composite: sentiment is classified "
            "toward the subject only and technical foundation is the subject's own site, "
            "so a rival composite would be computed on a different basis and would "
            "understate every rival by construction.",
            size=8.5,
            leading=12,
            grey=True,
        )

    shares = [m for m in proof["mentionShares"] if m["outranksSubject"]]
    if shares:
        doc.subheading(f"Named more often than {subject_name}")
        doc.table(
            ["Brand", "Appearances", "Best position"],
            [
                [
                    row["entityName"],
                    str(row["appearances"]),
                    "—" if row["bestPosition"] is None else str(row["bestPosition"]),
                ]
                for row in shares[:8]
            ],
            widths=[290, 100, 93],
        )

    _citation_table(
        doc,
        f"Sources citing {subject_name}",
        proof["subjectCitedDomains"],
        f"No answer cited {subject_name}'s own pages in this scan.",
    )
    _citation_table(
        doc,
        "Sources cited instead",
        proof["competitorCitedDomains"],
        "No engine cited any source in this scan, for any brand.",
    )


def _citation_table(
    doc: PdfDocument, title: str, rows: list[dict[str, Any]], empty: str
) -> None:
    doc.subheading(title)
    if not rows:
        doc.text(empty, size=10, grey=True)
        return
    doc.table(
        ["Domain", "Citations", "Attributed to"],
        [
            [
                row["domain"],
                str(row["citations"]),
                row.get("competitorName") or "—",
            ]
            for row in rows
        ],
        widths=[290, 90, 103],
    )


def _proof_heading(proof: dict[str, Any], subject_name: str) -> str:
    """Mirrors `proofHeading` in ReportView, claim for claim."""
    if proof["answeredResults"] == 0:
        return "No engine returned an answer to measure."
    if proof["subjectCitations"] == 0 and proof["totalCitations"] > 0:
        return f"Answers cite {proof['totalCitations']} sources. None of them are {subject_name}."
    ahead = sum(1 for m in proof["mentionShares"] if m["outranksSubject"])
    if ahead > 0:
        verb = "rival is" if ahead == 1 else "rivals are"
        return f"{ahead} {verb} named more often than {subject_name} in the same answers."
    return "Here is what the engines actually returned."


# ---------------------------------------------------------------------------
# 04 — FIX
# ---------------------------------------------------------------------------


def _fix_beat(doc: PdfDocument, story: narrative.Narrative) -> None:
    if not story.fixes:
        _beat(
            doc, 4, "What to change", "There is nothing specific to fix from this scan yet."
        )
        doc.text(
            "No dimension gap was large enough to name and the audit returned no "
            "findings. That is a thin scan rather than a clean site — run the technical "
            "audit and score the scan to get a fix list worth acting on.",
            size=10,
        )
        return

    _beat(doc, 4, "What to change", _fix_heading(story))

    if any(f.generated for f in story.fixes):
        doc.text(
            "The wording of these items was drafted against this scan's own figures. "
            "Which items appear, their order, and what each is worth are computed, not "
            "written.",
            size=8.5,
            leading=12,
            grey=True,
            space_after=4,
        )

    for index, fix in enumerate(story.fixes, start=1):
        doc.subheading(f"{index}. {fix.title}")
        doc.text(fix.detail, size=10, indent=0, space_after=2)
        worth = (
            f"Worth {fix.points_upside:.1f} points"
            if fix.points_upside is not None
            # An audit or citation fix contributes without a proportion this
            # system measures. It carries no number rather than an invented one.
            else "No measurable point value"
        )
        doc.text(
            f"{_PRIORITY[fix.priority]} · {_EFFORT[fix.effort]} · {worth}",
            size=8.5,
            leading=12,
            grey=True,
        )

    if any(f.source == "audit" for f in story.fixes):
        doc.space(4)
        doc.text(
            "Audit findings are structural checks on the site itself. They contribute to "
            "Technical Foundation but not in a proportion this system measures, so they "
            "are listed without a point value rather than with a guessed one.",
            font=ITALIC,
            size=8.5,
            leading=12,
            grey=True,
        )


def _fix_heading(story: narrative.Narrative) -> str:
    with_points = [f for f in story.fixes if f.points_upside]
    if with_points and story.recoverable_points > 0:
        return f"{len(story.fixes)} changes, worth {story.recoverable_points:.1f} points."
    return f"{len(story.fixes)} specific changes the audit named."


# ---------------------------------------------------------------------------
# 05 — PITCH
# ---------------------------------------------------------------------------


def _pitch_beat(
    doc: PdfDocument, data: dict[str, Any], story: narrative.Narrative, subject_name: str
) -> None:
    _beat(doc, 5, "The opportunity", _pitch_heading(story, subject_name))

    if (
        story.status == "scored"
        and story.composite is not None
        and story.potential_composite is not None
        and story.recoverable_points > 0
    ):
        doc.key_value("Composite today", f"{story.composite:.2f}")
        doc.key_value("Points the listed changes recover", f"{story.recoverable_points:.1f}")
        doc.key_value("Composite if they all land", f"{story.potential_composite:.2f}")
        doc.space(4)
        doc.text(
            "That is arithmetic over the figures already in this report — the points the "
            "listed fixes recover, added to the score as it stands. There is no revenue "
            "estimate and no traffic projection here, because nothing in this system "
            "measures either.",
            size=9.5,
            leading=13,
            grey=True,
        )
    else:
        doc.text(
            f"A complete scan would put a number on where {subject_name} stands and on "
            "what the changes above are worth. This one did not get far enough to do "
            "that, and no figure is offered in place of one.",
            size=10,
        )

    ahead = story.ahead_on_dimensions
    if ahead:
        doc.subheading("Where rivals are ahead today")
        doc.table(
            ["Rival", "Dimension", "Ahead by"],
            [
                [
                    row["competitorName"],
                    narrative.DIMENSION_LABEL.get(row["dimensionKey"], row["dimensionKey"]),
                    f"{row['delta']:.1f}",
                ]
                for row in ahead[:6]
            ],
            widths=[220, 170, 93],
        )


def _pitch_heading(story: narrative.Narrative, subject_name: str) -> str:
    if (
        story.status == "scored"
        and story.composite is not None
        and story.potential_composite is not None
        and story.recoverable_points > 0
    ):
        today = narrative._round(story.composite, 0)  # noqa: SLF001
        after = narrative._round(story.potential_composite, 0)  # noqa: SLF001
        return f"{today:.0f} today. {after:.0f} with the fixes above."
    return f"What a complete scan would tell you about {subject_name}."


# ---------------------------------------------------------------------------
# colophon
# ---------------------------------------------------------------------------


def _colophon(doc: PdfDocument, data: dict[str, Any]) -> None:
    doc.rule(space_above=16, space_below=8)
    audit = data.get("audit")
    if audit is None:
        doc.text(
            "The site was not technically audited for this scan, so Technical Foundation "
            "is excluded from the score rather than counted as zero.",
            size=8.5,
            leading=12,
            grey=True,
        )
    doc.text(
        f"Scan {data['scanId']} · beats {' · '.join(_BEATS)} · generated "
        f"{_date(data['generatedAt'])}",
        size=8,
        leading=11,
        grey=True,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_BEATS = ("score", "gap", "proof", "fix", "pitch")

_PRIORITY = {"high": "High priority", "medium": "Medium priority", "low": "Low priority"}
_EFFORT = {"S": "Small effort", "M": "Medium effort", "L": "Large effort"}

_DEGRADATION = {
    "NO_AUTHORITY_DATA": (
        "Citation Strength is measured against the best-cited brand in this scan rather "
        "than against an external authority ranking, which this system does not have a "
        "source for."
    ),
    "WEAK_COMPETITOR_SET": (
        "The competitor set was only weakly corroborated — some rivals were surfaced by a "
        "single signal. Share of Voice should be read as indicative rather than exact."
    ),
    "NO_CITATIONS_IN_SCAN": "No engine cited any source in this scan, for any brand.",
    "NO_COMPETITOR_SET": (
        "No competitors were detected, so no share comparison was possible."
    ),
    "TECHNICAL_FOUNDATION_NOT_MEASURED": (
        "The site had not been audited when this score was computed."
    ),
    "NO_ANSWERED_RESULTS": "No engine returned an answer for this scan.",
}

_DETECTION_STATUS = {
    "ok": "Search results and AI answers independently surfaced this set of rivals.",
    "weak_signal": (
        "Few candidates surfaced and the two detection signals largely disagreed. Treat "
        "this rival set as a starting point and correct it before sending the report on."
    ),
    "no_signal": (
        "Neither search results nor AI answers surfaced a usable competitor set, so no "
        "comparison is shown. That is a limit of the detection, not a finding about the "
        "brand."
    ),
}


def _beat(doc: PdfDocument, step: int, eyebrow: str, heading: str) -> None:
    """One beat opener: numbered eyebrow, then the claim.

    The heading is a CLAIM, never a category label — the rule `Beat` enforces on
    screen. The eyebrow supplies the structural label so the heading is free to
    argue.
    """
    doc.rule(space_above=14, space_below=10)
    doc.eyebrow(f"{step:02d}   {eyebrow}")
    doc.heading(heading)


def _band(score: float) -> str:
    """The visibility band. Mirrors `VisibilityBadge`'s thresholds."""
    if score >= 80:
        return "Dominant"
    if score >= 60:
        return "Established"
    if score >= 35:
        return "Emerging"
    if score >= 15:
        return "Marginal"
    return "Absent"


def _decimal(value: Any) -> str:
    """A string-encoded decimal, or an em dash.

    Null means the scan produced no comparison for this competitor on this
    dimension. It is a dash, never a zero — a rival that was not measured did
    not score nothing.
    """
    parsed = narrative.num(value)
    return "—" if parsed is None else f"{parsed:.2f}"


def _date(iso: str | None) -> str:
    """`04 Sep 2026`, UTC. Matches `lib/dates.ts` so the two documents agree."""
    if not iso:
        return "—"
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    try:
        from datetime import datetime

        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return f"{at.day:02d} {months[at.month - 1]} {at.year}"

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
score → gap → proof → fix → pitch, the sequence the narrative-report rule mandates and
`BEAT_SEQUENCE` fixes. The PDF is not a data dump with the same numbers in it;
it is the same argument on paper, so the order is not a display preference.

DEGRADED STATES ARE RENDERED, NOT SMOOTHED
------------------------------------------
Everything the screen refuses to fake, this refuses to fake:

  * a null composite prints **"Not scored"**, never 0 and never a dash that
    could read as a value
  * `insufficient_data` is its own sentence, distinct from never-scored
  * an excluded dimension prints its reason and no sub-score, never a zero
  * a null competitor set says the scan has no list, rather than omitting the
    beat and letting the absence read as "no rivals exist"
  * an empty `actionItems` list renders the deterministic derivation, exactly
    as api-contracts.md's note on that field says the fix beat does

The facts-only rule: every value written here is a name, a domain, a URL, a count,
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


# PARTIAL: some engine calls got no answer, or the deadline ended the scan with at
# least half of its planned prompts answered. FAILED: every engine failed, an
# error or a restart stopped it, or the deadline came with fewer than half
# answered. "Not every planned question was answered" is what PARTIAL means;
# FAILED says only that it failed, which is true of every one of those.
_OUTCOME_LINE = {
    "partial": (
        "This scan is partial: not every planned question was answered, so these "
        "results rest on fewer answers than planned."
    ),
    "failed": "This scan failed, so these results are incomplete.",
}


# ---------------------------------------------------------------------------
# cover
# ---------------------------------------------------------------------------


def _cover(doc: PdfDocument, data: dict[str, Any], subject_name: str) -> None:
    """The white-label surface. The AGENCY's identity, never ours.

    **Name and slug only, and the PDF deliberately does NOT carry the logo the
    web report now does** — Epic 9.22, decided rather than overlooked.

    A logo would be the first image this document has ever needed, and this
    module was built at zero dependencies behind an explicit licensing survey:
    WeasyPrint's required `Pyphen` is GPL/LGPL/MPL, which the dependency-licensing rule
    blocks, and React-PDF is a Node library in a Python process. Adding image
    support would reopen that survey for a letterhead mark, and it would also
    mean the server FETCHING an agency-supplied URL to embed it — a
    server-side request to an address a customer chose, which is a different
    security question from an `<img src>` the reader's own browser resolves.

    So the gap is named rather than silently missing: an agency's logo appears
    on the web report and not in the PDF. The accent colour is absent for the
    same reason plus a simpler one — it exists to tint a letterhead rule, and a
    rule in a colour nobody asked about is not worth a dependency.
    """
    agency = data["agency"]
    doc.text(agency["name"], font=BOLD, size=11, leading=15)
    doc.text(
        "AI visibility report",
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
    # Says what the report covers when the scan did not finish cleanly. Chosen
    # from the STATUS alone, because the public PDF carries no error code, and
    # it says nothing about the agency's scan allowance: this goes to a prospect.
    if (line := _OUTCOME_LINE.get(str(data.get("scanStatus")))) is not None:
        doc.text(line, font=BOLD, size=9.5, leading=16)
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
        if story.status == "failed":
            doc.text("This scan failed, so it has no score.", size=10, space_after=4)
        elif story.status == "insufficient_data":
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
                    narrative.points1(s.gap),
                ]
                for s in story.segments
            ],
            widths=[220, 80, 90, 93],
        )
        # What a measured dimension is measured FROM, where the label alone
        # would let a reader assume more precision than the input has — v3.
        # The same sentence the screen shows, from the mirrored table.
        for segment in story.segments:
            note = narrative.DIMENSION_NOTE.get(segment.key)
            if note:
                doc.text(f"{segment.label}. {note}", size=9, leading=12.5, space_after=4)

    if story.exclusions:
        doc.subheading("Left out of the score, and why")
        for exclusion in story.exclusions:
            reason = narrative.EXCLUSION_REASON.get(
                exclusion["reason"], exclusion["reason"]
            )
            detail = narrative.EXCLUSION_DETAIL.get(exclusion["reason"])
            # An excluded dimension is named with its reason and NO sub-score.
            # Printing it as zero would assert what the scoring engine
            # deliberately refused to assert. The sentence under the label is
            # the page's, so the file says why in the same words.
            doc.bullet(
                f"{exclusion['label']} — {reason}." + (f" {detail}" if detail else "")
            )

    flags = (data.get("score") or {}).get("degradationFlags") or []
    if flags:
        doc.subheading("Why some numbers are rougher than others")
        for flag in flags:
            doc.bullet(narrative.DEGRADATION_FLAG.get(flag, flag))


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
        f"{gap.subscore:.2f} out of 100, which leaves {narrative.points1(gap.gap)} points "
        "on the table "
        f"— more than any other dimension in this scan.",
        size=10,
        space_after=4,
    )

    runners = [s for s in story.segments if not s.is_biggest_gap and s.gap > 0]
    if runners:
        doc.subheading("The runners-up")
        for segment in sorted(runners, key=lambda s: (-s.gap, s.key))[:4]:
            doc.bullet(f"{segment.label} — {narrative.points1(segment.gap)} points.")


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
            "This scan has no competitor list, so no comparison is shown. "
            "That is a limit of the data, not a finding about the brand.",
            size=10,
        )
    elif not competitor_set["competitors"]:
        doc.text(
            "This scan\u2019s competitor list has no rivals, so no comparison is shown. "
            "Either search results and AI answers surfaced no usable set, or the list was "
            "emptied by hand. Neither is a finding about the brand.",
            size=10,
        )
    else:
        doc.text(
            _HAND_NAMED
            if all(c.get("isManualOverride") for c in competitor_set["competitors"])
            else _DETECTION_STATUS.get(competitor_set["status"], ""),
            size=9.5,
            leading=13,
            grey=True,
            space_after=2,
        )
        subject_score = data.get("score") or {}
        doc.table(
            [
                "Brand", "Mention rate", "Share of voice", "Citation strength",
                narrative.POSITION_COPY["heading"],
            ],
            [
                # The subject's own row first, so the position column has the
                # comparison it exists for — the screen's table does the same.
                [
                    subject_name,
                    _decimal(subject_score.get("mentionRate")),
                    _decimal(subject_score.get("shareOfVoice")),
                    _decimal(subject_score.get("citationStrength")),
                    narrative.position_cell(
                        subject_score.get("averagePosition"), subject_score.get("positionCount")
                    ),
                ],
                *[
                    [
                        competitor["name"],
                        _decimal(competitor.get("mentionRate")),
                        _decimal(competitor.get("shareOfVoice")),
                        _decimal(competitor.get("citationStrength")),
                        narrative.position_cell(
                            competitor.get("averagePosition"), competitor.get("positionCount")
                        ),
                    ]
                    for competitor in competitor_set["competitors"]
                ],
            ],
            widths=[135, 72, 78, 82, 116],
        )
        doc.text(narrative.POSITION_COPY["note"], size=8.5, leading=12, grey=True)
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
        narrative.own_pages_empty_message(
            data["subject"]["domain"],
            len(proof["subjectCitedDomains"]),
            len(proof["subjectOtherSites"]),
        )
        or f"No answer cited {subject_name}'s own pages in this scan.",
    )
    # M1 task 1.3: the brand's confirmed other sites, directly under that table, in the web's words.
    other_sites = narrative.other_sites_line(subject_name, proof["subjectOtherSites"])
    if other_sites:
        doc.space(4)
        doc.text(other_sites, size=10, space_after=4)
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
    """The fix beat's heading, the screen's (`ReportView.tsx`, `fixHeading`) word for word.

    "From this scan" and not "the audit named": a list with no point figure is the audit's
    findings and the citation fix, and the citation fix is not an audit finding.
    """
    count = len(story.fixes)
    noun = "change" if count == 1 else "changes"
    with_points = [f for f in story.fixes if f.points_upside]
    if with_points and story.recoverable_points > 0:
        return f"{count} {noun}, worth {story.recoverable_points:.1f} points."
    return f"{count} specific {noun} from this scan."


# ---------------------------------------------------------------------------
# 05 — PITCH
# ---------------------------------------------------------------------------


def _pitch_beat(
    doc: PdfDocument, data: dict[str, Any], story: narrative.Narrative, subject_name: str
) -> None:
    _beat(doc, 5, "The opportunity", _pitch_heading(story, subject_name))

    printed = _printed_figures(story)
    if (
        printed is not None
        and story.composite is not None
        and story.potential_composite is not None
        and story.recoverable_points > 0
    ):
        today, _after, gain = printed
        capped = narrative.ceiling_is_capped(story)
        doc.key_value("Composite today", f"{story.composite:.2f}")
        doc.key_value("Points the listed changes recover", f"{story.recoverable_points:.1f}")
        if not capped:
            doc.key_value(
                "Composite if every gap they address closes", f"{story.potential_composite:.2f}"
            )
        doc.space(4)
        if capped:
            # The rounded points carry the score past 100, so no sum is printed: the two
            # rows above and 100 would not add up (launch checklist 3.100).
            doc.text(
                f"Closing the gaps listed above recovers {story.recoverable_points:.1f} "
                "points, which takes the score to 100, the most a score can reach.",
                size=9.5,
                leading=13,
                grey=True,
            )
        elif gain == 0:
            # Points worth recovering, but not enough to move the printed score.
            # Said plainly, so the two equal numbers above are not read as a gain.
            doc.text(
                f"Closing the gaps listed above recovers {story.recoverable_points:.1f} "
                f"points, which does not change the rounded score of {today}.",
                size=9.5,
                leading=13,
                grey=True,
            )
        arithmetic = (
            ""
            if capped
            else "That is arithmetic over the figures already in this report — the points "
            "the listed fixes recover, added to the score as it stands. "
        )
        doc.text(
            arithmetic + "There is no revenue estimate and no traffic projection here, "
            "because nothing in this system measures either.",
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


def _printed_figures(story: narrative.Narrative) -> tuple[int, int, int] | None:
    """The pitch heading's two whole-number figures, and the difference between them.

    The difference is of the two printed (rounded) numbers, never a separately
    rounded figure, so "63 + 30" always sums to the 93 beside it. The heading is
    built from them, and the figure rows under it ask them whether there is an
    uplift to state, so the two can never disagree about that. `None` when there
    is no score. Mirrored by `printedFigures` in `ReportView.tsx`.
    """
    if (
        story.status != "scored"
        or story.composite is None
        or story.potential_composite is None
    ):
        return None
    today = int(narrative._round(story.composite, 0))  # noqa: SLF001
    after = int(narrative._round(story.potential_composite, 0))  # noqa: SLF001
    return today, after, after - today


def _pitch_heading(story: narrative.Narrative, subject_name: str) -> str:
    """The pitch beat's heading.

    The parenthetical is joined by no-break spaces so it never breaks across
    two lines. When the two printed numbers are equal there is nothing to add
    and only "63 today." is printed.
    Mirrored by `pitchHeading` in `ReportView.tsx`.
    """
    printed = _printed_figures(story)
    if printed is not None:
        today, after, gain = printed
        if gain > 0:
            return (
                f"{today} today. Up to {after} if these fixes close every gap "
                f"they address ({today}\u00a0+\u00a0{gain})."
            )
        return f"{today} today."
    return f"What a complete scan would tell you about {subject_name}."


# ---------------------------------------------------------------------------
# colophon
# ---------------------------------------------------------------------------


def _colophon(doc: PdfDocument, data: dict[str, Any]) -> None:
    audit = data.get("audit")
    if audit is None:
        doc.keep_together(16 + 3 * 12)
        doc.space(16)
        doc.text(
            "The site was not technically audited for this scan, so Technical Foundation "
            "is excluded from the score rather than counted as zero.",
            size=8.5,
            leading=12,
            grey=True,
        )
    # Drawn, with its own hairline, in the last page's bottom margin, outside the
    # flow, so it can never start a page of its own.
    doc.footer(f"Scan {data['scanId']} · generated {_date(data['generatedAt'])}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_PRIORITY = {"high": "High priority", "medium": "Medium priority", "low": "Low priority"}
_EFFORT = {"S": "Small effort", "M": "Medium effort", "L": "Large effort"}

# Degradation copy lives in `report_narrative.DEGRADATION_FLAG` since
# 2026-09-27, mirrored from `strings.ts` and tested against it. It lived here
# first, and shipped without two of the codes the screen had sentences for.

# A list the agency named by hand is stored as status ok with no confidence, and a scan adopts it
# whole, so every rival on it is a manual override. "Independently surfaced" is false of it.
_HAND_NAMED = "These rivals were named by the agency, not found by search results or AI answers."

_DETECTION_STATUS = {
    "ok": "Search results and AI answers independently surfaced this set of rivals.",
    "weak_signal": (
        "Few candidates surfaced and the two detection signals largely disagreed. Treat "
        "this rival set as a starting point."
    ),
    "no_signal": (
        "This scan\u2019s competitor list has no rivals, so no comparison is shown. Either "
        "search results and AI answers surfaced no usable set, or the list was emptied by "
        "hand. Neither is a finding about the brand."
    ),
}


def _beat(doc: PdfDocument, step: int, eyebrow: str, heading: str) -> None:
    """One beat opener: numbered eyebrow, then the claim.

    The heading is a CLAIM, never a category label — the rule `Beat` enforces on
    screen. The eyebrow supplies the structural label so the heading is free to
    argue.
    """
    # The rule, the label and the heading's first line travel together.
    doc.keep_together(14 + 10 + 1 + 11 + 15 * 1.4 + 26)
    doc.rule(space_above=14, space_below=10)
    doc.eyebrow(f"{step:02d}   {eyebrow}")
    doc.heading(heading)


def _band(score: float) -> str:
    """The visibility band, in the words the page uses for the same score.

    A copy of `visibilityBand` (`packages/design-system/src/tokens/color.ts`)
    and of the labels `VisibilityBadge` renders — the same duplication
    `report_narrative.py` carries for `derive.ts`, for the same reason: a
    Python process cannot call a TypeScript function.

    **It is policed the same way, because it drifted.** This function claimed
    to mirror the badge's thresholds and cut at 15 and 35 with its own words,
    so a prospect saw *Barely visible* on the page and *Marginal* in the PDF
    they had just downloaded from it — the exact failure `api-contracts.md`
    predicted for this route. Both sides now read
    `packages/shared-types/fixtures/visibility-bands.json` at every boundary
    (`test_visibility_band.py` here, `visibilityBand.test.ts` in the design
    system), so the next drift reddens a suite instead of reaching a client.
    """
    s = min(100.0, max(0.0, score))
    if s < 20:
        return "Absent"
    if s < 40:
        return "Barely visible"
    if s < 60:
        return "Emerging"
    if s < 80:
        return "Established"
    return "Highly visible"


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

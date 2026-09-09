"""The Python narrative derivation agrees with the TypeScript one — Epic 9.14.

THIS IS THE TEST THAT MAKES A DUPLICATED DERIVATION SAFE
--------------------------------------------------------
`derive.ts` computes the report's argument for the screen, and
`services/report_narrative.py` computes it for the PDF. Two implementations of
one arithmetic is exactly what `derive.ts`'s own docstring warns against — "the
chart annotating one dimension while the headline names another" — and the
duplication only exists because a Python process cannot call a TypeScript
function.

So it is policed rather than trusted. This file and
`apps/web/src/lib/report/crossLanguage.test.ts` read the SAME two checked-in
files:

  packages/shared-types/fixtures/report-cases.json      six report payloads
  packages/shared-types/fixtures/expected-narrative.json what they must produce

`expected-narrative.json` was generated from the TypeScript implementation,
which remains the source of truth. Change either derivation and one of the two
suites goes red on the exact field that moved.

The cases are real data and its degraded variants, not tidy inventions. The base
is `scan_01M0HDRGJNWNZDSJPP0NC3SV8W` — a genuine Help Scout scan from the Epic
5/6 verification run, whose citation strength really is 3.70 and whose biggest
gap really is a dimension other than the lowest-weighted one, which is the case
the gap formula exists to get right.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from avp_api.services import report_narrative as narrative

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
)
CASES: dict[str, Any] = json.loads((FIXTURES / "report-cases.json").read_text())
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected-narrative.json").read_text())


def _as_json(result: narrative.Narrative) -> dict[str, Any]:
    """The same projection `crossLanguage.test.ts` compares against."""
    return {
        "composite": result.composite,
        "status": result.status,
        "biggestGapKey": result.biggest_gap.key if result.biggest_gap else None,
        "recoverablePoints": result.recoverable_points,
        "potentialComposite": result.potential_composite,
        "fixes": [
            {
                "id": f.id,
                "title": f.title,
                "detail": f.detail,
                "priority": f.priority,
                "effort": f.effort,
                "pointsUpside": f.points_upside,
                "source": f.source,
                "generated": f.generated,
            }
            for f in result.fixes
        ],
    }


def test_the_fixture_pair_covers_every_case() -> None:
    """A case with no expectation would pass by never being compared."""
    assert set(CASES) == set(EXPECTED)
    assert len(CASES) >= 6


@pytest.mark.parametrize("case", sorted(CASES))
def test_python_agrees_with_typescript(case: str) -> None:
    assert _as_json(narrative.derive_narrative(CASES[case])) == EXPECTED[case]


# ---------------------------------------------------------------------------
# the properties the agreement test would still pass if BOTH sides broke
# ---------------------------------------------------------------------------


def test_an_unscored_report_derives_no_composite_and_no_zero() -> None:
    """scoring-spec.md: an unrunnable scan is never rendered as a bad score."""
    result = narrative.derive_narrative(CASES["unscored"])
    assert result.composite is None
    assert result.status == "not_scored"
    assert result.potential_composite is None
    # No dimensions, so no dimension fixes — and no invented ones either.
    assert all(f.source != "gap" for f in result.fixes)
    # The concrete recommendations survive, because they do not need a score.
    assert {f.source for f in result.fixes} == {"audit", "citation"}


def test_a_dimension_we_have_not_measured_never_becomes_a_fix() -> None:
    """OUR gap is not the client's, and must not be sold to them as one."""
    result = narrative.derive_narrative(CASES["not-yet-measured"])
    assert "gap:citation_strength" not in {f.id for f in result.fixes}
    assert any(e["reason"] == "NOT_YET_MEASURED" for e in result.exclusions)


def test_generated_wording_never_moves_a_number() -> None:
    """Epic 8's generator rewords; it does not re-decide the arithmetic."""
    plain = narrative.derive_narrative(CASES["scored-no-generated-fixes"])
    enriched = narrative.derive_narrative(CASES["scored-with-generated-fixes"])

    assert [f.id for f in plain.fixes] == [f.id for f in enriched.fixes]
    assert [f.points_upside for f in plain.fixes] == [f.points_upside for f in enriched.fixes]
    assert plain.recoverable_points == enriched.recoverable_points
    assert plain.composite == enriched.composite
    # The wording DID move, or this test proves nothing.
    assert [f.title for f in plain.fixes] != [f.title for f in enriched.fixes]
    assert any(f.generated for f in enriched.fixes)
    # An audit fix has no measurable point value and must not acquire one.
    for fix in enriched.fixes:
        if fix.source != "gap":
            assert fix.points_upside is None


def test_the_citation_fix_gets_its_own_slot_rather_than_taking_one() -> None:
    """It has no point value by design, so on a points ranking it truncates away."""
    result = narrative.derive_narrative(CASES["scored-no-generated-fixes"])
    measured = [f for f in result.fixes if f.source != "citation"]
    citation = [f for f in result.fixes if f.source == "citation"]
    assert len(measured) <= narrative.MAX_FIXES
    assert len(citation) == 1
    assert citation[0].priority == "medium"  # never 'high'


def test_the_citation_fix_names_a_domain_and_nothing_about_it() -> None:
    """The facts-only rule: a domain and a count are facts; what is ON it is not."""
    result = narrative.derive_narrative(CASES["scored-no-generated-fixes"])
    fix = next(f for f in result.fixes if f.source == "citation")
    assert "eesel.ai" in fix.title
    assert "cited 6 times" in fix.detail
    # Nothing describing the page's content, which we have never read.
    for tell in ("article", "blog post", "guide to", "reviews of", "says"):
        assert tell not in fix.detail.lower()


def test_the_biggest_gap_is_the_arithmetic_not_the_lowest_subscore() -> None:
    """The case the formula exists for.

    Help Scout's lowest sub-score is Citation Strength at 3.70 AND that is also
    the biggest gap — but only because its weight carries it. The renormalised
    `no-competitor-set` case is the check that matters: dropping Share of Voice
    redistributes weight and the gap figures all move.
    """
    full = narrative.derive_narrative(CASES["scored-no-generated-fixes"])
    reduced = narrative.derive_narrative(CASES["no-competitor-set"])
    assert full.biggest_gap is not None and reduced.biggest_gap is not None
    assert full.biggest_gap.key == reduced.biggest_gap.key == "citation_strength"
    # Same dimension, different number, because the weights were renormalised.
    assert reduced.biggest_gap.gap > full.biggest_gap.gap


def test_weights_are_renormalised_to_one_hundred() -> None:
    result = narrative.derive_narrative(CASES["no-competitor-set"])
    total = sum(s.weight for s in result.segments)
    assert 99.5 <= total <= 100.5, total


def test_rounding_is_half_up_like_javascript() -> None:
    """Python's built-in `round` is banker's rounding; JavaScript's is not.

    Left unhandled, a fix worth 19.25 points reads as 19.2 in the PDF and 19.3
    on screen — the two-documents-disagreeing failure this whole module is
    policed against.
    """
    assert narrative.round1(19.25) == 19.3
    assert narrative.round1(2.5) == 2.5
    assert narrative._round(2.5, 0) == 3.0  # noqa: SLF001
    assert narrative._round(3.5, 0) == 4.0  # noqa: SLF001
    assert round(2.5) == 2  # the behaviour being avoided


def test_headings_are_claims_not_category_labels() -> None:
    scored = narrative.derive_narrative(CASES["scored-no-generated-fixes"])
    unscored = narrative.derive_narrative(CASES["unscored"])

    assert narrative.score_heading(scored, "Help Scout") == (
        "Help Scout is present in some answers and absent from many."
    )
    assert narrative.score_heading(unscored, "Help Scout") == (
        "Help Scout has not been scored yet."
    )
    assert "Citation Strength is costing the most" in narrative.gap_heading(scored)
    assert narrative.gap_heading(unscored) == (
        "No gap can be measured until there is a score."
    )

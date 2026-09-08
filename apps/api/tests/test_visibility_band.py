"""The PDF's visibility band is the page's — policed, not trusted.

`report_pdf._band` is a Python copy of the design system's `visibilityBand`
and of the labels `VisibilityBadge` renders. It is a copy for the reason
`report_narrative.py` is: the PDF is a Python process. It drifted anyway —
its docstring said it mirrored the badge, and it cut at 15 and 35 with its own
words — so on 2026-09-08 the same 27.28 read "Barely visible" on the share
page and "Marginal" in the PDF downloaded from it, and 17.46 read "Absent" on
the page and "Marginal" in the file.

Both implementations now read one checked-in table at every threshold,
`packages/shared-types/fixtures/visibility-bands.json`, written from the
TypeScript side. `visibilityBand.test.ts` in the design system asserts the
table still describes what the page does; this file asserts the PDF says the
same thing. Move a threshold or a word on either side and one suite goes red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from avp_api.services.report_pdf import _band

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages" / "shared-types" / "fixtures" / "visibility-bands.json"
)
TABLE: dict[str, Any] = json.loads(FIXTURE.read_text())
CASES: list[dict[str, Any]] = TABLE["cases"]


@pytest.mark.parametrize("case", CASES, ids=[str(c["score"]) for c in CASES])
def test_the_pdf_says_what_the_page_says(case: dict[str, Any]) -> None:
    assert _band(float(case["score"])) == case["label"]


def test_the_table_covers_both_sides_of_every_threshold() -> None:
    """A fixture that quietly lost its boundary rows would police nothing."""
    scores = {c["score"] for c in CASES}
    for lower, _upper in TABLE["thresholds"].values():
        if lower > 0:
            assert lower in scores, f"no case AT the {lower} boundary"
            assert any(lower - 1 < s < lower for s in scores), f"no case just below {lower}"
    assert {c["band"] for c in CASES} == set(TABLE["thresholds"])


def test_the_two_live_scores_that_disagreed_no_longer_do() -> None:
    """The dry run's own numbers, so the failure it saw is the one asserted."""
    assert _band(27.28) == "Barely visible"
    assert _band(17.46) == "Absent"


@pytest.mark.parametrize("score", [-5.0, 100.0, 140.0])
def test_out_of_range_is_clamped_rather_than_unlabelled(score: float) -> None:
    assert _band(score) in {"Absent", "Highly visible"}

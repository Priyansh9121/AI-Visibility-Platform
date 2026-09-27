"""The report's authored copy is the same in the PDF and on screen — 2026-09-27.

`report_narrative.py` mirrors five string tables from `strings.ts` "word for
word", and until this test nothing checked that. The exclusion detail for
NO_AUTHORITY_DATA had already drifted by an apostrophe, and the PDF's
degradation table was missing two codes the screen had sentences for — so a
scan with no awareness prompts printed `NO_AWARENESS_POPULATION` raw in the
document a client is sent.

Same discipline as the gap arithmetic: both suites read ONE checked-in file,
`packages/shared-types/fixtures/report-copy.json`, generated from the
TypeScript tables (`AVP_WRITE_FIXTURES=1 pnpm test` in apps/web writes it).
Change the copy on either side and one suite goes red on the string that
moved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from avp_api.services import report_narrative as narrative

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
COPY: dict[str, Any] = json.loads((FIXTURES / "report-copy.json").read_text())


def _python_side() -> dict[str, Any]:
    """The same projection `copyContract.test.ts` makes of `strings.ts`."""
    return {
        "dimensionLabel": narrative.DIMENSION_LABEL,
        "dimensionNote": narrative.DIMENSION_NOTE,
        "exclusionReason": {
            code: {"label": label, "detail": narrative.EXCLUSION_DETAIL[code]}
            for code, label in narrative.EXCLUSION_REASON.items()
        },
        "degradationFlag": narrative.DEGRADATION_FLAG,
        "fixForDimension": {
            key: {"title": title, "detail": detail, "effort": effort}
            for key, (title, detail, effort) in narrative.FIX_FOR_DIMENSION.items()
        },
    }


@pytest.mark.parametrize("table", sorted(_python_side()))
def test_python_copy_matches_typescript(table: str) -> None:
    assert _python_side()[table] == COPY[table], table


def test_every_exclusion_reason_has_a_detail() -> None:
    """A label alone under a permanent exclusion leaves a reader with two words."""
    assert set(narrative.EXCLUSION_DETAIL) == set(narrative.EXCLUSION_REASON)


def test_citation_strength_copy_says_what_the_proxy_cannot_do() -> None:
    """The limitation is stated to the reader, not only in a docstring.

    The industry-spread tier is measured through a classification whose
    confidence is uncalibrated (build-log Epic 2.6/2.8, Finding 2). Copy that
    presented the tier without saying so would imply a precision it lacks.
    """
    for text in (
        narrative.DIMENSION_NOTE["citation_strength"],
        narrative.EXCLUSION_DETAIL["NO_AUTHORITY_DATA"],
    ):
        assert "uncalibrated" in text
        assert "coarse proxy" in text
        assert "mis-classified" in text
    # And that the measured case says which answers it was measured on (v4:
    # awareness only) and that market participants are out of the second tier.
    note = narrative.DIMENSION_NOTE["citation_strength"]
    assert "only answers to questions that did not name the brand" in note
    assert "including answers to questions that named the brand" not in note
    for text in (note, narrative.EXCLUSION_DETAIL["NO_AUTHORITY_DATA"]):
        assert "brands in a scanned market" in text

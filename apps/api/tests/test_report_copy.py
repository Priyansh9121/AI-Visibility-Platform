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
import re
from pathlib import Path
from typing import Any

import pytest

from avp_api.services import domain_authority as authority
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
        "positionCopy": narrative.POSITION_COPY,
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
    # The market-participant rule left the note on 2026-09-29, when the note
    # was cut to a glance; the exclusion detail and scoring-spec.md keep it.
    assert "brands in a scanned market" in narrative.EXCLUSION_DETAIL["NO_AUTHORITY_DATA"]


# What the first tier's list is told to the reader as. The code holds suffix labels
# and domains; the sentence holds words. These tables are the translation, and they
# are this test's, not the code's: a label or a domain added to the code with no
# entry here fails the test below, which is the prompt to say it to the reader.
_KIND_OF_LABEL = {
    "gov": "government",
    "gouv": "government",
    "gob": "government",
    "govt": "government",
    "gc": "government",
    "go": "government",
    "edu": "education",
    "ac": "education",
    "mil": "military",
}
_NAME_OF_DOMAIN = {
    "wikipedia.org": "Wikipedia",
    "wikidata.org": "Wikidata",
    "apnews.com": "AP",
    "ap.org": "AP",
    "reuters.com": "Reuters",
    "bbb.org": "Better Business Bureau",
    "trustpilot.com": "Trustpilot",
    "g2.com": "G2",
    "capterra.com": "Capterra",
}
_NAME_OF_FIRST_LABEL = {"capterra": "Capterra"}


def _says(text: str, word: str) -> bool:
    """`word` appears in `text` as a word, not inside a longer one."""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def test_the_tier_a_list_in_the_sentence_is_the_list_in_the_code() -> None:
    """What the sentence says the first tier holds is what `domain_authority` holds.

    The first tier of NO_AUTHORITY_DATA's sentence is a list a reader takes to be
    the whole of it, written by hand beside a list in code. Until 2026-10-03 it said
    "government and university domains" and named Wikipedia alone, while the code
    also holds `.mil`, Wikidata, and education labels (`edu`, `ac`) that cover
    schools and other education bodies, not only universities. Nothing tied the two.

    Held both ways: a kind of institution or a named domain on the code's list is in
    the sentence, and one that is not on it is not. The sentence is checked here
    and, through the shared fixture, on the web side too. A suffix label or domain
    added to the code with no word for it above fails this test; the fix is to say
    it to the reader in `report_narrative.EXCLUSION_DETAIL` and `strings.ts`, then
    regenerate the fixture (`AVP_WRITE_FIXTURES=1`, see `copyContract.test.ts`).

    Its limit: it holds the sentence to the kinds and names the tables above know. A
    name added to the sentence that is in neither table goes unseen.
    """
    detail = narrative.EXCLUSION_DETAIL["NO_AUTHORITY_DATA"]
    problems: list[str] = []

    labels = authority._CURATED_TLDS | authority._CURATED_CC_LABELS
    domains = authority._CURATED_NAMES
    first_labels = authority._CURATED_FIRST_LABELS

    for label in sorted(labels - set(_KIND_OF_LABEL)):
        problems.append(f"suffix label {label!r} is on the curated list, with no kind here")
    for domain in sorted(domains - set(_NAME_OF_DOMAIN)):
        problems.append(f"domain {domain!r} is on the curated list, with no name here")
    for label in sorted(first_labels - set(_NAME_OF_FIRST_LABEL)):
        problems.append(f"first label {label!r} is on the curated list, with no name here")

    kinds_on_the_list = {_KIND_OF_LABEL[x] for x in labels if x in _KIND_OF_LABEL}
    names_on_the_list = {_NAME_OF_DOMAIN[x] for x in domains if x in _NAME_OF_DOMAIN}
    names_on_the_list |= {
        _NAME_OF_FIRST_LABEL[x] for x in first_labels if x in _NAME_OF_FIRST_LABEL
    }

    for kind in sorted(set(_KIND_OF_LABEL.values())):
        on_the_list = kind in kinds_on_the_list
        said = _says(detail.lower(), kind)
        if on_the_list and not said:
            problems.append(f"the sentence does not say {kind!r} domains are on the list")
        if said and not on_the_list:
            problems.append(f"the sentence says {kind!r} domains are on the list; none is")
    for name in sorted(set(_NAME_OF_DOMAIN.values()) | set(_NAME_OF_FIRST_LABEL.values())):
        on_the_list = name in names_on_the_list
        said = _says(detail, name)
        if on_the_list and not said:
            problems.append(f"the sentence does not name {name!r}, which is on the list")
        if said and not on_the_list:
            problems.append(f"the sentence names {name!r}, which is not on the list")

    assert not problems, "\n" + "\n".join(problems)


def test_the_citation_strength_note_reads_at_a_glance() -> None:
    """The question it answers, in at most 45 words — 2026-09-29."""
    note = narrative.DIMENSION_NOTE["citation_strength"]
    assert "When AI names the brand, how well does it back it with a trusted source?" in note
    words = [w for w in note.split() if any(ch.isalnum() for ch in w)]
    assert len(words) <= 45, len(words)


def test_average_position_is_never_rendered_as_zero() -> None:
    """A brand never named on an unprompted question has no position."""
    assert narrative.position_cell(None, 0) == "Not named"
    assert narrative.position_cell("0", 0) == "Not named"
    assert not any(ch.isdigit() for ch in narrative.position_cell(None, 0))
    assert narrative.position_cell("2.40", 9) == "2.4 across 9 answers"
    assert narrative.position_cell("1.00", 1) == "1.0 across 1 answer"
    assert "does not enter the score" in narrative.POSITION_COPY["note"]

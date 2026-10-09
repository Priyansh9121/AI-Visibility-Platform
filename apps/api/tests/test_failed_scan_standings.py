"""A FAILED scan's report serves no per-engine standings, and the rest of its proof is as before.

Checklist 3.37, extending 3.24 (`test_failed_scan_hidden.py`) and 3.30
(`test_failed_scan_fixes_and_audit.py`). Founder decision (final): "proof.crossEngine.standings[]
(and anything else score-like the report still carries) is withheld for a FAILED scan, through
services/failed_scan.py."

WHAT A STANDING IS. One engine's mention rate and sentiment, from `scoring.mention_rate` and
`scoring.sentiment_score` on that engine's answers. The sentiment one is the Sentiment dimension's
own function over its own population (every answered result), so the standings' sentiments,
weighted by how many of each engine's answers named the subject, add up to the Sentiment sub-score
that a FAILED scan does not show. `_rebuilt_sentiment` below does that sum, on a scan that has not
failed, to show the concern is real before it shows it is gone.

THE SHAPE. `standings` is `[]`. The web draws no cross-engine section for an empty list
(`ReportView.tsx`, `CrossEngineReading`), the same as for a scan with no answers, and no schema
field becomes nullable. What stays is every fact: the counts, `engineCoverage`, `splits`,
`comparablePrompts` and `agreementRate` (the share of comparable questions that were not splits).

THE CONTRAST. One scan is built and only its status is changed, so the report that serves the
standings and the one that does not differ in nothing else: the rows are the same rows. The
PDF has never drawn the standings (it prints `engineCoverage`'s counts), so its half is held from
two sides: the `ReportOut` the renderer is handed has none for a FAILED scan, which is all a
renderer that did draw them would have to go on, and its text carries no figure that sums to the
withheld sub-score.

NOTHING IS WRITTEN. The stored rows are read as they always were, and each is compared column by
column before and after the reads (scoring-spec.md rule 5).

THE INVENTORY. Every field of the report that can carry a number (a Decimal, a float, an int, or a
container that does not say what it holds) is classified below as withheld for a FAILED scan or
served as a fact, with the reason. It is read from the schema's types, descending into models,
lists, tuples, sets, dicts and unions, and a type it cannot read fails it. A new field fails
`test_every_number_in_the_report_is_classified` until someone decides which it is.
"""

from __future__ import annotations

import collections.abc
import enum
import re
import types
import uuid
import zlib
from collections import deque
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Union, get_args, get_origin

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, Field, create_model
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy import update as sa_update

from avp_api.models import (
    ActionItem,
    BrandMention,
    Citation,
    Competitor,
    CompetitorSet,
    EngineResult,
    Prompt,
    Scan,
    ScanStatus,
    Score,
    TechnicalAudit,
    TechnicalAuditCheck,
)
from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.schemas.report import (
    CrossEngineOut,
    EngineStandingOut,
    ReportAuditOut,
    ReportOut,
    ReportProofOut,
    SplitPromptOut,
)
from avp_api.services import report as report_service
from avp_api.services import report_pdf as pdf_service
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.failed_scan import withhold_score_like_proof
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import SCAN_DEADLINE_EXCEEDED

BASE = "/api/v1"

SUBJECT_NAMED = "Zendesk is popular. Help Scout is simpler and well liked."
SUBJECT_NOT_NAMED = "Zendesk is popular and Freshdesk is cheaper."

# The four prompts run across three engines. `list(PromptIntent)[i % 3]` gives them the intents
# awareness, comparison, bottom_funnel, awareness, so the Mention Rate dimension (awareness only)
# and the standings' mention rates (every prompt) are different numbers on this data, as they are
# on a real scan.
LABELS: dict[tuple[Engine, int], Sentiment] = {
    # 100, 100, 50, 0 -> 62.50
    (Engine.CLAUDE, 0): Sentiment.POSITIVE,
    (Engine.CLAUDE, 1): Sentiment.POSITIVE,
    (Engine.CLAUDE, 2): Sentiment.NEUTRAL,
    (Engine.CLAUDE, 3): Sentiment.NEGATIVE,
    # 0, 50, 50, 50 -> 37.50
    (Engine.CLAUDE_SEARCH, 0): Sentiment.NEGATIVE,
    (Engine.CLAUDE_SEARCH, 1): Sentiment.NEUTRAL,
    (Engine.CLAUDE_SEARCH, 2): Sentiment.NEUTRAL,
    (Engine.CLAUDE_SEARCH, 3): Sentiment.NEUTRAL,
    # 100, 50, 50 -> 66.67 (it answers prompt 3 without naming the subject, so no label there)
    (Engine.CHATGPT, 0): Sentiment.POSITIVE,
    (Engine.CHATGPT, 1): Sentiment.NEUTRAL,
    (Engine.CHATGPT, 2): Sentiment.NEUTRAL,
}
WITHOUT_THE_SUBJECT = {(Engine.CHATGPT, 3)}

# What the report's standings say about that scan, in the engine order the API serves them.
STANDINGS = [
    {"engine": "chatgpt", "answered": 4, "mentioned": 3, "mentionRate": "75.00",
     "sentiment": "66.67"},
    {"engine": "claude", "answered": 4, "mentioned": 4, "mentionRate": "100.00",
     "sentiment": "62.50"},
    {"engine": "claude_search", "answered": 4, "mentioned": 4, "mentionRate": "100.00",
     "sentiment": "37.50"},
]
# The whole scan's Sentiment dimension: (250 + 150 + 200) over the 11 answers that named it.
SENTIMENT_SUB_SCORE = Decimal("54.55")
# Every figure a FAILED scan's payload must not carry: the sub-score and what adds up to it.
WITHHELD_SENTIMENTS = [SENTIMENT_SUB_SCORE, *(Decimal(s["sentiment"]) for s in STANDINGS)]


async def _sign_up(client: AsyncClient, email: str = "failedstandings@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Failed Standings Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def engines_that_differ(monkeypatch):  # noqa: ANN001, ANN201
    """Three engines whose answers differ in both ways a standing carries.

    chatgpt leaves the subject out of one answer, so its mention rate is 75; the labels the
    sentiment call returns differ by engine and by prompt, so each engine's sentiment is its own
    and none is a round number that a coincidence in the payload could match.
    """

    def _prompt_index(text: str) -> int:
        return int(text.removeprefix("question "))

    async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
        return [
            GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
            for i in range(4)
        ], "stub"

    async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
        return [
            EngineAnswer(
                engine=engine,
                engine_version="stub",
                prompt_text=prompt,
                text=(
                    SUBJECT_NOT_NAMED
                    if (engine, _prompt_index(prompt)) in WITHOUT_THE_SUBJECT
                    else SUBJECT_NAMED
                ),
                citations=(
                    [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                    if engine is Engine.CLAUDE_SEARCH
                    else []
                ),
                latency_ms=5,
            )
            for engine in engines
        ]

    async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
        return LABELS[(answer.engine, _prompt_index(answer.prompt_text))], Decimal("0.900")

    monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
    monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
    monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)


@pytest.fixture
def pdf_inputs(monkeypatch) -> list[ReportOut]:  # noqa: ANN001
    """The `ReportOut` each PDF render is handed, in order: all a renderer has to draw from."""
    seen: list[ReportOut] = []
    real = pdf_service.render_report_pdf

    def spy(report: ReportOut) -> bytes:
        seen.append(report)
        return real(report)

    monkeypatch.setattr(pdf_service, "render_report_pdf", spy)
    return seen


async def _a_scan(client: AsyncClient) -> str:
    """A scan that ran the whole chain: scored, audited, with its competitor set and fixes."""
    cid = (
        await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    ).json()["id"]
    resp = await client.post(
        f"{BASE}/clients/{cid}/scans", json={"engines": ["claude", "claude_search", "chatgpt"]}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


async def _set_status(session, sid: str, status: ScanStatus) -> None:  # noqa: ANN001
    """Change the status and nothing else: the same rows, read under another status."""
    await session.execute(sa_update(Scan).where(Scan.id == sid).values(status=status))
    await session.commit()


async def _end(session, sid: str, status: ScanStatus) -> None:  # noqa: ANN001
    """End a scan the way a deadline does."""
    await session.execute(
        sa_update(Scan)
        .where(Scan.id == sid)
        .values(status=status, error_code=SCAN_DEADLINE_EXCEEDED, finished_at=datetime.now(UTC))
    )
    await session.commit()


async def _report(client: AsyncClient, sid: str) -> dict[str, Any]:
    resp = await client.get(f"{BASE}/scans/{sid}/report")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _proof_without_standings(report: dict[str, Any]) -> dict[str, Any]:
    """The proof beat less the one part this item withholds: everything that must stay."""
    proof = {k: v for k, v in report["proof"].items() if k != "crossEngine"}
    cross = {k: v for k, v in report["proof"]["crossEngine"].items() if k != "standings"}
    return {**proof, "crossEngine": cross}


def _visible_text(pdf: bytes) -> str:
    """What the PDF draws, on one line: its streams are compressed and its lines wrap."""
    out: list[str] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for literal in re.finditer(rb"\((.*?)\) Tj", content, re.S):
            out.append(literal.group(1).decode("latin-1"))
    return " ".join(" ".join(out).split())


def _leaves(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar in a JSON payload with its path, a list's items sharing one `[]` step."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _leaves(inner, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for inner in value:
            yield from _leaves(inner, f"{path}[]")
    else:
        yield path, value


def _valued_paths(report: dict[str, Any], word: str) -> list[str]:
    """The path of every field whose name has `word` in it (any case) and which has a value."""
    return [p for p, leaf in _leaves(report) if word in p.lower() and leaf is not None]


def _numbers(report: dict[str, Any]) -> list[Decimal]:
    """Every number the payload carries, as a Decimal, however it is sent: a number or a string."""
    found: list[Decimal] = []
    for _, leaf in _leaves(report):
        if isinstance(leaf, bool) or leaf is None:
            continue
        try:
            found.append(Decimal(str(leaf)))
        except InvalidOperation:
            continue
    return found


def _rebuilt_sentiment(report: dict[str, Any]) -> Decimal | None:
    """What the standings add up to: each sentiment weighted by the answers that named the subject.

    None when the payload gives no terms to add.
    """
    terms = [
        (Decimal(s["sentiment"]), s["mentioned"])
        for s in report["proof"]["crossEngine"]["standings"]
        if s["sentiment"] is not None
    ]
    weight = sum(w for _, w in terms)
    if not weight:
        return None
    return sum((value * w for value, w in terms), Decimal(0)) / Decimal(weight)


async def _rows(session, model, column: str, values: list[str]) -> list[dict[str, Any]]:  # noqa: ANN001
    """Every column of every row whose `column` is among `values`, by a Core select.

    Not the ORM, so the session's identity map cannot answer for the database: what comes back is
    what is stored.
    """
    table = model.__table__
    rows = (
        await session.execute(
            select(table).where(table.c[column].in_(values)).order_by(table.c.id)
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _everything_the_report_reads(session, sid: str) -> dict[str, list[dict[str, Any]]]:  # noqa: ANN001
    """Every stored row `build_report` reads for a scan, every column of each."""
    out = {"scans": await _rows(session, Scan, "id", [sid])}
    for model in (EngineResult, Score, CompetitorSet, TechnicalAudit, ActionItem):
        out[model.__tablename__] = await _rows(session, model, "scan_id", [sid])
    result_ids = [row["id"] for row in out["engine_results"]]
    out["brand_mentions"] = await _rows(session, BrandMention, "engine_result_id", result_ids)
    out["citations"] = await _rows(session, Citation, "engine_result_id", result_ids)
    out["prompts"] = await _rows(
        session, Prompt, "id", sorted({row["prompt_id"] for row in out["engine_results"]})
    )
    out["competitors"] = await _rows(
        session, Competitor, "competitor_set_id", [row["id"] for row in out["competitor_sets"]]
    )
    out["technical_audit_checks"] = await _rows(
        session, TechnicalAuditCheck, "audit_id", [row["id"] for row in out["technical_audits"]]
    )
    return out


class TestTheStandingsAreWithheld:
    async def test_a_failed_scans_standings_are_empty_and_every_fact_beside_them_is_served(
        self, client: AsyncClient, session, engines_that_differ
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client)
        shown = await _report(client, sid)
        # Not vacuous: until the scan fails the report carries each engine's own figures, and
        # they are not round numbers a coincidence could produce.
        assert shown["scanStatus"] == "succeeded"
        assert shown["proof"]["crossEngine"]["standings"] == STANDINGS

        await _end(session, sid, ScanStatus.FAILED)
        body = await _report(client, sid)

        assert body["scanStatus"] == "failed"
        assert body["proof"]["crossEngine"]["standings"] == []
        # Every other thing in the proof beat is what it was: the counts, the per-engine
        # coverage, the citations, the shelf, and the rest of the cross-engine reading.
        assert _proof_without_standings(body) == _proof_without_standings(shown)
        cross = body["proof"]["crossEngine"]
        assert cross["comparablePrompts"] == 4
        assert len(cross["splits"]) == 1
        assert cross["agreementRate"] == "75.00"
        coverage = {
            c["engine"]: (c["answered"], c["mentioned"]) for c in body["proof"]["engineCoverage"]
        }
        assert coverage == {"chatgpt": (4, 3), "claude": (4, 4), "claude_search": (4, 4)}

    async def test_failed_is_the_only_status_whose_standings_are_withheld(
        self, client: AsyncClient, session, engines_that_differ
    ) -> None:  # noqa: ANN001
        """The same rows under every status: only FAILED loses them, and they come back."""
        await _sign_up(client)
        sid = await _a_scan(client)
        served = (await _report(client, sid))["proof"]
        assert served["crossEngine"]["standings"] == STANDINGS

        for status in ScanStatus:
            await _set_status(session, sid, status)
            proof = (await _report(client, sid))["proof"]
            expected = [] if status is ScanStatus.FAILED else STANDINGS
            assert proof["crossEngine"]["standings"] == expected, status.value
            assert proof["engineCoverage"] == served["engineCoverage"], status.value

        # FAILED was a read-time rule, so the scan that stops being FAILED is served in full.
        await _set_status(session, sid, ScanStatus.SUCCEEDED)
        assert (await _report(client, sid))["proof"] == served

    @pytest.mark.parametrize("ending", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL])
    async def test_a_scan_that_did_not_fail_keeps_its_standings_in_the_signed_in_report(
        self, client: AsyncClient, session, engines_that_differ, ending: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client)
        if ending is not ScanStatus.SUCCEEDED:
            await _end(session, sid, ending)

        body = await _report(client, sid)

        assert body["scanStatus"] == ending.value
        assert body["proof"]["crossEngine"]["standings"] == STANDINGS

    async def test_the_public_builder_withholds_them_too(
        self, client: AsyncClient, session, engines_that_differ
    ) -> None:  # noqa: ANN001
        """The share routes refuse a FAILED scan (3.29); if that ever loosened, so would this.

        `build_public_report` is `build_report` with two fields blanked, so the standings are
        withheld below the route and no caller has to remember to.
        """
        await _sign_up(client)
        sid = await _a_scan(client)
        await _end(session, sid, ScanStatus.PARTIAL)
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        kept = await report_service.build_public_report(session, scan)
        assert [s.engine.value for s in kept.proof.cross_engine.standings] == [
            "chatgpt", "claude", "claude_search",
        ]

        await _end(session, sid, ScanStatus.FAILED)
        await session.refresh(scan)
        withheld = await report_service.build_public_report(session, scan)

        assert withheld.proof.cross_engine.standings == []
        assert withheld.proof.cross_engine.splits == kept.proof.cross_engine.splits
        assert withheld.proof.cross_engine.comparable_prompts == 4


class TestThePdf:
    async def test_the_pdf_of_a_failed_scan_is_handed_no_standings_and_prints_no_sentiment_figure(
        self, client: AsyncClient, session, engines_that_differ, pdf_inputs
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client)
        printed: dict[ScanStatus, str] = {}
        handed: dict[ScanStatus, list[EngineStandingOut]] = {}
        for status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED):
            await _end(session, sid, status)
            resp = await client.get(f"{BASE}/scans/{sid}/report.pdf")
            assert resp.status_code == 200, resp.text
            printed[status] = _visible_text(resp.content)
            handed[status] = pdf_inputs[-1].proof.cross_engine.standings

        # What a renderer is handed: the same standings for the two statuses that count, and
        # none for FAILED.
        assert len(pdf_inputs) == 3
        assert [s.sentiment for s in handed[ScanStatus.SUCCEEDED]] == [
            Decimal("66.67"), Decimal("62.50"), Decimal("37.50"),
        ]
        assert handed[ScanStatus.PARTIAL] == handed[ScanStatus.SUCCEEDED]
        assert handed[ScanStatus.FAILED] == []

        # What it prints. The two statuses that count print the Sentiment sub-score in the
        # score table, so its absence from the FAILED one's is a difference and not a habit.
        for status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL):
            assert str(SENTIMENT_SUB_SCORE) in printed[status], status.value
        assert "This scan failed, so it has no score." in printed[ScanStatus.FAILED]
        for figure in WITHHELD_SENTIMENTS:
            assert str(figure) not in printed[ScanStatus.FAILED], figure


class TestNothingIsWritten:
    async def test_the_stored_rows_are_unchanged_column_by_column(
        self, client: AsyncClient, session, engines_that_differ, pdf_inputs
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client)
        await _end(session, sid, ScanStatus.FAILED)
        before = await _everything_the_report_reads(session, sid)
        # Not vacuous: there is a row in every table the report reads, the labels the standings
        # were computed from and the Sentiment sub-score are stored, and the scan is FAILED.
        assert all(before.values()), [name for name, rows in before.items() if not rows]
        assert before["scans"][0]["status"] == ScanStatus.FAILED
        assert {r["sentiment"] for r in before["engine_results"]} >= {
            Sentiment.POSITIVE, Sentiment.NEUTRAL, Sentiment.NEGATIVE,
        }
        assert before["scores"][0]["sentiment"] == SENTIMENT_SUB_SCORE

        body = await _report(client, sid)
        pdf = await client.get(f"{BASE}/scans/{sid}/report.pdf")
        assert pdf.status_code == 200, pdf.text
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        public = await report_service.build_public_report(session, scan)
        await session.commit()

        # The reads withheld the standings, and wrote nothing.
        assert body["proof"]["crossEngine"]["standings"] == []
        assert pdf_inputs[-1].proof.cross_engine.standings == []
        assert public.proof.cross_engine.standings == []
        after = await _everything_the_report_reads(session, sid)
        assert after.keys() == before.keys()
        for table in before:
            assert after[table] == before[table], table


class TestTheSentimentSubScoreCannotBeRebuilt:
    """The security review's concern: a weighted sum of the standings rebuilds the sub-score."""

    async def test_the_standings_rebuild_it_until_the_scan_fails_and_then_nothing_does(
        self, client: AsyncClient, session, engines_that_differ
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client)
        shown = await _report(client, sid)
        stored = Decimal(shown["score"]["sentiment"])
        assert stored == SENTIMENT_SUB_SCORE
        # The concern is real on this data: the standings add up to the stored sub-score, to
        # within the rounding of the figures they carry.
        rebuilt = _rebuilt_sentiment(shown)
        assert rebuilt is not None
        assert abs(rebuilt - stored) <= Decimal("0.01")
        shown_numbers = _numbers(shown)
        for figure in WITHHELD_SENTIMENTS:
            assert figure in shown_numbers, figure

        await _end(session, sid, ScanStatus.FAILED)
        body = await _report(client, sid)

        # No score, no standings: there is nothing to add up.
        assert body["score"] is None
        assert body["dimensions"] == []
        assert _rebuilt_sentiment(body) is None
        # A rebuild needs a sentiment figure to weigh, and the payload has none: no field named
        # for sentiment has a value, and neither the sub-score nor any engine's own sentiment is
        # among the numbers it sends, whatever the field. That is a check on those four figures
        # and on the names, not a proof about every way a label could be encoded; what keeps a
        # new number from arriving unclassified is `TestEveryNumberInTheReportIsClassified`.
        assert _valued_paths(body, "sentiment") == []
        numbers = _numbers(body)
        for figure in WITHHELD_SENTIMENTS:
            assert figure not in numbers, figure
        # The mention rates the standings carried go the same way: no mention-rate field has a
        # value (a rival's are null for a FAILED scan since 3.24).
        assert _valued_paths(body, "mentionrate") == []
        # What remains for each engine is two counts, and counts carry no sentiment: they are
        # the weights of the sum with none of the terms.
        assert {c["engine"] for c in body["proof"]["engineCoverage"]} == {
            "chatgpt", "claude", "claude_search",
        }


# THE INVENTORY. Every field of the report that can carry a number is listed here, either as
# withheld for a FAILED scan or as served, with the reason. What can carry a number is read from the
# types (`_number_paths`): a Decimal, a float or an int, or a container declared without saying what
# it holds (a bare `dict`, which could hold anything). A score this codebase builds is a Decimal
# (scoring-spec.md rule 3); the ints are counts and ordinals, and each is listed as a fact so that a
# new one is a decision too. A field in neither list fails
# `test_every_number_in_the_report_is_classified` until it is put in one, which is the choice 3.37
# asked to be made for each number the report carries. Booleans, strings, dates and enum members
# cannot carry a number and are not listed. A type the guard cannot read fails it, so that no shape
# hides one.
WITHHELD_FOR_A_FAILED_SCAN = (
    "score",  # null: the Score row is not read (3.24); weights and excluded dimensions with it
    "dimensions[]",  # [] (3.24)
    "actionItems[]",  # [] (3.30): the generator was handed the score; its points are arithmetic
    "audit.technicalFoundation",  # null (3.30)
    "competitorSet.competitors[].mentionRate",  # null: the comparison is not derived (3.24)
    "competitorSet.competitors[].shareOfVoice",
    "competitorSet.competitors[].citationStrength",
    "competitorSet.competitors[].averagePosition",
    "competitorSet.competitors[].positionCount",  # sent as 0: the count the average is over
    "proof.crossEngine.standings[]",  # [] (3.37): built by the Mention Rate and Sentiment functions
)
SERVED_FOR_A_FAILED_SCAN: dict[str, str] = {
    # Counts of where the questions came from (F5).
    "promptOrigin.fromGenerator": (
        "a count of the questions the generator wrote and that were kept, stored when the "
        "prompt set was built; a fact about the set, not an input to or output of the score"
    ),
    "promptOrigin.fromFallback": (
        "a count of the questions that came from templates, stored when the prompt set was "
        "built; a fact about the set, not an input to or output of the score"
    ),
    # Decimals that are not scores.
    "competitorSet.detectionConfidence": (
        "the detection step's own agreement between its two signals, written before scoring; "
        "scoring reads the set's status and never this"
    ),
    "competitorSet.competitors[].score": (
        "the ranking score detection ordered the rival by, kept so a set can be explained; it is "
        "not a visibility score"
    ),
    "proof.crossEngine.agreementRate": (
        "the share of comparable questions the engines did not split on: arithmetic over "
        "`splits` and `comparablePrompts`, which stay, and not an input to or output of the score"
    ),
    "audit.findings[].value": (
        "what a check measured on the site (a load time, a layout shift, a content age): a fact "
        "about the site, kept with the checks as 3.30 decided, which withholds the sub-score "
        "and not the evidence"
    ),
    # Ints: counts of what was run, returned, cited or checked, and ordinals as observed.
    **dict.fromkeys(
        [
            "proof.promptsRun",
            "proof.engineResults",
            "proof.answeredResults",
            "proof.resultsMentioningSubject",
            "proof.engineCoverage[].promptsRun",
            "proof.engineCoverage[].answered",
            "proof.engineCoverage[].mentioned",
            "proof.totalCitations",
            "proof.subjectCitations",
            "proof.subjectCitedDomains[].citations",
            "proof.subjectOtherSites[].citations",
            "proof.competitorCitedDomains[].citations",
            "proof.unclaimedCitedDomains[].citations",
            "proof.mentionShares[].appearances",
            "proof.crossEngine.comparablePrompts",
            "audit.passed",
            "audit.warned",
            "audit.failed",
            "audit.notApplicable",
            "competitorSet.confidenceCovers",
            "competitorSet.competitors[].serpMentions",
            "competitorSet.competitors[].coCitationMentions",
            "competitorSet.competitors[].signalCount",
        ],
        "a count of what the scan ran, returned, cited or checked: a fact about what was measured",
    ),
    **dict.fromkeys(
        [
            "proof.mentionShares[].bestPosition",
            "proof.promptShelf[].promptPosition",
            "proof.promptShelf[].subjectPosition",
            "proof.promptShelf[].slots[].position",
            "competitorSet.competitors[].rank",
        ],
        "an ordinal as observed: a place in an answer, in the prompt set, or in detection's order",
    ),
}

_SEQUENCES = (list, set, frozenset, tuple, collections.abc.Sequence, collections.abc.Set)
_MAPPINGS = (dict, collections.abc.Mapping)


def _leaf_kind(annotation: Any) -> str | None:
    """The kind of number a type can carry, or None if it cannot carry one.

    "decimal", "float" and "int" are what they say. "untyped" is a bare container or `Any`, which
    could hold anything. A type this does not know is an AssertionError, not a pass: the guard
    exists for the shape nobody thought of.
    """
    if annotation is Any or annotation is object:
        return "untyped"
    if not isinstance(annotation, type):
        raise AssertionError(f"the guard cannot read the type {annotation!r}")
    if annotation in (dict, list, tuple, set, frozenset):
        return "untyped"
    if issubclass(annotation, bool):
        return None
    if issubclass(annotation, Decimal):
        return "decimal"
    if issubclass(annotation, float):
        return "float"
    if issubclass(annotation, int):
        return "int"
    if issubclass(annotation, (str, bytes, date, time, uuid.UUID, enum.Enum, type(None))):
        return None
    raise AssertionError(f"the guard cannot read the type {annotation!r}")


def _number_paths(model: type[BaseModel], prefix: str = "") -> dict[str, str]:
    """The path of every field reachable from `model` that can carry a number, and its kind.

    A path is camelCase as it is sent. A list, tuple or set adds `[]`; a dict adds `{}` for its
    values and `{key}` for its keys; a model adds `.field`. Unions and `Annotated` are read
    through. A generic it does not handle is an AssertionError (see `_leaf_kind`).
    """
    found: dict[str, str] = {}

    def visit(annotation: Any, path: str) -> None:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is Annotated:
            visit(args[0], path)
        elif origin in (Union, types.UnionType):
            for arg in args:
                visit(arg, path)
        elif origin in _SEQUENCES:
            for arg in args:
                if arg is not Ellipsis:
                    visit(arg, f"{path}[]")
        elif origin in _MAPPINGS:
            key, value = args
            visit(key, f"{path}{{key}}")
            visit(value, f"{path}{{}}")
        elif origin is not None:
            raise AssertionError(f"the guard does not know the generic {origin!r} at {path}")
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found.update(_number_paths(annotation, path))
        else:
            kind = _leaf_kind(annotation)
            if kind is not None:
                found.setdefault(path, kind)

    for name, field in model.model_fields.items():
        visit(field.annotation, f"{prefix}.{to_camel(name)}" if prefix else to_camel(name))
    return found


def _is_under(path: str, parent: str) -> bool:
    """Whether `path` is `parent` or inside it: a field, a list item or a dict value of it."""
    return path == parent or path.startswith((f"{parent}.", f"{parent}[]", f"{parent}{{"))


def _unclassified(reachable: dict[str, str]) -> list[str]:
    """The number-capable paths that are neither withheld for a failed scan nor served as facts."""
    return sorted(
        path
        for path in reachable
        if path not in SERVED_FOR_A_FAILED_SCAN
        and not any(_is_under(path, w) for w in WITHHELD_FOR_A_FAILED_SCAN)
    )


def _values_under(report: Any, path: str) -> list[Any]:
    """Every scalar at or under a classification path, a list's items sharing a `[]` step."""
    return [leaf for leaf_path, leaf in _leaves(report) if _is_under(leaf_path, path)]


def _is_a_value(leaf: Any) -> bool:
    """Anything but null, and a count sent as zero, which is how a withheld count is sent."""
    return leaf is not None and not (
        isinstance(leaf, int) and not isinstance(leaf, bool) and leaf == 0
    )


class TestEveryNumberInTheReportIsClassified:
    def test_every_number_in_the_report_is_classified(self) -> None:
        reachable = _number_paths(ReportOut)
        # Not vacuous: the walk finds the figures this item is about, a count, and the two
        # bare dicts under `score` (which say nothing about what they hold).
        assert reachable["proof.crossEngine.standings[].sentiment"] == "decimal"
        assert reachable["proof.crossEngine.standings[].mentionRate"] == "decimal"
        assert reachable["proof.engineResults"] == "int"
        assert reachable["score.weights"] == "untyped"

        assert _unclassified(reachable) == [], (
            "A field that can carry a number was added to the report. Is it a fact about what was "
            "measured (a count, a position as observed), or built like a score? Put it in "
            "SERVED_FOR_A_FAILED_SCAN with the reason, or in WITHHELD_FOR_A_FAILED_SCAN and "
            "withhold it in services/failed_scan.py."
        )
        # And nothing is listed that the report no longer has.
        gone = [
            p
            for p in (*WITHHELD_FOR_A_FAILED_SCAN, *SERVED_FOR_A_FAILED_SCAN)
            if not any(_is_under(r, p) for r in reachable)
        ]
        assert gone == []

    async def test_a_failed_scans_payload_carries_no_value_at_a_withheld_path(
        self, client: AsyncClient, session, engines_that_differ
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client)
        shown = await _report(client, sid)
        # Not vacuous: until it fails, this scan has a value at each of the paths that is
        # populated on this data, and the figures the standings carry.
        for path in (
            "score", "dimensions[]", "actionItems[]", "audit.technicalFoundation",
            "competitorSet.competitors[].mentionRate", "proof.crossEngine.standings[]",
        ):
            assert [v for v in _values_under(shown, path) if _is_a_value(v)], path

        await _end(session, sid, ScanStatus.FAILED)
        body = await _report(client, sid)

        for path in WITHHELD_FOR_A_FAILED_SCAN:
            assert [v for v in _values_under(body, path) if _is_a_value(v)] == [], path
        # Served, not withheld: each is exactly what it was before the scan failed.
        for path in SERVED_FOR_A_FAILED_SCAN:
            assert _values_under(body, path) == _values_under(shown, path), path
        assert _values_under(body, "proof.crossEngine.agreementRate") == ["75.00"]
        assert [v for v in _values_under(body, "proof.engineCoverage[].answered") if v] == [4, 4, 4]


class _Row(BaseModel):
    weight: Decimal


class _AuditWithComponentScores(ReportAuditOut):
    """A report audit that gained the shape the guard once could not see: a dict of Decimals."""

    component_scores: dict[str, Decimal] = Field(default_factory=dict)


class _ReportWithComponentScores(ReportOut):
    audit: _AuditWithComponentScores | None


class TestTheGuardReadsEveryShapeANumberCanHideIn:
    """`_number_paths` is what `test_every_number_in_the_report_is_classified` stands on.

    It read lists, unions and models only, so a Decimal in a dict, a tuple or a set, a count
    declared as a bare `dict`, and any generic it had no branch for all passed it unseen. These
    hold the shapes it now reads, and that it fails on one it does not.
    """

    def test_it_reads_into_models_lists_tuples_sets_dicts_and_unions(self) -> None:
        class Probe(BaseModel):
            by_engine: dict[str, Decimal]
            pairs: tuple[Decimal, ...]
            fixed: tuple[Decimal, Decimal]
            seen: set[int]
            frozen: frozenset[float]
            rows: list[_Row]
            nested: dict[str, list[_Row]]
            maybe: Decimal | None = None
            wrapped: list[Annotated[Decimal, Field(ge=0)]]
            keyed: dict[int, str]

        assert _number_paths(Probe) == {
            "byEngine{}": "decimal",
            "pairs[]": "decimal",
            "fixed[]": "decimal",
            "seen[]": "int",
            "frozen[]": "float",
            "rows[].weight": "decimal",
            "nested{}[].weight": "decimal",
            "maybe": "decimal",
            "wrapped[]": "decimal",
            "keyed{key}": "int",
        }

    def test_it_names_a_container_that_does_not_say_what_it_holds(self) -> None:
        class Loose(BaseModel):
            weights: dict = Field(default_factory=dict)
            rows: list = Field(default_factory=list)
            anything: Any = None

        assert _number_paths(Loose) == {
            "weights": "untyped", "rows": "untyped", "anything": "untyped",
        }

    def test_it_passes_over_what_cannot_hold_a_number(self) -> None:
        class Quiet(BaseModel):
            flag: bool
            name: str
            when: datetime
            engine: Engine
            codes: list[str]
            labels: dict[str, str]
            maybe_text: str | None = None

        assert _number_paths(Quiet) == {}

    @pytest.mark.parametrize("shape", [deque[Decimal], type[int], timedelta], ids=str)
    def test_it_fails_on_a_shape_it_cannot_read(self, shape: Any) -> None:
        strange = create_model("Strange", value=(shape, ...))

        with pytest.raises(AssertionError, match="the guard (cannot read|does not know)"):
            _number_paths(strange)

    def test_a_dict_typed_decimal_added_to_the_report_is_unclassified(self) -> None:
        """The slip this closes: `components: dict[str, Decimal]` on a report audit.

        The guard did not read into a dict, so the report could have gained its component
        scores, the numbers that add up to the Technical Foundation sub-score, and it would
        have passed. It reads them as `audit.componentScores{}` and fails until they are
        classified.
        """
        reachable = _number_paths(_ReportWithComponentScores)

        assert reachable["audit.componentScores{}"] == "decimal"
        assert _unclassified(_number_paths(ReportOut)) == []
        assert _unclassified(reachable) == ["audit.componentScores{}"]


class TestTheHelper:
    def test_it_empties_the_standings_and_touches_nothing_else(self) -> None:
        proof = ReportProofOut(
            prompts_run=4,
            engine_results=12,
            answered_results=12,
            results_mentioning_subject=11,
            engine_coverage=[],
            cross_engine=CrossEngineOut(
                standings=[
                    EngineStandingOut(
                        engine=Engine.CLAUDE, answered=4, mentioned=4,
                        mention_rate=Decimal("100.00"), sentiment=Decimal("62.50"),
                    ),
                    EngineStandingOut(
                        engine=Engine.CHATGPT, answered=4, mentioned=0,
                        mention_rate=Decimal("0.00"), sentiment=None,
                    ),
                ],
                splits=[
                    SplitPromptOut(
                        prompt_id="prm_1", named_by=[Engine.CLAUDE], missed_by=[Engine.CHATGPT]
                    )
                ],
                comparable_prompts=4,
                agreement_rate=Decimal("75.00"),
            ),
            total_citations=0,
            subject_citations=0,
            subject_cited_domains=[],
            subject_other_sites=[],
            competitor_cited_domains=[],
            mention_shares=[],
        )
        before = proof.model_dump()

        withhold_score_like_proof(proof)

        after = proof.model_dump()
        assert after["cross_engine"]["standings"] == []
        before["cross_engine"]["standings"] = []
        assert after == before

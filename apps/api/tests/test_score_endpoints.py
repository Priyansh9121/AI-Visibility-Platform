"""Scoring endpoints and persistence.

Engine execution is stubbed (Epic 4's stubs) so these exercise the scoring
persistence path without paid model calls. Scoring itself makes no provider
calls at all, so nothing about the score is mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy import update as sa_update

from avp_api.deps import scan_executor
from avp_api.models import Scan, ScanStatus, Score
from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import extraction, scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scoring import FORMULA_VERSION

BASE = "/api/v1"


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN201
    """Stop after the engine phase — Epic 9.17.

    These tests assert how the product renders a MISSING score / audit /
    competitor set / fix list. Since 9.17 a scan started through the endpoint
    runs the whole chain and produces all four, so those absences are no longer
    reachable by simply not asking for them.

    They are still reachable in production — any chained phase can fail, and
    `scan_executor._attempt` deliberately lets the rest continue — so the states
    remain worth testing. This constructs them on purpose instead of relying on
    the product not finishing, which is a more honest setup than the one it
    replaces.
    """
    return engines_only_executor



async def _sign_up(client: AsyncClient, email: str = "score@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Score Test Agency", "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    def _install(n_prompts: int = 4, text: str | None = None):
        generated = [
            GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub"

        answer_text = text if text is not None else (
            "Zendesk is popular. Help Scout is simpler and well liked."
        )

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            out = []
            for engine in engines:
                citations = (
                    [CitedSource(url="https://g2.com/x", domain="g2.com", position=1),
                     CitedSource(url="https://helpscout.com/y", domain="helpscout.com", position=2)]
                    if engine is Engine.CLAUDE_SEARCH else []
                )
                out.append(EngineAnswer(
                    engine=engine, engine_version="stub", prompt_text=prompt,
                    text=answer_text, citations=citations, latency_ms=5))
            return out

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)

    return _install


async def _scan(client: AsyncClient, stub_engines, n: int = 4) -> str:  # noqa: ANN001
    resp = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    cid = resp.json()["id"]
    stub_engines(n_prompts=n)
    return (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]


class TestComputeScore:
    async def test_scores_a_real_scan_and_stores_the_breakdown(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scan(client, stub_engines)

        resp = await client.post(f"{BASE}/scans/{sid}/score")
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["id"].startswith("scor_")
        assert body["status"] == "scored"
        # The CURRENT version, not a literal. test_scoring.py pins the
        # literal in one place so a bump is a deliberate edit there; here
        # the endpoint only has to record whatever that is.
        assert body["formulaVersion"] == FORMULA_VERSION
        assert body["composite"] is not None
        # Sub-score breakdown is stored and retrievable (§7 item 2).
        assert body["mentionRate"] == "100.00"
        assert len(body["inputsDigest"]) == 64

    async def test_decimals_cross_the_wire_as_strings(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A JSON number is an IEEE double — the exact thing rule 3 forbids."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        for field in ("composite", "mentionRate", "sentiment"):
            assert isinstance(body[field], str), f"{field} must be a string"
        # v2.1: Citation Strength is excluded, so it crosses as null — never a
        # zero, and never a string that reads as a measurement.
        assert body["citationStrength"] is None
        assert body["excludedDimensions"]["citation_strength"] == "NO_AUTHORITY_DATA"
        assert all(c["citationStrength"] is None for c in body["competitors"])

    async def test_average_position_is_reported_beside_the_score_and_not_in_it(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """v4 section 3. The stub answer names Zendesk first and Help Scout second."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert body["averagePosition"] is not None
        assert isinstance(body["averagePosition"], str)  # a decimal, as strings
        assert body["positionCount"] > 0
        # Not a dimension: no weight, no sub-score, not excluded either.
        assert "average_position" not in body["weights"]
        assert "average_position" not in body["excludedDimensions"]
        # And the same figures come back on the read path, derived on read.
        again = (await client.get(f"{BASE}/scans/{sid}/score")).json()
        assert again["averagePosition"] == body["averagePosition"]
        assert again["positionCount"] == body["positionCount"]

    async def test_technical_foundation_excluded_with_its_own_reason(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """'Not yet checked' must not be reported as 'nothing found'."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert body["technicalFoundation"] is None
        assert body["excludedDimensions"]["technical_foundation"] == "NOT_YET_MEASURED"
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" in body["degradationFlags"]

    async def test_no_competitor_set_excludes_share_of_voice_visibly(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """v1.1 deviation: a detection failure must not earn 25 points."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert body["shareOfVoice"] is None
        assert body["excludedDimensions"]["share_of_voice"] == "NO_COMPETITOR_SET"
        assert "NO_COMPETITOR_SET" in body["degradationFlags"]

    async def test_stored_weights_are_the_effective_ones(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A breakdown must re-sum against its own weights."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        weights = {k: Decimal(v) for k, v in body["weights"].items()}
        assert "share_of_voice" not in weights, "excluded dimensions carry no weight"
        assert "citation_strength" not in weights, "excluded since v2.1"
        assert abs(sum(weights.values()) - Decimal("100")) < Decimal("0.01")

        camel = {
            "mention_rate": "mentionRate", "share_of_voice": "shareOfVoice",
            "citation_strength": "citationStrength", "sentiment": "sentiment",
            "technical_foundation": "technicalFoundation",
        }
        # Whatever survived the exclusions re-sums to the composite against
        # its own weights — the breakdown is auditable by a reader.
        rebuilt = sum(
            (w * Decimal(body[camel[k]]) for k, w in weights.items()),
            Decimal("0"),
        ) / Decimal("100")
        assert abs(rebuilt - Decimal(body["composite"])) < Decimal("0.01")

    async def test_rescoring_is_idempotent_per_formula_version(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """One row per (scan, formula_version), enforced by a unique constraint.

        scoring-spec.md rule 5 versions the FORMULA, not the invocation. Because
        scoring is deterministic, re-running it under the same formula against
        the same inputs yields an identical result — storing N identical rows
        would be noise, not history. A formula bump is what creates a new row.
        """
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        first = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        second = (await client.post(f"{BASE}/scans/{sid}/score")).json()

        assert first["id"] == second["id"], "same formula version reuses the row"
        assert first["inputsDigest"] == second["inputsDigest"]
        assert first["composite"] == second["composite"]

        history = (await client.get(f"{BASE}/scans/{sid}/scores")).json()
        assert len(history) == 1

    async def test_a_prior_formula_version_survives_rescoring(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """Rows for other formula versions are never touched.

        This is the property Epic 11's before/after reporting depends on: a
        formula change must add a row, not replace the history. Simulated by
        writing a v1.0 row directly, then scoring under the current version.
        """
        from avp_api import ids
        from avp_api.models import Score
        from avp_api.models.score import ScoreStatus

        await _sign_up(client)
        sid = await _scan(client, stub_engines)

        session.add(Score(
            id=ids.new_id(ids.SCORE), scan_id=sid, status=ScoreStatus.SCORED,
            composite=Decimal("41.00"), formula_version="v1.0-historic",
            weights={}, excluded_dimensions={}, degradation_flags=[],
        ))
        await session.commit()

        current = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert current["formulaVersion"] == FORMULA_VERSION

        history = (await client.get(f"{BASE}/scans/{sid}/scores")).json()
        assert {h["formulaVersion"] for h in history} == {
            "v1.0-historic",
            FORMULA_VERSION,
        }
        historic = next(h for h in history if h["formulaVersion"] == "v1.0-historic")
        assert historic["composite"] == "41.00", "prior version untouched"

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        from avp_api import ids

        resp = await client.post(f"{BASE}/scans/{ids.new_id(ids.SCAN)}/score")
        assert resp.status_code == 401


class TestReadScore:
    async def test_get_before_scoring_is_404(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        resp = await client.get(f"{BASE}/scans/{sid}/score")
        assert resp.status_code == 404
        assert "not been scored" in resp.json()["detail"]

    async def test_get_returns_the_latest_score(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/score")
        latest = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        got = (await client.get(f"{BASE}/scans/{sid}/score")).json()
        assert got["id"] == latest["id"]

    async def test_another_agency_cannot_score_or_read(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        from httpx import ASGITransport
        from httpx import AsyncClient as Second

        await _sign_up(client, "one@scoreiso.example")
        sid = await _scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/score")
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        # The second agency's browser writes too, so it sends the web app's Origin.
        async with Second(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await _sign_up(other, "two@scoreiso.example")
            assert (await other.get(f"{BASE}/scans/{sid}/score")).status_code == 404
            assert (await other.post(f"{BASE}/scans/{sid}/score")).status_code == 404


class TestInsufficientData:
    async def test_a_scan_with_no_answered_results_scores_null(
        self, client: AsyncClient
    ) -> None:
        """An unrunnable scan must never render as a bad score.

        This scan has run nothing, so no engine call answered. It used to be a
        scan whose every engine call failed, but that scan ends FAILED, and a
        FAILED scan has no score (checklist 3.30): `test_failed_scan_score_routes.py`
        pins what these routes say about it, a 409 that writes nothing.
        """
        await _sign_up(client)
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]

        class _Idle:
            """An executor that never runs the scan, so it stays queued."""

            async def submit(self, job, *, settings) -> None:  # noqa: ANN001, ARG002
                return None

        client._transport.app.dependency_overrides[scan_executor] = lambda: _Idle()  # noqa: SLF001
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert (await client.get(f"{BASE}/scans/{sid}")).json()["status"] == "queued"

        resp = await client.post(f"{BASE}/scans/{sid}/score")
        body = resp.json()

        assert resp.status_code == 201, resp.text
        assert body["status"] == "insufficient_data"
        assert body["composite"] is None
        assert body["reasonCode"] == "INSUFFICIENT_DATA"


class TestReScoringUnderANewFormulaVersion:
    """Re-scoring is an explicit act that ADDS a row and says so.

    Scoring v2 changed what Mention Rate means, so a scan scored under v1.1 and
    re-scored today produces a different composite from the same engine results.
    Rule 5 keeps both rows; these tests are about the half rule 5 does not cover
    on its own — that a reader is told the definition moved, rather than left to
    conclude their business did.
    """

    async def test_re_scoring_inserts_and_leaves_the_earlier_row_intact(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        from avp_api import ids
        from avp_api.models import Score, ScoreStatus

        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        session.add(Score(
            id=ids.new_id(ids.SCORE), scan_id=sid, status=ScoreStatus.SCORED,
            composite=Decimal("28.89"), formula_version="v1.1",
            weights={}, excluded_dimensions={}, degradation_flags=[],
        ))
        await session.commit()

        fresh = (await client.post(f"{BASE}/scans/{sid}/score")).json()

        assert fresh["formulaVersion"] == FORMULA_VERSION
        history = (await client.get(f"{BASE}/scans/{sid}/scores")).json()
        assert {h["formulaVersion"] for h in history} == {"v1.1", FORMULA_VERSION}
        old = next(h for h in history if h["formulaVersion"] == "v1.1")
        assert old["composite"] == "28.89", "the earlier definition's record was rewritten"

    async def test_the_score_names_the_definition_it_superseded(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The number moved because the formula did, and the payload says so.

        Without this the only reading available to a client is "the score
        dropped", which is a claim about their business. The true one is "the
        definition changed", which is a claim about ours.
        """
        from avp_api import ids
        from avp_api.models import Score, ScoreStatus

        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        session.add(Score(
            id=ids.new_id(ids.SCORE), scan_id=sid, status=ScoreStatus.SCORED,
            composite=Decimal("28.89"), formula_version="v1.1",
            weights={}, excluded_dimensions={}, degradation_flags=[],
        ))
        await session.commit()

        fresh = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert fresh["previousFormulaVersions"] == ["v1.1"]

        # And on every surface that shows the score, not just the one that made it.
        assert (await client.get(f"{BASE}/scans/{sid}/score")).json()[
            "previousFormulaVersions"
        ] == ["v1.1"]
        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert report["score"]["previousFormulaVersions"] == ["v1.1"]

    async def test_a_v4_row_is_not_re_scored_under_the_current_formula(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """v5 redefined Citation Strength (2026-09-29); every v4 number stays as scored.

        A v4 row with a full breakdown, written directly, then scored twice
        under the current formula: a current-version row is added once and refreshed in
        place, and every field of the v4 row reads exactly as written.
        """
        from avp_api import ids
        from avp_api.models import Score, ScoreStatus

        assert FORMULA_VERSION == "v8"
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        v4 = {
            "composite": "77.47", "mentionRate": "100.00", "shareOfVoice": "41.18",
            "citationStrength": "100.00", "sentiment": "83.33", "technicalFoundation": None,
            "inputsDigest": "4" * 64, "weights": {"citation_strength": "22.22"},
        }
        session.add(Score(
            id=ids.new_id(ids.SCORE), scan_id=sid, status=ScoreStatus.SCORED,
            formula_version="v4", composite=Decimal(v4["composite"]),
            mention_rate=Decimal(v4["mentionRate"]), share_of_voice=Decimal(v4["shareOfVoice"]),
            citation_strength=Decimal(v4["citationStrength"]),
            sentiment=Decimal(v4["sentiment"]), inputs_digest=v4["inputsDigest"],
            weights=v4["weights"], excluded_dimensions={}, degradation_flags=[],
        ))
        await session.commit()

        first = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        second = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert first["formulaVersion"] == second["formulaVersion"] == FORMULA_VERSION
        assert first["id"] == second["id"], "one current-version row, refreshed in place"
        assert second["previousFormulaVersions"] == ["v4"]

        history = (await client.get(f"{BASE}/scans/{sid}/scores")).json()
        assert sorted(h["formulaVersion"] for h in history) == ["v4", FORMULA_VERSION]
        kept = next(h for h in history if h["formulaVersion"] == "v4")
        assert {key: kept[key] for key in v4} == v4, "the v4 row was re-scored"

    async def test_a_scan_scored_once_names_nothing(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Most scans. An empty list, never a note about a change that did not happen."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)

        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()

        assert body["previousFormulaVersions"] == []

    async def test_re_scoring_twice_under_one_version_does_not_report_itself(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Rule 5 keys on (scan, version), so a second run at the same version
        updates that row rather than adding one. Nothing changed definition, so
        there is nothing to disclose."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)

        await client.post(f"{BASE}/scans/{sid}/score")
        again = (await client.post(f"{BASE}/scans/{sid}/score")).json()

        assert again["previousFormulaVersions"] == []
        history = (await client.get(f"{BASE}/scans/{sid}/scores")).json()
        assert len(history) == 1


# The sentence the founder decided on (checklist 3.97), pinned here as text rather than imported
# from the router: rewording what an agency reads is a deliberate edit in both places.
MATCHED_UNDER_AN_EARLIER_RULE = (
    "This scan's brand matches were made under an earlier rule, and answer text is not kept, "
    "so it cannot be re-scored under the current one. Run a new scan instead."
)
FAILED_NOT_SCORABLE = "This scan failed, so it cannot be scored."


async def _set_matcher_version(session, sid: str, version: str) -> None:  # noqa: ANN001
    await session.execute(sa_update(Scan).where(Scan.id == sid).values(matcher_version=version))
    await session.commit()


async def _stored_matcher_version(session, sid: str) -> str:  # noqa: ANN001
    return (
        await session.execute(select(Scan.matcher_version).where(Scan.id == sid))
    ).scalar_one()


async def _score_rows(session, sid: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """Every column of every Score row for a scan, oldest id first.

    A Core select, so the session's identity map cannot answer for the database. `computed_at`
    is in it, and scoring the scan again would move it: equality says "not scored, not touched".
    """
    table = Score.__table__
    rows = (
        await session.execute(select(table).where(table.c.scan_id == sid).order_by(table.c.id))
    ).mappings().all()
    return [dict(row) for row in rows]


async def _add_a_v5_row(session, sid: str) -> None:  # noqa: ANN001
    """A row under the formula the scan was first scored by, before v7 existed."""
    from avp_api import ids
    from avp_api.models import ScoreStatus

    session.add(Score(
        id=ids.new_id(ids.SCORE), scan_id=sid, status=ScoreStatus.SCORED,
        formula_version="v5", composite=Decimal("63.04"), mention_rate=Decimal("66.67"),
        share_of_voice=Decimal("41.18"), sentiment=Decimal("83.33"), inputs_digest="5" * 64,
        weights={}, excluded_dimensions={}, degradation_flags=[],
    ))
    await session.commit()


class TestAScanMatchedUnderAnEarlierRuleIsNotReScored:
    """Checklist 3.97, the founder's decision: an explicit re-score of such a scan is refused.

    v7 (2026-10-07) changed BRAND MATCHING, not only the arithmetic: `extraction.find_brand` began
    to find a 2-4 character short form written in capitals. Answer text is never stored (facts
    only), so a scan matched before v7 can never be matched again, and `POST /scans/{scanId}/score`
    over its stored matches wrote a row labelled v7 from matches v7 did not make. That is now a 409
    and no row.

    What a scan was matched by is `Scan.matcher_version`: stamped by the run itself, 'pre-v7' where
    nothing recorded it. The agency check and the FAILED check both come first.
    """

    async def test_a_scan_matched_before_v7_is_refused_and_its_score_rows_are_untouched(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        # Not vacuous: scored while its matcher is current, it gets its v7 row. It also keeps a v5
        # row, which is the state a scan first scored before v7 and then re-scored after it is in.
        assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
        await _add_a_v5_row(session, sid)
        before = await _score_rows(session, sid)
        assert sorted(row["formula_version"] for row in before) == ["v5", FORMULA_VERSION]
        await _set_matcher_version(session, sid, "pre-v7")

        resp = await client.post(f"{BASE}/scans/{sid}/score")

        assert resp.status_code == 409, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == "/problems/conflict"
        assert body["detail"] == MATCHED_UNDER_AN_EARLIER_RULE
        # Scoring again would refresh the v7 row in place and move `computed_at`; equality, column
        # for column, says it was neither scored nor touched, and no row was added.
        assert await _score_rows(session, sid) == before

    async def test_a_scan_scored_only_before_v7_gets_no_row_under_the_current_formula(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The case this exists for: a v5 row, a POST, and a v7 row of matches v7 never made."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        await _add_a_v5_row(session, sid)
        await _set_matcher_version(session, sid, "pre-v7")
        before = await _score_rows(session, sid)
        assert [row["formula_version"] for row in before] == ["v5"]

        resp = await client.post(f"{BASE}/scans/{sid}/score")

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == MATCHED_UNDER_AN_EARLIER_RULE
        assert await _score_rows(session, sid) == before
        # The refusal is of the re-score only: what was stored is still listed.
        history = (await client.get(f"{BASE}/scans/{sid}/scores")).json()
        assert [row["formulaVersion"] for row in history] == ["v5"]

    async def test_another_agencys_scan_is_the_same_404_as_an_unknown_one(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The agency check comes first, so the refusal never says another agency's scan exists."""
        from httpx import ASGITransport
        from httpx import AsyncClient as Second

        from avp_api import ids

        await _sign_up(client, "one@matcher.example")
        sid = await _scan(client, stub_engines)
        await _set_matcher_version(session, sid, "pre-v7")
        before = await _score_rows(session, sid)
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with Second(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await _sign_up(other, "two@matcher.example")
            unknown = await other.post(f"{BASE}/scans/{ids.new_id(ids.SCAN)}/score")
            resp = await other.post(f"{BASE}/scans/{sid}/score")

        assert unknown.status_code == 404, unknown.text
        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == unknown.json()["type"] == "/problems/not-found"
        assert resp.json()["detail"] == unknown.json()["detail"]
        assert MATCHED_UNDER_AN_EARLIER_RULE not in resp.text
        assert await _score_rows(session, sid) == before

    async def test_a_failed_scan_matched_before_v7_still_gets_the_failed_refusal(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """Order of the checks: a FAILED scan has no score, whatever matched it (3.30)."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        await _set_matcher_version(session, sid, "pre-v7")
        await session.execute(
            sa_update(Scan)
            .where(Scan.id == sid)
            .values(status=ScanStatus.FAILED, error_code="SCAN_DEADLINE_EXCEEDED",
                    finished_at=datetime.now(UTC))
        )
        await session.commit()

        resp = await client.post(f"{BASE}/scans/{sid}/score")

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == FAILED_NOT_SCORABLE
        assert await _score_rows(session, sid) == []

    async def test_a_scan_the_current_matcher_made_is_re_scored_as_before(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scan(client, stub_engines)
        # Made by this code: created by the ORM and run by the runner, which says so.
        assert await _stored_matcher_version(session, sid) == extraction.MATCHER_VERSION

        first = await client.post(f"{BASE}/scans/{sid}/score")
        second = await client.post(f"{BASE}/scans/{sid}/score")

        assert first.status_code == second.status_code == 201, second.text
        assert first.json()["formulaVersion"] == second.json()["formulaVersion"] == FORMULA_VERSION
        assert first.json()["id"] == second.json()["id"], "one row per formula version, refreshed"
        assert [row["formula_version"] for row in await _score_rows(session, sid)] == [
            FORMULA_VERSION
        ]

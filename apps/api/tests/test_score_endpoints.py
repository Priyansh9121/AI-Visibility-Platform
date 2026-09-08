"""Scoring endpoints and persistence.

Engine execution is stubbed (Epic 4's stubs) so these exercise the scoring
persistence path without paid model calls. Scoring itself makes no provider
calls at all, so nothing about the score is mocked.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
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
        for field in ("composite", "mentionRate", "citationStrength"):
            assert isinstance(body[field], str), f"{field} must be a string"

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
        assert abs(sum(weights.values()) - Decimal("100")) < Decimal("0.01")

        rebuilt = sum(
            (weights[k] * Decimal(body[camel]) for k, camel in [
                ("mention_rate", "mentionRate"),
                ("citation_strength", "citationStrength"),
                ("sentiment", "sentiment"),
            ]),
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
        async with Second(transport=transport, base_url="http://testserver") as other:
            await _sign_up(other, "two@scoreiso.example")
            assert (await other.get(f"{BASE}/scans/{sid}/score")).status_code == 404
            assert (await other.post(f"{BASE}/scans/{sid}/score")).status_code == 404


class TestInsufficientData:
    async def test_a_scan_with_no_answered_results_scores_null(
        self, client: AsyncClient, monkeypatch
    ) -> None:  # noqa: ANN001
        """An unrunnable scan must never render as a bad score."""
        await _sign_up(client)
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]

        generated = [GeneratedPrompt(text="q", intent=PromptIntent.AWARENESS)]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub"

        async def all_fail(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(engine=e, engine_version="stub", prompt_text=prompt,
                             status=EngineResultStatus.TIMEOUT, error_code="TIMEOUT")
                for e in engines
            ]

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", all_fail)

        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()

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

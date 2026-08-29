"""The chained scan — Epic 9.17.

WHAT THIS FILE IS FOR, AND WHY IT IS NOT verify_e2e.py
------------------------------------------------------
`scripts/verify_e2e.py` has proved the whole pipeline works since Epic 9.1. It
does so by calling every phase itself: detection, then the scan, then the audit,
then scoring, then fixes. That proves the PHASES work. It has never proved the
PRODUCT runs them, because the product did not — every scan started from the UI
stopped after engine execution, and the report it produced said "Not scored",
`NO_COMPETITOR_SET` and `TECHNICAL_FOUNDATION_NOT_MEASURED` every single time.

So the load-bearing test here calls exactly ONE endpoint,
`POST /clients/{clientId}/scans`, and then reads the DATABASE — not the API —
to assert that a competitor set, a technical audit, a score and a fix list all
exist. Reading through GET endpoints would be fine for correctness but weaker
as evidence: the claim is "nothing else had to be called", and the cleanest way
to demonstrate that is to call nothing else.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from avp_api.models import (
    ActionItem,
    Competitor,
    CompetitorSet,
    EngineResult,
    Scan,
    ScanStatus,
    Score,
    TechnicalAudit,
)

BASE = "/api/v1"



@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Deterministic prompt generation and engine answers.

    The same shape `test_scan_endpoints.py` uses. The answer deliberately names
    Zendesk, then the subject, then Front, so that "did the competitor set exist
    when the loop ran?" is answerable from the persisted position.
    """
    from decimal import Decimal

    from avp_api.models.engine_result import EngineResultStatus, Sentiment
    from avp_api.models.prompt import PromptIntent
    from avp_api.services import scan_runner
    from avp_api.services.engines import CitedSource, EngineAnswer
    from avp_api.services.prompts import GeneratedPrompt

    def _install(n_prompts: int = 3):
        generated = [
            GeneratedPrompt(text=f"question number {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub-generator"

        text = "Zendesk is popular. Help Scout is simpler and well liked. Front is newer."

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine,
                    engine_version="stub",
                    prompt_text=prompt,
                    text=text,
                    citations=(
                        [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                        if engine.value.endswith("search")
                        else []
                    ),
                    latency_ms=10,
                    status=EngineResultStatus.OK,
                )
                for engine in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(
            scan_runner.extraction_service, "classify_sentiment", fake_sentiment
        )

    return _install


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Chain Test Agency",
            "fullName": "Op",
            "email": "chain@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": f"https://{domain}"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _rows(engine, model, scan_id: str, **opts):  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        stmt = select(model).where(model.scan_id == scan_id)
        if opts.get("load"):
            stmt = stmt.options(selectinload(opts["load"]))
        return list((await s.execute(stmt)).scalars().all())


class TestTheProductRunsTheWholePipeline:
    async def test_one_post_produces_a_complete_report(
        self, client: AsyncClient, engine, stub_engines
    ) -> None:  # noqa: ANN001
        """THE load-bearing test of Epic 9.17.

        One endpoint call. Four phases nobody asked for individually.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=3)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert resp.status_code == 202, resp.text
        sid = resp.json()["id"]

        # --- nothing else is called from here down ---------------------------
        competitor_sets = await _rows(
            engine, CompetitorSet, sid, load=CompetitorSet.competitors
        )
        assert len(competitor_sets) == 1, "competitor detection did not run"
        assert competitor_sets[0].competitors, "detection ran but produced no rivals"

        audits = await _rows(engine, TechnicalAudit, sid)
        assert len(audits) == 1, "the technical audit did not run"
        assert audits[0].technical_foundation is not None

        scores = await _rows(engine, Score, sid)
        assert len(scores) == 1, "scoring did not run"
        assert scores[0].composite is not None, "scored, but with no composite"

        fixes = await _rows(engine, ActionItem, sid)
        assert fixes, "fix generation did not run"

    async def test_the_score_includes_technical_foundation(
        self, client: AsyncClient, engine, stub_engines
    ) -> None:  # noqa: ANN001
        """The audit ran BEFORE scoring, proved by what the score contains.

        `TECHNICAL_FOUNDATION_NOT_MEASURED` was on every score this product
        produced before 9.17, and it was not a scoring bug — it was scoring
        correctly reporting that nothing had audited the site. Ordering the
        audit first is what removes it.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        score = (await _rows(engine, Score, sid))[0]
        assert score.technical_foundation is not None
        assert "technical_foundation" not in (score.excluded_dimensions or {})
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in (score.degradation_flags or [])

    async def test_competitors_are_detected_before_the_engine_loop_reads_them(
        self, client: AsyncClient, engine, stub_engines
    ) -> None:  # noqa: ANN001
        """The ordering finding, asserted through its only observable effect.

        `run_scan` loads the competitor set at its top and feeds it into prompt
        generation AND fact extraction. Detection afterwards would write a set
        nothing had used, and the tell is in the engine results: with no set,
        brand detection finds only the subject and every mention is position
        1 of 1.

        The stub answer names Zendesk, then Help Scout, then Front. If the set
        existed when the loop ran, the subject is second of three.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        results = await _rows(engine, EngineResult, sid)
        mentioned = [r for r in results if r.mentioned]
        assert mentioned, "the stub answer names the subject; something else broke"
        assert all(r.brands_mentioned == 3 for r in mentioned), (
            "brand detection saw only the subject — the competitor set was not "
            "in place when the engine loop ran"
        )
        assert all(r.position == 2 for r in mentioned)

    async def test_the_scan_is_not_terminal_until_every_phase_has_run(
        self, client: AsyncClient, engine, stub_engines
    ) -> None:  # noqa: ANN001
        """`succeeded` must mean the report is ready, not "the engines finished".

        Before 9.17 the scan reached SUCCEEDED the moment the engine loop ended,
        which after this change would leave a ~30s window where the dashboard
        stops polling and the operator opens the degraded report the whole epic
        exists to stop producing.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            scan = await s.get(Scan, sid)
            assert scan is not None
            assert scan.status is ScanStatus.SUCCEEDED
            assert scan.finished_at is not None

        # The proof that the status was stamped AFTER the chain: everything the
        # chain produces already exists at the moment the scan reads terminal.
        assert await _rows(engine, Score, sid)
        assert await _rows(engine, ActionItem, sid)


class TestOneFailedPhaseDoesNotTakeOutTheRest:
    """Every phase is attempted regardless of the ones before it.

    Not defensiveness. `run_audit` raises `ValueError` on a domain
    `crawl.normalise_url` cannot parse, `score_scan` raises
    `SubScoreOutOfRangeError` if an audit ever writes a Technical Foundation
    outside 0-100 (`technical_audits` has no CHECK preventing it), and
    `generate_for_scan` raises `RuntimeError` when `ANTHROPIC_API_KEY` is unset.
    Each is one phase's problem; aborting the chain on any of them would turn
    one missing section of the report into three.
    """

    async def test_a_failed_audit_still_leaves_a_score(
        self, client: AsyncClient, engine, stub_engines, monkeypatch
    ) -> None:  # noqa: ANN001
        from avp_api.services import audit_runner

        async def exploding_audit(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
            raise ValueError("could not parse that domain")

        monkeypatch.setattr(audit_runner, "audit_site", exploding_audit)

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        assert not await _rows(engine, TechnicalAudit, sid), "the audit should have failed"
        # ...and the two phases after it ran anyway.
        scores = await _rows(engine, Score, sid)
        assert scores, "a failed audit aborted scoring"
        assert scores[0].composite is not None
        # Scoring degraded honestly rather than inventing a foundation.
        assert scores[0].technical_foundation is None
        assert await _rows(engine, ActionItem, sid), "a failed audit aborted fix generation"

    async def test_a_failed_detection_still_leaves_a_scored_scan(
        self, client: AsyncClient, engine, stub_engines, monkeypatch
    ) -> None:  # noqa: ANN001
        from avp_api.services import competitors as detection

        async def exploding_detect(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
            raise RuntimeError("SERPAPI_KEY is not configured")

        monkeypatch.setattr(detection, "detect_for_client", exploding_detect)

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        assert not await _rows(engine, CompetitorSet, sid)
        # The scan still measures whether the subject is mentioned at all,
        # which is most of what a first scan is for.
        scores = await _rows(engine, Score, sid)
        assert scores, "a failed detection aborted the whole chain"
        assert scores[0].composite is not None
        assert await _rows(engine, TechnicalAudit, sid)

    async def test_a_failed_scoring_still_leaves_an_audit(
        self, client: AsyncClient, engine, stub_engines, monkeypatch
    ) -> None:  # noqa: ANN001
        from avp_api.services import scoring_runner

        async def exploding_score(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
            raise ValueError("sub-score out of range")

        monkeypatch.setattr(scoring_runner, "score_scan", exploding_score)

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        assert not await _rows(engine, Score, sid)
        assert await _rows(engine, TechnicalAudit, sid), "scoring rolled back the audit"

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            scan = await s.get(Scan, sid)
            # A failed chained phase is not a failed scan. The engines answered.
            assert scan.status is ScanStatus.SUCCEEDED


class TestCompetitorSetsCarryForward:
    """A client's rivals survive into its later scans — including the operator's.

    `CompetitorSet` hangs off a SCAN and `scan_runner.load_competitors` looks it
    up by `scan_id` alone, so a second scan starts with no rivals unless
    something puts them there. Re-detecting per scan would spend six SerpApi
    searches against a 250/month quota AND silently discard a hand-corrected
    list, because `persist_detection` preserves manual rows only within the set
    it is writing.
    """

    async def test_a_second_scan_reuses_the_set_without_re_detecting(
        self, client: AsyncClient, engine, stub_engines, monkeypatch
    ) -> None:  # noqa: ANN001
        from avp_api.services import competitors as detection

        calls = {"n": 0}
        original = detection.detect_for_client

        async def counting_detect(*args, **kwargs):  # noqa: ANN002, ANN003
            calls["n"] += 1
            return await original(*args, **kwargs)

        monkeypatch.setattr(detection, "detect_for_client", counting_detect)

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        first = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        second = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert first != second, "the second scan reused the first, so nothing was proved"

        assert calls["n"] == 1, "detection ran again and spent SerpApi quota"
        carried = await _rows(
            engine, CompetitorSet, second, load=CompetitorSet.competitors
        )
        assert carried, "the second scan has no competitor set"
        original_set = await _rows(
            engine, CompetitorSet, first, load=CompetitorSet.competitors
        )
        assert {c.name for c in carried[0].competitors} == {
            c.name for c in original_set[0].competitors
        }

    async def test_an_operators_correction_survives_into_the_next_scan(
        self, client: AsyncClient, engine, stub_engines
    ) -> None:  # noqa: ANN001
        """The failure re-detection would have caused, asserted directly."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        await client.post(f"{BASE}/clients/{cid}/scans", json={})

        override = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Intercom", "domain": "intercom.com"}]},
        )
        assert override.status_code == 200, override.text

        second = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        carried = (await _rows(engine, CompetitorSet, second))[0]

        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            rows = list(
                (
                    await s.execute(
                        select(Competitor).where(Competitor.competitor_set_id == carried.id)
                    )
                ).scalars()
            )
        active = [r for r in rows if not r.is_suppressed]
        assert [r.name for r in active] == ["Intercom"], (
            "the operator's corrected set did not reach the next scan"
        )
        assert all(r.is_manual_override for r in active)
        # The struck rivals came too — a strike that lasted one scan would not
        # be a strike.
        assert any(r.is_suppressed for r in rows)

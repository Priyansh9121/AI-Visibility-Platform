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

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from avp_api.services.scoring import FORMULA_VERSION

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
    # `classify: False`, as every other scan test in the suite. Without it
    # intake crawls the real domain in a real Chromium and calls the
    # classification model — a live network call and a paid call per test,
    # in a file that otherwise stubs every external boundary. Found by the
    # API key discipline audit (2026-09-07) and by this file's own clock:
    # nine tests at ~50s, against ~5s once nothing left the machine.
    resp = await client.post(
        f"{BASE}/clients", json={"url": f"https://{domain}", "classify": False}
    )
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


class TestASiteWhoseRobotsTxtKeepsUsOut:
    """The whole chain against a site whose robots.txt blocks `*` — 2026-09-29.

    The REAL `audit_site`, over conftest's autouse stub, with only its network
    faked (`robots_web`) and a browser that fails the test if it is launched.
    """

    async def test_technical_foundation_is_excluded_and_the_block_stays_a_finding(
        self, client: AsyncClient, engine, stub_engines, robots_web, monkeypatch  # noqa: ANN001
    ) -> None:
        from decimal import Decimal

        from avp_api.models.technical_audit import AuditStatus
        from avp_api.services import audit_runner, scoring_runner, technical_audit

        def no_browser():  # noqa: ANN202
            raise AssertionError("the browser was launched for a disallowed homepage")

        monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)
        monkeypatch.setattr(technical_audit, "async_playwright", no_browser)
        robots_web.serve("https://helpscout.com/robots.txt", body="User-agent: *\nDisallow: /\n")

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=3)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        # The audit: robots.txt only, stored as a failed audit with the reason.
        assert robots_web.fetched() == ["https://helpscout.com/robots.txt"]
        (audit,) = await _rows(engine, TechnicalAudit, sid, load=TechnicalAudit.checks)
        assert audit.status is AuditStatus.FAILED
        assert (audit.error_code, audit.pages_crawled) == ("ROBOTS_DISALLOWED", 0)
        assert audit.technical_foundation is None
        checks = {c.check_key: (c.status.value, c.detail_code) for c in audit.checks}
        assert checks["indexable"] == ("fail", "ROBOTS_TXT_DISALLOW")
        assert checks["meta_title"] == ("not_applicable", "ROBOTS_DISALLOWED")

        # The score: Technical Foundation excluded under its own reason.
        (score,) = await _rows(engine, Score, sid)
        assert score.excluded_dimensions["technical_foundation"] == "CRAWL_DISALLOWED"
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in score.degradation_flags
        assert "technical_foundation" not in score.weights
        assert score.composite is not None

        # ...and its composite is the one with the dimension excluded, not a
        # zero carried at 10%. Re-scored from the same rows, unpersisted.
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        async def rescored(value: Decimal | None) -> Decimal | None:
            async def load(session, scan_id):  # noqa: ANN001, ANN202, ARG001
                return value, None

            monkeypatch.setattr(scoring_runner, "load_technical_foundation", load)
            async with factory() as s:
                scan = (await s.execute(select(Scan).where(Scan.id == sid))).scalar_one()
                _row, computed, _ = await scoring_runner.score_scan(s, scan, persist=False)
            return computed.composite

        assert await rescored(None) == score.composite
        assert await rescored(Decimal("0")) != score.composite

        # The report says why, and still carries the finding.
        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        tf = next(d for d in report["dimensions"] if d["key"] == "technical_foundation")
        assert (tf["included"], tf["exclusionReason"]) == (False, "CRAWL_DISALLOWED")
        assert report["audit"]["errorCode"] == "ROBOTS_DISALLOWED"
        assert {"checkKey": "indexable", "detailCode": "ROBOTS_TXT_DISALLOW"}.items() <= next(
            f for f in report["audit"]["findings"] if f["checkKey"] == "indexable"
        ).items()


class TestASiteWhoseRobotsTxtCannotBeRead:
    """The whole chain against a site whose robots.txt did not answer — launch checklist 3.41.

    The crawler fetches nothing, exactly as for a robots.txt that forbids it, but the
    site forbade nothing, so the audit, the score and the report must not say it did.
    """

    async def test_nothing_is_said_about_a_file_nobody_read(
        self, client: AsyncClient, engine, stub_engines, robots_web, monkeypatch  # noqa: ANN001
    ) -> None:
        from avp_api.models.technical_audit import AuditStatus
        from avp_api.services import audit_runner, technical_audit

        def no_browser():  # noqa: ANN202
            raise AssertionError("the browser was launched with no robots.txt to read")

        monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)
        monkeypatch.setattr(technical_audit, "async_playwright", no_browser)
        robots_web.serve("https://helpscout.com/robots.txt", status=503)

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=3)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        # The audit: robots.txt only, stored as a failed audit with its own code.
        assert robots_web.fetched() == ["https://helpscout.com/robots.txt"]
        (audit,) = await _rows(engine, TechnicalAudit, sid, load=TechnicalAudit.checks)
        assert audit.status is AuditStatus.FAILED
        assert (audit.error_code, audit.pages_crawled) == ("ROBOTS_UNREACHABLE", 0)
        assert audit.technical_foundation is None
        checks = {c.check_key: (c.status.value, c.detail_code) for c in audit.checks}
        assert checks["indexable"] == ("not_applicable", "ROBOTS_UNREACHABLE")
        assert checks["robots_txt_present"] == ("not_applicable", "ROBOTS_UNREACHABLE")
        assert checks["meta_title"] == ("not_applicable", "ROBOTS_UNREACHABLE")
        assert not [k for k, (status, _) in checks.items() if status in ("warn", "fail", "error")]

        # The score: Technical Foundation excluded, under the reason that says why.
        (score,) = await _rows(engine, Score, sid)
        assert score.excluded_dimensions["technical_foundation"] == "CRAWL_POLICY_UNREADABLE"
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in score.degradation_flags
        assert "technical_foundation" not in score.weights
        assert score.composite is not None
        assert score.formula_version == FORMULA_VERSION

        # No fix tells the client to publish a robots.txt that may well exist.
        fixes = await _rows(engine, ActionItem, sid)
        assert fixes, "fix generation did not run"
        assert "robots_txt_present" not in [f.source_key for f in fixes]
        assert "indexable" not in [f.source_key for f in fixes]

        # The report says what happened, and carries no finding.
        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        tf = next(d for d in report["dimensions"] if d["key"] == "technical_foundation")
        assert (tf["included"], tf["exclusionReason"]) == (False, "CRAWL_POLICY_UNREADABLE")
        assert report["audit"]["errorCode"] == "ROBOTS_UNREACHABLE"
        assert report["audit"]["findings"] == []


# ---------------------------------------------------------------------------
# M1 — the API's memory, and the audit step that could hang (checklist 3.20)
# ---------------------------------------------------------------------------

MEMORY_PHASES = ["competitors", "engines", "audit", "scoring", "alerts", "fixes", "finalized"]
MEMORY_FIELDS = [
    "process_tree_rss_mb", "cgroup_current_mb", "cgroup_anon_mb", "cgroup_peak_mb",
    "cgroup_limit_mb",
]


class TestMemoryIsLoggedAfterEachPhase:
    """`scan.phase_memory`: one line per phase, only numbers, and never able to cost a scan."""

    async def test_every_phase_logs_its_memory_in_the_order_it_ran(
        self, client: AsyncClient, stub_engines, monkeypatch  # noqa: ANN001
    ) -> None:
        from structlog.testing import capture_logs

        from avp_api.services import memory_stats

        numbers = {name: 10 * (position + 1) for position, name in enumerate(MEMORY_FIELDS)}
        monkeypatch.setattr(memory_stats, "snapshot", lambda: dict(numbers))
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        with capture_logs() as logs:
            sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        lines = [entry for entry in logs if entry["event"] == "scan.phase_memory"]
        assert [entry["phase"] for entry in lines] == MEMORY_PHASES
        for entry in lines:
            # The scan, the phase and the figures. Nothing else rides along.
            assert entry == {
                "event": "scan.phase_memory", "log_level": "info", "scan_id": sid,
                "phase": entry["phase"], **numbers,
            }

    async def test_what_is_logged_on_this_machine_is_ints_or_none(
        self, client: AsyncClient, stub_engines  # noqa: ANN001
    ) -> None:
        from structlog.testing import capture_logs

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        with capture_logs() as logs:
            sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        lines = [entry for entry in logs if entry["event"] == "scan.phase_memory"]
        assert len(lines) == len(MEMORY_PHASES)
        for entry in lines:
            assert set(entry) == {"event", "log_level", "scan_id", "phase", *MEMORY_FIELDS}
            assert entry["scan_id"] == sid
            assert all(
                entry[name] is None or type(entry[name]) is int for name in MEMORY_FIELDS
            ), entry

    async def test_the_phases_are_a_fixed_set(self) -> None:
        from avp_api.services import scan_executor

        assert list(scan_executor.MEMORY_PHASES) == MEMORY_PHASES

    async def test_a_snapshot_that_raises_costs_the_scan_nothing(
        self, client: AsyncClient, engine, stub_engines, monkeypatch  # noqa: ANN001
    ) -> None:
        from structlog.testing import capture_logs

        from avp_api.services import memory_stats

        def boom() -> dict[str, int | None]:
            raise RuntimeError("memory exploded")

        monkeypatch.setattr(memory_stats, "snapshot", boom)
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        with capture_logs() as logs:
            sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        # Nothing about the failure is logged, and nothing downstream of it failed.
        logged = {entry["event"] for entry in logs}
        assert not logged & {
            "scan.phase_memory", "scan.chain.phase_failed", "scan.execute.failed",
        }
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            scan = await s.get(Scan, sid)
            assert scan is not None and scan.status is ScanStatus.SUCCEEDED
        assert await _rows(engine, Score, sid) and await _rows(engine, ActionItem, sid)

    def test_the_helper_swallows_whatever_the_snapshot_does(
        self, monkeypatch  # noqa: ANN001
    ) -> None:
        from structlog.testing import capture_logs

        from avp_api.services import memory_stats, scan_executor

        def boom() -> dict[str, int | None]:
            raise RuntimeError("memory exploded")

        monkeypatch.setattr(memory_stats, "snapshot", boom)

        with capture_logs() as logs:
            assert scan_executor._log_phase_memory("scan_whatever", "audit") is None  # noqa: SLF001

        assert logs == []  # not even a line about the failure


def _a_browser_whose_page_read_never_returns() -> SimpleNamespace:
    """`async_playwright()`: everything answers except the audit's one `page.evaluate`."""

    async def never(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()

    landed = SimpleNamespace(url="https://helpscout.com/", redirected_from=None)
    page = SimpleNamespace(
        url="https://helpscout.com/", set_default_timeout=lambda ms: None,
        add_init_script=AsyncMock(), wait_for_timeout=AsyncMock(), close=AsyncMock(),
        goto=AsyncMock(return_value=SimpleNamespace(status=200, headers={}, request=landed)),
        evaluate=never,
    )
    context = SimpleNamespace(
        route=AsyncMock(), route_web_socket=AsyncMock(), on=lambda *args: None,
        add_init_script=AsyncMock(), new_page=AsyncMock(return_value=page), close=AsyncMock(),
    )
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    driver = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

    class Manager:
        async def __aenter__(self) -> SimpleNamespace:
            return driver

        async def __aexit__(self, *exc: object) -> bool:
            return False

    manager = Manager()
    manager.browser, manager.context, manager.page = browser, context, page  # type: ignore[attr-defined]
    return manager  # type: ignore[return-value]


class TestAHungBrowserStepIsAFailedAuditPhase:
    """Checklist 3.20.

    One audit step, the page read, has no timeout of its own. When it hung, the
    scan ran into its 2,400 s deadline and alerts and fix generation never ran.
    The browser run is now capped, and a hang comes out as the timeout the audit
    already had: a FAILED audit with `TIMEOUT`, and a chain that carries on.
    """

    async def test_the_audit_fails_with_a_timeout_and_every_later_phase_runs(
        self, client: AsyncClient, engine, stub_engines, robots_web, monkeypatch  # noqa: ANN001
    ) -> None:
        from structlog.testing import capture_logs

        from avp_api.models.technical_audit import AuditStatus
        from avp_api.services import audit_runner, technical_audit

        manager = _a_browser_whose_page_read_never_returns()
        monkeypatch.setattr(audit_runner, "audit_site", technical_audit.audit_site)
        monkeypatch.setattr(technical_audit, "async_playwright", lambda: manager)
        monkeypatch.setattr(technical_audit, "audit_run_cap_s", lambda timeout_ms: 0.3)
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        started = time.monotonic()
        with capture_logs() as logs:
            # Bounded here as well: without the cap this call would wait out the scan's
            # 2,400 s deadline, and a regression should fail this test, not hold the suite.
            posted = await asyncio.wait_for(
                client.post(f"{BASE}/clients/{cid}/scans", json={}), 60
            )
            sid = posted.json()["id"]
        assert time.monotonic() - started < 60  # the cap, not the scan's deadline

        # The audit: an ordinary failed one, with the code it already had for a timeout.
        (audit,) = await _rows(engine, TechnicalAudit, sid, load=TechnicalAudit.checks)
        assert audit.status is AuditStatus.FAILED
        assert (audit.error_code, audit.pages_crawled) == ("TIMEOUT", 0)
        assert [(c.check_key, c.status.value, c.detail_code) for c in audit.checks] == [
            ("site_reachable", "error", "TIMEOUT")
        ]
        # Everything the hang used to cost: the browser was closed ...
        manager.browser.close.assert_awaited_once()  # type: ignore[attr-defined]
        manager.context.close.assert_awaited_once()  # type: ignore[attr-defined]
        manager.page.close.assert_awaited_once()  # type: ignore[attr-defined]
        # ... the phases after the audit all ran ...
        phases = [e["phase"] for e in logs if e["event"] == "scan.phase_memory"]
        assert phases == MEMORY_PHASES
        scores = await _rows(engine, Score, sid)
        assert scores and scores[0].composite is not None
        assert scores[0].technical_foundation is None  # excluded, not scored as zero
        assert await _rows(engine, ActionItem, sid), "fix generation did not run"
        # ... and the scan ended on its own account, not on the deadline's.
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            scan = await s.get(Scan, sid)
            assert scan is not None
            assert scan.status is ScanStatus.SUCCEEDED
            assert scan.error_code is None

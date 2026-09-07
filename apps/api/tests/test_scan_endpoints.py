"""Scan execution endpoints.

Engines are stubbed so these exercise persistence, the prompt x engine matrix,
scoping and the API contract without paid model calls. The live scan is a
separate verification — scripts/verify_scan.py.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from avp_api.deps import scan_executor
from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.schemas.scan import RunScanRequest
from avp_api.services import scan_runner
from avp_api.services.engines import DEFAULT_ENGINES, CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

# Derived, never hardcoded. These tests asserted "x 2" until Epic 9.13 added a
# third engine and broke six of them at once. The count is a property of the
# registry, so read it from the registry — a fourth engine should not cost
# another afternoon of arithmetic.
N_ENGINES = len(DEFAULT_ENGINES)
ENGINE_NAMES = {e.value for e in DEFAULT_ENGINES}

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "scan@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Scan Test Agency", "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _run_scan(
    client: AsyncClient, cid: str, payload: dict | None = None
) -> dict:
    """Queue a scan and return its COMPLETED detail — Epic 9.5.

    The endpoint is asynchronous: it answers `202` with a QUEUED scan and never
    reports completion itself. The suite installs an inline executor (conftest),
    so the work has already finished by the time this returns — but the
    completed shape is still read where a real caller reads it,
    `GET /scans/{scanId}`, rather than from a queue receipt that does not carry
    it. The `202` is asserted here so every caller of this helper covers the new
    contract without restating it.
    """
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json=payload or {})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "queued"
    detail = await client.get(f"{BASE}/scans/{resp.json()['id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Deterministic prompt generation and engine answers."""

    def _install(n_prompts: int = 4, answer_text: str | None = None, fail: bool = False):
        generated = [
            GeneratedPrompt(text=f"question number {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub-generator"

        text = answer_text if answer_text is not None else (
            "Zendesk is popular. Help Scout is simpler and well liked. Front is newer."
        )

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            out = []
            for engine in engines:
                if fail:
                    out.append(EngineAnswer(
                        engine=engine, engine_version="stub", prompt_text=prompt,
                        status=EngineResultStatus.TIMEOUT, error_code="TIMEOUT"))
                    continue
                citations = (
                    [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                    if engine is Engine.CLAUDE_SEARCH else []
                )
                out.append(EngineAnswer(
                    engine=engine, engine_version="stub", prompt_text=prompt,
                    text=text, citations=citations, latency_ms=10))
            return out

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            from decimal import Decimal
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(
            scan_runner.extraction_service, "classify_sentiment", fake_sentiment
        )

    return _install


class TestRunScanRequest:
    """The schema half of the engines bound — pure, no request needed."""

    def test_duplicates_collapse_to_distinct_engines_in_order(self) -> None:
        req = RunScanRequest.model_validate(
            {"engines": ["claude", "chatgpt", "claude", "claude_search", "chatgpt"]}
        )
        assert req.engines == [Engine.CLAUDE, Engine.CHATGPT, Engine.CLAUDE_SEARCH]

    def test_duplicates_cannot_fill_the_ceiling(self) -> None:
        # Eight copies of one engine: a bare max_length would reject this and
        # an unbounded list would bill it eight times. It is one engine.
        req = RunScanRequest.model_validate({"engines": ["claude"] * 8})
        assert req.engines == [Engine.CLAUDE]
        # The ceiling itself is the enum: naming every engine once is the most
        # a request can ask for, and it is accepted.
        everything = RunScanRequest.model_validate({"engines": [e.value for e in Engine]})
        assert len(everything.engines) == len(Engine)

    def test_an_unknown_engine_is_rejected_by_the_enum(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RunScanRequest.model_validate({"engines": ["bing"]})

    def test_an_absent_or_empty_list_is_left_for_the_router_default(self) -> None:
        assert RunScanRequest.model_validate({}).engines is None
        assert RunScanRequest.model_validate({"engines": []}).engines == []


class TestRunScan:
    async def test_produces_a_result_for_every_prompt_times_engine(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The literal §7 acceptance shape."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=4)

        body = await _run_scan(client, cid)

        assert body["promptSet"] is not None
        assert len(body["promptSet"]["prompts"]) == 4
        # 4 prompts x N engines = one row per pair, no gaps and no dupes.
        expected = 4 * N_ENGINES
        assert len(body["results"]) == expected
        assert body["engineResultCount"] == expected
        pairs = {(r["promptId"], r["engine"]) for r in body["results"]}
        assert len(pairs) == expected
        assert {e for _, e in pairs} == ENGINE_NAMES
        assert body["status"] == "succeeded"

    async def test_mentions_and_citations_are_parsed(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        body = await _run_scan(client, cid)
        result = body["results"][0]
        assert result["mentioned"] is True
        # UPDATED IN EPIC 9.17, AND THE CHANGE IS THE POINT.
        #
        # This used to assert `position == 1` and `brandsMentioned == 1`, with a
        # comment explaining that no competitor set existed for the scan so the
        # subject was alone in its own ranking. That was true, and it was the
        # degradation this epic exists to remove: the endpoint now detects
        # rivals before the engine loop, so brand detection has something to
        # rank against.
        #
        # The stub answer names Zendesk, then Help Scout, then Front, and the
        # chain's detection stub returns Zendesk and Front — so the subject is
        # genuinely second of three rather than first of one.
        assert result["position"] == 2
        assert result["brandsMentioned"] == 3
        assert result["sentiment"] == "positive"
        assert len(result["responseDigest"]) == 64

        grounded = [r for r in body["results"] if r["engine"] == "claude_search"]
        assert all(len(r["citations"]) == 1 for r in grounded)
        assert grounded[0]["citations"][0]["sourceType"] == "review"

    async def test_position_orders_the_subject_against_known_competitors(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """With a competitor set present, position reflects who is named first."""
        from avp_api import ids
        from avp_api.models import Client, Competitor, CompetitorSet, Scan
        from avp_api.models.scan import ScanStatus

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=1)

        # Create the scan and its competitor set up front, QUEUED, so the
        # endpoint reuses it. Running a scan first would not work: a SUCCEEDED
        # scan is not reused, so the second POST would create a fresh Scan with
        # no competitor set attached.
        row = await session.get(Client, cid)
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=cid, agency_id=row.agency_id,
            status=ScanStatus.QUEUED,
        )
        session.add(scan)
        await session.flush()
        cset = CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, competitors=[])
        session.add(cset)
        await session.flush()
        cset.competitors.append(
            Competitor(
                id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id,
                name="Zendesk", domain="zendesk.com", rank=1,
                detection_source="both", signal_count=2,
            )
        )
        await session.commit()

        body = await _run_scan(client, cid)
        result = body["results"][0]
        assert result["brandsMentioned"] == 2
        assert result["position"] == 2, "Zendesk is named before Help Scout"
        names = [m["entityName"] for m in result["brandMentions"]]
        assert names == ["Zendesk", "helpscout.com"]

    async def test_answer_text_never_appears_in_any_response(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """ip-safety.md #7, asserted where the engine results actually are.

        This used to search the POST response, which carried every result. The
        queue receipt carries none (Epic 9.5), so that assertion alone would now
        pass no matter what the pipeline did with the prose. The responses that
        DO carry results are the ones worth searching, and the test asserts they
        are non-empty first so it cannot go quietly vacuous again.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2, answer_text="UNIQUE ENGINE PROSE MARKER 12345")

        queued = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert "UNIQUE ENGINE PROSE MARKER" not in queued.text
        sid = queued.json()["id"]

        detail = await client.get(f"{BASE}/scans/{sid}")
        assert detail.json()["results"], "nothing to search — the scan produced no results"
        assert "UNIQUE ENGINE PROSE MARKER" not in detail.text

        results = await client.get(f"{BASE}/scans/{sid}/results")
        assert results.json()["data"], "nothing to search — the results page is empty"
        assert "UNIQUE ENGINE PROSE MARKER" not in results.text

    async def test_prompt_limit_caps_the_set(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=10)
        body = await _run_scan(client, cid, {"promptLimit": 3})
        assert len(body["promptSet"]["prompts"]) == 3
        assert len(body["results"]) == 3 * N_ENGINES

    async def test_all_engines_failing_marks_the_scan_failed(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A depressed mention rate caused by an outage must not read as fact."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2, fail=True)
        body = await _run_scan(client, cid)
        assert body["status"] == "failed"
        assert body["errorCode"] == "ALL_ENGINE_CALLS_FAILED"
        assert all(r["status"] == "timeout" for r in body["results"])

    async def test_a_truncated_answer_is_a_missing_cell_not_an_absence(
        self, client: AsyncClient, stub_engines, monkeypatch
    ) -> None:  # noqa: ANN001
        """The audit's data-correctness bug, asserted where a caller would see it.

        One engine's answers come back cut off by the token budget. Before the
        `truncated` status existed that cell was recorded `answered_no_mention`
        and the scan read `succeeded`: a billed non-answer, counted against the
        mention rate as if the engine had chosen not to name the subject. Now
        the cell is a failure the scan-level status admits to.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        stubbed = scan_runner.engine_service.ask_all

        async def one_engine_truncates(prompt, *, engines, settings):  # noqa: ANN001
            answers = await stubbed(prompt, engines=engines, settings=settings)
            for a in answers:
                if a.engine is Engine.CHATGPT:
                    a.text = ""
                    a.status = EngineResultStatus.TRUNCATED
                    a.error_code = "ANSWER_TRUNCATED"
            return answers

        monkeypatch.setattr(scan_runner.engine_service, "ask_all", one_engine_truncates)

        body = await _run_scan(client, cid)

        assert body["status"] == "partial"
        cells = [r for r in body["results"] if r["engine"] == "chatgpt"]
        assert len(cells) == 2
        for cell in cells:
            assert cell["status"] == "truncated"
            assert cell["errorCode"] == "ANSWER_TRUNCATED"
            assert cell["mentioned"] is False
            assert cell["responseDigest"] is None
        # The other engines are untouched, and none of them was demoted.
        others = [r for r in body["results"] if r["engine"] != "chatgpt"]
        assert others and all(r["status"] == "ok" for r in others)

    async def test_unmentioned_subject_is_recorded_not_dropped(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A no-mention is the product's core finding — it must persist a row."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2, answer_text="Zendesk and Front are the leaders here.")
        body = await _run_scan(client, cid)
        assert len(body["results"]) == 2 * N_ENGINES
        assert all(r["mentioned"] is False for r in body["results"])
        assert all(r["status"] == "answered_no_mention" for r in body["results"])
        assert all(r["sentiment"] is None for r in body["results"])

    async def test_engine_versions_are_recorded_on_the_scan(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Provenance: a score shift must be attributable to an engine change."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=1)
        body = await _run_scan(client, cid)
        assert set(body["engineVersions"]) == ENGINE_NAMES

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        from avp_api import ids

        resp = await client.post(f"{BASE}/clients/{ids.new_id(ids.CLIENT)}/scans", json={})
        assert resp.status_code == 401


class TestScanReads:
    async def test_get_scan_and_paginated_results(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=3)
        created = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()
        sid = created["id"]

        assert (await client.get(f"{BASE}/scans/{sid}")).status_code == 200

        # Walk every page rather than asserting a fixed two. The row count is
        # 3 prompts x N engines, so a hardcoded page shape breaks whenever an
        # engine is added — which is exactly what happened in Epic 9.13.
        total = 3 * N_ENGINES
        seen: list[dict] = []
        cursor: str | None = None
        pages = 0
        while True:
            url = f"{BASE}/scans/{sid}/results?limit=4"
            if cursor:
                url += f"&cursor={cursor}"
            page = (await client.get(url)).json()
            pages += 1
            seen.extend(page["data"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
            assert pages < 10, "pagination did not terminate"

        assert len(seen) == total
        assert len({r["id"] for r in seen}) == total, "a row was repeated across pages"
        assert pages == (total + 3) // 4

    async def test_prompt_set_endpoint_returns_ordered_prompts(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=5)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        body = (await client.get(f"{BASE}/scans/{sid}/prompts")).json()
        positions = [p["position"] for p in body["prompts"]]
        assert positions == sorted(positions) == [1, 2, 3, 4, 5]

    async def test_list_scans_for_a_client(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=1)
        await client.post(f"{BASE}/clients/{cid}/scans", json={})
        body = (await client.get(f"{BASE}/clients/{cid}/scans")).json()
        assert len(body["data"]) == 1

    async def test_unknown_scan_is_404(self, client: AsyncClient) -> None:
        from avp_api import ids

        await _sign_up(client)
        assert (await client.get(f"{BASE}/scans/{ids.new_id(ids.SCAN)}")).status_code == 404

    async def test_another_agency_cannot_read_a_scan(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        from httpx import ASGITransport
        from httpx import AsyncClient as Second

        await _sign_up(client, "one@scaniso.example")
        cid = await _make_client(client)
        stub_engines(n_prompts=1)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with Second(transport=transport, base_url="http://testserver") as other:
            await _sign_up(other, "two@scaniso.example")
            assert (await other.get(f"{BASE}/scans/{sid}")).status_code == 404
            assert (await other.get(f"{BASE}/scans/{sid}/results")).status_code == 404


class TestScanIsQueued:
    """The 202 contract — Epic 9.5.

    These are the behaviours that did not exist before this slice. The rest of
    the file runs against an inline executor and would pass whether the endpoint
    were asynchronous or not; nothing here would.
    """

    @staticmethod
    def _defer(client: AsyncClient) -> list:
        """Swap the inline executor for one that records the job and never runs it.

        Without this the suite's inline executor finishes the scan during the
        POST, and "visible while still queued" becomes untestable — the very
        thing this slice exists to make true.
        """
        jobs: list = []

        class _Deferred:
            async def submit(self, job, *, settings) -> None:  # noqa: ANN001
                jobs.append(job)

        app = client._transport.app  # noqa: SLF001
        app.dependency_overrides[scan_executor] = lambda: _Deferred()
        return jobs

    async def test_post_returns_202_with_a_queued_scan(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        self._defer(client)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert body["id"].startswith("scan_")
        # ScanOut, not ScanDetailOut with empty fields: at 202 the prompt set
        # has not been generated, so the response must not offer a shape that
        # cannot distinguish "not yet" from "none".
        assert "results" not in body
        assert "promptSet" not in body
        assert body["promptCount"] == 0
        assert body["engineResultCount"] == 0

    async def test_the_scan_is_visible_as_queued_before_the_work_runs(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The whole point of the slice.

        Epic 9.3 found a running scan sat in an uncommitted transaction for its
        entire ~303s, so no other request could see it — "there is nothing to
        poll". This asserts there now is: the row is readable, by a different
        request, while the work has demonstrably not happened.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        jobs = self._defer(client)

        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        # The executor was handed the job and has not run it.
        assert len(jobs) == 1
        detail = (await client.get(f"{BASE}/scans/{sid}")).json()
        assert detail["status"] == "queued"
        assert detail["results"] == []
        assert detail["finishedAt"] is None
        # Not started, either — started_at is stamped by the executor, and the
        # stale-scan reaper depends on that distinction holding.
        assert detail["startedAt"] is None

    async def test_the_queued_scan_reaches_the_dashboard(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The consumer Epic 9.3 built, reading a scan that has not finished."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        self._defer(client)

        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        board = (await client.get(f"{BASE}/dashboard")).json()
        row = next(s for s in board["recentScans"] if s["id"] == sid)
        assert row["status"] == "queued"
        # The LEFT join's reason for existing — an unscored scan still appears.
        assert row["compositeScore"] is None
        assert board["isEmpty"] is False

    async def test_the_executor_is_handed_what_it_needs_to_run(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=5)
        jobs = self._defer(client)

        sid = (await client.post(
            f"{BASE}/clients/{cid}/scans",
            json={"promptLimit": 3, "engines": ["claude"]},
        )).json()["id"]

        job = jobs[0]
        assert job.scan_id == sid
        assert job.client_id == cid
        assert job.prompt_limit == 3
        assert job.engines == (Engine.CLAUDE,)

    async def test_duplicate_engines_are_collapsed_before_they_are_billed(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The audit's spend-ceiling finding at the request boundary.

        Unbounded, `["claude", "claude", "chatgpt", "claude"]` was four billed
        calls per prompt, three of them identical, which then collided on
        `uq_engine_results_prompt_engine` — after the money was spent.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        jobs = self._defer(client)

        resp = await client.post(
            f"{BASE}/clients/{cid}/scans",
            json={"engines": ["claude", "claude", "chatgpt", "claude"]},
        )

        assert resp.status_code == 202, resp.text
        assert jobs[0].engines == (Engine.CLAUDE, Engine.CHATGPT)

    async def test_an_engine_without_an_adapter_is_refused_before_anything_exists(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """`perplexity` is in the Engine enum — the product may measure it one
        day — and has no adapter. Before this check it reached the executor,
        which paid for competitor detection and then hit a KeyError inside
        `ask_all`, landing the scan at FAILED / EXECUTION_FAILED. Now: a 422,
        no job, and no scan row to strand.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        jobs = self._defer(client)

        resp = await client.post(
            f"{BASE}/clients/{cid}/scans", json={"engines": ["claude", "perplexity"]}
        )

        assert resp.status_code == 422, resp.text
        assert "perplexity" in resp.text
        assert jobs == []
        assert (await client.get(f"{BASE}/clients/{cid}/scans")).json()["data"] == []

    async def test_an_engine_the_enum_has_never_heard_of_is_a_422(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        jobs = self._defer(client)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={"engines": ["bing"]})

        assert resp.status_code == 422, resp.text
        assert jobs == []

    async def test_a_scan_already_running_gets_no_second_executor(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """`executor.submit` used to be unconditional. Re-running while a scan
        was in flight handed the same row to a second executor, which re-paid
        for the chain up to a unique violation on `prompt_sets`. The 202 now
        reports the scan in flight, and nothing is started."""
        from datetime import UTC, datetime

        from avp_api import ids
        from avp_api.models import Client, Scan, ScanStatus

        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        row = await session.get(Client, cid)
        running = Scan(
            id=ids.new_id(ids.SCAN), client_id=cid, agency_id=row.agency_id,
            status=ScanStatus.RUNNING, started_at=datetime.now(UTC),
        )
        session.add(running)
        await session.commit()
        jobs = self._defer(client)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})

        assert resp.status_code == 202, resp.text
        assert resp.json()["id"] == running.id
        assert resp.json()["status"] == "running"
        assert jobs == []

    async def test_queueing_twice_reuses_the_open_scan(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Sequential reuse works now that QUEUED is committed.

        `get_or_create_scan` always intended to reuse an open scan, but could
        never see one: the row was uncommitted for the whole run. It can now.

        This is the SEQUENTIAL case only. Two requests racing inside the window
        between SELECT and COMMIT still create two scans — that is Epic 9.6's
        partial unique index, and this test does not claim otherwise.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)
        self._defer(client)

        first = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        second = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        assert first == second
        assert len((await client.get(f"{BASE}/clients/{cid}/scans")).json()["data"]) == 1

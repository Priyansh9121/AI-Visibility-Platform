"""Scan execution endpoints.

Engines are stubbed so these exercise persistence, the prompt x engine matrix,
scoping and the API contract without paid model calls. The live scan is a
separate verification — scripts/verify_scan.py.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

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


class TestRunScan:
    async def test_produces_a_result_for_every_prompt_times_engine(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The literal §7 acceptance shape."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=4)

        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["promptSet"] is not None
        assert len(body["promptSet"]["prompts"]) == 4
        # 4 prompts x 2 engines = 8 rows, one per pair, no gaps and no dupes.
        assert len(body["results"]) == 8
        assert body["engineResultCount"] == 8
        pairs = {(r["promptId"], r["engine"]) for r in body["results"]}
        assert len(pairs) == 8
        assert {e for _, e in pairs} == {"claude", "claude_search"}
        assert body["status"] == "succeeded"

    async def test_mentions_and_citations_are_parsed(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2)

        body = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()
        result = body["results"][0]
        assert result["mentioned"] is True
        # No competitor set exists for this scan, so only the subject is
        # detectable — brand detection is scoped to the subject plus KNOWN
        # competitors, never open-ended entity extraction. Position is therefore
        # 1 of 1 here. Ordering against rivals is covered below.
        assert result["position"] == 1
        assert result["brandsMentioned"] == 1
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

        body = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()
        result = body["results"][0]
        assert result["brandsMentioned"] == 2
        assert result["position"] == 2, "Zendesk is named before Help Scout"
        names = [m["entityName"] for m in result["brandMentions"]]
        assert names == ["Zendesk", "helpscout.com"]

    async def test_answer_text_never_appears_in_the_response(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2, answer_text="UNIQUE ENGINE PROSE MARKER 12345")
        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert "UNIQUE ENGINE PROSE MARKER" not in resp.text

    async def test_prompt_limit_caps_the_set(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=10)
        body = (await client.post(f"{BASE}/clients/{cid}/scans", json={"promptLimit": 3})).json()
        assert len(body["promptSet"]["prompts"]) == 3
        assert len(body["results"]) == 6

    async def test_all_engines_failing_marks_the_scan_failed(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A depressed mention rate caused by an outage must not read as fact."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2, fail=True)
        body = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()
        assert body["status"] == "failed"
        assert body["errorCode"] == "ALL_ENGINE_CALLS_FAILED"
        assert all(r["status"] == "timeout" for r in body["results"])

    async def test_unmentioned_subject_is_recorded_not_dropped(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A no-mention is the product's core finding — it must persist a row."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_engines(n_prompts=2, answer_text="Zendesk and Front are the leaders here.")
        body = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()
        assert len(body["results"]) == 4
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
        body = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()
        assert set(body["engineVersions"]) == {"claude", "claude_search"}

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

        page1 = (await client.get(f"{BASE}/scans/{sid}/results?limit=4")).json()
        assert len(page1["data"]) == 4
        assert page1["nextCursor"] is not None
        page2 = (await client.get(
            f"{BASE}/scans/{sid}/results?limit=4&cursor={page1['nextCursor']}"
        )).json()
        assert len(page2["data"]) == 2
        assert page2["nextCursor"] is None

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

"""GET /clients/{id}/history — Epic 9.20.

The endpoint behind a client's Sources and Rankings trends. It reads; it
collects nothing and writes nothing, and these tests are mostly about that
claim and about the two ways a trend built from a re-detected competitor set
can quietly lie.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from avp_api.models.engine_result import Citation, Engine, EngineResult, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.models.scan import Scan
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

BASE = "/api/v1"


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """Stop after the engine phase, so a scan is cheap and deterministic here.

    Same reason `test_report_endpoint` uses it: this suite is about the SHAPE of
    a history across several scans, not about the chained phases, and running
    audit + fixes for every scan in every test would be paying for coverage
    another suite already provides.
    """
    return engines_only_executor


async def _sign_up(client: AsyncClient, email: str = "history@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "History Test Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    def _install(rivals: tuple[str, ...] = ("Zendesk", "Freshdesk"), domain: str = "g2.com"):
        generated = [
            GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
            for i in range(4)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub"

        named = " ".join(f"{r} is an option." for r in rivals)
        answer_text = f"Help Scout is simpler and well liked. {named}"

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            out = []
            for engine in engines:
                citations = (
                    [
                        CitedSource(url=f"https://{domain}/x", domain=domain, position=1),
                        CitedSource(
                            url="https://helpscout.com/y", domain="helpscout.com", position=2
                        ),
                    ]
                    if engine is Engine.CLAUDE_SEARCH
                    else []
                )
                out.append(
                    EngineAnswer(
                        engine=engine,
                        engine_version="stub",
                        prompt_text=prompt,
                        text=answer_text,
                        citations=citations,
                        latency_ms=5,
                    )
                )
            return out

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)

    return _install


async def _client_id(client: AsyncClient, url: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": url, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _detect(client: AsyncClient, cid: str, stub_discovery, rivals: tuple[str, ...]) -> None:  # noqa: ANN001
    """Give the client a competitor set.

    Without one, `share_of_voice` is EXCLUDED from the score (NO_COMPETITOR_SET)
    and there are no rivals to compare — so a Rankings trend has nothing to
    plot. That is correct product behaviour, and it means these tests have to
    detect a set on purpose rather than assume one appears.
    """
    stub_discovery(
        serp_domains=[f"{r.lower()}.com" for r in rivals],
        cocit_brands=[(r, f"{r.lower()}.com") for r in rivals],
    )
    resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
    assert resp.status_code == 201, resp.text


async def _run_scan(client: AsyncClient, cid: str) -> str:
    """Start a scan. **202, not 201** — the endpoint accepts rather than creates."""
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


class TestHistoryShape:
    async def test_a_client_with_no_scans_has_an_empty_series_not_an_error(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _client_id(client)

        resp = await client.get(f"{BASE}/clients/{cid}/history")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scans"] == []
        assert body["scansWithoutData"] == 0
        # Identity is still present: a client exists before it is measured.
        assert body["clientId"] == cid
        assert body["domain"] == "helpscout.com"

    async def test_one_scan_yields_one_point(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        stub_engines()
        sid = await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/history")).json()
        assert [s["scanId"] for s in body["scans"]] == [sid]
        point = body["scans"][0]
        assert point["citedDomains"], "a scan that cited sources must report them"
        assert point["shareOfVoice"] is not None

    async def test_scans_are_oldest_first_because_a_trend_reads_left_to_right(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        first = await _run_scan(client, cid)
        second = await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/history")).json()
        assert [s["scanId"] for s in body["scans"]] == [first, second]
        # And the opposite order to the clients LIST, deliberately.
        stamps = [s["scannedAt"] for s in body["scans"]]
        assert stamps == sorted(stamps)

    async def test_cited_domains_are_counted_not_truncated_to_the_report_s_cap(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        sid = await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/history")).json()
        reported = {d["domain"]: d["citations"] for d in body["scans"][0]["citedDomains"]}

        # Against the rows themselves, so the count is not merely self-consistent.
        rows = (
            await session.execute(
                select(Citation.source_domain, func.count())
                .join(EngineResult, EngineResult.id == Citation.engine_result_id)
                .where(EngineResult.scan_id == sid)
                .group_by(Citation.source_domain)
            )
        ).all()
        assert reported == {domain: count for domain, count in rows}

    async def test_the_subject_s_own_domain_is_flagged(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/history")).json()
        by_domain = {d["domain"]: d for d in body["scans"][0]["citedDomains"]}
        assert by_domain["helpscout.com"]["citesSubject"] is True
        assert by_domain["g2.com"]["citesSubject"] is False


class TestRankingsMaterial:
    async def test_every_rival_carries_the_three_comparable_dimensions(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        stub_engines()
        await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/history")).json()
        rivals = body["scans"][0]["competitors"]
        assert rivals, "the stub answers name rivals, so a set must exist"
        for r in rivals:
            assert r["mentionRate"] is not None
            assert r["shareOfVoice"] is not None
            assert r["citationStrength"] is not None

    async def test_no_rival_carries_a_composite_and_none_ever_will(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        # `CompetitorComparison` refuses to produce one: sentiment is classified
        # toward the subject only and technical foundation is the subject's own
        # site, so 25% of the weight has no rival input. A field here would be
        # an invitation to plot it against the client's real composite.
        await _sign_up(client)
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        stub_engines()
        await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/history")).json()
        assert body["scans"][0]["competitors"], "need rivals for this to mean anything"
        for r in body["scans"][0]["competitors"]:
            assert "composite" not in r

    async def test_subject_and_rival_share_of_voice_are_on_one_axis(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        # This is what makes the Rankings chart honest: `scoring.share_of_voice`
        # is subject mentions over total brand mentions, and
        # `compare_competitors` is that rival's appearances over the identical
        # total. So the field sums to at most 100 and a rise for one really is a
        # fall for another.
        await _sign_up(client)
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        stub_engines()
        await _run_scan(client, cid)

        point = (await client.get(f"{BASE}/clients/{cid}/history")).json()["scans"][0]
        total = float(point["shareOfVoice"]) + sum(
            float(r["shareOfVoice"]) for r in point["competitors"]
        )
        assert 0 < total <= 100.01

    async def test_a_rival_absent_from_one_scan_is_absent_not_zero(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """The case the whole trend design turns on.

        The endpoint must report what each scan's set actually contained. A row
        carrying 0.00 for a rival that was never in that scan's set would be a
        measurement nobody took, and the chart would draw a collapse.
        """
        await _sign_up(client)
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))

        stub_engines(rivals=("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)
        stub_engines(rivals=("Zendesk",))
        await _run_scan(client, cid)

        body = (await client.get(f"{BASE}/clients/{cid}/history")).json()
        assert len(body["scans"]) == 2
        names = [{r["name"] for r in s["competitors"]} for s in body["scans"]]
        # Whatever detection produced, the two scans are reported independently
        # and neither invents a row for a rival it did not contain.
        for scan_names, scan in zip(names, body["scans"], strict=True):
            for rival in scan["competitors"]:
                assert rival["name"] in scan_names


class TestScopingAndSafety:
    async def test_another_agency_s_client_is_not_found_not_forbidden(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        # 404 rather than 403, the same as every other client route: a 403
        # would confirm the id exists somewhere.
        await _sign_up(client, "owner-a@test.example")
        stub_engines()
        cid = await _client_id(client)
        await _run_scan(client, cid)
        await client.post(f"{BASE}/auth/logout")

        await _sign_up(client, "owner-b@test.example")
        resp = await client.get(f"{BASE}/clients/{cid}/history")
        assert resp.status_code == 404

    async def test_requires_a_session(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/clients/clnt_01ABCDEFGHIJKLMNOPQRSTUVWX/history")
        assert resp.status_code == 401

    async def test_reading_a_history_writes_nothing(
        self, client: AsyncClient, stub_engines, session
    ) -> None:  # noqa: ANN001
        """It derives the rival comparison on read; it must not persist it.

        `score_scan(persist=False)` is what this depends on, and the failure
        mode if it ever changed is silent: an extra Score row per history view.
        """
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _run_scan(client, cid)

        before = (await session.execute(select(func.count()).select_from(Scan))).scalar()
        from avp_api.models.score import Score

        scores_before = (
            await session.execute(select(func.count()).select_from(Score))
        ).scalar()

        for _ in range(3):
            assert (await client.get(f"{BASE}/clients/{cid}/history")).status_code == 200

        after = (await session.execute(select(func.count()).select_from(Scan))).scalar()
        scores_after = (
            await session.execute(select(func.count()).select_from(Score))
        ).scalar()
        assert (before, scores_before) == (after, scores_after)

    async def test_carries_no_field_that_could_hold_engine_prose(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        # ip-safety.md #7. Domains, brand names and counts are facts; the
        # answer's text is not ours to republish, and the stub answers carry a
        # distinctive sentence that must not appear anywhere in the response.
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _run_scan(client, cid)

        raw = (await client.get(f"{BASE}/clients/{cid}/history")).text
        assert "simpler and well liked" not in raw
        assert "question 0" not in raw

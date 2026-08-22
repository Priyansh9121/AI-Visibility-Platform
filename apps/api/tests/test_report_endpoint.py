"""The report projection — Epic 7.

Engine execution is stubbed (Epic 4's stubs) so these run without paid calls.
Nothing about the score, the competitor set or the audit is mocked — the point
of these tests is that the report reads what those epics actually persisted.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "report@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Report Test Agency", "fullName": "Op",
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


async def _scored_scan(client: AsyncClient, stub_engines, n: int = 4) -> str:  # noqa: ANN001
    sid = await _scan(client, stub_engines, n)
    assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
    return sid


class TestReportShape:
    async def test_returns_every_beat_s_material_in_one_call(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)

        resp = await client.get(f"{BASE}/scans/{sid}/report")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # score -> gap -> proof -> fix, each from a different epic's tables.
        assert body["scanId"] == sid
        assert body["score"]["status"] == "scored"
        assert body["dimensions"], "the gap beat needs dimensions"
        assert body["proof"]["engineResults"] > 0
        assert "audit" in body
        # The white-label surface: agency identity, never ours.
        assert body["agency"]["name"] == "Report Test Agency"
        assert body["agency"]["slug"]
        assert body["subject"]["domain"] == "helpscout.com"

    async def test_dimensions_are_ordered_heaviest_first(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The ledger stacks the heaviest segment at the bottom, so the order
        is part of the visualisation's contract, not a display preference."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        dims = (await client.get(f"{BASE}/scans/{sid}/report")).json()["dimensions"]

        assert [d["key"] for d in dims] == [
            "mention_rate", "share_of_voice", "citation_strength",
            "sentiment", "technical_foundation",
        ]

    async def test_included_weights_sum_to_100_so_the_ledger_is_honest(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The Luminance Ledger's identity is that total lit height IS the
        composite. That only holds if the weights it is given sum to 100."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        dims = (await client.get(f"{BASE}/scans/{sid}/report")).json()["dimensions"]

        included = [d for d in dims if d["included"]]
        assert sum(Decimal(d["weight"]) for d in included) == Decimal("100.00")

    async def test_decimals_cross_the_wire_as_strings(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A JSON number is an IEEE double — scoring-spec.md rule 3 forbids it."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        body = (await client.get(f"{BASE}/scans/{sid}/report")).json()

        assert isinstance(body["score"]["composite"], str)
        for dim in body["dimensions"]:
            assert isinstance(dim["weight"], str)
            assert dim["subscore"] is None or isinstance(dim["subscore"], str)

    async def test_report_is_stable_across_two_reads_of_the_same_data(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A document shown to a client twice must not reorder its evidence."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)

        first = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        second = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        # generatedAt is a timestamp of the read, so it is expected to differ.
        first.pop("generatedAt"), second.pop("generatedAt")
        assert first == second


class TestDegradedStates:
    async def test_unscored_scan_reports_a_null_score_not_a_zero(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """"Never scored" and "scored zero" are different things to show."""
        await _sign_up(client)
        sid = await _scan(client, stub_engines)

        body = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert body["score"] is None
        assert body["dimensions"] == []
        # The proof beat still has material — the scan ran, it just wasn't scored.
        assert body["proof"]["engineResults"] > 0

    async def test_technical_foundation_exclusion_carries_its_reason(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """NOT_YET_MEASURED must reach the UI, so it can say "not yet checked"
        rather than "nothing found"."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        dims = (await client.get(f"{BASE}/scans/{sid}/report")).json()["dimensions"]

        tech = next(d for d in dims if d["key"] == "technical_foundation")
        assert tech["included"] is False
        assert tech["subscore"] is None, "an excluded dimension is never a zero"
        assert tech["exclusionReason"] == "NOT_YET_MEASURED"

    async def test_no_competitor_set_is_visible_as_its_own_reason(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A detection failure must not read to a client as a bad result.

        Detection does not run in this fixture, so every scan here exercises
        the NO_COMPETITOR_SET path — the same one a real scan takes when SERP
        and co-citation both come back empty.
        """
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)

        body = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        sov = next(d for d in body["dimensions"] if d["key"] == "share_of_voice")
        assert sov["included"] is False
        assert sov["subscore"] is None
        assert sov["exclusionReason"] == "NO_COMPETITOR_SET"
        # It keeps its NOMINAL §6 weight so the report can say what the
        # dimension would have been worth had it been measurable.
        assert Decimal(sov["weight"]) == Decimal("25")
        # The INCLUDED weights still sum to 100 after redistribution, so the
        # ledger identity (total lit height == composite) survives exclusion.
        included = [d for d in body["dimensions"] if d["included"]]
        assert sum(Decimal(d["weight"]) for d in included) == Decimal("100.00")

    async def test_unaudited_scan_reports_a_null_audit(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        assert (await client.get(f"{BASE}/scans/{sid}/report")).json()["audit"] is None


class TestProofBeat:
    async def test_engine_coverage_counts_every_engine_separately(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        engines = {c["engine"] for c in proof["engineCoverage"]}
        assert engines == {"claude", "claude_search"}
        for coverage in proof["engineCoverage"]:
            assert coverage["answered"] <= coverage["promptsRun"]
            assert coverage["mentioned"] <= coverage["answered"]

    async def test_citations_are_grouped_by_domain_with_a_link_out(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """A citation is evidenced as a domain plus a link — never a quotation."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        subject = proof["subjectCitedDomains"]
        assert subject, "the stub cites helpscout.com"
        assert subject[0]["domain"] == "helpscout.com"
        assert subject[0]["citesSubject"] is True
        assert subject[0]["sampleUrl"].startswith("https://")
        assert subject[0]["citations"] >= 1

    async def test_competitor_citations_rank_above_unattributed_ones(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Who is cited INSTEAD of the subject is the point of this section.

        Found on the real Help Scout scan: zendesk.com and front.com were cited
        once each, and ranking purely by count pushed both out of a twelve-row
        list full of review blogs — dropping precisely the evidence the beat
        exists to show.
        """
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        rows = proof["competitorCitedDomains"]
        attributed = [i for i, r in enumerate(rows) if r["competitorName"]]
        unattributed = [i for i, r in enumerate(rows) if not r["competitorName"]]
        if attributed and unattributed:
            assert max(attributed) < min(unattributed), (
                "a competitor-attributed citation must outrank an unattributed one"
            )
        # Within each group the order is still most-cited first, and total.
        for group in (attributed, unattributed):
            counts = [rows[i]["citations"] for i in group]
            assert counts == sorted(counts, reverse=True)

    async def test_mention_share_marks_who_outranks_the_subject(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """`outranksSubject` is arithmetic over stored ordinals, not an opinion."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        shares = proof["mentionShares"]
        assert shares, "the stub answer names two brands"
        subject = next(s for s in shares if s["isSubject"])
        assert subject["outranksSubject"] is False, "the subject never outranks itself"
        # Sorted most-mentioned first.
        assert [s["appearances"] for s in shares] == sorted(
            (s["appearances"] for s in shares), reverse=True
        )


class TestAccessControl:
    async def test_requires_authentication(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/scans/scan_01ABC/report")
        assert resp.status_code == 401

    async def test_another_agency_s_scan_is_404_not_403(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Confirming an id exists is itself a cross-tenant leak."""
        await _sign_up(client, "owner@test.example")
        sid = await _scored_scan(client, stub_engines)
        await client.post(f"{BASE}/auth/logout")

        await _sign_up(client, "intruder@test.example")
        assert (await client.get(f"{BASE}/scans/{sid}/report")).status_code == 404

    async def test_unknown_scan_is_404(self, client: AsyncClient) -> None:
        await _sign_up(client)
        resp = await client.get(f"{BASE}/scans/scan_01DOESNOTEXIST000000000000/report")
        assert resp.status_code == 404

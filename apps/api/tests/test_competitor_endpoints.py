"""Competitor detection endpoints.

Discovery is stubbed so these exercise persistence, override semantics, scoping
and the API contract without paid SerpApi searches or model calls.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from avp_api.services import competitors as detection
from avp_api.services.cocitation import CoCitationHit, CoCitationResult
from avp_api.services.serp import SerpHit, SerpResult

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "comp@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Competitor Test Agency", "fullName": "Operator",
            "email": email, "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture
def stub_discovery(monkeypatch):  # noqa: ANN001, ANN201
    """Replace both discovery signals with deterministic stubs."""

    def _install(
        serp_domains: list[str] | None = None,
        cocit_brands: list[tuple[str, str | None]] | None = None,
        serp_ok: bool = True,
        cocit_ok: bool = True,
    ):
        async def fake_search_many(queries, **kwargs):  # noqa: ANN001, ANN003, ARG001
            if not serp_ok:
                return [SerpResult(query=q, ok=False, error_code="SERP_TIMEOUT") for q in queries]
            # Two distinct queries, mirroring a real run. A single-query stub
            # would be gated out by MIN_SERP_QUERIES_FOR_UNCORROBORATED and
            # these tests would assert against an always-empty set.
            return [
                SerpResult(
                    query=q,
                    hits=[
                        SerpHit(domain=d, position=i, query=q)
                        for i, d in enumerate(serp_domains or [], 1)
                    ],
                )
                for q in queries[:2]
            ]

        async def fake_run_prompts(prompts, **kwargs):  # noqa: ANN001, ANN003, ARG001
            if not cocit_ok:
                return [
                    CoCitationResult(prompt=p, ok=False, error_code="PROVIDER_ERROR")
                    for p in prompts
                ]
            return [
                CoCitationResult(
                    prompt=prompts[0],
                    hits=[
                        CoCitationHit(name=n, domain=d, position=i, prompt=prompts[0])
                        for i, (n, d) in enumerate(cocit_brands or [], 1)
                    ],
                )
            ]

        monkeypatch.setattr(detection.serp_service, "search_many", fake_search_many)
        monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", fake_run_prompts)

    return _install


class TestDetectEndpoint:
    async def test_detection_ranks_and_returns_competitors(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["front.com", "zendesk.com", "kayako.com"],
            cocit_brands=[
                ("Zendesk", "zendesk.com"),
                ("Front", "front.com"),
                ("Intercom", "intercom.com"),
            ],
        )

        resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["id"].startswith("cset_")
        assert body["status"] == "ok"
        ranks = [c["rank"] for c in body["competitors"]]
        assert ranks == sorted(ranks) == list(range(1, len(ranks) + 1))
        # Corroborated rivals must outrank single-signal ones.
        assert body["competitors"][0]["corroborated"] is True
        assert body["competitors"][0]["detectionSource"] == "both"

    async def test_detection_confidence_is_a_string_decimal(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["a.com", "b.com", "c.com"],
            cocit_brands=[("A", "a.com"), ("B", "b.com"), ("C", "c.com")],
        )
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        assert body["detectionConfidence"] == "1.000"
        assert isinstance(body["detectionConfidence"], str)

    async def test_single_signal_reports_null_confidence_not_zero(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """Only one signal ran, so agreement is unmeasurable."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com", "b.com", "c.com"], cocit_ok=False)
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        assert body["detectionConfidence"] is None
        assert body["status"] == "weak_signal"
        assert body["competitors"], "a usable set should still be returned"

    async def test_no_signal_returns_empty_set_not_an_error(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_ok=False, cocit_ok=False)
        resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        assert resp.status_code == 201
        assert resp.json()["status"] == "no_signal"
        assert resp.json()["competitors"] == []

    async def test_records_whether_industry_seeded_the_queries(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """Makes 'was this set built on a bad industry label?' answerable."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        # The client was created with classify: false, so it has no industry.
        assert body["usedIndustrySeed"] is False
        assert body["serpQueriesRun"] >= 1

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        from avp_api import ids

        resp = await client.post(f"{BASE}/clients/{ids.new_id(ids.CLIENT)}/competitors/detect")
        assert resp.status_code == 401

    async def test_unknown_client_is_404(self, client: AsyncClient) -> None:
        from avp_api import ids

        await _sign_up(client)
        resp = await client.post(f"{BASE}/clients/{ids.new_id(ids.CLIENT)}/competitors/detect")
        assert resp.status_code == 404


class TestOverride:
    async def test_replacing_the_set_marks_everything_manual(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [
                {"name": "Zendesk", "domain": "zendesk.com"},
                {"name": "Intercom", "domain": "intercom.com"},
            ]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [c["name"] for c in body["competitors"]] == ["Zendesk", "Intercom"]
        assert all(c["isManualOverride"] for c in body["competitors"])
        assert all(c["detectionSource"] == "manual" for c in body["competitors"])

    async def test_override_clears_detection_confidence(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """Confidence measures agreement between automated signals.

        Neither produced this set, so keeping the old value would attach a
        corroboration claim to rows that were never corroborated.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        detected = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        assert detected["detectionConfidence"] is not None

        body = (await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Zendesk", "domain": "zendesk.com"}]},
        )).json()
        assert body["detectionConfidence"] is None

    async def test_manual_entries_survive_redetection(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """The point of an override: a correction must not be silently undone."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
        )

        stub_discovery(serp_domains=["b.com", "c.com"], cocit_brands=[("B", "b.com")])
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        names = [c["name"] for c in body["competitors"]]
        assert "Operator Pick" in names
        kept = next(c for c in body["competitors"] if c["name"] == "Operator Pick")
        assert kept["isManualOverride"] is True
        assert kept["rank"] == 1, "operator picks keep the top ranks"

    async def test_empty_override_clears_the_set(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        body = (await client.put(
            f"{BASE}/clients/{cid}/competitors", json={"competitors": []}
        )).json()
        assert body["competitors"] == []
        assert body["status"] == "no_signal"


class TestReads:
    async def test_get_returns_the_latest_set_in_rank_order(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["a.com", "b.com", "c.com"],
            cocit_brands=[("A", "a.com"), ("B", "b.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        body = (await client.get(f"{BASE}/clients/{cid}/competitors")).json()
        ranks = [c["rank"] for c in body["competitors"]]
        assert ranks == sorted(ranks)

    async def test_get_before_detection_is_404(self, client: AsyncClient) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        resp = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert resp.status_code == 404
        assert "Run detection first" in resp.json()["detail"]

    async def test_another_agency_cannot_read_the_set(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        from httpx import ASGITransport
        from httpx import AsyncClient as Second

        await _sign_up(client, "one@iso.example")
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with Second(transport=transport, base_url="http://testserver") as other:
            await _sign_up(other, "two@iso.example")
            assert (await other.get(f"{BASE}/clients/{cid}/competitors")).status_code == 404


class TestSerpGateEndToEnd:
    """Proves the SERP gate is wired into the request path, not just the
    pure function. A rule that works in isolation and is never reached is
    indistinguishable from no rule at all."""

    async def test_single_query_serp_candidate_is_excluded_from_the_response(
        self, client: AsyncClient, monkeypatch
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)

        async def fake_search_many(queries, **kwargs):  # noqa: ANN001, ANN003, ARG001
            # Three domains appear in two queries each and clear the gate;
            # `oneoff.com` appears in one and does not. Three eligible
            # candidates are needed, or the floor (Epic 3.5 addendum) would
            # backfill `oneoff.com` and mask the exclusion under test.
            eligible = ["solid.com", "solid2.com", "solid3.com"]
            return [
                SerpResult(query=queries[0], hits=[
                    *[SerpHit(domain=d, position=i, query=queries[0])
                      for i, d in enumerate(eligible, 1)],
                    SerpHit(domain="oneoff.com", position=4, query=queries[0]),
                ]),
                SerpResult(query=queries[1], hits=[
                    SerpHit(domain=d, position=i, query=queries[1])
                    for i, d in enumerate(eligible, 1)
                ]),
            ]

        async def fake_run_prompts(prompts, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return [CoCitationResult(prompt=prompts[0], hits=[
                CoCitationHit(name="Named", domain="named.com", position=1, prompt=prompts[0]),
            ])]

        monkeypatch.setattr(detection.serp_service, "search_many", fake_search_many)
        monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", fake_run_prompts)

        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        domains = {c["domain"] for c in body["competitors"]}

        assert "solid.com" in domains, "two distinct queries clears the gate"
        assert "named.com" in domains, "co-citation candidates are exempt"
        assert "oneoff.com" not in domains, "one query is not enough evidence"
        # The gated candidate is still counted as examined.
        assert body["candidatesConsidered"] == 5


class TestOverrideReachesDownstream:
    """Epic 3.6: the override is only worth having if the rest of the system
    reads it.

    Epic 3 shipped `is_manual_override` and the endpoint that sets it, but
    nothing consumed the flag and no UI could set it. Epics 5-8 were then built
    on the competitor set without ever being pointed at a corrected one. These
    assert the downstream contract Epic 3.6's report affordance depends on —
    and, deliberately, they assert it WITHOUT any Epic 5-8 code changing, which
    is the evidence that Epic 3's design was complete rather than that this
    epic patched around a gap.
    """

    async def test_the_report_projection_carries_the_provenance_flag(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """The report table's "set by hand" badge reads this field.

        `ReportCompetitorOut` inherits it from `CompetitorOut` — no Epic 7
        schema change was needed, which is why the badge could be added to the
        report without touching the projection.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        scan_id = (await client.get(f"{BASE}/clients/{cid}/competitors")).json()["scanId"]

        before = (await client.get(f"{BASE}/scans/{scan_id}/report")).json()
        assert all(
            c["isManualOverride"] is False for c in before["competitorSet"]["competitors"]
        )

        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
        )

        after = (await client.get(f"{BASE}/scans/{scan_id}/report")).json()
        competitors = after["competitorSet"]["competitors"]
        assert [c["name"] for c in competitors] == ["Operator Pick"]
        assert all(c["isManualOverride"] is True for c in competitors)

    async def test_the_report_drops_the_corroboration_claim_after_an_override(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """`detectionConfidence` measures agreement between two automated
        signals. Neither produced a corrected set, so the report must stop
        making the claim rather than carry a stale one next to new rows."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["a.com"], cocit_brands=[("A", "a.com")]
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        scan_id = (await client.get(f"{BASE}/clients/{cid}/competitors")).json()["scanId"]

        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
        )
        report = (await client.get(f"{BASE}/scans/{scan_id}/report")).json()
        assert report["competitorSet"]["detectionConfidence"] is None

    # A third test here would assert that re-scoring compares against the
    # corrected set. It is deliberately NOT in this file.
    #
    # The first version of it passed, and passed for a bad reason: detection
    # and the scan pipeline create separate scans, so the competitor set under
    # test had no answered engine results, `compare_competitors` short-circuits
    # to [] in that case, and the assertion was `all(... for c in comparisons)`
    # — vacuously true over an empty list. Tightening it to assert the count
    # first is what exposed that. Making it real here would mean rebuilding
    # test_report_endpoint.py's whole engine-stub pipeline to get one scan that
    # carries both, which is scaffolding testing scaffolding.
    #
    # It is asserted in scripts/verify_competitor_override.py instead, against
    # a scan in avp_dev that has six real answered results, where the
    # comparison list is genuinely non-empty and the claim genuinely holds.


class TestStruckCompetitorsStayStruck:
    """Epic 3.6: the half of "correcting a set" Epic 3 could not express.

    `is_manual_override` protected additions. A REMOVAL left no row and
    therefore no record, so the next detection run surfaced the same rival,
    matched no override, and reinstated it. Every test below fails against
    Epic 3's persistence, and none of them existed then — the gap was found by
    running the mechanism against real data, not by the suite.
    """

    async def test_a_struck_rival_does_not_come_back_on_redetection(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        detected = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        assert {c["name"] for c in detected["competitors"]} >= {"Keep", "Strike"}

        # The operator keeps one and strikes the other.
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Keep", "domain": "keep.com"}]},
        )

        # Detection runs again and re-offers exactly the struck rival.
        stub_discovery(
            serp_domains=["strike.com"], cocit_brands=[("Strike", "strike.com")]
        )
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        names = [c["name"] for c in body["competitors"]]
        assert "Strike" not in names, "the struck rival was reinstated by re-detection"
        assert "Keep" in names

    async def test_a_struck_rival_is_not_returned_by_the_api(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """The tombstone is bookkeeping. Returning it would undo the correction
        in the one place the operator would look to confirm it."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        put = (await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Keep", "domain": "keep.com"}]},
        )).json()
        assert [c["name"] for c in put["competitors"]] == ["Keep"]

        got = (await client.get(f"{BASE}/clients/{cid}/competitors")).json()
        assert [c["name"] for c in got["competitors"]] == ["Keep"]

    async def test_a_struck_rival_leaves_the_report(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        scan_id = (await client.get(f"{BASE}/clients/{cid}/competitors")).json()["scanId"]
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Keep", "domain": "keep.com"}]},
        )
        report = (await client.get(f"{BASE}/scans/{scan_id}/report")).json()
        assert [c["name"] for c in report["competitorSet"]["competitors"]] == ["Keep"]

    async def test_an_operator_can_change_their_mind(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """Re-adding a struck rival must revive it, not collide with its own
        tombstone — `uq_competitors_set_name` means a name is either live or
        struck, never both."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Keep", "domain": "keep.com"}]},
        )
        revived = (await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={
                "competitors": [
                    {"name": "Keep", "domain": "keep.com"},
                    {"name": "Strike", "domain": "strike.com"},
                ]
            },
        )).json()
        assert [c["name"] for c in revived["competitors"]] == ["Keep", "Strike"]

    async def test_tombstones_do_not_consume_ranks(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """A suppressed row is not a competitor. If it took a rank, the next
        detected rival would start at 3 in a two-rival set and the report's
        numbering would have a hole in it."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Keep", "domain": "keep.com"}]},
        )
        stub_discovery(serp_domains=["fresh.com"], cocit_brands=[("Fresh", "fresh.com")])
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        ranks = sorted(c["rank"] for c in body["competitors"])
        assert ranks == list(range(1, len(ranks) + 1)), body["competitors"]

    async def test_striking_everything_still_blocks_redetection(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """An empty override is a statement that none of these are rivals, not
        an absence of opinion."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["strike.com"], cocit_brands=[("Strike", "strike.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(f"{BASE}/clients/{cid}/competitors", json={"competitors": []})

        stub_discovery(serp_domains=["strike.com"], cocit_brands=[("Strike", "strike.com")])
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        assert [c["name"] for c in body["competitors"]] == []

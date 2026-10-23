"""Competitor detection endpoints.

Discovery is stubbed so these exercise persistence, override semantics, scoping
and the API contract without paid SerpApi searches or model calls.
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api.models import Client, Competitor, CompetitorSet, Scan, ScanStatus
from avp_api.services import competitors as detection
from avp_api.services import scan_runner
from avp_api.services.cocitation import CoCitationHit, CoCitationResult
from avp_api.services.serp import SerpHit, SerpResult

BASE = "/api/v1"
# What `GET` and `PUT /clients/{clientId}/competitors` say for a client with no set on any
# scan. Whole, so a change to what a caller reads has to change this test. It tells nobody
# to press the detect route, which only paying agencies may (launch checklist 3.61).
NO_SET = (
    "No competitor set for this client yet. "
    "A scan runs detection for a client that has none."
)


async def _versioned(client: AsyncClient, cid: str, body: dict[str, object]) -> dict[str, object]:
    """`body` as the web sends it: naming the version of the list it has just read.

    None when the client has no list yet, which is what a writer that read none sends.
    """
    read = await client.get(f"{BASE}/clients/{cid}/competitors")
    return {**body, "version": read.json()["version"] if read.status_code == 200 else None}


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


async def _scan_that_took_the_list(session, cid: str) -> str:  # noqa: ANN001
    """A scan that started after the list was saved or detected, and so took it as its own set.

    A save or a detection run from the button writes the client's next list, and no scan's own
    set. A scan's report shows a list only once the scan has taken it, which is what the chain's
    competitor phase does at the start of a scan: this runs that one step for one scan.
    """
    owner = (await session.execute(select(Client).where(Client.id == cid))).scalar_one()
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=cid, agency_id=owner.agency_id,
        status=ScanStatus.SUCCEEDED,
    )
    session.add(scan)
    await session.flush()
    _found, how = await detection.ensure_set_for_scan(session, scan, owner)
    assert how in ("adopted", "carried_forward"), how
    await session.commit()
    return scan.id


# `stub_discovery` now lives in conftest.py — test_report_endpoint.py needs the
# same stub to build a mixed competitor set, and a second copy of a fixture this
# fiddly is a drift hazard for both.


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
            json=await _versioned(client, cid, {"competitors": [
                {"name": "Zendesk", "domain": "zendesk.com"},
                {"name": "Intercom", "domain": "intercom.com"},
            ]}),
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
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Zendesk", "domain": "zendesk.com"}]}
            ),
        )).json()
        assert body["detectionConfidence"] is None

    async def test_manual_entries_survive_redetection(
        self, client: AsyncClient, stub_discovery, a_day_passes
    ) -> None:  # noqa: ANN001
        """The point of an override: a correction must not be silently undone."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client,
                cid,
                {"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
            ),
        )

        await a_day_passes()  # detection from the button runs once per client per day
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
            f"{BASE}/clients/{cid}/competitors", json=await _versioned(
                client, cid, {"competitors": []}
            )
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

    async def test_get_before_a_set_exists_is_404(self, client: AsyncClient) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        resp = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/not-found"
        assert resp.json()["detail"] == NO_SET
        # True for every caller: an agency that may not press the detect route reads this
        # too, so it says nothing of the kind.
        assert "Run detection" not in resp.json()["detail"]

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


class TestAClientWithNoSetYet:
    """What `GET` and `PUT` say before any scan has made a set, and what makes one.

    The sentence names the thing that makes a set — a scan, which runs detection for a
    client that has none — so each half is pinned here: the 404 on both routes, that the
    refused `PUT` writes nothing, and that a scan really does end it.
    """

    async def test_put_before_a_set_exists_is_404_with_the_same_sentence(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Zendesk", "domain": "zendesk.com"}]},
        )

        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == "/problems/not-found"
        assert resp.json()["detail"] == NO_SET
        # Nothing was made by the refusal: naming rivals by hand does not start a set.
        assert (await client.get(f"{BASE}/clients/{cid}/competitors")).status_code == 404

    async def test_a_scan_is_what_ends_the_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sentence's second half is true: a scan runs detection for a client with none."""
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await client.get(f"{BASE}/clients/{cid}/competitors")).status_code == 404

        async def no_engines(*args, **kwargs) -> None:  # noqa: ANN002, ANN003, ARG001
            # The competitor-set phase is the one under test and it comes first; the engine
            # loop would otherwise call the real providers.
            return None

        monkeypatch.setattr(scan_runner, "run_scan", no_engines)
        started = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert started.status_code == 202, started.text

        found = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert found.status_code == 200, found.text
        # The scan's own set, with rivals the (stubbed) detection found.
        assert found.json()["scanId"] == started.json()["id"]
        assert found.json()["competitors"] != []


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

    Since launch checklist 3.78/3.79 a save is written to the client's next list and reaches a
    scan when the scan starts, so each test below follows a scan that took the list. The scan
    that had already started is held beside it: a save does not reach its report.
    """

    async def test_the_report_projection_carries_the_provenance_flag(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The report table's "set by hand" badge reads this field.

        `ReportCompetitorOut` inherits it from `CompetitorOut` — no Epic 7
        schema change was needed, which is why the badge could be added to the
        report without touching the projection.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        started = await _scan_that_took_the_list(session, cid)

        before = (await client.get(f"{BASE}/scans/{started}/report")).json()
        assert before["competitorSet"]["competitors"]
        assert all(
            c["isManualOverride"] is False for c in before["competitorSet"]["competitors"]
        )

        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client,
                cid,
                {"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
            ),
        )

        # The scan that had started keeps the list it measured.
        unchanged = (await client.get(f"{BASE}/scans/{started}/report")).json()
        assert unchanged["competitorSet"] == before["competitorSet"]
        # The next scan takes the saved one, and the badge's field is on it.
        later = await _scan_that_took_the_list(session, cid)
        competitors = (await client.get(f"{BASE}/scans/{later}/report")).json()[
            "competitorSet"
        ]["competitors"]
        assert [c["name"] for c in competitors] == ["Operator Pick"]
        assert all(c["isManualOverride"] is True for c in competitors)

    async def test_the_report_drops_the_corroboration_claim_after_an_override(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """`detectionConfidence` measures agreement between two automated
        signals. Neither produced a corrected set, so the report must stop
        making the claim rather than carry a stale one next to new rows."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["a.com"], cocit_brands=[("A", "a.com")]
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        started = await _scan_that_took_the_list(session, cid)

        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client,
                cid,
                {"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
            ),
        )
        # The scan that measured the detected list still says what the detection found: it did.
        kept = (await client.get(f"{BASE}/scans/{started}/report")).json()
        assert kept["competitorSet"]["detectionConfidence"] is not None
        later = await _scan_that_took_the_list(session, cid)
        report = (await client.get(f"{BASE}/scans/{later}/report")).json()
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
    #
    # And since launch checklist 3.78/3.79 the opposite is true and is tested, for a scan that
    # has answers: `test_a_finished_scans_list_is_final.py` names rivals after a scan finished
    # and holds its figures, its history and a re-score unchanged.


class TestStruckCompetitorsStayStruck:
    """Epic 3.6: the half of "correcting a set" Epic 3 could not express.

    `is_manual_override` protected additions. A REMOVAL left no row and
    therefore no record, so the next detection run surfaced the same rival,
    matched no override, and reinstated it. Every test below fails against
    Epic 3's persistence, and none of them existed then — the gap was found by
    running the mechanism against real data, not by the suite.
    """

    async def test_a_struck_rival_does_not_come_back_on_redetection(
        self, client: AsyncClient, stub_discovery, a_day_passes
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
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
            ),
        )

        # The next day, detection runs again and re-offers exactly the struck rival.
        await a_day_passes()
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
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
            ),
        )).json()
        assert [c["name"] for c in put["competitors"]] == ["Keep"]

        got = (await client.get(f"{BASE}/clients/{cid}/competitors")).json()
        assert [c["name"] for c in got["competitors"]] == ["Keep"]

    async def test_a_struck_rival_leaves_the_report_of_the_next_scan(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        started = await _scan_that_took_the_list(session, cid)
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
            ),
        )
        # The scan that measured both keeps both. It measured them.
        before = (await client.get(f"{BASE}/scans/{started}/report")).json()
        assert {c["name"] for c in before["competitorSet"]["competitors"]} == {"Keep", "Strike"}
        later = await _scan_that_took_the_list(session, cid)
        report = (await client.get(f"{BASE}/scans/{later}/report")).json()
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
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
            ),
        )
        revived = (await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(client, cid, {
                "competitors": [
                    {"name": "Keep", "domain": "keep.com"},
                    {"name": "Strike", "domain": "strike.com"},
                ]
            }),
        )).json()
        assert [c["name"] for c in revived["competitors"]] == ["Keep", "Strike"]

    async def test_tombstones_do_not_consume_ranks(
        self, client: AsyncClient, stub_discovery, a_day_passes
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
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
            ),
        )
        await a_day_passes()
        stub_discovery(serp_domains=["fresh.com"], cocit_brands=[("Fresh", "fresh.com")])
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        ranks = sorted(c["rank"] for c in body["competitors"])
        assert ranks == list(range(1, len(ranks) + 1)), body["competitors"]

    async def test_striking_everything_still_blocks_redetection(
        self, client: AsyncClient, stub_discovery, a_day_passes
    ) -> None:  # noqa: ANN001
        """An empty override is a statement that none of these are rivals, not
        an absence of opinion."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["strike.com"], cocit_brands=[("Strike", "strike.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(f"{BASE}/clients/{cid}/competitors", json=await _versioned(
            client, cid, {"competitors": []}
        ))

        await a_day_passes()
        stub_discovery(serp_domains=["strike.com"], cocit_brands=[("Strike", "strike.com")])
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        assert [c["name"] for c in body["competitors"]] == []


class TestAttributionSurvivesAnOverride:
    """Epic 3.9 / Finding 4: citations and mentions keep their competitor.

    `Citation.competitor_id` and `BrandMention.competitor_id` are both
    ON DELETE SET NULL, and both competitor write paths used to hard-delete
    rows — `apply_override` deleted every row before re-inserting, and
    `persist_detection` deleted every non-manual row on each run. So an
    override or a re-detection silently nulled the attribution for the whole
    scan, and nothing put it back.

    None of the override tests above could have caught it: not one of them
    builds a Citation or a BrandMention, because none of them runs a scan. The
    set was always exercised with nothing pointing at it. That is the coverage
    gap this class closes, and it is the reason the defect lived from Epic 3.6
    to Epic 3.8 in a file with twenty-six passing tests.
    """

    @staticmethod
    async def _attribute(session, scan_id: str) -> dict[str, str]:
        """Hang a citation and a brand mention off each detected competitor.

        Mirrors what `scan_runner` does at scan time: a citation whose
        `source_domain` is the rival's domain, and a mention of the rival by
        name, both carrying `competitor_id`.
        """
        from sqlalchemy import select

        from avp_api import ids
        from avp_api.models import (
            BrandMention,
            Citation,
            Competitor,
            CompetitorSet,
            EngineResult,
            Prompt,
            PromptSet,
        )
        from avp_api.models.engine_result import (
            CitationType,
            Engine,
            EngineResultStatus,
        )
        from avp_api.models.prompt import PromptIntent

        competitors = (
            await session.execute(
                select(Competitor)
                .join(CompetitorSet, CompetitorSet.id == Competitor.competitor_set_id)
                .where(CompetitorSet.scan_id == scan_id)
            )
        ).scalars().all()
        assert competitors, "nothing to attribute to — detection produced no rivals"

        prompt_set = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=scan_id)
        session.add(prompt_set)
        await session.flush()
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT),
            prompt_set_id=prompt_set.id,
            text="who are the alternatives",
            intent=PromptIntent.COMPARISON,
            position=1,
        )
        session.add(prompt)
        await session.flush()

        result = EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT),
            scan_id=scan_id,
            prompt_id=prompt.id,
            engine=Engine.CLAUDE,
            status=EngineResultStatus.OK,
            mentioned=True,
            position=1,
        )
        session.add(result)
        await session.flush()

        expected: dict[str, str] = {}
        for position, competitor in enumerate(competitors, start=1):
            session.add(
                Citation(
                    id=ids.new_id(ids.CITATION),
                    engine_result_id=result.id,
                    source_domain=competitor.domain or f"{competitor.name}.example",
                    source_url=f"https://{competitor.domain or 'x.example'}/p",
                    source_type=CitationType.COMPETITOR,
                    position=position,
                    cites_subject=False,
                    competitor_id=competitor.id,
                )
            )
            session.add(
                BrandMention(
                    id=ids.new_id(ids.BRAND_MENTION),
                    engine_result_id=result.id,
                    entity_name=competitor.name,
                    entity_domain=competitor.domain,
                    is_subject=False,
                    position=position,
                    competitor_id=competitor.id,
                )
            )
            expected[competitor.name] = competitor.id
        await session.commit()
        return expected

    @staticmethod
    async def _attribution(session, scan_id: str) -> dict[str, str | None]:
        """Current name -> competitor name, read back through the FKs."""
        from sqlalchemy import select

        from avp_api.models import BrandMention, Citation, Competitor, EngineResult

        out: dict[str, str | None] = {}
        cites = (
            await session.execute(
                select(Citation)
                .join(EngineResult, EngineResult.id == Citation.engine_result_id)
                .where(EngineResult.scan_id == scan_id)
            )
        ).scalars().all()
        mentions = (
            await session.execute(
                select(BrandMention)
                .join(EngineResult, EngineResult.id == BrandMention.engine_result_id)
                .where(EngineResult.scan_id == scan_id)
            )
        ).scalars().all()
        for row in [*cites, *mentions]:
            key = f"{type(row).__name__}:{getattr(row, 'source_domain', None) or row.entity_name}"
            if row.competitor_id is None:
                out[key] = None
                continue
            competitor = (
                await session.execute(
                    select(Competitor).where(Competitor.id == row.competitor_id)
                )
            ).scalar_one_or_none()
            out[key] = competitor.name if competitor else "<dangling>"
        return out

    async def test_an_override_leaves_a_finished_scans_attribution_alone(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """Since 3.78/3.79 a save does not touch a started scan's rows, so none can be nulled.

        The defect was an override nulling the attribution of rivals it kept. The scan's rows are
        now out of its reach, so every rival stays attributed, a struck one too: the scan
        measured it.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        scan_id = await _scan_that_took_the_list(session, cid)

        before = await self._attribute(session, scan_id)
        assert set(before) >= {"Keep", "Strike"}, before
        attributed = await self._attribution(session, scan_id)

        # The operator keeps Keep and strikes Strike.
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
            ),
        )
        assert resp.status_code == 200, resp.text

        after = await self._attribution(session, scan_id)
        assert after == attributed, "a save changed what the finished scan attributes"
        assert all(v is not None for v in after.values()), after
        assert any("strike" in k.lower() for k in after), after

    async def test_an_override_keeps_the_row_id_for_an_unchanged_rival(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The mechanism, asserted directly, on the list a save writes.

        Reusing the row is what kept the FKs alive when a save reached a scan's rows. It is still
        how `apply_override` works, and the next list is where it works now.
        """
        from sqlalchemy import select

        from avp_api.models import Competitor, CompetitorSet

        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["keep.com"], cocit_brands=[("Keep", "keep.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        async def ids_by_name() -> dict[str, str]:
            rows = (
                await session.execute(
                    select(Competitor)
                    .join(CompetitorSet, CompetitorSet.id == Competitor.competitor_set_id)
                    .where(CompetitorSet.client_id == cid, CompetitorSet.scan_id.is_(None))
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
            return {r.name: r.id for r in rows}

        before = await ids_by_name()
        assert "Keep" in before, before
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
            ),
        )
        session.expire_all()
        after = await ids_by_name()
        assert after.get("Keep") == before.get("Keep"), (
            f"the row was recreated rather than reused: {before} -> {after}"
        )

    async def test_redetection_leaves_a_finished_scans_attribution_alone(
        self, client: AsyncClient, session, stub_discovery, a_day_passes  # noqa: ANN001
    ) -> None:
        """Finding 4 named `apply_override`; `persist_detection` had it too.

        It hard-deleted every non-manual row on each run, so re-detecting a
        scan nulled the attribution for competitors it went on to re-find
        immediately afterwards. The button's detection now writes the next list, so a scan that
        has started keeps every row its answers point at.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "gone.com"],
            cocit_brands=[("Keep", "keep.com"), ("Gone", "gone.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        scan_id = await _scan_that_took_the_list(session, cid)
        await self._attribute(session, scan_id)
        attributed = await self._attribution(session, scan_id)

        # The next day, detection runs again and finds only Keep. The status is asserted: a
        # refused second run would leave the attribution as it was and pass untested.
        await a_day_passes()
        stub_discovery(serp_domains=["keep.com"], cocit_brands=[("Keep", "keep.com")])
        redetected = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        assert redetected.status_code == 201, redetected.text
        assert [c["name"] for c in redetected.json()["competitors"]] == ["Keep"]

        assert await self._attribution(session, scan_id) == attributed
        assert all(v is not None for v in attributed.values()), attributed

    async def test_apply_override_still_keeps_attribution_for_the_rivals_it_keeps(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The mechanism, for any caller that hands it a set whose rows have attribution.

        No route does now. `scripts/verify_competitor_override.py` does, and a future caller
        could, so the guarantee stays tested where it lives.
        """
        from sqlalchemy.orm import selectinload

        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "strike.com"],
            cocit_brands=[("Keep", "keep.com"), ("Strike", "strike.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        scan_id = await _scan_that_took_the_list(session, cid)
        await self._attribute(session, scan_id)
        cset = (
            await session.execute(
                select(CompetitorSet)
                .where(CompetitorSet.scan_id == scan_id)
                .options(selectinload(CompetitorSet.competitors))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

        await detection.apply_override(session, cset, [("Keep", "keep.com")])
        await session.commit()

        after = await self._attribution(session, scan_id)
        kept = {k: v for k, v in after.items() if "keep" in k.lower()}
        assert kept, after
        assert all(v == "Keep" for v in kept.values()), (
            f"an override nulled the attribution for a rival it kept: {kept}"
        )

    async def test_persist_detection_still_keeps_attribution_for_the_rivals_it_finds_again(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The same, for `persist_detection` handed a scan whose set has attribution."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["keep.com", "gone.com"],
            cocit_brands=[("Keep", "keep.com"), ("Gone", "gone.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        scan_id = await _scan_that_took_the_list(session, cid)
        await self._attribute(session, scan_id)

        stub_discovery(serp_domains=["keep.com"], cocit_brands=[("Keep", "keep.com")])
        owner = (await session.execute(select(Client).where(Client.id == cid))).scalar_one()
        scan = (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
        outcome = await detection.detect_for_client(owner)
        await detection.persist_detection(session, scan, outcome)
        await session.commit()

        after = await self._attribution(session, scan_id)
        kept = {k: v for k, v in after.items() if "keep" in k.lower()}
        assert kept, after
        assert all(v == "Keep" for v in kept.values()), (
            f"re-detection nulled the attribution for a rival it re-found: {kept}"
        )

    async def test_redetecting_the_same_rival_does_not_500(
        self, client: AsyncClient, stub_discovery, a_day_passes
    ) -> None:  # noqa: ANN001
        """A second, separate defect Epic 3.9 found while fixing Finding 4.

        `persist_detection` deleted every non-manual row and inserted the new
        candidates in the same flush. SQLAlchemy's unit of work orders INSERTs
        before DELETEs for a table, so deleting `Keep` and inserting `Keep`
        collided on uq_competitors_set_name and the endpoint returned 500.

        Re-running detection and finding the same rivals is the ordinary case,
        and it had been broken since Epic 3. No test caught it because every
        existing re-detection test used a DISJOINT result set — a.com then
        b.com/c.com, or keep.com then fresh.com — so no name was ever deleted
        and re-inserted in one flush.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["keep.com"], cocit_brands=[("Keep", "keep.com")])
        assert (
            await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        ).status_code == 201

        await a_day_passes()
        stub_discovery(serp_domains=["keep.com"], cocit_brands=[("Keep", "keep.com")])
        again = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        assert again.status_code == 201, f"re-detection failed: {again.text[:300]}"
        assert [c["name"] for c in again.json()["competitors"]] == ["Keep"]

    async def test_an_overridden_set_can_be_overridden_again(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """The same collision on the override path, which reuse also removes."""
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["keep.com"], cocit_brands=[("Keep", "keep.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        payload = {"competitors": [{"name": "Keep", "domain": "keep.com"}]}
        first = await client.put(f"{BASE}/clients/{cid}/competitors", json=await _versioned(
            client, cid, payload
        ))
        assert first.status_code == 200, first.text
        second = await client.put(f"{BASE}/clients/{cid}/competitors", json=await _versioned(
            client, cid, payload
        ))
        assert second.status_code == 200, f"re-submitting the same set failed: {second.text[:300]}"
        assert [c["name"] for c in second.json()["competitors"]] == ["Keep"]


class TestConfidenceSaysWhatItCovers:
    """Finding 3, closed in Epic 3.11.

    `detectionConfidence` measures agreement between two automated signals over
    the rows DETECTION ranked. Manual rows were corroborated by neither, so on a
    mixed set the figure alone is published for more rows than it describes.
    `confidenceCovers` states the scope so a reader is not left inferring it.
    """

    async def test_covers_every_row_when_nothing_was_overridden(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["a.com", "b.com"],
            cocit_brands=[("A", "a.com"), ("B", "b.com")],
        )
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()

        assert body["detectionConfidence"] is not None
        assert body["confidenceCovers"] == len(body["competitors"]), (
            "on a purely detected set the figure covers the whole list"
        )

    async def test_covers_only_the_detected_rows_on_a_mixed_set(
        self, client: AsyncClient, stub_discovery, a_day_passes
    ) -> None:  # noqa: ANN001
        """The case Finding 3 was actually about.

        An override alone clears the confidence, so it is re-detection that
        creates the misleading state: a fresh figure computed over detection's
        own candidates, published beside a list the operator has part-authored.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client,
                cid,
                {"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
            ),
        )

        await a_day_passes()
        stub_discovery(
            serp_domains=["b.com", "c.com"],
            cocit_brands=[("B", "b.com"), ("C", "c.com")],
        )
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()

        manual = [c for c in body["competitors"] if c["isManualOverride"]]
        assert manual, "the operator's row must have survived for this to test anything"
        assert body["detectionConfidence"] is not None, "re-detection writes a fresh figure"
        assert body["confidenceCovers"] == len(body["competitors"]) - len(manual)
        assert body["confidenceCovers"] < len(body["competitors"]), (
            "the whole point: the figure covers strictly fewer rows than are shown"
        )

    async def test_covers_nothing_once_the_set_is_entirely_hand_set(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        body = (await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Zendesk", "domain": "zendesk.com"}]}
            ),
        )).json()

        assert body["detectionConfidence"] is None
        assert body["confidenceCovers"] == 0

    async def test_a_struck_rival_stops_being_covered(
        self, client: AsyncClient, stub_discovery, a_day_passes
    ) -> None:  # noqa: ANN001
        """A tombstone is not a row the figure describes, and not a row at all.

        Struck rivals are held as suppressed manual rows so re-detection cannot
        reinstate them. Counting them would inflate the scope with rows the
        reader cannot see.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["a.com", "b.com"],
            cocit_brands=[("A", "a.com"), ("B", "b.com")],
        )
        detected = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()
        keep = detected["competitors"][0]

        # Strike everything but the first, then re-detect so a figure exists.
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": keep["name"], "domain": keep["domain"]}]}
            ),
        )
        await a_day_passes()
        stub_discovery(serp_domains=["c.com"], cocit_brands=[("C", "c.com")])
        body = (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).json()

        assert body["confidenceCovers"] == sum(
            1 for c in body["competitors"] if not c["isManualOverride"]
        )
        assert body["confidenceCovers"] <= len(body["competitors"]), (
            "the scope can never exceed the rows on the wire"
        )


# ---------------------------------------------------------------------------------------------
# A save names the list it read (launch checklist 3.113)
# ---------------------------------------------------------------------------------------------

# What `PUT /clients/{clientId}/competitors` says when the list is not the one the writer read.
# Whole, so a change to what a caller reads has to change this test.
LIST_NOT_READ = "Reload the list to see where it stands, then save again."
LIST_CHANGED = "The list changed since you read it."
LIST_CHANGED_TYPE = "/problems/competitor-list-changed"
A_VERSION = re.compile(r"^[0-9a-f]{32}$")


def _url(cid: str) -> str:
    return f"{BASE}/clients/{cid}/competitors"


def _rows(*pairs: tuple[str, str | None]) -> list[dict[str, str | None]]:
    return [{"name": name, "domain": domain} for name, domain in pairs]


async def _stored(session: AsyncSession) -> list[tuple[object, ...]]:
    """Every column of every competitor set and every row, tombstones included: any write shows."""
    stored: list[tuple[object, ...]] = []
    for table in (CompetitorSet.__table__, Competitor.__table__):
        result = await session.execute(select(*table.c).order_by(table.c.id))
        stored.extend((table.name, *row) for row in result)
    return stored


async def _a_client_with_a_list(client: AsyncClient, stub_discovery) -> tuple[str, dict]:  # noqa: ANN001
    """A signed-in client whose list is the two rivals detection found, and that list as read."""
    await _sign_up(client)
    cid = await _make_client(client)
    stub_discovery(
        serp_domains=["a.com", "b.com"], cocit_brands=[("A", "a.com"), ("B", "b.com")]
    )
    assert (await client.post(f"{_url(cid)}/detect")).status_code == 201
    read = await client.get(_url(cid))
    assert read.status_code == 200, read.text
    return cid, read.json()


class TestTheVersionTravelsWithTheList:
    async def test_detect_get_and_put_each_carry_it(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        detected = (await client.post(f"{_url(cid)}/detect")).json()
        read = (await client.get(_url(cid))).json()

        assert A_VERSION.match(detected["version"])
        assert read["version"] == detected["version"], "one list, one version"

        saved = await client.put(
            _url(cid),
            json={"competitors": _rows(("Zendesk", "zendesk.com")), "version": read["version"]},
        )
        assert saved.status_code == 200, saved.text
        assert A_VERSION.match(saved.json()["version"])
        assert saved.json()["version"] != read["version"], "the rivals changed"
        assert (await client.get(_url(cid))).json()["version"] == saved.json()["version"]

    async def test_saving_the_same_rivals_again_is_the_same_version(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        """The version is what the list holds, not a count of saves."""
        cid, read = await _a_client_with_a_list(client, stub_discovery)
        body = {"competitors": _rows(("Zendesk", "zendesk.com"), ("Front", "front.com"))}
        first = await client.put(_url(cid), json={**body, "version": read["version"]})
        second = await client.put(_url(cid), json={**body, "version": first.json()["version"]})

        assert (first.status_code, second.status_code) == (200, 200)
        assert second.json()["version"] == first.json()["version"]
        assert second.json()["id"] == first.json()["id"]

    async def test_the_response_to_the_stale_save_carries_the_list_as_get_says_it(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        cid, read = await _a_client_with_a_list(client, stub_discovery)
        refused = await client.put(
            _url(cid), json={"competitors": _rows(("Zendesk", None)), "version": "0" * 32}
        )
        assert refused.status_code == 409
        # The same list, the same JSON, version included: the web can load it as it stands.
        assert refused.json()["current"] == read


class TestAStaleSaveIsRefused:
    """The save names the version it read; under the lock, before anything is written, it must
    still be the list's version (launch checklist 3.113)."""

    async def test_naming_the_current_version_saves(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        cid, read = await _a_client_with_a_list(client, stub_discovery)
        resp = await client.put(
            _url(cid),
            json={"competitors": _rows(("Zendesk", "zendesk.com")), "version": read["version"]},
        )
        assert resp.status_code == 200, resp.text
        assert [c["name"] for c in resp.json()["competitors"]] == ["Zendesk"]

    async def test_an_old_version_is_a_409_with_the_list_as_it_is_and_writes_nothing(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        cid, read = await _a_client_with_a_list(client, stub_discovery)
        # Somebody else saves, naming what they read: the list moves on from `read`.
        moved = await client.put(
            _url(cid),
            json={"competitors": _rows(("Zendesk", "zendesk.com")), "version": read["version"]},
        )
        assert moved.status_code == 200
        before = await _stored(session)

        resp = await client.put(
            _url(cid),
            json={"competitors": _rows(("Front", "front.com")), "version": read["version"]},
        )

        assert resp.status_code == 409, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == LIST_CHANGED_TYPE
        assert body["status"] == 409
        assert body["detail"] == LIST_CHANGED
        assert body["current"] == moved.json(), "the list as it stands, version included"
        assert body["current"]["version"] == moved.json()["version"] != read["version"]
        assert await _stored(session) == before, "a refused save writes nothing"
        assert (await client.get(_url(cid))).json() == moved.json()

    async def test_no_version_is_a_409_with_the_reload_sentence_and_writes_nothing(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """A tab opened before versions existed names none: a list that exists is not that."""
        cid, read = await _a_client_with_a_list(client, stub_discovery)
        before = await _stored(session)

        resp = await client.put(_url(cid), json={"competitors": _rows(("Zendesk", None))})

        assert resp.status_code == 409, resp.text
        assert resp.json()["type"] == LIST_CHANGED_TYPE
        assert resp.json()["detail"] == LIST_NOT_READ
        assert resp.json()["current"] == read
        assert await _stored(session) == before

    async def test_an_explicit_null_is_no_version(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        cid, _read = await _a_client_with_a_list(client, stub_discovery)
        resp = await client.put(
            _url(cid), json={"competitors": _rows(("Zendesk", None)), "version": None}
        )
        assert resp.status_code == 409 and resp.json()["detail"] == LIST_NOT_READ

    async def test_checklist_3_113_a_rival_added_after_the_editor_opened_is_not_struck(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The editor reads the list (v1), "Add as a rival" saves (v2), then the editor saves.

        Before this, the editor's save replaced the list it had never seen: the new rival was
        struck, later scans skipped it, and detection could not bring it back.
        """
        cid, opened = await _a_client_with_a_list(client, stub_discovery)
        drafts = _rows(*((c["name"], c["domain"]) for c in opened["competitors"]))

        # The rival suggestions card reads the list and adds one, naming what it read.
        card_read = (await client.get(_url(cid))).json()
        added = await client.put(
            _url(cid),
            json={
                "competitors": [*drafts, *_rows(("Deakin", "deakin.edu.au"))],
                "version": card_read["version"],
            },
        )
        assert added.status_code == 200, added.text
        before = await _stored(session)

        # The editor, still showing v1, removes B and saves.
        resp = await client.put(
            _url(cid), json={"competitors": drafts[:1], "version": opened["version"]}
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["type"] == LIST_CHANGED_TYPE
        assert [c["name"] for c in resp.json()["current"]["competitors"]] == [
            *(d["name"] for d in drafts), "Deakin",
        ]
        assert await _stored(session) == before
        deakin = (
            await session.execute(select(Competitor).where(Competitor.name == "Deakin"))
        ).scalar_one()
        assert deakin.is_suppressed is False and deakin.is_manual_override is True
        assert "Deakin" in [c["name"] for c in (await client.get(_url(cid))).json()["competitors"]]

    async def test_a_detection_run_after_the_read_is_a_change_too(
        self, client: AsyncClient, stub_discovery, a_day_passes
    ) -> None:  # noqa: ANN001
        cid, opened = await _a_client_with_a_list(client, stub_discovery)
        await a_day_passes()
        stub_discovery(
            serp_domains=["c.com", "d.com"], cocit_brands=[("C", "c.com"), ("D", "d.com")]
        )
        redetected = await client.post(f"{_url(cid)}/detect")
        assert redetected.status_code == 201
        assert redetected.json()["version"] != opened["version"]

        resp = await client.put(
            _url(cid),
            json={"competitors": _rows(("A", "a.com")), "version": opened["version"]},
        )

        assert resp.status_code == 409 and resp.json()["detail"] == LIST_CHANGED
        assert resp.json()["current"] == (await client.get(_url(cid))).json()

    async def test_a_detection_run_that_found_the_same_rivals_is_not_a_change(
        self, client: AsyncClient, stub_discovery, a_day_passes
    ) -> None:  # noqa: ANN001
        """The version is what the editor shows, so a re-run that changes none of it conflicts
        with nothing."""
        cid, opened = await _a_client_with_a_list(client, stub_discovery)
        await a_day_passes()
        again = await client.post(f"{_url(cid)}/detect")
        assert again.status_code == 201
        assert again.json()["version"] == opened["version"]

        resp = await client.put(
            _url(cid),
            json={"competitors": _rows(("Zendesk", "zendesk.com")), "version": opened["version"]},
        )
        assert resp.status_code == 200, resp.text

    async def test_a_client_with_no_list_matches_a_save_that_names_none(
        self, client: AsyncClient, session  # noqa: ANN001
    ) -> None:
        """A writer that read no list names none, and that is what the client has: a first save."""
        await _sign_up(client)
        cid = await _make_client(client)
        owner = (await session.execute(select(Client).where(Client.id == cid))).scalar_one()
        session.add(
            Scan(
                id=ids.new_id(ids.SCAN), client_id=cid, agency_id=owner.agency_id,
                status=ScanStatus.SUCCEEDED,
            )
        )
        await session.commit()
        assert (await client.get(_url(cid))).status_code == 404

        first = await client.put(_url(cid), json={"competitors": _rows(("Zendesk", None))})
        assert first.status_code == 200, first.text

        # Now there is a list, so a second save that still names none has read an older state.
        second = await client.put(
            _url(cid), json={"competitors": _rows(("Zendesk", None), ("Front", None))}
        )
        assert second.status_code == 409 and second.json()["detail"] == LIST_NOT_READ
        assert second.json()["current"] == first.json()

        named = await client.put(
            _url(cid),
            json={
                "competitors": _rows(("Zendesk", None), ("Front", None)),
                "version": first.json()["version"],
            },
        )
        assert named.status_code == 200, named.text

    async def test_a_version_longer_than_a_hash_is_a_validation_error(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        cid, _read = await _a_client_with_a_list(client, stub_discovery)
        body = {"competitors": _rows(("Zendesk", None))}

        longest = await client.put(_url(cid), json={**body, "version": "a" * 64})
        assert (longest.status_code, longest.json()["detail"]) == (409, LIST_CHANGED)

        too_long = await client.put(_url(cid), json={**body, "version": "a" * 65})
        assert too_long.status_code == 422
        assert too_long.json()["errors"][0]["field"] == "version"


class TestTheOlderRefusalsComeFirst:
    """A save the route already refuses is refused in the same words whatever it names: the
    version is checked last, after them and before anything is written."""

    async def test_a_running_scan_is_still_the_running_scan_409(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        cid, _read = await _a_client_with_a_list(client, stub_discovery)
        scan = (
            await session.execute(
                select(Scan).where(Scan.client_id == cid).order_by(Scan.id.desc()).limit(1)
            )
        ).scalar_one()
        scan.status = ScanStatus.RUNNING
        await session.commit()

        resp = await client.put(
            _url(cid), json={"competitors": _rows(("Zendesk", None)), "version": "0" * 32}
        )

        assert resp.status_code == 409
        assert resp.json()["type"] == "/problems/conflict"
        assert resp.json()["detail"] == (
            "A scan of this client is running. Name the rivals after it finishes."
        )
        assert "current" not in resp.json()

    async def test_a_bad_domain_is_still_the_422(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        cid, _read = await _a_client_with_a_list(client, stub_discovery)
        resp = await client.put(
            _url(cid),
            json={"competitors": _rows(("Self", "helpscout.com")), "version": "0" * 32},
        )
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["field"] == "competitors.0.domain"

    async def test_a_client_with_no_set_is_still_the_404(self, client: AsyncClient) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        resp = await client.put(
            _url(cid), json={"competitors": _rows(("Zendesk", None)), "version": "0" * 32}
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == NO_SET


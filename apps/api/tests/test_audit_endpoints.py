"""Technical audit endpoints, and the scoring integration.

The crawl is stubbed so these run without network. The live audit and the
before/after scoring proof are scripts/verify_audit.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.services import audit_runner
from avp_api.services.technical_audit import AuditSignals

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "audit@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Audit Test Agency", "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def _client_and_scan(client: AsyncClient) -> tuple[str, str]:
    cid = (await client.post(
        f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
    )).json()["id"]
    from avp_api import ids
    # A scan is created by the scans endpoint; use the competitor detect path's
    # get_or_create via a direct scan POST with an empty engine list is not
    # available, so create through the scans router with a stubbed runner is
    # heavier than needed. Instead exercise the audit against a scan made by
    # the competitors router, which only needs the client.
    assert ids.CLIENT
    return cid, ""


@pytest.fixture
def stub_audit(monkeypatch):  # noqa: ANN001, ANN201
    def _install(**overrides):  # noqa: ANN003
        base = {
            "url": "https://helpscout.com", "domain": "helpscout.com", "http_status": 200,
            "ok": True, "schema_types": ["Organization", "WebSite", "FAQPage"],
            "has_organization_schema": True, "has_faq_schema": True,
            "has_title": True, "has_meta_description": True, "canonical_present": True,
            "open_graph_tag_count": 5, "robots_txt_present": True,
            "robots_allows_crawl": True, "has_sitemap": True, "is_indexable": True,
            "h1_count": 1, "word_count": 900, "content_age_days": 30,
            "lcp_ms": 1200, "cls": Decimal("0.02"),
        }
        base.update(overrides)

        async def fake_audit(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return AuditSignals(**base)

        monkeypatch.setattr(audit_runner, "audit_site", fake_audit)

    return _install


async def _make_scan(client: AsyncClient, session) -> tuple[str, str]:  # noqa: ANN001
    from avp_api import ids
    from avp_api.models import Client, Scan
    from avp_api.models.scan import ScanStatus

    cid = (await client.post(
        f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
    )).json()["id"]
    row = await session.get(Client, cid)
    scan = Scan(id=ids.new_id(ids.SCAN), client_id=cid, agency_id=row.agency_id,
                status=ScanStatus.QUEUED)
    session.add(scan)
    await session.commit()
    return cid, scan.id


class TestRunAudit:
    async def test_returns_pass_fail_and_detail_for_each_check(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        """§7's literal acceptance criterion."""
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        stub_audit()

        resp = await client.post(f"{BASE}/scans/{sid}/audit")
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["id"].startswith("taud_")
        assert body["status"] == "ok"
        assert body["technicalFoundation"] is not None

        keys = {c["checkKey"] for c in body["checks"]}
        # Every §7 area is represented: CWV, schema/structured data, indexation.
        assert {"cwv_lcp", "cwv_cls", "cwv_inp"} <= keys
        assert {"schema_present", "schema_business_entity"} <= keys
        assert {"indexable", "robots_txt_present", "sitemap_present"} <= keys
        for check in body["checks"]:
            assert check["status"] in {"pass", "warn", "fail", "not_applicable", "error"}

    async def test_inp_is_reported_as_not_applicable_with_a_reason(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        stub_audit()
        body = (await client.post(f"{BASE}/scans/{sid}/audit")).json()
        inp = next(c for c in body["checks"] if c["checkKey"] == "cwv_inp")
        assert inp["status"] == "not_applicable"
        assert inp["detailCode"] == "FIELD_METRIC_REQUIRES_REAL_USER_DATA"
        assert body["inpMs"] is None

    async def test_unreachable_site_is_failed_with_a_null_score(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        """Not a site with a bad foundation — a site we could not read."""
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        stub_audit(ok=False, error_code="FETCH_FAILED", http_status=None)
        body = (await client.post(f"{BASE}/scans/{sid}/audit")).json()
        assert body["status"] == "failed"
        assert body["technicalFoundation"] is None
        assert body["errorCode"] == "FETCH_FAILED"

    async def test_missing_date_signal_is_partial_not_ok(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        stub_audit(content_age_days=None)
        body = (await client.post(f"{BASE}/scans/{sid}/audit")).json()
        assert body["status"] == "partial"
        assert body["excludedComponents"] == {
            "content_freshness": "NO_DATE_SIGNAL_AVAILABLE"
        }
        assert body["technicalFoundation"] is not None, "scored on what was measurable"

    async def test_rerunning_replaces_rather_than_duplicating_checks(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        stub_audit()
        first = (await client.post(f"{BASE}/scans/{sid}/audit")).json()
        second = (await client.post(f"{BASE}/scans/{sid}/audit")).json()
        assert first["id"] == second["id"]
        assert len(first["checks"]) == len(second["checks"])

    async def test_no_page_content_appears_in_the_response(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        stub_audit()
        resp = await client.post(f"{BASE}/scans/{sid}/audit")
        for forbidden in ("metaDescription", "ogTitle", "html", "bodyText", "snippet"):
            assert forbidden not in resp.text

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        from avp_api import ids

        resp = await client.post(f"{BASE}/scans/{ids.new_id(ids.SCAN)}/audit")
        assert resp.status_code == 401


class TestReadAudit:
    async def test_get_before_auditing_is_404(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        resp = await client.get(f"{BASE}/scans/{sid}/audit")
        assert resp.status_code == 404
        assert "not been audited" in resp.json()["detail"]

    async def test_checks_come_back_in_display_order(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        """Crawlability first — it gates everything else."""
        await _sign_up(client)
        _, sid = await _make_scan(client, session)
        stub_audit()
        await client.post(f"{BASE}/scans/{sid}/audit")
        body = (await client.get(f"{BASE}/scans/{sid}/audit")).json()
        keys = [c["checkKey"] for c in body["checks"]]
        assert keys[0] == "site_reachable"
        assert keys.index("indexable") < keys.index("schema_present")
        assert keys.index("schema_present") < keys.index("cwv_lcp")

    async def test_another_agency_cannot_audit_or_read(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        from httpx import ASGITransport
        from httpx import AsyncClient as Second

        await _sign_up(client, "one@auditiso.example")
        _, sid = await _make_scan(client, session)
        stub_audit()
        await client.post(f"{BASE}/scans/{sid}/audit")
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with Second(transport=transport, base_url="http://testserver") as other:
            await _sign_up(other, "two@auditiso.example")
            assert (await other.get(f"{BASE}/scans/{sid}/audit")).status_code == 404
            assert (await other.post(f"{BASE}/scans/{sid}/audit")).status_code == 404


async def _scoreable_scan(client: AsyncClient, session) -> str:  # noqa: ANN001
    """A scan with real EngineResult rows, so scoring produces a composite.

    A bare scan scores `insufficient_data` with every dimension excluded as
    NO_ANSWERED_RESULTS, which would mask the Technical Foundation exclusion
    these tests are about.
    """
    from avp_api import ids
    from avp_api.models import EngineResult, Prompt, PromptSet
    from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
    from avp_api.models.prompt import PromptIntent

    _, sid = await _make_scan(client, session)

    pset = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=sid, generated_by="test",
                     generation_params={}, prompts=[])
    session.add(pset)
    await session.flush()
    prompt = Prompt(id=ids.new_id(ids.PROMPT), prompt_set_id=pset.id,
                    text="q", intent=PromptIntent.AWARENESS, position=1)
    pset.prompts.append(prompt)
    await session.flush()

    for engine in (Engine.CLAUDE, Engine.CLAUDE_SEARCH):
        session.add(EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT), scan_id=sid, prompt_id=prompt.id,
            engine=engine, engine_version="test", status=EngineResultStatus.OK,
            mentioned=True, position=1, sentiment=Sentiment.POSITIVE,
            brands_mentioned=1, brand_mentions=[], citations=[],
        ))
    await session.commit()
    return sid


class TestScoringIntegration:
    """The point of the epic: closing the NOT_YET_MEASURED exclusion."""

    async def test_before_audit_technical_foundation_is_excluded(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scoreable_scan(client, session)
        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert body["status"] == "scored"
        assert body["technicalFoundation"] is None
        assert body["excludedDimensions"]["technical_foundation"] == "NOT_YET_MEASURED"

    async def test_after_audit_the_dimension_is_included(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scoreable_scan(client, session)
        before = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        stub_audit()
        await client.post(f"{BASE}/scans/{sid}/audit")

        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert body["technicalFoundation"] is not None
        # The composite must actually move — a dimension that changes nothing
        # would mean the wire-up is cosmetic.
        assert body["composite"] != before["composite"]
        assert "technical_foundation" not in body["excludedDimensions"]
        assert "TECHNICAL_FOUNDATION_NOT_MEASURED" not in body["degradationFlags"]
        # All five §6 dimensions now carry weight where they have data.
        assert "technical_foundation" in body["weights"]

    async def test_a_failed_audit_leaves_the_dimension_excluded(
        self, client: AsyncClient, session, stub_audit
    ) -> None:  # noqa: ANN001
        """A site we could not read must not be scored as if it had been."""
        await _sign_up(client)
        sid = await _scoreable_scan(client, session)
        stub_audit(ok=False, error_code="FETCH_FAILED")
        await client.post(f"{BASE}/scans/{sid}/audit")

        body = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        assert body["technicalFoundation"] is None
        assert body["excludedDimensions"]["technical_foundation"] == "NOT_YET_MEASURED"

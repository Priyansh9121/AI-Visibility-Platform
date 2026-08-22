"""Intake endpoints and URL handling.

Classification is stubbed here so these tests exercise persistence, validation,
scoping and the API contract without a network call or an LLM spend. The live
end-to-end verification against real sites is separate — see docs/build-log.md.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.errors import Conflict
from avp_api.models import ClassificationStatus
from avp_api.services import intake as intake_service
from avp_api.services.classify import ClassificationOutcome
from avp_api.services.crawl import CrawlResult, CrawlSignals
from avp_api.services.intake import InvalidUrl, apply_outcome, parse_domain

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "intake@test.example") -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Intake Test Agency",
            "fullName": "Operator",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["agency"]["id"]


@pytest.fixture
def stub_classification(monkeypatch):  # noqa: ANN001, ANN201
    """Replace crawl+classify with a deterministic stub."""

    def _install(outcome: ClassificationOutcome, *, word_count: int = 250):
        async def fake_crawl(url: str, **kwargs):  # noqa: ANN001, ANN003, ARG001
            from avp_api.services.crawl import registrable_domain

            return CrawlResult(
                signals=CrawlSignals(
                    final_url=url,
                    registrable_domain=registrable_domain(url),
                    word_count=word_count,
                    pages_fetched=2,
                    urls_fetched=[url, f"{url}/about"],
                    schema_types=["LocalBusiness"],
                    h1_count=1,
                    has_title=True,
                    has_meta_description=True,
                ),
                text_extract="stub page text",
            )

        async def fake_classify(crawl, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return outcome

        monkeypatch.setattr(intake_service, "crawl_site", fake_crawl)
        monkeypatch.setattr(intake_service, "classify", fake_classify)

    return _install


CLASSIFIED = ClassificationOutcome(
    status="classified",
    industry="dental practice",
    niche="cosmetic and implant dentistry",
    brand_name="Northaven Dental",
    confidence="high",
    confidence_score=Decimal("0.920"),
)


class TestUrlParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("example.com", "example.com"),
            ("https://example.com", "example.com"),
            ("http://www.example.com/about?x=1", "example.com"),
            ("  https://SHOP.Example.CO.UK/path  ", "example.co.uk"),
            # PSL private section — must NOT collapse to github.io, or every
            # GitHub Pages prospect would collide on (agency_id, domain).
            ("https://practice.github.io", "practice.github.io"),
            ("https://myshop.myshopify.com", "myshop.myshopify.com"),
        ],
    )
    def test_registrable_domain_extraction(self, raw: str, expected: str) -> None:
        """Public Suffix List, not dot-splitting.

        Naive splitting gets example.co.uk and practice.github.io wrong in
        opposite directions.
        """
        _, domain = parse_domain(raw)
        assert domain == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "", "   ", "not a url", "localhost", "http://127.0.0.1:8000", "ftp:",
            # Reserved TLD (RFC 2606) with no public suffix — not a real site.
            "a.example", "site.invalid", "thing.localhost",
        ],
    )
    def test_invalid_urls_are_rejected(self, raw: str) -> None:
        with pytest.raises((InvalidUrl, ValueError)):
            parse_domain(raw)


class TestApplyOutcome:
    def test_classified_outcome_populates_industry(self) -> None:
        from avp_api import ids
        from avp_api.models import Client

        c = Client(id=ids.new_id(ids.CLIENT), agency_id="a", name="x-test.com", domain="x-test.com")
        apply_outcome(c, CLASSIFIED)
        assert c.classification_status is ClassificationStatus.CLASSIFIED
        assert c.industry == "dental practice"
        assert c.brand_name == "Northaven Dental"
        # The domain placeholder is replaced by the real business name.
        assert c.name == "Northaven Dental"

    def test_ambiguous_outcome_clears_industry(self) -> None:
        from avp_api import ids
        from avp_api.models import Client

        c = Client(
            id=ids.new_id(ids.CLIENT),
            agency_id="a",
            name="x-test.com",
            domain="x-test.com",
            industry="stale value",
        )
        apply_outcome(
            c,
            ClassificationOutcome(
                status="ambiguous",
                brand_name="Some Business",
                confidence="low",
                confidence_score=Decimal("0.410"),
                reason_code="LOW_CONFIDENCE",
            ),
        )
        assert c.industry is None
        assert c.classification_reason_code == "LOW_CONFIDENCE"
        # Still useful, still kept.
        assert c.brand_name == "Some Business"
        assert c.industry_confidence_score == Decimal("0.410")

    def test_operator_supplied_name_is_never_overwritten(self) -> None:
        from avp_api import ids
        from avp_api.models import Client

        c = Client(
            id=ids.new_id(ids.CLIENT),
            agency_id="a",
            name="My Own Label",
            domain="x-test.com",
        )
        apply_outcome(c, CLASSIFIED)
        assert c.name == "My Own Label"


class TestIntakeEndpoint:
    async def test_submitting_a_url_returns_a_classified_industry(
        self, client: AsyncClient, stub_classification
    ) -> None:  # noqa: ANN001
        """The §7 acceptance shape (live verification is separate)."""
        await _sign_up(client)
        stub_classification(CLASSIFIED)

        resp = await client.post(f"{BASE}/clients", json={"url": "northaven-dental.com"})
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["id"].startswith("clnt_")
        assert body["domain"] == "northaven-dental.com"
        assert body["classificationStatus"] == "classified"
        assert body["industry"] == "dental practice"
        assert body["industryNiche"] == "cosmetic and implant dentistry"
        assert body["brandName"] == "Northaven Dental"
        assert body["industryConfidence"] == "high"
        # NUMERIC crosses the wire as a string, like every decimal in this API.
        assert body["industryConfidenceScore"] == "0.920"
        assert body["classifierModel"] == "claude-opus-5"
        assert body["classifiedAt"] is not None

    async def test_crawl_facts_are_returned_but_not_page_text(
        self, client: AsyncClient, stub_classification
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_classification(CLASSIFIED)
        resp = await client.post(f"{BASE}/clients", json={"url": "x-test.com"})
        crawl = resp.json()["crawl"]
        assert crawl["pagesFetched"] == 2
        assert crawl["schemaTypes"] == ["LocalBusiness"]
        assert crawl["wordCount"] == 250
        # The transient page text must not appear anywhere in the response.
        assert "stub page text" not in resp.text
        assert "textExtract" not in resp.text

    async def test_ambiguous_classification_returns_null_industry(
        self, client: AsyncClient, stub_classification
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_classification(
            ClassificationOutcome(
                status="ambiguous",
                brand_name="Vague Holdings",
                confidence="low",
                confidence_score=Decimal("0.320"),
                reason_code="LOW_CONFIDENCE",
            )
        )
        body = (await client.post(f"{BASE}/clients", json={"url": "vague-test.com"})).json()
        assert body["classificationStatus"] == "ambiguous"
        assert body["industry"] is None
        assert body["classificationReasonCode"] == "LOW_CONFIDENCE"

    async def test_unreachable_site_is_unclassifiable_not_an_error(
        self, client: AsyncClient, stub_classification
    ) -> None:  # noqa: ANN001
        """A site that will not load is a normal outcome, not a 500."""
        await _sign_up(client)
        stub_classification(
            ClassificationOutcome(status="unclassifiable", reason_code="FETCH_FAILED")
        )
        resp = await client.post(f"{BASE}/clients", json={"url": "gone-test.com"})
        assert resp.status_code == 201
        assert resp.json()["classificationStatus"] == "unclassifiable"
        assert resp.json()["classificationReasonCode"] == "FETCH_FAILED"

    async def test_classify_false_skips_the_llm(self, client: AsyncClient) -> None:
        """No stub installed — proves no crawl or model call happens."""
        await _sign_up(client)
        resp = await client.post(
            f"{BASE}/clients", json={"url": "skip-test.com", "classify": False}
        )
        assert resp.status_code == 201
        assert resp.json()["classificationStatus"] == "pending"
        assert resp.json()["crawl"] is None

    async def test_invalid_url_returns_problem_json(self, client: AsyncClient) -> None:
        await _sign_up(client)
        resp = await client.post(f"{BASE}/clients", json={"url": "localhost"})
        assert resp.status_code == 409
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/invalid-url"

    async def test_duplicate_domain_for_same_agency_conflicts(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        await client.post(f"{BASE}/clients", json={"url": "dup-test.com", "classify": False})
        resp = await client.post(
            f"{BASE}/clients", json={"url": "https://www.dup-test.com/x", "classify": False}
        )
        assert resp.status_code == 409
        assert "clientId" in resp.json()

    async def test_intake_requires_authentication(self, client: AsyncClient) -> None:
        resp = await client.post(f"{BASE}/clients", json={"url": "x-test.com"})
        assert resp.status_code == 401


class TestClientReads:
    async def test_get_and_list(self, client: AsyncClient) -> None:
        await _sign_up(client)
        created = (
            await client.post(f"{BASE}/clients", json={"url": "a-test.com", "classify": False})
        ).json()

        fetched = await client.get(f"{BASE}/clients/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == created["id"]

        listed = await client.get(f"{BASE}/clients")
        assert listed.status_code == 200
        assert [c["id"] for c in listed.json()["data"]] == [created["id"]]
        assert listed.json()["nextCursor"] is None

    async def test_list_paginates_newest_first(self, client: AsyncClient) -> None:
        await _sign_up(client)
        created = []
        for i in range(5):
            r = await client.post(
                f"{BASE}/clients", json={"url": f"s{i}-test.com", "classify": False}
            )
            created.append(r.json()["id"])

        page1 = (await client.get(f"{BASE}/clients?limit=2")).json()
        assert [c["id"] for c in page1["data"]] == list(reversed(created))[:2]
        assert page1["nextCursor"] is not None

        page2 = (await client.get(f"{BASE}/clients?limit=2&cursor={page1['nextCursor']}")).json()
        assert [c["id"] for c in page2["data"]] == list(reversed(created))[2:4]

    async def test_unknown_id_is_404(self, client: AsyncClient) -> None:
        await _sign_up(client)
        from avp_api import ids

        resp = await client.get(f"{BASE}/clients/{ids.new_id(ids.CLIENT)}")
        assert resp.status_code == 404

    async def test_wrong_id_type_is_404_not_500(self, client: AsyncClient) -> None:
        """A scan id where a client id belongs must not reach the database."""
        await _sign_up(client)
        from avp_api import ids

        resp = await client.get(f"{BASE}/clients/{ids.new_id(ids.SCAN)}")
        assert resp.status_code == 404

    async def test_another_agencys_client_is_404_not_403(
        self, client: AsyncClient
    ) -> None:
        """Confirming an id exists is itself a cross-tenant leak."""
        from httpx import ASGITransport
        from httpx import AsyncClient as SecondAgency

        await _sign_up(client, "one@isolation.example")
        mine = (
            await client.post(f"{BASE}/clients", json={"url": "mine-test.com", "classify": False})
        ).json()["id"]
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with SecondAgency(transport=transport, base_url="http://testserver") as other:
            await _sign_up(other, "two@isolation.example")
            assert (await other.get(f"{BASE}/clients/{mine}")).status_code == 404
            assert (await other.get(f"{BASE}/clients")).json()["data"] == []


class TestServiceLevel:
    async def test_duplicate_raises_conflict_with_existing_id(self, session) -> None:  # noqa: ANN001
        from avp_api import ids
        from avp_api.models import Agency

        agency = Agency(id=ids.new_id(ids.AGENCY), name="A", slug=f"a{ids.new_id(ids.AGENCY)[-6:]}")
        session.add(agency)
        await session.flush()

        first = await intake_service.create_client_from_url(
            session, agency_id=agency.id, raw_url="dup-test.com"
        )
        await session.commit()

        with pytest.raises(Conflict) as excinfo:
            await intake_service.create_client_from_url(
                session, agency_id=agency.id, raw_url="www.dup-test.com"
            )
        assert excinfo.value.extra["clientId"] == first.id

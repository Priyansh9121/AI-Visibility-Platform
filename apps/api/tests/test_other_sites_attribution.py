"""A confirmed other site is the brand's own: attribution and scoring, v8 (M1, task 1.2).

RMIT University's own rmit.edu.vn (Tier A, `edu`) was a third-party source to the extractor, which
counted a citation as the client's own only on exact equality, so it raised RMIT's own Citation
Strength. Task 1.1 built the lists; this task makes a CONFIRMED site the brand's own everywhere,
from the next scan on. Proposed sites change nothing.

The founder's pair opens the file: an answer naming RMIT that cites rmit.edu.vn gives RMIT no
authority credit once confirmed, and exactly today's credit while only proposed.

Two binding rules from the security review of 1.1 are pinned here:

* the client's list is read ONCE, when the scan goes RUNNING, and nothing downstream reads it again
  (the report, the fix generator, scoring and an explicit re-score read what the scan STORED);
* a brand's main domain takes precedence over any confirmed other site.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api.models import (
    Citation,
    CitationType,
    ClassificationStatus,
    Client,
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
    Engine,
    EngineResult,
    EngineResultStatus,
    Prompt,
    PromptIntent,
    PromptSet,
    Scan,
    ScanStatus,
    Sentiment,
)
from avp_api.services import (
    domain_authority,
    extraction,
    fix_generator,
    fix_runner,
    scan_runner,
    scoring,
)
from avp_api.services.domain_authority import CURATED_ONLY
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.intake import dns_form
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.report import build_report
from avp_api.services.scoring import AUTHORITY_VALUES, CompetitorFacts, ResultFacts

BASE = "/api/v1"
RMIT = ("RMIT University", "rmit.edu.au")
MONASH = ("Monash University", "monash.edu")
TIER_A = AUTHORITY_VALUES[domain_authority.Tier.CURATED]


def _answer(text: str, *cited: str) -> EngineAnswer:
    return EngineAnswer(
        engine=Engine.CLAUDE_SEARCH, engine_version="stub", prompt_text="p", text=text,
        citations=[
            CitedSource(url=f"https://{d}/x", domain=d, position=n)
            for n, d in enumerate(cited, start=1)
        ],
    )


def _report_facts(report: Any) -> dict[str, Any]:
    """The report as plain data, without the timestamps that differ between two builds."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if not k.endswith("_at")}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(report.model_dump(mode="json"))


def _result_facts(facts: extraction.ExtractedFacts, result_id: str = "r1") -> ResultFacts:
    return ResultFacts(
        result_id=result_id, engine="claude_search", status=facts.status,
        mentioned=facts.mentioned, sentiment=None,
        brands=tuple((h.name, h.is_subject) for h in facts.brand_hits),
        citations=tuple((c.domain, c.cites_subject) for c in facts.citations),
    )


def _subject_credit(
    text: str,
    *cited: str,
    other_sites: frozenset[str] = frozenset(),
    rival_sites: frozenset[str] = frozenset(),
    competitors: list[tuple[str, str | None]] | None = None,
) -> tuple[Decimal, extraction.ExtractedFacts]:
    """Through `extract_facts` (so `cites_subject` is the real classifier's) into scoring."""
    facts = extraction.extract_facts(
        _answer(text, *cited), subject_name=RMIT[0], subject_domain=RMIT[1],
        competitors=competitors or [],
        subject_other_sites=other_sites, competitor_other_sites=rival_sites,
    )
    tiers = CURATED_ONLY.tiers_for(cited)
    subject, _ = scoring.answer_authority([_result_facts(facts)], [], tiers)
    [credit] = subject
    return credit, facts


class TestTheFoundersPair:
    """RMIT University (rmit.edu.au) named in an answer that cites ONLY rmit.edu.vn, at Tier A."""

    def test_confirmed_gives_no_credit_and_the_citation_cites_the_subject(self) -> None:
        credit, facts = _subject_credit(
            "RMIT University is a good choice.", "rmit.edu.vn",
            other_sites=frozenset({"rmit.edu.vn"}),
        )
        assert facts.mentioned
        assert credit == Decimal("0")
        [citation] = facts.citations
        assert (citation.domain, citation.cites_subject) == ("rmit.edu.vn", True)
        assert citation.source_type is CitationType.OWNED

    def test_only_proposed_is_exactly_todays_credit(self) -> None:
        # Today's code, run before this task: cites_subject False, credit Decimal("100").
        credit, facts = _subject_credit("RMIT University is a good choice.", "rmit.edu.vn")
        assert credit == TIER_A == Decimal("100")
        [citation] = facts.citations
        assert (citation.domain, citation.cites_subject) == ("rmit.edu.vn", False)


class TestTheSameWithASecondSource:
    TEXT = "RMIT University is a good choice."

    def test_a_confirmed_site_leaves_the_credit_and_a_proposed_one_does_not(self) -> None:
        confirmed, with_confirmed = _subject_credit(
            self.TEXT, "rmit.edu.vn", "mit.edu", other_sites=frozenset({"rmit.edu.vn"})
        )
        proposed, with_proposed = _subject_credit(self.TEXT, "rmit.edu.vn", "mit.edu")

        assert confirmed == TIER_A  # mit.edu only
        assert proposed == TIER_A + TIER_A  # rmit.edu.vn's tier value included, as today
        flags = lambda f: {c.domain: c.cites_subject for c in f.citations}  # noqa: E731
        assert flags(with_confirmed) == {"rmit.edu.vn": True, "mit.edu": False}
        assert flags(with_proposed) == {"rmit.edu.vn": False, "mit.edu": False}


class TestARivalsConfirmedSite:
    TEXT = "RMIT University and Monash University are both options."

    def test_the_citation_is_the_rivals_and_the_rivals_credit_leaves_it_out(self) -> None:
        sites = frozenset({"monash.edu.my"})
        facts = extraction.extract_facts(
            _answer(self.TEXT, "monash.edu.my", "mit.edu"), subject_name=RMIT[0],
            subject_domain=RMIT[1], competitors=[MONASH], competitor_other_sites=sites,
        )
        flags = {c.domain: (c.source_type, c.cites_subject) for c in facts.citations}
        assert flags["monash.edu.my"] == (CitationType.COMPETITOR, False)

        result = _result_facts(facts)
        tiers = CURATED_ONLY.tiers_for(["monash.edu.my", "mit.edu"])
        confirmed = CompetitorFacts("comp_1", MONASH[0], MONASH[1], other_sites=("monash.edu.my",))
        proposed = CompetitorFacts("comp_1", MONASH[0], MONASH[1])
        _, with_site = scoring.answer_authority([result], [confirmed], tiers)
        _, without = scoring.answer_authority([result], [proposed], tiers)

        assert with_site["comp_1"] == [TIER_A]  # mit.edu only
        assert without["comp_1"] == [TIER_A + TIER_A]  # as today

    def test_a_sibling_cited_in_its_unicode_spelling_still_matches(self) -> None:
        stored = dns_form("bücher.at")
        assert stored == "xn--bcher-kva.at"
        classified = extraction.classify_citation(
            "bücher.at", subject_domain="buecher.de", competitor_domains=set(),
            subject_other_sites=frozenset({stored}),
        )
        assert classified == (CitationType.OWNED, True)
        rival = CompetitorFacts("c", "Bücher", "buecher.de", other_sites=(stored,))
        result = ResultFacts(
            result_id="r", engine="e", status=EngineResultStatus.OK, mentioned=True,
            sentiment=None, brands=(("Bücher", False),), citations=(("bücher.at", False),),
        )
        _, rivals = scoring.answer_authority(
            [result], [rival], {"bücher.at": domain_authority.Tier.CURATED}
        )
        assert rivals["c"] == [Decimal("0")]


class TestAMainDomainTakesPrecedence:
    """Subject's domain, then the rivals' domains, then the subject's sites, then the rivals'."""

    def classify(self, domain: str, **kw: Any) -> tuple[CitationType, bool]:
        return extraction.classify_citation(
            domain, subject_domain="rmit.edu.au", competitor_domains={"monash.edu", "rmit.edu.vn"},
            **kw,
        )

    def test_a_rival_at_a_domain_confirmed_as_the_clients_site_stays_the_rivals(self) -> None:
        # The editor or detection can add a rival at a confirmed site after the confirmation.
        got = self.classify("rmit.edu.vn", subject_other_sites=frozenset({"rmit.edu.vn"}))
        assert got == (CitationType.COMPETITOR, False)

    def test_the_subject_main_domain_beats_a_rivals_other_site(self) -> None:
        got = self.classify(
            "rmit.edu.au", competitor_other_sites=frozenset({"rmit.edu.au"})
        )
        assert got == (CitationType.OWNED, True)

    def test_a_rivals_main_domain_beats_the_subjects_site_and_the_subjects_site_beats_the_rivals(
        self,
    ) -> None:
        both = {"subject_other_sites": frozenset({"x.edu.my"}),
                "competitor_other_sites": frozenset({"x.edu.my"})}
        assert self.classify("x.edu.my", **both) == (CitationType.OWNED, True)
        assert self.classify("monash.edu", **both) == (CitationType.COMPETITOR, False)

    def test_a_rivals_confirmed_site_is_a_competitor_citation(self) -> None:
        got = self.classify("monash.edu.my", competitor_other_sites=frozenset({"monash.edu.my"}))
        assert got == (CitationType.COMPETITOR, False)

    def test_an_unlisted_domain_is_what_it_was(self) -> None:
        assert self.classify("rmit.edu.my") == (CitationType.OTHER, False)

    def test_the_two_main_domain_comparisons_are_still_exact_strings(self) -> None:
        assert self.classify("RMIT.edu.au") == (CitationType.OTHER, False)


class TestTheCompetitorIdMapPutsMainDomainsFirst:
    async def test_a_rivals_other_site_never_overwrites_another_rivals_domain_or_name(
        self, session: AsyncSession
    ) -> None:
        client = Client(
            id=ids.new_id(ids.CLIENT), agency_id=await _agency(session), name=RMIT[0],
            domain=RMIT[1],
        )
        session.add(client)
        await session.flush()
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=client.agency_id,
            status=ScanStatus.RUNNING,
        )
        session.add(scan)
        await session.flush()
        cset = CompetitorSet(
            id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, status=DetectionStatus.OK,
            detection_confidence=Decimal("1"), serp_queries_run=6, candidates_considered=9,
            detected_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.add(cset)
        await session.flush()
        # The FIRST rival lists the SECOND's own domain and name as its other site.
        first = _rival(cset.id, "First", "first.com", 1, ["second.com", "xn--bcher-kva.at"])
        second = _rival(cset.id, "Second", "second.com", 2, [])
        session.add_all([first, second])
        await session.commit()

        mapping = await scan_runner._competitor_id_map(session, scan)  # noqa: SLF001

        assert mapping["second.com"] == second.id
        assert mapping["xn--bcher-kva.at"] == first.id
        assert mapping["First"] == first.id and mapping["Second"] == second.id


def _rival(set_id: str, name: str, domain: str, rank: int, sites: list[str]) -> Competitor:
    return Competitor(
        id=ids.new_id(ids.COMPETITOR), competitor_set_id=set_id, name=name, domain=domain,
        rank=rank, detection_source=DetectionSource.BOTH, signal_count=7, serp_mentions=4,
        co_citation_mentions=3, corroborated=True, other_sites=sites,
    )


async def _agency(session: AsyncSession) -> str:
    from avp_api.models import Agency

    agency = Agency(id=ids.new_id(ids.AGENCY), name="Attribution Agency", slug="attribution-agency")
    session.add(agency)
    await session.flush()
    return agency.id


class TestTheDigest:
    """A competitor with no other sites serialises exactly as before; one with a site does not."""

    PINNED_V7 = "e7023c5e7f9a3e35812613090b04d813809b0e5a5ea3c7e33e4e7bcb91c1db8c"

    def fixture(self, sites: tuple[str, ...] = ()) -> str:
        results = [
            ResultFacts(
                result_id="r1", engine="claude", status=EngineResultStatus.OK, mentioned=True,
                sentiment=Sentiment.POSITIVE, intent=PromptIntent.AWARENESS,
                brands=(("RMIT University", True), ("Monash University", False)),
                citations=(("monash.edu", False), ("rmit.edu.au", True), ("rmit.edu.vn", False)),
            )
        ]
        rivals = [CompetitorFacts("comp_1", "Monash University", "monash.edu", other_sites=sites)]
        return scoring.compute_inputs_digest(
            results, rivals, domain_tiers={"monash.edu": "curated"}
        )

    def test_no_other_sites_is_the_digest_v7_computed(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(scoring, "FORMULA_VERSION", "v7")
        assert self.fixture() == self.PINNED_V7

    def test_an_other_site_changes_it_and_the_order_does_not(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(scoring, "FORMULA_VERSION", "v7")
        one = self.fixture(("monash.edu.my",))
        assert one != self.PINNED_V7
        assert self.fixture(("a.com", "b.com")) == self.fixture(("b.com", "a.com"))
        assert self.fixture(("a.com",)) != self.fixture(("b.com",))

    def test_the_digest_moves_with_the_version(self) -> None:
        assert self.fixture() != self.PINNED_V7


class TestTheVersionsAreV8:
    def test_both_moved_together(self) -> None:
        assert scoring.FORMULA_VERSION == "v8"
        assert extraction.MATCHER_VERSION == "v8"


class TestTierBRefusesAConfirmedSite:
    """`load_platform_domains` refuses every confirmed site of a live client and of a competitor."""

    async def _two_industries_cite(
        self, client: AsyncClient, session: AsyncSession, domain: str
    ) -> str:
        await _sign_up(client)
        rmit = await _make_client(client, *RMIT)
        other = await _make_client(client, "Acme Retail", "acme-retail.com")
        for cid, industry in ((rmit, "education"), (other, "retail")):
            await session.execute(update(Client).where(Client.id == cid).values(
                industry=industry, classification_status=ClassificationStatus.CLASSIFIED
            ))
            sid = await _add_scan(session, cid)
            await _cite(session, sid, domain)
        await session.commit()
        return rmit

    async def test_it_is_a_platform_domain_until_it_is_a_confirmed_site(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        rmit = await self._two_industries_cite(client, session, "rmit.edu.vn")
        loaded = await domain_authority.load_platform_domains(
            session, min_industries=2, min_clients=2
        )
        assert "rmit.edu.vn" in loaded  # the control: the query can see it

        await session.execute(
            update(Client).where(Client.id == rmit).values(other_sites=["rmit.edu.vn"])
        )
        await session.commit()
        loaded = await domain_authority.load_platform_domains(
            session, min_industries=2, min_clients=2
        )
        assert "rmit.edu.vn" not in loaded

    async def test_a_rivals_confirmed_site_is_refused_too(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        rmit = await self._two_industries_cite(client, session, "monash.edu.my")
        sid = await _add_scan(session, rmit)
        cset = CompetitorSet(
            id=ids.new_id(ids.COMPETITOR_SET), scan_id=sid, status=DetectionStatus.OK,
            detection_confidence=Decimal("1"), serp_queries_run=6, candidates_considered=9,
            detected_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.add(cset)
        await session.flush()
        session.add(_rival(cset.id, *MONASH, 1, ["monash.edu.my"]))
        await session.commit()

        loaded = await domain_authority.load_platform_domains(
            session, min_industries=2, min_clients=2
        )
        assert "monash.edu.my" not in loaded

    async def test_a_deleted_clients_site_does_not_refuse_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        rmit = await self._two_industries_cite(client, session, "rmit.edu.vn")
        # The control for "live": two live clients are needed, so delete a third that lists it.
        ghost = await _make_client(client, "Ghost", "ghost-co.com")
        await session.execute(
            update(Client).where(Client.id == ghost).values(
                other_sites=["rmit.edu.vn"], deleted_at=datetime(2026, 9, 1, tzinfo=UTC)
            )
        )
        await session.commit()
        loaded = await domain_authority.load_platform_domains(
            session, min_industries=2, min_clients=2
        )
        assert rmit and "rmit.edu.vn" in loaded


class TestTheFixGeneratorAllowsTheClientsConfirmedSite:
    def facts(self, cited: set[str]) -> fix_generator.FixFacts:
        return fix_generator.FixFacts(domain="rmit.edu.au", cited_domains=frozenset(cited))

    def test_a_confirmed_site_the_scan_cited_is_allowed(self) -> None:
        facts = self.facts({"rmit.edu.au", "rmit.edu.vn", "g2.com"})
        text = "Link rmit.edu.vn from the pricing page."
        assert fix_generator.uncited_domain_in(text, facts) is None

    def test_a_confirmed_site_the_scan_did_not_cite_stays_out(self) -> None:
        # Launch checklist 3.95: generated fixes name only sites the scan cited. Nothing reads
        # `client.other_sites` here, so a later edit to the list cannot change an old scan's fixes.
        facts = self.facts({"rmit.edu.au", "g2.com"})
        assert (
            fix_generator.uncited_domain_in("Link rmit.edu.vn from the pricing page.", facts)
            == "rmit.edu.vn"
        )


# --- the real chain -----------------------------------------------------------------------------


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Attribution", "fullName": "Op", "email": "attr@test.example",
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient, name: str, domain: str) -> str:
    resp = await client.post(
        f"{BASE}/clients", json={"url": domain, "name": name, "classify": False}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


_scan_n = iter(range(1, 10_000))


async def _add_scan(session: AsyncSession, client_id: str) -> str:
    owner = (
        await session.execute(select(Client.agency_id).where(Client.id == client_id))
    ).scalar_one()
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=owner,
        status=ScanStatus.SUCCEEDED,
    )
    session.add(scan)
    await session.commit()
    return scan.id


async def _cite(session: AsyncSession, scan_id: str, domain: str) -> None:
    pset = ids.new_id(ids.PROMPT_SET)
    session.add(PromptSet(id=pset, scan_id=scan_id))
    await session.flush()
    prompt = Prompt(
        id=ids.new_id(ids.PROMPT), prompt_set_id=pset, text="q", intent=PromptIntent.AWARENESS,
        position=1,
    )
    session.add(prompt)
    await session.flush()
    result = EngineResult(
        id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan_id, prompt_id=prompt.id,
        engine=Engine.CLAUDE_SEARCH, status=EngineResultStatus.ANSWERED_NO_MENTION,
    )
    session.add(result)
    await session.flush()
    session.add(
        Citation(
            id=ids.new_id(ids.CITATION), engine_result_id=result.id, source_domain=domain,
            source_url=f"https://{domain}/p", source_type=CitationType.OTHER, position=1,
        )
    )
    await session.commit()


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    return engines_only_executor


@pytest.fixture
def stub_scan_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Three prompts; each answer names RMIT and Monash, cites a sibling of each and a stranger."""

    async def generate(**kwargs: Any) -> tuple[list[GeneratedPrompt], str]:  # noqa: ARG001
        return [
            GeneratedPrompt(text=f"question {n}", intent=list(PromptIntent)[n % 3])
            for n in range(3)
        ], "stub"

    async def ask_all(prompt: str, *, engines: Any, settings: Any) -> list[EngineAnswer]:  # noqa: ARG001
        return [
            _answer(
                "RMIT University and Monash University are both options.",
                "rmit.edu.vn", "monash.edu.my", "g2.com",
            )
            for _ in engines
        ]

    async def sentiment(answer: Any, *, subject_name: str, settings: Any = None):  # noqa: ANN202, ARG001
        return Sentiment.POSITIVE, Decimal("0.900")

    monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", generate)
    monkeypatch.setattr(scan_runner.engine_service, "ask_all", ask_all)
    monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", sentiment)


class TestThroughARealScan:
    async def _run(
        self, client: AsyncClient, stub_discovery, *, confirm: bool  # noqa: ANN001
    ) -> tuple[str, str]:
        await _sign_up(client)
        cid = await _make_client(client, *RMIT)
        stub_discovery(
            serp_domains=["monash.edu", "deakin.edu.au"],
            cocit_brands=[(MONASH[0], MONASH[1]), ("Deakin University", "deakin.edu.au")],
        )
        assert (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).status_code == 201
        if confirm:
            put = await client.put(
                f"{BASE}/clients/{cid}/other-sites",
                json={"client": ["rmit.edu.vn"],
                      "rivals": [{"rivalDomain": "monash.edu", "sites": ["monash.edu.my"]}],
                      "dismissed": []},
            )
            assert put.status_code == 200, put.text
        started = await client.post(
            f"{BASE}/clients/{cid}/scans", json={"engines": ["claude_search"]}
        )
        assert started.status_code == 202, started.text
        return cid, started.json()["id"]

    async def _stored(self, session: AsyncSession, sid: str) -> dict[str, tuple[Any, ...]]:
        rows = (
            await session.execute(
                select(
                    Citation.source_domain, Citation.cites_subject, Citation.source_type,
                    Citation.competitor_id,
                )
                .join(EngineResult, EngineResult.id == Citation.engine_result_id)
                .where(EngineResult.scan_id == sid)
                .distinct()
            )
        ).all()
        return {r[0]: tuple(r[1:]) for r in rows}

    async def test_a_confirmed_site_is_stored_as_the_brands_own_and_the_scan_is_v8(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, stub_scan_engines  # noqa: ANN001
    ) -> None:
        cid, sid = await self._run(client, stub_discovery, confirm=True)

        stored = await self._stored(session, sid)
        scan = await session.get(Scan, sid)
        monash_id = (
            await session.execute(
                select(Competitor.id).join(CompetitorSet).where(
                    CompetitorSet.scan_id == sid, Competitor.domain == "monash.edu"
                )
            )
        ).scalar_one()
        assert scan.matcher_version == extraction.MATCHER_VERSION == "v8"
        assert stored["rmit.edu.vn"] == (True, CitationType.OWNED, None)
        assert stored["monash.edu.my"] == (False, CitationType.COMPETITOR, monash_id)
        assert stored["g2.com"][0] is False and stored["g2.com"][2] is None

        # Neither is in the report's unclaimed list; the stranger is (the control).
        report = await build_report(session, scan)
        unclaimed = {d.domain for d in report.proof.unclaimed_cited_domains}
        assert "g2.com" in unclaimed
        assert not unclaimed & {"rmit.edu.vn", "monash.edu.my"}
        # ... nor in the fix generator's top sources.
        subject = await session.get(Client, cid)
        facts = await fix_runner.collect_facts(session, scan, subject)
        top = {d for d, _ in facts.top_cited_domains}
        assert "rmit.edu.vn" not in top
        assert {"rmit.edu.vn", "monash.edu.my"} <= facts.cited_domains

    async def test_only_proposed_is_stored_as_a_stranger_as_today(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, stub_scan_engines  # noqa: ANN001
    ) -> None:
        _, sid = await self._run(client, stub_discovery, confirm=False)

        stored = await self._stored(session, sid)

        assert stored["rmit.edu.vn"][0] is False
        assert stored["monash.edu.my"][0] is False and stored["monash.edu.my"][2] is None

    async def test_a_later_edit_of_the_list_changes_nothing_the_scan_stored_or_scored(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, stub_scan_engines  # noqa: ANN001
    ) -> None:
        """The list is read once, at RUNNING. The report, the fixes and a re-score read the rows."""
        cid, sid = await self._run(client, stub_discovery, confirm=True)
        before_rows = await self._stored(session, sid)
        first = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        scan = await session.get(Scan, sid)
        before_report = _report_facts(await build_report(session, scan))

        emptied = await client.put(
            f"{BASE}/clients/{cid}/other-sites",
            json={"client": [], "rivals": [], "dismissed": []},
        )
        assert emptied.status_code == 200, emptied.text
        session.expire_all()
        second = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        scan = await session.get(Scan, sid)

        assert await self._stored(session, sid) == before_rows
        assert second["inputsDigest"] == first["inputsDigest"]
        assert second["citationStrength"] == first["citationStrength"]
        assert _report_facts(await build_report(session, scan)) == before_report
        assert before_report["proof"]["unclaimed_cited_domains"]  # the report was real

    async def test_a_scan_stored_under_v7_is_refused_an_explicit_re_score(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, stub_scan_engines  # noqa: ANN001
    ) -> None:
        """Its citations were attributed without the confirmed lists; the guard says so (409)."""
        _, sid = await self._run(client, stub_discovery, confirm=False)
        await session.execute(update(Scan).where(Scan.id == sid).values(matcher_version="v7"))
        await session.commit()

        resp = await client.post(f"{BASE}/scans/{sid}/score")

        assert resp.status_code == 409, resp.text

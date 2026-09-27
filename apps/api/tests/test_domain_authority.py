"""The citing-domain authority lookup — the input Citation Strength gets in v3.

Two tiers, tested separately, because they are evidence of different kinds:
Tier A is a structural fact about a domain string and needs no data; Tier B
is a frequency across stored scans and needs a database. A third group pins
the thresholds to the argument for them rather than to their literals, the
pattern `test_alerts.py` set.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api.models import (
    Agency,
    Citation,
    Client,
    Competitor,
    CompetitorSet,
    EngineResult,
    Prompt,
    PromptSet,
    Scan,
)
from avp_api.models.client import ClassificationStatus
from avp_api.models.competitor import DetectionSource, DetectionStatus
from avp_api.models.engine_result import CitationType, Engine, EngineResultStatus
from avp_api.models.prompt import PromptIntent
from avp_api.models.scan import ScanStatus, ScanTrigger
from avp_api.services import domain_authority as authority
from avp_api.services.domain_authority import (
    CURATED_ONLY,
    DomainAuthority,
    Tier,
    curated_tier,
    load_domain_authority,
    load_platform_domains,
    normalise_domain,
)
from avp_api.services.scoring import CompetitorFacts, Dimension, ResultFacts, compute_score

# ---------------------------------------------------------------------------
# Tier A — the curated, structural list
# ---------------------------------------------------------------------------


class TestCuratedTier:
    @pytest.mark.parametrize(
        "domain",
        [
            # government / education, US-style
            "nih.gov",
            "travel.state.gov",
            "ca.gov",
            "mit.edu",
            "law.harvard.edu",
            "army.mil",
            # ... and their country forms
            "nhs.gov.uk",
            "ox.ac.uk",
            "ato.gov.au",
            "unsw.edu.au",
            "canada.gc.ca",
            "mhlw.go.jp",
            "service-public.gouv.fr",
            "sat.gob.mx",
            "ird.govt.nz",
            # reference works
            "wikipedia.org",
            "en.wikipedia.org",
            "wikidata.org",
            # wire services
            "apnews.com",
            "ap.org",
            "reuters.com",
            # cross-industry trust / review infrastructure
            "bbb.org",
            "trustpilot.com",
            "uk.trustpilot.com",
            "g2.com",
            "capterra.com",
            "capterra.co.uk",
            "capterra.ca",
            "capterra.com.de",
        ],
    )
    def test_matches_the_structural_list(self, domain: str) -> None:
        assert curated_tier(domain) is Tier.CURATED

    @pytest.mark.parametrize(
        "domain",
        [
            # the categories the list deliberately leaves out
            "nytimes.com",  # general news — contested ground
            "reddit.com",  # forums
            "webmd.com",  # industry-specific authority — Tier B's job
            "ieee.org",
            "americanbar.org",
            # shapes that LOOK like the suffix rules and are not
            "go.com",  # `go` only counts under a country code
            "gov.uk",  # the suffix alone, no institution in front
            "edu.pl",
            "government.com",
            "education.org",
            "govtrack.us",
            # a look-alike on a suffix that is not a public suffix
            "capterra.example.com",
            "wikipedia.org.evil.com",
            "g2crowd.io",
            # the ordinary run of cited domains
            "helpscout.com",
            "zendesk.com",
            "eesel.ai",
            "medium.com",
            "github.com",
            # junk
            "",
            "localhost",
            ".",
        ],
    )
    def test_everything_else_is_unclassified(self, domain: str) -> None:
        assert curated_tier(domain) is None

    def test_matching_is_on_the_normalised_domain(self) -> None:
        assert curated_tier("WWW.Reuters.com") is Tier.CURATED
        assert normalise_domain("WWW.Reuters.com.") == "reuters.com"


# ---------------------------------------------------------------------------
# the lookup object
# ---------------------------------------------------------------------------


class TestDomainAuthority:
    def test_curated_only_still_classifies_tier_a(self) -> None:
        """The default is 'no platform tier loaded', not 'no authority'."""
        assert CURATED_ONLY.tier_of("nih.gov") is Tier.CURATED
        assert CURATED_ONLY.tier_of("eesel.ai") is None

    def test_platform_domains_are_tier_b(self) -> None:
        lookup = DomainAuthority(platform_domains=frozenset({"eesel.ai"}))
        assert lookup.tier_of("eesel.ai") is Tier.PLATFORM
        assert lookup.tier_of("www.eesel.ai") is Tier.PLATFORM
        assert lookup.tier_of("featurebase.app") is None

    def test_curated_wins_over_platform(self) -> None:
        """A structural fact is never downgraded by a frequency count."""
        lookup = DomainAuthority(platform_domains=frozenset({"g2.com"}))
        assert lookup.tier_of("g2.com") is Tier.CURATED

    def test_tiers_for_returns_only_the_classified_in_sorted_order(self) -> None:
        lookup = DomainAuthority(platform_domains=frozenset({"eesel.ai"}))
        tiers = lookup.tiers_for(["zendesk.com", "nih.gov", "eesel.ai", "nih.gov", "blog.example"])
        assert list(tiers) == ["eesel.ai", "nih.gov"]
        assert tiers == {"eesel.ai": Tier.PLATFORM, "nih.gov": Tier.CURATED}
        assert authority.describe(tiers) == {"eesel.ai": "platform", "nih.gov": "curated"}


# ---------------------------------------------------------------------------
# the thresholds, argued rather than pinned
# ---------------------------------------------------------------------------


class TestTheThresholdsAreArgued:
    def test_industries_must_exceed_the_split_market_case(self) -> None:
        # Two industries can be one market under two labels — the stored data
        # has help-desk software under four. Three is the smallest count that
        # starts to mean "unrelated markets".
        assert authority.MIN_PLATFORM_INDUSTRIES >= 3

    def test_clients_bind_beyond_what_industries_already_imply(self) -> None:
        # N industries already imply N clients; a client threshold that did not
        # exceed the industry one would add nothing.
        assert authority.MIN_PLATFORM_CLIENTS > authority.MIN_PLATFORM_INDUSTRIES

    def test_the_thresholds_are_reachable_at_todays_volume(self) -> None:
        # 27 clients across 12 industry labels on the day this was written. A
        # threshold no domain could clear would make Tier B a fiction.
        assert authority.MIN_PLATFORM_CLIENTS <= 27
        assert authority.MIN_PLATFORM_INDUSTRIES <= 12


# ---------------------------------------------------------------------------
# Tier B — platform frequency, against real rows
# ---------------------------------------------------------------------------


async def _seed_scan_citing(
    session: AsyncSession,
    agency: Agency,
    *,
    n: int,
    industry: str | None,
    domains: list[str],
    deleted: bool = False,
    client_domain: str | None = None,
    competitor_domain: str | None = None,
) -> None:
    """One client, one scan, one answered result citing `domains`.

    `client_domain` makes this client's own site that domain; `competitor_domain`
    gives the scan a competitor set with one detected rival at that domain.
    Both are the v4 market-participant controls.
    """
    from datetime import UTC, datetime

    client = Client(
        id=ids.new_id(ids.CLIENT),
        agency_id=agency.id,
        name=f"Client {n}",
        domain=client_domain or f"client-{n}.example",
        industry=industry,
        classification_status=(
            ClassificationStatus.CLASSIFIED if industry else ClassificationStatus.PENDING
        ),
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    session.add(client)
    await session.flush()
    scan = Scan(
        id=ids.new_id(ids.SCAN),
        client_id=client.id,
        agency_id=agency.id,
        status=ScanStatus.SUCCEEDED,
        trigger=ScanTrigger.MANUAL,
        formula_version="test",
        prompt_count=1,
        engine_result_count=1,
    )
    session.add(scan)
    await session.flush()
    if competitor_domain:
        cset = CompetitorSet(
            id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, status=DetectionStatus.OK
        )
        session.add(cset)
        await session.flush()
        session.add(
            Competitor(
                id=ids.new_id(ids.COMPETITOR),
                competitor_set_id=cset.id,
                name="Rival",
                domain=competitor_domain,
                rank=1,
                detection_source=DetectionSource.BOTH,
            )
        )
        await session.flush()
    prompt_set = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id)
    session.add(prompt_set)
    await session.flush()
    prompt = Prompt(
        id=ids.new_id(ids.PROMPT),
        prompt_set_id=prompt_set.id,
        text="best tool?",
        intent=PromptIntent.AWARENESS,
        position=1,
    )
    session.add(prompt)
    await session.flush()
    result = EngineResult(
        id=ids.new_id(ids.ENGINE_RESULT),
        scan_id=scan.id,
        prompt_id=prompt.id,
        engine=Engine.CLAUDE_SEARCH,
        status=EngineResultStatus.OK,
        mentioned=True,
        position=1,
    )
    session.add(result)
    await session.flush()
    for i, domain in enumerate(domains, start=1):
        session.add(
            Citation(
                id=ids.new_id(ids.CITATION),
                engine_result_id=result.id,
                source_domain=domain,
                source_url=f"https://{domain}/page",
                source_type=CitationType.OTHER,
                position=i,
                cites_subject=False,
            )
        )
    await session.flush()


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    """Five clients in three industries, plus the controls that must not count.

    `wide.example` is cited by every qualifying client: three industries, five
    clients — exactly the thresholds, so the test fails if either drifts.
    `narrow.example` has the clients but only two industries. `few.example`
    has the industries but four clients. `unknown.example` is cited only by
    clients with no industry, and `gone.example` only by a deleted one; each
    would clear the thresholds if those rows were counted.

    v4 controls: `vendor.example` clears 3 / 5 and is one client's own domain;
    `rival.example` clears 3 / 5 and is a detected competitor's domain on one
    scan; `g2.com` clears 3 / 5 AND is a detected competitor, and must stay
    curated. Each is cited by the same five qualifying clients as `wide.example`.
    """
    agency = Agency(id=ids.new_id(ids.AGENCY), name="Authority Test", slug="authority-test")
    session.add(agency)
    await session.flush()

    industries = ["dental practice", "web analytics software", "agricultural machinery"]
    for n in range(5):
        await _seed_scan_citing(
            session,
            agency,
            n=n,
            industry=industries[n % 3],  # 0,1,2,0,1 -> three industries, five clients
            domains=[
                "wide.example", "few.example" if n < 4 else "other.example",
                "vendor.example", "rival.example", "g2.com",
            ],
            client_domain="vendor.example" if n == 0 else None,
            competitor_domain="rival.example" if n == 1 else None,
        )
    # g2.com detected as a competitor on a sixth client's scan, with no industry
    # so it adds nothing to any count.
    await _seed_scan_citing(
        session, agency, n=20, industry=None, domains=[], competitor_domain="g2.com"
    )
    # narrow: five clients, two industries
    for n in range(5, 10):
        await _seed_scan_citing(
            session, agency, n=n, industry=industries[n % 2], domains=["narrow.example"]
        )
    # unknown: five unclassified clients, cited everywhere
    for n in range(10, 15):
        await _seed_scan_citing(session, agency, n=n, industry=None, domains=["unknown.example"])
    # gone: five deleted clients across three industries
    for n in range(15, 20):
        await _seed_scan_citing(
            session, agency, n=n, industry=industries[n % 3], domains=["gone.example"],
            deleted=True,
        )
    await session.commit()
    return session


class TestPlatformTier:
    async def test_a_domain_across_enough_industries_and_clients_qualifies(
        self, seeded: AsyncSession
    ) -> None:
        assert await load_platform_domains(seeded) == frozenset({"wide.example"})

    async def test_clients_without_an_industry_do_not_count(self, seeded: AsyncSession) -> None:
        assert "unknown.example" not in await load_platform_domains(seeded)

    async def test_deleted_clients_do_not_count(self, seeded: AsyncSession) -> None:
        assert "gone.example" not in await load_platform_domains(seeded)

    async def test_the_thresholds_are_the_ones_in_the_module(self, seeded: AsyncSession) -> None:
        # Lower either threshold by one and a control gets in — so the
        # fixture is sized to the constants, not to a number of its own.
        assert await load_platform_domains(
            seeded, min_industries=authority.MIN_PLATFORM_INDUSTRIES - 1
        ) >= frozenset({"wide.example", "narrow.example"})
        assert await load_platform_domains(
            seeded, min_clients=authority.MIN_PLATFORM_CLIENTS - 1
        ) >= frozenset({"wide.example", "few.example"})

    async def test_a_clients_own_domain_cannot_be_platform_tier(
        self, seeded: AsyncSession
    ) -> None:
        """v4 — a market participant is an option, not a reference."""
        assert "vendor.example" not in await load_platform_domains(seeded)

    async def test_a_detected_competitors_domain_cannot_be_platform_tier(
        self, seeded: AsyncSession
    ) -> None:
        assert "rival.example" not in await load_platform_domains(seeded)

    async def test_a_curated_domain_detected_as_a_competitor_stays_curated(
        self, seeded: AsyncSession
    ) -> None:
        """Tier A is unaffected: a structural fact is never downgraded."""
        lookup = await load_domain_authority(seeded)
        assert "g2.com" not in lookup.platform_domains
        assert lookup.tier_of("g2.com") is Tier.CURATED

    async def test_the_production_lookup_carries_both_tiers(self, seeded: AsyncSession) -> None:
        lookup = await load_domain_authority(seeded)
        assert lookup.tier_of("wide.example") is Tier.PLATFORM
        assert lookup.tier_of("nih.gov") is Tier.CURATED
        assert lookup.tier_of("narrow.example") is None

    async def test_an_empty_database_yields_the_curated_tier_only(
        self, session: AsyncSession
    ) -> None:
        lookup = await load_domain_authority(session)
        assert lookup.platform_domains == frozenset()
        assert lookup.tier_of("g2.com") is Tier.CURATED


class TestTheLoadedLookupReachesTheScore:
    async def test_a_domain_that_crossed_the_thresholds_scores_the_dimension(
        self, seeded: AsyncSession
    ) -> None:
        """Fixture rows → Tier B → a measured Citation Strength, end to end.

        The same answer scored under the curated tier alone is excluded under
        NO_AUTHORITY_DATA; under the loaded lookup the platform domain gives
        the dimension its signal and five dimensions carry weight.
        """
        lookup = await load_domain_authority(seeded)
        results = [
            ResultFacts(
                result_id="eres_0001", engine="claude_search", status=EngineResultStatus.OK,
                mentioned=True, sentiment=None, intent=PromptIntent.AWARENESS,
                brands=(("Subject", True), ("Rival", False)),
                citations=(("wide.example", False), ("narrow.example", False)),
            )
        ]
        rival = [CompetitorFacts(competitor_id="comp_0001", name="Rival", domain="rival.example")]

        excluded = compute_score(results, rival, competitor_set_status=DetectionStatus.OK,
                                 authority=CURATED_ONLY)
        assert excluded.excluded_dimensions["citation_strength"] == "NO_AUTHORITY_DATA"

        scored = compute_score(results, rival, competitor_set_status=DetectionStatus.OK,
                               authority=lookup)
        assert "citation_strength" not in scored.excluded_dimensions
        assert scored.value(Dimension.CITATION_STRENGTH) is not None
        assert scored.sub_scores[Dimension.CITATION_STRENGTH].included
        assert scored.inputs_digest != excluded.inputs_digest

    async def test_the_digest_moves_when_a_domain_in_the_scan_changes_tier(
        self, seeded: AsyncSession
    ) -> None:
        """The v4 exclusion changes tiers, and `domain_tiers` carries that.

        Same results, two lookups: the loaded one (where `vendor.example` is
        refused) and one in which it had qualified. The digest differs, so a
        score that moved because the exclusion landed is attributable — rule 1
        — rather than assumed to be.
        """
        loaded = await load_domain_authority(seeded)
        assert "vendor.example" not in loaded.platform_domains
        as_if_admitted = DomainAuthority(
            platform_domains=loaded.platform_domains | {"vendor.example"}
        )
        results = [
            ResultFacts(
                result_id="eres_0001", engine="claude_search", status=EngineResultStatus.OK,
                mentioned=True, sentiment=None, intent=PromptIntent.AWARENESS,
                brands=(("Subject", True), ("Rival", False)),
                citations=(("vendor.example", False), ("wide.example", False)),
            )
        ]
        rival = [CompetitorFacts(competitor_id="comp_0001", name="Rival", domain="rival.example")]
        before = compute_score(results, rival, competitor_set_status=DetectionStatus.OK,
                               authority=as_if_admitted)
        after = compute_score(results, rival, competitor_set_status=DetectionStatus.OK,
                              authority=loaded)
        assert before.inputs_digest != after.inputs_digest
        # And a lookup change that touches no domain in the scan leaves it alone.
        elsewhere = DomainAuthority(platform_domains=loaded.platform_domains | {"far.example"})
        assert compute_score(results, rival, competitor_set_status=DetectionStatus.OK,
                             authority=elsewhere).inputs_digest == after.inputs_digest

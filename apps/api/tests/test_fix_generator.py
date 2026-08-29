"""Fix generation — Epic 8.

No network. The model call is stubbed at `messages.parse`, so everything below
exercises the real candidate rules, the real acceptance policy and the real
persistence. The live generation against a real scan is scripts/verify_fixes.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.models.action_item import ActionItemSource, Effort, Priority
from avp_api.services import fix_generator, fix_runner
from avp_api.services.fix_generator import (
    DimensionFact,
    FixFacts,
    GeneratedFix,
    GeneratedFixSet,
    accept,
    banned_claim_in,
    build_candidates,
    build_fix_prompt,
    machine_code_in,
)

BASE = "/api/v1"


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN201
    """Stop after the engine phase — Epic 9.17.

    These tests assert how the product renders a MISSING score / audit /
    competitor set / fix list. Since 9.17 a scan started through the endpoint
    runs the whole chain and produces all four, so those absences are no longer
    reachable by simply not asking for them.

    They are still reachable in production — any chained phase can fail, and
    `scan_executor._attempt` deliberately lets the rest continue — so the states
    remain worth testing. This constructs them on purpose instead of relying on
    the product not finishing, which is a more honest setup than the one it
    replaces.
    """
    return engines_only_executor



def dim(key: str, weight: str, subscore: str) -> DimensionFact:
    w = Decimal(weight)
    s = Decimal(subscore)
    # Quantised exactly as fix_runner.dimension_facts does, so what these
    # tests assert about the prompt is what production actually emits.
    return DimensionFact(
        key=key,
        weight=w.quantize(Decimal("0.01")),
        subscore=s,
        gap=(w * (Decimal(100) - s) / Decimal(100)).quantize(Decimal("0.01")),
    )


# The real Help Scout scan, as scripts/verify_fixes.py finds it.
HELPSCOUT = [
    dim("mention_rate", "30", "100.00"),
    dim("share_of_voice", "25", "30.00"),
    dim("citation_strength", "20", "3.70"),
    dim("sentiment", "15", "75.00"),
    dim("technical_foundation", "10", "87.50"),
]
HELPSCOUT_FINDINGS = [
    ("schema_faq", "warn", "NO_FAQ_SCHEMA"),
    ("schema_product_or_service", "warn", "NO_PRODUCT_OR_SERVICE_SCHEMA"),
]


def facts(**kw) -> FixFacts:  # noqa: ANN003
    base = {"domain": "helpscout.com", "brand_name": "Help Scout", "dimensions": HELPSCOUT}
    base.update(kw)
    return FixFacts(**base)


def generated(candidate_id: str, **kw) -> GeneratedFix:  # noqa: ANN003
    base = {
        "candidate_id": candidate_id,
        "title": "Add FAQPage schema to the pages that answer buyer questions",
        "detail": "Marks the question-and-answer sections up so a machine can read them.",
        "priority": Priority.HIGH,
        "effort": Effort.S,
        "priority_reason": "Largest gap on the list.",
        "effort_reason": "A template-level markup change.",
    }
    base.update(kw)
    return GeneratedFix(**base)


# ---------------------------------------------------------------------------
# candidate selection — Epic 7's rules, not new ones
# ---------------------------------------------------------------------------


class TestCandidates:
    def test_reproduces_the_client_s_list_for_the_real_scan(self) -> None:
        """The five candidates derive.ts produces for the Help Scout scan.

        Pinned as literal keys because this is the whole basis of the epic: the
        generator words candidates Epic 7 chose. If these drift from
        derive.test.ts's `the fix list` block, generated copy silently stops
        merging and the report quietly falls back to the string table.
        """
        keys = [c.key for c in build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)]
        assert keys == [
            "gap:citation_strength",
            "gap:share_of_voice",
            "gap:sentiment",
            "audit:schema_faq",
            "audit:schema_product_or_service",
        ]

    def test_dimension_gaps_below_the_floor_are_dropped(self) -> None:
        """technical_foundation's gap is 1.25 — noise beside one worth 19."""
        keys = [c.key for c in build_candidates(HELPSCOUT, [])]
        assert "gap:technical_foundation" not in keys

    def test_dimension_fixes_cannot_crowd_out_named_findings(self) -> None:
        """The Epic 7 regression: five dimensions all outrank an audit finding
        on points, so an uncapped list is entirely abstract."""
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        assert sum(1 for c in candidates if c.source is ActionItemSource.GAP) == 3
        assert sum(1 for c in candidates if c.source is ActionItemSource.AUDIT) == 2

    def test_a_crawl_blocking_check_is_promoted_above_everything(self) -> None:
        """Nothing else on the list can take effect underneath a noindex."""
        candidates = build_candidates(
            HELPSCOUT, [("indexable", "fail", "META_ROBOTS_NOINDEX"), *HELPSCOUT_FINDINGS]
        )
        assert candidates[0].key == "audit:indexable"
        assert candidates[0].rank == 1

    def test_never_names_a_dimension_we_have_not_measured(self) -> None:
        """NOT_YET_MEASURED is OUR missing capability. Generating "fix your
        technical foundation" from it would blame a client for a check that
        never ran."""
        keys = [
            c.key
            for c in build_candidates(
                HELPSCOUT, [], {"citation_strength": "NOT_YET_MEASURED"}
            )
        ]
        assert "gap:citation_strength" not in keys

    def test_carries_no_more_than_five(self) -> None:
        many = [(f"check_{i}", "warn", f"CODE_{i}") for i in range(20)]
        assert len(build_candidates(HELPSCOUT, many)) == fix_generator.MAX_FIXES

    def test_ranks_are_contiguous_from_one(self) -> None:
        """`rank >= 1` is a CHECK constraint, and (scan_id, rank) is what the
        report orders by."""
        ranks = [c.rank for c in build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)]
        assert ranks == [1, 2, 3, 4, 5]

    def test_audit_candidates_carry_no_invented_point_value(self) -> None:
        for candidate in build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS):
            if candidate.source is ActionItemSource.AUDIT:
                assert candidate.points_upside is None

    def test_a_finding_without_a_detail_code_produces_nothing(self) -> None:
        assert build_candidates([], [("meta_title", "warn", None)]) == []

    def test_selection_is_deterministic(self) -> None:
        first = [c.key for c in build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)]
        second = [c.key for c in build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)]
        assert first == second


class TestCandidateRulesMatchTheClient:
    """The constants are mirrored from derive.ts. Mirrors drift silently."""

    def test_caps_and_floor_match_the_derivation(self) -> None:
        assert fix_generator.MAX_FIXES == 5
        assert fix_generator.MAX_DIMENSION_FIXES == 3
        assert Decimal("2") == fix_generator.MIN_GAP_POINTS

    def test_blocking_checks_match_the_derivation(self) -> None:
        assert set(fix_generator.BLOCKING_CHECK_KEYS) == {"indexable", "robots_txt_present"}


# ---------------------------------------------------------------------------
# acceptance — what the generator is not allowed to get away with
# ---------------------------------------------------------------------------


class TestAcceptance:
    def test_a_fix_for_something_nobody_measured_is_discarded(self) -> None:
        """The entire design is that the model words findings rather than
        producing them. A fix keyed to an unmeasured dimension is an invention."""
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        accepted, rejected = accept(
            GeneratedFixSet(fixes=[generated("gap:backlink_authority")]), candidates
        )
        assert accepted == []
        assert rejected == ["gap:backlink_authority"]

    def test_rank_and_upside_come_from_the_candidate_not_the_model(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        accepted, _ = accept(
            GeneratedFixSet(fixes=[generated("audit:schema_faq")]), candidates
        )
        assert accepted[0].rank == 4
        assert accepted[0].points_upside is None

    def test_priority_and_effort_do_come_from_the_model(self) -> None:
        """Epic 7's priority was a three-branch heuristic and its effort a fixed
        per-code lookup. This is the field that had to change."""
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        accepted, _ = accept(
            GeneratedFixSet(
                fixes=[generated("gap:sentiment", priority=Priority.LOW, effort=Effort.L)]
            ),
            candidates,
        )
        assert accepted[0].priority is Priority.LOW
        assert accepted[0].effort is Effort.L

    def test_a_duplicate_candidate_is_taken_once(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        accepted, rejected = accept(
            GeneratedFixSet(
                fixes=[generated("audit:schema_faq"), generated("audit:schema_faq")]
            ),
            candidates,
        )
        assert len(accepted) == 1
        assert rejected == ["audit:schema_faq"]

    def test_output_is_returned_in_epic_seven_s_order(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        accepted, _ = accept(
            GeneratedFixSet(
                fixes=[
                    generated("audit:schema_faq"),
                    generated("gap:citation_strength"),
                ]
            ),
            candidates,
        )
        assert [f.source_key for f in accepted] == ["citation_strength", "schema_faq"]


class TestUnsupportableClaims:
    """The rendered page is swept for these already. Catching them here means
    generated copy cannot introduce one in the first place."""

    @pytest.mark.parametrize(
        "phrase",
        ["revenue", "ROI", "traffic", "conversion", "guarantee", "dominate", "act now"],
    )
    def test_a_claim_this_product_cannot_support_is_caught(self, phrase: str) -> None:
        assert banned_claim_in(f"Do the thing to improve {phrase} on the site") is not None

    def test_ordinary_copy_passes(self) -> None:
        assert banned_claim_in("Add FAQPage schema to the pages that answer questions") is None

    def test_a_fix_making_one_is_dropped_rather_than_stored(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        accepted, rejected = accept(
            GeneratedFixSet(
                fixes=[
                    generated(
                        "audit:schema_faq",
                        detail="This will grow organic traffic substantially.",
                    )
                ]
            ),
            candidates,
        )
        assert accepted == []
        assert rejected == ["audit:schema_faq"]

    def test_the_list_matches_the_rendered_page_sweep(self) -> None:
        """ReportView.test.tsx "makes no claim it cannot support" sweeps this
        exact vocabulary. Two lists that can disagree eventually will."""
        assert set(fix_generator.BANNED_CLAIMS) == {
            "revenue", "roi", "traffic", "leads", "conversion", "guarantee",
            "million", "x more", "skyrocket", "dominate", "act now", "limited time",
        }


# ---------------------------------------------------------------------------
# the prompt
# ---------------------------------------------------------------------------


class TestInternalIdentifiers:
    """Epic 7 resolves a finding through our own words, never the stored code.

    Epic 8 hands the generator those codes so it knows what happened, which
    makes it the first thing in the system able to put one back on the page.
    The first live generation did exactly that.
    """

    def test_a_quoted_detail_code_is_caught(self) -> None:
        candidate = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)[3]
        assert (
            machine_code_in("The schema_faq check returned NO_FAQ_SCHEMA", candidate)
            is not None
        )

    def test_a_quoted_check_key_is_caught(self) -> None:
        candidate = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)[3]
        assert machine_code_in("The schema_faq check did not pass", candidate) == "schema_faq"

    def test_plain_english_about_the_same_finding_passes(self) -> None:
        """The rule is about machine identifiers, not about the subject."""
        candidate = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)[3]
        assert (
            machine_code_in(
                "Add FAQPage schema to the pages that answer buyer questions", candidate
            )
            is None
        )

    def test_a_fix_quoting_a_code_is_dropped_rather_than_rendered(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        accepted, rejected = accept(
            GeneratedFixSet(
                fixes=[
                    generated(
                        "audit:schema_faq",
                        detail="The schema_faq check returned NO_FAQ_SCHEMA.",
                    )
                ]
            ),
            candidates,
        )
        assert accepted == []
        assert rejected == ["audit:schema_faq"]

    def test_the_system_prompt_states_the_rule(self) -> None:
        assert "Never quote an internal identifier back" in fix_generator.SYSTEM_PROMPT


class TestPrompt:
    def test_carries_the_measured_figures(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        prompt = build_fix_prompt(facts(composite=Decimal("58.24")), candidates)
        assert "Help Scout" in prompt
        assert "helpscout.com" in prompt
        assert "58.24" in prompt
        assert "citation_strength: weight 20.00, score 3.70, gap 19.26 points" in prompt

    def test_names_every_candidate_and_closes_the_list(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        prompt = build_fix_prompt(facts(), candidates)
        for candidate in candidates:
            assert candidate.key in prompt
        assert "and no others" in prompt

    def test_audit_candidates_carry_their_code_not_a_sentence(self) -> None:
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        prompt = build_fix_prompt(facts(), candidates)
        assert "NO_FAQ_SCHEMA" in prompt

    def test_is_deterministic(self) -> None:
        """Same scan, same prompt — so a re-run is comparable."""
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        assert build_fix_prompt(facts(), candidates) == build_fix_prompt(facts(), candidates)

    def test_the_system_prompt_forbids_unsupportable_claims(self) -> None:
        assert "revenue" in fix_generator.SYSTEM_PROMPT
        assert "never imply one" in fix_generator.SYSTEM_PROMPT

    def test_the_system_prompt_closes_the_candidate_list(self) -> None:
        assert "never invent a candidate" in fix_generator.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# failure paths — a provider outage degrades the report, never breaks it
# ---------------------------------------------------------------------------


class TestFailurePaths:
    async def test_no_candidates_is_not_a_failure(self) -> None:
        outcome = await fix_generator.generate_fixes(facts(), [])
        assert outcome.status == "empty"
        assert outcome.reason_code == "NO_CANDIDATES"
        assert outcome.fixes == []

    async def test_a_provider_error_writes_nothing(self, monkeypatch) -> None:  # noqa: ANN001
        import anthropic

        async def boom(**kwargs):  # noqa: ANN003, ARG001
            raise anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]

        monkeypatch.setattr(
            anthropic.resources.messages.AsyncMessages, "parse", lambda self, **kw: boom(**kw)
        )
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        outcome = await fix_generator.generate_fixes(facts(), candidates)
        assert outcome.status == "failed"
        assert outcome.reason_code == "PROVIDER_UNREACHABLE"
        assert outcome.fixes == []

    async def test_a_response_with_nothing_usable_is_a_failure_not_an_empty_list(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        """An empty list and "we generated five inventions" are different states."""
        import anthropic

        class FakeResponse:
            stop_reason = "end_turn"
            parsed_output = GeneratedFixSet(fixes=[generated("gap:nonsense")])

        async def fake(**kwargs):  # noqa: ANN003, ARG001
            return FakeResponse()

        monkeypatch.setattr(
            anthropic.resources.messages.AsyncMessages, "parse", lambda self, **kw: fake(**kw)
        )
        candidates = build_candidates(HELPSCOUT, HELPSCOUT_FINDINGS)
        outcome = await fix_generator.generate_fixes(facts(), candidates)
        assert outcome.status == "failed"
        assert outcome.reason_code == "NO_USABLE_FIXES"


# ---------------------------------------------------------------------------
# persistence — refresh in place, and never over the operator
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_model(monkeypatch):  # noqa: ANN001, ANN201
    """Replace the model call with a fixed, valid response for every candidate."""

    def _install(title: str = "Do the specific thing", priority: Priority = Priority.HIGH):
        import anthropic

        async def fake(**kwargs):  # noqa: ANN003
            content = kwargs["messages"][0]["content"]
            keys = [
                line.split(" — ")[0].removeprefix("- ")
                for line in content.splitlines()
                if line.startswith("- gap:") or line.startswith("- audit:")
            ]

            # Numbered rather than keyed: quoting the candidate key back would
            # embed an internal identifier in the copy, which the real guard
            # rejects. A stub that produces copy production would refuse is a
            # stub testing the wrong thing.
            class FakeResponse:
                stop_reason = "end_turn"
                parsed_output = GeneratedFixSet(
                    fixes=[
                        generated(key, title=f"{title} number {i}", priority=priority)
                        for i, key in enumerate(keys, start=1)
                    ]
                )

            return FakeResponse()

        monkeypatch.setattr(
            anthropic.resources.messages.AsyncMessages, "parse", lambda self, **kw: fake(**kw)
        )

    return _install


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Fix Test Agency",
            "fullName": "Op",
            "email": "fixes@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    from avp_api.models.engine_result import Sentiment
    from avp_api.models.prompt import PromptIntent
    from avp_api.services import scan_runner
    from avp_api.services.engines import CitedSource, EngineAnswer
    from avp_api.services.prompts import GeneratedPrompt

    def _install(n_prompts: int = 4):
        prompts = [
            GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return prompts, "stub"

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine,
                    engine_version="stub",
                    prompt_text=prompt,
                    text="Zendesk is popular. Help Scout is simpler and well liked.",
                    citations=[
                        CitedSource(url="https://g2.com/x", domain="g2.com", position=1)
                    ],
                    latency_ms=5,
                )
                for engine in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)

    return _install


async def _scored_scan(client: AsyncClient, stub_engines) -> str:  # noqa: ANN001
    resp = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    cid = resp.json()["id"]
    stub_engines()
    sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
    assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
    return sid


class TestEndpoint:
    async def test_generates_and_reads_back(
        self, client: AsyncClient, stub_engines, stub_model
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        stub_model()

        created = await client.post(f"{BASE}/scans/{sid}/fixes")
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "generated"
        assert body["generatedBy"] == fix_generator.FIX_MODEL
        assert len(body["items"]) >= 1

        read = await client.get(f"{BASE}/scans/{sid}/fixes")
        assert read.status_code == 200
        assert [i["id"] for i in read.json()["items"]] == [i["id"] for i in body["items"]]

    async def test_an_ungenerated_scan_is_empty_not_missing(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """"No fixes yet" is a state of a scan that exists."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        resp = await client.get(f"{BASE}/scans/{sid}/fixes")
        assert resp.status_code == 200
        assert resp.json() == {
            "scanId": sid,
            "status": "empty",
            "reasonCode": None,
            "generatedBy": None,
            "items": [],
        }

    async def test_another_agency_cannot_see_the_scan(
        self, client: AsyncClient, stub_engines, stub_model
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        stub_model()
        await client.post(f"{BASE}/scans/{sid}/fixes")

        await client.post(f"{BASE}/auth/sign-out")
        resp = await client.post(
            f"{BASE}/auth/sign-up",
            json={
                "agencyName": "Other Agency",
                "fullName": "Other",
                "email": "other@test.example",
                "password": "correct-horse-battery-staple",
            },
        )
        assert resp.status_code == 201
        # 404 not 403 — confirming an id exists leaks across tenants.
        assert (await client.get(f"{BASE}/scans/{sid}/fixes")).status_code == 404


class TestRegeneration:
    async def test_refreshes_in_place_rather_than_duplicating(
        self, client: AsyncClient, stub_engines, stub_model
    ) -> None:  # noqa: ANN001
        """A regenerated list is a better statement of the same measurement."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)

        stub_model(title="First wording")
        first = (await client.post(f"{BASE}/scans/{sid}/fixes")).json()["items"]
        stub_model(title="Second wording")
        second = (await client.post(f"{BASE}/scans/{sid}/fixes")).json()["items"]

        assert len(second) == len(first)
        assert [i["id"] for i in second] == [i["id"] for i in first]
        assert second[0]["title"].startswith("Second wording")

    async def test_operator_status_survives_regeneration(
        self, client: AsyncClient, session, stub_engines, stub_model
    ) -> None:  # noqa: ANN001
        """The competitor-override precedent: an operator who has worked the
        list must not have that work silently undone by the next run."""
        from sqlalchemy import select

        from avp_api.models import ActionItem, ActionItemStatus, Scan

        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        stub_model()
        await client.post(f"{BASE}/scans/{sid}/fixes")

        row = (
            await session.execute(
                select(ActionItem).where(ActionItem.scan_id == sid).order_by(ActionItem.rank)
            )
        ).scalars().first()
        assert row is not None
        row_id, key = row.id, row.source_key
        row.status = ActionItemStatus.DONE
        await session.commit()

        scan = (await session.execute(select(Scan).where(Scan.id == sid))).scalar_one()
        assert scan is not None
        stub_model(title="Regenerated")
        await client.post(f"{BASE}/scans/{sid}/fixes")

        refreshed = (
            await session.execute(select(ActionItem).where(ActionItem.id == row_id))
        ).scalar_one()
        await session.refresh(refreshed)
        assert refreshed.status is ActionItemStatus.DONE, "the operator's decision was overwritten"
        assert refreshed.source_key == key
        assert refreshed.title.startswith("Regenerated"), "the wording should still refresh"

    async def test_a_failed_generation_leaves_the_previous_list_intact(
        self, client: AsyncClient, stub_engines, stub_model, monkeypatch
    ) -> None:  # noqa: ANN001
        import anthropic

        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        stub_model(title="Good wording")
        before = (await client.post(f"{BASE}/scans/{sid}/fixes")).json()["items"]

        async def boom(**kwargs):  # noqa: ANN003, ARG001
            raise anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]

        monkeypatch.setattr(
            anthropic.resources.messages.AsyncMessages, "parse", lambda self, **kw: boom(**kw)
        )
        after = await client.post(f"{BASE}/scans/{sid}/fixes")
        assert after.json()["status"] == "failed"
        assert after.json()["reasonCode"] == "PROVIDER_UNREACHABLE"
        assert [i["title"] for i in after.json()["items"]] == [i["title"] for i in before]


class TestReportProjection:
    async def test_the_report_carries_the_generated_list(
        self, client: AsyncClient, stub_engines, stub_model
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        stub_model()
        await client.post(f"{BASE}/scans/{sid}/fixes")

        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert len(report["actionItems"]) >= 1
        item = report["actionItems"][0]
        assert item["source"] in ("gap", "audit")
        assert item["sourceKey"]

    async def test_an_ungenerated_report_carries_an_empty_list(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Not null and not absent — the fix beat renders its deterministic
        derivation in this case, which is a weaker report, not a broken one."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert report["actionItems"] == []

    async def test_a_dismissed_fix_leaves_the_plan(
        self, client: AsyncClient, session, stub_engines, stub_model
    ) -> None:  # noqa: ANN001
        from sqlalchemy import select

        from avp_api.models import ActionItem, ActionItemStatus

        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        stub_model()
        await client.post(f"{BASE}/scans/{sid}/fixes")

        rows = (
            await session.execute(select(ActionItem).where(ActionItem.scan_id == sid))
        ).scalars().all()
        before = len(rows)
        rows[0].status = ActionItemStatus.DISMISSED
        await session.commit()

        report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert len(report["actionItems"]) == before - 1


class TestFactCollection:
    def test_the_gap_arithmetic_matches_the_ledger(self) -> None:
        """gap = weight x (100 - subscore) / 100, in Decimal.

        19.26 is the figure the chart draws for citation_strength on the real
        scan, and derive.test.ts pins the same number.
        """
        facts_ = dim("citation_strength", "20", "3.70")
        assert facts_.gap == Decimal("19.26")

    def test_no_float_enters_the_gap_arithmetic(self) -> None:
        """scoring-spec.md rule 3, applied to the number that decides which
        fixes get written. Same categorical guard as test_scoring.py's."""
        import inspect

        source = inspect.getsource(fix_runner.dimension_facts)
        offenders = [
            line.strip()
            for line in source.splitlines()
            if "float(" in line and not line.strip().startswith("#")
        ]
        assert not offenders, offenders

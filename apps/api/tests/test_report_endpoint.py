"""The report projection — Epic 7.

Engine execution is stubbed (Epic 4's stubs) so these run without paid calls.
Nothing about the score, the competitor set or the audit is mocked — the point
of these tests is that the report reads what those epics actually persisted.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

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

        # Derived from the registry — see test_scan_endpoints.py's N_ENGINES.
        from avp_api.services.engines import DEFAULT_ENGINES

        engines = {c["engine"] for c in proof["engineCoverage"]}
        assert engines == {e.value for e in DEFAULT_ENGINES}
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


class TestCompetitorSetScope:
    """Finding 3 in the REPORT projection — Epic 3.11.

    `ReportCompetitorSetOut` is a separate schema from `CompetitorSetOut` and
    is built by a different function. Fixing the scope in one and not the other
    would leave the misleading figure exactly where a client actually reads it,
    so the property is asserted on both sides.
    """

    async def test_the_report_scopes_the_confidence_to_the_detected_rows(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        resp = await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )
        cid = resp.json()["id"]

        stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Operator Pick", "domain": "operatorpick.com"}]},
        )
        # Re-detect so a fresh confidence is written over a part-hand-set list.
        stub_discovery(
            serp_domains=["b.com", "c.com"],
            cocit_brands=[("B", "b.com"), ("C", "c.com")],
        )
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        stub_engines(n_prompts=4)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201

        cset = (await client.get(f"{BASE}/scans/{sid}/report")).json()["competitorSet"]
        assert cset is not None, "the scan must carry a set for this to test anything"

        manual = [c for c in cset["competitors"] if c["isManualOverride"]]
        assert manual, "the operator's row must have survived re-detection"
        assert cset["detectionConfidence"] is not None
        assert cset["confidenceCovers"] == len(cset["competitors"]) - len(manual)
        assert cset["confidenceCovers"] < len(cset["competitors"])


class TestAnsweredStatusUnit:
    """`_proof`'s definition of "answered" — the Epic 7.0 correction.

    These build rows directly rather than going through the stubbed scan
    runner, because the stub only ever produces OK results: it has no way to
    emit an `ANSWERED_NO_MENTION`, so no endpoint test can tell the two
    readings apart. Constructed here, which is the only way to negative-control
    it.
    """

    @staticmethod
    def _result(  # noqa: ANN205, PLR0913
        rid: str = "er1", pid: str = "p1", engine=Engine.CLAUDE,  # noqa: ANN001
        mentioned: bool = True, position: int | None = 1,
        mentions=(), citations=(), ok: bool = True,  # noqa: ANN001
    ):
        from avp_api.models import EngineResult
        from avp_api.models.engine_result import EngineResultStatus

        r = EngineResult(
            id=rid, scan_id="s1", prompt_id=pid, engine=engine,
            status=EngineResultStatus.OK if ok else EngineResultStatus.ERROR,
            mentioned=mentioned, position=position,
        )
        r.brand_mentions = list(mentions)
        r.citations = list(citations)
        return r

    @staticmethod
    def _mention(name: str, position: int | None, is_subject: bool = False, domain=None):  # noqa: ANN001, ANN205
        from avp_api.models import BrandMention

        return BrandMention(
            id=f"bm_{name}_{position}", engine_result_id="er1", entity_name=name,
            entity_domain=domain, is_subject=is_subject, position=position,
        )

    @staticmethod
    def _absent(rid: str = "er1", status=None):  # noqa: ANN001, ANN205
        """An answer that named a rival and cited a source, but not the subject."""
        from avp_api.models import BrandMention, Citation
        from avp_api.models.engine_result import CitationType, EngineResultStatus

        r = TestAnsweredStatusUnit._result(rid=rid, mentioned=False, position=None)
        r.status = status or EngineResultStatus.ANSWERED_NO_MENTION
        r.brand_mentions = [
            BrandMention(id=f"bm{rid}", engine_result_id=rid, entity_name="Zendesk",
                         entity_domain="zendesk.com", is_subject=False, position=1)
        ]
        r.citations = [
            Citation(id=f"c{rid}", engine_result_id=rid, source_domain="g2.com",
                     source_url="https://g2.com/x", source_type=CitationType.REVIEW,
                     position=1, cites_subject=False)
        ]
        return r

    def test_a_confirmed_absence_is_an_answer_not_a_failure(self) -> None:
        """The Epic 7.0 defect this corrects.

        `ANSWERED_NO_MENTION` means the engine answered and the subject was
        not in it. Folding it in with timeouts made the proof beat report a
        100% mention rate on a subject named in half the answers, and threw
        away the citations and rival mentions those answers carried — the most
        damning evidence the beat has. Invisible on the Help Scout fixture,
        which names the subject in all six of its answers.
        """
        from avp_api.models import Citation
        from avp_api.models.engine_result import CitationType, EngineResultStatus
        from avp_api.services.report import _proof

        rows = [
            self._result(
                rid=f"ok{i}", mentioned=True, position=1,
                mentions=[self._mention("Help Scout", 1, is_subject=True)],
                citations=[Citation(
                    id=f"cok{i}", engine_result_id=f"ok{i}", source_domain="g2.com",
                    source_url="https://g2.com/x", source_type=CitationType.REVIEW,
                    position=1, cites_subject=False,
                )],
            )
            for i in range(3)
        ]
        rows += [
            self._absent(rid=f"no{i}", status=EngineResultStatus.ANSWERED_NO_MENTION)
            for i in range(3)
        ]
        proof = _proof(rows, None)

        assert proof.answered_results == 6, "six engines answered"
        assert proof.results_mentioning_subject == 3, "three of them named the subject"
        coverage = proof.engine_coverage[0]
        assert (coverage.mentioned, coverage.answered) == (3, 6), (
            "'named 3 of 3' would claim a perfect mention rate on a subject named half the time"
        )
        # Three of these six citations come from answers the subject was absent
        # from. Under the old rule those three were discarded outright.
        assert proof.total_citations == 6, "an absent answer's citations are still evidence"
        zendesk = next(m for m in proof.mention_shares if m.entity_name == "Zendesk")
        assert zendesk.appearances == 3, (
            "the rival's mentions live ONLY in the answers the subject is missing from;"
            " under the old rule the rival did not appear in the report at all"
        )

    def test_a_real_failure_is_still_excluded(self) -> None:
        """The other half of the same rule — an error is not an answer, and
        widening the definition must not have swept timeouts in too."""
        from avp_api.models.engine_result import EngineResultStatus
        from avp_api.services.report import _proof

        for status in (EngineResultStatus.ERROR, EngineResultStatus.TIMEOUT,
                       EngineResultStatus.RATE_LIMITED):
            proof = _proof([self._absent(rid="e1", status=status)], None)
            assert proof.answered_results == 0, f"{status.value} is not an answer"
            assert proof.total_citations == 0, f"{status.value} carries no usable evidence"


class TestAnswerShelf:
    """Direction A of design-direction.md §5 — Epic 7.1.

    The aggregate tables answer "how often overall". These assert the thing
    they structurally cannot: in THIS question, who stood where, and was the
    subject there at all.
    """

    async def test_one_row_per_answer_in_prompt_then_engine_order(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        shelf = proof["promptShelf"]
        assert len(shelf) == proof["engineResults"], "every answer gets a row"
        keys = [(r["promptPosition"], r["engine"]) for r in shelf]
        assert keys == sorted(keys), "a report that reorders its own evidence is not a document"
        assert len(set(keys)) == len(keys), "one row per (prompt, engine), never two"

    async def test_the_row_label_is_our_own_generated_question(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """ip-safety.md #7 permits our own content. The prompt is ours — the
        same field `PromptOut.text` has returned since Epic 4."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        texts = {r["promptText"] for r in proof["promptShelf"]}
        assert texts == {f"question {i}" for i in range(4)}, "our generated prompts, verbatim"

    async def test_slots_carry_ordinals_and_names_and_nothing_else(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        slots = [s for row in proof["promptShelf"] for s in row["slots"]]
        assert slots, "the stub answer names the subject"
        for slot in slots:
            assert set(slot) == {
                "position", "entityName", "entityDomain", "isSubject",
                "competitorName", "cited",
            }
            assert slot["position"] >= 1

    async def test_slot_order_is_the_order_the_answer_named_them(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """The whole point of Direction A: an answer has slots, and who stands
        in front of whom is the argument.

        The stub answer names Zendesk before Help Scout, so with Zendesk in the
        competitor set the shelf must show the rival in slot 1 and the subject
        behind it.
        """
        await _sign_up(client)
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        stub_discovery(serp_domains=["zendesk.com"], cocit_brands=[("Zendesk", "zendesk.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        stub_engines(n_prompts=3)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        multi = [r for r in proof["promptShelf"] if len(r["slots"]) > 1]
        assert multi, "Zendesk must be detected in the answer for this to test anything"
        for row in multi:
            positions = [s["position"] for s in row["slots"]]
            assert positions == sorted(positions), "slots must render in answer order"
            assert row["slots"][0]["competitorName"] == "Zendesk"
            assert row["slots"][0]["isSubject"] is False
            subject_slot = next(s for s in row["slots"] if s["isSubject"])
            assert subject_slot["position"] > row["slots"][0]["position"]
            assert row["subjectPosition"] == subject_slot["position"]

    async def test_absence_is_an_answered_row_that_says_so_not_a_missing_row(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """Direction A draws absence as an explicit empty notch.

        The answer must still read as ANSWERED. A confirmed absence stored as
        `ANSWERED_NO_MENTION` that renders as "no answer" draws nothing at all,
        which is the one thing this visualisation must never do — the band of
        holes IS the finding.
        """
        await _sign_up(client)
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        stub_discovery(serp_domains=["zendesk.com"], cocit_brands=[("Zendesk", "zendesk.com")])
        await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        # A real answer that names a rival and never names the subject.
        stub_engines(n_prompts=3, text="Zendesk is popular and widely recommended.")
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        shelf = proof["promptShelf"]
        assert len(shelf) == proof["engineResults"], "absent rows are still rows"
        assert all(r["answered"] is True for r in shelf), (
            "the engine answered; it just did not name the subject"
        )
        assert all(r["subjectPresent"] is False for r in shelf)
        assert all(r["subjectPosition"] is None for r in shelf)
        # The rival is still on the shelf — that is what makes the hole legible.
        assert all(r["slots"] for r in shelf), "an absence row still shows who WAS named"
        assert all(r["slots"][0]["competitorName"] == "Zendesk" for r in shelf)
        assert all(not any(s["isSubject"] for s in r["slots"]) for r in shelf)

    async def test_presence_reads_the_authoritative_flag_not_the_slot_list(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """`subject_present` must never be derived from len(slots).

        A mention stored without an ordinal cannot take a slot, and deriving
        presence from the slot list would then render a false absence — telling
        a client they were not named in an answer that named them.
        """
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        for row in proof["promptShelf"]:
            if row["subjectPresent"]:
                assert row["subjectPosition"] is not None
        named = sum(1 for r in proof["promptShelf"] if r["subjectPresent"])
        assert named == proof["resultsMentioningSubject"], (
            "the shelf's presence count must equal the aggregate the tables report"
        )

    async def test_citation_presence_is_recorded_per_answer_not_per_scan(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """Direction A's anchor tick. Being named and being cited are two
        different facts, and the stub gives one engine citations and not the
        other — so a scan-wide flag would be wrong on half the rows."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        by_engine = {r["engine"]: r for r in proof["promptShelf"]}
        assert by_engine["claude_search"]["subjectCited"] is True, "this engine cites helpscout.com"
        assert by_engine["claude"]["subjectCited"] is False, "this one returns no citations"
        # And named-without-cited is representable, which is the interesting case.
        assert by_engine["claude"]["subjectPresent"] is True

    async def test_an_unanswered_result_is_not_an_absence(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """We did not get an answer to be absent from. `answered: false` lets
        the UI draw that differently from a notch."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        for row in proof["promptShelf"]:
            if not row["answered"]:
                assert row["slots"] == []
                assert row["subjectPresent"] is False

    async def test_the_shelf_is_capped_in_whole_prompts(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """design-direction.md §5 flags the cap itself. It keeps whole prompts:
        a row missing one of its engines reads as that engine not answering."""
        from avp_api.services.report import MAX_SHELF_PROMPTS

        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=MAX_SHELF_PROMPTS + 4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        shelf = proof["promptShelf"]
        prompt_ids = {r["promptId"] for r in shelf}
        assert len(prompt_ids) == MAX_SHELF_PROMPTS
        per_prompt = {pid: sum(1 for r in shelf if r["promptId"] == pid) for pid in prompt_ids}
        assert len(set(per_prompt.values())) == 1, "every kept prompt keeps all its engines"
        # And it keeps the FIRST prompts, in the operator's order.
        assert sorted(r["promptPosition"] for r in shelf)[-1] == MAX_SHELF_PROMPTS


class TestUnclaimedCitedDomains:
    """Direction C's deliverable — Epic 7.1.

    The heaviest domain that is cited and belongs to nobody in the scan. It is
    its own field because the evidence table's ranking and cap are tuned for a
    different job and would hide exactly this.
    """

    async def test_it_excludes_the_subject_and_every_detected_competitor(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        rows = proof["unclaimedCitedDomains"]
        assert rows, "the stub cites g2.com, which nobody in the scan owns"
        for row in rows:
            assert row["citesSubject"] is False
            assert row["competitorName"] is None
        assert "helpscout.com" not in {r["domain"] for r in rows}

    async def test_it_survives_the_evidence_table_s_cap(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        """The regression this field exists to prevent: the fix beat must not
        inherit a display cap's decision about what evidence fits."""
        from avp_api.services.report import MAX_CITED_DOMAINS

        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines, n=4)
        proof = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]

        assert len(proof["competitorCitedDomains"]) <= MAX_CITED_DOMAINS
        for row in proof["unclaimedCitedDomains"]:
            # Present regardless of whether the capped table happened to keep it.
            assert row["citations"] >= 1
        assert proof["unclaimedCitedDomains"], "the heaviest unclaimed domain is always carried"


class TestProofProjectionUnit:
    """`_proof` as a pure function over rows — Epic 7.1.

    The endpoint tests above run through the stubbed scan runner, which always
    writes a positioned mention whenever it writes a named subject. So they
    cannot exercise the case where those two facts disagree, and a presence
    rule derived from the slot list passes all of them. Constructed directly
    here, which is the only way to negative-control it.
    """

    @staticmethod
    def _prompt(pid: str = "p1", position: int = 1):  # noqa: ANN205
        from avp_api.models import Prompt

        return Prompt(
            id=pid, prompt_set_id="ps1", text=f"generated question {position}",
            intent=PromptIntent.COMPARISON, position=position,
        )

    @staticmethod
    def _result(  # noqa: ANN205, PLR0913
        rid: str = "er1", pid: str = "p1", engine=Engine.CLAUDE,  # noqa: ANN001
        mentioned: bool = True, position: int | None = 1,
        mentions=(), citations=(), ok: bool = True,  # noqa: ANN001
    ):
        from avp_api.models import EngineResult
        from avp_api.models.engine_result import EngineResultStatus

        r = EngineResult(
            id=rid, scan_id="s1", prompt_id=pid, engine=engine,
            status=EngineResultStatus.OK if ok else EngineResultStatus.ERROR,
            mentioned=mentioned, position=position,
        )
        r.brand_mentions = list(mentions)
        r.citations = list(citations)
        return r

    @staticmethod
    def _mention(name: str, position: int | None, is_subject: bool = False, domain=None):  # noqa: ANN001, ANN205
        from avp_api.models import BrandMention

        return BrandMention(
            id=f"bm_{name}_{position}", engine_result_id="er1", entity_name=name,
            entity_domain=domain, is_subject=is_subject, position=position,
        )

    def test_a_positionless_mention_never_renders_as_an_absence(self) -> None:
        """The defect this rule exists to prevent.

        `BrandMention.position` is nullable. A subject named in an answer but
        stored without an ordinal cannot take a slot on a shelf whose entire
        meaning is the order — and deriving presence from the slot list would
        then tell a client they were not named in an answer that named them.
        `mentioned` is the authoritative flag and the only correct source.
        """
        from avp_api.services.report import _proof

        result = self._result(mentions=[self._mention("Help Scout", None, is_subject=True)])
        proof = _proof([result], None, {"p1": self._prompt()})

        row = proof.prompt_shelf[0]
        assert row.slots == [], "no ordinal, no slot — an invented one would be a fabrication"
        assert row.subject_present is True, "but the absence is FALSE and must not be drawn"
        assert proof.results_mentioning_subject == 1

    def test_slots_keep_the_stored_ordinal_rather_than_re_indexing(self) -> None:
        """If a positionless mention were dropped and the rest renumbered, a
        brand named 4th would render as 3rd — a fact the answer did not state."""
        from avp_api.services.report import _proof

        result = self._result(
            mentions=[
                self._mention("Zendesk", 1),
                self._mention("Ghost", None),
                self._mention("Help Scout", 4, is_subject=True),
            ],
        )
        proof = _proof([result], None, {"p1": self._prompt()})

        row = proof.prompt_shelf[0]
        assert [s.position for s in row.slots] == [1, 4], "stored ordinals, not 1..n"
        assert [s.entity_name for s in row.slots] == ["Zendesk", "Help Scout"]

    def test_an_errored_answer_carries_no_slots_and_no_absence(self) -> None:
        from avp_api.services.report import _proof

        result = self._result(ok=False, mentioned=False, position=None)
        proof = _proof([result], None, {"p1": self._prompt()})

        row = proof.prompt_shelf[0]
        assert row.answered is False
        assert row.slots == []
        assert row.subject_present is False
        assert row.subject_cited is False

    def test_rows_survive_a_prompt_the_loader_could_not_resolve(self) -> None:
        """A result whose prompt row is missing is dropped from the shelf
        rather than crashing the whole report — the other beats still have
        evidence to show."""
        from avp_api.services.report import _proof

        proof = _proof([self._result(pid="gone")], None, {})
        assert proof.prompt_shelf == []
        assert proof.engine_results == 1, "the aggregate still counts it"

class TestUnclaimedRankingUnit:
    """The unclaimed list's ordering and floor, on data that can tell them apart.

    The stubbed scan cites exactly one unclaimed domain, so at the endpoint
    level every ordering and every threshold passes. Constructed here instead.
    """

    @staticmethod
    def _cited(domain: str, n: int, competitor_id: str | None = None, subject: bool = False):  # noqa: ANN205
        from avp_api.models import Citation
        from avp_api.models.engine_result import CitationType

        return [
            Citation(
                id=f"c_{domain}_{i}", engine_result_id="er1", source_domain=domain,
                source_url=f"https://{domain}/p{i}", source_type=CitationType.REVIEW,
                position=i, cites_subject=subject, competitor_id=competitor_id,
            )
            for i in range(1, n + 1)
        ]

    def _proof_for(self, citations):  # noqa: ANN001, ANN202
        from avp_api.services.report import _proof

        result = TestProofProjectionUnit._result(citations=citations)
        return _proof([result], None, {"p1": TestProofProjectionUnit._prompt()})

    def test_the_heaviest_unclaimed_domain_leads_regardless_of_attribution(self) -> None:
        """The evidence table ranks competitor-attributed domains first on
        purpose. That is right for proof and exactly wrong here: the whole
        finding is the domain nobody owns that is out-citing everyone."""
        proof = self._proof_for(
            self._cited("reddit.com", 6)
            + self._cited("blog.example", 4)
            + self._cited("helpscout.com", 5, subject=True)
        )

        rows = proof.unclaimed_cited_domains
        assert [r.domain for r in rows] == ["reddit.com", "blog.example"]
        assert [r.citations for r in rows] == [6, 4]
        # And the evidence table still orders itself the other way — unchanged.
        assert proof.competitor_cited_domains[0].domain == "reddit.com"

    def test_a_single_citation_is_not_a_recommendation(self) -> None:
        """One citation is a coincidence. Put to a client as a content gap it
        spends their trust on noise."""
        proof = self._proof_for(self._cited("reddit.com", 3) + self._cited("oneoff.example", 1))

        assert [r.domain for r in proof.unclaimed_cited_domains] == ["reddit.com"]
        # The one-off is still in the evidence table — it is real, just not a plan.
        assert "oneoff.example" in {r.domain for r in proof.competitor_cited_domains}

    def test_the_list_is_capped_so_the_fix_stays_a_plan(self) -> None:
        from avp_api.services.report import MAX_UNCLAIMED_DOMAINS

        citations = []
        for i in range(MAX_UNCLAIMED_DOMAINS + 3):
            citations += self._cited(f"site{i}.example", 10 - i)
        proof = self._proof_for(citations)

        assert len(proof.unclaimed_cited_domains) == MAX_UNCLAIMED_DOMAINS
        assert [r.citations for r in proof.unclaimed_cited_domains] == [10, 9, 8]

    def test_ties_break_on_the_domain_so_two_reads_never_reorder(self) -> None:
        proof = self._proof_for(self._cited("b.example", 4) + self._cited("a.example", 4))
        assert [r.domain for r in proof.unclaimed_cited_domains] == ["a.example", "b.example"]


class TestTheCrossEngineReading:
    """Where the engines disagree — Epic 9.23, end to end.

    The unit rules live in `test_divergence.py`. These assert the projection
    carries them out of a REAL scan: engines that answer differently produce a
    split, and an engine that fails produces nothing at all.
    """

    @pytest.fixture
    def disagreeing_engines(self, monkeypatch):  # noqa: ANN001, ANN201
        """One engine names the subject; another, answering the same prompt, does not."""
        def _install(*, failing: Engine | None = None):
            generated = [
                GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
                for i in range(4)
            ]

            async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
                return generated, "stub"

            async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
                out = []
                for engine in engines:
                    if engine is failing:
                        out.append(EngineAnswer(
                            engine=engine, engine_version="stub", prompt_text=prompt,
                            status=EngineResultStatus.TIMEOUT, error_code="TIMEOUT",
                            latency_ms=5))
                        continue
                    # chatgpt answers the same question without naming them.
                    names_subject = engine is not Engine.CHATGPT
                    out.append(EngineAnswer(
                        engine=engine, engine_version="stub", prompt_text=prompt,
                        text=(
                            "Zendesk is popular. Help Scout is simpler and well liked."
                            if names_subject
                            else "Zendesk is popular and Freshdesk is cheaper."
                        ),
                        latency_ms=5))
                return out

            async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
                return Sentiment.POSITIVE, Decimal("0.900")

            monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
            monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
            monkeypatch.setattr(
                scan_runner.extraction_service, "classify_sentiment", fake_sentiment
            )

        return _install

    async def test_a_real_scan_where_one_engine_omits_them_reports_every_prompt_as_a_split(
        self, client: AsyncClient, disagreeing_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "cross1@test.example")
        disagreeing_engines()
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        cross = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]["crossEngine"]

        assert cross["comparablePrompts"] == 4
        assert len(cross["splits"]) == 4, "every prompt was answered differently"
        for split in cross["splits"]:
            assert split["missedBy"] == ["chatgpt"]
            assert "claude" in split["namedBy"]
        assert cross["agreementRate"] == "0.00"

        rates = {s["engine"]: s["mentionRate"] for s in cross["standings"]}
        assert rates["claude"] == "100.00"
        assert rates["chatgpt"] == "0.00"

    async def test_an_engine_that_timed_out_is_in_no_split_and_costs_nothing(
        self, client: AsyncClient, disagreeing_engines
    ) -> None:  # noqa: ANN001
        """The rule that keeps an outage from becoming a visibility finding.

        chatgpt times out on every prompt. The two Claude engines agree, so
        there is no disagreement to report — and chatgpt must not appear on the
        missing side of one, because it never had an opinion.
        """
        await _sign_up(client, "cross2@test.example")
        disagreeing_engines(failing=Engine.CHATGPT)
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        cross = (await client.get(f"{BASE}/scans/{sid}/report")).json()["proof"]["crossEngine"]

        assert cross["splits"] == []
        assert cross["comparablePrompts"] == 4, "the two engines that answered are comparable"
        assert cross["agreementRate"] == "100.00"

        chatgpt = next(s for s in cross["standings"] if s["engine"] == "chatgpt")
        assert chatgpt["answered"] == 0
        # No sentiment invented for an engine that never answered.
        assert chatgpt["sentiment"] is None

    async def test_the_public_report_carries_the_same_reading(
        self, client: AsyncClient, disagreeing_engines
    ) -> None:  # noqa: ANN001
        """One projection, not two — the guarantee Epic 9.8 built the share path on."""
        await _sign_up(client, "cross3@test.example")
        disagreeing_engines()
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        private = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()
        public = (await client.get(f"{BASE}/reports/{token}")).json()

        assert public["proof"]["crossEngine"] == private["proof"]["crossEngine"]

"""GET /clients/{id}/answer-gaps — Epic B.

The endpoint behind the Answer gaps screen. It reads; it collects nothing and
writes nothing. These tests are mostly about the one distinction the whole
feature turns on — an answer that named a RIVAL instead of this client is a
gap, an answer that named NOBODY is not — and about the two claims the screen
must refuse to make when the data cannot support them.
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

# The four prompts every scan in this suite asks. Named rather than numbered so
# a failing assertion says which SITUATION broke, not which index.
COVERED = "covered question"
ABSENT = "absent question"
EMPTY = "empty question"
PARTIAL = "partial question"
PROMPTS = (COVERED, ABSENT, EMPTY, PARTIAL)


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """Stop after the engine phase. This suite is about the grid, not the chain."""
    return engines_only_executor


async def _sign_up(client: AsyncClient, email: str = "gaps@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Gaps Test Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Four prompts, each engineered to land in a different `GapKind`.

    The answers differ PER PROMPT, which the history suite's stub does not do —
    a stub that answered identically everywhere could not tell a grid apart
    from a single repeated row, and the grid is the thing under test.
    """

    def _install(
        *,
        subject: str = "Help Scout",
        rivals: tuple[str, ...] = ("Zendesk", "Freshdesk"),
        cite_subject_on: tuple[str, ...] = (COVERED,),
        partial_engine: Engine = Engine.CLAUDE_SEARCH,
    ):
        generated = [
            GeneratedPrompt(text=text, intent=list(PromptIntent)[i % 3])
            for i, text in enumerate(PROMPTS)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub"

        rival_text = " ".join(f"{r} is an option." for r in rivals)

        def _text(prompt: str, engine: Engine) -> str:
            if prompt == COVERED:
                return f"{subject} is simpler. {rival_text}"
            if prompt == ABSENT:
                # Rivals named, subject absent. THE gap.
                return rival_text
            if prompt == EMPTY:
                # No brand at all. Not a gap, and the distinction under test.
                return "It depends on your budget and your team size."
            # PARTIAL: named on every engine except one.
            return rival_text if engine is partial_engine else f"{subject} works well."

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            out = []
            for engine in engines:
                citations = [
                    CitedSource(url="https://g2.com/x", domain="g2.com", position=1)
                ]
                # The subject's OWN domain, on the prompts the caller chose.
                # `cites_subject` is derived from the domain by `extract_facts`.
                if prompt in cite_subject_on:
                    citations.append(
                        CitedSource(
                            url="https://helpscout.com/y",
                            domain="helpscout.com",
                            position=2,
                        )
                    )
                out.append(
                    EngineAnswer(
                        engine=engine,
                        engine_version="stub",
                        prompt_text=prompt,
                        text=_text(prompt, engine),
                        citations=citations,
                        latency_ms=5,
                    )
                )
            return out

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(
            scan_runner.extraction_service, "classify_sentiment", fake_sentiment
        )

    return _install


async def _client_id(client: AsyncClient, url: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": url, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _detect(client: AsyncClient, cid: str, stub_discovery, rivals: tuple[str, ...]) -> None:  # noqa: ANN001
    """Give the client a competitor set.

    Not optional here. `extract_facts` SEARCHES for the brands it is handed and
    discovers none, so without a detected set no rival leaves a `BrandMention`
    row, every ABSENT answer looks like NO_BRANDS, and the distinction this
    suite exists to test cannot arise. See `services/answer_gaps._mention_rows`.
    """
    stub_discovery(
        serp_domains=[f"{r.lower().replace(' ', '')}.com" for r in rivals],
        cocit_brands=[(r, f"{r.lower().replace(' ', '')}.com") for r in rivals],
    )
    resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
    assert resp.status_code == 201, resp.text


async def _run_scan(client: AsyncClient, cid: str) -> str:
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


async def _gaps(client: AsyncClient, cid: str, **params: str) -> dict:
    resp = await client.get(f"{BASE}/clients/{cid}/answer-gaps", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(body: dict, text: str) -> dict:
    return next(r for r in body["rows"] if r["text"] == text)


class TestTheDistinctionThatMattersMost:
    """An answer naming nobody is not an answer this client lost."""

    async def test_a_prompt_no_engine_answered_with_a_brand_is_not_a_gap(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        empty = _row(body, EMPTY)
        assert empty["kind"] == "no_brands"
        # It is NOT absent, even though the client is just as unnamed here as
        # in the ABSENT row. The difference is whether anyone else won it.
        assert empty["rivalsNamedOn"] == 0
        assert body["noBrands"] == 1

    async def test_a_prompt_a_rival_won_is_a_gap(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        absent = _row(body, ABSENT)
        assert absent["kind"] == "absent"
        assert absent["subjectNamedOn"] == 0
        assert absent["rivalsNamedOn"] > 0
        assert body["absent"] == 1

    async def test_the_two_are_never_summed_into_one_figure(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """The regression that would quietly double a client's reported gaps."""
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        assert body["absent"] == 1
        assert body["noBrands"] == 1
        # Both rows have the client absent on every engine. Only one is a gap.
        assert _row(body, ABSENT)["absentOn"] == _row(body, EMPTY)["absentOn"]
        assert _row(body, ABSENT)["kind"] != _row(body, EMPTY)["kind"]


class TestClaimsTheDataMustNotSupport:
    async def test_no_citation_gap_is_reported_when_the_domain_is_never_cited(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """Without evidence the domain is citable, "you have content but it was
        not cited" is a claim about content this schema does not store."""
        await _sign_up(client)
        stub_engines(cite_subject_on=())
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        assert body["subjectCitable"] is False
        assert body["uncited"] == 0
        assert all(r["kind"] != "uncited" for r in body["rows"])
        # The covered prompt is still COVERED — it was named everywhere.
        assert _row(body, COVERED)["kind"] == "covered"

    async def test_a_citation_gap_is_reported_once_the_domain_proves_citable(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        # Cited on COVERED only, so PARTIAL/ABSENT/EMPTY have no owned citation.
        stub_engines(cite_subject_on=(COVERED,))
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        assert body["subjectCitable"] is True
        assert _row(body, COVERED)["subjectCited"] is True
        assert _row(body, COVERED)["kind"] == "covered"

    async def test_a_client_with_no_scans_is_null_rather_than_an_error(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _client_id(client)
        resp = await client.get(f"{BASE}/clients/{cid}/answer-gaps")
        assert resp.status_code == 200
        assert resp.json() is None


class TestRecurrence:
    async def test_absent_on_counts_engines_because_prompts_do_not_recur(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """Prompt text is regenerated every scan, so recurrence is per engine."""
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        engines = len(body["engines"])
        assert engines >= 2, "the recurrence axis needs more than one engine"

        assert _row(body, ABSENT)["absentOn"] == engines
        assert _row(body, COVERED)["absentOn"] == 0
        # The point of the axis: a gap on SOME engines ranks below one on all.
        partial = _row(body, PARTIAL)
        assert partial["kind"] == "partial"
        assert 0 < partial["absentOn"] < engines

    async def test_every_row_totals_to_the_engines_that_answered(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        for row in (await _gaps(client, cid))["rows"]:
            assert row["subjectNamedOn"] + row["absentOn"] == row["enginesAnswered"]

    async def test_the_kind_totals_sum_to_the_prompt_count(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """Every prompt lands in exactly one bucket — no double-count, no drop."""
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        buckets = ("absent", "partial", "uncited", "covered", "noBrands", "unanswered")
        assert sum(body[k] for k in buckets) == body["prompts"] == len(body["rows"])

    async def test_rivals_are_counted_across_scans_because_they_persist(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """The cross-scan half of recurrence, on the axis that survives."""
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        # Detected ONCE PER SCAN, because a `CompetitorSet` belongs to a scan.
        # The real chain does this itself as phase 3, before the engine loop
        # (`scan_executor`: "competitor detection comes FIRST"); the cheap
        # engines-only executor this suite uses skips that phase, so a second
        # scan would otherwise run with no rivals and this would assert against
        # a fixture artefact rather than against the rollup.
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        assert body["rivals"], "a rival won answers and must be reported"
        top = body["rivals"][0]
        assert top["scansPresent"] == 2, "counted across both scans"
        assert top["answersWon"] >= 2
        # The subject never appears among its own rivals.
        assert all(r["name"] != "Help Scout" for r in body["rivals"])


class TestGridShape:
    async def test_the_subject_is_always_the_first_column(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """Even outranked, the client must not drop out of its own grid."""
        await _sign_up(client)
        # Rivals win three prompts to the subject's one, so ranking alone would
        # push the subject down.
        stub_engines(rivals=("Zendesk", "Freshdesk", "Intercom"))
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk", "Intercom"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        assert body["brands"][0]["isSubject"] is True
        assert sum(b["isSubject"] for b in body["brands"]) == 1

    async def test_every_row_has_a_cell_for_every_column(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        body = await _gaps(client, cid)
        names = [b["name"] for b in body["brands"]]
        for row in body["rows"]:
            assert [c["brand"] for c in row["cells"]] == names

    async def test_a_specific_scan_can_be_selected_and_others_are_listed(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        first = await _run_scan(client, cid)
        second = await _run_scan(client, cid)

        latest = await _gaps(client, cid)
        assert latest["scanId"] == second, "newest by default"
        assert latest["availableScanIds"] == [second, first]

        picked = await _gaps(client, cid, scanId=first)
        assert picked["scanId"] == first


class TestTenancy:
    async def test_another_agency_s_client_is_not_found(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client, "owner@test.example")
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        await client.post(f"{BASE}/auth/sign-out")
        await _sign_up(client, "stranger@test.example")
        resp = await client.get(f"{BASE}/clients/{cid}/answer-gaps")
        # 404 rather than 403 — the endpoint must not confirm the id exists.
        assert resp.status_code == 404

    async def test_a_scan_id_from_another_client_yields_nothing(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        stub_engines()
        mine = await _client_id(client)
        await _detect(client, mine, stub_discovery, ("Zendesk", "Freshdesk"))
        other = await _client_id(client, "example.com")
        await _run_scan(client, mine)
        other_scan = await _run_scan(client, other)

        resp = await client.get(
            f"{BASE}/clients/{mine}/answer-gaps", params={"scanId": other_scan}
        )
        assert resp.status_code == 200
        assert resp.json() is None


class TestIpSafety:
    async def test_the_response_carries_no_engine_answer_text(
        self, client: AsyncClient, stub_engines, stub_discovery
    ) -> None:  # noqa: ANN001
        """The facts-only rule. The only free text here is the prompt, which is ours.

        The stub's answers contain a phrase that appears nowhere in any prompt,
        so if any field ever started echoing an answer this fails.
        """
        await _sign_up(client)
        stub_engines()
        cid = await _client_id(client)
        await _detect(client, cid, stub_discovery, ("Zendesk", "Freshdesk"))
        await _run_scan(client, cid)

        resp = await client.get(f"{BASE}/clients/{cid}/answer-gaps")
        raw = resp.text
        for phrase in ("is an option", "is simpler", "It depends on your budget"):
            assert phrase not in raw, f"answer text leaked into the response: {phrase!r}"
        # Our own prompt text IS present, and that is the permitted case.
        assert COVERED in raw

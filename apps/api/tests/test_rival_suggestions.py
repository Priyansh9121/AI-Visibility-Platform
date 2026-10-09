"""GET /clients/{clientId}/rival-suggestions: a probable rival, suggested as a rival — F2.

The founder's decision: a domain with the client's own restricted ending (edu.au for rmit.edu.au)
that the scan's answers named a brand for ("Deakin University") is a probable rival. "Get onto"
skips it (`test_probable_rivals_report.py`); the client's Competitors page suggests it, as
"deakin.edu.au: cited 6 times, named in 4 answers. Add it as a rival?", and the agency adds it
through the existing `PUT /clients/{clientId}/competitors`. This route is where the page reads the
suggestions. Held here:

* the founder's own scan, through the real pipeline, gives exactly the suggestion
  `probable-rival.json` holds (the web prints its sentence from the same data), and qilt.edu.au and
  uac.edu.au, cited and never named, are not suggested;
* who is suggested: a domain the latest scan that recorded the fact had named in at least one
  answer, not CURRENTLY claimed (the client's own domain and confirmed sites, an active rival's
  domain and other sites); a struck rival is claimed by nobody and is suggested again; the name to
  add it with is its label (capitals for two to four letters);
* from which scan: the newest finished scan that recorded `named_domains`; NULL, `{}`, a client
  with no scan and a client with no restricted ending have none;
* the whole loop: adding the suggestion through the PUT is what makes it stop being suggested;
* scoped as the competitors routes are (another agency's client is the same 404 as an unknown one;
  signed out is a 401), and it reads: it writes nothing (`test_reads_are_reads.py` takes it too).
"""

from __future__ import annotations

import itertools
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs
from ulid import ULID

from avp_api import ids
from avp_api.models import (
    Citation,
    CitationType,
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
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

BASE = "/api/v1"
FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
FIXTURE: dict[str, Any] = json.loads((FIXTURES / "probable-rival.json").read_text(encoding="utf-8"))
CLIENT_NAME, CLIENT_DOMAIN = FIXTURE["client"]["name"], FIXTURE["client"]["domain"]
ENGINES = {"engines": ["claude_search", "chatgpt"]}
NOT_FOUND = "No client with that identifier."

# The founder's example (see `test_probable_rivals_report.py`, which holds the same plan).
RMIT_PLAN: dict[str, tuple[str, tuple[str, ...]]] = {
    "which universities offer design": (
        "Deakin University and RMIT University are both good choices.",
        ("deakin.edu.au", "qilt.edu.au", "uac.edu.au", "rmit.edu.au"),
    ),
    "where to study teaching": (
        "Consider Deakin University for teaching.",
        ("deakin.edu.au", "qilt.edu.au"),
    ),
    "best for engineering": (
        "RMIT University is strong in engineering.",
        ("deakin.edu.au",),
    ),
}


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """A scan started over HTTP stops after the engines: the chained phases are not under test."""
    return engines_only_executor


# --- building blocks ----------------------------------------------------------------------------

# A scan's id is a ULID, and "the latest scan" is the highest id. Two ULIDs minted in one
# millisecond are in no order, so the scans these tests add are given strictly rising ones.
_clock = itertools.count(int(time.time()) + 10_000)


async def sign_up(client: AsyncClient, email: str = "suggest@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Suggestions Agency", "fullName": "Operator",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def make_client(
    client: AsyncClient, domain: str = CLIENT_DOMAIN, name: str = CLIENT_NAME
) -> str:
    resp = await client.post(
        f"{BASE}/clients", json={"url": domain, "name": name, "classify": False}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def add_scan(
    session: AsyncSession, client_id: str, named: dict[str, int] | None,
    status: ScanStatus = ScanStatus.SUCCEEDED, cited: dict[str, int] | None = None,
) -> str:
    """A scan the client has, with `named_domains` as given and, if `cited`, an answered result
    that cites each domain that many times. (An answered result is what makes a scan "finished".)"""
    owner = (
        await session.execute(select(Client.agency_id).where(Client.id == client_id))
    ).scalar_one()
    scan = Scan(
        id=f"{ids.SCAN}_{ULID.from_timestamp(next(_clock))}",
        client_id=client_id, agency_id=owner, status=status, named_domains=named,
    )
    session.add(scan)
    await session.commit()
    await cite(session, scan.id, cited if cited is not None else {"filler.example": 1})
    return str(scan.id)


async def cite(
    session: AsyncSession, scan_id: str, counts: dict[str, int],
    status: EngineResultStatus = EngineResultStatus.ANSWERED_NO_MENTION,
    engine: Engine = Engine.CLAUDE_SEARCH,
) -> None:
    """One result of the scan (an engine's answer to its prompt) citing each domain that often.
    A prompt has one result per engine, so a second call for a scan names another engine."""
    pset = (
        await session.execute(select(PromptSet.id).where(PromptSet.scan_id == scan_id))
    ).scalar_one_or_none()
    if pset is None:
        pset = ids.new_id(ids.PROMPT_SET)
        session.add(PromptSet(id=pset, scan_id=scan_id))
        await session.flush()
        session.add(Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=pset, text="best university?",
            intent=PromptIntent.AWARENESS, position=1,
        ))
        await session.flush()
    prompt_id = (
        await session.execute(select(Prompt.id).where(Prompt.prompt_set_id == pset))
    ).scalars().first()
    result = EngineResult(
        id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan_id, prompt_id=prompt_id,
        engine=engine, status=status,
    )
    session.add(result)
    await session.flush()
    for domain, count in counts.items():
        for n in range(count):
            session.add(Citation(
                id=ids.new_id(ids.CITATION), engine_result_id=result.id,
                source_domain=domain, source_url=f"https://{domain}/page-{n}",
                source_type=CitationType.OTHER, position=n + 1,
            ))
    await session.commit()


async def add_set(
    session: AsyncSession, scan_id: str, *rivals: tuple[str, str],
    sites: dict[str, list[str]] | None = None, struck: tuple[str, ...] = (),
) -> str:
    """A scan's own set of rivals (name, domain), each with the other sites `sites` gives it."""
    cset = CompetitorSet(
        id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan_id, status=DetectionStatus.OK,
        detection_confidence=Decimal("1"), serp_queries_run=6, candidates_considered=9,
        detected_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(cset)
    await session.flush()
    for rank, (name, domain) in enumerate(rivals, start=1):
        session.add(Competitor(
            id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=name, domain=domain,
            rank=rank, detection_source=DetectionSource.BOTH, signal_count=7, serp_mentions=4,
            co_citation_mentions=3, corroborated=True,
            other_sites=list((sites or {}).get(domain, [])), is_suppressed=domain in struck,
        ))
    await session.commit()
    return str(cset.id)


def fake_engines(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, tuple[str, tuple[str, ...]]]
) -> None:
    generated = [
        GeneratedPrompt(text=question, intent=list(PromptIntent)[n % 3])
        for n, question in enumerate(plan)
    ]

    async def generate(**kwargs: Any):  # noqa: ANN202, ARG001
        return generated, "stub-generator"

    async def ask_all(prompt: str, *, engines: Any, settings: Any):  # noqa: ANN202, ARG001
        body, cited = plan[prompt]
        return [
            EngineAnswer(
                engine=e, engine_version="stub", prompt_text=prompt, text=body, latency_ms=5,
                citations=[
                    CitedSource(url=f"https://{domain}/page", domain=domain, position=n)
                    for n, domain in enumerate(cited, start=1)
                ],
            )
            for e in engines
        ]

    async def sentiment(answer: Any, *, subject_name: str, settings: Any = None):  # noqa: ANN202, ARG001
        return Sentiment.POSITIVE, Decimal("0.900")

    monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", generate)
    monkeypatch.setattr(scan_runner.engine_service, "ask_all", ask_all)
    monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", sentiment)


async def run_scan(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any],
    name: str = CLIENT_NAME, domain: str = CLIENT_DOMAIN,
) -> str:
    """Sign up, add the client, run a scan through the real pipeline. Returns the client id."""
    await sign_up(client)
    cid = await make_client(client, domain, name)
    fake_engines(monkeypatch, plan)
    started = await client.post(f"{BASE}/clients/{cid}/scans", json=ENGINES)
    assert started.status_code == 202, started.text
    return cid


async def suggestions(client: AsyncClient, cid: str) -> list[dict[str, Any]]:
    resp = await client.get(f"{BASE}/clients/{cid}/rival-suggestions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"suggestions"}
    out: list[dict[str, Any]] = body["suggestions"]
    return out


def domains_of(found: list[dict[str, Any]]) -> list[str]:
    return [s["domain"] for s in found]


async def put_rivals(client: AsyncClient, cid: str, *rivals: tuple[str, str | None]) -> Any:
    return await client.put(
        f"{BASE}/clients/{cid}/competitors",
        json={"competitors": [{"name": name, "domain": domain} for name, domain in rivals]},
    )


# ===== the founder's scan =====================================================================


class TestTheFoundersSuggestion:
    async def test_the_founders_scan_suggests_deakin_with_its_numbers(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = await run_scan(client, monkeypatch, RMIT_PLAN)

        assert await suggestions(client, cid) == [FIXTURE["suggestion"]]

    async def test_qilt_and_uac_were_cited_and_never_named_so_they_are_not_suggested(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = await run_scan(client, monkeypatch, RMIT_PLAN)
        found = await suggestions(client, cid)
        assert "qilt.edu.au" not in domains_of(found) and "uac.edu.au" not in domains_of(found)

    async def test_the_answer_is_facts_only_a_domain_a_name_and_two_counts(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = await run_scan(client, monkeypatch, RMIT_PLAN)
        (only,) = await suggestions(client, cid)
        assert set(only) == {"domain", "name", "citations", "answersNaming"}
        assert isinstance(only["citations"], int) and isinstance(only["answersNaming"], int)

    async def test_adding_it_through_the_put_is_what_ends_the_suggestion(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole loop the Competitors page makes: read, add (the current list plus this one),
        read again. The page sends the suggestion's own `name` and `domain`."""
        cid = await run_scan(client, monkeypatch, RMIT_PLAN)
        (suggestion,) = await suggestions(client, cid)

        first = await put_rivals(client, cid, ("Monash University", "monash.edu"))
        assert first.status_code == 200, first.text
        assert domains_of(await suggestions(client, cid)) == ["deakin.edu.au"]

        added = await put_rivals(
            client, cid, ("Monash University", "monash.edu"),
            (suggestion["name"], suggestion["domain"]),
        )
        assert added.status_code == 200, added.text
        assert [c["name"] for c in added.json()["competitors"]] == ["Monash University", "Deakin"]
        assert await suggestions(client, cid) == []

    async def test_a_rival_struck_again_is_suggested_again(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = await run_scan(client, monkeypatch, RMIT_PLAN)
        (suggestion,) = await suggestions(client, cid)
        await put_rivals(client, cid, (suggestion["name"], suggestion["domain"]))
        assert await suggestions(client, cid) == []

        struck = await put_rivals(client, cid)  # an empty list strikes every rival
        assert struck.status_code == 200, struck.text
        assert domains_of(await suggestions(client, cid)) == ["deakin.edu.au"]


# ===== who is suggested =======================================================================


class TestWhoIsSuggested:
    async def test_a_name_is_capitals_for_two_to_four_letters_else_capitalised(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        named = {"unsw.edu.au": 2, "uts.edu.au": 1, "deakin.edu.au": 3, "latrobe.edu.au": 1}
        await add_scan(session, cid, named, cited=dict.fromkeys(named, 2))

        found = await suggestions(client, cid)

        assert {s["domain"]: s["name"] for s in found} == {
            "unsw.edu.au": "UNSW", "uts.edu.au": "UTS", "deakin.edu.au": "Deakin",
            "latrobe.edu.au": "Latrobe",
        }

    async def test_most_cited_first_then_by_domain(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        named = {"b.edu.au": 1, "a.edu.au": 3, "c.edu.au": 2, "d.edu.au": 1}
        await add_scan(
            session, cid, named, cited={"a.edu.au": 5, "b.edu.au": 5, "c.edu.au": 9, "d.edu.au": 1}
        )

        found = await suggestions(client, cid)

        assert [(s["domain"], s["citations"]) for s in found] == [
            ("c.edu.au", 9), ("a.edu.au", 5), ("b.edu.au", 5), ("d.edu.au", 1),
        ]
        assert [s["answersNaming"] for s in found] == [2, 3, 1, 1]

    async def test_a_domain_no_answer_named_is_not_suggested(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, {"qilt.edu.au": 0, "uac.edu.au": 0},
                       cited={"qilt.edu.au": 4, "uac.edu.au": 2})
        assert await suggestions(client, cid) == []

    async def test_citations_are_the_scans_answered_results_only(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The same counting the report's list uses: an engine that failed cited no one."""
        await sign_up(client)
        cid = await make_client(client)
        sid = await add_scan(session, cid, {"deakin.edu.au": 2}, cited={"deakin.edu.au": 3})
        await cite(
            session, sid, {"deakin.edu.au": 5}, status=EngineResultStatus.ERROR,
            engine=Engine.CHATGPT,
        )

        (only,) = await suggestions(client, cid)

        assert only["citations"] == 3

    async def test_a_domain_cited_by_no_answered_result_has_no_line_to_say(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """"cited 0 times" is not a suggestion anyone should be shown."""
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, {"deakin.edu.au": 2}, cited={"other.example": 3})
        assert await suggestions(client, cid) == []

    async def test_a_com_client_has_no_probable_rival(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = await run_scan(client, monkeypatch, RMIT_PLAN, "Help Scout", "helpscout.com")
        assert await suggestions(client, cid) == []

    async def test_deakin_com_for_an_edu_au_client_is_not_suggested(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = {"which universities": ("Deakin University is a good choice.", ("deakin.com",))}
        cid = await run_scan(client, monkeypatch, plan)
        assert await suggestions(client, cid) == []

    async def test_a_client_with_no_scan_has_none(self, client: AsyncClient) -> None:
        await sign_up(client)
        assert await suggestions(client, await make_client(client)) == []

    async def test_a_scan_that_recorded_nothing_has_none_and_so_has_an_empty_measurement(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, None, cited={"deakin.edu.au": 6})
        assert await suggestions(client, cid) == []
        await add_scan(session, cid, {}, cited={"deakin.edu.au": 6})
        assert await suggestions(client, cid) == []


# ===== not currently claimed ==================================================================


class TestNotCurrentlyClaimed:
    async def _scan_naming(self, session: AsyncSession, cid: str, *domains: str) -> str:
        return await add_scan(
            session, cid, dict.fromkeys(domains, 2), cited=dict.fromkeys(domains, 3)
        )

    async def test_a_current_rivals_domain_is_not_suggested(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        sid = await self._scan_naming(session, cid, "deakin.edu.au", "unsw.edu.au")
        await add_set(session, sid, ("Deakin University", "deakin.edu.au"))

        assert domains_of(await suggestions(client, cid)) == ["unsw.edu.au"]

    async def test_the_list_read_is_the_one_the_next_scan_will_use(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A save is ahead of the scan that ran: adding UNSW by hand ends its suggestion at once,
        and replacing the list so that it no longer holds Deakin suggests Deakin again."""
        await sign_up(client)
        cid = await make_client(client)
        sid = await self._scan_naming(session, cid, "deakin.edu.au", "unsw.edu.au")
        await add_set(session, sid, ("Deakin University", "deakin.edu.au"))
        assert domains_of(await suggestions(client, cid)) == ["unsw.edu.au"]

        saved = await put_rivals(client, cid, ("UNSW", "unsw.edu.au"))

        assert saved.status_code == 200, saved.text
        assert domains_of(await suggestions(client, cid)) == ["deakin.edu.au"]

    async def test_a_struck_rival_is_nobodys_and_is_suggested(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        sid = await self._scan_naming(session, cid, "deakin.edu.au")
        await add_set(session, sid, ("Deakin University", "deakin.edu.au"),
                      struck=("deakin.edu.au",))

        assert domains_of(await suggestions(client, cid)) == ["deakin.edu.au"]

    async def test_a_rivals_confirmed_other_site_is_not_suggested(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """monash.edu.au is Monash University's other site, once the agency says so; it is not a
        new rival, whatever the answers named."""
        await sign_up(client)
        cid = await make_client(client)
        sid = await self._scan_naming(session, cid, "monash.edu.au", "deakin.edu.au")
        await add_set(session, sid, ("Monash University", "monash.edu"),
                      sites={"monash.edu": ["monash.edu.au"]})

        assert domains_of(await suggestions(client, cid)) == ["deakin.edu.au"]

    async def test_a_rivals_other_site_that_was_struck_with_it_is_claimed_by_nobody(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        sid = await self._scan_naming(session, cid, "monash.edu.au")
        await add_set(session, sid, ("Monash University", "monash.edu"),
                      sites={"monash.edu": ["monash.edu.au"]}, struck=("monash.edu",))

        assert domains_of(await suggestions(client, cid)) == ["monash.edu.au"]

    async def test_the_clients_own_confirmed_site_and_own_domain_are_not_suggested(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Neither can be in the scan's map by construction; the claim is made all the same, so a
        hand-edited row cannot make the page offer a client its own site as a rival."""
        await sign_up(client)
        cid = await make_client(client)
        await self._scan_naming(session, cid, "deakin.edu.au", "rmit.edu.au", "unsw.edu.au")
        await session.execute(
            text("UPDATE clients SET other_sites = CAST(:v AS jsonb) WHERE id = :id"),
            {"v": json.dumps(["unsw.edu.au"]), "id": cid},
        )
        await session.commit()

        assert domains_of(await suggestions(client, cid)) == ["deakin.edu.au"]

    async def test_a_domain_is_claimed_in_whatever_spelling_it_is_held(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        sid = await self._scan_naming(session, cid, "xn--bcher-kva.edu.au")
        await add_set(session, sid, ("Bücher", "bücher.edu.au"))

        assert await suggestions(client, cid) == []


# ===== which scan =============================================================================


class TestWhichScan:
    async def test_the_newest_finished_scan_that_recorded_the_fact_is_the_one_read(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, {"old.edu.au": 5}, cited={"old.edu.au": 9})
        await add_scan(session, cid, {"newer.edu.au": 2}, cited={"newer.edu.au": 4})

        assert await suggestions(client, cid) == [
            {"domain": "newer.edu.au", "name": "Newer", "citations": 4, "answersNaming": 2}
        ]

    async def test_a_later_scan_that_measured_nothing_named_replaces_an_earlier_one(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, {"old.edu.au": 5}, cited={"old.edu.au": 9})
        await add_scan(session, cid, {}, cited={"old.edu.au": 9})

        assert await suggestions(client, cid) == []

    async def test_a_later_scan_that_recorded_nothing_leaves_the_earlier_one_in_force(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """NULL is "not recorded", not "none": the last measurement still stands."""
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, {"old.edu.au": 5}, cited={"old.edu.au": 9})
        await add_scan(session, cid, None, cited={"old.edu.au": 9})

        assert domains_of(await suggestions(client, cid)) == ["old.edu.au"]

    @pytest.mark.parametrize(
        "status", [ScanStatus.FAILED, ScanStatus.RUNNING, ScanStatus.QUEUED, ScanStatus.CANCELLED]
    )
    async def test_a_scan_that_is_not_finished_is_not_read(
        self, client: AsyncClient, session: AsyncSession, status: ScanStatus
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, {"old.edu.au": 5}, cited={"old.edu.au": 9})
        await add_scan(session, cid, {"new.edu.au": 2}, status=status, cited={"new.edu.au": 4})

        assert domains_of(await suggestions(client, cid)) == ["old.edu.au"]

    async def test_a_partial_scan_is_finished(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        cid = await make_client(client)
        await add_scan(session, cid, {"new.edu.au": 2}, status=ScanStatus.PARTIAL,
                       cited={"new.edu.au": 4})
        assert domains_of(await suggestions(client, cid)) == ["new.edu.au"]

    async def test_another_clients_scan_is_never_read(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client)
        mine = await make_client(client)
        theirs = await make_client(client, "monash.edu.au", "Monash")
        await add_scan(session, theirs, {"deakin.edu.au": 4}, cited={"deakin.edu.au": 9})

        assert await suggestions(client, mine) == []
        assert domains_of(await suggestions(client, theirs)) == ["deakin.edu.au"]


# ===== scope ==================================================================================


class TestScope:
    async def test_another_agencys_client_is_the_same_404_as_an_unknown_one(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await sign_up(client, "owner@suggest.example")
        cid = await make_client(client)
        await add_scan(session, cid, {"deakin.edu.au": 4}, cited={"deakin.edu.au": 9})
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await sign_up(other, "intruder@suggest.example")
            unknown = f"{ids.CLIENT}_{ULID()}"
            seen = {
                "theirs": await other.get(f"{BASE}/clients/{cid}/rival-suggestions"),
                "unknown": await other.get(f"{BASE}/clients/{unknown}/rival-suggestions"),
                "malformed": await other.get(f"{BASE}/clients/not-an-id/rival-suggestions"),
            }

        assert {k: v.status_code for k, v in seen.items()} == dict.fromkeys(seen, 404)
        assert {v.json()["detail"] for v in seen.values()} == {NOT_FOUND}
        assert "deakin" not in seen["theirs"].text

    async def test_signed_out_is_a_401(self, client: AsyncClient) -> None:
        await sign_up(client)
        cid = await make_client(client)
        await client.post(f"{BASE}/auth/logout")

        resp = await client.get(f"{BASE}/clients/{cid}/rival-suggestions")

        assert resp.status_code == 401

    async def test_asking_writes_nothing(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = await run_scan(client, monkeypatch, RMIT_PLAN)

        async def snapshot() -> dict[str, list[Any]]:
            await session.rollback()
            out: dict[str, list[Any]] = {}
            for table in ("scans", "clients", "competitor_sets", "competitors"):
                out[table] = [
                    tuple(row) for row in (await session.execute(
                        text(f'SELECT xmin::text, xmax::text, * FROM "{table}" ORDER BY id')
                    )).all()
                ]
            await session.rollback()
            return out

        before = await snapshot()
        for _ in range(3):
            assert len(await suggestions(client, cid)) == 1
        assert await snapshot() == before

    async def test_asking_logs_no_domain_and_no_name(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = await run_scan(client, monkeypatch, RMIT_PLAN)
        with capture_logs() as logs:
            await suggestions(client, cid)
        assert "deakin" not in repr(logs).lower()

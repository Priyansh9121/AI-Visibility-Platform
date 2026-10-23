"""GET and PUT /clients/{clientId}/other-sites: a brand's other sites (M1, task 1.1).

A domain that carries a brand's name under another public suffix (rmit.edu.vn for rmit.edu.au) is
PROPOSED as the brand's other site and counts as the brand's own only once the agency CONFIRMS it.
Task 1.1 builds the lists and the route; nothing here makes extraction or scoring read them. The
tests hold: that proposing writes nothing; what GET lists (the confirmed sites of the client and of
each active rival of the list the client's next scan will use, the proposals of the latest finished
scan, the dismissed domains); what PUT refuses (one plain sentence naming the domain, nothing
written); that a rival's sites live on the rows of the client's NEXT list and never on a scan's own
set; that those sites are carried wherever a rival's row is copied or rewritten; that a list over
its cap is refused before any entry of it is read; and that one host in two spellings
(`bücher.de`, `xn--bcher-kva.de`) is one host everywhere a domain is compared, while a domain is
stored in the spelling it was given.
"""

from __future__ import annotations

import itertools
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
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
    User,
    UserRole,
)
from avp_api.services import competitors as detection
from avp_api.services import other_sites, scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

BASE = "/api/v1"
CLIENT_DOMAIN = "rmit.edu.au"
CLIENT_NAME = "RMIT University"
MONASH = ("Monash University", "monash.edu")
DEAKIN = ("Deakin University", "deakin.edu.au")
RUNNING = "A scan of this client is running. Change its other sites after it finishes."
VALIDATION = "/problems/validation-failed"

# Twenty suffixes of the Public Suffix List, none of them `edu.au`: `rmit.<suffix>` is the name
# `rmit` under another ending, twenty ways. (`edu.nz` and `edu.th` are not on the list.)
TWENTY_SUFFIXES = [
    "edu.vn", "edu.my", "edu.sg", "edu.hk", "edu.cn", "edu.in", "edu.ph", "edu.tw", "ac.uk",
    "ac.nz", "com.sg", "com.hk", "com.vn", "co.uk", "co.nz", "de", "fr", "nl", "ac.th", "edu.pk",
]


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """A scan started over HTTP stops after the engines: the chained phases are not under test."""
    return engines_only_executor


# --- building blocks ----------------------------------------------------------------------------

# A scan's id is a ULID, and "the latest scan" is the highest id. Two ULIDs minted in one
# millisecond are in no order, so the scans these tests add are given strictly rising ones.
_clock = itertools.count(int(time.time()) + 10_000)


async def _versioned(client: AsyncClient, cid: str, body: dict[str, object]) -> dict[str, object]:
    """`body` as the web sends it: naming the version of the list it has just read.

    None when the client has no list yet, which is what a writer that read none sends.
    """
    read = await client.get(f"{BASE}/clients/{cid}/competitors")
    return {**body, "version": read.json()["version"] if read.status_code == 200 else None}


async def _sign_up(client: AsyncClient, email: str = "sites@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Other Sites Agency", "fullName": "Operator",
            "email": email, "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _make_client(
    client: AsyncClient, domain: str = CLIENT_DOMAIN, name: str = CLIENT_NAME
) -> str:
    resp = await client.post(
        f"{BASE}/clients", json={"url": domain, "name": name, "classify": False}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_scan(
    session: AsyncSession, client_id: str, status: ScanStatus = ScanStatus.SUCCEEDED
) -> str:
    owner = (
        await session.execute(select(Client.agency_id).where(Client.id == client_id))
    ).scalar_one()
    scan = Scan(
        id=f"{ids.SCAN}_{ULID.from_timestamp(next(_clock))}",
        client_id=client_id, agency_id=owner, status=status,
    )
    session.add(scan)
    await session.commit()
    return scan.id


async def _add_set(
    session: AsyncSession,
    scan_id: str,
    *rivals: tuple[str, str],
    sites: dict[str, list[str]] | None = None,
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
        session.add(
            Competitor(
                id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=name,
                domain=domain, rank=rank, detection_source=DetectionSource.BOTH,
                signal_count=7, serp_mentions=4, co_citation_mentions=3, corroborated=True,
                other_sites=list((sites or {}).get(domain, [])),
            )
        )
    await session.commit()
    return cset.id


async def _cite(
    session: AsyncSession,
    scan_id: str,
    counts: dict[str, int],
    *,
    engine: Engine = Engine.CLAUDE_SEARCH,
    status: EngineResultStatus = EngineResultStatus.ANSWERED_NO_MENTION,
) -> None:
    """One result of the scan (an engine's answer to its prompt) citing each domain that often."""
    pset = (
        await session.execute(select(PromptSet.id).where(PromptSet.scan_id == scan_id))
    ).scalar_one_or_none()
    if pset is None:
        pset = ids.new_id(ids.PROMPT_SET)
        session.add(PromptSet(id=pset, scan_id=scan_id))
        await session.flush()
        session.add(
            Prompt(
                id=ids.new_id(ids.PROMPT), prompt_set_id=pset, text="best university?",
                intent=PromptIntent.AWARENESS, position=1,
            )
        )
        await session.flush()
    prompt_id = (
        await session.execute(select(Prompt.id).where(Prompt.prompt_set_id == pset))
    ).scalar_one()
    result = EngineResult(
        id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan_id, prompt_id=prompt_id,
        engine=engine, status=status,
    )
    session.add(result)
    await session.flush()
    for domain, count in counts.items():
        for n in range(count):
            session.add(
                Citation(
                    id=ids.new_id(ids.CITATION), engine_result_id=result.id,
                    source_domain=domain, source_url=f"https://{domain}/page-{n}",
                    source_type=CitationType.OTHER, position=n + 1,
                )
            )
    await session.commit()


def _body(
    own: tuple[str, ...] | list[str] = (),
    rivals: tuple[tuple[str, list[str]], ...] | list[tuple[str, list[str]]] = (),
    dismissed: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    return {
        "client": list(own),
        "rivals": [{"rivalDomain": domain, "sites": list(sites)} for domain, sites in rivals],
        "dismissed": list(dismissed),
    }


def _put(client: AsyncClient, cid: str, body: dict[str, Any]):  # noqa: ANN202
    return client.put(f"{BASE}/clients/{cid}/other-sites", json=body)


async def _lists(session: AsyncSession, cid: str) -> tuple[list[str], list[str]]:
    """The client's confirmed and dismissed lists as the database holds them."""
    row = (
        await session.execute(
            select(Client.other_sites, Client.dismissed_other_sites).where(Client.id == cid)
        )
    ).one()
    return row[0], row[1]


async def _rows(session: AsyncSession, set_id: str) -> list[tuple[Any, ...]]:
    """Every row of a set (tombstones too) as plain values: (name, domain, other sites, struck)."""
    result = await session.execute(
        select(Competitor.name, Competitor.domain, Competitor.other_sites, Competitor.is_suppressed)
        .where(Competitor.competitor_set_id == set_id)
        .order_by(Competitor.rank, Competitor.name)
    )
    return [tuple(row) for row in result]


async def _sites_by_domain(session: AsyncSession, set_id: str) -> dict[str, list[str]]:
    return {r[1]: r[2] for r in await _rows(session, set_id) if not r[3]}


async def _next_list_ids(session: AsyncSession, cid: str) -> list[str]:
    return list(
        (
            await session.execute(
                select(CompetitorSet.id).where(
                    CompetitorSet.client_id == cid, CompetitorSet.scan_id.is_(None)
                )
            )
        ).scalars()
    )


async def _everything(session: AsyncSession, cid: str) -> dict[str, Any]:
    """Every row a save could touch: the client's lists, its sets, and their rows."""
    scan_ids = select(Scan.id).where(Scan.client_id == cid)
    owned = or_(CompetitorSet.client_id == cid, CompetitorSet.scan_id.in_(scan_ids))
    sets = (
        await session.execute(
            select(CompetitorSet.id, CompetitorSet.scan_id, CompetitorSet.client_id)
            .where(owned).order_by(CompetitorSet.id)
        )
    ).all()
    rows = (
        await session.execute(
            select(
                Competitor.id, Competitor.competitor_set_id, Competitor.name,
                Competitor.domain, Competitor.rank, Competitor.other_sites,
                Competitor.is_suppressed,
            )
            .join(CompetitorSet, CompetitorSet.id == Competitor.competitor_set_id)
            .where(owned).order_by(Competitor.id)
        )
    ).all()
    return {
        "lists": await _lists(session, cid),
        "sets": [tuple(s) for s in sets],
        "rows": [tuple(r) for r in rows],
    }


async def _adopt(session: AsyncSession, cid: str) -> tuple[str, str]:
    """A scan that starts now and takes the client's next list as its own set: (scan, set)."""
    scan_id = await _add_scan(session, cid, ScanStatus.QUEUED)
    scan = (await session.execute(select(Scan).where(Scan.id == scan_id))).scalar_one()
    owner = (await session.execute(select(Client).where(Client.id == cid))).scalar_one()
    adopted, how = await detection.ensure_set_for_scan(session, scan, owner)
    assert how == "adopted" and adopted is not None, how
    await session.commit()
    return scan_id, adopted.id


def _problem_sentence(resp: Any, domain: str) -> str:
    """The 422's sentence: one, in `detail` and in the one error, naming the domain."""
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == VALIDATION and body["status"] == 422
    assert len(body["errors"]) == 1, body["errors"]
    assert body["errors"][0]["message"] == body["detail"]
    assert body["detail"].startswith(domain + " ") and body["detail"].endswith(".")
    return body["detail"]


# --- proposing changes nothing ------------------------------------------------------------------


class TestProposedIsNotConfirmed:
    async def test_a_scan_that_cited_the_sibling_proposes_it_and_nothing_is_confirmed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        set_id = await _add_set(session, scan_id, MONASH)
        await _cite(session, scan_id, {"rmit.edu.vn": 4, "monash.edu.my": 2, "wikipedia.org": 9})
        before = await _everything(session, cid)

        resp = await client.get(f"{BASE}/clients/{cid}/other-sites")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "confirmed": [],
            "proposals": [
                {
                    "domain": "rmit.edu.vn", "citations": 4, "brandKind": "client",
                    "brandName": CLIENT_NAME, "brandDomain": CLIENT_DOMAIN,
                },
                {
                    "domain": "monash.edu.my", "citations": 2, "brandKind": "rival",
                    "brandName": MONASH[0], "brandDomain": MONASH[1],
                },
            ],
            "dismissed": [],
        }
        # Reading it wrote nothing: the client's lists are still empty, and so is every row.
        assert await _lists(session, cid) == ([], [])
        assert await _sites_by_domain(session, set_id) == {"monash.edu": []}
        assert await _everything(session, cid) == before

    async def test_after_a_real_scan_the_sibling_is_proposed_and_still_counted_as_a_stranger(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, monkeypatch  # noqa: ANN001
    ) -> None:
        """Through the pipeline: a proposed sibling is typed as a stranger.

        The scan types a citation by the CONFIRMED list (task 1.2), and this test never confirms:
        the client's list is empty, so `rmit.edu.vn` stays a source that does not cite the client,
        and `rmit.edu.au` is the one that does.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(
            serp_domains=["monash.edu", "deakin.edu.au"],
            cocit_brands=[(MONASH[0], MONASH[1]), (DEAKIN[0], DEAKIN[1])],
        )
        assert (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).status_code == 201

        async def generate(**kwargs: Any) -> tuple[list[GeneratedPrompt], str]:  # noqa: ARG001
            prompts = [
                GeneratedPrompt(text=f"question {n}", intent=list(PromptIntent)[n % 3])
                for n in range(3)
            ]
            return prompts, "stub"

        async def ask_all(prompt: str, *, engines: Any, settings: Any) -> list[EngineAnswer]:  # noqa: ARG001
            return [
                EngineAnswer(
                    engine=engine, engine_version="stub", prompt_text=prompt,
                    text="RMIT University is one option.", latency_ms=5,
                    citations=[
                        CitedSource(url="https://rmit.edu.vn/x", domain="rmit.edu.vn", position=1),
                        CitedSource(url="https://rmit.edu.au/y", domain="rmit.edu.au", position=2),
                    ],
                )
                for engine in engines
            ]

        async def sentiment(answer: Any, *, subject_name: str, settings: Any = None):  # noqa: ANN202, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", sentiment)
        started = await client.post(
            f"{BASE}/clients/{cid}/scans", json={"engines": ["claude_search"]}
        )
        assert started.status_code == 202, started.text

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        cited = (
            await session.execute(
                select(func.count()).select_from(Citation).where(
                    Citation.source_domain == "rmit.edu.vn"
                )
            )
        ).scalar_one()
        assert cited > 0
        assert got["proposals"] == [
            {
                "domain": "rmit.edu.vn", "citations": cited, "brandKind": "client",
                "brandName": CLIENT_NAME, "brandDomain": CLIENT_DOMAIN,
            }
        ]
        assert got["confirmed"] == [] and got["dismissed"] == []
        # The client's own list is still empty, and the cited site is still a stranger's.
        assert await _lists(session, cid) == ([], [])
        flags = (
            await session.execute(
                select(Citation.source_domain, Citation.cites_subject).distinct()
            )
        ).all()
        assert {tuple(r) for r in flags} == {("rmit.edu.vn", False), ("rmit.edu.au", True)}


# --- GET ----------------------------------------------------------------------------------------


class TestGet:
    async def test_a_client_never_scanned_has_nothing_to_confirm_or_propose(
        self, client: AsyncClient
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        resp = await client.get(f"{BASE}/clients/{cid}/other-sites")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"confirmed": [], "proposals": [], "dismissed": []}

    async def test_proposals_come_from_the_latest_finished_scan_and_no_other(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        older = await _add_scan(session, cid)
        await _cite(session, older, {"rmit.edu.my": 9})
        latest = await _add_scan(session, cid)
        await _cite(session, latest, {"rmit.edu.vn": 4})

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert [(p["domain"], p["citations"]) for p in got["proposals"]] == [("rmit.edu.vn", 4)]

    @pytest.mark.parametrize(
        "status",
        [ScanStatus.RUNNING, ScanStatus.QUEUED, ScanStatus.FAILED, ScanStatus.CANCELLED],
    )
    async def test_a_newer_scan_that_has_not_finished_is_not_the_one_read(
        self, client: AsyncClient, session: AsyncSession, status: ScanStatus
    ) -> None:
        """Running and queued scans have measured nothing yet, failed and cancelled never will."""
        await _sign_up(client)
        cid = await _make_client(client)
        finished = await _add_scan(session, cid)
        await _cite(session, finished, {"rmit.edu.vn": 4})
        newer = await _add_scan(session, cid, status)
        await _cite(session, newer, {"rmit.com.sg": 7})

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert [(p["domain"], p["citations"]) for p in got["proposals"]] == [("rmit.edu.vn", 4)]

    async def test_a_finished_scan_with_no_answered_result_is_not_the_one_read(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        earlier = await _add_scan(session, cid)
        await _cite(session, earlier, {"rmit.edu.vn": 4})
        empty = await _add_scan(session, cid)
        await _cite(session, empty, {"rmit.com.sg": 7}, status=EngineResultStatus.ERROR)

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert [(p["domain"], p["citations"]) for p in got["proposals"]] == [("rmit.edu.vn", 4)]

    async def test_a_partial_scan_is_a_finished_scan(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid, ScanStatus.PARTIAL)
        await _cite(session, scan_id, {"rmit.edu.vn": 2})

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert [p["domain"] for p in got["proposals"]] == ["rmit.edu.vn"]

    async def test_citations_are_counted_per_domain_over_the_answered_results_only(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _cite(session, scan_id, {"rmit.edu.vn": 2}, engine=Engine.CLAUDE_SEARCH)
        await _cite(
            session, scan_id, {"rmit.edu.vn": 3, "rmit.edu.my": 1}, engine=Engine.PERPLEXITY,
            status=EngineResultStatus.OK,
        )
        # An engine that never answered did not cite anyone.
        await _cite(
            session, scan_id, {"rmit.edu.vn": 50, "rmit.com.sg": 8}, engine=Engine.CHATGPT,
            status=EngineResultStatus.ERROR,
        )

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert [(p["domain"], p["citations"]) for p in got["proposals"]] == [
            ("rmit.edu.vn", 5),
            ("rmit.edu.my", 1),
        ]

    async def test_the_rivals_are_those_the_next_scan_will_use(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A save of the rivals list is ahead of the latest scan's own set, as for the editor."""
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH)
        await _cite(session, scan_id, {"monash.edu.my": 3, "deakin.edu.my": 2})
        before = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        saved = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": DEAKIN[0], "domain": DEAKIN[1]}]}
            ),
        )
        assert saved.status_code == 200, saved.text

        after = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert [(p["brandName"], p["domain"]) for p in before["proposals"]] == [
            (MONASH[0], "monash.edu.my")
        ]
        assert [(p["brandName"], p["domain"]) for p in after["proposals"]] == [
            (DEAKIN[0], "deakin.edu.my")
        ]

    async def test_confirmed_lists_the_clients_sites_then_each_active_rivals_in_rank_order(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(
            session, scan_id, MONASH, DEAKIN,
            sites={MONASH[1]: ["monash.edu.my", "monash.com.sg"], DEAKIN[1]: ["deakin.edu.my"]},
        )
        await session.execute(
            update(Client).where(Client.id == cid).values(
                other_sites=["rmit.edu.vn", "rmit.edu.my"], dismissed_other_sites=["rmit.nl"]
            )
        )
        await session.commit()

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        def item(domain: str, kind: str, name: str, brand: str) -> dict[str, str]:
            return {"domain": domain, "brandKind": kind, "brandName": name, "brandDomain": brand}

        assert got["confirmed"] == [
            item("rmit.edu.vn", "client", CLIENT_NAME, CLIENT_DOMAIN),
            item("rmit.edu.my", "client", CLIENT_NAME, CLIENT_DOMAIN),
            item("monash.edu.my", "rival", MONASH[0], MONASH[1]),
            item("monash.com.sg", "rival", MONASH[0], MONASH[1]),
            item("deakin.edu.my", "rival", DEAKIN[0], DEAKIN[1]),
        ]
        assert got["dismissed"] == ["rmit.nl"]

    async def test_a_struck_rival_lists_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH, DEAKIN, sites={DEAKIN[1]: ["deakin.edu.my"]})
        await session.execute(
            update(Competitor).where(Competitor.domain == DEAKIN[1]).values(is_suppressed=True)
        )
        await session.commit()

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert got["confirmed"] == []

    async def test_a_confirmed_domain_is_not_proposed_and_neither_is_a_dismissed_one(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH, sites={MONASH[1]: ["monash.edu.my"]})
        await _cite(
            session, scan_id,
            {"rmit.edu.vn": 4, "rmit.edu.my": 3, "monash.edu.my": 2, "monash.com.sg": 1},
        )
        await session.execute(
            update(Client).where(Client.id == cid).values(
                other_sites=["rmit.edu.vn"], dismissed_other_sites=["monash.com.sg"]
            )
        )
        await session.commit()

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        assert [p["domain"] for p in got["proposals"]] == ["rmit.edu.my"]
        assert [c["domain"] for c in got["confirmed"]] == ["rmit.edu.vn", "monash.edu.my"]


# --- PUT ----------------------------------------------------------------------------------------


class TestPut:
    async def test_confirming_the_clients_site_lists_it_as_confirmed_and_proposes_it_no_more(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _cite(session, scan_id, {"rmit.edu.vn": 4, "rmit.edu.my": 1})

        resp = await _put(client, cid, _body(own=["rmit.edu.vn"]))

        assert resp.status_code == 200, resp.text
        assert resp.json() == (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        got = resp.json()
        assert got["confirmed"] == [
            {
                "domain": "rmit.edu.vn", "brandKind": "client",
                "brandName": CLIENT_NAME, "brandDomain": CLIENT_DOMAIN,
            }
        ]
        assert [p["domain"] for p in got["proposals"]] == ["rmit.edu.my"]
        assert got["dismissed"] == []
        assert await _lists(session, cid) == (["rmit.edu.vn"], [])

    async def test_dismissing_a_domain_means_it_is_never_proposed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _cite(session, scan_id, {"rmit.edu.vn": 4, "rmit.edu.my": 1})

        resp = await _put(client, cid, _body(dismissed=["rmit.edu.vn"]))

        assert resp.status_code == 200, resp.text
        assert [p["domain"] for p in resp.json()["proposals"]] == ["rmit.edu.my"]
        assert resp.json()["dismissed"] == ["rmit.edu.vn"]
        assert resp.json()["confirmed"] == []
        # And it stays out through later scans: the list is the client's, not the scan's.
        later = await _add_scan(session, cid)
        await _cite(session, later, {"rmit.edu.vn": 8})
        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        assert [p["domain"] for p in got["proposals"]] == []
        assert await _lists(session, cid) == ([], ["rmit.edu.vn"])

    async def test_a_save_replaces_the_client_list_the_rival_lists_and_the_dismissed_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH, DEAKIN)
        first = await _put(
            client, cid,
            _body(
                own=["rmit.edu.vn", "rmit.edu.my"],
                rivals=[(MONASH[1], ["monash.edu.my"]), (DEAKIN[1], ["deakin.edu.my"])],
                dismissed=["wikipedia.org", "rmit.nl"],
            ),
        )
        assert first.status_code == 200, first.text

        second = await _put(
            client, cid,
            _body(own=["rmit.com.sg"], rivals=[(MONASH[1], ["monash.com.sg"])], dismissed=[]),
        )

        assert second.status_code == 200, second.text
        assert [c["domain"] for c in second.json()["confirmed"]] == [
            "rmit.com.sg", "monash.com.sg"
        ]
        assert second.json()["dismissed"] == []
        assert await _lists(session, cid) == (["rmit.com.sg"], [])
        [next_list] = await _next_list_ids(session, cid)
        assert await _sites_by_domain(session, next_list) == {
            "monash.edu": ["monash.com.sg"], "deakin.edu.au": [],
        }

    async def test_domains_are_kept_in_the_form_a_cited_domain_has(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Lower case, no `www.`, once each: so the later comparison to a cited domain is equal."""
        await _sign_up(client)
        cid = await _make_client(client)

        resp = await _put(
            client, cid,
            _body(
                own=["WWW.RMIT.EDU.VN", "rmit.edu.vn", " rmit.edu.my. "],
                dismissed=["Wikipedia.ORG", "wikipedia.org"],
            ),
        )

        assert resp.status_code == 200, resp.text
        assert await _lists(session, cid) == (
            ["rmit.edu.vn", "rmit.edu.my"], ["wikipedia.org"]
        )

    async def test_a_save_with_nothing_to_say_about_rivals_makes_no_next_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        own = await _add_set(session, scan_id, MONASH)
        before = await _rows(session, own)

        resp = await _put(client, cid, _body(own=["rmit.edu.vn"], dismissed=["wikipedia.org"]))

        assert resp.status_code == 200, resp.text
        assert await _next_list_ids(session, cid) == []
        assert await _rows(session, own) == before

    async def test_a_member_may_save_as_an_owner_may(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Any seat of the agency: the route asks for a signed-in user, and not for a role."""
        await _sign_up(client)
        cid = await _make_client(client)
        await session.execute(update(User).values(role=UserRole.MEMBER))
        await session.commit()

        saved = await _put(client, cid, _body(own=["rmit.edu.vn"]))
        read = await client.get(f"{BASE}/clients/{cid}/other-sites")

        assert (saved.status_code, read.status_code) == (200, 200)
        assert await _lists(session, cid) == (["rmit.edu.vn"], [])


class TestPutRefuses:
    """422, one plain sentence naming the domain, and nothing is written."""

    async def _setup(
        self, client: AsyncClient, session: AsyncSession
    ) -> tuple[str, dict[str, Any]]:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH, DEAKIN)
        await _put(client, cid, _body(own=["rmit.edu.my"], dismissed=["wikipedia.org"]))
        return cid, await _everything(session, cid)

    async def test_a_site_that_is_not_the_clients_name_elsewhere(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(client, cid, _body(own=["rmit.edu.vn", "wikipedia.org"]))

        assert _problem_sentence(resp, "wikipedia.org") == (
            "wikipedia.org is not the same name as rmit.edu.au with a different ending, "
            "so it cannot be confirmed as one of its other sites."
        )
        assert resp.json()["errors"][0]["field"] == "client.1"
        assert resp.json()["errors"][0]["type"] == "not_the_same_name"
        assert await _everything(session, cid) == before

    async def test_a_shared_hosting_address_is_refused_and_a_scan_that_cited_it_proposes_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)
        await _cite(session, await _add_scan(session, cid), {"rmit.github.io": 6, "rmit.edu.vn": 2})
        before = await _everything(session, cid)

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        resp = await _put(client, cid, _body(own=["rmit.github.io"]))

        assert [p["domain"] for p in got["proposals"]] == ["rmit.edu.vn"]
        assert _problem_sentence(resp, "rmit.github.io") == (
            "rmit.github.io is an address on a service anyone can sign up to, so it cannot be "
            "confirmed as one of rmit.edu.au's other sites."
        )
        error = resp.json()["errors"][0]
        assert (error["field"], error["type"]) == ("client.0", "shared_hosting")
        assert await _everything(session, cid) == before

    @pytest.mark.parametrize("site", ["rmit-online.com", "myrmit.edu.vn", "rmit.edu.au"])
    async def test_a_name_that_only_looks_like_it_and_the_clients_own_domain(
        self, client: AsyncClient, session: AsyncSession, site: str
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(client, cid, _body(own=[site]))

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"].startswith(site + " ")
        assert await _everything(session, cid) == before

    async def test_a_rival_domain_that_is_not_an_active_rival_of_the_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(client, cid, _body(rivals=[("zendesk.com", ["zendesk.nl"])]))

        assert _problem_sentence(resp, "zendesk.com") == (
            "zendesk.com is not one of this client's rivals."
        )
        assert resp.json()["errors"][0]["field"] == "rivals.0.rivalDomain"
        assert resp.json()["errors"][0]["type"] == "unknown_rival"
        assert await _everything(session, cid) == before

    async def test_a_struck_rival_is_not_an_active_rival(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, _ = await self._setup(client, session)
        saved = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": DEAKIN[0], "domain": DEAKIN[1]}]}
            ),
        )
        assert saved.status_code == 200, saved.text
        before = await _everything(session, cid)

        resp = await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))

        assert _problem_sentence(resp, "monash.edu") == (
            "monash.edu is not one of this client's rivals."
        )
        assert await _everything(session, cid) == before

    async def test_a_rival_site_that_is_not_the_rivals_name_elsewhere(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(
            client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my", "deakin.edu.my"])])
        )

        assert _problem_sentence(resp, "deakin.edu.my") == (
            "deakin.edu.my is not the same name as monash.edu with a different ending, "
            "so it cannot be confirmed as one of its other sites."
        )
        assert resp.json()["errors"][0]["field"] == "rivals.0.sites.1"
        assert await _everything(session, cid) == before

    async def test_a_rival_listed_twice(self, client: AsyncClient, session: AsyncSession) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(
            client, cid,
            _body(rivals=[(MONASH[1], ["monash.edu.my"]), ("WWW.monash.edu", ["monash.nl"])]),
        )

        assert _problem_sentence(resp, "monash.edu") == "monash.edu is listed more than once."
        assert resp.json()["errors"][0]["field"] == "rivals.1.rivalDomain"
        assert await _everything(session, cid) == before

    async def test_both_confirmed_and_dismissed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(client, cid, _body(own=["rmit.edu.vn"], dismissed=["rmit.edu.vn"]))

        assert _problem_sentence(resp, "rmit.edu.vn") == (
            "rmit.edu.vn cannot be both confirmed and dismissed."
        )
        assert resp.json()["errors"][0]["field"] == "dismissed.0"
        assert resp.json()["errors"][0]["type"] == "confirmed_and_dismissed"
        assert await _everything(session, cid) == before

    async def test_a_rival_site_that_is_also_dismissed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(
            client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])], dismissed=["monash.edu.my"])
        )

        assert _problem_sentence(resp, "monash.edu.my").endswith("both confirmed and dismissed.")
        assert await _everything(session, cid) == before

    async def test_a_domain_for_one_brand_only(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """`acme.fr` is the name of all three brands; it can be confirmed for one of them."""
        await _sign_up(client)
        cid = await _make_client(client, "acme.com.au", "Acme")
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, ("Acme Inc", "acme.com"), ("Acme GmbH", "acme.de"))
        before = await _everything(session, cid)

        client_and_rival = await _put(
            client, cid, _body(own=["acme.fr"], rivals=[("acme.com", ["acme.fr"])])
        )
        two_rivals = await _put(
            client, cid,
            _body(rivals=[("acme.com", ["acme.fr"]), ("acme.de", ["acme.fr", "acme.nl"])]),
        )

        assert _problem_sentence(client_and_rival, "acme.fr") == (
            "acme.fr can be confirmed for one brand only."
        )
        assert client_and_rival.json()["errors"][0]["field"] == "rivals.0.sites.0"
        assert client_and_rival.json()["errors"][0]["type"] == "one_brand_only"
        assert _problem_sentence(two_rivals, "acme.fr").endswith("for one brand only.")
        assert two_rivals.json()["errors"][0]["field"] == "rivals.1.sites.0"
        assert await _everything(session, cid) == before

    async def test_a_site_that_is_another_brands_own_domain(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """`proposals()` never offers a brand's own domain; a save may not confirm one either."""
        await _sign_up(client)
        cid = await _make_client(client, "acme.com.au", "Acme")
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, ("Acme Inc", "acme.com"), ("Acme GmbH", "acme.de"))
        before = await _everything(session, cid)

        client_own = await _put(client, cid, _body(rivals=[("acme.com", ["acme.com.au"])]))
        rival_own = await _put(client, cid, _body(own=["acme.de"]))

        assert _problem_sentence(client_own, "acme.com.au") == (
            "acme.com.au is a brand's own domain here, so it cannot be confirmed as another site."
        )
        assert client_own.json()["errors"][0]["type"] == "a_brands_own_domain"
        assert _problem_sentence(rival_own, "acme.de").endswith("as another site.")
        assert await _everything(session, cid) == before

    @pytest.mark.parametrize(
        "typed",
        [
            "https://rmit.edu.vn", "rmit.edu.vn/about", "shop.rmit.edu.vn", "rmit.edu.vn:8080",
            "rmit.edu.vn?x=1", "user:secret-password@rmit.edu.vn", "not a domain", "",
            "localhost", "192.168.0.1", "co.uk",
        ],
    )
    async def test_something_that_is_not_just_a_domain_is_refused_and_not_quoted(
        self, client: AsyncClient, session: AsyncSession, typed: str
    ) -> None:
        cid, before = await self._setup(client, session)

        for body, field in (
            (_body(own=[typed]), "client.0"),
            (_body(dismissed=[typed]), "dismissed.0"),
            (_body(rivals=[(MONASH[1], [typed])]), "rivals.0.sites.0"),
            (_body(rivals=[(typed, [])]), "rivals.0.rivalDomain"),
        ):
            resp = await _put(client, cid, body)
            assert resp.status_code == 422, (typed, resp.text)
            problem = resp.json()
            assert problem["type"] == VALIDATION
            assert problem["errors"] == [
                {"field": field, "message": problem["detail"], "type": "invalid_domain"}
            ]
            # One sentence, and none of it is what was typed (it can hold a password).
            assert "secret-password" not in resp.text
            if typed:
                assert typed not in problem["detail"]
        assert await _everything(session, cid) == before

    async def test_more_than_twenty_sites_for_a_brand(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)
        client_sites = [f"rmit.{suffix}" for suffix in TWENTY_SUFFIXES] + ["rmit.com"]
        rival_sites = [f"monash.{suffix}" for suffix in TWENTY_SUFFIXES] + ["monash.com"]

        too_many_own = await _put(client, cid, _body(own=client_sites))
        too_many_rival = await _put(client, cid, _body(rivals=[(MONASH[1], rival_sites)]))

        assert too_many_own.status_code == too_many_rival.status_code == 422
        assert too_many_own.json()["detail"] == "rmit.edu.au can have at most 20 other sites."
        assert too_many_own.json()["errors"][0]["field"] == "client"
        assert too_many_own.json()["errors"][0]["type"] == "too_many_sites"
        assert too_many_rival.json()["detail"] == "monash.edu can have at most 20 other sites."
        assert too_many_rival.json()["errors"][0]["field"] == "rivals.0.sites"
        assert await _everything(session, cid) == before

    async def test_more_than_two_hundred_dismissed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(client, cid, _body(dismissed=[f"site-{n}.com" for n in range(201)]))

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "At most 200 domains can be dismissed."
        assert resp.json()["errors"][0]["field"] == "dismissed"
        assert await _everything(session, cid) == before

    async def test_exactly_at_the_caps_is_allowed(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH)
        own = [f"rmit.{suffix}" for suffix in TWENTY_SUFFIXES]
        theirs = [f"monash.{suffix}" for suffix in TWENTY_SUFFIXES]
        dismissed = [f"site-{n}.com" for n in range(200)]

        body = _body(own=own, rivals=[(MONASH[1], theirs)], dismissed=dismissed)

        resp = await _put(client, cid, body)

        assert resp.status_code == 200, resp.text
        assert len(resp.json()["confirmed"]) == 40 and len(resp.json()["dismissed"]) == 200
        assert (await _lists(session, cid)) == (own, dismissed)

    async def test_more_rivals_than_a_list_can_hold(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await _put(
            client, cid, _body(rivals=[(f"rival-{n}.com", []) for n in range(11)])
        )

        assert resp.status_code == 422, resp.text
        assert resp.json()["type"] == VALIDATION
        assert await _everything(session, cid) == before

    async def test_a_list_longer_than_a_request_can_carry_is_refused_before_it_is_read(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A bound on the work a request can ask for, far above the caps a person meets."""
        cid, before = await self._setup(client, session)
        many = [f"site-{n}.com" for n in range(1001)]

        for body, field in (
            (_body(own=many), "client"),
            (_body(dismissed=many), "dismissed"),
            (_body(rivals=[(MONASH[1], many)]), "rivals.0.sites"),
        ):
            resp = await _put(client, cid, body)
            assert resp.status_code == 422, resp.text
            assert resp.json()["errors"][0]["field"] == field
            assert resp.json()["errors"][0]["type"] == "too_long"
        assert await _everything(session, cid) == before

    @pytest.mark.parametrize("missing", ["client", "rivals", "dismissed"])
    async def test_a_body_that_leaves_out_a_list_is_refused_and_clears_nothing(
        self, client: AsyncClient, session: AsyncSession, missing: str
    ) -> None:
        """All three lists are replaced, so all three are named: an absent one is not "empty"."""
        cid, before = await self._setup(client, session)
        body = _body(own=["rmit.edu.vn"])
        del body[missing]

        resp = await _put(client, cid, body)

        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"][0]["field"] == missing
        assert await _everything(session, cid) == before


@pytest.fixture
def normalised(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every string the service hands to `intake.rival_domain` while the test runs.

    The app runs in this process (ASGI transport), so what a request makes it do can be counted.
    `rival_domain` parses, splits and IDNA-encodes: it is the work a refusal should not repeat
    for entries it is about to refuse anyway.
    """
    seen: list[str] = []
    real = other_sites.rival_domain

    def counting(raw: str) -> str:
        seen.append(raw)
        return real(raw)

    monkeypatch.setattr(other_sites, "rival_domain", counting)
    return seen


async def _seeded(client: AsyncClient, session: AsyncSession) -> tuple[str, dict[str, Any]]:
    """RMIT with two rivals on its latest set and something saved: (client id, every row)."""
    await _sign_up(client)
    cid = await _make_client(client)
    scan_id = await _add_scan(session, cid)
    await _add_set(session, scan_id, MONASH, DEAKIN)
    await _put(client, cid, _body(own=["rmit.edu.my"], dismissed=["wikipedia.org"]))
    return cid, await _everything(session, cid)


class TestTheCapsComeBeforeAnyEntryIsRead:
    """The schema lets a list carry 50 domains (250 dismissed); the caps a person meets are 20 and
    200.

    Each entry costs a parse, a Public Suffix List split and an IDNA encode on the event loop, so
    the caps are checked on the lists as sent, after the rivals' own domains (at most 10) and
    before any site or dismissed domain is read.
    """

    async def test_a_request_at_the_schemas_limits_is_refused_having_read_only_the_rivals(
        self, client: AsyncClient, session: AsyncSession, normalised: list[str]
    ) -> None:
        cid, before = await _seeded(client, session)
        sites = [f"site-{n}.com" for n in range(50)]
        dismissed = [f"site-{n}.com" for n in range(250)]
        rivals = [(f"rival-{n}.com", sites) for n in range(10)]
        normalised.clear()

        resp = await _put(client, cid, _body(own=sites, rivals=rivals, dismissed=dismissed))

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "rmit.edu.au can have at most 20 other sites."
        assert resp.json()["errors"][0]["field"] == "client"
        assert resp.json()["errors"][0]["type"] == "too_many_sites"
        # Ten domains read, where reading every entry of the twelve lists would be 810.
        assert normalised == [domain for domain, _sites in rivals]
        assert await _everything(session, cid) == before

    async def test_a_list_of_21_with_a_repeat_is_refused_because_entries_are_counted_as_sent(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """20 distinct domains and a 21st entry that repeats one: refused, not folded to 20."""
        cid, before = await _seeded(client, session)
        own = [f"rmit.{suffix}" for suffix in TWENTY_SUFFIXES]
        theirs = [f"monash.{suffix}" for suffix in TWENTY_SUFFIXES]
        dismissed = [f"site-{n}.com" for n in range(200)]

        for body, field, kind in (
            (_body(own=[*own, own[0]]), "client", "too_many_sites"),
            (_body(rivals=[(MONASH[1], [*theirs, theirs[0]])]), "rivals.0.sites", "too_many_sites"),
            (_body(dismissed=[*dismissed, dismissed[0]]), "dismissed", "too_many_dismissed"),
        ):
            resp = await _put(client, cid, body)
            assert resp.status_code == 422, (field, resp.text)
            assert (resp.json()["errors"][0]["field"], resp.json()["errors"][0]["type"]) == (
                field, kind,
            )
        assert await _everything(session, cid) == before


class TestTheSchemaCutsAListOffJustAboveTheCaps:
    """A8b. The schema bounds a list at 50 sites for a brand and 250 dismissed
    (`other_sites_bounds`), a little above the service's 20 and 200, so that the body this route
    can be sent is at most about 200 KB. From 21 to 50 and from 201 to 250 the service still
    answers, with its sentence; past the bound the schema answers first, in its own words, and
    nothing is read."""

    async def test_a_list_up_to_the_bound_still_gets_the_services_sentence(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await _seeded(client, session)
        fifty = [f"site-{n}.com" for n in range(50)]
        two_fifty = [f"site-{n}.com" for n in range(250)]

        for body, field, kind, sentence in (
            (_body(own=fifty), "client", "too_many_sites",
             "rmit.edu.au can have at most 20 other sites."),
            (_body(rivals=[(MONASH[1], fifty)]), "rivals.0.sites", "too_many_sites",
             "monash.edu can have at most 20 other sites."),
            (_body(dismissed=two_fifty), "dismissed", "too_many_dismissed",
             "At most 200 domains can be dismissed."),
        ):
            resp = await _put(client, cid, body)
            assert resp.status_code == 422, (field, resp.text)
            assert resp.json()["detail"] == sentence
            assert (resp.json()["errors"][0]["field"], resp.json()["errors"][0]["type"]) == (
                field, kind,
            )
        assert await _everything(session, cid) == before

    async def test_one_entry_past_the_bound_is_the_schemas_answer_and_nothing_is_read(
        self, client: AsyncClient, session: AsyncSession, normalised: list[str]
    ) -> None:
        cid, before = await _seeded(client, session)
        fifty_one = [f"site-{n}.com" for n in range(51)]
        two_fifty_one = [f"site-{n}.com" for n in range(251)]
        normalised.clear()

        for body, field, longest in (
            (_body(own=fifty_one), "client", 50),
            (_body(rivals=[(MONASH[1], fifty_one)]), "rivals.0.sites", 50),
            (_body(dismissed=two_fifty_one), "dismissed", 250),
            (_body(rivals=[(f"rival-{n}.com", []) for n in range(11)]), "rivals", 10),
        ):
            resp = await _put(client, cid, body)
            assert resp.status_code == 422, (field, resp.text)
            problem = resp.json()
            assert problem["type"] == VALIDATION
            [error] = problem["errors"]
            assert (error["field"], error["type"]) == (field, "too_long")
            assert f"at most {longest} items" in error["message"]
        assert normalised == [], "something was read for a list the schema cut off"
        assert await _everything(session, cid) == before


class TestOneHostInTwoSpellings:
    """A cited domain keeps the spelling the engine gave and a typed one the spelling typed.

    So one host can arrive as `bücher.de` and as `xn--bcher-kva.de`, or as `пример.рф` and as
    `пример.xn--p1ai`. Every comparison is made on the DNS form, so it is one host; and what a
    save writes to the three lists is that form too (`xn--bcher-kva.at` for `bücher.at`): lower
    case ASCII, which a JSONB column holds in any database encoding. This suite's local cluster
    is SQL_ASCII, which refuses a non-ASCII character in JSONB, so a Unicode spelling that is
    confirmed or dismissed through the route and answered with a 200 is the proof that nothing
    non-ASCII is written. A proposal keeps the spelling the engine cited.
    """

    async def _write_raw_list(self, session: AsyncSession, cid: str, sites: list[str]) -> None:
        """The client's confirmed list as raw JSON text, in a spelling the route no longer writes.

        A list written by hand, or by a save from before the DNS form was what is stored. (No
        `\\u` escape: the cluster is SQL_ASCII, which takes the bytes as they are.)
        """
        raw = "[" + ", ".join(f'"{site}"' for site in sites) + "]"
        await session.execute(
            text("UPDATE clients SET other_sites = CAST(:raw AS jsonb) WHERE id = :cid"),
            {"raw": raw, "cid": cid},
        )
        await session.commit()

    async def test_the_clients_own_host_in_the_other_spelling_is_not_proposed_and_is_refused(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "пример.рф", "Пример")
        scan_id = await _add_scan(session, cid)
        await _cite(
            session, scan_id,
            {"пример.xn--p1ai": 3, "xn--e1afmkfd.xn--p1ai": 2, "пример.com": 1},
        )
        before = await _everything(session, cid)

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        refused = await _put(client, cid, _body(own=["пример.xn--p1ai"]))

        assert [p["domain"] for p in got["proposals"]] == ["пример.com"]
        assert _problem_sentence(refused, "пример.xn--p1ai") == (
            "пример.xn--p1ai is a brand's own domain here, so it cannot be confirmed as "
            "another site."
        )
        assert refused.json()["errors"][0]["field"] == "client.0"
        assert refused.json()["errors"][0]["type"] == "a_brands_own_domain"
        assert await _everything(session, cid) == before

    async def test_a_unicode_spelling_is_confirmed_and_stored_and_listed_in_the_dns_form(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "xn--bcher-kva.de", "Bücher")
        scan_id = await _add_scan(session, cid)
        await _cite(session, scan_id, {"bücher.at": 3, "bücher.de": 2, "bücher.nl": 1})

        proposed = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        saved = await _put(client, cid, _body(own=["bücher.at"]))

        # Proposed as it was cited; the brand's own host in the other spelling (`bücher.de`) is not.
        assert proposed["proposals"] == [
            {
                "domain": "bücher.at", "citations": 3, "brandKind": "client",
                "brandName": "Bücher", "brandDomain": "xn--bcher-kva.de",
            },
            {
                "domain": "bücher.nl", "citations": 1, "brandKind": "client",
                "brandName": "Bücher", "brandDomain": "xn--bcher-kva.de",
            },
        ]
        # Accepted on a cluster that cannot hold a non-ASCII character in JSONB, stored and listed
        # as the DNS form, and the cited Unicode spelling is not asked about again.
        assert saved.status_code == 200, saved.text
        assert saved.json()["confirmed"] == [
            {
                "domain": "xn--bcher-kva.at", "brandKind": "client",
                "brandName": "Bücher", "brandDomain": "xn--bcher-kva.de",
            }
        ]
        assert await _lists(session, cid) == (["xn--bcher-kva.at"], [])
        assert [(p["domain"], p["citations"]) for p in saved.json()["proposals"]] == [
            ("bücher.nl", 1)
        ]
        assert (await client.get(f"{BASE}/clients/{cid}/other-sites")).json() == saved.json()
        # The same host saved in the other spelling is the same entry.
        again = await _put(client, cid, _body(own=["xn--bcher-kva.at"]))
        assert again.status_code == 200, again.text
        assert again.json() == saved.json()
        assert await _lists(session, cid) == (["xn--bcher-kva.at"], [])

    async def test_a_unicode_spelling_is_dismissed_and_stored_and_listed_in_the_dns_form(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "bücher.de", "Bücher")
        scan_id = await _add_scan(session, cid)
        await _cite(session, scan_id, {"bücher.at": 3, "bücher.nl": 1})

        saved = await _put(client, cid, _body(dismissed=["bücher.nl"]))

        assert saved.status_code == 200, saved.text
        assert saved.json()["dismissed"] == ["xn--bcher-kva.nl"]
        assert await _lists(session, cid) == ([], ["xn--bcher-kva.nl"])
        # The cited Unicode spelling is never proposed again, whatever later scans cite.
        assert [p["domain"] for p in saved.json()["proposals"]] == ["bücher.at"]
        later = await _add_scan(session, cid)
        await _cite(session, later, {"bücher.nl": 9, "xn--bcher-kva.nl": 4, "bücher.fr": 2})
        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        assert [p["domain"] for p in got["proposals"]] == ["bücher.fr"]
        # And it is one entry however it is typed.
        again = await _put(client, cid, _body(dismissed=["xn--bcher-kva.nl", "BÜCHER.NL"]))
        assert again.status_code == 200, again.text
        assert await _lists(session, cid) == ([], ["xn--bcher-kva.nl"])

    async def test_an_internationalised_domain_of_any_script_is_stored_as_ascii(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        resp = await _put(
            client, cid, _body(dismissed=["日本語.jp", "Straße.DE", "пример.рф", "wikipedia.org"])
        )

        assert resp.status_code == 200, resp.text
        assert await _lists(session, cid) == (
            [], ["xn--wgv71a119e.jp", "xn--strae-oqa.de", "xn--e1afmkfd.xn--p1ai", "wikipedia.org"]
        )
        assert resp.json()["dismissed"] == [
            "xn--wgv71a119e.jp", "xn--strae-oqa.de", "xn--e1afmkfd.xn--p1ai", "wikipedia.org"
        ]

    @pytest.mark.parametrize(
        ("confirmed", "dismissed"),
        [("bücher.at", "xn--bcher-kva.at"), ("xn--bcher-kva.at", "bücher.at")],
    )
    async def test_one_host_confirmed_in_one_spelling_and_dismissed_in_the_other(
        self, client: AsyncClient, session: AsyncSession, confirmed: str, dismissed: str
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "bücher.de", "Bücher")
        before = await _everything(session, cid)

        resp = await _put(client, cid, _body(own=[confirmed], dismissed=[dismissed]))

        assert _problem_sentence(resp, dismissed) == (
            f"{dismissed} cannot be both confirmed and dismissed."
        )
        assert resp.json()["errors"][0]["field"] == "dismissed.0"
        assert resp.json()["errors"][0]["type"] == "confirmed_and_dismissed"
        assert await _everything(session, cid) == before

    async def test_one_host_in_two_spellings_for_two_brands(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "bücher.de", "Bücher")
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, ("Bücher Verlag", "xn--bcher-kva.com"))
        before = await _everything(session, cid)

        resp = await _put(
            client, cid,
            _body(own=["xn--bcher-kva.at"], rivals=[("xn--bcher-kva.com", ["bücher.at"])]),
        )

        assert _problem_sentence(resp, "bücher.at") == (
            "bücher.at can be confirmed for one brand only."
        )
        assert resp.json()["errors"][0]["field"] == "rivals.0.sites.0"
        assert resp.json()["errors"][0]["type"] == "one_brand_only"
        assert await _everything(session, cid) == before

    async def test_a_rival_written_in_the_other_spelling_from_its_row_is_that_rival(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        own_set = await _add_set(session, scan_id, ("Bücher Verlag", "bücher.com"))

        resp = await _put(client, cid, _body(rivals=[("xn--bcher-kva.com", ["bücher.at"])]))

        assert resp.status_code == 200, resp.text
        assert resp.json()["confirmed"] == [
            {
                "domain": "xn--bcher-kva.at", "brandKind": "rival",
                "brandName": "Bücher Verlag", "brandDomain": "bücher.com",
            }
        ]
        # The site is on the rival's row of the next list, in the DNS form, and the row keeps the
        # domain it had; the scan's own set is left as it was.
        [next_list] = await _next_list_ids(session, cid)
        assert await _sites_by_domain(session, next_list) == {"bücher.com": ["xn--bcher-kva.at"]}
        assert await _sites_by_domain(session, own_set) == {"bücher.com": []}

    async def test_a_save_that_only_respells_what_is_stored_changes_nothing_and_makes_no_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Same host, same stored string: no difference, so no row to write and no list to make."""
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(
            session, scan_id, ("Bücher Verlag", "bücher.com"),
            sites={"bücher.com": ["xn--bcher-kva.at"]},
        )
        before = await _everything(session, cid)

        resp = await _put(client, cid, _body(rivals=[("xn--bcher-kva.com", ["bücher.at"])]))

        assert resp.status_code == 200, resp.text
        assert [c["domain"] for c in resp.json()["confirmed"]] == ["xn--bcher-kva.at"]
        assert await _next_list_ids(session, cid) == []
        assert await _everything(session, cid) == before

    async def test_a_rival_named_twice_in_two_spellings_is_named_twice(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, ("Bücher Verlag", "bücher.com"))
        before = await _everything(session, cid)

        resp = await _put(
            client, cid, _body(rivals=[("bücher.com", []), ("xn--bcher-kva.com", [])])
        )

        assert _problem_sentence(resp, "xn--bcher-kva.com") == (
            "xn--bcher-kva.com is listed more than once."
        )
        assert resp.json()["errors"][0]["field"] == "rivals.1.rivalDomain"
        assert resp.json()["errors"][0]["type"] == "repeated_rival"
        assert await _everything(session, cid) == before

    async def test_the_confirmed_list_shows_one_host_once_whatever_the_spellings(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A state the route cannot make now: one host in two spellings, for two brands."""
        await _sign_up(client)
        cid = await _make_client(client, "bücher.de", "Bücher")
        scan_id = await _add_scan(session, cid)
        await _add_set(
            session, scan_id, ("Bücher Verlag", "xn--bcher-kva.com"),
            sites={"xn--bcher-kva.com": ["xn--bcher-kva.at"]},
        )
        await self._write_raw_list(session, cid, ["bücher.at"])
        await _cite(session, scan_id, {"bücher.at": 2, "xn--bcher-kva.at": 1, "bücher.nl": 1})

        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()

        # Listed once, for the brand that comes first, and neither spelling is proposed.
        assert [(c["domain"], c["brandKind"]) for c in got["confirmed"]] == [
            ("bücher.at", "client")
        ]
        assert [p["domain"] for p in got["proposals"]] == ["bücher.nl"]

    async def test_a_save_writes_the_dns_form_over_a_list_written_in_another_spelling(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "bücher.de", "Bücher")
        await self._write_raw_list(session, cid, ["bücher.at", "BÜCHER.nl"])

        resp = await _put(client, cid, _body(own=["bücher.at", "bücher.nl"]))

        assert resp.status_code == 200, resp.text
        assert await _lists(session, cid) == (["xn--bcher-kva.at", "xn--bcher-kva.nl"], [])


class TestPutWhileAScanRuns:
    async def test_a_running_scan_means_409_and_nothing_is_written(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        done = await _add_scan(session, cid)
        await _add_set(session, done, MONASH, sites={MONASH[1]: ["monash.edu.my"]})
        await _add_scan(session, cid, ScanStatus.RUNNING)
        before = await _everything(session, cid)

        resp = await _put(
            client, cid,
            _body(
                own=["rmit.edu.vn"], rivals=[(MONASH[1], ["monash.com.sg"])], dismissed=["x.com"]
            ),
        )

        assert resp.status_code == 409, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/conflict" and resp.json()["detail"] == RUNNING
        assert await _everything(session, cid) == before
        assert await _next_list_ids(session, cid) == []
        # Reading is still allowed.
        assert (await client.get(f"{BASE}/clients/{cid}/other-sites")).status_code == 200

    @pytest.mark.parametrize("status", [ScanStatus.QUEUED, ScanStatus.SUCCEEDED, ScanStatus.FAILED])
    async def test_a_scan_that_is_not_running_is_no_reason_to_refuse(
        self, client: AsyncClient, session: AsyncSession, status: ScanStatus
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid, status)

        resp = await _put(client, cid, _body(own=["rmit.edu.vn"]))

        assert resp.status_code == 200, resp.text

    async def test_only_the_most_recent_scan_counts(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A scan stranded as running long ago is not the most recent one."""
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid, ScanStatus.RUNNING)
        await _add_scan(session, cid, ScanStatus.SUCCEEDED)

        resp = await _put(client, cid, _body(own=["rmit.edu.vn"]))

        assert resp.status_code == 200, resp.text


class TestWhatAnotherAgencyCannotDo:
    async def test_another_agencys_client_is_the_404_an_unknown_one_is_on_both_routes(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client, "owner@sites.example")
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH)
        await _cite(session, scan_id, {"rmit.edu.vn": 4})
        await _put(client, cid, _body(own=["rmit.edu.my"], rivals=[(MONASH[1], ["monash.nl"])]))
        before = await _everything(session, cid)
        await client.post(f"{BASE}/auth/logout")

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await _sign_up(other, "intruder@sites.example")
            unknown = f"{ids.CLIENT}_{ULID()}"
            seen = {
                "put": await _put(other, cid, _body(own=["rmit.edu.vn"])),
                "get": await other.get(f"{BASE}/clients/{cid}/other-sites"),
                "put-unknown": await _put(other, unknown, _body(own=["rmit.edu.vn"])),
                "get-unknown": await other.get(f"{BASE}/clients/{unknown}/other-sites"),
                "get-malformed": await other.get(f"{BASE}/clients/not-an-id/other-sites"),
            }

        assert {k: v.status_code for k, v in seen.items()} == dict.fromkeys(seen, 404)
        assert {v.json()["detail"] for v in seen.values()} == {"No client with that identifier."}
        assert "rmit.edu.vn" not in seen["get"].text and "monash.nl" not in seen["get"].text
        assert await _everything(session, cid) == before

    async def test_signed_out_is_a_401_on_both_routes(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await client.post(f"{BASE}/auth/logout")

        got = await client.get(f"{BASE}/clients/{cid}/other-sites")
        put = await _put(client, cid, _body(own=["rmit.edu.vn"]))

        assert (got.status_code, put.status_code) == (401, 401)
        assert await _lists(session, cid) == ([], [])


# --- rivals' sites live on the next list --------------------------------------------------------


class TestARivalsSitesLandOnTheNextList:
    async def test_the_first_save_copies_the_latest_set_and_leaves_the_scans_own_alone(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        own = await _add_set(session, scan_id, MONASH, DEAKIN)
        before = await _rows(session, own)

        resp = await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        assert next_list != own
        assert await _sites_by_domain(session, next_list) == {
            "monash.edu": ["monash.edu.my"], "deakin.edu.au": [],
        }
        # The scan that ran keeps the set it ran with, row for row.
        assert await _rows(session, own) == before
        assert await _sites_by_domain(session, own) == {"monash.edu": [], "deakin.edu.au": []}
        assert resp.json()["confirmed"] == [
            {
                "domain": "monash.edu.my", "brandKind": "rival",
                "brandName": MONASH[0], "brandDomain": MONASH[1],
            }
        ]
        # The editor's list is now the next list, carrying the same rivals as before.
        listed = (await client.get(f"{BASE}/clients/{cid}/competitors")).json()
        assert listed["scanId"] is None and listed["id"] == next_list
        assert [c["name"] for c in listed["competitors"]] == [MONASH[0], DEAKIN[0]]

    async def test_a_second_save_edits_the_same_next_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH, DEAKIN)
        await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))
        [next_list] = await _next_list_ids(session, cid)
        rival_row_ids = (
            await session.execute(
                select(Competitor.id).where(Competitor.competitor_set_id == next_list)
                .order_by(Competitor.id)
            )
        ).scalars().all()

        await _put(client, cid, _body(rivals=[(DEAKIN[1], ["deakin.edu.my"])]))

        assert await _next_list_ids(session, cid) == [next_list]
        assert await _sites_by_domain(session, next_list) == {
            "monash.edu": [], "deakin.edu.au": ["deakin.edu.my"],
        }
        assert (
            await session.execute(
                select(Competitor.id).where(Competitor.competitor_set_id == next_list)
                .order_by(Competitor.id)
            )
        ).scalars().all() == rival_row_ids

    async def test_a_rival_left_out_of_a_save_has_its_sites_cleared(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH, DEAKIN)
        await _put(
            client, cid,
            _body(rivals=[(MONASH[1], ["monash.edu.my"]), (DEAKIN[1], ["deakin.edu.my"])]),
        )

        resp = await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        assert await _sites_by_domain(session, next_list) == {
            "monash.edu": ["monash.edu.my"], "deakin.edu.au": [],
        }

    async def test_clearing_a_rivals_sites_that_a_scan_holds_makes_a_list_and_leaves_the_scan(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """The sites are on the set a scan ran with: clearing them is for the NEXT list."""
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        own = await _add_set(session, scan_id, MONASH, sites={MONASH[1]: ["monash.edu.my"]})
        before = await _rows(session, own)

        resp = await _put(client, cid, _body())

        assert resp.status_code == 200, resp.text
        assert resp.json()["confirmed"] == []
        [next_list] = await _next_list_ids(session, cid)
        assert await _sites_by_domain(session, next_list) == {"monash.edu": []}
        assert await _rows(session, own) == before

    async def test_a_scan_that_has_started_keeps_its_own_set_whatever_is_saved_later(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH, DEAKIN)
        await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))
        [saved] = await _next_list_ids(session, cid)

        _scan, adopted = await _adopt(session, cid)
        assert adopted == saved and await _next_list_ids(session, cid) == []
        adopted_rows = await _rows(session, adopted)
        resp = await _put(
            client, cid,
            _body(rivals=[(MONASH[1], ["monash.com.sg"]), (DEAKIN[1], ["deakin.edu.my"])]),
        )

        assert resp.status_code == 200, resp.text
        [newer] = await _next_list_ids(session, cid)
        assert newer != adopted
        assert await _sites_by_domain(session, newer) == {
            "monash.edu": ["monash.com.sg"], "deakin.edu.au": ["deakin.edu.my"],
        }
        assert await _rows(session, adopted) == adopted_rows
        assert (await _sites_by_domain(session, adopted))["monash.edu"] == ["monash.edu.my"]

    async def test_the_scan_that_starts_next_takes_the_saved_sites_with_the_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH)
        await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))

        _scan, adopted = await _adopt(session, cid)

        assert await _sites_by_domain(session, adopted) == {"monash.edu": ["monash.edu.my"]}
        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        assert [c["domain"] for c in got["confirmed"]] == ["monash.edu.my"]

    async def test_a_client_with_no_set_can_still_save_its_own_lists_but_has_no_rivals_to_name(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)

        own = await _put(client, cid, _body(own=["rmit.edu.vn"]))
        rival = await _put(client, cid, _body(rivals=[(MONASH[1], [])]))

        assert own.status_code == 200, own.text
        assert rival.status_code == 422, rival.text
        assert await _lists(session, cid) == (["rmit.edu.vn"], [])
        assert await _next_list_ids(session, cid) == []


# --- carried wherever a rival's row is copied or rewritten --------------------------------------


class TestCarryingARivalsSites:
    def test_copy_rows_copies_the_sites_and_not_the_list(self) -> None:
        source = CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), competitors=[])
        original = ["monash.edu.my", "monash.com.sg"]
        source.competitors.append(
            Competitor(
                id=ids.new_id(ids.COMPETITOR), competitor_set_id=source.id, name=MONASH[0],
                domain=MONASH[1], rank=1, detection_source=DetectionSource.MANUAL,
                signal_count=0, serp_mentions=0, co_citation_mentions=0, corroborated=False,
                is_manual_override=True, is_suppressed=False, other_sites=original,
            )
        )
        copy = CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), competitors=[])

        detection._copy_rows(copy, source)  # noqa: SLF001

        [row] = copy.competitors
        assert row.other_sites == ["monash.edu.my", "monash.com.sg"]
        assert row.other_sites is not original
        row.other_sites.append("monash.nl")
        assert original == ["monash.edu.my", "monash.com.sg"]

    async def test_a_scan_with_no_next_list_carries_the_latest_sets_sites_forward(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH, sites={MONASH[1]: ["monash.edu.my"]})
        later = await _add_scan(session, cid, ScanStatus.QUEUED)
        scan = (await session.execute(select(Scan).where(Scan.id == later))).scalar_one()
        owner = (await session.execute(select(Client).where(Client.id == cid))).scalar_one()

        carried, how = await detection.ensure_set_for_scan(session, scan, owner)
        await session.commit()

        assert how == "carried_forward" and carried is not None
        assert await _sites_by_domain(session, carried.id) == {"monash.edu": ["monash.edu.my"]}

    async def test_a_competitor_save_that_keeps_a_rival_keeps_its_sites(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(session, scan_id, MONASH, DEAKIN)
        await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))

        # Reordered, one added, one struck: Monash is still Monash, at the same domain.
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(client, cid, {"competitors": [
                {"name": "Intercom", "domain": "intercom.com"},
                {"name": MONASH[0], "domain": MONASH[1]},
            ]}),
        )

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        assert await _sites_by_domain(session, next_list) == {
            "intercom.com": [], "monash.edu": ["monash.edu.my"],
        }

    async def test_the_first_competitor_save_keeps_the_sites_a_scans_set_holds(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """No next list yet: the save copies the latest set (sites and all), then rewrites it."""
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(
            session, scan_id, MONASH, DEAKIN, sites={MONASH[1]: ["monash.edu.my"]}
        )

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": MONASH[0], "domain": MONASH[1]}]}
            ),
        )

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        rows = await _rows(session, next_list)
        assert {r[1]: (r[2], r[3]) for r in rows} == {
            "monash.edu": (["monash.edu.my"], False), "deakin.edu.au": ([], True),
        }

    async def test_a_competitor_save_that_changes_a_rivals_domain_clears_its_sites(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """They were confirmed against the old domain."""
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH)
        await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": MONASH[0], "domain": "monash.edu.au"}]}
            ),
        )

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        assert await _sites_by_domain(session, next_list) == {"monash.edu.au": []}
        # And what was cleared is not listed as confirmed.
        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        assert got["confirmed"] == []

    async def test_a_competitor_save_that_drops_the_domain_clears_its_sites(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH)
        await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors", json=await _versioned(
                client, cid, {"competitors": [{"name": MONASH[0]}]}
            )
        )

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        [row] = await _rows(session, next_list)
        assert row[1] is None and row[2] == []

    @pytest.mark.parametrize(
        ("old", "new", "kept"),
        [
            # The same host, spelled the same way or another.
            (None, None, True),
            ("acme.com", "acme.com", True),
            ("acme.com", "ACME.com", True),
            ("bücher.com", "xn--bcher-kva.com", True),
            ("xn--bcher-kva.com", "bücher.com", True),
            ("BÜCHER.com", "xn--bcher-kva.com", True),
            ("пример.рф", "пример.xn--p1ai", True),
            # A name that cannot be put in DNS form is compared as it is.
            ("a_b.com", "a_b.com", True),
            # Another host, or none.
            ("acme.com", "acme.de", False),
            ("bücher.com", "bücher.de", False),
            ("bücher.com", "bucher.com", False),
            ("xn--bcher-kva.com", "xn--bcher-kva.de", False),
            ("acme.com", None, False),
            (None, "acme.com", False),
        ],
    )
    def test_a_rivals_sites_stay_while_its_host_stays_in_either_spelling(
        self, old: str | None, new: str | None, kept: bool
    ) -> None:
        row = Competitor(
            id=ids.new_id(ids.COMPETITOR), name="Rival", domain=old, rank=1,
            other_sites=["xn--bcher-kva.at"],
        )

        detection._forget_other_sites_if_the_domain_moved(row, new)  # noqa: SLF001

        assert row.other_sites == (["xn--bcher-kva.at"] if kept else [])

    @pytest.mark.parametrize(
        ("stored", "saved"),
        [("bücher.com", "xn--bcher-kva.com"), ("xn--bcher-kva.com", "bücher.com")],
    )
    async def test_a_competitor_save_under_the_other_spelling_of_the_same_host_keeps_its_sites(
        self, client: AsyncClient, session: AsyncSession, stored: str, saved: str
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(
            session, scan_id, ("Bücher Verlag", stored), sites={stored: ["xn--bcher-kva.at"]}
        )

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Bücher Verlag", "domain": saved}]}
            ),
        )

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        # The row now has the domain as it was typed, and the sites it had.
        assert await _sites_by_domain(session, next_list) == {saved: ["xn--bcher-kva.at"]}
        got = (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()
        assert [(c["domain"], c["brandDomain"]) for c in got["confirmed"]] == [
            ("xn--bcher-kva.at", saved)
        ]

    @pytest.mark.parametrize("moved_to", ["xn--bcher-kva.de", "bücher.de", "bucher.com"])
    async def test_a_competitor_save_that_moves_a_rival_to_another_host_clears_its_sites(
        self, client: AsyncClient, session: AsyncSession, moved_to: str
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        await _add_set(
            session, scan_id, ("Bücher Verlag", "bücher.com"),
            sites={"bücher.com": ["xn--bcher-kva.at"]},
        )

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Bücher Verlag", "domain": moved_to}]}
            ),
        )

        assert resp.status_code == 200, resp.text
        [next_list] = await _next_list_ids(session, cid)
        assert await _sites_by_domain(session, next_list) == {moved_to: []}
        assert (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()["confirmed"] == []

    async def test_a_struck_rival_re_added_with_the_same_domain_gets_its_sites_back(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Struck rows are kept, sites and all; the same rival back is the same row."""
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH, DEAKIN)
        await _put(client, cid, _body(rivals=[(MONASH[1], ["monash.edu.my"])]))
        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": DEAKIN[0], "domain": DEAKIN[1]}]}
            ),
        )
        [next_list] = await _next_list_ids(session, cid)
        struck = {r[1]: r for r in await _rows(session, next_list)}["monash.edu"]
        assert struck[3] is True and struck[2] == ["monash.edu.my"]
        assert (await client.get(f"{BASE}/clients/{cid}/other-sites")).json()["confirmed"] == []

        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(client, cid, {"competitors": [
                {"name": MONASH[0], "domain": MONASH[1]}, {"name": DEAKIN[0], "domain": DEAKIN[1]}
            ]}),
        )

        assert (await _sites_by_domain(session, next_list))["monash.edu"] == ["monash.edu.my"]

    async def test_a_new_rival_starts_with_no_sites(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH)

        await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, {"competitors": [{"name": "Intercom", "domain": "intercom.com"}]}
            ),
        )

        [next_list] = await _next_list_ids(session, cid)
        assert (await _sites_by_domain(session, next_list)) == {"intercom.com": []}


class TestDetectionLeavesARivalsSitesAlone:
    async def _detected(
        self, client: AsyncClient, stub_discovery, domain: str, name: str  # noqa: ANN001
    ) -> tuple[str, str]:
        await _sign_up(client)
        cid = await _make_client(client)
        stub_discovery(serp_domains=[domain], cocit_brands=[(name, domain)])
        resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
        assert resp.status_code == 201, resp.text
        return cid, resp.json()["id"]

    async def test_a_rival_found_again_keeps_its_sites_and_a_new_one_has_none(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, a_day_passes  # noqa: ANN001
    ) -> None:
        cid, next_list = await self._detected(client, stub_discovery, "acme.com", "Acme")
        saved = await _put(client, cid, _body(rivals=[("acme.com", ["acme.co.uk"])]))
        assert saved.status_code == 200, saved.text
        await a_day_passes()
        stub_discovery(
            serp_domains=["acme.com", "globex.com"],
            cocit_brands=[("Acme", "acme.com"), ("Globex", "globex.com")],
        )

        again = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert again.status_code == 201, again.text
        assert again.json()["id"] == next_list
        sites = await _sites_by_domain(session, next_list)
        assert sites == {"acme.com": ["acme.co.uk"], "globex.com": []}

    async def test_a_rival_found_again_under_the_other_spelling_of_its_host_keeps_its_sites(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, a_day_passes  # noqa: ANN001
    ) -> None:
        cid, next_list = await self._detected(client, stub_discovery, "bücher.com", "Bücher")
        saved = await _put(client, cid, _body(rivals=[("bücher.com", ["bücher.at"])]))
        assert saved.status_code == 200, saved.text
        assert await _sites_by_domain(session, next_list) == {"bücher.com": ["xn--bcher-kva.at"]}
        await a_day_passes()
        stub_discovery(
            serp_domains=["xn--bcher-kva.com"], cocit_brands=[("Bücher", "xn--bcher-kva.com")]
        )

        again = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert again.status_code == 201, again.text
        assert again.json()["id"] == next_list
        # Detection gave it the domain in the other spelling, and its sites stayed.
        assert await _sites_by_domain(session, next_list) == {
            "xn--bcher-kva.com": ["xn--bcher-kva.at"]
        }

    async def test_a_rival_found_again_under_another_domain_loses_the_sites_of_the_old_one(
        self, client: AsyncClient, session: AsyncSession, stub_discovery, a_day_passes  # noqa: ANN001
    ) -> None:
        cid, next_list = await self._detected(client, stub_discovery, "acme.com", "Acme")
        await _put(client, cid, _body(rivals=[("acme.com", ["acme.co.uk"])]))
        await a_day_passes()
        stub_discovery(serp_domains=["acme.de"], cocit_brands=[("Acme", "acme.de")])

        again = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert again.status_code == 201, again.text
        assert await _sites_by_domain(session, next_list) == {"acme.de": []}


# --- a rival at a domain already confirmed as another brand's other site ------------------------

SAVE_REFUSED = "One or more fields were invalid."


def _rivals(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"competitors": [{"name": name, "domain": domain} for name, domain in pairs]}


class TestTheEditorRefusesAConfirmedOtherSite:
    """`PUT /clients/{clientId}/competitors` (the founder, 2026-10-09): 422 before any write."""

    async def _setup(
        self, client: AsyncClient, session: AsyncSession
    ) -> tuple[str, dict[str, Any]]:
        """RMIT with `rmit.edu.vn` confirmed, and Monash (a rival) with `monash.edu.my`."""
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH, DEAKIN)
        saved = await _put(
            client, cid, _body(own=["rmit.edu.vn"], rivals=[(MONASH[1], ["monash.edu.my"])])
        )
        assert saved.status_code == 200, saved.text
        return cid, await _everything(session, cid)

    def _only_error(self, resp: Any) -> dict[str, Any]:
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["type"] == VALIDATION and body["detail"] == SAVE_REFUSED
        [error] = body["errors"]
        return error

    async def test_a_rival_at_the_clients_confirmed_site_is_refused_and_nothing_is_written(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=_rivals(MONASH, ("RMIT Vietnam", "rmit.edu.vn")),
        )

        assert self._only_error(resp) == {
            "field": "competitors.1.domain",
            "message": (
                "rmit.edu.vn is confirmed as RMIT University's other site. "
                "Remove it there first."
            ),
            "type": "confirmed_other_site",
        }
        assert await _everything(session, cid) == before

    async def test_a_rival_at_a_kept_rivals_confirmed_site_is_refused_with_that_rivals_name(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=_rivals(("Monash Malaysia", "monash.edu.my"), MONASH),
        )

        error = self._only_error(resp)
        assert error["field"] == "competitors.0.domain"
        assert error["message"] == (
            "monash.edu.my is confirmed as Monash University's other site. Remove it there first."
        )
        assert error["type"] == "confirmed_other_site"
        assert await _everything(session, cid) == before

    async def test_each_offending_rival_is_named_by_its_index(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, before = await self._setup(client, session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=_rivals(
                ("RMIT Vietnam", "rmit.edu.vn"), DEAKIN, MONASH,
                ("Monash Malaysia", "monash.edu.my"),
            ),
        )

        assert resp.status_code == 422, resp.text
        assert [(e["field"], e["type"]) for e in resp.json()["errors"]] == [
            ("competitors.0.domain", "confirmed_other_site"),
            ("competitors.3.domain", "confirmed_other_site"),
        ]
        assert await _everything(session, cid) == before

    async def test_the_comparison_is_on_the_dns_form_and_the_message_keeps_the_spelling_sent(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "bücher.de", "Bücher")
        await _add_set(session, await _add_scan(session, cid), MONASH)
        assert (await _put(client, cid, _body(own=["bücher.at"]))).status_code == 200
        before = await _everything(session, cid)
        assert before["lists"][0] == ["xn--bcher-kva.at"]

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors", json=_rivals(MONASH, ("Bücher Wien", "bücher.at"))
        )

        error = self._only_error(resp)
        assert error["message"] == (
            "bücher.at is confirmed as Bücher's other site. Remove it there first."
        )
        assert await _everything(session, cid) == before

    async def test_a_rival_struck_in_the_same_save_no_longer_holds_its_sites(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Monash is not kept, so `monash.edu.my` is nobody's confirmed site on the new list."""
        cid, _ = await self._setup(client, session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors", json=await _versioned(
                client, cid, _rivals(("Monash Malaysia", "monash.edu.my"))
            )
        )

        assert resp.status_code == 200, resp.text

    async def test_a_rival_moved_to_another_host_in_the_same_save_carries_no_sites(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Monash kept at ANOTHER host loses its sites (`_apply_manual`), so none is refused."""
        cid, _ = await self._setup(client, session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, _rivals(("Monash", "monash.com"), ("Monash Malaysia", "monash.edu.my"))
            ),
        )

        assert resp.status_code == 200, resp.text

    async def test_a_rival_that_is_not_a_confirmed_site_saves_as_before(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        cid, _ = await self._setup(client, session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(
                client, cid, _rivals(MONASH, DEAKIN, ("Intercom", "intercom.com"))
            ),
        )

        assert resp.status_code == 200, resp.text
        assert [c["domain"] for c in resp.json()["competitors"]] == [
            MONASH[1], DEAKIN[1], "intercom.com"
        ]

    async def test_a_client_that_confirmed_nothing_refuses_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(client, cid, _rivals(MONASH, ("RMIT Vietnam", "rmit.edu.vn"))),
        )

        assert resp.status_code == 200, resp.text


class TestDetectionSkipsAConfirmedOtherSite:
    async def test_a_candidate_at_a_confirmed_site_is_not_written_and_is_logged_once(
        self, client: AsyncClient, session: AsyncSession, stub_discovery  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH)
        saved = await _put(
            client, cid, _body(own=["rmit.edu.vn"], rivals=[(MONASH[1], ["monash.edu.my"])])
        )
        assert saved.status_code == 200, saved.text
        found = [
            ("RMIT Vietnam", "rmit.edu.vn"), ("Monash Malaysia", "monash.edu.my"),
            ("Globex", "globex.com"),
        ]
        stub_discovery(serp_domains=[d for _n, d in found], cocit_brands=found)

        with structlog.testing.capture_logs() as events:
            resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert resp.status_code == 201, resp.text
        domains = [c["domain"] for c in resp.json()["competitors"]]
        assert "globex.com" in domains
        assert "rmit.edu.vn" not in domains and "monash.edu.my" not in domains
        skipped = [e for e in events if e["event"] == "competitors.skipped"]
        assert sorted((e["domain"], e["reason"], e["client_id"]) for e in skipped) == [
            ("monash.edu.my", "confirmed_other_site", cid),
            ("rmit.edu.vn", "confirmed_other_site", cid),
        ]
        assert "@" not in repr(skipped)

    async def test_a_confirmed_site_in_the_other_spelling_is_skipped_too(
        self, client: AsyncClient, session: AsyncSession, stub_discovery  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client, "bücher.de", "Bücher")
        await _add_set(session, await _add_scan(session, cid), MONASH)
        assert (await _put(client, cid, _body(own=["bücher.at"]))).status_code == 200
        found = [("Bücher Wien", "xn--bcher-kva.at"), ("Globex", "globex.com")]
        stub_discovery(serp_domains=[d for _n, d in found], cocit_brands=found)

        with structlog.testing.capture_logs() as events:
            resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert resp.status_code == 201, resp.text
        assert "xn--bcher-kva.at" not in [c["domain"] for c in resp.json()["competitors"]]
        skipped = [e for e in events if e["event"] == "competitors.skipped"]
        assert [e["domain"] for e in skipped] == ["xn--bcher-kva.at"]

    async def test_a_client_with_nothing_confirmed_skips_nothing_and_logs_nothing(
        self, client: AsyncClient, session: AsyncSession, stub_discovery  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        found = [("RMIT Vietnam", "rmit.edu.vn"), ("Globex", "globex.com")]
        stub_discovery(serp_domains=[d for _n, d in found], cocit_brands=found)

        with structlog.testing.capture_logs() as events:
            resp = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert resp.status_code == 201, resp.text
        assert {"rmit.edu.vn", "globex.com"} <= {c["domain"] for c in resp.json()["competitors"]}
        assert [e for e in events if e["event"] == "competitors.skipped"] == []


# --- what is logged, and what the schema holds --------------------------------------------------


class TestTheLogLine:
    async def test_a_save_logs_one_line_with_the_client_id_and_counts_only(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client, "someone.private@test.example")
        cid = await _make_client(client)
        await _add_set(session, await _add_scan(session, cid), MONASH)

        with structlog.testing.capture_logs() as events:
            resp = await _put(
                client, cid,
                _body(
                    own=["rmit.edu.vn", "rmit.edu.my"],
                    rivals=[(MONASH[1], ["monash.edu.my"])],
                    dismissed=["wikipedia.org"],
                ),
            )
        assert resp.status_code == 200, resp.text

        [saved] = [e for e in events if e["event"] == "other_sites.saved"]
        assert saved["client_id"] == cid
        counts = {k: v for k, v in saved.items() if k not in ("event", "log_level", "client_id")}
        assert counts == {"client_sites": 2, "rival_sites": 1, "dismissed": 1}
        assert all(type(v) is int for v in counts.values())
        everything = repr(events)
        for private in ("rmit.edu.vn", "monash.edu.my", "wikipedia.org", "someone.private"):
            assert private not in everything

    async def test_a_refused_save_and_a_read_log_no_such_line(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        await _add_scan(session, cid)

        with structlog.testing.capture_logs() as events:
            refused = await _put(client, cid, _body(own=["wikipedia.org"]))
            read = await client.get(f"{BASE}/clients/{cid}/other-sites")

        assert (refused.status_code, read.status_code) == (422, 200)
        assert [e for e in events if e["event"] == "other_sites.saved"] == []


class TestTheSchema:
    async def test_the_three_lists_are_json_not_null_and_empty_by_default(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        found = (
            await session.execute(
                text(
                    "select table_name, column_name, data_type, is_nullable, column_default "
                    "from information_schema.columns "
                    "where (table_name, column_name) in ("
                    "('clients', 'other_sites'), ('clients', 'dismissed_other_sites'), "
                    "('competitors', 'other_sites')) order by 1, 2"
                )
            )
        ).all()

        assert [tuple(r) for r in found] == [
            ("clients", "dismissed_other_sites", "jsonb", "NO", "'[]'::jsonb"),
            ("clients", "other_sites", "jsonb", "NO", "'[]'::jsonb"),
            ("competitors", "other_sites", "jsonb", "NO", "'[]'::jsonb"),
        ]

    async def test_a_new_client_and_a_rival_written_without_the_column_start_with_nothing(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        scan_id = await _add_scan(session, cid)
        cset = CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan_id)
        session.add(cset)
        await session.flush()
        # Written without the column, as an instance that predates it would: the database fills it.
        await session.execute(
            text(
                "insert into competitors (id, competitor_set_id, name, rank, detection_source, "
                "signal_count, serp_mentions, co_citation_mentions, corroborated, "
                "is_manual_override, is_suppressed) "
                "values ('comp_old', :set_id, 'Old', 1, 'manual', 0, 0, 0, false, false, false)"
            ),
            {"set_id": cset.id},
        )
        await session.commit()

        assert await _lists(session, cid) == ([], [])
        assert (
            await session.execute(
                select(Competitor.other_sites).where(Competitor.id == "comp_old")
            )
        ).scalar_one() == []

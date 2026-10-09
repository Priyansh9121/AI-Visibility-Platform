"""The report names the brand's confirmed other sites; the CSV attributes them (M1, 1.3).

After task 1.2 a citation of a CONFIRMED other site is stored as the client's own
(`cites_subject` True). The report says so where a reader sees the evidence: under the "Cited
RMIT University" table, "RMIT University's other sites: rmit.edu.vn — 4 citations", the same
sentence on the web and the PDF (`report_narrative.other_sites_line`, `otherSitesLine` in
`derive.ts`, both read against `other-sites-lines.json`). The CSV's `Attributed to` names the brand.

Held here, on RMIT's shape (rmit.edu.au, its confirmed rmit.edu.vn, a rival's confirmed
monash.edu.my, a stranger):

* `subjectCitedDomains` keeps only the client's own domain, as the scan typed it; every other
  entry the scan stored as the client's own is `subjectOtherSites`, most cited first, then domain;
* `subjectCitations` (the total) is unchanged;
* a scan from before v8 has no such entry, so its report is what it was, and a failed scan's
  report gives `[]`;
* the PDF and the web print the sentence from one table of cases;
* the CSV row of the confirmed site reads the client's name; a rival's confirmed site, the rival's.
"""

from __future__ import annotations

import json
import re
import zlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from avp_api.schemas.report import ReportOut
from avp_api.services import report_narrative as narrative
from avp_api.services.report import build_report
from avp_api.services.report_csv import report_rows
from avp_api.services.report_pdf import render_report_pdf

BASE = "/api/v1"
FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
_TABLE: dict[str, Any] = json.loads(
    (FIXTURES / "other-sites-lines.json").read_text(encoding="utf-8")
)
LINES: list[dict[str, Any]] = _TABLE["cases"]
EMPTY_MESSAGES: list[dict[str, Any]] = _TABLE["emptyMessages"]
OLD_EMPTY = "No answer cited RMIT University's own pages in this scan."
NEW_EMPTY = "No answer cited rmit.edu.au."
RMIT_LINE = "RMIT University's other sites: rmit.edu.vn — 4 citations"


def _visible_text(pdf: bytes) -> str:
    """Every string the document draws, as `test_report_pdf._visible_text` reads it."""
    out: list[str] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for literal in re.finditer(rb"\((.*?)\) Tj", content, re.S):
            raw = re.sub(
                rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), literal.group(1)
            )
            raw = raw.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            out.append(raw.decode("cp1252", errors="replace"))
    return re.sub(r"[^\S ]+", " ", " ".join(out))


# --- the helper, against the table the web reads --------------------------------------------------


@pytest.mark.parametrize("spec", LINES, ids=[c["case"] for c in LINES])
def test_the_line_is_the_same_sentence_the_web_prints(spec: dict[str, Any]) -> None:
    assert narrative.other_sites_line(spec["subject"], spec["rows"]) == spec["expected"]


def test_the_table_covers_one_two_and_none() -> None:
    expected = [c["expected"] for c in LINES]
    assert None in expected
    assert any(e and "1 citation" in e and "1 citations" not in e for e in expected)
    assert any(e and e.count(" — ") >= 2 and "; " in e for e in expected)


@pytest.mark.parametrize("spec", EMPTY_MESSAGES, ids=[c["case"] for c in EMPTY_MESSAGES])
def test_the_empty_message_is_the_same_sentence_the_web_prints(spec: dict[str, Any]) -> None:
    got = narrative.own_pages_empty_message(
        spec["domain"], spec["ownCount"], spec["otherSitesCount"]
    )
    assert got == spec["expected"]


def _pdf_for(report: ReportOut, own: list[dict[str, Any]], other: list[dict[str, Any]]) -> str:
    data = report.model_dump(by_alias=True, mode="json")
    data["proof"]["subjectCitedDomains"] = own
    data["proof"]["subjectOtherSites"] = other
    return _visible_text(render_report_pdf(ReportOut.model_validate(data)))


class TestThePdfEmptyMessage:
    SITE = {"domain": "rmit.edu.vn", "citations": 4, "citesSubject": True,
            "competitorName": None, "sampleUrl": None}
    OWN = {**SITE, "domain": "rmit.edu.au", "citations": 2}

    async def test_own_empty_with_other_sites_says_the_domain_was_not_cited(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        text = _pdf_for(await _rmit_report(client, session), [], [self.SITE])
        assert NEW_EMPTY in text
        assert "own pages in this scan" not in text

    async def test_both_empty_keeps_todays_sentence(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        text = _pdf_for(await _rmit_report(client, session), [], [])
        assert OLD_EMPTY in text
        assert NEW_EMPTY not in text

    async def test_own_cited_prints_no_empty_message(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        text = _pdf_for(await _rmit_report(client, session), [self.OWN], [self.SITE])
        assert NEW_EMPTY not in text and "own pages in this scan" not in text


# --- the report builder, on RMIT's shape -------------------------------------------------------


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Sites", "fullName": "Op", "email": "sites@test.example",
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def _rmit(client: AsyncClient) -> str:
    resp = await client.post(
        f"{BASE}/clients",
        json={"url": "rmit.edu.au", "name": "RMIT University", "classify": False},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _scan(
    session: AsyncSession, client_id: str, *, status: ScanStatus = ScanStatus.SUCCEEDED,
    matcher: str | None = None,
) -> Scan:
    owner = (
        await session.execute(select(Client.agency_id).where(Client.id == client_id))
    ).scalar_one()
    scan = Scan(id=ids.new_id(ids.SCAN), client_id=client_id, agency_id=owner, status=status)
    session.add(scan)
    await session.flush()
    if matcher is not None:
        scan.matcher_version = matcher
    await session.commit()
    return scan


async def _monash(session: AsyncSession, scan_id: str) -> str:
    cset = CompetitorSet(
        id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan_id, status=DetectionStatus.OK,
        detection_confidence=Decimal("1"), serp_queries_run=6, candidates_considered=9,
        detected_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(cset)
    await session.flush()
    rival = Competitor(
        id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name="Monash University",
        domain="monash.edu", rank=1, detection_source=DetectionSource.BOTH, signal_count=7,
        serp_mentions=4, co_citation_mentions=3, corroborated=True,
        other_sites=["monash.edu.my"],
    )
    session.add(rival)
    await session.commit()
    return rival.id


async def _cite(
    session: AsyncSession, scan_id: str, rows: list[tuple[str, int, bool, str | None]]
) -> None:
    """(domain, times, cites_subject, competitor_id): one answered result per citation."""
    pset = ids.new_id(ids.PROMPT_SET)
    session.add(PromptSet(id=pset, scan_id=scan_id))
    await session.flush()
    n = 0
    for domain, times, cites_subject, rival_id in rows:
        for _ in range(times):
            n += 1
            prompt = Prompt(
                id=ids.new_id(ids.PROMPT), prompt_set_id=pset, text=f"q{n}",
                intent=PromptIntent.AWARENESS, position=n,
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
                    id=ids.new_id(ids.CITATION), engine_result_id=result.id,
                    source_domain=domain, source_url=f"https://{domain}/p",
                    source_type=CitationType.OWNED if cites_subject else CitationType.OTHER,
                    position=1, cites_subject=cites_subject, competitor_id=rival_id,
                )
            )
    await session.commit()


async def _rmit_report(client: AsyncClient, session: AsyncSession) -> ReportOut:
    await _sign_up(client)
    cid = await _rmit(client)
    scan = await _scan(session, cid)
    monash_id = await _monash(session, scan.id)
    await _cite(
        session, scan.id,
        [
            ("rmit.edu.au", 2, True, None),
            ("rmit.edu.vn", 4, True, None),
            ("monash.edu.my", 3, False, monash_id),
            ("g2.com", 1, False, None),
        ],
    )
    return await build_report(session, scan)


class TestTheFoundersCase:
    async def test_the_confirmed_site_is_split_from_the_own_domain(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        report = await _rmit_report(client, session)

        own = [(d.domain, d.citations) for d in report.proof.subject_cited_domains]
        other = [(d.domain, d.citations) for d in report.proof.subject_other_sites]
        assert own == [("rmit.edu.au", 2)]
        assert other == [("rmit.edu.vn", 4)]
        assert all(d.cites_subject for d in report.proof.subject_other_sites)
        # The total is the client's own pages, confirmed sites included: unchanged.
        assert report.proof.subject_citations == 6
        assert report.proof.total_citations == 10
        # The rival's confirmed site is the rival's, in the other table, not here.
        assert [d.domain for d in report.proof.competitor_cited_domains][0] == "monash.edu.my"
        assert "subjectOtherSites" in report.model_dump(by_alias=True)["proof"]

    async def test_the_web_and_the_pdf_print_the_founders_sentence(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        report = await _rmit_report(client, session)

        rows = [d.model_dump() for d in report.proof.subject_other_sites]
        assert narrative.other_sites_line("RMIT University", rows) == RMIT_LINE
        assert RMIT_LINE in _visible_text(render_report_pdf(report))

    async def test_the_csv_names_the_brand_for_its_site_and_the_rival_for_the_rivals(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        report = await _rmit_report(client, session)

        rows = report_rows(report)
        attributed = {r[3]: r[5] for r in rows if r[4] == "Attributed to"}
        counted = {r[3]: r[5] for r in rows if r[4] == "Citations (count)"}
        assert attributed["rmit.edu.vn"] == "RMIT University"
        assert counted["rmit.edu.vn"] == "4"
        assert attributed["rmit.edu.au"] == "RMIT University"
        assert attributed["monash.edu.my"] == "Monash University"
        assert attributed["g2.com"] == "Third party"
        # The report's order: the client's own pages, then its other sites, then the rest.
        order = [r[3] for r in rows if r[4] == "Attributed to"]
        assert order.index("rmit.edu.au") < order.index("rmit.edu.vn") < order.index(
            "monash.edu.my"
        )


class TestOrderAndEdges:
    async def test_other_sites_rank_most_cited_first_then_domain(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        scan = await _scan(session, await _rmit(client))
        await _cite(
            session, scan.id,
            [("rmit.university", 2, True, None), ("rmit.edu.sg", 2, True, None),
             ("rmit.edu.vn", 5, True, None), ("rmit.edu.au", 1, True, None)],
        )

        report = await build_report(session, scan)

        assert [(d.domain, d.citations) for d in report.proof.subject_other_sites] == [
            ("rmit.edu.vn", 5), ("rmit.edu.sg", 2), ("rmit.university", 2),
        ]
        assert [d.domain for d in report.proof.subject_cited_domains] == ["rmit.edu.au"]

    async def test_the_own_domain_is_read_as_the_scan_typed_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Exact, as `classify_citation` compares it: a `www.` spelling is another entry."""
        await _sign_up(client)
        scan = await _scan(session, await _rmit(client))
        await _cite(
            session, scan.id,
            [("rmit.edu.au", 1, True, None), ("www.rmit.edu.au", 1, True, None)],
        )

        report = await build_report(session, scan)

        assert [d.domain for d in report.proof.subject_cited_domains] == ["rmit.edu.au"]
        assert [d.domain for d in report.proof.subject_other_sites] == ["www.rmit.edu.au"]

    async def test_a_scan_from_before_v8_reports_what_it_always_did(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """No stored entry is a confirmed site, so nothing moves and no sentence is printed."""
        await _sign_up(client)
        scan = await _scan(session, await _rmit(client), matcher="v7")
        await _cite(
            session, scan.id,
            [("rmit.edu.au", 3, True, None), ("rmit.edu.vn", 4, False, None),
             ("g2.com", 1, False, None)],
        )

        report = await build_report(session, scan)

        assert report.proof.subject_other_sites == []
        assert [(d.domain, d.citations) for d in report.proof.subject_cited_domains] == [
            ("rmit.edu.au", 3)
        ]
        assert report.proof.subject_citations == 3
        assert "other sites" not in _visible_text(render_report_pdf(report))

    async def test_a_failed_scan_with_no_citations_gives_an_empty_list(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A failed scan keeps the proof facts it has, as for `subjectCitedDomains`; none, none."""
        await _sign_up(client)
        scan = await _scan(session, await _rmit(client), status=ScanStatus.FAILED)

        report = await build_report(session, scan)

        assert report.proof.subject_other_sites == []
        assert report.proof.subject_cited_domains == []

    async def test_the_endpoint_serves_the_field(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await _sign_up(client)
        cid = await _rmit(client)
        scan = await _scan(session, cid)
        await _cite(session, scan.id, [("rmit.edu.vn", 4, True, None)])

        body = (await client.get(f"{BASE}/scans/{scan.id}/report")).json()

        assert body["proof"]["subjectOtherSites"] == [
            {"domain": "rmit.edu.vn", "citations": 4, "citesSubject": True,
             "competitorName": None, "sampleUrl": "https://rmit.edu.vn/p"}
        ]

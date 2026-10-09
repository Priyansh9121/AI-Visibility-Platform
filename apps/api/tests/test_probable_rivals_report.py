"""A probable rival is never the "Get onto" target — F2 (2026-10-09), the founder's decision.

RMIT University's report told the agency to "Get onto deakin.edu.au — the source these answers
keep citing". Deakin is a university the answers named, a rival, not a source. The rule: a domain is
a PROBABLE RIVAL only if (a) it has the client's own restricted ending (edu.au for rmit.edu.au) AND
(b) the scan's answers named a brand whose name matches its label ("Deakin University"). A probable
rival is skipped by "Get onto <domain>", which names the next unclaimed domain; it is suggested on
the client's Competitors page (`test_rival_suggestions.py`) and never in the client-facing report.

Held here, on the founder's own example, built through the REAL pipeline (an answer's text is
counted by the scan, stored as `scans.named_domains`, read by the report) and read from every
surface that derives a fix from `proof.unclaimedCitedDomains`:

* the founder's three tests: deakin.edu.au is skipped and the next domain is named, with the JSON
  report, the narrative the PDF prints, the PDF itself, the CSV and the shared link agreeing (the
  web reads the same words from `probable-rival.json`, in `derive.test.ts`); qilt.edu.au and
  uac.edu.au, edu.au and cited and never named, stay valid targets; and no surface of the
  client-facing report carries a suggestion;
* the rest of the rule: `deakin.com` (not the client's ending) is not skipped, a `.com` client has
  no probable rival, a scan that recorded nothing (NULL, or `{}`) reports exactly as it did, the
  skip happens before the cap, and a domain compares in its DNS form;
* the unclaimed list has one producer and readers that read the report, so a new reader cannot
  quietly bypass the skip.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import re
import zlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api.config import Settings
from avp_api.models import Citation, EngineResult
from avp_api.models.engine_result import CitationType, Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.schemas.report import ReportOut
from avp_api.services import report_narrative as narrative
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.report import MAX_UNCLAIMED_DOMAINS, _proof

BASE = "/api/v1"
SRC = Path(__file__).resolve().parents[1] / "src" / "avp_api"
FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
FIXTURE: dict[str, Any] = json.loads((FIXTURES / "probable-rival.json").read_text(encoding="utf-8"))
CLIENT_NAME, CLIENT_DOMAIN = FIXTURE["client"]["name"], FIXTURE["client"]["domain"]
ENGINES = {"engines": ["claude_search", "chatgpt"]}
BOM = "﻿"

# The founder's example, in three questions that two engines each answer (six answers):
# deakin.edu.au is cited 6 times and named in 4 answers (the first two questions name Deakin
# University); qilt.edu.au is cited 4 times and uac.edu.au twice and no answer names either; the
# client's own pages are cited twice. `probable-rival.json` holds these same numbers.
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

# What must never reach a client-facing surface: the suggestion's words, and the stored fact.
SUGGESTION_WORDS = ("add it as a rival", "add as a rival", "probable rival", "rival-suggestion")
SUGGESTION_KEYS = ("namedDomains", "named_domains", "rivalSuggestion", "probableRival")


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """The engine phase only: these tests are about what the scan stores and the report reads."""
    return engines_only_executor


@pytest.fixture
def csv_on(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "report_csv_enabled", True)


def fake_engines(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, tuple[str, tuple[str, ...]]]
) -> None:
    """Prompt generation and engine answers: each prompt of `plan` is answered by every engine
    with `(text, cited domains)`."""
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


async def sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Report Agency", "fullName": "Op",
              "email": "probable@test.example", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def scan_of(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any],
    name: str = CLIENT_NAME, domain: str = CLIENT_DOMAIN,
) -> str:
    """Sign up, add the client, run one scan through the real pipeline. Returns the scan id."""
    await sign_up(client)
    made = await client.post(
        f"{BASE}/clients", json={"url": domain, "name": name, "classify": False}
    )
    assert made.status_code == 201, made.text
    fake_engines(monkeypatch, plan)
    started = await client.post(f"{BASE}/clients/{made.json()['id']}/scans", json=ENGINES)
    assert started.status_code == 202, started.text
    return str(started.json()["id"])


async def report_of(client: AsyncClient, sid: str) -> dict[str, Any]:
    resp = await client.get(f"{BASE}/scans/{sid}/report")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def unclaimed(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"domain": d["domain"], "citations": d["citations"]}
        for d in report["proof"]["unclaimedCitedDomains"]
    ]


def visible_text(pdf: bytes) -> str:
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


def csv_rows(body: bytes) -> list[list[str]]:
    decoded = body.decode("utf-8")
    assert decoded.startswith(BOM)
    return list(csv.reader(io.StringIO(decoded[1:], newline="")))


def csv_fix_titles(body: bytes) -> list[str]:
    """The fixes' titles, each once (a fix has a row per attribute, all keyed by its title)."""
    return list(dict.fromkeys(row[3] for row in csv_rows(body) if row[0] == "Fixes"))


def stranger(client: AsyncClient) -> AsyncClient:
    """Somebody holding a share link: the same app, no cookie."""
    return AsyncClient(
        transport=ASGITransport(app=client._transport.app),  # noqa: SLF001
        base_url="http://testserver", headers={"Origin": client.headers["Origin"]},
    )


async def shared_token(client: AsyncClient, sid: str) -> str:
    resp = await client.post(f"{BASE}/scans/{sid}/share")
    assert resp.status_code == 200, resp.text
    return str(resp.json()["token"])


async def record(session: AsyncSession, sid: str, value: str) -> None:
    """Overwrite what the scan recorded: SQL NULL, or a JSON document."""
    await session.execute(
        text("UPDATE scans SET named_domains = CAST(:v AS jsonb) WHERE id = :id"),
        {"v": value if value != "NULL" else None, "id": sid},
    )
    await session.commit()


def the_citation_fix(narrative_fixes: list[narrative.Fix]) -> narrative.Fix:
    return next(f for f in narrative_fixes if f.source == "citation")


# ===== the founder's three tests ==============================================================


class TestTheFirstTestDeakinIsSkippedAndEverySurfaceNamesTheNextDomain:
    async def test_the_scan_records_what_the_fixture_says_and_the_report_skips_deakin(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)

        stored = (await session.execute(
            text("SELECT named_domains FROM scans WHERE id = :id"), {"id": sid}
        )).scalar_one()
        assert stored == {"deakin.edu.au": FIXTURE["suggestion"]["answersNaming"],
                          "qilt.edu.au": 0, "uac.edu.au": 0}

        report = await report_of(client, sid)
        assert unclaimed(report) == FIXTURE["unclaimedAfter"]
        assert report["proof"]["subjectCitations"] == FIXTURE["subjectCitations"]
        # The evidence table is a record of what the answers cited, and still lists Deakin.
        cited = {d["domain"]: d["citations"] for d in report["proof"]["competitorCitedDomains"]}
        assert cited["deakin.edu.au"] == FIXTURE["suggestion"]["citations"] == 6

    async def test_the_narrative_the_pdf_prints_names_the_next_domain(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)
        report = await report_of(client, sid)

        fix = the_citation_fix(narrative.derive_narrative(report).fixes)

        assert fix.id == FIXTURE["fix"]["id"]
        assert fix.title == FIXTURE["fix"]["title"]
        assert fix.detail == FIXTURE["fix"]["detail"]
        assert "deakin" not in fix.title and "deakin" not in fix.detail

    async def test_the_pdf_names_the_next_domain_and_never_gets_onto_deakin(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)

        pdf = await client.get(f"{BASE}/scans/{sid}/report.pdf")
        assert pdf.status_code == 200
        words = visible_text(pdf.content)

        assert FIXTURE["fix"]["title"] in words
        assert FIXTURE["fix"]["detail"] in words
        assert "Get onto deakin.edu.au" not in words

    async def test_the_csv_names_the_next_domain_and_never_gets_onto_deakin(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, csv_on: None
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)

        body = await client.get(f"{BASE}/scans/{sid}/report.csv")
        assert body.status_code == 200, body.text
        titles = csv_fix_titles(body.content)

        assert FIXTURE["fix"]["title"] in titles
        assert [t for t in titles if t.startswith("Get onto")] == [FIXTURE["fix"]["title"]]

    async def test_the_shared_link_names_the_next_domain_in_json_and_in_pdf(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)
        token = await shared_token(client, sid)

        async with stranger(client) as visitor:
            shared = await visitor.post(f"{BASE}/reports/shared", json={"token": token})
            assert shared.status_code == 200, shared.text
            pdf = await visitor.post(f"{BASE}/reports/shared/pdf", json={"token": token})
            assert pdf.status_code == 200

        assert unclaimed(shared.json()) == FIXTURE["unclaimedAfter"]
        assert unclaimed(shared.json()) == unclaimed(await report_of(client, sid))
        words = visible_text(pdf.content)
        assert FIXTURE["fix"]["title"] in words
        assert "Get onto deakin.edu.au" not in words

    async def test_every_surface_agrees(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, csv_on: None
    ) -> None:
        """One scan, every reader of the unclaimed list: one title, the same one."""
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)
        report = await report_of(client, sid)
        token = await shared_token(client, sid)

        pdf_words = visible_text((await client.get(f"{BASE}/scans/{sid}/report.pdf")).content)
        csv_titles = csv_fix_titles((await client.get(f"{BASE}/scans/{sid}/report.csv")).content)
        async with stranger(client) as visitor:
            shared = (await visitor.post(f"{BASE}/reports/shared", json={"token": token})).json()
        narrative_title = the_citation_fix(narrative.derive_narrative(report).fixes).title

        titles = {
            "narrative": narrative_title,
            "pdf": next(t for t in re.findall(r"Get onto \S+ — the source", pdf_words)),
            "csv": next(t for t in csv_titles if t.startswith("Get onto")),
            "shared": the_citation_fix(narrative.derive_narrative(shared).fixes).title,
        }
        assert titles["pdf"] == "Get onto qilt.edu.au — the source"
        assert titles["narrative"] == titles["csv"] == titles["shared"] == FIXTURE["fix"]["title"]


class TestTheSecondTestQiltAndUacStayValidTargets:
    async def test_edu_au_domains_the_answers_never_named_are_still_listed_and_named_in_the_fix(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)
        report = await report_of(client, sid)

        listed = [d["domain"] for d in report["proof"]["unclaimedCitedDomains"]]
        assert listed == ["qilt.edu.au", "uac.edu.au"]
        fix = the_citation_fix(narrative.derive_narrative(report).fixes)
        assert fix.title.startswith("Get onto qilt.edu.au")
        assert "The same is true of uac.edu.au." in fix.detail

    async def test_a_domain_that_is_named_is_the_only_one_skipped(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Skipping follows the answers: let one answer name Qilt and it is a probable rival too."""
        plan = copy.deepcopy(RMIT_PLAN)
        plan["best for engineering"] = (
            "RMIT University is strong in engineering, and QILT publishes its surveys.",
            ("deakin.edu.au",),
        )
        sid = await scan_of(client, monkeypatch, plan)

        stored = (await session.execute(
            text("SELECT named_domains FROM scans WHERE id = :id"), {"id": sid}
        )).scalar_one()
        assert stored["qilt.edu.au"] == 2 and stored["uac.edu.au"] == 0
        assert [d["domain"] for d in (await report_of(client, sid))["proof"][
            "unclaimedCitedDomains"
        ]] == ["uac.edu.au"]


class TestTheThirdTestTheReportNeverCarriesTheSuggestion:
    async def test_no_surface_of_the_client_facing_report_carries_it(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, csv_on: None
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)
        token = await shared_token(client, sid)

        agency_json = (await client.get(f"{BASE}/scans/{sid}/report")).text
        pdf_words = visible_text((await client.get(f"{BASE}/scans/{sid}/report.pdf")).content)
        csv_text = (await client.get(f"{BASE}/scans/{sid}/report.csv")).content.decode("utf-8")
        async with stranger(client) as visitor:
            shared_json = (
                await visitor.post(f"{BASE}/reports/shared", json={"token": token})
            ).text
            shared_pdf = visible_text(
                (await visitor.post(f"{BASE}/reports/shared/pdf", json={"token": token})).content
            )

        surfaces = {"agency json": agency_json, "shared json": shared_json, "pdf": pdf_words,
                    "shared pdf": shared_pdf, "csv": csv_text}
        for name, body in surfaces.items():
            assert len(body) > 500, f"{name} was read"
            for word in SUGGESTION_WORDS:
                assert word not in body.lower(), (name, word)
            for key in SUGGESTION_KEYS:
                assert key not in body, (name, key)
        # The suggestion's own sentence, whole, nowhere.
        for name, body in surfaces.items():
            assert FIXTURE["sentence"] not in body, name

    def test_no_report_field_is_named_for_a_suggestion_or_the_stored_fact(self) -> None:
        """The schema the report and the shared link both serve has no field for it."""
        schema = ReportOut.model_json_schema(by_alias=True)
        names = {
            prop
            for definition in [schema, *schema.get("$defs", {}).values()]
            for prop in definition.get("properties", {})
        }
        assert "unclaimedCitedDomains" in names, "the walk found the proof beat"
        assert {n for n in names if re.search(r"suggest|probable|named_?domains", n, re.I)} == set()


# ===== the rest of the rule ===================================================================


class TestNotTheClientsEnding:
    async def test_deakin_com_is_not_skipped_even_when_the_answers_name_deakin(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = {
            "which universities offer design": (
                "Deakin University is a good choice.",
                ("deakin.com", "deakin.edu.au"),
            ),
            "where to study teaching": ("Consider Deakin University.", ("deakin.com",)),
        }
        sid = await scan_of(client, monkeypatch, plan)

        stored = (await session.execute(
            text("SELECT named_domains FROM scans WHERE id = :id"), {"id": sid}
        )).scalar_one()
        assert list(stored) == ["deakin.edu.au"], "deakin.com is not measured at all"
        report = await report_of(client, sid)
        assert [d["domain"] for d in report["proof"]["unclaimedCitedDomains"]] == ["deakin.com"]
        fix = the_citation_fix(narrative.derive_narrative(report).fixes)
        assert fix.title.startswith("Get onto deakin.com")

    async def test_a_com_client_has_no_probable_rival(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .com says nothing about the kind of body a domain is: nothing is skipped, ever."""
        sid = await scan_of(client, monkeypatch, RMIT_PLAN, "Help Scout", "helpscout.com")

        stored = (await session.execute(
            text("SELECT named_domains FROM scans WHERE id = :id"), {"id": sid}
        )).scalar_one()
        assert stored == {}
        report = await report_of(client, sid)
        assert unclaimed(report)[0] == {"domain": "deakin.edu.au", "citations": 6}
        fix = the_citation_fix(narrative.derive_narrative(report).fixes)
        assert fix.title.startswith("Get onto deakin.edu.au")

    async def test_a_school_under_a_state_suffix_is_not_under_edu_au(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """scotch.vic.edu.au has the ending `vic.edu.au`, not `edu.au`: Deakin is not skipped."""
        sid = await scan_of(client, monkeypatch, RMIT_PLAN, "Scotch College", "scotch.vic.edu.au")
        report = await report_of(client, sid)
        # The ending is not edu.au, so nothing is measured and nothing is skipped. (RMIT's own
        # domain is just another source to this client, which is why the list is longer.)
        assert unclaimed(report)[:2] == FIXTURE["unclaimedBefore"][:2]
        fix = the_citation_fix(narrative.derive_narrative(report).fixes)
        assert fix.title.startswith("Get onto deakin.edu.au")


class TestAScanThatRecordedNothing:
    async def test_null_and_empty_report_exactly_as_before_and_apart_from_the_skip_nothing_moves(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = await scan_of(client, monkeypatch, RMIT_PLAN)
        recorded = await report_of(client, sid)

        await record(session, sid, "NULL")
        old = await report_of(client, sid)
        await record(session, sid, "{}")
        empty = await report_of(client, sid)
        await record(session, sid, '{"deakin.edu.au": 0, "qilt.edu.au": 0, "uac.edu.au": 0}')
        zeros = await report_of(client, sid)

        for body in (recorded, old, empty, zeros):
            body.pop("generatedAt")
        # A scan from before the column (NULL), a measurement that found nothing ({}) and one
        # that found nothing named (all 0) report the same thing: the list as it always was.
        assert unclaimed({"proof": old["proof"]}) == FIXTURE["unclaimedBefore"]
        assert old == empty == zeros
        # And the recorded scan differs from them in the unclaimed list alone.
        assert unclaimed(recorded) == FIXTURE["unclaimedAfter"]
        assert {**recorded["proof"], "unclaimedCitedDomains": None} == {
            **old["proof"], "unclaimedCitedDomains": None
        }
        assert {k: v for k, v in recorded.items() if k != "proof"} == {
            k: v for k, v in old.items() if k != "proof"
        }
        fix = the_citation_fix(narrative.derive_narrative(old).fixes)
        assert fix.title == "Get onto deakin.edu.au — the source these answers keep citing"


class TestTheSkipInTheProofBeat:
    """`_proof` as a pure function over rows, with the stored fact passed in."""

    SUBJECT = CLIENT_DOMAIN

    @staticmethod
    def result(n: int, *cited: str) -> EngineResult:
        rid = f"er{n}"
        row = EngineResult(
            id=rid, scan_id="s1", prompt_id=f"p{n}", engine=Engine.CLAUDE,
            status=EngineResultStatus.OK, mentioned=True, position=1,
        )
        row.brand_mentions = []
        row.citations = [
            Citation(
                id=f"{rid}_{i}", engine_result_id=rid, source_domain=domain,
                source_url=f"https://{domain}/p", source_type=CitationType.OTHER, position=i,
                cites_subject=domain == CLIENT_DOMAIN, competitor_id=None,
            )
            for i, domain in enumerate(cited, start=1)
        ]
        return row

    def proof(self, times: dict[str, int], named: object = None) -> Any:
        rows = []
        for domain, count in times.items():
            rows += [self.result(len(rows) + k, domain) for k in range(count)]
        return _proof(rows, None, {}, subject_domain=self.SUBJECT, named_domains=named)

    def domains(self, times: dict[str, int], named: object = None) -> list[str]:
        return [r.domain for r in self.proof(times, named).unclaimed_cited_domains]

    CITED = {"deakin.edu.au": 6, "qilt.edu.au": 4, "uac.edu.au": 2}

    def test_the_heaviest_probable_rival_is_skipped_and_the_next_domain_leads(self) -> None:
        named = {"deakin.edu.au": 4, "qilt.edu.au": 0, "uac.edu.au": 0}
        proof = self.proof(self.CITED, named)
        assert [(r.domain, r.citations) for r in proof.unclaimed_cited_domains] == [
            ("qilt.edu.au", 4), ("uac.edu.au", 2)
        ]
        # What the answers cited stays a fact: the evidence table lists it, and counts it.
        assert "deakin.edu.au" in {r.domain for r in proof.competitor_cited_domains}
        assert proof.total_citations == 12

    @pytest.mark.parametrize("named", [None, {}, {"deakin.edu.au": 0}, [], "x", 3])
    def test_nothing_recorded_or_nothing_named_skips_nothing(self, named: object) -> None:
        assert self.domains(self.CITED, named) == ["deakin.edu.au", "qilt.edu.au", "uac.edu.au"]

    def test_one_answer_naming_a_domain_is_enough(self) -> None:
        assert self.domains(self.CITED, {"deakin.edu.au": 1}) == ["qilt.edu.au", "uac.edu.au"]

    @pytest.mark.parametrize("count", [True, "3", -1, 2.0, None])
    def test_an_entry_that_is_not_a_count_of_answers_skips_nothing(self, count: object) -> None:
        assert self.domains(self.CITED, {"deakin.edu.au": count})[0] == "deakin.edu.au"

    def test_the_skip_comes_before_the_cap_so_the_list_is_still_full(self) -> None:
        cited = {"big.edu.au": 10, "a.example": 9, "b.example": 8, "c.example": 7, "d.example": 6}
        assert len(self.domains(cited, None)) == MAX_UNCLAIMED_DOMAINS
        assert self.domains(cited, {"big.edu.au": 3}) == ["a.example", "b.example", "c.example"]

    def test_every_probable_rival_is_skipped_not_only_the_heaviest(self) -> None:
        named = {"deakin.edu.au": 4, "qilt.edu.au": 2}
        assert self.domains(self.CITED, named) == ["uac.edu.au"]

    def test_with_every_domain_skipped_there_is_no_citation_fix_to_derive(self) -> None:
        named = {"deakin.edu.au": 4, "qilt.edu.au": 2, "uac.edu.au": 1}
        assert self.domains(self.CITED, named) == []

    def test_the_floor_still_applies_after_the_skip(self) -> None:
        cited = {"deakin.edu.au": 6, "once.example": 1}
        assert self.domains(cited, {"deakin.edu.au": 3}) == []

    def test_a_domain_is_matched_on_its_dns_form(self) -> None:
        """`bücher.edu.au` and `xn--bcher-kva.edu.au` are one host, whichever the map keys."""
        unicode_form, ascii_form = "bücher.edu.au", "xn--bcher-kva.edu.au"
        cited = {unicode_form: 3, "qilt.edu.au": 2}
        assert self.domains(cited, {ascii_form: 2}) == ["qilt.edu.au"]
        cited = {ascii_form: 3, "qilt.edu.au": 2}
        assert self.domains(cited, {unicode_form: 2}) == ["qilt.edu.au"]

    def test_only_the_named_host_is_skipped_not_a_domain_that_merely_shares_its_label(self) -> None:
        cited = {"deakin.edu.au": 6, "deakin.com": 5, "deakin.org": 4}
        assert self.domains(cited, {"deakin.edu.au": 4}) == ["deakin.com", "deakin.org"]

    def test_a_rival_the_scan_already_attributes_is_not_in_the_list_either_way(self) -> None:
        """Attribution is unchanged: a detected rival is no unclaimed domain to begin with."""
        row = self.result(1, "deakin.edu.au", "deakin.edu.au")
        for citation in row.citations:
            citation.competitor_id = "comp1"
        assert _proof([row], SimpleNamespace(active_competitors=[
            SimpleNamespace(id="comp1", name="Deakin")
        ]), {}, subject_domain=self.SUBJECT, named_domains=None).unclaimed_cited_domains == []


class TestTheFixesCopyIsUnchanged:
    def test_the_fixtures_two_domains_make_the_fixtures_fix(self) -> None:
        """The words themselves are not F2's: the fix reads the list it is given."""
        title, detail, effort = narrative.fix_for_unclaimed_domains(
            FIXTURE["unclaimedAfter"], FIXTURE["subjectCitations"]
        )  # type: ignore[misc]
        assert (title, detail, effort) == (
            FIXTURE["fix"]["title"], FIXTURE["fix"]["detail"], FIXTURE["fix"]["effort"]
        )

    def test_the_before_list_made_the_fix_the_founder_objected_to(self) -> None:
        title, _detail, _effort = narrative.fix_for_unclaimed_domains(
            FIXTURE["unclaimedBefore"], FIXTURE["subjectCitations"]
        )  # type: ignore[misc]
        assert title == "Get onto deakin.edu.au — the source these answers keep citing"


# ===== who reads the list =====================================================================


class TestEveryReaderOfTheUnclaimedListReadsTheReport:
    """The skip is made where the list is made. These are the only files that name the list, so a
    new reader that built its own would have to appear here, and be given the skip."""

    NAMES = ("unclaimed_cited_domains", "unclaimedCitedDomains")

    def test_the_list_has_one_producer_and_readers_that_read_its_output(self) -> None:
        found = {
            str(path.relative_to(SRC))
            for path in SRC.rglob("*.py")
            if any(name in path.read_text(encoding="utf-8") for name in self.NAMES)
        }
        assert found == {
            "schemas/report.py",  # the field
            "services/report.py",  # the one producer: skips a probable rival
            "services/report_narrative.py",  # the PDF's and the CSV's fix list, from the report
        }

    def test_the_csv_and_the_pdf_take_their_fix_list_from_the_report_and_one_function_makes_it(
        self,
    ) -> None:
        for name in ("report_csv.py", "report_pdf.py"):
            source = (SRC / "services" / name).read_text(encoding="utf-8")
            assert "derive_narrative(" in source, name
        callers = {
            str(path.relative_to(SRC))
            for path in SRC.rglob("*.py")
            if "fix_for_unclaimed_domains" in path.read_text(encoding="utf-8")
        }
        assert callers == {"services/report_narrative.py"}

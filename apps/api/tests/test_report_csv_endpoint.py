"""`GET /scans/{scanId}/report.csv` — the report's figures as a file — F3, 2026-10-06.

The route, the report's `csvExportAvailable` field, the scoping and the refusals. The builder, the
guard and the setting are `test_report_csv.py`'s; the facts-only sweep is `test_facts_only.py`'s.

The founder's brief: from the signed-in report, one CSV of the facts the report already shows.
Off by default. What is held here:

- **Off is off.** With `REPORT_CSV_ENABLED` unset the route is a 404 that says what an unknown
  path says, and the report says the export is not available. Nothing else about the report moves.
- **On, it is the report.** The file is built from the projection `GET /scans/{id}/report`
  serves, so every figure in it is a figure that response holds; the two are compared here.
- **Scoped as the report is.** Another agency's scan is a 404, whatever its status, and never a
  409 that would tell a stranger how somebody else's scan ended. Signed out is a 401.
- **A scan that has no figures to give is a 409** with a type of its own and a plain sentence:
  failed (its score is withheld, as for sharing), queued and running.
- **The field and the route agree.** The report says the export is available exactly when the
  route would give it.
- **Share links get nothing.** No route a token can reach, and the shared report never says the
  export is available.
- **A formula typed into a name, a prompt or a fix title reaches the file as text.**
- **A read.** It writes nothing and logs no name.
"""

from __future__ import annotations

import contextlib
import csv
import inspect
import io
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from avp_api import ids
from avp_api.config import Settings
from avp_api.models import (
    ActionItem,
    ActionItemSource,
    ActionItemStatus,
    AuditStatus,
    BrandMention,
    Citation,
    CitationType,
    ClassificationStatus,
    Client,
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
    Effort,
    Engine,
    EngineResult,
    EngineResultStatus,
    Priority,
    Prompt,
    PromptIntent,
    PromptSet,
    Scan,
    ScanStatus,
    ScanTrigger,
    Sentiment,
    TechnicalAudit,
    TechnicalAuditCheck,
)
from avp_api.models.technical_audit import CheckStatus
from avp_api.routers import report as report_router
from avp_api.services import report_csv, scoring_runner
from avp_api.services.report_narrative import ENGINE_LABEL, derive_narrative

BASE = "/api/v1"
PATH = "/scans/{}/report.csv"
BOM = b"\xef\xbb\xbf"
PASSWORD = "correct-horse-battery-staple"
# What a spreadsheet reads as the start of a formula: OWASP's list, and the full-width forms of the
# four that are visible.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
# A `;`, a tab, a carriage return or a line feed, then optional white space, then one of them: a
# new cell or a new record where a spreadsheet splits on any of them.
AFTER_SEPARATOR = re.compile(r"[;\t\r\n]\s*[=+\-@＝＋－＠]")

SUBJECT = "Help Scout"
SUBJECT_DOMAIN = "helpscout.com"
RIVALS = (("Zendesk", "zendesk.com"), ("Freshdesk", "freshdesk.com"))
PROMPTS = (
    ("what is the best help desk for a small team", PromptIntent.AWARENESS),
    ("help scout vs zendesk", PromptIntent.COMPARISON),
    ("which shared inbox tool should we buy", PromptIntent.BOTTOM_FUNNEL),
)

# What a scan finishing at this moment is named for: the file's date.
SCANNED = datetime(2026, 10, 1, 9, 5, tzinfo=UTC)
# The client, the fixed words and that date, as the PDF's name is built.
FILENAME = "Help-Scout-ai-visibility-2026-10-01.csv"

# The three sentences a refusal gives. Pinned in words, not read back from the code that says
# them: a person at the screen reads these.
FAILED_SENTENCE = "This scan failed, so its figures cannot be exported."
QUEUED_SENTENCE = (
    "This scan has not started yet, so there are no figures to export. "
    "Try again when it has finished."
)
RUNNING_SENTENCE = (
    "This scan is still running, so its figures are not final. Try again when it has finished."
)
# The sentence the file's `Scan status` row gives a partial scan: the PDF cover's, word for word.
PARTIAL_SENTENCE = (
    "This scan is partial: not every planned question was answered, "
    "so these results rest on fewer answers than planned."
)


# ---------------------------------------------------------------------------
# setup: a scan, seeded in whatever status and with whatever names the test needs
# ---------------------------------------------------------------------------


@pytest.fixture
def csv_on(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "report_csv_enabled", True)


async def _sign_up(
    client: AsyncClient, *, email: str = "csv@test.example", agency: str = "CSV Test Agency"
) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": agency, "fullName": "Op", "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["agency"]["id"])


def _another_browser(client: AsyncClient) -> AsyncClient:
    """The same app with its own cookies: a second agency's session, or nobody's."""
    return AsyncClient(
        transport=ASGITransport(app=client._transport.app),  # noqa: SLF001
        base_url="http://testserver",
        headers={"Origin": client.headers["Origin"]},
    )


async def _seed(
    session: AsyncSession,
    agency_id: str,
    *,
    status: ScanStatus = ScanStatus.SUCCEEDED,
    name: str = SUBJECT,
    rivals: tuple[tuple[str, str], ...] = RIVALS,
    prompt_prefix: str = "",
    fix_prefix: str = "",
    domain: str = SUBJECT_DOMAIN,
    scored: bool = True,
    answered: bool = True,
) -> str:
    """A scan of three prompts on two engines, with a competitor set, an audit and fixes.

    The shape is fixed so a figure can be asserted by hand: Claude answered all three and named
    the subject in all three (2nd, 1st, 3rd); ChatGPT answered two, named it in one (1st), had no
    answer to the second and no mention in the first. g2.com is cited three times and belongs to
    no one in the scan, so the report derives the citation fix for it. A finished scan is scored
    by `score_scan`, as the product scores one.
    """
    client = Client(
        id=ids.new_id(ids.CLIENT), agency_id=agency_id, name=name, domain=domain,
        brand_name=name, industry="customer support software",
        classification_status=ClassificationStatus.CLASSIFIED,
    )
    session.add(client)
    await session.flush()
    started = None if status is ScanStatus.QUEUED else datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    finished = (
        SCANNED if status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED) else None
    )
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=agency_id, status=status,
        trigger=ScanTrigger.MANUAL, formula_version="v5", prompt_count=len(PROMPTS),
        engine_result_count=len(PROMPTS) * 2, started_at=started, finished_at=finished,
    )
    session.add(scan)
    await session.flush()

    cset = CompetitorSet(
        id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, status=DetectionStatus.OK,
        detection_confidence=Decimal("0.667"), serp_queries_run=2, co_citation_prompts_run=3,
        candidates_considered=4, detected_at=datetime(2026, 10, 1, 8, 55, tzinfo=UTC),
    )
    session.add(cset)
    await session.flush()
    competitors: dict[str, Competitor] = {}
    for rank, (rival, rival_domain) in enumerate(rivals, start=1):
        competitors[rival_domain] = Competitor(
            id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=rival,
            domain=rival_domain, rank=rank, detection_source=DetectionSource.BOTH,
            signal_count=2, serp_mentions=2, co_citation_mentions=2, corroborated=True,
            score=Decimal("4.500"), is_manual_override=False, is_suppressed=False,
        )
        session.add(competitors[rival_domain])
    await session.flush()
    zendesk, freshdesk = (competitors[d] for _, d in rivals)

    pset = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=scan.id, generated_by="test")
    session.add(pset)
    await session.flush()
    prompts = []
    for position, (text, intent) in enumerate(PROMPTS, start=1):
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=pset.id, text=f"{prompt_prefix}{text}",
            intent=intent, position=position,
        )
        prompts.append(prompt)
        session.add(prompt)
    await session.flush()

    # prompt index, engine -> (status, brands in the order named, subject's sentiment, citations)
    ok, no_mention, error = (
        EngineResultStatus.OK, EngineResultStatus.ANSWERED_NO_MENTION, EngineResultStatus.ERROR,
    )
    named = {
        (0, Engine.CLAUDE): (ok, [zendesk, None, freshdesk], Sentiment.POSITIVE,
                             [domain, "g2.com"]),
        (0, Engine.CHATGPT): (no_mention, [zendesk, freshdesk], None, []),
        (1, Engine.CLAUDE): (ok, [None, zendesk], Sentiment.NEUTRAL, ["zendesk.com", "g2.com"]),
        (1, Engine.CHATGPT): (error, [], None, []),
        (2, Engine.CLAUDE): (ok, [zendesk, freshdesk, None], Sentiment.POSITIVE, []),
        (2, Engine.CHATGPT): (ok, [None], Sentiment.POSITIVE, ["g2.com"]),
    }
    if not answered:
        # No call was answered, so there is nothing to score: the score row says "insufficient
        # data", with every dimension left out, and the file says "Not scored".
        named = {key: (error, [], None, []) for key in named}
    for (index, engine), (result_status, brands, sentiment, cited) in named.items():
        mentioned = None in brands
        result = EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan.id, prompt_id=prompts[index].id,
            engine=engine, engine_version="test", status=result_status, mentioned=mentioned,
            position=brands.index(None) + 1 if mentioned else None, sentiment=sentiment,
            sentiment_confidence=Decimal("0.900") if sentiment else None,
            brands_mentioned=len(brands), latency_ms=800,
        )
        session.add(result)
        await session.flush()
        for position, brand in enumerate(brands, start=1):
            session.add(BrandMention(
                id=ids.new_id(ids.BRAND_MENTION), engine_result_id=result.id,
                entity_name=name if brand is None else brand.name,
                entity_domain=domain if brand is None else brand.domain,
                is_subject=brand is None, competitor_id=None if brand is None else brand.id,
                position=position,
            ))
        for position, cited_domain in enumerate(cited, start=1):
            rival = competitors.get(cited_domain)
            session.add(Citation(
                id=ids.new_id(ids.CITATION), engine_result_id=result.id, source_domain=cited_domain,
                source_url=f"https://{cited_domain}/page", source_type=CitationType.OTHER,
                position=position, cites_subject=cited_domain == domain,
                competitor_id=rival.id if rival else None,
            ))
    await session.flush()

    audit = TechnicalAudit(
        id=ids.new_id(ids.TECHNICAL_AUDIT), scan_id=scan.id, url_audited=f"https://{domain}/",
        status=AuditStatus.OK, technical_foundation=Decimal("71.00"),
        audited_at=datetime(2026, 10, 1, 9, 2, tzinfo=UTC), pages_crawled=1,
        schema_types=["Organization"], has_organization_schema=True, is_indexable=True,
        robots_allows_crawl=True, has_sitemap=True, canonical_present=True,
    )
    session.add(audit)
    await session.flush()
    for key, check_status, code in (
        ("title", CheckStatus.PASS, None), ("schema_faq", CheckStatus.WARN, "NO_FAQ_SCHEMA"),
    ):
        session.add(TechnicalAuditCheck(
            id=ids.new_id(ids.AUDIT_CHECK), audit_id=audit.id, check_key=key, status=check_status,
            detail_code=code,
        ))
    # Generated wording for every fix the report might derive; the report merges the ones it does.
    for rank, (source, key) in enumerate(
        (
            (ActionItemSource.GAP, "mention_rate"), (ActionItemSource.GAP, "share_of_voice"),
            (ActionItemSource.GAP, "citation_strength"), (ActionItemSource.GAP, "sentiment"),
            (ActionItemSource.AUDIT, "schema_faq"),
        ),
        start=1,
    ):
        session.add(ActionItem(
            id=ids.new_id(ids.ACTION_ITEM), scan_id=scan.id, title=f"{fix_prefix}fix for {key}",
            detail=f"A long paragraph of our own advice about {key}, never in the file {rank}",
            priority=Priority.HIGH, effort=Effort.M, status=ActionItemStatus.OPEN, source=source,
            source_key=key, rank=rank, dimension_key=None, points_upside=Decimal("4.00"),
            generated_by="test",
        ))
    await session.commit()

    if scored and status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL):
        fresh = await session.get(Scan, scan.id)
        assert fresh is not None
        await scoring_runner.score_scan(session, fresh)
        await session.commit()
    return scan.id


def parse(body: bytes) -> list[list[str]]:
    assert body.startswith(BOM)
    return list(csv.reader(io.StringIO(body.decode("utf-8")[1:], newline="")))


def section(rows: list[list[str]], name: str) -> list[list[str]]:
    return [row for row in rows if row[0] == name]


async def _report(client: AsyncClient, scan_id: str) -> dict[str, Any]:
    resp = await client.get(f"{BASE}/scans/{scan_id}/report")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


# ---------------------------------------------------------------------------
# off is off
# ---------------------------------------------------------------------------


class TestOffByDefault:
    async def test_the_route_is_a_404_that_says_what_an_unknown_path_says(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)

        off = await client.get(f"{BASE}{PATH.format(sid)}")
        unknown = await client.get(f"{BASE}/scans/{sid}/report.xyz")
        assert off.status_code == unknown.status_code == 404
        assert off.headers["content-type"] == unknown.headers["content-type"]
        a, b = off.json(), unknown.json()
        assert a.pop("instance") == f"{BASE}/scans/{sid}/report.csv"
        assert b.pop("instance") == f"{BASE}/scans/{sid}/report.xyz"
        assert a == b, "off is indistinguishable from a route that is not there"
        assert a["type"] == "/problems/not-found"

    async def test_an_unfinished_or_failed_scan_is_a_404_too_when_it_is_off(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Off says nothing about the scan, so it cannot say how the scan ended."""
        agency_id = await _sign_up(client)
        for status in (ScanStatus.FAILED, ScanStatus.RUNNING, ScanStatus.QUEUED):
            sid = await _seed(session, agency_id, status=status, domain=f"{status.value}.example")
            resp = await client.get(f"{BASE}{PATH.format(sid)}")
            assert resp.status_code == 404, status
            assert resp.json()["detail"] == "Not Found"

    async def test_the_report_says_the_export_is_not_available(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        assert (await _report(client, sid))["csvExportAvailable"] is False

    async def test_switching_it_on_changes_that_one_field_and_nothing_else(
        self, client: AsyncClient, session: AsyncSession, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        off = await _report(client, sid)
        monkeypatch.setattr(settings, "report_csv_enabled", True)
        on = await _report(client, sid)
        for body in (off, on):
            body.pop("generatedAt")
        assert off.pop("csvExportAvailable") is False
        assert on.pop("csvExportAvailable") is True
        assert off == on


# ---------------------------------------------------------------------------
# on: the file
# ---------------------------------------------------------------------------


class TestTheFile:
    async def test_a_finished_scan_gives_a_csv_attachment_that_is_never_cached(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert resp.headers["content-disposition"] == f'attachment; filename="{FILENAME}"'
        assert resp.headers["cache-control"] == "private, no-store"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.content.startswith(BOM + b"Section,Engine,Prompt,Item,Measure,Value\r\n")

    async def test_nothing_typed_into_a_name_reaches_the_name_of_the_file(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        """The client is in the file's name, reduced as the PDF's is: a quote, a semicolon and a
        line break become hyphens, so a header cannot be split by what an operator typed."""
        agency_id = await _sign_up(client)
        name = 'a"; filename="evil.exe\r\nSet-Cookie: x=y'
        sid = await _seed(session, agency_id, name=name)
        resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-disposition"] == (
            'attachment; filename="a-filename-evil.exe-Set-Cookie-x-y-ai-visibility-2026-10-01.csv"'
        )
        assert "evil" not in resp.headers.get("set-cookie", "")

    async def test_a_client_with_nothing_left_of_its_name_is_named_for_the_fixed_words_and_the_date(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id, name="日本語")
        resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-disposition"] == (
            'attachment; filename="ai-visibility-2026-10-01.csv"'
        )

    async def test_every_section_is_there_with_the_rows_this_scan_has(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        rows = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)
        assert rows[0] == ["Section", "Engine", "Prompt", "Item", "Measure", "Value"]
        body = rows[1:]
        assert all(len(row) == 6 for row in rows)
        assert list(report_csv.SECTIONS) == [
            "Scan", "Dimension scores", "Share of voice", "Answers", "Cited domains", "Fixes",
        ]
        assert {row[0] for row in body} == set(report_csv.SECTIONS)
        assert body[0][0] == "Scan", "whose scan it is comes first"

        # The date, the status, the overall score and the two counts of where the questions came
        # from.
        assert len(section(body, "Scan")) == 5
        assert len(section(body, "Dimension scores")) == 5
        # The client and two rivals.
        assert len(section(body, "Share of voice")) == 3
        # Two engines: asked, listed below, received, naming the client, sentiment = 5 each.
        # Six answers: answered (6), named the client (5 answered), position (4 named).
        assert len(section(body, "Answers")) == 2 * 5 + 6 + 5 + 4
        # helpscout.com, zendesk.com and g2.com, each a count and an attribution.
        assert len(section(body, "Cited domains")) == 6
        assert section(body, "Fixes"), "the report's fix list is in it"

    async def test_the_figures_are_this_scans_by_hand(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        body = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)[1:]
        answers = section(body, "Answers")
        answered, named = "Answered (yes/no)", "Named the client (yes/no)"
        position = "Position when named (1 = first)"

        def row(engine: str, prompt: str, measure: str) -> str:
            hits = [r[5] for r in answers if r[1] == engine and r[2] == prompt and r[4] == measure]
            assert len(hits) == 1, (engine, prompt, measure, hits)
            return hits[0]

        first, second, third = (text for text, _ in PROMPTS)
        assert row("Claude", first, position) == "2"
        assert row("Claude", second, position) == "1"
        assert row("Claude", third, position) == "3"
        assert row("ChatGPT", first, named) == "no"
        assert row("ChatGPT", second, answered) == "no", "an error is no answer"
        assert [r for r in answers if r[1] == "ChatGPT" and r[2] == second] == [
            ["Answers", "ChatGPT", second, SUBJECT, answered, "no"]
        ], "and says nothing else of that prompt"
        assert row("ChatGPT", third, position) == "1"

        # By engine for the scan: what each returned, how many are listed below, and the tone.
        def by_engine(engine: str) -> list[list[str]]:
            return [r[4:] for r in answers if r[1] == engine and r[2] == ""]

        assert by_engine("Claude") == [
            ["Prompts asked (count)", "3"],
            ["Prompts listed below (count)", "3"],
            ["Answers received (count)", "3"],
            ["Answers naming the client (count)", "3"],
            ["Sentiment (out of 100)", "83.33"],  # (100 + 50 + 100) / 3
        ]
        assert by_engine("ChatGPT") == [
            ["Prompts asked (count)", "3"],
            ["Prompts listed below (count)", "3"],
            ["Answers received (count)", "2"],
            ["Answers naming the client (count)", "1"],
            ["Sentiment (out of 100)", "100.00"],
        ]

        cited = section(body, "Cited domains")
        assert ["Cited domains", "", "", "g2.com", "Citations (count)", "3"] in cited
        assert ["Cited domains", "", "", "g2.com", "Attributed to", "Third party"] in cited
        assert ["Cited domains", "", "", "zendesk.com", "Attributed to", "Zendesk"] in cited
        # The client's own pages are credited to the client, by name, and never to "Subject".
        assert ["Cited domains", "", "", SUBJECT_DOMAIN, "Attributed to", SUBJECT] in cited
        assert "Subject" not in {cell for r in body for cell in r}

        # The fixes the report derives, with our generated wording where it was written.
        fixes = section(body, "Fixes")
        assert any(r[3] == "Get onto g2.com — the source these answers keep citing" for r in fixes)
        assert any(r[3] == "fix for schema_faq" and r[4] == "Priority" for r in fixes)

    async def test_the_scan_section_names_the_client_the_date_the_status_and_the_score(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        composite = (await _report(client, sid))["score"]["composite"]
        body = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)[1:]
        assert section(body, "Scan") == [
            ["Scan", "", "", SUBJECT, "Scan date", "2026-10-01"],
            ["Scan", "", "", SUBJECT, "Scan status", "Complete"],
            ["Scan", "", "", SUBJECT, "Overall score (out of 100)", composite],
            ["Scan", "", "", SUBJECT, "Questions written by the generator", "Not recorded"],
            ["Scan", "", "", SUBJECT, "Questions from templates", "Not recorded"],
        ]

    async def test_a_scan_stored_with_its_counts_gives_both_in_the_scan_section(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        """F5: how many questions the generator wrote and how many came from templates."""
        from sqlalchemy import update

        from avp_api.models import PromptSet

        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        await session.execute(
            update(PromptSet)
            .where(PromptSet.scan_id == sid)
            .values(generation_params={"fromGenerator": 17, "fromFallback": 3})
        )
        await session.commit()
        body = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)[1:]
        scan = section(body, "Scan")
        assert ["Scan", "", "", SUBJECT, "Questions written by the generator", "17"] in scan
        assert ["Scan", "", "", SUBJECT, "Questions from templates", "3"] in scan
        assert len(scan) == 5

    async def test_a_scan_that_was_never_scored_says_not_scored_and_has_no_dimensions(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id, scored=False)
        assert (await _report(client, sid))["score"] is None
        body = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)[1:]
        assert [r[5] for r in section(body, "Scan") if r[4] == "Overall score (out of 100)"] == [
            "Not scored"
        ]
        assert section(body, "Dimension scores") == []

    async def test_a_scan_with_too_little_data_says_not_scored_and_why_each_dimension_is_left_out(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        """The second way a scan is "Not scored": a score row exists and says there was too
        little data. The status and the score are paired as the rule needs; the file follows the
        score row, and the button is told the same (`csv-score-cases.json`)."""
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id, answered=False)
        score = (await _report(client, sid))["score"]
        assert score["status"] == "insufficient_data" and score["composite"] is None
        body = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)[1:]
        assert [r[5] for r in section(body, "Scan") if r[4] == "Overall score (out of 100)"] == [
            "Not scored"
        ]
        dimensions = section(body, "Dimension scores")
        assert len(dimensions) == 5
        assert {r[4] for r in dimensions} == {"Left out of the score because"}
        assert {r[5] for r in dimensions} == {"No answers came back"}
        assert "Score (out of 100)" not in {r[4] for r in body}

    async def test_the_decimals_are_the_places_the_button_says(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        """The button says scores, shares and sentiment keep two decimal places: the overall
        score, each dimension's score, the share of voice and an engine's sentiment, as the
        database and the scoring code hand them over. Points are one place, counts whole."""
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        body = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)[1:]
        two = {
            "Overall score (out of 100)", "Score (out of 100)", "Share of voice (out of 100)",
            "Sentiment (out of 100)",
        }
        whole = {
            "Prompts asked (count)", "Prompts listed below (count)", "Answers received (count)",
            "Answers naming the client (count)", "Position when named (1 = first)",
            "Citations (count)",
        }
        seen: set[str] = set()
        for row in body:
            measure, value = row[4], row[5]
            if measure in two:
                assert re.fullmatch(r"\d+\.\d{2}", value), row
            elif measure == "Points the score gains":
                assert re.fullmatch(r"\d+\.\d", value), row
            elif measure in whole:
                assert re.fullmatch(r"\d+", value), row
            else:
                continue
            seen.add(measure)
        assert seen == two | whole | {"Points the score gains"}

    async def test_a_partial_scan_gives_the_file_and_the_file_says_it_is_partial(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        """A scan that ran out of time shrinks both counts, so only this row says it."""
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id, status=ScanStatus.PARTIAL)
        resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 200
        body = parse(resp.content)[1:]
        assert [r[5] for r in section(body, "Scan") if r[4] == "Scan status"] == [PARTIAL_SENTENCE]
        assert (await _report(client, sid))["csvExportAvailable"] is True


class TestItIsTheReport:
    async def test_every_figure_in_the_file_is_one_the_reports_own_response_holds(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        """The CSV is built from the projection `GET /report` serves; the two are compared."""
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        report = await _report(client, sid)
        body = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)[1:]
        proof = report["proof"]
        answers = section(body, "Answers")
        name = report["subject"]["brandName"] or report["subject"]["name"]

        scanned = datetime.fromisoformat(report["scannedAt"].replace("Z", "+00:00"))
        assert [r[5] for r in section(body, "Scan") if r[4] == "Scan date"] == [
            scanned.astimezone(UTC).date().isoformat()
        ]
        assert [r[5] for r in section(body, "Scan") if r[4] == "Overall score (out of 100)"] == [
            report["score"]["composite"]
        ]

        for cov in proof["engineCoverage"]:
            label = ENGINE_LABEL[cov["engine"]]
            listed = len([s for s in proof["promptShelf"] if s["engine"] == cov["engine"]])
            for measure, value in (
                ("Prompts asked (count)", cov["promptsRun"]),
                ("Prompts listed below (count)", listed),
                ("Answers received (count)", cov["answered"]),
                ("Answers naming the client (count)", cov["mentioned"]),
            ):
                assert ["Answers", label, "", name, measure, str(value)] in answers
        for standing in proof["crossEngine"]["standings"]:
            label = ENGINE_LABEL[standing["engine"]]
            got = [r[5] for r in answers if r[1] == label and r[4] == "Sentiment (out of 100)"]
            assert got == ([] if standing["sentiment"] is None else [standing["sentiment"]])

        for shelf in proof["promptShelf"]:
            label, text = ENGINE_LABEL[shelf["engine"]], shelf["promptText"]
            mine = {r[4]: r[5] for r in answers if r[1] == label and r[2] == text}
            assert mine["Answered (yes/no)"] == ("yes" if shelf["answered"] else "no")
            if shelf["answered"]:
                assert mine["Named the client (yes/no)"] == (
                    "yes" if shelf["subjectPresent"] else "no"
                )
            if shelf.get("subjectPosition") is not None:
                assert mine["Position when named (1 = first)"] == str(shelf["subjectPosition"])
            else:
                assert "Position when named (1 = first)" not in mine

        sov = {r[3]: r[5] for r in section(body, "Share of voice")}
        assert sov[name] == (report["score"]["shareOfVoice"] or "")
        for rival in report["competitorSet"]["competitors"]:
            assert sov[rival["name"]] == (rival["shareOfVoice"] or "")

        listed_domains = proof["subjectCitedDomains"] + proof["competitorCitedDomains"]
        cited = {
            (r[3], r[4]): r[5]
            for r in section(body, "Cited domains")
            if r[4] == "Citations (count)"
        }
        for entry in listed_domains:
            assert cited[(entry["domain"], "Citations (count)")] == str(entry["citations"])
        assert len(cited) == len(listed_domains)
        credited = sorted(
            (r[3], r[5]) for r in section(body, "Cited domains") if r[4] == "Attributed to"
        )
        assert credited == sorted(
            (e["domain"], e["competitorName"] or (name if e["citesSubject"] else "Third party"))
            for e in listed_domains
        )

        dims = {r[3]: (r[4], r[5]) for r in section(body, "Dimension scores")}
        for dim in report["dimensions"]:
            label = {
                "mention_rate": "Mention Rate", "share_of_voice": "Share of Voice",
                "citation_strength": "Citation Strength", "sentiment": "Sentiment",
                "technical_foundation": "Technical Foundation",
            }[dim["key"]]
            if dim["included"]:
                assert dims[label] == ("Score (out of 100)", dim["subscore"])
            else:
                assert dims[label][0] == "Left out of the score because"

        story = derive_narrative(report)
        titles = [r[3] for r in section(body, "Fixes") if r[4] == "Priority"]
        assert titles == [fix.title for fix in story.fixes]
        points = {r[3]: r[5] for r in section(body, "Fixes") if r[4] == "Points the score gains"}
        assert points == {
            fix.title: f"{fix.points_upside:.1f}"
            for fix in story.fixes if fix.points_upside is not None
        }

    async def test_the_file_carries_no_paragraph_of_ours_and_nothing_a_page_or_an_answer_said(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        text = (await client.get(f"{BASE}{PATH.format(sid)}")).content.decode("utf-8")
        assert "A long paragraph of our own advice" not in text, "a fix's detail is not exported"
        assert "https://" not in text and "http://" not in text, "no page address, only domains"


# ---------------------------------------------------------------------------
# who may ask, and for which scans
# ---------------------------------------------------------------------------


class TestWhoMayAsk:
    @pytest.mark.parametrize("on", [True, False])
    async def test_signed_out_is_a_401_whatever_the_setting(
        self, client: AsyncClient, session: AsyncSession, settings: Settings,
        monkeypatch: pytest.MonkeyPatch, on: bool,
    ) -> None:
        monkeypatch.setattr(settings, "report_csv_enabled", on)
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        async with _another_browser(client) as nobody:
            resp = await nobody.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 401
        assert resp.json()["type"] == "/problems/authentication-required"
        assert resp.content.startswith(BOM) is False

    async def test_another_agencys_scan_is_a_404_in_every_status_and_never_a_409(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        await _sign_up(client)
        async with _another_browser(client) as other:
            theirs = await _sign_up(other, email="other@test.example", agency="Other Agency")
        unknown = await client.get(f"{BASE}{PATH.format('scan_01UNKNOWN0000000000000000')}")
        assert unknown.status_code == 404
        reference = unknown.json()
        reference.pop("instance")
        for status in ScanStatus:
            sid = await _seed(session, theirs, status=status, domain=f"{status.value}.example")
            resp = await client.get(f"{BASE}{PATH.format(sid)}")
            assert resp.status_code == 404, status
            assert resp.json()["type"] == "/problems/not-found"
            # The same answer as an id that does not exist: nothing says the scan is there.
            body = resp.json()
            body.pop("instance")
            assert body == reference, status

    async def test_the_owner_gets_their_own_and_the_other_agency_cannot_see_the_report_either(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        mine = await _sign_up(client)
        sid = await _seed(session, mine)
        assert (await client.get(f"{BASE}{PATH.format(sid)}")).status_code == 200
        async with _another_browser(client) as other:
            await _sign_up(other, email="other@test.example", agency="Other Agency")
            assert (await other.get(f"{BASE}{PATH.format(sid)}")).status_code == 404
            assert (await other.get(f"{BASE}/scans/{sid}/report")).status_code == 404


class TestScansWithoutFigures:
    @pytest.mark.parametrize(
        ("status", "sentence"),
        [
            (ScanStatus.FAILED, FAILED_SENTENCE),
            (ScanStatus.QUEUED, QUEUED_SENTENCE),
            (ScanStatus.RUNNING, RUNNING_SENTENCE),
        ],
    )
    async def test_a_failed_queued_or_running_scan_is_a_409_with_a_type_and_a_sentence(
        self, client: AsyncClient, session: AsyncSession, csv_on: None,
        status: ScanStatus, sentence: str,
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id, status=status)
        resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 409, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json() == {
            "type": "/problems/scan-not-exportable",
            "title": "This scan's figures cannot be exported",
            "status": 409,
            "detail": sentence,
            "instance": f"{BASE}/scans/{sid}/report.csv",
        }
        # A refusal is not a file: no mark, no figure, nothing about the scan but the sentence.
        assert not resp.content.startswith(BOM)
        assert SUBJECT not in resp.text

    async def test_a_cancelled_scan_is_refused_too(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        """Nothing writes this status today; the list is an allow-list, so it is refused."""
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id, status=ScanStatus.CANCELLED)
        resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 409
        assert resp.json()["type"] == "/problems/scan-not-exportable"

    async def test_a_failed_scans_score_is_not_in_any_answer_it_gives(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id, status=ScanStatus.FAILED)
        assert (await client.get(f"{BASE}{PATH.format(sid)}")).status_code == 409
        report = await _report(client, sid)
        assert report["score"] is None
        assert report["csvExportAvailable"] is False


class TestTheFieldAndTheRouteAgree:
    async def test_the_report_says_available_exactly_when_the_route_gives_the_file(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        for status in ScanStatus:
            sid = await _seed(session, agency_id, status=status, domain=f"{status.value}.example")
            available = (await _report(client, sid))["csvExportAvailable"]
            code = (await client.get(f"{BASE}{PATH.format(sid)}")).status_code
            assert available is (status in (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL)), status
            assert code == (200 if available else 409), status


# ---------------------------------------------------------------------------
# share links get nothing
# ---------------------------------------------------------------------------


class TestShareLinksGetNothing:
    async def test_the_shared_report_never_says_the_export_is_available(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        assert (await _report(client, sid))["csvExportAvailable"] is True
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        async with _another_browser(client) as stranger:
            shared = await stranger.post(f"{BASE}/reports/shared", json={"token": token})
        assert shared.status_code == 200, shared.text
        assert shared.json()["csvExportAvailable"] is False

    async def test_no_route_a_token_can_reach_gives_a_csv(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        spec = client._transport.app.openapi()  # noqa: SLF001
        csv_paths = {p: sorted(m) for p, m in spec["paths"].items() if ".csv" in p}
        assert csv_paths == {f"{BASE}/scans/{{scanId}}/report.csv": ["get"]}, (
            "the one route is the signed-in one, and it has no token variant"
        )
        async with _another_browser(client) as stranger:
            for method, path, kwargs in (
                ("post", "/reports/shared/csv", {"json": {"token": token}}),
                ("get", f"/reports/{token}.csv", {}),
                ("get", f"/reports/shared.csv?token={token}", {}),
                ("get", PATH.format(sid), {"headers": {"Authorization": f"Bearer {token}"}}),
            ):
                resp = await getattr(stranger, method)(f"{BASE}{path}", **kwargs)
                assert resp.status_code in (401, 404, 405), (path, resp.status_code)
                assert not resp.content.startswith(BOM), path

    async def test_withdrawing_the_link_changes_nothing_for_the_agency(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        await client.post(f"{BASE}/scans/{sid}/share")
        await client.delete(f"{BASE}/scans/{sid}/share")
        assert (await client.get(f"{BASE}{PATH.format(sid)}")).status_code == 200


# ---------------------------------------------------------------------------
# a formula typed into a name, a prompt or a fix title
# ---------------------------------------------------------------------------


# (id, typed in front of a word, what the file holds in front of it).
#
# The projection strips white space from either end of a string it is given, so a tab, a carriage
# return, a line feed or a space in front of `=` reaches the file as `=` and is guarded as one: the
# guard has to hold whichever layer does the work, and `test_report_csv.py` holds it with the strip
# out of the way. The later ones start a formula INSIDE the cell: after a `;` or a tab, where a
# spreadsheet that splits on either begins a new cell (`csv` quotes neither, so that was how a
# competitor named `Acme;=1+1` got into the last column as a formula), and after a line break,
# where a reader in a `;` locale ends the record even inside the quotes `csv` writes.
HOSTILE = [
    ("eq", "=", "'="),
    ("plus", "+", "'+"),
    ("minus", "-", "'-"),
    ("at", "@", "'@"),
    ("tab-eq", "\t=", "'="),
    ("cr-eq", "\r=", "'="),
    ("space-eq", " =", "'="),
    ("lf-eq", "\n=", "'="),
    ("fullwidth-eq", "＝", "'＝"),
    ("fullwidth-plus", "＋", "'＋"),
    ("fullwidth-minus", "－", "'－"),
    ("fullwidth-at", "＠", "'＠"),
    ("semicolon-eq", "x;=", "x;'="),
    ("tab-inside-eq", "x\t=", "x\t'="),
    ("lf-inside-eq", "x\n=1;", "x\n'=1;"),
    ("semicolon-crlf-eq", "x;\r\n=1;", "x;'\r'\n'=1;"),
    ("acme-lf-eq", "Acme\n=1+1;", "Acme\n'=1+1;"),
]


class TestAFormulaReachesTheFileAsText:
    @pytest.mark.parametrize(
        ("typed", "held"),
        [(typed, held) for _id, typed, held in HOSTILE],
        ids=[hostile_id for hostile_id, _typed, _held in HOSTILE],
    )
    async def test_a_client_a_competitor_a_prompt_and_a_fix_title_are_all_text(
        self, client: AsyncClient, session: AsyncSession, csv_on: None, typed: str, held: str
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(
            session, agency_id,
            name=f"{typed}client",
            rivals=((f"{typed}rival one", "zendesk.com"), (f"{typed}rival two", "freshdesk.com")),
            prompt_prefix=typed, fix_prefix=typed,
        )
        resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 200, resp.text
        rows = parse(resp.content)
        cells = [cell for row in rows for cell in row]

        # The client, in every section that names it: the scan, the answers, the table of
        # shares, and the domains credited to its own pages, where the file used to say "Subject".
        for name in ("Scan", "Answers", "Share of voice"):
            assert f"{held}client" in [r[3] for r in section(rows, name)], name
        attributed = [r[5] for r in section(rows, "Cited domains") if r[4] == "Attributed to"]
        assert f"{held}client" in attributed
        # Each rival, in the table and again in the citation it was credited with.
        assert {r[3] for r in section(rows, "Share of voice")} == {
            f"{held}client", f"{held}rival one", f"{held}rival two",
        }
        assert f"{held}rival one" in attributed
        # Every prompt, in the column it is in.
        assert {r[2] for r in section(rows, "Answers") if r[2]} == {
            f"{held}{text}" for text, _ in PROMPTS
        }
        # The fix titles the report derived and our generator worded.
        fixes = [r[3] for r in section(rows, "Fixes") if r[4] == "Priority"]
        assert any(title.startswith(f"{held}fix for") for title in fixes), fixes

        # And the property under all of it: no cell can start a formula, in front or after a
        # separator, with white space in between or not.
        assert not [c for c in cells if c[:1] in FORMULA_STARTS]
        assert not [c for c in cells if c.lstrip()[:1] in FORMULA_STARTS]
        assert not [c for c in cells if AFTER_SEPARATOR.search(c)]
        # And the file as a reader in a `;` locale reads it, at its worst: every `;`, tab and line
        # break starts a cell or a record, and what follows none of them is a formula.
        for piece in re.split(r"[;\t\r\n]", resp.content.decode("utf-8")):
            assert piece.lstrip()[:1] not in FORMULA_STARTS, repr(piece)

    async def test_a_name_that_is_an_email_address_or_carries_a_comma_survives(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        name = '@acme, "the" agency <ops@acme.example>'
        sid = await _seed(session, agency_id, name=name)
        rows = parse((await client.get(f"{BASE}{PATH.format(sid)}")).content)
        assert [r for r in rows if r[3] == f"'{name}"]


# ---------------------------------------------------------------------------
# a read
# ---------------------------------------------------------------------------

_WRITES = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|COPY|ALTER|DROP|CREATE|LOCK)\b"
    r"|\bFOR\s+(NO\s+KEY\s+)?(UPDATE|SHARE)\b|\bFOR\s+KEY\s+SHARE\b",
    re.IGNORECASE,
)


@contextlib.contextmanager
def _statements(engine: AsyncEngine) -> Iterator[list[str]]:
    seen: list[str] = []

    def before(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:  # noqa: ANN401, ARG001
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", before)


class TestItIsARead:
    async def test_opening_the_file_writes_and_locks_nothing(
        self, client: AsyncClient, session: AsyncSession, engine: AsyncEngine, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        with _statements(engine) as seen:
            resp = await client.get(f"{BASE}{PATH.format(sid)}")
        assert resp.status_code == 200
        assert any(s.lstrip().upper().startswith("SELECT") for s in seen)
        assert not [s for s in seen if _WRITES.search(s)], (
            "a statement that writes or locks a row was issued"
        )

    async def test_no_name_reaches_a_log_line(
        self, client: AsyncClient, session: AsyncSession, csv_on: None, capsys, caplog  # noqa: ANN001
    ) -> None:
        """Names are an operator's own text; the file is the only place they are put."""
        agency_id = await _sign_up(client)
        sentinel = "LOGSENTINEL-csv@client.example"
        rival = "LOGSENTINEL-rival"
        sid = await _seed(
            session,
            agency_id,
            name=sentinel,
            rivals=((rival, "zendesk.com"), ("Freshdesk", "freshdesk.com")),
        )
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as events:
            resp = await client.get(f"{BASE}{PATH.format(sid)}")
            assert resp.status_code == 200
            await client.get(f"{BASE}{PATH.format(sid)}")
        assert sentinel.encode() in resp.content, "it is in the file, which is the point"
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        assert "LOGSENTINEL" not in everything
        assert "client.example" not in everything

    async def test_the_file_is_the_same_twice(
        self, client: AsyncClient, session: AsyncSession, csv_on: None
    ) -> None:
        agency_id = await _sign_up(client)
        sid = await _seed(session, agency_id)
        first = await client.get(f"{BASE}{PATH.format(sid)}")
        second = await client.get(f"{BASE}{PATH.format(sid)}")
        assert first.content == second.content


def test_the_fixed_sentences_are_the_ones_the_code_says() -> None:
    """The words a person reads, held to the words the service holds (the route is held above)."""
    assert report_csv.refusal_detail(ScanStatus.FAILED) == FAILED_SENTENCE
    assert report_csv.refusal_detail(ScanStatus.QUEUED) == QUEUED_SENTENCE
    assert report_csv.refusal_detail(ScanStatus.RUNNING) == RUNNING_SENTENCE


def test_the_route_says_a_signed_out_caller_gets_the_401_and_off_is_not_the_same_for_all() -> None:
    """Off is a 404 for somebody signed in; a signed-out caller still gets the 401 every route
    gives (`TestWhoMayAsk`), so the route's own words must not say "whoever asks"."""
    doc = " ".join((inspect.getdoc(report_router.get_report_csv) or "").split())
    assert "whoever asks" not in doc
    assert "With the setting off a signed-in caller gets a `404`" in doc
    # This module also holds the shared-report routes, which never answer 401, so it is the
    # signed-in routes that give it.
    assert "every route here gives" not in doc
    assert "A signed-out caller gets the `401` every signed-in route here gives." in doc


def test_the_route_says_where_the_guard_reaches_and_what_it_gives() -> None:
    """The route's words about the guard are true of it: it reaches a formula that starts after a
    `;`, a tab or a line break inside a cell, so a name somebody typed cannot become one."""
    doc = " ".join((inspect.getdoc(report_router.get_report_csv) or "").split())
    assert "straight after a `;`, a tab or a line break inside a cell" in doc
    assert "so a name somebody typed cannot become a formula" in doc

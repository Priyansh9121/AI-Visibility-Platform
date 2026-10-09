"""F6 section 3: the report's question line, the past-year flag, and the Prompts tab's read.

* the helpers print the sentences the web prints, from one table (`question-lines.json`);
* `GET /clients/{id}/questions` says whether new questions are asked for and which set the client
  keeps (where it was written, since when it has been reused, the rivals it was written with and
  whether the list has changed); the PUT answers with the same body;
* the report carries `questionSet` (null for a scan before this change), on the shared report too,
  and the PDF prints the line and the past-year flag.
"""

from __future__ import annotations

import json
import re
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from avp_api import ids
from avp_api.models import (
    Client,
    Competitor,
    CompetitorSet,
    DetectionSource,
    DetectionStatus,
    Engine,
    EngineResult,
    EngineResultStatus,
    Prompt,
    PromptSet,
    Scan,
    ScanStatus,
)
from avp_api.models.client import ClassificationStatus
from avp_api.services import question_sets, question_view
from avp_api.services import report_narrative as narrative
from avp_api.services.report import build_public_report, build_report
from avp_api.services.report_pdf import render_report_pdf
from test_question_reuse import (
    BASE,
    add_set,
    build,
    client_row,
    new_scan,
    questions,
    sign_up,
    stub_generator,
)

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "fixtures"
TABLE: dict[str, Any] = json.loads((FIXTURES / "question-lines.json").read_text(encoding="utf-8"))


def _visible_text(pdf: bytes) -> str:
    """Every string the document draws, as `test_other_sites_report._visible_text` reads it."""
    out: list[str] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for literal in re.finditer(rb"\((.*?)\) Tj", content, re.S):
            raw = re.sub(
                rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), literal.group(1)
            )
            raw = raw.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            out.append(raw.decode("cp1252", errors="replace"))
    return re.sub(r"[^\S ]+", " ", " ".join(out))


def _drawn(pdf: bytes) -> list[tuple[int, float, str]]:
    """(page, baseline, text) of every string the document draws, in the order it draws them."""
    out: list[tuple[int, float, str]] = []
    for page, match in enumerate(re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S), start=1):
        content = zlib.decompress(match.group(1))
        drawing = rb"BT /\w+ [\d.]+ Tf [\d.]+ ([\d.]+) Td \((.*?)\) Tj ET"
        for op in re.finditer(drawing, content, re.S):
            raw = re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), op.group(2))
            raw = raw.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            out.append((page, float(op.group(1)), raw.decode("cp1252", errors="replace")))
    return out


# --- the helpers, against the table the web reads -----------------------------------------------


@pytest.mark.parametrize("spec", TABLE["setLines"], ids=[c["case"] for c in TABLE["setLines"]])
def test_the_set_line_is_the_sentence_the_web_prints(spec: dict[str, Any]) -> None:
    assert narrative.question_set_line(spec["questionSet"]) == spec["expected"]


@pytest.mark.parametrize("spec", TABLE["pastYear"], ids=[c["case"] for c in TABLE["pastYear"]])
def test_a_question_names_a_past_year_as_the_web_decides(spec: dict[str, Any]) -> None:
    assert narrative.names_past_year(spec["text"], spec["scanYear"]) is spec["expected"]


@pytest.mark.parametrize(
    "spec", TABLE["pastYearLines"], ids=[c["case"] for c in TABLE["pastYearLines"]]
)
def test_the_past_year_line_is_the_sentence_the_web_prints(spec: dict[str, Any]) -> None:
    got = narrative.past_year_line(spec["count"], prompts_tab=spec["promptsTab"])
    assert got == spec["expected"]


@pytest.mark.parametrize(
    "spec", TABLE["shelfCases"], ids=[c["case"] for c in TABLE["shelfCases"]]
)
def test_the_flagged_questions_come_once_each_in_question_order(spec: dict[str, Any]) -> None:
    assert narrative.past_year_questions(spec["rows"], spec["scanYear"]) == spec["expected"]


def test_a_day_is_read_in_utc_and_a_moment_without_a_zone_is_taken_as_utc() -> None:
    day = question_view.utc_day
    plus_three = timezone(timedelta(hours=3))
    assert day(datetime(2026, 10, 10, 1, 30, tzinfo=plus_three)) == date(2026, 10, 9)
    assert day(datetime(2026, 10, 9, 23, 59, 59, tzinfo=UTC)) == date(2026, 10, 9)
    assert day(datetime(2026, 10, 10, 0, 0, 0)) == date(2026, 10, 10)


def test_the_tables_cover_each_branch() -> None:
    expected = [c["expected"] for c in TABLE["setLines"]]
    assert None in expected and "New questions" in expected
    assert any(e and e.startswith("Same questions as the scan of ") for e in expected)
    assert {c["expected"] for c in TABLE["pastYear"]} == {True, False}
    assert any("1 question names" in (c["expected"] or "") for c in TABLE["pastYearLines"])


# --- GET /clients/{id}/questions ------------------------------------------------------------------


def _url(client_id: str) -> str:
    return f"{BASE}/clients/{client_id}/questions"


async def _dated(session: AsyncSession, scan: Scan, when: datetime) -> None:
    scan.finished_at = when
    await session.commit()


async def _rivals(session: AsyncSession, scan: Scan, names: list[str]) -> None:
    cset = CompetitorSet(
        id=ids.new_id(ids.COMPETITOR_SET), scan_id=scan.id, status=DetectionStatus.OK,
        detected_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(cset)
    await session.flush()
    for rank, name in enumerate(names, start=1):
        session.add(Competitor(
            id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=name,
            domain=f"{name.lower().replace(' ', '')}.edu", rank=rank,
            detection_source=DetectionSource.BOTH, signal_count=1, serp_mentions=1,
            co_citation_mentions=0, corroborated=False,
        ))
    await session.commit()


@asynccontextmanager
async def _other_agency(
    client: AsyncClient, email: str = "other-agency@test.example"
) -> AsyncIterator[AsyncClient]:
    """A second agency, signed in on a client of its own against the same app and database."""
    transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
    async with AsyncClient(
        transport=transport, base_url="http://testserver",
        headers={"Origin": client.headers["Origin"]},
    ) as other:
        await sign_up(other, email)
        yield other


async def _client_of(
    other: AsyncClient, session: AsyncSession, domain: str = "rmit.com"
) -> Client:
    """The other agency's client on `domain`, made ready the way `client_row` readies ours."""
    made = await other.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert made.status_code == 201, made.text
    row = await session.get(Client, made.json()["id"])
    row.industry = "university"
    row.classification_status = ClassificationStatus.CLASSIFIED
    row.brand_name = "RMIT University"
    await session.commit()
    return row


async def _stray_scan(
    session: AsyncSession, row: Client, other_agency_id: str,
    status: ScanStatus = ScanStatus.SUCCEEDED,
) -> Scan:
    """A scan row that names THIS client under ANOTHER agency's id; the app never writes one.

    The queries tested here read a client's scans by the agency's id as well as the client's, a
    second line behind the client's own id, and this is the one shape that tells the two apart.
    """
    stray = await new_scan(session, row, status)
    stray.agency_id = other_agency_id
    await session.commit()
    return stray


async def _name_source(session: AsyncSession, scan: Scan, source_scan_id: str) -> None:
    """Make the scan's set say it was copied from `source_scan_id`, true or not."""
    prompt_set = (
        await session.execute(select(PromptSet).where(PromptSet.scan_id == scan.id))
    ).scalar_one()
    prompt_set.generation_params = {
        **prompt_set.generation_params, "reusedFromScanId": source_scan_id,
    }
    await session.commit()


class TestGetQuestions:
    async def test_before_any_set_there_is_no_current_set_and_the_flag_is_off(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        resp = await client.get(_url(row.id))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"newQuestionsNextScan": False, "currentSet": None}

    async def test_a_fresh_set_is_described_by_its_scan_and_its_rivals(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        scan = await new_scan(session, row)
        await _dated(session, scan, datetime(2026, 10, 9, 3, 12, tzinfo=UTC))
        await build(session, scan, row, settings)
        await _rivals(session, scan, ["Monash"])

        body = (await client.get(_url(row.id))).json()

        assert body["newQuestionsNextScan"] is False
        current = body["currentSet"]
        assert current["sourceScanId"] == scan.id
        written_at = datetime.fromisoformat(current["writtenAt"])
        assert written_at == datetime(2026, 10, 9, 3, 12, tzinfo=UTC)
        assert current["questionCount"] == 20
        assert current["reusedSince"] is None
        assert current["writtenWithRivals"] == ["Monash"]
        assert current["rivalsChanged"] is False

    async def test_reused_since_is_the_first_scan_that_reused_it(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await new_scan(session, row)
        await _dated(session, first, datetime(2026, 10, 1, tzinfo=UTC))
        await build(session, first, row, settings)
        second = await new_scan(session, row)
        await _dated(session, second, datetime(2026, 10, 5, tzinfo=UTC))
        await build(session, second, row, settings)
        third = await new_scan(session, row)
        await _dated(session, third, datetime(2026, 10, 9, tzinfo=UTC))
        await build(session, third, row, settings)

        current = (await client.get(_url(row.id))).json()["currentSet"]

        assert current["sourceScanId"] == first.id
        assert datetime.fromisoformat(current["writtenAt"]) == datetime(2026, 10, 1, tzinfo=UTC)
        assert datetime.fromisoformat(current["reusedSince"]) == datetime(2026, 10, 5, tzinfo=UTC)

    async def test_a_changed_rival_list_is_said_and_the_order_and_case_are_not_a_change(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        scan = await new_scan(session, row)
        await build(session, scan, row, settings)  # written with ["Monash"]
        await _rivals(session, scan, ["monash"])
        same = (await client.get(_url(row.id))).json()["currentSet"]
        assert same["rivalsChanged"] is False

        later = await new_scan(session, row)
        await _rivals(session, later, ["Monash", "Deakin"])
        changed = (await client.get(_url(row.id))).json()["currentSet"]
        assert changed["writtenWithRivals"] == ["Monash"]
        assert changed["rivalsChanged"] is True

    async def test_a_set_written_before_rivals_were_recorded_is_never_called_changed(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        scan = await new_scan(session, row)
        await add_set(session, scan, generated_by="claude-sonnet-4-5", params={})
        await _rivals(session, scan, ["Monash"])

        current = (await client.get(_url(row.id))).json()["currentSet"]

        assert current["writtenWithRivals"] is None
        assert current["rivalsChanged"] is False
        assert current["questionCount"] == 20

    async def test_a_template_filled_set_is_no_current_set(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        scan = await new_scan(session, row)
        await add_set(
            session, scan, generated_by="claude-sonnet-4-5",
            params={"fromGenerator": 12, "fromFallback": 8},
        )
        assert (await client.get(_url(row.id))).json()["currentSet"] is None

    async def test_the_put_answers_with_the_same_body_as_the_get(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        await build(session, await new_scan(session, row), row, settings)

        put = await client.put(_url(row.id), json={"newQuestionsNextScan": True})
        got = await client.get(_url(row.id))

        assert put.status_code == 200, put.text
        assert put.json() == got.json()
        assert got.json()["newQuestionsNextScan"] is True
        assert got.json()["currentSet"]["questionCount"] == 20

    async def test_another_agency_an_unknown_client_and_no_session_are_refused(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        await client.post(f"{BASE}/auth/logout")
        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport, base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await sign_up(other, "intruder@test.example")
            mine = await other.get(_url(row.id))
            unknown = await other.get(_url(f"{ids.CLIENT}_{ULID()}"))
        assert (mine.status_code, unknown.status_code) == (404, 404)
        assert mine.json()["detail"] == unknown.json()["detail"]
        assert (await client.get(_url(row.id))).status_code == 401


# --- the report ---------------------------------------------------------------------------------


async def _answer_every_question(session: AsyncSession, scan: Scan) -> None:
    """One answered result per question of the scan's set, so the report has its question list."""
    prompt_ids = (
        await session.execute(
            select(Prompt.id).join(PromptSet, PromptSet.id == Prompt.prompt_set_id)
            .where(PromptSet.scan_id == scan.id)
        )
    ).scalars().all()
    for pid in prompt_ids:
        session.add(EngineResult(
            id=ids.new_id(ids.ENGINE_RESULT), scan_id=scan.id, prompt_id=pid,
            engine=Engine.CLAUDE_SEARCH, status=EngineResultStatus.ANSWERED_NO_MENTION,
        ))
    await session.commit()


def _with_years(n: int, past: int) -> list[dict]:
    """`n` questions, the first `past` of which name 2025 (the scans below are made in 2026)."""
    out = questions(n)
    for item in out[:past]:
        item["text"] += " in 2025"
    return out


class TestReportQuestionSet:
    async def test_a_fresh_set_is_new_questions_and_a_copy_names_its_source(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await new_scan(session, row)
        await _dated(session, first, datetime(2026, 10, 9, 3, 12, tzinfo=UTC))
        await build(session, first, row, settings)
        second = await new_scan(session, row)
        await build(session, second, row, settings)

        fresh = await build_report(session, first)
        copy = await build_report(session, second)

        assert fresh.question_set is not None and fresh.question_set.reused is False
        assert fresh.question_set.source_scan_date is None
        assert copy.question_set is not None and copy.question_set.reused is True
        # A day, not a moment: the time the source scan finished (03:12) is not sent (S3).
        assert type(copy.question_set.source_scan_date) is date
        assert copy.question_set.source_scan_date == date(2026, 10, 9)
        data = copy.model_dump(by_alias=True, mode="json")
        assert data["questionSet"] == {"reused": True, "sourceScanDate": "2026-10-09"}
        assert narrative.question_set_line(data["questionSet"]) == (
            "Same questions as the scan of 9 Oct 2026"
        )

    async def test_the_day_is_the_utc_day_whatever_the_zone_it_was_stored_in(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await new_scan(session, row)
        plus_three = timezone(timedelta(hours=3))
        await build(session, first, row, settings)
        second = await new_scan(session, row)
        await build(session, second, row, settings)

        async def day_on(finished_at: datetime) -> date | None:
            await _dated(session, first, finished_at)
            return (await build_report(session, second)).question_set.source_scan_date

        # 01:30 on 10 Oct at +03:00 is 22:30 on 9 Oct UTC.
        assert await day_on(datetime(2026, 10, 10, 1, 30, tzinfo=plus_three)) == date(2026, 10, 9)
        assert await day_on(datetime(2026, 10, 9, 23, 59, 59, tzinfo=UTC)) == date(2026, 10, 9)
        assert await day_on(datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC)) == date(2026, 10, 10)

    async def test_a_scan_from_before_this_change_has_no_line(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        scan = await new_scan(session, row)
        await add_set(session, scan, generated_by="claude-sonnet-4-5", params={})
        assert (await build_report(session, scan)).question_set is None

    async def test_a_scan_with_no_questions_has_no_line(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        assert (await build_report(session, await new_scan(session, row))).question_set is None

    async def test_a_template_filled_fresh_set_is_still_new_questions(
        self, client, session  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        scan = await new_scan(session, row)
        fp = question_sets.fingerprint([("a", "awareness")])
        await add_set(
            session, scan, generated_by="claude-sonnet-4-5+fallback",
            params={"fromGenerator": 10, "fromFallback": 10, "fingerprint": fp},
        )
        report = await build_report(session, scan)
        assert report.question_set is not None and report.question_set.reused is False

    async def test_the_shared_report_carries_it_too(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        scan = await new_scan(session, row)
        await build(session, scan, row, settings)
        public = await build_public_report(session, scan)
        assert public.question_set is not None and public.prompt_origin is None

    async def test_a_link_holder_gets_it_on_the_report_and_on_its_pdf(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """The decision for the shared report: it carries the line, and still not `promptOrigin`.

        Reached the way a stranger reaches it: a token, no session. The questions are ours and the
        date is a scan's; `promptOrigin` (counts of template questions) stays the agency's.
        """
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await new_scan(session, row)
        await _dated(session, first, datetime(2026, 10, 9, 3, 12, tzinfo=UTC))
        await build(session, first, row, settings)
        second = await new_scan(session, row)
        await build(session, second, row, settings)
        token = (await client.post(f"{BASE}/scans/{second.id}/share")).json()["token"]
        client.cookies.clear()

        shared = await client.post(f"{BASE}/reports/shared", json={"token": token})
        pdf = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})

        assert shared.status_code == 200, shared.text
        # The day only (S3): a link holder is not told at what time of day the source scan ended.
        assert shared.json()["questionSet"] == {"reused": True, "sourceScanDate": "2026-10-09"}
        assert shared.json()["promptOrigin"] is None
        assert pdf.status_code == 200
        assert "Same questions as the scan of 9 Oct 2026" in _visible_text(pdf.content)

    async def test_the_pdf_prints_the_line_and_nothing_for_an_old_scan(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await new_scan(session, row)
        await _dated(session, first, datetime(2026, 10, 9, 3, 12, tzinfo=UTC))
        await build(session, first, row, settings)
        second = await new_scan(session, row)
        await build(session, second, row, settings)
        old = await new_scan(session, row)
        await add_set(session, old, generated_by="claude-sonnet-4-5", params={})

        reused = _visible_text(render_report_pdf(await build_report(session, second)))
        fresh = _visible_text(render_report_pdf(await build_report(session, first)))
        before = _visible_text(render_report_pdf(await build_report(session, old)))

        assert "Same questions as the scan of 9 Oct 2026" in reused
        assert "New questions" in fresh and "Same questions as" not in fresh
        assert "New questions" not in before and "Same questions as" not in before


class TestTheSourceIsThisClientsOwnScan:
    """S1, S4, S5 and L1: a stored scan id is a claim, and only this client's own scans back it.

    `generation_params.reusedFromScanId` is written by the scan runner and is true when it is
    written; the report is the thing a stranger with a link reads, so it asks the database whether
    that scan is this client's, in this agency, before it prints a date. The client's id and the
    agency's id are two scopes, and a "stray" row (this client's id under another agency's, which
    the app never writes) is the one shape that tells them apart.
    """

    async def _reused_pair(self, client, session, settings, monkeypatch):  # noqa: ANN001, ANN202
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await new_scan(session, row)
        await _dated(session, first, datetime(2026, 10, 9, 3, 12, tzinfo=UTC))
        await build(session, first, row, settings)
        second = await new_scan(session, row)
        await build(session, second, row, settings)
        return row, first, second

    @pytest.mark.parametrize(
        "whose",
        ["another agency", "another client of this agency", "this client under another agency"],
    )
    async def test_a_source_that_is_not_this_clients_has_no_date(
        self, client, session, settings, monkeypatch, whose  # noqa: ANN001
    ) -> None:
        """Each scope refuses on its own: a scan failing only the client's, or only the agency's."""
        row, _first, second = await self._reused_pair(client, session, settings, monkeypatch)
        before = await build_report(session, second)
        assert before.question_set.source_scan_date == date(2026, 10, 9)  # the control: it is there

        if whose == "another client of this agency":
            made = await client.post(
                f"{BASE}/clients", json={"url": "elsewhere.example.edu", "classify": False}
            )
            assert made.status_code == 201, made.text
            foreign = await new_scan(session, await session.get(Client, made.json()["id"]))
        else:
            async with _other_agency(client) as other:
                theirs = await _client_of(other, session)
            if whose == "another agency":
                foreign = await new_scan(session, theirs)
            else:
                # THIS client's id under the other agency's: only the agency scope turns it away.
                foreign = await _stray_scan(session, row, theirs.agency_id)
        await _dated(session, foreign, datetime(2026, 9, 1, 8, 0, tzinfo=UTC))
        await _name_source(session, second, foreign.id)

        seen = (await build_report(session, second), await build_public_report(session, second))
        for report in seen:
            assert report.question_set is not None and report.question_set.reused is True
            assert report.question_set.source_scan_date is None
            data = report.model_dump(by_alias=True, mode="json")
            assert narrative.question_set_line(data["questionSet"]) == (
                "Same questions as an earlier scan"
            )
            assert "2026-09-01" not in json.dumps(data)

        # And on the wire, to the signed-in agency and to a link holder.
        token = (await client.post(f"{BASE}/scans/{second.id}/share")).json()["token"]
        agency_sees = await client.get(f"{BASE}/scans/{second.id}/report")
        client.cookies.clear()
        link_sees = await client.post(f"{BASE}/reports/shared", json={"token": token})
        for resp in (agency_sees, link_sees):
            assert resp.status_code == 200, resp.text
            assert resp.json()["questionSet"] == {"reused": True, "sourceScanDate": None}
            assert "2026-09-01" not in resp.text

    async def test_a_second_agency_on_the_same_domain_is_never_named(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """S5: two agencies, one domain, a set each; neither is ever named in the other's views."""
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        a1 = await new_scan(session, row)
        await _dated(session, a1, datetime(2026, 10, 1, tzinfo=UTC))
        await build(session, a1, row, settings)
        a2 = await new_scan(session, row)
        await _dated(session, a2, datetime(2026, 10, 5, tzinfo=UTC))
        await build(session, a2, row, settings)
        # The other agency's scans are the NEWER ones, with the very same questions.
        async with _other_agency(client) as other:
            theirs = await _client_of(other, session)
            b1 = await new_scan(session, theirs)
            await _dated(session, b1, datetime(2026, 10, 20, tzinfo=UTC))
            await build(session, b1, theirs, settings)
            b2 = await new_scan(session, theirs)
            await _dated(session, b2, datetime(2026, 10, 25, tzinfo=UTC))
            await build(session, b2, theirs, settings)
            own = (await other.get(_url(theirs.id))).json()["currentSet"]
        assert own["sourceScanId"] == b1.id  # the control: the other agency does have a set

        asked = await client.get(_url(row.id))
        history = await client.get(f"{BASE}/clients/{row.id}/history")
        report = await build_report(session, a2)
        shared = await build_public_report(session, a2)

        current = asked.json()["currentSet"]
        assert current["sourceScanId"] == a1.id
        assert datetime.fromisoformat(current["writtenAt"]) == datetime(2026, 10, 1, tzinfo=UTC)
        assert datetime.fromisoformat(current["reusedSince"]) == datetime(2026, 10, 5, tzinfo=UTC)
        for each in (report, shared):
            assert each.question_set.reused is True
            assert each.question_set.source_scan_date == date(2026, 10, 1)
        assert history.status_code == 200, history.text
        reused_from = [s["questionSet"]["reusedFromScanId"] for s in history.json()["scans"]]
        assert reused_from == [None, a1.id]
        for body in (asked.text, history.text, report.model_dump_json(), shared.model_dump_json()):
            assert b1.id not in body and b2.id not in body

    async def test_a_scan_row_of_another_agency_is_not_the_first_reuse(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """S4: the first-reuse date counts this agency's scans of the client and no others."""
        row = await client_row(client, session)
        stub_generator(monkeypatch, questions(20))
        first = await new_scan(session, row)
        await _dated(session, first, datetime(2026, 10, 1, tzinfo=UTC))
        await build(session, first, row, settings)
        # A row that names this client under ANOTHER agency's id is not a state the app writes. It
        # sorts between the two real scans and carries the earliest date of any that copied `first`.
        async with _other_agency(client) as other:
            theirs = await _client_of(other, session)
        stray = await _stray_scan(session, row, theirs.agency_id)
        await add_set(
            session, stray, generated_by="reused",
            params={"reusedFromScanId": first.id, "fingerprint": "f" * 64},
        )
        await _dated(session, stray, datetime(2026, 10, 3, tzinfo=UTC))
        second = await new_scan(session, row)
        await _dated(session, second, datetime(2026, 10, 5, tzinfo=UTC))
        await build(session, second, row, settings)

        got = (await client.get(_url(row.id))).json()["currentSet"]

        assert got["sourceScanId"] == first.id
        assert datetime.fromisoformat(got["reusedSince"]) == datetime(2026, 10, 5, tzinfo=UTC)

    async def test_a_running_scan_row_of_another_agency_does_not_hold_the_flag(
        self, client, session  # noqa: ANN001
    ) -> None:
        """S4: only this agency's RUNNING scan of the client refuses the PUT."""
        row = await client_row(client, session)
        async with _other_agency(client) as other:
            theirs = await _client_of(other, session)
        stray = await _stray_scan(session, row, theirs.agency_id, ScanStatus.RUNNING)

        allowed = await client.put(_url(row.id), json={"newQuestionsNextScan": True})
        assert allowed.status_code == 200, allowed.text

        # The control: the same row under this agency's id does refuse (one open scan per client).
        stray.agency_id = row.agency_id
        await session.commit()
        refused = await client.put(_url(row.id), json={"newQuestionsNextScan": False})
        assert refused.status_code == 409


class TestPastYearFlag:
    async def _report(self, session, settings, client, monkeypatch, payload):  # noqa: ANN001, ANN202
        row = await client_row(client, session)
        stub_generator(monkeypatch, payload)
        scan = await new_scan(session, row)
        scan.finished_at = datetime(2026, 10, 9, tzinfo=UTC)
        await build(session, scan, row, settings)
        await _answer_every_question(session, scan)
        return await build_report(session, scan)

    async def test_the_pdf_counts_the_questions_that_name_a_past_year_and_lists_them(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        report = await self._report(session, settings, client, monkeypatch, _with_years(20, 2))

        text = _visible_text(render_report_pdf(report))

        assert "2 questions name a past year." in text
        # The pointer is for the signed-in web report: the PDF goes to the client.
        assert "Prompts tab" not in text
        flagged = [p.prompt_text for p in report.proof.prompt_shelf if "2025" in p.prompt_text]
        assert len(flagged) == 2
        for question in flagged:
            assert question in text

    async def test_a_scan_with_no_such_question_prints_no_flag_line(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        report = await self._report(session, settings, client, monkeypatch, questions(20))
        assert "name a past year" not in _visible_text(render_report_pdf(report))
        assert "names a past year" not in _visible_text(render_report_pdf(report))

    async def test_the_scans_own_year_is_the_scans_not_the_calendars(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        payload = questions(20)
        payload[0]["text"] += " in 2026"
        report = await self._report(session, settings, client, monkeypatch, payload)
        assert "past year" not in _visible_text(render_report_pdf(report))

    async def test_a_flagged_question_the_pdf_cannot_set_is_counted_and_not_printed(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """U10: the writer drops a character its font lacks ("Zürich" would print as "Zrich")."""
        payload = _with_years(20, 3)
        payload[1]["text"] = "best universities in Zürich in 2025"
        report = await self._report(session, settings, client, monkeypatch, payload)

        text = _visible_text(render_report_pdf(report))

        assert "3 questions name a past year." in text
        assert "Zrich" not in text and "Zürich" not in text
        printable = [
            p.prompt_text for p in report.proof.prompt_shelf
            if "2025" in p.prompt_text and "Zürich" not in p.prompt_text
        ]
        assert len(printable) == 2
        for question in printable:
            assert f"{question} — past year" in text
        assert text.count("— past year") == 2

    async def test_a_set_line_clears_the_figure_rows_above_it(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """U11: the rows are 15pt apart, so a line 18pt under the last one read as another row."""
        report = await self._report(session, settings, client, monkeypatch, questions(20))

        drawn = _drawn(render_report_pdf(report))

        last_row = next(d for d in drawn if d[2].startswith("Citations pointing at"))
        line = next(d for d in drawn if d[2] == "New questions")
        assert line[0] == last_row[0], "the two sit on one page"
        # 10pt of space, then the line's own 14pt of leading.
        assert last_row[1] - line[1] == pytest.approx(24, abs=0.01)

    async def test_the_past_year_line_clears_them_too_when_it_is_the_first_line(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """A scan from before fingerprints has no set line; the next line takes its place."""
        report = await self._report(session, settings, client, monkeypatch, _with_years(20, 2))
        report = report.model_copy(update={"question_set": None})

        drawn = _drawn(render_report_pdf(report))

        last_row = next(d for d in drawn if d[2].startswith("Citations pointing at"))
        line = next(d for d in drawn if d[2] == "2 questions name a past year.")
        assert line[0] == last_row[0]
        assert last_row[1] - line[1] == pytest.approx(24, abs=0.01)

    async def test_the_past_year_line_follows_the_set_line_as_it_always_did(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        report = await self._report(session, settings, client, monkeypatch, _with_years(20, 2))

        drawn = _drawn(render_report_pdf(report))

        set_line = next(d for d in drawn if d[2] == "New questions")
        past = next(d for d in drawn if d[2] == "2 questions name a past year.")
        assert set_line[0] == past[0]
        # The set line's 4pt after it, 4pt of space, and the line's own 14pt of leading.
        assert set_line[1] - past[1] == pytest.approx(22, abs=0.01)

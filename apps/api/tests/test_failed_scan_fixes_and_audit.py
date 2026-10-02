"""A FAILED scan serves no fix written from its score, and no Technical Foundation sub-score.

Checklist 3.30, extending 3.24 (`test_failed_scan_hidden.py`).

THE FIXES. The fix generator's one prompt carries the scan's composite and every
dimension score, and each fix's `detail` is told to quote "this scan's actual
numbers", so what it writes can say "share of voice at 30 out of 100". For a scan
still running its input includes the score (the founder's decision). If the scan then
FAILS, a fix it wrote can quote the score the scan no longer shows, and blanking
`pointsUpside` would leave the wording. So the rule is the whole list: nothing the
generator wrote is served for a FAILED scan, on any route.

* `GET /scans/{id}/fixes` is `[]` (`status: "empty"`), the answer for a scan whose fixes
  were never generated.
* `POST /scans/{id}/fixes` is a 409 and makes no model call. If the scan fails while the
  call runs, it is the same 409.
* The report's `actionItems` is `[]`, so the report and its PDF word the fix beat from
  their own fixed copy.

The rows are kept. A scan whose deadline came after its chain had run is FAILED and keeps
its fix rows and its audit row, and nothing here deletes or changes a stored value
(scoring-spec.md rule 5). Each test pins both halves, the response and the row, and shows
the fixes BEFORE the scan fails so that an empty list is a change and not an absence.
`Generator` stands in for the model the way the real one behaves: it reads the composite
out of the prompt and quotes it, so a stored fix really does hold the withheld score.

THE AUDIT. `GET/POST /scans/{id}/audit` blank `technicalFoundation`, and `components`
with it: the four component scores whose weighted sum, with `componentWeights`, IS the
sub-score. `componentWeights` is this audit's own weighting (the nominal weights with an
unmeasured component's share redistributed), which says how it was measured and is not a
score, and it stays with every fact about the site. `POST` reads the scan's status again
after the crawl.
"""

from __future__ import annotations

import inspect
import re
import zlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import anthropic
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from avp_api import ids
from avp_api.models import ActionItem, Scan, ScanStatus, TechnicalAudit
from avp_api.models.action_item import Effort, Priority
from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.routers import action_items as action_items_router
from avp_api.routers import audits as audits_router
from avp_api.routers import scores as scores_router
from avp_api.services import alerts as alerts_service
from avp_api.services import audit_runner, scan_runner
from avp_api.services import report as report_service
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.fix_generator import GeneratedFix, GeneratedFixSet
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import SCAN_DEADLINE_EXCEEDED

BASE = "/api/v1"

FAILED_NO_FIXES = "This scan failed, so no fixes are written for it."


async def _sign_up(client: AsyncClient, email: str = "failedderived@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Failed Derived Agency",
            "fullName": "Op",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    def _install(n_prompts: int = 4):
        generated = [
            GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub"

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine,
                    engine_version="stub",
                    prompt_text=prompt,
                    text="Zendesk is popular. Help Scout is simpler and well liked.",
                    citations=(
                        [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                        if engine is Engine.CLAUDE_SEARCH
                        else []
                    ),
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


class Generator:
    """The fix generator's model call, standing in for the real one.

    The real one is told to refer to "this scan's actual numbers", and does: a stored fix
    has read "share of voice at 30 out of 100". This reads the composite out of the prompt,
    where `build_fix_prompt` puts it ("Visibility score: N out of 100"), and quotes it in
    the title and the detail of every fix, so a scan scored when its fixes were written has
    fixes that hold the score. `QUOTE` is what that wording says, and what a test looks for
    in anything the scan must not serve.

    `calls` counts the fix generations that reached the model. `wording` is the first word
    of every title, so a regeneration is visible in a stored row. `during_the_call` runs in
    the middle of a call, which is when a scan can fail under a request waiting on it.
    """

    QUOTE = "your visibility score of"

    def __init__(self) -> None:
        self.calls = 0
        self.wording = "Lift"
        self.during_the_call: Callable[[], Awaitable[None]] | None = None


@pytest.fixture
def generator(monkeypatch) -> Generator:  # noqa: ANN001
    """Installed before any scan runs, so the chain's own phase 8 uses it too."""
    gen = Generator()
    # What conftest's autouse stub left in place: it answers every other call.
    previous = anthropic.resources.messages.AsyncMessages.parse

    async def parse(self, **kwargs):  # noqa: ANN001, ANN003, ANN202
        if kwargs.get("output_format") is not GeneratedFixSet:
            return await previous(self, **kwargs)
        gen.calls += 1
        if gen.during_the_call is not None:
            await gen.during_the_call()
        content = kwargs["messages"][0]["content"]
        keys = [
            line.split(" — ")[0].removeprefix("- ")
            for line in content.splitlines()
            if line.startswith("- gap:") or line.startswith("- audit:")
        ]
        composite = re.search(r"Visibility score: (\S+) out of 100", content)
        quoted = f"{gen.QUOTE} {composite.group(1)}" if composite else "your visibility"

        class _Response:
            stop_reason = "end_turn"
            parsed_output = GeneratedFixSet(
                fixes=[
                    GeneratedFix(
                        candidate_id=key,
                        title=f"{gen.wording} change {i}: {quoted} out of 100",
                        detail=f"This sits behind {quoted} out of 100, so do it next.",
                        priority=Priority.MEDIUM,
                        effort=Effort.M,
                        priority_reason="It moves a weighted dimension.",
                        effort_reason="A template edit and a redeploy.",
                    )
                    for i, key in enumerate(keys, start=1)
                ]
            )

        return _Response()

    monkeypatch.setattr(anthropic.resources.messages.AsyncMessages, "parse", parse)
    return gen


async def _a_scan(client: AsyncClient, stub_engines) -> str:  # noqa: ANN001
    """A scan that ran the whole chain, so it has a score, an audit and a fix list."""
    cid = (
        await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    ).json()["id"]
    stub_engines(n_prompts=4)
    resp = await client.post(
        f"{BASE}/clients/{cid}/scans", json={"engines": ["claude", "claude_search", "chatgpt"]}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


def _ending(status: ScanStatus):  # noqa: ANN202
    """The columns a deadline writes: the status and its code, nothing else touched."""
    return sa_update(Scan).values(
        status=status,
        error_code=SCAN_DEADLINE_EXCEEDED,
        finished_at=datetime.now(UTC),
    )


async def _end(session, sid: str, status: ScanStatus) -> None:  # noqa: ANN001
    """End a scan the way a deadline does."""
    await session.execute(_ending(status).where(Scan.id == sid))
    await session.commit()


async def _end_in_another_transaction(engine, sid: str, status: ScanStatus) -> None:  # noqa: ANN001
    """What a deadline or the reaper does while a request is waiting on something else.

    On an engine of its own, as the executor's own sessions are: borrowed from the suite's
    pool in the middle of a request, the connection left that pool slow to hand out the
    next one, and the row-lock test in `test_failed_scan_score_routes.py` that ran next
    timed out waiting for its request (2026-10-02, seen only in that order).
    """
    own = create_async_engine(engine.url, poolclass=NullPool)
    try:
        async with own.begin() as connection:
            await connection.execute(_ending(status).where(Scan.id == sid))
    finally:
        await own.dispose()


async def _stored(session, model, sid: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """Every column of every row a scan has in a table, oldest first.

    A Core select rather than the ORM, so the session's identity map cannot answer
    for the database: what comes back is what is stored.
    """
    table = model.__table__
    rows = (
        await session.execute(select(table).where(table.c.scan_id == sid).order_by(table.c.id))
    ).mappings().all()
    return [dict(row) for row in rows]


def _without(body: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in keys}


def _rebuilt(audit: dict[str, Any]) -> Decimal:
    """The sub-score the response's own components and weights add up to."""
    total = sum(
        (Decimal(score) * Decimal(audit["componentWeights"][key])
         for key, score in audit["components"].items()),
        Decimal("0"),
    )
    return total / Decimal(100)


def _visible_text(pdf: bytes) -> str:
    """What the PDF draws, on one line: its streams are compressed and its lines wrap."""
    out: list[str] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for literal in re.finditer(rb"\((.*?)\) Tj", content, re.S):
            out.append(literal.group(1).decode("latin-1"))
    return " ".join(" ".join(out).split())


class TestTheFixList:
    async def test_a_failed_scans_fix_list_is_empty_and_its_rows_are_kept(
        self, client: AsyncClient, session, stub_engines, generator
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        shown = await client.get(f"{BASE}/scans/{sid}/fixes")
        # Not vacuous: the scan has fixes, written from its score, until it fails. Their
        # wording quotes the score and some carry its recoverable points.
        assert shown.json()["status"] == "generated"
        assert shown.json()["items"]
        assert Generator.QUOTE in shown.text
        assert any(item["pointsUpside"] is not None for item in shown.json()["items"])
        rows_before = await _stored(session, ActionItem, sid)
        assert rows_before
        assert all(Generator.QUOTE in row["detail"] for row in rows_before)

        await _end(session, sid, ScanStatus.FAILED)
        resp = await client.get(f"{BASE}/scans/{sid}/fixes")

        assert resp.status_code == 200, resp.text
        # The answer for a scan whose fixes were never generated
        # (`test_fix_generator.py::test_an_ungenerated_scan_is_empty_not_missing`).
        assert resp.json() == {
            "scanId": sid,
            "status": "empty",
            "reasonCode": None,
            "generatedBy": None,
            "items": [],
        }
        assert Generator.QUOTE not in resp.text
        # Kept: every column of every row is what it was, `updated_at` included.
        assert await _stored(session, ActionItem, sid) == rows_before

    async def test_posting_for_a_failed_scan_is_refused_and_no_model_call_is_made(
        self, client: AsyncClient, session, stub_engines, generator
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        # Not vacuous: the chain's own phase 8 called the generator, and a POST for a
        # scan that has not failed calls it again and rewrites the rows.
        assert generator.calls == 1
        generator.wording = "Second pass"
        regenerated = await client.post(f"{BASE}/scans/{sid}/fixes")
        assert regenerated.status_code == 201, regenerated.text
        assert generator.calls == 2
        assert all(item["title"].startswith("Second pass") for item in regenerated.json()["items"])
        rows_before = await _stored(session, ActionItem, sid)
        assert all(row["title"].startswith("Second pass") for row in rows_before)

        await _end(session, sid, ScanStatus.FAILED)
        generator.wording = "Third pass"
        resp = await client.post(f"{BASE}/scans/{sid}/fixes")

        assert resp.status_code == 409, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == "/problems/conflict"
        assert body["detail"] == FAILED_NO_FIXES
        assert "items" not in body
        assert Generator.QUOTE not in resp.text
        # No model call, and so no row written or changed.
        assert generator.calls == 2
        assert await _stored(session, ActionItem, sid) == rows_before

    @pytest.mark.parametrize(
        ("ending", "refused"), [(ScanStatus.FAILED, True), (ScanStatus.PARTIAL, False)]
    )
    async def test_a_scan_that_ends_while_the_fixes_are_written_is_judged_by_how_it_ended(
        self,
        client: AsyncClient,
        engine,
        session,
        stub_engines,
        generator,
        ending: ScanStatus,
        refused: bool,
    ) -> None:  # noqa: ANN001
        """The call takes seconds, and a deadline or the reaper can end the scan in them.

        The request loaded the scan before the call and its commit does not expire it, so
        without a second read it would serve a failed scan's fixes. PARTIAL is the
        contrast: a scan that ends in a status that counts is served as before.
        """
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        first_rows = await _stored(session, ActionItem, sid)
        assert first_rows
        assert generator.calls == 1
        generator.wording = "Second pass"

        async def the_scan_ends() -> None:
            await _end_in_another_transaction(engine, sid, ending)

        generator.during_the_call = the_scan_ends
        resp = await client.post(f"{BASE}/scans/{sid}/fixes")

        assert generator.calls == 2
        if refused:
            assert resp.status_code == 409, resp.text
            assert resp.json()["type"] == "/problems/conflict"
            assert resp.json()["detail"] == FAILED_NO_FIXES
            assert Generator.QUOTE not in resp.text
        else:
            assert resp.status_code == 201, resp.text
            assert resp.json()["items"]
            assert all(i["title"].startswith("Second pass") for i in resp.json()["items"])
            assert any(i["pointsUpside"] is not None for i in resp.json()["items"])
        # Either way the call's rows were written and are kept.
        written = await _stored(session, ActionItem, sid)
        assert len(written) == len(first_rows)
        assert all(row["title"].startswith("Second pass") for row in written)
        # And what the scan serves after it is judged by how it ended.
        listed = await client.get(f"{BASE}/scans/{sid}/fixes")
        assert bool(listed.json()["items"]) is not refused
        report = await client.get(f"{BASE}/scans/{sid}/report")
        assert bool(report.json()["actionItems"]) is not refused

    @pytest.mark.parametrize("ending", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL])
    async def test_a_scan_that_did_not_fail_is_served_its_fixes_as_before(
        self, client: AsyncClient, session, stub_engines, generator, ending: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        if ending is not ScanStatus.SUCCEEDED:
            await _end(session, sid, ending)

        listed = (await client.get(f"{BASE}/scans/{sid}/fixes")).json()
        posted = await client.post(f"{BASE}/scans/{sid}/fixes")

        assert listed["status"] == "generated"
        assert any(item["pointsUpside"] is not None for item in listed["items"])
        assert all(Generator.QUOTE in item["detail"] for item in listed["items"])
        assert posted.status_code == 201, posted.text
        assert posted.json()["status"] == "generated"
        assert any(item["pointsUpside"] is not None for item in posted.json()["items"])
        assert generator.calls == 2


class TestTheReport:
    async def test_a_failed_scans_report_lists_no_generated_fix(
        self, client: AsyncClient, session, stub_engines, generator
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        shown = await client.get(f"{BASE}/scans/{sid}/report")
        # Not vacuous: the report lists the generated fixes, quoting the score, until
        # the scan fails.
        assert shown.json()["actionItems"]
        assert Generator.QUOTE in shown.text
        findings = shown.json()["audit"]["findings"]
        assert findings
        rows_before = await _stored(session, ActionItem, sid)

        await _end(session, sid, ScanStatus.FAILED)
        resp = await client.get(f"{BASE}/scans/{sid}/report")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scanStatus"] == "failed"
        assert body["actionItems"] == []
        assert Generator.QUOTE not in resp.text
        # The facts the fix beat words its own list from are served as before.
        assert body["audit"]["findings"] == findings
        assert await _stored(session, ActionItem, sid) == rows_before

    @pytest.mark.parametrize("ending", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL])
    async def test_a_scan_that_did_not_fail_keeps_its_generated_fixes_in_the_report(
        self, client: AsyncClient, session, stub_engines, generator, ending: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        shown = (await client.get(f"{BASE}/scans/{sid}/report")).json()["actionItems"]
        assert shown
        assert any(item["pointsUpside"] is not None for item in shown)
        if ending is not ScanStatus.SUCCEEDED:
            await _end(session, sid, ending)

        after = (await client.get(f"{BASE}/scans/{sid}/report")).json()["actionItems"]

        assert after == shown

    async def test_the_pdf_of_a_failed_scan_prints_none_of_the_generated_wording(
        self, client: AsyncClient, session, stub_engines, generator
    ) -> None:  # noqa: ANN001
        """The PDF renders the `ReportOut` that `build_report` returns, as the JSON route does.

        `get_report_pdf` calls `build_report` and hands the result to
        `report_pdf.render_report_pdf`, which dumps that object and derives its fix beat
        from its `actionItems`. There is no second query, so what the JSON route does not
        serve the PDF cannot print.
        """
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        pdf = await client.get(f"{BASE}/scans/{sid}/report.pdf")
        assert pdf.status_code == 200
        # Not vacuous: the PDF prints the generated wording, with the score it quotes,
        # until the scan fails.
        assert Generator.QUOTE in _visible_text(pdf.content)

        await _end(session, sid, ScanStatus.FAILED)
        failed = await client.get(f"{BASE}/scans/{sid}/report.pdf")

        assert failed.status_code == 200
        text = _visible_text(failed.content)
        assert "This scan failed, so it has no score." in text
        assert Generator.QUOTE not in text
        assert "Lift change" not in text


class TestTheAudit:
    async def test_a_failed_scans_audit_has_no_technical_foundation_and_keeps_its_facts(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        shown = (await client.get(f"{BASE}/scans/{sid}/audit")).json()
        # Not vacuous: the sub-score, the components that add up to it and the facts are all
        # there until the scan fails.
        assert shown["technicalFoundation"] is not None
        assert shown["checks"]
        assert shown["components"]
        assert shown["componentWeights"]
        # That is why they go with it: the response's own numbers rebuild the sub-score.
        assert abs(_rebuilt(shown) - Decimal(shown["technicalFoundation"])) <= Decimal("0.01")
        rows_before = await _stored(session, TechnicalAudit, sid)
        assert rows_before[0]["technical_foundation"] is not None

        await _end(session, sid, ScanStatus.FAILED)
        resp = await client.get(f"{BASE}/scans/{sid}/audit")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["technicalFoundation"] is None
        assert body["components"] == {}
        # The weights are this audit's own weighting, which says how it was measured and is
        # not a score, and they stay with every fact about the site: the checks, the
        # booleans, the counts, the dates, the schema types.
        assert body["componentWeights"] == shown["componentWeights"]
        assert _without(body, "technicalFoundation", "components") == _without(
            shown, "technicalFoundation", "components"
        )
        assert body["checks"]
        # The stored sub-score is still there: the response blanks, nothing is changed.
        assert await _stored(session, TechnicalAudit, sid) == rows_before

    async def test_posting_an_audit_for_a_failed_scan_blanks_it_too(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        # Not vacuous: re-auditing before the scan fails returns the sub-score and the
        # components that add up to it.
        before = await client.post(f"{BASE}/scans/{sid}/audit")
        assert before.status_code == 201, before.text
        assert before.json()["technicalFoundation"] is not None
        assert before.json()["components"]
        assert abs(
            _rebuilt(before.json()) - Decimal(before.json()["technicalFoundation"])
        ) <= Decimal("0.01")

        await _end(session, sid, ScanStatus.FAILED)
        resp = await client.post(f"{BASE}/scans/{sid}/audit")

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["technicalFoundation"] is None
        assert body["components"] == {}
        # The facts stay: the same checks and the same weights as the audit before it.
        assert body["checks"]
        assert [(c["checkKey"], c["status"]) for c in body["checks"]] == [
            (c["checkKey"], c["status"]) for c in before.json()["checks"]
        ]
        assert body["componentWeights"] == before.json()["componentWeights"]
        assert body["componentWeights"]
        # The re-audit refreshed the one stored row, and the stored sub-score is not blanked.
        rows = await _stored(session, TechnicalAudit, sid)
        assert len(rows) == 1
        assert rows[0]["technical_foundation"] is not None

    @pytest.mark.parametrize(
        ("ending", "withheld"), [(ScanStatus.FAILED, True), (ScanStatus.PARTIAL, False)]
    )
    async def test_a_scan_that_ends_while_its_site_is_crawled_is_judged_by_how_it_ended(
        self,
        client: AsyncClient,
        engine,
        session,
        stub_engines,
        monkeypatch,
        ending: ScanStatus,
        withheld: bool,
    ) -> None:  # noqa: ANN001
        """The crawl takes seconds, and a deadline or the reaper can end the scan in them.

        The request loaded the scan before the crawl and its commit does not expire it, so
        without a second read it would hand back the sub-score and the components of a scan
        that failed. PARTIAL is the contrast: a scan that ends in a status that counts keeps
        them.
        """
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        # Not vacuous: the audit has a sub-score and components until a scan fails.
        assert (await client.get(f"{BASE}/scans/{sid}/audit")).json()["technicalFoundation"]
        crawl = audit_runner.audit_site  # conftest's stand-in for the browser

        async def crawl_while_the_scan_ends(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
            await _end_in_another_transaction(engine, sid, ending)
            return await crawl(url, **kwargs)

        monkeypatch.setattr(audit_runner, "audit_site", crawl_while_the_scan_ends)
        resp = await client.post(f"{BASE}/scans/{sid}/audit")

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert (body["technicalFoundation"] is None) is withheld
        assert (body["components"] == {}) is withheld
        assert body["checks"]
        assert body["componentWeights"]
        # Either way the audit it wrote is stored with its sub-score.
        rows = await _stored(session, TechnicalAudit, sid)
        assert len(rows) == 1
        assert rows[0]["technical_foundation"] is not None

    @pytest.mark.parametrize("ending", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL])
    async def test_a_scan_that_did_not_fail_keeps_its_technical_foundation_and_components(
        self, client: AsyncClient, session, stub_engines, ending: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        if ending is not ScanStatus.SUCCEEDED:
            await _end(session, sid, ending)

        got = (await client.get(f"{BASE}/scans/{sid}/audit")).json()
        posted = (await client.post(f"{BASE}/scans/{sid}/audit")).json()

        for audit in (got, posted):
            assert audit["technicalFoundation"] is not None
            assert audit["components"]
            assert audit["componentWeights"]
            assert abs(_rebuilt(audit) - Decimal(audit["technicalFoundation"])) <= Decimal("0.01")


class TestTheReportAndTheseRoutesAgree:
    """The report, the fix list and the audit route withhold the same things.

    They share one rule and one set of helpers (`services/failed_scan.py`). This pins
    the outcome from the outside, so a fourth place that forgets it, or a change to the
    rule in one of them, shows here. The report carries no component scores at all; if
    it ever does, this fails until they are withheld for a FAILED scan too.
    """

    async def test_the_same_things_are_present_then_absent_in_all_three(
        self, client: AsyncClient, session, stub_engines, generator
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)

        async def seen() -> dict[str, bool]:
            report = (await client.get(f"{BASE}/scans/{sid}/report")).json()
            fixes = (await client.get(f"{BASE}/scans/{sid}/fixes")).json()
            audit = (await client.get(f"{BASE}/scans/{sid}/audit")).json()
            return {
                "report fixes": bool(report["actionItems"]),
                "listed fixes": bool(fixes["items"]),
                "report foundation": report["audit"]["technicalFoundation"] is not None,
                "audit foundation": audit["technicalFoundation"] is not None,
                "audit components": bool(audit["components"]),
                "report components": bool(
                    {"components", "componentWeights"} & report["audit"].keys()
                ),
            }

        assert await seen() == {
            "report fixes": True,
            "listed fixes": True,
            "report foundation": True,
            "audit foundation": True,
            "audit components": True,
            "report components": False,
        }

        await _end(session, sid, ScanStatus.FAILED)

        assert await seen() == {
            "report fixes": False,
            "listed fixes": False,
            "report foundation": False,
            "audit foundation": False,
            "audit components": False,
            "report components": False,
        }


class TestTenancyComesFirst:
    @pytest.mark.parametrize(
        ("method", "suffix"),
        [("GET", "fixes"), ("POST", "fixes"), ("GET", "audit"), ("POST", "audit")],
    )
    async def test_another_agencys_failed_scan_is_the_same_404_as_an_unknown_one(
        self, client: AsyncClient, session, stub_engines, generator, method: str, suffix: str
    ) -> None:  # noqa: ANN001
        """The agency check runs before the status check, and before any audit or model call.

        A 409, a 200 with nothing in it, or any answer that differs from an unknown id's,
        would confirm that the scan exists.
        """
        await _sign_up(client)
        sid = await _a_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)
        fixes_before = await _stored(session, ActionItem, sid)
        audit_before = await _stored(session, TechnicalAudit, sid)
        calls_before = generator.calls
        client.cookies.clear()
        await _sign_up(client, "otheragency@test.example")

        foreign = await client.request(method, f"{BASE}/scans/{sid}/{suffix}")
        unknown = await client.request(method, f"{BASE}/scans/{ids.new_id(ids.SCAN)}/{suffix}")

        assert foreign.status_code == unknown.status_code == 404, foreign.text
        # Same type, title and detail. `instance` is the requested path, so the ids differ
        # in it and nowhere else.
        foreign_body, unknown_body = foreign.json(), unknown.json()
        for body in (foreign_body, unknown_body):
            body.pop("instance")
        assert foreign_body == unknown_body
        assert foreign_body["type"] == "/problems/not-found"
        assert foreign_body["detail"] == "No scan with that identifier."
        # Nothing ran for the stranger: no re-audit, no model call, no regenerated fixes.
        assert generator.calls == calls_before
        assert await _stored(session, ActionItem, sid) == fixes_before
        assert await _stored(session, TechnicalAudit, sid) == audit_before


class TestOneRuleForWhichScansAreWithheld:
    """`services/failed_scan.py` is the one place that says which scans are withheld.

    Its docstring names five callers. This keeps that true: none of them decides for
    itself that a scan failed, so the rule cannot be changed in one and not the others.
    The alert feed filters rows in SQL and cannot call the function, so it is held to it
    by outcome instead, for every status (`test_alerts.py`).
    """

    @pytest.mark.parametrize(
        "module",
        [report_service, action_items_router, audits_router, scores_router, alerts_service],
        ids=lambda module: module.__name__.rsplit(".", 1)[-1],
    )
    def test_the_caller_asks_the_helper_and_does_not_compare_the_status_itself(
        self, module
    ) -> None:  # noqa: ANN001
        source = inspect.getsource(module)

        assert "score_is_withheld" in source or "served_fixes" in source
        assert "ScanStatus.FAILED" not in source

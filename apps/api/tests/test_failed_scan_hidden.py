"""A FAILED scan has no score on the dashboard or the report, and cannot be shared.

Checklist 3.24, founder decision: "Failed scans cannot be shared (API refuses
minting with a specific problem type); a failed scan's score is never shown or
counted on the dashboard or report. Partial scans stay shareable."

A scan whose deadline came after scoring is FAILED but keeps its Score row.
Scoring never re-scores history, so the row stays; these tests pin that it is
only not READ.
"""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from avp_api.models import Scan, ScanStatus, Score
from avp_api.models.engine_result import Engine, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import SCAN_DEADLINE_EXCEEDED

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, email: str = "failedscan@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Failed Scan Agency",
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


async def _scored_scan(client: AsyncClient, stub_engines) -> str:  # noqa: ANN001
    cid = (
        await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    ).json()["id"]
    stub_engines(n_prompts=4)
    sid = (
        await client.post(
            f"{BASE}/clients/{cid}/scans",
            json={"engines": ["claude", "claude_search", "chatgpt"]},
        )
    ).json()["id"]
    assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
    return sid


def _visible_text(pdf: bytes) -> str:
    """What the PDF draws: its content streams are compressed."""
    out: list[str] = []
    for match in re.finditer(rb"stream\n(.*?)\nendstream", pdf, re.S):
        content = zlib.decompress(match.group(1))
        for literal in re.finditer(rb"\((.*?)\) Tj", content, re.S):
            out.append(literal.group(1).decode("latin-1"))
    return "\n".join(out)


async def _end(session, sid: str, status: ScanStatus) -> None:  # noqa: ANN001
    await session.execute(
        sa_update(Scan)
        .where(Scan.id == sid)
        .values(
            status=status,
            error_code=SCAN_DEADLINE_EXCEEDED,
            finished_at=datetime.now(UTC),
        )
    )
    await session.commit()


async def _give_the_audit_a_foundation_score(session, sid: str) -> None:  # noqa: ANN001
    """The scan's own audit, with a Technical Foundation value to be blanked."""
    from avp_api.models.technical_audit import TechnicalAudit

    audit = (
        await session.execute(select(TechnicalAudit).where(TechnicalAudit.scan_id == sid))
    ).scalar_one()
    audit.technical_foundation = Decimal("72.50")
    await session.commit()


async def _score_rows(session, sid: str) -> int:  # noqa: ANN001
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Score).where(Score.scan_id == sid)
            )
        ).scalar_one()
    )


class TestSharing:
    async def test_minting_for_a_failed_scan_is_refused_with_its_own_problem_type(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)

        resp = await client.post(f"{BASE}/scans/{sid}/share")

        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["type"] == "/problems/failed-scan-not-shareable"
        assert body["detail"] == "This scan failed, so its report cannot be shared."
        assert "token" not in body
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan.share_token is None

    async def test_another_agencys_failed_scan_is_a_404_not_a_409(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The tenancy check runs before the status check: a 409 would confirm the id exists."""
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)
        client.cookies.clear()
        await _sign_up(client, "otheragency@test.example")

        resp = await client.post(f"{BASE}/scans/{sid}/share")

        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == "/problems/not-found"

    async def test_a_partial_scan_still_mints(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.PARTIAL)

        resp = await client.post(f"{BASE}/scans/{sid}/share")

        assert resp.status_code == 200, resp.text
        assert resp.json()["token"]

    async def test_a_succeeded_scan_still_mints(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        assert (await client.post(f"{BASE}/scans/{sid}/share")).status_code == 200


class TestTheScoreIsNotShown:
    async def test_the_dashboard_shows_no_composite_and_does_not_count_a_failed_scan(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)

        before = (await client.get(f"{BASE}/dashboard")).json()
        assert before["scoredScanCount"] == 1
        assert before["recentScans"][0]["compositeScore"] is not None

        await _end(session, sid, ScanStatus.FAILED)
        body = (await client.get(f"{BASE}/dashboard")).json()

        entry = next(s for s in body["recentScans"] if s["id"] == sid)
        assert entry["status"] == "failed"
        assert entry["compositeScore"] is None
        assert body["scoredScanCount"] == 0
        # The row is not deleted or changed: it is only not read.
        assert await _score_rows(session, sid) == 1

    async def test_a_partial_scans_score_is_still_shown_and_counted(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.PARTIAL)

        body = (await client.get(f"{BASE}/dashboard")).json()
        assert body["scoredScanCount"] == 1
        assert body["recentScans"][0]["compositeScore"] is not None

    async def test_the_report_of_a_failed_scan_carries_no_score(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _give_the_audit_a_foundation_score(session, sid)
        shown = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        # Not vacuous: everything the failed report must blank is present first.
        assert shown["score"] is not None
        assert shown["dimensions"]
        assert shown["audit"]["technicalFoundation"] is not None
        before_items = shown["actionItems"]
        assert any(i["pointsUpside"] is not None for i in before_items)
        rivals = shown["competitorSet"]["competitors"]
        assert rivals
        assert any(r["mentionRate"] is not None for r in rivals)

        await _end(session, sid, ScanStatus.FAILED)
        resp = await client.get(f"{BASE}/scans/{sid}/report")

        assert resp.status_code == 200
        body = resp.json()
        assert body["scanStatus"] == "failed"
        assert body["score"] is None
        assert body["dimensions"] == []
        # Score-derived numbers go with it: the sub-score and the recoverable points.
        assert body["audit"] is not None
        assert body["audit"]["technicalFoundation"] is None
        assert len(body["actionItems"]) == len(before_items)
        assert all(i["pointsUpside"] is None for i in body["actionItems"])
        for rival in body["competitorSet"]["competitors"]:
            assert rival["mentionRate"] is None
            assert rival["shareOfVoice"] is None
        assert await _score_rows(session, sid) == 1

    async def test_the_pdf_of_a_failed_scan_prints_no_composite(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)

        resp = await client.get(f"{BASE}/scans/{sid}/report.pdf")
        assert resp.status_code == 200
        text = _visible_text(resp.content)
        assert "This scan failed, so it has no score." in text
        assert "/ 100" not in text

    async def test_an_already_shared_scan_that_then_fails_serves_without_a_score(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        await _end(session, sid, ScanStatus.FAILED)

        resp = await client.post(f"{BASE}/reports/shared", json={"token": token})

        assert resp.status_code == 200, resp.text
        assert resp.json()["score"] is None
        pdf = await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})
        assert pdf.status_code == 200
        text = _visible_text(pdf.content)
        assert "This scan failed, so it has no score." in text
        assert "has no score: this scan failed." in text
        assert "/ 100" not in text

    async def test_a_failed_scan_is_not_a_point_on_the_clients_history(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await _end(session, sid, ScanStatus.FAILED)
        scan = await session.get(Scan, sid)
        resp = await client.get(f"{BASE}/clients/{scan.client_id}/history")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scans"] == []
        assert body["scansWithoutData"] == 1

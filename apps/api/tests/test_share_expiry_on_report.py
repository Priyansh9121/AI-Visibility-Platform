"""`shareExpiresAt` on the signed-in report — checklist 3.25.

The report says when the scan's LIVE link ends so the share bar can offer Withdraw
without minting first. It is the expiry only, never the token or the URL, and it is
blanked on the public routes like `scanErrorCode`.
"""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime, timedelta
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


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Failed Scan Agency",
            "fullName": "Op",
            "email": "failedscan@test.example",
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


async def _score_rows(session, sid: str) -> int:  # noqa: ANN001
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Score).where(Score.scan_id == sid)
            )
        ).scalar_one()
    )



async def _expire(session, sid: str) -> None:  # noqa: ANN001
    await session.execute(
        sa_update(Scan)
        .where(Scan.id == sid)
        .values(share_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()


async def _report(client: AsyncClient, sid: str) -> dict:  # noqa: ANN001
    resp = await client.get(f"{BASE}/scans/{sid}/report")
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestShareExpiresAt:
    async def test_null_when_the_scan_was_never_shared(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        body = await _report(client, sid)
        assert "shareExpiresAt" in body
        assert body["shareExpiresAt"] is None

    async def test_the_live_links_expiry_and_nothing_else_about_it(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        minted = (await client.post(f"{BASE}/scans/{sid}/share")).json()

        resp = await client.get(f"{BASE}/scans/{sid}/report")

        assert resp.json()["shareExpiresAt"] == minted["expiresAt"]
        assert minted["token"] not in resp.text

    async def test_null_once_the_link_has_expired(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/share")
        await _expire(session, sid)
        assert (await _report(client, sid))["shareExpiresAt"] is None

    async def test_null_after_the_link_is_withdrawn(
        self, client: AsyncClient, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/share")
        assert (await _report(client, sid))["shareExpiresAt"] is not None
        assert (await client.delete(f"{BASE}/scans/{sid}/share")).status_code == 204
        assert (await _report(client, sid))["shareExpiresAt"] is None

    async def test_a_failed_scan_shared_before_it_failed_still_reports_its_link(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        await client.post(f"{BASE}/scans/{sid}/share")
        await session.execute(
            sa_update(Scan).where(Scan.id == sid).values(status=ScanStatus.FAILED)
        )
        await session.commit()
        assert (await _report(client, sid))["shareExpiresAt"] is not None

    async def test_always_null_on_the_public_report_and_its_pdf_payload(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        from avp_api.services import report as report_service

        await _sign_up(client)
        sid = await _scored_scan(client, stub_engines)
        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]

        resp = await client.post(f"{BASE}/reports/shared", json={"token": token})
        assert resp.status_code == 200, resp.text
        assert resp.json()["shareExpiresAt"] is None

        scan = await session.get(Scan, sid)
        assert (await report_service.build_report(session, scan)).share_expires_at is not None
        assert (await report_service.build_public_report(session, scan)).share_expires_at is None
        assert (
            await client.post(f"{BASE}/reports/shared/pdf", json={"token": token})
        ).status_code == 200

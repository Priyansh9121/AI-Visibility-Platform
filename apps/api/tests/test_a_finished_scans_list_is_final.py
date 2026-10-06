"""Naming rivals after a scan finished leaves that scan alone (founder decision, 2026-10-05).

    "A rival named after a scan finished counts only for later scans: never for that scan, its
    history figure, or an explicit re-score of it."

The scans here are real ones: `POST /clients/{clientId}/scans` runs the whole chain (competitor
set, engines, audit, score, fixes) with the suite's stubs at the network edge, so each has a stored
Score, a competitor set of its own (or none), and rival figures read from its answers. Then rivals
are named with `PUT /clients/{clientId}/competitors`, and nothing the scan shows may move: its own
set (every column, `updated_at` included), the report, the score read back, its client-history
figures and an explicit `POST /scans/{scanId}/score`. The three starting points are the three shapes
a finished scan's list can have: with rivals, empty, and no set at all.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api.models import Competitor, CompetitorSet, Scan, ScanStatus
from avp_api.models.engine_result import EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import competitors as detection
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt

BASE = "/api/v1"
NAMED = [{"name": "Intercom", "domain": "intercom.com"}, {"name": "Freshdesk"}]

# What a score read or a re-score returns that says what the score IS (not when it was written).
SCORE_FIELDS = (
    "status", "composite", "mentionRate", "shareOfVoice", "citationStrength", "sentiment",
    "technicalFoundation", "formulaVersion", "weights", "excludedDimensions",
    "degradationFlags", "reasonCode", "inputsDigest", "competitors", "averagePosition",
    "positionCount",
)


@pytest.fixture
def stub_engines(monkeypatch):  # noqa: ANN001, ANN201
    """Prompts and answers without a provider. The answer names Intercom, who is never detected."""

    def _install(n_prompts: int = 3):
        generated = [
            GeneratedPrompt(text=f"question number {i}", intent=list(PromptIntent)[i % 3])
            for i in range(n_prompts)
        ]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return generated, "stub-generator"

        text = (
            "Zendesk is popular. Help Scout is simpler and well liked. Front is newer. "
            "Intercom is an option too."
        )

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine,
                    engine_version="stub",
                    prompt_text=prompt,
                    text=text,
                    citations=(
                        [CitedSource(url="https://g2.com/x", domain="g2.com", position=1)]
                        if engine.value.endswith("search")
                        else []
                    ),
                    latency_ms=10,
                    status=EngineResultStatus.OK,
                )
                for engine in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)

    return _install


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Final List Agency", "fullName": "Op",
            "email": "final@test.example", "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient) -> str:
    resp = await client.post(
        f"{BASE}/clients", json={"url": "https://helpscout.com", "classify": False}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _finished_scan(
    client: AsyncClient, stub_discovery, monkeypatch: pytest.MonkeyPatch, kind: str  # noqa: ANN001
) -> tuple[str, str]:
    """A scan that ran the whole chain, whose list is `kind`: rivals, empty, or none at all."""
    cid = await _make_client(client)
    if kind == "empty":
        # Neither signal answers: detection finishes with nothing, and the set is stored empty.
        stub_discovery(serp_ok=False, cocit_ok=False)
    elif kind == "none":

        async def exploding_detect(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
            raise RuntimeError("SERPAPI_KEY is not configured")

        monkeypatch.setattr(detection, "detect_for_client", exploding_detect)
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert resp.status_code == 202, resp.text
    return cid, resp.json()["id"]


async def _own_set(session: AsyncSession, scan_id: str) -> tuple[tuple[Any, ...], list[Any]] | None:
    """The scan's set as every column holds it, rows and tombstones, so any write shows."""
    header = (
        await session.execute(
            select(*CompetitorSet.__table__.c).where(CompetitorSet.scan_id == scan_id)
        )
    ).one_or_none()
    if header is None:
        return None
    rows = (
        await session.execute(
            select(*Competitor.__table__.c)
            .where(Competitor.competitor_set_id == header.id)
            .order_by(Competitor.id)
        )
    ).all()
    return tuple(header), [tuple(r) for r in rows]


def _figures(score: dict[str, Any]) -> dict[str, Any]:
    return {k: score.get(k) for k in SCORE_FIELDS}


async def _history_point(client: AsyncClient, cid: str, scan_id: str) -> dict[str, Any]:
    history = (await client.get(f"{BASE}/clients/{cid}/history")).json()
    return next(s for s in history["scans"] if s["scanId"] == scan_id)


async def _snapshot(
    client: AsyncClient, session: AsyncSession, cid: str, scan_id: str
) -> dict[str, Any]:
    report = (await client.get(f"{BASE}/scans/{scan_id}/report")).json()
    report.pop("generatedAt")  # the one field that is the time of the read
    score = await client.get(f"{BASE}/scans/{scan_id}/score")
    assert score.status_code == 200, score.text
    return {
        "set": await _own_set(session, scan_id),
        "report": report,
        "score": _figures(score.json()),
        "history": await _history_point(client, cid, scan_id),
    }


def _active_names(own: tuple[tuple[Any, ...], list[Any]] | None) -> list[str]:
    if own is None:
        return []
    columns = [c.name for c in Competitor.__table__.c]
    name, suppressed = columns.index("name"), columns.index("is_suppressed")
    return [r[name] for r in own[1] if not r[suppressed]]


@pytest.mark.parametrize("kind", ["rivals", "empty", "none"])
class TestNamingRivalsAfterAScanFinished:
    async def test_its_set_report_score_history_and_a_re_score_are_unchanged(
        self, client: AsyncClient, session, stub_engines, stub_discovery, monkeypatch, kind  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        stub_engines()
        cid, sid = await _finished_scan(client, stub_discovery, monkeypatch, kind)

        # The starting point is the shape this case names, or the test proves nothing.
        start = await _own_set(session, sid)
        if kind == "rivals":
            assert _active_names(start), "the scan should have detected rivals"
        elif kind == "empty":
            assert start is not None and _active_names(start) == []
        else:
            assert start is None
        point = await _history_point(client, cid, sid)
        assert (point["shareOfVoice"] is not None) == (kind == "rivals")

        # A re-score of a scan nothing has touched: what the re-score after the save must equal.
        baseline = await client.post(f"{BASE}/scans/{sid}/score")
        assert baseline.status_code == 201, baseline.text
        before = await _snapshot(client, session, cid, sid)

        named = await client.put(f"{BASE}/clients/{cid}/competitors", json={"competitors": NAMED})
        assert named.status_code == 200, named.text

        after = await _snapshot(client, session, cid, sid)
        # Each piece on its own, so a failure names what moved.
        assert after["set"] == before["set"], "the scan's own competitor set was written"
        assert after["history"] == before["history"], "the client history moved"
        assert after["score"] == before["score"], "the score read back moved"
        assert after["report"] == before["report"], "the report moved"

        rescored = await client.post(f"{BASE}/scans/{sid}/score")
        assert rescored.status_code == 201, rescored.text
        assert _figures(rescored.json()) == _figures(baseline.json()), "a re-score moved"
        assert await _history_point(client, cid, sid) == before["history"]

        # And the rivals went where they belong: the client's list, for later scans.
        assert named.json()["scanId"] is None
        assert [c["name"] for c in named.json()["competitors"]] == ["Intercom", "Freshdesk"]

    async def test_the_next_scan_uses_the_named_rivals_and_the_finished_one_stays_as_it_was(
        self, client: AsyncClient, session, stub_engines, stub_discovery, monkeypatch, kind  # noqa: ANN001
    ) -> None:
        await _sign_up(client)
        stub_engines()
        cid, first = await _finished_scan(client, stub_discovery, monkeypatch, kind)
        before = await _snapshot(client, session, cid, first)

        named = await client.put(f"{BASE}/clients/{cid}/competitors", json={"competitors": NAMED})
        assert named.status_code == 200, named.text

        detected: list[int] = []

        async def never(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
            detected.append(1)
            raise AssertionError("a client that has named its rivals is not detected again")

        monkeypatch.setattr(detection, "detect_for_client", never)
        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert resp.status_code == 202, resp.text
        second = resp.json()["id"]
        assert second != first and detected == []

        # The new scan took the named list as its own, and measured Intercom with it.
        assert _active_names(await _own_set(session, second)) == ["Intercom", "Freshdesk"]
        report = (await client.get(f"{BASE}/scans/{second}/report")).json()
        rivals = {c["name"]: c for c in report["competitorSet"]["competitors"]}
        assert set(rivals) == {"Intercom", "Freshdesk"}
        assert rivals["Intercom"]["mentionRate"] is not None
        # No next list is left: it is the second scan's set now.
        assert (await client.get(f"{BASE}/clients/{cid}/competitors")).json()["scanId"] == second

        # And the first scan is as it was, after a later scan has taken the list.
        assert await _snapshot(client, session, cid, first) == before


class TestAScanQueuedBeforeTheSave:
    async def test_it_takes_the_saved_list_when_it_runs(
        self, client: AsyncClient, session, stub_engines, stub_discovery, a_day_passes, monkeypatch  # noqa: ANN001
    ) -> None:
        """The open scan a detection run leaves is queued when rivals are named, and runs after."""
        await _sign_up(client)
        stub_engines()
        cid, first = await _finished_scan(client, stub_discovery, monkeypatch, "rivals")
        before = await _snapshot(client, session, cid, first)

        # A detection run from the button leaves a scan QUEUED and carries no set of its own.
        stub_discovery(serp_domains=["kayako.com"], cocit_brands=[("Kayako", "kayako.com")])
        await a_day_passes()
        assert (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).status_code == 201
        queued = (
            await session.execute(
                select(Scan.id).where(Scan.client_id == cid, Scan.status == ScanStatus.QUEUED)
            )
        ).scalar_one()
        assert await _own_set(session, queued) is None

        # Rivals are named while it waits, then the scan runs: it is the same queued scan.
        named = await client.put(f"{BASE}/clients/{cid}/competitors", json={"competitors": NAMED})
        assert named.status_code == 200, named.text

        async def never(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
            raise AssertionError("a client that has named its rivals is not detected again")

        monkeypatch.setattr(detection, "detect_for_client", never)
        run = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert run.status_code == 202, run.text
        assert run.json()["id"] == queued, "the queued scan was not the one that ran"
        assert _active_names(await _own_set(session, queued)) == ["Intercom", "Freshdesk"]
        assert await _snapshot(client, session, cid, first) == before

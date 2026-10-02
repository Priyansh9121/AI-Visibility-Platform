"""A FAILED scan has no score on the score routes either.

Checklist 3.30, founder decision: "Extend 3.24's rule: GET /scans/{id}/score,
GET /scans/{id}/scores and POST /scans/{id}/score treat a FAILED scan as having
no score." 3.24 (`test_failed_scan_hidden.py`) took the score off the dashboard
and the report; these are the routes it did not reach.

A scan whose deadline came after scoring is FAILED and keeps its Score row.
Scoring never re-scores history (scoring-spec.md rule 5), so every test here
pins both halves: the route says there is no score, and the rows are exactly as
they were, column for column.

How each route answers an unscored scan decides how it answers a FAILED one:

* `GET /score`: `404 not-found`, "This scan has not been scored yet." The FAILED
  scan gets the same status and type, with its own sentence.
* `GET /scores`: `200 []`. The FAILED scan gets the same.
* `POST /score` computes a score, so an unscored scan has no refusal to copy: a
  scan that has run nothing is a `201` that writes an `insufficient_data` row
  (`test_score_endpoints.py::TestInsufficientData`). A FAILED scan must write
  nothing, so it gets the closest existing refusal, the 409 `conflict` the share
  route's own FAILED refusal (`failed-scan-not-shareable`) is built on.

`POST /score` also reads the scan's status under a row lock
(`TestPostScoreReadsTheStatusUnderALock`): the check and the write are one step, so a scan
that fails after the route loaded it cannot get a Score row written, or the one it kept
refreshed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy import update as sa_update

from avp_api import ids
from avp_api.models import Scan, ScanStatus, Score
from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.models.score import ScoreStatus
from avp_api.services import scan_runner
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import SCAN_DEADLINE_EXCEEDED

BASE = "/api/v1"

FAILED_NO_SCORE = "This scan failed, so it has no score."
FAILED_NOT_SCORABLE = "This scan failed, so it cannot be scored."


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """Stop after the engine phase, so a scan has a score only when a test gives it one.

    The default chain scores every scan it finishes (phase 7). These tests need
    "a scan with no score", and "a failed scan with one", on purpose.
    """
    return engines_only_executor


async def _sign_up(client: AsyncClient, email: str = "failedscore@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Failed Score Agency",
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


async def _client_and_scan(client: AsyncClient, stub_engines) -> tuple[str, str]:  # noqa: ANN001
    cid = (
        await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    ).json()["id"]
    stub_engines(n_prompts=4)
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert resp.status_code == 202, resp.text
    return cid, resp.json()["id"]


async def _another_scan(client: AsyncClient, cid: str) -> str:
    """A second scan for the client. The first has finished, so nothing is open."""
    resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


async def _end(session, sid: str, status: ScanStatus) -> None:  # noqa: ANN001
    """End a scan the way a deadline does: the status and its code, nothing else touched."""
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


async def _score_rows(session, sid: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """Every column of every Score row for a scan, oldest first.

    A Core select rather than the ORM, so the session's identity map cannot
    answer for the database: what comes back is what is stored. `computed_at`
    is in it, and a re-score would move it, so equality says "not touched".
    """
    table = Score.__table__
    rows = (
        await session.execute(
            select(table).where(table.c.scan_id == sid).order_by(table.c.id)
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _add_a_historic_row(session, sid: str) -> None:  # noqa: ANN001
    """A row under an older formula version, so the scan carries a history."""
    session.add(
        Score(
            id=ids.new_id(ids.SCORE),
            scan_id=sid,
            status=ScoreStatus.SCORED,
            composite=Decimal("41.00"),
            formula_version="v1.1",
            weights={},
            excluded_dimensions={},
            degradation_flags=[],
        )
    )
    await session.commit()


class TestGetScore:
    async def test_a_failed_scan_answers_as_a_scan_not_yet_scored_does(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid, failed = await _client_and_scan(client, stub_engines)
        assert (await client.post(f"{BASE}/scans/{failed}/score")).status_code == 201
        unscored = await _another_scan(client, cid)
        # Not vacuous: the stored score is served until the scan fails.
        assert (await client.get(f"{BASE}/scans/{failed}/score")).status_code == 200
        before = await _score_rows(session, failed)
        assert len(before) == 1

        await _end(session, failed, ScanStatus.FAILED)
        reference = await client.get(f"{BASE}/scans/{unscored}/score")
        resp = await client.get(f"{BASE}/scans/{failed}/score")

        assert reference.status_code == 404
        assert "not been scored" in reference.json()["detail"]
        # The same status and the same problem type as the unscored scan.
        assert resp.status_code == reference.status_code == 404, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == reference.json()["type"] == "/problems/not-found"
        assert body["title"] == reference.json()["title"]
        assert body["detail"] == FAILED_NO_SCORE
        # The row is not deleted or changed: it is only not read.
        assert await _score_rows(session, failed) == before

    async def test_a_failed_scan_that_was_never_scored_says_it_failed(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        """Failed is the truer answer than "not scored yet": nothing is coming."""
        await _sign_up(client)
        _, failed = await _client_and_scan(client, stub_engines)
        await _end(session, failed, ScanStatus.FAILED)

        resp = await client.get(f"{BASE}/scans/{failed}/score")

        assert resp.status_code == 404
        assert resp.json()["detail"] == FAILED_NO_SCORE
        assert await _score_rows(session, failed) == []

    @pytest.mark.parametrize("ending", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL])
    async def test_a_scan_that_did_not_fail_is_served_as_before(
        self, client: AsyncClient, session, stub_engines, ending: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _client_and_scan(client, stub_engines)
        created = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        if ending is not ScanStatus.SUCCEEDED:
            await _end(session, sid, ending)

        resp = await client.get(f"{BASE}/scans/{sid}/score")

        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == created["id"]
        assert created["composite"] is not None
        assert resp.json()["composite"] == created["composite"]


class TestListScores:
    async def test_a_failed_scan_lists_no_scores_as_a_scan_not_yet_scored_does(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid, failed = await _client_and_scan(client, stub_engines)
        assert (await client.post(f"{BASE}/scans/{failed}/score")).status_code == 201
        await _add_a_historic_row(session, failed)
        unscored = await _another_scan(client, cid)
        # Not vacuous: both rows are listed until the scan fails.
        shown = (await client.get(f"{BASE}/scans/{failed}/scores")).json()
        assert {row["formulaVersion"] for row in shown} >= {"v1.1"}
        assert len(shown) == 2
        before = await _score_rows(session, failed)
        assert len(before) == 2

        await _end(session, failed, ScanStatus.FAILED)
        reference = await client.get(f"{BASE}/scans/{unscored}/scores")
        resp = await client.get(f"{BASE}/scans/{failed}/scores")

        assert reference.status_code == 200
        assert reference.json() == []
        # The same status and the same body as the unscored scan: an empty list,
        # history rows included.
        assert resp.status_code == reference.status_code == 200, resp.text
        assert resp.json() == []
        assert await _score_rows(session, failed) == before

    @pytest.mark.parametrize("ending", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL])
    async def test_a_scan_that_did_not_fail_lists_its_scores_as_before(
        self, client: AsyncClient, session, stub_engines, ending: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _client_and_scan(client, stub_engines)
        created = (await client.post(f"{BASE}/scans/{sid}/score")).json()
        if ending is not ScanStatus.SUCCEEDED:
            await _end(session, sid, ending)

        listed = (await client.get(f"{BASE}/scans/{sid}/scores")).json()

        assert [row["id"] for row in listed] == [created["id"]]


class TestPostScore:
    async def test_a_failed_scan_is_refused_and_nothing_is_computed_or_stored(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, failed = await _client_and_scan(client, stub_engines)
        assert (await client.post(f"{BASE}/scans/{failed}/score")).status_code == 201
        before = await _score_rows(session, failed)
        assert len(before) == 1
        await _end(session, failed, ScanStatus.FAILED)

        resp = await client.post(f"{BASE}/scans/{failed}/score")

        assert resp.status_code == 409, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == "/problems/conflict"
        assert body["detail"] == FAILED_NOT_SCORABLE
        # Scoring the row again would refresh it in place and move `computed_at`.
        # Equality, column for column, says it was not scored and not touched.
        assert await _score_rows(session, failed) == before

    async def test_a_failed_scan_with_no_score_gets_none(
        self, client: AsyncClient, session, stub_engines
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, failed = await _client_and_scan(client, stub_engines)
        await _end(session, failed, ScanStatus.FAILED)
        assert await _score_rows(session, failed) == []

        resp = await client.post(f"{BASE}/scans/{failed}/score")

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == FAILED_NOT_SCORABLE
        assert await _score_rows(session, failed) == []

    @pytest.mark.parametrize("ending", [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL])
    async def test_a_scan_that_did_not_fail_is_scored_as_before(
        self, client: AsyncClient, session, stub_engines, ending: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _client_and_scan(client, stub_engines)
        if ending is not ScanStatus.SUCCEEDED:
            await _end(session, sid, ending)

        resp = await client.post(f"{BASE}/scans/{sid}/score")

        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "scored"
        assert resp.json()["composite"] is not None
        assert len(await _score_rows(session, sid)) == 1


async def _a_request_waits_on_a_row_lock(engine, request: asyncio.Future) -> bool:  # noqa: ANN001
    """True once a backend is waiting on a lock; False if the request answered first.

    Polls `pg_stat_activity` rather than sleeping a fixed time, so the test neither races
    nor waits longer than it must. The request is the only waiter possible: the writer
    holds its lock and the probe runs plain reads.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10.0
    async with engine.connect() as probe:
        while loop.time() < deadline and not request.done():
            waiting = (
                await probe.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
            if waiting:
                return True
            await asyncio.sleep(0.02)
    return False


class TestPostScoreReadsTheStatusUnderALock:
    """3.30 round 2 (the security review's Low): the FAILED check and the write are one step.

    `_load_scan` reads the scan, and a deadline or the reaper can end it FAILED before the
    route writes the Score. A check on that copy alone would let a failed scan get a Score
    row written, or the row it kept refreshed (`computed_at` moving). The route reads the
    scan again `FOR UPDATE` with `populate_existing`, so the status it checks is the current
    one and a status write has to wait for the score to be committed. Under the lock there
    is the database and nothing else: scoring is arithmetic over stored rows, and no
    outbound call is made.

    The test holds another transaction's status write open, uncommitted, so the request's
    first read of the scan (a plain read, which does not wait) sees the old status, as it
    does in the window the race needs. The locked read then has to wait for that write,
    and has to answer with what it committed.
    """

    @pytest.mark.parametrize("already_scored", [False, True])
    async def test_a_scan_that_fails_between_the_load_and_the_write_is_not_scored(
        self,
        client: AsyncClient,
        engine,
        session,
        stub_engines,
        already_scored: bool,
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        _, sid = await _client_and_scan(client, stub_engines)
        if already_scored:
            assert (await client.post(f"{BASE}/scans/{sid}/score")).status_code == 201
        before = await _score_rows(session, sid)
        assert len(before) == int(already_scored)

        async with engine.connect() as writer, writer.begin():
            await writer.execute(
                sa_update(Scan)
                .where(Scan.id == sid)
                .values(
                    status=ScanStatus.FAILED,
                    error_code=SCAN_DEADLINE_EXCEEDED,
                    finished_at=datetime.now(UTC),
                )
            )
            request = asyncio.ensure_future(client.post(f"{BASE}/scans/{sid}/score"))
            waited = await _a_request_waits_on_a_row_lock(engine, request)
        # The writer committed on leaving the block, and the request can go on.
        resp = await asyncio.wait_for(request, timeout=10)

        assert waited, f"the request did not wait for the row lock: it answered {resp.status_code}"
        assert resp.status_code == 409, resp.text
        assert resp.json()["type"] == "/problems/conflict"
        assert resp.json()["detail"] == FAILED_NOT_SCORABLE
        # No Score row written, and the one the scan kept is as it was, column for column.
        assert await _score_rows(session, sid) == before

    async def test_a_scan_that_has_not_failed_is_scored_under_the_same_lock(
        self, client: AsyncClient, engine, session, stub_engines
    ) -> None:  # noqa: ANN001
        """The contrast: a writer that leaves the scan SUCCEEDED does not stop the score."""
        await _sign_up(client)
        _, sid = await _client_and_scan(client, stub_engines)

        async with engine.connect() as writer, writer.begin():
            await writer.execute(
                sa_update(Scan)
                .where(Scan.id == sid)
                .values(finished_at=datetime.now(UTC))
            )
            request = asyncio.ensure_future(client.post(f"{BASE}/scans/{sid}/score"))
            waited = await _a_request_waits_on_a_row_lock(engine, request)
        resp = await asyncio.wait_for(request, timeout=10)

        assert waited, f"the request did not wait for the row lock: it answered {resp.status_code}"
        assert resp.status_code == 201, resp.text
        assert resp.json()["composite"] is not None
        assert len(await _score_rows(session, sid)) == 1


class TestAScanWhoseEngineCallsAllFailed:
    """The commonest FAILED scan: every engine call errored (`ALL_ENGINE_CALLS_FAILED`).

    Until 3.30 these routes scored such a scan as `insufficient_data`: POST wrote
    the row and GET served it. It is a FAILED scan like any other now, so there is
    no score to write or to serve, and nothing is written.
    """

    async def test_it_has_no_score_on_any_of_the_three_routes(
        self, client: AsyncClient, session, monkeypatch
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = (
            await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
        ).json()["id"]

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return [GeneratedPrompt(text="q", intent=PromptIntent.AWARENESS)], "stub"

        async def all_fail(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(
                    engine=engine,
                    engine_version="stub",
                    prompt_text=prompt,
                    status=EngineResultStatus.TIMEOUT,
                    error_code="TIMEOUT",
                )
                for engine in engines
            ]

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", all_fail)
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]
        scan = (await client.get(f"{BASE}/scans/{sid}")).json()
        assert (scan["status"], scan["errorCode"]) == ("failed", "ALL_ENGINE_CALLS_FAILED")

        posted = await client.post(f"{BASE}/scans/{sid}/score")
        got = await client.get(f"{BASE}/scans/{sid}/score")
        listed = await client.get(f"{BASE}/scans/{sid}/scores")

        assert posted.status_code == 409, posted.text
        assert posted.json()["detail"] == FAILED_NOT_SCORABLE
        assert got.status_code == 404, got.text
        assert got.json()["detail"] == FAILED_NO_SCORE
        assert listed.status_code == 200
        assert listed.json() == []
        assert await _score_rows(session, sid) == []


class TestTenancyComesFirst:
    @pytest.mark.parametrize(
        ("method", "suffix"),
        [("GET", "score"), ("GET", "scores"), ("POST", "score")],
    )
    async def test_another_agencys_failed_scan_is_the_same_404_as_an_unknown_one(
        self, client: AsyncClient, session, stub_engines, method: str, suffix: str
    ) -> None:  # noqa: ANN001
        """The agency check runs before the status check on every route.

        A 409, or "this scan failed", for a scan that is not the caller's would
        confirm that the id exists.
        """
        await _sign_up(client)
        _, failed = await _client_and_scan(client, stub_engines)
        assert (await client.post(f"{BASE}/scans/{failed}/score")).status_code == 201
        await _end(session, failed, ScanStatus.FAILED)
        before = await _score_rows(session, failed)
        client.cookies.clear()
        await _sign_up(client, "otheragency@test.example")

        foreign = await client.request(method, f"{BASE}/scans/{failed}/{suffix}")
        unknown = await client.request(method, f"{BASE}/scans/{ids.new_id(ids.SCAN)}/{suffix}")

        assert foreign.status_code == unknown.status_code == 404, foreign.text
        # Same type, title and detail. `instance` is the requested path, so the
        # ids differ in it and nowhere else.
        foreign_body, unknown_body = foreign.json(), unknown.json()
        for body in (foreign_body, unknown_body):
            body.pop("instance")
        assert foreign_body == unknown_body
        assert foreign_body["type"] == "/problems/not-found"
        assert foreign_body["detail"] == "No scan with that identifier."
        assert await _score_rows(session, failed) == before

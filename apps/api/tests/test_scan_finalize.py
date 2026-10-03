"""`finalize_scan` stamps a scan only while its row is still RUNNING — launch checklist 3.36.

THE DEFECT
----------
`finalize_scan` asked its own in-memory copy of the scan whether it was already
terminal, then stamped the verdict through the ORM: an UPDATE with no condition on the
status. Nothing refreshes that copy when somebody else writes the row
(`expire_on_commit` is off, `db.py`), and the chain is only stopped when its next
heartbeat, up to 30 seconds later, finds the row no longer RUNNING
(`scan_executor._heartbeat`). A reaper that failed a scan whose chain was still alive
was therefore overwritten by the chain's last act: FAILED became SUCCEEDED or PARTIAL,
and a failed scan's share link, which is kept so the agency can withdraw it (3.29),
opened again.

WHAT IS PINNED HERE
-------------------
* A scan another writer ended before finalize's own write — the real reaper's, already
  committed, or still in flight when finalize's write arrives — is left exactly as that
  writer left it, column by column (status, `error_code`, `finished_at`, the rest).
  Finalize logs `scan.finalize_lost` with the scan's id and the status it found, and
  returns the scan as stored. The same holds for every other terminal status.
* A RUNNING scan is finalized as it always was: SUCCEEDED, PARTIAL or FAILED by the one
  rule (`terminal_status_for`), from the failures recounted off the persisted rows.
* A scan already terminal in memory is not touched, and nothing is logged about it.
* End to end, through the real executor and the real public routes: a link minted while
  the scan ran stays dead when the reaper failed the scan and the chain then finalized.
  The same run with no reaper ends SUCCEEDED and the same link opens, so the refusal is
  the reaper's verdict surviving, not an artefact of how the run is set up.

ROUND 2 (the security review's two Lows and its nit). Making finalize conditional made
two older, unconditional writers matter, because nothing overwrites a reaper's FAILED
any more:

* `run_scan` assigns RUNNING to the chain's copy of the scan, and the ORM writes it. A
  failed phase refreshes that copy (`_attempt`), so after a reaper's committed FAILED
  the copy reads FAILED and the assignment would bring the scan back to RUNNING, to be
  stamped by the finalize that follows. The chain stops before `run_scan` instead. A
  copy that still says RUNNING writes no status (the ORM writes only what changed), and
  finalize's own condition is what holds then; both halves are pinned.
* `_mark_failed` ran for any exception, including in an executor whose claim matched
  nothing because another executor holds the scan. It may now fail only what it owns:
  a scan it has claimed, or one still QUEUED.
* Whatever is pending on the session goes out before finalize's stamp, never after it.

Postgres is real throughout. The reaper's write and finalize's are the code's own
statements, never stand-ins for them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import Client, EngineResult, Prompt, PromptSet, Scan, ScanStatus
from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_executor as executor_module
from avp_api.services import scan_runner
from avp_api.services.engines import EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import (
    EXECUTION_FAILED,
    EXECUTOR_LOST,
    LEASE,
    SCAN_DEADLINE_EXCEEDED,
    ScanJob,
    execute_scan,
    reap_stale_scans,
)

BASE = "/api/v1"

OK = EngineResultStatus.OK
NO_MENTION = EngineResultStatus.ANSWERED_NO_MENTION
ERROR = EngineResultStatus.ERROR
TIMEOUT = EngineResultStatus.TIMEOUT
RATE_LIMITED = EngineResultStatus.RATE_LIMITED

# Derived, not retyped: the code `terminal_status_for` writes when every call failed.
ALL_ENGINE_CALLS_FAILED = scan_runner.terminal_status_for(1, 1)[1]

# The two events finalize can log about its own write.
FINALIZE_EVENTS = ("scan.finalized", "scan.finalize_lost")

# What a faked engine returns. It names Zendesk, then the subject, then Front.
ANSWER = "Zendesk is popular. Help Scout is simpler and well liked. Front is newer."


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Finalize Test Agency",
            "fullName": "Op",
            "email": "finalize@test.example",
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _make_client(client: AsyncClient) -> str:
    # `classify: False`: no crawl and no model call at intake.
    resp = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _scan_with_results(  # noqa: ANN001
    session,
    client_id: str,
    outcomes: list[EngineResultStatus],
    *,
    status: ScanStatus = ScanStatus.RUNNING,
    lease_expires_at: datetime | None = None,
    finished_at: datetime | None = None,
    error_code: str | None = None,
) -> str:
    """A scan row with one persisted engine result per outcome. Returns the scan's id.

    This is what the chain has written by the time it reaches `finalize_scan`: the
    prompt set and every engine result, with the counts the engine phase committed.
    """
    owner = await session.get(Client, client_id)
    assert owner is not None
    scan = Scan(
        id=ids.new_id(ids.SCAN),
        client_id=client_id,
        agency_id=owner.agency_id,
        status=status,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
        lease_expires_at=lease_expires_at,
        finished_at=finished_at,
        error_code=error_code,
        prompt_count=len(outcomes),
        engine_result_count=len(outcomes),
    )
    session.add(scan)
    await session.flush()
    prompt_set = PromptSet(
        id=ids.new_id(ids.PROMPT_SET),
        scan_id=scan.id,
        generated_by="test",
        generation_params={},
        prompts=[],
    )
    session.add(prompt_set)
    for position in range(1, len(outcomes) + 1):
        prompt_set.prompts.append(
            Prompt(
                id=ids.new_id(ids.PROMPT),
                prompt_set_id=prompt_set.id,
                text=f"question {position}",
                intent=PromptIntent.AWARENESS,
                position=position,
            )
        )
    await session.flush()
    for prompt, outcome in zip(prompt_set.prompts, outcomes, strict=True):
        session.add(
            EngineResult(
                id=ids.new_id(ids.ENGINE_RESULT),
                scan_id=scan.id,
                prompt_id=prompt.id,
                engine=Engine.CLAUDE,
                status=outcome,
                mentioned=outcome is OK,
                position=1 if outcome is OK else None,
                brand_mentions=[],
                citations=[],
            )
        )
    await session.commit()
    return scan.id


async def _stored(session, scan_id: str) -> dict:  # noqa: ANN001
    """Every column of the scan's row as the table holds it, by raw SQL.

    Raw, so no identity-mapped copy is consulted: this is what is in Postgres.
    """
    result = await session.execute(text("SELECT * FROM scans WHERE id = :id"), {"id": scan_id})
    return dict(result.mappings().one())


def _chain_sessions(engine) -> async_sessionmaker:  # noqa: ANN001
    """Sessions built as the executor's are (`db.get_sessionmaker`): the chain's own copy
    of a scan is never expired by anyone else's write."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def _events_about_the_stamp(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event"] in FINALIZE_EVENTS]


async def _the_reaper_fails_it(session, factory, scan_id: str) -> dict:  # noqa: ANN001
    """The scan's lease runs out and the reaper fails it. Returns the row as the reaper left it.

    The lease runs out as it does when the heartbeat cannot renew it. The reaper is the real
    one (`reap_stale_scans`), on a connection of its own, and it commits.
    """
    await session.execute(
        sa_update(Scan)
        .where(Scan.id == scan_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    async with factory() as reaper_session:
        assert await reap_stale_scans(reaper_session) == 1
        await reaper_session.commit()
    reaped = await _stored(session, scan_id)
    # The precondition, so a test says so itself if the reaper stops doing this.
    assert reaped["status"] == "failed"
    assert reaped["error_code"] == EXECUTOR_LOST
    return reaped


async def _until_a_statement_waits_on_a_row_lock(session) -> None:  # noqa: ANN001
    """Poll until some backend is blocked on a lock.

    `pg_stat_clear_snapshot()` first: inside a transaction Postgres serves
    `pg_stat_activity` from a snapshot taken at the first read, so without it every
    later poll would see the first answer.
    """
    for _ in range(250):
        await session.execute(text("SELECT pg_stat_clear_snapshot()"))
        waiting = (
            await session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("nothing ever waited on the row lock")


# ---------------------------------------------------------------------------
# a scan somebody else ended
# ---------------------------------------------------------------------------


class TestAScanSomebodyElseEnded:
    async def test_the_reapers_failure_is_not_overwritten(
        self, client: AsyncClient, session, engine
    ) -> None:  # noqa: ANN001
        """The defect itself, with the reaper's own write.

        The chain holds its copy of the scan as RUNNING. The lease has run out (the
        heartbeat could not renew it), so `reap_stale_scans` fails the scan on its own
        connection and commits. The chain has not been told: its next heartbeat is up
        to 30 seconds away. Its finalize must write nothing.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session, cid, [OK, OK, OK], lease_expires_at=datetime.now(UTC) - timedelta(minutes=1)
        )
        factory = _chain_sessions(engine)

        async with factory() as chain_session:
            chain_scan = await chain_session.get(Scan, sid)
            assert chain_scan is not None
            assert chain_scan.status is ScanStatus.RUNNING

            async with factory() as reaper_session:
                assert await reap_stale_scans(reaper_session) == 1
                await reaper_session.commit()

            reaped = await _stored(session, sid)
            # The precondition, so the test says so itself if the reaper stops doing this.
            assert reaped["status"] == "failed"
            assert reaped["error_code"] == EXECUTOR_LOST
            assert reaped["finished_at"] is not None
            # And the defect's setting: the chain's copy still says RUNNING.
            assert chain_scan.status is ScanStatus.RUNNING

            with structlog.testing.capture_logs() as events:
                returned = await scan_runner.finalize_scan(chain_session, chain_scan)

        # Nothing written: the reaper's row, every column, as it left it.
        assert await _stored(session, sid) == reaped
        # The scan comes back as stored, not as the chain remembered it.
        assert returned is chain_scan
        assert returned.status is ScanStatus.FAILED
        assert returned.error_code == EXECUTOR_LOST
        assert returned.finished_at == reaped["finished_at"]
        # One line, with the scan's id and the status found, and nothing else.
        lost = _events_about_the_stamp(events)
        assert [{k: v for k, v in e.items() if k != "log_level"} for e in lost] == [
            {"event": "scan.finalize_lost", "scan_id": sid, "status": "failed"}
        ]
        assert lost[0]["log_level"] == "warning"

    async def test_a_failure_that_lands_while_finalize_waits_for_the_row_is_kept(
        self, client: AsyncClient, session, engine
    ) -> None:  # noqa: ANN001
        """The race a read followed by a write can lose, against real row locks.

        The reaper's UPDATE has run and not committed. Finalize's own write has to wait
        for the row. When the reaper commits, a write decided on an earlier READ of the
        row (or with no condition at all) still goes ahead and replaces FAILED; a
        conditional UPDATE checks the status again once the row is free, and matches
        nothing.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session, cid, [OK, OK, OK], lease_expires_at=datetime.now(UTC) - timedelta(minutes=1)
        )
        factory = _chain_sessions(engine)

        async with factory() as chain_session, factory() as reaper_session:
            chain_scan = await chain_session.get(Scan, sid)
            assert chain_scan is not None
            assert await reap_stale_scans(reaper_session) == 1  # run, not committed

            finalizing = asyncio.create_task(scan_runner.finalize_scan(chain_session, chain_scan))
            try:
                await _until_a_statement_waits_on_a_row_lock(session)
                assert not finalizing.done(), "finalize wrote without waiting for the row"
                await reaper_session.commit()
                returned = await asyncio.wait_for(finalizing, timeout=10)
            finally:
                if not finalizing.done():
                    finalizing.cancel()
                    await asyncio.gather(finalizing, return_exceptions=True)

        stored = await _stored(session, sid)
        assert stored["status"] == "failed", "finalize overwrote a failure that landed"
        assert stored["error_code"] == EXECUTOR_LOST
        assert stored["error_detail"].startswith("No executor renewed")
        assert returned.status is ScanStatus.FAILED
        assert returned.finished_at == stored["finished_at"]

    @pytest.mark.parametrize(
        ("ended_as", "error_code"),
        [
            (ScanStatus.FAILED, EXECUTION_FAILED),
            (ScanStatus.PARTIAL, SCAN_DEADLINE_EXCEEDED),
            (ScanStatus.SUCCEEDED, None),
            (ScanStatus.CANCELLED, None),
        ],
        ids=["failed", "partial", "succeeded", "cancelled"],
    )
    async def test_whatever_ended_it_stays_as_it_was(
        self,
        client: AsyncClient,
        session,
        engine,
        ended_as: ScanStatus,
        error_code: str | None,
    ) -> None:  # noqa: ANN001
        """The condition is on RUNNING, so it holds for every status a scan can end in:
        `_mark_failed`'s FAILED, the deadline's PARTIAL, another finalize's SUCCEEDED."""
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session, cid, [OK, ERROR, OK], lease_expires_at=datetime.now(UTC) + LEASE
        )
        factory = _chain_sessions(engine)

        async with factory() as chain_session:
            chain_scan = await chain_session.get(Scan, sid)
            assert chain_scan is not None
            await session.execute(
                sa_update(Scan)
                .where(Scan.id == sid)
                .values(
                    status=ended_as,
                    error_code=error_code,
                    finished_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            await session.commit()
            ended = await _stored(session, sid)

            returned = await scan_runner.finalize_scan(chain_session, chain_scan)

        assert await _stored(session, sid) == ended
        assert returned.status is ended_as
        assert returned.error_code == error_code
        assert returned.finished_at == ended["finished_at"]


# ---------------------------------------------------------------------------
# a scan still RUNNING is finalized as before
# ---------------------------------------------------------------------------


class TestARunningScanIsFinalizedAsBefore:
    @pytest.mark.parametrize(
        ("outcomes", "status", "error_code"),
        [
            ([OK, NO_MENTION, OK, OK], ScanStatus.SUCCEEDED, None),
            ([OK, ERROR, OK, TIMEOUT], ScanStatus.PARTIAL, None),
            ([ERROR, TIMEOUT, RATE_LIMITED, ERROR], ScanStatus.FAILED, ALL_ENGINE_CALLS_FAILED),
        ],
        ids=["succeeded", "partial", "failed_every_call"],
    )
    async def test_the_verdict_comes_from_the_persisted_results(
        self,
        client: AsyncClient,
        session,
        engine,
        outcomes: list[EngineResultStatus],
        status: ScanStatus,
        error_code: str | None,
    ) -> None:  # noqa: ANN001
        """No other writer: the stamp lands as the one rule says, from a recount.

        An answered-but-absent subject (`ANSWERED_NO_MENTION`) is an answer, not a
        failure; an error, a timeout and a rate limit are failures. The count is read
        from the rows, never handed in.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session, cid, outcomes, lease_expires_at=datetime.now(UTC) + LEASE
        )
        factory = _chain_sessions(engine)

        async with factory() as chain_session:
            chain_scan = await chain_session.get(Scan, sid)
            assert chain_scan is not None
            started = datetime.now(UTC)
            with structlog.testing.capture_logs() as events:
                returned = await scan_runner.finalize_scan(chain_session, chain_scan)
            ended = datetime.now(UTC)

        stored = await _stored(session, sid)
        assert stored["status"] == status.value
        assert stored["error_code"] == error_code
        assert started <= stored["finished_at"] <= ended
        # The chain's copy reads what the table says.
        assert returned is chain_scan
        assert returned.status is status
        assert returned.error_code == error_code
        assert returned.finished_at == stored["finished_at"]
        # The one existing log line, with its existing fields, and no loss.
        failures = sum(outcome not in (OK, NO_MENTION) for outcome in outcomes)
        stamped = _events_about_the_stamp(events)
        assert [{k: v for k, v in e.items() if k != "log_level"} for e in stamped] == [
            {
                "event": "scan.finalized",
                "scan_id": sid,
                "status": status.value,
                "failures": failures,
                "results": len(outcomes),
            }
        ]

    async def test_state_pending_on_the_scan_is_written_before_the_stamp_not_after_it(
        self, client: AsyncClient, session, engine
    ) -> None:  # noqa: ANN001
        """The security review's nit: nothing may be written over the stamp.

        The session does not autoflush, so state left pending on the chain's copy used to
        be flushed by the commit AFTER the conditional UPDATE, as an ORM write with no
        condition at all. Here an old `finished_at` is pending. Flushed after the stamp it
        would replace the stamp's own; flushed before, the stamp is the last write.
        """
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session, cid, [OK, OK, OK], lease_expires_at=datetime.now(UTC) + LEASE
        )
        factory = _chain_sessions(engine)

        async with factory() as chain_session:
            chain_scan = await chain_session.get(Scan, sid)
            assert chain_scan is not None
            chain_scan.finished_at = datetime.now(UTC) - timedelta(days=1)  # pending, unflushed
            started = datetime.now(UTC)
            returned = await scan_runner.finalize_scan(chain_session, chain_scan)
            ended = datetime.now(UTC)

        stored = await _stored(session, sid)
        assert stored["status"] == "succeeded"
        assert started <= stored["finished_at"] <= ended, "pending state was written over the stamp"
        assert returned.finished_at == stored["finished_at"]


# ---------------------------------------------------------------------------
# a scan already terminal in memory
# ---------------------------------------------------------------------------


class TestAScanAlreadyTerminalInMemoryIsUntouched:
    @pytest.mark.parametrize(
        "status",
        [ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED, ScanStatus.CANCELLED],
        ids=lambda s: s.value,
    )
    async def test_it_is_returned_as_it_is_and_nothing_is_logged(
        self, client: AsyncClient, session, engine, status: ScanStatus
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session,
            cid,
            [OK, ERROR],
            status=status,
            finished_at=datetime.now(UTC) - timedelta(hours=1),
            error_code="SOMETHING_EARLIER",
        )
        factory = _chain_sessions(engine)

        async with factory() as chain_session:
            chain_scan = await chain_session.get(Scan, sid)
            assert chain_scan is not None
            before = await _stored(session, sid)
            with structlog.testing.capture_logs() as events:
                returned = await scan_runner.finalize_scan(chain_session, chain_scan)

        assert returned is chain_scan
        assert await _stored(session, sid) == before
        # Not a lost write, and not a stamp: it never reached the write.
        assert _events_about_the_stamp(events) == []

    async def test_a_second_finalize_of_the_same_scan_writes_nothing(
        self, client: AsyncClient, session, engine
    ) -> None:  # noqa: ANN001
        """A second stamp would move `finished_at` forward for a scan that finished earlier."""
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session, cid, [OK, OK, OK], lease_expires_at=datetime.now(UTC) + LEASE
        )
        factory = _chain_sessions(engine)

        async with factory() as chain_session:
            chain_scan = await chain_session.get(Scan, sid)
            assert chain_scan is not None
            await scan_runner.finalize_scan(chain_session, chain_scan)
            first = await _stored(session, sid)
            assert first["status"] == "succeeded"

            with structlog.testing.capture_logs() as events:
                again = await scan_runner.finalize_scan(chain_session, chain_scan)

        assert again is chain_scan
        assert await _stored(session, sid) == first
        assert _events_about_the_stamp(events) == []


# ---------------------------------------------------------------------------
# the consequence: a failed scan's link stays dead (3.29)
# ---------------------------------------------------------------------------


async def _queued_scan(client: AsyncClient) -> tuple[str, ScanJob]:
    """A queued scan and the job that would run it. Nothing has run."""
    await _sign_up(client)
    cid = await _make_client(client)
    jobs: list[ScanJob] = []

    class _Deferred:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
    queued = await client.post(f"{BASE}/clients/{cid}/scans", json={})
    assert queued.status_code == 202, queued.text
    return queued.json()["id"], jobs[0]


def _fake_engines(monkeypatch: pytest.MonkeyPatch, n_prompts: int = 3) -> None:
    """Prompt generation and engine answers: every prompt answers, so no engine failed."""
    generated = [
        GeneratedPrompt(text=f"question {i}", intent=list(PromptIntent)[i % 3])
        for i in range(n_prompts)
    ]

    async def fake_generate(**kwargs):  # noqa: ANN003, ANN202, ARG001
        return generated, "stub-generator"

    async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ANN202, ARG001
        return [
            EngineAnswer(
                engine=e,
                engine_version="stub",
                prompt_text=prompt,
                text=ANSWER,
                latency_ms=10,
                status=EngineResultStatus.OK,
            )
            for e in engines
        ]

    async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ANN202, ARG001
        return Sentiment.POSITIVE, Decimal("0.900")

    monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
    monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
    monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)


async def _stranger_opens(client: AsyncClient, route: str, token: str) -> httpx.Response:
    """A public share route as its reader calls it: with no session cookie.

    The agency's cookie is put back afterwards.
    """
    saved = httpx.Cookies(client.cookies)
    client.cookies.clear()
    try:
        return await client.post(f"{BASE}{route}", json={"token": token})
    finally:
        client.cookies.update(saved)


def _assert_dead_link_answer(resp: httpx.Response) -> None:
    """The one answer every dead link gets: a 404 `not-found` naming no reason."""
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["type"] == "/problems/not-found"
    assert body["detail"] == "No report with that link."


class TestAFailedScansLinkStaysDeadWhenTheChainFinalizes:
    @pytest.mark.parametrize("reaped", [False, True], ids=["nobody_else_wrote", "reaper_first"])
    async def test_a_link_minted_while_the_scan_ran(
        self,
        client: AsyncClient,
        session,
        engine,
        settings,
        monkeypatch: pytest.MonkeyPatch,
        reaped: bool,
    ) -> None:  # noqa: ANN001
        """The whole run, through the real executor and the real public routes.

        The chain runs every phase for real. Just before its closing `finalize_scan`
        (still the real one, wrapped), the agency shares the report while the scan is
        RUNNING and, in one arm, the reaper then fails the scan on its own connection
        and commits. The chain does not know: it finalizes.

        * `reaper_first`: the scan stays FAILED, row unchanged column by column, with
          `scan.finalize_lost` logged; the link is refused on the JSON and the PDF route
          exactly as a withdrawn one is, and the token is kept so it can be withdrawn.
        * `nobody_else_wrote`: the same run ends SUCCEEDED and the same link opens. That
          is what makes the other arm's refusal evidence rather than a side effect of
          the setup.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch)
        factory = _chain_sessions(engine)
        real_finalize = scan_runner.finalize_scan
        seen: dict = {}

        async def share_then_maybe_get_reaped_then_finalize(session_, scan_):  # noqa: ANN001, ANN202
            assert scan_.status is ScanStatus.RUNNING
            minted = await client.post(f"{BASE}/scans/{sid}/share")
            assert minted.status_code == 200, minted.text
            seen["token"] = minted.json()["token"]
            if reaped:
                seen["reaped"] = await _the_reaper_fails_it(session, factory, sid)
            return await real_finalize(session_, scan_)

        monkeypatch.setattr(
            scan_runner, "finalize_scan", share_then_maybe_get_reaped_then_finalize
        )

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)

        token = seen["token"]
        stored = await _stored(session, sid)
        as_json = await _stranger_opens(client, "/reports/shared", token)
        as_pdf = await _stranger_opens(client, "/reports/shared/pdf", token)
        stamped = _events_about_the_stamp(events)

        if reaped:
            assert stored == seen["reaped"], "the chain's finalize changed a row the reaper ended"
            assert stored["status"] == "failed"
            assert stored["error_code"] == EXECUTOR_LOST
            # Kept, so the agency can still withdraw it (3.25, 3.29).
            assert stored["share_token"] == token
            assert [(e["event"], e["scan_id"], e["status"]) for e in stamped] == [
                ("scan.finalize_lost", sid, "failed")
            ]
            _assert_dead_link_answer(as_json)
            _assert_dead_link_answer(as_pdf)
        else:
            assert stored["status"] == "succeeded"
            assert stored["error_code"] is None
            assert [(e["event"], e["scan_id"], e["status"]) for e in stamped] == [
                ("scan.finalized", sid, "succeeded")
            ]
            assert as_json.status_code == 200, as_json.text
            assert as_json.json()["scanStatus"] == "succeeded"
            assert as_pdf.status_code == 200, as_pdf.text
            assert as_pdf.content.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# a reaped scan is not brought back to life
# ---------------------------------------------------------------------------


def _watch_the_chain(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every call into `run_scan` and `finalize_scan`. Each is still the real one."""
    calls: list[str] = []
    real_run_scan = scan_runner.run_scan
    real_finalize = scan_runner.finalize_scan

    async def run_scan_watched(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append("run_scan")
        return await real_run_scan(*args, **kwargs)

    async def finalize_watched(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append("finalize_scan")
        return await real_finalize(*args, **kwargs)

    monkeypatch.setattr(scan_runner, "run_scan", run_scan_watched)
    monkeypatch.setattr(scan_runner, "finalize_scan", finalize_watched)
    return calls


class TestAReapedScanIsNotBroughtBackToLife:
    """`run_scan` assigns RUNNING to the chain's copy of the scan, and the ORM writes it.

    That is harmless while the copy already says RUNNING: the ORM writes only what
    changed. But `_attempt` refreshes the copy after a failed phase, so when the reaper
    has failed the scan and the competitor phase then raises, the copy reads FAILED and
    `run_scan`'s assignment would write RUNNING over the reaper's verdict. The heartbeat's
    next renewal then matches again, and the finalize that follows stamps SUCCEEDED or
    PARTIAL on a scan that had been failed.

    The fix is a check in `_run_chain` before `run_scan`: a copy that does not say
    RUNNING means somebody has ended the scan, so the chain stops and writes nothing.
    The first test is that case. The second is the case beside it, where nothing has
    refreshed the copy: it says RUNNING, the chain goes on until its heartbeat would stop
    it, and the rows stay the reaper's all the same.
    """

    async def test_a_chain_that_finds_its_scan_failed_stops_before_the_engine_loop(
        self, client: AsyncClient, session, engine, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch)  # reached only if the chain wrongly goes on
        factory = _chain_sessions(engine)
        calls = _watch_the_chain(monkeypatch)
        seen: dict = {}

        async def the_reaper_strikes_and_the_phase_raises(  # noqa: ANN202
            session_, scan_, client_, **kwargs  # noqa: ANN001, ANN003, ARG001
        ):
            seen["reaped"] = await _the_reaper_fails_it(session, factory, sid)
            raise RuntimeError("the competitor phase failed")

        monkeypatch.setattr(
            executor_module.detection,
            "ensure_set_for_scan",
            the_reaper_strikes_and_the_phase_raises,
        )

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)

        # The premise: the phase failed, so `_attempt` refreshed the chain's copy of the
        # scan to what the reaper wrote.
        assert [e["phase"] for e in events if e["event"] == "scan.chain.phase_failed"] == [
            "competitors"
        ]
        # The chain stopped there. Nothing was written: the reaper's row, every column.
        # And nothing after the phase ran.
        assert await _stored(session, sid) == seen["reaped"], "something wrote the reaper's row"
        assert calls == [], "the chain went on past a scan that had been ended"
        stopped = [e for e in events if e["event"] == "scan.chain.stopped_not_running"]
        assert [{k: v for k, v in e.items() if k != "log_level"} for e in stopped] == [
            {"event": "scan.chain.stopped_not_running", "scan_id": sid, "status": "failed"}
        ]
        assert stopped[0]["log_level"] == "warning"
        assert _events_about_the_stamp(events) == []

    async def test_a_chain_that_has_not_been_told_runs_on_but_cannot_restamp_the_scan(
        self, client: AsyncClient, session, engine, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        """Nothing refreshes the chain's copy, so it still says RUNNING and cannot tell.

        It goes on (`run_scan`, the phases, finalize) until its heartbeat would stop it.
        `run_scan`'s assignment changes nothing for a copy that already says RUNNING, so
        no status is written there, and finalize's own condition holds the row. The
        columns that say how the scan ended are the reaper's at the end. Others move, as
        they always did: `run_scan` writes the engine loop's own counts.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch)
        factory = _chain_sessions(engine)
        calls = _watch_the_chain(monkeypatch)
        seen: dict = {}

        async def the_reaper_strikes(  # noqa: ANN202
            session_, scan_, client_, **kwargs  # noqa: ANN001, ANN003, ARG001
        ):
            seen["reaped"] = await _the_reaper_fails_it(session, factory, sid)

        monkeypatch.setattr(executor_module.detection, "ensure_set_for_scan", the_reaper_strikes)

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)

        assert calls == ["run_scan", "finalize_scan"], "the chain had no way to know, and went on"
        stored = await _stored(session, sid)
        for column in ("status", "error_code", "error_detail", "finished_at", "lease_expires_at"):
            assert stored[column] == seen["reaped"][column], column
        assert [
            {k: v for k, v in e.items() if k != "log_level"}
            for e in _events_about_the_stamp(events)
        ] == [{"event": "scan.finalize_lost", "scan_id": sid, "status": "failed"}]
        assert not [e for e in events if e["event"] == "scan.chain.stopped_not_running"]


# ---------------------------------------------------------------------------
# an executor fails only what it owns
# ---------------------------------------------------------------------------


class TestAnExecutorFailsOnlyWhatItOwns:
    """`_mark_failed` runs for any exception in `execute_scan`, whether or not it claimed.

    Two POSTs can adopt one QUEUED scan, so two executors are handed it. The loser's
    claim matches nothing; if one of its database calls then raises, its failure was
    written with `status IN ('queued', 'running')`, which matches the WINNER's live scan.
    Before finalize was conditional the winner's own stamp covered that over; now nothing
    does. An executor that has not claimed the scan may fail it only while it is still
    QUEUED (nobody has it), and one that has claimed it fails it as before.
    """

    @pytest.mark.parametrize(
        ("claimed", "status", "is_failed"),
        [
            (True, ScanStatus.QUEUED, True),
            (True, ScanStatus.RUNNING, True),
            (False, ScanStatus.QUEUED, True),
            (False, ScanStatus.RUNNING, False),
        ],
        ids=["claimed_queued", "claimed_running", "unclaimed_queued", "unclaimed_running"],
    )
    async def test_a_failure_is_written_to_what_the_executor_owns_and_to_a_queued_scan(
        self,
        client: AsyncClient,
        session,
        settings,
        claimed: bool,
        status: ScanStatus,
        is_failed: bool,
    ) -> None:  # noqa: ANN001
        await _sign_up(client)
        cid = await _make_client(client)
        sid = await _scan_with_results(
            session,
            cid,
            [OK],
            status=status,
            lease_expires_at=datetime.now(UTC) + LEASE if status is ScanStatus.RUNNING else None,
        )
        before = await _stored(session, sid)

        await executor_module._mark_failed(  # noqa: SLF001
            sid, RuntimeError("an error of ours"), claimed=claimed, settings=settings
        )

        stored = await _stored(session, sid)
        if is_failed:
            assert stored["status"] == "failed"
            assert stored["error_code"] == EXECUTION_FAILED
            assert stored["error_detail"] == "RuntimeError"
            assert stored["finished_at"] is not None
        else:
            assert stored == before, "an executor that does not own the scan failed it"

    async def test_a_losing_executor_that_raises_leaves_the_winners_scan_to_finish(
        self, client: AsyncClient, session, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        """Both executors are real. The winner is held inside its first phase.

        The same job goes to a second executor: its claim matches nothing, and then its
        refresh raises (a dropped connection, say). The winner's scan must be exactly as
        the winner left it, and the winner must go on to finish it.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch)
        in_the_first_phase = asyncio.Event()
        release_the_winner = asyncio.Event()

        async def the_winner_holds_the_phase(  # noqa: ANN202
            session_, scan_, client_, **kwargs  # noqa: ANN001, ANN003, ARG001
        ):
            in_the_first_phase.set()
            await release_the_winner.wait()

        async def the_connection_drops(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("the connection dropped")

        monkeypatch.setattr(
            executor_module.detection, "ensure_set_for_scan", the_winner_holds_the_phase
        )

        winner = asyncio.create_task(execute_scan(job, settings=settings))
        try:
            await asyncio.wait_for(in_the_first_phase.wait(), timeout=10)
            held = await _stored(session, sid)
            assert held["status"] == "running", "the winner's claim is not what this test needs"

            with monkeypatch.context() as patch:
                patch.setattr(AsyncSession, "refresh", the_connection_drops)
                await execute_scan(job, settings=settings)

            assert await _stored(session, sid) == held, "the loser touched the winner's scan"
            release_the_winner.set()
            await asyncio.wait_for(winner, timeout=60)
        finally:
            release_the_winner.set()
            if not winner.done():
                winner.cancel()
                await asyncio.gather(winner, return_exceptions=True)

        finished = await _stored(session, sid)
        assert finished["status"] == "succeeded"
        assert finished["error_code"] is None

    async def test_an_executor_that_raises_before_it_claims_still_fails_a_queued_scan(
        self, client: AsyncClient, session, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        """Why an unclaimed executor may still fail a QUEUED scan, and is not made to skip it.

        Nobody holds the scan, so it would sit QUEUED, counted against the allowance, until
        the never-started reap took it a quarter of an hour later.
        """
        sid, job = await _queued_scan(client)
        before = await _stored(session, sid)
        assert before["status"] == "queued"

        async def the_connection_drops(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("the connection dropped")

        monkeypatch.setattr(AsyncSession, "get", the_connection_drops)

        await execute_scan(job, settings=settings)

        stored = await _stored(session, sid)
        assert stored["status"] == "failed"
        assert stored["error_code"] == EXECUTION_FAILED
        assert stored["error_detail"] == "RuntimeError"
        assert stored["started_at"] is None, "this executor never claimed the scan"

    async def test_an_executor_that_raises_right_after_its_claim_still_fails_its_scan(
        self, client: AsyncClient, session, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        """A claim that has committed is the executor's, however soon after it raises.

        The refresh straight after the claim's commit is where it raises here, so a claim
        flag set only after that refresh would leave a RUNNING scan nobody is running.
        """
        sid, job = await _queued_scan(client)
        before = await _stored(session, sid)
        assert before["status"] == "queued"
        assert before["started_at"] is None

        async def the_connection_drops(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("the connection dropped")

        monkeypatch.setattr(AsyncSession, "refresh", the_connection_drops)

        await execute_scan(job, settings=settings)

        stored = await _stored(session, sid)
        assert stored["started_at"] is not None, "the claim did not commit"
        assert stored["status"] == "failed"
        assert stored["error_code"] == EXECUTION_FAILED
        assert stored["error_detail"] == "RuntimeError"

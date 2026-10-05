"""A scan that hits its deadline keeps the answers that finished — decision 7, 2026-10-01.

THE DECISION (the founder's, final)
-----------------------------------
"Scan deadline: count by cause. Add a failure reason; restarts and our errors
never count. A scan that hits its deadline finalises as PARTIAL with the answers
it has, and counts."

Before it, a scan that ran out of time ended FAILED / SCAN_DEADLINE_EXCEEDED
whatever it had done, and the engine loop made that expensive: `run_scan`
gathers every prompt and persists only after the gather, so the answers already
paid for sat in memory and were thrown away with the task.

WHAT IS PINNED HERE
-------------------
* A deadline in the engine loop keeps the prompts that finished — written with
  the existing `persist_result`, once, by the executor — and, when at least half
  of the PLANNED prompts have an answered engine result (the founder's 50% rule,
  `deadline_partial_counts`), scores them and ends PARTIAL /
  SCAN_DEADLINE_EXCEEDED. That scan holds an allowance slot.
* Below that line (too few prompts answered, nothing finished, or everything
  that finished was an engine error) it ends FAILED / SCAN_DEADLINE_EXCEEDED,
  is not scored, keeps its rows and holds no slot.
* A deadline after the engine phase keeps every row and never re-scores a scan
  that already has a score. It runs no audit, no alerts and no fixes: fixes
  spend money, and a deadline is the moment to stop spending it. The 50% rule
  judges this path too: below it the scan ends FAILED and holds no slot, and
  the score the chain made before the deadline stays.
* A scan whose lease is already gone is not this executor's to write.
* The rows are never written twice, and a finalise that itself fails falls back
  to the plain failure — the one that costs the customer nothing.
* The allowance reads the STATUS and never the error code: FAILED holds no slot
  whatever the cause, so a restart or one of our own errors can never count.

No engine, SerpApi, browser or Stripe call is made: engines are faked at
`engine_service.ask_all`, and `conftest.stub_chain_externals` covers detection,
the audit and the fix model.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import (
    ActionItem,
    BrandMention,
    Client,
    EngineResult,
    Prompt,
    PromptSet,
    Scan,
    ScanStatus,
    Score,
    TechnicalAudit,
)
from avp_api.models.engine_result import ANSWERED_STATUSES, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import scan_allowance, scan_runner, scoring_runner
from avp_api.services import scan_executor as executor_module
from avp_api.services.engines import EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import (
    EXECUTION_FAILED,
    EXECUTOR_LOST,
    EXECUTOR_NEVER_STARTED,
    LEASE,
    SCAN_DEADLINE_EXCEEDED,
    ScanDeadlineError,
    ScanJob,
    deadline_partial_counts,
    execute_scan,
)

BASE = "/api/v1"

# The pipeline's own code (a claim, detection against stubs, a prompt set, the
# prompts that return at once; for the one test that stops in the fix phase, the
# audit, the score and the alerts too) takes under half a second here. What must
# NOT return inside the deadline sleeps for an hour. Generous on purpose: the
# tests that need the fast work to finish assert that it did, so a machine too
# slow for this number fails with that message rather than a puzzling row count.
DEADLINE = timedelta(seconds=2)

# The text a faked engine returns. It names Zendesk, then the subject, then Front
# (test_scan_chain.py's), so brand detection has real work to do.
ANSWER = "Zendesk is popular. Help Scout is simpler and well liked. Front is newer."

# Derived, not retyped: the code `terminal_status_for` writes when every call failed.
ALL_ENGINE_CALLS_FAILED = scan_runner.terminal_status_for(1, 1)[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _queued_scan(client: AsyncClient) -> tuple[str, ScanJob]:
    """A queued scan and the job that would run it. Nothing has run."""
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Deadline Test Agency", "fullName": "Op",
              "email": "deadline@test.example", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text
    # `classify: False`: no crawl and no model call at intake.
    made = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert made.status_code == 201, made.text

    jobs: list[ScanJob] = []

    class _Deferred:
        async def submit(self, job, *, settings) -> None:  # noqa: ANN001
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
    queued = await client.post(f"{BASE}/clients/{made.json()['id']}/scans", json={})
    assert queued.status_code == 202, queued.text
    return queued.json()["id"], jobs[0]


def _fake_engines(monkeypatch: pytest.MonkeyPatch, plan: dict[str, str]) -> SimpleNamespace:
    """Prompt generation and engine answers, decided per prompt.

    `plan` maps each generated prompt's text to what its engines do:

    * `"answers"` returns at once with an answer that names the subject;
    * `"late"` is `"answers"` after a short pause, so it finishes AFTER the
      prompts behind it;
    * `"errors"` returns at once with every engine in error;
    * `"mixed"` returns at once, its first engine answering and every other in
      error: the prompt is answered, the scan is not clean;
    * `"raises"` raises, as an adapter bug would;
    * `"times_out"` raises the builtin `TimeoutError`, as a bug of ours might —
      no clock is involved, so it is not the deadline;
    * `"hangs"` never returns (an hour's sleep; the deadline cancels it).

    Returns what the fakes saw: `in_loop` is set the moment a prompt reaches the
    engines, and `returned` lists the prompts that came back, so a test can
    assert its own precondition instead of trusting a clock.
    """
    generated = [
        GeneratedPrompt(text=text, intent=list(PromptIntent)[i % 3])
        for i, text in enumerate(plan)
    ]
    seen = SimpleNamespace(in_loop=asyncio.Event(), returned=[])

    async def fake_generate(**kwargs):  # noqa: ANN003, ANN202, ARG001
        return generated, "stub-generator"

    async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ANN202, ARG001
        seen.in_loop.set()
        what = plan[prompt]
        if what == "hangs":
            await asyncio.sleep(3600)
        if what == "late":
            await asyncio.sleep(0.2)
        if what == "raises":
            raise RuntimeError("SENTINEL-engine-detail")
        if what == "times_out":
            raise TimeoutError("SENTINEL-our-own-timeout")
        seen.returned.append(prompt)
        if what == "errors":
            return [
                EngineAnswer(
                    engine=e, engine_version="stub", prompt_text=prompt,
                    status=EngineResultStatus.ERROR, error_code="PROVIDER_ERROR",
                )
                for e in engines
            ]
        if what == "mixed":
            return [
                EngineAnswer(
                    engine=e, engine_version="stub", prompt_text=prompt, text=ANSWER,
                    latency_ms=10, status=EngineResultStatus.OK,
                )
                if i == 0
                else EngineAnswer(
                    engine=e, engine_version="stub", prompt_text=prompt,
                    status=EngineResultStatus.ERROR, error_code="PROVIDER_ERROR",
                )
                for i, e in enumerate(engines)
            ]
        return [
            EngineAnswer(
                engine=e, engine_version="stub", prompt_text=prompt, text=ANSWER,
                latency_ms=10, status=EngineResultStatus.OK,
            )
            for e in engines
        ]

    async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ANN202, ARG001
        return Sentiment.POSITIVE, Decimal("0.900")

    monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
    monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
    monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", fake_sentiment)
    return seen


def _watch_what_a_deadline_must_not_run(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any call into the audit, the alerts or the fix list.

    For the tests whose chain is stopped inside the engine loop, so none of the
    three is reachable from it: a call recorded here came from the finalise.
    Each wrapper still delegates, so nothing about a run changes by watching it.
    """
    called: list[str] = []

    def watch(module: object, name: str, label: str) -> None:
        original = getattr(module, name)

        async def watched(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            called.append(label)
            return await original(*args, **kwargs)

        monkeypatch.setattr(module, name, watched)

    watch(executor_module.audit_runner, "run_audit", "audit")
    watch(executor_module.alerts_service, "generate_for_scan", "alerts")
    watch(executor_module.fix_runner, "generate_for_scan", "fixes")
    return called


async def _scan(session, sid: str) -> Scan:  # noqa: ANN001
    scan = await session.get(Scan, sid)
    assert scan is not None
    await session.refresh(scan)
    return scan


async def _rows(session, model, sid: str) -> list:  # noqa: ANN001
    return list((await session.execute(select(model).where(model.scan_id == sid))).scalars())


async def _prompt_ids(session, sid: str) -> dict[str, str]:  # noqa: ANN001
    """This scan's prompts, by text."""
    rows = await session.execute(
        select(Prompt.text, Prompt.id)
        .join(PromptSet, PromptSet.id == Prompt.prompt_set_id)
        .where(PromptSet.scan_id == sid)
    )
    return {text: prompt_id for text, prompt_id in rows}


async def _slots_held(session, sid: str) -> int:  # noqa: ANN001
    """What the allowance itself counts for this scan's agency, as of now."""
    scan = await session.get(Scan, sid)
    assert scan is not None
    return await scan_allowance.count_scans(session, scan.agency_id, now=datetime.now(UTC))


# ---------------------------------------------------------------------------
# a deadline in the engine loop
# ---------------------------------------------------------------------------


class TestADeadlineInTheEngineLoopKeepsWhatFinished:
    async def test_the_prompts_that_finished_are_kept_and_the_scan_counts(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """k of n prompts finished: exactly k x engines rows, a score, a slot."""
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {
            "finished 1": "answers", "finished 2": "answers", "finished 3": "answers",
            "unfinished 1": "hangs", "unfinished 2": "hangs",
        })
        beyond = _watch_what_a_deadline_must_not_run(monkeypatch)
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        # The precondition, so a clock too slow for DEADLINE says so by name.
        assert sorted(fakes.returned) == ["finished 1", "finished 2", "finished 3"]

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.PARTIAL
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert scan.error_detail == "ScanDeadlineError"
        assert scan.finished_at is not None
        # The finalise renewed the lease before it touched anything: a lease
        # pushed out from the moment of the deadline, not the one the claim
        # stamped when the scan began (`started_at`), DEADLINE ago.
        assert scan.lease_expires_at > scan.started_at + LEASE + DEADLINE / 2

        by_text = await _prompt_ids(session, sid)
        rows = await _rows(session, EngineResult, sid)
        assert len(rows) == 3 * len(job.engines)
        assert {r.prompt_id for r in rows} == {
            by_text["finished 1"], by_text["finished 2"], by_text["finished 3"]
        }
        # The count says what is in the table, not what the scan set out to do.
        assert scan.engine_result_count == len(rows)
        assert scan.prompt_count == 5

        # Facts as `persist_result` writes them: a digest, never the answer; and
        # the facts are the ones the engine loop extracted, rivals included.
        assert all(r.response_digest and len(r.response_digest) == 64 for r in rows)
        assert all(r.mentioned and r.position == 2 and r.brands_mentioned == 3 for r in rows)
        mentions = list((await session.execute(
            select(BrandMention)
            .join(EngineResult, EngineResult.id == BrandMention.engine_result_id)
            .where(EngineResult.scan_id == sid)
        )).scalars())
        rivals = [m for m in mentions if not m.is_subject]
        assert rivals, "the rivals were not persisted"
        assert all(m.competitor_id is not None for m in rivals), (
            "the competitor map did not reach the finalise, so rivals lost their ids"
        )

        # Scored from what was kept.
        assert len(await _rows(session, Score, sid)) == 1

        # And it holds its slot, by the allowance's own count.
        assert await _slots_held(session, sid) == 1

        # No audit, no alerts, no fixes. Fixes spend money.
        assert beyond == []
        assert await _rows(session, TechnicalAudit, sid) == []
        assert await _rows(session, ActionItem, sid) == []

    async def test_nothing_finished_the_scan_fails_and_holds_no_slot(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {"unfinished 1": "hangs", "unfinished 2": "hangs"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert fakes.in_loop.is_set(), "the deadline fell before the engine loop"
        assert fakes.returned == []
        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert scan.finished_at is not None
        assert await _rows(session, EngineResult, sid) == []
        assert await _rows(session, Score, sid) == []
        assert await _slots_held(session, sid) == 0

    async def test_only_failed_calls_finished_the_scan_fails_and_holds_no_slot(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """An engine error is not an answer: nothing here is worth a slot.

        The failed calls are kept as the record they are, exactly as an
        all-failed scan keeps them, and the scan is FAILED with the deadline as
        its cause.
        """
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {
            "broken 1": "errors", "broken 2": "errors", "unfinished": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert sorted(fakes.returned) == ["broken 1", "broken 2"]
        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        rows = await _rows(session, EngineResult, sid)
        assert len(rows) == 2 * len(job.engines)
        assert all(r.status is EngineResultStatus.ERROR for r in rows)
        assert scan.engine_result_count == len(rows)
        assert await _slots_held(session, sid) == 0

    async def test_the_rows_read_in_prompt_order_whichever_prompt_finished_first(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """`GET /scans/{scanId}/results` is ascending by id.

        `run_scan` writes in prompt order, so the report reads in prompt order.
        The deadline's rows must read the same way even though the loop finished
        its prompts in a different order: here the first prompt finishes after
        the second, and the third never does.
        """
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {
            "first": "late", "second": "answers", "third": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert fakes.returned == ["second", "first"], "the first prompt must finish last"
        by_text = await _prompt_ids(session, sid)
        rows = sorted(await _rows(session, EngineResult, sid), key=lambda r: r.id)
        assert len(rows) == 2 * len(job.engines)
        assert list(dict.fromkeys(r.prompt_id for r in rows)) == [
            by_text["first"], by_text["second"]
        ]
        # And within a prompt, the engines in the order the scan asked for them.
        assert [r.engine for r in rows[: len(job.engines)]] == list(job.engines)

    async def test_just_below_half_the_planned_prompts_the_scan_fails_and_keeps_its_rows(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """2 of 5 planned prompts answered: below the founder's half. FAILED, no score, no slot."""
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {
            "done 1": "answers", "done 2": "answers",
            "unfinished 1": "hangs", "unfinished 2": "hangs", "unfinished 3": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert sorted(fakes.returned) == ["done 1", "done 2"]
        scan = await _scan(session, sid)
        assert scan.prompt_count == 5
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        rows = await _rows(session, EngineResult, sid)
        assert len(rows) == 2 * len(job.engines)
        assert scan.engine_result_count == len(rows)
        assert await _rows(session, Score, sid) == []
        assert await _slots_held(session, sid) == 0

    async def test_exactly_half_the_planned_prompts_the_scan_counts(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """2 of 4 planned prompts answered: the line itself is in. PARTIAL, scored, slot held."""
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {
            "done 1": "answers", "done 2": "answers",
            "unfinished 1": "hangs", "unfinished 2": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert sorted(fakes.returned) == ["done 1", "done 2"]
        scan = await _scan(session, sid)
        assert scan.prompt_count == 4
        assert scan.status is ScanStatus.PARTIAL
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert len(await _rows(session, Score, sid)) == 1
        assert await _slots_held(session, sid) == 1

    async def test_a_prompt_with_one_engine_answering_is_an_answered_prompt(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """2 of 4 prompts each with one ok and one error result: answered, so PARTIAL."""
        sid, job = await _queued_scan(client)
        assert len(job.engines) >= 2, "a mixed prompt needs two engines"
        _fake_engines(monkeypatch, {
            "mixed 1": "mixed", "mixed 2": "mixed",
            "unfinished 1": "hangs", "unfinished 2": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.PARTIAL
        rows = await _rows(session, EngineResult, sid)
        assert any(r.status is EngineResultStatus.ERROR for r in rows)
        assert len(await _rows(session, Score, sid)) == 1
        assert await _slots_held(session, sid) == 1

    async def test_a_prompt_whose_every_engine_errored_is_not_an_answered_prompt(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """Errored prompts add nothing: 1 answered of 4 planned is below half."""
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {
            "done": "answers", "broken 1": "errors", "broken 2": "errors",
            "unfinished": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        # 1 answered of 4 planned: below half.
        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert await _slots_held(session, sid) == 0


class TestTheDeadlineRule:
    @pytest.mark.parametrize(
        ("answered", "planned", "counts"),
        [(49, 100, False), (50, 100, True), (0, 10, False), (10, 10, True),
         (1, 2, True), (1, 3, False), (5, 0, False)],
    )
    def test_at_least_half_the_planned_prompts_answered(
        self, answered: int, planned: int, counts: bool
    ) -> None:
        assert deadline_partial_counts(answered, planned) is counts


# ---------------------------------------------------------------------------
# a deadline after the engine phase
# ---------------------------------------------------------------------------


class TestADeadlineAfterTheEngineLoop:
    async def test_every_row_is_kept_and_a_score_that_exists_is_not_recomputed(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The fix list never returns: the audit, the score and the alerts are done."""
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {"first": "answers", "second": "answers"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        reached: list[str] = []

        async def fixes_never_return(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            reached.append("fixes")
            await asyncio.sleep(3600)

        monkeypatch.setattr(executor_module.fix_runner, "generate_for_scan", fixes_never_return)

        scored: list[str] = []
        real_score_scan = scoring_runner.score_scan

        async def counting_score_scan(session_, scan, **kwargs):  # noqa: ANN001, ANN003, ANN202
            scored.append(scan.id)
            return await real_score_scan(session_, scan, **kwargs)

        monkeypatch.setattr(scoring_runner, "score_scan", counting_score_scan)

        await execute_scan(job, settings=settings)

        assert reached == ["fixes"], "the deadline did not fall in the fix phase"
        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.PARTIAL
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        rows = await _rows(session, EngineResult, sid)
        assert len(rows) == 2 * len(job.engines)
        assert scan.engine_result_count == len(rows)
        # The chain's own scoring phase ran once; the finalise did not run it again.
        assert scored == [sid]
        assert len(await _rows(session, Score, sid)) == 1
        assert len(await _rows(session, TechnicalAudit, sid)) == 1
        assert await _rows(session, ActionItem, sid) == []
        assert await _slots_held(session, sid) == 1

    async def test_below_half_the_planned_prompts_it_fails_and_the_score_it_made_stays(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The founder's half judges this path too: 1 of 4 planned prompts answered.

        The chain scored the scan (phase 7) before the fix list hung, so a Score
        row already exists when the deadline falls. The scan still ends FAILED /
        SCAN_DEADLINE_EXCEEDED and holds no slot; the score is kept, because the
        finalise deletes nothing it did not write, and the rows are kept too.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {
            "answered": "answers",
            "broken 1": "errors", "broken 2": "errors", "broken 3": "errors",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        reached: list[str] = []

        async def fixes_never_return(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            reached.append("fixes")
            await asyncio.sleep(3600)

        monkeypatch.setattr(executor_module.fix_runner, "generate_for_scan", fixes_never_return)

        scored: list[str] = []
        real_score_scan = scoring_runner.score_scan

        async def counting_score_scan(session_, scan, **kwargs):  # noqa: ANN001, ANN003, ANN202
            scored.append(scan.id)
            return await real_score_scan(session_, scan, **kwargs)

        monkeypatch.setattr(scoring_runner, "score_scan", counting_score_scan)

        await execute_scan(job, settings=settings)

        assert reached == ["fixes"], "the deadline did not fall in the fix phase"
        scan = await _scan(session, sid)
        assert scan.prompt_count == 4
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        rows = await _rows(session, EngineResult, sid)
        assert len(rows) == 4 * len(job.engines)
        assert scan.engine_result_count == len(rows)
        # One prompt has an answered result; the other three are all errors.
        answered = {r.prompt_id for r in rows if r.status in ANSWERED_STATUSES}
        assert len(answered) == 1
        # The chain's own scoring phase ran once and its Score is still there.
        assert scored == [sid]
        assert len(await _rows(session, Score, sid)) == 1
        assert await _slots_held(session, sid) == 0


# ---------------------------------------------------------------------------
# a lease that is already gone
# ---------------------------------------------------------------------------


class TestALeaseThatIsAlreadyGone:
    async def test_a_scan_the_reaper_already_failed_is_left_exactly_as_it_was(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The deadline fires on a row that is no longer RUNNING: write nothing.

        The reaper's verdict stands, and the answers that finished are not
        written onto a scan somebody else has already given up on. The heartbeat
        (30s) never fires inside the test, so the lease is taken from under a
        chain that has not noticed, which is the window this guards.
        """
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {"finished": "answers", "unfinished": "hangs"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        reaped_at = datetime.now(UTC) - timedelta(minutes=2)

        async def the_reaper() -> None:
            await fakes.in_loop.wait()
            await session.execute(
                sa_update(Scan).where(Scan.id == sid).values(
                    status=ScanStatus.FAILED, error_code=EXECUTOR_LOST,
                    error_detail="reaped", finished_at=reaped_at,
                )
            )
            await session.commit()

        await asyncio.gather(execute_scan(job, settings=settings), the_reaper())

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTOR_LOST
        assert scan.error_detail == "reaped"
        assert scan.finished_at == reaped_at
        assert await _rows(session, EngineResult, sid) == []
        assert await _rows(session, Score, sid) == []
        assert await _slots_held(session, sid) == 0

    async def test_a_row_taken_after_the_renewal_is_not_stamped_over(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The stamp is conditional on the row still being RUNNING.

        The renewal said the row was ours, and something took it before the
        stamp — here, at the moment the finalise asks whether a score exists.
        The stamp matches nothing, says so, and the verdict that landed stands.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {"finished": "answers", "unfinished": "hangs"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        real_latest_score = scoring_runner.latest_score

        async def taken_then_asked(session_, scan_id):  # noqa: ANN001, ANN202
            await session.execute(
                sa_update(Scan).where(Scan.id == scan_id).values(
                    status=ScanStatus.FAILED, error_code=EXECUTOR_LOST, error_detail="taken",
                )
            )
            await session.commit()
            return await real_latest_score(session_, scan_id)

        monkeypatch.setattr(scoring_runner, "latest_score", taken_then_asked)

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTOR_LOST
        assert scan.error_detail == "taken"
        names = [e["event"] for e in events]
        assert "scan.deadline.lease_lost_before_stamp" in names
        assert "scan.finalized_after_deadline" not in names
        assert await _slots_held(session, sid) == 0


# ---------------------------------------------------------------------------
# only the deadline keeps anything
# ---------------------------------------------------------------------------


class TestOnlyTheDeadlineKeepsAnything:
    async def test_an_error_of_ours_is_still_the_plain_failure_and_keeps_nothing(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """A prompt finished, then the loop raised: that is OUR error, not the clock.

        `EXECUTION_FAILED`, no rows, no slot — exactly as before the decision.
        What the loop had finished is not kept, because nothing is known about
        the state the failure left behind, and the customer is not charged for
        our fault.
        """
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {"finished": "answers", "explodes": "raises"})

        await execute_scan(job, settings=settings)

        assert fakes.returned == ["finished"], "the first prompt must finish before the error"
        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTION_FAILED
        assert scan.error_detail == "RuntimeError"
        assert await _rows(session, EngineResult, sid) == []
        assert await _slots_held(session, sid) == 0

    async def test_a_timeout_of_ours_is_not_the_deadline_and_keeps_nothing(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """A `TimeoutError` the chain raised itself, long before the clock.

        `_until_deadline` used to turn ANY `TimeoutError` leaving the chain into
        `ScanDeadlineError`, so with a prompt finished this would have ended
        PARTIAL / SCAN_DEADLINE_EXCEEDED and counted: an error of ours holding
        an allowance slot. It is `EXECUTION_FAILED`, it keeps nothing and it
        never counts. `MAX_SCAN_DURATION` is left at its 2,400s, so the clock
        cannot be what fired; the real deadline's own tests are above.
        """
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {"finished": "answers", "times out": "times_out"})

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)

        assert fakes.returned == ["finished"], "the first prompt must finish before the timeout"
        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == EXECUTION_FAILED
        assert scan.error_detail == "TimeoutError"
        assert await _rows(session, EngineResult, sid) == []
        assert await _rows(session, Score, sid) == []
        assert await _slots_held(session, sid) == 0
        # And the deadline's path was never taken.
        names = [e["event"] for e in events]
        assert "scan.execute.failed" in names
        assert "scan.deadline_exceeded" not in names
        assert "scan.finalized_after_deadline" not in names


class TestOnlyTheClocksTimeoutIsTheDeadline:
    """`_until_deadline` itself, with no database and no scan.

    `asyncio.timeout` converts only the cancellation IT caused. The clause that
    turns its `TimeoutError` into `ScanDeadlineError` catches every
    `TimeoutError` that leaves the block, including one the chain raised, so it
    has to ask the timeout whether the clock is what fired.
    """

    async def test_a_timeout_the_chain_raised_itself_passes_through_unchanged(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", timedelta(seconds=60))
        ours = TimeoutError("SENTINEL-our-own-timeout")

        async def chain() -> None:
            raise ours

        with pytest.raises(TimeoutError) as caught:
            await executor_module._until_deadline(chain())  # noqa: SLF001

        assert caught.value is ours
        assert not isinstance(caught.value, ScanDeadlineError)

    async def test_the_clocks_own_timeout_is_still_the_deadline(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", timedelta(seconds=0.05))
        reached: list[str] = []

        async def chain() -> None:
            reached.append("started")
            await asyncio.sleep(3600)
            reached.append("finished")

        with pytest.raises(ScanDeadlineError):
            await executor_module._until_deadline(chain())  # noqa: SLF001

        assert reached == ["started"]


# ---------------------------------------------------------------------------
# a failure never overwrites a scan that has finished
# ---------------------------------------------------------------------------


async def _until_a_statement_waits_on_a_row_lock(session) -> None:  # noqa: ANN001
    """Poll until some backend is blocked on a lock.

    `pg_stat_clear_snapshot()` first: inside a transaction Postgres serves
    `pg_stat_activity` from a snapshot taken at the first read, so without it
    every later poll would see the first answer.
    """
    for _ in range(250):
        await session.execute(text("SELECT pg_stat_clear_snapshot()"))
        waiting = (await session.execute(text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ))).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("nothing ever waited on the row lock")


class TestAFailureNeverOverwritesAFinishedScan:
    """`_mark_failed` is the fallback of the deadline's finalise, and so can run
    right after (or while) a PARTIAL lands. It must never replace one.
    """

    @pytest.mark.parametrize(
        "exc", [ScanDeadlineError(), RuntimeError("an error of ours")], ids=["deadline", "ours"]
    )
    async def test_a_scan_that_is_already_partial_stays_partial(
        self, client: AsyncClient, session, settings, exc: Exception
    ) -> None:  # noqa: ANN001
        sid, _ = await _queued_scan(client)
        finished = datetime.now(UTC) - timedelta(minutes=1)
        await session.execute(sa_update(Scan).where(Scan.id == sid).values(
            status=ScanStatus.PARTIAL, error_code=SCAN_DEADLINE_EXCEEDED,
            error_detail="ScanDeadlineError", finished_at=finished,
        ))
        await session.commit()

        await executor_module._mark_failed(  # noqa: SLF001
            sid, exc, claimed=True, settings=settings
        )

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.PARTIAL
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert scan.error_detail == "ScanDeadlineError"
        assert scan.finished_at == finished
        assert await _slots_held(session, sid) == 1

    async def test_a_partial_that_lands_while_the_failure_waits_for_the_row_is_kept(
        self, client: AsyncClient, session, settings, engine
    ) -> None:  # noqa: ANN001
        """The race a read followed by a write can lose, against real row locks.

        Another transaction has set the scan PARTIAL and not committed — the
        deadline's stamp, in flight. `_mark_failed` runs and has to wait for the
        row. When the PARTIAL commits, a write decided on an earlier READ of the
        row still goes ahead and replaces it with FAILED; a conditional UPDATE
        checks the status again once the row is free, and matches nothing.
        """
        sid, _ = await _queued_scan(client)
        await session.execute(sa_update(Scan).where(Scan.id == sid).values(
            status=ScanStatus.RUNNING, started_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + LEASE,
        ))
        await session.commit()
        stamped_at = datetime.now(UTC)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        async with factory() as other:
            await other.execute(sa_update(Scan).where(Scan.id == sid).values(
                status=ScanStatus.PARTIAL, error_code=SCAN_DEADLINE_EXCEEDED,
                error_detail="ScanDeadlineError", finished_at=stamped_at,
            ))
            writer = asyncio.create_task(
                executor_module._mark_failed(  # noqa: SLF001
                    sid, ScanDeadlineError(), claimed=True, settings=settings
                )
            )
            try:
                await _until_a_statement_waits_on_a_row_lock(session)
                assert not writer.done(), "the failure was written without waiting for the row"
                await other.commit()
                await asyncio.wait_for(writer, timeout=10)
            finally:
                if not writer.done():
                    writer.cancel()
                    await asyncio.gather(writer, return_exceptions=True)

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.PARTIAL, "the failure overwrote a PARTIAL that landed"
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert scan.error_detail == "ScanDeadlineError"
        assert scan.finished_at == stamped_at
        assert await _slots_held(session, sid) == 1


# ---------------------------------------------------------------------------
# the rows are written once
# ---------------------------------------------------------------------------


class TestTheRowsAreWrittenOnce:
    async def test_results_already_committed_are_not_written_a_second_time(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """The deadline's worst moment: the loop's own commit has landed and the
        flag that says so has not been set.

        Reached for real, not by resetting the flag: the chain session's commit
        is held open right after the commit that writes the results, which is
        exactly where `run_scan` would set `persisted`. The holder still holds
        both prompts, so a finalise that trusted it rather than the table would
        write every row again.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {"first": "answers", "second": "answers"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        real_run_scan = scan_runner.run_scan
        real_commit = AsyncSession.commit
        seen: dict = {}

        async def run_scan_watched(session_, scan, client_, **kwargs):  # noqa: ANN001, ANN003, ANN202
            progress = kwargs.get("progress")
            seen["progress"] = progress

            async def commit_then_stick(self) -> None:  # noqa: ANN001
                await real_commit(self)
                if self is session_ and progress is not None and progress.completed:
                    # Every prompt has finished, so this is the commit that
                    # wrote their results. The deadline lands before the flag.
                    await asyncio.sleep(3600)

            monkeypatch.setattr(AsyncSession, "commit", commit_then_stick)
            return await real_run_scan(session_, scan, client_, **kwargs)

        monkeypatch.setattr(scan_runner, "run_scan", run_scan_watched)

        await execute_scan(job, settings=settings)

        progress = seen["progress"]
        assert progress is not None, "execute_scan gave run_scan no progress holder"
        assert progress.persisted is False, "the deadline did not fall before the flag"
        assert len(progress.completed) == 2, "the holder no longer held what the loop had written"
        rows = await _rows(session, EngineResult, sid)
        assert len(rows) == 2 * len(job.engines), "the results were written twice"
        assert len({(r.prompt_id, r.engine) for r in rows}) == len(rows)
        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.PARTIAL
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert scan.engine_result_count == len(rows)
        # Nothing had scored it yet, so the finalise did.
        assert len(await _rows(session, Score, sid)) == 1


# ---------------------------------------------------------------------------
# a finalise that goes wrong
# ---------------------------------------------------------------------------


class TestAFinaliseThatGoesWrong:
    @pytest.mark.parametrize("failure", ["raises", "hangs", "stuck_in_a_statement"])
    async def test_a_score_that_cannot_be_computed_does_not_cost_the_partial(
        self, client: AsyncClient, session, settings, monkeypatch, failure: str
    ) -> None:  # noqa: ANN001
        """The answers are kept and the scan counts; the score is simply absent.

        Three ways to fail: the score raises; it never returns; and it is stuck
        inside a real database statement when the budget cancels it, which
        leaves its session unusable. The stamp must survive all three, because
        the score is only ever the part of this that can be given up.

        Logged with the error CLASS only. The message here carries a sentinel
        that must not be found anywhere in what was logged.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {"finished": "answers", "unfinished": "hangs"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        monkeypatch.setattr(executor_module, "DEADLINE_SCORING_BUDGET", timedelta(seconds=0.2))

        async def cannot_score(session_, scan, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
            if failure == "hangs":
                await asyncio.sleep(3600)
            if failure == "stuck_in_a_statement":
                await session_.execute(text("SELECT pg_sleep(30)"))
            raise RuntimeError("SENTINEL-scoring-detail")

        monkeypatch.setattr(scoring_runner, "score_scan", cannot_score)

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.PARTIAL
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert len(await _rows(session, EngineResult, sid)) == len(job.engines)
        assert await _rows(session, Score, sid) == []
        assert await _slots_held(session, sid) == 1

        failed = [e for e in events if e["event"] == "scan.deadline.scoring_failed"]
        assert len(failed) == 1
        assert failed[0]["scan_id"] == sid
        assert failed[0]["error"] == ("RuntimeError" if failure == "raises" else "TimeoutError")
        assert "SENTINEL" not in repr(events)

    async def test_a_finalise_that_raises_falls_back_to_the_plain_failure(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """When it cannot say what the scan kept, it does not claim a slot for it.

        FAILED / SCAN_DEADLINE_EXCEEDED is `_mark_failed`'s ending, the one that
        costs the customer nothing.
        """
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {"finished": "answers", "unfinished": "hangs"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        async def cannot_count(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("SENTINEL-database-detail")

        monkeypatch.setattr(scan_runner, "count_answered_prompts", cannot_count)

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)

        scan = await _scan(session, sid)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        assert scan.error_detail == "ScanDeadlineError"
        assert scan.finished_at is not None
        assert await _slots_held(session, sid) == 0

        failed = [e for e in events if e["event"] == "scan.deadline.finalize_failed"]
        assert len(failed) == 1
        assert failed[0]["error"] == "RuntimeError"
        assert "SENTINEL" not in repr(events)

    def test_the_scoring_budget_sits_well_inside_the_fresh_lease(self) -> None:
        """The finalise renews the lease once and then works inside it."""
        assert executor_module.DEADLINE_SCORING_BUDGET < LEASE / 2


# ---------------------------------------------------------------------------
# the engine loop reports what it has finished
# ---------------------------------------------------------------------------


class TestTheEngineLoopReportsWhatItHasFinished:
    @pytest.mark.parametrize("defer", [True, False], ids=["deferred", "terminal"])
    async def test_the_holder_fills_as_prompts_finish_and_is_emptied_once_they_are_written(
        self, client: AsyncClient, session, settings, monkeypatch, defer: bool
    ) -> None:  # noqa: ANN001
        sid, job = await _queued_scan(client)
        scan = await session.get(Scan, sid)
        row = await session.get(Client, scan.client_id)
        # A competitor set, so the map the holder carries has something in it.
        await executor_module.detection.ensure_set_for_scan(
            session, scan, row, settings=settings
        )
        await session.commit()

        progress = scan_runner.EnginePhaseProgress()
        seen: dict = {}

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", _two_prompts)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", _sentiment)

        async def ask_all(prompt, *, engines, settings):  # noqa: ANN001, ANN202, ARG001
            if prompt == "late":
                # Held until the early prompt has finished and been recorded:
                # what the holder shows NOW is what a deadline would find.
                async with asyncio.timeout(10):
                    while not progress.completed:
                        await asyncio.sleep(0)
                seen["mid_loop"] = [prompt_id for prompt_id, _ in progress.completed]
                seen["flag_mid_loop"] = progress.persisted
            return [
                EngineAnswer(engine=e, engine_version="stub", prompt_text=prompt, text=ANSWER)
                for e in engines
            ]

        monkeypatch.setattr(scan_runner.engine_service, "ask_all", ask_all)

        real_commit = AsyncSession.commit

        async def commit_and_note(self) -> None:  # noqa: ANN001
            if progress.completed and "at_the_write" not in seen:
                # The commit that writes the loop's results (the earlier commits
                # come before any prompt has finished): what the holder holds as
                # they land.
                seen["at_the_write"] = [
                    (prompt_id, [engine for engine, _, _ in per_engine])
                    for prompt_id, per_engine in progress.completed
                ]
                seen["flag_at_the_write"] = progress.persisted
            await real_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", commit_and_note)

        await scan_runner.run_scan(
            session, scan, row, settings=settings, engines=job.engines,
            progress=progress, defer_terminal_status=defer,
        )

        by_text = await _prompt_ids(session, sid)
        assert seen["mid_loop"] == [by_text["early"]], "not recorded as the prompt finished"
        assert seen["flag_mid_loop"] is False, "marked written before anything was"
        # As the results were written the holder held every prompt, with every
        # engine in the order the scan asked for them.
        assert sorted(prompt_id for prompt_id, _ in seen["at_the_write"]) == sorted(
            by_text.values()
        )
        assert all(engines == list(job.engines) for _, engines in seen["at_the_write"])
        assert seen["flag_at_the_write"] is False
        # Written, so no longer held: the raw answers it carried would otherwise
        # live through the audit, the score, the alerts and the fixes for nothing.
        assert progress.persisted is True
        assert progress.completed == []
        assert progress.engines == job.engines
        assert progress.competitor_ids, "the competitor map did not reach the holder"
        assert progress.competitor_ids == await scan_runner._competitor_id_map(  # noqa: SLF001
            session, scan
        )

    async def test_a_caller_that_passes_no_holder_behaves_as_before(
        self, client: AsyncClient, session, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        sid, job = await _queued_scan(client)
        _fake_engines(monkeypatch, {"only": "answers"})
        scan = await session.get(Scan, sid)
        row = await session.get(Client, scan.client_id)

        await scan_runner.run_scan(session, scan, row, settings=settings, engines=job.engines)

        assert scan.status is ScanStatus.SUCCEEDED
        assert len(await _rows(session, EngineResult, sid)) == len(job.engines)


class _EngineBug(Exception):  # noqa: N818 - a stand-in for any bug in the loop
    """What the failing prompt raises: the caller must see this type, not a group."""


class TestAFailedPromptEndsTheOthers:
    """Checklist 3.77 (U1): one prompt slot raising ends the slots still asking the engines.

    `asyncio.gather` left them running, still making paid calls, after the scan had failed.
    """

    async def test_the_other_slots_are_cancelled_and_the_scan_sees_the_original_error(
        self, client: AsyncClient, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        sid, job = await _queued_scan(client)
        scan = await session.get(Scan, sid)
        row = await session.get(Client, scan.client_id)
        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", _two_prompts)
        monkeypatch.setattr(scan_runner.extraction_service, "classify_sentiment", _sentiment)
        progress = scan_runner.EnginePhaseProgress()
        waiting, cancelled, all_waiting = [], [], asyncio.Event()

        async def ask_all(prompt, *, engines, settings):  # noqa: ANN001, ANN202, ARG001
            if prompt == "early":
                waiting.append(prompt)
                all_waiting.set()
                try:
                    await asyncio.Event().wait()  # nobody sets it: only a cancellation ends it
                except asyncio.CancelledError:
                    cancelled.append(prompt)
                    raise
            async with asyncio.timeout(30):  # a safety net for a broken fix, never a pause
                await all_waiting.wait()
            raise _EngineBug

        monkeypatch.setattr(scan_runner.engine_service, "ask_all", ask_all)

        with pytest.raises(_EngineBug):
            await scan_runner.run_scan(
                session, scan, row, settings=settings, engines=job.engines, progress=progress
            )

        assert waiting == ["early"], "the sibling slot never reached the engines"
        assert cancelled == ["early"], "the sibling slot was left running"
        assert progress.completed == []


async def _two_prompts(**kwargs):  # noqa: ANN003, ANN202, ARG001
    return (
        [
            GeneratedPrompt(text="early", intent=PromptIntent.AWARENESS),
            GeneratedPrompt(text="late", intent=PromptIntent.COMPARISON),
        ],
        "stub-generator",
    )


async def _sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ANN202, ARG001
    return Sentiment.POSITIVE, Decimal("0.900")


# ---------------------------------------------------------------------------
# which endings count
# ---------------------------------------------------------------------------


async def _seeded(session, sid: str, *, status: ScanStatus, error_code: str | None) -> None:  # noqa: ANN001
    """One more scan for the same client, in a given ending, written directly."""
    scan = await session.get(Scan, sid)
    assert scan is not None
    session.add(Scan(
        id=ids.new_id(ids.SCAN), client_id=scan.client_id, agency_id=scan.agency_id,
        status=status, error_code=error_code, finished_at=datetime.now(UTC),
    ))
    await session.commit()


class TestWhichEndingsCount:
    """Count by cause, which in the allowance means: count by STATUS.

    `count_scans` never reads `error_code`. FAILED holds no slot whoever failed
    it — the platform restarting, the platform erroring, every engine failing,
    or the clock — and PARTIAL holds one whatever it carries. These are seeded
    rows asked of the allowance's own function, so a change that made the
    allowance read the code would fail here.
    """

    @pytest.mark.parametrize(
        "code",
        [
            EXECUTOR_LOST,
            EXECUTOR_NEVER_STARTED,
            EXECUTION_FAILED,
            ALL_ENGINE_CALLS_FAILED,
            SCAN_DEADLINE_EXCEEDED,
        ],
    )
    async def test_a_failed_scan_holds_no_slot_whatever_the_cause(
        self, client: AsyncClient, session, code: str
    ) -> None:  # noqa: ANN001
        sid, _ = await _queued_scan(client)
        await session.execute(sa_update(Scan).where(Scan.id == sid).values(
            status=ScanStatus.FAILED, error_code=code, finished_at=datetime.now(UTC),
        ))
        await session.commit()

        assert await _slots_held(session, sid) == 0

    async def test_a_partial_scan_that_hit_its_deadline_holds_one(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        sid, _ = await _queued_scan(client)
        await session.execute(sa_update(Scan).where(Scan.id == sid).values(
            status=ScanStatus.PARTIAL, error_code=SCAN_DEADLINE_EXCEEDED,
            finished_at=datetime.now(UTC),
        ))
        await session.commit()

        assert await _slots_held(session, sid) == 1

    async def test_the_counts_add_up_across_the_endings(
        self, client: AsyncClient, session
    ) -> None:  # noqa: ANN001
        """Two that count and three that do not, side by side in one agency."""
        sid, _ = await _queued_scan(client)
        await session.execute(sa_update(Scan).where(Scan.id == sid).values(
            status=ScanStatus.SUCCEEDED, finished_at=datetime.now(UTC),
        ))
        await session.commit()
        await _seeded(session, sid, status=ScanStatus.PARTIAL, error_code=SCAN_DEADLINE_EXCEEDED)
        for code in (EXECUTOR_LOST, EXECUTION_FAILED, SCAN_DEADLINE_EXCEEDED):
            await _seeded(session, sid, status=ScanStatus.FAILED, error_code=code)

        assert await _slots_held(session, sid) == 2


# ---------------------------------------------------------------------------
# the screens can say why — checklist 3.15
# ---------------------------------------------------------------------------


class TestTheScreensCanSayWhy:
    """What the executor writes is what the report and the list return.

    The seeded rows in `test_report_endpoint.py` and `test_dashboard.py` pin the
    projection for every ending. These run the real deadline, so the code the
    EXECUTOR stamps is the code the READS hand the web, through the routes a
    browser uses; the web turns the pair into the sentence that says whether the
    scan counted (`scanOutcome.ts`).
    """

    async def test_a_partial_scan_says_it_ran_out_of_time_in_its_report_and_in_the_list(
        self, client: AsyncClient, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {
            "finished 1": "answers", "finished 2": "answers", "finished 3": "answers",
            "unfinished 1": "hangs", "unfinished 2": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        # The precondition, so a clock too slow for DEADLINE says so by name.
        assert sorted(fakes.returned) == ["finished 1", "finished 2", "finished 3"]

        report = await client.get(f"{BASE}/scans/{sid}/report")
        assert report.status_code == 200, report.text
        assert report.json()["scanStatus"] == "partial"
        assert report.json()["scanErrorCode"] == SCAN_DEADLINE_EXCEEDED

        listed = {
            entry["id"]: entry
            for entry in (await client.get(f"{BASE}/dashboard")).json()["recentScans"]
        }
        assert listed[sid]["status"] == "partial"
        assert listed[sid]["errorCode"] == SCAN_DEADLINE_EXCEEDED

    async def test_a_failed_scan_with_nothing_in_it_still_has_a_report_that_says_why(
        self, client: AsyncClient, settings, monkeypatch
    ) -> None:  # noqa: ANN001
        """A report IS served for a FAILED scan, and it is empty but for the cause."""
        sid, job = await _queued_scan(client)
        fakes = _fake_engines(monkeypatch, {"unfinished 1": "hangs", "unfinished 2": "hangs"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert fakes.in_loop.is_set(), "the deadline fell before the engine loop"
        report = await client.get(f"{BASE}/scans/{sid}/report")
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["scanStatus"] == "failed"
        assert body["scanErrorCode"] == SCAN_DEADLINE_EXCEEDED
        assert body["score"] is None
        assert body["proof"]["engineResults"] == 0

        listed = {
            entry["id"]: entry
            for entry in (await client.get(f"{BASE}/dashboard")).json()["recentScans"]
        }
        assert listed[sid]["status"] == "failed"
        assert listed[sid]["errorCode"] == SCAN_DEADLINE_EXCEEDED

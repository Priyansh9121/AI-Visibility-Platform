"""What a scan records for probable rivals — F2 (2026-10-09).

Whether an answer named a brand can only be told while its text exists, and the text is never
stored. So the scan counts it as it persists its results: `scans.named_domains`, a map from each
cited domain with the client's restricted ending (edu.au for rmit.edu.au) to the number of answers
that named its label. Held here, against the real scan code and engines faked at `ask_all`:

* BOTH persist paths record it: `run_scan`, and the deadline's salvage of the prompts that had
  finished (`scan_executor._persist_finished`);
* `{}` is a measurement (nothing qualified, or the client has no restricted ending), and NULL is
  "not recorded": a scan from before the column, or one that wrote no results, is never guessed;
* measuring can never fail a scan: it runs after every answer has been paid for;
* a log line carries ids and counts only; and the migration adds the column and takes it away.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from structlog.testing import capture_logs

from avp_api import ids
from avp_api.deps import scan_executor
from avp_api.models import Client, Scan, ScanStatus
from avp_api.models.engine_result import Engine, EngineResultStatus, Sentiment
from avp_api.models.prompt import PromptIntent
from avp_api.services import extraction, probable_rivals, scan_runner
from avp_api.services import scan_executor as executor_module
from avp_api.services.engines import CitedSource, EngineAnswer
from avp_api.services.prompts import GeneratedPrompt
from avp_api.services.scan_executor import ScanJob, execute_scan

BASE = "/api/v1"
RMIT = ("RMIT University", "rmit.edu.au")

# The pipeline's own code takes well under half a second against stubs; what must not return
# inside the deadline sleeps for an hour (`test_scan_deadline.py` has the same reasoning).
DEADLINE = timedelta(seconds=2)

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_DIR = REPO_ROOT / "infra" / "db"
ALEMBIC = REPO_ROOT / "apps" / "api" / ".venv" / "bin" / "alembic"
REVISION = "5f08c7c74129"
SCRATCH_PREFIX = "avp_named_domains_"

ENGINES = {"engines": ["claude_search", "chatgpt"]}

# What a faked prompt does: answers with (text, cited domains), errors, or never returns.
Plan = dict[str, Any]


@pytest.fixture
def scan_executor_factory(engines_only_executor):  # noqa: ANN001, ANN201
    """The engine phase only: these tests are about what it writes."""
    return engines_only_executor


def fake_engines(monkeypatch: pytest.MonkeyPatch, plan: Plan) -> SimpleNamespace:
    """Prompt generation and engine answers, decided per prompt. `plan` maps a prompt's text to
    `("answer text", ("cited.domain", ...))`, to `"errors"`, or to `"hangs"`."""
    generated = [
        GeneratedPrompt(text=question, intent=list(PromptIntent)[n % 3])
        for n, question in enumerate(plan)
    ]
    seen = SimpleNamespace(returned=[])

    async def generate(**kwargs: Any):  # noqa: ANN202, ARG001
        return generated, "stub-generator"

    async def ask_all(prompt: str, *, engines: Any, settings: Any):  # noqa: ANN202, ARG001
        what = plan[prompt]
        if what == "hangs":
            await asyncio.sleep(3600)
        seen.returned.append(prompt)
        if what == "errors":
            return [
                EngineAnswer(
                    engine=e, engine_version="stub", prompt_text=prompt,
                    status=EngineResultStatus.ERROR, error_code="PROVIDER_ERROR",
                )
                for e in engines
            ]
        body, cited = what
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
    return seen


async def sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Named Domains Agency", "fullName": "Op",
              "email": "named@test.example", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


async def make_client(client: AsyncClient, name: str, domain: str) -> str:
    resp = await client.post(
        f"{BASE}/clients", json={"url": domain, "name": name, "classify": False}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def run_scan(client: AsyncClient, cid: str) -> str:
    started = await client.post(f"{BASE}/clients/{cid}/scans", json=ENGINES)
    assert started.status_code == 202, started.text
    return str(started.json()["id"])


async def stored(session: AsyncSession, scan_id: str) -> tuple[bool, dict[str, int] | None]:
    """(is the column SQL NULL, what the column holds). Read raw, past the ORM's identity map."""
    row = (
        await session.execute(
            text("SELECT named_domains IS NULL, named_domains FROM scans WHERE id = :id"),
            {"id": scan_id},
        )
    ).one()
    return bool(row[0]), row[1]


async def queued_scan(client: AsyncClient, name: str, domain: str) -> tuple[str, ScanJob]:
    """A queued scan and the job that would run it. Nothing has run."""
    await sign_up(client)
    cid = await make_client(client, name, domain)
    jobs: list[ScanJob] = []

    class _Deferred:
        async def submit(self, job: ScanJob, *, settings: Any) -> None:
            jobs.append(job)

    client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
    queued = await client.post(f"{BASE}/clients/{cid}/scans", json=ENGINES)
    assert queued.status_code == 202, queued.text
    return str(queued.json()["id"]), jobs[0]


# The answers of the founder's example, in three prompts. Two engines answer each.
FOUNDERS_PLAN: Plan = {
    "which universities offer design": (
        "Deakin University and RMIT University are both good choices.",
        ("deakin.edu.au", "qilt.edu.au", "deakin.com"),
    ),
    "where to study teaching": (
        "Consider Deakin University for teaching.",
        ("deakin.edu.au", "uac.edu.au"),
    ),
    "best for engineering": (
        "RMIT University is strong in engineering.",
        ("qilt.edu.au",),
    ),
}


class TestARunRecordsHowManyAnswersNamedEachCitedDomain:
    async def test_the_founders_example(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deakin.edu.au is named by 4 of the 6 answers; qilt and uac are cited and never named;
        deakin.com is not under the client's ending and is not measured at all."""
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, FOUNDERS_PLAN)

        sid = await run_scan(client, cid)

        is_null, named = await stored(session, sid)
        assert is_null is False
        assert named == {"deakin.edu.au": 4, "qilt.edu.au": 0, "uac.edu.au": 0}

    async def test_it_counts_answers_and_not_mentions(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, {
            "one": (
                "Deakin University, Deakin University again, and Deakin University once more.",
                ("deakin.edu.au",),
            ),
        })

        sid = await run_scan(client, cid)

        assert (await stored(session, sid))[1] == {"deakin.edu.au": 2}  # two engines, two answers

    async def test_an_answer_that_failed_counts_for_nothing(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, {
            "fails": "errors",
            "answers": ("Deakin University is a choice.", ("deakin.edu.au",)),
        })

        sid = await run_scan(client, cid)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.PARTIAL  # one prompt's engines failed
        assert (await stored(session, sid))[1] == {"deakin.edu.au": 2}  # the other's two answered

    async def test_a_scan_in_which_nothing_answered_measured_nothing_and_says_so(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, {"fails": "errors"})

        sid = await run_scan(client, cid)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.FAILED
        assert await stored(session, sid) == (False, {})

    async def test_a_client_with_no_restricted_ending_records_an_empty_measurement(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`{}` and not NULL: the scan did measure, and the client has no probable rivals."""
        await sign_up(client)
        cid = await make_client(client, "Help Scout", "helpscout.com")
        fake_engines(monkeypatch, FOUNDERS_PLAN)

        sid = await run_scan(client, cid)

        assert await stored(session, sid) == (False, {})

    async def test_a_school_under_a_state_suffix_has_none_either(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, "Scotch College", "scotch.vic.edu.au")
        fake_engines(monkeypatch, FOUNDERS_PLAN)

        sid = await run_scan(client, cid)

        assert await stored(session, sid) == (False, {})


class TestAScanThatRecordedNothingIsNull:
    async def test_a_queued_scan_has_not_recorded_it(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        sid, _job = await queued_scan(client, *RMIT)
        assert await stored(session, sid) == (True, None)

    async def test_a_scan_the_orm_creates_is_sql_null_even_when_none_is_set_on_purpose(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Python None is SQL NULL for this column, never the JSON `null` that `IS NOT NULL`
        would count as recorded."""
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        agency_id = (await session.get(Client, cid)).agency_id  # type: ignore[union-attr]
        scan = Scan(
            id=ids.new_id(ids.SCAN), client_id=cid, agency_id=agency_id,
            status=ScanStatus.SUCCEEDED, named_domains=None,
        )
        session.add(scan)
        await session.commit()

        assert await stored(session, scan.id) == (True, None)

    async def test_a_scan_that_wrote_no_results_before_its_deadline_stays_null(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {"unfinished 1": "hangs", "unfinished 2": "hangs"})
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.FAILED
        assert (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one() == 0
        assert await stored(session, sid) == (True, None)


class TestTheDeadlineSalvageRecordsItToo:
    """`_persist_finished` writes the prompts that had finished, and so records what they named."""

    async def test_the_prompts_that_finished_are_counted_and_the_ones_that_did_not_are_not(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "finished 1": ("Deakin University is a choice.", ("deakin.edu.au", "qilt.edu.au")),
            "finished 2": ("Deakin University again.", ("deakin.edu.au",)),
            "finished 3": ("RMIT University only.", ("uac.edu.au",)),
            "unfinished": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.PARTIAL
        rows = (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one()
        assert rows == 3 * len(job.engines)
        # The same count `run_scan` would have made over the same three prompts.
        assert await stored(session, sid) == (
            False, {"deakin.edu.au": 2 * len(job.engines), "qilt.edu.au": 0, "uac.edu.au": 0}
        )

    async def test_a_prompt_that_only_failed_is_not_counted(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "broken": "errors",
            "finished": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "unfinished 1": "hangs",
            "unfinished 2": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert await stored(session, sid) == (False, {"deakin.edu.au": len(job.engines)})

    async def _salvage(
        self, client: AsyncClient, session: AsyncSession, *, told: bool
    ) -> tuple[str, bool]:
        """Salvage one finished prompt into a scan that has no rows, with or without the context a
        holder carries once `run_scan` has started. Returns the scan id and the rows written."""
        from avp_api.models import Prompt, PromptSet

        sid, job = await queued_scan(client, *RMIT)
        pset = PromptSet(id=ids.new_id(ids.PROMPT_SET), scan_id=sid)
        session.add(pset)
        await session.flush()
        prompt = Prompt(
            id=ids.new_id(ids.PROMPT), prompt_set_id=pset.id, text="q",
            intent=PromptIntent.AWARENESS, position=1,
        )
        session.add(prompt)
        await session.commit()

        engine_answer = EngineAnswer(
            engine=job.engines[0], engine_version="stub", prompt_text="q",
            text="Deakin University is a choice.",
            citations=[
                CitedSource(url="https://deakin.edu.au/x", domain="deakin.edu.au", position=1)
            ],
        )
        facts = extraction.extract_facts(
            engine_answer, subject_name=RMIT[0], subject_domain=RMIT[1], competitors=[]
        )
        progress = scan_runner.EnginePhaseProgress()
        progress.completed.append((prompt.id, [(job.engines[0], facts, engine_answer)]))
        if told:
            progress.named_domains_context = scan_runner.NamedDomainsContext(
                subject_name=RMIT[0], client_domain=RMIT[1], competitors=()
            )
        scan = await session.get(Scan, sid)
        assert scan is not None

        # The two calls `_finalize_after_deadline` makes, in its order: the count first, before the
        # salvage's session reads anything (should-fix 1, part 7), then the write that takes it.
        named = await executor_module._named_domains_for_salvage(sid, progress)  # noqa: SLF001
        await executor_module._persist_finished(  # noqa: SLF001
            session, scan, progress, named_domains=named
        )

        rows = (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one()
        return sid, rows == 1

    async def test_a_holder_run_scan_has_told_the_client_is_recorded(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        sid, written = await self._salvage(client, session, told=True)
        assert written
        assert await stored(session, sid) == (False, {"deakin.edu.au": 1})

    async def test_a_holder_that_was_not_told_the_client_writes_the_rows_and_leaves_it_null(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """A caller that never said whose scan this was gives nothing to say it from: NULL."""
        sid, written = await self._salvage(client, session, told=False)
        assert written
        assert await stored(session, sid) == (True, None)


class TestMeasuringNeverFailsAScan:
    async def test_an_exception_in_the_helper_leaves_the_scan_whole_and_the_column_null(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, FOUNDERS_PLAN)

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("SENTINEL-answer-text deakin.edu.au")

        monkeypatch.setattr(probable_rivals, "named_domains", boom)

        with capture_logs() as logs:
            sid = await run_scan(client, cid)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.SUCCEEDED
        rows = (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one()
        assert rows == 3 * 2  # every answer that was paid for was kept
        assert await stored(session, sid) == (True, None)

        failed = [e for e in logs if e["event"] == "scan.named_domains_failed"]
        assert len(failed) == 1
        assert failed[0]["scan_id"] == sid
        assert failed[0]["error"] == "RuntimeError"
        # The exception's class and nothing it said: a message could carry an answer.
        assert "SENTINEL" not in repr(logs) and "deakin" not in repr(failed)

    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """The ids of the threads `probable_rivals.named_domains` is called on."""
        seen: list[int] = []
        real = probable_rivals.named_domains

        def spy(*args: Any, **kwargs: Any) -> dict[str, int]:
            seen.append(threading.get_ident())
            return real(*args, **kwargs)

        monkeypatch.setattr(probable_rivals, "named_domains", spy)
        return seen

    async def test_run_scan_counts_off_the_event_loop(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scan is a task in the API's own loop, and the count is seconds of CPU on a big
        university scan, so it runs in a thread and the loop keeps serving."""
        seen = self._spy(monkeypatch)
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, FOUNDERS_PLAN)

        await run_scan(client, cid)

        assert len(seen) == 1 and seen[0] != threading.get_ident()

    async def test_the_deadline_salvage_counts_off_the_event_loop_too(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen = self._spy(monkeypatch)
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "finished": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "unfinished": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)

        await execute_scan(job, settings=settings)

        assert len(seen) == 1 and seen[0] != threading.get_ident()
        assert await stored(session, sid) == (False, {"deakin.edu.au": len(job.engines)})

    async def test_the_line_a_scan_leaves_carries_ids_and_counts_only(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, FOUNDERS_PLAN)

        with capture_logs() as logs:
            sid = await run_scan(client, cid)

        lines = [e for e in logs if e["event"] == "scan.named_domains"]
        assert len(lines) == 1
        line = lines[0]
        assert line["scan_id"] == sid
        assert line["domains"] == 3 and line["named"] == 1
        assert set(line) == {"event", "log_level", "scan_id", "domains", "named"}
        for event in logs:
            assert "deakin" not in repr(event) and "Deakin" not in repr(event), event


# --- the count's limit, its pool, its reuse and its place in the salvage -------------------------
# The security review of 2026-10-10, should-fix 1: the count had no time limit.

CONTEXT = scan_runner.NamedDomainsContext(
    subject_name=RMIT[0], client_domain=RMIT[1], competitors=()
)


def finished(
    *answers: tuple[str, tuple[str, ...]],
) -> list[tuple[Engine, extraction.ExtractedFacts, EngineAnswer]]:
    """`(engine, facts, answer)` tuples as `run_prompt` returns them, one per `(text, cited)`."""
    results = []
    for body, cited in answers:
        engine_answer = EngineAnswer(
            engine=Engine.CLAUDE, engine_version="stub", prompt_text="p", text=body,
            citations=[
                CitedSource(url=f"https://{domain}/page", domain=domain, position=n)
                for n, domain in enumerate(cited, start=1)
            ],
        )
        facts = extraction.extract_facts(
            engine_answer, subject_name=RMIT[0], subject_domain=RMIT[1], competitors=[]
        )
        results.append((Engine.CLAUDE, facts, engine_answer))
    return results


def thread_names(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The thread name of each call of `probable_rivals.named_domains` (it still counts)."""
    seen: list[str] = []
    real = probable_rivals.named_domains

    def spy(*args: Any, **kwargs: Any) -> dict[str, int]:
        seen.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(probable_rivals, "named_domains", spy)
    return seen


def deadline_lands_at_the_count(monkeypatch: pytest.MonkeyPatch, *, after_counting: bool) -> None:
    """Make the deadline land at `run_scan`'s count: right after it, or before it.

    Only `run_scan`'s own call waits for the deadline (forever, so the clock ends it). Any later
    call goes straight through to the real function, so a salvage that counted again would be
    seen in the numbers and not by hanging.
    """
    counted = scan_runner.record_named_domains
    calls: list[int] = []

    async def wrapper(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        first = len(calls) == 1
        if after_counting or not first:
            await counted(*args, **kwargs)
        if first:
            await asyncio.sleep(3600)

    monkeypatch.setattr(scan_runner, "record_named_domains", wrapper)


class TestTheCountHasABudgetAndAPoolOfItsOwn:
    """It read every answer once per candidate domain with no limit on how long that took.

    It runs after every answer has been paid for and before they are written, so a slow count held
    a scan's commit, and with it the results, for as long as it took. It now has a budget; over it
    the column stays NULL, which every report reads as it read before F2, and the scan carries on.
    The thread cannot be stopped, so it runs on a pool of its own: a count left running past its
    budget never takes a worker from the loop's default pool, which the database and Redis
    connections share.
    """

    def test_the_budget_is_fifteen_seconds(self) -> None:
        assert scan_runner.NAMED_DOMAINS_BUDGET_S == 15

    def test_the_pool_has_two_workers(self) -> None:
        # One can be left running past a count's budget; the next count has the other.
        assert scan_runner._NAMED_DOMAINS_POOL._max_workers == 2  # noqa: SLF001

    async def test_a_scan_counts_on_a_thread_of_that_pool(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = thread_names(monkeypatch)
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, FOUNDERS_PLAN)

        await run_scan(client, cid)

        assert len(names) == 1 and names[0].startswith("avp-named-domains")

    async def test_a_count_over_its_budget_is_dropped_and_not_waited_for(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, FOUNDERS_PLAN)
        monkeypatch.setattr(scan_runner, "NAMED_DOMAINS_BUDGET_S", 0.3)
        release = threading.Event()
        ran_on: list[str] = []

        def outlasts_its_budget(*args: Any, **kwargs: Any) -> dict[str, int]:
            ran_on.append(threading.current_thread().name)
            release.wait(8)
            return {"deakin.edu.au": 99}

        monkeypatch.setattr(probable_rivals, "named_domains", outlasts_its_budget)

        try:
            with capture_logs() as logs:
                began = time.perf_counter()
                sid = await run_scan(client, cid)
                took = time.perf_counter() - began
            # The thread cannot be stopped: it is still running, on a worker of its own pool.
            running = {t.name for t in threading.enumerate() if t.is_alive()}
            assert len(ran_on) == 1 and ran_on[0] in running
        finally:
            release.set()

        assert took < 5, f"the scan waited {took:.1f}s for a count with a 0.3s budget"
        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.SUCCEEDED
        rows = (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one()
        assert rows == 3 * 2  # every answer that was paid for was kept
        # Not recorded, which every report reads as it read before F2.
        assert await stored(session, sid) == (True, None)

        failed = [e for e in logs if e["event"] == "scan.named_domains_failed"]
        assert len(failed) == 1
        assert failed[0]["scan_id"] == sid and failed[0]["error"] == "TimeoutError"
        assert not [e for e in logs if e["event"] == "scan.named_domains"]


NOT_COUNTS = [
    pytest.param(["deakin.edu.au"], id="a list"),
    pytest.param("deakin.edu.au", id="text"),
    pytest.param(None, id="nothing at all"),
    pytest.param({"deakin.edu.au": "4"}, id="a count that is text"),
    pytest.param({"deakin.edu.au": 4.0}, id="a count that is a float"),
    pytest.param({"deakin.edu.au": True}, id="a count that is a boolean"),
    pytest.param({7: 4}, id="a domain that is not text"),
]


class TestAResultThatIsNotACountIsNotRecorded:
    """Nothing after the count can raise out of `run_scan` or out of the salvage.

    The result is checked, inside the guarded block, to be a map of text to whole numbers before
    the column is set or a line is made from it. A value the database cannot store would
    otherwise fail the commit that writes every paid answer.
    """

    @pytest.mark.parametrize("returned", NOT_COUNTS)
    async def test_run_scan_finishes_the_scan_and_leaves_the_column_null(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
        returned: Any,
    ) -> None:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        fake_engines(monkeypatch, FOUNDERS_PLAN)
        monkeypatch.setattr(probable_rivals, "named_domains", lambda *_a, **_k: returned)

        with capture_logs() as logs:
            sid = await run_scan(client, cid)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.SUCCEEDED
        rows = (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one()
        assert rows == 3 * 2
        assert await stored(session, sid) == (True, None)

        failed = [e for e in logs if e["event"] == "scan.named_domains_failed"]
        assert len(failed) == 1
        # The exception's class and the scan, nothing it said and nothing that was returned.
        assert set(failed[0]) == {"event", "log_level", "scan_id", "error"}
        assert failed[0]["scan_id"] == sid and failed[0]["error"] == "TypeError"
        assert not [e for e in logs if e["event"] == "scan.named_domains"]

    @pytest.mark.parametrize("returned", [NOT_COUNTS[0], NOT_COUNTS[3]])
    async def test_the_salvage_ends_the_scan_partial_and_leaves_the_column_null(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch, returned: Any,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "finished": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "unfinished": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        monkeypatch.setattr(probable_rivals, "named_domains", lambda *_a, **_k: returned)

        with capture_logs() as logs:
            await execute_scan(job, settings=settings)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.PARTIAL, scan.error_code
        rows = (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one()
        assert rows == len(job.engines)  # the prompt that finished was kept
        assert await stored(session, sid) == (True, None)
        assert not [e for e in logs if e["event"] == "scan.deadline.finalize_failed"]
        failed = [e for e in logs if e["event"] == "scan.named_domains_failed"]
        assert len(failed) == 1 and failed[0]["error"] == "TypeError"


class TestWhatRunScanLeavesOnTheHolder:
    """`EnginePhaseProgress` records that the count was attempted, and what it came to.

    A dict, or None when the count failed or ran out of budget. `run_scan` sets both right after
    its count, so the deadline's salvage can tell "never tried" from "tried, and nothing came of
    it", and write what was counted instead of counting again.
    """

    async def test_a_holder_that_has_counted_nothing_says_so(self) -> None:
        progress = scan_runner.EnginePhaseProgress()
        assert progress.named_domains_attempted is False
        assert progress.named_domains_result is None

    async def test_a_count_that_finished_is_recorded_with_its_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        thread_names(monkeypatch)
        progress = scan_runner.EnginePhaseProgress()
        scan = SimpleNamespace(id="scan_1", named_domains=None)

        await scan_runner.record_named_domains(
            scan,  # type: ignore[arg-type]
            finished(("Deakin University is large.", ("deakin.edu.au", "qilt.edu.au"))),
            context=CONTEXT, progress=progress,
        )

        assert progress.named_domains_attempted is True
        assert progress.named_domains_result == {"deakin.edu.au": 1, "qilt.edu.au": 0}
        assert scan.named_domains == progress.named_domains_result

    async def test_a_count_that_failed_is_recorded_as_attempted_with_no_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> dict[str, int]:
            raise RuntimeError("SENTINEL")

        monkeypatch.setattr(probable_rivals, "named_domains", boom)
        progress = scan_runner.EnginePhaseProgress()
        scan = SimpleNamespace(id="scan_1", named_domains=None)

        await scan_runner.record_named_domains(
            scan,  # type: ignore[arg-type]
            finished(("Deakin University.", ("deakin.edu.au",))),
            context=CONTEXT, progress=progress,
        )

        assert progress.named_domains_attempted is True
        assert progress.named_domains_result is None
        assert scan.named_domains is None

    async def test_a_count_that_ran_out_of_budget_is_recorded_the_same_way(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = threading.Event()
        monkeypatch.setattr(scan_runner, "NAMED_DOMAINS_BUDGET_S", 0.2)
        monkeypatch.setattr(
            probable_rivals, "named_domains", lambda *_a, **_k: (release.wait(8), {})[1]
        )
        progress = scan_runner.EnginePhaseProgress()
        scan = SimpleNamespace(id="scan_1", named_domains=None)

        try:
            await scan_runner.record_named_domains(
                scan,  # type: ignore[arg-type]
                finished(("Deakin University.", ("deakin.edu.au",))),
                context=CONTEXT, progress=progress,
            )
        finally:
            release.set()

        assert progress.named_domains_attempted is True
        assert progress.named_domains_result is None
        assert scan.named_domains is None

    async def test_with_no_context_nothing_is_attempted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = thread_names(monkeypatch)
        progress = scan_runner.EnginePhaseProgress()
        scan = SimpleNamespace(id="scan_1", named_domains=None)

        await scan_runner.record_named_domains(
            scan,  # type: ignore[arg-type]
            finished(("Deakin University.", ("deakin.edu.au",))),
            context=None, progress=progress,
        )

        assert names == []
        assert progress.named_domains_attempted is False
        assert progress.named_domains_result is None
        assert scan.named_domains is None


class TestTheSalvageTakesWhatRunScanCounted:
    """The deadline's salvage writes `run_scan`'s own count and counts only when it never made one.

    `run_scan` counts after the paid answers are in and before it commits them. A deadline that
    lands between the two used to make the salvage count the same answers again, and one that ran
    out of budget the first time got a second, equally long, try.
    """

    async def test_it_writes_the_count_run_scan_made_and_does_not_make_it_again(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "one": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "two": ("Deakin University again.", ("deakin.edu.au",)),
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        # What a second count would say is not what the first did, so a recount is visible.
        says = iter([{"deakin.edu.au": 7}, {"deakin.edu.au": 99}])
        calls: list[str] = []

        def counts(*_args: Any, **_kwargs: Any) -> dict[str, int]:
            calls.append(threading.current_thread().name)
            return next(says)

        monkeypatch.setattr(probable_rivals, "named_domains", counts)
        deadline_lands_at_the_count(monkeypatch, after_counting=True)  # before the commit

        await execute_scan(job, settings=settings)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.PARTIAL, scan.error_code
        rows = (await session.execute(
            text("SELECT count(*) FROM engine_results WHERE scan_id = :id"), {"id": sid}
        )).scalar_one()
        assert rows == 2 * len(job.engines)  # the salvage wrote the results run_scan had not
        assert len(calls) == 1  # once in total
        assert await stored(session, sid) == (False, {"deakin.edu.au": 7})

    @pytest.mark.parametrize("how", ["raises", "outlasts its budget"])
    async def test_it_does_not_try_again_a_count_that_came_to_nothing(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch, how: str,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "one": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "two": ("Deakin University again.", ("deakin.edu.au",)),
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        monkeypatch.setattr(scan_runner, "NAMED_DOMAINS_BUDGET_S", 0.3)
        release = threading.Event()
        calls: list[str] = []

        def comes_to_nothing(*_args: Any, **_kwargs: Any) -> dict[str, int]:
            calls.append(threading.current_thread().name)
            if how == "raises":
                raise RuntimeError("SENTINEL-answer-text")
            release.wait(20)
            return {"deakin.edu.au": 99}

        monkeypatch.setattr(probable_rivals, "named_domains", comes_to_nothing)
        deadline_lands_at_the_count(monkeypatch, after_counting=True)

        try:
            with capture_logs() as logs:
                await execute_scan(job, settings=settings)
        finally:
            release.set()

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan is not None and scan.status is ScanStatus.PARTIAL, scan.error_code
        assert len(calls) == 1  # the salvage did not give it a second try
        assert await stored(session, sid) == (True, None)
        failed = [e for e in logs if e["event"] == "scan.named_domains_failed"]
        assert len(failed) == 1
        assert failed[0]["error"] == ("RuntimeError" if how == "raises" else "TimeoutError")
        assert "SENTINEL" not in repr(logs)

    async def test_it_counts_once_when_run_scan_never_got_to_its_count(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "one": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "two": ("Deakin University again.", ("deakin.edu.au", "qilt.edu.au")),
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        names = thread_names(monkeypatch)
        deadline_lands_at_the_count(monkeypatch, after_counting=False)

        await execute_scan(job, settings=settings)

        assert len(names) == 1 and names[0].startswith("avp-named-domains")
        assert await stored(session, sid) == (
            False, {"deakin.edu.au": 2 * len(job.engines), "qilt.edu.au": 0}
        )

    async def test_it_counts_again_a_count_the_deadline_interrupted_on_the_other_worker(
        self, client: AsyncClient, session: AsyncSession, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A count the deadline cancelled did not finish, so it is not an attempt that came to
        anything. Its thread cannot be stopped and is still running; the salvage's count has the
        pool's other worker."""
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "one": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "two": ("Deakin University again.", ("deakin.edu.au", "qilt.edu.au")),
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        real = probable_rivals.named_domains
        release = threading.Event()
        names: list[str] = []

        def first_count_is_still_running(*args: Any, **kwargs: Any) -> dict[str, int]:
            names.append(threading.current_thread().name)
            if len(names) == 1:
                release.wait(20)
                return {"deakin.edu.au": 1000}  # what nobody is waiting for any more
            return real(*args, **kwargs)

        monkeypatch.setattr(probable_rivals, "named_domains", first_count_is_still_running)

        try:
            await execute_scan(job, settings=settings)
        finally:
            release.set()

        assert len(names) == 2 and len(set(names)) == 2
        assert all(name.startswith("avp-named-domains") for name in names)
        assert await stored(session, sid) == (
            False, {"deakin.edu.au": 2 * len(job.engines), "qilt.edu.au": 0}
        )

    async def test_a_recorded_result_is_returned_untouched_and_nothing_is_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = thread_names(monkeypatch)
        progress = scan_runner.EnginePhaseProgress(named_domains_context=CONTEXT)
        progress.completed.append(
            ("prompt_1", finished(("Deakin University.", ("deakin.edu.au",))))
        )
        progress.named_domains_attempted = True
        progress.named_domains_result = {"deakin.edu.au": 42}

        found = await executor_module._named_domains_for_salvage("scan_1", progress)  # noqa: SLF001

        assert found == {"deakin.edu.au": 42} and names == []

    async def test_a_holder_that_never_counted_is_counted_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = thread_names(monkeypatch)
        progress = scan_runner.EnginePhaseProgress(named_domains_context=CONTEXT)
        progress.completed.append(
            ("prompt_1", finished(("Deakin University.", ("deakin.edu.au",))))
        )

        found = await executor_module._named_domains_for_salvage("scan_1", progress)  # noqa: SLF001

        assert found == {"deakin.edu.au": 1} and len(names) == 1

    async def test_nothing_is_counted_when_the_results_are_already_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = thread_names(monkeypatch)
        progress = scan_runner.EnginePhaseProgress(named_domains_context=CONTEXT)
        progress.completed.append(
            ("prompt_1", finished(("Deakin University.", ("deakin.edu.au",))))
        )
        progress.persisted = True

        found = await executor_module._named_domains_for_salvage("scan_1", progress)  # noqa: SLF001

        assert found is None and names == []

    async def test_nothing_is_counted_when_no_prompt_finished(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = thread_names(monkeypatch)
        progress = scan_runner.EnginePhaseProgress(named_domains_context=CONTEXT)

        found = await executor_module._named_domains_for_salvage("scan_1", progress)  # noqa: SLF001

        assert found is None and names == []

    async def test_nothing_is_counted_for_a_holder_that_was_never_told_whose_scan_it_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = thread_names(monkeypatch)
        progress = scan_runner.EnginePhaseProgress()
        progress.completed.append(
            ("prompt_1", finished(("Deakin University.", ("deakin.edu.au",))))
        )

        found = await executor_module._named_domains_for_salvage("scan_1", progress)  # noqa: SLF001

        assert found is None and names == []


class TestTheSalvageCountsOutsideItsTransaction:
    """No connection sits idle in an open transaction while the count runs.

    The salvage's session reads `scans`, `engine_results` and `prompts` before it writes, which
    opens a transaction and holds share locks on those tables. A count of seconds inside it would
    keep them held, and a deploy's `ALTER TABLE scans` with a 5 s `lock_timeout` would fail
    against it. The count (or `run_scan`'s own) is therefore taken BEFORE the session starts its
    reads, and handed in to be written with the results.
    """

    async def test_no_connection_is_idle_in_a_transaction_while_the_salvage_counts(
        self, client: AsyncClient, session: AsyncSession, engine: Any, settings: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid, job = await queued_scan(client, *RMIT)
        fake_engines(monkeypatch, {
            "finished": ("Deakin University is a choice.", ("deakin.edu.au",)),
            "unfinished": "hangs",
        })
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", DEADLINE)
        loop = asyncio.get_running_loop()
        real = probable_rivals.named_domains
        idle: list[int] = []

        async def idle_in_transaction() -> int:
            async with engine.connect() as conn:
                return int((await conn.execute(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                ))).scalar_one())

        def probes_while_counting(*args: Any, **kwargs: Any) -> dict[str, int]:
            # On the count's thread; the loop is free, so it answers while this waits.
            idle.append(asyncio.run_coroutine_threadsafe(idle_in_transaction(), loop).result(20))
            return real(*args, **kwargs)

        monkeypatch.setattr(probable_rivals, "named_domains", probes_while_counting)

        await execute_scan(job, settings=settings)

        assert idle == [0]
        assert await stored(session, sid) == (False, {"deakin.edu.au": len(job.engines)})


class TestARivalsShortFormIsNotTheStrangersName:
    """The scan passes the client's and the rivals' short forms to the matcher, as it does for
    brand detection: "UNSW" in an answer names the rival that owns it, not a stranger's domain."""

    async def _scan_with_rival(
        self, client: AsyncClient, stub_discovery: Any, monkeypatch: pytest.MonkeyPatch,
        *, with_rival: bool,
    ) -> str:
        await sign_up(client)
        cid = await make_client(client, *RMIT)
        if with_rival:
            stub_discovery(serp_domains=["unsw.com"], cocit_brands=[("UNSW", "unsw.com")])
            detected = await client.post(f"{BASE}/clients/{cid}/competitors/detect")
            assert detected.status_code == 201, detected.text
            assert [c["name"] for c in detected.json()["competitors"]] == ["UNSW"]
        fake_engines(monkeypatch, {
            "who is large": ("UNSW is large and so is RMIT University.", ("unsw.edu.au",)),
        })
        return await run_scan(client, cid)

    async def test_without_the_rival_the_capitals_name_the_candidate(
        self, client: AsyncClient, session: AsyncSession, stub_discovery: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid = await self._scan_with_rival(client, stub_discovery, monkeypatch, with_rival=False)
        assert (await stored(session, sid))[1] == {"unsw.edu.au": 2}

    async def test_with_the_rival_the_form_is_the_rivals(
        self, client: AsyncClient, session: AsyncSession, stub_discovery: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sid = await self._scan_with_rival(client, stub_discovery, monkeypatch, with_rival=True)
        assert (await stored(session, sid))[1] == {"unsw.edu.au": 0}


# --- the migration ------------------------------------------------------------------------------


def alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ALEMBIC), *args], cwd=DB_DIR, env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True, text=True, timeout=180,
    )


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_the_revision_adds_the_column_to_scans_that_predate_it_and_takes_it_away(
    scratch_databases,  # noqa: ANN001
) -> None:
    """The real revision, up and down, over a database at the revision before it that already
    holds a scan. The scan comes out of the upgrade NOT RECORDED (NULL): the fact cannot be
    recomputed, so it is never guessed. The column is nullable with no default, so an older
    instance that is still serving during the deploy inserts a scan without it. The downgrade
    drops the column and keeps the scan."""
    agency, client, scan, later = (
        ids.new_id(ids.AGENCY), ids.new_id(ids.CLIENT), ids.new_id(ids.SCAN), ids.new_id(ids.SCAN),
    )
    insert_scan = text(
        "INSERT INTO scans (id, client_id, agency_id, status, trigger, formula_version, "
        "prompt_count, engine_result_count) VALUES "
        "(:id, :client, :agency, 'succeeded', 'manual', 'v1', 0, 0)"
    )
    async with scratch_databases.database(SCRATCH_PREFIX) as url:
        before = alembic(["upgrade", "d5b2f84a6c13"], url)
        assert before.returncode == 0, f"{before.stdout}\n{before.stderr}"
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO agencies (id, name, slug, seat_limit) "
                         "VALUES (:id, 'Migration Agency', 'migration-agency', 3)"),
                    {"id": agency},
                )
                await conn.execute(
                    text("INSERT INTO clients (id, agency_id, name, domain, kind, "
                         "classification_status) VALUES (:id, :agency, 'C', 'example.edu.au', "
                         "'prospect', 'pending')"),
                    {"id": client, "agency": agency},
                )
                await conn.execute(insert_scan, {"id": scan, "client": client, "agency": agency})
                columns = {
                    r[0] for r in await conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'scans'"
                    ))
                }
            assert "named_domains" not in columns

            up = alembic(["upgrade", REVISION], url)
            assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

            async with engine.begin() as conn:
                # What the instance still serving during the deploy writes: no named_domains.
                await conn.execute(insert_scan, {"id": later, "client": client, "agency": agency})
                kind = (await conn.execute(text(
                    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_name = 'scans' AND column_name = 'named_domains'"
                ))).one()
                values = dict((await conn.execute(text(
                    "SELECT id, named_domains IS NULL FROM scans"
                ))).all())
                # A recorded measurement round-trips, an empty one included.
                await conn.execute(
                    text("UPDATE scans SET named_domains = CAST(:v AS jsonb) WHERE id = :id"),
                    {"v": '{"deakin.edu.au": 3, "qilt.edu.au": 0}', "id": later},
                )
                read = (await conn.execute(
                    text("SELECT named_domains FROM scans WHERE id = :id"), {"id": later}
                )).scalar_one()
            assert tuple(kind) == ("jsonb", "YES", None)
            assert values == {scan: True, later: True}
            assert read == {"deakin.edu.au": 3, "qilt.edu.au": 0}

            down = alembic(["downgrade", "d5b2f84a6c13"], url)
            assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
            async with engine.begin() as conn:
                columns = {
                    r[0] for r in await conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'scans'"
                    ))
                }
                kept = (await conn.execute(text("SELECT count(*) FROM scans"))).scalar_one()
            assert "named_domains" not in columns
            assert kept == 2

            again = alembic(["upgrade", REVISION], url)
            assert again.returncode == 0, f"{again.stdout}\n{again.stderr}"
        finally:
            await engine.dispose()

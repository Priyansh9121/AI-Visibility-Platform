"""Running a scan out of band — Epic 9.5.

WHY THIS EXISTS
---------------
`POST /clients/{clientId}/scans` used to run the whole pipeline inline and
commit once, at the end. Epic 9.2 measured that at ~303s for this endpoint, and
Epic 9.3 found the consequence: the scan row lives inside an uncommitted
transaction for its entire duration, so no other request can see it. The
dashboard could not show a scan in flight because, as far as Postgres was
concerned, there was not one yet.

The fix is not really "run it in the background" — it is **committing at the
boundaries**, so the row exists and moves through visible states. The executor
below is the smallest thing that lets the endpoint return before the work is
done.

WHY A NARROW PROTOCOL RATHER THAN `avp_workers.Orchestrator`
------------------------------------------------------------
`apps/workers` already defines an `Orchestrator` protocol
(`enqueue`/`result`/`status`) that Epic 1.2 built as the seam for exactly this
moment. It is deliberately not reused, for three reasons:

1. **`apps/api` cannot import it.** There is no dependency on `avp-workers`;
   the path dependency runs the other way, and `orchestrator.py` states that
   the API "must not import worker code". Reusing it would mean inverting a
   dependency direction that was chosen on purpose.
2. **Its shape is job-id-centric, and this mechanism has no job ids.**
   `enqueue` returns an opaque id that `result`/`status` are then keyed on.
   A background task in-process has no such handle.
3. **We do not want one.** The `Scan` row *is* the job record: `scan.status` is
   the status and `GET /scans/{scanId}` is the result. Introducing a parallel
   job id would create a second source of truth for "is this scan running",
   and two sources of truth for one fact is how they drift.

A future Celery slice implements `ScanExecutor.submit` by publishing with
`send_task` — one method, no job-id plumbing. That is cheaper than implementing
`Orchestrator`, not more expensive. `Orchestrator` remains unused, and for this
purpose is superseded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Literal, Protocol, get_args

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import get_sessionmaker
from ..models import Agency, Client, EngineResult, Prompt, Scan, ScanStatus
from ..models.engine_result import Engine
from . import alerts as alerts_service
from . import audit_runner, fix_runner, memory_stats, scan_runner, scoring_runner, usage
from . import competitors as detection
from .browser_slot import SCAN_AUDIT_WAIT_S
from .prompts import PromptsUnavailable
from .scan_allowance import (
    CODE_PROMPTS_UNAVAILABLE_COUNTED,
    FREE_PROMPT_FAILURES_PER_DAY,
    IN_FLIGHT_WINDOW,
    PROMPT_FAILURE_WINDOW,
)

logger = structlog.get_logger(__name__)

# Statuses a scan can no longer move out of. Nothing may re-mark these.
TERMINAL: frozenset[ScanStatus] = frozenset(
    {ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED, ScanStatus.CANCELLED}
)

# --- the lease ---------------------------------------------------------------
# `STALE_AFTER = 900s` off `started_at` used to stand here, and its failure was
# the audit's diagnosis: duration and identity treated as one signal. Nothing
# told "still genuinely working" from "gone" except elapsed time, and the
# arithmetic did not fit — a maximal scan's engine phase alone can legitimately
# run 976s, so the constant reaped scans that were still running and still
# billing, and the next POST started a second full run beside the first.
#
# A lease asks a different question. The executor says "still here" on a clock;
# the reaper acts on silence. Duration stops being evidence of anything.
#
# WHY THESE TWO NUMBERS, AND WHY NOT A SUM OF CALL CEILINGS
# ---------------------------------------------------------
# The obvious derivation — LEASE = the longest gap between two renewals, where
# renewals sit next to the paid calls — makes LEASE a sum of ceilings living in
# five other modules, so a phase added later silently outgrows it with nothing
# but a hand-maintained test to notice. A clock does not have that property:
# the interval is the interval whatever the chain grows into, which is the
# whole reason this shape was chosen over renewing per phase or per prompt.
#
# LEASE is SIX intervals, not the conventional three. Five consecutive missed
# renewals are tolerated, and the fifth is not slack for its own sake:
# `database_pool_size` is 10 with 5 overflow (config.py) and nothing bounds how
# many executors run at once, so a saturated pool can park a renewal on
# SQLAlchemy's unconfigured 30-second `pool_timeout` — one whole interval, on a
# machine that is entirely healthy. At three intervals two such waits would
# expire a live executor's lease. At six they cannot.
#
# What it buys: a dead executor's scan is reapable within 180s rather than
# 900s. What it costs: one UPDATE per running scan every 30s.
HEARTBEAT_INTERVAL = timedelta(seconds=30)
LEASE = timedelta(seconds=180)

# --- the deadline, which is the one thing a lease cannot see ------------------
# A lease detects an executor that has STOPPED SPEAKING. It cannot detect one
# that is alive, speaking, and getting nowhere: the heartbeat is its own task,
# so an await inside the chain that never returns leaves it renewing happily
# forever. `STALE_AFTER` did catch that, by accident, and retiring it gave the
# case up. There was at least one such await — `page.evaluate` in
# `technical_audit.py` takes no timeout and is outside `set_default_timeout`'s
# reach (launch checklist 3.20). M1 (2026-10-02) caps the browser run that holds
# it (`technical_audit.audit_run_cap_s`, and `crawl.crawl_run_cap_s`), so that
# hang now ends as a failed audit phase, minutes in and not at this deadline. The
# deadline stays for the next await nobody has bounded.
#
# THE BACKSTOP IS HERE AND NOT IN THE REAPER, deliberately. The reaper judges
# other processes, and giving it a duration rule would put back exactly the
# inference the lease exists to remove: "long, therefore dead". This is an
# executor's ceiling on its own work — the same shape as `CallBound`, one
# level up — and unlike a reaper it actually STOPS the work rather than
# relabelling the row while the spending carries on.
#
# 2,400s clears a maximal scan computed from the ceilings themselves: two
# co-citation waves and two SerpApi waves for detection, one prompt
# generation, ceil(MAX_PROMPTS / PROMPT_CONCURRENCY) engine slots each costing
# one engine ceiling plus one sentiment ceiling per engine, an audit page, and
# fix generation — about 1,500s if every single call times out, which no real
# scan does. `test_scan_executor.py` recomputes that sum from the constants, so
# raising a ceiling anywhere fails a test here instead of quietly eating the
# margin — and LOWERING one does too, which is how this moved: it was 3,600s
# against a 3,042s worst case at PROMPT_CONCURRENCY=4, and Epic 18.1's raise to
# 12 halved the slot count, so the CI gate failed the "not more than twice the
# worst case" half of that test. A backstop twice as loose as the thing it
# backs is not a backstop. Same 1.6x margin as before, on the new number.
#
# M1 (2026-10-02) put a cap on the audit's browser run, and the sum now counts the
# audit at that cap (its side files, then `technical_audit.audit_run_cap_s`) instead
# of one page's timeouts: about 1,900s, so 2,400s clears it by about 1.26x. The
# number is unchanged. What the cap buys is that a hung audit step costs minutes of
# that sum and still leaves the scan its alerts and its fixes.
#
# M1 round 2 bounded the audit's WAIT for the one browser (`browser_slot`), and the
# sum counts that too, at its bound, `SCAN_AUDIT_WAIT_S`, before the run itself. The
# bound was 300s, which took the sum to about 2,200s and left a margin of about ×1.09.
#
# Launch checklist 3.41 (round 3) bounded the audit's reads BEFORE the browser, which the
# sum had counted as one request's timeout (10s): robots.txt, the sitemap and the
# homepage's redirects, a request each under its own deadline, are now all under
# `crawl.PRE_BROWSER_BUDGET_S` (60s) together, and the sum counts that budget whole
# (2,244.5s at a 300s wait, a margin of ×1.07). Launch checklist 3.35 (the founder's
# decision) then made the wait 120s. F5 part B (2026-10-09) gave the prompt generator a
# second retry, so its ceiling went from 122s to 182s (three attempts of 60s plus backoff);
# the sum assumes, as before, that every call in a scan times out at its ceiling. Recomputed
# from the constants, the sum now stands at:
#
#     worst case 2,124.5s of 2,400s: slack 275.5s, margin ×1.13
#
# A full wait AND a full run in one scan is still the pathological case. The test below
# adds each term and checks that the line above says what it computes, so a bound that
# moves without this number fails there and not in production.
MAX_SCAN_DURATION = timedelta(seconds=2_400)

# WHAT THE DEADLINE KEEPS — decision 7, the founder, 2026-10-01
# --------------------------------------------------------------
# "A scan that hits its deadline finalises as PARTIAL with the answers it has,
# and counts." Until then the deadline ended every scan FAILED, however much of
# it was done and paid for, and the engine loop made that expensive: it holds
# its answers in memory until the whole loop ends (`scan_runner.run_scan`).
#
# So the deadline is now a CAUSE and not a verdict. `_finalize_after_deadline`
# keeps the answers the loop had finished (`scan_runner.EnginePhaseProgress`),
# scores them, and stamps PARTIAL when at least half of the PLANNED prompts have
# an answered engine result (`deadline_partial_counts`, the founder's 50% rule),
# FAILED when fewer do. Only the deadline does this: a restart (`EXECUTOR_LOST`,
# `EXECUTOR_NEVER_STARTED`) and an error of ours (`EXECUTION_FAILED`) end FAILED
# and never count: the allowance counts a FAILED scan only when its code is
# PROMPTS_UNAVAILABLE_COUNTED (`scan_allowance.count_scans`), which none of these is.
#
# The scoring is the one piece of work the finalise adds, and it is DB-only
# (`scoring_runner.score_scan` reads persisted rows and writes one Score; no
# engine, no vendor). A minute is generous for that and sits well inside the
# 180s lease the finalise renews before it starts.
DEADLINE_SCORING_BUDGET = timedelta(seconds=60)


class ScanDeadlineError(Exception):
    """The chain ran past `MAX_SCAN_DURATION` while its lease was still live."""

# Distinct from ALL_ENGINE_CALLS_FAILED (the pipeline ran and every engine
# failed) and from EXECUTION_FAILED (the pipeline raised). This one means the
# process that was running the scan disappeared without saying anything.
EXECUTOR_LOST = "EXECUTOR_LOST"
# A scan was submitted, sat QUEUED for the whole in-flight window, and no
# executor ever claimed it (typically the process restarted between the
# response and the background task). Nothing ran, so nothing was spent.
EXECUTOR_NEVER_STARTED = "EXECUTOR_NEVER_STARTED"
EXECUTION_FAILED = "EXECUTION_FAILED"
# The prompt phase could not produce a set worth scanning (F5): the generator gave too few usable
# questions, or failed, or timed out, and no template-only set is ever scanned. FAILED before any
# engine call, so no engine is asked. It is not free to us: the generator's call, and on a
# client's first scan the competitor detection that ran before it, were paid for. So it is free
# to the agency three times in a rolling 24 hours and COUNTS after that, recorded as
# PROMPTS_UNAVAILABLE_COUNTED (`_prompts_failure_code`; `scan_allowance` counts that code).
PROMPTS_UNAVAILABLE = "PROMPTS_UNAVAILABLE"
# The prompt phase failed because the generator call itself failed (an API error or a timeout):
# our outage, never charged and not counted toward the daily three. `error_detail` marks the row.
GENERATION_FAILED = "generation_failed"
OUTAGE_DETAIL = "PromptsUnavailable:generation_failed"
# The chain was alive and renewing, and still did not finish in time. Distinct
# from EXECUTOR_LOST (it went silent) and EXECUTION_FAILED (it raised). A CAUSE,
# not a verdict — decision 7: it sits on a PARTIAL scan with answers for at least
# half its planned prompts (which counts) and on a FAILED one with fewer (which
# does not).
SCAN_DEADLINE_EXCEEDED = "SCAN_DEADLINE_EXCEEDED"


async def renew_lease(scan_id: str, *, settings: Settings) -> bool:
    """Push this scan's lease out. **False means the lease is no longer ours.**

    On its OWN short-lived session, never the chain's. Three reasons, and the
    first two are not style:

    * The chain's session is mid-transaction for minutes at a time. A renewal
      issued on it would not be visible to the reaper or to anything else until
      that transaction committed, which is exactly when the renewal stops
      mattering.
    * Committing on the chain's session would commit whatever it has flushed
      and not yet committed, which is not this function's business.
    * Inside `run_scan` the chain's session is shared by `PROMPT_CONCURRENCY`
      gathered coroutines, and `AsyncSession` is not safe for concurrent use.

    Zero matched rows is a definite answer from the database: this row is not
    RUNNING any more, so somebody else owns its fate — the reaper failed it, or
    it has already been finalised. An EXCEPTION is not an answer, and the
    caller treats the two differently.
    """
    factory = get_sessionmaker(settings)
    async with factory() as session:
        renewed = (
            await session.execute(
                update(Scan)
                .where(Scan.id == scan_id, Scan.status == ScanStatus.RUNNING)
                .values(lease_expires_at=datetime.now(UTC) + LEASE)
                .returning(Scan.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        await session.commit()
    return renewed is not None


async def _until_deadline(chain: Awaitable[None]) -> None:
    """`chain`, with `MAX_SCAN_DURATION` as a hard ceiling on the whole thing.

    Wrapped INSIDE the task rather than around the await of it, so the deadline
    cancels the chain itself and there is no ambiguity about whose cancellation
    it was. An external cancellation — the heartbeat taking the lease away —
    passes through untouched, because `asyncio.timeout` only converts the one
    it caused.

    **Only the CLOCK's timeout is a deadline.** The `except` below catches every
    `TimeoutError` that leaves the block, and the chain can raise one of its own
    (a bug, a connect that timed out). Called a deadline, it would end the scan
    PARTIAL and count it (decision 7), which makes an error of ours cost the
    customer an allowance slot. So it asks the timeout whether the clock is what
    fired, and re-raises anything else untouched: that takes `execute_scan`'s
    plain path, FAILED / EXECUTION_FAILED, which never counts.
    """
    try:
        async with asyncio.timeout(MAX_SCAN_DURATION.total_seconds()) as deadline:
            await chain
    except TimeoutError:
        if not deadline.expired():
            raise
        raise ScanDeadlineError from None


async def _heartbeat(scan_id: str, *, settings: Settings, owner: asyncio.Task, state: dict) -> None:
    """Renew on a clock until the lease is lost, then cancel the work.

    **A renewal that matches zero rows stops the executor.** Not a log line and
    onwards: this is the half a reaper alone never had — today it stamps the
    row and the work carries on, billing against a scan somebody else has
    already written off. Cancelling `owner` is what makes the stamp mean
    something.

    A renewal that RAISES is a different thing and is not treated as loss. The
    database being briefly unreachable says nothing about who owns the row, and
    `LEASE` is six intervals precisely so a few of these cost nothing. If the
    outage outlasts the lease the row is reaped, and the first renewal that
    completes afterwards returns False through the branch above.
    """
    interval = HEARTBEAT_INTERVAL.total_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            held = await renew_lease(scan_id, settings=settings)
        except Exception:  # noqa: BLE001 - not an answer about ownership
            logger.warning("scan.lease.renewal_failed", scan_id=scan_id, exc_info=True)
            continue
        if not held:
            state["lost"] = True
            owner.cancel()
            return


async def run_under_lease(
    chain: Awaitable[None], *, scan_id: str, settings: Settings
) -> bool:
    """Run `chain` while holding the scan's lease. False means the lease went.

    The chain runs as its OWN task so that losing the lease cancels the work
    and nothing else. Cancelling `asyncio.current_task()` instead would reach
    whatever is above us — under `InlineScanExecutor` that is the request
    handler, which has done nothing wrong.

    Cancellation is what stops the billing, and it reaches the chain wherever
    it is waiting: `_attempt` catches `Exception`, and `CancelledError` is a
    `BaseException`, so no phase can swallow it on the way out.

    A cancellation this function did not cause is re-raised rather than
    reported as a lost lease — `state["lost"]` is how the two are told apart.
    """
    state: dict = {}
    work = asyncio.create_task(_until_deadline(chain))
    beat = asyncio.create_task(
        _heartbeat(scan_id, settings=settings, owner=work, state=state)
    )
    try:
        await work
    except asyncio.CancelledError:
        if not state.get("lost"):
            raise
        return False
    finally:
        beat.cancel()
    return True


@dataclass(frozen=True, slots=True)
class ScanJob:
    """Everything the executor needs, by value.

    Ids rather than ORM instances: the job outlives the request's session, and a
    detached instance bound to a closed session is a bug waiting for a load.
    """

    scan_id: str
    client_id: str
    engines: tuple[Engine, ...]
    prompt_limit: int | None = None


class ScanExecutor(Protocol):
    """How a queued scan gets run. One method, because that is all there is."""

    async def submit(self, job: ScanJob, *, settings: Settings) -> None: ...


# The phases `_run_chain` logs the process's memory after (`scan.phase_memory`), in
# the order it runs them. A fixed set, so a line can be found and compared by name:
# "engines" is prompt generation and the engine loop, "finalized" the closing stamp.
MemoryPhase = Literal["competitors", "engines", "audit", "scoring", "alerts", "fixes", "finalized"]
MEMORY_PHASES: tuple[str, ...] = get_args(MemoryPhase)


def _log_phase_memory(scan_id: str, phase: MemoryPhase) -> None:
    """One `scan.phase_memory` line: the scan, the phase, and the numbers in `memory_stats`.

    **This can never affect the scan.** It takes the scan's id as a plain string, so
    it touches no ORM attribute that a rolled-back phase might have expired; it
    catches everything, including a snapshot that raises; and it logs nothing about
    a failure, because a memory figure is not worth a second line or a first
    exception. `cgroup_peak_mb` is the container's running maximum, so a jump between
    two consecutive lines is the peak the phase between them reached.
    """
    try:
        logger.info("scan.phase_memory", scan_id=scan_id, phase=phase, **memory_stats.snapshot())
    except Exception:  # noqa: BLE001 - see above
        return


async def _attempt(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    phase: str,
    coro_factory: Callable[[], Awaitable[object]],
) -> bool:
    """Run one chained phase. Log and swallow whatever it raises. Commit on success.

    **Every phase is attempted regardless of whether an earlier one failed**,
    which is the whole point of doing it this way rather than with a single
    try around the chain. The phases are independent producers, not steps that
    build on each other's success: the audit needs only the client's domain,
    scoring degrades to `NOT_YET_MEASURED` without an audit, and fix generation
    degrades again without a score. Aborting the rest on one failure would turn
    one missing section of the report into three, and the report already knows
    how to render each absence honestly on its own.

    These are not hypothetical failures. `run_audit` raises `ValueError` on a
    domain `crawl.normalise_url` cannot parse; `score_scan` raises
    `SubScoreOutOfRangeError` if an audit ever writes a Technical Foundation
    outside 0-100, which `technical_audits` has no CHECK constraint to prevent;
    and `generate_for_scan` raises `RuntimeError` when `ANTHROPIC_API_KEY` is
    unset. Each is one phase's problem and none of them should cost the others.

    **Commits per phase, not once at the end.** `run_audit`, `score_scan` and
    `generate_for_scan` all only flush — the caller owns the transaction — so a
    single commit at the end would mean a raise in fix generation discarding a
    perfectly good audit and score, including the paid model calls that
    produced them. It also rolls back on failure, because Postgres aborts a
    transaction on error and every later statement on that connection would
    fail until it is cleared.
    """
    try:
        await coro_factory()
        await session.commit()
        return True
    except Exception:  # noqa: BLE001 - one phase's failure is not the chain's
        logger.exception("scan.chain.phase_failed", scan_id=scan.id, phase=phase)
        await session.rollback()
        # `rollback()` EXPIRES every persistent instance, and unlike `commit()`
        # it does so regardless of `expire_on_commit=False` (db.py). `scan` and
        # `client` are handed to every phase after this one, and the first
        # attribute access on an expired instance under the async session is a
        # lazy refresh — which raises MissingGreenlet rather than reloading.
        #
        # So one failed phase would take out every phase after it, by exactly
        # the mechanism this function exists to prevent. Reloading here is what
        # makes "attempt them all" true rather than merely intended.
        try:
            await session.refresh(scan)
            await session.refresh(client)
        except Exception:  # noqa: BLE001 - the row is gone; nothing left to run
            logger.exception("scan.chain.reload_failed", scan_id=scan.id, phase=phase)
        return False


async def execute_scan(job: ScanJob, *, settings: Settings) -> None:
    """Run a queued scan to completion, on its own session.

    Deliberately opens a **new** session rather than accepting one. The request
    that queued the scan has already returned by the time this runs, and its
    session is closed; borrowing it would be a use-after-free.

    Never raises. A background task with nobody to catch it would otherwise
    disappear into the event loop's exception handler, leaving the row at
    RUNNING with no record of why.

    THE CHAIN — Epic 9.17
    ---------------------
    Until this epic, this function ran the engine loop and stopped. Every scan
    started from the UI therefore produced a report reading "Not scored",
    `NO_COMPETITOR_SET` and `TECHNICAL_FOUNDATION_NOT_MEASURED` — not because
    those phases were unbuilt, but because nothing called them. `verify_e2e.py`
    proved the whole pipeline worked by calling each phase itself; the product
    never did.

    The order below is `verify_e2e.py`'s, and it is not arbitrary:

        competitor set -> engine loop -> audit -> score -> fixes

    **Competitor detection comes FIRST, before the engine loop, not after it.**
    `run_scan` reads the competitor list at its top and feeds it into two
    places nothing can correct afterwards: prompt generation is seeded with the
    rival names, and fact extraction scopes brand detection to them, which is
    what produces `position`, `brands_mentioned`, Share of Voice, and the
    `competitor_id` attribution on every mention and citation. A scan run
    without a set does not fail — it reports SUCCEEDED with the subject at
    position 1 of 1 in every answer. Detection afterwards would write a set
    that nothing had used.

    Audit before scoring, because `scoring_runner.load_technical_foundation`
    reads the audit row and excludes the dimension as `NOT_YET_MEASURED` when
    there is none. Fixes last, because they read all three.

    THE CLAIM — API key discipline audit, 2026-09-07
    -------------------------------------------------
    A scan is claimed before it is run, with one conditional UPDATE: QUEUED
    becomes RUNNING for exactly the executor whose statement finds it QUEUED.
    Until this existed the only guard was `scan.status in TERMINAL`, which
    QUEUED and RUNNING are deliberately outside of — the first executor has to
    be let through — so two POSTs that adopted the same open scan produced two
    executors, and the second re-ran the paid chain: competitor detection, an
    8k-token prompt-generation call, and only then a unique violation on
    `prompt_sets` that marked the scan FAILED while the first executor was
    still legitimately running it. `uq_scans_one_open_per_client` never
    prevented this. It bounds ROWS, and both executors held the same row.

    The claim comes before competitor detection, not just before the loop,
    because detection is the first phase that spends money.

    **What an executor may FAIL depends on whether it holds the claim**
    (`holds_claim` below, set once its claim has committed). One whose claim
    matched nothing is not running the scan, and the scan in the table may be
    another executor's live one: if one of its own database calls then raises,
    `_mark_failed` may fail it only while it is still QUEUED. Only an executor
    whose claim has committed may fail a RUNNING scan.

    THE LEASE — built 2026-09-08
    ----------------------------
    The claim stamps `lease_expires_at`; a heartbeat on its own session pushes
    it out every `HEARTBEAT_INTERVAL` while the chain runs; `reap_stale_scans`
    fails RUNNING scans whose lease has expired. Duration is no longer evidence
    of anything — `STALE_AFTER` is gone, and a scan is reaped for going quiet.

    **A renewal that matches zero rows means the lease is gone, and the
    executor stops.** That is the half a reaper alone never had: it stamped the
    row and the work carried on, billing against a scan already written off.
    Zero rows cancels the chain task, `run_under_lease` returns False, and this
    function returns without writing a status — the row belongs to whoever
    took it.

    The renewal is deliberately NOT next to each paid call. That shape makes
    the lease length a sum of ceilings living in five other modules, so a phase
    added later silently outgrows it. A clock does not care what the chain
    grows into. See the constants above for both numbers and for why the lease
    is six intervals rather than three.

    The claim stays QUEUED-only. Re-claiming an expired RUNNING scan was in the
    design and is not built, because `run_scan` is not resumable:
    `build_prompt_set` always inserts a new `prompt_sets` row, so a re-claim
    would pay for competitor detection and a 182s-ceiling prompt generation and
    then die on `uq_prompt_sets_scan_id`, landing FAILED / EXECUTION_FAILED —
    strictly worse than the reaper's FAILED / EXECUTOR_LOST at no spend, after
    which the next POST opens a clean row. It also has no caller: the router
    reaps before adopting and never submits a job for a RUNNING scan. Widening
    the WHERE is worth revisiting only alongside a resumable chain and a
    fencing token, because after a re-claim the row is still RUNNING and the
    old executor's renewal would match it and renew somebody else's lease.

    THE DEADLINE — decision 7, 2026-10-01
    -------------------------------------
    A chain that outruns `MAX_SCAN_DURATION` is stopped, and what it had
    finished is kept (`_finalize_after_deadline`): PARTIAL / SCAN_DEADLINE_
    EXCEEDED when at least half of the planned prompts have an answered engine
    result (`deadline_partial_counts`), which counts; FAILED with the same code
    when fewer do, which does not. The rule judges a deadline in any phase,
    including one after the engine loop. The engine loop reports each finished prompt
    into `progress` as it goes, because it writes nothing itself until the
    whole loop is done. Every OTHER exception is an error of ours: it keeps
    `_mark_failed`'s EXECUTION_FAILED and never counts.
    """
    factory = get_sessionmaker(settings)
    # One per job. Filled by the engine loop as prompts finish, and read only
    # by the deadline's finalise — the one caller that learns of the loop's
    # results from somewhere other than `run_scan`'s return.
    progress = scan_runner.EnginePhaseProgress()
    # Whether THIS executor's claim has committed. Read only by the handler below, to tell
    # `_mark_failed` what the executor may fail; see "THE CLAIM" above.
    holds_claim = False
    try:
        async with factory() as session:
            scan = await session.get(Scan, job.scan_id)
            client = await session.get(Client, job.client_id)
            if scan is None or client is None:
                logger.warning(
                    "scan.execute.vanished", scan_id=job.scan_id, client_id=job.client_id
                )
                return
            if scan.status in TERMINAL:
                # Already finished — a duplicate submission, or a reaper got
                # here first. Re-running would duplicate the spend.
                logger.info(
                    "scan.execute.already_terminal",
                    scan_id=scan.id, status=scan.status.value,
                )
                return

            # -- the claim: QUEUED -> RUNNING, for exactly one executor -------
            #
            # A concurrent executor's own claim blocks on this row until the
            # commit below, then matches nothing. Committed at once for that
            # reason: an uncommitted claim is no claim.
            claimed = (
                await session.execute(
                    update(Scan)
                    .where(
                        Scan.id == scan.id,
                        Scan.status == ScanStatus.QUEUED,
                        # Not a row already reapable (submitted, then past the
                        # window): it must not be both claimable and reapable.
                        or_(
                            Scan.submitted_at.is_(None),
                            Scan.submitted_at >= datetime.now(UTC) - IN_FLIGHT_WINDOW,
                        ),
                    )
                    .values(
                        status=ScanStatus.RUNNING,
                        started_at=datetime.now(UTC),
                        lease_expires_at=datetime.now(UTC) + LEASE,
                    )
                    .returning(Scan.id)
                    .execution_options(synchronize_session=False)
                )
            ).scalar_one_or_none()
            await session.commit()
            # Ours, and durable, once the UPDATE matched and this commit has returned. Set
            # before the refresh below, which can raise: an executor whose claim has
            # committed owns a RUNNING scan, however soon after it fails.
            holds_claim = claimed is not None
            await session.refresh(scan)
            if claimed is None:
                # RUNNING, and not ours. Somebody else's executor has it, and
                # the reaper is the only thing entitled to take it back.
                logger.info(
                    "scan.execute.not_claimed",
                    scan_id=scan.id, status=scan.status.value,
                )
                return

            # -- the work, for as long as the lease holds -----------------
            #
            # Cancelled from under us the moment a renewal matches zero
            # rows. Everything the chain would have gone on to spend is
            # what that cancellation saves.
            #
            # In the scope that totals what the scan pays for (`usage.py`): the chain runs as a
            # task started INSIDE it, so it and every task it starts share the accumulator, and
            # one `scan.usage` line is logged when this block ends however it ends: finished,
            # PARTIAL, FAILED, raised, cancelled by a lost lease, or out of time. An executor
            # that held no claim never gets here, and spent nothing.
            with usage.scan_scope(scan.id):
                held = await run_under_lease(
                    _run_chain(session, scan, client, job, settings=settings, progress=progress),
                    scan_id=scan.id,
                    settings=settings,
                )
            if not held:
                # The row is not ours. Somebody else — the reaper, or a
                # finalise that beat us — owns its status now, and writing
                # one here would be this executor's last act of trespass.
                logger.warning("scan.execute.lease_lost", scan_id=scan.id)
                return
    except ScanDeadlineError:
        # Not a crash: the chain was alive and renewing, and ran out of time.
        # By the time this runs the chain's session is closed (the `async with`
        # above has exited), so the finalise has the table to itself. Ids and
        # counts only in the log: `held_prompts` is the finished prompts whose
        # results are not written yet (the holder is emptied once they are, so
        # a deadline after the engine phase reads 0).
        logger.warning(
            "scan.deadline_exceeded",
            scan_id=job.scan_id,
            limit_s=MAX_SCAN_DURATION.total_seconds(),
            held_prompts=len(progress.completed),
        )
        await _finalize_after_deadline(job.scan_id, progress, settings=settings)
    except PromptsUnavailable as exc:
        # An expected ending, not a crash: `build_prompt_set` has logged `prompts.unavailable`
        # with the reason, and nothing was asked of any engine.
        logger.warning("scan.execute.prompts_unavailable", scan_id=job.scan_id, reason=exc.reason)
        await _mark_failed(job.scan_id, exc, claimed=holds_claim, settings=settings)
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never re-raised
        logger.exception("scan.execute.failed", scan_id=job.scan_id)
        await _mark_failed(job.scan_id, exc, claimed=holds_claim, settings=settings)


async def _run_chain(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    job: ScanJob,
    *,
    settings: Settings,
    progress: scan_runner.EnginePhaseProgress,
) -> None:
    """Every phase, in order, on the claimed scan.

    Extracted from `execute_scan` so it can run as its own task under
    `run_under_lease` — losing the lease has to cancel THIS and nothing
    above it. The order and the reasoning for it are unchanged and are
    documented on `execute_scan`. `progress` goes to the engine loop and to
    nothing else: it is how the deadline finds what the loop had finished.
    """
    # -- phase 3: the competitor set, BEFORE the loop that reads it --
    #
    # Attempted like every other phase rather than allowed to abort the
    # scan: a scan with no rivals is degraded, and the report says so,
    # but it still measures whether the subject is mentioned at all,
    # which is most of what a first scan is for.
    await _attempt(
        session, scan, client, "competitors",
        lambda: detection.ensure_set_for_scan(
            session, scan, client, settings=settings
        ),
    )
    _log_phase_memory(job.scan_id, "competitors")

    # -- the scan must still be RUNNING before the loop --------------
    #
    # `_attempt` refreshes the scan after a failed phase, so when the reaper has already
    # failed it and the phase above then raised, this copy reads FAILED. `run_scan` assigns
    # RUNNING to its scan, and the ORM would write that over the reaper's verdict: the
    # heartbeat's next renewal then matches again, and `finalize_scan` stamps SUCCEEDED or
    # PARTIAL on a scan that had been failed. A copy that does not say RUNNING means somebody
    # has ended the scan, so stop here and write nothing. A copy that does say RUNNING cannot
    # be told from a live scan until the next heartbeat; `run_scan` writes no status for it
    # (the ORM writes only what changed), and `finalize_scan`'s condition on the row is what
    # holds then. Launch checklist 3.36.
    if scan.status is not ScanStatus.RUNNING:
        logger.warning(
            "scan.chain.stopped_not_running", scan_id=scan.id, status=scan.status.value
        )
        return

    # -- phases 4-5: prompt generation and the engine loop -----------
    #
    # Not wrapped in `_attempt`. This one IS the scan: it commits its
    # own progress, and if it raises there is nothing downstream worth
    # running, so the outer handler marks the scan FAILED as it always
    # has. `defer_terminal_status` leaves it RUNNING so the dashboard
    # keeps polling while the phases below finish.
    await scan_runner.run_scan(
        session, scan, client,
        settings=settings, engines=job.engines, prompt_limit=job.prompt_limit,
        defer_terminal_status=True, progress=progress,
    )
    _log_phase_memory(job.scan_id, "engines")

    # -- phase 6: the technical audit --------------------------------
    #
    # Its wait for the process's one browser is the scan's own, longer one: nobody is
    # at a screen, and a run ahead of it can take minutes (`browser_slot`). A wait that
    # still runs out is a failed audit with `BROWSER_BUSY`, and the chain goes on.
    # It is never counted against an agency's one run on a request route.
    await _attempt(
        session, scan, client, "audit",
        lambda: audit_runner.run_audit(session, scan, client, slot_wait_s=SCAN_AUDIT_WAIT_S),
    )
    _log_phase_memory(job.scan_id, "audit")

    # -- phase 7: scoring --------------------------------------------
    await _attempt(
        session, scan, client, "scoring",
        lambda: scoring_runner.score_scan(session, scan),
    )
    _log_phase_memory(job.scan_id, "scoring")

    # -- phase 7b: alerts, AFTER scoring because they read its score --
    #
    # Ordered here and not earlier for a reason the phase list makes
    # easy to get wrong: `generate_for_scan` compares this scan's
    # STORED composite against its baseline's, and phase 7 is what
    # writes it. Run before scoring, every visibility alert would
    # compare `None` and silently produce nothing.
    await _attempt(
        session, scan, client, "alerts",
        lambda: alerts_service.generate_for_scan(session, scan, client),
    )
    _log_phase_memory(job.scan_id, "alerts")

    # -- phase 8: the fix list ---------------------------------------
    await _attempt(
        session, scan, client, "fixes",
        lambda: fix_runner.generate_for_scan(
            session, scan, client, settings=settings
        ),
    )
    _log_phase_memory(job.scan_id, "fixes")

    # Only now is the scan finished. Stamped from the persisted engine
    # results, so it says what the ENGINE phase achieved — the chained
    # phases each record their own outcome on their own rows, and a
    # failed audit is not a failed scan.
    await scan_runner.finalize_scan(session, scan)
    _log_phase_memory(job.scan_id, "finalized")


async def _count_results(session: AsyncSession, scan_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(EngineResult)
                .where(EngineResult.scan_id == scan_id)
            )
        ).scalar_one()
    )


async def _persist_finished(
    session: AsyncSession, scan: Scan, progress: scan_runner.EnginePhaseProgress
) -> None:
    """Write what the engine loop had finished, once, and commit it.

    With `persist_result` — the loop's own writer — so a row written here is
    the row the loop would have written: same facts, same digest, same
    competitor links (`progress.competitor_ids`). The prompts are loaded by id
    in THIS session (the loop's own is closed) and written in prompt order, as
    `run_scan` writes them; completion order is the order the loop happened to
    finish in, not the order the rest of the product reads the rows.
    """
    prompts = {
        prompt.id: prompt
        for prompt in (
            await session.execute(
                select(Prompt).where(
                    Prompt.id.in_([prompt_id for prompt_id, _ in progress.completed])
                )
            )
        ).scalars()
    }
    for prompt_id, per_engine in sorted(
        progress.completed, key=lambda finished: prompts[finished[0]].position
    ):
        for engine, facts, answer in per_engine:
            scan_runner.persist_result(
                session,
                scan=scan,
                prompt=prompts[prompt_id],
                engine=engine,
                facts=facts,
                answer=answer,
                competitor_ids=progress.competitor_ids,
            )
    await session.commit()


# The founder's rule (2026-10-01): a deadline-partial counts only if at least
# this share of the PLANNED prompts has an answered engine result.
DEADLINE_PARTIAL_MIN_ANSWERED_SHARE = Fraction(1, 2)


def deadline_partial_counts(answered_prompts: int, planned_prompts: int) -> bool:
    """Whether a scan that hit its deadline ends PARTIAL (counts) rather than FAILED.

    Integer arithmetic, no float. A plan that was never recorded (0 or less)
    cannot be measured against, so it does not count even if answers exist.
    """
    share = DEADLINE_PARTIAL_MIN_ANSWERED_SHARE
    return (
        planned_prompts > 0
        and answered_prompts >= 1
        and answered_prompts * share.denominator >= planned_prompts * share.numerator
    )


async def _finalize_after_deadline(
    scan_id: str, progress: scan_runner.EnginePhaseProgress, *, settings: Settings
) -> None:
    """End a scan that ran out of time, keeping the answers it had — decision 7.

    The founder's decision (2026-10-01): "A scan that hits its deadline
    finalises as PARTIAL with the answers it has, and counts." The founder then
    set the line it must clear: at least half of the PLANNED prompts answered.
    Which side a scan is on is read from the TABLE, after anything the loop had
    finished has been written to it, and the same rule judges a deadline in any
    phase, including one that falls after the engine loop (audit, score, alerts,
    fixes):

    * at least half of the PLANNED prompts (`Scan.prompt_count`) have an
      answered engine result (`OK` or `ANSWERED_NO_MENTION`;
      `scan_runner.count_answered_prompts`; the rule is `deadline_partial_counts`):
      the scan is scored if it has no score, and ends PARTIAL /
      SCAN_DEADLINE_EXCEEDED. It counts.
    * fewer, or none — nothing finished, too few prompts were answered,
      everything that finished was an engine error, or the plan was never
      recorded: FAILED / SCAN_DEADLINE_EXCEEDED. It does not count, and this
      function does not score it. The rows written are kept either way, and so
      is anything the chain wrote before the deadline: a score made in phase 7
      stays on a scan that ends FAILED here, and nothing is deleted.

    No audit, no alerts and no fixes are run by the finalise: the deadline
    stopped them, and fixes spend money. The report renders each absence
    honestly, as it does for a phase that failed.

    THE ORDER IS THE POINT.

    1. **The lease is renewed first.** False means the row is no longer RUNNING
       — the reaper failed it, or a finalise beat us — and its status is not
       ours to write: return, and the reaper's verdict stands. True also gives
       everything below a fresh 180 seconds, so the finalise cannot be reaped
       half done, and the scan keeps its allowance slot until it is stamped. It
       also queues this UPDATE behind any transaction of the loop's that the
       cancellation interrupted mid-commit, so the count below is taken after
       that commit has landed or been abandoned.
    2. **The table is the truth, not the holder.** Results are written from
       `progress` only if the scan has NO rows: the loop's own commit may have
       landed after the deadline fired and before it set `progress.persisted`,
       and a second write would double every row.
    3. **One conditional UPDATE stamps it** (`WHERE status = 'running'`), so a
       row that stopped being ours between step 1 and here is left alone.

    A FRESH session, for the reason `_mark_failed` gives: the chain's own is
    closed and may be broken. Anything that goes wrong falls back to
    `_mark_failed`: FAILED, which never counts — the ending that costs the
    customer nothing when this cannot say what the scan kept.
    """
    try:
        if not await renew_lease(scan_id, settings=settings):
            logger.warning("scan.deadline.lease_gone", scan_id=scan_id)
            return

        factory = get_sessionmaker(settings)
        async with factory() as session:
            scan = await session.get(Scan, scan_id)
            if scan is None:
                logger.warning("scan.deadline.vanished", scan_id=scan_id)
                return

            # -- keep what finished ------------------------------------------
            rows = await _count_results(session, scan_id)
            if rows == 0 and progress.completed and not progress.persisted:
                await _persist_finished(session, scan, progress)
                rows = await _count_results(session, scan_id)
            answered = await scan_runner.count_answered_prompts(session, scan_id)
            planned = scan.prompt_count or 0
            # `prompt_count` 0/None with answers present means the plan was
            # never recorded: not measurable, so FAILED and uncounted.
            counts = deadline_partial_counts(answered, planned)

            # -- score it, once, if there is anything to score ---------------
            #
            # A score the chain already made is never redone. A failure here
            # costs the score and nothing else: the answers are kept and the
            # scan is stamped without one. Rolled back, because Postgres
            # aborts a transaction on error and the stamp below runs on this
            # session — which stays usable even when the budget cancels a
            # statement mid-flight (the test pins that case). `scan_id`, not
            # `scan.id`: the rollback expires `scan`.
            if counts and await scoring_runner.latest_score(session, scan_id) is None:
                try:
                    # DB-only work, well inside the fresh lease — see the constant.
                    async with asyncio.timeout(DEADLINE_SCORING_BUDGET.total_seconds()):
                        await scoring_runner.score_scan(session, scan)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 - the scan is stamped without a score
                    # The exception CLASS only: a message can carry anything.
                    logger.warning(
                        "scan.deadline.scoring_failed",
                        scan_id=scan_id,
                        error=type(exc).__name__,
                    )
                    await session.rollback()

            # -- stamp it, if it is still ours -------------------------------
            status = ScanStatus.PARTIAL if counts else ScanStatus.FAILED
            stamped = (
                await session.execute(
                    update(Scan)
                    .where(Scan.id == scan_id, Scan.status == ScanStatus.RUNNING)
                    .values(
                        status=status,
                        error_code=SCAN_DEADLINE_EXCEEDED,
                        # The exception TYPE only, as `_mark_failed` writes it.
                        error_detail=ScanDeadlineError.__name__,
                        finished_at=datetime.now(UTC),
                        # What is actually in the table, not what the loop set
                        # out to write.
                        engine_result_count=rows,
                    )
                    .returning(Scan.id)
                    .execution_options(synchronize_session=False)
                )
            ).scalar_one_or_none()
            await session.commit()
        if stamped is None:
            logger.warning("scan.deadline.lease_lost_before_stamp", scan_id=scan_id)
            return
        logger.info(
            "scan.finalized_after_deadline",
            scan_id=scan_id,
            status=status.value,
            results=rows,
            answered_prompts=answered,
            planned_prompts=planned,
        )
    except Exception as exc:  # noqa: BLE001 - the plain failure is the safe ending
        logger.warning(
            "scan.deadline.finalize_failed", scan_id=scan_id, error=type(exc).__name__
        )
        # A deadline can only follow a claim: it is raised inside `run_under_lease`.
        await _mark_failed(scan_id, ScanDeadlineError(), claimed=True, settings=settings)


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, ScanDeadlineError):
        return SCAN_DEADLINE_EXCEEDED
    if isinstance(exc, PromptsUnavailable):
        return PROMPTS_UNAVAILABLE
    return EXECUTION_FAILED


async def _prompts_failure_code(session: AsyncSession, scan_id: str, *, now: datetime) -> str:
    """PROMPTS_UNAVAILABLE, or the counted code when the agency has used its free three.

    Only a failure caused by the model's ANSWER comes here (`too_few_questions`,
    `unusable_answer`, `schema_violation`). An outage (`generation_failed`: an API error or a
    timeout) is ours: `_mark_failed` never calls this for it, writes it with
    `OUTAGE_DETAIL`, and the count below skips such rows, so an outage is never charged and
    never uses up the three.

    Free when the agency had fewer than `FREE_PROMPT_FAILURES_PER_DAY` prompt-phase failures
    (free or counted) finished in the rolling 24 hours before `now`; otherwise it counts. No
    timezone, no calendar day. The agency row is locked FOR UPDATE for the count and the write
    that follows in the caller's transaction, so two failures of one agency cannot both read
    "two so far". That lock is the one `assert_can_start` takes, held here only for these two
    statements and with no other lock held, so it cannot deadlock; at worst a start waits.
    """
    agency_id = (
        await session.execute(select(Scan.agency_id).where(Scan.id == scan_id))
    ).scalar_one_or_none()
    if agency_id is None:
        return PROMPTS_UNAVAILABLE
    await session.execute(
        select(Agency.id).where(Agency.id == agency_id).with_for_update()
    )
    earlier = (
        await session.execute(
            select(func.count())
            .select_from(Scan)
            .where(
                Scan.agency_id == agency_id,
                Scan.id != scan_id,
                Scan.status == ScanStatus.FAILED,
                Scan.error_code.in_((PROMPTS_UNAVAILABLE, CODE_PROMPTS_UNAVAILABLE_COUNTED)),
                Scan.finished_at >= now - PROMPT_FAILURE_WINDOW,
                # No upper bound: `now` was read before the lock, and a racing failure that
                # committed just before it can carry a later `finished_at` (READ COMMITTED sees it).
                or_(Scan.error_detail.is_(None), Scan.error_detail != OUTAGE_DETAIL),
            )
        )
    ).scalar_one()
    return PROMPTS_UNAVAILABLE if earlier < FREE_PROMPT_FAILURES_PER_DAY else (
        CODE_PROMPTS_UNAVAILABLE_COUNTED
    )


async def _mark_failed(
    scan_id: str, exc: Exception, *, claimed: bool, settings: Settings
) -> None:
    """Record a crashed scan, on a session that is definitely usable.

    A **fresh** session, not the one that raised. Postgres aborts a transaction
    on error, so every further statement on that connection fails until it is
    rolled back — writing the failure through it is exactly as likely to fail as
    the thing that just did. `session_scope` makes the same assumption for
    requests; a background task has no such wrapper, so it is explicit here.

    FAILED, so it never counts against the allowance, whatever the cause — except a
    `PromptsUnavailable` past the agency's free three in 24 hours, which is written
    PROMPTS_UNAVAILABLE_COUNTED and counts (`_prompts_failure_code`). A
    `ScanDeadlineError` reaches here only as `_finalize_after_deadline`'s
    fallback (decision 7): a deadline ordinarily ends PARTIAL or FAILED
    through that function, and this is what is left when it cannot be written.
    A `PromptsUnavailable` ends PROMPTS_UNAVAILABLE. Every other exception is an error of ours
    and ends EXECUTION_FAILED.

    **ONE conditional UPDATE, not a read and then a write.** A finished scan is
    never re-marked, and that has to be a condition of the statement itself:
    this is the fallback of a finalise that may have just landed a PARTIAL (its
    commit can be in flight, or its reply lost), and a write decided on an
    earlier READ of the row would wait for that commit and then replace the
    PARTIAL with FAILED. `WHERE status IN ('queued', 'running')` is checked
    again once the row is free, so it matches nothing instead. The same rule as
    the old guard (`scan.status in TERMINAL`), without its window.

    **WHAT IT MAY FAIL DEPENDS ON `claimed`** — launch checklist 3.36, the
    security review's second Low. This runs for any exception in
    `execute_scan`, including one in an executor whose claim matched nothing
    because another executor holds the scan (two POSTs can adopt one QUEUED
    scan). `status IN ('queued', 'running')` would then match that other
    executor's LIVE scan, and nothing overwrites a failure any more
    (`finalize_scan` is conditional too). So an executor whose claim has
    committed fails a QUEUED or RUNNING scan, as before; one that has not
    claimed fails it only while it is still QUEUED, which nobody holds. It
    does not skip the write: left QUEUED the scan would be counted against the
    allowance until the never-started reap took it a quarter of an hour later.
    A RUNNING scan is somebody's, and only the reaper may take it back.
    """
    # What this executor may fail: what it has claimed, or, without a claim, only a scan
    # nobody holds.
    may_fail = (ScanStatus.QUEUED, ScanStatus.RUNNING) if claimed else (ScanStatus.QUEUED,)
    factory = get_sessionmaker(settings)
    try:
        async with factory() as session:
            now = datetime.now(UTC)
            code = _failure_code(exc)
            detail = type(exc).__name__
            if isinstance(exc, PromptsUnavailable):
                if exc.reason == GENERATION_FAILED:
                    # An outage of ours (API error or timeout): always free, outside the three.
                    detail = OUTAGE_DETAIL
                else:
                    # Free or counted, decided once, here, and written on the row.
                    code = await _prompts_failure_code(session, scan_id, now=now)
            await session.execute(
                update(Scan)
                .where(Scan.id == scan_id, Scan.status.in_(may_fail))
                .values(
                    status=ScanStatus.FAILED,
                    error_code=code,
                    # The exception TYPE only. `error_detail` carries our own
                    # diagnostics and never a vendor response body, which could
                    # hold third-party content (the facts-only rule).
                    error_detail=detail,
                    finished_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - the datastore is gone; the reaper covers it
        logger.exception("scan.execute.failed_to_record", scan_id=scan_id)


class BackgroundScanExecutor:
    """Runs the scan in this process, after the response is sent.

    Epic 9.4 chose FastAPI's `BackgroundTasks` over Celery for this first slice:
    Celery is a dependency only of a package the API cannot import, so using it
    means a broker client in the API process, a worker to run and supervise, a
    docker-compose service, and an `asyncio.run` bridge — a deployment change,
    for a pre-pilot single-instance product.

    What it gives up is real and is not hidden: this does not survive a restart
    and does not scale past one instance. That is precisely why
    `reap_stale_scans` below is part of this slice rather than a later one.
    """

    def __init__(self, background: object) -> None:
        # `starlette.background.BackgroundTasks`, typed loosely so this module
        # does not depend on the web framework to be unit-tested.
        self._background = background

    async def submit(self, job: ScanJob, *, settings: Settings) -> None:
        self._background.add_task(execute_scan, job, settings=settings)  # type: ignore[attr-defined]


class InlineScanExecutor:
    """Runs the scan before `submit` returns.

    The test seam. It keeps "the scan is finished once the POST returns" true in
    the suite, so the endpoint's async-ness does not force a poll loop into
    every test that merely needs a completed scan to assert against.
    """

    async def submit(self, job: ScanJob, *, settings: Settings) -> None:
        await execute_scan(job, settings=settings)


async def fail_never_started_scans(
    session: AsyncSession, *, now: datetime, scan_id: str | None = None
) -> list[str]:
    """Fail QUEUED scans that were submitted, then never claimed, past the window.

    The one rule, used by the global reaper and by the single-row lazy reap in
    `GET /scans/{scanId}` (`scan_id` scopes it to that row). A QUEUED row with
    `submitted_at IS NULL` is never touched: that is detection's open scan.
    Returns the ids failed. The caller commits.
    """
    stmt = update(Scan).where(
        Scan.status == ScanStatus.QUEUED,
        Scan.submitted_at.is_not(None),
        Scan.submitted_at < now - IN_FLIGHT_WINDOW,
    )
    if scan_id is not None:
        stmt = stmt.where(Scan.id == scan_id)
    result = await session.execute(
        stmt.values(
            status=ScanStatus.FAILED,
            error_code=EXECUTOR_NEVER_STARTED,
            error_detail=(
                "No executor started this scan within 15 minutes of it being submitted."
            ),
            finished_at=datetime.now(UTC),
        )
        .returning(Scan.id)
        .execution_options(synchronize_session=False)
    )
    return list(result.scalars().all())


async def reap_stale_scans(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Fail scans whose executor went away. Returns how many were reaped.

    **RUNNING by lease; QUEUED only once submitted and past the window**, and
    the distinction is load-bearing. QUEUED alone means "open, nobody has
    claimed it": that is the state a detect-only run leaves behind for a later
    scan to pick up (`competitors.py`'s `get_or_create_scan`), and reaping it
    would break detect-now-scan-tomorrow. So a QUEUED row with
    `submitted_at IS NULL` is never reaped, however old. But a QUEUED row with
    a `submitted_at` older than `IN_FLIGHT_WINDOW` was handed to an executor
    that never claimed it (the process restarted first); left alone it holds
    the client's open scan and, before the window closed, an allowance slot
    with nothing running. That half is failed as `EXECUTOR_NEVER_STARTED`; the
    executor's claim is a conditional QUEUED -> RUNNING UPDATE, so a reaped
    scan can never be picked up late.

    RUNNING means an executor claimed it and started work. Without this the row
    stays RUNNING forever when that executor goes away: the dashboard shows
    "Running…", re-run stays disabled, and the next POST reuses the row and
    dies on `prompt_sets`' unique constraint. Nothing else corrects it.

    **KEYED ON THE LEASE, NOT ON DURATION.** `started_at < now() - STALE_AFTER`
    used to stand here and asked the wrong question: it reaped a scan for
    taking long, and a maximal scan legitimately takes longer than the constant
    allowed. The predicate now reads "this executor has not said anything for
    `LEASE`", which is the thing the reaper was always trying to detect.

    NULL IS NOT EXPIRED, and the strictness is deliberate. A scan driven
    straight through `scan_runner.run_scan` — `scripts/verify_e2e.py`,
    `scripts/verify_scoring.py`, `tests/conftest.py` — sets RUNNING itself and
    holds no lease, and reaping those mid-flight would fail work that is
    perfectly alive. The cost is the other half of the same coin: one of those
    scripts dying mid-run leaves a RUNNING row nothing will ever reap, where
    `STALE_AFTER` would have caught it fifteen minutes later. Accepted, because
    it strands only the scratch clients those scripts create, and the fix for
    it is for them to hold a lease rather than for the reaper to guess.

    Two UPDATEs, covered by `ix_scans_status`. The caller commits. Returns the
    combined count.
    """
    moment = now or datetime.now(UTC)
    result = await session.execute(
        update(Scan)
        .where(
            Scan.status == ScanStatus.RUNNING,
            Scan.lease_expires_at.is_not(None),
            Scan.lease_expires_at < moment,
        )
        .values(
            status=ScanStatus.FAILED,
            error_code=EXECUTOR_LOST,
            error_detail="No executor renewed this scan's lease before it expired.",
            finished_at=datetime.now(UTC),
        )
        # RETURNING rather than rowcount: it is portably typed, and it names
        # which scans were reaped so the log says what happened to what.
        .returning(Scan.id)
    )
    reaped = list(result.scalars().all())
    if reaped:
        logger.warning(
            "scan.reaped",
            count=len(reaped),
            scan_ids=reaped,
            lease_s=LEASE.total_seconds(),
        )
    never_started = await fail_never_started_scans(session, now=moment)
    if never_started:
        logger.warning(
            "scan.reaped_never_started",
            count=len(never_started),
            scan_ids=never_started,
            window_s=IN_FLIGHT_WINDOW.total_seconds(),
        )
    return len(reaped) + len(never_started)

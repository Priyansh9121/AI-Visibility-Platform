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
from typing import Protocol

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import get_sessionmaker
from ..models import Client, Scan, ScanStatus
from ..models.engine_result import Engine
from . import alerts as alerts_service
from . import audit_runner, fix_runner, scan_runner, scoring_runner
from . import competitors as detection

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

# Distinct from ALL_ENGINE_CALLS_FAILED (the pipeline ran and every engine
# failed) and from EXECUTION_FAILED (the pipeline raised). This one means the
# process that was running the scan disappeared without saying anything.
EXECUTOR_LOST = "EXECUTOR_LOST"
EXECUTION_FAILED = "EXECUTION_FAILED"


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
    work = asyncio.create_task(chain)
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
    would pay for competitor detection and a 122s-ceiling prompt generation and
    then die on `uq_prompt_sets_scan_id`, landing FAILED / EXECUTION_FAILED —
    strictly worse than the reaper's FAILED / EXECUTOR_LOST at no spend, after
    which the next POST opens a clean row. It also has no caller: the router
    reaps before adopting and never submits a job for a RUNNING scan. Widening
    the WHERE is worth revisiting only alongside a resumable chain and a
    fencing token, because after a re-claim the row is still RUNNING and the
    old executor's renewal would match it and renew somebody else's lease.
    """
    factory = get_sessionmaker(settings)
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
                    .where(Scan.id == scan.id, Scan.status == ScanStatus.QUEUED)
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
            held = await run_under_lease(
                _run_chain(session, scan, client, job, settings=settings),
                scan_id=scan.id,
                settings=settings,
            )
            if not held:
                # The row is not ours. Somebody else — the reaper, or a
                # finalise that beat us — owns its status now, and writing
                # one here would be this executor's last act of trespass.
                logger.warning("scan.execute.lease_lost", scan_id=scan.id)
                return
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never re-raised
        logger.exception("scan.execute.failed", scan_id=job.scan_id)
        await _mark_failed(job.scan_id, exc, settings=settings)


async def _run_chain(
    session: AsyncSession,
    scan: Scan,
    client: Client,
    job: ScanJob,
    *,
    settings: Settings,
) -> None:
    """Every phase, in order, on the claimed scan.

    Extracted from `execute_scan` so it can run as its own task under
    `run_under_lease` — losing the lease has to cancel THIS and nothing
    above it. The order and the reasoning for it are unchanged and are
    documented on `execute_scan`.
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
        defer_terminal_status=True,
    )

    # -- phase 6: the technical audit --------------------------------
    await _attempt(
        session, scan, client, "audit",
        lambda: audit_runner.run_audit(session, scan, client),
    )

    # -- phase 7: scoring --------------------------------------------
    await _attempt(
        session, scan, client, "scoring",
        lambda: scoring_runner.score_scan(session, scan),
    )

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

    # -- phase 8: the fix list ---------------------------------------
    await _attempt(
        session, scan, client, "fixes",
        lambda: fix_runner.generate_for_scan(
            session, scan, client, settings=settings
        ),
    )

    # Only now is the scan finished. Stamped from the persisted engine
    # results, so it says what the ENGINE phase achieved — the chained
    # phases each record their own outcome on their own rows, and a
    # failed audit is not a failed scan.
    await scan_runner.finalize_scan(session, scan)


async def _mark_failed(scan_id: str, exc: Exception, *, settings: Settings) -> None:
    """Record a crashed scan, on a session that is definitely usable.

    A **fresh** session, not the one that raised. Postgres aborts a transaction
    on error, so every further statement on that connection fails until it is
    rolled back — writing the failure through it is exactly as likely to fail as
    the thing that just did. `session_scope` makes the same assumption for
    requests; a background task has no such wrapper, so it is explicit here.
    """
    factory = get_sessionmaker(settings)
    try:
        async with factory() as session:
            scan = await session.get(Scan, scan_id)
            if scan is None or scan.status in TERMINAL:
                return
            scan.status = ScanStatus.FAILED
            scan.error_code = EXECUTION_FAILED
            # The exception TYPE only. `error_detail` carries our own
            # diagnostics and never a vendor response body, which could hold
            # third-party content (ip-safety.md #7).
            scan.error_detail = type(exc).__name__
            scan.finished_at = datetime.now(UTC)
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


async def reap_stale_scans(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Fail scans whose executor went away. Returns how many were reaped.

    **RUNNING only — never QUEUED**, and the distinction is load-bearing.
    QUEUED means "open, nobody has claimed it": that is the state a detect-only
    run leaves behind for a later scan to pick up (`competitors.py`'s
    `get_or_create_scan`), and reaping it would break detect-now-scan-tomorrow.
    A QUEUED row stranded by a crash is harmless anyway — `get_or_create_scan`
    reuses it on the next request.

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

    A single UPDATE, covered by `ix_scans_status`. The caller commits.
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
    return len(reaped)

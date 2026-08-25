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
from . import scan_runner

logger = structlog.get_logger(__name__)

# Statuses a scan can no longer move out of. Nothing may re-mark these.
TERMINAL: frozenset[ScanStatus] = frozenset(
    {ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED, ScanStatus.CANCELLED}
)

# --- the stale-scan threshold ------------------------------------------------
# Derived from measurement, not rounded to taste. Epic 9.2 timed this endpoint's
# share of the pipeline at ~303s (prompt generation 14.6s + scan loop 288.5s) on
# a 24-prompt run, against a 361.3s full-pipeline total. 900s is roughly 3x that
# measured duration, and also clears the 600s `task_soft_time_limit` the repo
# already treats as "stuck, not slow" (apps/workers/celery_app.py).
#
# A scan still legitimately running at 15 minutes is not slow; something that
# was holding it has gone away.
STALE_AFTER = timedelta(seconds=900)

# Distinct from ALL_ENGINE_CALLS_FAILED (the pipeline ran and every engine
# failed) and from EXECUTION_FAILED (the pipeline raised). This one means the
# process that was running the scan disappeared without saying anything.
EXECUTOR_LOST = "EXECUTOR_LOST"
EXECUTION_FAILED = "EXECUTION_FAILED"


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


async def execute_scan(job: ScanJob, *, settings: Settings) -> None:
    """Run a queued scan to completion, on its own session.

    Deliberately opens a **new** session rather than accepting one. The request
    that queued the scan has already returned by the time this runs, and its
    session is closed; borrowing it would be a use-after-free.

    Never raises. A background task with nobody to catch it would otherwise
    disappear into the event loop's exception handler, leaving the row at
    RUNNING with no record of why.
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
            await scan_runner.run_scan(
                session, scan, client,
                settings=settings, engines=job.engines, prompt_limit=job.prompt_limit,
            )
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never re-raised
        logger.exception("scan.execute.failed", scan_id=job.scan_id)
        await _mark_failed(job.scan_id, exc, settings=settings)


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

    RUNNING means an executor claimed it and started work. If that has been true
    for longer than `STALE_AFTER`, the executor is gone, and without this the row
    stays RUNNING forever: the dashboard shows "Running…", re-run stays disabled,
    and the next POST reuses the row and dies on `prompt_sets`' unique
    constraint. Nothing else in the system corrects it.

    A single UPDATE, covered by `ix_scans_status`. The caller commits.
    """
    cutoff = (now or datetime.now(UTC)) - STALE_AFTER
    result = await session.execute(
        update(Scan)
        .where(
            Scan.status == ScanStatus.RUNNING,
            Scan.started_at.is_not(None),
            Scan.started_at < cutoff,
        )
        .values(
            status=ScanStatus.FAILED,
            error_code=EXECUTOR_LOST,
            error_detail="No executor reported on this scan before the deadline.",
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
            stale_after_s=STALE_AFTER.total_seconds(),
        )
    return len(reaped)

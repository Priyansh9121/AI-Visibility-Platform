"""Wake every 10 minutes and start a due automatic rescan — F2, 2026-10-06.

`services/auto_rescan.run_once` does the work (what is due, the claim, the skips); this runs it,
in a background task for as long as the app runs (the shape of `price_watch.py`). It waits one
interval BEFORE the first wake, so an API that is restarting over and over starts nothing.

**Started only when `AUTO_RESCAN_ENABLED` is on.** Off, the default, there is no task, no query and
no line. It can never fail or delay a start, since the first wake comes after the app is up, and a
wake that raises is logged by its class and tried again 10 minutes later. The task ends only when
cancelled, which the shutdown does before it closes the pools, waiting at most `STOP_TIMEOUT_S`.

The scans it starts run in this process, as a manual one does, as tasks of their own: a wake does
not wait for a scan. `stop` cancels them too (a scan stopped by a deploy fails as it does today and
the reaper says so; the next rescan is then 30 days after it).
"""

from __future__ import annotations

import asyncio

import structlog

from .config import Settings
from .db import get_sessionmaker
from .services import auto_rescan
from .services.scan_executor import ScanJob, execute_scan

logger = structlog.get_logger(__name__)

WAKE_INTERVAL_S = 600.0
TASK_NAME = "auto-rescan"
STOP_TIMEOUT_S = 5.0

_scan_tasks: set[asyncio.Task[None]] = set()


class ProcessScanExecutor:
    """Runs a scan in this process as a task of its own, so the wake that started it is free."""

    async def submit(self, job: ScanJob, *, settings: Settings) -> None:
        task = asyncio.create_task(execute_scan(job, settings=settings), name=f"scan-{job.scan_id}")
        _scan_tasks.add(task)
        task.add_done_callback(_scan_tasks.discard)


async def watch_rescans(settings: Settings, *, interval_s: float = WAKE_INTERVAL_S) -> None:
    """Wake every `interval_s`, until cancelled. Only cancellation ends it."""
    executor = ProcessScanExecutor()
    while True:
        await asyncio.sleep(interval_s)
        try:
            await auto_rescan.run_once(
                settings, factory=get_sessionmaker(settings), executor=executor
            )
        except Exception as exc:  # noqa: BLE001 - a failed wake is tried again next time
            logger.warning("rescan.wake_failed", error=type(exc).__name__)


def start(settings: Settings) -> asyncio.Task[None] | None:
    """Start the loop when `AUTO_RESCAN_ENABLED` is on; None, and nothing, otherwise."""
    if not settings.auto_rescan_enabled:
        return None
    logger.info("rescan.watch_started", interval_s=WAKE_INTERVAL_S)
    # Read here, at call time, so a test can shorten it for the whole app.
    return asyncio.create_task(
        watch_rescans(settings, interval_s=WAKE_INTERVAL_S), name=TASK_NAME
    )


async def stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the loop and its scans and wait, at most `STOP_TIMEOUT_S`. Safe with None."""
    if task is None:
        return
    running = [task, *_scan_tasks]
    for each in running:
        each.cancel()
    done, _still_running = await asyncio.wait(set(running), timeout=STOP_TIMEOUT_S)
    for finished in done:
        if not finished.cancelled():
            finished.exception()

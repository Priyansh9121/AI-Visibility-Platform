"""Sweep for finished scans every minute and email their requesters — F1, 2026-10-06.

`services/scan_emails.sweep` does the work; this runs it once as the API starts and then every
`SWEEP_INTERVAL_S`, in a background task for as long as the app runs (the shape of
`price_watch.py`). The first sweep also covers a scan that finished just before a restart.

**Started only when the feature is available** (`EMAIL_SCAN_NOTIFICATIONS` on AND email
configured): otherwise there is no task and nothing is read. It can never fail or delay a start,
because the first sweep runs inside the task, and a sweep that raises is logged by its class and
tried again a minute later. The task ends only when cancelled, which the shutdown does before it
closes the pools, waiting at most `STOP_TIMEOUT_S`.
"""

from __future__ import annotations

import asyncio

import structlog

from .config import Settings
from .services import scan_emails

logger = structlog.get_logger(__name__)

SWEEP_INTERVAL_S = 60.0
TASK_NAME = "scan-finished-emails"
STOP_TIMEOUT_S = 5.0


async def watch_scans(settings: Settings, *, interval_s: float = SWEEP_INTERVAL_S) -> None:
    """Sweep now, then every `interval_s`, until cancelled. Only cancellation ends it."""
    while True:
        try:
            await scan_emails.sweep(settings)
        except Exception as exc:  # noqa: BLE001 - a failed sweep is tried again next minute
            logger.warning("scan_emails.sweep_failed", error=type(exc).__name__)
        await asyncio.sleep(interval_s)


def start(settings: Settings) -> asyncio.Task[None] | None:
    """Start the sweep as a task when the feature is available; None, and nothing, otherwise."""
    if not settings.scan_emails_available:
        if settings.email_scan_notifications:
            logger.info("scan_emails.not_started", reason="email is not configured")
        return None
    logger.info(
        "scan_emails.watch_started",
        interval_s=SWEEP_INTERVAL_S,
        window_s=scan_emails.WINDOW.total_seconds(),
    )
    # Read here, at call time, so a test can shorten it for the whole app.
    return asyncio.create_task(
        watch_scans(settings, interval_s=SWEEP_INTERVAL_S), name=TASK_NAME
    )


async def stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the sweep and wait for it, at most `STOP_TIMEOUT_S`. Safe with None."""
    if task is None:
        return
    task.cancel()
    done, _still_running = await asyncio.wait({task}, timeout=STOP_TIMEOUT_S)
    for finished in done:
        if not finished.cancelled():
            finished.exception()

"""Celery application.

Configuration choices worth stating, because the defaults are wrong for this
workload — every pipeline stage calls a slow, flaky, rate-limited, and
*expensive* third-party API.
"""

from __future__ import annotations

from celery import Celery

from .config import settings

celery_app = Celery("avp", broker=settings.broker_url, backend=settings.result_backend)

celery_app.conf.update(
    # --- serialisation ---------------------------------------------------
    # JSON only. Celery's pickle support is remote code execution for anyone
    # who can write to the broker.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # --- delivery guarantees ---------------------------------------------
    # A task is acknowledged only after it completes, so a worker killed
    # mid-scan returns the job to the queue instead of dropping it. Safe here
    # because tasks are written to be idempotent (see orchestrator.py).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Each worker prefetches one task. The default of 4 lets a single worker
    # hoard long-running scans while its peers sit idle.
    worker_prefetch_multiplier=1,
    # --- retries -----------------------------------------------------------
    task_default_retry_delay=10,
    task_max_retries=3,
    # Jitter matters: without it, a rate-limited engine returns 429 to every
    # in-flight task at once and they all retry in lockstep, reproducing the
    # burst that caused the limit.
    task_retry_backoff=True,
    task_retry_backoff_max=300,
    task_retry_jitter=True,
    # --- timeouts ----------------------------------------------------------
    # Epic 9 targets URL-in to report-out in under 5 minutes. A single stage
    # exceeding 10 minutes is stuck, not slow.
    task_soft_time_limit=600,
    task_time_limit=660,
    # --- results -----------------------------------------------------------
    result_expires=60 * 60 * 24,
    # --- routing -----------------------------------------------------------
    task_default_queue=settings.task_default_queue,
    # Engine calls are IO-bound and slow; audits are CPU-bound and fast. Mixing
    # them on one queue lets a burst of scans starve the audits.
    task_routes={
        "avp.engine.*": {"queue": "engines"},
        "avp.audit.*": {"queue": "audits"},
        "avp.*": {"queue": "scans"},
    },
    task_track_started=True,
)

# Import task modules so they register on the app.
celery_app.autodiscover_tasks(["avp_workers.tasks"], force=True)

from .tasks import health  # noqa: E402,F401  (import for side-effect registration)

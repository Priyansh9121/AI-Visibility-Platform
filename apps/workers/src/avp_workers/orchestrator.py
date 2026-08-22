"""Orchestration abstraction.

**Why this exists.** product-spec.md §5.1 leaves the choice open: "Celery or
Temporal". Epic 1 picks Celery (rationale in docs/build-log.md). This module is
the seam that keeps that from being a one-way door.

Pipeline code from Epic 2 onward depends on the `Orchestrator` protocol, not on
`celery` directly. Swapping in Temporal later means writing one new
implementation of this protocol, not rewriting every pipeline stage.

**Why the pipeline persists after every stage.** Temporal's headline advantage
is durable execution: a workflow that dies at step 7 resumes at step 7 instead
of re-running steps 1-6. Celery has no such thing. But most of that benefit is
recoverable at the data layer, because §5.3 already requires each stage's
output to be persisted — CompetitorSet, PromptSet, EngineResult are durable
entities, not in-flight state. So each stage is written to (a) check whether
its output already exists and return it if so, and (b) write its output before
returning. A retried scan then skips the expensive LLM and engine calls it has
already paid for.

That is the trade: a modest amount of idempotency discipline in exchange for not
operating a Temporal cluster before the product has a single user.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Orchestrator(Protocol):
    """Minimal surface the scan pipeline needs from a job runner."""

    def enqueue(self, task_name: str, /, **kwargs: Any) -> str:
        """Schedule a task. Returns an opaque job id."""
        ...

    def result(self, job_id: str) -> Any:
        """Fetch a completed result, or None if still running."""
        ...

    def status(self, job_id: str) -> str:
        """One of: pending, running, succeeded, failed."""
        ...


class CeleryOrchestrator:
    """Celery-backed implementation."""

    def __init__(self, app: Any | None = None) -> None:
        if app is None:
            from .celery_app import celery_app

            app = celery_app
        self._app = app

    def enqueue(self, task_name: str, /, **kwargs: Any) -> str:
        # `send_task` publishes by name without importing the task, which is
        # what the API process needs — it must not import worker code. But
        # `send_task` also bypasses `task_always_eager`, so tests would need a
        # live broker. Dispatch through the registry when eager is on, keeping
        # pipeline code from Epic 2 onward unit-testable without Redis.
        if self._app.conf.task_always_eager and task_name in self._app.tasks:
            return str(self._app.tasks[task_name].apply(kwargs=kwargs).id)
        return str(self._app.send_task(task_name, kwargs=kwargs).id)

    def result(self, job_id: str) -> Any:
        async_result = self._app.AsyncResult(job_id)
        return async_result.result if async_result.ready() else None

    def status(self, job_id: str) -> str:
        state = self._app.AsyncResult(job_id).state
        return {
            "PENDING": "pending",
            "RECEIVED": "pending",
            "STARTED": "running",
            "RETRY": "running",
            "SUCCESS": "succeeded",
            "FAILURE": "failed",
            "REVOKED": "failed",
        }.get(state, "pending")

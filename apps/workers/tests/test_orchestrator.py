"""The orchestration seam.

product-spec.md §5.1 leaves "Celery or Temporal" open. Epic 1 chose Celery.
These tests pin the abstraction that keeps that from being a one-way door.
"""

from __future__ import annotations

from typing import Any

from celery import Celery

from avp_workers.orchestrator import CeleryOrchestrator, Orchestrator


def test_celery_orchestrator_satisfies_the_protocol() -> None:
    assert isinstance(CeleryOrchestrator(Celery("test")), Orchestrator)


def test_a_non_celery_implementation_also_satisfies_it() -> None:
    """The point of the seam: Temporal can be dropped in without touching
    pipeline code."""

    class FakeOrchestrator:
        def enqueue(self, task_name: str, /, **kwargs: Any) -> str:
            return "job-1"

        def result(self, job_id: str) -> Any:
            return {"ok": True}

        def status(self, job_id: str) -> str:
            return "succeeded"

    assert isinstance(FakeOrchestrator(), Orchestrator)


def test_eager_round_trip_without_a_broker() -> None:
    """Tasks execute correctly in-process, so tests need no live Redis."""
    app = Celery("test")
    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        # Eager results still round-trip through a backend when queried by id,
        # so an in-memory one is needed for status()/result() to resolve.
        result_backend="cache+memory://",
        task_store_eager_result=True,
    )

    @app.task(name="avp.test.double")
    def double(value: int) -> int:
        return value * 2

    orch = CeleryOrchestrator(app)
    job_id = orch.enqueue("avp.test.double", value=21)
    assert orch.status(job_id) == "succeeded"
    assert orch.result(job_id) == 42


def test_celery_states_map_to_the_protocol_vocabulary() -> None:
    """Celery's state names leak scheduler detail; the protocol normalises."""
    app = Celery("test")
    orch = CeleryOrchestrator(app)

    class FakeResult:
        def __init__(self, state: str) -> None:
            self.state = state

    app.AsyncResult = lambda job_id: FakeResult(job_id)  # type: ignore[assignment]

    assert orch.status("PENDING") == "pending"
    assert orch.status("STARTED") == "running"
    assert orch.status("RETRY") == "running"
    assert orch.status("SUCCESS") == "succeeded"
    assert orch.status("FAILURE") == "failed"
    assert orch.status("REVOKED") == "failed"
    # An unrecognised future state must not crash a status poll.
    assert orch.status("SOME_NEW_STATE") == "pending"

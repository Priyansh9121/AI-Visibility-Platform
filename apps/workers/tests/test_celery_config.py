"""Celery configuration.

Defaults matter here: every pipeline stage calls a slow, flaky, rate-limited
and expensive third-party API, and Celery's out-of-the-box settings are tuned
for none of that.
"""

from __future__ import annotations

import pytest

from avp_workers.celery_app import celery_app
from avp_workers.config import WorkerSettings


def test_pickle_is_not_accepted() -> None:
    """Celery's pickle support is RCE for anyone who can write to the broker."""
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"


def test_tasks_are_acknowledged_late() -> None:
    """A worker killed mid-scan must requeue its job, not drop it."""
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


def test_prefetch_is_one() -> None:
    """The default of 4 lets one worker hoard long scans while peers idle."""
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_retries_use_jittered_backoff() -> None:
    """Without jitter, rate-limited tasks retry in lockstep and re-burst."""
    assert celery_app.conf.task_retry_backoff is True
    assert celery_app.conf.task_retry_jitter is True
    assert celery_app.conf.task_max_retries == 3


def test_time_limits_bound_a_stuck_stage() -> None:
    """Epic 9 targets a full scan in under 5 minutes."""
    assert celery_app.conf.task_soft_time_limit == 600
    assert celery_app.conf.task_time_limit > celery_app.conf.task_soft_time_limit


def test_queues_are_separated_by_workload() -> None:
    """IO-bound engine calls must not starve CPU-bound audits."""
    routes = celery_app.conf.task_routes
    assert routes["avp.engine.*"]["queue"] == "engines"
    assert routes["avp.audit.*"]["queue"] == "audits"
    assert routes["avp.*"]["queue"] == "scans"


def test_health_tasks_are_registered() -> None:
    registered = {name for name in celery_app.tasks if name.startswith("avp.")}
    assert registered == {
        "avp.health.ping",
        "avp.health.echo",
        "avp.health.check_datastores",
    }


def test_broker_and_session_store_use_different_redis_databases() -> None:
    """Flushing a wedged queue must not sign every user out."""
    s = WorkerSettings.from_env()
    assert s.broker_url != s.redis_url
    assert s.result_backend != s.redis_url
    assert s.broker_url != s.result_backend
    assert s.broker_url.endswith("/1")
    assert s.result_backend.endswith("/2")


def test_settings_honour_explicit_overrides(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://elsewhere:6379/7")
    s = WorkerSettings.from_env()
    assert s.broker_url == "redis://elsewhere:6379/7"


def test_redis_timeouts_default_and_honour_env(monkeypatch) -> None:  # noqa: ANN001
    """A dead Redis must fail a worker's connect fast, not hang it."""
    monkeypatch.delenv("REDIS_CONNECT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("REDIS_SOCKET_TIMEOUT_SECONDS", raising=False)
    s = WorkerSettings.from_env()
    assert (s.redis_connect_timeout, s.redis_socket_timeout) == (2.0, 2.0)
    monkeypatch.setenv("REDIS_CONNECT_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT_SECONDS", "4")
    s = WorkerSettings.from_env()
    assert (s.redis_connect_timeout, s.redis_socket_timeout) == (1.5, 4.0)


def test_celery_bounds_connect_but_not_the_broker_read() -> None:
    """Connect is bounded on broker and result backend. The broker's blocking
    BRPOP/pub-sub reads get NO socket_timeout (kombu already bounds BRPOP at 1s;
    a socket timeout would fire on an idle listener)."""
    from avp_workers.config import settings

    broker = celery_app.conf.broker_transport_options
    assert broker["socket_connect_timeout"] == settings.redis_connect_timeout
    assert "socket_timeout" not in broker
    assert celery_app.conf.redis_socket_connect_timeout == settings.redis_connect_timeout


def test_health_check_redis_client_carries_the_timeouts(monkeypatch) -> None:  # noqa: ANN001
    import redis as redis_lib

    from avp_workers.config import settings
    from avp_workers.tasks import health

    seen: dict[str, object] = {}

    class _Fake:
        def ping(self) -> None: ...
        def close(self) -> None: ...

    def _from_url(url: str, **kwargs: object) -> _Fake:
        seen.update(kwargs)
        return _Fake()

    monkeypatch.setattr(redis_lib.Redis, "from_url", staticmethod(_from_url))
    health.check_datastores()
    assert seen["socket_connect_timeout"] == settings.redis_connect_timeout
    assert seen["socket_timeout"] == settings.redis_socket_timeout


@pytest.mark.parametrize("name", ["REDIS_CONNECT_TIMEOUT_SECONDS", "REDIS_SOCKET_TIMEOUT_SECONDS"])
@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf", "-inf"])
def test_a_timeout_that_disables_the_bound_is_rejected(name, bad, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError):
        WorkerSettings.from_env()

"""Redis connect and read timeouts: a slow or dead Redis must fail fast.

Without a socket timeout, redis-py waits on a silent socket forever, so sign-in
(rate limiter, then the session store) and every authenticated request hang
until the platform's own request timeout. With one, the call raises and the API
answers a 503 problem (fail closed) in a bounded time.

The "silent Redis" is a local TCP server that accepts connections and never
replies, so the READ timeout is exercised deterministically with no external
host. Every hang-prone call runs under a watchdog: a missing timeout fails the
test after WATCHDOG seconds instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import structlog
from httpx import AsyncClient
from pydantic import ValidationError
from redis.exceptions import RedisError, ResponseError

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.deps import redis_client

BASE = "/api/v1"
WATCHDOG = 15.0
BOUND = 5.0


async def _watched(coro: Any) -> Any:
    """Run `coro`, failing (not hanging) if it outlives the watchdog."""
    try:
        return await asyncio.wait_for(coro, WATCHDOG)
    except TimeoutError:
        pytest.fail(f"still hanging after {WATCHDOG}s: no Redis timeout is in force")


@pytest_asyncio.fixture
async def silent_redis_url() -> AsyncIterator[str]:
    """A TCP server that accepts and never answers."""
    held: list[asyncio.StreamWriter] = []

    async def _hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)
        with contextlib.suppress(ConnectionError, asyncio.CancelledError):
            await reader.read()  # never answered; returns only on client close

    server = await asyncio.start_server(_hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        for w in held:
            w.close()
        server.close()


@pytest_asyncio.fixture
async def fresh_pool() -> AsyncIterator[None]:
    redis_module._pool = None  # noqa: SLF001
    yield
    await redis_module.close_redis()


def test_defaults_are_short_but_not_tight() -> None:
    s = Settings(_env_file=None)
    assert s.redis_connect_timeout_seconds == 2
    assert s.redis_socket_timeout_seconds == 2


@pytest.mark.parametrize("name", ["redis_connect_timeout_seconds", "redis_socket_timeout_seconds"])
@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_a_timeout_that_disables_the_bound_is_rejected(name: str, bad: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: bad})


def test_pool_carries_the_timeouts_from_settings(fresh_pool: None) -> None:
    s = Settings(
        _env_file=None,
        redis_url="redis://127.0.0.1:6379/15",
        redis_connect_timeout_seconds=1.5,
        redis_socket_timeout_seconds=3.5,
    )
    kwargs = redis_module.get_redis(s).connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == 1.5
    assert kwargs["socket_timeout"] == 3.5
    assert kwargs["health_check_interval"] == 30


@pytest.mark.asyncio
async def test_a_silent_redis_raises_within_the_timeout(
    silent_redis_url: str, fresh_pool: None
) -> None:
    s = Settings(
        _env_file=None,
        redis_url=silent_redis_url,
        redis_connect_timeout_seconds=0.3,
        redis_socket_timeout_seconds=0.3,
    )
    r = redis_module.get_redis(s)
    start = time.monotonic()
    with pytest.raises(RedisError):
        await _watched(r.get("k"))
    assert time.monotonic() - start < BOUND


@pytest.mark.asyncio
async def test_login_and_me_answer_503_when_redis_is_silent(
    client: AsyncClient, silent_redis_url: str, settings: Settings, fresh_pool: None
) -> None:
    low = settings.model_copy(
        update={
            "redis_url": silent_redis_url,
            "redis_connect_timeout_seconds": 0.3,
            "redis_socket_timeout_seconds": 0.3,
        }
    )
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    app.dependency_overrides[redis_client] = lambda: redis_module.get_redis(low)
    client.cookies.set(settings.session_cookie_name, "some-session-token")
    with structlog.testing.capture_logs() as logs:
        for method, path, body in (
            ("POST", "/auth/login", {"email": "slow@redis.example", "password": "x" * 12}),
            ("GET", "/auth/me", None),
        ):
            start = time.monotonic()
            resp = await _watched(client.request(method, f"{BASE}{path}", json=body))
            assert time.monotonic() - start < BOUND, path
            assert resp.status_code == 503, (path, resp.text)
            assert resp.headers["content-type"].startswith("application/problem+json")
            assert resp.json()["type"] == "/problems/service-unavailable"
            assert silent_redis_url not in resp.text
            assert "127.0.0.1" not in resp.text
    assert silent_redis_url not in repr(logs)
    assert "slow@redis.example" not in repr(logs)


@pytest.mark.asyncio
async def test_me_answers_503_when_redis_refuses_connections(
    client: AsyncClient, settings: Settings, fresh_pool: None
) -> None:
    low = settings.model_copy(
        update={"redis_url": "redis://127.0.0.1:1/0", "redis_connect_timeout_seconds": 0.3}
    )
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    app.dependency_overrides[redis_client] = lambda: redis_module.get_redis(low)
    client.cookies.set(settings.session_cookie_name, "some-session-token")
    resp = await _watched(client.get(f"{BASE}/auth/me"))
    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "/problems/service-unavailable"
    assert "127.0.0.1" not in resp.text


@pytest.mark.asyncio
async def test_only_unreachable_redis_is_a_503_other_redis_errors_stay_500s(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Lua bug or a rotated password must not read as an outage."""
    from avp_api.sessions import SessionStore

    async def _boom(self: SessionStore, token: str) -> None:
        raise ResponseError("ERR some server-side text")

    monkeypatch.setattr(SessionStore, "get", _boom)
    client.cookies.set(settings.session_cookie_name, "some-session-token")
    with pytest.raises(ResponseError):
        await client.get(f"{BASE}/auth/me")

"""The two Redis lines the API writes about itself — K1, Redis hardening.

`redis.eviction_policy` at start, and `redis.data_lost` when the sentinel key is missing
(`avp_api/redis_watch.py`). Production's Redis holds the sessions, the rate-limit windows and the
Google sign-in state on one instance that a restart empties; nothing said so until these.

What is pinned, each by a test that fails without it:

* the policy is the one `CONFIG GET` returns, logged once as `policy`, after the memory line;
* anything else is `unknown`, and the start goes on: a refused command, a timeout, a dead
  connection, an odd reply, a malformed address; and the start waits no longer than one Redis
  call's own timeout;
* an empty Redis logs one `redis.data_lost` and gets the sentinel (no expiry, the time as its
  value); the next check says nothing; a deleted sentinel (a wipe) is exactly one more line, and
  two checks racing for it are one line;
* a wipe while the app keeps running is noticed on a later tick, with no new start;
* a check that cannot reach Redis is skipped quietly and tried again, and no error text (which can
  carry a host) reaches the log;
* after a restart the pool can be holding a connection that went silent, and the first command on
  it times out: the check tries once more, on the fresh connection that failure clears, so the
  wipe is reported on that tick and not a whole tick later (a real proxy in front of the test
  Redis plays the restart: it resets the connections, or leaves them open and answers nothing);
* the watcher is a task the shutdown cancels cleanly, before the connection pool is closed.

Variants of Redis's answer come from a small fake: `CONFIG SET` is never used on the real one, and
no real Redis can be told to refuse `CONFIG`. The sentinel itself is exercised on the real test
Redis, because "exactly one of two racers wins" is Redis's atomicity and not ours to fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import inspect
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError
from structlog.testing import capture_logs

from avp_api import main, redis_watch
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.logs import configure_logging
from avp_api.redis_watch import SENTINEL_KEY
from avp_api.services import memory_stats

WATCHDOG = 15.0
# A host-looking marker put inside every fake error, so a test can see that no error text is logged.
HOST = "10.0.0.7:6379"


def _lost(logs: list[dict[str, Any]]) -> int:
    return len([entry for entry in logs if entry["event"] == "redis.data_lost"])


def _policies(logs: list[dict[str, Any]]) -> list[str]:
    return [entry["policy"] for entry in logs if entry["event"] == "redis.eviction_policy"]


async def _until(predicate: Callable[[], bool], *, seconds: float = 5.0) -> None:
    """Wait for `predicate()`. A test that waits for something that never comes fails, not hangs."""
    try:
        async with asyncio.timeout(seconds):
            while not predicate():
                await asyncio.sleep(0.005)
    except TimeoutError:
        pytest.fail(f"still waiting after {seconds}s")


async def _nothing() -> None:
    return None


def _watchers() -> list[asyncio.Task[Any]]:
    return [task for task in asyncio.all_tasks() if task.get_name() == redis_watch.TASK_NAME]


@pytest_asyncio.fixture
async def redis_db(settings: Settings) -> AsyncIterator[Redis]:
    """The suite's test Redis (`AVP_TEST_REDIS_URL`), emptied before and after.

    The conftest empties it only after a test that asked for a datastore fixture, and these
    tests start from an empty Redis: an empty one is what makes the first check a loss.
    """
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.flushdb()
    yield redis
    await redis.flushdb()
    await redis.aclose()


@pytest_asyncio.fixture
async def fresh_pool() -> AsyncIterator[None]:
    """No shared pool when the test starts (`get_redis` would reuse one built for another URL)."""
    redis_module._pool = None  # noqa: SLF001
    yield
    await redis_module.close_redis()


@pytest_asyncio.fixture
async def silent_redis_url() -> AsyncIterator[str]:
    """A TCP server that accepts a connection and never answers: a Redis that is up and stuck."""
    held: list[asyncio.StreamWriter] = []

    async def _hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)
        with contextlib.suppress(ConnectionError, asyncio.CancelledError):
            await reader.read()  # never answered; returns only when the client closes

    server = await asyncio.start_server(_hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        for writer in held:
            writer.close()
        server.close()


@pytest.fixture
def debug_logging(settings: Settings) -> None:
    """structlog filtering at DEBUG, as `create_app` leaves it in the test environment.

    An earlier test may have left it at INFO, and a test that looks for a debug line would then
    be looking for a line nothing can write.
    """
    configure_logging(settings)


class FakeRedis:
    """The two calls the watcher makes, answered from a script.

    `config` is what `config_get` returns, or an exception it raises. `sets` is what each
    successive `set` returns (or raises); once it is used up, `set` returns None, the answer of an
    `NX` set whose key exists.
    """

    def __init__(self, config: object = None, sets: list[object] | None = None) -> None:
        self.config = config
        self.sets = list(sets or [])
        self.set_calls = 0

    async def config_get(self, pattern: str) -> object:
        assert pattern == "maxmemory-policy"
        if isinstance(self.config, BaseException):
            raise self.config
        return self.config

    async def set(self, key: str, value: str, *, nx: bool = False) -> object:  # noqa: ARG002
        self.set_calls += 1
        reply = self.sets.pop(0) if self.sets else None
        if isinstance(reply, BaseException):
            raise reply
        return reply


class SeveringProxy:
    """A TCP forwarder in front of the test Redis, so a test can kill the connections through it.

    That is what a Redis restart does to the connections a pool is holding, in the two ways it can:
    `sever` resets them (the client is told at once), and `go_silent` leaves them open and
    answers nothing (a host that vanished: the client learns only when a command times out).
    Connections made after either are forwarded normally: Redis is back.
    """

    def __init__(self, host: str, port: int) -> None:
        self._target = (host, port)
        self._writers: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None
        self._generation = 0  # connections of an earlier generation are the silent ones

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._forward, "127.0.0.1", 0)
        return int(self._server.sockets[0].getsockname()[1])

    async def _forward(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_reader, upstream_writer = await asyncio.open_connection(*self._target)
        self._writers += [writer, upstream_writer]
        born = self._generation

        async def pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
            with contextlib.suppress(ConnectionError, asyncio.CancelledError):
                while chunk := await source.read(65536):
                    if born != self._generation:
                        continue  # silent: read it, answer nothing
                    sink.write(chunk)
                    await sink.drain()
            sink.close()

        await asyncio.gather(pump(reader, upstream_writer), pump(upstream_reader, writer))

    def sever(self) -> None:
        for writer in self._writers:
            writer.transport.abort()

    def go_silent(self) -> None:
        self._generation += 1

    async def stop(self) -> None:
        self.sever()
        if self._server is not None:
            self._server.close()


@pytest_asyncio.fixture
async def pooled_through_a_proxy(
    settings: Settings, redis_db: Redis
) -> AsyncIterator[tuple[Redis, SeveringProxy]]:
    """A client built as `redis_client.get_redis` builds it (a pool, no retries, a health check
    every 30 s, only the timeouts shorter), reaching the test Redis through a proxy that can kill
    its connections."""
    target = urlsplit(settings.redis_url)
    proxy = SeveringProxy(target.hostname or "127.0.0.1", target.port or 6379)
    port = await proxy.start()
    pool = ConnectionPool.from_url(
        f"redis://127.0.0.1:{port}{target.path}",
        decode_responses=True,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
        health_check_interval=30,
    )
    try:
        yield Redis(connection_pool=pool), proxy
    finally:
        await pool.aclose()
        await proxy.stop()


# ---------------------------------------------------------------------------
# (a) the eviction policy
# ---------------------------------------------------------------------------


class TestTheEvictionPolicy:
    async def test_it_is_the_one_config_get_returns_logged_once_as_policy(
        self, redis_db: Redis
    ) -> None:
        expected = (await redis_db.config_get("maxmemory-policy"))["maxmemory-policy"]

        with capture_logs() as logs:
            returned = await redis_watch.log_eviction_policy(redis_db)

        assert returned == expected
        assert logs == [
            {"event": "redis.eviction_policy", "log_level": "info", "policy": expected}
        ]

    @pytest.mark.parametrize(
        "policy",
        ["noeviction", "allkeys-lru", "allkeys-lfu", "allkeys-random", "volatile-lru",
         "volatile-lfu", "volatile-random", "volatile-ttl"],
    )
    async def test_every_policy_redis_has_is_logged_as_it_is(self, policy: str) -> None:
        fake = FakeRedis(config={"maxmemory-policy": policy})

        with capture_logs() as logs:
            await redis_watch.log_eviction_policy(fake)

        assert _policies(logs) == [policy]

    @pytest.mark.parametrize(
        "config",
        [
            # A managed Redis refusing or renaming the command.
            ResponseError(f"ERR unknown command 'CONFIG' ({HOST})"),
            ResponseError(f"NOPERM this user has no permissions to run 'config' ({HOST})"),
            # Nobody there, or nobody answering.
            RedisConnectionError(f"Error 111 connecting to {HOST}. Connection refused."),
            RedisTimeoutError(f"Timeout reading from {HOST}"),
            OSError(f"[Errno 65] No route to host {HOST}"),
            # Anything else is "unknown" too: this must not raise.
            RuntimeError(f"something nobody planned for {HOST}"),
        ],
        ids=["refused", "no-permission", "connection", "timeout", "os-error", "anything-else"],
    )
    async def test_a_refusal_a_timeout_or_a_dead_connection_is_unknown_and_never_raises(
        self, config: Exception
    ) -> None:
        fake = FakeRedis(config=config)

        with capture_logs() as logs:
            await redis_watch.log_eviction_policy(fake)

        assert logs == [
            {"event": "redis.eviction_policy", "log_level": "info", "policy": "unknown"}
        ]
        # The error's text can carry the host: only the word "unknown" is logged.
        assert HOST not in repr(logs)

    @pytest.mark.parametrize(
        "reply",
        [
            None,
            {},
            [],
            ["maxmemory-policy", "noeviction"],
            "noeviction",
            {"maxmemory": "0"},
            {"maxmemory-policy": ""},
            {"maxmemory-policy": None},
            {"maxmemory-policy": 7},
            {"maxmemory-policy": b"noeviction"},
            {"maxmemory-policy": "NOEVICTION"},
            {"maxmemory-policy": "no eviction"},
            {"maxmemory-policy": "-lru"},
            {"maxmemory-policy": "noeviction\nmore"},
            {"maxmemory-policy": "a" * 33},
            {"maxmemory-policy": f"proxy error from {HOST}"},
        ],
        ids=lambda reply: repr(reply)[:40],
    )
    async def test_a_reply_that_is_not_a_policy_name_is_unknown(self, reply: object) -> None:
        fake = FakeRedis(config=reply)

        with capture_logs() as logs:
            await redis_watch.log_eviction_policy(fake)

        assert _policies(logs) == ["unknown"]
        assert HOST not in repr(logs)


class TestTheLifespanLogsIt:
    """Through `main.lifespan`, the real start-up hook, on a real pool."""

    async def test_it_comes_once_and_after_the_memory_line(
        self, settings: Settings, redis_db: Redis, fresh_pool: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(main, "dispose_engine", _nothing)
        # A fixed reading, so the memory line is there on any machine (macOS has no /proc).
        monkeypatch.setattr(memory_stats, "snapshot", lambda: {"process_tree_rss_mb": 97})
        expected = (await redis_db.config_get("maxmemory-policy"))["maxmemory-policy"]
        app = main.create_app(settings)

        with capture_logs() as logs:
            async with main.lifespan(app):
                pass

        assert _policies(logs) == [expected]
        events = [entry["event"] for entry in logs]
        assert events.index("app.memory_at_start") < events.index("redis.eviction_policy")

    async def test_a_redis_that_refuses_config_is_unknown_and_the_app_starts_and_watches(
        self, settings: Settings, redis_db: Redis, fresh_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def refuse(self: Redis, *args: object, **kwargs: object) -> object:
            raise ResponseError(f"ERR unknown command 'CONFIG' ({HOST})")

        monkeypatch.setattr(main, "dispose_engine", _nothing)
        monkeypatch.setattr(Redis, "config_get", refuse)
        app = main.create_app(settings)
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(app):
                reached = True
                # Not knowing the policy does not stop the watching: the sentinel is the same Redis.
                await _until(lambda: _lost(logs) == 1)

        assert reached
        assert _policies(logs) == ["unknown"]
        assert HOST not in repr(logs)

    async def test_nobody_listening_is_unknown_and_the_app_starts_anyway(
        self, settings: Settings, fresh_pool: None, monkeypatch: pytest.MonkeyPatch,
        debug_logging: None,
    ) -> None:
        monkeypatch.setattr(main, "dispose_engine", _nothing)
        dead = settings.model_copy(
            update={"redis_url": "redis://127.0.0.1:1/0", "redis_connect_timeout_seconds": 0.3}
        )
        app = main.create_app(dead)
        reached = False
        start = time.monotonic()

        with capture_logs() as logs:
            async with main.lifespan(app):
                reached = True
                # The watcher's first check fails too, and is skipped.
                await _until(
                    lambda: any(e["event"] == "redis.sentinel.check_skipped" for e in logs)
                )

        assert reached
        assert time.monotonic() - start < WATCHDOG
        assert _policies(logs) == ["unknown"]
        # Neither the policy read nor the sentinel check wrote anything above info, and no loss.
        levels = {e["log_level"] for e in logs if e["event"].startswith("redis.")}
        assert levels <= {"info", "debug"}
        assert _lost(logs) == 0
        assert "127.0.0.1" not in repr(logs)

    async def test_a_redis_that_never_answers_delays_the_start_by_about_its_read_timeout(
        self, settings: Settings, silent_redis_url: str, fresh_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main, "dispose_engine", _nothing)
        stuck = settings.model_copy(
            update={
                "redis_url": silent_redis_url,
                "redis_connect_timeout_seconds": 0.3,
                "redis_socket_timeout_seconds": 0.3,
            }
        )
        app = main.create_app(stuck)
        lifespan = main.lifespan(app)

        with capture_logs() as logs:
            began = time.monotonic()
            try:
                await asyncio.wait_for(lifespan.__aenter__(), WATCHDOG)
            except TimeoutError:
                pytest.fail("the start is hanging: no Redis timeout is in force")
            waited = time.monotonic() - began
            await asyncio.wait_for(lifespan.__aexit__(None, None, None), WATCHDOG)

        # One call's read timeout (0.3 s) and not the sentinel check on top of it: that runs in the
        # task. A generous ceiling, because a loaded machine is slow, but nowhere near the watchdog.
        assert waited < 1.5, waited
        assert _policies(logs) == ["unknown"]

    async def test_a_malformed_address_does_not_stop_the_app_starting(
        self, settings: Settings, fresh_pool: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(main, "dispose_engine", _nothing)
        broken = settings.model_copy(update={"redis_url": "not-a-redis-url"})
        app = main.create_app(broken)
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(app):
                reached = True
                assert _watchers() == [], "there is nothing to watch with"

        assert reached
        assert _policies(logs) == ["unknown"]


# ---------------------------------------------------------------------------
# (b) the sentinel
# ---------------------------------------------------------------------------


class TestTheSentinel:
    async def test_an_empty_redis_is_one_data_lost_line_and_the_sentinel_is_written(
        self, redis_db: Redis
    ) -> None:
        before = datetime.now(UTC)

        with capture_logs() as logs:
            wrote = await redis_watch.check_sentinel(redis_db)

        assert wrote is True
        # One warning and nothing else on it: no field, so nothing that could be sensitive.
        assert logs == [{"event": "redis.data_lost", "log_level": "warning"}]
        # The value is the ISO time it was written, and the key never expires.
        written = datetime.fromisoformat(await redis_db.get(SENTINEL_KEY))
        assert written.tzinfo is not None
        assert before - timedelta(seconds=1) <= written <= datetime.now(UTC) + timedelta(seconds=1)
        assert await redis_db.pttl(SENTINEL_KEY) == -1

    async def test_a_second_check_says_nothing_and_leaves_the_sentinel_alone(
        self, redis_db: Redis
    ) -> None:
        await redis_watch.check_sentinel(redis_db)
        first = await redis_db.get(SENTINEL_KEY)

        with capture_logs() as logs:
            wrote = await redis_watch.check_sentinel(redis_db)

        assert wrote is False
        assert logs == []
        assert await redis_db.get(SENTINEL_KEY) == first

    async def test_a_wipe_is_exactly_one_more_line_and_then_quiet_again(
        self, redis_db: Redis
    ) -> None:
        with capture_logs() as logs:
            await redis_watch.check_sentinel(redis_db)
            await redis_watch.check_sentinel(redis_db)
            assert _lost(logs) == 1

            await redis_db.flushdb()  # Redis restarted, empty
            assert await redis_watch.check_sentinel(redis_db) is True
            assert _lost(logs) == 2

            assert await redis_watch.check_sentinel(redis_db) is False
            assert await redis_watch.check_sentinel(redis_db) is False
            assert _lost(logs) == 2
        assert await redis_db.exists(SENTINEL_KEY) == 1

    async def test_deleting_only_the_sentinel_counts_as_a_wipe_too(self, redis_db: Redis) -> None:
        await redis_watch.check_sentinel(redis_db)
        await redis_db.set("rl:login:ip:abc", "1", ex=60)  # other data is not what is watched

        with capture_logs() as logs:
            await redis_db.delete(SENTINEL_KEY)
            await redis_watch.check_sentinel(redis_db)

        assert _lost(logs) == 1

    @pytest.mark.parametrize("racers", [2, 10])
    async def test_checks_racing_for_a_wiped_sentinel_are_one_line(
        self, settings: Settings, redis_db: Redis, racers: int
    ) -> None:
        """Each racer has its own client, as separate processes (a deploy's two instances) do."""
        url = settings.redis_url
        clients = [Redis.from_url(url, decode_responses=True) for _ in range(racers)]
        try:
            with capture_logs() as logs:
                results = await asyncio.gather(*(redis_watch.check_sentinel(c) for c in clients))
        finally:
            for c in clients:
                await c.aclose()

        assert sorted(results) == [False] * (racers - 1) + [True]
        assert _lost(logs) == 1

    async def test_the_first_check_after_a_restart_sees_the_wipe_though_its_connection_is_dead(
        self, redis_db: Redis, pooled_through_a_proxy: tuple[Redis, SeveringProxy]
    ) -> None:
        """What a restart really looks like from the pool: its idle connection is dead, and the
        pool does not retry, so the first command on it fails. The check must still say so on this
        tick, not the next one, or "within a minute" is two."""
        pooled, proxy = pooled_through_a_proxy

        with capture_logs() as logs:
            assert await redis_watch.check_sentinel(pooled) is True  # the first start
            assert _lost(logs) == 1

            await redis_db.flushdb()  # Redis comes back empty...
            proxy.sever()  # ...having dropped every connection it held
            await asyncio.sleep(0.05)  # the reset reaches the client

            assert await redis_watch.check_sentinel(pooled) is True
            assert _lost(logs) == 2
            assert await redis_watch.check_sentinel(pooled) is False  # and then quiet again
            assert _lost(logs) == 2

    async def test_a_connection_that_went_silent_in_the_restart_does_not_hide_the_wipe_a_tick(
        self, redis_db: Redis, pooled_through_a_proxy: tuple[Redis, SeveringProxy]
    ) -> None:
        """The harder restart: the host went away without resetting anything, so the pool still
        holds a connection that looks fine and answers nothing. The first command on it times out
        and the pool, which does not retry, hands that error up. A check that gave up there would
        notice the wipe a whole tick later (two minutes, not one); this one tries again, on the
        fresh connection the failure has already cleared."""
        pooled, proxy = pooled_through_a_proxy

        with capture_logs() as logs:
            assert await redis_watch.check_sentinel(pooled) is True  # the first start
            await redis_db.flushdb()  # Redis comes back empty...
            proxy.go_silent()  # ...and the connection the pool holds is dead to it

            assert await redis_watch.check_sentinel(pooled) is True
        assert _lost(logs) == 2

    async def test_a_check_that_cannot_reach_redis_is_skipped_quietly_and_the_next_one_tries(
        self, settings: Settings, debug_logging: None
    ) -> None:
        refused = RedisConnectionError(f"Error 111 connecting to {HOST}. Connection refused.")
        fake = FakeRedis(
            sets=[
                refused, refused,  # a dead connection is tried once more, and Redis is down
                RedisTimeoutError(f"Timeout reading from {HOST}"),
                RedisTimeoutError(f"Timeout reading from {HOST}"),
                # A refusal is an answer, and not tried again.
                ResponseError(f"OOM command not allowed when used memory > 'maxmemory'. {HOST}"),
                None,  # reachable again, and the sentinel is there
                True,  # reachable again, and it was missing
            ]
        )

        with capture_logs() as logs:
            for _ in range(5):
                await redis_watch.check_sentinel(fake)

        assert fake.set_calls == 7
        # The three failures are not warnings: at most a debug line, the error's class only.
        skipped = [e for e in logs if e["event"] == "redis.sentinel.check_skipped"]
        assert {e["log_level"] for e in skipped} <= {"debug"}
        assert [e["error"] for e in skipped] == [
            "ConnectionError", "TimeoutError", "ResponseError"
        ]
        assert [e["event"] for e in logs if e["log_level"] != "debug"] == ["redis.data_lost"]
        assert HOST not in repr(logs)

    async def test_a_dead_connection_is_tried_once_more_and_the_loss_is_reported_on_that_try(
        self,
    ) -> None:
        fake = FakeRedis(sets=[RedisConnectionError(f"Connection closed by {HOST}"), True])

        with capture_logs() as logs:
            assert await redis_watch.check_sentinel(fake) is True

        assert fake.set_calls == 2
        assert logs == [{"event": "redis.data_lost", "log_level": "warning"}]

    @pytest.mark.parametrize(
        "failure",
        [RedisConnectionError(HOST), RedisTimeoutError(HOST), OSError(HOST)],
        ids=["connection", "timeout", "os-error"],
    )
    async def test_it_is_one_more_try_and_no_more(self, failure: Exception) -> None:
        fake = FakeRedis(sets=[failure, failure, True])

        with capture_logs():
            assert await redis_watch.check_sentinel(fake) is False

        assert fake.set_calls == 2  # the True was never asked for: that is the next tick's

    async def test_a_refusal_is_an_answer_and_is_not_tried_again(self) -> None:
        fake = FakeRedis(sets=[ResponseError(f"OOM command not allowed ({HOST})"), True])

        with capture_logs():
            assert await redis_watch.check_sentinel(fake) is False

        assert fake.set_calls == 1

    async def test_nothing_the_check_can_meet_escapes_it(self) -> None:
        for error in (OSError("x"), RuntimeError("x"), ValueError("x"), RedisTimeoutError("x")):
            with capture_logs():
                assert await redis_watch.check_sentinel(FakeRedis(sets=[error])) is False


class TestTheWatcher:
    async def test_a_wipe_while_the_app_keeps_running_is_noticed_on_a_later_tick(
        self, redis_db: Redis
    ) -> None:
        with capture_logs() as logs:
            task = asyncio.create_task(redis_watch.watch_sentinel(redis_db, interval_s=0.02))
            try:
                await _until(lambda: _lost(logs) == 1)  # the first check, on an empty Redis
                await redis_db.flushdb()  # Redis restarts; the API does not
                await _until(lambda: _lost(logs) == 2)
                # And then nothing more, however long it goes on checking (about ten ticks).
                await asyncio.sleep(0.2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert _lost(logs) == 2

    async def test_it_keeps_going_through_errors_and_reports_the_loss_when_redis_is_back(
        self, debug_logging: None
    ) -> None:
        fake = FakeRedis(
            sets=[RedisConnectionError(HOST), RedisTimeoutError(HOST), True]
        )

        with capture_logs() as logs:
            task = asyncio.create_task(redis_watch.watch_sentinel(fake, interval_s=0.01))
            try:
                await _until(lambda: _lost(logs) == 1)
                await _until(lambda: fake.set_calls >= 6)  # and on, finding the key each time
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert _lost(logs) == 1
        assert task.cancelled()

    async def test_only_cancellation_ends_it(self, redis_db: Redis) -> None:
        task = asyncio.create_task(redis_watch.watch_sentinel(redis_db, interval_s=0.01))
        await asyncio.sleep(0.1)
        assert not task.done()

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()

    def test_the_default_interval_is_inside_a_minute(self) -> None:
        """"Noticed within a minute": the interval is 60 s, and both places it is used say so."""
        assert redis_watch.CHECK_INTERVAL_S == 60.0
        default = inspect.signature(redis_watch.watch_sentinel).parameters["interval_s"].default
        assert default == redis_watch.CHECK_INTERVAL_S


class TestTheLifespanWatchesAndLetsGo:
    async def test_a_redis_wipe_while_the_app_runs_is_noticed_by_the_running_app(
        self, settings: Settings, redis_db: Redis, fresh_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main, "dispose_engine", _nothing)
        monkeypatch.setattr(redis_watch, "CHECK_INTERVAL_S", 0.02)
        app = main.create_app(settings)

        with capture_logs() as logs:
            async with main.lifespan(app):
                await _until(lambda: _lost(logs) == 1)  # started against an empty Redis
                await redis_db.delete(SENTINEL_KEY)  # the wipe, with the app up
                await _until(lambda: _lost(logs) == 2)

        assert _lost(logs) == 2

    async def test_the_watcher_is_cancelled_cleanly_at_shutdown_before_the_pool_closes(
        self, settings: Settings, redis_db: Redis, fresh_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main, "dispose_engine", _nothing)
        order: list[str] = []
        closing = redis_module.close_redis

        async def watching_close() -> None:
            order.append(f"pool closed, watcher alive={any(not t.done() for t in _watchers())}")
            await closing()

        monkeypatch.setattr(main, "close_redis", watching_close)
        loop = asyncio.get_running_loop()
        reports: list[dict[str, Any]] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: reports.append(context))
        try:
            app = main.create_app(settings)
            async with main.lifespan(app):
                running = _watchers()
                assert len(running) == 1
                assert not running[0].done()
                watcher = running[0]

            assert watcher.cancelled()
            assert _watchers() == []
            # A task destroyed while pending, or an exception nobody retrieved, is reported when the
            # task is collected: collect it, and give the loop a turn to deliver the report.
            del running, watcher
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous)

        assert reports == []
        assert order == ["pool closed, watcher alive=False"]

    async def test_a_second_stop_and_a_stop_with_nothing_are_harmless(
        self, settings: Settings, redis_db: Redis, fresh_pool: None
    ) -> None:
        task = await redis_watch.start(settings)
        assert task is not None

        await redis_watch.stop(task)
        await redis_watch.stop(task)
        await redis_watch.stop(None)

        assert task.cancelled()

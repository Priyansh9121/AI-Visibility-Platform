"""One hung SerpApi request no longer costs a scan its SERP signal — 2026-10-03.

Production logged `competitors.serp_failed error_code=SERP_TIMEOUT` for a scan's
SERP queries: each had waited out the 25 s timeout. The measurement that followed
ran `serp.search` for real: 24 uncached searches, all ok, the slowest 6.93 s and
the p95 2.54 s. So 25 s was not too short, and a provider that answers in 1.5 s
does not take 25 s on several queries at once unless a connection hung. The fix is
a 10 s deadline on each attempt (connect 5 s, the other phases 9 s), ONE more attempt
after a 1 s pause on a new client, only where no answer came, a line for every retry,
and a failure line that says what kind of failure it was.

`search` is the real one. The provider is faked in two ways. Most tests script a
`MockTransport`, one step per request: an answer, or the exception a transport
raises when none came. Five run against a real local socket (the idiom of
`test_redis_timeouts.py`): a TCP server that accepts and never answers, or answers
only from its second connection, or answers a byte at a time and never finishes, and
a port nothing listens on. There httpx's own timeouts and the attempt deadline do the
enforcing and a retry is seen to be a second connection; nothing about the bound is
simulated. Their timeouts are scaled down; the shipped numbers are pinned in
`TestTheBoundIsTheMeasuredOne`.

Nothing here reaches SerpApi: the account is a free plan of 250 searches a month,
shared with production.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
import structlog

from avp_api.config import Settings
from avp_api.services import serp
from avp_api.services.serp import SerpResult, search, search_many

# `search_many` is imported here, by name, on purpose: conftest's autouse
# `stub_chain_externals` replaces `serp.search_many` for every test with a fake that
# never searches. The name bound at import is the real one.

API_KEY = "SERPKEYSENTINEL-9f3c1a77d2b84e55"
URL_WITH_KEY = f"https://serpapi.com/search.json?engine=google&q=x&api_key={API_KEY}"

# Measured 2026-10-03 through `serp.search`: the slowest of 24 real searches, and
# the p95. And the wait this module had before.
MEASURED_SLOWEST_S = 6.93
MEASURED_P95_S = 2.54
OLD_TIMEOUT_S = 25.0

# The shipped numbers, read before any test swaps them for small ones.
SHIPPED_PHASE_TIMEOUTS = serp.PHASE_TIMEOUTS
SHIPPED_PAUSE_S = serp.RETRY_PAUSE_S

# The same mechanism at a scale a test can wait for. A phase budget under the attempt
# deadline, as shipped, so that a stalled phase ends on its own timeout.
SMALL_PHASE_S = 0.15
SMALL_DEADLINE_S = 0.6
SMALL_PAUSE_S = 0.05
# What a run may take beyond its waits: building a client loads the CA bundle, and a
# loaded CI machine is slow. Small beside the 25 s that this is guarding against.
SLACK_S = 3.0
# A hang fails the test after this long instead of hanging the suite. The trickle tests
# are the ones that never end unless the deadline cuts them, so theirs is shorter.
WATCHDOG_S = 15.0
TRICKLE_WATCHDOG_S = 6.0

# The failures where no answer came: httpx's own timeouts, and the transport errors
# that are not timeouts (refused, reset, the server hung up).
NO_ANSWER: list[type[httpx.TransportError]] = [
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
]


def organic(*domains: str) -> dict[str, Any]:
    return {
        "organic_results": [
            {"position": i, "link": f"https://{d}/page", "title": f"Title {i}"}
            for i, d in enumerate(domains, start=1)
        ]
    }


def answer(*domains: str) -> httpx.Response:
    return httpx.Response(200, json=organic(*(domains or ("front.com", "kayako.com"))))


def install(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
    *,
    clients: list[httpx.AsyncClient] | None = None,
    timeouts: list[Any] | None = None,
) -> None:
    """Make every client `search` opens talk to `handler` instead of SerpApi.

    The client is the real one, built with the keyword arguments `search` passed, so
    the timeout it was given is the one the test sees. `clients` and `timeouts`
    collect what was built.
    """
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if timeouts is not None:
            timeouts.append(kwargs.get("timeout"))
        kwargs["transport"] = httpx.MockTransport(handler)
        client = real(*args, **kwargs)
        if clients is not None:
            clients.append(client)
        return client

    monkeypatch.setattr(serp.httpx, "AsyncClient", factory)


class FakeSerpApi:
    """SerpApi as `search` sees it: each request is answered by the next step of a script.

    A step is a response, or an exception to raise (what a transport does when no
    answer came). A request beyond the script fails the test, so "exactly N
    requests" is checked by the fake as well as by the count.
    """

    def __init__(self, *steps: httpx.Response | Exception) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.timeouts: list[Any] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert self.steps, "SerpApi was asked more times than the test scripted"
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSerpApi:
        install(monkeypatch, self.handler, clients=self.clients, timeouts=self.timeouts)
        return self


@pytest.fixture
def serp_settings() -> Settings:
    """A recognisable key, so its absence from a log can be asserted."""
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        serpapi_key=API_KEY,
    )


@pytest.fixture
def fast_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pause between attempts, short enough that a retry costs a test nothing."""
    monkeypatch.setattr(serp, "RETRY_PAUSE_S", 0.01)


@pytest.fixture
def small_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The phase budgets, the attempt deadline and the pause, at a scale a test can wait out."""
    monkeypatch.setattr(
        serp, "PHASE_TIMEOUTS", httpx.Timeout(SMALL_PHASE_S, connect=SMALL_PHASE_S)
    )
    monkeypatch.setattr(serp, "ATTEMPT_TIMEOUT_S", SMALL_DEADLINE_S)
    monkeypatch.setattr(serp, "RETRY_PAUSE_S", SMALL_PAUSE_S)


def failure_lines(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == "serp.attempts_failed"]


def retried_lines(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == "serp.retried"]


def assert_no_exception_detail(events: list[dict[str, Any]]) -> None:
    """No captured event carries a traceback or the exception itself.

    Either would render the exception's message, and an httpx error's message can carry
    the request URL, which carries the API key.
    """
    carrying = [e["event"] for e in events if "exc_info" in e or "exception" in e]
    assert carrying == []


# What each failure is called in the logs: a timeout by its phase, anything else by its
# class name. Shared by the failure line and the retry line.
KIND_CASES = [
    (httpx.ConnectTimeout, "connect"),
    (httpx.ReadTimeout, "read"),
    (httpx.WriteTimeout, "write"),
    (httpx.PoolTimeout, "pool"),
    (httpx.ConnectError, "ConnectError"),
    (httpx.ReadError, "ReadError"),
    (httpx.RemoteProtocolError, "RemoteProtocolError"),
]


# ---------------------------------------------------------------------------
# the numbers
# ---------------------------------------------------------------------------


class TestTheBoundIsTheMeasuredOne:
    def test_an_attempt_has_a_ten_second_deadline_and_phase_budgets_under_it(self) -> None:
        timeout = SHIPPED_PHASE_TIMEOUTS
        assert serp.ATTEMPT_TIMEOUT_S == 10.0
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
            5.0, 9.0, 9.0, 9.0,
        )

    def test_every_phase_budget_is_under_the_deadline_so_a_stalled_phase_names_itself(
        self,
    ) -> None:
        """Equal to it, or over it, the deadline would always fire first and every
        stall after the connect would read `total`, never `read`, `write` or `pool`."""
        timeout = SHIPPED_PHASE_TIMEOUTS
        for phase in (timeout.connect, timeout.read, timeout.write, timeout.pool):
            assert phase is not None
            assert phase < serp.ATTEMPT_TIMEOUT_S

    def test_ten_seconds_is_above_every_search_measured_and_below_the_old_wait(self) -> None:
        assert MEASURED_P95_S < MEASURED_SLOWEST_S < serp.ATTEMPT_TIMEOUT_S < OLD_TIMEOUT_S
        # The read budget too: the slowest search measured took 6.93 s end to end.
        assert MEASURED_SLOWEST_S < serp.PHASE_TIMEOUT_S

    def test_there_are_two_attempts_and_a_pause_of_one_second_between_them(self) -> None:
        assert serp.MAX_ATTEMPTS == 2
        assert SHIPPED_PAUSE_S == 1.0

    def test_the_worst_case_is_21_seconds_a_query_and_42_a_detection(self) -> None:
        assert serp.QUERY_CEILING_S == 10 + 1 + 10 == 21.0

        queries = serp.build_queries(
            brand_name="Acme", domain="acme.com", industry="project software", niche=None
        )
        assert len(queries) == 6
        concurrency = inspect.signature(search_many).parameters["concurrency"].default
        waves = math.ceil(len(queries) / concurrency)
        assert waves == 2
        # It was two waves of 25 s.
        assert waves * serp.QUERY_CEILING_S == 42.0
        assert waves * serp.QUERY_CEILING_S < waves * OLD_TIMEOUT_S == 50.0

    async def test_the_client_is_built_with_the_phase_budgets_not_httpxs_default(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """A dropped keyword argument would silently bring back httpx's own 5 s."""
        fake = FakeSerpApi(answer()).install(monkeypatch)

        await search("q", settings=serp_settings)

        assert fake.timeouts == [serp.PHASE_TIMEOUTS]
        assert fake.timeouts[0].read == serp.PHASE_TIMEOUT_S
        assert fake.timeouts[0] != httpx.Timeout(5.0)


# ---------------------------------------------------------------------------
# a request that got no answer is made once more
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_pause")
class TestAHungRequestIsTriedOnceMore:
    @pytest.mark.parametrize("failure", NO_ANSWER, ids=lambda c: c.__name__)
    async def test_a_first_attempt_that_got_no_answer_is_followed_by_one_that_does(
        self, failure: type[httpx.TransportError], monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings,
    ) -> None:
        fake = FakeSerpApi(failure("simulated"), answer("front.com", "kayako.com")).install(
            monkeypatch
        )

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.ok is True
        assert result.error_code is None
        assert [(h.domain, h.position) for h in result.hits] == [
            ("front.com", 1), ("kayako.com", 2),
        ]
        assert len(fake.requests) == 2
        # A query the retry rescued is not a failed query.
        assert failure_lines(events) == []

    async def test_each_attempt_is_a_new_client_so_a_new_connection(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = FakeSerpApi(httpx.ReadTimeout("simulated"), answer()).install(monkeypatch)

        await search("q", settings=serp_settings)

        assert len(fake.clients) == 2
        assert fake.clients[0] is not fake.clients[1]
        assert fake.timeouts == [serp.PHASE_TIMEOUTS, serp.PHASE_TIMEOUTS]

    async def test_the_retry_asks_the_same_question(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = FakeSerpApi(httpx.ReadTimeout("simulated"), answer()).install(monkeypatch)

        await search("acme alternatives", settings=serp_settings)

        first, second = fake.requests
        assert first.url == second.url
        assert second.url.params["q"] == "acme alternatives"

    async def test_the_retry_waits_out_the_pause_first(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        pause = 0.2
        monkeypatch.setattr(serp, "RETRY_PAUSE_S", pause)
        stamps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            stamps.append(time.perf_counter())
            if len(stamps) == 1:
                raise httpx.ReadTimeout("simulated", request=request)
            return answer()

        install(monkeypatch, handler)

        result = await search("q", settings=serp_settings)

        assert result.ok
        assert len(stamps) == 2
        assert stamps[1] - stamps[0] >= pause - 0.02

    async def test_the_answer_to_the_retry_is_judged_like_any_other(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """A rate limit that follows a timeout is a rate limit — and a third request is not made."""
        fake = FakeSerpApi(
            httpx.ReadTimeout("simulated"), httpx.Response(429, json={"error": "slow down"})
        ).install(monkeypatch)

        result = await search("q", settings=serp_settings)

        assert result.ok is False
        assert result.error_code == "SERP_RATE_LIMITED"
        assert len(fake.requests) == 2


@pytest.mark.usefixtures("fast_pause")
class TestWhenEveryAttemptFails:
    @pytest.mark.parametrize("failure", NO_ANSWER, ids=lambda c: c.__name__)
    async def test_two_failures_are_two_attempts_and_the_code_names_the_failure(
        self, failure: type[httpx.TransportError], monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings,
    ) -> None:
        fake = FakeSerpApi(failure("simulated"), failure("simulated")).install(monkeypatch)

        result = await search("q", settings=serp_settings)

        assert result.ok is False
        assert result.hits == []
        expected = "SERP_TIMEOUT" if issubclass(failure, httpx.TimeoutException) else (
            "SERP_UNREACHABLE"
        )
        assert result.error_code == expected
        # Exactly two: a third would be a loop, and would cost a search.
        assert len(fake.requests) == 2

    @pytest.mark.parametrize(
        ("first", "second", "code"),
        [
            (httpx.ReadTimeout, httpx.ConnectError, "SERP_UNREACHABLE"),
            (httpx.ConnectError, httpx.ReadTimeout, "SERP_TIMEOUT"),
        ],
        ids=["timeout-then-refused", "refused-then-timeout"],
    )
    async def test_the_code_is_the_last_failures(
        self, first: type[httpx.TransportError], second: type[httpx.TransportError], code: str,
        monkeypatch: pytest.MonkeyPatch, serp_settings: Settings,
    ) -> None:
        FakeSerpApi(first("simulated"), second("simulated")).install(monkeypatch)

        result = await search("q", settings=serp_settings)

        assert result.error_code == code


# ---------------------------------------------------------------------------
# an answer is never asked for twice
# ---------------------------------------------------------------------------

ANSWERS = [
    pytest.param(401, {"json": {"error": "Invalid API key."}}, "SERP_AUTH_FAILED", id="401"),
    pytest.param(429, {"json": {"error": "Too many requests."}}, "SERP_RATE_LIMITED", id="429"),
    pytest.param(500, {"content": b"upstream broke"}, "SERP_HTTP_500", id="500"),
    pytest.param(503, {"content": b"overloaded"}, "SERP_HTTP_503", id="503"),
    pytest.param(200, {"content": b"<html>not json</html>"}, "SERP_BAD_JSON", id="bad-json"),
    pytest.param(
        200, {"json": {"error": "Google hasn't returned any results."}}, "SERP_API_ERROR",
        id="error-payload",
    ),
]


@pytest.mark.usefixtures("fast_pause")
class TestAnAnswerIsNotRetried:
    @pytest.mark.parametrize(("status", "body", "code"), ANSWERS)
    async def test_one_request_and_the_code_it_always_had(
        self, status: int, body: dict[str, Any], code: str,
        monkeypatch: pytest.MonkeyPatch, serp_settings: Settings,
    ) -> None:
        fake = FakeSerpApi(httpx.Response(status, **body)).install(monkeypatch)

        result = await search("q", settings=serp_settings)

        assert result.ok is False
        assert result.error_code == code
        assert len(fake.requests) == 1
        assert fake.steps == []

    async def test_a_body_httpx_cannot_decode_is_an_answer_and_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """`DecodingError` is an httpx error but not a transport one: the server did speak."""
        fake = FakeSerpApi(httpx.DecodingError("simulated")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.ok is False
        assert result.error_code == "SERP_UNREACHABLE"
        assert len(fake.requests) == 1
        (line,) = failure_lines(events)
        assert (line["attempts"], line["kinds"]) == (1, ["DecodingError"])

    async def test_an_answer_that_is_not_a_failure_is_one_request_too(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = FakeSerpApi(answer("front.com")).install(monkeypatch)

        result = await search("q", settings=serp_settings)

        assert result.ok is True
        assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# the failure line: what kind it was, and nothing that is a secret
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_pause")
class TestTheFailureLineSaysWhatKindItWas:
    async def test_one_line_of_numbers_and_kinds_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        FakeSerpApi(httpx.ReadTimeout("simulated"), httpx.ReadTimeout("simulated")).install(
            monkeypatch
        )

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        (line,) = failure_lines(events)
        assert line["log_level"] == "warning"
        assert set(line) - {"event", "log_level"} == {"attempts", "elapsed_ms", "kinds"}
        assert line["attempts"] == 2
        assert line["kinds"] == ["read", "read"]
        assert isinstance(line["elapsed_ms"], int)
        assert line["elapsed_ms"] >= 0
        # And the result says what it always said.
        assert result.error_code == "SERP_TIMEOUT"

    @pytest.mark.parametrize(
        ("failure", "kind"),
        KIND_CASES,
        ids=lambda value: value.__name__ if isinstance(value, type) else value,
    )
    async def test_a_timeout_is_its_phase_and_anything_else_is_its_class_name(
        self, failure: type[httpx.TransportError], kind: str,
        monkeypatch: pytest.MonkeyPatch, serp_settings: Settings,
    ) -> None:
        FakeSerpApi(failure("simulated"), failure("simulated")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        (line,) = failure_lines(events)
        assert line["kinds"] == [kind, kind]

    async def test_attempts_that_failed_differently_are_each_named_in_order(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        FakeSerpApi(httpx.ReadTimeout("simulated"), httpx.ConnectError("simulated")).install(
            monkeypatch
        )

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        (line,) = failure_lines(events)
        assert line["kinds"] == ["read", "ConnectError"]

    async def test_elapsed_covers_both_attempts_and_the_pause(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        pause = 0.2
        monkeypatch.setattr(serp, "RETRY_PAUSE_S", pause)
        FakeSerpApi(httpx.ReadTimeout("simulated"), httpx.ReadTimeout("simulated")).install(
            monkeypatch
        )

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        (line,) = failure_lines(events)
        assert line["elapsed_ms"] >= (pause - 0.02) * 1000


@pytest.mark.usefixtures("fast_pause")
class TestARescuedRetryIsLogged:
    """A retry that worked is not a failure, and is still a second search on the plan.

    SerpApi may already have run, and billed, the search whose answer never arrived, so
    the extra searches against the shared plan have to be countable from the logs: one
    line, numbers and kinds only. `attempts` is how many were made; `kinds` is how the
    ones before the last failed.
    """

    async def test_one_line_of_numbers_and_kinds_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        FakeSerpApi(httpx.ReadTimeout("simulated"), answer()).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.ok is True
        (line,) = retried_lines(events)
        assert line["log_level"] == "info"
        assert set(line) - {"event", "log_level"} == {"attempts", "kinds", "elapsed_ms"}
        assert line["attempts"] == 2
        assert line["kinds"] == ["read"]
        assert isinstance(line["elapsed_ms"], int)
        assert line["elapsed_ms"] >= 0
        # Rescued, so not a failed query.
        assert failure_lines(events) == []

    @pytest.mark.parametrize(
        ("failure", "kind"),
        KIND_CASES,
        ids=lambda value: value.__name__ if isinstance(value, type) else value,
    )
    async def test_the_kind_is_how_the_first_attempt_failed(
        self, failure: type[httpx.TransportError], kind: str,
        monkeypatch: pytest.MonkeyPatch, serp_settings: Settings,
    ) -> None:
        FakeSerpApi(failure("simulated"), answer()).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        (line,) = retried_lines(events)
        assert line["kinds"] == [kind]

    async def test_a_retry_that_ends_in_an_error_answer_is_logged_too(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """The second search was spent whatever it said."""
        FakeSerpApi(
            httpx.ReadTimeout("simulated"), httpx.Response(429, json={"error": "slow down"})
        ).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.error_code == "SERP_RATE_LIMITED"
        (line,) = retried_lines(events)
        assert (line["attempts"], line["kinds"]) == (2, ["read"])
        assert failure_lines(events) == []

    async def test_a_query_that_needed_no_retry_logs_neither_line(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        FakeSerpApi(answer()).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        assert retried_lines(events) == []
        assert failure_lines(events) == []

    async def test_a_query_whose_retry_failed_too_logs_the_failure_line_and_not_this_one(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        FakeSerpApi(httpx.ReadTimeout("simulated"), httpx.ReadTimeout("simulated")).install(
            monkeypatch
        )

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        assert retried_lines(events) == []
        assert len(failure_lines(events)) == 1


@pytest.mark.usefixtures("fast_pause")
class TestTheKeyReachesNoLog:
    """Not in the line, not in any other, not on the result.

    Every writer a run can reach is read: structlog's events, stdout, stderr and
    stdlib logging. The exceptions here carry a message that names the full request
    URL, key and all, which is what an httpx error's text can do.
    """

    @staticmethod
    def _nothing_secret(
        everything: str, result: SerpResult, extra: tuple[str, ...] = ()
    ) -> None:
        for secret in (API_KEY, URL_WITH_KEY, *extra):
            assert secret not in everything, f"{secret!r} reached a log"
            assert secret not in repr(result), f"{secret!r} is on the result"
            assert secret not in repr(result.redacted()), f"{secret!r} is on its log view"

    @pytest.mark.parametrize("failure", NO_ANSWER, ids=lambda c: c.__name__)
    async def test_a_failed_search_logs_no_key_url_or_exception_message(
        self, failure: type[httpx.TransportError], monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings, capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        message = f"GET {URL_WITH_KEY} did not complete"
        FakeSerpApi(failure(message), failure(message)).install(monkeypatch)
        # The failures here are raised by the transport, so httpx writes nothing of its
        # own and everything down to DEBUG can be read.
        caplog.set_level("DEBUG")

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        captured = capsys.readouterr()
        everything = json.dumps(events, default=str) + captured.out + captured.err + caplog.text
        assert failure_lines(events), "the failure was not logged, so this proves nothing"
        assert_no_exception_detail(events)
        self._nothing_secret(everything, result, extra=(message,))

    @pytest.mark.parametrize("failure", NO_ANSWER, ids=lambda c: c.__name__)
    async def test_a_rescued_retry_logs_no_key_url_or_exception_message(
        self, failure: type[httpx.TransportError], monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings, capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        message = f"GET {URL_WITH_KEY} did not complete"
        FakeSerpApi(failure(message), answer()).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        captured = capsys.readouterr()
        # Stdlib logging at the levels this app runs at, as below: the second attempt
        # gets a response, and httpx's own INFO line for one names the whole URL.
        everything = json.dumps(events, default=str) + captured.out + captured.err + caplog.text
        assert retried_lines(events), "the retry was not logged, so this proves nothing"
        assert_no_exception_detail(events)
        self._nothing_secret(everything, result, extra=(message,))

    @pytest.mark.parametrize(("status", "body", "code"), ANSWERS)
    async def test_an_answer_that_is_a_failure_logs_no_key_either(
        self, status: int, body: dict[str, Any], code: str, monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings, capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The body echoes the request URL, as SerpApi does on some errors.
        if "json" in body:
            body = {"json": {**body["json"], "echo": URL_WITH_KEY}}
        else:
            body = {"content": body["content"] + URL_WITH_KEY.encode()}
        FakeSerpApi(httpx.Response(status, **body)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        captured = capsys.readouterr()
        # Stdlib logging is read at the levels this app runs at. httpx's own INFO line
        # for a response prints the whole URL, key included: `configure_logging` pins it
        # off, and `test_secrets_stay_out_of_logs.py` holds that with the root opened up.
        everything = json.dumps(events, default=str) + captured.out + captured.err + caplog.text
        assert result.error_code == code
        assert_no_exception_detail(events)
        self._nothing_secret(everything, result)


# ---------------------------------------------------------------------------
# the bound, against a provider that goes silent
# ---------------------------------------------------------------------------


def silent_but_honouring_the_read_timeout(
    seen: list[float],
) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
    """A SerpApi that takes the request and says nothing, as httpx's transport sees it.

    The real transport gives up after the `read` timeout the client set. This waits
    exactly that long and raises exactly what the transport raises, so what is measured
    is the budget `search` configured and not one the test chose.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(time.perf_counter())
        await asyncio.sleep(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("silent", request=request)

    return handler


@pytest.mark.usefixtures("small_timeouts")
class TestAQueryThatTimesOutTwiceIsBounded:
    async def test_it_costs_two_attempts_and_a_pause_and_not_more(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        seen: list[float] = []
        install(monkeypatch, silent_but_honouring_the_read_timeout(seen))
        floor = 2 * SMALL_PHASE_S + SMALL_PAUSE_S

        started = time.perf_counter()
        result = await asyncio.wait_for(search("q", settings=serp_settings), WATCHDOG_S)
        elapsed = time.perf_counter() - started

        assert result.ok is False
        assert result.error_code == "SERP_TIMEOUT"
        assert len(seen) == 2
        assert floor - 0.02 <= elapsed <= floor + SLACK_S, f"{elapsed:.2f}s against {floor:.2f}s"
        # The pause sat between the two, and was not spent twice.
        assert seen[1] - seen[0] >= SMALL_PHASE_S + SMALL_PAUSE_S - 0.02

    async def test_a_detections_six_queries_are_two_waves_of_that(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """Six queries, three at a time, every one hung: 2 x (2 attempts + a pause)."""
        seen: list[float] = []
        install(monkeypatch, silent_but_honouring_the_read_timeout(seen))
        per_query = 2 * SMALL_PHASE_S + SMALL_PAUSE_S

        started = time.perf_counter()
        results = await asyncio.wait_for(
            search_many([f"q{i}" for i in range(6)], settings=serp_settings), WATCHDOG_S
        )
        elapsed = time.perf_counter() - started

        assert [r.error_code for r in results] == ["SERP_TIMEOUT"] * 6
        assert len(seen) == 12
        assert 2 * per_query - 0.02 <= elapsed <= 2 * per_query + SLACK_S


@pytest.mark.usefixtures("small_timeouts")
class TestTheAttemptIsCutAtItsDeadline:
    """httpx's budgets are per network step; the deadline is the attempt's.

    A transport that ignores every timeout it is given is the extreme of a response that
    keeps arriving (the real-socket version is below): nothing httpx enforces ever fires,
    so only the deadline can end the attempt, and 21 s is a ceiling and not a hope.
    """

    async def test_a_transport_that_ignores_every_timeout_is_cut_twice_inside_the_ceiling(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        seen: list[float] = []

        async def never_answers(request: httpx.Request) -> httpx.Response:
            seen.append(time.perf_counter())
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        install(monkeypatch, never_answers)
        ceiling = 2 * SMALL_DEADLINE_S + SMALL_PAUSE_S

        started = time.perf_counter()
        with structlog.testing.capture_logs() as events:
            result = await asyncio.wait_for(search("q", settings=serp_settings), WATCHDOG_S)
        elapsed = time.perf_counter() - started

        assert result.ok is False
        assert result.error_code == "SERP_TIMEOUT"
        assert len(seen) == 2
        assert ceiling - 0.02 <= elapsed <= ceiling + SLACK_S, (
            f"{elapsed:.2f}s against {ceiling:.2f}s"
        )
        # Each attempt ran to its deadline, and the pause sat between them.
        assert seen[1] - seen[0] >= SMALL_DEADLINE_S + SMALL_PAUSE_S - 0.05
        (line,) = failure_lines(events)
        assert (line["attempts"], line["kinds"]) == (2, ["total", "total"])
        assert_no_exception_detail(events)

    async def test_a_cut_attempt_is_retried_like_any_other_and_the_second_can_answer(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        calls = 0

        async def hangs_once(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(3600)
            return answer("front.com", "kayako.com")

        install(monkeypatch, hangs_once)

        with structlog.testing.capture_logs() as events:
            result = await asyncio.wait_for(search("q", settings=serp_settings), WATCHDOG_S)

        assert result.ok is True
        assert [(h.domain, h.position) for h in result.hits] == [
            ("front.com", 1), ("kayako.com", 2),
        ]
        assert calls == 2
        (line,) = retried_lines(events)
        assert (line["attempts"], line["kinds"]) == (2, ["total"])
        assert failure_lines(events) == []

    async def test_a_phase_that_stalls_ends_on_its_own_timeout_and_not_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """The phase budgets sit under the deadline, so this reads `read` and not `total`."""
        seen: list[float] = []
        install(monkeypatch, silent_but_honouring_the_read_timeout(seen))

        with structlog.testing.capture_logs() as events:
            await asyncio.wait_for(search("q", settings=serp_settings), WATCHDOG_S)

        (line,) = failure_lines(events)
        assert line["kinds"] == ["read", "read"]

    async def test_an_outer_deadline_still_cuts_the_search_and_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """The scan's own deadline is an `asyncio.timeout` around the whole chain.

        It reaches this code as a cancellation, which is not a failure to retry. If the
        search swallowed it the scan could not be stopped from here.
        """
        calls = 0

        async def never_answers(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        install(monkeypatch, never_answers)

        # Swallowed, the search would go on, retry and answer with a result, and this
        # block would end without raising.
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(SMALL_DEADLINE_S / 3):
                await search("q", settings=serp_settings)

        assert calls <= 1, "the cancelled search was tried again"


@pytest.mark.usefixtures("fast_pause")
class TestSearchManyStillRunsThreeAtOnce:
    def test_the_default_is_three(self) -> None:
        assert inspect.signature(search_many).parameters["concurrency"].default == 3

    async def test_three_requests_are_in_flight_and_never_a_fourth(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        in_flight = 0
        peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.03)
            finally:
                in_flight -= 1
            return answer("front.com")

        install(monkeypatch, handler)

        results = await search_many([f"q{i}" for i in range(9)], settings=serp_settings)

        assert len(results) == 9
        assert all(r.ok for r in results)
        assert peak == 3

    async def test_a_retry_stays_inside_its_slot(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """Every query fails once and is rescued; still never more than three at once."""
        attempts: dict[str, int] = {}
        in_flight = 0
        peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            query = request.url.params["q"]
            attempts[query] = attempts.get(query, 0) + 1
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.02)
                if attempts[query] == 1:
                    raise httpx.ReadTimeout("simulated", request=request)
            finally:
                in_flight -= 1
            return answer("front.com")

        install(monkeypatch, handler)

        results = await search_many([f"q{i}" for i in range(6)], settings=serp_settings)

        assert all(r.ok for r in results)
        assert list(attempts.values()) == [2] * 6
        assert peak <= 3


# ---------------------------------------------------------------------------
# a real connection that hangs
# ---------------------------------------------------------------------------


class LocalSerpApi:
    """A TCP server standing in for SerpApi: it accepts, and says nothing.

    From connection number `answers_from` on it answers every request with a small
    result page instead, so "the first connection hung and the second did not" is a
    thing it can be told to do. With `trickle_every` it does not stay silent: it sends
    the response's headers at once and then one byte of a body that never ends, every
    `trickle_every` seconds, so that every read completes inside httpx's budget and the
    response never does. `connections` counts what it accepted, `bytes_trickled` what
    it dripped.
    """

    def __init__(
        self, *, answers_from: int | None = None, trickle_every: float | None = None
    ) -> None:
        self.answers_from = answers_from
        self.trickle_every = trickle_every
        self.connections = 0
        self.bytes_trickled = 0
        self.url = ""
        self._writers: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        mine = self.connections
        self._writers.append(writer)
        with contextlib.suppress(
            ConnectionError, asyncio.CancelledError, asyncio.IncompleteReadError
        ):
            await reader.readuntil(b"\r\n\r\n")  # the request, in full
            if self.answers_from is not None and mine >= self.answers_from:
                body = json.dumps(organic("front.com", "kayako.com")).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n" + body
                )
                await writer.drain()
                writer.close()
                return
            if self.trickle_every is not None:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 1000000\r\n\r\n"
                )
                while not writer.is_closing():
                    writer.write(b" ")
                    await writer.drain()
                    self.bytes_trickled += 1
                    await asyncio.sleep(self.trickle_every)
                return
            await reader.read()  # no answer, until the client gives up and closes

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/search.json"

    def stop(self) -> None:
        for writer in self._writers:
            writer.close()
        if self._server is not None:
            self._server.close()


def use_a_real_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every client `search` opens gets httpx's own transport, which enforces timeouts.

    Handing the client a transport of its own also keeps a proxy in a developer's
    environment from taking a request meant for localhost. The client's timeout is
    still the one `search` passed.
    """
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.AsyncHTTPTransport()
        return real(*args, **kwargs)

    monkeypatch.setattr(serp.httpx, "AsyncClient", factory)


@contextlib.asynccontextmanager
async def local_serpapi(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers_from: int | None = None,
    trickle_every: float | None = None,
) -> AsyncIterator[LocalSerpApi]:
    """`search` pointed at a `LocalSerpApi`, over a real httpx transport."""
    server = LocalSerpApi(answers_from=answers_from, trickle_every=trickle_every)
    await server.start()
    monkeypatch.setattr(serp, "SERPAPI_URL", server.url)
    use_a_real_transport(monkeypatch)
    try:
        yield server
    finally:
        server.stop()


@pytest.mark.usefixtures("small_timeouts")
class TestAgainstAConnectionThatHangs:
    async def test_a_silent_server_costs_two_connections_and_two_read_timeouts(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        floor = 2 * SMALL_PHASE_S + SMALL_PAUSE_S
        async with local_serpapi(monkeypatch) as server:
            started = time.perf_counter()
            with structlog.testing.capture_logs() as events:
                result = await asyncio.wait_for(search("q", settings=serp_settings), WATCHDOG_S)
            elapsed = time.perf_counter() - started

        assert result.ok is False
        assert result.error_code == "SERP_TIMEOUT"
        # The retry was a second connection, and there was no third.
        assert server.connections == 2
        assert floor - 0.02 <= elapsed <= floor + SLACK_S, f"{elapsed:.2f}s against {floor:.2f}s"
        (line,) = failure_lines(events)
        assert line["attempts"] == 2
        # The server took the request and said nothing: the read gave up, not the connect.
        assert line["kinds"] == ["read", "read"]
        assert floor * 1000 - 20 <= line["elapsed_ms"] <= (floor + SLACK_S) * 1000

    async def test_a_hung_first_connection_is_rescued_by_a_second_one(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        async with local_serpapi(monkeypatch, answers_from=2) as server:
            started = time.perf_counter()
            with structlog.testing.capture_logs() as events:
                result = await asyncio.wait_for(search("q", settings=serp_settings), WATCHDOG_S)
            elapsed = time.perf_counter() - started

        assert result.ok is True, result.error_code
        assert [(h.domain, h.position) for h in result.hits] == [
            ("front.com", 1), ("kayako.com", 2),
        ]
        assert server.connections == 2
        assert SMALL_PHASE_S + SMALL_PAUSE_S - 0.02 <= elapsed <= (
            SMALL_PHASE_S + SMALL_PAUSE_S + SLACK_S
        )
        assert failure_lines(events) == []
        # The rescue cost a second search, and is on the record.
        (line,) = retried_lines(events)
        assert (line["attempts"], line["kinds"]) == (2, ["read"])

    async def test_a_response_that_trickles_a_byte_at_a_time_is_cut_at_the_attempt_deadline(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """Every read finishes inside httpx's budget; only the deadline can end the attempt."""
        ceiling = 2 * SMALL_DEADLINE_S + SMALL_PAUSE_S
        async with local_serpapi(monkeypatch, trickle_every=0.03) as server:
            started = time.perf_counter()
            with structlog.testing.capture_logs() as events:
                result = await asyncio.wait_for(
                    search("q", settings=serp_settings), TRICKLE_WATCHDOG_S
                )
            elapsed = time.perf_counter() - started

        assert result.ok is False
        assert result.error_code == "SERP_TIMEOUT"
        # The retry was a second connection, and the query ended inside its ceiling.
        assert server.connections == 2
        assert ceiling - 0.02 <= elapsed <= ceiling + SLACK_S, (
            f"{elapsed:.2f}s against {ceiling:.2f}s"
        )
        (line,) = failure_lines(events)
        assert (line["attempts"], line["kinds"]) == (2, ["total", "total"])
        # And it really was arriving: this was not a stall, which would have read `read`.
        assert server.bytes_trickled > 10

    async def test_a_trickling_first_connection_is_rescued_by_a_second_one(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        async with local_serpapi(monkeypatch, answers_from=2, trickle_every=0.03) as server:
            started = time.perf_counter()
            with structlog.testing.capture_logs() as events:
                result = await asyncio.wait_for(
                    search("q", settings=serp_settings), TRICKLE_WATCHDOG_S
                )
            elapsed = time.perf_counter() - started

        assert result.ok is True, result.error_code
        assert server.connections == 2
        assert SMALL_DEADLINE_S + SMALL_PAUSE_S - 0.02 <= elapsed <= (
            SMALL_DEADLINE_S + SMALL_PAUSE_S + SLACK_S
        )
        (line,) = retried_lines(events)
        assert (line["attempts"], line["kinds"]) == (2, ["total"])
        assert failure_lines(events) == []

    async def test_a_refused_connection_is_unreachable_after_two_attempts(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """Nothing listens: a real `ConnectError`, tried again, then named for what it is."""
        monkeypatch.setattr(serp, "SERPAPI_URL", "http://127.0.0.1:1/search.json")
        use_a_real_transport(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await asyncio.wait_for(search("q", settings=serp_settings), WATCHDOG_S)

        assert result.ok is False
        assert result.error_code == "SERP_UNREACHABLE"
        (line,) = failure_lines(events)
        assert line["attempts"] == 2
        assert line["kinds"] == ["ConnectError", "ConnectError"]

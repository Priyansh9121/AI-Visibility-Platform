"""A line for every competitor search SerpApi answered — 2026-10-03.

The logs already said when a search failed (`serp.attempts_failed`) and when a retry
rescued one (`serp.retried`). Neither says how long a search that worked took, so the
latency distribution of production's searches was not visible, and the budgets in
`serp.py` (10 s an attempt, 9 s a read) cannot be judged without it. `serp.completed` is
that line: one for each query SerpApi answered, whatever it answered, with five facts.

    elapsed_ms   from the first attempt's start to the end of parsing the answer
    attempts     1, or 2 when the first attempt got no answer and the retry did
    hits         the organic hits parsed (0 for an error answer)
    status       the HTTP status SerpApi sent
    error_code   None when the answer was a result page, else this module's code for it

Nothing else rides along: not the query, the market (`gl`), `hl`, `num`, `engine`, the
key, the URL, the response body or an exception's message. The URL carries the API key
and the query is the client's brand. A query that got no answer keeps its
`serp.attempts_failed` line and logs no `serp.completed`; a missing key sends nothing and
logs neither. So a query that reached SerpApi has exactly one of the two lines.

The provider is faked as in `test_serp_search.py`: a `MockTransport` scripted one step per
request, an answer or the exception a transport raises when none came. Nothing here
reaches SerpApi: the account is a free plan of 250 searches a month, shared with
production.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import structlog
from pydantic import SecretStr

from avp_api.config import Settings
from avp_api.services import serp
from avp_api.services.serp import search, search_many

# `search` and `search_many` are imported here, by name, on purpose: conftest's autouse
# `stub_chain_externals` replaces `serp.search_many` for every test with a fake that never
# searches. The name bound at import is the real one (as in test_serp_search.py).

API_KEY = "SERPKEYSENTINEL-6b0d3e91a4c75f28"
URL_WITH_KEY = f"https://serpapi.com/search.json?engine=google&q=x&api_key={API_KEY}"
# What a query, a market, an error body and an exception's message look like when each is
# recognisable, so that its absence from a log line can be asserted.
QUERY = "ZQXSENTINEL helpdesk alternatives"
MARKET = "nz"
BODY_SENTINEL = "BODYSENTINEL-41c8e7"
MESSAGE_SENTINEL = "MESSAGESENTINEL-9a2d"

COMPLETED = "serp.completed"
FAILED = "serp.attempts_failed"
RETRIED = "serp.retried"

# The five facts and nothing else; structlog adds `event` and `log_level` itself.
COMPLETED_FACTS = {"elapsed_ms", "attempts", "hits", "status", "error_code"}

# What a loaded CI machine may add to a wait: small beside the budgets this guards.
SLACK_S = 3.0
# The attempt deadline and the phase budgets at a scale a test can wait out.
SMALL_PHASE_S = 0.15
SMALL_DEADLINE_S = 0.6
SMALL_PAUSE_S = 0.05

# The failures where no answer came, as in test_serp_search.py.
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

# An answer that says no, and the code this module has always given it:
# (HTTP status, response arguments, error_code).
ERROR_ANSWERS = [
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
) -> None:
    """Make every client `search` opens talk to `handler` instead of SerpApi."""
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(serp.httpx, "AsyncClient", factory)


class FakeSerpApi:
    """SerpApi as `search` sees it: each request is answered by the next step of a script.

    A step is a response, or an exception to raise (what a transport does when no answer
    came). A request beyond the script fails the test, so "exactly N requests" is checked
    by the fake as well as by the count.
    """

    def __init__(self, *steps: httpx.Response | Exception) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert self.steps, "SerpApi was asked more times than the test scripted"
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSerpApi:
        install(monkeypatch, self.handler)
        return self


def settings_with_key(key: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        serpapi_key=key,
    )


@pytest.fixture
def serp_settings() -> Settings:
    """A recognisable key, so its absence from a log line can be asserted."""
    return settings_with_key(API_KEY)


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


def lines(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == name]


def serp_events(events: list[dict[str, Any]]) -> list[str]:
    """The name of every `serp.*` event, in the order it was logged."""
    return [e["event"] for e in events if e["event"].startswith("serp.")]


def assert_the_five_facts(line: dict[str, Any]) -> None:
    """The line is `serp.completed`, at info, with its five facts, as the types it promises."""
    assert line["event"] == COMPLETED
    assert line["log_level"] == "info"
    assert set(line) - {"event", "log_level"} == COMPLETED_FACTS
    for fact in ("elapsed_ms", "attempts", "hits", "status"):
        # `type(...) is int`: a bool, a float or a string would not be one.
        assert type(line[fact]) is int, f"{fact} is {line[fact]!r}"
    assert line["elapsed_ms"] >= 0
    assert line["attempts"] in (1, 2)
    assert line["error_code"] is None or isinstance(line["error_code"], str)


# ---------------------------------------------------------------------------
# an answer
# ---------------------------------------------------------------------------


class TestAnAnsweredQueryLogsOneLine:
    async def test_a_result_page_logs_one_completed_line_of_five_facts(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        fake = FakeSerpApi(answer("front.com", "kayako.com", "zendesk.com")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.ok is True
        assert len(result.hits) == 3
        assert len(fake.requests) == 1
        (line,) = lines(events, COMPLETED)
        assert_the_five_facts(line)
        assert line["attempts"] == 1
        assert line["hits"] == 3
        assert line["status"] == 200
        assert line["error_code"] is None
        # The only serp line: nothing was retried, nothing failed.
        assert serp_events(events) == [COMPLETED]

    async def test_hits_are_the_parsed_hits_and_not_the_raw_results(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """A result with no usable link, or on a host with no public suffix, is not a hit."""
        payload = {
            "organic_results": [
                {"position": 1, "link": "https://front.com/a", "title": "A"},
                {"position": 2, "link": "http://127.0.0.1/b", "title": "B"},
                {"position": 3, "link": "", "title": "C"},
                {"position": 4, "title": "D"},
                {"position": 5, "link": "https://kayako.com/e", "title": "E"},
            ]
        }
        FakeSerpApi(httpx.Response(200, json=payload)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert [h.domain for h in result.hits] == ["front.com", "kayako.com"]
        (line,) = lines(events, COMPLETED)
        assert line["hits"] == 2

    @pytest.mark.parametrize(
        "payload",
        [{}, {"organic_results": []}, {"organic_results": None}],
        ids=["no-key", "empty", "null"],
    )
    async def test_a_page_with_no_results_is_hits_zero_and_still_not_an_error(
        self, payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """`hits=0` with `error_code=None` is an answer that ranked nothing."""
        FakeSerpApi(httpx.Response(200, json=payload)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.ok is True
        (line,) = lines(events, COMPLETED)
        assert_the_five_facts(line)
        assert (line["hits"], line["status"], line["error_code"]) == (0, 200, None)


class TestElapsedMs:
    async def test_it_runs_from_the_first_attempt_to_the_answer(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        waited = 0.2

        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(waited)
            return answer()

        install(monkeypatch, slow)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        (line,) = lines(events, COMPLETED)
        assert (waited - 0.02) * 1000 <= line["elapsed_ms"] <= (waited + SLACK_S) * 1000

    async def test_it_includes_the_time_spent_parsing_the_answer(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """The transport answers at once, so the only time to count is the parsing."""
        per_hit = 0.05
        real = serp.registrable_domain

        def slow_domain(url: str) -> str:
            time.sleep(per_hit)
            return real(url)

        monkeypatch.setattr(serp, "registrable_domain", slow_domain)
        FakeSerpApi(answer("a.com", "b.com", "c.com", "d.com")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        (line,) = lines(events, COMPLETED)
        assert line["hits"] == 4
        assert (4 * per_hit - 0.02) * 1000 <= line["elapsed_ms"] <= (4 * per_hit + SLACK_S) * 1000

    async def test_on_a_retried_query_it_covers_the_first_attempt_and_the_pause(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """It starts at the first attempt, not at the one that was answered."""
        first_wait = 0.15
        pause = 0.2
        monkeypatch.setattr(serp, "RETRY_PAUSE_S", pause)
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(first_wait)
                raise httpx.ReadTimeout("simulated", request=request)
            return answer()

        install(monkeypatch, handler)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        assert calls == 2
        (line,) = lines(events, COMPLETED)
        assert (first_wait + pause - 0.03) * 1000 <= line["elapsed_ms"]
        assert line["elapsed_ms"] <= (first_wait + pause + SLACK_S) * 1000


# ---------------------------------------------------------------------------
# an answer to the retry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_pause")
class TestAQueryAnsweredOnTheRetry:
    @pytest.mark.parametrize(
        "failure", [httpx.ReadTimeout, httpx.ConnectError], ids=lambda c: c.__name__
    )
    async def test_the_retry_line_then_one_completed_line_with_two_attempts(
        self, failure: type[httpx.TransportError], monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings,
    ) -> None:
        fake = FakeSerpApi(failure("simulated"), answer("front.com", "kayako.com")).install(
            monkeypatch
        )

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.ok is True
        assert len(fake.requests) == 2
        # `serp.retried` stays as it was, and is followed by the one line for the answer.
        assert serp_events(events) == [RETRIED, COMPLETED]
        (retried,) = lines(events, RETRIED)
        (completed,) = lines(events, COMPLETED)
        assert_the_five_facts(completed)
        assert retried["attempts"] == completed["attempts"] == 2
        assert (completed["hits"], completed["status"], completed["error_code"]) == (2, 200, None)
        # Both are counted from the first attempt, and the second is read later.
        assert completed["elapsed_ms"] >= retried["elapsed_ms"]

    async def test_a_retry_that_ends_in_an_error_answer_is_one_completed_line_with_its_code(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """The second search was spent whatever it said, and it said no."""
        FakeSerpApi(
            httpx.ReadTimeout("simulated"), httpx.Response(429, json={"error": "slow down"})
        ).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.error_code == "SERP_RATE_LIMITED"
        assert serp_events(events) == [RETRIED, COMPLETED]
        (line,) = lines(events, COMPLETED)
        assert_the_five_facts(line)
        assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (
            2, 0, 429, "SERP_RATE_LIMITED",
        )


@pytest.mark.usefixtures("small_timeouts")
class TestAnAttemptCutAtItsDeadlineAndThenAnswered:
    async def test_it_is_two_attempts_and_the_line_counts_the_whole_cut_attempt(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        calls = 0

        async def hangs_once(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(3600)
            return answer()

        install(monkeypatch, hangs_once)

        with structlog.testing.capture_logs() as events:
            result = await asyncio.wait_for(search("q", settings=serp_settings), 15.0)

        assert result.ok is True
        assert calls == 2
        assert serp_events(events) == [RETRIED, COMPLETED]
        (retried,) = lines(events, RETRIED)
        assert retried["kinds"] == ["total"]
        (line,) = lines(events, COMPLETED)
        assert line["attempts"] == 2
        # The cut attempt ran to its deadline, and all of it is in the line.
        assert line["elapsed_ms"] >= (SMALL_DEADLINE_S - 0.03) * 1000


# ---------------------------------------------------------------------------
# an answer that says no
# ---------------------------------------------------------------------------


class TestEveryErrorAnswerIsLoggedToo:
    @pytest.mark.parametrize(("status", "body", "code"), ERROR_ANSWERS)
    async def test_one_completed_line_with_no_hits_its_status_and_its_code(
        self, status: int, body: dict[str, Any], code: str,
        monkeypatch: pytest.MonkeyPatch, serp_settings: Settings,
    ) -> None:
        fake = FakeSerpApi(httpx.Response(status, **body)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        # What the caller gets is what it always got.
        assert result.ok is False
        assert result.error_code == code
        assert len(fake.requests) == 1
        (line,) = lines(events, COMPLETED)
        assert_the_five_facts(line)
        assert line["attempts"] == 1
        assert line["hits"] == 0
        assert line["status"] == status
        assert line["error_code"] == code
        assert lines(events, FAILED) == []
        assert lines(events, RETRIED) == []

    @pytest.mark.parametrize(
        ("status", "existing_line"),
        [(401, "serp.auth_failed"), (500, "serp.http_error")],
        ids=["401", "500"],
    )
    async def test_the_line_an_error_answer_already_had_is_still_logged_before_it(
        self, status: int, existing_line: str, monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings,
    ) -> None:
        FakeSerpApi(httpx.Response(status, content=b"no")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        assert serp_events(events) == [existing_line, COMPLETED]
        (existing,) = lines(events, existing_line)
        if status == 500:
            assert existing["status"] == 500
            assert existing["log_level"] == "warning"
        else:
            assert existing["log_level"] == "error"


# ---------------------------------------------------------------------------
# no answer
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_pause")
class TestAQueryThatGotNoAnswerKeepsItsFailureLineAndLogsNoCompletedOne:
    @pytest.mark.parametrize("failure", NO_ANSWER, ids=lambda c: c.__name__)
    async def test_two_attempts_with_no_answer_log_the_failure_line_alone(
        self, failure: type[httpx.TransportError], monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings,
    ) -> None:
        fake = FakeSerpApi(failure("simulated"), failure("simulated")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.ok is False
        assert len(fake.requests) == 2
        assert serp_events(events) == [FAILED]
        (line,) = lines(events, FAILED)
        assert line["attempts"] == 2

    async def test_a_body_httpx_cannot_decode_is_the_failure_line_alone(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """httpx raised before there was a response, so there is no status to log."""
        FakeSerpApi(httpx.DecodingError("simulated")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=serp_settings)

        assert result.error_code == "SERP_UNREACHABLE"
        assert serp_events(events) == [FAILED]
        (line,) = lines(events, FAILED)
        assert (line["attempts"], line["kinds"]) == (1, ["DecodingError"])


@pytest.mark.usefixtures("fast_pause")
class TestEveryQueryThatReachedSerpApiLogsExactlyOneOfTheTwo:
    """`serp.completed` or `serp.attempts_failed`, whichever way the query ended."""

    @staticmethod
    def _scripts() -> dict[str, list[httpx.Response | Exception]]:
        """Fresh steps each time: a response is not meant to be served twice."""
        return {
            "answered": [answer()],
            "answered-nothing": [httpx.Response(200, json={})],
            "answered-after-a-retry": [httpx.ReadTimeout("x"), answer()],
            "401": [httpx.Response(401, json={"error": "Invalid API key."})],
            "429": [httpx.Response(429, json={"error": "slow down"})],
            "500": [httpx.Response(500, content=b"broke")],
            "bad-json": [httpx.Response(200, content=b"<html>")],
            "error-payload": [httpx.Response(200, json={"error": "none"})],
            "429-after-a-retry": [httpx.ReadTimeout("x"), httpx.Response(429, json={"error": "x"})],
            "silent-twice": [httpx.ReadTimeout("x"), httpx.ReadTimeout("x")],
            "refused-twice": [httpx.ConnectError("x"), httpx.ConnectError("x")],
            "undecodable-body": [httpx.DecodingError("x")],
        }

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            ("answered", COMPLETED),
            ("answered-nothing", COMPLETED),
            ("answered-after-a-retry", COMPLETED),
            ("401", COMPLETED),
            ("429", COMPLETED),
            ("500", COMPLETED),
            ("bad-json", COMPLETED),
            ("error-payload", COMPLETED),
            ("429-after-a-retry", COMPLETED),
            ("silent-twice", FAILED),
            ("refused-twice", FAILED),
            ("undecodable-body", FAILED),
        ],
    )
    async def test_one_line_and_the_right_one(
        self, script: str, expected: str, monkeypatch: pytest.MonkeyPatch,
        serp_settings: Settings,
    ) -> None:
        assert set(self._scripts()) == {
            "answered", "answered-nothing", "answered-after-a-retry", "401", "429", "500",
            "bad-json", "error-payload", "429-after-a-retry", "silent-twice", "refused-twice",
            "undecodable-body",
        }
        FakeSerpApi(*self._scripts()[script]).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await search("q", settings=serp_settings)

        outcome = [name for name in serp_events(events) if name in (COMPLETED, FAILED)]
        assert outcome == [expected]


class TestAMissingKeyLogsNeitherLine:
    """Nothing is sent, so there is nothing to time, and nothing to say about it."""

    async def test_an_empty_key_is_serpapi_key_missing_sends_nothing_and_logs_no_serp_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`search`'s own check. `Settings` turns a blank key into an unset one, which
        `provider_key` refuses (the next test), so only a settings object that is handed an
        empty `SecretStr` after its validators have run gets as far as this code."""
        settings = settings_with_key(API_KEY)
        settings.serpapi_key = SecretStr("")
        # Any request would fail the fake: it has no steps.
        fake = FakeSerpApi().install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=settings)

        assert result.ok is False
        assert result.error_code == "SERPAPI_KEY_MISSING"
        assert fake.requests == []
        assert serp_events(events) == []

    async def test_an_unset_key_is_serpapi_key_missing_sends_nothing_and_logs_no_serp_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key at all is the same answer as an empty one: a result, not an exception, so a
        deployment without the key degrades the detection and does not fail it."""
        settings = settings_with_key("")
        assert settings.serpapi_key is None
        fake = FakeSerpApi().install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=settings)

        assert result.ok is False
        assert result.error_code == "SERPAPI_KEY_MISSING"
        assert fake.requests == []
        assert serp_events(events) == []

    async def test_search_many_with_no_key_returns_one_missing_key_result_per_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = settings_with_key("")
        fake = FakeSerpApi().install(monkeypatch)

        results = await search_many(["a", "b", "c"], settings=settings)

        assert [r.error_code for r in results] == ["SERPAPI_KEY_MISSING"] * 3
        assert [r.query for r in results] == ["a", "b", "c"]
        assert fake.requests == []


# ---------------------------------------------------------------------------
# what the line must not carry
# ---------------------------------------------------------------------------

# Keys that would carry the request, its answer or an exception.
FORBIDDEN_FIELDS = {
    "q", "query", "gl", "hl", "num", "engine", "api_key", "key", "url", "params", "body",
    "text", "content", "error", "exception", "exc_info",
}


def assert_nothing_of_the_request_is_logged(events: list[dict[str, Any]]) -> None:
    """No captured event names the key, the query, the market, a URL, a body or an exception.

    Every key and every value of every event is read as it would be rendered, and not only
    those of `serp.completed`: a line of the module's that is not this one is held to it too.
    """
    in_no_line = (
        API_KEY, URL_WITH_KEY, "serpapi.com", "://", "api_key", QUERY, "ZQXSENTINEL",
        BODY_SENTINEL, MESSAGE_SENTINEL,
    )
    for event in events:
        name = event["event"]
        assert not set(event) & FORBIDDEN_FIELDS, f"{name} carries {set(event) & FORBIDDEN_FIELDS}"
        rendered = json.dumps(event, default=str)
        for secret in in_no_line:
            assert secret not in rendered, f"{secret!r} reached {name}"
        # The market, the language and the engine, as whole values.
        for value in (MARKET, "en", "google"):
            assert json.dumps(value) not in rendered, f"{value!r} reached {name}"


@pytest.mark.usefixtures("fast_pause")
class TestTheLinesCarryNothingOfTheRequest:
    """The request carries the key, the query and the market; no line of the module may.

    Each case asserts that the request really carried all three and that the line that
    should have been logged was, so that an absence is not an absence of the line.
    """

    @staticmethod
    def _echo(status: int, **body: Any) -> httpx.Response:
        """An error answer that echoes the request URL, as SerpApi does on some errors."""
        if "json" in body:
            body = {"json": {**body["json"], "echo": URL_WITH_KEY, "note": BODY_SENTINEL}}
        else:
            body = {"content": body["content"] + URL_WITH_KEY.encode() + BODY_SENTINEL.encode()}
        return httpx.Response(status, **body)

    @staticmethod
    def _message() -> str:
        return f"GET {URL_WITH_KEY} did not complete {MESSAGE_SENTINEL}"

    def _scenarios(self) -> dict[str, tuple[list[httpx.Response | Exception], str]]:
        message = self._message()
        return {
            "answered": ([answer("front.com", "kayako.com")], COMPLETED),
            "retried-and-answered": (
                [httpx.ReadTimeout(message), answer("front.com")], COMPLETED,
            ),
            "retried-and-refused": (
                [httpx.ConnectError(message), self._echo(429, json={"error": "slow"})],
                COMPLETED,
            ),
            "401": ([self._echo(401, json={"error": "Invalid API key."})], COMPLETED),
            "429": ([self._echo(429, json={"error": "Too many requests."})], COMPLETED),
            "500": ([self._echo(500, content=b"upstream broke")], COMPLETED),
            "bad-json": ([self._echo(200, content=b"<html>not json</html>")], COMPLETED),
            "error-payload": ([self._echo(200, json={"error": "No results."})], COMPLETED),
            "no-answer": ([httpx.ReadTimeout(message), httpx.ReadTimeout(message)], FAILED),
            "undecodable-body": ([httpx.DecodingError(message)], FAILED),
        }

    @pytest.mark.parametrize(
        "scenario",
        [
            "answered", "retried-and-answered", "retried-and-refused", "401", "429", "500",
            "bad-json", "error-payload", "no-answer", "undecodable-body",
        ],
    )
    async def test_no_line_names_the_key_the_query_the_market_a_url_a_body_or_a_message(
        self, scenario: str, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings,
    ) -> None:
        steps, expected = self._scenarios()[scenario]
        fake = FakeSerpApi(*steps).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await search(QUERY, settings=serp_settings, country=MARKET)

        sent = fake.requests[0].url.params
        assert (sent["api_key"], sent["q"], sent["gl"]) == (API_KEY, QUERY, MARKET)
        assert lines(events, expected), f"{expected} was not logged, so this proves nothing"
        assert_nothing_of_the_request_is_logged(events)
        assert not [e for e in events if "exc_info" in e or "exception" in e]


# ---------------------------------------------------------------------------
# several queries
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_pause")
class TestSearchManyLogsOneLineForEachQuery:
    async def test_six_answered_queries_log_six_completed_lines(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        install(monkeypatch, lambda request: answer("front.com", "kayako.com"))

        with structlog.testing.capture_logs() as events:
            results = await search_many([f"q{i}" for i in range(6)], settings=serp_settings)

        assert len(results) == 6
        assert all(r.ok for r in results)
        assert serp_events(events) == [COMPLETED] * 6
        for line in lines(events, COMPLETED):
            assert_the_five_facts(line)
            assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (
                1, 2, 200, None,
            )

    async def test_six_queries_that_end_six_ways_log_exactly_one_of_the_two_each(
        self, monkeypatch: pytest.MonkeyPatch, serp_settings: Settings
    ) -> None:
        """Answered x2, rate-limited, rescued by a retry, never answered, and a 500."""
        asked: dict[str, int] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            query = request.url.params["q"]
            asked[query] = asked.get(query, 0) + 1
            if query == "rate-limited":
                return httpx.Response(429, json={"error": "slow down"})
            if query == "rescued" and asked[query] == 1:
                raise httpx.ReadTimeout("simulated", request=request)
            if query == "silent":
                raise httpx.ReadTimeout("simulated", request=request)
            if query == "broken":
                return httpx.Response(500, content=b"upstream broke")
            return answer("front.com", "kayako.com")

        install(monkeypatch, handler)
        queries = ["fine", "also-fine", "rate-limited", "rescued", "silent", "broken"]

        with structlog.testing.capture_logs() as events:
            results = await search_many(queries, settings=serp_settings)

        assert [r.error_code for r in results] == [
            None, None, "SERP_RATE_LIMITED", None, "SERP_TIMEOUT", "SERP_HTTP_500",
        ]
        assert asked == {
            "fine": 1, "also-fine": 1, "rate-limited": 1, "rescued": 2, "silent": 2, "broken": 1,
        }
        completed = lines(events, COMPLETED)
        failed = lines(events, FAILED)
        # Six queries, one line each.
        assert len(completed) + len(failed) == 6
        assert (len(completed), len(failed), len(lines(events, RETRIED))) == (5, 1, 1)
        assert sorted(line["status"] for line in completed) == [200, 200, 200, 429, 500]
        assert sorted(line["attempts"] for line in completed) == [1, 1, 1, 1, 2]
        assert sorted(line["hits"] for line in completed) == [0, 0, 2, 2, 2]
        for line in completed:
            assert_the_five_facts(line)

"""What SerpApi was asked, counted for the scan it was asked in — U1, 2026-10-04.

A scan's `scan.usage` says, for its detection's searches, how many requests were made (retries
included), how many were answered ok, how many were answered with an error, and how many got
no answer at all. The reason for the split is what each costs. Per SerpApi's pricing page
(read 2026-10-03, quoted in `serp.py`): only a successful search counts toward the month, and
a cached one does not; an errored one is not billed; one that never got an answer is the one
nobody can say, which is why it is counted on its own and is never read as free.

`serpapi_billed_or_unknown` is the two that might have been billed added together, the answered
and the unanswered: an upper bound on what SerpApi billed, left out of the line when it is zero.

The key travels in the request's URL, and SerpApi and httpx both echo that URL in places (an
error body, an exception's text). So every test here also holds the whole run's log to one rule:
no line, as the console prints it or as JSON, holds the key, the URL's host and path, or the URL.

The provider is faked as in `test_serp_completed.py`: a `MockTransport` scripted one step per
request, an answer or the exception a transport raises when none came. Nothing here reaches
SerpApi, whose free plan of 250 searches a month is shared with production.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import structlog
from pydantic import SecretStr

from avp_api.config import Settings
from avp_api.services import serp, usage
from avp_api.services.serp import search, search_many

# Imported by name, on purpose: conftest's autouse `stub_chain_externals` replaces
# `serp.search_many` for every test. The name bound at import is the real one.

KEY = "SERPKEYSENTINEL-6b0d3e91a4c75f28"
ORGANIC = {"organic_results": [{"position": 1, "link": "https://front.com/a", "title": "t"}]}
# What the request URL is, with the key in it, and the parts of it that must not be logged.
URL = serp.SERPAPI_URL
URL_WITH_KEY = f"{URL}?engine=google&q=x&api_key={KEY}"
HOST_AND_PATH = "serpapi.com/search.json"


def assert_no_key_or_url_in(events: list[dict[str, Any]]) -> None:
    """Nothing the run logged, as the console prints it or as JSON, holds the key or the URL."""
    render = structlog.dev.ConsoleRenderer(colors=False)
    printed = "\n".join(
        render(None, "info", {**e, "level": e.get("log_level", "info")}) for e in events)
    shown = printed + json.dumps(events, default=str)
    for forbidden in (KEY, URL_WITH_KEY, URL, HOST_AND_PATH, "api_key"):
        assert forbidden not in shown, f"{forbidden!r} reached the log: {shown[:300]}"


@pytest.fixture(autouse=True)
def no_key_or_url_in_any_line() -> Any:
    """Every test below is also a test that nothing it makes SerpApi do puts the key in a log."""
    with structlog.testing.capture_logs() as events:
        yield events
    assert_no_key_or_url_in(events)


class Script:
    """SerpApi as `search` sees it: each request answered by the next step of its script."""

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

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Script:
        real = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(self.handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(serp.httpx, "AsyncClient", factory)
        return self


def ok() -> httpx.Response:
    return httpx.Response(200, json=ORGANIC)


def timed_out(request_less: str = "no answer") -> httpx.ReadTimeout:
    return httpx.ReadTimeout(request_less)


@pytest.fixture
def keyed() -> Settings:
    return Settings(
        _env_file=None, environment="test",
        database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment", serpapi_key=SecretStr(KEY),
    )


@pytest.fixture(autouse=True)
def fast_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serp, "RETRY_PAUSE_S", 0.01)


def counts(scan: usage.ScanUsage) -> tuple[int, int, int, int]:
    f = scan.log_fields()
    return (f["serpapi_attempted"], f["serpapi_ok"], f["serpapi_error"], f["serpapi_no_answer"])


def billed_or_unknown(scan: usage.ScanUsage) -> int | None:
    """`serpapi_billed_or_unknown`, or None when the line leaves it out (it is zero)."""
    value = scan.log_fields().get("serpapi_billed_or_unknown")
    assert value is None or isinstance(value, int)
    return value


class TestOneQuery:
    async def test_an_answer_is_one_request_answered_ok(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(ok()).install(monkeypatch)
        with usage.scan_scope("scan_1") as scan:
            result = await search("q", settings=keyed)

        assert result.ok
        assert counts(scan) == (1, 1, 0, 0)
        assert billed_or_unknown(scan) == 1

    @pytest.mark.parametrize("answer", [
        pytest.param(httpx.Response(401, json={"error": "Invalid API key."}), id="401"),
        pytest.param(httpx.Response(429, json={"error": "Too many requests."}), id="429"),
        pytest.param(httpx.Response(500, content=b"upstream broke"), id="500"),
        pytest.param(httpx.Response(503, content=b"overloaded"), id="503"),
        pytest.param(httpx.Response(200, content=b"<html>not json</html>"), id="bad-json"),
        pytest.param(httpx.Response(200, json={"error": "Google hasn't returned results."}),
                     id="error-payload"),
    ])
    async def test_an_answer_that_says_no_is_one_request_answered_with_an_error(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch, answer: httpx.Response
    ) -> None:
        script = Script(answer).install(monkeypatch)
        with usage.scan_scope("scan_1") as scan:
            result = await search("q", settings=keyed)

        assert not result.ok
        assert len(script.requests) == 1, "an answer, whatever it says, is never asked for twice"
        assert counts(scan) == (1, 0, 1, 0)
        # An answer that says no is not billed, and nothing about it is unknown.
        assert billed_or_unknown(scan) is None

    async def test_a_timeout_then_an_answer_is_two_requests_one_without_an_answer_and_one_ok(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(timed_out(), ok()).install(monkeypatch)
        with usage.scan_scope("scan_1") as scan:
            result = await search("q", settings=keyed)

        assert result.ok
        assert counts(scan) == (2, 1, 0, 1)
        assert billed_or_unknown(scan) == 2

    async def test_a_timeout_then_an_error_answer_is_two_requests_one_each(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(timed_out(), httpx.Response(429, json={"error": "x"})).install(monkeypatch)
        with usage.scan_scope("scan_1") as scan:
            await search("q", settings=keyed)

        assert counts(scan) == (2, 0, 1, 1)
        # The error answer is not billed; the request that got no answer may have been.
        assert billed_or_unknown(scan) == 1

    @pytest.mark.parametrize("failure", [
        httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
        httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError,
    ])
    async def test_two_failures_with_no_answer_are_two_requests_that_nobody_can_price(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch, failure: type[httpx.TransportError]
    ) -> None:
        Script(failure("a"), failure("b")).install(monkeypatch)
        with usage.scan_scope("scan_1") as scan:
            result = await search("q", settings=keyed)

        assert not result.ok
        assert counts(scan) == (2, 0, 0, 2)
        assert billed_or_unknown(scan) == 2

    async def test_a_missing_key_sends_nothing_and_counts_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script().install(monkeypatch)
        no_key = Settings(
            _env_file=None, environment="test",
            database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
            app_secret="test-secret-not-used-in-any-real-environment", serpapi_key=None,
        )
        with (
            usage.scan_scope("scan_1") as scan,
            pytest.raises(RuntimeError, match="SERPAPI_KEY is not configured"),
        ):
            await search("q", settings=no_key)

        assert script.requests == []
        assert counts(scan) == (0, 0, 0, 0)
        assert billed_or_unknown(scan) is None

    async def test_outside_a_scan_nothing_is_recorded_and_nothing_breaks(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(timed_out(), ok()).install(monkeypatch)
        result = await search("q", settings=keyed)
        assert result.ok


class TestADetectionsQueries:
    async def test_the_counts_add_up_across_queries_and_to_the_searches_attempted(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three queries run three at once: one retried and answered, one never answered, one
        answered with an error. Scripted by the query, since the order they start in is not
        something a scan promises."""
        by_query: dict[str, list[httpx.Response | Exception]] = {
            "retried": [timed_out(), ok()],
            "silent": [timed_out(), timed_out()],
            "refused": [httpx.Response(401, json={"error": "Invalid API key."})],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            step = by_query[request.url.params["q"]].pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        real = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(serp.httpx, "AsyncClient", factory)

        with usage.scan_scope("scan_1") as scan:
            results = await search_many(["retried", "silent", "refused"], settings=keyed)

        assert [r.ok for r in results] == [True, False, False]
        attempted, answered_ok, error, no_answer = counts(scan)
        assert (attempted, answered_ok, error, no_answer) == (5, 1, 1, 3)
        assert answered_ok + error + no_answer == attempted
        assert billed_or_unknown(scan) == answered_ok + no_answer == 4
        assert all(not steps for steps in by_query.values()), "every scripted request was made"


def echoing_the_url(message: str = "") -> str:
    """What SerpApi and httpx put in an error's text: the request URL, key and all."""
    return f"{message} {URL_WITH_KEY}".strip()


class TestNoKeyOrUrlReachesAnyLine:
    """Whatever happens to a search, in a scan's scope so that its `scan.usage` is logged too, and
    with every place the URL could be echoed doing so: an error body, a transport error's text."""

    @pytest.mark.parametrize("steps", [
        pytest.param([ok()], id="answered"),
        pytest.param([timed_out(echoing_the_url("read timed out")), ok()], id="retried-then-ok"),
        pytest.param(
            [httpx.ConnectError(echoing_the_url()), httpx.ConnectError(echoing_the_url())],
            id="never-answered"),
        pytest.param(
            [httpx.Response(401, json={"error": echoing_the_url("Invalid API key.")})],
            id="401-body-echoes-the-url"),
        pytest.param(
            [httpx.Response(500, content=echoing_the_url("upstream broke").encode())],
            id="500-body-echoes-the-url"),
        pytest.param(
            [httpx.Response(200, content=echoing_the_url("<html>").encode())],
            id="bad-json-echoes-the-url"),
        pytest.param(
            [httpx.Response(200, json={"error": echoing_the_url("Google had nothing.")})],
            id="error-payload-echoes-the-url"),
    ])
    async def test_whatever_happens_to_a_search_no_line_holds_the_key_or_the_url(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch,
        no_key_or_url_in_any_line: list[dict[str, Any]],
        steps: list[httpx.Response | Exception],
    ) -> None:
        script = Script(*steps).install(monkeypatch)

        with usage.scan_scope("scan_1"):
            await search("q", settings=keyed)

        # The key really was in play, in the request's URL ...
        assert KEY in str(script.requests[0].url)
        # ... the run logged lines of its own (the scan's total, and SerpApi's) ...
        events = no_key_or_url_in_any_line
        assert [e["event"] for e in events if e["event"] == "scan.usage"] == ["scan.usage"]
        assert [e for e in events if e["event"].startswith("serp.")]
        # ... and not one of them has the key or the URL.
        assert_no_key_or_url_in(events)

    async def test_the_scan_total_names_no_host_path_or_key(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch,
        no_key_or_url_in_any_line: list[dict[str, Any]],
    ) -> None:
        Script(ok(), ok()).install(monkeypatch)

        with usage.scan_scope("scan_1"):
            await search_many(["a", "b"], settings=keyed)

        (total,) = [e for e in no_key_or_url_in_any_line if e["event"] == "scan.usage"]
        assert {k for k in total if k.startswith("serpapi")} == {
            "serpapi_attempted", "serpapi_ok", "serpapi_error", "serpapi_no_answer",
            "serpapi_billed_or_unknown"}
        assert all(isinstance(v, int) for k, v in total.items() if k.startswith("serpapi"))


class TestACountingFailureNeverChangesASearch:
    async def test_a_failing_accumulator_leaves_the_searchs_answer_as_it_was(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recording runs inside `search`, whose answer is not usage's to change."""
        def broken(self: usage.ScanUsage, **kwargs: Any) -> None:
            raise RuntimeError("the accumulator broke")

        monkeypatch.setattr(usage.ScanUsage, "add_serp", broken)
        Script(ok()).install(monkeypatch)

        with usage.scan_scope("scan_1"):
            result = await search("q", settings=keyed)

        assert result.ok and [h.domain for h in result.hits] == ["front.com"]

    async def test_a_failing_accumulator_leaves_a_failed_searchs_answer_as_it_was(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(self: usage.ScanUsage, **kwargs: Any) -> None:
            raise RuntimeError("the accumulator broke")

        monkeypatch.setattr(usage.ScanUsage, "add_serp", broken)
        Script(timed_out(), timed_out()).install(monkeypatch)

        with usage.scan_scope("scan_1"):
            result = await search("q", settings=keyed)

        assert not result.ok and result.error_code == "SERP_TIMEOUT"

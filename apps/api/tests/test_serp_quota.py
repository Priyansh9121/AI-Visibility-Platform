"""How many SerpApi searches are left, read once for each detection — U2, 2026-10-04.

The plan is 250 searches a month, shared with production, and nothing said how many were left
until someone opened SerpApi's account page. `serp.quota_left` asks the account endpoint
(`account.json`: free, and not a search), and `detect_from_facts` calls it once, after its
searches and while the model is still answering, so the figure is what the searches left and the
read costs the detection nothing it was not already waiting for. It writes `serp.quota_left`
(`left`, and `plan_searches` when the account says), `serp.quota_low` beside it when `left` is
under 50, and, when it could not read, `serp.quota_unavailable`: one info line that names the
kind of failure and nothing else. It is INFO, not DEBUG, so that a failed read is in the logs Render
keeps and nobody has to turn DEBUG on to see it (which would also print `classify.result` and
`fixes.reasoning`).

The account's body carries the API key (SerpApi echoes it) and the owner's address, and the
request's URL carries the key. None of them may reach a log, so every test that reads the account
says so about every line it wrote, and the fake's body carries both.

The provider is faked: a `MockTransport` for the answers and, for the tests that need httpx's own
timeouts to do the enforcing, a real local socket (the idiom of `test_serp_search.py`). Nothing
here reaches SerpApi: the account is a free plan of 250 searches a month, shared with production.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import logging
import re
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient
from pydantic import SecretStr

from avp_api.config import Settings
from avp_api.logs import configure_logging
from avp_api.models import Client
from avp_api.models.client import ClassificationStatus, ClientKind
from avp_api.services import cocitation as cocitation_service
from avp_api.services import competitors as detection
from avp_api.services import serp
from avp_api.services.cocitation import CoCitationHit, CoCitationResult
from avp_api.services.serp import quota_left, search_many

# `search_many` and `quota_left` are imported here, by name, on purpose: conftest's autouse
# `stub_chain_externals` replaces `serp.search_many` and `serp.quota_left` for every test with
# fakes that reach nothing. The names bound at import are the real ones, and so is the client
# class, which a test below replaces more than once.
REAL_SEARCH_MANY = search_many
REAL_QUOTA_LEFT = quota_left
REAL_ASYNC_CLIENT = httpx.AsyncClient

BASE = "/api/v1"
API_KEY = "SERPKEYSENTINEL-U2-6f2a91c3d8e54b07"
OWNER = "owner@serpapi-account.example"
ACCOUNT_PATH = "/account.json"
SEARCH_PATH = "/search.json"

# The shipped bound, read before any test swaps it for a small one.
SHIPPED_PHASE_TIMEOUTS = serp.QUOTA_PHASE_TIMEOUTS
SHIPPED_DEADLINE_S = serp.QUOTA_TIMEOUT_S

# The same mechanism at a scale a test can wait for: a phase budget under the deadline, as shipped.
SMALL_PHASE_S = 0.15
SMALL_DEADLINE_S = 0.6
# What a run may take beyond its waits: building a client loads the CA bundle, and a loaded CI
# machine is slow. Small beside the 5 s that this is guarding.
SLACK_S = 3.0
# A hang fails the test after this long instead of hanging the suite.
WATCHDOG_S = 15.0

# Everything a line must never carry: the key, the owner's address, the key's name, a host, a URL.
FORBIDDEN = (API_KEY, OWNER, "@", "api_key", "serpapi", "://", ".json")

Events = list[dict[str, Any]]


# ---------------------------------------------------------------------------
# the fakes
# ---------------------------------------------------------------------------


def account(**changes: Any) -> dict[str, Any]:
    """An `account.json` body as SerpApi sends it: the key and the owner's address are in it."""
    body: dict[str, Any] = {
        "account_id": "acct_0123456789",
        "api_key": API_KEY,
        "account_email": OWNER,
        "account_status": "Active",
        "plan_id": "free",
        "plan_name": "Free Plan",
        "plan_monthly_price": 0.0,
        "searches_per_month": 250,
        "plan_searches_left": 184,
        "extra_credits": 0,
        "total_searches_left": 184,
        "this_month_usage": 66,
        "this_hour_searches": 0,
        "last_hour_searches": 0,
        "account_rate_limit_per_hour": 50,
    }
    body.update(changes)
    return body


def without(body: dict[str, Any], *names: str) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key not in names}


def ok_account(**changes: Any) -> httpx.Response:
    return httpx.Response(200, json=account(**changes))


def organic_page() -> httpx.Response:
    return httpx.Response(200, json={"organic_results": [
        {"position": i, "link": f"https://{domain}/page", "title": f"Title {i}"}
        for i, domain in enumerate(("zendesk.com", "front.com", "kayako.com"), start=1)
    ]})


Step = httpx.Response | Exception | Callable[[httpx.Request], Awaitable[httpx.Response]]


async def never(request: httpx.Request) -> httpx.Response:
    """An answer that does not come."""
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


class FakeSerpApi:
    """SerpApi as `search_many` and `quota_left` see it, over a `MockTransport`.

    A search is answered by `search()` and the account by `account()`: each returns a response,
    an exception to raise (what a transport does when no answer came) or an async callable that
    answers later or never. `requests` keeps every request in the order it was made, and
    `account_started` is set when the first account request arrives.
    """

    def __init__(
        self,
        *,
        account: Callable[[], Step] = ok_account,
        search: Callable[[], Step] = organic_page,
    ) -> None:
        self.account = account
        self.search = search
        self.requests: list[httpx.Request] = []
        self.timeouts: list[Any] = []
        self.account_started = asyncio.Event()

    @property
    def accounts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == ACCOUNT_PATH]

    @property
    def searches(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == SEARCH_PATH]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == ACCOUNT_PATH:
            self.account_started.set()
            step = self.account()
        elif request.url.path == SEARCH_PATH:
            step = self.search()
        else:
            raise AssertionError(f"SerpApi was asked for {request.url.path}")
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        return await step(request)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSerpApi:
        """Every client the serp module opens talks to `handler` instead of SerpApi.

        The client is the real class, built with the keyword arguments it was given, so the
        timeout it was handed is the one `timeouts` shows. Installing a second fake replaces
        the first.
        """

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            self.timeouts.append(kwargs.get("timeout"))
            kwargs["transport"] = httpx.MockTransport(self.handler)
            return REAL_ASYNC_CLIENT(*args, **kwargs)

        monkeypatch.setattr(serp.httpx, "AsyncClient", factory)
        return self


def settings_with(key: str | SecretStr | None, **more: Any) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        serpapi_key=key,
        **more,
    )


@pytest.fixture
def keyed() -> Settings:
    """A recognisable key, so its absence from a log can be asserted."""
    return settings_with(API_KEY)


@pytest.fixture(autouse=True)
def logging_is_left_as_found() -> Iterator[None]:
    """structlog filters by the level it was last configured at, and an earlier test may have
    left it above a level a line here is written at, so that the line is dropped before
    `capture_logs` sees it. This module sets a level that drops nothing, and puts back whatever
    it found, the levels `configure_logging` pins on httpx, httpcore and the SDK's logger
    included."""
    pinned = ("httpx", "httpcore", "anthropic")
    levels = {name: logging.getLogger(name).level for name in pinned}
    found = dict(structlog.get_config())
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG))
    yield
    structlog.configure(**found)
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture
def real_serp(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real search and the real quota read go back, so detection reaches the fake."""
    monkeypatch.setattr(serp, "search_many", REAL_SEARCH_MANY)
    monkeypatch.setattr(serp, "quota_left", REAL_QUOTA_LEFT)
    monkeypatch.setattr(serp, "RETRY_PAUSE_S", 0.01)


@pytest.fixture
def small_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The read's phase budgets and deadline, at a scale a test can wait out."""
    monkeypatch.setattr(serp, "QUOTA_PHASE_TIMEOUTS", httpx.Timeout(SMALL_PHASE_S))
    monkeypatch.setattr(serp, "QUOTA_TIMEOUT_S", SMALL_DEADLINE_S)


def named(events: Events, name: str) -> Events:
    return [e for e in events if e["event"] == name]


def fields(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k not in ("event", "log_level")}


def unavailable(kind: str) -> dict[str, Any]:
    """The one line a read that failed writes, and all it says. INFO, so that it is in the logs
    Render keeps."""
    return {"event": "serp.quota_unavailable", "log_level": "info", "kind": kind}


def assert_nothing_secret(events: Events) -> None:
    """Every key and every value of every event, as it would be rendered, free of what must
    never be logged. The needles are looked for in `key=repr(value)`, so a value that is an
    object, a list or a number is read as the renderer would print it."""
    for event in events:
        for key, value in event.items():
            rendered = f"{key}={value!r}"
            for needle in FORBIDDEN:
                assert needle not in rendered, f"{needle!r} in {event['event']}: {rendered}"


async def read(
    keyed: Settings, fake: FakeSerpApi, monkeypatch: pytest.MonkeyPatch
) -> tuple[int | None, Events]:
    """One `quota_left` against `fake`, and every structlog event it wrote."""
    fake.install(monkeypatch)
    with structlog.testing.capture_logs() as events:
        left = await asyncio.wait_for(REAL_QUOTA_LEFT(keyed), WATCHDOG_S)
    return left, events


# ---------------------------------------------------------------------------
# what is read
# ---------------------------------------------------------------------------


class TestWhatIsRead:
    async def test_it_returns_the_plans_remaining_searches(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        left, _ = await read(keyed, FakeSerpApi(), monkeypatch)
        assert left == 184

    async def test_it_is_the_plans_figure_and_not_the_total_with_extra_credits(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """The two differ only when one-time extra credits were bought. `plan_searches_left`
        is read, so `left` is always 'of the plan's `searches_per_month`'."""
        fake = FakeSerpApi(account=lambda: ok_account(
            plan_searches_left=184, extra_credits=7, total_searches_left=191,
        ))
        left, events = await read(keyed, fake, monkeypatch)

        assert left == 184
        assert [fields(e) for e in named(events, "serp.quota_left")] == [
            {"left": 184, "plan_searches": 250}
        ]

    async def test_it_is_one_get_to_the_account_endpoint_with_the_key_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi()
        await read(keyed, fake, monkeypatch)

        (request,) = fake.requests
        assert request.method == "GET"
        assert request.url.host == "serpapi.com"
        assert request.url.path == ACCOUNT_PATH, "the account endpoint, not a search"
        # The premise of every test below: the key IS in the request.
        assert dict(request.url.params) == {"api_key": API_KEY}

    async def test_the_key_is_read_from_the_settings_it_is_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSerpApi()
        other = "SERPKEYSENTINEL-U2-other-0000000000000000"
        await read(settings_with(other), fake, monkeypatch)

        assert fake.accounts[0].url.params["api_key"] == other

    async def test_the_client_is_built_with_the_reads_own_budgets(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi()
        await read(keyed, fake, monkeypatch)

        assert fake.timeouts == [SHIPPED_PHASE_TIMEOUTS]

    async def test_a_failure_is_not_asked_again(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """No retry: the figure is a convenience, and a second wait is a cost to detection."""
        fake = FakeSerpApi(account=lambda: httpx.ReadTimeout("no answer"))
        left, _ = await read(keyed, fake, monkeypatch)

        assert left is None
        assert len(fake.accounts) == 1


# ---------------------------------------------------------------------------
# the lines it writes
# ---------------------------------------------------------------------------

_LINE = re.compile(r"^\S+ \S+ \[(\w+)\s*\] (\S+)\s*(.*?)\s*$")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def rendered_lines(out: str) -> list[tuple[str, str, str]]:
    """What structlog printed, as (level, event, `key=value ...`): the timestamp and the colour
    (there is none when stdout is not a terminal) taken off."""
    found = []
    for line in _ANSI.sub("", out).splitlines():
        matched = _LINE.match(line)
        assert matched, f"a line that is not structlog's: {line!r}"
        found.append((matched.group(1), matched.group(2), matched.group(3)))
    return found


class TestTheLines:
    async def test_a_good_read_writes_one_info_line_with_the_count_and_the_plans_size(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        left, events = await read(keyed, FakeSerpApi(), monkeypatch)

        assert left == 184
        assert events == [
            {"event": "serp.quota_left", "log_level": "info", "left": 184, "plan_searches": 250}
        ]

    @pytest.mark.parametrize(
        ("left", "low"),
        [(250, False), (184, False), (51, False), (50, False), (49, True), (1, True), (0, True)],
    )
    async def test_under_fifty_is_a_warning_beside_the_info_line_and_fifty_is_not(
        self, left: int, low: bool, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """Both lines: the info line is the reading, so a reader who searches for it finds
        every figure, the lowest ones included; the warning is the alert."""
        fake = FakeSerpApi(account=lambda: ok_account(
            plan_searches_left=left, total_searches_left=left,
        ))
        got, events = await read(keyed, fake, monkeypatch)

        assert got == left
        assert [(e["event"], e["log_level"]) for e in events] == [
            ("serp.quota_left", "info"),
            *([("serp.quota_low", "warning")] if low else []),
        ]
        for event in events:
            assert fields(event) == {"left": left, "plan_searches": 250}

    @pytest.mark.parametrize("size", [
        pytest.param(lambda: without(account(plan_searches_left=12), "searches_per_month"),
                     id="missing"),
        pytest.param(lambda: account(plan_searches_left=12, searches_per_month="250"),
                     id="a-string"),
        pytest.param(lambda: account(plan_searches_left=12, searches_per_month=None), id="null"),
        pytest.param(lambda: account(plan_searches_left=12, searches_per_month=True), id="a-bool"),
        pytest.param(lambda: account(plan_searches_left=12, searches_per_month=-1),
                     id="negative"),
        pytest.param(lambda: account(plan_searches_left=12, searches_per_month=250.5),
                     id="a-float"),
    ])
    async def test_the_plans_size_is_left_out_when_the_account_does_not_give_one_cleanly(
        self, size: Callable[[], dict[str, Any]], monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi(account=lambda: httpx.Response(200, json=size()))
        left, events = await read(keyed, fake, monkeypatch)

        assert left == 12
        assert [(e["event"], fields(e)) for e in events] == [
            ("serp.quota_left", {"left": 12}),
            ("serp.quota_low", {"left": 12}),
        ]

    async def test_a_line_carries_the_count_and_the_plans_size_and_no_other_fact_of_the_body(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """Not the plan's name or price, the usage, the hour's searches, the key, the address."""
        _, events = await read(keyed, FakeSerpApi(), monkeypatch)

        assert set(events[0]) == {"event", "log_level", "left", "plan_searches"}
        assert_nothing_secret(events)

    async def test_the_lines_as_the_app_prints_them(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Through the real structlog pipeline, as `configure_logging` sets it up and as
        Render's Logs tab shows it: the verbatim lines."""
        structlog.reset_defaults()
        configure_logging(keyed)
        fake = FakeSerpApi(account=lambda: ok_account(
            plan_searches_left=49, total_searches_left=49,
        )).install(monkeypatch)
        capsys.readouterr()

        await asyncio.wait_for(REAL_QUOTA_LEFT(keyed), WATCHDOG_S)

        assert rendered_lines(capsys.readouterr().out) == [
            ("info", "serp.quota_left", "left=49 plan_searches=250"),
            ("warning", "serp.quota_low", "left=49 plan_searches=250"),
        ]
        assert len(fake.accounts) == 1

    async def test_at_info_a_good_read_and_a_failed_one_are_one_line_each(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """INFO is what Render keeps in staging and production, so a failed read is there too:
        nobody has to turn DEBUG on to see it, which would also print the model-reasoning lines
        (`classify.result`, `fixes.reasoning`)."""
        structlog.reset_defaults()
        info_only = settings_with(API_KEY, log_level="INFO")
        configure_logging(info_only)
        capsys.readouterr()

        FakeSerpApi().install(monkeypatch)
        await asyncio.wait_for(REAL_QUOTA_LEFT(info_only), WATCHDOG_S)
        good = rendered_lines(capsys.readouterr().out)

        FakeSerpApi(account=lambda: httpx.Response(500, content=b"down")).install(monkeypatch)
        await asyncio.wait_for(REAL_QUOTA_LEFT(info_only), WATCHDOG_S)
        failed = rendered_lines(capsys.readouterr().out)

        assert good == [("info", "serp.quota_left", "left=184 plan_searches=250")]
        assert failed == [("info", "serp.quota_unavailable", "kind=http_500")]


# ---------------------------------------------------------------------------
# when it cannot read
# ---------------------------------------------------------------------------

# Each way the read can fail, as (what the account does, the kind the line names). Each is
# a function, so that every use gets a response or an exception of its own.
FAILURES: list[Any] = [
    pytest.param(lambda: httpx.ConnectTimeout("no answer"), "connect", id="connect-timeout"),
    pytest.param(lambda: httpx.ReadTimeout("no answer"), "read", id="timeout"),
    pytest.param(lambda: httpx.WriteTimeout("no answer"), "write", id="write-timeout"),
    pytest.param(lambda: httpx.PoolTimeout("no answer"), "pool", id="pool-timeout"),
    pytest.param(lambda: httpx.ConnectError("refused"), "ConnectError", id="refused"),
    pytest.param(lambda: httpx.RemoteProtocolError("hung up"), "RemoteProtocolError",
                 id="hung-up"),
    pytest.param(lambda: httpx.Response(500, content=b"upstream broke"), "http_500", id="500"),
    pytest.param(lambda: httpx.Response(503, content=b"overloaded"), "http_503", id="503"),
    pytest.param(lambda: httpx.Response(429, json={"error": "Too many requests."}), "http_429",
                 id="429"),
    pytest.param(lambda: httpx.Response(401, json={"error": "Invalid API key."}), "http_401",
                 id="401"),
    pytest.param(lambda: httpx.Response(200, content=b"<html>not json</html>"), "bad_json",
                 id="bad-json"),
    pytest.param(lambda: httpx.Response(200, content=b"[184]"), "bad_json", id="a-list"),
    pytest.param(lambda: httpx.Response(200, content=b"null"), "bad_json", id="null-body"),
    pytest.param(lambda: httpx.Response(200, json={"error": "Invalid API key."}), "no_count",
                 id="error-body"),
    pytest.param(lambda: httpx.Response(200, json=without(account(), "plan_searches_left")),
                 "no_count", id="missing-field"),
    pytest.param(lambda: ok_account(plan_searches_left="184"), "no_count", id="string-count"),
    pytest.param(lambda: ok_account(plan_searches_left=184.5), "no_count", id="float-count"),
    pytest.param(lambda: ok_account(plan_searches_left=True), "no_count", id="bool-count"),
    pytest.param(lambda: ok_account(plan_searches_left=None), "no_count", id="null-count"),
    pytest.param(lambda: ok_account(plan_searches_left=-3), "no_count", id="negative-count"),
    pytest.param(lambda: ok_account(plan_searches_left=[184]), "no_count", id="list-count"),
    # An exception whose text names the request, as an httpx error's can: never logged.
    pytest.param(
        lambda: httpx.ConnectError(
            f"cannot connect to https://serpapi.com/account.json?api_key={API_KEY} for {OWNER}"
        ),
        "ConnectError", id="an-error-that-names-the-url",
    ),
]


class TestWhenItCannotRead:
    @pytest.mark.parametrize(("failure", "kind"), FAILURES)
    async def test_it_returns_none_and_writes_one_info_line_naming_the_kind(
        self, failure: Callable[[], Step], kind: str,
        monkeypatch: pytest.MonkeyPatch, keyed: Settings,
    ) -> None:
        left, events = await read(keyed, FakeSerpApi(account=failure), monkeypatch)

        assert left is None
        # One INFO line: never a warning or an error, and never more than one. It says the kind
        # and nothing else: no status text, no body, no exception.
        assert [e["log_level"] for e in events] == ["info"]
        assert events == [unavailable(kind)]
        assert_nothing_secret(events)

    @pytest.mark.parametrize(
        "key", [None, "", "   ", SecretStr("")], ids=["none", "empty", "blank", "an-empty-secret"]
    )
    async def test_without_a_key_nothing_is_sent_and_the_kind_is_no_key(
        self, key: str | SecretStr | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSerpApi()
        left, events = await read(settings_with(key), fake, monkeypatch)

        assert left is None
        assert fake.requests == []
        assert events == [unavailable("no_key")]

    @pytest.mark.parametrize(
        "exc", [RuntimeError("boom"), ValueError("bad"), KeyError("x"), OSError("socket")]
    )
    async def test_it_never_raises_whatever_the_transport_raises(
        self, exc: Exception, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        left, events = await read(keyed, FakeSerpApi(account=lambda: exc), monkeypatch)

        assert left is None
        # The class name, never the message.
        assert events == [unavailable(type(exc).__name__)]

    async def test_cancelling_the_read_is_never_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """Never raises is not never cancelled: a scan's deadline cancels what it is running."""
        fake = FakeSerpApi(account=lambda: never).install(monkeypatch)

        task = asyncio.ensure_future(REAL_QUOTA_LEFT(keyed))
        await asyncio.wait_for(fake.account_started.wait(), WATCHDOG_S)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, WATCHDOG_S)


# ---------------------------------------------------------------------------
# the key, the address and the URL never reach a log
# ---------------------------------------------------------------------------

EVERY_ANSWER: list[Any] = [
    pytest.param(lambda: ok_account(), id="good"),
    pytest.param(lambda: ok_account(plan_searches_left=3), id="low"),
    *[pytest.param(param.values[0], id=param.id) for param in FAILURES],
]


class TestNothingSecretIsLogged:
    @pytest.mark.parametrize("step", EVERY_ANSWER)
    async def test_no_structlog_event_carries_the_key_the_address_or_a_url(
        self, step: Callable[[], Step], monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi(account=step)
        _, events = await read(keyed, fake, monkeypatch)

        (request,) = fake.requests
        assert request.url.params["api_key"] == API_KEY, "the key is in the request"
        assert events, "a line to look at"
        assert_nothing_secret(events)
        everything = json.dumps(events, default=repr)
        assert API_KEY not in everything
        assert OWNER not in everything

    async def test_no_event_carries_a_traceback_or_the_exception_itself(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """Either would render the exception's text, and an httpx error's text can carry the
        request URL, which carries the key."""
        fake = FakeSerpApi(account=lambda: httpx.ConnectError(f"https://x/?api_key={API_KEY}"))
        _, events = await read(keyed, fake, monkeypatch)

        assert events
        assert [e["event"] for e in events if "exc_info" in e or "exception" in e] == []


@pytest.fixture
def unconfigured_loggers() -> Iterator[None]:
    """`httpx` and `httpcore` as a process nothing has configured has them: no level of their
    own. Earlier tests have usually pinned them already (every `create_app` does)."""
    names = ("httpx", "httpcore", "anthropic")
    found = {name: logging.getLogger(name).level for name in names}
    for name in found:
        logging.getLogger(name).setLevel(logging.NOTSET)
    yield
    for name, level in found.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.usefixtures("unconfigured_loggers")
class TestNoStdlibRecordIsTheKey:
    """httpx writes each request's whole URL at INFO, and the key is in the account's URL."""

    async def test_left_alone_httpx_does_write_the_key_so_the_test_below_has_a_line_to_catch(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        FakeSerpApi().install(monkeypatch)
        caplog.set_level(logging.INFO)

        await asyncio.wait_for(REAL_QUOTA_LEFT(keyed), WATCHDOG_S)

        assert API_KEY in caplog.text, "httpx no longer writes the URL: the premise is gone"

    @pytest.mark.parametrize("root_level", [logging.INFO, logging.DEBUG], ids=["INFO", "DEBUG"])
    async def test_with_logging_configured_as_the_app_does_nothing_written_names_the_key(
        self, root_level: int, monkeypatch: pytest.MonkeyPatch, keyed: Settings,
        caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """stdlib records (where httpx writes) and stdout (where structlog prints), for a good
        read, a low one and each failure, at the app's own level and with the root raised."""
        configure_logging(keyed)
        caplog.set_level(root_level)
        capsys.readouterr()

        for param in EVERY_ANSWER:
            FakeSerpApi(account=param.values[0]).install(monkeypatch)
            await asyncio.wait_for(REAL_QUOTA_LEFT(keyed), WATCHDOG_S)

        printed = capsys.readouterr().out
        assert "serp.quota_left" in printed, "stdout carried the lines: there was something to read"
        for channel in (caplog.text, printed):
            for needle in (API_KEY, OWNER, "api_key", "serpapi.com", "account.json"):
                assert needle not in channel, needle


# ---------------------------------------------------------------------------
# the bound
# ---------------------------------------------------------------------------


class TestTheBound:
    def test_the_deadline_is_five_seconds_and_each_phase_budget_a_second_under_it(self) -> None:
        timeout = SHIPPED_PHASE_TIMEOUTS
        assert SHIPPED_DEADLINE_S == 5.0
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
            4.0, 4.0, 4.0, 4.0,
        )

    def test_every_phase_budget_is_under_the_deadline_so_a_stalled_phase_names_itself(
        self,
    ) -> None:
        """Equal to it, a hung connect would always be cut by the deadline first and every
        failure would read `total`, never `connect` or `read`."""
        timeout = SHIPPED_PHASE_TIMEOUTS
        for phase in (timeout.connect, timeout.read, timeout.write, timeout.pool):
            assert phase is not None
            assert phase < SHIPPED_DEADLINE_S

    def test_the_read_is_shorter_than_one_search_and_than_the_models_ceiling(self) -> None:
        """It runs beside the co-citation calls, so it can only lengthen a detection's worst case
        (the sum `test_scan_executor.py` checks) if it could outlast them."""
        assert serp.ATTEMPT_TIMEOUT_S > SHIPPED_DEADLINE_S
        assert cocitation_service.CO_CITATION_BOUND.ceiling > SHIPPED_DEADLINE_S

    def test_below_fifty_searches_is_low(self) -> None:
        assert serp.QUOTA_LOW_BELOW == 50

    @pytest.mark.usefixtures("small_timeouts")
    async def test_a_response_that_never_comes_ends_at_the_deadline_with_the_kind_total(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """A `MockTransport` enforces no timeout of its own, so only the deadline can end this."""
        started = time.perf_counter()
        left, events = await read(keyed, FakeSerpApi(account=lambda: never), monkeypatch)
        elapsed = time.perf_counter() - started

        assert left is None
        assert SMALL_DEADLINE_S - 0.05 <= elapsed <= SMALL_DEADLINE_S + SLACK_S, elapsed
        assert events == [unavailable("total")]


class LocalAccount:
    """A TCP server standing in for SerpApi's account endpoint: it accepts, and says nothing.

    With `trickle_every` it does not stay silent: it sends the response's headers at once and
    then one byte of a body that never ends every `trickle_every` seconds, so that every read
    completes inside httpx's budget and the response never does.
    """

    def __init__(self, *, trickle_every: float | None = None) -> None:
        self.trickle_every = trickle_every
        self.connections = 0
        self.url = ""
        self._writers: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        with contextlib.suppress(
            ConnectionError, asyncio.CancelledError, asyncio.IncompleteReadError
        ):
            await reader.readuntil(b"\r\n\r\n")  # the request, in full
            if self.trickle_every is None:
                await reader.read()  # no answer, until the client gives up and closes
                return
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 1000000\r\n\r\n"
            )
            while not writer.is_closing():
                writer.write(b" ")
                await writer.drain()
                await asyncio.sleep(self.trickle_every)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}{ACCOUNT_PATH}"

    def stop(self) -> None:
        for writer in self._writers:
            writer.close()
        if self._server is not None:
            self._server.close()


def use_a_real_transport(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """`quota_left` pointed at `url`, over httpx's own transport, which enforces timeouts (and
    which, handed to the client by hand, never takes a proxy from a developer's environment)."""
    monkeypatch.setattr(serp, "ACCOUNT_URL", url)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.AsyncHTTPTransport()
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(serp.httpx, "AsyncClient", factory)


@contextlib.asynccontextmanager
async def local_account(
    monkeypatch: pytest.MonkeyPatch, *, trickle_every: float | None = None
) -> AsyncIterator[LocalAccount]:
    server = LocalAccount(trickle_every=trickle_every)
    await server.start()
    use_a_real_transport(monkeypatch, server.url)
    try:
        yield server
    finally:
        server.stop()


@pytest.mark.usefixtures("small_timeouts")
class TestAgainstAConnectionThatHangs:
    """Nothing about the bound is simulated here: httpx's own timeouts and the deadline do it."""

    async def test_a_silent_server_costs_one_connection_and_one_read_timeout(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        async with local_account(monkeypatch) as server:
            started = time.perf_counter()
            with structlog.testing.capture_logs() as events:
                left = await asyncio.wait_for(REAL_QUOTA_LEFT(keyed), WATCHDOG_S)
            elapsed = time.perf_counter() - started

        assert left is None
        # One connection, and no second: it is not retried.
        assert server.connections == 1
        assert SMALL_PHASE_S - 0.02 <= elapsed <= SMALL_PHASE_S + SLACK_S, elapsed
        # The server took the request and said nothing: the read gave up, not the connect.
        assert events == [unavailable("read")]

    async def test_a_response_that_trickles_is_cut_at_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """Every read finishes inside httpx's budget; only the deadline can end the read."""
        async with local_account(monkeypatch, trickle_every=0.03) as server:
            started = time.perf_counter()
            with structlog.testing.capture_logs() as events:
                left = await asyncio.wait_for(REAL_QUOTA_LEFT(keyed), WATCHDOG_S)
            elapsed = time.perf_counter() - started

        assert left is None
        assert server.connections == 1
        assert SMALL_DEADLINE_S - 0.05 <= elapsed <= SMALL_DEADLINE_S + SLACK_S, elapsed
        assert events == [unavailable("total")]

    async def test_a_refused_connection_is_named_for_what_it_is(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        use_a_real_transport(monkeypatch, "http://127.0.0.1:1/account.json")

        with structlog.testing.capture_logs() as events:
            left = await asyncio.wait_for(REAL_QUOTA_LEFT(keyed), WATCHDOG_S)

        assert left is None
        assert events == [unavailable("ConnectError")]


# ---------------------------------------------------------------------------
# detection reads it once, after the searches, beside the model
# ---------------------------------------------------------------------------


def a_detection_of_acme() -> dict[str, Any]:
    """The six queries `build_queries` makes for a client with a brand and an industry."""
    return {"brand_name": "Acme", "domain": "acme.com", "industry": "project software"}


async def detect(settings: Settings) -> detection.DetectionOutcome:
    return await detection.detect_from_facts(**a_detection_of_acme(), settings=settings)


def summary(outcome: detection.DetectionOutcome) -> tuple[Any, ...]:
    """What a detection concluded, in comparable terms."""
    return (
        outcome.status, outcome.detection_confidence, outcome.serp_queries_run,
        outcome.co_citation_prompts_run, outcome.candidates_considered,
        [candidate.resolved_name() for candidate in outcome.candidates],
    )


def the_model_names_zendesk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model's side of detection, faked: it names one brand the searches also found."""

    async def run_seed_prompts(prompts: list[str], **kwargs: Any) -> list[CoCitationResult]:
        hit = CoCitationHit(name="Zendesk", domain="zendesk.com", position=1, prompt=prompts[0])
        return [CoCitationResult(prompt=prompts[0], hits=[hit])]

    monkeypatch.setattr(cocitation_service, "run_seed_prompts", run_seed_prompts)


async def stubbed_search_many(queries: list[str], **kwargs: Any) -> list[serp.SerpResult]:
    """The suite's own stand-in for the searches, for the test that has no key to search with."""
    return [
        serp.SerpResult(
            query=q,
            hits=[
                serp.SerpHit(domain="zendesk.com", position=1, query=q),
                serp.SerpHit(domain="front.com", position=2, query=q),
            ],
        )
        for q in queries[:2]
    ]


@pytest.mark.usefixtures("real_serp")
class TestADetectionReadsItOnce:
    def test_the_detection_these_tests_run_makes_six_searches(self) -> None:
        """The premise of the counts below, from the code that builds the queries."""
        assert len(serp.build_queries(**a_detection_of_acme(), niche=None)) == 6

    async def test_six_searches_and_one_account_call(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi().install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await detect(keyed)

        assert len(fake.searches) == 6
        assert len(fake.accounts) == 1
        assert len(fake.requests) == 7
        # Every request carried the key, and no line did.
        assert {r.url.params["api_key"] for r in fake.requests} == {API_KEY}
        assert_nothing_secret(events)
        assert [fields(e) for e in named(events, "serp.quota_left")] == [
            {"left": 184, "plan_searches": 250}
        ]
        assert named(events, "serp.quota_low") == []

    async def test_a_low_figure_is_one_warning_for_the_detection(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi(account=lambda: ok_account(
            plan_searches_left=49, total_searches_left=49,
        )).install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        with structlog.testing.capture_logs() as events:
            await detect(keyed)

        assert len(fake.accounts) == 1
        assert [fields(e) for e in named(events, "serp.quota_low")] == [
            {"left": 49, "plan_searches": 250}
        ]
        assert [fields(e) for e in named(events, "serp.quota_left")] == [
            {"left": 49, "plan_searches": 250}
        ]

    async def test_a_detection_without_an_industry_makes_three_searches_and_still_one_read(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi().install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        await detection.detect_from_facts(brand_name="Acme", domain="acme.com", settings=keyed)

        assert (len(fake.searches), len(fake.accounts)) == (3, 1)

    async def test_the_account_is_read_after_every_search_so_the_figure_reflects_them(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi().install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        await detect(keyed)

        assert [r.url.path for r in fake.requests] == [SEARCH_PATH] * 6 + [ACCOUNT_PATH]

    async def test_a_retried_search_does_not_make_a_second_read(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """Every search times out and is asked again: twelve search requests, one account call."""
        fake = FakeSerpApi(search=lambda: httpx.ReadTimeout("no answer")).install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        outcome = await detect(keyed)

        assert (len(fake.searches), len(fake.accounts)) == (12, 1)
        assert outcome.serp_queries_run == 0

    async def test_the_entry_point_the_route_and_the_chain_call_reads_once_too(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        fake = FakeSerpApi().install(monkeypatch)
        the_model_names_zendesk(monkeypatch)
        row = Client(
            id="clnt_test", agency_id="agcy_test", name="Acme Holdings", brand_name="Acme",
            domain="acme.com", kind=ClientKind.PROSPECT,
            classification_status=ClassificationStatus.CLASSIFIED,
            industry="project software", industry_niche=None,
        )

        await detection.detect_for_client(row, settings=keyed)

        assert (len(fake.searches), len(fake.accounts)) == (6, 1)


@pytest.mark.usefixtures("real_serp")
class TestAFailedReadDoesNotTouchTheDetection:
    @pytest.mark.parametrize(("failure", "kind"), FAILURES)
    async def test_every_failure_leaves_the_outcome_as_it_was_and_writes_one_info_line(
        self, failure: Callable[[], Step], kind: str,
        monkeypatch: pytest.MonkeyPatch, keyed: Settings,
    ) -> None:
        the_model_names_zendesk(monkeypatch)
        FakeSerpApi().install(monkeypatch)
        baseline = summary(await detect(keyed))

        fake = FakeSerpApi(account=failure).install(monkeypatch)
        with structlog.testing.capture_logs() as events:
            outcome = await detect(keyed)

        assert summary(outcome) == baseline
        assert len(fake.accounts) == 1
        assert [e for e in events if e["event"].startswith("serp.quota")] == [unavailable(kind)]
        assert_nothing_secret(events)

    async def test_a_detection_with_no_key_reads_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The searches are the suite's stand-in here (with no key the real ones raise): the
        read sends nothing and writes its one info line, and the detection is untouched."""
        monkeypatch.setattr(serp, "search_many", stubbed_search_many)
        fake = FakeSerpApi().install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        with structlog.testing.capture_logs() as events:
            outcome = await detect(settings_with(None))

        assert fake.requests == []
        assert outcome.candidates
        assert [e for e in events if e["event"].startswith("serp.quota")] == [
            unavailable("no_key")
        ]

    async def test_a_read_that_raises_cannot_fail_a_detection(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """`quota_left` never raises. This is for the day it does: a line about the quota is not
        worth a competitor set."""
        the_model_names_zendesk(monkeypatch)
        FakeSerpApi().install(monkeypatch)
        baseline = summary(await detect(keyed))

        async def broken(settings: Settings | None = None) -> int | None:
            raise RuntimeError("the read blew up")

        monkeypatch.setattr(serp, "quota_left", broken)

        assert summary(await detect(keyed)) == baseline


@pytest.mark.usefixtures("real_serp", "small_timeouts")
class TestTheReadCostsDetectionNothingItWasNotWaitingFor:
    async def test_the_account_is_asked_while_the_model_is_still_answering(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """Deterministic, no clock: the model's side waits (up to three seconds) for the account
        request to arrive. If the read waited its turn behind the model, it would not arrive in
        time and the model's side would record that it did not."""
        fake = FakeSerpApi().install(monkeypatch)
        overlapped: list[bool] = []

        async def run_seed_prompts(prompts: list[str], **kwargs: Any) -> list[CoCitationResult]:
            try:
                await asyncio.wait_for(fake.account_started.wait(), 3.0)
                overlapped.append(True)
            except TimeoutError:
                overlapped.append(False)
            return [CoCitationResult(prompt=p) for p in prompts]

        monkeypatch.setattr(cocitation_service, "run_seed_prompts", run_seed_prompts)

        await detect(keyed)

        assert overlapped == [True]

    async def test_a_hung_account_holds_a_detection_for_no_longer_than_its_deadline(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """The worst the read can do when the model's side is instant: the whole of its bound."""
        FakeSerpApi(account=lambda: never).install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        started = time.perf_counter()
        with structlog.testing.capture_logs() as events:
            outcome = await asyncio.wait_for(detect(keyed), WATCHDOG_S)
        elapsed = time.perf_counter() - started

        assert outcome.candidates, "the detection went on and finished"
        assert SMALL_DEADLINE_S - 0.05 <= elapsed <= SMALL_DEADLINE_S + SLACK_S, elapsed
        assert [e for e in events if e["event"].startswith("serp.quota")] == [unavailable("total")]

    async def test_a_model_that_outlasts_the_read_is_not_delayed_by_it(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """The read starts when the searches are done and has ended (its line is written)
        before the model's side returns, so the detection ends when the model's side does.
        Ordered, not timed: a marker is put among the lines at the moment the model returns."""
        FakeSerpApi(account=lambda: never).install(monkeypatch)

        with structlog.testing.capture_logs() as events:

            async def run_seed_prompts(
                prompts: list[str], **kwargs: Any
            ) -> list[CoCitationResult]:
                await asyncio.sleep(3 * SMALL_DEADLINE_S)  # well past the read's deadline
                events.append({"event": "model returned", "log_level": "marker"})
                return [CoCitationResult(prompt=p) for p in prompts]

            monkeypatch.setattr(cocitation_service, "run_seed_prompts", run_seed_prompts)
            await asyncio.wait_for(detect(keyed), WATCHDOG_S)

        order = [
            e["event"] for e in events if e["event"] in ("serp.quota_unavailable", "model returned")
        ]
        assert order == ["serp.quota_unavailable", "model returned"]


@pytest.mark.usefixtures("real_serp")
class TestNothingIsLeftRunning:
    @staticmethod
    def leaked(before: set[asyncio.Task[Any]]) -> list[asyncio.Task[Any]]:
        """Tasks started since `before` that are still running, this test's own aside."""
        return [
            t for t in asyncio.all_tasks() - before
            if t is not asyncio.current_task() and not t.done()
        ]

    async def settle(self, before: set[asyncio.Task[Any]]) -> None:
        """Give a cancellation the moments it needs to land, and no more. A second is far under
        the read's own deadline (5 s here), so a read that was NOT cancelled is still running
        when this ends, and is found: this is not a wait for it to finish by itself."""
        deadline = time.perf_counter() + 1.0
        while self.leaked(before) and time.perf_counter() < deadline:
            await asyncio.sleep(0.01)

    async def test_a_model_that_fails_cancels_the_read_that_is_still_in_flight(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        before = asyncio.all_tasks()
        fake = FakeSerpApi(account=lambda: never).install(monkeypatch)

        async def run_seed_prompts(prompts: list[str], **kwargs: Any) -> list[CoCitationResult]:
            await asyncio.wait_for(fake.account_started.wait(), WATCHDOG_S)
            raise RuntimeError("the model's side failed")

        monkeypatch.setattr(cocitation_service, "run_seed_prompts", run_seed_prompts)

        with pytest.raises(RuntimeError, match="the model's side failed"):
            await asyncio.wait_for(detect(keyed), WATCHDOG_S)

        await self.settle(before)
        assert self.leaked(before) == []

    async def test_cancelling_a_detection_cancels_its_read(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """A scan's deadline cancels the chain; the read must go with it and not outlive it."""
        before = asyncio.all_tasks()
        fake = FakeSerpApi(account=lambda: never).install(monkeypatch)
        release = asyncio.Event()

        async def run_seed_prompts(prompts: list[str], **kwargs: Any) -> list[CoCitationResult]:
            await release.wait()
            return []

        monkeypatch.setattr(cocitation_service, "run_seed_prompts", run_seed_prompts)

        task = asyncio.ensure_future(detect(keyed))
        await asyncio.wait_for(fake.account_started.wait(), WATCHDOG_S)
        assert [t for t in self.leaked(before) if t is not task], "the read is in flight"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, WATCHDOG_S)

        await self.settle(before)
        assert self.leaked(before) == []


# ---------------------------------------------------------------------------
# a read that raised is never reported as unseen
# ---------------------------------------------------------------------------


async def turns(count: int = 10) -> None:
    """Let the event loop run `count` rounds: what a callback or a cancellation needs, no clock."""
    for _ in range(count):
        await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def watching_asyncio() -> AsyncIterator[list[dict[str, Any]]]:
    """What the event loop is asked to report as an error, and where that goes.

    The loop's handler is replaced by one that keeps each report and then does what the loop's
    own does, which is to write it to the `asyncio` logger, with the traceback. That logger is
    given a handler on stderr, as a deployed API has it (nothing configures the root logger, so
    Python's last resort writes the record to stderr). Garbage is collected first, so that what
    an earlier test left behind is not collected, and reported, inside the window.
    """
    gc.collect()
    loop = asyncio.get_running_loop()
    reported: list[dict[str, Any]] = []

    def keep_and_log(where: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        reported.append(context)
        where.default_exception_handler(context)

    on_stderr = logging.StreamHandler(sys.stderr)
    previous = loop.get_exception_handler()
    loop.set_exception_handler(keep_and_log)
    logging.getLogger("asyncio").addHandler(on_stderr)
    try:
        yield reported
    finally:
        logging.getLogger("asyncio").removeHandler(on_stderr)
        loop.set_exception_handler(previous)


class TestAReadThatRaisedIsNeverReportedAsUnseen:
    """A task that ends with an exception nobody asked for is reported when it is collected:
    asyncio's `Task exception was never retrieved`, with the traceback, on stderr. The traceback
    carries the exception's text, and an httpx error's text can carry the request's URL, which
    carries the API key. `quota_left` never raises, so this is for the day it does (the security
    review of U2, L1): whatever way the detection ends, the read's outcome is retrieved.

    Three ways for a read to end with an exception that nothing awaited. In the first two it
    raised, and then the model's side failed or the detection was cancelled, so that the `await`
    that would have retrieved it was never reached: `Task.cancel()` marks a finished task's
    exception as handled (it clears the flag before it looks at the task's state), so the
    detection's `quota.cancel()` covers these two by that effect, which nothing in the code
    says. In the third the read is still in flight when the model's side fails, and raises as
    the cancellation reaches it, after `cancel()` was called: nothing but the done callback
    retrieves that. And the ordinary fourth, a read cancelled in flight, which ends cancelled
    and has no exception to retrieve: the callback must leave it alone (asking a cancelled task
    for its exception raises, inside the callback, and the loop would report that).
    """

    async def test_left_alone_asyncio_does_write_the_text_of_an_error_nobody_asked_about(
        self, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The hazard, shown, so that the test below cannot pass for want of a line to catch."""

        async def raises() -> None:
            raise ValueError("the text of the error")

        async with watching_asyncio() as reported:
            task = asyncio.ensure_future(raises())
            await turns()
            assert task.done()
            del task
            gc.collect()
            await turns()

        assert [c["message"] for c in reported] == ["Task exception was never retrieved"]
        assert "the text of the error" in caplog.text
        assert "the text of the error" in capsys.readouterr().err

    @pytest.mark.parametrize("ending", [
        pytest.param("raised, then the model fails", id="raised-then-the-model-fails"),
        pytest.param("raised, then cancelled", id="raised-then-the-detection-is-cancelled"),
        pytest.param("raises as it is cancelled", id="raises-as-the-failing-model-cancels-it"),
        pytest.param("ends cancelled", id="is-cancelled-in-flight-by-the-failing-model"),
    ])
    async def test_asyncio_reports_nothing_about_the_read_whichever_way_it_ends(
        self, ending: str, monkeypatch: pytest.MonkeyPatch, keyed: Settings,
        caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
    ) -> None:
        read_started = asyncio.Event()
        raising = httpx.ConnectError(
            f"cannot connect to https://serpapi.com/account.json?api_key={API_KEY} for {OWNER}"
        )

        async def read_that_raises(settings: Settings | None = None) -> int | None:
            read_started.set()
            if ending.startswith("raised"):
                raise raising
            try:
                await asyncio.Event().wait()  # in flight, until the cancellation reaches it
            except asyncio.CancelledError:
                if ending == "ends cancelled":
                    raise
                raise raising from None
            return None

        async def the_model(prompts: list[str], **kwargs: Any) -> list[CoCitationResult]:
            await read_started.wait()
            await turns()  # a read that raised at once has ended, with its exception, by now
            if ending != "raised, then cancelled":
                raise RuntimeError("the model's side failed")
            await asyncio.Event().wait()  # until the detection is cancelled
            return []

        async def the_detection() -> None:
            """Ends the detection the way `ending` says. Its frames are gone when it returns."""
            if ending != "raised, then cancelled":
                with pytest.raises(RuntimeError, match="the model's side failed"):
                    await detect(keyed)
                return
            task = asyncio.ensure_future(detect(keyed))
            await asyncio.wait_for(read_started.wait(), WATCHDOG_S)
            await turns()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # The searches are the suite's stand-in (conftest), so nothing here can reach SerpApi.
        monkeypatch.setattr(serp, "quota_left", read_that_raises)
        monkeypatch.setattr(cocitation_service, "run_seed_prompts", the_model)

        async with watching_asyncio() as reported:
            with structlog.testing.capture_logs() as events:
                await the_detection()
                await turns()  # a read that was cancelled ends here
                gc.collect()  # and its task is collected here, if nothing holds it
                await turns()

        out, err = capsys.readouterr()
        # Nothing was reported about the read's task, and nothing about a callback that raised
        # (the loop's report for one is `Exception in callback ...`, with the task's repr cut
        # short by `reprlib`, so its name is not in it), and nothing at all said the text.
        assert [
            c["message"] for c in reported
            if "serp.quota_left" in repr(c) or c["message"].startswith("Exception in callback")
        ] == []
        channels = {
            "caplog": caplog.text, "stdout": out, "stderr": err,
            "structlog": json.dumps(events, default=repr),
        }
        for channel, text in channels.items():
            for needle in (API_KEY, OWNER, "serpapi.com", "api_key", "serp.quota_left"):
                assert needle not in text, (channel, needle)


# ---------------------------------------------------------------------------
# the suite itself reaches nothing
# ---------------------------------------------------------------------------


class TestTheSuiteNeverReadsTheAccountByAccident:
    async def test_detection_under_the_suites_defaults_opens_no_client_for_the_account(
        self, monkeypatch: pytest.MonkeyPatch, keyed: Settings
    ) -> None:
        """conftest's autouse `stub_chain_externals` stands in for the read, as it does for the
        searches: every detection a test runs, the chain's and the route's included, would
        otherwise spend a real call on whatever key the settings hold. No client is opened."""
        opened: list[Any] = []

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            opened.append(kwargs.get("timeout"))
            kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(500))
            return REAL_ASYNC_CLIENT(*args, **kwargs)

        monkeypatch.setattr(serp.httpx, "AsyncClient", factory)

        outcome = await detect(keyed)

        assert opened == []
        assert outcome.candidates, "the suite's stubs produced a detection"
        assert serp.quota_left is not REAL_QUOTA_LEFT


# ---------------------------------------------------------------------------
# through the route
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("real_serp")
class TestThroughTheDetectRoute:
    @pytest.mark.parametrize(
        "account_step",
        [lambda: ok_account(), lambda: httpx.Response(500, content=b"down"),
         lambda: httpx.ReadTimeout("no answer")],
        ids=["read", "500", "timeout"],
    )
    async def test_pressing_detect_reads_the_account_once_and_the_answer_is_the_same(
        self, account_step: Callable[[], Step], client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        signed_up = await client.post(
            f"{BASE}/auth/sign-up",
            json={
                "agencyName": "Quota Test Agency", "fullName": "Operator",
                "email": "quota@test.example", "password": "correct-horse-battery-staple",
            },
        )
        assert signed_up.status_code == 201, signed_up.text
        made = await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )
        assert made.status_code == 201, made.text
        client_id = made.json()["id"]

        fake = FakeSerpApi(account=account_step).install(monkeypatch)
        the_model_names_zendesk(monkeypatch)

        resp = await client.post(f"{BASE}/clients/{client_id}/competitors/detect")

        assert resp.status_code == 201, resp.text
        # No industry, so the three brand-anchored queries.
        assert (len(fake.searches), len(fake.accounts)) == (3, 1)
        body = resp.json()
        assert body["competitors"], "the set came back whatever the read did"
        assert body["serpQueriesRun"] == 3

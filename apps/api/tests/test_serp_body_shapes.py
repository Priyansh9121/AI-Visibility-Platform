"""A search answer that is not a page of results is `SERP_BAD_JSON`, not an exception — 3.66.

2026-10-04. `search` promises never to raise for an upstream failure. `_judge` (S2) took a
200's JSON to be an object holding a list of objects without looking, so a body that was a
list, a string, a number or `null`, or an object whose `organic_results` was not a list or
held something that is not an object, raised `AttributeError` or `TypeError` out of `search`,
and out of `search_many` with it (`asyncio.gather` re-raises the first). No `serp.completed`
line, nothing counted for the scan's total (U1's `usage.record_serp_search` is the line after
the parse), and a whole detection lost to one odd answer. A list or a string that merely had
the word `error` in it was wrong more quietly: the test for an `error` key is `in`, which is
true of both, so it was answered `SERP_API_ERROR` and SerpApi had reported no error.

The same promise, one level down. A result whose `link` was a number, a list, an object or a
bool raised `AttributeError` in `registrable_domain`. A result whose `position` was a string, a
list or an object went on to scoring and raised `TypeError` there, and an integer of 309 digits
or more raised `OverflowError`, out of `detect_from_facts` and after the searches were paid for.
And a body nested past what the JSON parser goes (9,997 levels on Python 3.12) raised
`RecursionError`, which is a `RuntimeError`, so `except ValueError` let it out of `search`.

What a 200 gives, by what it holds:

    the body                                   the result
    -----------------------------------------  ---------------------------------------------
    not JSON, or nested past the parser        ok=False, SERP_BAD_JSON, no hits
    JSON that is not an object: a list, a      ok=False, SERP_BAD_JSON, no hits
      string, a number, true, false, null
    an object with an `error`                  ok=False, SERP_API_ERROR (as it always was)
    `organic_results` absent, null or []       ok=True, no hits: Google had nothing (as it was)
    `organic_results` not a list: an object,   ok=False, SERP_BAD_JSON, no hits
      a string, a number, true, false
    `organic_results` a list in which some     ok=True: the objects are read as they always were
      items are not objects                      and the other items are skipped
    `organic_results` a list, not empty, in    ok=False, SERP_BAD_JSON, no hits
      which no item is an object
    an item whose `link` is not a string       skipped, as an item with no usable link is
    an item whose `position` is not a whole    its place on the page: what an item with no
      number from 1 to `serp.MAX_POSITION`       `position` has always had

The bodies are each one `serp.completed` line (the status, `hits`, `error_code`), asked for once,
and one answered request in the scan's total: an answered error for a body that could not be
read, as U1 already counted the body that was never JSON (`test_serp_usage.py`), and an `ok` one
for a page that could.

Three choices, and why.

* An item that is not an object is SKIPPED and the objects among its neighbours are kept, as the
  module already skips an item with no usable link. A 200 is a search SerpApi counts, and the plan
  is 250 a month shared with production, so one stray item must not throw away the page it came on;
  and a hit is a fact taken from one item alone, so a bad neighbour cannot make a good one wrong.
  The skipped item keeps its place in the page's numbering, as one with no link always did.
* But a list in which NOT ONE item is an object is not a page of results, and an `ok` search with
  no hits would say it was. `decide_detection` counts the `ok` searches (`both_signals_ran`), and
  a search that could not be read must not turn "we could not tell" into "SerpApi ran and found
  nothing". So that is `SERP_BAD_JSON`, a failed search, and the detection reads the signal as one
  that did not run. One object, however empty, makes it a list of results; `[]` is Google having
  nothing to say.
* A `position` is a whole number from 1 to 1000, or it is not a position. A whole number in
  that range is the provider's rank and is kept, `2.0` included (JSON has one kind of number, so
  it is the whole number 2, and it comes out an `int`). Anything else, a bool, a fraction, NaN,
  infinity, a string, zero, a negative and a number past the ceiling among it, is the item's place
  on the page, counting the items that were skipped: the number an item with no `position` has
  always had. The ceiling is there because Google answers at most 100 results to a request and
  `search` asks for 10, so no real rank is near it, and an absurd one must not reach scoring.

The provider is faked as in `test_serp_completed.py`: a `MockTransport` scripted one step per
request. Nothing here reaches SerpApi, whose free plan of 250 searches a month is shared with
production.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog
from pydantic import SecretStr

from avp_api.config import Settings
from avp_api.services import cocitation as cocitation_service
from avp_api.services import competitors as detection
from avp_api.services import serp, usage
from avp_api.services.cocitation import CoCitationHit, CoCitationResult
from avp_api.services.serp import SerpResult, search, search_many

# `search` and `search_many` are imported by name, on purpose: conftest's autouse
# `stub_chain_externals` replaces `serp.search_many` for every test. The name bound at import is
# the real one (as in test_serp_completed.py).

KEY = "SERPKEYSENTINEL-3a91c5e07d24b86f"
URL_WITH_KEY = f"{serp.SERPAPI_URL}?engine=google&q=x&api_key={KEY}"
BAD = "SERP_BAD_JSON"
COMPLETED = "serp.completed"
RETRIED = "serp.retried"
# The five facts and nothing else; structlog adds `event` and `log_level` itself.
COMPLETED_FACTS = {"elapsed_ms", "attempts", "hits", "status", "error_code"}

GOOD = {"position": 1, "link": "https://front.com/a", "title": "Front"}

# The real client, taken at import, before any test has swapped it: `install` builds on this one, so
# a test that installs twice is answered by the second handler and not the first.
REAL_ASYNC_CLIENT = httpx.AsyncClient

# A 200 whose JSON is not an object. Each of these raised before: `.get` on a list or a string,
# `in` on a number, `true`, `false` or `null`.
NOT_AN_OBJECT = [
    pytest.param([], id="list-empty"),
    pytest.param([1, 2, 3], id="list"),
    pytest.param([{"organic_results": [GOOD]}], id="list-holding-a-page"),
    pytest.param("front.com", id="string"),
    pytest.param("", id="string-empty"),
    pytest.param(5, id="number"),
    pytest.param(0, id="number-zero"),
    pytest.param(1.5, id="number-fraction"),
    pytest.param(True, id="true"),
    pytest.param(False, id="false"),
    pytest.param(None, id="null"),
    # `"error" in payload` is true of both of these, and they were answered SERP_API_ERROR.
    pytest.param(["error"], id="list-with-the-word-error"),
    pytest.param("an error occurred", id="string-with-the-word-error"),
]

# An object, so it passes the test above, whose `organic_results` is not a list. The falsy ones
# did not raise before (they were taken for an empty page); they are not a list either.
RESULTS_NOT_A_LIST = [
    pytest.param({"organic_results": {"1": GOOD}}, id="results-object"),
    pytest.param({"organic_results": {}}, id="results-empty-object"),
    pytest.param({"organic_results": "front.com"}, id="results-string"),
    pytest.param({"organic_results": ""}, id="results-empty-string"),
    pytest.param({"organic_results": 5}, id="results-number"),
    pytest.param({"organic_results": 0}, id="results-zero"),
    pytest.param({"organic_results": True}, id="results-true"),
    pytest.param({"organic_results": False}, id="results-false"),
]

# A list that is not empty and has no object in it.
RESULTS_NONE_AN_OBJECT = [
    pytest.param({"organic_results": ["front.com", "kayako.com"]}, id="results-all-strings"),
    pytest.param({"organic_results": [1, 2]}, id="results-all-numbers"),
    pytest.param({"organic_results": [None]}, id="results-null"),
    pytest.param({"organic_results": [[GOOD]]}, id="results-a-list-in-a-list"),
    pytest.param({"organic_results": ["a", None, 3, [], True]}, id="results-a-mixture"),
]

BAD_BODIES = [*NOT_AN_OBJECT, *RESULTS_NOT_A_LIST, *RESULTS_NONE_AN_OBJECT]

# A result item's `link` that is not a string. `registrable_domain` takes a string and raised
# `AttributeError` for each truthy one; the falsy ones were always taken for no link (`or ""`).
ODD_LINKS = [
    pytest.param(5, id="number"),
    pytest.param(1.5, id="fraction"),
    pytest.param(True, id="true"),
    pytest.param(["https://front.com/a"], id="list"),
    pytest.param({"url": "https://front.com/a"}, id="object"),
    pytest.param(0, id="zero"),
    pytest.param(False, id="false"),
    pytest.param([], id="empty-list"),
    pytest.param({}, id="empty-object"),
    pytest.param(None, id="null"),
]

# The shipped ceiling on a result's `position`, pinned here as the numbers of `serp.py`'s bound
# are pinned in `test_serp_search.py`: the cases below are built on it, and one test reads it off
# `serp`.
CEILING = 1000

# A result item's `position` that is not a whole number from 1 to the ceiling. Scoring takes
# `1 / sqrt(max(1, position))` and raised `TypeError` for a string, a list or an object, and
# `OverflowError` for an integer of 309 digits or more. A bool, a fraction, NaN, infinity, a
# negative and zero did not raise, and are not ranks either. The falsy ones were always given the
# item's place on the page (`or index`); so is every one of these now.
ODD_POSITIONS = [
    pytest.param("1", id="string"),
    pytest.param("first", id="string-word"),
    pytest.param([1], id="list"),
    pytest.param({"rank": 1}, id="object"),
    pytest.param(True, id="true"),
    pytest.param(1.5, id="fraction"),
    pytest.param(-1, id="negative"),
    pytest.param(-2.0, id="negative-float"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(CEILING + 1, id="past-the-ceiling"),
    pytest.param(1e300, id="float-past-the-ceiling"),
    pytest.param(10**400, id="more-digits-than-a-float-holds"),
    pytest.param(0, id="zero"),
    pytest.param(0.0, id="zero-float"),
    pytest.param(False, id="false"),
    pytest.param(None, id="null"),
]

# One page as a provider might send it, item by item: the link and the position sent, and the link
# and position the item is worth. An unreadable link is worth none; an odd position is worth the
# item's place on the page, which is its number in this list; a rank that is a whole number is
# worth itself, even when it is not the item's place (the last row).
ODD_PAGE: list[tuple[Any, Any, str, int]] = [
    ("https://zendesk.com/a", "1", "https://zendesk.com/a", 1),
    (5, 2, "", 2),
    ("https://front.com/b", [3], "https://front.com/b", 3),
    (["https://kayako.com/c"], {"rank": 4}, "", 4),
    ("https://kayako.com/d", True, "https://kayako.com/d", 5),
    ("https://helpscout.com/e", 1.5, "https://helpscout.com/e", 6),
    ("https://freshdesk.com/f", -4, "https://freshdesk.com/f", 7),
    ("https://intercom.com/g", 10**400, "https://intercom.com/g", 8),
    ("https://gorgias.com/h", float("nan"), "https://gorgias.com/h", 9),
    ("https://hubspot.com/i", 12, "https://hubspot.com/i", 12),
]

# How deeply nested a body is when the JSON parser refuses it: the parser stops at 9,997 levels on
# Python 3.12.10, with `RecursionError`, which is a `RuntimeError` and not a `ValueError`. This is
# twenty times that, and the body is under a megabyte and refused at once. Every shape is wrong
# whether or not a parser would take it (a list at the top, or no object in `organic_results`).
DEEP = 200_000
NESTED_SHAPES = ["arrays", "objects-in-an-array", "inside-organic-results"]


def body(value: Any) -> httpx.Response:
    """A 200 whose body is `value` as JSON.

    Not `httpx.Response(json=value)`: that sends no body at all for `None`, and `null` has to
    be the four bytes to be the case it is.
    """
    return httpx.Response(
        200, content=json.dumps(value).encode(), headers={"content-type": "application/json"}
    )


def page(*domains: str) -> httpx.Response:
    return body({"organic_results": [
        {"position": i, "link": f"https://{d}/page", "title": f"Title {i}"}
        for i, d in enumerate(domains, start=1)
    ]})


def nested(shape: str) -> httpx.Response:
    """A 200 whose body is nested `DEEP` levels, in one of `NESTED_SHAPES`."""
    if shape == "arrays":
        content = b"[" * DEEP + b"]" * DEEP
    elif shape == "objects-in-an-array":
        content = b"[" + b'{"a":' * DEEP + b"1" + b"}" * DEEP + b"]"
    else:
        content = b'{"organic_results":' + b"[" * DEEP + b"]" * DEEP + b"}"
    return httpx.Response(200, content=content, headers={"content-type": "application/json"})


def install(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Make every client `search` opens talk to `handler` instead of SerpApi."""
    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(serp.httpx, "AsyncClient", factory)


class Script:
    """SerpApi as `search` sees it: each request answered by the next step of its script.

    A step is a response, or an exception to raise (what a transport does when no answer came).
    A request beyond the script fails the test.
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

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Script:
        install(monkeypatch, self.handler)
        return self


@pytest.fixture
def keyed() -> Settings:
    return Settings(
        _env_file=None, environment="test",
        database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment", serpapi_key=SecretStr(KEY),
    )


@pytest.fixture(autouse=True)
def fast_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """So that a body wrongly taken for a failure to answer would cost a test nothing to find."""
    monkeypatch.setattr(serp, "RETRY_PAUSE_S", 0.01)


def serp_events(events: list[dict[str, Any]]) -> list[str]:
    """The name of every `serp.*` event, in the order it was logged."""
    return [e["event"] for e in events if e["event"].startswith("serp.")]


def completed(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == COMPLETED]


def counts(scan: usage.ScanUsage) -> tuple[int, int, int, int]:
    """The scan's SerpApi counts: requests made, answered ok, answered with an error, no answer."""
    f = scan.log_fields()
    return (f["serpapi_attempted"], f["serpapi_ok"], f["serpapi_error"], f["serpapi_no_answer"])


def assert_no_exception_in(events: list[dict[str, Any]]) -> None:
    """No event carries a traceback or the exception: either would render its message."""
    assert [e["event"] for e in events if "exc_info" in e or "exception" in e] == []


def assert_one_unreadable_answer(result: SerpResult, events: list[dict[str, Any]]) -> None:
    """What every one of `BAD_BODIES` gives: no hits, the code, and exactly one `serp.completed`."""
    assert result.ok is False
    assert result.error_code == BAD
    assert result.hits == []
    assert result.titles == []
    # The one serp line: an answer was got, so no `serp.attempts_failed`, and nothing was retried.
    assert serp_events(events) == [COMPLETED]
    (line,) = completed(events)
    assert line["log_level"] == "info"
    assert set(line) - {"event", "log_level"} == COMPLETED_FACTS
    assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (1, 0, 200, BAD)
    assert type(line["elapsed_ms"]) is int and line["elapsed_ms"] >= 0
    assert_no_exception_in(events)


# ---------------------------------------------------------------------------
# a body that cannot be read
# ---------------------------------------------------------------------------


class TestABodyThatIsNotAnObject:
    @pytest.mark.parametrize("value", NOT_AN_OBJECT)
    async def test_is_serp_bad_json_with_one_completed_line_and_is_asked_for_once(
        self, value: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(body(value)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert_one_unreadable_answer(result, events)
        # An answer, whatever it says, is not asked for twice.
        assert len(script.requests) == 1
        assert script.steps == []


class TestAnObjectWhoseResultsAreNotAList:
    @pytest.mark.parametrize("value", RESULTS_NOT_A_LIST)
    async def test_is_serp_bad_json_with_one_completed_line_and_is_asked_for_once(
        self, value: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(body(value)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert_one_unreadable_answer(result, events)
        assert len(script.requests) == 1
        assert script.steps == []


class TestAListOfResultsInWhichNoneIsAnObject:
    @pytest.mark.parametrize("value", RESULTS_NONE_AN_OBJECT)
    async def test_is_serp_bad_json_and_not_a_search_that_ran_and_found_nothing(
        self, value: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(body(value)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        # `ok=False`: `decide_detection` counts the `ok` searches, and this one told it nothing.
        assert_one_unreadable_answer(result, events)
        assert len(script.requests) == 1
        assert script.steps == []


class TestABodyNestedPastTheParser:
    """The parser stops at a depth of its own and raises `RecursionError`, which is a
    `RuntimeError` and not a `ValueError`, so the `except ValueError` round `response.json()` let
    it out of `search`."""

    @pytest.mark.parametrize("shape", NESTED_SHAPES)
    async def test_is_serp_bad_json_with_one_completed_line_and_is_asked_for_once(
        self, shape: str, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(nested(shape)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert_one_unreadable_answer(result, events)
        assert len(script.requests) == 1
        assert script.steps == []

    @pytest.mark.parametrize("shape", NESTED_SHAPES)
    async def test_the_scans_total_counts_it_as_an_answered_error(
        self, shape: str, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(nested(shape)).install(monkeypatch)

        with usage.scan_scope("scan_1") as scan:
            result = await search("q", settings=keyed)

        assert result.error_code == BAD
        assert counts(scan) == (1, 0, 1, 0)
        assert "serpapi_billed_or_unknown" not in scan.log_fields()


# ---------------------------------------------------------------------------
# a page that can be read, in whole or in part
# ---------------------------------------------------------------------------


class TestAListWithItemsThatAreNotObjectsAmongThoseThatAre:
    async def test_the_objects_are_kept_and_the_rest_skipped(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = [
            {"position": 1, "link": "https://front.com/a", "title": "Front"},
            "a string", None, 7, ["a", "list"], True,
            {"position": 6, "link": "https://kayako.com/b", "title": "Kayako"},
        ]
        script = Script(body({"organic_results": results})).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert result.ok is True
        assert result.error_code is None
        assert [(h.domain, h.position, h.query) for h in result.hits] == [
            ("front.com", 1, "q"), ("kayako.com", 6, "q"),
        ]
        assert result.titles == ["Front", "Kayako"]
        assert len(script.requests) == 1
        assert serp_events(events) == [COMPLETED]
        (line,) = completed(events)
        assert set(line) - {"event", "log_level"} == COMPLETED_FACTS
        assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (
            1, 2, 200, None,
        )
        assert_no_exception_in(events)

    async def test_a_skipped_item_keeps_its_place_in_the_numbering_as_one_with_no_link_does(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`position` falls back to the item's place in the page, and a skipped item is a place."""
        Script(
            body({"organic_results": [
                "a string", {"link": "https://front.com/a"}, {"link": "https://kayako.com/b"},
            ]}),
            body({"organic_results": [
                {"link": ""}, {"link": "https://front.com/a"}, {"link": "https://kayako.com/b"},
            ]}),
        ).install(monkeypatch)

        not_an_object = await search("q", settings=keyed)
        no_link = await search("q", settings=keyed)

        assert [(h.domain, h.position) for h in not_an_object.hits] == [
            ("front.com", 2), ("kayako.com", 3),
        ]
        assert [(h.domain, h.position) for h in no_link.hits] == [
            (h.domain, h.position) for h in not_an_object.hits
        ]

    async def test_a_scan_counts_it_as_an_answered_search_that_ran(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(body({"organic_results": [GOOD, "a string"]})).install(monkeypatch)

        with usage.scan_scope("scan_1") as scan:
            result = await search("q", settings=keyed)

        assert result.ok is True
        assert counts(scan) == (1, 1, 0, 0)
        assert scan.log_fields()["serpapi_billed_or_unknown"] == 1


class TestAnItemWhoseLinkIsNotAString:
    """`registrable_domain` takes a string. Given a number, a list, an object or a bool it raised
    `AttributeError`, out of `search` and so out of `search_many` and the detection. A link that
    nobody can read is no usable link, and the item is skipped as one with none is."""

    @pytest.mark.parametrize("link", ODD_LINKS)
    async def test_it_is_skipped_like_an_item_with_no_usable_link_and_keeps_its_place(
        self, link: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = [
            {"position": 1, "link": link, "title": "Odd"},
            {"link": "https://front.com/a", "title": "Front"},
            {"link": "https://kayako.com/b", "title": "Kayako"},
        ]
        script = Script(body({"organic_results": results})).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert result.ok is True
        assert result.error_code is None
        # The odd item is no hit and its title is not kept. The other two have no `position`, so
        # they are numbered by their place on the page, and the odd item holds the first place.
        assert [(h.domain, h.position) for h in result.hits] == [
            ("front.com", 2), ("kayako.com", 3),
        ]
        assert result.titles == ["Front", "Kayako"]
        assert len(script.requests) == 1
        assert serp_events(events) == [COMPLETED]
        (line,) = completed(events)
        assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (
            1, 2, 200, None,
        )
        assert_no_exception_in(events)

    async def test_a_page_of_such_items_is_a_page_with_nothing_on_it_and_not_a_bad_body(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """They are objects, so the list is a list of results, as a list of objects with no link
        at all always was."""
        results = [{"position": 1, "link": 5}, {"position": 2, "link": ["https://front.com/a"]}]
        Script(body({"organic_results": results})).install(monkeypatch)

        with (
            structlog.testing.capture_logs() as events,
            usage.scan_scope("scan_1") as scan,
        ):
            result = await search("q", settings=keyed)

        assert result.ok is True
        assert result.error_code is None
        assert result.hits == []
        (line,) = completed(events)
        assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (
            1, 0, 200, None,
        )
        assert counts(scan) == (1, 1, 0, 0)


class TestAnItemWhosePositionIsNotAWholeNumber:
    """Scoring takes the rank as an int (`competitors._position_weight`), and a position of
    another kind raised there, out of `detect_from_facts` and after the searches were paid for. A
    `position` is a whole number from 1 to `serp.MAX_POSITION` or it is not a position, and the
    item then has its place on the page: what an item with no `position` has always had, and the
    same numbering (it counts the items that were skipped)."""

    @pytest.mark.parametrize("position", ODD_POSITIONS)
    async def test_it_gets_the_items_place_on_the_page_and_scoring_can_take_it(
        self, position: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The odd item is the third: after a result that is read and one that is skipped.
        results = [GOOD, "a string", {"link": "https://kayako.com/b", "position": position}]
        Script(body({"organic_results": results})).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert result.ok is True
        assert [(h.domain, h.position) for h in result.hits] == [
            ("front.com", 1), ("kayako.com", 3),
        ]
        for hit in result.hits:
            assert type(hit.position) is int
            # What scoring does with it, which is what raised.
            assert 0 < detection._position_weight(hit.position) <= 1
        (line,) = completed(events)
        assert (line["hits"], line["error_code"]) == (2, None)
        assert_no_exception_in(events)

    @pytest.mark.parametrize(
        ("sent", "kept"),
        [(1, 1), (7, 7), (CEILING, CEILING), (4.0, 4)],
        ids=["first", "seventh", "the-ceiling", "a-whole-float"],
    )
    async def test_a_whole_number_from_one_to_the_ceiling_is_the_providers_rank_and_is_kept(
        self, sent: Any, kept: int, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The item is third on the page, so a rank that is kept is not the item's place. JSON has
        one kind of number, so `4.0` is the whole number 4, and it is an `int` that comes out."""
        results = ["a string", "another", {"link": "https://front.com/a", "position": sent}]
        Script(body({"organic_results": results})).install(monkeypatch)

        result = await search("q", settings=keyed)

        assert [(h.domain, h.position) for h in result.hits] == [("front.com", kept)]
        assert type(result.hits[0].position) is int

    def test_the_ceiling_is_a_thousand(self) -> None:
        """Google answers at most 100 results to a request and `search` asks for 10, so no real
        rank is near it. It is there so that an absurd number cannot reach scoring."""
        assert serp.MAX_POSITION == CEILING == 1000


class TestWhatWasNeverWrongIsUnchanged:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({}, id="no-results-key"),
            pytest.param({"organic_results": None}, id="null"),
            pytest.param({"organic_results": []}, id="empty-list"),
            pytest.param(
                {"organic_results": [{"position": 1}, {"link": "http://127.0.0.1/b"}]},
                id="objects-with-no-usable-link",
            ),
            # One object is enough to make it a list of results, however little is in it.
            pytest.param({"organic_results": [{"position": 1}, "a string"]}, id="one-empty-object"),
        ],
    )
    async def test_a_page_with_nothing_on_it_is_a_search_that_ran_and_found_nothing(
        self, payload: dict[str, Any], keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(body(payload)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert result.ok is True
        assert result.error_code is None
        assert result.hits == []
        assert serp_events(events) == [COMPLETED]
        (line,) = completed(events)
        assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (
            1, 0, 200, None,
        )

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"error": "Google hasn't returned any results."}, id="error"),
            # The provider's own error, whatever else the object holds.
            pytest.param({"error": "No luck.", "organic_results": "a string"}, id="error-and-junk"),
        ],
    )
    async def test_an_object_with_an_error_is_still_the_providers_own_error(
        self, payload: dict[str, Any], keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Script(body(payload)).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert (result.ok, result.error_code) == (False, "SERP_API_ERROR")
        (line,) = completed(events)
        assert (line["hits"], line["status"], line["error_code"]) == (0, 200, "SERP_API_ERROR")

    @pytest.mark.parametrize(
        ("status", "code"),
        [(401, "SERP_AUTH_FAILED"), (429, "SERP_RATE_LIMITED"), (500, "SERP_HTTP_500")],
    )
    async def test_a_status_that_is_not_200_is_judged_before_its_body_is_read(
        self, status: int, code: str, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A list as the body of a 429 is a 429, and never a bad body."""
        Script(httpx.Response(status, content=b"[1, 2, 3]")).install(monkeypatch)

        with structlog.testing.capture_logs() as events:
            result = await search("q", settings=keyed)

        assert (result.ok, result.error_code) == (False, code)
        (line,) = completed(events)
        assert (line["status"], line["error_code"]) == (status, code)


# ---------------------------------------------------------------------------
# several queries
# ---------------------------------------------------------------------------


class TestSearchManyWithOneUnreadableAnswer:
    QUERIES = ["q0", "q1", "the-odd-one", "q3", "q4", "q5"]

    @staticmethod
    def _install_one_odd_answer(
        monkeypatch: pytest.MonkeyPatch, odd: Callable[[], httpx.Response]
    ) -> list[str]:
        """Every query is answered with a page of two hits but `the-odd-one`, which gets `odd()`."""
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            query = request.url.params["q"]
            asked.append(query)
            return odd() if query == "the-odd-one" else page("front.com", "kayako.com")

        install(monkeypatch, handler)
        return asked

    @pytest.mark.parametrize("odd", BAD_BODIES)
    async def test_six_queries_with_one_unreadable_answer_are_still_six_results(
        self, odd: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = self._install_one_odd_answer(monkeypatch, lambda: body(odd))

        with structlog.testing.capture_logs() as events:
            results = await search_many(self.QUERIES, settings=keyed)

        # Six results, in the order the queries were given, and only the odd one failed.
        assert [r.query for r in results] == self.QUERIES
        assert [r.error_code for r in results] == [None, None, BAD, None, None, None]
        assert [len(r.hits) for r in results] == [2, 2, 0, 2, 2, 2]
        assert sorted(asked) == sorted(self.QUERIES)
        # One `serp.completed` for each of the six, and nothing else from the module.
        assert serp_events(events) == [COMPLETED] * 6
        assert Counter(line["error_code"] for line in completed(events)) == {None: 5, BAD: 1}
        assert_no_exception_in(events)

    @pytest.mark.parametrize("shape", NESTED_SHAPES)
    async def test_six_queries_with_one_nested_past_the_parser_are_still_six_results(
        self, shape: str, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_one_odd_answer(monkeypatch, lambda: nested(shape))

        with structlog.testing.capture_logs() as events:
            results = await search_many(self.QUERIES, settings=keyed)

        assert [r.error_code for r in results] == [None, None, BAD, None, None, None]
        assert [len(r.hits) for r in results] == [2, 2, 0, 2, 2, 2]
        assert serp_events(events) == [COMPLETED] * 6
        assert_no_exception_in(events)

    async def test_six_queries_with_one_page_of_odd_fields_are_still_six_results(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        odd_page = {"organic_results": [
            {"position": 1, "link": 5},
            {"position": "2", "link": "https://zendesk.com/a"},
            {"position": [3], "link": ["https://front.com/b"]},
        ]}
        self._install_one_odd_answer(monkeypatch, lambda: body(odd_page))

        results = await search_many(self.QUERIES, settings=keyed)

        # Read, and not a failure: the page has one result that can be used, and it is numbered by
        # its place on the page because the position it came with is not a number.
        assert [r.error_code for r in results] == [None] * 6
        assert [len(r.hits) for r in results] == [2, 2, 1, 2, 2, 2]
        assert [(h.domain, h.position) for h in results[2].hits] == [("zendesk.com", 2)]

    async def test_the_scans_total_has_six_requests_five_ok_and_one_answered_with_an_error(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_one_odd_answer(monkeypatch, lambda: body([]))

        with usage.scan_scope("scan_1") as scan:
            results = await search_many(self.QUERIES, settings=keyed)

        assert len(results) == 6
        assert counts(scan) == (6, 5, 1, 0)
        # The five that were read may have been billed; the one that could not be read is an error.
        assert scan.log_fields()["serpapi_billed_or_unknown"] == 5


# ---------------------------------------------------------------------------
# the detection
# ---------------------------------------------------------------------------


def the_model_names_zendesk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model's side of a detection, faked: it names one brand the searches may also find."""

    async def run_seed_prompts(prompts: list[str], **kwargs: Any) -> list[CoCitationResult]:
        hit = CoCitationHit(name="Zendesk", domain="zendesk.com", position=1, prompt=prompts[0])
        return [CoCitationResult(prompt=prompts[0], hits=[hit])]

    monkeypatch.setattr(cocitation_service, "run_seed_prompts", run_seed_prompts)


async def detect(
    monkeypatch: pytest.MonkeyPatch, keyed: Settings, answer_for: Callable[[str], httpx.Response]
) -> detection.DetectionOutcome:
    """A detection for a client with a brand and an industry, which is six searches, each answered
    by `answer_for(query)`, through the real `search_many`, with the model's side faked."""
    install(monkeypatch, lambda request: answer_for(request.url.params["q"]))
    # conftest replaces `serp.search_many` for every test; the real one is bound at import above.
    monkeypatch.setattr(serp, "search_many", search_many)
    the_model_names_zendesk(monkeypatch)
    return await detection.detect_from_facts(
        brand_name="Acme", domain="acme.com", industry="project software", settings=keyed
    )


class TestADetectionSurvivesAnUnreadableAnswer:
    """Before, one answer of the wrong shape raised out of `search_many` and took the detection
    with it. And `serp_queries_run` is what `decide_detection` reads as "the SERP signal ran", so a
    page that could not be read must not be counted in it."""

    async def test_one_unreadable_answer_among_six_costs_that_search_and_not_the_detection(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[str] = []

        def answer_for(query: str) -> httpx.Response:
            asked.append(query)
            return body(["a", "list"]) if query == "Acme vs" else page("zendesk.com", "front.com")

        outcome = await detect(monkeypatch, keyed, answer_for)

        assert len(asked) == 6 and "Acme vs" in asked
        # Five searches ran. The sixth could not be read, and is not counted as one that did.
        assert outcome.serp_queries_run == 5
        assert {c.domain for c in outcome.candidates} == {"zendesk.com", "front.com"}

    async def test_six_unreadable_pages_are_a_signal_that_did_not_run_and_six_empty_ones_are_not(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unreadable = await detect(
            monkeypatch, keyed, lambda query: body({"organic_results": ["a", "b"]})
        )
        empty = await detect(monkeypatch, keyed, lambda query: body({"organic_results": []}))

        # Unreadable: the searches did not run as far as the detection can tell, so the model's
        # rival has nothing to be corroborated by or to disagree with. The confidence is
        # unmeasurable, which is None and not 0.
        assert (unreadable.serp_queries_run, unreadable.detection_confidence) == (0, None)
        # Empty: the six searches ran and found no rival the model had named. That is a
        # measurement, and a confidence of its own.
        assert empty.serp_queries_run == 6
        assert empty.detection_confidence is not None
        assert [c.domain for c in unreadable.candidates] == ["zendesk.com"]
        assert [c.domain for c in empty.candidates] == ["zendesk.com"]

    async def test_a_body_nested_past_the_parser_costs_that_search_and_not_the_detection(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def answer_for(query: str) -> httpx.Response:
            if query == "Acme vs":
                return nested("arrays")
            return page("zendesk.com", "front.com")

        outcome = await detect(monkeypatch, keyed, answer_for)

        assert outcome.serp_queries_run == 5
        assert {c.domain for c in outcome.candidates} == {"zendesk.com", "front.com"}


def summary(outcome: detection.DetectionOutcome) -> tuple[Any, ...]:
    """What a detection concluded, scores and all, in comparable terms."""
    return (
        outcome.status, outcome.detection_confidence, outcome.serp_queries_run,
        outcome.co_citation_prompts_run, outcome.candidates_considered,
        [(c.domain, c.score) for c in outcome.candidates],
    )


class TestADetectionScoresAPageWhoseItemsAreOdd:
    """Links that are not strings and positions that are not whole numbers raised, in `search` and
    in scoring, and took the detection with them after its searches were paid for. Now a page
    with such items scores as the same page would with nothing odd in it: an unreadable link is
    no link, and a position that is not a rank is the item's place on the page."""

    @pytest.mark.parametrize(
        ("odd_links", "odd_positions"),
        [(True, False), (False, True), (True, True)],
        ids=["odd-links", "odd-positions", "both"],
    )
    async def test_it_scores_as_the_same_page_with_nothing_odd_in_it(
        self, odd_links: bool, odd_positions: bool, keyed: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sent = [
            {
                "link": link if odd_links else kept_link,
                "position": position if odd_positions else kept_position,
            }
            for link, position, kept_link, kept_position in ODD_PAGE
        ]
        clean = [
            {"link": kept_link, "position": kept_position}
            for _, _, kept_link, kept_position in ODD_PAGE
        ]

        odd = await detect(monkeypatch, keyed, lambda query: body({"organic_results": sent}))
        twin = await detect(monkeypatch, keyed, lambda query: body({"organic_results": clean}))

        # The six searches ran, the eight results that have a link became candidates (the two that
        # have none are not), and the odd page scored exactly as the clean one did.
        assert odd.serp_queries_run == twin.serp_queries_run == 6
        assert odd.candidates_considered == twin.candidates_considered == 8
        assert summary(odd) == summary(twin)


# ---------------------------------------------------------------------------
# U1: what the scan's total says about it
# ---------------------------------------------------------------------------


class TestTheScansTotalCountsItAsAnAnsweredError:
    """As the body that was never JSON always was (`test_serp_usage.py`): one request, answered
    with an error, so not billed, and nothing about it unknown."""

    @pytest.mark.parametrize("value", BAD_BODIES)
    async def test_one_request_answered_with_an_error_and_nothing_unknown(
        self, value: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(body(value)).install(monkeypatch)

        with usage.scan_scope("scan_1") as scan:
            result = await search("q", settings=keyed)

        assert result.error_code == BAD
        assert len(script.requests) == 1
        assert counts(scan) == (1, 0, 1, 0)
        assert "serpapi_billed_or_unknown" not in scan.log_fields()

    async def test_the_same_as_the_body_that_was_never_json(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(
            httpx.Response(200, content=b"<html>not json</html>"), body([1, 2, 3])
        ).install(monkeypatch)

        with usage.scan_scope("never_json") as never_json:
            old = await search("q", settings=keyed)
        with usage.scan_scope("a_list") as a_list:
            new = await search("q", settings=keyed)

        assert old.error_code == new.error_code == BAD
        assert len(script.requests) == 2
        assert counts(never_json) == counts(a_list) == (1, 0, 1, 0)

    async def test_a_timeout_then_an_unreadable_answer_is_two_requests_one_each(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(httpx.ReadTimeout("no answer"), body("a string")).install(monkeypatch)

        with (
            structlog.testing.capture_logs() as events,
            usage.scan_scope("scan_1") as scan,
        ):
            result = await search("q", settings=keyed)

        assert result.error_code == BAD
        assert len(script.requests) == 2
        # The retry is logged, and then the one line for the answer, with both attempts in it.
        assert serp_events(events) == [RETRIED, COMPLETED]
        (line,) = completed(events)
        assert (line["attempts"], line["hits"], line["status"], line["error_code"]) == (
            2, 0, 200, BAD,
        )
        # The error answer is not billed; the request that got no answer may have been.
        assert counts(scan) == (2, 0, 1, 1)
        assert scan.log_fields()["serpapi_billed_or_unknown"] == 1


# ---------------------------------------------------------------------------
# no key, no URL
# ---------------------------------------------------------------------------


def assert_no_key_or_url_in(events: list[dict[str, Any]]) -> None:
    """Nothing the run logged, as the console prints it or as JSON, holds the key or the URL."""
    render = structlog.dev.ConsoleRenderer(colors=False)
    printed = "\n".join(
        render(None, "info", {**e, "level": e.get("log_level", "info")}) for e in events)
    shown = printed + json.dumps(events, default=str)
    for forbidden in (KEY, URL_WITH_KEY, serp.SERPAPI_URL, "serpapi.com/search.json", "api_key"):
        assert forbidden not in shown, f"{forbidden!r} reached the log: {shown[:300]}"


class TestNoKeyOrUrlReachesAnyLine:
    """SerpApi echoes the request URL on some answers, and the URL carries the key. A body that
    cannot be read is one the code never looks inside, so nothing of it may be logged either."""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(URL_WITH_KEY, id="string"),
            pytest.param([URL_WITH_KEY], id="list"),
            pytest.param({"organic_results": URL_WITH_KEY}, id="results-string"),
            pytest.param({"organic_results": {"echo": URL_WITH_KEY}}, id="results-object"),
            pytest.param({"organic_results": [URL_WITH_KEY, URL_WITH_KEY]}, id="results-strings"),
            pytest.param({"organic_results": [URL_WITH_KEY, GOOD]}, id="results-kept-in-part"),
        ],
    )
    async def test_whatever_the_body_echoes_no_line_holds_the_key_or_the_url(
        self, value: Any, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = Script(body(value)).install(monkeypatch)

        with (
            structlog.testing.capture_logs() as events,
            usage.scan_scope("scan_1"),
        ):
            await search("q", settings=keyed)

        # The key really was in play, in the request's URL ...
        assert KEY in str(script.requests[0].url)
        # ... the run logged the search's line and the scan's total ...
        assert len(completed(events)) == 1
        assert [e["event"] for e in events if e["event"] == "scan.usage"] == ["scan.usage"]
        # ... and not one line has the key or the URL, nor an exception.
        assert_no_key_or_url_in(events)
        assert_no_exception_in(events)

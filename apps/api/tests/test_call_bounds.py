"""Every paid Anthropic call outside the engine adapters has a ceiling, and the
ceiling bounds the whole call.

`test_engine_timeout.py` proved this for the engine adapters after Epic 9.1
measured a call that burned 271.6s and returned nothing: the SDK's `timeout`
bounds one attempt, its retry policy retries timeouts, and the pinned default
of two retries on a 600s read timeout is a thirty-minute call with three
billed generations. Five other call sites made the same paid call and
inherited exactly that (API key discipline audit, 2026-09-07). These tests
hold each of them to the same STRONG property — a call whose every attempt
hangs fails within a bounded total time — and not the weak one, "it
eventually returns", which the 271.6s call also satisfied.

The Anthropic client under test is the real one. Only its HTTP transport is
faked, so the timeout and retry budget exercised are the ones each site
genuinely passes rather than values re-declared by the test. What differs per
site is the failure SHAPE, and each site's own is asserted: `(None, None)`
from sentiment, a fallback prompt set, an outcome carrying `TIMEOUT`. What
does not differ is the bound.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType

import anthropic
import httpx
import pytest

from avp_api.config import Settings
from avp_api.models.engine_result import Engine
from avp_api.services import call_bounds, classify, cocitation, engines, extraction
from avp_api.services.call_bounds import CallBound
from avp_api.services.crawl import CrawlResult, CrawlSignals
from avp_api.services.engines import EngineAnswer

# Epic 9.1's outlier, in seconds — the number every ceiling here must beat.
MEASURED_OUTLIER_SECONDS = 271.6
# What every site inherited before it declared a bound: the pinned SDK's 600s
# read timeout across DEFAULT_MAX_RETRIES + 1 attempts, before backoff.
INHERITED_CEILING_SECONDS = 600.0 * (anthropic._constants.DEFAULT_MAX_RETRIES + 1)


@pytest.fixture
def bound_settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        anthropic_api_key="test-key-never-sent-anywhere",
    )


# --- the sites ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Site:
    """One bounded call site: how to call it, and what its timeout looks like."""

    name: str
    module: ModuleType
    bound_attr: str
    # The production ceiling, pinned. Changing a bound means changing this on
    # purpose, and the build log records why each number is what it is.
    ceiling: float
    call: Callable[[Settings], Awaitable[object]]
    assert_timed_out: Callable[[object], None]

    @property
    def bound(self) -> CallBound:
        return getattr(self.module, self.bound_attr)


def _mentioning_answer() -> EngineAnswer:
    return EngineAnswer(
        engine=Engine.CLAUDE, engine_version="test", prompt_text="q",
        text="Help Scout is a strong option for a shared inbox.",
    )


def _co_citation_timed_out(result: object) -> None:
    # "Never raises": a not-ok result with a code, and no hits invented.
    assert isinstance(result, cocitation.CoCitationResult)
    assert result.ok is False
    assert result.error_code == "TIMEOUT"
    assert result.hits == []


def _sentiment_timed_out(result: object) -> None:
    # "Returns (None, None) on failure" — the docstring's contract, and the
    # one `run_scan` relies on to leave sentiment unset rather than crash.
    assert result == (None, None)


def _classifiable_crawl() -> CrawlResult:
    text = "word " * 200
    signals = CrawlSignals(
        final_url="https://northaven-dental.example",
        registrable_domain="northaven-dental.example",
        word_count=len(text.split()),
    )
    return CrawlResult(signals=signals, text_extract=text, ok=True)


def _classification_timed_out(result: object) -> None:
    # An unclassifiable outcome with a reason a reader can act on. TIMEOUT,
    # not PROVIDER_UNREACHABLE: the ladder checks APITimeoutError before the
    # APIConnectionError it subclasses.
    assert isinstance(result, classify.ClassificationOutcome)
    assert result.status == "unclassifiable"
    assert result.reason_code == "TIMEOUT"
    assert result.industry is None


SITES = [
    Site(
        name="classify",
        module=classify,
        bound_attr="CLASSIFIER_BOUND",
        ceiling=26.0,
        call=lambda s: classify.classify(_classifiable_crawl(), settings=s),
        assert_timed_out=_classification_timed_out,
    ),
    Site(
        name="classify_sentiment",
        module=extraction,
        bound_attr="SENTIMENT_BOUND",
        ceiling=62.0,
        call=lambda s: extraction.classify_sentiment(
            _mentioning_answer(), subject_name="Help Scout", settings=s
        ),
        assert_timed_out=_sentiment_timed_out,
    ),
    Site(
        name="run_seed_prompt",
        module=cocitation,
        bound_attr="CO_CITATION_BOUND",
        ceiling=62.0,
        call=lambda s: cocitation.run_seed_prompt(
            "What are the best alternatives to Help Scout?",
            subject_brand="Help Scout",
            settings=s,
        ),
        assert_timed_out=_co_citation_timed_out,
    ),
]


@pytest.fixture(params=SITES, ids=lambda s: s.name)
def site(request: pytest.FixtureRequest) -> Site:
    return request.param


# --- the fakes, as in test_engine_timeout.py ---------------------------------


class _TimingOutTransport(httpx.AsyncBaseTransport):
    """Every attempt times out, instantly.

    Raising `ReadTimeout` rather than sleeping keeps the test fast while
    exercising the real SDK code path: the SDK converts this to
    `APITimeoutError` and applies its retry policy to it.
    """

    def __init__(self) -> None:
        self.attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        raise httpx.ReadTimeout("simulated per-attempt timeout", request=request)


class _HangingTransport(httpx.AsyncBaseTransport):
    """Never answers, and ignores the per-attempt timeout entirely.

    httpx enforces timeouts inside the transport, so a transport that declines
    to honour them cannot be interrupted by the SDK. That is precisely the
    pathological case the outer deadline exists for.
    """

    def __init__(self) -> None:
        self.attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


def _install(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> dict:
    """Point every `CallBound.client()` at `transport`, capturing the client kwargs."""
    captured: dict = {}
    real_cls = anthropic.AsyncAnthropic

    def factory(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return real_cls(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(call_bounds.anthropic, "AsyncAnthropic", factory)
    return captured


# --- the properties ----------------------------------------------------------


class TestTheBoundIsExplicit:
    def test_the_ceiling_is_the_arithmetic_and_far_below_what_was_inherited(
        self, site: Site
    ) -> None:
        bound = site.bound
        assert bound.ceiling == pytest.approx(site.ceiling)
        # The arithmetic must stay the arithmetic: attempts x per-attempt bound.
        assert bound.ceiling == bound.timeout * (bound.max_retries + 1) + bound.backoff_allowance
        assert bound.ceiling < MEASURED_OUTLIER_SECONDS < INHERITED_CEILING_SECONDS

    def test_the_backoff_allowance_covers_the_sdk_schedule(self, site: Site) -> None:
        """Read from the pinned SDK, not recalled: 0.5s doubling toward 8.0s, jitter <= 1.0."""
        c = anthropic._constants
        worst_case = sum(
            min(c.INITIAL_RETRY_DELAY * 2**n, c.MAX_RETRY_DELAY)
            for n in range(site.bound.max_retries)
        )
        assert worst_case <= site.bound.backoff_allowance

    def test_the_budget_is_passed_not_inherited(
        self, monkeypatch: pytest.MonkeyPatch, site: Site, bound_settings: Settings
    ) -> None:
        """A dropped kwarg silently restores the SDK default. Guard the kwarg itself."""
        captured = _install(monkeypatch, _TimingOutTransport())

        asyncio.run(site.call(bound_settings))

        assert captured["timeout"] == site.bound.timeout
        assert captured["max_retries"] == site.bound.max_retries
        assert site.bound.max_retries < anthropic._constants.DEFAULT_MAX_RETRIES

    def test_the_engine_adapters_and_this_module_do_the_same_arithmetic(self) -> None:
        """`engines.py` keeps its own constants on purpose; the two must not drift."""
        as_bound = CallBound(
            timeout=engines.DEFAULT_TIMEOUT,
            max_retries=engines.MAX_RETRIES,
            backoff_allowance=engines.RETRY_BACKOFF_ALLOWANCE,
        )
        assert as_bound.ceiling == engines.ENGINE_CALL_CEILING


class TestAllAttemptsTimeOut:
    def test_it_stops_after_the_retry_budget_and_reports_a_timeout(
        self, monkeypatch: pytest.MonkeyPatch, site: Site, bound_settings: Settings
    ) -> None:
        transport = _TimingOutTransport()
        _install(monkeypatch, transport)

        result = asyncio.run(site.call(bound_settings))

        assert transport.attempts == site.bound.max_retries + 1
        # The defect in one line: the SDK default would have made this 3.
        assert transport.attempts != anthropic._constants.DEFAULT_MAX_RETRIES + 1
        site.assert_timed_out(result)

    def test_the_whole_call_is_bounded_even_when_every_attempt_hangs(
        self, monkeypatch: pytest.MonkeyPatch, site: Site, bound_settings: Settings
    ) -> None:
        """The strong property: bounded total time, not "it eventually returns".

        The bound is swapped for a scaled-down one so the test is fast; the
        mechanism under test is the same outer deadline that enforces the
        production ceiling, and the tests above pin that ceiling.
        """
        scaled = CallBound(timeout=0.2, max_retries=0, backoff_allowance=0.3)
        assert scaled.ceiling == pytest.approx(0.5)
        monkeypatch.setattr(site.module, site.bound_attr, scaled)
        transport = _HangingTransport()
        _install(monkeypatch, transport)

        started = time.perf_counter()
        result = asyncio.run(site.call(bound_settings))
        elapsed = time.perf_counter() - started

        # Bounded — and bounded by OUR deadline, not by the transport giving up.
        assert elapsed < scaled.ceiling + 0.5, (
            f"{site.name} ran {elapsed:.2f}s against a {scaled.ceiling}s ceiling"
        )
        assert transport.attempts == 1
        site.assert_timed_out(result)


class TestEachSiteKeepsItsOwnConstraint:
    """A bound is not one number for everyone. Two sites carry a constraint
    of their own, and each is pinned here so a retune cannot quietly break it."""

    def test_classification_keeps_its_thirty_second_promise(self) -> None:
        """`classify.py` promises a result inside thirty seconds, crawl included.

        What has to fit is the CEILING — every attempt plus backoff — and not
        the per-attempt bound, which is the mistake the engine adapters made.
        """
        assert classify.CLASSIFIER_BOUND.ceiling <= 30.0

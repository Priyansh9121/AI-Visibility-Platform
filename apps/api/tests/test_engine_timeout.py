"""The engine call has a ceiling, and the ceiling bounds the whole call.

Epic 9.1 measured a single `claude_search` call that burned **271.6s and
returned nothing** — 16.5% of all engine time in that run. `DEFAULT_TIMEOUT`
read like a 90s guarantee, but it bounds one HTTP *attempt*; the Anthropic SDK
retries timeouts (`APITimeoutError` subclasses `APIConnectionError`, which its
retry policy always retries), so the real ceiling was 3 x 90s plus backoff.

These tests prove the **strong** property — a call whose every attempt times out
fails *within a bounded total time* — and not the weak one, "it eventually
returns", which the 271.6s call also satisfied.

The Anthropic client under test is the real one. Only its HTTP transport is
faked, so the timeout and retry budget exercised here are the ones `engines.py`
genuinely passes rather than values re-declared by the test.
"""

from __future__ import annotations

import asyncio
import time

import anthropic
import httpx
import pytest

from avp_api.config import Settings
from avp_api.models.engine_result import EngineResultStatus
from avp_api.services import engines
from avp_api.services.engines import ClaudeParametricAdapter

# Epic 9.1's outlier, in seconds. The number this fix exists to make impossible.
MEASURED_OUTLIER_SECONDS = 271.6


@pytest.fixture
def engine_settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        anthropic_api_key="test-key-never-sent-anywhere",
    )


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
    pathological case the outer deadline exists for — a hung connection no
    per-attempt setting can bound.
    """

    def __init__(self) -> None:
        self.attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


def _install(monkeypatch, transport: httpx.AsyncBaseTransport) -> dict:
    """Point the adapter's real Anthropic client at `transport`, capturing kwargs."""
    captured: dict = {}
    real_cls = anthropic.AsyncAnthropic

    def factory(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return real_cls(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(engines.anthropic, "AsyncAnthropic", factory)
    return captured


class TestRetryBudgetIsExplicit:
    def test_the_stated_ceiling_is_bounded_and_far_below_the_measured_outlier(self) -> None:
        assert pytest.approx(122.0) == engines.ENGINE_CALL_CEILING
        assert engines.ENGINE_CALL_CEILING < MEASURED_OUTLIER_SECONDS
        # The arithmetic must stay the arithmetic: attempts x per-attempt bound.
        assert engines.ENGINE_CALL_CEILING == (
            engines.DEFAULT_TIMEOUT * (engines.MAX_RETRIES + 1)
            + engines.RETRY_BACKOFF_ALLOWANCE
        )

    def test_the_retry_budget_is_passed_not_inherited(
        self, monkeypatch, engine_settings: Settings
    ) -> None:
        """A dropped kwarg silently restores the SDK default. Guard the kwarg itself."""
        transport = _TimingOutTransport()
        captured = _install(monkeypatch, transport)

        asyncio.run(ClaudeParametricAdapter().ask("q", settings=engine_settings))

        assert captured["max_retries"] == engines.MAX_RETRIES
        assert captured["timeout"] == engines.DEFAULT_TIMEOUT
        # Explicitly below the pinned SDK's own default, which is what produced
        # the three-attempt 271.6s call.
        assert engines.MAX_RETRIES < anthropic._constants.DEFAULT_MAX_RETRIES  # noqa: SLF001


class TestAllAttemptsTimeOut:
    def test_it_stops_after_the_retry_budget_rather_than_the_sdk_default(
        self, monkeypatch, engine_settings: Settings
    ) -> None:
        transport = _TimingOutTransport()
        _install(monkeypatch, transport)

        answer = asyncio.run(ClaudeParametricAdapter().ask("q", settings=engine_settings))

        assert transport.attempts == engines.MAX_RETRIES + 1 == 2
        # The defect in one line: the SDK default would have made this 3.
        assert transport.attempts != anthropic._constants.DEFAULT_MAX_RETRIES + 1  # noqa: SLF001
        assert answer.status is EngineResultStatus.TIMEOUT
        assert answer.error_code == "TIMEOUT"
        assert not answer.ok
        assert answer.text == ""

    def test_the_whole_call_is_bounded_even_when_every_attempt_hangs(
        self, monkeypatch, engine_settings: Settings
    ) -> None:
        """The strong property: bounded total time, not "it eventually returns".

        The ceiling is scaled down so the test is fast; the mechanism under test
        is the same outer deadline that bounds the production 122.0s value, and
        the test above pins that value.
        """
        ceiling = 0.5
        monkeypatch.setattr(engines, "ENGINE_CALL_CEILING", ceiling)
        transport = _HangingTransport()
        _install(monkeypatch, transport)

        started = time.perf_counter()
        answer = asyncio.run(ClaudeParametricAdapter().ask("q", settings=engine_settings))
        elapsed = time.perf_counter() - started

        # Bounded — and bounded by *our* deadline, not by the transport giving up.
        assert elapsed < ceiling + 0.5, f"call ran {elapsed:.2f}s against a {ceiling}s ceiling"
        assert answer.status is EngineResultStatus.TIMEOUT
        assert answer.error_code == "TIMEOUT"
        assert answer.latency_ms <= int((ceiling + 0.5) * 1000)


class TestTimeoutsAreClassifiedAsTimeouts:
    def test_a_timeout_is_a_timeout_not_an_unreachable_provider(self) -> None:
        """`APITimeoutError` subclasses `APIConnectionError`.

        Checked in the wrong order, the connection branch swallows every timeout
        and reports PROVIDER_UNREACHABLE — which is exactly what Epic 9.1 saw on
        the 271.6s call, and why `EngineResultStatus.TIMEOUT` was unreachable.
        """
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        status, code = engines._map_error(anthropic.APITimeoutError(request=request))  # noqa: SLF001
        assert status is EngineResultStatus.TIMEOUT
        assert code == "TIMEOUT"

    def test_a_genuine_connection_failure_is_still_unreachable(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        status, code = engines._map_error(  # noqa: SLF001
            anthropic.APIConnectionError(request=request)
        )
        assert status is EngineResultStatus.ERROR
        assert code == "PROVIDER_UNREACHABLE"

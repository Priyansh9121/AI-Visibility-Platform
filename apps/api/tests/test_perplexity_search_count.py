"""Perplexity's search fee is counted — U4, 2026-10-05.

Production `llm.call` lines showed `search_invocations=0` for every Perplexity call although
every answer is grounded. The Agent API reports the count under the tool's name as it ran,
`usage.tool_calls_details.search_web.invocation`, not `web_search`. The body below is the
`usage` of one real call made on 2026-10-05 with the adapter's own request (counts and the
vendor's own dollar figures only). `web_search` is kept as a fallback, never added to it.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import structlog

from avp_api.config import Settings
from avp_api.models.engine_result import EngineResultStatus
from avp_api.services import engines, usage
from avp_api.services.engines import PerplexityAdapter
from avp_api.services.usage import CallUsage, Purpose, ScanUsage

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_e2e.py"

OBSERVED_USAGE: dict[str, Any] = {
    "cost": {"cache_creation_cost": 0.00072, "currency": "USD", "input_cost": 0.00014,
             "output_cost": 0.00081, "tool_calls_cost": 0.0025,
             "tool_calls_cost_details": {"search_web": 0.0025}, "total_cost": 0.00417},
    "input_tokens": 3424,
    "input_tokens_details": {"cache_creation_input_tokens": 2880, "cache_read_input_tokens": 0,
                             "cached_tokens": 0},
    "output_tokens": 323, "output_tokens_details": {"reasoning_tokens": 0},
    "tool_calls_details": {"search_web": {"cost_usd": 0.0025, "invocation": 1}},
    "total_tokens": 3747,
}


def body(usage_block: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "r", "status": "completed", "model": "perplexity/sonar",
        "output": [{"type": "search_results", "results": []},
                   {"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "an answer",
                                 "annotations": []}]}],
        "usage": usage_block,
    }


def with_tools(tools: object) -> dict[str, Any]:
    return body({"input_tokens": 10, "output_tokens": 5, "tool_calls_details": tools})


class TestReadingTheCount:
    def test_the_observed_body_is_one_search_and_its_tokens_are_read_as_before(self) -> None:
        counts = usage.counts_from_perplexity(body(OBSERVED_USAGE))
        assert (counts.search_invocations, counts.input_tokens, counts.output_tokens,
                counts.cache_read_tokens) == (1, 3424, 323, 0)

    def test_web_search_alone_is_the_fallback(self) -> None:
        got = usage.counts_from_perplexity(with_tools({"web_search": {"invocation": 3}}))
        assert got.search_invocations == 3

    def test_both_keys_the_observed_one_wins_and_they_are_not_added(self) -> None:
        got = usage.counts_from_perplexity(with_tools(
            {"search_web": {"invocation": 2}, "web_search": {"invocation": 5}}))
        assert got.search_invocations == 2

    def test_neither_key_is_zero(self) -> None:
        assert usage.counts_from_perplexity(with_tools({})).search_invocations == 0
        assert usage.counts_from_perplexity(body({})).search_invocations == 0

    @pytest.mark.parametrize("junk", ["2", -1, True, None, [1], {"n": 1}, float("nan")])
    def test_a_key_that_is_present_with_no_number_is_zero_and_does_not_fall_back(
        self, junk: object
    ) -> None:
        got = usage.counts_from_perplexity(with_tools(
            {"search_web": {"invocation": junk}, "web_search": {"invocation": 4}}))
        assert got.search_invocations == 0

    def test_other_tools_are_not_web_searches(self) -> None:
        got = usage.counts_from_perplexity(with_tools(
            {"fetch_url": {"invocation": 2}, "other": {"invocation": 7}}))
        assert got.search_invocations == 0
        got = usage.counts_from_perplexity(with_tools(
            {"search_web": {"invocation": 1}, "fetch_url": {"invocation": 2}}))
        assert got.search_invocations == 1

    def test_the_vendors_dollar_figure_is_never_read(self) -> None:
        counts = usage.counts_from_perplexity(body(OBSERVED_USAGE))
        assert "0.0025" not in repr(counts) and "0.00417" not in repr(counts)


@pytest.fixture
def keyed() -> Settings:
    return Settings(
        _env_file=None, environment="test",
        database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        perplexity_api_key="test-perplexity-key",
    )


@pytest.fixture(autouse=True)
def _fresh_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engines, "_GATES", {})
    monkeypatch.setattr(engines, "_PACERS", {})
    monkeypatch.setattr(engines.PerplexityAdapter, "min_start_interval", None)


class TestThroughTheAdapter:
    async def test_the_llm_call_line_carries_the_search(
        self, keyed: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = httpx.AsyncClient

        class Routed(real):  # type: ignore[valid-type, misc]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("transport", httpx.MockTransport(
                    lambda request: httpx.Response(200, json=body(OBSERVED_USAGE))))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Routed)

        with structlog.testing.capture_logs() as events:
            answer = await PerplexityAdapter().ask("a prompt", settings=keyed)

        assert answer.status is EngineResultStatus.OK
        (line,) = [e for e in events if e["event"] == "llm.call"]
        assert (line["provider"], line["search_invocations"], line["input_tokens"]) == (
            "perplexity", 1, 3424)
        assert "0.0025" not in repr(line) and "0.00417" not in repr(line)


def perplexity_call(searches: int) -> CallUsage:
    return CallUsage(provider="perplexity", model="perplexity/sonar", purpose=Purpose.ENGINE,
                     engine="perplexity", input_tokens=3424, output_tokens=323,
                     search_invocations=searches)


@pytest.fixture(scope="module")
def e2e() -> Iterator[ModuleType]:
    name = "verify_e2e_u4"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


class TestScanUsageAndPricing:
    def test_scan_usage_sums_searches_per_engine_and_in_total(self) -> None:
        scan = ScanUsage("scan_01U4")
        for _ in range(3):
            scan.add(perplexity_call(1))
        fields = scan.log_fields()
        assert fields["engine_perplexity_search_invocations"] == 3
        assert fields["engines_search_invocations"] == 3

    def test_price_usage_prices_them_at_the_perplexity_rate(self, e2e: ModuleType) -> None:
        scan = ScanUsage("scan_01U4")
        for _ in range(4):
            scan.add(perplexity_call(1))
        line = "scan.usage " + " ".join(f"{k}={v}" for k, v in scan.log_fields().items())
        pricing = e2e.price_usage(line)
        row = next(r for r in pricing.rows if r.name == "engine: perplexity")
        assert row.search_invocations == 4
        assert row.search_cost == Decimal(4) * Decimal("0.0025")
        tokens = (Decimal(4 * 3424) * Decimal("0.25") + Decimal(4 * 323) * Decimal("2.5")
                  ) / Decimal(1_000_000)
        assert row.cost == tokens + Decimal("0.01")

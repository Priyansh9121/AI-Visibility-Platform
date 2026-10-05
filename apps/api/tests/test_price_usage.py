"""`scripts/verify_e2e.py` prices a pasted `scan.usage` line — U1, 2026-10-04.

Production logs counts and never a dollar figure. The rates live in the one place that has
always held them, the script's `RATES` table, and each now carries the date it was read and
the vendor page it was read from, because a rate is perishable and a figure with no date is
a guess. `--price-usage` takes the line as Render shows it and prints, per line and in total,
what those counts cost at those rates, with the dates, and says which rates it does not have
rather than pricing the line at nothing.

The line under test is built here from the real accumulator and rendered by structlog's real
console renderer, the way production writes it, so the producer and this reader cannot drift
apart without a test failing.

SerpApi is priced by the plan, which this repo does not hold, so it is counted and listed as a
missing rate unless `--serp-per-search` names the price of one search. Given one, the script
prices `serpapi_billed_or_unknown`, the answered searches and the unanswered ones together: an
upper bound on what SerpApi billed, since an answered search is billed unless SerpApi served it
from its cache, an error answer is not billed, and a search nobody answered may have been. A line
written before that field existed has it worked out from the two it is the sum of.

The price is an input like any other and is refused unless it is one: a finite number of at least
zero. NaN, sNaN, an infinity or a negative value is a message and exit code 2, never a traceback
and never a figure.

Every expected figure below is written out as arithmetic on literals (tokens times a rate per
million), NOT read back from the script's own table, so a rate edited in the table fails here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import re
import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import structlog

from avp_api.services.usage import CallUsage, Purpose, ScanUsage

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_e2e.py"
MILLION = Decimal(1_000_000)


@pytest.fixture(scope="module")
def e2e() -> Iterator[ModuleType]:
    name = "verify_e2e_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: a dataclass looks its own module up in `sys.modules`, and the
    # script defines several.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


def call(**overrides: Any) -> CallUsage:
    base: dict[str, Any] = {
        "provider": "anthropic", "model": "claude-opus-5", "purpose": Purpose.ENGINE,
        "engine": "claude",
    }
    return CallUsage(**{**base, **overrides})


def a_scan(*, extra: list[CallUsage] | None = None, reasoning: bool = True) -> ScanUsage:
    """A scan's accumulator with every line used, at numbers that are easy to price by hand.

    `reasoning=False` is the same scan with no reasoning counts reported at all: the output
    counts are unchanged, because reasoning is already inside them.
    """
    r = (lambda n: n) if reasoning else (lambda n: 0)  # noqa: E731
    scan = ScanUsage("scan_01PRICED")
    for _ in range(3):
        scan.add(call(engine="claude", input_tokens=80, output_tokens=160,
                      reasoning_tokens=r(100)))
        scan.add(call(engine="chatgpt", provider="openai", model="gpt-5.5-2026-04-23",
                      input_tokens=111, output_tokens=222, reasoning_tokens=r(77)))
        scan.add(call(engine="perplexity", provider="perplexity", model="perplexity/sonar",
                      input_tokens=1758, output_tokens=432, search_invocations=2))
        scan.add(call(engine="gemini", provider="google", model="gemini-3.8-flash",
                      input_tokens=20, output_tokens=35, reasoning_tokens=r(5)))
    for _ in range(12):
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-haiku-4-5-20251001",
                      input_tokens=410, output_tokens=33))
    scan.add(call(purpose=Purpose.PROMPT_GENERATION, engine=None, input_tokens=600,
                  output_tokens=2500, reasoning_tokens=r(1800)))
    scan.add(call(purpose=Purpose.FIXES, engine=None, input_tokens=2200, output_tokens=5100,
                  reasoning_tokens=r(3000)))
    for _ in range(2):
        scan.add(call(purpose=Purpose.DETECTION, engine=None, input_tokens=300, output_tokens=140))
    for c in extra or []:
        scan.add(c)
    # Three queries: one asked again and answered (2 requests, 1 unanswered, 1 ok), one that
    # was never answered (2 requests) and one answered with an error (1): 5, 1 ok, 1 error, 3.
    scan.add_serp(attempts=2, no_answer=1, answer="ok")
    scan.add_serp(attempts=2, no_answer=2, answer=None)
    scan.add_serp(attempts=1, no_answer=0, answer="error")
    return scan


def rendered(scan: ScanUsage, *, prefix: bool = True) -> str:
    """The line as Render shows it: structlog's console renderer, with its timestamp and level."""
    event = {"event": "scan.usage", **scan.log_fields()}
    if prefix:
        event = {"timestamp": "2026-10-04 03:49:18", "level": "info", **event}
    return structlog.dev.ConsoleRenderer(colors=False)(None, "info", event)


def per_million(tokens: int, rate: str) -> Decimal:
    return Decimal(tokens) * Decimal(rate) / MILLION


# What the line above costs, line by line, at the rates in the script's table on 2026-10-03.
CLAUDE = per_million(240, "5") + per_million(480, "25")
CHATGPT = per_million(333, "5") + per_million(666, "30")
PERPLEXITY = (
    per_million(5274, "0.25") + per_million(1296, "2.5")
    # six invocations at a quarter of a cent
    + Decimal(6) * Decimal("0.0025")
)
GEMINI = per_million(60, "0.75") + per_million(105, "3.75")
SENTIMENT = per_million(4920, "1") + per_million(396, "5")
PROMPT_GENERATION = per_million(600, "5") + per_million(2500, "25")
FIXES = per_million(2200, "5") + per_million(5100, "25")
DETECTION = per_million(600, "5") + per_million(280, "25")
EVERYTHING = (
    CLAUDE + CHATGPT + PERPLEXITY + GEMINI + SENTIMENT + PROMPT_GENERATION + FIXES + DETECTION
)


class TestPricingALine:
    def test_each_line_is_priced_from_its_own_counts_and_the_model_that_served_it(
        self, e2e: ModuleType
    ) -> None:
        pricing = e2e.price_usage(rendered(a_scan()))

        by_name = {row.name: row for row in pricing.rows}
        assert pricing.scan_id == "scan_01PRICED"
        assert by_name["engine: claude"].cost == CLAUDE
        assert by_name["engine: chatgpt"].cost == CHATGPT
        assert by_name["engine: perplexity"].cost == PERPLEXITY
        assert by_name["engine: gemini"].cost == GEMINI
        assert by_name["sentiment"].cost == SENTIMENT
        assert by_name["prompt generation"].cost == PROMPT_GENERATION
        assert by_name["fixes"].cost == FIXES
        assert by_name["detection"].cost == DETECTION
        assert by_name["sentiment"].model == "claude-haiku-4-5-20251001"
        assert by_name["engine: chatgpt"].model == "gpt-5.5-2026-04-23"
        assert by_name["engine: claude"].calls == 3

    def test_the_total_is_those_lines_added_and_is_money_in_decimal_not_a_float(
        self, e2e: ModuleType
    ) -> None:
        pricing = e2e.price_usage(rendered(a_scan()))

        assert pricing.total == EVERYTHING
        assert isinstance(pricing.total, Decimal)
        assert all(isinstance(r.cost, Decimal) for r in pricing.rows)
        # Every line a model served has a rate; the only thing missing is SerpApi's plan price.
        assert pricing.missing == [
            "serpapi: no per-search rate recorded (the plan sets it; pass --serp-per-search)"]

    def test_the_engines_total_is_not_priced_again_on_top_of_its_engines(
        self, e2e: ModuleType
    ) -> None:
        pricing = e2e.price_usage(rendered(a_scan()))

        assert "engines" not in {row.name for row in pricing.rows}
        # The total of the engines would add the four engines' cost a second time.
        assert pricing.total < EVERYTHING + CLAUDE

    def test_reasoning_is_inside_the_output_count_and_is_not_charged_a_second_time(
        self, e2e: ModuleType
    ) -> None:
        reported = e2e.price_usage(rendered(a_scan(reasoning=True)))
        unreported = e2e.price_usage(rendered(a_scan(reasoning=False)))

        assert reported.total == unreported.total == EVERYTHING

    def test_the_grounded_claude_engine_pays_its_per_search_fee_on_top_of_its_tokens(
        self, e2e: ModuleType
    ) -> None:
        scan = a_scan(extra=[
            call(engine="claude_search", input_tokens=21_000, output_tokens=800,
                 search_invocations=3),
        ])

        pricing = e2e.price_usage(rendered(scan))

        row = {r.name: r for r in pricing.rows}["engine: claude_search"]
        tokens = per_million(21_000, "5") + per_million(800, "25")
        # Three searches at ten dollars a thousand.
        assert row.cost == tokens + Decimal(3) * Decimal("0.01")
        assert row.search_cost == Decimal("0.03")

    def test_cache_tokens_are_shown_and_the_line_says_they_were_not_discounted(
        self, e2e: ModuleType
    ) -> None:
        scan = a_scan(extra=[call(engine="claude", cache_read_tokens=1000)])

        pricing = e2e.price_usage(rendered(scan))

        assert any("cache" in note.lower() for note in pricing.notes)


class TestWhatIsMissing:
    def test_a_model_with_no_rate_is_named_and_left_out_of_the_total(
        self, e2e: ModuleType
    ) -> None:
        scan = a_scan(extra=[call(
            engine="gemini", provider="google", model="mystery-model-9",
            input_tokens=1000, output_tokens=1000)])

        pricing = e2e.price_usage(rendered(scan))

        # Gemini's line is served by a model with a rate and one without, so it is not priced:
        # the half that has a rate cannot be told from the half that does not.
        gemini = {r.name: r for r in pricing.rows}["engine: gemini"]
        assert gemini.cost is None
        assert "engine: gemini: no rate for mystery-model-9" in pricing.missing
        # ... and what is left in the total is exactly the other lines.
        assert pricing.total == EVERYTHING - GEMINI

    def test_two_snapshots_of_one_family_are_one_rate_and_are_priced_together(
        self, e2e: ModuleType
    ) -> None:
        scan = ScanUsage("scan_01X")
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-haiku-4-5-20251001",
                      input_tokens=1_000_000))
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-haiku-4-5-20261201",
                      input_tokens=1_000_000))

        pricing = e2e.price_usage(rendered(scan))

        (row,) = pricing.rows
        assert row.model == "claude-haiku-4-5-20251001+claude-haiku-4-5-20261201"
        assert row.cost == Decimal(2)  # two million input tokens at a dollar a million
        assert pricing.missing == []

    def test_two_models_with_different_rates_in_one_line_are_not_priced_as_one(
        self, e2e: ModuleType
    ) -> None:
        scan = ScanUsage("scan_01X")
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-haiku-4-5-20251001",
                      input_tokens=1000))
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-opus-5",
                      input_tokens=1000))

        pricing = e2e.price_usage(rendered(scan))

        (row,) = pricing.rows
        assert row.cost is None
        assert any("different rates" in m for m in pricing.missing)
        assert pricing.total == Decimal(0)

    def test_a_line_with_only_the_engines_total_prices_no_engine_and_says_so(
        self, e2e: ModuleType
    ) -> None:
        """`engines_*` is every engine together and is never priced: it would count each twice."""
        pricing = e2e.price_usage(
            "scan.usage scan_id=scan_01X engines_calls=12 engines_input_tokens=5000 "
            "engines_output_tokens=600 engines_model=claude-opus-5")

        assert pricing.rows == []
        assert pricing.total == Decimal(0)
        assert pricing.missing == [
            "engines: the line has no engine_<id>_* fields, so no engine is priced"]

    def test_a_single_unknown_model_is_a_missing_rate_and_never_a_free_line(
        self, e2e: ModuleType
    ) -> None:
        scan = ScanUsage("scan_01X")
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-haiku-9",
                      input_tokens=5000, output_tokens=500))

        pricing = e2e.price_usage(rendered(scan))

        (row,) = [r for r in pricing.rows if r.name == "sentiment"]
        assert row.cost is None and row.model == "claude-haiku-9"
        assert pricing.missing == ["sentiment: no rate for claude-haiku-9"]
        assert pricing.total == Decimal(0)
        assert "claude-haiku-9" in e2e.format_usage_pricing(pricing)

    def test_a_line_that_spent_nothing_needs_no_rate(self, e2e: ModuleType) -> None:
        scan = ScanUsage("scan_01X")
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-haiku-9", ok=False))

        pricing = e2e.price_usage(rendered(scan))

        assert pricing.missing == []

    def test_a_snapshot_name_finds_its_family_rate_by_prefix(self, e2e: ModuleType) -> None:
        assert e2e.rate_for("gpt-5.5-2026-04-23") is e2e.RATES["gpt-5.5"]
        assert e2e.rate_for("claude-haiku-4-5-20251001") is e2e.RATES["claude-haiku-4-5"]
        assert e2e.rate_for("mystery-model-9") is None

    def test_serpapi_is_counted_and_has_no_rate_unless_the_plans_is_given(
        self, e2e: ModuleType
    ) -> None:
        pricing = e2e.price_usage(rendered(a_scan()))

        assert (pricing.serp.attempted, pricing.serp.ok, pricing.serp.error,
                pricing.serp.no_answer) == (5, 1, 1, 3)
        assert pricing.serp.billed_or_unknown == 4
        assert pricing.serp.cost is None
        assert any("serpapi" in m.lower() for m in pricing.missing)
        # What may have been billed is the answered ones and the ones nobody answered; an error
        # answer is not billed.
        text = e2e.format_usage_pricing(pricing)
        assert re.search(r"billed or unknown.*4", text, re.I | re.S)
        assert re.search(r"error.*not billed", text, re.I | re.S)

    def test_a_per_search_rate_prices_serpapi_billed_or_unknown_as_an_upper_bound(
        self, e2e: ModuleType
    ) -> None:
        pricing = e2e.price_usage(rendered(a_scan()), serp_per_search=Decimal("0.015"))

        # One answered ok and three nobody answered: four searches that may have been billed.
        assert pricing.serp.billed_or_unknown == 4
        assert pricing.serp.cost == Decimal("0.060")
        assert not any("serpapi" in m.lower() for m in pricing.missing)
        # It is in the total, and it says what it is.
        assert pricing.total == EVERYTHING + Decimal("0.060")
        text = e2e.format_usage_pricing(pricing)
        assert re.search(r"serpapi billed or unknown.*upper bound", text, re.I | re.S)
        assert "0.0600" in text

    def test_the_field_is_read_off_the_line_when_it_is_there(self, e2e: ModuleType) -> None:
        line = rendered(a_scan())
        assert "serpapi_billed_or_unknown=4" in line

        assert e2e.price_usage(line, serp_per_search=Decimal("0.01")).serp.billed_or_unknown == 4

    def test_an_older_line_without_the_field_prices_the_same(self, e2e: ModuleType) -> None:
        """Lines logged before `serpapi_billed_or_unknown` existed have no such field."""
        line = rendered(a_scan())
        older = re.sub(r"\s*serpapi_billed_or_unknown=\d+", "", line)
        assert "serpapi_billed_or_unknown" not in older
        assert "serpapi_ok=1" in older and "serpapi_no_answer=3" in older

        newer_priced = e2e.price_usage(line, serp_per_search=Decimal("0.015"))
        older_priced = e2e.price_usage(older, serp_per_search=Decimal("0.015"))

        assert older_priced.serp.billed_or_unknown == 4 == newer_priced.serp.billed_or_unknown
        assert older_priced.serp.cost == newer_priced.serp.cost == Decimal("0.060")
        assert older_priced.total == newer_priced.total

    def test_an_older_line_without_the_rate_still_lists_serpapi_as_missing(
        self, e2e: ModuleType
    ) -> None:
        older = re.sub(r"\s*serpapi_billed_or_unknown=\d+", "", rendered(a_scan()))

        pricing = e2e.price_usage(older)

        assert pricing.serp.cost is None
        assert any("serpapi" in m.lower() for m in pricing.missing)

    def test_the_field_is_what_is_priced_when_a_line_has_it(self, e2e: ModuleType) -> None:
        """A line this code never writes (the field is not the sum of the two): the line's own
        figure is what it says, so it is the one priced."""
        line = ("scan.usage scan_id=scan_01X serpapi_attempted=6 serpapi_ok=2 serpapi_error=1 "
                "serpapi_no_answer=3 serpapi_billed_or_unknown=9")

        pricing = e2e.price_usage(line, serp_per_search=Decimal("0.01"))

        assert pricing.serp.billed_or_unknown == 9
        assert pricing.serp.cost == Decimal("0.09")

    def test_a_scan_that_asked_serpapi_nothing_has_nothing_to_price_or_to_miss(
        self, e2e: ModuleType
    ) -> None:
        line = ("scan.usage scan_id=scan_01X serpapi_attempted=0 serpapi_ok=0 "
                "serpapi_error=0 serpapi_no_answer=0")

        pricing = e2e.price_usage(line, serp_per_search=Decimal("0.015"))

        assert pricing.serp.billed_or_unknown == 0
        assert pricing.serp.cost is None
        assert pricing.missing == []
        assert "serpapi" not in e2e.format_usage_pricing(pricing).lower()

    def test_an_error_answer_alone_is_not_billed_so_it_prices_at_nothing(
        self, e2e: ModuleType
    ) -> None:
        line = ("scan.usage scan_id=scan_01X serpapi_attempted=2 serpapi_ok=0 "
                "serpapi_error=2 serpapi_no_answer=0")

        pricing = e2e.price_usage(line, serp_per_search=Decimal("0.015"))

        assert pricing.serp.billed_or_unknown == 0
        assert pricing.serp.cost == Decimal(0)
        assert pricing.missing == []


class TestTheDatesRatesWereRead:
    def test_every_rate_carries_the_day_it_was_read_and_the_page_it_came_from(
        self, e2e: ModuleType
    ) -> None:
        assert set(e2e.RATES) == {
            "claude-opus-5", "claude-haiku-4-5", "gpt-5.5", "perplexity/sonar", "gemini-3.8-flash"}
        for model, rate in e2e.RATES.items():
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rate.read), (model, rate.read)
            assert rate.source and "/" in rate.source, (model, rate.source)
            assert isinstance(rate.input, Decimal) and isinstance(rate.output, Decimal)

    def test_the_vendor_pages_the_2026_10_03_reading_covers(self, e2e: ModuleType) -> None:
        reads = {m: (r.read, r.source) for m, r in e2e.RATES.items()}
        assert reads["claude-opus-5"] == (
            "2026-10-03", "platform.claude.com/docs/en/about-claude/pricing")
        assert reads["claude-haiku-4-5"] == reads["claude-opus-5"]
        assert reads["gpt-5.5"] == ("2026-10-03", "developers.openai.com/api/docs/pricing")
        assert reads["gemini-3.8-flash"] == ("2026-10-03", "ai.google.dev/gemini-api/docs/pricing")
        assert reads["perplexity/sonar"] == (
            "2026-10-03", "docs.perplexity.ai/docs/agent-api/models")

    def test_the_per_tool_fees_carry_theirs_too(self, e2e: ModuleType) -> None:
        for name, fee in e2e.TOOL_FEES.items():
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fee.read), (name, fee.read)
            assert fee.source and "/" in fee.source, (name, fee.source)
            assert isinstance(fee.per_invocation, Decimal)

    def test_the_priced_output_names_each_rate_it_used_with_its_date(
        self, e2e: ModuleType
    ) -> None:
        text = e2e.format_usage_pricing(e2e.price_usage(rendered(a_scan())))

        for source in ("platform.claude.com/docs/en/about-claude/pricing",
                       "developers.openai.com/api/docs/pricing",
                       "ai.google.dev/gemini-api/docs/pricing",
                       "docs.perplexity.ai/docs/agent-api/models"):
            assert source in text, source
        assert "2026-10-03" in text
        assert "perishable" in text.lower()


class TestWhatCanBePasted:
    def test_the_line_as_render_shows_it_with_its_timestamp_and_level(
        self, e2e: ModuleType
    ) -> None:
        line = rendered(a_scan(), prefix=True)
        assert line.startswith("2026-10-04 03:49:18 [info")
        assert e2e.price_usage(line).total == EVERYTHING

    def test_the_bare_fields_price_the_same(self, e2e: ModuleType) -> None:
        assert e2e.price_usage(rendered(a_scan(), prefix=False)).total == EVERYTHING

    def test_a_json_line_prices_the_same_so_a_json_renderer_would_not_break_it(
        self, e2e: ModuleType
    ) -> None:
        line = json.dumps({"event": "scan.usage", "level": "info", **a_scan().log_fields()})
        assert e2e.price_usage(line).total == EVERYTHING

    def test_a_line_that_has_wandered_in_with_other_text_is_still_found(
        self, e2e: ModuleType
    ) -> None:
        pasted = "\n".join([
            "2026-10-04 03:49:17 [info     ] scan.finalized  scan_id=scan_01PRICED"
            " status=succeeded",
            rendered(a_scan()),
            "2026-10-04 03:49:19 [info     ] scan.phase_memory  phase=finalized",
        ])
        assert e2e.price_usage(pasted).total == EVERYTHING

    def test_a_line_that_is_not_a_scan_usage_line_is_refused_in_words(
        self, e2e: ModuleType
    ) -> None:
        with pytest.raises(ValueError, match="scan.usage"):
            e2e.price_usage("2026-10-04 03:49:18 [info     ] llm.call  provider=anthropic")
        with pytest.raises(ValueError, match="scan.usage"):
            e2e.price_usage("")

    def test_a_field_it_does_not_know_is_ignored_and_a_missing_one_is_zero(
        self, e2e: ModuleType
    ) -> None:
        line = "scan.usage scan_id=scan_01X sentiment_calls=2 sentiment_input_tokens=1000000 " \
               "sentiment_model=claude-haiku-4-5-20251001 something_new=7"
        pricing = e2e.price_usage(line)
        assert pricing.total == Decimal("1")  # a million input tokens at a dollar
        assert pricing.rows[0].output_tokens == 0


class TestTheCommandLine:
    def test_it_prices_without_a_database_a_key_or_a_provider(
        self, e2e: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)

        code = asyncio.run(e2e.main(["--price-usage", rendered(a_scan())]))

        out = capsys.readouterr().out
        assert code == 0
        assert "scan_01PRICED" in out
        assert f"{EVERYTHING:.4f}" in out
        assert "2026-10-03" in out

    def test_it_reads_the_line_from_standard_input_when_given_a_dash(
        self, e2e: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(rendered(a_scan())))

        code = asyncio.run(e2e.main(["--price-usage", "-"]))

        assert code == 0
        assert f"{EVERYTHING:.4f}" in capsys.readouterr().out

    def test_a_bad_line_is_a_message_and_a_nonzero_exit_not_a_traceback(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = asyncio.run(e2e.main(["--price-usage", "nonsense"]))

        assert code == 2
        assert "scan.usage" in capsys.readouterr().err

    def test_the_per_search_rate_is_taken_from_the_flag(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = asyncio.run(e2e.main([
            "--price-usage", rendered(a_scan()), "--serp-per-search", "0.015"]))

        out = capsys.readouterr().out
        assert code == 0
        # Four searches that may have been billed, at 0.015 each, labelled as what they are.
        assert "0.0600" in out
        assert re.search(r"serpapi billed or unknown.*upper bound", out, re.I | re.S)

    def test_the_flag_says_what_it_is_applied_to(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as helped:
            asyncio.run(e2e.main(["--help"]))

        assert helped.value.code == 0
        assert "serpapi_billed_or_unknown" in " ".join(capsys.readouterr().out.split())


BAD_PRICES = [
    pytest.param("NaN", id="NaN"),
    pytest.param("nan", id="nan"),
    pytest.param("sNaN", id="sNaN"),
    pytest.param("Infinity", id="Infinity"),
    pytest.param("inf", id="inf"),
    pytest.param("-1", id="minus-one"),
    pytest.param("-0.015", id="minus-a-price"),
    pytest.param("abc", id="not-a-number"),
    pytest.param("", id="empty"),
    pytest.param("1e", id="half-a-number"),
    pytest.param("1E+99999999999999999999", id="an-exponent-decimal-cannot-hold"),
]
# The same refused when written `--flag=value`, which is the only way a value that starts with a
# dash is read as a value and not as another option.
BAD_PRICES_WITH_A_DASH = [
    pytest.param("-NaN", id="minus-NaN"),
    pytest.param("-sNaN", id="minus-sNaN"),
    pytest.param("-Infinity", id="minus-Infinity"),
    pytest.param("-inf", id="minus-inf"),
    pytest.param("-1e-9", id="minus-a-tiny-price"),
]


class TestThePerSearchRateIsRefusedUnlessItIsAPrice:
    """`--serp-per-search` is money in, so it is checked like money: finite, and not below zero."""

    @pytest.mark.parametrize("text", BAD_PRICES)
    def test_a_value_that_is_not_a_price_is_exit_code_2_and_a_plain_message(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str], text: str
    ) -> None:
        with pytest.raises(SystemExit) as refused:
            asyncio.run(e2e.main([
                "--price-usage", rendered(a_scan()), f"--serp-per-search={text}"]))

        captured = capsys.readouterr()
        assert refused.value.code == 2
        assert captured.out == "", "nothing was priced"
        assert "--serp-per-search" in captured.err
        assert "Traceback" not in captured.err
        assert "InvalidOperation" not in captured.err

    @pytest.mark.parametrize("text", BAD_PRICES_WITH_A_DASH)
    def test_a_value_that_starts_with_a_dash_is_refused_the_same_way(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str], text: str
    ) -> None:
        with pytest.raises(SystemExit) as refused:
            asyncio.run(e2e.main([
                "--price-usage", rendered(a_scan()), f"--serp-per-search={text}"]))

        captured = capsys.readouterr()
        assert refused.value.code == 2
        assert captured.out == "" and "Traceback" not in captured.err
        assert "--serp-per-search" in captured.err

    @pytest.mark.parametrize("text", ["NaN", "sNaN", "Infinity", "-1"])
    def test_the_space_separated_form_is_refused_too(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str], text: str
    ) -> None:
        with pytest.raises(SystemExit) as refused:
            asyncio.run(e2e.main([
                "--price-usage", rendered(a_scan()), "--serp-per-search", text]))

        assert refused.value.code == 2
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("text", ["NaN", "sNaN", "Infinity", "-Infinity", "-0.5"])
    def test_the_message_says_what_a_price_is(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str], text: str
    ) -> None:
        with pytest.raises(SystemExit):
            asyncio.run(e2e.main([
                "--price-usage", rendered(a_scan()), f"--serp-per-search={text}"]))

        message = " ".join(capsys.readouterr().err.split())
        assert "finite number of 0 or more" in message
        assert text in message

    def test_a_price_too_large_to_multiply_is_a_message_and_exit_code_2_too(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Finite and not negative, so it is a price to the parser, and it overflows `Decimal`
        when it is multiplied: `decimal.Overflow` is not `InvalidOperation`."""
        code = asyncio.run(e2e.main([
            "--price-usage", rendered(a_scan()), "--serp-per-search=1e999999999"]))

        captured = capsys.readouterr()
        assert code == 2
        assert captured.out == "" and "Traceback" not in captured.err
        assert "--serp-per-search" in captured.err

    @pytest.mark.parametrize("bad", [
        Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
        Decimal("-1"), Decimal("-0.0001"),
    ])
    def test_the_function_refuses_them_too_for_a_caller_that_is_not_the_command_line(
        self, e2e: ModuleType, bad: Decimal
    ) -> None:
        with pytest.raises(ValueError, match="finite number of 0 or more"):
            e2e.price_usage(rendered(a_scan()), serp_per_search=bad)

    @pytest.mark.parametrize("fine", ["0", "0.015", "1", "0.000001", "-0", "-0.0", "250"])
    def test_a_price_is_accepted_and_priced(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str], fine: str
    ) -> None:
        code = asyncio.run(e2e.main([
            "--price-usage", rendered(a_scan()), f"--serp-per-search={fine}"]))

        out = capsys.readouterr().out
        assert code == 0
        expected_cost = Decimal(fine).copy_abs() * 4
        assert f"{expected_cost:.4f}" in out
        assert "-0.0000" not in out, "a negative zero is zero"

    def test_no_price_given_is_still_fine_and_still_lists_serpapi_as_missing(
        self, e2e: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = asyncio.run(e2e.main(["--price-usage", rendered(a_scan())]))

        assert code == 0
        assert "no per-search rate recorded" in capsys.readouterr().out


class TestTheLiveMeterStillWorks:
    def test_it_prices_a_call_through_the_dated_table_as_it_always_did(
        self, e2e: ModuleType
    ) -> None:
        e2e.TOKENS.clear()
        entry = e2e.record_tokens(
            "probe", "claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
        assert entry.token_cost == pytest.approx(30.0)
        assert not entry.unpriced_models
        e2e.record_tokens("probe", "no-such-model", input_tokens=1, output_tokens=1)
        assert e2e.TOKENS["probe"].unpriced_models == {"no-such-model"}
        e2e.TOKENS.clear()

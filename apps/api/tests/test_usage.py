"""The usage module itself — U1, 2026-10-04.

`test_llm_call_lines.py` proves what each paid call logs, from each provider's own response.
This file proves the parts that are not any one provider's:

* the per-scan accumulator is carried by a context variable, so a call made anywhere inside a
  scan's chain adds to that scan, through the tasks `asyncio.gather` and `create_task` start
  (which copy the context), and a call made outside one only logs;
* a scan's total is built from the same records the per-call lines are, so the two cannot
  disagree, and classification, which is not part of a scan, is never in it;
* the total is ONE line of flat `key=value` counts a pasted line can be parsed back from;
* counting a call can never break it: a malformed `usage`, a failing logger, a failing
  accumulator and a cancelled task all leave the call's own outcome as it was;
* a purpose cannot be added without its line in a scan's total.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from avp_api.services import usage
from avp_api.services.usage import CallUsage, Purpose, ScanUsage

ENGINE = Purpose.ENGINE


def call(**overrides: Any) -> CallUsage:
    base: dict[str, Any] = {
        "provider": "anthropic", "model": "claude-opus-5", "purpose": ENGINE, "engine": "claude",
    }
    return CallUsage(**{**base, **overrides})


def events_named(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == name]


# ---------------------------------------------------------------------------
# what a response's usage is read as
# ---------------------------------------------------------------------------


class TestReadingAUsageBlock:
    def test_anthropic_reads_the_typed_message_and_leaves_input_as_reported(self) -> None:
        message = SimpleNamespace(
            model="claude-opus-5-20261001",
            usage=SimpleNamespace(
                input_tokens=12, output_tokens=34,
                cache_read_input_tokens=5, cache_creation_input_tokens=6,
                output_tokens_details=SimpleNamespace(thinking_tokens=20),
                server_tool_use=SimpleNamespace(web_search_requests=2, web_fetch_requests=9),
            ),
        )
        counts = usage.counts_from_anthropic(message)
        assert counts == usage.Counts(
            model="claude-opus-5-20261001", input_tokens=12, output_tokens=34,
            reasoning_tokens=20, cache_read_tokens=5, cache_write_tokens=6,
            # Searches only: this product never asks for a fetch.
            search_invocations=2,
        )

    def test_openai_reads_the_chat_completions_body(self) -> None:
        counts = usage.counts_from_openai({
            "model": "gpt-5.5-2026-04-23",
            "usage": {
                "prompt_tokens": 100, "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 10},
                "completion_tokens_details": {"reasoning_tokens": 30},
            },
        })
        assert counts == usage.Counts(
            model="gpt-5.5-2026-04-23", input_tokens=100, output_tokens=50,
            reasoning_tokens=30, cache_read_tokens=10,
        )

    def test_perplexity_reads_the_agent_api_body(self) -> None:
        counts = usage.counts_from_perplexity({
            "model": "perplexity/sonar",
            "usage": {
                "input_tokens": 1758, "output_tokens": 432,
                "input_tokens_details": {"cache_read_input_tokens": 5},
                "tool_calls_details": {"search_web": {"invocation": 2}},
                "cost": {"total_cost": 0.5},
            },
        })
        assert counts == usage.Counts(
            model="perplexity/sonar", input_tokens=1758, output_tokens=432,
            cache_read_tokens=5, search_invocations=2,
        )

    def test_gemini_adds_thoughts_to_the_output_and_counts_the_grounding_queries(self) -> None:
        counts = usage.counts_from_gemini({
            "modelVersion": "gemini-3.8-flash-001",
            "candidates": [{"groundingMetadata": {"webSearchQueries": ["a", "b", "c"]}}],
            "usageMetadata": {
                "promptTokenCount": 20, "candidatesTokenCount": 30, "thoughtsTokenCount": 5,
                "cachedContentTokenCount": 4,
            },
        })
        assert counts == usage.Counts(
            model="gemini-3.8-flash-001", input_tokens=20, output_tokens=35,
            reasoning_tokens=5, cache_read_tokens=4, search_invocations=3,
        )

    @pytest.mark.parametrize("junk", [
        None, "", "12", [], {}, True, False, -5, float("nan"), float("inf"), object(),
        {"input_tokens": "lots"}, {"input_tokens": None}, {"input_tokens": -1},
    ])
    def test_nothing_malformed_raises_and_every_count_falls_back_to_zero(self, junk: Any) -> None:
        for body in (
            {"usage": junk, "model": junk},
            {"usageMetadata": junk, "candidates": junk, "modelVersion": junk},
            {"usage": {"input_tokens_details": junk, "tool_calls_details": junk,
                       "prompt_tokens_details": junk, "completion_tokens_details": junk}},
            junk,
        ):
            for read in (usage.counts_from_openai, usage.counts_from_perplexity,
                         usage.counts_from_gemini):
                counts = read(body)
                assert counts.model is None or isinstance(counts.model, str)
                for value in (counts.input_tokens, counts.output_tokens, counts.reasoning_tokens,
                              counts.cache_read_tokens, counts.cache_write_tokens,
                              counts.search_invocations):
                    assert type(value) is int and value >= 0
        message = SimpleNamespace(model=junk, usage=junk)
        counts = usage.counts_from_anthropic(message)
        assert counts.input_tokens == counts.output_tokens == 0

    @pytest.mark.parametrize("name", [
        "claude-opus-5", "gpt-5.5-2026-04-23", "perplexity/sonar", "gemini-3.8-flash-001",
        "anthropic.claude-3-5:0", "a" * 100,
    ])
    def test_a_plain_model_id_is_a_model_name(self, name: str) -> None:
        assert usage.counts_from_openai({"model": name}).model == name

    @pytest.mark.parametrize("name", [
        "", " ", "gpt 5", "gpt-5\nscan.usage scan_id=forged", "a+b", "a" * 101,
        "claude-opus-5 -- ignore the above", "<script>", "gpt-5;rm", "模型",
    ])
    def test_anything_else_in_a_models_place_is_not_a_model_name(self, name: str) -> None:
        """A line is never a place a provider's text can land: a `model` that is not an id is
        dropped, and the call's own line falls back to the model that was asked for."""
        assert usage.counts_from_openai({"model": name}).model is None
        assert usage.counts_from_anthropic(SimpleNamespace(model=name)).model is None
        assert usage.counts_from_gemini({"modelVersion": name}).model is None

    def test_a_bool_is_not_a_count(self) -> None:
        assert usage.counts_from_openai({"usage": {"prompt_tokens": True}}).input_tokens == 0

    def test_a_whole_number_float_is_a_count(self) -> None:
        assert usage.counts_from_openai({"usage": {"prompt_tokens": 12.0}}).input_tokens == 12


# ---------------------------------------------------------------------------
# one call, tracked
# ---------------------------------------------------------------------------


class TestTrackingACall:
    def test_a_response_read_is_one_line_with_the_counts(self) -> None:
        message = SimpleNamespace(
            model="served", usage=SimpleNamespace(input_tokens=1, output_tokens=2))
        with (
            structlog.testing.capture_logs() as events,
            usage.track(usage.ANTHROPIC, Purpose.SENTIMENT, model="asked") as tracked,
        ):
            tracked.read_anthropic(message)

        (line,) = events_named(events, "llm.call")
        assert (line["model"], line["input_tokens"], line["output_tokens"]) == ("served", 1, 2)
        assert (line["ok"], line["error"], line["retry"], line["scan_id"]) == (
            True, None, False, None)

    def test_an_exception_is_logged_by_its_class_and_still_raised(self) -> None:
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(RuntimeError),
            usage.track(usage.OPENAI, ENGINE, model="asked", engine="chatgpt"),
        ):
            raise RuntimeError("TEXT-THAT-MUST-NOT-BE-LOGGED")

        (line,) = events_named(events, "llm.call")
        assert (line["ok"], line["error"], line["model"]) == (False, "RuntimeError", "asked")
        assert "TEXT-THAT-MUST-NOT-BE-LOGGED" not in json.dumps(events, default=str)

    def test_a_http_error_status_is_named_by_its_status_not_its_message(self) -> None:
        import httpx

        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        error = httpx.HTTPStatusError(
            "SECRET-BODY", request=request, response=httpx.Response(503, request=request))
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(httpx.HTTPStatusError),
            usage.track(usage.OPENAI, ENGINE, model="m", engine="chatgpt"),
        ):
            raise error

        (line,) = events_named(events, "llm.call")
        assert line["error"] == "http_503"

    async def test_a_cancelled_call_is_logged_and_still_cancelled(self) -> None:
        started = asyncio.Event()

        async def work() -> None:
            with usage.track(usage.GOOGLE, ENGINE, model="m", engine="gemini"):
                started.set()
                await asyncio.sleep(3600)

        with structlog.testing.capture_logs() as events:
            task = asyncio.create_task(work())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        (line,) = events_named(events, "llm.call")
        assert (line["ok"], line["error"]) == (False, "CancelledError")

    def test_a_failing_log_never_hides_the_calls_own_exception_or_breaks_a_clean_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*args: Any, **kwargs: Any) -> None:
            raise OSError("the log stream is gone")

        monkeypatch.setattr(usage, "logger", SimpleNamespace(info=broken))

        with (
            pytest.raises(ValueError, match="the call's own"),
            usage.track(usage.ANTHROPIC, ENGINE, model="m", engine="claude"),
        ):
            raise ValueError("the call's own")
        # And a call that went fine returns as it would have.
        with usage.track(usage.ANTHROPIC, ENGINE, model="m", engine="claude") as tracked:
            tracked.read_anthropic(SimpleNamespace(model="m", usage=None))

    def test_a_failing_accumulator_in_exit_leaves_the_calls_result_and_its_line_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`__exit__` adds each request to the scan; that is as harmless as the log beside it."""
        def broken(self: ScanUsage, call: CallUsage) -> None:
            raise RuntimeError("the accumulator broke")

        monkeypatch.setattr(ScanUsage, "add", broken)
        message = SimpleNamespace(model="m", usage=SimpleNamespace(input_tokens=3, output_tokens=4))
        with (
            structlog.testing.capture_logs() as events,
            usage.scan_scope("scan_1"),
            usage.track(usage.ANTHROPIC, ENGINE, model="m", engine="claude") as tracked,
        ):
            tracked.read_anthropic(message)
            result = "the call's own result"

        assert result == "the call's own result"
        (line,) = events_named(events, "llm.call")
        assert (line["scan_id"], line["input_tokens"], line["ok"]) == ("scan_1", 3, True)

    def test_a_failing_accumulator_in_exit_never_replaces_the_calls_own_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(self: ScanUsage, call: CallUsage) -> None:
            raise RuntimeError("the accumulator broke")

        monkeypatch.setattr(ScanUsage, "add", broken)
        with (
            pytest.raises(ValueError, match="the call's own") as raised,
            usage.scan_scope("scan_1"),
            usage.track(usage.ANTHROPIC, ENGINE, model="m", engine="claude"),
        ):
            raise ValueError("the call's own")

        # Nothing from the bookkeeping rides along as the exception's context either.
        assert raised.value.__context__ is None

    def test_an_sdk_without_middleware_gets_no_options_and_its_calls_still_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallback `sdk_client_options` promises: a client with nothing to report with."""
        monkeypatch.setattr(usage, "_SDK_TAKES_MIDDLEWARE", False)
        assert usage.sdk_client_options() == {}

        message = SimpleNamespace(model="m", usage=SimpleNamespace(input_tokens=3, output_tokens=4))
        with (
            structlog.testing.capture_logs() as events,
            usage.track(usage.ANTHROPIC, ENGINE, model="asked", engine="claude") as tracked,
        ):
            tracked.read_anthropic(message)

        (line,) = events_named(events, "llm.call")
        assert (line["ok"], line["retry"], line["input_tokens"]) == (True, False, 3)

    def test_a_reader_that_raises_leaves_the_call_unharmed_and_unanswered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(message: Any) -> usage.Counts:
            raise RuntimeError("an SDK type changed under us")

        monkeypatch.setattr(usage, "counts_from_anthropic", broken)
        with (
            structlog.testing.capture_logs() as events,
            usage.track(usage.ANTHROPIC, ENGINE, model="asked", engine="claude") as tracked,
        ):
            tracked.read_anthropic(SimpleNamespace())

        (line,) = events_named(events, "llm.call")
        assert (line["ok"], line["model"], line["input_tokens"]) == (False, "asked", 0)

    def test_attempts_that_failed_before_the_answering_one_are_lines_too(self) -> None:
        with (
            structlog.testing.capture_logs() as events,
            usage.track(usage.PERPLEXITY, ENGINE, model="asked", engine="perplexity") as t,
        ):
            t.begin_attempt()
            t.attempt_failed("http_429")
            t.begin_attempt()
            t.read_perplexity({"model": "served", "usage": {"input_tokens": 9}})

        first, second = events_named(events, "llm.call")
        assert (first["ok"], first["retry"], first["error"]) == (False, False, "http_429")
        assert (second["ok"], second["retry"], second["input_tokens"]) == (True, True, 9)

    def test_three_attempts_make_two_retries(self) -> None:
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(RuntimeError),
            usage.track(usage.ANTHROPIC, ENGINE, model="m", engine="claude") as t,
        ):
            for label in ("http_529", "APITimeoutError"):
                t.begin_attempt()
                t.attempt_failed(label)
            t.begin_attempt()
            raise RuntimeError

        first, second, third = events_named(events, "llm.call")
        assert [(c["retry"], c["ok"]) for c in (first, second, third)] == [
            (False, False), (True, False), (True, False)]
        assert [c["error"] for c in (first, second)] == ["http_529", "APITimeoutError"]

    def test_the_sdk_middleware_counts_nothing_for_a_call_that_is_not_tracked(self) -> None:
        """No tracker, no bookkeeping: a client used outside `track` behaves as it always did."""
        async def go() -> str:
            async def call_next(request: Any) -> str:
                return "response"

            return await usage.count_attempts(SimpleNamespace(), call_next)

        assert asyncio.run(go()) == "response"

    def test_the_sdk_middleware_is_offered_to_a_client_that_supports_it(self) -> None:
        import anthropic

        options = usage.sdk_client_options()
        assert options == {"middleware": [usage.count_attempts]}
        # And the pinned SDK takes it: a client built with it exists and keeps its retries.
        client = anthropic.AsyncAnthropic(api_key="k", max_retries=1, **options)
        assert client.max_retries == 1
        assert len(client.middleware) == 1


# ---------------------------------------------------------------------------
# a scan's total
# ---------------------------------------------------------------------------


class TestTheScanTotal:
    def test_a_scan_with_no_calls_still_has_every_line_and_the_three_counters_every_line_has(
        self,
    ) -> None:
        fields = ScanUsage("scan_1").log_fields()
        assert fields["scan_id"] == "scan_1"
        for line in ("engines", "sentiment", "prompt_generation", "fixes", "detection"):
            assert [k for k in fields if k.startswith(f"{line}_")] == [
                f"{line}_calls", f"{line}_input_tokens", f"{line}_output_tokens"], line
            assert (fields[f"{line}_calls"], fields[f"{line}_input_tokens"],
                    fields[f"{line}_output_tokens"]) == (0, 0, 0)
        for key in ("attempted", "ok", "error", "no_answer"):
            assert fields[f"serpapi_{key}"] == 0
        # Nothing was asked of SerpApi, so there is nothing billed or unknown to say.
        assert "serpapi_billed_or_unknown" not in fields
        # No engine ran, so no engine has a line of its own.
        assert not [k for k in fields if k.startswith("engine_")]

    def test_a_counter_that_is_zero_is_left_out_and_a_model_nobody_named_is_too(self) -> None:
        scan = ScanUsage("scan_1")
        scan.add(call(engine="claude", input_tokens=10, output_tokens=20, model=None))
        scan.add(call(engine="claude", input_tokens=1, output_tokens=2, reasoning_tokens=1,
                      model=None))
        f = scan.log_fields()

        assert f["engine_claude_reasoning_tokens"] == 1
        for absent in ("unanswered", "retries", "cache_read_tokens", "cache_write_tokens",
                       "search_invocations", "model"):
            assert f"engine_claude_{absent}" not in f, absent
        # A line with a model carries it.
        scan.add(call(engine="claude", model="claude-opus-5"))
        assert scan.log_fields()["engine_claude_model"] == "claude-opus-5"

    def test_each_line_is_the_sum_of_its_calls(self) -> None:
        scan = ScanUsage("scan_1")
        scan.add(call(engine="claude", input_tokens=10, output_tokens=20, reasoning_tokens=5))
        scan.add(call(engine="claude", input_tokens=1, output_tokens=2, retry=True))
        scan.add(call(engine="chatgpt", provider="openai", model="gpt-5.5-2026-04-23",
                      input_tokens=100, output_tokens=200, cache_read_tokens=7))
        scan.add(call(engine="perplexity", provider="perplexity", model="perplexity/sonar",
                      ok=False, error="http_429"))
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="claude-haiku-4-5-20251001",
                      input_tokens=40, output_tokens=4))
        scan.add(call(purpose=Purpose.PROMPT_GENERATION, engine=None, input_tokens=600,
                      output_tokens=2500, reasoning_tokens=1800))
        scan.add(call(purpose=Purpose.FIXES, engine=None, input_tokens=2000, output_tokens=5000,
                      search_invocations=0))
        scan.add(call(purpose=Purpose.DETECTION, engine=None, input_tokens=300, output_tokens=100))
        scan.add(call(purpose=Purpose.DETECTION, engine=None, input_tokens=300, output_tokens=100,
                      retry=True))
        f = scan.log_fields()

        # The engines line is every engine together ...
        assert (f["engines_calls"], f["engines_unanswered"], f["engines_retries"]) == (4, 1, 1)
        assert (f["engine_claude_calls"], "engine_claude_unanswered" in f) == (2, False)
        assert (f["engines_input_tokens"], f["engines_output_tokens"]) == (111, 222)
        assert (f["engines_reasoning_tokens"], f["engines_cache_read_tokens"]) == (5, 7)
        # Only answered requests name a model: Perplexity's one request got no answer.
        assert f["engines_model"] == "claude-opus-5+gpt-5.5-2026-04-23"
        # ... and each engine that ran has its own.
        assert (f["engine_claude_calls"], f["engine_claude_retries"]) == (2, 1)
        assert (f["engine_claude_input_tokens"], f["engine_claude_output_tokens"]) == (11, 22)
        assert (f["engine_chatgpt_calls"], f["engine_chatgpt_cache_read_tokens"]) == (1, 7)
        assert (f["engine_perplexity_calls"], f["engine_perplexity_unanswered"]) == (1, 1)
        assert f["engine_chatgpt_model"] == "gpt-5.5-2026-04-23"
        assert not [k for k in f if k.startswith("engine_gemini")]
        # The four lines that are not engines.
        assert (f["sentiment_calls"], f["sentiment_input_tokens"]) == (1, 40)
        assert f["sentiment_model"] == "claude-haiku-4-5-20251001"
        assert (f["prompt_generation_calls"], f["prompt_generation_reasoning_tokens"]) == (1, 1800)
        assert (f["fixes_calls"], f["fixes_output_tokens"]) == (1, 5000)
        assert (f["detection_calls"], f["detection_retries"], f["detection_input_tokens"]) == (
            2, 1, 600)

    def test_the_engines_line_is_the_sum_of_the_per_engine_lines_for_every_counter(self) -> None:
        scan = ScanUsage("scan_1")
        for i, engine in enumerate(("claude", "chatgpt", "perplexity", "gemini", "claude_search")):
            scan.add(call(
                engine=engine, input_tokens=10 * (i + 1), output_tokens=20 * (i + 1),
                reasoning_tokens=i, cache_read_tokens=i + 1, cache_write_tokens=i + 2,
                search_invocations=i, retry=bool(i % 2), ok=bool(i % 3),
            ))
        f = scan.log_fields()
        for counter in ("calls", "unanswered", "retries", "input_tokens", "output_tokens",
                        "reasoning_tokens", "cache_read_tokens", "cache_write_tokens",
                        "search_invocations"):
            per_engine = sum(
                value for key, value in f.items()
                if key.startswith("engine_") and key.endswith(f"_{counter}")
            )
            assert f.get(f"engines_{counter}", 0) == per_engine, counter
            assert per_engine > 0 or counter in ("unanswered", "retries"), counter

    def test_classification_is_not_part_of_a_scan(self) -> None:
        scan = ScanUsage("scan_1")
        scan.add(call(purpose=Purpose.CLASSIFICATION, engine=None, input_tokens=999,
                      output_tokens=999))
        f = scan.log_fields()
        assert all(value in (0, None) for key, value in f.items() if key != "scan_id"), f
        assert not [k for k in f if "classification" in k]

    def test_a_mixed_engine_line_names_every_model_and_is_not_one_of_them(self) -> None:
        scan = ScanUsage("scan_1")
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="a-1", input_tokens=1))
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model="a-2", input_tokens=1))
        scan.add(call(purpose=Purpose.SENTIMENT, engine=None, model=None, input_tokens=1))
        assert scan.log_fields()["sentiment_model"] == "a-1+a-2"

    def test_a_request_that_got_no_answer_names_no_model_in_the_total(self) -> None:
        """`gpt-5.5` is what we ask for and `gpt-5.5-2026-04-23` is what answers: a failed
        request carrying the first would make an answered line look served by two models."""
        scan = ScanUsage("scan_1")
        scan.add(call(engine="chatgpt", model="gpt-5.5", ok=False, error="http_500"))
        scan.add(call(engine="chatgpt", model="gpt-5.5-2026-04-23", input_tokens=5))
        scan.add(call(engine="chatgpt", model="gpt-5.5", ok=False, retry=True))
        f = scan.log_fields()
        assert f["engine_chatgpt_model"] == "gpt-5.5-2026-04-23"
        assert f["engines_model"] == "gpt-5.5-2026-04-23"
        assert f["engine_chatgpt_calls"] == 3

    def test_the_total_is_flat_so_it_is_one_parseable_line(self) -> None:
        scan = ScanUsage("scan_1")
        scan.add(call(input_tokens=1))
        for key, value in scan.log_fields().items():
            assert isinstance(value, str | int | None), (key, value)
            assert " " not in key and key == key.lower()
        rendered = structlog.dev.ConsoleRenderer(colors=False)(
            None, "info", {"event": "scan.usage", **scan.log_fields()})
        parsed = dict(part.split("=", 1) for part in rendered.split() if "=" in part)
        assert parsed["scan_id"] == "scan_1"
        assert parsed["engine_claude_calls"] == "1"
        assert parsed["engines_input_tokens"] == "1"

    def test_serpapi_counts_add_up_to_the_searches_attempted(self) -> None:
        scan = ScanUsage("scan_1")
        scan.add_serp(attempts=2, no_answer=1, answer="ok")        # a retry that answered
        scan.add_serp(attempts=2, no_answer=2, answer=None)        # asked twice, never answered
        scan.add_serp(attempts=1, no_answer=0, answer="error")     # answered with an error
        scan.add_serp(attempts=1, no_answer=0, answer="ok")
        f = scan.log_fields()
        assert (f["serpapi_attempted"], f["serpapi_ok"], f["serpapi_error"],
                f["serpapi_no_answer"]) == (6, 2, 1, 3)
        assert f["serpapi_ok"] + f["serpapi_error"] + f["serpapi_no_answer"] == f[
            "serpapi_attempted"]
        # What may have been billed: the answered ones and the ones nobody can say about.
        assert f["serpapi_billed_or_unknown"] == f["serpapi_ok"] + f["serpapi_no_answer"] == 5

    def test_serpapi_billed_or_unknown_is_ok_plus_no_answer_and_is_left_out_when_zero(
        self,
    ) -> None:
        """An upper bound on what SerpApi billed: an answered search is billed unless SerpApi
        served it from its cache, an error answer is not billed, and a search nobody answered
        may have been."""
        scan = ScanUsage("scan_1")
        assert "serpapi_billed_or_unknown" not in scan.log_fields()

        scan.add_serp(attempts=1, no_answer=0, answer="error")
        # Asked, answered with an error, not billed: nothing to say, so no field.
        assert scan.log_fields()["serpapi_attempted"] == 1
        assert "serpapi_billed_or_unknown" not in scan.log_fields()

        scan.add_serp(attempts=2, no_answer=1, answer="ok")      # one unanswered, one ok
        assert scan.log_fields()["serpapi_billed_or_unknown"] == 2
        scan.add_serp(attempts=2, no_answer=2, answer=None)      # asked twice, never answered
        f = scan.log_fields()
        assert f["serpapi_billed_or_unknown"] == 4 == f["serpapi_ok"] + f["serpapi_no_answer"]

    def test_the_billed_or_unknown_field_is_a_flat_key_value_like_the_rest(self) -> None:
        scan = ScanUsage("scan_1")
        scan.add_serp(attempts=1, no_answer=0, answer="ok")
        rendered = structlog.dev.ConsoleRenderer(colors=False)(
            None, "info", {"event": "scan.usage", **scan.log_fields()})
        parsed = dict(part.split("=", 1) for part in rendered.split() if "=" in part)
        assert parsed["serpapi_billed_or_unknown"] == "1"


def one_call(
    *, requests: int, answered: bool, engine: str | None = "perplexity",
    purpose: Purpose = ENGINE, provider: str = usage.PERPLEXITY,
) -> None:
    """One tracked call that made `requests` requests, reported the way an adapter reports them.

    Every request but the last got no usage back and was sent again. The last was answered, or
    (`answered=False`) was refused too and ended the call by raising, as an adapter's does.
    """
    assert requests >= 1
    try:
        with usage.track(provider, purpose, model="asked", engine=engine) as tracked:
            for _ in range(requests - 1):
                tracked.begin_attempt()
                tracked.attempt_failed("http_429")
            tracked.begin_attempt()
            if not answered:
                raise RuntimeError("the last request got no answer either")
            tracked.read_perplexity({"model": "served", "usage": {"input_tokens": 9}})
    except RuntimeError:
        pass


class TestWhatUnansweredAndRetriesCount:
    """`calls` and `retries` count REQUESTS and `unanswered` counts CALLS (U5, 2026-10-06).

    What set it: a Perplexity prompt was refused with a 429, sent again and answered, and the
    scan's total said `engine_perplexity_unanswered=1` of a scan whose every result was there.
    A request that a retry rescued is a retry, not a prompt that never got an answer. A call is
    everything one `track(...)` wraps, which for an engine is one prompt asked of one engine.
    """

    def test_a_429_then_an_answer_is_a_retry_and_nothing_is_unanswered(self) -> None:
        with usage.scan_scope("scan_1") as scan:
            one_call(requests=2, answered=True)

        f = scan.log_fields()
        assert (f["engine_perplexity_calls"], f["engine_perplexity_retries"]) == (2, 1)
        assert "engine_perplexity_unanswered" not in f
        assert (f["engines_calls"], f["engines_retries"]) == (2, 1)
        assert "engines_unanswered" not in f

    def test_two_requests_and_no_answer_is_one_unanswered_call_and_one_retry(self) -> None:
        with usage.scan_scope("scan_1") as scan:
            one_call(requests=2, answered=False)

        f = scan.log_fields()
        assert (f["engine_perplexity_calls"], f["engine_perplexity_unanswered"],
                f["engine_perplexity_retries"]) == (2, 1, 1)
        assert (f["engines_calls"], f["engines_unanswered"], f["engines_retries"]) == (2, 1, 1)

    @pytest.mark.parametrize("requests", [1, 2, 3, 4])
    def test_a_call_that_got_no_answer_is_one_unanswered_call_however_many_requests_it_made(
        self, requests: int
    ) -> None:
        with usage.scan_scope("scan_1") as scan:
            one_call(requests=requests, answered=False)

        f = scan.log_fields()
        assert f["engine_perplexity_calls"] == requests
        assert f["engine_perplexity_unanswered"] == 1
        assert f.get("engine_perplexity_retries", 0) == requests - 1

    @pytest.mark.parametrize("requests", [1, 2, 3, 4])
    def test_an_answered_call_is_never_unanswered_however_many_requests_failed_before_it(
        self, requests: int
    ) -> None:
        with usage.scan_scope("scan_1") as scan:
            one_call(requests=requests, answered=True)

        f = scan.log_fields()
        assert f["engine_perplexity_calls"] == requests
        assert "engine_perplexity_unanswered" not in f
        assert f.get("engine_perplexity_retries", 0) == requests - 1

    def test_each_engine_is_counted_on_its_own_and_the_engines_line_is_their_sum(self) -> None:
        with usage.scan_scope("scan_1") as scan:
            one_call(requests=2, answered=True)                          # perplexity, rescued
            one_call(requests=2, answered=False)                         # perplexity, no answer
            one_call(requests=1, answered=False, engine="claude")        # claude, refused once
            one_call(requests=1, answered=True, engine="chatgpt")        # chatgpt, answered
            one_call(requests=3, answered=True, engine="gemini")         # gemini, rescued after two

        f = scan.log_fields()
        per_engine = {
            engine: (f[f"engine_{engine}_calls"], f.get(f"engine_{engine}_unanswered", 0),
                     f.get(f"engine_{engine}_retries", 0))
            for engine in ("perplexity", "claude", "chatgpt", "gemini")
        }
        # (requests made, calls that never got an answer, requests that were sent again)
        assert per_engine == {
            "perplexity": (4, 1, 2), "claude": (1, 1, 0), "chatgpt": (1, 0, 0), "gemini": (3, 0, 2),
        }
        assert (f["engines_calls"], f["engines_unanswered"], f["engines_retries"]) == (9, 2, 4)

    @pytest.mark.parametrize(
        "purpose",
        [Purpose.SENTIMENT, Purpose.PROMPT_GENERATION, Purpose.FIXES, Purpose.DETECTION],
        ids=lambda p: p.value,
    )
    def test_the_lines_that_are_not_engines_count_a_call_once_too(self, purpose: Purpose) -> None:
        with usage.scan_scope("scan_1") as scan:
            one_call(requests=2, answered=True, engine=None, purpose=purpose,
                     provider=usage.ANTHROPIC)
            one_call(requests=2, answered=False, engine=None, purpose=purpose,
                     provider=usage.ANTHROPIC)

        f = scan.log_fields()
        line = purpose.value
        assert (f[f"{line}_calls"], f[f"{line}_unanswered"], f[f"{line}_retries"]) == (4, 1, 2)

    def test_a_request_made_on_its_own_ends_its_call_and_one_that_was_followed_does_not(
        self,
    ) -> None:
        scan = ScanUsage("scan_1")
        scan.add(call(ok=False))
        assert scan.log_fields()["engine_claude_unanswered"] == 1

        scan.add(call(ok=False, last=False))   # a later request of its call went on from it

        f = scan.log_fields()
        assert (f["engine_claude_calls"], f["engine_claude_unanswered"]) == (2, 1)
        # The marker is the accumulator's business: the `llm.call` line does not carry it.
        assert "last" not in call(last=False).log_fields()

    def test_a_total_can_be_checked_against_its_lines(self) -> None:
        """What the runbook says to do: `_calls` is the lines, `_retries` the ones with
        `retry=True`, and `_unanswered` the ones with `ok=False` less the ones with `retry=True`."""
        with structlog.testing.capture_logs() as events, usage.scan_scope("scan_1") as scan:
            # (requests, answered): three of the six calls, the ones ending in False, got nothing.
            for requests, answered in [(1, True), (2, True), (2, False), (3, False), (3, True),
                                       (1, False)]:
                one_call(requests=requests, answered=answered)

        lines = events_named(events, "llm.call")
        f = scan.log_fields()
        no_usage = sum(1 for line in lines if not line["ok"])
        resent = sum(1 for line in lines if line["retry"])
        assert f["engine_perplexity_calls"] == len(lines) == 12
        assert f["engine_perplexity_retries"] == resent == 6
        assert f["engine_perplexity_unanswered"] == no_usage - resent == 3


class TestEveryPurposeHasItsLine:
    """A scan's total has a fixed line for each thing a scan pays for. A purpose added without
    its line would be dropped from the total by the very rule that stops counting from breaking
    a call, so a test is what stops it being added."""

    SCAN_PURPOSES = [p for p in Purpose if p not in (Purpose.ENGINE, Purpose.CLASSIFICATION)]

    def test_there_are_purposes_to_look_at(self) -> None:
        """So the tests below cannot pass for want of a purpose to check."""
        assert Purpose.SENTIMENT in self.SCAN_PURPOSES
        assert Purpose.PROMPT_GENERATION in self.SCAN_PURPOSES

    @pytest.mark.parametrize("purpose", SCAN_PURPOSES, ids=lambda p: p.value)
    def test_every_purpose_that_is_part_of_a_scan_has_a_fixed_line(self, purpose: Purpose) -> None:
        assert purpose.value in usage.FIXED_LINES

    def test_the_engines_have_their_line_and_classification_has_none(self) -> None:
        assert "engines" in usage.FIXED_LINES
        assert Purpose.CLASSIFICATION.value not in usage.FIXED_LINES

    def test_every_fixed_line_is_the_engines_or_a_purpose(self) -> None:
        assert {str(line) for line in usage.FIXED_LINES} == (
            {"engines"} | {p.value for p in self.SCAN_PURPOSES})

    @pytest.mark.parametrize("purpose", list(Purpose), ids=lambda p: p.value)
    def test_a_scan_can_add_a_request_of_every_purpose_without_failing(
        self, purpose: Purpose
    ) -> None:
        scan = ScanUsage("scan_1")
        scan.add(call(purpose=purpose, input_tokens=5))
        fields = scan.log_fields()
        if purpose is Purpose.CLASSIFICATION:
            assert all(v in (0, None) for k, v in fields.items() if k != "scan_id")
        else:
            line = "engines" if purpose is Purpose.ENGINE else purpose.value
            assert fields[f"{line}_calls"] == 1 and fields[f"{line}_input_tokens"] == 5


# ---------------------------------------------------------------------------
# the scope a scan's chain runs in
# ---------------------------------------------------------------------------


def a_call(*, tokens: int = 1, purpose: Purpose = ENGINE, engine: str | None = "claude") -> None:
    """One tracked call that read `tokens` input tokens."""
    with usage.track(usage.ANTHROPIC, purpose, model="m", engine=engine) as tracked:
        tracked.read_anthropic(SimpleNamespace(
            model="m", usage=SimpleNamespace(input_tokens=tokens, output_tokens=0)))


class TestTheScanScope:
    def test_one_total_line_is_logged_when_the_scope_ends_and_it_equals_the_calls(self) -> None:
        with structlog.testing.capture_logs() as events, usage.scan_scope("scan_42"):
            a_call(tokens=3)
            a_call(tokens=4)

        (total,) = events_named(events, "scan.usage")
        assert total["log_level"] == "info" and total["scan_id"] == "scan_42"
        per_call = events_named(events, "llm.call")
        assert [c["scan_id"] for c in per_call] == ["scan_42", "scan_42"]
        assert total["engines_calls"] == len(per_call) == 2
        assert total["engines_input_tokens"] == sum(c["input_tokens"] for c in per_call) == 7

    def test_it_is_logged_when_the_scan_ends_in_an_exception_and_the_exception_goes_on(
        self,
    ) -> None:
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(RuntimeError, match="the chain broke"),
            usage.scan_scope("scan_42"),
        ):
            a_call(tokens=3)
            raise RuntimeError("the chain broke")

        (total,) = events_named(events, "scan.usage")
        assert total["engines_input_tokens"] == 3

    async def test_it_is_logged_when_the_scan_is_cancelled(self) -> None:
        inside = asyncio.Event()

        async def chain() -> None:
            with usage.scan_scope("scan_42"):
                a_call(tokens=3)
                inside.set()
                await asyncio.sleep(3600)

        with structlog.testing.capture_logs() as events:
            task = asyncio.create_task(chain())
            await inside.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(events_named(events, "scan.usage")) == 1

    def test_a_call_after_the_scope_is_outside_it_and_nothing_leaks(self) -> None:
        with structlog.testing.capture_logs() as events:
            with usage.scan_scope("scan_42"):
                pass
            a_call(tokens=3)

        assert events_named(events, "llm.call")[0]["scan_id"] is None
        (total,) = events_named(events, "scan.usage")
        assert total["engines_calls"] == 0

    def test_a_call_with_no_scope_only_logs(self) -> None:
        with structlog.testing.capture_logs() as events:
            a_call(tokens=3)

        assert len(events_named(events, "llm.call")) == 1
        assert events_named(events, "scan.usage") == []

    async def test_tasks_started_inside_the_scope_add_to_it(self) -> None:
        """`gather` and `create_task` copy the context; the accumulator is shared by reference."""
        async def one(tokens: int) -> None:
            await asyncio.sleep(0)
            a_call(tokens=tokens)

        with structlog.testing.capture_logs() as events, usage.scan_scope("scan_42"):
            await asyncio.gather(*(one(n) for n in range(1, 6)))
            inner = asyncio.create_task(one(10))
            await inner

        (total,) = events_named(events, "scan.usage")
        assert total["engines_calls"] == 6
        assert total["engines_input_tokens"] == 1 + 2 + 3 + 4 + 5 + 10
        assert {c["scan_id"] for c in events_named(events, "llm.call")} == {"scan_42"}

    async def test_two_scans_at_once_keep_their_own_totals(self) -> None:
        async def scan(scan_id: str, tokens: int, calls: int) -> None:
            with usage.scan_scope(scan_id):
                for _ in range(calls):
                    await asyncio.sleep(0)
                    a_call(tokens=tokens)

        with structlog.testing.capture_logs() as events:
            await asyncio.gather(scan("scan_a", 1, 3), scan("scan_b", 100, 2))

        totals = {t["scan_id"]: t for t in events_named(events, "scan.usage")}
        scan_a, scan_b = totals["scan_a"], totals["scan_b"]
        assert (scan_a["engines_calls"], scan_a["engines_input_tokens"]) == (3, 3)
        assert (scan_b["engines_calls"], scan_b["engines_input_tokens"]) == (2, 200)
        for line in events_named(events, "llm.call"):
            assert line["input_tokens"] == (1 if line["scan_id"] == "scan_a" else 100)

    def test_a_classification_inside_a_scope_keeps_its_line_and_stays_out_of_the_total(
        self,
    ) -> None:
        with structlog.testing.capture_logs() as events, usage.scan_scope("scan_42"):
            a_call(tokens=9, purpose=Purpose.CLASSIFICATION, engine=None)

        (line,) = events_named(events, "llm.call")
        assert line["purpose"] == "classification"
        (total,) = events_named(events, "scan.usage")
        assert total["engines_calls"] == 0 and total["engines_input_tokens"] == 0

    def test_a_failing_total_line_never_hides_the_scans_own_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*args: Any, **kwargs: Any) -> None:
            raise OSError("the log stream is gone")

        monkeypatch.setattr(usage, "logger", SimpleNamespace(info=broken))
        with (
            pytest.raises(RuntimeError, match="the chain broke"),
            usage.scan_scope("scan_42"),
        ):
            raise RuntimeError("the chain broke")

    def test_a_failing_total_that_cannot_be_built_never_hides_the_scans_own_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(self: ScanUsage) -> dict[str, object]:
            raise RuntimeError("the total could not be built")

        monkeypatch.setattr(ScanUsage, "log_fields", broken)
        with (
            pytest.raises(RuntimeError, match="the chain broke"),
            usage.scan_scope("scan_42"),
        ):
            raise RuntimeError("the chain broke")

    def test_a_failure_recording_a_serp_search_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recording runs inside `serp.search`, whose answer is not usage's to change."""
        def broken(self: ScanUsage, **kwargs: Any) -> None:
            raise RuntimeError("the accumulator broke")

        monkeypatch.setattr(ScanUsage, "add_serp", broken)
        with usage.scan_scope("scan_42"):
            usage.record_serp_search(attempts=2, no_answer=1, answer="ok")  # does not raise

    def test_serp_attempts_outside_a_scope_are_not_recorded_anywhere(self) -> None:
        usage.record_serp_search(attempts=2, no_answer=1, answer="ok")  # nothing to add to
        with usage.scan_scope("scan_42") as scan:
            pass
        assert scan.log_fields()["serpapi_attempted"] == 0

    def test_serp_attempts_inside_a_scope_are_recorded(self) -> None:
        with usage.scan_scope("scan_42") as scan:
            usage.record_serp_search(attempts=2, no_answer=1, answer="ok")
        assert scan.log_fields()["serpapi_attempted"] == 2

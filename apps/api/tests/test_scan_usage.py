"""A scan ends with ONE line of what it used — U1, 2026-10-04.

`scan.usage` is the line a scan's cost is read from. It carries the scan's id and, for each of
the five things a scan pays for (the engines together and each engine on its own, sentiment,
prompt generation, fixes, competitor detection), how many requests were made, how many calls
never got an answer (a call is everything one prompt, one engine took to answer: a request that a
retry rescued is a retry, not an unanswered call: U5, 2026-10-06), how many requests were a
retry, the input, output and reasoning tokens, the cache tokens and the search invocations, plus
what SerpApi was asked: searches attempted, answered ok, answered with an error, and not
answered at all.

It is built from the same records the per-call `llm.call` lines are, so the one cannot
disagree with the other. These tests prove that by running the REAL chain — the real executor,
the real adapters, the real helper calls — with only the providers' transports faked, and
adding the per-call lines up themselves, independently of the code under test.

What is pinned here, each by a test that fails without it:

* a scan that succeeds ends with exactly one `scan.usage`, and every one of its fields equals
  the sum of the scan's own per-call lines, including the field set itself;
* detection is in the first scan's total and not in the next scan's, which carries the set
  forward and asks nothing;
* SerpApi's counts for a detection with one retry that answered, one query that never got an
  answer and one that answered with an error add up to the searches attempted;
* a scan that ends PARTIAL, FAILED, in an error of ours, or at its deadline ends with exactly
  one too;
* calls made outside a scan (the detect route, the fixes route) log their own lines, carry no
  scan id and have no total;
* no key any of the five providers was given, and no URL one travels in, is in anything the run
  logs. Each key is a recognisable sentinel, each provider's fake records the key it was shown
  (so the test cannot pass for want of a key in play), and the fakes echo that key back in their
  error messages, as a hostile provider might.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import anthropic
import httpx
import pytest
import structlog
from httpx import AsyncClient
from pydantic import SecretStr

from avp_api.deps import scan_executor
from avp_api.models import Scan, ScanStatus
from avp_api.services import (
    cocitation,
    engines,
    extraction,
    fix_generator,
    prompts,
    scan_runner,
    serp,
)
from avp_api.services import competitors as detection
from avp_api.services import scan_executor as executor_module
from avp_api.services.scan_executor import SCAN_DEADLINE_EXCEEDED, ScanJob, execute_scan

# Bound at import, before conftest's autouse `stub_chain_externals` replaces them for every
# test: the real search and the real co-citation calls, which these tests put back and fake
# one layer lower, at the providers' transports.
REAL_SEARCH_MANY = serp.search_many
REAL_RUN_SEED_PROMPTS = cocitation.run_seed_prompts
_REAL_HTTPX_CLIENT = httpx.AsyncClient

# The fix stub in conftest sits above the transport; opted out so fixes reach it like the rest.
pytestmark = pytest.mark.real_parse

BASE = "/api/v1"
ANSWER_SENTINEL = "ANSWERSENTINEL-3d7a"
ANSWER = (
    f"Zendesk is popular. Help Scout is simpler and well liked. Front is newer. {ANSWER_SENTINEL}"
)
PROMPT_SENTINEL = "PROMPTSENTINEL-81c4"
ERROR_SENTINEL = "ERRORSENTINEL-62be"
PROMPTS = 3
FOUR = ("claude", "chatgpt", "perplexity", "gemini")

# One recognisable key for each provider a scan talks to, by the `Settings` field that holds it.
KEYS = {
    "anthropic_api_key": "ANTHROPICKEYSENTINEL-4a91c7d2",
    "openai_api_key": "OPENAIKEYSENTINEL-b83e05f6",
    "serpapi_key": "SERPAPIKEYSENTINEL-17d6a0c9",
    "perplexity_api_key": "PERPLEXITYKEYSENTINEL-e52b8a31",
    "google_ai_api_key": "GOOGLEKEYSENTINEL-9c40d7ab",
}
# Where SerpApi's key travels, and so what its URL looks like.
SERPAPI_HOST_AND_PATH = "serpapi.com/search.json"

COUNTERS = (
    "calls", "unanswered", "retries", "input_tokens", "output_tokens", "reasoning_tokens",
    "cache_read_tokens", "cache_write_tokens", "search_invocations",
)
FIXED_LINES = ("engines", "sentiment", "prompt_generation", "fixes", "detection")


# ---------------------------------------------------------------------------
# the providers, faked at their transports
# ---------------------------------------------------------------------------


class Providers:
    """Every host a scan talks to, answered from memory, with each request recorded.

    The usage each answers with is distinct per purpose, so a total that counted the wrong
    thing would be the wrong number and not an accidentally equal one. `down` names a provider
    that refuses everything; `hangs` one that never answers; `flaky_sentiment` makes the first
    sentiment request an overloaded 529 the SDK retries; `flaky_perplexity` makes the first
    Perplexity request a 429 that names no wait, which the adapter sends again once and which
    then answers; `throttled` names a provider whose every request is that 429, so its one
    retry is refused too.
    """

    def __init__(
        self, *, down: frozenset[str] = frozenset(), hangs: frozenset[str] = frozenset(),
        flaky_sentiment: bool = False, serp_script: bool = False,
        flaky_perplexity: bool = False, throttled: frozenset[str] = frozenset(),
    ) -> None:
        self.down = down
        self.hangs = hangs
        self.flaky_sentiment = flaky_sentiment
        self.flaky_perplexity = flaky_perplexity
        self.throttled = throttled
        self.serp_script = serp_script
        self.anthropic_bodies: list[dict[str, Any]] = []
        self.serp_requests: list[str] = []
        self.unexpected: list[str] = []
        self.keys_seen: set[str] = set()
        self._serp_attempts: dict[str, int] = {}
        self._sentiment_seen = 0
        self._perplexity_seen = 0

    def _key_shown_to_us(self, request: httpx.Request) -> str:
        """The key this request carried, wherever its provider wants it, noted for the test."""
        headers = request.headers
        key = (
            headers.get("x-api-key")                                  # Anthropic
            or headers.get("x-goog-api-key")                          # Gemini
            or headers.get("authorization", "").removeprefix("Bearer ")  # OpenAI, Perplexity
            or request.url.params.get("api_key")                      # SerpApi: in the URL
            or ""
        )
        if key:
            self.keys_seen.add(key)
        return key

    # -- Anthropic ---------------------------------------------------------------------

    def anthropic(self, request: httpx.Request) -> httpx.Response | Any:
        body = json.loads(request.content)
        self.anthropic_bodies.append(body)
        key = self._key_shown_to_us(request)
        if "anthropic" in self.hangs:
            return self._never()
        if "anthropic" in self.down:
            # A refusal that echoes the key it refused: nothing may log the SDK's message.
            return httpx.Response(401, json={"type": "error", "error": {
                "type": "authentication_error", "message": f"{ERROR_SENTINEL} {key}"}})
        system = body.get("system")
        if system == extraction.SENTIMENT_SYSTEM:
            self._sentiment_seen += 1
            if self.flaky_sentiment and self._sentiment_seen == 1:
                return httpx.Response(529, headers={"retry-after-ms": "1"}, json={
                    "type": "error", "error": {"type": "overloaded_error", "message": "x"}})
            return self._structured(
                {"sentiment": "positive", "confidence": 0.9},
                model="claude-haiku-4-5-20251001", input_tokens=410, output_tokens=33)
        if system == prompts.SYSTEM_PROMPT:
            generated = [
                {"text": f"{PROMPT_SENTINEL} question number {i}",
                 "intent": ("awareness", "comparison", "bottom_funnel")[i % 3]}
                for i in range(24)
            ]
            return self._structured(
                {"prompts": generated}, model="claude-opus-5", input_tokens=600,
                output_tokens=2500, output_tokens_details={"thinking_tokens": 1800})
        if system == cocitation.SYSTEM_PROMPT:
            return self._structured(
                {"brands": [{"name": "Zendesk", "domain": "zendesk.com"},
                            {"name": "Front", "domain": "front.com"}],
                 "subject_named": False},
                model="claude-opus-5", input_tokens=300, output_tokens=140)
        if system == fix_generator.SYSTEM_PROMPT:
            content = body["messages"][0]["content"]
            keys = [
                line.split(" — ")[0].removeprefix("- ")
                for line in content.splitlines()
                if line.startswith("- gap:") or line.startswith("- audit:")
            ]
            fixes = [
                {"candidate_id": key, "title": f"Improve the site signal number {i}",
                 "detail": "Add the missing markup to the page and confirm it validates.",
                 "priority": "medium", "effort": "M",
                 "priority_reason": "It moves a weighted dimension.",
                 "effort_reason": "A template edit and a redeploy."}
                for i, key in enumerate(keys, start=1)
            ]
            return self._structured(
                {"fixes": fixes}, model="claude-opus-5", input_tokens=2200,
                output_tokens=5100, output_tokens_details={"thinking_tokens": 3000})
        if system is None and "tools" not in body:
            return httpx.Response(200, json=self._message(
                [{"type": "text", "text": ANSWER}], model="claude-opus-5",
                input_tokens=80, output_tokens=160,
                output_tokens_details={"thinking_tokens": 100}))
        self.unexpected.append(f"anthropic request with system={str(system)[:30]!r}")
        return httpx.Response(500, json={})

    @staticmethod
    def _message(content: list[dict[str, Any]], *, model: str, **usage: Any) -> dict[str, Any]:
        return {"id": "msg_test", "type": "message", "role": "assistant", "model": model,
                "content": content, "stop_reason": "end_turn", "stop_sequence": None,
                "usage": usage}

    def _structured(self, payload: dict[str, Any], *, model: str, **usage: Any) -> httpx.Response:
        return httpx.Response(200, json=self._message(
            [{"type": "text", "text": json.dumps(payload)}], model=model, **usage))

    @staticmethod
    async def _never() -> httpx.Response:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    # -- everything else -----------------------------------------------------------------

    def http(self, request: httpx.Request) -> httpx.Response | Any:
        host = request.url.host
        key = self._key_shown_to_us(request)
        if host == "api.openai.com":
            return self._engine("openai", key, httpx.Response(200, json={
                "model": "gpt-5.5-2026-04-23",
                "choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant", "content": ANSWER}}],
                "usage": {"prompt_tokens": 111, "completion_tokens": 222,
                          "completion_tokens_details": {"reasoning_tokens": 77}},
            }))
        if host == "api.perplexity.ai":
            self._perplexity_seen += 1
            if "perplexity" in self.throttled or (
                self.flaky_perplexity and self._perplexity_seen == 1
            ):
                # A 429 that names a wait of nothing, so the adapter's one retry is taken at once.
                # Like every refusal here it echoes the key it was shown: nothing may log it.
                return httpx.Response(429, headers={"Retry-After": "0"}, json={
                    "error": {"message": f"{ERROR_SENTINEL} {key}"}})
            return self._engine("perplexity", key, httpx.Response(200, json={
                "status": "completed", "model": "perplexity/sonar",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": ANSWER, "annotations": []}]}],
                "usage": {"input_tokens": 1758, "output_tokens": 432,
                          "tool_calls_details": {"search_web": {"invocation": 2}}},
            }))
        if host == "generativelanguage.googleapis.com":
            return self._engine("gemini", key, httpx.Response(200, json={
                "candidates": [{"finishReason": "STOP", "content": {
                    "role": "model", "parts": [{"text": ANSWER}]}}],
                "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 30,
                                  "thoughtsTokenCount": 5},
            }))
        if host == "serpapi.com":
            return self._serp(request)
        self.unexpected.append(f"request to {host}")
        return httpx.Response(500, json={})

    def _engine(self, name: str, key: str, ok: httpx.Response) -> httpx.Response | Any:
        if name in self.hangs:
            return self._never()
        if name in self.down:
            return httpx.Response(401, json={"error": {"message": f"{ERROR_SENTINEL} {key}"}})
        return ok

    def _serp(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        self.serp_requests.append(query)
        attempt = self._serp_attempts[query] = self._serp_attempts.get(query, 0) + 1
        results = {"organic_results": [
            {"position": 1, "link": "https://www.zendesk.com/a", "title": "t"},
            {"position": 2, "link": "https://www.front.com/b", "title": "t"},
        ]}
        if not self.serp_script:
            return httpx.Response(200, json=results)
        # One query retried and then answered, one that never answers, one answered with an error.
        # (SerpApi and httpx both put the request URL, key and all, in places: an error body and
        # an exception's text. Here they do, so that a line which logged either would show it.)
        if query.endswith("alternatives"):
            if attempt == 1:
                raise httpx.ReadTimeout(f"no answer from {request.url}", request=request)
            return httpx.Response(200, json=results)
        if query.endswith("competitors"):
            raise httpx.ReadTimeout(f"no answer from {request.url}", request=request)
        return httpx.Response(200, json={
            "error": f"Google hasn't returned any results. ({request.url})"})

    # -- wiring --------------------------------------------------------------------------

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Providers:
        real_anthropic = anthropic.AsyncAnthropic
        handler = self.http

        # A SUBCLASS of the real client, not a function: the Anthropic SDK checks
        # `isinstance(http_client, httpx.AsyncClient)` against whatever that name is, and these
        # tests fake both the SDK's transport and every other provider's.
        class Routed(_REAL_HTTPX_CLIENT):  # type: ignore[valid-type, misc]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("transport", httpx.MockTransport(handler))
                super().__init__(*args, **kwargs)

        def anthropic_factory(**kwargs: Any) -> anthropic.AsyncAnthropic:
            return real_anthropic(
                **kwargs,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.anthropic)),
            )

        monkeypatch.setattr(httpx, "AsyncClient", Routed)
        monkeypatch.setattr(anthropic, "AsyncAnthropic", anthropic_factory)
        return self


@pytest.fixture
def four_engines(settings, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """A key behind every default engine, so a scan runs all four, as production does.

    Every provider's key is a recognisable sentinel, not only the two that are not set by
    default, so that its absence from the logs can be asserted for all five.
    """
    for field, key in KEYS.items():
        monkeypatch.setattr(settings, field, SecretStr(key))
    # Perplexity's start pacer and Gemini's gate are per process; keep each test its own.
    monkeypatch.setattr(engines, "_GATES", {})
    monkeypatch.setattr(engines, "_PACERS", {})
    # On the CLASS, which monkeypatch restores exactly: on the registered instance it would put
    # the class's own value back as an instance attribute and shadow the class from then on.
    monkeypatch.setattr(engines.PerplexityAdapter, "min_start_interval", None)
    # The suite's stubs for the two detection signals stand above the providers; the real ones
    # go back, so detection's SerpApi searches and co-citation calls reach the transports.
    monkeypatch.setattr(detection.serp_service, "search_many", REAL_SEARCH_MANY)
    monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", REAL_RUN_SEED_PROMPTS)
    monkeypatch.setattr(serp, "RETRY_PAUSE_S", 0.01)


# ---------------------------------------------------------------------------
# driving a scan, and reading its lines
# ---------------------------------------------------------------------------


class Account:
    """A signed-in agency with one client, whose scans are queued and run by the test."""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self.jobs: list[ScanJob] = []
        self.client_id = ""

    async def open(self) -> Account:
        resp = await self.client.post(
            f"{BASE}/auth/sign-up",
            json={"agencyName": "Usage Test Agency", "fullName": "Op",
                  "email": "usage@test.example", "password": "correct-horse-battery-staple"},
        )
        assert resp.status_code == 201, resp.text
        made = await self.client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
        assert made.status_code == 201, made.text
        self.client_id = made.json()["id"]

        jobs = self.jobs

        class Deferred:
            async def submit(self, job: ScanJob, *, settings: Any) -> None:  # noqa: ARG002
                jobs.append(job)

        self.client._transport.app.dependency_overrides[scan_executor] = (  # noqa: SLF001
            lambda: Deferred()
        )
        return self

    async def queue(self) -> ScanJob:
        queued = await self.client.post(
            f"{BASE}/clients/{self.client_id}/scans", json={"promptLimit": PROMPTS})
        assert queued.status_code == 202, queued.text
        return self.jobs[-1]


def named(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == name]


def facts(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k not in ("event", "log_level")}


def expected_total(
    per_call: list[dict[str, Any]], scan_id: str, serp_counts: dict[str, int]
) -> dict[str, Any]:
    """The `scan.usage` fields those per-call lines add up to — worked out here, on its own."""
    groups: dict[str, list[dict[str, Any]]] = {line: [] for line in FIXED_LINES}
    for call in per_call:
        if call["purpose"] == "engine":
            groups["engines"].append(call)
            groups.setdefault(f"engine_{call['engine']}", []).append(call)
        elif call["purpose"] in groups:
            groups[call["purpose"]].append(call)
        else:
            assert call["purpose"] == "classification", call["purpose"]
    out: dict[str, Any] = {"scan_id": scan_id}
    for prefix, calls in groups.items():
        # A call's requests but its first are `retry=True`, and every request but its last got no
        # usage back, so the lines with no usage, less the lines that were sent again, are the
        # calls whose last request got none: what `unanswered` counts (U5, 2026-10-06; it counted
        # every line with no usage, a request a retry rescued included, and the production scan
        # that answered every prompt said one had gone unanswered).
        no_usage = sum(1 for c in calls if not c["ok"])
        resent = sum(1 for c in calls if c["retry"])
        counted = {
            "calls": len(calls),
            "unanswered": no_usage - resent,
            "retries": resent,
            **{counter: sum(c[counter] for c in calls) for counter in COUNTERS[3:]},
        }
        # The line's own rule: a counter that is zero is left out, except the three every line has.
        for counter, value in counted.items():
            if value or counter in ("calls", "input_tokens", "output_tokens"):
                out[f"{prefix}_{counter}"] = value
        # Only a request that was answered names the model that served it.
        models = sorted({c["model"] for c in calls if c["model"] and c["ok"]})
        if models:
            out[f"{prefix}_model"] = "+".join(models)
    out.update({f"serpapi_{k}": v for k, v in serp_counts.items()})
    # What SerpApi may have billed: the answered searches and the ones nobody answered. The field
    # is left out when it is zero, as every other counter that is.
    may_have_been_billed = serp_counts["ok"] + serp_counts["no_answer"]
    if may_have_been_billed:
        out["serpapi_billed_or_unknown"] = may_have_been_billed
    return out


def assert_the_total_is_the_sum_of_the_lines(
    events: list[dict[str, Any]], scan_id: str, serp_counts: dict[str, int]
) -> dict[str, Any]:
    (total,) = named(events, "scan.usage")
    assert total["log_level"] == "info"
    per_call = named(events, "llm.call")
    assert per_call, "the scan made no paid call, so there is nothing to add up"
    assert {c["scan_id"] for c in per_call} == {scan_id}
    # The WHOLE line, field for field: the values, and which fields there are at all.
    assert facts(total) == expected_total(per_call, scan_id, serp_counts)
    return total


NO_SERP = {"attempted": 0, "ok": 0, "error": 0, "no_answer": 0}


def assert_no_key_or_url_in_any_line(events: list[dict[str, Any]]) -> None:
    """Nothing the run logged, as the console prints it or as JSON, holds a key or a SerpApi URL.

    EVERYTHING the run logged, not only the two lines this change adds.
    """
    render = structlog.dev.ConsoleRenderer(colors=False)
    printed = "\n".join(
        render(None, "info", {**e, "level": e.get("log_level", "info")}) for e in events)
    shown = printed + json.dumps(events, default=str)
    for name, key in KEYS.items():
        assert key not in shown, f"the {name} reached the log"
    assert SERPAPI_HOST_AND_PATH not in shown, "SerpApi's URL reached the log"
    assert "api_key" not in shown, "a key parameter's name reached the log"


async def run(account: Account, settings: Any) -> tuple[ScanJob, list[dict[str, Any]]]:
    job = await account.queue()
    with structlog.testing.capture_logs() as events:
        await execute_scan(job, settings=settings)
    assert_no_key_or_url_in_any_line(events)
    return job, events


async def status_of(session: Any, scan_id: str) -> Scan:
    scan = await session.get(Scan, scan_id)
    assert scan is not None
    await session.refresh(scan)
    return scan


# ---------------------------------------------------------------------------
# a scan that succeeds
# ---------------------------------------------------------------------------


class TestAScanThatSucceeds:
    async def test_it_ends_with_one_total_that_is_the_sum_of_its_own_calls(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        providers = Providers(flaky_sentiment=True, serp_script=True).install(monkeypatch)
        account = await Account(client).open()

        job, events = await run(account, settings)

        scan = await status_of(session, job.scan_id)
        assert scan.status is ScanStatus.SUCCEEDED, (scan.status, scan.error_code)
        assert providers.unexpected == []
        total = assert_the_total_is_the_sum_of_the_lines(
            events, job.scan_id,
            # Three queries: one that timed out and was asked again and answered (2 requests,
            # 1 with no answer, 1 ok), one that never got an answer (2 requests, no answer) and
            # one answered with an error (1 request): 5 asked, 1 ok, 1 error, 3 with no answer.
            {"attempted": 5, "ok": 1, "error": 1, "no_answer": 3},
        )

        # The scan the totals were checked against is the scan this test thinks it is:
        assert tuple(e.value for e in job.engines) == FOUR
        for name in FOUR:
            assert total[f"engine_{name}_calls"] == PROMPTS, name
        assert total["engines_calls"] == PROMPTS * len(FOUR)
        # Sentiment is asked of every answer, and one request was an overloaded 529 first, which
        # the SDK sent again and which was answered: a retry, not a call that went unanswered.
        assert total["sentiment_calls"] == PROMPTS * len(FOUR) + 1
        assert total["sentiment_retries"] == 1
        assert "sentiment_unanswered" not in total
        assert total["sentiment_model"] == "claude-haiku-4-5-20251001"
        assert total["prompt_generation_calls"] == 1
        assert total["prompt_generation_reasoning_tokens"] == 1800
        assert total["fixes_calls"] == 1
        assert total["fixes_output_tokens"] == 5100
        # Two seed prompts, and the model that served them is the one the response named.
        assert total["detection_calls"] == 2
        assert total["detection_model"] == "claude-opus-5"
        # Reasoning is shown on its own and is INSIDE the output count.
        assert total["engine_gemini_output_tokens"] == PROMPTS * 35
        assert total["engine_gemini_reasoning_tokens"] == PROMPTS * 5
        assert total["engine_perplexity_search_invocations"] == PROMPTS * 2
        # What SerpApi may have billed: the answered one and the three nobody answered.
        assert total["serpapi_billed_or_unknown"] == (
            total["serpapi_ok"] + total["serpapi_no_answer"]) == 4
        # Nothing but counts in the whole scan's log, in either kind of line.
        blob = json.dumps(named(events, "llm.call") + named(events, "scan.usage"), default=str)
        for sentinel in (ANSWER_SENTINEL, PROMPT_SENTINEL, ERROR_SENTINEL, "Help Scout",
                         "helpscout.com", "Zendesk"):
            assert sentinel not in blob, sentinel
        # EVERY key the run used was a sentinel and was really shown to its provider: SerpApi's
        # (in the URL), Anthropic's, OpenAI's, Google's and Perplexity's. None is in anything the
        # run logged. (`run` has already checked it; this is where the keys are proved to be in
        # play, so the check cannot pass for want of one.)
        assert providers.keys_seen == set(KEYS.values())
        assert_no_key_or_url_in_any_line(events)

    async def test_the_total_is_one_line_of_flat_counts_that_parses_back(
        self, client: AsyncClient, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers().install(monkeypatch)
        account = await Account(client).open()

        job, events = await run(account, settings)

        (total,) = named(events, "scan.usage")
        for key, value in facts(total).items():
            assert isinstance(value, str | int | None), (key, value)
        rendered = structlog.dev.ConsoleRenderer(colors=False)(None, "info", dict(total))
        assert "\n" not in rendered.strip()
        parsed = dict(part.split("=", 1) for part in rendered.split() if "=" in part)
        assert parsed["scan_id"] == job.scan_id
        assert int(parsed["engines_calls"]) == total["engines_calls"]
        assert parsed["sentiment_model"] == "claude-haiku-4-5-20251001"

    async def test_the_next_scan_carries_the_set_forward_and_detection_is_not_in_its_total(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        providers = Providers().install(monkeypatch)
        account = await Account(client).open()
        first_job, first = await run(account, settings)
        first_total = assert_the_total_is_the_sum_of_the_lines(
            first, first_job.scan_id, {"attempted": 3, "ok": 3, "error": 0, "no_answer": 0})
        assert first_total["detection_calls"] == 2
        asked_so_far = len(providers.serp_requests)
        assert asked_so_far == 3

        second_job, second = await run(account, settings)

        assert second_job.scan_id != first_job.scan_id
        assert (await status_of(session, second_job.scan_id)).status is ScanStatus.SUCCEEDED
        # Nothing was asked of SerpApi for it ...
        assert len(providers.serp_requests) == asked_so_far
        total = assert_the_total_is_the_sum_of_the_lines(second, second_job.scan_id, NO_SERP)
        # ... and the model was never asked to find rivals, so detection is a line of zeros.
        assert total["detection_calls"] == 0
        assert "detection_model" not in total
        # Nothing was asked of SerpApi, so nothing may have been billed, and the field is absent.
        assert total["serpapi_attempted"] == 0
        assert "serpapi_billed_or_unknown" not in total
        assert not [c for c in named(second, "llm.call") if c["purpose"] == "detection"]
        # Everything else a scan pays for is in it.
        assert total["engines_calls"] == PROMPTS * len(FOUR)
        assert total["prompt_generation_calls"] == 1
        assert total["fixes_calls"] == 1
        # One total each: the first scan's was not logged again by the second.
        assert len(named(first, "scan.usage")) == len(named(second, "scan.usage")) == 1


# ---------------------------------------------------------------------------
# a request that a retry rescued is a retry, not a prompt that went unanswered
# ---------------------------------------------------------------------------


class TestARetryIsNotAnUnansweredPrompt:
    """U5, 2026-10-06. The production fact: one Perplexity request was refused with a 429, sent
    again and answered, every result of the scan was there, and its total said
    `engine_perplexity_unanswered=1`. `_calls` and `_retries` count requests; `_unanswered` counts
    the calls (a prompt asked of an engine) that never got an answer."""

    async def test_a_429_that_the_retry_answered_leaves_no_prompt_unanswered(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers(flaky_perplexity=True).install(monkeypatch)
        account = await Account(client).open()

        job, events = await run(account, settings)

        scan = await status_of(session, job.scan_id)
        assert scan.status is ScanStatus.SUCCEEDED, (scan.status, scan.error_code)
        total = assert_the_total_is_the_sum_of_the_lines(
            events, job.scan_id, {"attempted": 3, "ok": 3, "error": 0, "no_answer": 0})
        # Every engine answered every prompt, and one Perplexity request was made twice.
        assert total["engine_perplexity_calls"] == PROMPTS + 1
        assert total["engine_perplexity_retries"] == 1
        assert "engine_perplexity_unanswered" not in total
        assert total["engines_calls"] == PROMPTS * len(FOUR) + 1
        assert total["engines_retries"] == 1
        assert "engines_unanswered" not in total
        # The request that was refused is still a line of its own, with no usage and its reason.
        refused = [
            c for c in named(events, "llm.call") if not c["ok"] and c["engine"] == "perplexity"]
        assert [(c["error"], c["retry"], c["input_tokens"]) for c in refused] == [
            ("http_429", False, 0)]
        # How a reader gets the prompts from the line: requests less retries.
        assert total["engine_perplexity_calls"] - total["engine_perplexity_retries"] == PROMPTS

    async def test_a_429_that_the_retry_got_too_is_one_unanswered_prompt_for_each_prompt(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers(throttled=frozenset({"perplexity"})).install(monkeypatch)
        account = await Account(client).open()

        job, events = await run(account, settings)

        assert (await status_of(session, job.scan_id)).status is ScanStatus.PARTIAL
        total = assert_the_total_is_the_sum_of_the_lines(
            events, job.scan_id, {"attempted": 3, "ok": 3, "error": 0, "no_answer": 0})
        # Each prompt made two requests and neither was answered: two requests, one retry and ONE
        # unanswered call for each prompt, not one for each request.
        assert total["engine_perplexity_calls"] == 2 * PROMPTS
        assert total["engine_perplexity_retries"] == PROMPTS
        assert total["engine_perplexity_unanswered"] == PROMPTS
        assert total["engine_perplexity_input_tokens"] == 0
        # The other engines are counted on their own and none of them went unanswered.
        for name in ("claude", "chatgpt", "gemini"):
            assert total[f"engine_{name}_calls"] == PROMPTS, name
            assert f"engine_{name}_unanswered" not in total, name
            assert f"engine_{name}_retries" not in total, name
        assert total["engines_unanswered"] == PROMPTS
        assert total["engines_retries"] == PROMPTS


# ---------------------------------------------------------------------------
# a scan that does not succeed still ends with one
# ---------------------------------------------------------------------------


class TestAScanThatDoesNotSucceedStillEndsWithOne:
    async def test_a_scan_with_one_engine_down_is_partial_and_its_total_says_so(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers(down=frozenset({"perplexity"})).install(monkeypatch)
        account = await Account(client).open()

        job, events = await run(account, settings)

        assert (await status_of(session, job.scan_id)).status is ScanStatus.PARTIAL
        total = assert_the_total_is_the_sum_of_the_lines(
            events, job.scan_id, {"attempted": 3, "ok": 3, "error": 0, "no_answer": 0})
        assert (total["engine_perplexity_calls"], total["engine_perplexity_unanswered"]) == (
            PROMPTS, PROMPTS)
        assert total["engine_perplexity_input_tokens"] == 0
        assert "engine_claude_unanswered" not in total
        assert total["engines_unanswered"] == PROMPTS

    async def test_a_scan_where_every_provider_refuses_is_failed_and_its_total_says_so(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers(down=frozenset({"anthropic", "openai", "perplexity", "gemini"})).install(
            monkeypatch)
        account = await Account(client).open()

        job, events = await run(account, settings)

        assert (await status_of(session, job.scan_id)).status is ScanStatus.FAILED
        total = assert_the_total_is_the_sum_of_the_lines(
            events, job.scan_id, {"attempted": 3, "ok": 3, "error": 0, "no_answer": 0})
        assert total["engines_calls"] == total["engines_unanswered"] == PROMPTS * len(FOUR)
        assert total["engines_input_tokens"] == total["engines_output_tokens"] == 0
        # A request that got no answer names the model that was ASKED for in its own line, so the
        # line says what was being called, but a scan's total names only models that served one:
        # there are none here, and a total must not call an alias a second model.
        assert {c["model"] for c in named(events, "llm.call") if c["engine"] == "claude"} == {
            engines.ANSWER_MODEL}
        assert "engine_claude_model" not in total and "engines_model" not in total
        assert {c["error"] for c in named(events, "llm.call")} == {"http_401"}

    async def test_a_scan_that_ends_in_an_error_of_ours_has_its_total_too(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers().install(monkeypatch)

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError(ERROR_SENTINEL)

        # After every engine call has been made and paid for.
        monkeypatch.setattr(scan_runner, "persist_result", boom)
        account = await Account(client).open()

        job, events = await run(account, settings)

        scan = await status_of(session, job.scan_id)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == executor_module.EXECUTION_FAILED
        total = assert_the_total_is_the_sum_of_the_lines(
            events, job.scan_id, {"attempted": 3, "ok": 3, "error": 0, "no_answer": 0})
        assert total["engines_calls"] == PROMPTS * len(FOUR)
        assert total["fixes_calls"] == 0
        assert ERROR_SENTINEL not in json.dumps(named(events, "scan.usage"), default=str)

    async def test_a_scan_that_hits_its_deadline_has_its_total_with_the_calls_it_cut_short(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers(hangs=frozenset({"gemini"})).install(monkeypatch)
        # The chain's own work before the engines (a claim, detection, a prompt set) takes about a
        # second here; the hanging provider is what the deadline is for. Generous, so that a slow
        # machine reaches the engines and fails on what it asserts and not on arriving late.
        monkeypatch.setattr(executor_module, "MAX_SCAN_DURATION", timedelta(seconds=5))
        account = await Account(client).open()

        job, events = await run(account, settings)

        scan = await status_of(session, job.scan_id)
        assert scan.error_code == SCAN_DEADLINE_EXCEEDED
        total = assert_the_total_is_the_sum_of_the_lines(
            events, job.scan_id, {"attempted": 3, "ok": 3, "error": 0, "no_answer": 0})
        # The hanging provider's calls were cut off and are logged as that, once each ...
        assert total["engine_gemini_calls"] == total["engine_gemini_unanswered"] == PROMPTS
        gemini = [c for c in named(events, "llm.call") if c["engine"] == "gemini"]
        assert {c["error"] for c in gemini} == {"CancelledError"}
        # ... after the answers the others had already given.
        assert "engine_claude_unanswered" not in total
        assert total["engine_claude_input_tokens"] == PROMPTS * 80

    async def test_a_duplicate_submission_runs_nothing_and_makes_no_total(
        self, client: AsyncClient, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A scan already finished is not run again, so nothing is spent and nothing is totalled."""
        Providers().install(monkeypatch)
        account = await Account(client).open()
        job = await account.queue()
        await execute_scan(job, settings=settings)  # the real run: claims it and finishes it

        with structlog.testing.capture_logs() as events:
            await execute_scan(job, settings=settings)  # a duplicate submission

        assert named(events, "scan.usage") == []
        assert named(events, "llm.call") == []


# ---------------------------------------------------------------------------
# calls that are not part of a scan
# ---------------------------------------------------------------------------


class TestCallsOutsideAScanOnlyLog:
    async def test_the_detect_route_logs_its_calls_with_no_scan_and_no_total(
        self, client: AsyncClient, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers(serp_script=True).install(monkeypatch)
        account = await Account(client).open()

        with structlog.testing.capture_logs() as events:
            resp = await client.post(f"{BASE}/clients/{account.client_id}/competitors/detect")

        assert resp.status_code == 201, resp.text
        assert_no_key_or_url_in_any_line(events)
        calls = named(events, "llm.call")
        assert [c["purpose"] for c in calls] == ["detection", "detection"]
        assert {c["scan_id"] for c in calls} == {None}
        assert all(c["ok"] and c["input_tokens"] == 300 for c in calls)
        assert named(events, "scan.usage") == []

    async def test_the_fixes_route_logs_its_call_with_no_scan_and_no_total(
        self, client: AsyncClient, session: Any, settings: Any, four_engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Providers().install(monkeypatch)
        account = await Account(client).open()
        job, _ = await run(account, settings)
        assert (await status_of(session, job.scan_id)).status is ScanStatus.SUCCEEDED

        with structlog.testing.capture_logs() as events:
            resp = await client.post(f"{BASE}/scans/{job.scan_id}/fixes")

        assert resp.status_code == 201, resp.text
        assert_no_key_or_url_in_any_line(events)
        (call,) = named(events, "llm.call")
        assert (call["purpose"], call["scan_id"], call["output_tokens"]) == ("fixes", None, 5100)
        assert named(events, "scan.usage") == []

"""F5 part A: the prompt fallback never writes filler, and the counts are honest.

Founder, 2026-10-09: RMIT University's second scan had about half its questions from the
templates ("who are the best providers of university", "how much does university cost"). The
templates must be true sentences in a stated market, or the prompt phase fails visibly. One
over-long generated question must not sink the set. The generator/template split is logged and
stored. Every model call here is a stub; none reaches a provider.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
import structlog
from httpx import AsyncClient

from avp_api.models import Client, PromptSet, Scan, ScanStatus
from avp_api.models.client import ClassificationStatus
from avp_api.models.prompt import PromptIntent
from avp_api.services import prompts, scan_allowance, scan_runner, serp
from avp_api.services import scan_executor as executor_module
from avp_api.services.scan_executor import ScanJob, execute_scan

BASE = "/api/v1"

AWARE = PromptIntent.AWARENESS
COMPARE = PromptIntent.COMPARISON
BOTTOM = PromptIntent.BOTTOM_FUNNEL


def sentences(industry: str | None, domain: str, brand: str | None = "Acme") -> list[str]:
    return [
        p.text
        for p in prompts.fallback_prompts(brand_name=brand, domain=domain, industry=industry)
    ]


# --- the templates ------------------------------------------------------------------------------


def expected(noun: str, market: str, brand: str) -> list[str]:
    return [
        f"what is the best {noun} in {market}",
        f"which {noun} is best in {market}",
        f"how do i choose the right {noun} in {market}",
        f"what matters most when choosing the right {noun} in {market}",
        f"what are the best alternatives to {brand} in {market}",
        f"how does {brand} compare with other {noun} options in {market}",
        f"is {brand} a good choice in {market}",
        f"how much does {brand} cost",
        f"what should i know before choosing {brand}",
    ]


class TestTheTemplatesAreTrueSentences:
    def test_university_in_australia_is_the_founders_regression_case(self) -> None:
        got = sentences("university", "rmit.edu.au", "RMIT University")
        assert got == [
            "what is the best university in australia",
            "which university is best in australia",
            "how do i choose the right university in australia",
            "what matters most when choosing the right university in australia",
            "what are the best alternatives to RMIT University in australia",
            "how does RMIT University compare with other university options in australia",
            "is RMIT University a good choice in australia",
            "how much does RMIT University cost",
            "what should i know before choosing RMIT University",
        ]
        joined = " | ".join(got)
        for filler in ("providers of", "companies", "university cost", "top rated options"):
            assert filler not in joined
        # Every sentence that is not about the brand's own price names the market.
        for text in got:
            assert "australia" in text or text.endswith("RMIT University cost") or (
                "before choosing RMIT University" in text
            )

    def test_the_intents_are_four_awareness_three_comparison_two_bottom_funnel(self) -> None:
        got = prompts.fallback_prompts(brand_name="Acme", domain="acme.com.au", industry="dentist")
        assert [p.intent for p in got] == [AWARE] * 4 + [COMPARE] * 3 + [BOTTOM] * 2

    def test_a_person_noun_reads_as_whole_sentences(self) -> None:
        assert sentences("dentist", "northaven.co.nz", "Northaven Dental") == [
            "what is the best dentist in new zealand",
            "which dentist is best in new zealand",
            "how do i choose the right dentist in new zealand",
            "what matters most when choosing the right dentist in new zealand",
            "what are the best alternatives to Northaven Dental in new zealand",
            "how does Northaven Dental compare with other dentist options in new zealand",
            "is Northaven Dental a good choice in new zealand",
            "how much does Northaven Dental cost",
            "what should i know before choosing Northaven Dental",
        ]

    def test_a_mass_noun_reads_as_whole_sentences(self) -> None:
        assert sentences("Customer Service Software", "helpscout.co.uk", "Help Scout") == [
            "what is the best customer service software in the uk",
            "which customer service software is best in the uk",
            "how do i choose the right customer service software in the uk",
            "what matters most when choosing the right customer service software in the uk",
            "what are the best alternatives to Help Scout in the uk",
            "how does Help Scout compare with other customer service software options in the uk",
            "is Help Scout a good choice in the uk",
            "how much does Help Scout cost",
            "what should i know before choosing Help Scout",
        ]

    def test_the_brand_falls_back_to_the_domain(self) -> None:
        got = sentences("dentist", "northaven.com.au", None)
        assert got == expected("dentist", "australia", "northaven.com.au")

    @pytest.mark.parametrize(
        ("tld", "market"),
        [
            ("au", "australia"), ("nz", "new zealand"), ("uk", "the uk"), ("ie", "ireland"),
            ("ca", "canada"), ("us", "the us"), ("za", "south africa"), ("in", "india"),
            ("sg", "singapore"), ("my", "malaysia"), ("ph", "the philippines"),
            ("hk", "hong kong"), ("id", "indonesia"), ("jp", "japan"),
        ],
    )
    def test_every_country_ending_detection_knows_has_a_market_phrase(
        self, tld: str, market: str
    ) -> None:
        assert sentences("plumber", f"acme.{tld}") == expected("plumber", market, "Acme")

    def test_the_market_list_is_detections_list(self) -> None:
        assert set(prompts.MARKET_BY_TLD) == set(serp.SEARCH_COUNTRY_BY_TLD)

    @pytest.mark.parametrize(
        "domain", ["rmit.com", "zendesk.com", "acme.org", "acme.io", "rmit.edu.vn", "acme", ""]
    )
    def test_a_domain_that_names_no_country_has_no_market_and_so_no_templates(
        self, domain: str
    ) -> None:
        assert sentences("university", domain) == []

    @pytest.mark.parametrize(
        "industry",
        [
            None, "", "   ", "other", "Unknown", "general", "business", "services", "Company",
            "n/a", "one two three four five", "b2b saas 2.0", "software (crm)",
        ],
    )
    def test_a_missing_placeholder_or_unreadable_label_has_no_templates(
        self, industry: str | None
    ) -> None:
        assert sentences(industry, "acme.com.au") == []

    @pytest.mark.parametrize(
        ("label", "noun"),
        [
            ("  University ", "university"),
            ("Arts & Crafts", "arts & crafts"),
            ("e-commerce platform", "e-commerce platform"),
            ("Customer   Service  Software", "customer service software"),
            ("a b c d", "a b c d"),
        ],
    )
    def test_a_label_of_one_to_four_words_is_normalised_and_accepted(
        self, label: str, noun: str
    ) -> None:
        assert sentences(label, "acme.sg")[0] == f"what is the best {noun} in singapore"

    def test_a_suffix_that_merely_ends_in_a_country_code_is_read_from_the_last_label(self) -> None:
        assert sentences("dentist", "ACME.CO.UK.")[0] == "what is the best dentist in the uk"
        assert sentences("dentist", "au.example.com") == []


# --- the generator, stubbed ---------------------------------------------------------------------


def questions(n: int, *, prefix: str = "q") -> list[dict]:
    """n distinct generated questions, in the 9/7/4 mix so none is trimmed by the quotas."""
    mix = [AWARE] * 9 + [COMPARE] * 7 + [BOTTOM] * 4
    return [
        {"text": f"{prefix} number {i} about choosing a provider", "intent": mix[i % 20].value}
        for i in range(n)
    ]


def stub_generator(monkeypatch: pytest.MonkeyPatch, payload: list[dict] | Exception) -> list[dict]:
    """Replace the one model call. Records each request; never reaches a provider."""
    calls: list[dict] = []

    async def parse(self, **kwargs):  # noqa: ANN001, ANN003, ARG001
        calls.append(kwargs)
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=prompts.GeneratedPromptSet.model_validate({"prompts": payload}),
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(anthropic.resources.messages.AsyncMessages, "parse", parse)
    return calls


async def generate(settings, *, industry="university", domain="rmit.edu.au"):  # noqa: ANN001, ANN201
    return await prompts.generate_prompts(
        brand_name="RMIT University", domain=domain, industry=industry, niche=None,
        settings=settings,
    )


class TestGenerateDecidesWhatIsScanned:
    async def test_twenty_from_the_generator_use_no_template(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        stub_generator(monkeypatch, questions(20))
        result = await generate(settings)
        assert (result.from_generator, result.from_fallback, result.dropped) == (20, 0, 0)
        assert len(result.prompts) == 20
        assert result.generated_by == prompts.GENERATOR_MODEL
        assert result.unavailable is None

    async def test_seventeen_are_topped_up_with_three_templates(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        stub_generator(monkeypatch, questions(17))
        result = await generate(settings)
        assert (result.from_generator, result.from_fallback) == (17, 3)
        assert len(result.prompts) == 20
        templates = sentences("university", "rmit.edu.au", "RMIT University")
        assert [p.text for p in result.prompts[17:]] == templates[:3]
        assert result.generated_by == f"{prompts.GENERATOR_MODEL}+fallback"

    async def test_seventeen_with_no_market_stay_seventeen(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        stub_generator(monkeypatch, questions(17))
        result = await generate(settings, domain="rmit.com")
        assert (result.from_generator, result.from_fallback) == (17, 0)
        assert len(result.prompts) == 17
        assert result.generated_by == prompts.GENERATOR_MODEL
        assert result.unavailable is None

    async def test_exactly_fifteen_is_enough(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        stub_generator(monkeypatch, questions(prompts.MIN_GENERATED))
        result = await generate(settings, domain="rmit.com")
        assert result.unavailable is None and len(result.prompts) == 15

    async def test_ten_is_not_enough_and_no_template_set_is_made(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        stub_generator(monkeypatch, questions(10))
        result = await generate(settings)
        assert result.unavailable == "too_few_questions"
        assert result.prompts == []

    @pytest.mark.parametrize(
        ("failure", "reason"),
        [
            (TimeoutError(), "generation_failed"),
            (
                anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
                "generation_failed",
            ),
        ],
    )
    async def test_a_failed_call_is_unavailable_not_a_template_set(
        self, settings, monkeypatch, failure, reason  # noqa: ANN001
    ) -> None:
        stub_generator(monkeypatch, failure)
        result = await generate(settings)
        assert result.unavailable == reason
        assert result.prompts == []
        assert (result.from_generator, result.from_fallback) == (0, 0)

    async def test_one_overlong_question_among_twenty_costs_only_itself(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        payload = questions(20)
        payload[3]["text"] = "x" * (prompts.MAX_PROMPT_CHARS + 1)
        stub_generator(monkeypatch, payload)
        # No market in the domain, so no templates: the 19 are the whole set.
        result = await generate(settings, domain="rmit.com")
        assert result.unavailable is None
        assert (result.from_generator, result.from_fallback, result.dropped) == (19, 0, 1)
        assert len(result.prompts) == 19
        assert all(len(p.text) <= prompts.MAX_PROMPT_CHARS for p in result.prompts)
        assert result.generated_by == prompts.GENERATOR_MODEL

    async def test_a_blank_question_is_dropped_and_counted(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        payload = questions(21)
        payload[0]["text"] = "   "
        stub_generator(monkeypatch, payload)
        result = await generate(settings)
        assert result.dropped == 1 and result.from_generator == 20

    async def test_the_schema_the_model_is_given_carries_no_length_limit(self) -> None:
        schema = prompts.GeneratedPromptSet.model_json_schema()
        assert '"maxLength"' not in json.dumps(schema)


# --- build_prompt_set: the log lines and the stored counts --------------------------------------


async def _client_row(client: AsyncClient, session, *, domain: str, industry: str):  # noqa: ANN001, ANN202
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "F5 Agency", "fullName": "Op", "email": "f5@test.example",
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text
    made = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert made.status_code == 201, made.text
    row = await session.get(Client, made.json()["id"])
    row.industry = industry
    row.classification_status = ClassificationStatus.CLASSIFIED
    row.brand_name = "RMIT University"
    await session.commit()
    return row


async def _queued_scan(session, row: Client) -> Scan:  # noqa: ANN001
    from avp_api import ids

    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=row.id, agency_id=row.agency_id,
        status=ScanStatus.RUNNING,
    )
    session.add(scan)
    await session.commit()
    return scan


class TestBuildPromptSetCountsAndLogs:
    async def test_a_generator_only_set_logs_generated_and_stores_both_counts(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await _client_row(client, session, domain="rmit.edu.au", industry="university")
        scan = await _queued_scan(session, row)
        stub_generator(monkeypatch, questions(20))

        with structlog.testing.capture_logs() as events:
            prompt_set = await scan_runner.build_prompt_set(
                session, scan, row, competitors=[], settings=settings
            )

        (generated,) = [e for e in events if e["event"] == "prompts.generated"]
        assert (generated["scan_id"], generated["generated"], generated["dropped"]) == (
            scan.id, 20, 0
        )
        assert not [e for e in events if e["event"] == "prompts.fallback_used"]
        assert prompt_set.generation_params["fromGenerator"] == 20
        assert prompt_set.generation_params["fromFallback"] == 0
        assert prompt_set.generated_by == prompts.GENERATOR_MODEL

    async def test_a_topped_up_set_logs_the_fallback_and_stores_it(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await _client_row(client, session, domain="rmit.edu.au", industry="university")
        scan = await _queued_scan(session, row)
        stub_generator(monkeypatch, questions(17))

        with structlog.testing.capture_logs() as events:
            prompt_set = await scan_runner.build_prompt_set(
                session, scan, row, competitors=[], settings=settings
            )

        (generated,) = [e for e in events if e["event"] == "prompts.generated"]
        assert (generated["generated"], generated["dropped"]) == (17, 0)
        (used,) = [e for e in events if e["event"] == "prompts.fallback_used"]
        assert (used["scan_id"], used["fallback"], used["reason"]) == (scan.id, 3, "under_target")
        assert prompt_set.generation_params["fromGenerator"] == 17
        assert prompt_set.generation_params["fromFallback"] == 3
        assert len(prompt_set.prompts) == 20

    async def test_one_overlong_question_is_counted_as_dropped(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await _client_row(client, session, domain="rmit.com", industry="university")
        scan = await _queued_scan(session, row)
        payload = questions(20)
        payload[5]["text"] = "y" * 400
        stub_generator(monkeypatch, payload)

        with structlog.testing.capture_logs() as events:
            prompt_set = await scan_runner.build_prompt_set(
                session, scan, row, competitors=[], settings=settings
            )

        (generated,) = [e for e in events if e["event"] == "prompts.generated"]
        assert (generated["generated"], generated["dropped"]) == (19, 1)
        assert not [e for e in events if e["event"] == "prompts.fallback_used"]
        assert len(prompt_set.prompts) == 19
        assert "y" * 50 not in repr(events)

    async def test_the_counts_are_those_of_the_questions_kept_after_a_limit(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """17 generated + 3 templates cut to 6: the six kept are all the generator's."""
        row = await _client_row(client, session, domain="rmit.edu.au", industry="university")
        scan = await _queued_scan(session, row)
        stub_generator(monkeypatch, questions(17))

        with structlog.testing.capture_logs() as events:
            prompt_set = await scan_runner.build_prompt_set(
                session, scan, row, competitors=[], settings=settings, limit=6
            )

        assert len(prompt_set.prompts) == 6
        assert prompt_set.generation_params["fromGenerator"] == 6
        assert prompt_set.generation_params["fromFallback"] == 0
        (generated,) = [e for e in events if e["event"] == "prompts.generated"]
        assert generated["generated"] == 6
        assert not [e for e in events if e["event"] == "prompts.fallback_used"]

    async def test_a_limit_that_keeps_some_templates_counts_them(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await _client_row(client, session, domain="rmit.edu.au", industry="university")
        scan = await _queued_scan(session, row)
        stub_generator(monkeypatch, questions(17))

        prompt_set = await scan_runner.build_prompt_set(
            session, scan, row, competitors=[], settings=settings, limit=18
        )

        assert len(prompt_set.prompts) == 18
        assert prompt_set.generation_params["fromGenerator"] == 17
        assert prompt_set.generation_params["fromFallback"] == 1

    async def test_an_unavailable_phase_logs_why_stores_nothing_and_raises(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        row = await _client_row(client, session, domain="rmit.edu.au", industry="university")
        scan = await _queued_scan(session, row)
        stub_generator(monkeypatch, questions(10))

        with structlog.testing.capture_logs() as events, pytest.raises(prompts.PromptsUnavailable):
            await scan_runner.build_prompt_set(
                session, scan, row, competitors=[], settings=settings
            )

        (line,) = [e for e in events if e["event"] == "prompts.unavailable"]
        assert (line["scan_id"], line["reason"]) == (scan.id, "too_few_questions")
        assert not [e for e in events if e["event"] == "prompts.fallback_used"]
        assert (await session.execute(
            PromptSet.__table__.select().where(PromptSet.scan_id == scan.id)
        )).first() is None

    async def test_a_stubbed_generate_that_returns_the_old_pair_still_builds_a_set(
        self, client, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """Thirty test modules stub `generate_prompts` with `(prompts, generated_by)`."""
        row = await _client_row(client, session, domain="rmit.edu.au", industry="university")
        scan = await _queued_scan(session, row)

        async def old_pair(**kwargs):  # noqa: ANN003, ARG001
            return [prompts.GeneratedPrompt(text="q1", intent=AWARE)], "stub"

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", old_pair)
        prompt_set = await scan_runner.build_prompt_set(
            session, scan, row, competitors=[], settings=settings
        )
        assert prompt_set.generation_params["fromGenerator"] == 1
        assert prompt_set.generation_params["fromFallback"] == 0


# --- the scan ends FAILED, before any engine call -----------------------------------------------


class TestAScanWithoutQuestionsFailsBeforeAnyEngineCall:
    async def test_ten_questions_fail_the_scan_and_it_is_not_counted(
        self, client: AsyncClient, session, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        from datetime import UTC, datetime

        from avp_api.deps import scan_executor

        row = await _client_row(client, session, domain="rmit.edu.au", industry="university")
        jobs: list = []

        class _Deferred:
            async def submit(self, job, *, settings) -> None:  # noqa: ANN001, ARG002
                jobs.append(job)

        client._transport.app.dependency_overrides[scan_executor] = lambda: _Deferred()  # noqa: SLF001
        sid = (await client.post(f"{BASE}/clients/{row.id}/scans", json={})).json()["id"]

        stub_generator(monkeypatch, questions(10))
        engine_calls: list[str] = []

        async def ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            engine_calls.append(prompt)
            return []

        async def no_rivals(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            return None

        monkeypatch.setattr(scan_runner.engine_service, "ask_all", ask_all)
        monkeypatch.setattr(executor_module.detection, "ensure_set_for_scan", no_rivals)

        await execute_scan(jobs[0], settings=settings)

        scan = await session.get(Scan, sid)
        await session.refresh(scan)
        assert scan.status is ScanStatus.FAILED
        assert scan.error_code == "PROMPTS_UNAVAILABLE"
        assert engine_calls == []
        assert scan.finished_at is not None
        assert isinstance(jobs[0], ScanJob)
        held = await scan_allowance.count_scans(
            session, row.agency_id, now=datetime.now(UTC)
        )
        assert held == 0, "a failed prompt phase must not use up one of the agency's scans"


class TestAnOverlongQuestionWithTemplatesAvailable:
    async def test_nineteen_kept_are_topped_up_by_one_template(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        """The same case with a market: 19 is under the target, so the first template fills it."""
        payload = questions(20)
        payload[3]["text"] = "x" * (prompts.MAX_PROMPT_CHARS + 1)
        stub_generator(monkeypatch, payload)
        result = await generate(settings)
        assert (result.from_generator, result.from_fallback, result.dropped) == (19, 1, 1)
        assert len(result.prompts) == 20
        assert result.prompts[-1].text == "what is the best university in australia"


# --- F5 part B: the generation call itself -------------------------------------------------------
#
# Seven live calls on 2026-10-09 (US$0.17, founder-approved) all returned 20 questions in 11-14 s,
# so a short production set was most likely a failed call. Thinking is now set explicitly, the
# SDK retries one more time, and a failed call logs its HTTP status. Every call here is a stub.

_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class TestTheCallIsMadeAsSpecified:
    async def test_thinking_effort_tokens_and_model_are_explicit(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        calls = stub_generator(monkeypatch, questions(20))
        await generate(settings)
        assert len(calls) == 1
        sent = calls[0]
        assert sent["thinking"] == {"type": "disabled"}
        assert sent["output_config"] == {"effort": "medium"}
        assert sent["max_tokens"] == 8000
        assert sent["model"] == prompts.GENERATOR_MODEL == "claude-opus-5"

    def test_the_bound_is_sixty_seconds_for_each_of_three_attempts(self) -> None:
        assert prompts.GENERATOR_BOUND.timeout == 60.0
        assert prompts.GENERATOR_BOUND.max_retries == 2
        assert prompts.GENERATOR_BOUND.ceiling == 60.0 * 3 + 2.0


@pytest.mark.real_parse
class TestATransientFailureIsRetried:
    @staticmethod
    def _install(monkeypatch: pytest.MonkeyPatch, statuses: list[int]) -> list[int]:
        """The real SDK client over a MockTransport: each request gets the next status."""
        seen: list[int] = []
        good = {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": json.dumps({"prompts": questions(20)})}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            status = statuses[len(seen)]
            seen.append(status)
            if status == 200:
                return httpx.Response(200, json=good)
            return httpx.Response(
                status,
                json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}},
            )

        real_cls = anthropic.AsyncAnthropic

        def factory(**kwargs):  # noqa: ANN003, ANN202
            return real_cls(
                **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
            )

        monkeypatch.setattr(anthropic, "AsyncAnthropic", factory)
        return seen

    async def test_an_overloaded_answer_then_a_good_one_is_a_full_set(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        seen = self._install(monkeypatch, [529, 200])
        result = await generate(settings)
        assert seen == [529, 200]
        assert result.unavailable is None
        assert (result.from_generator, result.from_fallback) == (20, 0)
        assert result.generated_by == prompts.GENERATOR_MODEL

    async def test_two_overloaded_answers_then_a_good_one_still_succeed(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        # The second retry is the one this change adds.
        seen = self._install(monkeypatch, [529, 529, 200])
        result = await generate(settings)
        assert seen == [529, 529, 200]
        assert result.unavailable is None and result.from_generator == 20


class TestAFailedCallLogsItsStatus:
    async def test_a_status_error_logs_the_class_and_the_http_status(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        response = httpx.Response(529, request=httpx.Request("POST", _MESSAGES_URL))
        failure = anthropic.APIStatusError("overloaded", response=response, body=None)
        stub_generator(monkeypatch, failure)
        with structlog.testing.capture_logs() as events:
            result = await generate(settings)
        assert result.unavailable == "generation_failed"
        [line] = [e for e in events if e["event"] == "prompts.generation_failed"]
        assert line["error"] == "APIStatusError"
        assert line["status"] == 529
        assert "overloaded" not in json.dumps(line, default=str)

    async def test_a_timeout_logs_the_class_and_no_status(
        self, settings, monkeypatch  # noqa: ANN001
    ) -> None:
        stub_generator(monkeypatch, TimeoutError())
        with structlog.testing.capture_logs() as events:
            await generate(settings)
        [line] = [e for e in events if e["event"] == "prompts.generation_failed"]
        assert line["error"] == "TimeoutError"
        assert "status" not in line

"""A client records a classifier model and a classification time only when a model was asked.

Launch checklist 3.71, found by T1 (the intake card's end-to-end test): for a site whose
robots.txt does not answer, `POST /clients` stored and returned `classifierModel:
"claude-opus-5"` and a `classifiedAt`, though no page was read and no model was called.
`ClassificationOutcome.model` defaulted to the classifier's model, and `apply_outcome` copied
it, with the time of the write, onto every client.

The rule these tests hold:

- `classify` returns a `ClassificationOutcome` whose `model` is the model it ASKED, and None on
  the two paths that return before asking one: a crawl that did not succeed (a refusal by
  robots.txt, an address that is not public, a failed fetch, a busy or timed-out browser), and
  a page with too little text.
- A model that was asked is named whatever came back: an answer (a classification, an
  ambiguous call, "not a business site") or no answer (a refusal, nothing parseable, an answer
  that does not fit the schema, and every way the call can fail). A failed call is recorded as
  the ATTEMPT, as it was before this item: the model that was asked, and when. Nothing in the
  row says the model classified the client; the status and the reason code say what came back.
- `apply_outcome` writes the model and the time together, or neither. A client that was
  classified once and then refused has both cleared, as every other field of the old outcome is.

The paths are driven the way a request drives them. The crawl is the real one, with its
network edges faked, where that is simple (`robots_web`, and a stand-in for the browser that
fails the test if it is started), and stubbed at `crawl_site` where it is not. The model is the
SDK's `AsyncMessages.parse`, replaced by `ScriptedModel`: nothing reaches a provider, a test
that does not script it fails if the model is asked, and what a call was asked is recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, NoReturn

import anthropic
import httpx
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from avp_api import ids
from avp_api.config import Settings
from avp_api.models import ClassificationStatus, Client
from avp_api.services import crawl, intake
from avp_api.services.classify import (
    CLASSIFIER_MODEL,
    MIN_WORDS_FOR_CLASSIFICATION,
    ClassificationOutcome,
    IndustryClassification,
    classify,
)
from avp_api.services.crawl import CrawlResult, CrawlSignals

BASE = "/api/v1"
DOMAIN = "prospect-site.com"
ROBOTS = f"https://{DOMAIN}/robots.txt"


# ---------------------------------------------------------------------------
# the model, as the SDK's `parse`
# ---------------------------------------------------------------------------


class ScriptedModel:
    """`AsyncMessages.parse`, scripted: what the model does, and what it was asked.

    Unscripted, a call fails the test: the paths that must not ask the model say so by not
    scripting it, and `asked` is what they then assert on as well.
    """

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self._does: Callable[[], Any] | None = None

    def answers(
        self, parsed: IndustryClassification | None, *, stop_reason: str = "end_turn"
    ) -> None:
        response = SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)
        self._does = lambda: response

    def fails(self, exc: BaseException) -> None:
        def raise_it() -> NoReturn:
            raise exc

        self._does = raise_it

    async def parse(self, **kwargs: Any) -> Any:
        self.asked.append(kwargs)
        if self._does is None:
            raise AssertionError("the model was asked, on a path that must not ask it")
        return self._does()


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> ScriptedModel:
    scripted = ScriptedModel()
    monkeypatch.setattr(
        anthropic.resources.messages.AsyncMessages,
        "parse",
        lambda self, **kwargs: scripted.parse(**kwargs),
    )
    return scripted


def parsed(**overrides: Any) -> IndustryClassification:
    """What the model returns for a dental practice, unless a test says otherwise."""
    base: dict[str, Any] = {
        "is_classifiable": True,
        "industry": "dental practice",
        "niche": "cosmetic and implant dentistry",
        "brand_name": "Northaven Dental",
        "confidence": "high",
        "confidence_score": 0.92,
        "rationale": "The site describes clinical dental services and appointments.",
    }
    base.update(overrides)
    return IndustryClassification(**base)


def a_crawl(
    *, words: int = 250, ok: bool = True, error_code: str | None = None
) -> CrawlResult:
    """What the crawler hands the classifier: a site, or why there is none."""
    signals = CrawlSignals(
        final_url=f"https://{DOMAIN}/",
        registrable_domain=DOMAIN,
        word_count=words,
        pages_fetched=1 if ok else 0,
        urls_fetched=[f"https://{DOMAIN}/"] if ok else [],
    )
    return CrawlResult(
        signals=signals, text_extract="word " * words if ok else "", ok=ok, error_code=error_code
    )


# ---------------------------------------------------------------------------
# classify(): which paths ask the model
# ---------------------------------------------------------------------------

# Every code a crawl that did not succeed can carry (`crawl.py`), `BROWSER_BUSY` included
# (the intake refuses that one before it classifies, but `classify` is called by scripts too),
# and no code at all, which `classify` reports as `FETCH_FAILED`.
CRAWL_FAILURES = [
    "ROBOTS_DISALLOWED",
    "ROBOTS_UNREACHABLE",
    "ADDRESS_NOT_PUBLIC",
    "FETCH_FAILED",
    "HTTP_403",
    "HTTP_404",
    "BROWSER_ERROR",
    "TIMEOUT",
    "BROWSER_BUSY",
    None,
]


class TestPathsThatAskNoModel:
    @pytest.mark.parametrize("error_code", CRAWL_FAILURES, ids=str)
    async def test_a_crawl_that_did_not_succeed_asks_no_model_and_names_none(
        self, settings: Settings, model: ScriptedModel, error_code: str | None
    ) -> None:
        outcome = await classify(a_crawl(ok=False, error_code=error_code), settings=settings)

        assert outcome.status == "unclassifiable"
        assert outcome.reason_code == (error_code or "FETCH_FAILED")
        assert outcome.model is None
        assert model.asked == []

    async def test_a_page_with_too_little_text_asks_no_model_and_names_none(
        self, settings: Settings, model: ScriptedModel
    ) -> None:
        outcome = await classify(a_crawl(words=10), settings=settings)

        assert outcome.status == "unclassifiable"
        assert outcome.reason_code == "INSUFFICIENT_CONTENT"
        assert outcome.model is None
        assert model.asked == []

    async def test_the_model_is_asked_from_the_word_count_the_guard_lets_through(
        self, settings: Settings, model: ScriptedModel
    ) -> None:
        # One word under the threshold: no model. At it: asked, and named.
        just_under = await classify(
            a_crawl(words=MIN_WORDS_FOR_CLASSIFICATION - 1), settings=settings
        )
        assert (just_under.reason_code, just_under.model) == ("INSUFFICIENT_CONTENT", None)
        assert model.asked == []

        model.answers(parsed())
        at_it = await classify(a_crawl(words=MIN_WORDS_FOR_CLASSIFICATION), settings=settings)
        assert (at_it.status, at_it.model) == ("classified", CLASSIFIER_MODEL)
        assert len(model.asked) == 1


def _a_validation_error() -> ValidationError:
    try:
        parsed(industry="x" * 200)
    except ValidationError as exc:
        return exc
    raise AssertionError("an industry of 200 characters should not validate")


_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls: type[anthropic.APIStatusError], status: int, message: str = "x") -> Any:
    return cls(message, response=httpx.Response(status, request=_REQUEST), body=None)


# Every way the call itself can end without an answer, and the reason `classify` gives it.
CALLS_THAT_FAIL = [
    pytest.param(
        _status_error(anthropic.AuthenticationError, 401), "PROVIDER_AUTH_FAILED", id="auth"
    ),
    pytest.param(
        _status_error(anthropic.RateLimitError, 429), "PROVIDER_RATE_LIMITED", id="rate-limited"
    ),
    pytest.param(
        _status_error(anthropic.BadRequestError, 400, "your credit balance is too low"),
        "PROVIDER_QUOTA_EXHAUSTED",
        id="quota",
    ),
    pytest.param(
        _status_error(anthropic.BadRequestError, 400), "PROVIDER_BAD_REQUEST", id="bad-request"
    ),
    pytest.param(anthropic.APITimeoutError(request=_REQUEST), "TIMEOUT", id="attempt-timeout"),
    pytest.param(TimeoutError(), "TIMEOUT", id="deadline"),
    pytest.param(
        anthropic.APIConnectionError(request=_REQUEST), "PROVIDER_UNREACHABLE", id="unreachable"
    ),
    pytest.param(
        _status_error(anthropic.InternalServerError, 500), "PROVIDER_ERROR", id="server-error"
    ),
    pytest.param(_a_validation_error(), "PROVIDER_SCHEMA_VIOLATION", id="schema-violation"),
]


class TestPathsThatAskTheModel:
    async def test_a_classification_names_the_model_the_request_named(
        self, settings: Settings, model: ScriptedModel
    ) -> None:
        model.answers(parsed())

        outcome = await classify(a_crawl(), settings=settings)

        assert outcome.status == "classified"
        assert outcome.model == CLASSIFIER_MODEL
        # The name recorded is the one that was sent, not a constant that merely agrees today.
        assert [call["model"] for call in model.asked] == [outcome.model]

    @pytest.mark.parametrize(
        ("answer", "status", "reason"),
        [
            pytest.param(parsed(confidence_score=0.4, confidence="low"), "ambiguous",
                         "LOW_CONFIDENCE", id="ambiguous"),
            pytest.param(parsed(is_classifiable=False, industry=None), "unclassifiable",
                         "NOT_A_BUSINESS_SITE", id="not-a-business-site"),
        ],
    )
    async def test_an_answer_that_is_not_a_classification_still_names_the_model(
        self, settings: Settings, model: ScriptedModel,
        answer: IndustryClassification, status: str, reason: str,
    ) -> None:
        model.answers(answer)

        outcome = await classify(a_crawl(), settings=settings)

        assert (outcome.status, outcome.reason_code) == (status, reason)
        assert outcome.model == CLASSIFIER_MODEL

    @pytest.mark.parametrize(("failure", "reason"), CALLS_THAT_FAIL)
    async def test_a_call_that_was_made_and_failed_names_the_model_it_asked(
        self, settings: Settings, model: ScriptedModel, failure: BaseException, reason: str
    ) -> None:
        model.fails(failure)

        outcome = await classify(a_crawl(), settings=settings)

        assert outcome.status == "unclassifiable"
        assert outcome.reason_code == reason
        assert outcome.industry is None
        # The attempt: what was asked is what is named.
        assert [call["model"] for call in model.asked] == [CLASSIFIER_MODEL]
        assert outcome.model == CLASSIFIER_MODEL

    async def test_a_refusal_names_the_model(
        self, settings: Settings, model: ScriptedModel
    ) -> None:
        model.answers(None, stop_reason="refusal")

        outcome = await classify(a_crawl(), settings=settings)

        assert (outcome.status, outcome.reason_code) == ("unclassifiable", "PROVIDER_REFUSED")
        assert outcome.model == CLASSIFIER_MODEL

    async def test_an_answer_with_nothing_to_parse_names_the_model(
        self, settings: Settings, model: ScriptedModel
    ) -> None:
        model.answers(None)

        outcome = await classify(a_crawl(), settings=settings)

        assert (outcome.status, outcome.reason_code) == ("unclassifiable", "PROVIDER_NO_OUTPUT")
        assert outcome.model == CLASSIFIER_MODEL


# ---------------------------------------------------------------------------
# apply_outcome(): the two fields are written together, or not at all
# ---------------------------------------------------------------------------


def _a_client(**fields: Any) -> Client:
    return Client(
        id=ids.new_id(ids.CLIENT), agency_id="agcy_x", name=DOMAIN, domain=DOMAIN, **fields
    )


class TestApplyOutcome:
    def test_a_model_that_was_asked_is_recorded_with_the_time_of_the_write(self) -> None:
        client = _a_client()
        before = datetime.now(UTC)

        intake.apply_outcome(
            client,
            ClassificationOutcome(
                status="classified", industry="dental practice", model=CLASSIFIER_MODEL
            ),
        )

        assert client.classifier_model == CLASSIFIER_MODEL
        assert client.classified_at is not None
        assert before <= client.classified_at <= datetime.now(UTC)

    @pytest.mark.parametrize(
        "outcome",
        [
            pytest.param(
                ClassificationOutcome(status="unclassifiable", reason_code="ROBOTS_UNREACHABLE"),
                id="crawl-refused",
            ),
            pytest.param(
                ClassificationOutcome(status="unclassifiable", reason_code="INSUFFICIENT_CONTENT"),
                id="too-little-text",
            ),
        ],
    )
    def test_an_outcome_from_no_model_records_neither(self, outcome: ClassificationOutcome) -> None:
        client = _a_client()

        intake.apply_outcome(client, outcome)

        assert client.classification_status is ClassificationStatus.UNCLASSIFIABLE
        assert client.classification_reason_code == outcome.reason_code
        assert client.classifier_model is None
        assert client.classified_at is None

    def test_a_call_that_failed_is_recorded_as_the_attempt(self) -> None:
        client = _a_client()

        intake.apply_outcome(
            client,
            ClassificationOutcome(
                status="unclassifiable", reason_code="PROVIDER_ERROR", model=CLASSIFIER_MODEL
            ),
        )

        assert client.classification_status is ClassificationStatus.UNCLASSIFIABLE
        assert client.classifier_model == CLASSIFIER_MODEL
        assert client.classified_at is not None

    def test_a_default_outcome_names_no_model(self) -> None:
        # The bug was a default that said a model had run. Nothing is claimed unless a path
        # that asked a model says so.
        assert ClassificationOutcome(status="unclassifiable").model is None

    def test_a_client_classified_before_and_refused_now_has_both_cleared(self) -> None:
        client = _a_client()
        intake.apply_outcome(
            client,
            ClassificationOutcome(
                status="classified",
                industry="dental practice",
                confidence="high",
                confidence_score=Decimal("0.920"),
                model=CLASSIFIER_MODEL,
            ),
        )
        assert client.classifier_model == CLASSIFIER_MODEL and client.classified_at is not None

        intake.apply_outcome(
            client, ClassificationOutcome(status="unclassifiable", reason_code="ROBOTS_DISALLOWED")
        )

        # The old industry, confidence and status are replaced by this outcome; the model and
        # the time that described them go with them rather than claim this one.
        assert client.industry is None and client.industry_confidence_score is None
        assert client.classifier_model is None
        assert client.classified_at is None


# ---------------------------------------------------------------------------
# POST /clients and POST /clients/{id}/reclassify: what a person is given
# ---------------------------------------------------------------------------


async def _sign_up(client: AsyncClient, email: str = "provenance@test.example") -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Provenance Agency", "fullName": "Op", "email": email,
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


def _no_browser() -> NoReturn:
    raise AssertionError("a browser was started for a site that was refused before it")


def _crawl_site_returns(
    monkeypatch: pytest.MonkeyPatch, *results: CrawlResult
) -> Callable[[], int]:
    """Stand in for `crawl_site`: each call returns the next result, the last one repeating.

    For the crawls that fail inside a browser, which this file does not fake. The classifier
    and everything after it are the real ones. Returns how many times it was called.
    """
    calls: list[str] = []

    async def fake_crawl_site(url: str, **kwargs: Any) -> CrawlResult:  # noqa: ARG001
        calls.append(url)
        return results[min(len(calls), len(results)) - 1]

    monkeypatch.setattr(intake, "crawl_site", fake_crawl_site)
    return lambda: len(calls)


def _utc(instant: str) -> datetime:
    assert instant.endswith("Z"), instant
    return datetime.fromisoformat(instant)


async def _add(client: AsyncClient) -> dict[str, Any]:
    resp = await client.post(f"{BASE}/clients", json={"url": DOMAIN, "classify": True})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _read_back(client: AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    """The client as the API reads it from the database, not as the write answered."""
    resp = await client.get(f"{BASE}/clients/{body['id']}")
    assert resp.status_code == 200, resp.text
    stored: dict[str, Any] = resp.json()
    return stored


def _neither(body: dict[str, Any]) -> None:
    assert body["classifierModel"] is None
    assert body["classifiedAt"] is None


class TestAClientWhoseSiteWasNotReadHasNoModelAndNoTime:
    async def test_robots_txt_that_does_not_answer(
        self, client: AsyncClient, robots_web: Any, model: ScriptedModel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        robots_web.fail(ROBOTS, httpx.ReadTimeout("no answer"))
        monkeypatch.setattr(crawl, "async_playwright", _no_browser)
        await _sign_up(client)

        body = await _add(client)

        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ROBOTS_UNREACHABLE"
        _neither(body)
        _neither(await _read_back(client, body))
        assert model.asked == []

    async def test_robots_txt_that_forbids_the_crawler_the_home_page(
        self, client: AsyncClient, robots_web: Any, model: ScriptedModel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        robots_web.serve(ROBOTS, body="User-agent: *\nDisallow: /\n")
        monkeypatch.setattr(crawl, "async_playwright", _no_browser)
        await _sign_up(client)

        body = await _add(client)

        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ROBOTS_DISALLOWED"
        _neither(body)
        _neither(await _read_back(client, body))
        assert model.asked == []

    async def test_an_address_that_is_not_public(
        self, client: AsyncClient, robots_web: Any, model: ScriptedModel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        robots_web.resolve_to(DOMAIN, "192.168.1.10")
        monkeypatch.setattr(crawl, "async_playwright", _no_browser)
        await _sign_up(client)

        body = await _add(client)

        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ADDRESS_NOT_PUBLIC"
        _neither(body)
        _neither(await _read_back(client, body))
        assert robots_web.requests == [] and model.asked == []

    @pytest.mark.parametrize("code", ["FETCH_FAILED", "HTTP_403", "BROWSER_ERROR", "TIMEOUT"])
    async def test_a_crawl_that_fails(
        self, client: AsyncClient, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch, code: str
    ) -> None:
        _crawl_site_returns(monkeypatch, a_crawl(ok=False, error_code=code))
        await _sign_up(client)

        body = await _add(client)

        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == code
        _neither(body)
        _neither(await _read_back(client, body))
        assert model.asked == []

    async def test_a_page_with_too_little_text_to_classify(
        self, client: AsyncClient, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _crawl_site_returns(monkeypatch, a_crawl(words=12))
        await _sign_up(client)

        body = await _add(client)

        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "INSUFFICIENT_CONTENT"
        _neither(body)
        _neither(await _read_back(client, body))
        assert model.asked == []


class TestAClientWhoseSiteWentToTheModelHasBoth:
    async def test_a_normal_classification(
        self, client: AsyncClient, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _crawl_site_returns(monkeypatch, a_crawl())
        model.answers(parsed())
        await _sign_up(client)
        before = datetime.now(UTC)

        body = await _add(client)

        after = datetime.now(UTC)
        assert body["classificationStatus"] == "classified"
        assert body["industry"] == "dental practice"
        assert body["classifierModel"] == CLASSIFIER_MODEL
        assert [call["model"] for call in model.asked] == [body["classifierModel"]]
        assert before <= _utc(body["classifiedAt"]) <= after
        stored = await _read_back(client, body)
        assert stored["classifierModel"] == CLASSIFIER_MODEL
        assert stored["classifiedAt"] == body["classifiedAt"]

    async def test_a_call_that_was_made_and_failed_is_recorded_as_the_attempt(
        self, client: AsyncClient, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _crawl_site_returns(monkeypatch, a_crawl())
        model.fails(anthropic.APIConnectionError(request=_REQUEST))
        await _sign_up(client)
        before = datetime.now(UTC)

        body = await _add(client)

        after = datetime.now(UTC)
        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "PROVIDER_UNREACHABLE"
        assert body["industry"] is None
        assert body["classifierModel"] == CLASSIFIER_MODEL
        assert before <= _utc(body["classifiedAt"]) <= after
        assert (await _read_back(client, body))["classifierModel"] == CLASSIFIER_MODEL

    async def test_a_model_that_says_it_is_not_a_business_site_is_still_recorded(
        self, client: AsyncClient, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _crawl_site_returns(monkeypatch, a_crawl())
        model.answers(parsed(is_classifiable=False, industry=None))
        await _sign_up(client)

        body = await _add(client)

        assert body["classificationReasonCode"] == "NOT_A_BUSINESS_SITE"
        assert body["classifierModel"] == CLASSIFIER_MODEL
        assert body["classifiedAt"] is not None


class TestReclassifying:
    async def test_a_classified_client_whose_site_is_then_refused_has_both_cleared(
        self, client: AsyncClient, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First read: the site is read and the model classifies it. Second: robots.txt now
        # forbids the crawler, so nothing is read and no model is asked.
        _crawl_site_returns(
            monkeypatch, a_crawl(), a_crawl(ok=False, error_code="ROBOTS_DISALLOWED")
        )
        model.answers(parsed())
        await _sign_up(client)
        classified = await _add(client)
        assert classified["classifierModel"] == CLASSIFIER_MODEL
        assert classified["classifiedAt"] is not None

        resp = await client.post(f"{BASE}/clients/{classified['id']}/reclassify")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ROBOTS_DISALLOWED"
        assert body["industry"] is None
        _neither(body)
        _neither(await _read_back(client, body))
        assert len(model.asked) == 1  # the first read's, and none for the second


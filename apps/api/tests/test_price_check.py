"""The API checks the Stripe Price it sells against — P2, US$150 a month (2026-10-04).

The founder decided the price. Stripe's Price objects are the founder's to create in the
dashboard, so the code only states the number (`services/billing.py`: `PLAN_PRICE_USD`, with the
currency and the period beside it) and checks the Price that `STRIPE_PRICE_ID` names against it,
so that a Price at the wrong amount is a log line the day it is configured and not a customer's
receipt. `billing.price_check` does the reading; `price_watch.py` runs it at start and then every
hour, in a task.

What is pinned, each by a test that fails without it:

* the plan is 150 US dollars, once a month, and the amount in cents is derived from the price and
  typed nowhere (the web's copy of the price is held to this one in
  `apps/web/src/components/marketing/planPrice.test.ts`);
* a Price that matches is one info line, `billing.price_check`, with `ok=True` and the Price id;
* every way a Price can differ (the old $160 and the $29 before it, another currency, yearly,
  every two months, no recurrence at all, no amount, one that is archived, one that is metered)
  is one warning, `billing.price_mismatch`, with what was expected and what was found, amounts
  in cents. An archived Price (`active` is not true) and a metered one (`recurring.usage_type` is
  not `licensed`) are never an `ok`: checkout refuses the first and bills the second by usage;
* an error from Stripe, a timeout, or a Price that is not shaped like one is one warning,
  `billing.price_check_failed`, with the error's class and nothing else: the message can carry
  request details. Whatever happens, the check never raises;
* with Stripe not configured (no key, no Price id, or neither) there is no Stripe call and no line;
* a DEPLOYED API (staging or production) with exactly one of the two settings logs one ERROR,
  `billing.half_configured`, at start, naming only the variable that is missing (`missing`, never a
  value): billing's gates are off for every agency in that state and nothing else says so. So does
  one with both set and no `STRIPE_WEBHOOK_SECRET` (customers could pay, and every webhook would be
  refused). Both set with the webhook secret is the price check's, neither set says nothing, a
  local or test API says nothing in any combination, and the start goes on whatever happens
  (added from the L2 and L5 security reviews);
* the key is in no line, in any channel, whatever Stripe says;
* the check is a background task, and only a DEPLOYED API starts it: a local or test start never
  calls Stripe, whatever the developer's `.env` holds (the L1 security review). The app starts
  while Stripe does not answer, the task checks again after every interval, and shutdown cancels
  it before the pools close, without waiting for ever on a check that will not stop.

Nothing here reaches Stripe. `_client` is replaced by a fake that offers the one read the check
makes, and, until a test installs it, by one that raises: forgetting the fake is a loud failure and
not a request to a real account (the same two guards `test_billing.py` has).
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import stripe
from pydantic import SecretStr
from structlog.testing import capture_logs

from avp_api import main, price_watch, redis_watch
from avp_api.config import Settings
from avp_api.services import billing as billing_service
from avp_api.services import scan_allowance

WATCHDOG = 15.0
# A key-looking value that is distinctive enough to find in any channel.
KEY = "sk_" + "test_PRICECHECKKEYSENTINEL0123456789abcdef"
PRICE_ID = "price_test_fake_150"
WEBHOOK_SECRET = "whsec" + "_test_PRICECHECKWEBHOOKSENTINEL0123456789"
# What a Stripe error's text can carry and a log line must not: a host and a request id.
DETAIL = "10.0.0.7:443 request req_PRICECHECKREQUESTSENTINEL"

SRC = Path(__file__).resolve().parents[1] / "src" / "avp_api"

# The decision, written out here and not imported: editing the code cannot quietly edit what
# these tests pin. 150 dollars is 15000 cents.
EXPECTED_AMOUNT = 15000
EXPECTED_CURRENCY = "usd"
EXPECTED_INTERVAL = "month"
EXPECTED_INTERVAL_COUNT = 1
EXPECTED_USAGE_TYPE = "licensed"

OK_LINE = {
    "event": "billing.price_check",
    "log_level": "info",
    "ok": True,
    "price_id": PRICE_ID,
    "trigger": "watch",
}


def mismatch_line(**actual: Any) -> dict[str, Any]:
    """`billing.price_mismatch` as it must read: what is expected, and what was found."""
    found = {
        "amount": EXPECTED_AMOUNT,
        "currency": EXPECTED_CURRENCY,
        "interval": EXPECTED_INTERVAL,
        "interval_count": EXPECTED_INTERVAL_COUNT,
        "active": True,
        "usage_type": EXPECTED_USAGE_TYPE,
        **actual,
    }
    return {
        "event": "billing.price_mismatch",
        "log_level": "warning",
        "price_id": PRICE_ID,
        "expected_amount": EXPECTED_AMOUNT,
        "actual_amount": found["amount"],
        "expected_currency": EXPECTED_CURRENCY,
        "actual_currency": found["currency"],
        "expected_interval": EXPECTED_INTERVAL,
        "actual_interval": found["interval"],
        "expected_interval_count": EXPECTED_INTERVAL_COUNT,
        "actual_interval_count": found["interval_count"],
        "expected_active": True,
        "actual_active": found["active"],
        "expected_usage_type": EXPECTED_USAGE_TYPE,
        "actual_usage_type": found["usage_type"],
        "trigger": "watch",
    }


def failed_line(error_type: str) -> dict[str, Any]:
    return {
        "event": "billing.price_check_failed",
        "log_level": "warning",
        "error_type": error_type,
        "trigger": "watch",
    }


def half_line(missing: str) -> dict[str, Any]:
    """`billing.half_configured`: an ERROR, the variable that is not set, and nothing else."""
    return {"event": "billing.half_configured", "log_level": "error", "missing": missing}


DEPLOYED = ["staging", "production"]
LOCAL = ["local", "test"]
# The two ways to have exactly one of the two settings, and the variable each leaves unset.
HALVES = [
    pytest.param(
        {"stripe_secret_key": SecretStr(KEY), "stripe_price_id": None},
        "STRIPE_PRICE_ID",
        id="a-key-and-no-price-id",
    ),
    pytest.param(
        {"stripe_secret_key": None, "stripe_price_id": PRICE_ID},
        "STRIPE_SECRET_KEY",
        id="a-price-id-and-no-key",
    ),
]
# All three settings: the whole of billing, as a deployed API needs it.
BOTH = {
    "stripe_secret_key": SecretStr(KEY),
    "stripe_price_id": PRICE_ID,
    "stripe_webhook_secret": SecretStr(WEBHOOK_SECRET),
}
# The key and the Price id and no webhook secret: customers could pay, and no webhook would be
# accepted to record it.
BOTH_WITHOUT_WEBHOOK = {**BOTH, "stripe_webhook_secret": None}
NEITHER = {"stripe_secret_key": None, "stripe_price_id": None, "stripe_webhook_secret": None}


def a_price(
    *,
    amount: Any = EXPECTED_AMOUNT,
    currency: Any = EXPECTED_CURRENCY,
    interval: Any = EXPECTED_INTERVAL,
    interval_count: Any = EXPECTED_INTERVAL_COUNT,
    active: Any = True,
    usage_type: Any = EXPECTED_USAGE_TYPE,
    recurring: bool = True,
) -> SimpleNamespace:
    """A Price shaped as Stripe sends one: an amount in cents, a currency, whether it is `active`
    (can still be bought), and `recurring` (null for a one-time Price) with its `usage_type`. The
    fields take any value, so a test can send one Stripe never would."""
    return SimpleNamespace(
        id=PRICE_ID,
        object="price",
        active=active,
        unit_amount=amount,
        currency=currency,
        recurring=(
            SimpleNamespace(interval=interval, interval_count=interval_count, usage_type=usage_type)
            if recurring
            else None
        ),
    )


async def _until(predicate: Callable[[], bool], *, seconds: float = 5.0) -> None:
    """Wait for `predicate()`. A test that waits for something that never comes fails, not hangs."""
    try:
        async with asyncio.timeout(seconds):
            while not predicate():
                await asyncio.sleep(0.005)
    except TimeoutError:
        pytest.fail(f"still waiting after {seconds}s")


async def _nothing() -> None:
    return None


def _price_tasks() -> list[asyncio.Task[Any]]:
    return [task for task in asyncio.all_tasks() if task.get_name() == price_watch.TASK_NAME]


def _billing(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"].startswith("billing.")]


class FakeStripe:
    """The one Stripe call the check makes, `client.v1.prices.retrieve_async`, and a record of it.

    It offers nothing else: a check that asked Stripe for anything more (a list, a write) meets an
    AttributeError, which the check reports as a failure, which fails the test that expects a
    clean answer. `outcomes` is what each successive call does: an exception is raised, anything
    else is returned; the last one repeats. `hang` makes a call wait until it is cancelled.
    """

    def __init__(self) -> None:
        self.outcomes: list[object] = [a_price()]
        self.calls: list[str] = []  # the Price ids asked for
        self.clients_built: list[Settings] = []  # one entry per `_client(settings)`
        self.hang = False
        self.cancelled = False

    def serve(self, *outcomes: object) -> FakeStripe:
        self.outcomes = list(outcomes)
        return self

    @property
    def v1(self) -> Any:
        fake = self

        class _Prices:
            async def retrieve_async(
                self, price: str, params: Any = None, options: Any = None  # noqa: ARG002
            ) -> Any:
                fake.calls.append(price)
                if fake.hang:
                    try:
                        await asyncio.Event().wait()  # never set: only a cancellation ends it
                    except asyncio.CancelledError:
                        fake.cancelled = True
                        raise
                outcome = fake.outcomes.pop(0) if len(fake.outcomes) > 1 else fake.outcomes[0]
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        return SimpleNamespace(prices=_Prices())


@pytest.fixture(autouse=True)
def _no_unstubbed_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any reach for a real Stripe client fails the test, loudly."""

    def _forbidden(settings: Any) -> Any:  # noqa: ARG001
        raise AssertionError("this test reached for a real Stripe client; use `stripe_fake`")

    monkeypatch.setattr(billing_service, "_client", _forbidden)


@pytest.fixture
def stripe_fake(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    fake = FakeStripe()

    def build(settings: Settings) -> FakeStripe:
        fake.clients_built.append(settings)
        return fake

    monkeypatch.setattr(billing_service, "_client", build)
    return fake


@pytest.fixture
def configured(settings: Settings) -> Settings:
    """The suite's settings with Stripe set. The shared ones leave it unset, on purpose: with both
    set, the scan allowance's gate is on for every test (see `conftest.settings`). Still the TEST
    environment: `price_check` itself does not look at the environment, `price_watch.start` does."""
    return settings.model_copy(
        update={"stripe_secret_key": SecretStr(KEY), "stripe_price_id": PRICE_ID}
    )


@pytest.fixture
def deployed(settings: Settings) -> Settings:
    """A deployed API with billing set up whole (the key, the Price id and the webhook secret), so
    that the only lines a start can write are the price check's. A copy, not `create_app`'s
    settings: nothing here validates them, which is how a test gets a production environment
    without a production `APP_SECRET`."""
    return settings.model_copy(update={"environment": "production", **BOTH})


# ---------------------------------------------------------------------------
# the plan, as the API states it
# ---------------------------------------------------------------------------


class TestThePlanTheApiStates:
    def test_it_is_150_us_dollars_a_month_once_a_month(self) -> None:
        assert billing_service.PLAN_PRICE_USD == 150
        assert billing_service.PLAN_CURRENCY == "usd"
        assert billing_service.PLAN_INTERVAL == "month"
        assert billing_service.PLAN_INTERVAL_COUNT == 1

    def test_the_amount_in_cents_is_derived_from_the_price(self) -> None:
        assert billing_service.PLAN_UNIT_AMOUNT_CENTS == 15000
        assert billing_service.PLAN_UNIT_AMOUNT_CENTS == billing_service.PLAN_PRICE_USD * 100

    def test_the_price_is_typed_once_and_the_cents_not_at_all(self) -> None:
        """The web test reads `PLAN_PRICE_USD` out of this file, so there must be exactly one
        assignment to find; and a second literal for the amount in cents would be a second copy
        that could move alone."""
        source = (SRC / "services" / "billing.py").read_text()
        assert len(re.findall(r"^PLAN_PRICE_USD = \d+\s*$", source, re.M)) == 1
        assert not re.search(r"\b15000\b", source)


# ---------------------------------------------------------------------------
# a Price that matches, and every way one can differ
# ---------------------------------------------------------------------------


class TestAMatchingPrice:
    async def test_it_is_one_info_line_with_ok_and_the_price_id(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        with capture_logs() as logs:
            await billing_service.price_check(configured)

        assert logs == [OK_LINE]

    async def test_it_is_one_read_of_the_configured_price_through_the_client(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        with capture_logs():
            await billing_service.price_check(configured)

        # One client, built from these settings, and one read of the Price id these settings name.
        assert stripe_fake.clients_built == [configured]
        assert stripe_fake.calls == [PRICE_ID]


class TestAMismatch:
    @pytest.mark.parametrize(
        ("price", "found"),
        [
            # The price before the 2026-10-04 repricing, still the one test-mode Price that exists.
            (a_price(amount=16000), {"amount": 16000}),
            # The price before that one.
            (a_price(amount=2900), {"amount": 2900}),
            # Dollars where cents were meant.
            (a_price(amount=150), {"amount": 150}),
            (a_price(currency="eur"), {"currency": "eur"}),
            (a_price(currency="gbp"), {"currency": "gbp"}),
            (a_price(interval="year"), {"interval": "year"}),
            (a_price(interval="week"), {"interval": "week"}),
            (a_price(interval_count=2), {"interval_count": 2}),
            (a_price(interval="year", interval_count=2), {"interval": "year", "interval_count": 2}),
            # A one-time Price: Stripe sends `recurring: null`, so no interval, count or usage.
            (
                a_price(recurring=False),
                {"interval": None, "interval_count": None, "usage_type": None},
            ),
            # A Price with no single amount (tiered or custom): `unit_amount: null`.
            (a_price(amount=None), {"amount": None}),
            # An archived Price: right in every other way, and checkout will not take it.
            (a_price(active=False), {"active": False}),
            # A metered Price bills by reported usage, not a flat amount a month.
            (a_price(usage_type="metered"), {"usage_type": "metered"}),
            # No `active` at all: not "true", so not the plan.
            (a_price(active=None), {"active": None}),
            # Everything wrong at once.
            (
                a_price(
                    amount=2900, currency="eur", interval="year", interval_count=3,
                    active=False, usage_type="metered",
                ),
                {
                    "amount": 2900, "currency": "eur", "interval": "year", "interval_count": 3,
                    "active": False, "usage_type": "metered",
                },
            ),
        ],
        ids=[
            "old-price-160", "older-price-29", "dollars-not-cents", "euro", "pound", "yearly",
            "weekly", "every-two-months", "every-two-years", "one-time", "no-amount",
            "archived", "metered", "no-active-field", "everything-wrong",
        ],
    )
    async def test_it_is_one_warning_with_what_was_expected_and_what_was_found(
        self,
        configured: Settings,
        stripe_fake: FakeStripe,
        price: SimpleNamespace,
        found: dict[str, Any],
    ) -> None:
        stripe_fake.serve(price)

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        assert logs == [mismatch_line(**found)]

    async def test_the_old_price_line_reads_in_cents_on_both_sides(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        """The case the founder will meet first: `STRIPE_PRICE_ID` still names the $160 Price."""
        stripe_fake.serve(a_price(amount=16000))

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        (line,) = logs
        assert (line["expected_amount"], line["actual_amount"]) == (15000, 16000)
        assert line["log_level"] == "warning"

    async def test_a_mismatch_is_not_an_ok(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        stripe_fake.serve(a_price(amount=16000))

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        assert not [e for e in logs if e["event"] == "billing.price_check"]

    @pytest.mark.parametrize(
        ("price", "field", "actual"),
        [
            (a_price(active=False), "active", False),
            (a_price(active=None), "active", None),
            (a_price(usage_type="metered"), "usage_type", "metered"),
        ],
        ids=["archived", "no-active-field", "metered"],
    )
    async def test_an_archived_or_metered_price_is_never_an_ok(
        self,
        configured: Settings,
        stripe_fake: FakeStripe,
        price: SimpleNamespace,
        field: str,
        actual: Any,
    ) -> None:
        """The amount, the currency and the period can all be right and the Price still not the
        plan: an archived Price cannot be bought, and a metered one is billed by usage. Both say
        what was expected and what was found, at WARNING."""
        stripe_fake.serve(price)

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        (line,) = logs
        assert line["event"] == "billing.price_mismatch"
        assert line["log_level"] == "warning"
        expected = {"active": True, "usage_type": "licensed"}[field]
        assert (line[f"expected_{field}"], line[f"actual_{field}"]) == (expected, actual)


class TestTheRealStripePriceObject:
    """The fakes above are plain objects. The SDK hands back a `stripe.Price`, which is not a
    `dict` and answers a field that is not there differently, so the check is also run on the
    real class. It is built from the JSON Stripe documents for a Price by `construct_from`, which
    is pure data: no client is made and nothing leaves the process."""

    PRICE_JSON: dict[str, Any] = {
        "id": PRICE_ID,
        "object": "price",
        "active": True,
        "billing_scheme": "per_unit",
        "created": 1790000000,
        "currency": "usd",
        "custom_unit_amount": None,
        "livemode": False,
        "lookup_key": None,
        "metadata": {},
        "nickname": None,
        "product": "prod_test_fake",
        "recurring": {
            "interval": "month",
            "interval_count": 1,
            "meter": None,
            "trial_period_days": None,
            "usage_type": "licensed",
        },
        "tax_behavior": "unspecified",
        "tiers_mode": None,
        "transform_quantity": None,
        "type": "recurring",
        "unit_amount": 15000,
        "unit_amount_decimal": "15000",
    }

    def real(self, **changes: Any) -> stripe.Price:
        return stripe.Price.construct_from({**self.PRICE_JSON, **changes}, None)

    async def test_a_150_dollar_monthly_price_is_ok(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        stripe_fake.serve(self.real())

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        assert logs == [OK_LINE]

    @pytest.mark.parametrize(
        ("changes", "found"),
        [
            ({"unit_amount": 16000, "unit_amount_decimal": "16000"}, {"amount": 16000}),
            ({"currency": "eur"}, {"currency": "eur"}),
            (
                {"recurring": {**PRICE_JSON["recurring"], "interval": "year"}},
                {"interval": "year"},
            ),
            (
                {"recurring": {**PRICE_JSON["recurring"], "interval_count": 2}},
                {"interval_count": 2},
            ),
            # Stripe sends `recurring: null` for a one-time Price...
            (
                {"recurring": None, "type": "one_time"},
                {"interval": None, "interval_count": None, "usage_type": None},
            ),
            # ...and `unit_amount: null` for a tiered one.
            ({"unit_amount": None, "billing_scheme": "tiered"}, {"amount": None}),
            # An archived Price is still returned by a retrieve, with `active: false`.
            ({"active": False}, {"active": False}),
            # A metered one carries a meter, and its usage type says so.
            (
                {
                    "recurring": {
                        **PRICE_JSON["recurring"], "usage_type": "metered", "meter": "mtr_x"
                    }
                },
                {"usage_type": "metered"},
            ),
        ],
        ids=[
            "old-price-160", "euro", "yearly", "every-two-months", "one-time", "tiered",
            "archived", "metered",
        ],
    )
    async def test_every_difference_is_read_off_the_real_object(
        self,
        configured: Settings,
        stripe_fake: FakeStripe,
        changes: dict[str, Any],
        found: dict[str, Any],
    ) -> None:
        stripe_fake.serve(self.real(**changes))

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        assert logs == [mismatch_line(**found)]


# ---------------------------------------------------------------------------
# a failure is a warning with the class name, and never an exception
# ---------------------------------------------------------------------------


class TestAFailure:
    @pytest.mark.parametrize(
        "error",
        [
            stripe.AuthenticationError(
                f"Invalid API Key provided: {KEY} {DETAIL}", http_status=401
            ),
            stripe.PermissionError(f"This key cannot read prices {DETAIL}", http_status=403),
            stripe.InvalidRequestError(
                f"No such price: '{PRICE_ID}' {DETAIL}", param="price", code="resource_missing"
            ),
            stripe.RateLimitError(f"Too many requests {DETAIL}", http_status=429),
            stripe.APIConnectionError(f"Network error {DETAIL}"),
            stripe.APIError(f"Stripe had a problem {DETAIL}", http_status=500),
            httpx.ReadTimeout(f"read timed out {DETAIL}"),
            TimeoutError(f"timed out {DETAIL}"),
            OSError(f"[Errno 65] No route to host {DETAIL}"),
            RuntimeError(f"something nobody planned for {DETAIL}"),
        ],
        ids=[
            "bad-key", "no-permission", "no-such-price", "rate-limited", "network", "stripe-5xx",
            "httpx-timeout", "timeout", "os-error", "anything-else",
        ],
    )
    async def test_it_is_one_warning_with_the_class_name_and_nothing_else(
        self, configured: Settings, stripe_fake: FakeStripe, error: Exception
    ) -> None:
        stripe_fake.serve(error)

        with capture_logs() as logs:
            await billing_service.price_check(configured)  # and does not raise

        assert logs == [failed_line(type(error).__name__)]
        # The message is not in it, nor the key, nor what the message carried.
        for secret in (str(error), KEY, DETAIL, "10.0.0.7", "req_PRICECHECKREQUESTSENTINEL"):
            assert secret not in repr(logs)

    @pytest.mark.parametrize(
        "returned",
        [
            None,
            "price_test_fake_150",
            a_price(amount="15000"),
            a_price(amount=15000.0),
            a_price(amount=True),
            a_price(currency=840),
            a_price(interval=["month"]),
            a_price(interval_count="1"),
            a_price(active="true"),
            a_price(active=1),
            a_price(usage_type=7),
        ],
        ids=[
            "nothing", "a-string", "amount-as-text", "amount-as-float", "amount-as-bool",
            "currency-as-number", "interval-as-list", "count-as-text", "active-as-text",
            "active-as-number", "usage-as-number",
        ],
    )
    async def test_a_price_that_is_not_shaped_like_one_is_a_failure_not_a_mismatch(
        self, configured: Settings, stripe_fake: FakeStripe, returned: object
    ) -> None:
        stripe_fake.serve(returned)

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        # Nothing odd is copied into the line: only the class that names the problem.
        assert logs == [failed_line("MalformedPriceError")]

    async def test_a_call_that_never_returns_is_a_timeout_after_its_own_bound(
        self,
        configured: Settings,
        stripe_fake: FakeStripe,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(billing_service, "PRICE_CHECK_TIMEOUT_S", 0.05)
        stripe_fake.hang = True

        with capture_logs() as logs:
            await asyncio.wait_for(billing_service.price_check(configured), WATCHDOG)

        assert logs == [failed_line("TimeoutError")]
        assert stripe_fake.cancelled, "the hung call was left running"

    async def test_building_the_client_can_fail_too(
        self, configured: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(settings: Settings) -> Any:  # noqa: ARG001
            raise ValueError(f"cannot build a client {KEY}")

        monkeypatch.setattr(billing_service, "_client", broken)

        with capture_logs() as logs:
            await billing_service.price_check(configured)

        assert logs == [failed_line("ValueError")]

    async def test_cancelling_the_check_is_not_swallowed(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        """"Never raises" is about what Stripe can do to it. A shutdown that cancels the task still
        has to stop it, and a check that caught the cancellation would outlive it."""
        stripe_fake.hang = True

        with capture_logs() as logs:
            task = asyncio.create_task(billing_service.price_check(configured))
            await _until(lambda: stripe_fake.calls == [PRICE_ID])
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()
        assert logs == []


# ---------------------------------------------------------------------------
# not configured: nothing happens
# ---------------------------------------------------------------------------


class TestNotConfigured:
    @pytest.mark.parametrize(
        "update",
        [
            {"stripe_secret_key": None, "stripe_price_id": None},
            {"stripe_secret_key": SecretStr(KEY), "stripe_price_id": None},
            {"stripe_secret_key": None, "stripe_price_id": PRICE_ID},
        ],
        ids=["neither", "a-key-and-no-price", "a-price-and-no-key"],
    )
    async def test_no_call_to_stripe_and_no_line(
        self,
        settings: Settings,
        stripe_fake: FakeStripe,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
        update: dict[str, Any],
    ) -> None:
        unconfigured = settings.model_copy(update=update)
        caplog.set_level("DEBUG")

        with capture_logs() as logs:
            await billing_service.price_check(unconfigured)

        assert logs == []
        assert stripe_fake.clients_built == []
        assert stripe_fake.calls == []
        # Not on stdout and not through stdlib logging either.
        assert "billing" not in capsys.readouterr().out
        assert not [r for r in caplog.records if "billing" in r.getMessage() or "stripe" in r.name]

    def test_it_is_configured_exactly_when_the_scan_allowance_gate_is_on(
        self, settings: Settings
    ) -> None:
        """One meaning of "Stripe is configured" in the API (`scan_allowance.gate_enabled`)."""
        for key in (None, SecretStr(KEY)):
            for price in (None, PRICE_ID):
                s = settings.model_copy(update={"stripe_secret_key": key, "stripe_price_id": price})
                assert billing_service.is_configured(s) is scan_allowance.gate_enabled(s)
                assert billing_service.is_configured(s) is (key is not None and price is not None)


# ---------------------------------------------------------------------------
# half configured: a deployed API with one of the two settings says so
# ---------------------------------------------------------------------------


class TestHalfConfigured:
    """Billing is on only when BOTH settings are set, so with one set a deployed API has its
    billing gates off for every agency and nothing else says so (the L2 security review). With
    both set and no webhook secret customers can pay and every webhook is refused, so a payment
    is never recorded (L5)."""

    @pytest.mark.parametrize("environment", DEPLOYED)
    @pytest.mark.parametrize(("update", "missing"), HALVES)
    def test_a_deployed_api_with_one_of_the_two_names_the_one_that_is_missing(
        self, settings: Settings, environment: str, update: dict[str, Any], missing: str
    ) -> None:
        deployed = settings.model_copy(update={"environment": environment, **update})

        with capture_logs() as logs:
            billing_service.log_half_configured(deployed)

        assert logs == [half_line(missing)]

    @pytest.mark.parametrize("environment", DEPLOYED)
    def test_both_set_without_the_webhook_secret_names_the_webhook_secret(
        self, settings: Settings, environment: str
    ) -> None:
        deployed = settings.model_copy(
            update={"environment": environment, **BOTH_WITHOUT_WEBHOOK}
        )

        with capture_logs() as logs:
            billing_service.log_half_configured(deployed)

        assert logs == [half_line("STRIPE_WEBHOOK_SECRET")]

    @pytest.mark.parametrize("environment", DEPLOYED)
    @pytest.mark.parametrize("update", [BOTH, NEITHER], ids=["all-three", "none"])
    def test_billing_set_up_whole_is_the_price_checks_and_none_of_it_says_nothing(
        self, settings: Settings, environment: str, update: dict[str, Any]
    ) -> None:
        deployed = settings.model_copy(update={"environment": environment, **update})

        with capture_logs() as logs:
            billing_service.log_half_configured(deployed)

        assert logs == []

    @pytest.mark.parametrize("environment", DEPLOYED)
    def test_the_webhook_secret_alone_is_not_billing_and_says_nothing(
        self, settings: Settings, environment: str
    ) -> None:
        deployed = settings.model_copy(
            update={"environment": environment, **NEITHER,
                    "stripe_webhook_secret": SecretStr(WEBHOOK_SECRET)}
        )

        with capture_logs() as logs:
            billing_service.log_half_configured(deployed)

        assert logs == []

    @pytest.mark.parametrize(("update", "missing"), HALVES)
    def test_a_missing_key_or_price_id_is_named_before_the_webhook_secret(
        self, settings: Settings, update: dict[str, Any], missing: str
    ) -> None:
        """One line, and the one that explains most: with the key or the Price id missing,
        billing is off and a webhook secret has nothing to do yet. Whether the webhook secret is
        set makes no difference to what is said."""
        for webhook in (None, SecretStr(WEBHOOK_SECRET)):
            deployed = settings.model_copy(
                update={"environment": "production", **update, "stripe_webhook_secret": webhook}
            )

            with capture_logs() as logs:
                billing_service.log_half_configured(deployed)

            assert logs == [half_line(missing)]

    @pytest.mark.parametrize("environment", LOCAL)
    @pytest.mark.parametrize(
        "update",
        [BOTH, BOTH_WITHOUT_WEBHOOK, NEITHER, HALVES[0].values[0], HALVES[1].values[0]],
        ids=["all-three", "no-webhook-secret", "none", "a-key-only", "a-price-id-only"],
    )
    def test_a_local_or_test_api_says_nothing_in_any_combination(
        self, settings: Settings, environment: str, update: dict[str, Any]
    ) -> None:
        """A half-set pair is ordinary on a developer's machine; the suite leaves Stripe unset."""
        local = settings.model_copy(update={"environment": environment, **update})

        with capture_logs() as logs:
            billing_service.log_half_configured(local)

        assert logs == []

    @pytest.mark.parametrize(
        ("update", "missing"),
        [*HALVES, pytest.param(BOTH_WITHOUT_WEBHOOK, "STRIPE_WEBHOOK_SECRET", id="no-webhook")],
    )
    def test_the_line_names_the_variable_and_carries_no_value(
        self,
        settings: Settings,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
        update: dict[str, Any],
        missing: str,
    ) -> None:
        caplog.set_level("DEBUG")
        deployed = settings.model_copy(update={"environment": "production", **update})

        with capture_logs() as events:
            billing_service.log_half_configured(deployed)
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text

        (line,) = events
        # Three fields and no more: the event, its level, and which variable is not set.
        assert set(line) == {"event", "log_level", "missing"}
        assert line["log_level"] == "error"
        # The key, the Price id and the webhook secret are the values the line must not carry,
        # whichever of them is set.
        assert KEY not in everything
        assert KEY.removeprefix("sk_test_") not in everything
        assert PRICE_ID not in everything
        assert WEBHOOK_SECRET not in everything
        assert WEBHOOK_SECRET.removeprefix("whsec_") not in everything


# ---------------------------------------------------------------------------
# the key never reaches a log
# ---------------------------------------------------------------------------


class TestTheKeyIsInNoLine:
    async def test_whatever_stripe_says(
        self,
        configured: Settings,
        stripe_fake: FakeStripe,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Every outcome, with the key sitting in the error text where a real Stripe error can put
        it (an authentication error quotes the key it refused), searched in all three channels the
        suite knows: structlog's events, stdout, and stdlib logging."""
        caplog.set_level("DEBUG")
        outcomes: list[object] = [
            a_price(),
            a_price(amount=16000),
            stripe.AuthenticationError(f"Invalid API Key provided: {KEY}", http_status=401),
            stripe.APIConnectionError(f"could not reach the host with {KEY} {DETAIL}"),
            a_price(amount="15000"),
        ]

        with capture_logs() as events:
            for outcome in outcomes:
                stripe_fake.serve(outcome)
                await billing_service.price_check(configured)
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text

        assert [e["event"] for e in events] == [
            "billing.price_check",
            "billing.price_mismatch",
            "billing.price_check_failed",
            "billing.price_check_failed",
            "billing.price_check_failed",
        ]
        assert KEY not in everything
        assert KEY.removeprefix("sk_test_") not in everything
        assert DETAIL not in everything


# ---------------------------------------------------------------------------
# the loop: now, and again after every interval
# ---------------------------------------------------------------------------


class TestTheHourlyLoop:
    async def test_it_checks_at_once_and_again_after_every_interval(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        with capture_logs() as logs:
            task = asyncio.create_task(price_watch.watch_price(configured, interval_s=0.02))
            try:
                await _until(lambda: len(stripe_fake.calls) >= 3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()
        assert stripe_fake.calls[:3] == [PRICE_ID] * 3
        # One line per check, each the same: nothing is logged between checks.
        assert logs and all(entry == OK_LINE for entry in logs)
        assert len(logs) == len(stripe_fake.calls)

    async def test_it_does_not_check_again_before_the_interval_is_up(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        task = asyncio.create_task(price_watch.watch_price(configured, interval_s=60.0))
        try:
            await _until(lambda: len(stripe_fake.calls) == 1)  # the check at start
            await asyncio.sleep(0.3)
            assert len(stripe_fake.calls) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_a_failure_does_not_end_it_and_the_next_check_reads_the_new_price(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        """The founder creates the $150 Price and points `STRIPE_PRICE_ID` at it: the warnings
        stop, and what the loop says next is the all-clear."""
        stripe_fake.serve(
            stripe.APIConnectionError(f"down {DETAIL}"),
            a_price(amount=16000),
            a_price(),
        )

        with capture_logs() as logs:
            task = asyncio.create_task(price_watch.watch_price(configured, interval_s=0.01))
            try:
                await _until(lambda: len(logs) >= 4)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert logs[:4] == [
            failed_line("APIConnectionError"),
            mismatch_line(amount=16000),
            OK_LINE,
            OK_LINE,
        ]

    async def test_only_cancellation_ends_it(
        self, configured: Settings, stripe_fake: FakeStripe
    ) -> None:
        task = asyncio.create_task(price_watch.watch_price(configured, interval_s=0.01))
        await asyncio.sleep(0.1)
        assert not task.done()

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()

    def test_the_default_interval_is_an_hour(self) -> None:
        """"At most once an hour": 3600 s, and both places it is used say so."""
        assert price_watch.CHECK_INTERVAL_S == 3600.0
        default = inspect.signature(price_watch.watch_price).parameters["interval_s"].default
        assert default == price_watch.CHECK_INTERVAL_S


class TestStartAndStop:
    async def test_nothing_is_started_when_stripe_is_not_configured(
        self, settings: Settings, stripe_fake: FakeStripe
    ) -> None:
        unconfigured = settings.model_copy(
            update={"environment": "production", "stripe_secret_key": None,
                    "stripe_price_id": None}
        )

        assert price_watch.start(unconfigured) is None
        await asyncio.sleep(0.05)
        assert _price_tasks() == []
        assert stripe_fake.clients_built == []

    @pytest.mark.parametrize("environment", LOCAL)
    async def test_a_local_or_test_api_never_starts_the_check_though_stripe_is_configured(
        self, configured: Settings, stripe_fake: FakeStripe, environment: str
    ) -> None:
        """A developer's `.env` holds a test key and a Price id, and a start on a laptop must not
        reach Stripe from it (the stripe-billing-ops skill's first gate): no task, no client, no
        call, no line."""
        local = configured.model_copy(update={"environment": environment})

        with capture_logs() as logs:
            assert price_watch.start(local) is None
            await asyncio.sleep(0.05)

        assert _price_tasks() == []
        assert stripe_fake.clients_built == []
        assert stripe_fake.calls == []
        assert logs == []

    @pytest.mark.parametrize("environment", DEPLOYED)
    async def test_a_deployed_api_with_stripe_configured_starts_the_check(
        self, deployed: Settings, stripe_fake: FakeStripe, environment: str
    ) -> None:
        deployed = deployed.model_copy(update={"environment": environment})

        with capture_logs() as logs:
            task = price_watch.start(deployed)
            assert task is not None
            try:
                await _until(lambda: logs == [OK_LINE])
            finally:
                await price_watch.stop(task)

        assert stripe_fake.calls == [PRICE_ID]

    async def test_a_named_task_is_started_when_it_is_configured_and_deployed(
        self, deployed: Settings, stripe_fake: FakeStripe
    ) -> None:
        with capture_logs() as logs:
            task = price_watch.start(deployed)
            assert task is not None
            try:
                assert _price_tasks() == [task]
                await _until(lambda: logs == [OK_LINE])
            finally:
                await price_watch.stop(task)

        assert task.cancelled()
        assert _price_tasks() == []

    async def test_the_interval_is_read_when_the_task_starts(
        self, deployed: Settings, stripe_fake: FakeStripe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So a test can shorten it for a whole app; without this the first test of the app
        waits an hour for its second check."""
        monkeypatch.setattr(price_watch, "CHECK_INTERVAL_S", 0.02)

        with capture_logs():
            task = price_watch.start(deployed)
            assert task is not None
            try:
                await _until(lambda: len(stripe_fake.calls) >= 3)
            finally:
                await price_watch.stop(task)

    async def test_a_second_stop_and_a_stop_with_nothing_are_harmless(
        self, deployed: Settings, stripe_fake: FakeStripe
    ) -> None:
        task = price_watch.start(deployed)
        assert task is not None

        await price_watch.stop(task)
        await price_watch.stop(task)
        await price_watch.stop(None)

        assert task.cancelled()

    async def test_stop_does_not_wait_for_ever_on_a_check_that_will_not_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`price_check` gives up its cancellation to nobody, but a shutdown that waited for a
        task that did would never finish: the pools would stay open and the deploy would hang.
        `stop` waits `STOP_TIMEOUT_S` and then lets go."""
        monkeypatch.setattr(price_watch, "STOP_TIMEOUT_S", 0.05)
        release = asyncio.Event()

        async def stubborn() -> None:
            while True:
                try:
                    await release.wait()
                    return
                except asyncio.CancelledError:
                    continue  # told to stop, and does not

        task = asyncio.create_task(stubborn())
        await asyncio.sleep(0)  # let it start waiting
        # `stop` runs as a task of its own and is waited on with `asyncio.wait`, which gives up
        # after the watchdog: `wait_for` would cancel it and then wait for ever for the answer,
        # so a `stop` that never returns would hang this test instead of failing it.
        stopping = asyncio.create_task(price_watch.stop(task))
        try:
            finished, _ = await asyncio.wait({stopping}, timeout=WATCHDOG)

            assert finished, "`stop` is still waiting on a task that ignores its cancellation"
            assert not task.done(), "the premise: this task ignored the cancellation"
        finally:
            release.set()
            await asyncio.gather(stopping, task, return_exceptions=True)

    def test_stop_waits_a_bounded_few_seconds(self) -> None:
        assert 0 < price_watch.STOP_TIMEOUT_S <= 10

    async def test_a_check_that_fails_as_it_is_stopped_is_not_reported_again_at_collection(
        self,
    ) -> None:
        """`stop` reads the outcome of a task that ended in an error while it was being cancelled
        (`cancel()` already marks a task that was over), so that failure is not left for the
        event loop to report, as "Task exception was never retrieved", when the task is
        collected."""
        loop = asyncio.get_running_loop()
        reports: list[dict[str, Any]] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: reports.append(context))
        try:

            async def fails_on_the_way_out() -> None:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    raise RuntimeError("it failed while stopping") from None

            task = asyncio.create_task(fails_on_the_way_out())
            await asyncio.sleep(0)  # let it start waiting

            await price_watch.stop(task)

            assert task.done()
            assert not task.cancelled(), "the premise: it ended in an error, not in a cancellation"
            del task
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous)

        assert reports == []

    def test_the_loop_makes_no_stripe_call_of_its_own(self) -> None:
        """Stripe calls stay in `services/billing.py`: the loop only calls `price_check`."""
        source = (SRC / "price_watch.py").read_text()
        assert not re.search(r"^\s*(import|from) stripe\b", source, re.M)
        assert "retrieve_async" not in source
        assert "_client" not in source


# ---------------------------------------------------------------------------
# through the lifespan, the real start-up hook
# ---------------------------------------------------------------------------


@pytest.fixture
def lifespan_without_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main.lifespan` with the engine, the Redis pool and the Redis watcher left out: this file
    is about the price check, and what the lifespan does for Postgres and Redis has its own
    tests (`test_redis_watch.py`, `test_memory_stats.py`)."""

    async def no_watcher(settings: Settings) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(main, "dispose_engine", _nothing)
    monkeypatch.setattr(main, "close_redis", _nothing)
    monkeypatch.setattr(redis_watch, "start", no_watcher)


def _bare_app(settings: Settings) -> Any:
    """What `main.lifespan` reads of an app: `app.state.settings`, and nothing else.

    Not `create_app`: it also configures structlog's level for the environment (INFO when
    deployed), and these tests need a deployed environment without leaving that behind for every
    test that runs after them.
    """
    return SimpleNamespace(state=SimpleNamespace(settings=settings))


@pytest.mark.usefixtures("lifespan_without_pools")
class TestTheLifespan:
    async def test_the_app_starts_while_stripe_does_not_answer_and_shutdown_cancels_the_check(
        self, deployed: Settings, stripe_fake: FakeStripe
    ) -> None:
        stripe_fake.hang = True
        lifespan = main.lifespan(_bare_app(deployed))

        with capture_logs() as logs:
            try:
                await asyncio.wait_for(lifespan.__aenter__(), WATCHDOG)
            except TimeoutError:
                pytest.fail("the start is waiting for the price check")
            # The start is over; the check is out at Stripe, in its own task, and has not come back.
            await _until(lambda: stripe_fake.calls == [PRICE_ID])
            (task,) = _price_tasks()
            assert not task.done()
            assert not stripe_fake.cancelled

            await asyncio.wait_for(lifespan.__aexit__(None, None, None), WATCHDOG)

        assert task.cancelled()
        assert stripe_fake.cancelled
        assert _price_tasks() == []
        # A check that never answered said nothing.
        assert _billing(logs) == []

    async def test_the_first_check_runs_at_start(
        self, deployed: Settings, stripe_fake: FakeStripe
    ) -> None:
        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                await _until(lambda: _billing(logs) == [OK_LINE])

        assert stripe_fake.calls == [PRICE_ID]

    async def test_a_mismatch_at_start_is_a_warning_and_the_app_starts_anyway(
        self, deployed: Settings, stripe_fake: FakeStripe
    ) -> None:
        stripe_fake.serve(a_price(amount=16000))
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                reached = True
                await _until(lambda: _billing(logs) == [mismatch_line(amount=16000)])

        assert reached

    async def test_a_stripe_error_at_start_is_a_warning_and_the_app_starts_anyway(
        self, deployed: Settings, stripe_fake: FakeStripe
    ) -> None:
        stripe_fake.serve(stripe.AuthenticationError(f"Invalid API Key provided: {KEY}"))
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                reached = True
                await _until(lambda: _billing(logs) == [failed_line("AuthenticationError")])

        assert reached
        assert KEY not in repr(logs)

    async def test_the_hourly_check_runs_in_the_running_app(
        self, deployed: Settings, stripe_fake: FakeStripe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(price_watch, "CHECK_INTERVAL_S", 0.02)

        with capture_logs():
            async with main.lifespan(_bare_app(deployed)):
                await _until(lambda: len(stripe_fake.calls) >= 3)

    @pytest.mark.parametrize("environment", LOCAL)
    async def test_a_local_app_with_stripe_configured_makes_no_call_and_writes_no_line(
        self, configured: Settings, stripe_fake: FakeStripe, environment: str
    ) -> None:
        """The L1 case through the real start-up hook: a key and a Price id in a developer's
        `.env`, and the API started on their machine. Nothing reaches Stripe."""
        local = configured.model_copy(update={"environment": environment, **BOTH})
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(local)):
                reached = True
                await asyncio.sleep(0.05)
                assert _price_tasks() == []

        assert reached
        assert stripe_fake.clients_built == []
        assert stripe_fake.calls == []
        assert _billing(logs) == []

    async def test_not_configured_the_app_starts_with_no_task_no_call_and_no_line(
        self, settings: Settings, stripe_fake: FakeStripe
    ) -> None:
        unconfigured = settings.model_copy(
            update={"environment": "production", **NEITHER}
        )
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(unconfigured)):
                reached = True
                await asyncio.sleep(0.05)
                assert _price_tasks() == []

        assert reached
        assert stripe_fake.clients_built == []
        assert _billing(logs) == []

    async def test_the_check_is_cancelled_before_the_pools_close(
        self,
        deployed: Settings,
        stripe_fake: FakeStripe,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The task is never mid-call on a pool that has been closed, and its cancellation leaves
        nothing behind: no task destroyed while pending, no exception nobody retrieved."""
        stripe_fake.hang = True
        order: list[str] = []

        def alive() -> bool:
            return any(not t.done() for t in _price_tasks())

        async def closing_engine() -> None:
            order.append(f"engine disposed, check alive={alive()}")

        async def closing_redis() -> None:
            order.append(f"redis closed, check alive={alive()}")

        monkeypatch.setattr(main, "dispose_engine", closing_engine)
        monkeypatch.setattr(main, "close_redis", closing_redis)
        loop = asyncio.get_running_loop()
        reports: list[dict[str, Any]] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: reports.append(context))
        try:
            # A watchdog on the whole body: a check that ignored its cancellation would otherwise
            # make the shutdown wait for it for ever.
            with capture_logs():
                async with asyncio.timeout(WATCHDOG), main.lifespan(_bare_app(deployed)):
                    await _until(lambda: stripe_fake.calls == [PRICE_ID])
                    running = _price_tasks()
                    assert len(running) == 1
                    watcher = running[0]

            assert watcher.cancelled()
            assert _price_tasks() == []
            # A task destroyed while pending, or an exception nobody retrieved, is reported when
            # the task is collected: collect it, and give the loop a turn to deliver the report.
            del running, watcher
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous)

        assert reports == []
        assert order == [
            "engine disposed, check alive=False",
            "redis closed, check alive=False",
        ]


@pytest.mark.usefixtures("lifespan_without_pools")
class TestTheLifespanSaysWhenBillingIsHalfConfigured:
    @pytest.mark.parametrize(("update", "missing"), HALVES)
    async def test_a_deployed_half_configured_app_logs_the_error_once_and_starts_anyway(
        self,
        settings: Settings,
        stripe_fake: FakeStripe,
        update: dict[str, Any],
        missing: str,
    ) -> None:
        deployed = settings.model_copy(update={"environment": "production", **update})
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                reached = True
                await asyncio.sleep(0.05)  # long enough for anything else to have said its piece
                assert _price_tasks() == []

        assert reached
        # Once, and nothing else about billing: no price check runs on half a configuration.
        assert _billing(logs) == [half_line(missing)]
        assert stripe_fake.clients_built == []

    @pytest.mark.parametrize("environment", LOCAL)
    @pytest.mark.parametrize(("update", "missing"), HALVES)
    async def test_a_local_half_configured_app_says_nothing(
        self,
        settings: Settings,
        stripe_fake: FakeStripe,
        environment: str,
        update: dict[str, Any],
        missing: str,
    ) -> None:
        local = settings.model_copy(update={"environment": environment, **update})

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(local)):
                await asyncio.sleep(0.05)

        assert _billing(logs) == []

    async def test_a_deployed_app_with_billing_set_up_whole_runs_the_price_check_and_not_the_error(
        self, deployed: Settings, stripe_fake: FakeStripe
    ) -> None:
        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                await _until(lambda: _billing(logs) == [OK_LINE])
                await asyncio.sleep(0.05)

        assert _billing(logs) == [OK_LINE]

    async def test_a_deployed_app_without_the_webhook_secret_says_so_and_still_checks_the_price(
        self, settings: Settings, stripe_fake: FakeStripe
    ) -> None:
        """Customers could pay and every webhook would be refused: the error, once, at start,
        and then the price check as usual. It names the variable and carries no value."""
        deployed = settings.model_copy(update={"environment": "production", **BOTH_WITHOUT_WEBHOOK})
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                reached = True
                await _until(lambda: len(_billing(logs)) == 2)
                await asyncio.sleep(0.05)

        assert reached
        assert _billing(logs) == [half_line("STRIPE_WEBHOOK_SECRET"), OK_LINE]
        assert WEBHOOK_SECRET not in repr(logs)

    async def test_a_deployed_app_with_neither_set_says_nothing(
        self, settings: Settings, stripe_fake: FakeStripe
    ) -> None:
        deployed = settings.model_copy(update={"environment": "production", **NEITHER})

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                await asyncio.sleep(0.05)
                assert _price_tasks() == []

        assert _billing(logs) == []
        assert stripe_fake.clients_built == []

    async def test_a_failure_in_that_check_cannot_stop_the_start(
        self, deployed: Settings, stripe_fake: FakeStripe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(settings: Settings) -> None:  # noqa: ARG001
            raise RuntimeError(f"it went wrong {KEY}")

        monkeypatch.setattr(billing_service, "log_half_configured", boom)
        reached = False

        with capture_logs() as logs:
            async with main.lifespan(_bare_app(deployed)):
                reached = True
                # The rest of the start went on: the price check still runs.
                await _until(lambda: _billing(logs) == [OK_LINE])

        assert reached
        assert KEY not in repr(logs)

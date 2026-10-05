"""Checkout and the price check (launch checklist 3.74, founder decision 2026-10-05).

Checkout refuses only when the latest price check CONFIRMED a mismatch (amount, currency,
interval or an archived Price) and a fresh read, made right there, still confirms it. A check
that has not run, an `ok`, and a read that fails or times out never block. Pinned here:

* no check yet, and a latest `ok`: checkout proceeds and reads no Price;
* a recorded mismatch and a fresh read that still differs (each of amount, currency, interval,
  archived): 503 `plan-price-mismatch` with one plain sentence, one ERROR line carrying
  `agency_id` and `price_id` only, and no customer, list, expire or session at Stripe;
* a recorded mismatch and a fresh read that is now the plan, or fails, or times out: proceeds;
* a failed read after a mismatch LEAVES the record (the next checkout reads again); an ok read
  clears it, and so does Stripe being unconfigured;
* the fresh read is made before the agency row lock, and an agency that is already subscribed
  (or needs attention) hears its 409, not the 503;
* `price_check`'s log lines carry `trigger`: `watch` from the hourly task, `checkout` from here.

Stripe is a fake: `GuardStub` is `test_billing.py`'s stub with the one Price read added.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from httpx import AsyncClient

from avp_api.services import billing as billing_service

# Fixtures and helpers from test_billing.py. The two autouse guards are imported so they apply here.
from test_billing import (  # noqa: F401
    BASE,
    FAKE_PRICE_ID,
    FAKE_SECRET_KEY,
    _fake_stripe_settings,
    _no_unstubbed_stripe,
    _sign_up,
    _StripeStub,
)

WATCHDOG = 15.0
SENTENCE = (
    "Subscribing is not available right now because the plan's price is not set up "
    "correctly. Please try again later."
)


def a_price(**over: Any) -> SimpleNamespace:
    terms: dict[str, Any] = {
        "amount": 15000,
        "currency": "usd",
        "interval": "month",
        "interval_count": 1,
        "active": True,
        "usage_type": "licensed",
    }
    terms.update(over)
    return SimpleNamespace(
        id=FAKE_PRICE_ID,
        active=terms["active"],
        unit_amount=terms["amount"],
        currency=terms["currency"],
        recurring=SimpleNamespace(
            interval=terms["interval"],
            interval_count=terms["interval_count"],
            usage_type=terms["usage_type"],
        ),
    )


MISMATCHES = [
    pytest.param(a_price(amount=9900), id="amount"),
    pytest.param(a_price(currency="eur"), id="currency"),
    pytest.param(a_price(interval="year"), id="interval"),
    pytest.param(a_price(active=False), id="archived"),
]


class GuardStub(_StripeStub):
    """The checkout stub plus `prices.retrieve_async`; `outcomes` as in `test_price_check.py`."""

    def __init__(self) -> None:
        super().__init__()
        self.outcomes: list[object] = [a_price()]
        self.price_reads = 0

    @property
    def v1(self) -> Any:
        base = super().v1
        stub = self

        class _Prices:
            async def retrieve_async(self, price: str, params: Any = None) -> Any:  # noqa: ARG002
                stub.price_reads += 1
                stub.call_log.append("prices.retrieve")
                outcome = stub.outcomes.pop(0) if len(stub.outcomes) > 1 else stub.outcomes[0]
                if isinstance(outcome, BaseException):
                    raise outcome
                if outcome == "hang":
                    await asyncio.sleep(3600)
                return outcome

        base.prices = _Prices()
        return base


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> GuardStub:
    s = GuardStub()
    monkeypatch.setattr(billing_service, "_client", lambda settings: s)  # noqa: ARG005
    billing_service.reset_price_check_record()
    yield s
    billing_service.reset_price_check_record()


async def _record(settings: Any, stub: GuardStub, *outcomes: object) -> None:
    """Run `price_check` against the next outcome and leave its record behind."""
    stub.outcomes = list(outcomes)
    await billing_service.price_check(settings)
    stub.price_reads = 0
    stub.call_log.clear()


async def _checkout(client: AsyncClient, agency_id: str | None = None) -> tuple[Any, str, Any]:
    if agency_id is None:
        agency_id = (await _sign_up(client))["agency"]["id"]
    with structlog.testing.capture_logs() as events:
        resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    return resp, agency_id, events


def _nothing_created(stub: GuardStub) -> None:
    assert stub.customers_created == []
    assert stub.sessions_created == []
    assert stub.sessions_listed == []
    assert stub.sessions_expired == []
    assert stub.subscriptions_listed == []


async def test_with_no_check_yet_checkout_proceeds_and_reads_no_price(
    client: AsyncClient, stub: GuardStub
) -> None:
    resp, _, _ = await _checkout(client)
    assert resp.status_code == 201, resp.text
    assert stub.price_reads == 0
    assert len(stub.sessions_created) == 1


async def test_after_an_ok_check_checkout_proceeds_and_reads_no_price(
    client: AsyncClient, stub: GuardStub, settings
) -> None:  # noqa: ANN001
    await _record(settings, stub, a_price())
    resp, _, _ = await _checkout(client)
    assert resp.status_code == 201, resp.text
    assert stub.price_reads == 0


@pytest.mark.parametrize("price", MISMATCHES)
async def test_a_confirmed_mismatch_refuses_checkout_before_any_stripe_write(
    client: AsyncClient, stub: GuardStub, settings, price: Any
) -> None:  # noqa: ANN001
    await _record(settings, stub, price)
    resp, agency_id, events = await _checkout(client)

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["type"].endswith("/problems/plan-price-mismatch")
    assert body["detail"] == SENTENCE
    assert stub.price_reads == 1  # the fresh read, and only that
    assert stub.call_log == ["prices.retrieve"]
    _nothing_created(stub)

    lines = [e for e in events if e["event"] == "billing.checkout_refused_price_mismatch"]
    assert len(lines) == 1, events
    assert lines[0]["log_level"] == "error"
    assert set(lines[0]) == {"event", "log_level", "agency_id", "price_id"}
    assert lines[0]["agency_id"] == agency_id
    assert lines[0]["price_id"] == FAKE_PRICE_ID
    everything = json.dumps(events, default=str)
    assert FAKE_SECRET_KEY not in everything
    assert "@" not in everything


async def test_a_price_fixed_since_the_last_check_does_not_block(
    client: AsyncClient, stub: GuardStub, settings
) -> None:  # noqa: ANN001
    await _record(settings, stub, a_price(amount=9900))
    stub.outcomes = [a_price()]
    resp, agency_id, events = await _checkout(client)
    assert resp.status_code == 201, resp.text
    assert stub.price_reads == 1
    assert not [e for e in events if e["event"] == "billing.checkout_refused_price_mismatch"]
    # The fresh read updated the record: the next checkout reads nothing.
    assert billing_service.price_mismatch_confirmed() is False
    stub.price_reads = 0
    resp2, _, _ = await _checkout(client, agency_id)
    assert resp2.status_code == 201, resp2.text
    assert stub.price_reads == 0


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(RuntimeError("boom"), id="error"),
        pytest.param("hang", id="timeout"),
    ],
)
async def test_a_failed_fresh_read_never_blocks_checkout(
    client: AsyncClient, stub: GuardStub, settings, monkeypatch, failure: Any  # noqa: ANN001
) -> None:
    monkeypatch.setattr(billing_service, "PRICE_CHECK_TIMEOUT_S", 0.05)
    await _record(settings, stub, a_price(amount=9900))
    stub.outcomes = [failure]
    resp, _, events = await asyncio.wait_for(_checkout(client), WATCHDOG)
    assert resp.status_code == 201, resp.text
    assert stub.price_reads == 1
    assert not [e for e in events if e["event"] == "billing.checkout_refused_price_mismatch"]
    failed = [e for e in events if e["event"] == "billing.price_check_failed"]
    assert len(failed) == 1
    assert failed[0]["trigger"] == "checkout"
    # A failed read confirmed nothing, so the record stays and the next checkout reads again.
    assert billing_service.price_mismatch_confirmed() is True


async def test_a_failed_check_after_a_mismatch_leaves_the_record(
    stub: GuardStub, settings
) -> None:  # noqa: ANN001
    await _record(settings, stub, a_price(amount=9900))
    assert billing_service.price_mismatch_confirmed() is True
    await _record(settings, stub, RuntimeError("down"))
    assert billing_service.price_mismatch_confirmed() is True


async def test_a_failed_read_leaves_the_record_so_the_next_checkout_reads_again(
    client: AsyncClient, stub: GuardStub, settings
) -> None:  # noqa: ANN001
    await _record(settings, stub, a_price(amount=9900))
    stub.outcomes = [RuntimeError("down"), a_price(amount=9900)]
    resp, agency_id, _ = await _checkout(client)
    assert resp.status_code == 201, resp.text  # a failed read never blocks
    assert stub.price_reads == 1
    resp2, _, _ = await _checkout(client, agency_id)
    assert stub.price_reads == 2  # it read again
    assert resp2.status_code == 503, resp2.text  # and this time it confirmed


async def test_the_fresh_read_is_made_before_the_agency_row_is_locked(
    client: AsyncClient, stub: GuardStub, settings, monkeypatch  # noqa: ANN001
) -> None:
    await _record(settings, stub, a_price(amount=9900))
    seen: list[str] = []
    orig = billing_service.price_check

    async def spy(*a: Any, **k: Any) -> Any:
        seen.append("read")
        return await orig(*a, **k)

    monkeypatch.setattr(billing_service, "price_check", spy)
    from sqlalchemy.ext.asyncio import AsyncSession

    real_execute = AsyncSession.execute

    async def watch_execute(self: Any, statement: Any, *a: Any, **k: Any) -> Any:
        if "lock_timeout" in str(statement):
            seen.append("lock")
        return await real_execute(self, statement, *a, **k)

    monkeypatch.setattr(AsyncSession, "execute", watch_execute)
    resp, _, _ = await _checkout(client)
    assert resp.status_code == 503, resp.text
    assert seen == ["read", "lock"]


@pytest.mark.parametrize("status", ["active", "trialing", "past_due", "unpaid"])
async def test_a_subscribed_agency_hears_its_409_not_the_price_503(
    client: AsyncClient, engine, stub: GuardStub, settings, status: str  # noqa: ANN001
) -> None:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from avp_api.models import Agency

    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            update(Agency).where(Agency.id == agency_id).values(subscription_status=status)
        )
        await s.commit()
    await _record(settings, stub, a_price(amount=9900))
    resp, _, events = await _checkout(client, agency_id)
    assert resp.status_code == 409, resp.text
    assert not [e for e in events if e["event"] == "billing.checkout_refused_price_mismatch"]
    _nothing_created(stub)


async def test_an_ok_check_after_a_mismatch_clears_the_record(
    stub: GuardStub, settings
) -> None:  # noqa: ANN001
    await _record(settings, stub, a_price(active=False))
    assert billing_service.price_mismatch_confirmed() is True
    await _record(settings, stub, a_price())
    assert billing_service.price_mismatch_confirmed() is False


async def test_stripe_not_configured_clears_the_record(
    stub: GuardStub, settings, monkeypatch  # noqa: ANN001
) -> None:
    await _record(settings, stub, a_price(amount=9900))
    monkeypatch.setattr(settings, "stripe_price_id", None)
    await billing_service.price_check(settings)
    assert billing_service.price_mismatch_confirmed() is False


@pytest.mark.parametrize(
    ("trigger", "kwargs"),
    [("watch", {}), ("watch", {"trigger": "watch"}), ("checkout", {"trigger": "checkout"})],
)
async def test_every_line_carries_what_ran_the_check_and_the_call_returns_the_outcome(
    stub: GuardStub, settings, trigger: str, kwargs: dict[str, str]  # noqa: ANN001
) -> None:
    stub.outcomes = [a_price(), a_price(amount=9900), RuntimeError("x")]
    with structlog.testing.capture_logs() as events:
        results = [await billing_service.price_check(settings, **kwargs) for _ in range(3)]
    assert results == ["ok", "mismatch", None]
    assert [e["event"] for e in events] == [
        "billing.price_check",
        "billing.price_mismatch",
        "billing.price_check_failed",
    ]
    assert [e["trigger"] for e in events] == [trigger] * 3


async def test_a_checkout_that_reads_the_price_logs_trigger_checkout(
    client: AsyncClient, stub: GuardStub, settings
) -> None:  # noqa: ANN001
    await _record(settings, stub, a_price(amount=9900))
    _, _, events = await _checkout(client)
    lines = [e for e in events if e["event"] == "billing.price_mismatch"]
    assert [e["trigger"] for e in lines] == ["checkout"]

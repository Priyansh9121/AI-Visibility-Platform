"""Subscription billing — Epic 9.15.

NOTHING IN THIS FILE TOUCHES THE NETWORK, AND THAT IS ENFORCED RATHER THAN
INTENDED
--------------------------------------------------------------------------
Two guards, both autouse, because the developer's real `apps/api/.env` DOES
reach `Settings` in this suite — pydantic-settings reads the dotenv for any
field the fixture does not pass explicitly, and pytest runs with `apps/api` as
its working directory. A test-mode Stripe key is still a live credential
pointing at a real account, and a test that forgot to stub would quietly create
real customers in it.

  * `_fake_stripe_settings` pins obviously-fake values over whatever the dotenv
    supplied, so no real key is in play even if a stub is missed.
  * `_no_unstubbed_stripe` replaces the client factory with one that RAISES.
    Forgetting to stub is therefore a loud failure naming the problem, not a
    slow test and a stranger's row in the Stripe dashboard.

The webhook tests are the exception that proves the rule: they use the REAL
`stripe.Webhook.construct_event` against real HMAC signatures computed here.
That is pure cryptography with no I/O, and mocking it would have meant asserting
that our mock rejects what we told it to reject — which is the shape of test
that passes forever while the thing it names is broken.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import stripe
import structlog
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api.models import Agency, Invitation, StripeWebhookEvent, User
from avp_api.security import token_digest
from avp_api.services import billing as billing_service

# The real client factory, captured before the autouse guard replaces it.
_REAL_CLIENT = billing_service._client  # noqa: SLF001

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
MEMBER_PASSWORD = "an-entirely-different-passphrase"

FAKE_SECRET_KEY = "sk_test_not_a_real_key_for_tests_only"
FAKE_PRICE_ID = "price_test_fake"
FAKE_WEBHOOK_SECRET = "whsec_test_fake_signing_secret"

# A fixed instant, so "renews on" assertions do not drift with the clock.
PERIOD_END_TS = 1790000000  # 2026-09-21T14:13:20Z
PERIOD_END = datetime.fromtimestamp(PERIOD_END_TS, tz=UTC)

# Founder decision 4 (2026-10-01): these four statuses are live at Stripe, so a
# second checkout would create a second subscription. Written out here rather
# than imported from `services.billing`, so editing the code cannot quietly
# edit the decision these tests pin. `active` and `trialing` are the paid-up
# pair and keep `subscription-already-active`.
NEEDS_ATTENTION = ["incomplete", "past_due", "paused", "unpaid"]
# Security review L1: checkout DENIES BY DEFAULT. Only no subscription and the
# two ended statuses may start one, so a status Stripe does not have today is
# refused as well, and a status Stripe adds later cannot quietly let a second
# subscription through.
UNKNOWN_STATUS = "on_hold"
REFUSED_AS_NEEDS_ATTENTION = [*NEEDS_ATTENTION, UNKNOWN_STATUS]
ENDED = ["canceled", "incomplete_expired"]
NEEDS_ATTENTION_DETAIL = (
    "This agency already has a subscription that needs attention. "
    "Manage it from Manage billing in Settings."
)
# The ONE call checkout makes to ask Stripe about a customer's subscriptions:
# every status at once, a full page.
LIST_ALL_SUBSCRIPTIONS = {"customer": "cus_test_001", "status": "all", "limit": 100}
# Two different 503 sentences, so an operator can tell which step failed: asking
# Stripe about the customer's subscriptions, or clearing its open sessions.
SUBSCRIPTION_CHECK_FAILED_DETAIL = (
    "Could not check this agency's existing subscriptions at Stripe. "
    "Try again in a moment."
)
EXPIRE_FAILED_DETAIL = (
    "Could not clear the previous checkout session at Stripe. Try again in a moment."
)
# Founder decision 5 (2026-10-01): a Checkout link lives one hour. Stripe's own
# default is 24 hours, and it accepts 30 minutes to 24 hours. Written out here
# as seconds rather than imported from `services.billing`, for the same reason
# as the decision-4 values above.
CHECKOUT_LINK_SECONDS = 3600


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_stripe_settings(settings, monkeypatch):  # noqa: ANN001, ANN201
    """Pin fake Stripe settings over anything the developer's `.env` supplied.

    `monkeypatch.setattr` restores the originals after each test, so this does
    not leak into the rest of the suite.
    """
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr(FAKE_SECRET_KEY))
    monkeypatch.setattr(settings, "stripe_price_id", FAKE_PRICE_ID)
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr(FAKE_WEBHOOK_SECRET))


@pytest.fixture(autouse=True)
def _no_unstubbed_stripe(monkeypatch):  # noqa: ANN001, ANN201
    """Any un-stubbed reach for a Stripe client fails the test, loudly."""

    def _forbidden(settings: Any) -> Any:  # noqa: ARG001
        raise AssertionError(
            "This test reached for a real Stripe client. Use the `stripe_stub` "
            "fixture, or assert on a path that must not call Stripe at all."
        )

    monkeypatch.setattr(billing_service, "_client", _forbidden)


class _StripeStub:
    """The three Stripe calls this service makes, and a record of them.

    Deliberately not a `Mock`. What these tests assert is the PARAMETERS sent to
    Stripe — mode, price, metadata — and a recorded dict reads better in an
    assertion than a call-args tuple does.
    """

    def __init__(self) -> None:
        self.customers_created: list[dict[str, Any]] = []
        self.sessions_created: list[dict[str, Any]] = []
        self.portals_created: list[dict[str, Any]] = []
        self.next_customer_id = "cus_test_001"
        self.next_session_url = "https://checkout.stripe.test/c/pay/cs_test_001"
        self.next_portal_url = "https://billing.stripe.test/p/session/bps_test_001"
        self.session_url_override: str | None = ""
        # Open Checkout Sessions, as Stripe would report them, and the calls
        # made to list and expire them.
        self.open_sessions: list[str] = []
        self.sessions_listed: list[dict[str, Any]] = []
        self.sessions_expired: list[str] = []
        self.expire_error: Exception | None = None
        self.customer_delay = 0.0
        self.open_has_more = False
        # The customer's subscriptions at Stripe, per status, in the order they
        # are listed: e.g. {"active": ["sub_x"], "canceled": ["sub_old"]}. Ended
        # ones count: `status: "all"` returns them too.
        self.subscriptions_at_stripe: dict[str, list[str]] = {}
        # If set, listed verbatim instead: for shapes the per-status dict above
        # cannot express (a null status, or no status field at all).
        self.subscription_objects: list[Any] | None = None
        self.subscriptions_listed: list[dict[str, Any]] = []
        self.subscriptions_has_more = False
        self.list_subscriptions_error: Exception | None = None
        # Every Stripe call, in the order it was made: "<resource>.<verb>".
        self.call_log: list[str] = []
        # Called at the start of `checkout.sessions.list`, to stage what happened
        # at Stripe in the moment between two of our calls (a customer paying).
        self.on_sessions_list: Any = None

    # -- shape mirroring `client.v1.<resource>.<verb>_async` ----------------
    @property
    def v1(self) -> Any:
        stub = self

        class _Customers:
            async def create_async(self, params: dict[str, Any]) -> Any:
                stub.call_log.append("customers.create")
                stub.customers_created.append(params)
                n = len(stub.customers_created)
                if stub.customer_delay:
                    await asyncio.sleep(stub.customer_delay)
                cid = stub.next_customer_id if n == 1 else f"cus_test_{n:03d}"
                return SimpleNamespace(id=cid)

        class _Sessions:
            async def create_async(self, params: dict[str, Any]) -> Any:
                stub.call_log.append("checkout.sessions.create")
                stub.sessions_created.append(params)
                sid = f"cs_test_{len(stub.sessions_created):03d}"
                stub.open_sessions.append(sid)
                url = (
                    stub.next_session_url
                    if stub.session_url_override == ""
                    else stub.session_url_override
                )
                return SimpleNamespace(id=sid, url=url)

            async def list_async(self, params: dict[str, Any]) -> Any:
                stub.call_log.append("checkout.sessions.list")
                stub.sessions_listed.append(params)
                if stub.on_sessions_list is not None:
                    stub.on_sessions_list()
                return SimpleNamespace(
                    data=[SimpleNamespace(id=i) for i in stub.open_sessions],
                    has_more=stub.open_has_more,
                )

            async def expire_async(self, session: str, params: Any = None) -> Any:  # noqa: ARG002
                stub.call_log.append("checkout.sessions.expire")
                if stub.expire_error is not None:
                    raise stub.expire_error
                stub.sessions_expired.append(session)
                stub.open_sessions.remove(session)
                return SimpleNamespace(id=session, status="expired")

        class _PortalSessions:
            async def create_async(self, params: dict[str, Any]) -> Any:
                stub.call_log.append("billing_portal.sessions.create")
                stub.portals_created.append(params)
                return SimpleNamespace(id="bps_test_001", url=stub.next_portal_url)

        class _Subscriptions:
            async def list_async(self, params: dict[str, Any]) -> Any:
                stub.call_log.append("subscriptions.list")
                stub.subscriptions_listed.append(params)
                if stub.list_subscriptions_error is not None:
                    raise stub.list_subscriptions_error
                if stub.subscription_objects is not None:
                    return SimpleNamespace(
                        data=stub.subscription_objects, has_more=stub.subscriptions_has_more
                    )
                # As at Stripe: `"all"` is every status, any other value filters.
                wanted = params.get("status", "")
                data = [
                    SimpleNamespace(id=sub_id, status=status)
                    for status, ids in stub.subscriptions_at_stripe.items()
                    if wanted in ("all", status)
                    for sub_id in ids
                ]
                return SimpleNamespace(data=data, has_more=stub.subscriptions_has_more)

        return SimpleNamespace(
            subscriptions=_Subscriptions(),
            customers=_Customers(),
            checkout=SimpleNamespace(sessions=_Sessions()),
            billing_portal=SimpleNamespace(sessions=_PortalSessions()),
        )


@pytest.fixture
def stripe_stub(monkeypatch):  # noqa: ANN001, ANN201
    stub = _StripeStub()
    monkeypatch.setattr(billing_service, "_client", lambda settings: stub)  # noqa: ARG005
    return stub


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _sign_up(
    client: AsyncClient,
    *,
    email: str = "owner@billing.example",
    agency: str = "Billing Test Agency",
) -> dict:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": agency,
            "fullName": "Owner Person",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _sign_up_with_google(client: AsyncClient, engine, **kwargs: Any) -> dict:  # noqa: ANN001
    """`_sign_up`, then link a Google identity to the owner: a free trial needs one (2026-10-05).

    A password-only owner is the plain no-trial checkout; `test_billing_trial_identity.py`
    holds that rule. The trial tests in this file are about the OTHER rules (one trial per
    agency, Stripe's list, the setting), so their owner is the kind who may have one.
    """
    me = await _sign_up(client, **kwargs)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(User).where(User.id == me["user"]["id"]))).scalar_one()
        row.google_sub = f"sub-{row.id}"
        await s.commit()
    return me


async def _become_member(client: AsyncClient, engine, agency_id: str) -> None:  # noqa: ANN001
    """Invite a member, redeem the invitation, and end up signed in as them.

    Accepting sets a fresh session cookie on the same client, which replaces the
    owner's — so every call after this one is made as a member.
    """
    email = "member@billing.example"
    resp = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations",
        json={"email": email, "role": "member"},
    )
    assert resp.status_code == 201, resp.text

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    token = "test-token-member"
    async with factory() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(token)
        await s.commit()

    accepted = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Mem Ber", "password": MEMBER_PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["role"] == "member"


async def _agency(engine, agency_id: str) -> Agency:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        return (
            await s.execute(select(Agency).where(Agency.id == agency_id))
        ).scalar_one()


def _sign(
    payload: bytes, secret: str = FAKE_WEBHOOK_SECRET, *, timestamp: int | None = None
) -> str:
    """A genuine Stripe-Signature header for this exact payload.

    Stripe signs `"{timestamp}.{body}"` with HMAC-SHA256. Written out here
    rather than called from a private SDK helper, so that the test would still
    catch us if the SDK's verification ever stopped matching the documented
    scheme.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    signed = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


_event_seq = itertools.count(1)
EVENT_BASE_TS = 1780000000


def _event(
    event_type: str,
    obj: dict[str, Any],
    *,
    event_id: str | None = None,
    created: int | None = None,
) -> bytes:
    """A Stripe event body. Each call gets a fresh id and a later `created`.

    Unique ids because the webhook now dedupes by id: two deliveries that share
    one are, correctly, one event. Later `created` because an event older than
    the agency's stored state is ignored. Pass either explicitly to test those.
    """
    n = next(_event_seq)
    return json.dumps(
        {
            "id": event_id or f"evt_test_{n:04d}",
            "type": event_type,
            "created": created if created is not None else EVENT_BASE_TS + n * 10,
            "data": {"object": obj},
        }
    ).encode()


def _subscription(
    *,
    status: str = "active",
    sub_id: str = "sub_test_001",
    customer: str = "cus_test_001",
    agency_id: str | None = None,
    period_end: int | None = PERIOD_END_TS,
    legacy_period_end: bool = False,
    cancel_at_period_end: bool | None = None,
    trial_end: int | None = None,
) -> dict[str, Any]:
    """A Subscription payload shaped like the pinned API version sends one.

    `current_period_end` lives on the ITEM, not on the subscription — that is
    the change in `2026-08-26.dahlia` that would otherwise have made every
    renewal date silently absent. `legacy_period_end` produces the older shape,
    which replayed events and older endpoint API versions still deliver.

    `trial_end` is a top-level field, present (as Unix seconds) on a subscription
    that has or had a trial; left out here, the payload is exactly what it was
    before trials existed.
    """
    obj: dict[str, Any] = {
        "id": sub_id,
        "object": "subscription",
        "status": status,
        "customer": customer,
        "metadata": {"agency_id": agency_id} if agency_id else {},
    }
    if legacy_period_end:
        obj["current_period_end"] = period_end
        obj["items"] = {"data": [{"id": "si_test_001"}]}
    else:
        obj["items"] = {"data": [{"id": "si_test_001", "current_period_end": period_end}]}
    if cancel_at_period_end is not None:
        obj["cancel_at_period_end"] = cancel_at_period_end
    if trial_end is not None:
        obj["trial_end"] = trial_end
    return obj


async def _deliver(client: AsyncClient, payload: bytes, signature: str | None = "") -> Any:
    headers = {"Content-Type": "application/json"}
    if signature == "":
        signature = _sign(payload)
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return await client.post(f"{BASE}/billing/webhook", content=payload, headers=headers)


# ---------------------------------------------------------------------------
# checkout — the customer id is written once and reused
# ---------------------------------------------------------------------------


async def test_checkout_returns_a_url_and_records_the_customer(
    client: AsyncClient, engine, stripe_stub: _StripeStub
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"url": stripe_stub.next_session_url}

    agency = await _agency(engine, agency_id)
    assert agency.stripe_customer_id == "cus_test_001"
    # Creating a customer is not subscribing. Nothing else moved.
    assert agency.stripe_subscription_id is None
    assert agency.subscription_status is None


async def test_a_second_checkout_reuses_the_customer(
    client: AsyncClient, engine, stripe_stub: _StripeStub
) -> None:  # noqa: ANN001
    """Someone who opens checkout, thinks better of it, and returns tomorrow.

    That is the ordinary case, not an edge one, and a second customer would mean
    a second card and a second subscription — with the webhook updating whichever
    of them our column happened to point at.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    for _ in range(3):
        assert (
            await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
        ).status_code == 201

    assert len(stripe_stub.customers_created) == 1
    assert len(stripe_stub.sessions_created) == 3
    assert (await _agency(engine, agency_id)).stripe_customer_id == "cus_test_001"
    # Every session was created against the SAME customer.
    assert {s["customer"] for s in stripe_stub.sessions_created} == {"cus_test_001"}


async def test_checkout_asks_stripe_for_a_subscription_at_the_configured_price(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    me = await _sign_up(client)
    await client.post(f"{BASE}/agencies/{me['agency']['id']}/billing/checkout")

    params = stripe_stub.sessions_created[0]
    assert params["mode"] == "subscription"
    assert params["line_items"] == [{"price": FAKE_PRICE_ID, "quantity": 1}]


async def test_each_checkout_link_expires_an_hour_after_it_is_created(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """Founder decision 5: `expires_at` is now + 1 hour, as an int Unix timestamp.

    Stripe's own default is 24 hours. Two checkouts, because pressing Subscribe
    again expires the first link and makes a new one, and the new one must get a
    fresh hour of its own rather than carry over the first's.

    The clock is bracketed, not frozen, so the test does not care how the service
    reads it, and nothing is slept on. `int()` floors, so the stamp lies between
    the second before the request and the second after it, plus the hour. That is
    exact: there is no tolerance for a value computed once at import to hide in.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    for n in range(2):
        before = int(time.time())
        resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
        after = int(time.time())
        assert resp.status_code == 201, resp.text

        params = stripe_stub.sessions_created[n]
        expires_at = params.get("expires_at")
        # An int, not a float or a string, and absent is a failure, not a default.
        assert type(expires_at) is int, params
        assert before + CHECKOUT_LINK_SECONDS <= expires_at <= after + CHECKOUT_LINK_SECONDS

    # The second link was a new session, with the first one cleared away.
    assert len(stripe_stub.sessions_created) == 2
    assert stripe_stub.sessions_expired == ["cs_test_001"]


async def test_checkout_labels_everything_with_the_agency_id(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """Three objects, three events, each able to name its own agency.

    Without this the webhook can only resolve an agency by customer id, which
    fails for anything created before the column was written.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")

    params = stripe_stub.sessions_created[0]
    assert params["client_reference_id"] == agency_id
    assert params["metadata"]["agency_id"] == agency_id
    assert params["subscription_data"]["metadata"]["agency_id"] == agency_id
    assert stripe_stub.customers_created[0]["metadata"]["agency_id"] == agency_id


async def test_return_urls_come_from_configuration_not_the_request(
    client: AsyncClient, settings, stripe_stub: _StripeStub
) -> None:  # noqa: ANN001
    """`Host` is attacker-controlled; a success URL built from one is phishing."""
    me = await _sign_up(client)
    await client.post(
        f"{BASE}/agencies/{me['agency']['id']}/billing/checkout",
        headers={"Host": "evil.example"},
    )

    params = stripe_stub.sessions_created[0]
    assert params["success_url"].startswith(settings.public_web_base_url)
    assert params["cancel_url"].startswith(settings.public_web_base_url)
    assert "evil.example" not in params["success_url"]
    assert "evil.example" not in params["cancel_url"]


async def test_a_session_with_no_url_fails_rather_than_navigating_nowhere(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    me = await _sign_up(client)
    stripe_stub.session_url_override = None

    resp = await client.post(f"{BASE}/agencies/{me['agency']['id']}/billing/checkout")
    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "/problems/billing-not-configured"


# ---------------------------------------------------------------------------
# missing configuration names the variable, the way every provider key does
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "variable"),
    [
        ("stripe_secret_key", "STRIPE_SECRET_KEY"),
        ("stripe_price_id", "STRIPE_PRICE_ID"),
    ],
)
async def test_checkout_without_a_key_names_the_exact_variable(
    client: AsyncClient, settings, monkeypatch, field: str, variable: str
) -> None:  # noqa: ANN001
    """The courtesy `provider_key` gives every other integration.

    Not a 500 and not a silent disable: a 503 whose detail says which line to
    add to `.env`. Note that no `stripe_stub` is requested here — the point is
    that this fails BEFORE any client is built, which the autouse guard would
    otherwise turn into a loud AssertionError.
    """
    monkeypatch.setattr(settings, field, None)
    me = await _sign_up(client)

    resp = await client.post(f"{BASE}/agencies/{me['agency']['id']}/billing/checkout")
    assert resp.status_code == 503, resp.text
    problem = resp.json()
    assert problem["type"] == "/problems/billing-not-configured"
    assert variable in problem["detail"]
    assert resp.headers["content-type"].startswith("application/problem+json")


# ---------------------------------------------------------------------------
# checkout — refused while a subscription is live
# ---------------------------------------------------------------------------


async def _set_status(engine, agency_id: str, value: str | None) -> None:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        row.subscription_status = value
        await s.commit()


@pytest.mark.parametrize("live", ["active", "trialing"])
async def test_checkout_is_refused_while_a_subscription_is_live(
    client: AsyncClient, engine, stripe_stub: _StripeStub, live: str  # noqa: ANN001
) -> None:
    """A second subscription would bill the agency twice; Stripe does not stop it."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _set_status(engine, agency_id, live)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 409, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "/problems/subscription-already-active"
    assert body["title"] == "Subscription already active"
    assert body["detail"] == (
        "This agency already has a current subscription. "
        "Manage it from Manage billing in Settings."
    )
    # Nothing of Stripe's, and not the status, comes back in the refusal.
    assert live not in body["detail"]
    assert "cus_" not in resp.text
    # Refused before any Stripe call: no session, and no customer minted either.
    assert stripe_stub.sessions_created == []
    assert stripe_stub.customers_created == []


@pytest.mark.parametrize("state", [None, "canceled", "incomplete_expired"])
async def test_checkout_still_works_when_there_is_no_subscription_or_it_is_over(
    client: AsyncClient, engine, stripe_stub: _StripeStub, state: str | None  # noqa: ANN001
) -> None:
    """`past_due`, `unpaid`, `paused` and `incomplete` are NOT in this list any
    more: founder decision 4 made them live at Stripe, so they are refused."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _set_status(engine, agency_id, state)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"url": stripe_stub.next_session_url}
    assert len(stripe_stub.sessions_created) == 1


@pytest.mark.parametrize("state", ["canceled", "incomplete_expired"])
async def test_checkout_proceeds_for_an_ended_subscription_with_an_existing_customer(
    client: AsyncClient, engine, stripe_stub: _StripeStub, state: str  # noqa: ANN001
) -> None:
    """The returning customer: a subscription that is over, and a card on file.

    The previous open session is expired first, then Stripe is asked about the
    customer's subscriptions and finds none live, and a new session is created
    against the SAME customer.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    await _set_status(engine, agency_id, state)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"url": stripe_stub.next_session_url}
    assert len(stripe_stub.sessions_created) == 2
    assert len(stripe_stub.customers_created) == 1
    assert stripe_stub.sessions_expired == ["cs_test_001"]
    assert stripe_stub.subscriptions_listed == [LIST_ALL_SUBSCRIPTIONS]


# ---------------------------------------------------------------------------
# checkout — refused while a subscription needs attention (decision 4, 2026-10-01)
# ---------------------------------------------------------------------------


def _stripe_calls(stub: _StripeStub) -> dict[str, int]:
    """How many times each Stripe call has been made, to prove one was NOT."""
    return {
        "customers_created": len(stub.customers_created),
        "sessions_created": len(stub.sessions_created),
        "portals_created": len(stub.portals_created),
        "sessions_listed": len(stub.sessions_listed),
        "sessions_expired": len(stub.sessions_expired),
        "subscriptions_listed": len(stub.subscriptions_listed),
    }


def _assert_needs_attention_refusal(resp: Any, *, state: str | None) -> None:
    """The refusal's exact shape, and what it must not carry."""
    assert resp.status_code == 409, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "/problems/subscription-needs-attention"
    assert body["title"] == "Subscription needs attention"
    assert body["status"] == 409
    assert body["detail"] == NEEDS_ATTENTION_DETAIL
    assert "Manage billing" in body["detail"]
    # Neither a Stripe id nor the raw status (when there is one) comes back.
    for leaked in ("cus_", "sub_", *([state] if state else [])):
        assert leaked not in resp.text, leaked


@pytest.mark.parametrize("has_customer", [False, True])
@pytest.mark.parametrize("state", REFUSED_AS_NEEDS_ATTENTION)
async def test_checkout_is_refused_while_a_subscription_needs_attention(
    client: AsyncClient,
    engine,  # noqa: ANN001
    stripe_stub: _StripeStub,
    state: str,
    has_customer: bool,
) -> None:
    """Live at Stripe, just not paid up: a second checkout would be a second
    subscription. Refused before any Stripe call, with or without a customer.

    Includes a status Stripe does not have (`on_hold`): checkout denies by
    default, and only no subscription or an ended one may start another."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    if has_customer:
        assert (
            await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
        ).status_code == 201
    await _set_status(engine, agency_id, state)
    before = _stripe_calls(stripe_stub)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    _assert_needs_attention_refusal(resp, state=state)

    # No Stripe call of any kind: not asked, not listed, not expired, not minted.
    assert _stripe_calls(stripe_stub) == before
    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == state
    assert bool(agency.stripe_customer_id) is has_customer


@pytest.mark.parametrize("stored", [None, "canceled"])
@pytest.mark.parametrize("at_stripe", REFUSED_AS_NEEDS_ATTENTION)
async def test_a_needs_attention_subscription_at_stripe_refuses_checkout_despite_our_row(
    client: AsyncClient,
    engine,  # noqa: ANN001
    stripe_stub: _StripeStub,
    stored: str | None,
    at_stripe: str,
) -> None:
    """Our row says nothing (or `canceled`); Stripe still has the subscription.

    Anything at Stripe that is neither paid up nor over is refused, including a
    status this server has never seen."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    await _set_status(engine, agency_id, stored)
    stripe_stub.subscriptions_at_stripe = {at_stripe: ["sub_elsewhere_001"]}
    before = _stripe_calls(stripe_stub)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    _assert_needs_attention_refusal(resp, state=at_stripe)

    # Stripe WAS asked (the customer exists): the open session was cleared first,
    # then the subscriptions were listed, once. Nothing was created.
    assert stripe_stub.call_log[2:] == [
        "checkout.sessions.list",
        "checkout.sessions.expire",
        "subscriptions.list",
    ]
    assert stripe_stub.subscriptions_listed == [LIST_ALL_SUBSCRIPTIONS]
    after = _stripe_calls(stripe_stub)
    assert after["customers_created"] == before["customers_created"]
    assert after["sessions_created"] == before["sessions_created"]
    assert (await _agency(engine, agency_id)).subscription_status == stored


@pytest.mark.parametrize("stored", [None, "canceled"])
@pytest.mark.parametrize(
    "listed",
    [
        pytest.param([SimpleNamespace(id="sub_x", status=None)], id="null-status"),
        pytest.param([{"id": "sub_x"}], id="no-status-field"),
    ],
)
async def test_a_listed_subscription_with_no_readable_status_refuses_checkout(
    client: AsyncClient,
    engine,  # noqa: ANN001
    stripe_stub: _StripeStub,
    stored: str | None,
    listed: list[Any],
) -> None:
    """Fail closed. Stripe LISTED it, so it exists: a status we cannot read is
    not "no subscription" (which is what a null status means on OUR row), it is
    one we cannot tell is over, and a second checkout beside it could bill the
    agency twice."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    await _set_status(engine, agency_id, stored)
    stripe_stub.subscription_objects = listed
    before = _stripe_calls(stripe_stub)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    _assert_needs_attention_refusal(resp, state=None)

    assert stripe_stub.subscriptions_listed == [LIST_ALL_SUBSCRIPTIONS]
    after = _stripe_calls(stripe_stub)
    assert after["customers_created"] == before["customers_created"]
    assert after["sessions_created"] == before["sessions_created"]
    assert (await _agency(engine, agency_id)).subscription_status == stored


async def test_checkout_asks_stripe_for_every_subscription_in_one_call_then_creates_a_session(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """ONE list call, every status, a full page: it runs under the agency row
    lock, where six sequential round trips held it six times as long. Nothing
    live on it = a session."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    assert stripe_stub.subscriptions_listed == []  # a brand-new customer is not asked

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    assert stripe_stub.subscriptions_listed == [LIST_ALL_SUBSCRIPTIONS]
    assert len(stripe_stub.sessions_created) == 2


async def test_ended_subscriptions_at_stripe_do_not_refuse_checkout(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """`status: "all"` lists the ended ones too; a customer who cancelled once,
    or whose first attempt expired, must still be able to start again."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    stripe_stub.subscriptions_at_stripe = {
        "canceled": ["sub_old_001"],
        "incomplete_expired": ["sub_old_002"],
    }

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    assert stripe_stub.subscriptions_listed == [LIST_ALL_SUBSCRIPTIONS]
    assert len(stripe_stub.sessions_created) == 2


@pytest.mark.parametrize("needs_attention_listed_first", [True, False])
async def test_a_paid_up_subscription_outranks_one_that_needs_attention(
    client: AsyncClient, stripe_stub: _StripeStub, needs_attention_listed_first: bool
) -> None:
    """Both live at once is not a real case, but which refusal is reported must
    not depend on the order Stripe lists them in."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    listed = ["past_due", "trialing"] if needs_attention_listed_first else ["trialing", "past_due"]
    stripe_stub.subscriptions_at_stripe = {status: [f"sub_{status}"] for status in listed}
    before = len(stripe_stub.sessions_created)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "/problems/subscription-already-active"
    assert "sub_" not in resp.text
    assert len(stripe_stub.sessions_created) == before


async def test_a_full_page_of_subscriptions_with_none_live_fails_rather_than_creates(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """If Stripe says there are more than the page holds, a live subscription
    could be on the next one. Not being able to prove there is none is not the
    same as there being none, so the checkout fails closed, as the open-sessions
    check does."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    stripe_stub.subscriptions_at_stripe = {"canceled": ["sub_old_001"]}
    stripe_stub.subscriptions_has_more = True
    before = _stripe_calls(stripe_stub)

    with structlog.testing.capture_logs() as events:
        resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 503, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/service-unavailable"
    assert resp.json()["detail"] == SUBSCRIPTION_CHECK_FAILED_DETAIL
    assert "sub_" not in resp.text
    # The open session was cleared first; the subscriptions were listed once;
    # nothing was created.
    after = _stripe_calls(stripe_stub)
    assert after["sessions_expired"] == before["sessions_expired"] + 1
    assert after["subscriptions_listed"] == before["subscriptions_listed"] + 1
    assert after["customers_created"] == before["customers_created"]
    assert after["sessions_created"] == before["sessions_created"]

    # Worth a log line, because it is not a real case: the agency and customer
    # ids, and nothing else.
    truncated = [e for e in events if e["event"] == "billing.checkout_subscriptions_truncated"]
    assert len(truncated) == 1, events
    assert truncated[0]["log_level"] == "warning"
    assert set(truncated[0]) - {"event", "log_level"} == {"agency_id", "customer_id"}
    assert truncated[0]["agency_id"] == agency_id
    assert truncated[0]["customer_id"] == "cus_test_001"
    assert "@" not in json.dumps(events, default=str)


@pytest.mark.parametrize(
    ("at_stripe", "problem"),
    [
        ("active", "subscription-already-active"),
        ("past_due", "subscription-needs-attention"),
    ],
)
async def test_a_full_page_still_refuses_when_a_live_subscription_is_on_it(
    client: AsyncClient, stripe_stub: _StripeStub, at_stripe: str, problem: str
) -> None:
    """A live subscription that WAS found is the answer; the full page only
    matters when nothing live was on it."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    stripe_stub.subscriptions_at_stripe = {at_stripe: ["sub_elsewhere_001"]}
    stripe_stub.subscriptions_has_more = True

    with structlog.testing.capture_logs() as events:
        resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == f"/problems/{problem}"
    assert len(stripe_stub.sessions_created) == 1
    # The "full page" warning is for the 503 only: here the answer was found.
    assert not [e for e in events if e["event"] == "billing.checkout_subscriptions_truncated"]


async def test_a_stripe_failure_while_listing_subscriptions_fails_the_checkout(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """503 with its OWN sentence and log event, and no new session: if Stripe
    cannot say whether a subscription is live, creating a session would risk
    exactly the double subscription."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201

    stripe_stub.list_subscriptions_error = stripe.APIConnectionError("boom cus_secret")
    with structlog.testing.capture_logs() as events:
        resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "/problems/service-unavailable"
    # Its own sentence, not the one for clearing the previous session.
    assert resp.json()["detail"] == SUBSCRIPTION_CHECK_FAILED_DETAIL
    assert resp.json()["detail"] != EXPIRE_FAILED_DETAIL
    assert "boom" not in resp.text
    # The sessions step ran first and finished; nothing was created after it.
    assert stripe_stub.sessions_expired == ["cs_test_001"]
    assert len(stripe_stub.sessions_created) == 1

    # Logged under its own event: the error CLASS only, never its message.
    failed = [e for e in events if e["event"] == "billing.checkout_subscription_check_failed"]
    assert len(failed) == 1, events
    assert failed[0]["agency_id"] == agency_id
    assert failed[0]["error_type"] == "APIConnectionError"
    assert set(failed[0]) - {"event", "log_level"} == {"agency_id", "error_type"}
    assert not [e for e in events if e["event"] == "billing.checkout_expire_failed"]
    assert "boom" not in json.dumps(events, default=str)


async def test_checkout_clears_open_sessions_before_it_asks_about_subscriptions(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """Security review L2: the ORDER of the two checks.

    An open session stays payable until it is expired. Asking about
    subscriptions first left a window: the customer pays the open session after
    we looked and before we expired it, and we then created a second session
    beside a subscription we never saw. Expiring first closes it: a session that
    completed earlier shows up as a subscription, and one that completes during
    the expire makes the expire fail (503).
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    assert stripe_stub.call_log == ["customers.create", "checkout.sessions.create"]

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    assert stripe_stub.call_log[2:] == [
        "checkout.sessions.list",
        "checkout.sessions.expire",
        "subscriptions.list",
        "checkout.sessions.create",
    ]


@pytest.mark.parametrize(
    ("completed_as", "problem"),
    [
        ("active", "subscription-already-active"),
        ("incomplete", "subscription-needs-attention"),
    ],
)
async def test_a_session_paid_in_the_meantime_is_found_as_a_subscription(
    client: AsyncClient, stripe_stub: _StripeStub, completed_as: str, problem: str
) -> None:
    """The customer pays the open session just as the next checkout starts. It
    is no longer open, so there is nothing to expire, but it is a subscription
    by the time Stripe is asked: a 409, not a second session beside it."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201

    def _pay_the_open_session() -> None:
        stripe_stub.open_sessions.remove("cs_test_001")
        stripe_stub.subscriptions_at_stripe = {completed_as: ["sub_from_cs_test_001"]}

    stripe_stub.on_sessions_list = _pay_the_open_session

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == f"/problems/{problem}"
    assert "sub_" not in resp.text
    assert len(stripe_stub.sessions_created) == 1
    assert stripe_stub.call_log[2:] == ["checkout.sessions.list", "subscriptions.list"]


@pytest.mark.parametrize(
    ("status", "live"),
    [
        *[(s, True) for s in ("active", "trialing", *NEEDS_ATTENTION, UNKNOWN_STATUS)],
        *[(s, False) for s in (None, *ENDED)],
    ],
)
def test_a_subscription_is_live_at_stripe_unless_there_is_none_or_it_is_over(
    status: str | None, live: bool
) -> None:
    """Deny by default (security review L1): what is listed is what is OVER, so
    a status Stripe adds later counts as live until somebody decides otherwise."""
    assert billing_service.is_live_at_stripe(status) is live


def test_is_active_still_counts_only_active_and_trialing() -> None:
    """The scan allowance reads `is_active()`; neither decision 4 nor L1 moves it."""
    assert frozenset({"active", "trialing"}) == billing_service.ACTIVE_STATUSES
    assert frozenset(ENDED) == billing_service.TERMINAL_STATUSES
    for state in ("active", "trialing"):
        assert billing_service.is_active(Agency(subscription_status=state)) is True
    for state in (*NEEDS_ATTENTION, UNKNOWN_STATUS, *ENDED, None):
        assert billing_service.is_active(Agency(subscription_status=state)) is False


async def test_the_checkout_route_documents_all_three_409s(client: AsyncClient) -> None:
    """The published contract: three problem types, and `checkout-in-progress`
    is a 409 (the route's docstring used to call it a 503)."""
    schema = (await client.get(f"{BASE}/openapi.json")).json()
    op = schema["paths"][f"{BASE}/agencies/{{agencyId}}/billing/checkout"]["post"]

    declared = op["responses"]["409"]["description"]
    for problem in (
        "subscription-already-active",
        "subscription-needs-attention",
        "checkout-in-progress",
    ):
        assert f"/problems/{problem}" in declared
    assert "application/problem+json" in op["responses"]["409"]["content"]

    prose = " ".join(op["description"].split())
    assert "409 subscription-needs-attention" in prose
    assert "409 checkout-in-progress" in prose
    assert "503 checkout-in-progress" not in prose


async def test_a_refused_checkout_lists_and_expires_nothing(
    client: AsyncClient, engine, stripe_stub: _StripeStub  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    await _set_status(engine, agency_id, "active")

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 409, resp.text
    assert stripe_stub.sessions_listed == []
    assert stripe_stub.sessions_expired == []
    assert stripe_stub.open_sessions == ["cs_test_001"]


async def test_a_new_checkout_expires_the_previous_open_session(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """Two payable sessions would let the same agency subscribe twice."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    first = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert first.status_code == 201, first.text
    assert stripe_stub.sessions_listed == []  # a brand-new customer has none
    assert stripe_stub.open_sessions == ["cs_test_001"]

    second = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert second.status_code == 201, second.text
    assert stripe_stub.sessions_listed == [
        {"customer": "cus_test_001", "status": "open", "limit": 100}
    ]
    assert stripe_stub.sessions_expired == ["cs_test_001"]
    assert stripe_stub.open_sessions == ["cs_test_002"]
    assert len(stripe_stub.sessions_created) == 2


async def test_a_stripe_failure_while_expiring_fails_the_checkout(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    """503, and no new session. Carrying on would leave two payable sessions,
    which is the exact double-subscription this step exists to prevent. It is
    also what a session completing DURING the expire looks like: Stripe refuses
    to expire it, and the checkout fails closed."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201

    stripe_stub.expire_error = stripe.APIConnectionError("boom cus_secret")
    with structlog.testing.capture_logs() as events:
        resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "/problems/service-unavailable"
    # The sentence and the log event for THIS step; not the subscription check's.
    assert resp.json()["detail"] == EXPIRE_FAILED_DETAIL
    assert "boom" not in resp.text
    assert len(stripe_stub.sessions_created) == 1
    # It stopped there: the subscriptions were never asked about.
    assert stripe_stub.subscriptions_listed == []
    failed = [e for e in events if e["event"] == "billing.checkout_expire_failed"]
    assert len(failed) == 1, events
    assert failed[0]["error_type"] == "APIConnectionError"
    assert not [e for e in events if e["event"] == "billing.checkout_subscription_check_failed"]


async def test_concurrent_first_checkouts_create_one_customer(
    client: AsyncClient, engine, stripe_stub: _StripeStub  # noqa: ANN001
) -> None:
    """Both requests would read a NULL customer id and each create one; the
    second UPDATE would then orphan the first customer. The agency row is locked
    for the request, so the second waits and reuses the first's customer."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    stripe_stub.customer_delay = 0.3

    url = f"{BASE}/agencies/{agency_id}/billing/checkout"
    r1, r2 = await asyncio.gather(client.post(url), client.post(url))
    assert (r1.status_code, r2.status_code) == (201, 201), (r1.text, r2.text)

    assert len(stripe_stub.customers_created) == 1
    assert (await _agency(engine, agency_id)).stripe_customer_id == "cus_test_001"
    assert {s["customer"] for s in stripe_stub.sessions_created} == {"cus_test_001"}
    # The loser of the race expired the winner's session: one open at the end.
    assert len(stripe_stub.open_sessions) == 1


@pytest.mark.parametrize(
    "stored_status", ["active", "trialing", *NEEDS_ATTENTION, UNKNOWN_STATUS]
)
async def test_a_second_subscription_id_over_a_live_one_logs_a_warning(
    client: AsyncClient, engine, stored_status: str  # noqa: ANN001
) -> None:
    """Live, for the backstop, means live AT STRIPE: anything but no subscription
    and the two ended statuses, the same test checkout refuses on (deny by
    default, so an unknown status counts). A second subscription arriving over
    any of them got past that."""
    me = await _sign_up(client, email="dupe@billing.example")
    agency_id = me["agency"]["id"]
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        row.stripe_customer_id = "cus_test_001"
        row.stripe_subscription_id = "sub_first"
        row.subscription_status = stored_status
        await s.commit()

    payload = _event(
        "checkout.session.completed",
        {
            "id": "cs_x",
            "mode": "subscription",
            "customer": "cus_test_001",
            "subscription": "sub_second",
            "metadata": {"agency_id": agency_id},
        },
    )
    with structlog.testing.capture_logs() as events:
        resp = await _deliver(client, payload)
    assert resp.status_code == 200, resp.text

    warned = [e for e in events if e.get("log_level") == "warning" and "subscription" in e["event"]]
    assert len(warned) == 1, events
    w = warned[0]
    assert w["agency_id"] == agency_id
    assert w["stored_subscription_id"] == "sub_first"
    assert w["incoming_subscription_id"] == "sub_second"
    assert "@" not in json.dumps(w, default=str)
    # Behaviour is unchanged: the newest subscription id is stored.
    assert (await _agency(engine, agency_id)).stripe_subscription_id == "sub_second"


async def test_the_same_subscription_id_does_not_warn(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    me = await _sign_up(client, email="same@billing.example")
    agency_id = me["agency"]["id"]
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        row.stripe_customer_id = "cus_test_001"
        row.stripe_subscription_id = "sub_first"
        row.subscription_status = "active"
        await s.commit()

    payload = _event(
        "customer.subscription.created",
        _subscription(sub_id="sub_first", agency_id=agency_id),
    )
    with structlog.testing.capture_logs() as events:
        resp = await _deliver(client, payload)
    assert resp.status_code == 200, resp.text
    assert not [e for e in events if e.get("log_level") == "warning"], events


@pytest.mark.parametrize("stored_status", [None, "canceled", "incomplete_expired"])
async def test_a_second_subscription_id_over_an_ended_one_does_not_warn(
    client: AsyncClient, engine, stored_status: str | None  # noqa: ANN001
) -> None:
    """A new subscription after the old one ended is a new life, not a duplicate:
    only a status that is live at Stripe makes the arrival of a second id news."""
    me = await _sign_up(client, email="ended@billing.example")
    agency_id = me["agency"]["id"]
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        row.stripe_customer_id = "cus_test_001"
        row.stripe_subscription_id = "sub_first"
        row.subscription_status = stored_status
        await s.commit()

    payload = _event(
        "checkout.session.completed",
        {
            "id": "cs_x",
            "mode": "subscription",
            "customer": "cus_test_001",
            "subscription": "sub_second",
            "metadata": {"agency_id": agency_id},
        },
    )
    with structlog.testing.capture_logs() as events:
        resp = await _deliver(client, payload)
    assert resp.status_code == 200, resp.text
    assert not [e for e in events if e.get("log_level") == "warning"], events
    assert (await _agency(engine, agency_id)).stripe_subscription_id == "sub_second"


async def _hold_row_lock(engine, agency_id: str, mode: str, seconds: float = 6.0):  # noqa: ANN001, ANN202
    """Lock the agency row in a background task that lets go by itself.

    The holder rolls back after `seconds`, so if `lock_timeout` (or the lock
    mode) ever regresses the checkout under test proceeds after ~6s and the
    test FAILS on its elapsed-time and problem-type assertions, instead of
    waiting on a lock nobody will release. Returns the task; await it last.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    ready = asyncio.Event()

    async def _run() -> None:
        async with factory() as holder:
            await holder.execute(
                text(f"SELECT id FROM agencies WHERE id = :i FOR {mode}"),  # noqa: S608
                {"i": agency_id},
            )
            ready.set()
            await asyncio.sleep(seconds)
            await holder.rollback()

    task = asyncio.create_task(_run())
    await ready.wait()
    return task


async def _timed_checkout(client: AsyncClient, agency_id: str) -> tuple[Any, float]:
    started = time.monotonic()
    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    return resp, time.monotonic() - started


async def test_stripe_client_has_an_explicit_timeout_and_one_retry(settings) -> None:  # noqa: ANN001
    """The stripe default is 80s and two retries per call, under a row lock."""
    client = _REAL_CLIENT(settings)
    requestor = client._requestor  # noqa: SLF001
    assert requestor._options.max_network_retries == 1  # noqa: SLF001
    http = requestor._client  # noqa: SLF001
    assert http._timeout == 15  # noqa: SLF001


async def test_a_held_agency_lock_gives_checkout_in_progress_quickly(
    client: AsyncClient, engine, stripe_stub: _StripeStub  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    holder = await _hold_row_lock(engine, agency_id, "NO KEY UPDATE")
    resp, elapsed = await _timed_checkout(client, agency_id)
    await holder

    assert resp.status_code == 409, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/checkout-in-progress"
    assert resp.json()["detail"] == (
        "A checkout for this agency is already in progress. Try again in a moment."
    )
    assert elapsed < 5, elapsed
    assert stripe_stub.customers_created == []
    assert stripe_stub.sessions_created == []


async def test_child_row_inserts_do_not_block_a_repeat_checkout(
    client: AsyncClient, engine, stripe_stub: _StripeStub  # noqa: ANN001
) -> None:
    """Inserting a scan, client or alert takes FOR KEY SHARE on the agency. With a
    customer already recorded, checkout writes no key column and locks FOR NO KEY
    UPDATE, which is compatible with it; FOR UPDATE would not be."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    holder = await _hold_row_lock(engine, agency_id, "KEY SHARE")
    resp, elapsed = await _timed_checkout(client, agency_id)
    await holder
    assert resp.status_code == 201, resp.text
    assert elapsed < 5, elapsed


async def test_a_first_checkout_blocked_by_a_child_insert_fails_before_stripe(
    client: AsyncClient, engine, stripe_stub: _StripeStub  # noqa: ANN001
) -> None:
    """Recording the first customer id writes a unique-indexed (key) column, so the
    first checkout takes the stronger lock up front, before any Stripe call."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    holder = await _hold_row_lock(engine, agency_id, "KEY SHARE")
    resp, elapsed = await _timed_checkout(client, agency_id)
    await holder
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "/problems/checkout-in-progress"
    assert elapsed < 5, elapsed
    assert stripe_stub.customers_created == []


@pytest.mark.parametrize("stored", [None, "canceled"])
@pytest.mark.parametrize("at_stripe", ["active", "trialing"])
async def test_a_live_stripe_subscription_refuses_checkout_despite_a_stale_status(
    client: AsyncClient,
    engine,  # noqa: ANN001
    stripe_stub: _StripeStub,
    stored: str | None,
    at_stripe: str,
) -> None:
    """Paid, but the webhook has not landed: our row still says nothing."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    await _set_status(engine, agency_id, stored)
    stripe_stub.subscriptions_at_stripe = {at_stripe: ["sub_paid_elsewhere"]}
    before = len(stripe_stub.sessions_created)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "/problems/subscription-already-active"
    assert resp.json()["detail"] == (
        "This agency already has a current subscription. "
        "Manage it from Manage billing in Settings."
    )
    assert "sub_" not in resp.text
    assert len(stripe_stub.sessions_created) == before
    # The open session was cleared first (it is the first call), then Stripe was
    # asked once, for the customer's subscriptions of every status.
    assert stripe_stub.sessions_expired == ["cs_test_001"]
    assert stripe_stub.subscriptions_listed == [LIST_ALL_SUBSCRIPTIONS]
    assert (await _agency(engine, agency_id)).subscription_status == stored


async def test_more_open_sessions_than_one_page_fails_rather_than_creates(
    client: AsyncClient, stripe_stub: _StripeStub
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    assert (await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")).status_code == 201
    stripe_stub.open_has_more = True

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 503, resp.text
    assert resp.json()["type"] == "/problems/service-unavailable"
    assert len(stripe_stub.sessions_created) == 1
    # Failing closed on the first list stops the checkout before the second.
    assert stripe_stub.subscriptions_listed == []


@pytest.mark.parametrize("state", ["active", *NEEDS_ATTENTION])
async def test_a_member_gets_403_not_409_on_a_subscribed_agency(
    client: AsyncClient, engine, stripe_stub: _StripeStub, state: str  # noqa: ANN001
) -> None:
    """The subscription check runs after authority, so it cannot leak state —
    not for a paid-up agency and not for one whose subscription needs attention."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _set_status(engine, agency_id, state)
    await _become_member(client, engine, agency_id)

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "/problems/permission-denied"
    assert state not in resp.text
    assert _stripe_calls(stripe_stub) == dict.fromkeys(_stripe_calls(stripe_stub), 0)


@pytest.mark.parametrize("state", ["active", *NEEDS_ATTENTION])
async def test_another_agencys_id_is_404_even_when_that_agency_is_subscribed(
    client: AsyncClient, engine, stripe_stub: _StripeStub, state: str  # noqa: ANN001
) -> None:
    first = await _sign_up(client, email="a2@billing.example", agency="Agency A2")
    await _set_status(engine, first["agency"]["id"], state)
    await client.post(f"{BASE}/auth/logout")
    await _sign_up(client, email="b2@billing.example", agency="Agency B2")

    resp = await client.post(f"{BASE}/agencies/{first['agency']['id']}/billing/checkout")
    assert resp.status_code == 404, resp.text
    assert resp.json()["type"] == "/problems/not-found"
    assert state not in resp.text
    assert stripe_stub.customers_created == []
    assert stripe_stub.sessions_created == []
    assert stripe_stub.sessions_listed == []
    assert stripe_stub.sessions_expired == []
    assert stripe_stub.subscriptions_listed == []


# ---------------------------------------------------------------------------
# authority — Epic 9.14's rules, applied to money
# ---------------------------------------------------------------------------


async def test_billing_requires_a_session(client: AsyncClient) -> None:
    for method, path in [
        ("GET", f"{BASE}/agencies/agcy_01ANY/billing"),
        ("POST", f"{BASE}/agencies/agcy_01ANY/billing/checkout"),
        ("POST", f"{BASE}/agencies/agcy_01ANY/billing/portal"),
    ]:
        resp = await client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
        assert resp.json()["type"] == "/problems/authentication-required"


async def test_a_member_may_not_touch_billing(client: AsyncClient, engine) -> None:  # noqa: ANN001
    """`UserRole` says the owner is the billing contact. A member is not it.

    The READ is gated too. A member shown "No active subscription" beside a
    Subscribe button that 403s is a worse screen than one that does not offer
    it, and Settings already knows how to degrade a forbidden section.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _become_member(client, engine, agency_id)

    for method, path in [
        ("GET", f"{BASE}/agencies/{agency_id}/billing"),
        ("POST", f"{BASE}/agencies/{agency_id}/billing/checkout"),
        ("POST", f"{BASE}/agencies/{agency_id}/billing/portal"),
    ]:
        resp = await client.request(method, path)
        assert resp.status_code == 403, f"{method} {path} -> {resp.text}"
        assert resp.json()["type"] == "/problems/permission-denied"


async def test_another_agencys_id_is_404_never_403(client: AsyncClient) -> None:
    """Confirming an id exists is the cross-tenant leak. 404 for all three."""
    await _sign_up(client)
    other = "agcy_01SOMEBODYELSES"

    for method, path in [
        ("GET", f"{BASE}/agencies/{other}/billing"),
        ("POST", f"{BASE}/agencies/{other}/billing/checkout"),
        ("POST", f"{BASE}/agencies/{other}/billing/portal"),
    ]:
        resp = await client.request(method, path)
        assert resp.status_code == 404, f"{method} {path} -> {resp.text}"
        assert resp.json()["type"] == "/problems/not-found"


async def test_one_agency_cannot_see_anothers_subscription(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """A real second agency, really subscribed, and still a 404."""
    first = await _sign_up(client, email="a@billing.example", agency="Agency A")
    first_id = first["agency"]["id"]

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        agency = (
            await s.execute(select(Agency).where(Agency.id == first_id))
        ).scalar_one()
        agency.subscription_status = "active"
        agency.stripe_customer_id = "cus_first"
        await s.commit()

    await client.post(f"{BASE}/auth/logout")
    await _sign_up(client, email="b@billing.example", agency="Agency B")

    resp = await client.get(f"{BASE}/agencies/{first_id}/billing")
    assert resp.status_code == 404
    assert "cus_first" not in resp.text


# ---------------------------------------------------------------------------
# reading status — from our columns, never from Stripe
# ---------------------------------------------------------------------------


async def test_a_new_agency_has_no_subscription_and_is_not_gated(
    client: AsyncClient,
) -> None:
    me = await _sign_up(client)

    resp = await client.get(f"{BASE}/agencies/{me['agency']['id']}/billing")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "subscriptionStatus": None,
        "isActive": False,
        "currentPeriodEnd": None,
        "hasBillingAccount": False,
        # The allowance — 2026-09-28. Billing is configured in this file's
        # fixtures, so the gate is on: one free scan, none used, no reset.
        "scansUsedThisPeriod": 0,
        "scanLimit": 1,
        "periodResetsAt": None,
        # 2026-09-29: this owner signed up by password and has no Google link,
        # so the free scan would ask for one first.
        "freeScanRequiresGoogle": True,
        # 2026-09-29: nothing is cancelling — there is no subscription at all.
        "cancelAtPeriodEnd": False,
        # 2026-10-04: no subscription, so no trial under way.
        "trialEndsAt": None,
        # Round two: nothing to promise either: the trial is off by default in this file.
        "trialEligible": False,
        # 2026-10-05: and no Google to ask for either: there is no trial to need it for.
        "trialRequiresGoogle": False,
    }

    # And READING is never withheld because of it — the gate is on starting a
    # scan, not on any page. The dashboard is the cheapest proof.
    assert (await client.get(f"{BASE}/dashboard")).status_code == 200


async def test_reading_status_never_calls_stripe(client: AsyncClient, engine) -> None:  # noqa: ANN001
    """Asserted by NOT requesting `stripe_stub`.

    The autouse guard makes any reach for a client raise, so this test passing
    is proof the read path is pure database. A settings screen that called a
    payment API on every page load would fail whenever Stripe was slow.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        agency = (
            await s.execute(select(Agency).where(Agency.id == agency_id))
        ).scalar_one()
        agency.stripe_customer_id = "cus_written"
        agency.stripe_subscription_id = "sub_written"
        agency.subscription_status = "active"
        agency.subscription_current_period_end = PERIOD_END
        await s.commit()

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert body["subscriptionStatus"] == "active"
    assert body["isActive"] is True
    assert body["hasBillingAccount"] is True
    assert body["currentPeriodEnd"].startswith("2026-09-21T14:13:20")


async def test_status_never_leaks_stripe_identifiers(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        agency = (
            await s.execute(select(Agency).where(Agency.id == agency_id))
        ).scalar_one()
        agency.stripe_customer_id = "cus_secret"
        agency.stripe_subscription_id = "sub_secret"
        await s.commit()

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).text
    assert "cus_secret" not in body
    assert "sub_secret" not in body


# ---------------------------------------------------------------------------
# the webhook refuses anything it cannot verify
# ---------------------------------------------------------------------------


async def test_a_missing_signature_is_refused(client: AsyncClient) -> None:
    payload = _event("checkout.session.completed", {"customer": "cus_x"})
    resp = await _deliver(client, payload, signature=None)
    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-webhook-signature"


async def test_a_wrong_secret_is_refused(client: AsyncClient) -> None:
    payload = _event("checkout.session.completed", {"customer": "cus_x"})
    resp = await _deliver(client, payload, signature=_sign(payload, "whsec_wrong"))
    assert resp.status_code == 400, resp.text


async def test_a_garbage_signature_header_is_refused(client: AsyncClient) -> None:
    payload = _event("checkout.session.completed", {"customer": "cus_x"})
    resp = await _deliver(client, payload, signature="not-a-signature")
    assert resp.status_code == 400, resp.text


async def test_a_signature_for_a_different_body_is_refused(client: AsyncClient) -> None:
    """The tampering case, and the reason the RAW body is what gets verified."""
    honest = _event("customer.subscription.updated", _subscription(status="canceled"))
    forged = _event("customer.subscription.updated", _subscription(status="active"))

    resp = await _deliver(client, forged, signature=_sign(honest))
    assert resp.status_code == 400, resp.text


async def test_a_replayed_signature_outside_the_window_is_refused(
    client: AsyncClient,
) -> None:
    """Stripe's default tolerance is 300s. A captured header is not a key."""
    payload = _event("checkout.session.completed", {"customer": "cus_x"})
    stale = _sign(payload, timestamp=int(time.time()) - 3600)

    resp = await _deliver(client, payload, signature=stale)
    assert resp.status_code == 400, resp.text


async def test_no_configured_secret_refuses_rather_than_trusting(
    client: AsyncClient, settings, monkeypatch
) -> None:  # noqa: ANN001
    """The load-bearing one.

    `services/email.py` treats an unset key as a supported development mode,
    because the consequence there is a message nobody receives. Here the
    consequence would be believing a stranger about money, so the answer is the
    opposite: refuse, and say which variable is missing.
    """
    monkeypatch.setattr(settings, "stripe_webhook_secret", None)
    payload = _event("checkout.session.completed", {"customer": "cus_x"})

    resp = await _deliver(client, payload, signature=_sign(payload))
    assert resp.status_code == 503, resp.text
    problem = resp.json()
    assert problem["type"] == "/problems/billing-not-configured"
    assert "STRIPE_WEBHOOK_SECRET" in problem["detail"]


async def test_an_unverified_payload_changes_nothing(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """The refusal is not merely a status code — no write happened."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    payload = _event(
        "customer.subscription.updated",
        _subscription(status="active", agency_id=agency_id),
    )
    assert (await _deliver(client, payload, signature="t=1,v1=deadbeef")).status_code == 400

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status is None
    assert agency.stripe_subscription_id is None


# ---------------------------------------------------------------------------
# the webhook is the only thing that marks an agency subscribed
# ---------------------------------------------------------------------------


async def test_checkout_completed_marks_the_agency_active(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """And note what did NOT happen: no browser came back.

    The redirect after payment is a UX nicety. Someone who pays and closes the
    tab is still subscribed, because nothing was ever waiting on that tab.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    payload = _event(
        "checkout.session.completed",
        {
            "id": "cs_test_001",
            "object": "checkout_session",
            "mode": "subscription",
            "customer": "cus_test_001",
            "subscription": "sub_test_001",
            "client_reference_id": agency_id,
            "metadata": {"agency_id": agency_id},
        },
    )
    resp = await _deliver(client, payload)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "active"
    assert agency.stripe_customer_id == "cus_test_001"
    assert agency.stripe_subscription_id == "sub_test_001"


@pytest.mark.parametrize(
    ("status", "expected_active"),
    [
        ("active", True),
        ("trialing", True),
        ("past_due", False),
        ("unpaid", False),
        ("incomplete", False),
        ("paused", False),
    ],
)
async def test_subscription_updated_stores_the_status_verbatim(
    client: AsyncClient, engine, status: str, expected_active: bool
) -> None:  # noqa: ANN001
    """Including `past_due`, which is NOT active.

    north-star.md §5.4 row 4 asks for a billing-failure state rendered as
    honestly as `partial` is. Counting a failed payment as active would be
    exactly the silent degradation that row forbids.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    payload = _event(
        "customer.subscription.updated",
        _subscription(status=status, agency_id=agency_id),
    )
    assert (await _deliver(client, payload)).json()["handled"] is True

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == status
    assert billing_service.is_active(agency) is expected_active

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert body["subscriptionStatus"] == status
    assert body["isActive"] is expected_active


async def test_an_unrecognised_status_is_stored_rather_than_rejected(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """Why the column is a VARCHAR and not an enum.

    If Stripe adds a status and this raised, the event would be retried and then
    abandoned, and the row would hold a status that stopped being true days ago
    — a wrong answer wearing the face of a right one.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    payload = _event(
        "customer.subscription.updated",
        _subscription(status="some_future_status", agency_id=agency_id),
    )
    assert (await _deliver(client, payload)).status_code == 200

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "some_future_status"
    assert billing_service.is_active(agency) is False


async def test_the_renewal_date_is_read_from_the_subscription_item(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """The API-version trap this integration would otherwise have walked into.

    `current_period_end` is not a top-level Subscription field on
    `2026-08-26.dahlia`. Reading it there returns nothing, the status still
    saves, Settings still says "Active", and the date is simply never present.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    payload = _event(
        "customer.subscription.updated", _subscription(agency_id=agency_id)
    )
    await _deliver(client, payload)

    assert (await _agency(engine, agency_id)).subscription_current_period_end == PERIOD_END


async def test_the_legacy_top_level_period_end_still_works(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """Replayed events carry the API version of the endpoint that received them."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    payload = _event(
        "customer.subscription.updated",
        _subscription(agency_id=agency_id, legacy_period_end=True),
    )
    await _deliver(client, payload)

    assert (await _agency(engine, agency_id)).subscription_current_period_end == PERIOD_END


async def test_subscription_deleted_cancels_and_clears_the_renewal_date(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """A cancelled subscription has no next renewal.

    Leaving the old date behind would let Settings render "renews" against a
    date in the past, which is worse than rendering nothing.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    await _deliver(
        client,
        _event("customer.subscription.updated", _subscription(agency_id=agency_id)),
    )
    assert (await _agency(engine, agency_id)).subscription_current_period_end is not None

    await _deliver(
        client,
        _event(
            "customer.subscription.deleted",
            _subscription(status="canceled", agency_id=agency_id),
        ),
    )

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "canceled"
    assert agency.subscription_current_period_end is None
    assert billing_service.is_active(agency) is False


async def test_deletion_is_authoritative_even_if_the_payload_disagrees(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    await _deliver(
        client,
        _event(
            "customer.subscription.deleted",
            _subscription(status="active", agency_id=agency_id),
        ),
    )
    assert (await _agency(engine, agency_id)).subscription_status == "canceled"


async def test_an_agency_is_found_by_customer_id_without_metadata(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """A subscription started by hand in the Stripe dashboard has no metadata.

    That is a real thing a founder does while testing, and the fallback is what
    stops it silently doing nothing.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        agency = (
            await s.execute(select(Agency).where(Agency.id == agency_id))
        ).scalar_one()
        agency.stripe_customer_id = "cus_by_hand"
        await s.commit()

    payload = _event(
        "customer.subscription.updated",
        _subscription(customer="cus_by_hand", agency_id=None),
    )
    assert (await _deliver(client, payload)).json()["handled"] is True
    assert (await _agency(engine, agency_id)).subscription_status == "active"


# ---------------------------------------------------------------------------
# events we do not handle are accepted, not argued with
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    ["invoice.paid", "payment_intent.succeeded", "customer.created", "charge.refunded"],
)
async def test_an_unhandled_event_type_is_accepted_and_ignored(
    client: AsyncClient, engine, event_type: str
) -> None:  # noqa: ANN001
    """200, not 500 and not 400.

    Stripe sends whatever the account is configured to send. Answering non-2xx
    to an event nobody wants makes Stripe retry it, back off, and eventually
    mark the endpoint unhealthy — degrading delivery of the events that matter.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    payload = _event(event_type, {"id": "obj_1", "customer": "cus_test_001"})
    resp = await _deliver(client, payload)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True, "handled": False}
    assert (await _agency(engine, agency_id)).subscription_status is None


async def test_an_event_for_an_unknown_agency_is_accepted_and_ignored(
    client: AsyncClient,
) -> None:
    """A Stripe account can hold customers this database has never heard of.

    Asking Stripe to retry forever would not conjure a row.
    """
    await _sign_up(client)
    payload = _event(
        "customer.subscription.updated", _subscription(customer="cus_nobody")
    )

    resp = await _deliver(client, payload)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True, "handled": False}


async def test_the_webhook_needs_no_session(client: AsyncClient, engine) -> None:  # noqa: ANN001
    """Signed out entirely, and it still works. The HMAC is the credential."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await client.post(f"{BASE}/auth/logout")
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    payload = _event(
        "customer.subscription.updated", _subscription(agency_id=agency_id)
    )
    assert (await _deliver(client, payload)).status_code == 200
    assert (await _agency(engine, agency_id)).subscription_status == "active"


# ---------------------------------------------------------------------------
# idempotency and ordering
# ---------------------------------------------------------------------------


async def _ledger(engine) -> dict[str, StripeWebhookEvent]:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        rows = (await s.execute(select(StripeWebhookEvent))).scalars().all()
    return {r.id: r for r in rows}


def _at(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


async def test_the_same_event_delivered_twice_applies_once(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    payload = _event(
        "customer.subscription.updated",
        _subscription(agency_id=agency_id),
        event_id="evt_dup",
        created=1780000100,
    )

    first = await _deliver(client, payload)
    assert first.status_code == 200
    assert first.json() == {"received": True, "handled": True}

    # Change the state behind the webhook's back; a re-application would undo it.
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        row.subscription_status = "past_due"
        await s.commit()

    second = await _deliver(client, payload)
    assert second.status_code == 200
    assert second.json() == {"received": True, "handled": False, "duplicate": True}
    assert (await _agency(engine, agency_id)).subscription_status == "past_due"
    ledger = await _ledger(engine)
    assert list(ledger) == ["evt_dup"]
    assert ledger["evt_dup"].outcome.value == "applied"


async def test_two_concurrent_deliveries_of_one_event_apply_once(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    payload = _event(
        "customer.subscription.updated",
        _subscription(agency_id=agency_id),
        event_id="evt_race",
        created=1780000100,
    )

    results = await asyncio.gather(
        *[_deliver(client, payload, _sign(payload)) for _ in range(4)]
    )

    bodies = [r.json() for r in results]
    assert all(r.status_code == 200 for r in results), bodies
    assert sum(1 for b in bodies if b["handled"] is True) == 1, bodies
    assert sum(1 for b in bodies if b.get("duplicate") is True) == 3, bodies
    assert list(await _ledger(engine)) == ["evt_race"]
    assert (await _agency(engine, agency_id)).subscription_status == "active"


async def test_the_insert_itself_conflicts_across_two_sessions(engine) -> None:  # noqa: ANN001
    """The mechanism under the race test, with no HTTP in the way: the second
    insert blocks on the first's uncommitted row and then does nothing."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    args = {
        "event_id": "evt_two_sessions",
        "event_type": "customer.subscription.updated",
        "created_at": _at(1780000100),
        "agency_id": None,
        "outcome": billing_service.StripeWebhookOutcome.APPLIED,
    }
    async with factory() as one, factory() as two:
        assert await billing_service._record_event(one, **args) is True  # noqa: SLF001
        second = asyncio.ensure_future(
            billing_service._record_event(two, **args)  # noqa: SLF001
        )
        await asyncio.sleep(0.3)
        assert not second.done(), "the second insert should be waiting on the first"
        await one.commit()
        assert await asyncio.wait_for(second, 5) is False
        await two.rollback()


async def test_an_older_event_arriving_late_is_stale_and_changes_nothing(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    deleted = _event(
        "customer.subscription.deleted",
        _subscription(status="canceled", agency_id=agency_id),
        event_id="evt_t2",
        created=1780000200,
    )
    older = _event(
        "customer.subscription.updated",
        _subscription(status="active", agency_id=agency_id),
        event_id="evt_t1",
        created=1780000100,
    )
    assert (await _deliver(client, deleted)).json() == {"received": True, "handled": True}
    resp = await _deliver(client, older)

    assert resp.status_code == 200
    assert resp.json() == {"received": True, "handled": False, "stale": True}
    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "canceled"
    assert agency.stripe_event_at == _at(1780000200)
    ledger = await _ledger(engine)
    assert ledger["evt_t1"].outcome.value == "stale"
    assert ledger["evt_t2"].outcome.value == "applied"
    assert ledger["evt_t1"].agency_id == agency_id


async def test_two_events_in_the_same_second_both_apply_in_arrival_order(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    ts = 1780000300

    for status in ("past_due", "active"):
        payload = _event(
            "customer.subscription.updated",
            _subscription(status=status, agency_id=agency_id),
            created=ts,
        )
        assert (await _deliver(client, payload)).json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "active"  # the later arrival won
    assert {r.outcome.value for r in (await _ledger(engine)).values()} == {"applied"}


async def test_checkout_then_a_newer_subscription_update_both_apply(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    checkout = _event(
        "checkout.session.completed",
        {
            "id": "cs_test_1",
            "object": "checkout.session",
            "mode": "subscription",
            "customer": "cus_test_001",
            "subscription": "sub_test_001",
            "metadata": {"agency_id": agency_id},
        },
        created=1780000400,
    )
    update = _event(
        "customer.subscription.updated",
        _subscription(status="trialing", agency_id=agency_id),
        created=1780000401,
    )
    assert (await _deliver(client, checkout)).json()["handled"] is True
    assert (await _deliver(client, update)).json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "trialing"
    assert agency.stripe_event_at == _at(1780000401)
    assert {r.outcome.value for r in (await _ledger(engine)).values()} == {"applied"}


async def test_stripe_event_at_only_moves_forward(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    seen: list[datetime | None] = []

    for created in (1780000500, 1780000400, 1780000500, 1780000600):
        payload = _event(
            "customer.subscription.updated",
            _subscription(agency_id=agency_id),
            created=created,
        )
        await _deliver(client, payload)
        seen.append((await _agency(engine, agency_id)).stripe_event_at)

    assert seen == [_at(1780000500), _at(1780000500), _at(1780000500), _at(1780000600)]


async def test_an_unmatched_event_writes_no_ledger_row_and_applies_when_resent(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    """Nothing is held for an unknown customer, so nothing is deduped.

    An operator links the agency, then uses Stripe's "Resend": the same event id
    must apply, not be answered as a duplicate.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    payload = _event(
        "customer.subscription.updated",
        _subscription(customer="cus_nobody"),
        event_id="evt_nobody",
    )
    resp = await _deliver(client, payload)

    assert resp.json() == {"received": True, "handled": False}
    assert await _ledger(engine) == {}

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        row.stripe_customer_id = "cus_nobody"
        await s.commit()

    again = await _deliver(client, payload)
    assert again.json() == {"received": True, "handled": True}
    assert (await _agency(engine, agency_id)).subscription_status == "active"
    assert (await _ledger(engine))["evt_nobody"].outcome.value == "applied"


@pytest.mark.parametrize("created", [None, "1780000100", True, "abc"])
async def test_an_event_without_a_numeric_created_is_ignored(
    client: AsyncClient, engine, created: Any  # noqa: ANN001
) -> None:
    """Never the server clock: it would set a watermark that could sit in the future."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    first = _event(
        "customer.subscription.updated",
        _subscription(status="past_due", agency_id=agency_id),
        created=1780000100,
    )
    assert (await _deliver(client, first)).json()["handled"] is True

    body = json.loads(
        _event(
            "customer.subscription.updated",
            _subscription(status="active", agency_id=agency_id),
            event_id="evt_nocreated",
        )
    )
    if created is None:
        del body["created"]
    else:
        body["created"] = created
    payload = json.dumps(body).encode()
    resp = await _deliver(client, payload)

    assert resp.status_code == 200
    assert resp.json() == {"received": True, "handled": False}
    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "past_due"
    assert agency.stripe_event_at == _at(1780000100)
    assert "evt_nocreated" not in await _ledger(engine)


async def test_a_retried_created_in_the_same_second_does_not_regress_to_incomplete(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    ts = 1780001000
    updated = _event(
        "customer.subscription.updated",
        _subscription(status="active", agency_id=agency_id),
        created=ts,
    )
    created = _event(
        "customer.subscription.created",
        _subscription(status="incomplete", agency_id=agency_id),
        event_id="evt_created_retry",
        created=ts,
    )
    assert (await _deliver(client, updated)).json()["handled"] is True
    resp = await _deliver(client, created)

    assert resp.json() == {"received": True, "handled": False, "stale": True}
    assert (await _agency(engine, agency_id)).subscription_status == "active"
    assert (await _ledger(engine))["evt_created_retry"].outcome.value == "stale"


async def test_active_after_deleted_in_the_same_second_does_not_resurrect(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    ts = 1780001100
    deleted = _event(
        "customer.subscription.deleted",
        _subscription(status="canceled", agency_id=agency_id),
        created=ts,
    )
    updated = _event(
        "customer.subscription.updated",
        _subscription(status="active", agency_id=agency_id),
        created=ts,
    )
    checkout = _event(
        "checkout.session.completed",
        {
            "id": "cs_tie",
            "object": "checkout.session",
            "mode": "subscription",
            "customer": "cus_test_001",
            "subscription": "sub_test_001",
            "metadata": {"agency_id": agency_id},
        },
        created=ts,
    )
    assert (await _deliver(client, deleted)).json()["handled"] is True
    for late in (updated, checkout):
        assert (await _deliver(client, late)).json() == {
            "received": True,
            "handled": False,
            "stale": True,
        }
    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "canceled"


async def test_a_new_subscription_after_a_cancellation_applies(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    ts = 1780001200
    deleted = _event(
        "customer.subscription.deleted",
        _subscription(status="canceled", sub_id="sub_old", agency_id=agency_id),
        created=ts,
    )
    fresh = _event(
        "customer.subscription.created",
        _subscription(status="incomplete", sub_id="sub_new", agency_id=agency_id),
        created=ts,  # same second: only the id tells them apart
    )
    activated = _event(
        "customer.subscription.updated",
        _subscription(status="active", sub_id="sub_new", agency_id=agency_id),
        created=ts + 1,
    )
    for payload in (deleted, fresh, activated):
        assert (await _deliver(client, payload)).json() == {"received": True, "handled": True}
    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "active"
    assert agency.stripe_subscription_id == "sub_new"


async def test_the_state_machine_guard_is_one_named_function() -> None:
    """The rule itself, table-driven, with no database."""
    guard = billing_service._blocked_by_state_machine  # noqa: SLF001
    agency = Agency(stripe_subscription_id="sub_1", subscription_status="canceled")
    assert guard(agency, "sub_1", "active") is True
    assert guard(agency, "sub_1", "canceled") is False
    assert guard(agency, "sub_2", "active") is False
    agency.subscription_status = "incomplete_expired"
    assert guard(agency, "sub_1", "incomplete") is True
    agency.subscription_status = "past_due"
    assert guard(agency, "sub_1", "incomplete") is True
    assert guard(agency, "sub_1", "active") is False
    agency.subscription_status = "incomplete"
    assert guard(agency, "sub_1", "incomplete") is False
    assert guard(agency, "sub_1", "active") is False


async def test_the_metadata_route_locks_the_agency_row(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    """Deterministic proof of the lock: no HTTP, no timing luck.

    Session one resolves the agency by `metadata.agency_id` and holds its
    transaction open; session two doing the same must WAIT, and proceed only
    after session one commits.
    """
    agency_id = (await _sign_up(client))["agency"]["id"]
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    obj = {"metadata": {"agency_id": agency_id}}
    async with factory() as one, factory() as two:
        assert await billing_service._resolve_agency(one, obj) is not None  # noqa: SLF001
        second = asyncio.ensure_future(
            billing_service._resolve_agency(two, obj)  # noqa: SLF001
        )
        await asyncio.sleep(0.5)
        assert not second.done(), "the metadata route must lock the agency row"
        await one.commit()
        assert await asyncio.wait_for(second, 5) is not None
        await two.rollback()


@pytest.mark.parametrize("deleted_first", [True, False])
async def test_concurrent_different_events_on_the_metadata_route_end_canceled(
    client: AsyncClient, engine, deleted_first: bool  # noqa: ANN001
) -> None:
    """deleted@T3 racing updated-active@T2 must end canceled, watermark T3.

    Different subscription ids on purpose, so the state-machine guard cannot
    mask a missing lock: only the row lock and the timestamp order protect this.
    """
    t2, t3 = 1780002000, 1780002001
    for i in range(6):
        me = await _sign_up(
            client, email=f"race{i}{int(deleted_first)}@billing.example", agency=f"Race {i}"
        )
        agency_id = me["agency"]["id"]
        deleted = _event(
            "customer.subscription.deleted",
            _subscription(status="canceled", sub_id=f"sub_del_{i}",
                          customer=f"cus_del_{i}{int(deleted_first)}", agency_id=agency_id),
            created=t3,
        )
        updated = _event(
            "customer.subscription.updated",
            _subscription(status="active", sub_id=f"sub_upd_{i}",
                          customer=f"cus_upd_{i}{int(deleted_first)}", agency_id=agency_id),
            created=t2,
        )
        order = [deleted, updated] if deleted_first else [updated, deleted]
        results = await asyncio.gather(*[_deliver(client, p) for p in order])
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        agency = await _agency(engine, agency_id)
        assert agency.subscription_status == "canceled", i
        assert agency.stripe_event_at == _at(t3), i


async def test_three_first_checkout_events_delivered_together_do_not_deadlock(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    base = 1780003000
    for i in range(5):
        me = await _sign_up(client, email=f"first{i}@billing.example", agency=f"First {i}")
        agency_id = me["agency"]["id"]
        sub = f"sub_first_{i}"
        created = _event(
            "customer.subscription.created",
            _subscription(
                status="incomplete", sub_id=sub, customer=f"cus_first_{i}", agency_id=agency_id
            ),
            created=base,
        )
        updated = _event(
            "customer.subscription.updated",
            _subscription(
                status="active", sub_id=sub, customer=f"cus_first_{i}", agency_id=agency_id
            ),
            created=base + 1,
        )
        checkout = _event(
            "checkout.session.completed",
            {
                "id": f"cs_first_{i}",
                "object": "checkout.session",
                "mode": "subscription",
                "customer": f"cus_first_{i}",
                "subscription": sub,
                "metadata": {"agency_id": agency_id},
            },
            created=base + 2,
        )
        results = await asyncio.wait_for(
            asyncio.gather(*[_deliver(client, p) for p in (created, updated, checkout)]),
            30,
        )
        assert [r.status_code for r in results] == [200, 200, 200], [r.text for r in results]
        agency = await _agency(engine, agency_id)
        assert agency.subscription_status == "active", i
        # Checkout never moves the watermark; the newest subscription event does.
        assert agency.stripe_event_at == _at(base + 1), i
        assert agency.subscription_current_period_end == _at(PERIOD_END_TS), i


def _checkout(agency_id: str, *, created: int, **extra: Any) -> bytes:
    session: dict[str, Any] = {
        "id": f"cs_{created}",
        "object": "checkout.session",
        "mode": "subscription",
        "customer": "cus_test_001",
        "subscription": "sub_test_001",
        "metadata": {"agency_id": agency_id},
    }
    session.update(extra)
    return _event("checkout.session.completed", session, created=created)


async def test_a_subscription_update_delivered_after_a_newer_checkout_still_applies(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780004000
    update = _event(
        "customer.subscription.updated",
        _subscription(status="active", agency_id=agency_id),
        created=t,
    )
    assert (await _deliver(client, _checkout(agency_id, created=t + 1))).json()["handled"] is True
    assert (await _deliver(client, update)).json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "active"
    assert agency.subscription_current_period_end == _at(PERIOD_END_TS)
    assert agency.stripe_event_at == _at(t)
    assert {r.outcome.value for r in (await _ledger(engine)).values()} == {"applied"}


@pytest.mark.parametrize(
    "extra",
    [{"mode": "payment"}, {"mode": "setup", "subscription": None}, {"subscription": None}],
)
async def test_a_non_subscription_checkout_is_ignored_and_cannot_undo_a_cancellation(
    client: AsyncClient, engine, extra: dict[str, Any]  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    deleted = _event(
        "customer.subscription.deleted",
        _subscription(status="canceled", agency_id=agency_id),
        created=1780004100,
    )
    assert (await _deliver(client, deleted)).json()["handled"] is True
    before = await _agency(engine, agency_id)

    resp = await _deliver(client, _checkout(agency_id, created=1780004200, **extra))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True, "handled": False}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "canceled"
    assert agency.stripe_subscription_id == before.stripe_subscription_id
    assert agency.stripe_event_at == before.stripe_event_at
    assert len(await _ledger(engine)) == 1


async def test_unhandled_types_and_bad_signatures_write_no_ledger_row(
    client: AsyncClient, engine
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    await _deliver(client, _event("invoice.paid", {"id": "in_1", "customer": "cus_x"}))
    signed_wrong = _event(
        "customer.subscription.updated", _subscription(agency_id=agency_id)
    )
    assert (await _deliver(client, signed_wrong, "t=1,v1=deadbeef")).status_code == 400
    assert (await _deliver(client, signed_wrong, None)).status_code == 400

    assert await _ledger(engine) == {}


async def test_a_handler_failure_rolls_the_ledger_row_back_so_the_retry_applies(
    client: AsyncClient, engine, monkeypatch  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    payload = _event(
        "customer.subscription.updated",
        _subscription(agency_id=agency_id),
        event_id="evt_boom",
        created=1780000700,
    )

    real = billing_service._apply_subscription_event  # noqa: SLF001

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("handler failed")

    monkeypatch.setattr(billing_service, "_apply_subscription_event", boom)
    with pytest.raises(RuntimeError):
        await _deliver(client, payload)
    assert await _ledger(engine) == {}
    assert (await _agency(engine, agency_id)).stripe_event_at is None

    monkeypatch.setattr(billing_service, "_apply_subscription_event", real)
    retry = await _deliver(client, payload)
    assert retry.json() == {"received": True, "handled": True}
    assert (await _agency(engine, agency_id)).subscription_status == "active"
    assert list(await _ledger(engine)) == ["evt_boom"]


# ---------------------------------------------------------------------------
# the hosted portal
# ---------------------------------------------------------------------------


async def test_portal_returns_a_url_for_a_subscribed_agency(
    client: AsyncClient, engine, stripe_stub: _StripeStub
) -> None:  # noqa: ANN001
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        agency = (
            await s.execute(select(Agency).where(Agency.id == agency_id))
        ).scalar_one()
        agency.stripe_customer_id = "cus_test_001"
        agency.subscription_status = "active"
        await s.commit()

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/portal")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"url": stripe_stub.next_portal_url}
    assert stripe_stub.portals_created[0]["customer"] == "cus_test_001"


async def test_portal_before_subscribing_refuses_with_a_sentence(
    client: AsyncClient,
) -> None:
    """An empty portal is a worse answer than an absent button.

    No `stripe_stub`: this must refuse before reaching for a client at all.
    """
    me = await _sign_up(client)

    resp = await client.post(f"{BASE}/agencies/{me['agency']['id']}/billing/portal")
    assert resp.status_code == 503, resp.text
    assert "Subscribe first" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# cancelled in the portal, paid to the end of the period — 2026-09-29
# ---------------------------------------------------------------------------


class TestCancelAtPeriodEnd:
    """Stripe's portal cancels at the END of the billing period.

    Until then the subscription stays `active` with `cancel_at_period_end:
    true`, and Settings must say "Ends", not "Renews". Both the cancel and a
    resume arrive as `customer.subscription.updated`; the end arrives as
    `customer.subscription.deleted`.
    """

    async def _update(self, client: AsyncClient, agency_id: str, cancelling: bool) -> None:
        payload = _event(
            "customer.subscription.updated",
            _subscription(agency_id=agency_id, cancel_at_period_end=cancelling),
        )
        assert (await _deliver(client, payload)).json()["handled"] is True

    async def test_cancelled_then_resumed(self, client: AsyncClient, engine) -> None:  # noqa: ANN001
        me = await _sign_up(client)
        agency_id = me["agency"]["id"]

        await self._update(client, agency_id, cancelling=True)
        agency = await _agency(engine, agency_id)
        assert agency.subscription_cancel_at_period_end is True
        assert agency.subscription_status == "active"
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["cancelAtPeriodEnd"] is True
        assert body["isActive"] is True
        assert body["currentPeriodEnd"] is not None

        await self._update(client, agency_id, cancelling=False)
        assert (await _agency(engine, agency_id)).subscription_cancel_at_period_end is False
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["cancelAtPeriodEnd"] is False

    async def test_an_event_without_the_field_is_not_cancelling(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        me = await _sign_up(client)
        agency_id = me["agency"]["id"]
        payload = _event("customer.subscription.updated", _subscription(agency_id=agency_id))
        assert (await _deliver(client, payload)).json()["handled"] is True
        assert (await _agency(engine, agency_id)).subscription_cancel_at_period_end is False

    async def test_the_plan_still_applies_until_the_period_ends(
        self, client: AsyncClient, engine
    ) -> None:  # noqa: ANN001
        """`is_active` reads the status, which stays `active`: the month is paid for."""
        from avp_api.services import scan_allowance

        me = await _sign_up(client)
        agency_id = me["agency"]["id"]
        await self._update(client, agency_id, cancelling=True)

        agency = await _agency(engine, agency_id)
        assert billing_service.is_active(agency) is True
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["scanLimit"] == scan_allowance.PAID_SCANS_PER_MONTH
        assert body["periodResetsAt"] is not None

    async def test_deleted_still_makes_the_agency_free(self, client: AsyncClient, engine) -> None:  # noqa: ANN001
        me = await _sign_up(client)
        agency_id = me["agency"]["id"]
        await self._update(client, agency_id, cancelling=True)

        payload = _event(
            "customer.subscription.deleted",
            _subscription(status="canceled", agency_id=agency_id, cancel_at_period_end=True),
        )
        assert (await _deliver(client, payload)).json()["handled"] is True

        agency = await _agency(engine, agency_id)
        assert agency.subscription_status == "canceled"
        assert agency.subscription_cancel_at_period_end is False
        assert billing_service.is_active(agency) is False
        body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
        assert body["cancelAtPeriodEnd"] is False
        assert body["isActive"] is False
        assert body["currentPeriodEnd"] is None
        assert body["scanLimit"] == 1


# ---------------------------------------------------------------------------
# the free trial — 2026-10-04
#
# Founder decision: a 30-day free trial, the card taken at checkout, charged on
# day 31 unless cancelled. `STRIPE_TRIAL_DAYS` is its length, and 0, the default,
# is no trial at all: then checkout and everything above this line is exactly
# what it was. Nothing here reaches Stripe: `_client` is the stub, as above.
# ---------------------------------------------------------------------------

# 30 days after PERIOD_END_TS, as Stripe states it: Unix seconds.
TRIAL_END_TS = 1792592000  # 2026-10-21T14:13:20Z
TRIAL_END = datetime.fromtimestamp(TRIAL_END_TS, tz=UTC)


@pytest.fixture
def trial_30(settings, monkeypatch):  # noqa: ANN001, ANN201
    """The trial switched on, at the 30 days the founder decided on."""
    monkeypatch.setattr(settings, "stripe_trial_days", 30)


def _params_before_trials(settings, agency_id: str) -> dict[str, Any]:  # noqa: ANN001
    """The Checkout Session params as they were before a trial existed, `expires_at` aside.

    Written out in full and not built from the service's own constants, so that
    what a trial adds is exactly what the tests below say it adds, and an edit to
    the service cannot quietly edit what "today's checkout" means.
    """
    base = settings.public_web_base_url.rstrip("/")
    return {
        "mode": "subscription",
        "customer": "cus_test_001",
        "line_items": [{"price": FAKE_PRICE_ID, "quantity": 1}],
        "client_reference_id": agency_id,
        "metadata": {"agency_id": agency_id},
        "subscription_data": {"metadata": {"agency_id": agency_id}},
        "success_url": f"{base}/settings?checkout=success",
        "cancel_url": f"{base}/settings?checkout=cancelled",
    }


def _with_trial(params: dict[str, Any], days: int) -> dict[str, Any]:
    """What a trial adds to those params, and nothing else."""
    return {
        **params,
        "payment_method_collection": "always",
        "subscription_data": {**params["subscription_data"], "trial_period_days": days},
    }


async def _checkout_params(
    client: AsyncClient, stub: _StripeStub, agency_id: str
) -> dict[str, Any]:
    """Start a checkout and return the params it sent Stripe, less the clock."""
    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    params = dict(stub.sessions_created[-1])
    assert type(params.pop("expires_at")) is int
    return params


async def _set_fields(engine, agency_id: str, **fields: Any) -> None:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        for name, value in fields.items():
            setattr(row, name, value)
        await s.commit()


def _created_lines(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == "billing.checkout_created"]


# -- checkout ----------------------------------------------------------------


@pytest.mark.parametrize("configured", ["left unset", "set to 0"])
async def test_with_no_trial_configured_checkout_sends_the_params_it_always_did(
    client: AsyncClient, settings, monkeypatch, stripe_stub: _StripeStub, configured: str  # noqa: ANN001
) -> None:
    """The default is no trial, and then nothing about checkout is different."""
    if configured == "set to 0":
        monkeypatch.setattr(settings, "stripe_trial_days", 0)
    assert settings.stripe_trial_days == 0
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]

    with structlog.testing.capture_logs() as events:
        params = await _checkout_params(client, stripe_stub, agency_id)

    assert params == _params_before_trials(settings, agency_id)
    assert "payment_method_collection" not in params
    assert "trial_period_days" not in params["subscription_data"]
    # `trial_days` is in the line either way: 0 says there was none.
    created = _created_lines(events)
    assert len(created) == 1, events
    assert created[0]["trial_days"] == 0


@pytest.mark.parametrize("days", [1, 14, 30, 730])
async def test_a_new_agency_checks_out_with_the_trial_and_the_card_collected(
    client: AsyncClient, engine, settings, monkeypatch, stripe_stub: _StripeStub, days: int  # noqa: ANN001
) -> None:
    """`trial_period_days` is the setting, and the card is taken at checkout.

    `payment_method_collection="always"` is what makes Stripe take it: left to
    itself a trial checkout may skip the card, and then there would be nothing
    to charge on day 31.
    """
    monkeypatch.setattr(settings, "stripe_trial_days", days)
    me = await _sign_up_with_google(client, engine)
    agency_id = me["agency"]["id"]

    with structlog.testing.capture_logs() as events:
        params = await _checkout_params(client, stripe_stub, agency_id)

    assert params == _with_trial(_params_before_trials(settings, agency_id), days)
    assert params["subscription_data"]["trial_period_days"] == days
    assert params["payment_method_collection"] == "always"
    # Nothing else about the session moved: same price, same metadata, same URLs.
    assert params["line_items"] == [{"price": FAKE_PRICE_ID, "quantity": 1}]
    created = _created_lines(events)
    assert len(created) == 1, events
    assert created[0]["trial_days"] == days
    assert set(created[0]) - {"event", "log_level"} == {
        "agency_id", "session_id", "trial_days", "trial_withheld_no_google", "user_id"
    }
    assert created[0]["trial_withheld_no_google"] is False


@pytest.mark.usefixtures("trial_30")
async def test_a_returning_customer_who_never_subscribed_gets_the_trial_without_another_round_trip(
    client: AsyncClient, engine, settings, stripe_stub: _StripeStub  # noqa: ANN001
) -> None:
    """Someone who opened checkout, left, and came back has never had a subscription.

    Stripe is asked about the customer's subscriptions anyway (that call is how a
    double subscription is refused), and the trial rides on THAT answer: no new
    call, and the same four calls in the same order as without a trial.
    """
    me = await _sign_up_with_google(client, engine)
    agency_id = me["agency"]["id"]
    first = await _checkout_params(client, stripe_stub, agency_id)
    assert first["subscription_data"]["trial_period_days"] == 30
    stripe_stub.call_log.clear()

    second = await _checkout_params(client, stripe_stub, agency_id)

    assert second == _with_trial(_params_before_trials(settings, agency_id), 30)
    assert stripe_stub.call_log == [
        "checkout.sessions.list",
        "checkout.sessions.expire",
        "subscriptions.list",
        "checkout.sessions.create",
    ]
    assert stripe_stub.subscriptions_listed == [LIST_ALL_SUBSCRIPTIONS]


ROW_HAS_HAD_A_SUBSCRIPTION = [
    pytest.param(
        {"subscription_status": "canceled", "stripe_subscription_id": "sub_old"},
        id="canceled, with its subscription id",
    ),
    pytest.param(
        {"subscription_status": "incomplete_expired", "stripe_subscription_id": "sub_old"},
        id="incomplete_expired, with its subscription id",
    ),
    pytest.param({"subscription_status": "canceled"}, id="a status and no id"),
    pytest.param({"stripe_subscription_id": "sub_old"}, id="an id and no status"),
    pytest.param(
        {"stripe_event_at": datetime(2026, 5, 1, tzinfo=UTC)},
        id="only the watermark a subscription event leaves",
    ),
]


@pytest.mark.usefixtures("trial_30")
@pytest.mark.parametrize("stored", ROW_HAS_HAD_A_SUBSCRIPTION)
async def test_an_agency_that_has_ever_had_a_subscription_gets_no_trial(
    client: AsyncClient, engine, settings, stripe_stub: _StripeStub, stored: dict[str, Any]  # noqa: ANN001
) -> None:
    """One trial per agency, ever: a stored subscription of any status ends it.

    Only `canceled` and `incomplete_expired` can reach a checkout at all (every
    other status is refused with a 409 first), so those are the two that matter
    in practice; the rest are what a half-written row looks like, and they count.
    Checkout itself still works: it is the trial that is not offered.
    """
    me = await _sign_up_with_google(client, engine)
    agency_id = me["agency"]["id"]
    assert (await _checkout_params(client, stripe_stub, agency_id))["subscription_data"][
        "trial_period_days"
    ] == 30
    await _set_fields(engine, agency_id, **stored)

    with structlog.testing.capture_logs() as events:
        params = await _checkout_params(client, stripe_stub, agency_id)

    assert params == _params_before_trials(settings, agency_id)
    assert "payment_method_collection" not in params
    assert "trial_period_days" not in params["subscription_data"]
    assert _created_lines(events)[0]["trial_days"] == 0


@pytest.mark.usefixtures("trial_30")
@pytest.mark.parametrize("ended", ["canceled", "incomplete_expired"])
async def test_a_customer_with_an_ended_subscription_at_stripe_gets_no_trial(
    client: AsyncClient, engine, settings, stripe_stub: _StripeStub, ended: str  # noqa: ANN001
) -> None:
    """Our row says nothing (the webhook never landed), Stripe remembers.

    The customer's subscription list is asked for every status, so an ended one is
    on it. It does not refuse the checkout, and it does end the trial.
    """
    me = await _sign_up_with_google(client, engine)
    agency_id = me["agency"]["id"]
    assert (await _checkout_params(client, stripe_stub, agency_id))["subscription_data"][
        "trial_period_days"
    ] == 30
    stripe_stub.subscriptions_at_stripe = {ended: ["sub_old_001"]}

    params = await _checkout_params(client, stripe_stub, agency_id)

    assert params == _params_before_trials(settings, agency_id)
    assert stripe_stub.subscriptions_listed[-1] == LIST_ALL_SUBSCRIPTIONS


@pytest.mark.usefixtures("trial_30")
async def test_the_trial_changes_none_of_the_refusals(
    client: AsyncClient, engine, stripe_stub: _StripeStub  # noqa: ANN001
) -> None:
    """A trial is never a way round a live subscription: still a 409, still no Stripe call."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _set_status(engine, agency_id, "trialing")

    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")

    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "/problems/subscription-already-active"
    assert stripe_stub.sessions_created == []
    assert stripe_stub.customers_created == []


# -- the webhook: a trial, in both orders ---------------------------------------


def _trial_created(agency_id: str, *, created: int, event_id: str = "evt_trial_created") -> bytes:
    """`customer.subscription.created`, the subscription a trial checkout starts: `trialing`."""
    return _event(
        "customer.subscription.created",
        _subscription(status="trialing", agency_id=agency_id, trial_end=TRIAL_END_TS),
        event_id=event_id,
        created=created,
    )


def _trial_checkout(agency_id: str, *, created: int) -> bytes:
    """`checkout.session.completed` for a trial: nothing was paid, Stripe says so."""
    return _checkout(agency_id, created=created, payment_status="no_payment_required")


def _id_of(payload: bytes) -> str:
    return str(json.loads(payload)["id"])


@pytest.mark.parametrize("checkout_after", [0, 1, 60])
async def test_trialing_created_then_checkout_completed_leaves_the_agency_trialing(
    client: AsyncClient, engine, checkout_after: int  # noqa: ANN001
) -> None:
    """THE TRAP, order one: the subscription event lands first.

    `checkout.session.completed` writes `active`, because it cannot know better:
    the session carries no subscription status. With a trial that guess is wrong
    by construction, and if it were applied after the real `trialing` Settings
    would hide the trial and say Renews for a plan that has not been charged.
    Whatever second the checkout event is stamped with, it must not move the agency.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780010000
    created = _trial_created(agency_id, created=t)
    checkout = _trial_checkout(agency_id, created=t + checkout_after)

    assert (await _deliver(client, created)).json() == {"received": True, "handled": True}
    resp = await _deliver(client, checkout)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True, "handled": False, "stale": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "trialing"
    assert agency.subscription_trial_end == TRIAL_END
    assert agency.stripe_subscription_id == "sub_test_001"
    assert agency.stripe_customer_id == "cus_test_001"
    assert agency.stripe_event_at == _at(t)
    ledger = await _ledger(engine)
    assert ledger["evt_trial_created"].outcome.value == "applied"
    assert ledger[_id_of(checkout)].outcome.value == "stale"

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert body["subscriptionStatus"] == "trialing"
    assert body["isActive"] is True
    assert body["trialEndsAt"].startswith("2026-10-21T14:13:20")


@pytest.mark.parametrize("trialing_created_after_checkout", [-1, 0, 1])
async def test_checkout_completed_then_trialing_created_leaves_the_agency_trialing(
    client: AsyncClient, engine, trialing_created_after_checkout: int  # noqa: ANN001
) -> None:
    """THE TRAP, order two: the checkout event lands first.

    It writes its guess, `active`, for the moment until the subscription event
    says what the subscription really is. Checkout never moves the watermark, so
    the subscription event is never stale on its account, whichever second each is
    stamped with.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780020000
    checkout = _trial_checkout(agency_id, created=t)
    created = _trial_created(agency_id, created=t + trialing_created_after_checkout)

    assert (await _deliver(client, checkout)).json() == {"received": True, "handled": True}
    assert (await _agency(engine, agency_id)).subscription_status == "active"
    assert (await _deliver(client, created)).json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "trialing"
    assert agency.subscription_trial_end == TRIAL_END
    assert agency.stripe_event_at == _at(t + trialing_created_after_checkout)
    assert {r.outcome.value for r in (await _ledger(engine)).values()} == {"applied"}
    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert body["subscriptionStatus"] == "trialing"
    assert body["trialEndsAt"].startswith("2026-10-21T14:13:20")


async def test_the_two_trial_events_delivered_together_end_trialing_whichever_wins(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    """Concurrent deliveries serialise on the agency row, in either order."""
    base = 1780030000
    for i in range(6):
        me = await _sign_up(client, email=f"trial{i}@billing.example", agency=f"Trial {i}")
        agency_id = me["agency"]["id"]
        sub, cus = f"sub_trial_{i}", f"cus_trial_{i}"
        created = _event(
            "customer.subscription.created",
            _subscription(
                status="trialing", sub_id=sub, customer=cus, agency_id=agency_id,
                trial_end=TRIAL_END_TS,
            ),
            created=base,
        )
        checkout = _event(
            "checkout.session.completed",
            {
                "id": f"cs_trial_{i}", "object": "checkout.session", "mode": "subscription",
                "customer": cus, "subscription": sub, "payment_status": "no_payment_required",
                "metadata": {"agency_id": agency_id},
            },
            created=base + 1,
        )
        order = [created, checkout] if i % 2 == 0 else [checkout, created]
        results = await asyncio.wait_for(
            asyncio.gather(*[_deliver(client, p) for p in order]), 30
        )
        assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
        agency = await _agency(engine, agency_id)
        assert agency.subscription_status == "trialing", i
        assert agency.subscription_trial_end == TRIAL_END, i
        assert agency.stripe_event_at == _at(base), i


def test_checkouts_guess_never_overwrites_a_trial() -> None:
    """The rule itself, table-driven, with no database."""
    guard = billing_service._blocked_by_state_machine  # noqa: SLF001

    def agency(status: str | None) -> Agency:
        return Agency(stripe_subscription_id="sub_1", subscription_status=status)

    # The trap: checkout's `active` (a guess) over a `trialing` (a fact).
    assert guard(agency("trialing"), "sub_1", "active", optimistic=True) is True
    # The first charge is a subscription event saying `active`, and applies.
    assert guard(agency("trialing"), "sub_1", "active") is False
    # `trialing` is the ONLY status the guess is kept off: checkout's `active` still
    # applies over every other one, as it did before trials existed.
    for status in (None, "active", "incomplete", "past_due", "unpaid", "paused"):
        assert guard(agency(status), "sub_1", "active", optimistic=True) is False, status
    # A different subscription id is a new life, whatever the old one's status.
    assert guard(agency("trialing"), "sub_2", "active", optimistic=True) is False
    # A trial moves on to any real status a subscription event brings.
    for new in ("trialing", "active", "past_due", "unpaid", "paused", "canceled",
                "incomplete_expired"):
        assert guard(agency("trialing"), "sub_1", new) is False, new
    # The rule that was already there: `incomplete` is never written over a status.
    assert guard(agency("trialing"), "sub_1", "incomplete") is True


# -- the webhook: where a trial goes -------------------------------------------


async def _start_trial(client: AsyncClient, agency_id: str, *, created: int) -> None:
    resp = await _deliver(client, _trial_created(agency_id, created=created))
    assert resp.json() == {"received": True, "handled": True}, resp.text


async def test_trialing_becomes_active_at_the_first_charge(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780050000
    await _start_trial(client, agency_id, created=t)
    next_period_end = PERIOD_END_TS + 30 * 86400
    charged = _event(
        "customer.subscription.updated",
        _subscription(
            status="active", agency_id=agency_id, trial_end=TRIAL_END_TS,
            period_end=next_period_end,
        ),
        created=t + 100,
    )

    assert (await _deliver(client, charged)).json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "active"
    assert billing_service.is_active(agency) is True
    assert agency.subscription_current_period_end == _at(next_period_end)
    # The column mirrors what Stripe says, which keeps `trial_end` after a trial...
    assert agency.subscription_trial_end == TRIAL_END
    # ...and the API says there is no trial under way any more.
    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert body["subscriptionStatus"] == "active"
    assert body["isActive"] is True
    assert body["trialEndsAt"] is None
    assert body["scanLimit"] == 40


@pytest.mark.parametrize(
    ("event_type", "payload_status"),
    [
        pytest.param("customer.subscription.deleted", "canceled", id="deleted"),
        pytest.param("customer.subscription.updated", "canceled", id="updated to canceled"),
    ],
)
async def test_a_cancelled_trial_ends_canceled_and_free(
    client: AsyncClient, engine, event_type: str, payload_status: str  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780060000
    await _start_trial(client, agency_id, created=t)
    ended = _event(
        event_type,
        _subscription(status=payload_status, agency_id=agency_id, trial_end=TRIAL_END_TS),
        created=t + 100,
    )

    assert (await _deliver(client, ended)).json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "canceled"
    assert billing_service.is_active(agency) is False
    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert body["subscriptionStatus"] == "canceled"
    assert body["isActive"] is False
    assert body["trialEndsAt"] is None
    # Free again: the plan's forty a month went with the subscription.
    assert body["scanLimit"] == 1


async def test_a_trial_that_never_converts_ends_incomplete_expired_and_stays_ended(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780070000
    await _start_trial(client, agency_id, created=t)
    expired = _event(
        "customer.subscription.updated",
        _subscription(status="incomplete_expired", agency_id=agency_id, trial_end=TRIAL_END_TS),
        created=t + 100,
    )
    assert (await _deliver(client, expired)).json() == {"received": True, "handled": True}

    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "incomplete_expired"
    assert billing_service.is_active(agency) is False
    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert (body["isActive"], body["trialEndsAt"], body["scanLimit"]) == (False, None, 1)

    # A `trialing` event that was only late does not bring the trial back.
    late = _event(
        "customer.subscription.updated",
        _subscription(status="trialing", agency_id=agency_id, trial_end=TRIAL_END_TS),
        created=t + 100,
    )
    assert (await _deliver(client, late)).json() == {
        "received": True, "handled": False, "stale": True,
    }
    assert (await _agency(engine, agency_id)).subscription_status == "incomplete_expired"


async def test_a_trial_whose_first_charge_fails_is_past_due_not_trialing_and_not_active(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780080000
    await _start_trial(client, agency_id, created=t)
    failed = _event(
        "customer.subscription.updated",
        _subscription(status="past_due", agency_id=agency_id, trial_end=TRIAL_END_TS),
        created=t + 100,
    )
    assert (await _deliver(client, failed)).json() == {"received": True, "handled": True}

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()
    assert body["subscriptionStatus"] == "past_due"
    assert (body["isActive"], body["trialEndsAt"], body["scanLimit"]) == (False, None, 1)


async def test_trial_events_follow_the_same_duplicate_and_stale_rules(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    """Nothing about a trial exempts an event from the ledger or the watermark."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780090000
    created = _trial_created(agency_id, created=t)
    assert (await _deliver(client, created)).json() == {"received": True, "handled": True}
    # The same event again: a duplicate, and nothing is applied twice.
    assert (await _deliver(client, created)).json() == {
        "received": True, "handled": False, "duplicate": True,
    }
    # An older subscription event arriving late is stale and changes nothing.
    older = _event(
        "customer.subscription.updated",
        _subscription(status="active", agency_id=agency_id, trial_end=TRIAL_END_TS),
        created=t - 5,
    )
    assert (await _deliver(client, older)).json() == {
        "received": True, "handled": False, "stale": True,
    }
    agency = await _agency(engine, agency_id)
    assert agency.subscription_status == "trialing"
    assert agency.stripe_event_at == _at(t)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no trial_end on the payload"),
        pytest.param({"trial_end": None}, id="trial_end null"),
        pytest.param({"trial_end": "soon"}, id="trial_end not a number"),
        pytest.param({"trial_end": True}, id="trial_end a boolean"),
    ],
)
async def test_a_subscription_with_no_readable_trial_end_stores_none(
    client: AsyncClient, engine, payload: dict[str, Any]  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    sub = _subscription(status="active", agency_id=agency_id)
    sub.update(payload)
    assert (
        await _deliver(client, _event("customer.subscription.updated", sub))
    ).json()["handled"] is True
    assert (await _agency(engine, agency_id)).subscription_trial_end is None


# -- customer.subscription.trial_will_end: a log line, and nothing else ----------


def _will_end(agency_id: str, *, created: int) -> bytes:
    return _event(
        "customer.subscription.trial_will_end",
        _subscription(status="trialing", agency_id=agency_id, trial_end=TRIAL_END_TS),
        created=created,
    )


def _billing_state(agency: Agency) -> dict[str, Any]:
    """Every column a webhook may write, to compare before and after."""
    return {
        "stripe_customer_id": agency.stripe_customer_id,
        "stripe_subscription_id": agency.stripe_subscription_id,
        "subscription_status": agency.subscription_status,
        "subscription_current_period_end": agency.subscription_current_period_end,
        "subscription_cancel_at_period_end": agency.subscription_cancel_at_period_end,
        "subscription_trial_end": agency.subscription_trial_end,
        "stripe_event_at": agency.stripe_event_at,
    }


async def test_trial_will_end_logs_ids_only_and_changes_nothing(
    client: AsyncClient, engine, caplog, capsys  # noqa: ANN001
) -> None:
    me = await _sign_up(client, email="ends@billing.example")
    agency_id = me["agency"]["id"]
    t = 1780100000
    await _start_trial(client, agency_id, created=t)
    before = _billing_state(await _agency(engine, agency_id))
    ledger_before = set(await _ledger(engine))
    caplog.set_level("DEBUG")

    with structlog.testing.capture_logs() as events:
        resp = await _deliver(client, _will_end(agency_id, created=t + 200_000))

    assert resp.status_code == 200, resp.text
    # Handled (it was logged), and it applied nothing.
    assert resp.json() == {"received": True, "handled": True}
    lines = [e for e in events if e["event"] == "billing.trial_will_end"]
    assert len(lines) == 1, events
    assert lines[0]["log_level"] == "info"
    assert set(lines[0]) - {"event", "log_level"} == {"agency_id", "subscription_id"}
    assert lines[0]["agency_id"] == agency_id
    assert lines[0]["subscription_id"] == "sub_test_001"
    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    for secret in (FAKE_SECRET_KEY, FAKE_WEBHOOK_SECRET, "ends@billing.example"):
        assert secret not in everything, secret
    assert "@" not in json.dumps(events, default=str)

    # No state moved, the watermark included, and no ledger row was written.
    assert _billing_state(await _agency(engine, agency_id)) == before
    assert set(await _ledger(engine)) == ledger_before


async def test_trial_will_end_does_not_move_the_watermark(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    """If it did, every real event stamped before it would be stale from then on.

    It is delivered stamped far in the future; a subscription update stamped
    between the trial's start and that must still apply.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780110000
    await _start_trial(client, agency_id, created=t)
    assert (await _deliver(client, _will_end(agency_id, created=t + 500_000))).status_code == 200
    assert (await _agency(engine, agency_id)).stripe_event_at == _at(t)

    cancelling = _event(
        "customer.subscription.updated",
        _subscription(
            status="trialing", agency_id=agency_id, trial_end=TRIAL_END_TS,
            cancel_at_period_end=True,
        ),
        created=t + 5,
    )
    assert (await _deliver(client, cancelling)).json() == {"received": True, "handled": True}
    agency = await _agency(engine, agency_id)
    assert agency.subscription_cancel_at_period_end is True
    assert agency.stripe_event_at == _at(t + 5)


async def test_trial_will_end_on_an_agency_with_no_subscription_still_changes_nothing(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    """Delivered out of order, before anything else: it still writes no state."""
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    before = _billing_state(await _agency(engine, agency_id))

    with structlog.testing.capture_logs() as events:
        resp = await _deliver(client, _will_end(agency_id, created=1780120000))

    assert resp.json() == {"received": True, "handled": True}
    assert len([e for e in events if e["event"] == "billing.trial_will_end"]) == 1
    assert _billing_state(await _agency(engine, agency_id)) == before
    assert await _ledger(engine) == {}


async def test_trial_will_end_for_an_unknown_agency_is_unmatched_and_logs_no_trial_line(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    await _sign_up(client)
    payload = _event(
        "customer.subscription.trial_will_end",
        _subscription(status="trialing", customer="cus_nobody", trial_end=TRIAL_END_TS),
    )

    with structlog.testing.capture_logs() as events:
        resp = await _deliver(client, payload)

    assert resp.json() == {"received": True, "handled": False}
    assert not [e for e in events if e["event"] == "billing.trial_will_end"]
    assert [e for e in events if e["event"] == "stripe.webhook.unmatched"]
    assert await _ledger(engine) == {}


async def test_trial_will_end_writes_nothing_even_before_the_router_decides(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    """The router rolls back a result that does not write, which would hide a write made here.

    So the service is asked directly, in a session of its own: the result says it writes
    nothing, the agency row it locked is untouched in memory (no status, no watermark), and
    no ledger row was inserted into the transaction.
    """
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    t = 1780140000
    await _start_trial(client, agency_id, created=t)
    before = _billing_state(await _agency(engine, agency_id))
    ledger_before = set(await _ledger(engine))

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        result = await billing_service.apply_event(
            s, json.loads(_will_end(agency_id, created=t + 300_000))
        )
        assert result is billing_service.WebhookResult.LOGGED
        assert result.writes is False
        agency = (await s.execute(select(Agency).where(Agency.id == agency_id))).scalar_one()
        assert _billing_state(agency) == before
        assert not s.dirty and not s.new
        inserted = set((await s.execute(select(StripeWebhookEvent.id))).scalars().all())
        assert inserted == ledger_before
        await s.rollback()


# -- GET /billing: the trial's end -----------------------------------------------


@pytest.mark.parametrize(
    ("status", "stored_trial_end", "shown"),
    [
        pytest.param("trialing", TRIAL_END, True, id="trialing"),
        pytest.param("trialing", None, False, id="trialing, no end recorded"),
        pytest.param("active", TRIAL_END, False, id="active after its trial"),
        pytest.param("canceled", TRIAL_END, False, id="canceled"),
        pytest.param("past_due", TRIAL_END, False, id="past_due"),
        pytest.param("incomplete_expired", TRIAL_END, False, id="incomplete_expired"),
        pytest.param(None, None, False, id="no subscription"),
        pytest.param(None, TRIAL_END, False, id="no status, a stale end"),
    ],
)
async def test_billing_exposes_the_trial_end_only_while_the_agency_is_trialing(
    client: AsyncClient, engine, status: str | None, stored_trial_end: datetime | None,  # noqa: ANN001
    shown: bool,
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _set_fields(
        engine, agency_id, subscription_status=status, subscription_trial_end=stored_trial_end
    )

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()

    if shown:
        assert body["trialEndsAt"].startswith("2026-10-21T14:13:20"), body
    else:
        assert body["trialEndsAt"] is None, body


async def test_billing_for_a_trialing_agency_says_it_is_paid_up_and_has_the_full_allowance(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    me = await _sign_up(client)
    agency_id = me["agency"]["id"]
    await _set_fields(
        engine, agency_id, stripe_customer_id="cus_test_001", subscription_status="trialing",
        subscription_current_period_end=TRIAL_END, subscription_trial_end=TRIAL_END,
    )

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()

    assert body["subscriptionStatus"] == "trialing"
    assert body["isActive"] is True
    assert body["scanLimit"] == 40
    assert body["periodResetsAt"] is not None
    assert body["currentPeriodEnd"].startswith("2026-10-21T14:13:20")
    assert body["trialEndsAt"].startswith("2026-10-21T14:13:20")
    # Still nothing of Stripe's in the body.
    assert "cus_" not in json.dumps(body)


# -- GET /billing: trialEligible — whether Settings may promise the trial --------------
#
# Round two (security review L3). Settings must promise a trial only when checkout would give
# one, so the read says it: true only when a trial is on (`trial_days_offered`, which is 0
# wherever billing is not configured) AND this agency's own row passes the same test checkout
# applies (`_has_had_a_subscription` is false). A read makes no Stripe call, so it cannot see
# the other half of checkout's test, the customer's subscription list at Stripe.

# Every shape of row that is NOT "never subscribed", and the one that is. The second column is
# whether a trial is open to it, with a trial on and Stripe's own list empty.
TRIAL_ELIGIBILITY_ROWS = [
    pytest.param({}, True, id="a new agency"),
    pytest.param(
        {"stripe_customer_id": "cus_test_001"}, True,
        id="a customer and no subscription: it opened a checkout and left",
    ),
    *[
        pytest.param(row.values[0], False, id=str(row.id))
        for row in ROW_HAS_HAD_A_SUBSCRIPTION
    ],
    *[
        pytest.param({"subscription_status": status}, False, id=f"status {status}")
        for status in ("trialing", "active", *NEEDS_ATTENTION, UNKNOWN_STATUS, *ENDED)
    ],
]


@pytest.mark.usefixtures("trial_30")
@pytest.mark.parametrize(("row", "eligible"), TRIAL_ELIGIBILITY_ROWS)
async def test_billing_says_whether_the_agency_would_be_given_the_trial(
    client: AsyncClient, engine, row: dict[str, Any], eligible: bool  # noqa: ANN001
) -> None:
    """No `stripe_stub` is requested: a read that reached Stripe would fail on the guard."""
    me = await _sign_up_with_google(client, engine)
    agency_id = me["agency"]["id"]
    await _set_fields(engine, agency_id, **row)

    body = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()

    assert body["trialEligible"] is eligible, (row, body)


async def test_billing_says_no_trial_is_open_when_none_is_configured(
    client: AsyncClient, settings  # noqa: ANN001
) -> None:
    """A brand-new agency, and the trial off: nothing to offer, so nothing to promise."""
    assert settings.stripe_trial_days == 0
    me = await _sign_up(client)
    body = (await client.get(f"{BASE}/agencies/{me['agency']['id']}/billing")).json()
    assert body["trialEligible"] is False


@pytest.mark.usefixtures("trial_30")
@pytest.mark.parametrize("missing", ["stripe_secret_key", "stripe_price_id"])
async def test_billing_says_no_trial_is_open_where_checkout_cannot_start_one(
    client: AsyncClient, engine, settings, monkeypatch, missing: str  # noqa: ANN001
) -> None:
    """The same rule as `GET /billing/plan`: a trial nobody can start is not one to promise."""
    monkeypatch.setattr(settings, missing, None)
    me = await _sign_up_with_google(client, engine)
    body = (await client.get(f"{BASE}/agencies/{me['agency']['id']}/billing")).json()
    assert body["trialEligible"] is False


# The rows checkout can run for at all: only no subscription and an ended one may start it, and
# every other status is refused with a 409 before any session exists to compare.
TRIAL_CHECKOUT_ROWS = [
    row for row in TRIAL_ELIGIBILITY_ROWS
    if row.values[0].get("subscription_status") in (None, *ENDED)
]


@pytest.mark.usefixtures("trial_30")
@pytest.mark.parametrize(("row", "eligible"), TRIAL_CHECKOUT_ROWS)
async def test_billing_and_checkout_agree_on_who_gets_the_trial(
    client: AsyncClient,
    engine,  # noqa: ANN001
    stripe_stub: _StripeStub,
    row: dict[str, Any],
    eligible: bool,
) -> None:
    """The read and the checkout apply ONE row test: Settings promises what checkout gives.

    The session checkout makes carries the trial exactly when `trialEligible` was true. Stripe's
    own list is empty here, which is the half of checkout's test a read cannot see.
    """
    me = await _sign_up_with_google(client, engine)
    agency_id = me["agency"]["id"]
    await _set_fields(engine, agency_id, **row)

    said = (await client.get(f"{BASE}/agencies/{agency_id}/billing")).json()["trialEligible"]
    params = await _checkout_params(client, stripe_stub, agency_id)
    given = "trial_period_days" in params["subscription_data"]

    assert said is eligible
    assert given is eligible
    assert said is given


@pytest.mark.parametrize(("row", "eligible"), TRIAL_ELIGIBILITY_ROWS)
def test_the_row_test_is_one_function_for_the_read_and_checkout(
    settings, monkeypatch, row: dict[str, Any], eligible: bool  # noqa: ANN001
) -> None:
    """`trial_days_for` is what both read: the days this agency's row would be given."""
    monkeypatch.setattr(settings, "stripe_trial_days", 30)
    agency = Agency(**row)
    assert billing_service.trial_days_for(agency, settings) == (30 if eligible else 0)
    monkeypatch.setattr(settings, "stripe_trial_days", 0)
    assert billing_service.trial_days_for(agency, settings) == 0


# -- GET /billing/plan: the trial, said in public --------------------------------

PLAN_PATH = f"{BASE}/billing/plan"


async def test_the_plan_says_no_trial_by_default(client: AsyncClient) -> None:
    resp = await client.get(PLAN_PATH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"trialDays": 0}


@pytest.mark.parametrize("days", [1, 30, 730])
async def test_the_plan_says_the_trial_the_setting_says(
    client: AsyncClient, settings, monkeypatch, days: int  # noqa: ANN001
) -> None:
    monkeypatch.setattr(settings, "stripe_trial_days", days)
    resp = await client.get(PLAN_PATH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"trialDays": days}


@pytest.mark.parametrize("missing", ["stripe_secret_key", "stripe_price_id"])
async def test_the_plan_claims_no_trial_where_checkout_cannot_start_one(
    client: AsyncClient, settings, monkeypatch, missing: str  # noqa: ANN001
) -> None:
    """A trial on a deployment that cannot take a checkout is a promise nobody can use.

    `STRIPE_TRIAL_DAYS=30` with the key or the Price unset is a half-configured
    deployment, and checkout there answers 503; the public page must not say 30.
    """
    monkeypatch.setattr(settings, "stripe_trial_days", 30)
    monkeypatch.setattr(settings, missing, None)
    assert (await client.get(PLAN_PATH)).json() == {"trialDays": 0}


async def test_the_plan_is_public_carries_no_agency_data_and_sets_no_cookie(
    client: AsyncClient, settings, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(settings, "stripe_trial_days", 30)
    # No sign-up, no session: a stranger's request.
    anonymous = await client.get(PLAN_PATH)
    assert anonymous.status_code == 200, anonymous.text
    assert set(anonymous.json()) == {"trialDays"}
    assert "set-cookie" not in anonymous.headers

    # The same body for a signed-in owner: nothing about the caller is in it.
    me = await _sign_up(client, email="plan@billing.example")
    signed_in = await client.get(PLAN_PATH)
    assert signed_in.json() == anonymous.json()
    text_out = signed_in.text
    for private in (me["agency"]["id"], "plan@billing.example", "cus_", "sub_", FAKE_PRICE_ID):
        assert private not in text_out, private


async def test_the_plan_is_never_cached(
    client: AsyncClient, settings, monkeypatch  # noqa: ANN001
) -> None:
    """The page's sentence must follow the setting, not a copy a browser kept.

    A short cache would let a page say "30 days free" for minutes after a deploy
    turned the trial off, and checkout would not give one.
    """
    monkeypatch.setattr(settings, "stripe_trial_days", 30)
    resp = await client.get(PLAN_PATH)
    assert resp.headers["cache-control"] == "no-store"
    # And it is really read each time: the next answer follows the setting.
    monkeypatch.setattr(settings, "stripe_trial_days", 0)
    assert (await client.get(PLAN_PATH)).json() == {"trialDays": 0}


async def test_the_plan_is_a_read_the_origin_check_and_cors_leave_as_they_were(
    client: AsyncClient, settings, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(settings, "stripe_trial_days", 30)
    stranger = {"Origin": "https://evil.example"}
    # A GET is not an Origin-checked write, so it answers, wherever it comes from...
    from_elsewhere = await client.get(PLAN_PATH, headers=stranger)
    assert from_elsewhere.status_code == 200
    # ...but CORS still withholds the answer from a page on an unlisted origin,
    assert "access-control-allow-origin" not in from_elsewhere.headers
    # and still lets the web app's own origin read it.
    listed = await client.get(PLAN_PATH, headers={"Origin": client.headers["Origin"]})
    assert listed.headers["access-control-allow-origin"] == client.headers["Origin"]
    # It is a read and only a read: a write to it is refused as the wrong method.
    write = await client.post(PLAN_PATH, headers={"Origin": client.headers["Origin"]})
    assert write.status_code == 405


async def test_the_contract_carries_the_trial(client: AsyncClient) -> None:
    schema = (await client.get(f"{BASE}/openapi.json")).json()

    plan = schema["paths"][PLAN_PATH]["get"]
    ref = plan["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    out = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
    assert out["properties"]["trialDays"]["type"] == "integer"
    assert out["required"] == ["trialDays"]

    status = schema["components"]["schemas"]["BillingStatusOut"]
    assert "trialEndsAt" in status["properties"]
    assert "trialEndsAt" in status["required"]
    # Round two: whether Settings may promise the trial, as a plain required boolean.
    assert status["properties"]["trialEligible"]["type"] == "boolean"
    assert "trialEligible" in status["required"]


# -- nothing secret in a trial's log lines --------------------------------------


async def test_a_trial_checkout_and_its_events_log_no_key_secret_or_address(
    client: AsyncClient, engine, settings, monkeypatch, stripe_stub: _StripeStub, caplog, capsys  # noqa: ANN001
) -> None:
    """The whole trial, start to first charge: ids, numbers and event names only."""
    monkeypatch.setattr(settings, "stripe_trial_days", 30)
    email = "private.owner@billing.example"
    me = await _sign_up_with_google(client, engine, email=email)
    agency_id = me["agency"]["id"]
    caplog.set_level("DEBUG")
    t = 1780130000

    with structlog.testing.capture_logs() as events:
        await _checkout_params(client, stripe_stub, agency_id)
        await _deliver(client, _trial_created(agency_id, created=t))
        await _deliver(client, _trial_checkout(agency_id, created=t))
        await _deliver(client, _will_end(agency_id, created=t + 1000))
        await _deliver(
            client,
            _event(
                "customer.subscription.updated",
                _subscription(status="active", agency_id=agency_id, trial_end=TRIAL_END_TS),
                created=t + 2000,
            ),
        )

    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    for secret in (FAKE_SECRET_KEY, FAKE_WEBHOOK_SECRET, "whsec_", "sk_test_", email):
        assert secret not in everything, secret
    assert "@" not in json.dumps(events, default=str)
    # The lines this change adds or extends are there, and every field in them is an id,
    # a count or a status: never free text.
    seen = {e["event"] for e in events}
    assert {"billing.checkout_created", "billing.trial_will_end"} <= seen
    for event in events:
        if event["event"].startswith(("billing.", "stripe.webhook.")):
            for key, value in event.items():
                assert isinstance(value, str | int | bool) or value is None, (event["event"], key)

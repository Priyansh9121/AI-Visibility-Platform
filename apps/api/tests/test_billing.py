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

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api.models import Agency, Invitation
from avp_api.security import token_digest
from avp_api.services import billing as billing_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
MEMBER_PASSWORD = "an-entirely-different-passphrase"

FAKE_SECRET_KEY = "sk_test_not_a_real_key_for_tests_only"
FAKE_PRICE_ID = "price_test_fake"
FAKE_WEBHOOK_SECRET = "whsec_test_fake_signing_secret"

# A fixed instant, so "renews on" assertions do not drift with the clock.
PERIOD_END_TS = 1790000000  # 2026-09-21T14:13:20Z
PERIOD_END = datetime.fromtimestamp(PERIOD_END_TS, tz=UTC)


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
        self.next_customer_id = "cus_test_001"
        self.next_session_url = "https://checkout.stripe.test/c/pay/cs_test_001"
        self.session_url_override: str | None = ""

    # -- shape mirroring `client.v1.<resource>.<verb>_async` ----------------
    @property
    def v1(self) -> Any:
        stub = self

        class _Customers:
            async def create_async(self, params: dict[str, Any]) -> Any:
                stub.customers_created.append(params)
                return SimpleNamespace(id=stub.next_customer_id)

        class _Sessions:
            async def create_async(self, params: dict[str, Any]) -> Any:
                stub.sessions_created.append(params)
                url = (
                    stub.next_session_url
                    if stub.session_url_override == ""
                    else stub.session_url_override
                )
                return SimpleNamespace(id="cs_test_001", url=url)

        return SimpleNamespace(
            customers=_Customers(),
            checkout=SimpleNamespace(sessions=_Sessions()),
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


def _event(event_type: str, obj: dict[str, Any]) -> bytes:
    return json.dumps(
        {"id": "evt_test_001", "type": event_type, "data": {"object": obj}}
    ).encode()


def _subscription(
    *,
    status: str = "active",
    sub_id: str = "sub_test_001",
    customer: str = "cus_test_001",
    agency_id: str | None = None,
    period_end: int | None = PERIOD_END_TS,
    legacy_period_end: bool = False,
) -> dict[str, Any]:
    """A Subscription payload shaped like the pinned API version sends one.

    `current_period_end` lives on the ITEM, not on the subscription — that is
    the change in `2026-08-26.dahlia` that would otherwise have made every
    renewal date silently absent. `legacy_period_end` produces the older shape,
    which replayed events and older endpoint API versions still deliver.
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
# authority — Epic 9.14's rules, applied to money
# ---------------------------------------------------------------------------


async def test_billing_requires_a_session(client: AsyncClient) -> None:
    for method, path in [
        ("GET", f"{BASE}/agencies/agcy_01ANY/billing"),
        ("POST", f"{BASE}/agencies/agcy_01ANY/billing/checkout"),
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
    }

    # And nothing is withheld because of it. The dashboard is the cheapest
    # proof that no paywall was introduced by this epic.
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

"""A free trial needs a verified Google identity on the user who starts checkout — 2026-10-05.

Founder decision (launch checklist 3.72): the free scan's rule, `has_verified_identity(user)`
(a linked `google_sub`), applies to the free TRIAL too. The rule is on the USER starting the
checkout, not the agency: a password-only owner gets the plain checkout. Since checkout became
owner-only (launch checklist 3.130, 2026-10-11) the user starting it is always the agency's owner:
an admin is refused with a 403 whatever identity they hold, and the identity of an admin seat
beside the owner never decides the owner's trial. Without the identity checkout still works,
exactly as a no-trial checkout does.

`GET /agencies/{id}/billing` says the same in advance, for the requesting user:
`trialEligible` (their Subscribe would get the trial) and `trialRequiresGoogle` (it would not,
and Google is the only thing missing). An admin has no Subscribe, so both are false for an admin,
and are not worked out from the owner's identity (launch checklist 3.130).

Fixtures and the Stripe fake are `test_billing.py`'s, imported (nothing here reaches Stripe).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api.models import Agency, Invitation, User
from avp_api.security import token_digest

# Fixtures (the autouse ones apply here once imported) and helpers, not tests.
from test_billing import (  # noqa: F401
    BASE,
    PASSWORD,
    _agency,
    _fake_stripe_settings,
    _no_unstubbed_stripe,
    _sign_up,
    _StripeStub,
    stripe_stub,
)

TRIAL_DAYS = 30
OWNER_EMAIL = "owner-trial@billing.example"


@pytest.fixture
def trial_on(settings, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "stripe_trial_days", TRIAL_DAYS)


def _factory(engine):  # noqa: ANN001, ANN202
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _link_google(engine, email: str) -> None:  # noqa: ANN001
    async with _factory(engine)() as s:
        await s.execute(
            update(User).where(User.email == email).values(google_sub=f"sub-{email}")
        )
        await s.commit()


async def _sign_in_as_admin(client: AsyncClient, engine, agency_id: str, *, google: bool) -> str:  # noqa: ANN001
    """Invite an ADMIN, accept as them (the client now holds their cookie), return their email."""
    email = "admin-trial@billing.example"
    resp = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": "admin"}
    )
    assert resp.status_code == 201, resp.text
    async with _factory(engine)() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest("test-token-admin")
        await s.commit()
    accepted = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": "test-token-admin", "fullName": "Ad Min", "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["role"] == "admin"
    if google:
        await _link_google(engine, email)
    return email


async def _sign_in_as_owner(client: AsyncClient) -> None:
    """Sign the owner back in: after `_sign_in_as_admin` the client holds the admin's cookie."""
    resp = await client.post(
        f"{BASE}/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["role"] == "owner"


async def _user_id(engine, email: str) -> str:  # noqa: ANN001
    async with _factory(engine)() as s:
        return (await s.execute(select(User.id).where(User.email == email))).scalar_one()


async def _owner(client: AsyncClient, engine, *, google: bool) -> str:  # noqa: ANN001
    me = await _sign_up(client, email=OWNER_EMAIL)
    if google:
        await _link_google(engine, OWNER_EMAIL)
    return me["agency"]["id"]


async def _checkout(client: AsyncClient, agency_id: str) -> Any:
    resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 201, resp.text
    return resp


async def _billing(client: AsyncClient, agency_id: str) -> dict[str, Any]:
    resp = await client.get(f"{BASE}/agencies/{agency_id}/billing")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# checkout
# ---------------------------------------------------------------------------


async def test_a_google_linked_owner_gets_the_trial(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on  # noqa: ANN001, F811
) -> None:
    agency_id = await _owner(client, engine, google=True)
    await _checkout(client, agency_id)
    (params,) = stripe_stub.sessions_created
    assert params["subscription_data"]["trial_period_days"] == TRIAL_DAYS
    assert params["payment_method_collection"] == "always"


async def test_a_password_only_owner_gets_a_plain_checkout(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on  # noqa: ANN001, F811
) -> None:
    agency_id = await _owner(client, engine, google=False)
    resp = await _checkout(client, agency_id)
    assert resp.json() == {"url": stripe_stub.next_session_url}
    (params,) = stripe_stub.sessions_created
    assert "trial_period_days" not in params["subscription_data"]
    assert "payment_method_collection" not in params
    # The rest of the session is what it always was.
    assert params["mode"] == "subscription"
    assert params["subscription_data"]["metadata"] == {"agency_id": agency_id}


@pytest.mark.parametrize("google", [True, False])
async def test_an_admin_does_not_start_a_checkout_whatever_identity_they_hold(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on, google: bool  # noqa: ANN001, F811
) -> None:
    """Checkout is the owner's alone (launch checklist 3.130): an admin's Google changes nothing.

    The owner has Google in both cases, so a trial would be on offer if the admin's checkout got
    through at all. It is refused with the role's 403, asks Stripe nothing and logs no checkout.
    """
    agency_id = await _owner(client, engine, google=True)
    await _sign_in_as_admin(client, engine, agency_id, google=google)
    with structlog.testing.capture_logs() as events:
        resp = await client.post(f"{BASE}/agencies/{agency_id}/billing/checkout")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == "/problems/permission-denied"
    assert stripe_stub.call_log == []
    assert [e for e in events if e["event"] == "billing.checkout_created"] == []


@pytest.mark.parametrize(("owner_google", "admin_google"), [(True, False), (False, True)])
async def test_the_owners_identity_decides_the_trial_not_an_admin_seat_beside_them(
    client: AsyncClient,
    engine,  # noqa: ANN001
    stripe_stub: _StripeStub,  # noqa: F811
    trial_on,  # noqa: ANN001, F811
    owner_google: bool,
    admin_google: bool,
) -> None:
    """The rule is on the user who starts the checkout, and that is the owner. No Google is lent.

    A password-only owner next to an admin who has Google used to get the trial through the
    admin's checkout. The admin cannot start one now, so that owner's checkout is the plain one;
    and a Google owner is not held back by a password-only admin.
    """
    agency_id = await _owner(client, engine, google=owner_google)
    await _sign_in_as_admin(client, engine, agency_id, google=admin_google)
    await _sign_in_as_owner(client)
    await _checkout(client, agency_id)
    (params,) = stripe_stub.sessions_created
    assert ("trial_period_days" in params["subscription_data"]) is owner_google
    assert ("payment_method_collection" in params) is owner_google


@pytest.mark.parametrize("google", [True, False])
async def test_with_the_trial_off_checkout_is_unchanged(
    client: AsyncClient, engine, stripe_stub: _StripeStub, settings, monkeypatch, google: bool  # noqa: ANN001, F811
) -> None:
    monkeypatch.setattr(settings, "stripe_trial_days", 0)
    agency_id = await _owner(client, engine, google=google)
    with structlog.testing.capture_logs() as events:
        await _checkout(client, agency_id)
    (params,) = stripe_stub.sessions_created
    assert "trial_period_days" not in params["subscription_data"]
    assert "payment_method_collection" not in params
    (created,) = [e for e in events if e["event"] == "billing.checkout_created"]
    # Nothing was withheld: there was no trial to withhold.
    assert created["trial_days"] == 0
    assert created["trial_withheld_no_google"] is False


@pytest.mark.parametrize("google", [True, False])
async def test_an_agency_that_had_a_subscription_is_unchanged(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on, google: bool  # noqa: ANN001, F811
) -> None:
    agency_id = await _owner(client, engine, google=google)
    async with _factory(engine)() as s:
        await s.execute(
            update(Agency).where(Agency.id == agency_id).values(subscription_status="canceled")
        )
        await s.commit()
    with structlog.testing.capture_logs() as events:
        await _checkout(client, agency_id)
    (params,) = stripe_stub.sessions_created
    assert "trial_period_days" not in params["subscription_data"]
    (created,) = [e for e in events if e["event"] == "billing.checkout_created"]
    assert created["trial_withheld_no_google"] is False


async def test_a_customer_stripe_remembers_a_subscription_for_is_not_called_withheld(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on  # noqa: ANN001, F811
) -> None:
    """Stripe's half of the old rule ends the trial for a Google user too; Google is not why."""
    agency_id = await _owner(client, engine, google=False)
    await _checkout(client, agency_id)  # makes the customer
    stripe_stub.subscriptions_at_stripe = {"canceled": ["sub_old"]}
    with structlog.testing.capture_logs() as events:
        await _checkout(client, agency_id)
    (created,) = [e for e in events if e["event"] == "billing.checkout_created"]
    assert created["trial_days"] == 0
    assert created["trial_withheld_no_google"] is False


async def test_the_log_line_says_withheld_and_carries_no_email(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on  # noqa: ANN001, F811
) -> None:
    agency_id = await _owner(client, engine, google=False)
    with structlog.testing.capture_logs() as events:
        await _checkout(client, agency_id)
    (created,) = [e for e in events if e["event"] == "billing.checkout_created"]
    assert created["trial_days"] == 0
    assert created["trial_withheld_no_google"] is True
    assert created["agency_id"] == agency_id
    assert created["user_id"] == await _user_id(engine, OWNER_EMAIL)
    assert "@" not in json.dumps(created, default=str)
    assert "@" not in json.dumps(events, default=str)


async def test_a_trial_given_is_logged_as_not_withheld(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on  # noqa: ANN001, F811
) -> None:
    agency_id = await _owner(client, engine, google=True)
    with structlog.testing.capture_logs() as events:
        await _checkout(client, agency_id)
    (created,) = [e for e in events if e["event"] == "billing.checkout_created"]
    assert created["trial_days"] == TRIAL_DAYS
    assert created["trial_withheld_no_google"] is False
    assert "@" not in json.dumps(created, default=str)


# ---------------------------------------------------------------------------
# GET /billing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("google", "eligible", "requires_google"),
    [(True, True, False), (False, False, True)],
)
async def test_billing_says_what_this_users_checkout_would_give(
    client: AsyncClient, engine, trial_on, google: bool, eligible: bool, requires_google: bool  # noqa: ANN001
) -> None:
    agency_id = await _owner(client, engine, google=google)
    body = await _billing(client, agency_id)
    assert body["trialEligible"] is eligible
    assert body["trialRequiresGoogle"] is requires_google


@pytest.mark.parametrize(
    ("owner_google", "admin_google"), [(True, True), (True, False), (False, True), (False, False)]
)
async def test_an_admin_reads_no_trial_whoever_has_linked_google(
    client: AsyncClient, engine, trial_on, owner_google: bool, admin_google: bool  # noqa: ANN001
) -> None:
    """Only the owner can subscribe (launch checklist 3.130): for an admin both fields are false.

    Both fields say what a Subscribe from THIS sign-in would get, and an admin has none, so they
    are false whatever the admin has linked. They are not worked out from the owner's identity
    either: that would tell an admin whether the owner has linked Google, and the answer here is
    the same in all four ways round. The owner's own values are what they were with no admin
    seat beside them.
    """
    agency_id = await _owner(client, engine, google=owner_google)
    alone = await _billing(client, agency_id)
    await _sign_in_as_admin(client, engine, agency_id, google=admin_google)
    as_admin = await _billing(client, agency_id)
    assert (as_admin["trialEligible"], as_admin["trialRequiresGoogle"]) == (False, False)
    await _sign_in_as_owner(client)
    as_owner = await _billing(client, agency_id)
    assert (as_owner["trialEligible"], as_owner["trialRequiresGoogle"]) == (
        owner_google,
        not owner_google,
    )
    assert (as_owner["trialEligible"], as_owner["trialRequiresGoogle"]) == (
        alone["trialEligible"],
        alone["trialRequiresGoogle"],
    )


@pytest.mark.parametrize("google", [True, False])
async def test_billing_with_the_trial_off_promises_nothing_and_asks_for_no_google(
    client: AsyncClient, engine, settings, monkeypatch, google: bool  # noqa: ANN001
) -> None:
    monkeypatch.setattr(settings, "stripe_trial_days", 0)
    agency_id = await _owner(client, engine, google=google)
    body = await _billing(client, agency_id)
    assert (body["trialEligible"], body["trialRequiresGoogle"]) == (False, False)


@pytest.mark.parametrize("google", [True, False])
async def test_billing_for_an_agency_that_had_a_subscription_asks_for_no_google(
    client: AsyncClient, engine, trial_on, google: bool  # noqa: ANN001
) -> None:
    """No trial for this agency whatever the user has, so Google would change nothing."""
    agency_id = await _owner(client, engine, google=google)
    async with _factory(engine)() as s:
        await s.execute(
            update(Agency).where(Agency.id == agency_id).values(subscription_status="canceled")
        )
        await s.commit()
    body = await _billing(client, agency_id)
    assert (body["trialEligible"], body["trialRequiresGoogle"]) == (False, False)
    assert (await _agency(engine, agency_id)).subscription_status == "canceled"

"""A free trial needs a verified Google identity on the user who starts checkout — 2026-10-05.

Founder decision (launch checklist 3.72): the free scan's rule, `has_verified_identity(user)`
(a linked `google_sub`), applies to the free TRIAL too. The rule is on the USER starting the
checkout, not the agency: a password-only owner gets the plain checkout, and so does a
password-only admin of an agency whose owner has Google. Without the identity checkout still
works, exactly as a no-trial checkout does.

`GET /agencies/{id}/billing` says the same in advance, for the requesting user:
`trialEligible` (their Subscribe would get the trial) and `trialRequiresGoogle` (it would not,
and Google is the only thing missing).

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


@pytest.mark.parametrize(("google", "trial"), [(True, True), (False, False)])
async def test_the_rule_is_on_the_admin_who_starts_it_not_the_owner(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on, google: bool, trial: bool  # noqa: ANN001, F811
) -> None:
    """The owner has Google in both cases; the admin who clicks Subscribe decides."""
    agency_id = await _owner(client, engine, google=True)
    await _sign_in_as_admin(client, engine, agency_id, google=google)
    await _checkout(client, agency_id)
    (params,) = stripe_stub.sessions_created
    assert ("trial_period_days" in params["subscription_data"]) is trial
    assert ("payment_method_collection" in params) is trial


async def test_a_google_owner_is_not_held_back_by_a_password_only_admin_elsewhere(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on  # noqa: ANN001, F811
) -> None:
    """The other direction: a password-only owner, a Google admin: the admin's checkout has it."""
    agency_id = await _owner(client, engine, google=False)
    await _sign_in_as_admin(client, engine, agency_id, google=True)
    await _checkout(client, agency_id)
    (params,) = stripe_stub.sessions_created
    assert params["subscription_data"]["trial_period_days"] == TRIAL_DAYS


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


async def test_the_log_names_the_admin_who_started_it_not_the_owner(
    client: AsyncClient, engine, stripe_stub: _StripeStub, trial_on  # noqa: ANN001, F811
) -> None:
    agency_id = await _owner(client, engine, google=True)
    admin_email = await _sign_in_as_admin(client, engine, agency_id, google=False)
    with structlog.testing.capture_logs() as events:
        await _checkout(client, agency_id)
    (created,) = [e for e in events if e["event"] == "billing.checkout_created"]
    admin_id = await _user_id(engine, admin_email)
    assert created["user_id"] == admin_id
    assert created["user_id"] != await _user_id(engine, OWNER_EMAIL)
    assert created["trial_withheld_no_google"] is True
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


async def test_billing_reads_per_user_not_per_agency(
    client: AsyncClient, engine, trial_on  # noqa: ANN001
) -> None:
    agency_id = await _owner(client, engine, google=True)
    assert (await _billing(client, agency_id))["trialEligible"] is True
    await _sign_in_as_admin(client, engine, agency_id, google=False)
    body = await _billing(client, agency_id)
    assert (body["trialEligible"], body["trialRequiresGoogle"]) == (False, True)


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

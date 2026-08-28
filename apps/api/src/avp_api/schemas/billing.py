"""Billing schemas — Epic 9.15.

WHAT THESE RESPONSES DELIBERATELY DO NOT CARRY
----------------------------------------------
No `stripe_customer_id` and no `stripe_subscription_id`. The browser has no use
for either: it cannot call Stripe, and every action it can take goes through an
endpoint here that already knows which agency it is acting for. Putting a
vendor's internal identifier in a response body would mean it appears in a
network panel, a proxy log and any error report the frontend ever sends — for no
capability gained.

`hasBillingAccount` is the derived fact the screen actually needs: whether there
is a customer at Stripe for the portal to open. It is a boolean rather than the
id itself for exactly the reason above.
"""

from __future__ import annotations

from datetime import datetime

from .common import ApiModel


class BillingStatusOut(ApiModel):
    """An agency's subscription, as recorded — never as freshly asked of Stripe.

    Every field here is read from the `agencies` row. **This endpoint makes no
    call to Stripe**, which is a deliberate property rather than an optimisation:
    a settings screen that reached a payment API on every page load would be
    slower, would fail when Stripe was slow, and would put a third party in the
    path of a page that has four other things to render. The webhook is what
    keeps these columns true.

    `subscriptionStatus` is Stripe's own string, passed through unmapped — see
    `models/tenancy.py` for why the column is not an enum. A client that wants
    to know "is this agency paid up" should read `isActive` rather than
    comparing strings, because the set of statuses that mean yes is a decision
    that lives in `services/billing.py` and may grow.
    """

    subscription_status: str | None
    is_active: bool
    current_period_end: datetime | None
    has_billing_account: bool


class CheckoutSessionOut(ApiModel):
    """Where to send the browser to pay.

    A URL and nothing else. The frontend navigates to it; there is no Stripe
    JavaScript library in `apps/web` and this shape is why one is not needed.
    """

    url: str


class PortalSessionOut(ApiModel):
    """Where to send the browser to manage an existing subscription.

    Same shape as `CheckoutSessionOut` and deliberately a separate class. They
    are two different resources with two different preconditions — a portal
    session requires an existing customer and a checkout session creates one —
    and collapsing them into a shared `UrlOut` would make the OpenAPI schema
    describe them as interchangeable.
    """

    url: str

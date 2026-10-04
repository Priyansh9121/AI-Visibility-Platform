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
    # --- the scan allowance — 2026-09-28, `services/scan_allowance.py` --------
    # Scans started this period that hold a slot (not failed, not cancelled):
    # this calendar month (UTC) for a paid agency, lifetime for a free one.
    scans_used_this_period: int
    # 1 free for life, 40 a month on the plan — or null when billing is not
    # configured on this deployment and no gate applies. The web reads the
    # numbers from here and never carries its own copy of them.
    scan_limit: int | None
    # First instant of next month (UTC) for a paid agency; null for free,
    # whose allowance does not reset.
    period_resets_at: datetime | None
    # Cancelled, but paid until `current_period_end` — 2026-09-29. Stripe's
    # portal cancels at the end of the period, so the subscription stays
    # active (and `is_active` true) until then; the screen says "Ends", not
    # "Renews". Always false for a subscription that is over or never began.
    cancel_at_period_end: bool
    # True when THIS sign-in would be refused the free scan for want of a
    # linked Google identity — 2026-09-29. Only ever true with the gate on, the
    # agency free and the requesting user holding no `google_sub`; exactly the
    # case a scan start answers `402 SCAN_LIMIT_VERIFY_REQUIRED`. The web reads
    # it and never works it out itself.
    free_scan_requires_google: bool
    # When the free trial ends and the card is first charged — 2026-10-04. Set
    # ONLY while the status is `trialing`, and null in every other state: after
    # the trial converts the date is history, and an agency that never had one
    # has none. The web says "Free trial until <day and time, UTC>, then <price> a
    # month" from it and nothing else, so a trial is never claimed for an agency
    # that is not in one.
    trial_ends_at: datetime | None
    # Whether Settings may promise this agency a free trial — 2026-10-04, round two. True only
    # when a trial is on (`STRIPE_TRIAL_DAYS` above 0, with billing configured: the same
    # reading `GET /billing/plan` gives) AND this agency's own row has never had a
    # subscription, the row test checkout applies. It CANNOT see the other half of checkout's
    # test, the customer's subscription list at Stripe: a read never calls Stripe. So true
    # means nothing in our record says no, and checkout can still refuse the trial to a
    # customer Stripe remembers an ended subscription for. The web promises a trial only on
    # a true and never works it out itself.
    trial_eligible: bool


class BillingPlanOut(ApiModel):
    """What the plan offers the public — today, only the length of its free trial.

    `GET /billing/plan`, with no session, no agency and nothing secret in it:
    it is read by the public pricing card and landing page, before anybody has
    signed up. `trialDays` is `STRIPE_TRIAL_DAYS`, the one place the length of
    the trial is stated, so what a page says and what checkout does cannot
    disagree. **0 means there is no trial**, and is also the answer on a
    deployment where checkout is not configured (a trial nobody can start is
    not one to promise). A page says "the first N days after you subscribe are
    free" only for an N above 0.

    It describes the offer to a NEW agency. Whether a particular agency still
    gets the trial is not here: that is the agency's own history, and checkout
    decides it (`services/billing.create_checkout_session`).
    """

    trial_days: int


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


class WebhookAck(ApiModel):
    """The webhook's answer to Stripe. Read by a human running `stripe listen`.

    `duplicate` and `stale` are present only when true (the route drops `None`),
    so an applied, ignored or unmatched event keeps the two-key body it always
    had. Stripe only looks at the status code.
    """

    received: bool
    handled: bool
    duplicate: bool | None = None
    stale: bool | None = None

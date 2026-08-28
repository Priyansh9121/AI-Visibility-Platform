"""Subscription billing against Stripe — Epic 9.15.

The first commercial code in this product. north-star.md §5.4 row 3 recorded
the vendor as "NOT decided"; it is decided now, and this module is the whole of
the integration.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
It is flat monthly subscription billing against ONE Stripe Price. This commit
carries the half a browser can reach: create a Stripe customer once, and create
a Checkout Session against the configured Price. The webhook that believes
Stripe, and the hosted portal, land in their own commits after this one.

It is **not** metered or usage-based billing, which north-star.md §5.2 argues
is the right eventual model. That needs §5.4 row 2's `UsageRecord` to exist
first, and it does not. Nothing here should be read as a step taken toward it
or away from it.

It is **not** a paywall. No function in this module is consulted before letting
anybody do anything. `subscription_status` records whether an agency PAYS; it
does not record what an agency MAY DO, and no caller treats it as though it
did. Sign-up, onboarding, scanning, scoring and reporting are exactly as free
as they were before this file existed.

TEST-MODE KEYS, AND NO CODE THAT KNOWS THE DIFFERENCE
-----------------------------------------------------
The keys this runs against are `sk_test_…`. **There is deliberately no live/test
branch anywhere in this module, and no setting that selects one.** Going live is
swapping two environment variables, which is the founder's decision to make when
Stripe's business verification is done. If the switch needed a code change, the
code would be the thing standing between a decision and its effect — and that
change would get written in a hurry, on the day revenue was waiting on it.

WHY THE OFFICIAL SDK, WHEN `email.py` REFUSED THE `resend` SDK
--------------------------------------------------------------
`services/email.py` rejected a vendor SDK and used `httpx` directly, and its
reasoning was good: the send is one authenticated JSON POST, and the SDK's
default client is synchronous `requests` inside an async service.

Neither half of that applies here.

The first half fails because of `verify_webhook` below. Stripe's webhook
signature scheme is a timestamped HMAC with a replay window, and hand-rolling
the verification of a payments webhook — the one place where getting it wrong
means believing an attacker who says they paid — is not a saving, it is a
liability. `stripe.Webhook.construct_event` is the reason this dependency
exists; everything else it does here is a bonus.

The second half fails on inspection: this module only ever calls the SDK's
`*_async` methods, and `stripe` 15.6 routes those through its `HTTPXClient`
when `httpx` is importable, which it is — `httpx` has been a direct dependency
since Epic 3. So no call here blocks the event loop, and `email.py`'s objection
is answered rather than ignored.

Licence: `stripe` 15.6.0 is MIT. Verified by reading the `LICENSE` file out of
the sdist rather than trusting the PyPI classifier, per ip-safety.md #6, which
says not to assume. Its two runtime dependencies were already present:
`requests` (Apache-2.0, via `tldextract`) and `typing_extensions` (PSF-2.0).

ip-safety.md #7 is not at stake. Nothing here touches engine answers or
competitor pages; the only third-party data is our own Stripe account's view of
our own customers.
"""

from __future__ import annotations

from typing import Any

import stripe
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import BillingNotConfigured
from ..models import Agency

logger = structlog.get_logger(__name__)

# Which of Stripe's statuses mean "this agency is paid up".
#
# `trialing` counts. No trial is configured on the Price today, so in practice
# nothing produces it — but if a trial is ever added in the dashboard, an agency
# inside one is a customer in good standing, and a product that told them
# otherwise would be wrong on the day it mattered most.
#
# Everything else — past_due, unpaid, incomplete, incomplete_expired, canceled,
# paused — is NOT active. Note especially that `past_due` is not active: north
# -star.md §5.4 row 4 asks for a billing-failure state "rendered as honestly as
# `partial` is today", and quietly counting a failed payment as active would be
# the silent degradation that row exists to forbid.
ACTIVE_STATUSES = frozenset({"active", "trialing"})


def _require(settings: Settings, name: str) -> str:
    """A Stripe setting, or a 503 that names the environment variable.

    Wraps `Settings.provider_key` rather than reimplementing its check, so the
    sentence an operator reads is the same one every other integration in this
    codebase produces. The only thing added is the HTTP status: a missing key is
    a deployment state, not a bug in this code, and `BillingNotConfigured` says
    that in a response body instead of leaving a bare 500 for somebody to go
    and correlate against a log line.
    """
    try:
        return settings.provider_key(name)
    except RuntimeError as exc:
        raise BillingNotConfigured(detail=str(exc)) from exc


def _client(settings: Settings) -> stripe.StripeClient:
    """A Stripe client bound to the configured secret key.

    Constructed per call rather than assigning the module-global
    `stripe.api_key`. A process-wide mutable credential is a thing tests have to
    remember to unset, and forgetting is how one test's key leaks into another
    test's assertion. It is also the seam the suite replaces: every test in
    `test_billing.py` monkeypatches this function, which is why no automated
    test can reach the network even by accident.

    Uses the `.v1` namespace deliberately — the flat `client.customers` accessor
    is deprecated in 15.6 and emits a DeprecationWarning, which this project's
    pytest configuration is configured to take seriously.
    """
    return stripe.StripeClient(api_key=_require(settings, "stripe_secret_key"))


def is_active(agency: Agency) -> bool:
    """Whether this agency's subscription is one we would call live.

    The single interpretation of `subscription_status`. It exists so that no
    call site anywhere has to know which of Stripe's eight status strings are
    the good ones — that knowledge belongs in one place, next to the constant
    that encodes it.
    """
    return agency.subscription_status in ACTIVE_STATUSES


def _get(obj: Any, key: str) -> Any:
    """Read a field from a Stripe object or a plain dict.

    A verified webhook payload arrives as `stripe.Event`, whose nested objects
    support attribute access; a replayed fixture in a test is a plain dict. Both
    are the same JSON, and neither this module nor its tests should have to care
    which one they were handed.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


# ---------------------------------------------------------------------------
# customer + checkout
# ---------------------------------------------------------------------------


async def ensure_customer(db: AsyncSession, agency: Agency, *, settings: Settings) -> str:
    """The agency's Stripe customer id, creating one only if it has none.

    **The reuse is the point of this function.** Creating a customer on every
    visit to the pricing page would leave a trail of duplicates in the Stripe
    dashboard, each able to hold its own card and its own subscription, and the
    webhook — which resolves an agency by customer id — would then be updating
    whichever duplicate happened to be stored. Someone who opens checkout,
    thinks better of it, and comes back tomorrow is the ordinary case, not an
    edge one.

    The id is committed by the caller as part of the same request that creates
    the Checkout Session. `uq_agencies_stripe_customer_id` is the backstop
    underneath this check rather than the mechanism: two simultaneous checkout
    requests from the same agency would both find NULL here, and the constraint
    is what stops the second one silently pointing the agency at a second
    customer.
    """
    if agency.stripe_customer_id:
        return agency.stripe_customer_id

    client = _client(settings)
    customer = await client.v1.customers.create_async(
        params={
            "name": agency.name,
            # Agency id in metadata as well as in our own column, so that
            # somebody looking at a customer in the Stripe dashboard can tell
            # which tenant it belongs to without a database query.
            "metadata": {"agency_id": agency.id},
        }
    )

    agency.stripe_customer_id = customer.id
    await db.flush()
    logger.info("billing.customer_created", agency_id=agency.id, customer_id=customer.id)
    return customer.id


async def create_checkout_session(
    db: AsyncSession, agency: Agency, *, settings: Settings
) -> str:
    """Start a subscription. Returns the URL to send the browser to.

    A URL rather than a client secret, because this integration deliberately has
    no frontend Stripe library: the browser navigates to a page Stripe hosts and
    comes back. That is one fewer dependency in `apps/web`, one fewer script on
    a page that takes card details, and no publishable key to keep in step.

    **The return URLs come from configuration, never from the request.**
    `public_web_base_url` is the same setting share links use, and config.py
    already argues why at length: `Host` and `Origin` are attacker-controlled,
    and a success URL built from a spoofed header is a phishing page wearing our
    checkout flow.

    **Neither return URL is load-bearing.** `success_url` carries no session id
    and the screen it lands on reads the database, not the query string. The
    browser coming back is a courtesy; `checkout.session.completed` is the
    event that changes anything. That is why closing the tab mid-payment does
    not lose the subscription — see `apply_event`.

    `client_reference_id` and the two metadata bags all carry the agency id, on
    purpose and redundantly: the Checkout Session, the Customer and the
    Subscription are three different objects arriving in three different events,
    and each one should be able to answer "whose is this?" without a join.
    """
    # BOTH settings are read before anything is created, and the order matters.
    # `ensure_customer` creates a Stripe customer as a side effect, so checking
    # the price id afterwards meant a misconfigured server left a real customer
    # behind for a checkout that was never going to work — and the next attempt,
    # after the price was configured, would reuse it and hide that it happened.
    # Found by `test_checkout_without_a_key_names_the_exact_variable`, which was
    # written to assert the error message and caught the ordering instead.
    _require(settings, "stripe_secret_key")
    price_id = _require(settings, "stripe_price_id")

    customer_id = await ensure_customer(db, agency, settings=settings)
    base = settings.public_web_base_url.rstrip("/")

    client = _client(settings)
    session = await client.v1.checkout.sessions.create_async(
        params={
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": agency.id,
            "metadata": {"agency_id": agency.id},
            # Copied onto the Subscription itself, so that every
            # `customer.subscription.*` event names its agency directly rather
            # than relying on a customer-id lookup.
            "subscription_data": {"metadata": {"agency_id": agency.id}},
            "success_url": f"{base}/settings?checkout=success",
            "cancel_url": f"{base}/settings?checkout=cancelled",
        }
    )

    if not session.url:
        # Stripe returns a URL for every hosted session; this would mean the API
        # changed shape under us. Better a loud failure than navigating a
        # browser to "undefined".
        raise BillingNotConfigured(
            detail="Stripe returned a checkout session with no URL to send the browser to."
        )

    logger.info("billing.checkout_created", agency_id=agency.id, session_id=session.id)
    return session.url

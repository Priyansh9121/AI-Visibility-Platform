"""Subscription billing against Stripe — Epic 9.15.

The first commercial code in this product. north-star.md §5.4 row 3 recorded
the vendor as "NOT decided"; it is decided now, and this module is the whole of
the integration.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
It is flat monthly subscription billing against ONE Stripe Price: create a
Checkout Session, and believe the webhook. Stripe's hosted portal, for managing
the result, lands in the commit after this one.

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

from datetime import UTC, datetime
from typing import Any

import stripe
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import BillingNotConfigured, InvalidWebhookSignature
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

# The events this service acts on. Anything else is acknowledged and ignored —
# see `apply_event`.
HANDLED_EVENT_TYPES = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


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


def period_end_of(subscription: Any) -> datetime | None:
    """When the current paid period ends, across two shapes of Subscription.

    **`current_period_end` is no longer a top-level field on Subscription.** On
    the API version this SDK pins (`2026-08-26.dahlia`) it lives on each
    subscription ITEM, because a subscription's items can bill on different
    schedules. Reading `subscription["current_period_end"]` — the field every
    older example uses — returns nothing, and the failure is silent: the status
    saves correctly, Settings says "Active", and the renewal date is simply
    never there.

    So items are read first, and the legacy top-level field is the fallback
    rather than the other way round. The fallback is not dead code: a webhook
    delivery is stamped with the API version configured on the endpoint, which
    may be older than the SDK's, and events can be replayed from Stripe's
    dashboard months later.

    With one Price and one item there is exactly one date to find. If a
    subscription ever carries several items the earliest is taken, because the
    first thing that renews is the first thing that can fail.
    """
    candidates: list[int] = []

    items = _get(subscription, "items")
    data = _get(items, "data") if items is not None else None
    for item in data or []:
        value = _get(item, "current_period_end")
        if isinstance(value, int):
            candidates.append(value)

    if not candidates:
        legacy = _get(subscription, "current_period_end")
        if isinstance(legacy, int):
            candidates.append(legacy)

    if not candidates:
        return None
    return datetime.fromtimestamp(min(candidates), tz=UTC)


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


# ---------------------------------------------------------------------------
# webhook
# ---------------------------------------------------------------------------


def verify_webhook(payload: bytes, signature: str | None, *, settings: Settings) -> Any:
    """Verify a webhook against `STRIPE_WEBHOOK_SECRET`, or refuse.

    **There is no path through this function that returns an unverified event.**
    Not when the signature header is missing, not when it is wrong, and — the
    one worth stating loudest — not when the secret is unset. An unconfigured
    server refuses with `BillingNotConfigured`; it does not decide that
    verification is optional today. A payload claiming a subscription is active
    is precisely what somebody would forge, and "we were not set up to check" is
    not a reason to believe one.

    That is deliberately NOT the shape `services/email.py` uses for its own
    unset key. There, an absent `RESEND_API_KEY` logs instead of sending, and
    that is a real, supported development mode because the consequence of the
    unconfigured path is a message nobody receives. Here the consequence would
    be trusting a stranger about money. Same question, opposite answer, because
    the thing at stake is different.

    `construct_event` also enforces a replay window (Stripe's default tolerance,
    300 seconds), so a signature captured and replayed later fails too. That is
    a property of the SDK's implementation and a second reason not to have
    hand-rolled this.
    """
    secret = _require(settings, "stripe_webhook_secret")

    if not signature:
        raise InvalidWebhookSignature(
            detail="No Stripe-Signature header was supplied."
        )

    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except stripe.SignatureVerificationError as exc:
        logger.warning("billing.webhook_rejected", reason=type(exc).__name__)
        raise InvalidWebhookSignature(
            detail="The Stripe-Signature header did not match this payload."
        ) from exc
    except ValueError as exc:
        # Malformed JSON. Same refusal: an unparseable body is not an event.
        raise InvalidWebhookSignature(
            detail="The webhook payload could not be parsed."
        ) from exc


async def _resolve_agency(db: AsyncSession, obj: Any) -> Agency | None:
    """Find the agency an event object belongs to.

    Three routes, tried in order of how directly each one says what it means:

    1. `metadata.agency_id` — written by us when the session, customer and
       subscription were created. It is not a lookup at all, it is the answer.
    2. The subscription id. `customer.subscription.deleted` carries a
       subscription and little else that helps.
    3. The customer id. The fallback for anything created outside our checkout
       flow — a subscription started by hand in the Stripe dashboard, which is
       a real thing a founder does while testing.

    Metadata is trusted because this is only ever called on a payload that
    `verify_webhook` has already authenticated. On an unverified body it would
    be an attacker naming the agency they would like to upgrade, which is
    exactly why nothing calls this without verifying first.
    """
    metadata = _get(obj, "metadata")
    agency_id = _get(metadata, "agency_id") if metadata is not None else None
    if agency_id:
        found = (
            await db.execute(select(Agency).where(Agency.id == str(agency_id)))
        ).scalar_one_or_none()
        if found is not None:
            return found

    subscription_id = _get(obj, "subscription") or (
        _get(obj, "id") if str(_get(obj, "object") or "") == "subscription" else None
    )
    if subscription_id:
        found = (
            await db.execute(
                select(Agency).where(Agency.stripe_subscription_id == str(subscription_id))
            )
        ).scalar_one_or_none()
        if found is not None:
            return found

    customer_id = _get(obj, "customer")
    if customer_id:
        return (
            await db.execute(
                select(Agency).where(Agency.stripe_customer_id == str(customer_id))
            )
        ).scalar_one_or_none()

    return None


async def apply_event(db: AsyncSession, event: Any) -> bool:
    """Apply one verified Stripe event. Returns whether it was acted on.

    **This is the only thing in the system that may change a subscription
    status.** No endpoint the browser can reach writes one. That is the whole
    architecture of this feature in one sentence: the browser's redirect after
    checkout is a UX nicety, and Stripe telling us is the fact. An operator who
    pays and then closes the tab before the redirect still ends up subscribed,
    because nothing was ever waiting on that tab.

    **An unhandled event type is a success, not an error.** Stripe endpoints
    receive whatever the account is configured to send, and answering non-2xx to
    an event we simply do not care about makes Stripe retry it, back off, and
    eventually mark the endpoint unhealthy — degrading delivery of the events we
    DO care about. So this returns False and the router answers 200.
    """
    event_type = str(_get(event, "type") or "")
    data = _get(event, "data")
    obj = _get(data, "object") if data is not None else None

    if event_type not in HANDLED_EVENT_TYPES or obj is None:
        logger.info("billing.webhook_ignored", event_type=event_type)
        return False

    agency = await _resolve_agency(db, obj)
    if agency is None:
        # Not an error either. A Stripe account can carry customers this
        # database has never heard of — a founder clicking around the dashboard
        # makes one — and asking Stripe to retry forever would not conjure a
        # row. Logged loudly enough to notice if it ever becomes common.
        logger.warning("billing.webhook_unmatched", event_type=event_type)
        return False

    if event_type == "checkout.session.completed":
        _apply_checkout_completed(agency, obj)
    else:
        _apply_subscription_event(agency, obj, deleted=event_type.endswith(".deleted"))

    logger.info(
        "billing.webhook_applied",
        event_type=event_type,
        agency_id=agency.id,
        status=agency.subscription_status,
    )
    return True


def _apply_checkout_completed(agency: Agency, session: Any) -> None:
    """Checkout finished: record the customer and subscription, mark active.

    The session object carries ids but no subscription status and no period end
    — those belong to the Subscription, which arrives in its own event moments
    later and fills them in. Rather than making an extra API call inside a
    webhook to fetch what is already on its way, the status is set to `active`
    here and corrected by the subscription event if it turns out to be anything
    else.

    Writing `active` optimistically is safe in a way it would not be in the
    other direction: a completed Checkout Session in `subscription` mode means
    payment succeeded. If it somehow did not, the `customer.subscription.*`
    event that follows overwrites this with the truth within seconds, and the
    worst case is a screen that was briefly too generous — not a screen that
    denied somebody something they had paid for.
    """
    customer_id = _get(session, "customer")
    subscription_id = _get(session, "subscription")

    if customer_id:
        agency.stripe_customer_id = str(customer_id)
    if subscription_id:
        agency.stripe_subscription_id = str(subscription_id)
    agency.subscription_status = "active"


def _apply_subscription_event(agency: Agency, subscription: Any, *, deleted: bool) -> None:
    """A subscription was created, changed, or cancelled.

    The status is taken from the payload rather than inferred from the event
    type, with one exception: `customer.subscription.deleted` is authoritative
    that the subscription is over, so `canceled` is used if the payload somehow
    disagrees. Stripe does send `status: "canceled"` on that event; not relying
    on it costs nothing and means the cancellation cannot be missed.

    Every status Stripe sends is stored verbatim, including ones this codebase
    has never seen. `models/tenancy.py` explains why the column is a string
    rather than an enum, and this is the write that would otherwise raise.
    """
    subscription_id = _get(subscription, "id")
    if subscription_id:
        agency.stripe_subscription_id = str(subscription_id)

    customer_id = _get(subscription, "customer")
    if customer_id:
        agency.stripe_customer_id = str(customer_id)

    status = _get(subscription, "status")
    agency.subscription_status = "canceled" if deleted else (str(status) if status else None)

    # A cancelled subscription has no next renewal, and leaving yesterday's date
    # behind would let Settings render "renews" against a date in the past.
    agency.subscription_current_period_end = (
        None if deleted else period_end_of(subscription)
    )

"""Subscription billing against Stripe — Epic 9.15.

The first commercial code in this product. north-star.md §5.4 row 3 recorded
the vendor as "NOT decided"; it is decided now, and this module is the whole of
the integration.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
It is flat monthly subscription billing against ONE Stripe Price: create a
Checkout Session, believe the webhook, and hand the customer Stripe's own
portal to manage the result. That is the entire surface.

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
the sdist rather than trusting the PyPI classifier, per the dependency-licensing rule, which
says not to assume. Its two runtime dependencies were already present:
`requests` (Apache-2.0, via `tldextract`) and `typing_extensions` (PSF-2.0).

The facts-only rule is not at stake. Nothing here touches engine answers or
competitor pages; the only third-party data is our own Stripe account's view of
our own customers.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime, timedelta
from typing import Any

import stripe
import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import (
    BillingNotConfigured,
    CheckoutInProgress,
    InvalidWebhookSignature,
    ServiceUnavailable,
    SubscriptionAlreadyActive,
    SubscriptionNeedsAttention,
)
from ..models import Agency, StripeWebhookEvent, StripeWebhookOutcome

logger = structlog.get_logger(__name__)

STRIPE_TIMEOUT_SECONDS = 15
STRIPE_MAX_RETRIES = 1
# How long a checkout waits for another checkout of the same agency.
CHECKOUT_LOCK_TIMEOUT = "3s"
# How long a Checkout link stays payable (founder decision 5, 2026-10-01).
# Stripe's default is 24 hours and it accepts 30 minutes to 24 hours. A shorter
# link also narrows the window the open-session expiry in
# `create_checkout_session` guards: an older session stays payable until then.
CHECKOUT_LINK_LIFETIME = timedelta(hours=1)
# The two 409 refusals of a second checkout. Neither detail carries a Stripe id
# or the status itself; both point at the billing portal.
_ALREADY_ACTIVE_DETAIL = (
    "This agency already has a current subscription. "
    "Manage it from Manage billing in Settings."
)
_NEEDS_ATTENTION_DETAIL = (
    "This agency already has a subscription that needs attention. "
    "Manage it from Manage billing in Settings."
)
# The 503 for asking Stripe about the customer's subscriptions, whether the call
# failed or came back with a full page and nothing live on it. Its own sentence,
# so an operator can tell it from the one for clearing the previous session.
_SUBSCRIPTION_CHECK_FAILED_DETAIL = (
    "Could not check this agency's existing subscriptions at Stripe. "
    "Try again in a moment."
)

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
#
# "Not active" is not "over", though: past_due, unpaid, paused and incomplete are
# all still open at Stripe. See TERMINAL_STATUSES and `is_live_at_stripe`.
ACTIVE_STATUSES = frozenset({"active", "trialing"})

# Statuses a Stripe subscription never leaves: it is OVER. They, and having no
# subscription at all, are the only states from which a checkout may start.
#
# Deliberately a list of what is over and not of what is live (founder decision
# 4, and security review L1): see `is_live_at_stripe` for why.
TERMINAL_STATUSES = frozenset({"canceled", "incomplete_expired"})

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
    return stripe.StripeClient(
        api_key=_require(settings, "stripe_secret_key"),
        # Explicit, not the SDK defaults (80s, two retries per call): checkout
        # makes several calls while holding the agency row lock, and a slow
        # Stripe must not hold it for minutes.
        http_client=stripe.HTTPXClient(timeout=STRIPE_TIMEOUT_SECONDS),
        max_network_retries=STRIPE_MAX_RETRIES,
    )


def is_active(agency: Agency) -> bool:
    """Whether this agency's subscription is one we would call live.

    The single interpretation of `subscription_status`. It exists so that no
    call site anywhere has to know which of Stripe's eight status strings are
    the good ones — that knowledge belongs in one place, next to the constant
    that encodes it.
    """
    return agency.subscription_status in ACTIVE_STATUSES


def is_live_at_stripe(status: str | None) -> bool:
    """Whether a subscription in this status is still open at Stripe.

    **Deny by default.** Everything is live except having no subscription at
    all and the TERMINAL statuses (`canceled`, `incomplete_expired`). So
    `past_due`, `unpaid`, `paused` and `incomplete` are live, and so is a status
    this code has never seen. Checkout refuses on it, and the webhook's
    second-subscription backstop warns on it.

    Written the other way, as a list of the live statuses, it would fail OPEN
    the day Stripe adds one, and a second subscription billing the agency twice
    is the one outcome a checkout exists to prevent. Listing what is over fails
    closed: a status nobody has decided about refuses a checkout until somebody
    does.

    `None` here means no subscription at all: it is the answer for a STORED
    status, where our own row has never had one. A subscription that Stripe
    LISTS always exists, so there a null or missing status is not "none"; the
    Stripe-side check in `create_checkout_session` counts it live.

    Not `is_active`. That still counts only `active` and `trialing`, because it
    answers "is this agency paid up", and so does the scan allowance.
    """
    return status is not None and status not in TERMINAL_STATUSES


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
    the Checkout Session. **The mechanism is the row lock**, taken by
    `create_checkout_session` before this is called: two simultaneous first
    checkouts serialise on it, and the second finds the first's committed
    customer here. `uq_agencies_stripe_customer_id` is single-column and only
    forbids two AGENCIES sharing a customer; it does NOT stop one agency's
    second UPDATE overwriting the first customer id, which is why the lock is
    needed. Call this only on a row locked `FOR UPDATE` in the same transaction.
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

    # A subscription that is live at Stripe means a second checkout would create
    # a second subscription beside it: one the agency pays for (ACTIVE_STATUSES)
    # or one that is open but needs attention (founder decision 4). "Live" is
    # `is_live_at_stripe`, which denies by default: only no subscription and an
    # ended one may start a checkout. Refused before `ensure_customer`, so
    # nothing is created at Stripe. Caller authority is settled by the router
    # before this is reached.
    #
    # The agency is re-read `FOR UPDATE` in this request's transaction and the
    # guard and `ensure_customer` run against THAT row, so concurrent checkouts
    # serialise: the second waits for the first's commit and then sees its
    # customer id (and any status the webhook has since written). The lock is
    # held across the Stripe calls below, deliberately: it is per agency, and it
    # is what makes "one customer, at most one open session" true.
    #
    # Bounded and gentle: `lock_timeout` so a stuck holder gives a clear 409
    # instead of a queue of pooled connections, and FOR NO KEY UPDATE
    # when the agency already has a customer, so child inserts (scans, clients,
    # alerts take FOR KEY SHARE) are not blocked.
    # Recording the FIRST customer id writes a unique-indexed column, which
    # Postgres treats as a key change, so that case takes the full FOR UPDATE
    # (up front, before any Stripe call). A repeat checkout writes no key.
    key_share = bool(agency.stripe_customer_id)
    await db.execute(text(f"SET LOCAL lock_timeout = '{CHECKOUT_LOCK_TIMEOUT}'"))
    try:
        agency = (
            await db.execute(
                select(Agency)
                .where(Agency.id == agency.id)
                .with_for_update(key_share=key_share)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":  # lock_not_available
            raise CheckoutInProgress(
                "A checkout for this agency is already in progress. "
                "Try again in a moment."
            ) from exc
        raise

    if is_active(agency):
        raise SubscriptionAlreadyActive(_ALREADY_ACTIVE_DETAIL)
    if is_live_at_stripe(agency.subscription_status):
        raise SubscriptionNeedsAttention(_NEEDS_ATTENTION_DETAIL)

    had_customer = bool(agency.stripe_customer_id)
    customer_id = await ensure_customer(db, agency, settings=settings)
    base = settings.public_web_base_url.rstrip("/")

    client = _client(settings)

    # An older open Checkout Session stays payable at Stripe until it expires,
    # so paying two of them would create two subscriptions. Two checks against
    # Stripe, IN THIS ORDER (security review L2): expire every open session of
    # this customer first, THEN ask what subscriptions it has.
    #
    # The other way round leaves a window: the customer pays an open session
    # after we looked at their subscriptions and before we expired it, and we
    # would create a second session beside a subscription we never saw. Expiring
    # first closes it. A session that completed earlier is a subscription by the
    # time we ask, and one that completes DURING the expire makes the expire
    # fail, which fails the checkout.
    #
    # A brand-new customer cannot have either, so it is not asked. If Stripe
    # fails in either step the checkout FAILS (503) rather than carrying on,
    # because carrying on is exactly the double-subscription case; the caller
    # can simply retry. Each step answers with its own sentence and log event.
    if had_customer:
        try:
            open_sessions = await client.v1.checkout.sessions.list_async(
                params={"customer": customer_id, "status": "open", "limit": 100}
            )
            if open_sessions.has_more:
                # Cannot prove they are all expired, so do not create another.
                raise ServiceUnavailable(
                    "Could not clear the previous checkout sessions at Stripe. "
                    "Try again in a moment."
                )
            for old_session in open_sessions.data:
                await client.v1.checkout.sessions.expire_async(old_session.id)
        except stripe.StripeError as exc:
            logger.warning(
                "billing.checkout_expire_failed",
                agency_id=agency.id,
                error_type=type(exc).__name__,
            )
            raise ServiceUnavailable(
                "Could not clear the previous checkout session at Stripe. "
                "Try again in a moment."
            ) from exc

        # A subscription that is live at Stripe refuses the checkout even when
        # our row does not say so yet (paid, webhook not landed) or says
        # `canceled`. ONE call for the customer's subscriptions of every status:
        # this runs under the agency row lock, and a call per status held it for
        # six round trips. Paid-up outranks needs-attention, so which refusal is
        # reported never depends on the order Stripe lists them in; either one
        # points at the same place.
        try:
            subs = await client.v1.subscriptions.list_async(
                params={"customer": customer_id, "status": "all", "limit": 100}
            )
        except stripe.StripeError as exc:
            logger.warning(
                "billing.checkout_subscription_check_failed",
                agency_id=agency.id,
                error_type=type(exc).__name__,
            )
            raise ServiceUnavailable(_SUBSCRIPTION_CHECK_FAILED_DETAIL) from exc
        # Read through `_get`, and tested against what is OVER rather than through
        # `is_live_at_stripe`: a subscription Stripe LISTS exists, so a status
        # that is null or missing is not "no subscription" (which is all that
        # None means on our own row). It is one we cannot tell is over, so it
        # counts as live. Fail closed.
        statuses = [_get(sub, "status") for sub in subs.data]
        if any(status in ACTIVE_STATUSES for status in statuses):
            raise SubscriptionAlreadyActive(_ALREADY_ACTIVE_DETAIL)
        if any(status not in TERMINAL_STATUSES for status in statuses):
            raise SubscriptionNeedsAttention(_NEEDS_ATTENTION_DETAIL)
        if subs.has_more:
            # Reached only when a full page had nothing live on it. More than
            # 100 subscriptions on ONE customer is not a real case (one plan,
            # one agency). But if it ever happened, a live one could be on the
            # next page, and not being able to prove there is none is not the
            # same as there being none: fail closed, as the open-sessions check
            # above does, and say so, since nothing else would.
            logger.warning(
                "billing.checkout_subscriptions_truncated",
                agency_id=agency.id,
                customer_id=customer_id,
            )
            raise ServiceUnavailable(_SUBSCRIPTION_CHECK_FAILED_DETAIL)

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
            # Whole Unix seconds, as Stripe takes them. Read per call, so every
            # link gets its own hour.
            "expires_at": int((datetime.now(UTC) + CHECKOUT_LINK_LIFETIME).timestamp()),
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


async def create_portal_session(agency: Agency, *, settings: Settings) -> str:
    """Stripe's hosted billing portal. Returns the URL to navigate to.

    Update a card, cancel, download an invoice — all of it, for one more API
    call and no UI of our own. Building any of those screens here would mean
    handling card details, dunning states and invoice PDFs, which is a product
    in itself and one Stripe already ships.

    Requires a customer. The caller checks, and the check is not merely
    defensive: the portal for an agency that has never paid is an empty page
    with no invoices and no card, which is a worse answer than the button not
    being there.
    """
    if not agency.stripe_customer_id:
        raise BillingNotConfigured(
            detail=(
                "This agency has no billing account yet. Subscribe first — the "
                "portal manages an existing subscription and cannot start one."
            )
        )

    base = settings.public_web_base_url.rstrip("/")
    client = _client(settings)
    session = await client.v1.billing_portal.sessions.create_async(
        params={"customer": agency.stripe_customer_id, "return_url": f"{base}/settings"}
    )
    logger.info("billing.portal_created", agency_id=agency.id)
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
        logger.warning("stripe.webhook.rejected", reason=type(exc).__name__)
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

    The row is returned `FOR UPDATE` on ALL THREE routes, with `populate_existing`
    so a session-cached copy is refreshed to the locked row. Two different events
    for one agency therefore serialise: the second waits for the first's commit
    and then reads the `stripe_event_at` and status the first wrote. The lock is
    taken BEFORE the ledger insert (see `apply_event`), so a waiting transaction
    never holds the ledger row's foreign-key `KEY SHARE` on the agency while it
    waits, which is what would otherwise deadlock against the holder's UPDATE.

    Metadata is trusted because this is only ever called on a payload that
    `verify_webhook` has already authenticated. On an unverified body it would
    be an attacker naming the agency they would like to upgrade, which is
    exactly why nothing calls this without verifying first.
    """
    metadata = _get(obj, "metadata")
    agency_id = _get(metadata, "agency_id") if metadata is not None else None
    if agency_id:
        # Locked like the two routes below. This is the route our own checkout
        # metadata takes, so it is the common one; an unlocked read here would
        # make the stale check and the watermark run on a snapshot another
        # transaction is about to change.
        found = (
            await db.execute(
                select(Agency)
                .where(Agency.id == str(agency_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found

    subscription_id = _get(obj, "subscription") or (
        _get(obj, "id") if str(_get(obj, "object") or "") == "subscription" else None
    )
    if subscription_id:
        found = (
            await db.execute(
                select(Agency)
                .where(Agency.stripe_subscription_id == str(subscription_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found

    customer_id = _get(obj, "customer")
    if customer_id:
        return (
            await db.execute(
                select(Agency)
                .where(Agency.stripe_customer_id == str(customer_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    return None


class WebhookResult(str, enum.Enum):
    """What `apply_event` did. The router maps this to the response body."""

    IGNORED = "ignored"  # a type we do not handle: nothing recorded
    APPLIED = "applied"
    STALE = "stale"  # older than the agency's stored state
    UNMATCHED = "unmatched"  # no agency: NOT recorded, nothing changed
    DUPLICATE = "duplicate"  # this event id was already recorded

    @property
    def writes(self) -> bool:
        """Whether the transaction holds anything worth committing."""
        return self in (WebhookResult.APPLIED, WebhookResult.STALE)


# TERMINAL_STATUSES, the statuses a Stripe subscription never leaves, is defined
# beside ACTIVE_STATUSES at the top of this module: checkout reads it too.


def _blocked_by_state_machine(
    agency: Agency, subscription_id: str | None, new_status: str | None
) -> bool:
    """Whether this event would move ONE subscription backwards. True = ignore it.

    Timestamps cannot settle this: Stripe's `created` has one-second resolution,
    so a retry or a sibling event can share a second with the event that
    already superseded it, and an equal timestamp applies in arrival order. So
    the subscription's own life cycle is checked as well, and only for the SAME
    subscription id (a new subscription after a cancellation is a new life):

    * a terminal status (`canceled`, `incomplete_expired`) is never overwritten
      by a different status, including checkout's optimistic `active`;
    * `incomplete` is never written over any status but `incomplete`, because
      Stripe only enters it when the subscription is created, so a later
      `incomplete` is a retried or reordered `created`.
    """
    if not subscription_id or subscription_id != agency.stripe_subscription_id:
        return False
    current = agency.subscription_status
    if new_status is None or current is None or new_status == current:
        return False
    if current in TERMINAL_STATUSES:
        return True
    return new_status == "incomplete"


def _incoming_state(event_type: str, obj: Any) -> tuple[str | None, str | None]:
    """The (subscription id, resulting status) this event would write."""
    if event_type == "checkout.session.completed":
        sub = _get(obj, "subscription")
        return (str(sub) if sub else None, "active")
    sub = _get(obj, "id")
    if event_type.endswith(".deleted"):
        return (str(sub) if sub else None, "canceled")
    status = _get(obj, "status")
    return (str(sub) if sub else None, str(status) if status else None)


async def _record_event(
    db: AsyncSession,
    *,
    event_id: str,
    event_type: str,
    created_at: datetime,
    agency_id: str | None,
    outcome: StripeWebhookOutcome,
) -> bool:
    """Insert the dedupe row. True if this call inserted it, False on a duplicate.

    `ON CONFLICT (id) DO NOTHING RETURNING id` in the caller's transaction: a
    concurrent insert of the same id blocks on the other's uncommitted row and
    then conflicts, and a rollback removes the row so Stripe's retry runs.
    """
    inserted = await db.execute(
        pg_insert(StripeWebhookEvent)
        .values(
            id=event_id,
            event_type=event_type,
            stripe_created_at=created_at,
            agency_id=agency_id,
            outcome=outcome,
        )
        .on_conflict_do_nothing(index_elements=[StripeWebhookEvent.id])
        .returning(StripeWebhookEvent.id)
    )
    return inserted.scalar_one_or_none() is not None


async def apply_event(db: AsyncSession, event: Any) -> WebhookResult:
    """Apply one verified Stripe event, once, and never over newer state.

    **This is the only thing in the system that may change a subscription
    status.** No endpoint the browser can reach writes one. The browser's
    redirect after checkout is a UX nicety, and Stripe telling us is the fact.

    **Idempotent.** The event id is recorded in `stripe_webhook_events` in this
    same transaction; an id already there is a `DUPLICATE` and nothing is
    applied. A raise anywhere rolls the row back with the change.

    **Ordered.** The agency is locked `FOR UPDATE`. An event whose `created` is
    strictly older than `agencies.stripe_event_at` is `STALE`: recorded, applied
    to nothing. Equal timestamps apply in arrival order (Stripe's clock has
    one-second resolution), except that `_blocked_by_state_machine` also makes
    an event `STALE` when it would move the same subscription backwards. Every
    applied `customer.subscription.*` event moves `stripe_event_at` forward
    only; `checkout.session.completed` is judged against it but never moves it,
    and one that is not a subscription-mode session is `IGNORED`. An event with no numeric
    `created` is ignored. An event matching no agency is `UNMATCHED` and is not
    recorded, so Stripe's "Resend" applies it once an operator links the agency.

    **An unhandled event type is a success, not an error, and leaves no row.**
    Answering non-2xx to an event we do not care about makes Stripe retry it,
    back off, and eventually mark the endpoint unhealthy.

    The caller commits when `result.writes`.
    """
    event_type = str(_get(event, "type") or "")
    event_id = str(_get(event, "id") or "")
    data = _get(event, "data")
    obj = _get(data, "object") if data is not None else None

    if event_type not in HANDLED_EVENT_TYPES or obj is None or not event_id:
        logger.info("stripe.webhook.ignored", event_id=event_id, event_type=event_type)
        return WebhookResult.IGNORED

    if event_type == "checkout.session.completed" and not (
        _get(obj, "mode") == "subscription" and _get(obj, "subscription")
    ):
        # A payment- or setup-mode session for a linked customer says nothing
        # about a subscription, and the guard skips a null subscription id, so
        # applying it could write `active` over `canceled`.
        logger.info(
            "stripe.webhook.ignored",
            event_id=event_id,
            event_type=event_type,
            reason="not_a_subscription_checkout",
        )
        return WebhookResult.IGNORED

    created = _get(event, "created")
    if not isinstance(created, int | float) or isinstance(created, bool):
        # Never substitute our own clock: a watermark set from it could sit in
        # the future and make every real event stale.
        logger.info(
            "stripe.webhook.ignored",
            event_id=event_id,
            event_type=event_type,
            reason="missing_or_invalid_created",
        )
        return WebhookResult.IGNORED
    created_at = datetime.fromtimestamp(created, tz=UTC)

    # ORDER MATTERS: lock the agency, THEN insert the ledger row, THEN update.
    # A second transaction waits here, before it holds anything, so it never
    # owns the ledger insert's FK `KEY SHARE` on the agency row that the holder
    # is about to UPDATE.
    agency = await _resolve_agency(db, obj)

    if agency is None:
        # Not an error, and deliberately NOT recorded: a Stripe account can
        # carry customers this database has never heard of, and a ledger row
        # would make Stripe's "Resend" a duplicate after an operator links the
        # agency. Nothing is held, so there is nothing to dedupe.
        logger.warning("stripe.webhook.unmatched", event_id=event_id, event_type=event_type)
        return WebhookResult.UNMATCHED

    sub_id, new_status = _incoming_state(event_type, obj)
    older = agency.stripe_event_at is not None and created_at < agency.stripe_event_at
    if older or _blocked_by_state_machine(agency, sub_id, new_status):
        outcome = StripeWebhookOutcome.STALE
    else:
        outcome = StripeWebhookOutcome.APPLIED

    if not await _record_event(
        db,
        event_id=event_id,
        event_type=event_type,
        created_at=created_at,
        agency_id=agency.id,
        outcome=outcome,
    ):
        logger.info("stripe.webhook.duplicate", event_id=event_id, event_type=event_type)
        return WebhookResult.DUPLICATE

    if outcome is StripeWebhookOutcome.STALE:
        logger.info(
            "stripe.webhook.stale",
            event_id=event_id,
            event_type=event_type,
            agency_id=agency.id,
        )
        return WebhookResult.STALE

    if (
        event_type in ("checkout.session.completed", "customer.subscription.created")
        and sub_id
        and agency.stripe_subscription_id
        and sub_id != agency.stripe_subscription_id
        and is_live_at_stripe(agency.subscription_status)
    ):
        # Backstop signal, behaviour unchanged: a second subscription was
        # created for an agency that already has a live one. "Live" is
        # `is_live_at_stripe`, the same test checkout refuses on (anything but
        # no subscription and an ended one), so one arriving over any of them
        # got past that refusal. Ids only.
        logger.warning(
            "stripe.webhook.second_subscription",
            event_id=event_id,
            agency_id=agency.id,
            stored_subscription_id=agency.stripe_subscription_id,
            incoming_subscription_id=sub_id,
        )

    if event_type == "checkout.session.completed":
        # Ids and an assumed `active` only: it is judged against the watermark
        # but never moves it, or a subscription event created a second earlier
        # and delivered after it would be stale and the period end never set.
        _apply_checkout_completed(agency, obj)
    else:
        _apply_subscription_event(agency, obj, deleted=event_type.endswith(".deleted"))
        # Monotonic: the agency row is locked and freshly loaded, so this max()
        # cannot race; the watermark never goes backwards.
        agency.stripe_event_at = (
            created_at
            if agency.stripe_event_at is None
            else max(agency.stripe_event_at, created_at)
        )

    logger.info(
        "stripe.webhook.applied",
        event_id=event_id,
        event_type=event_type,
        agency_id=agency.id,
        status=agency.subscription_status,
    )
    return WebhookResult.APPLIED


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

    # Cancelled in the portal but paid to the end of the period — 2026-09-29.
    # Stripe keeps such a subscription `active` with `cancel_at_period_end: true`
    # until the period ends, and resuming it sets the flag back to false; both
    # arrive as `customer.subscription.updated`. The status is untouched, so the
    # agency stays paid until then. A deleted subscription is simply over.
    agency.subscription_cancel_at_period_end = (
        False if deleted else bool(_get(subscription, "cancel_at_period_end"))
    )

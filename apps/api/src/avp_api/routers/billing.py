"""Subscription billing — Epic 9.15.

Recorded in docs/api-contracts.md.

WHY THIS IS A NEW ROUTER AND NOT MORE OF `agencies.py`
------------------------------------------------------
Three of these four routes are agency-scoped and would sit perfectly well in
`agencies.py`. The fourth is why they do not.

`POST /billing/webhook` has **no session, no principal, no role and no agency in
its path.** It is authenticated by an HMAC over its own raw body, its caller is
a machine at Stripe, and the question it answers is "did Stripe really send
this" rather than "who are you and may you". Every other route in `agencies.py`
opens by resolving a principal and checking a role; this one cannot, and a
router whose module docstring says "Owner or admin. A member holds a seat; they
do not decide who else does" would then be carrying a route that gate does not
describe.

Keeping them together would mean one module with two authority models in it,
and the failure mode of that is specific and bad: somebody adds a route, copies
the shape of the one above it, and copies the wrong one. `report.py` is the
precedent for a router spanning two path families with no prefix, so that is
the shape used here — `/agencies/{id}/billing/...` and `/billing/webhook`.

WHY ALL THREE AUTHENTICATED ROUTES ARE OWNER/ADMIN
---------------------------------------------------
Epic 9.14 set owner-or-admin for seat management on the argument that a member
holds a seat and does not decide who else does. The same argument applies more
strongly to money: `UserRole`'s own docstring says "OWNER is the billing
contact", and a member is not it.

The READ is gated too, which is worth defending because it is the less obvious
half. A member seeing "No active subscription" beside a Subscribe button they
will get a 403 from is a worse screen than one that does not offer it, and the
settings page already knows how to degrade a forbidden section — it does exactly
this for the seat roster, which a member also cannot read.

THE ONE THING THE BROWSER CANNOT DO
------------------------------------
**No route here lets a browser set a subscription status.** Checkout returns a
URL and the portal returns a URL; neither writes a status. The only thing in the
entire system that may mark an agency subscribed is a Stripe-signed webhook.
That is not defensive tidiness — it is the difference between a subscription
being a fact and a subscription being whatever the last request claimed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Path, Request, status

from ..deps import DbDep, RequireAdmin, SettingsDep, assert_own_agency
from ..schemas.billing import (
    BillingStatusOut,
    CheckoutSessionOut,
    PortalSessionOut,
    WebhookAck,
)
from ..services import billing as billing_service
from ..services import scan_allowance

router = APIRouter(tags=["billing"])


@router.get("/agencies/{agencyId}/billing", response_model=BillingStatusOut)
async def read_billing(
    principal: RequireAdmin,
    db: DbDep,
    settings: SettingsDep,
    agency_id: str = Path(alias="agencyId"),
) -> Any:
    """This agency's subscription, as recorded, and its scan allowance. **Never asks Stripe.**

    The allowance — `scansUsedThisPeriod`, `scanLimit`, `periodResetsAt` — is
    one COUNT over the agency's scans (2026-09-28, `services/scan_allowance`).
    `scanLimit` is null when billing is not configured on this deployment,
    which is the honest way to say no gate applies. `freeScanRequiresGoogle`
    (2026-09-29) is true when the free scan is what this sign-in would start
    and it has no linked Google identity — the case a scan start refuses with
    `SCAN_LIMIT_VERIFY_REQUIRED`. It is about the requesting user, not the
    agency: two seats of one agency can read it differently.

    `cancelAtPeriodEnd` (2026-09-29) is true when the subscription was
    cancelled in Stripe's portal and runs to `currentPeriodEnd` without
    renewing. It is still `active` until then, so `isActive` stays true and
    the scan allowance stays the plan's.

    Every field comes off the `agencies` row the webhook maintains. A settings
    screen that called a payment processor on page load would be slower, would
    fail whenever Stripe was slow, and would put a third party in the render
    path of a page with four other sections on it — for a value that would be
    no more current than the webhook already makes it.

    The agency is already loaded on the principal, so there is no query here at
    all beyond the one `current_principal` performs on every request.

    **Errors:** `401`, `403` (member role), `404` (another agency's id).
    """
    assert_own_agency(principal.agency_id, agency_id)
    agency = principal.agency
    current = await scan_allowance.allowance(db, agency, settings, user=principal.user)

    return BillingStatusOut(
        subscription_status=agency.subscription_status,
        is_active=billing_service.is_active(agency),
        current_period_end=agency.subscription_current_period_end,
        # Whether the hosted portal has anything to open. Derived rather than
        # exposing the customer id, which the browser has no use for.
        has_billing_account=bool(agency.stripe_customer_id),
        scans_used_this_period=current.scans_used,
        scan_limit=current.scan_limit,
        period_resets_at=current.period_resets_at,
        free_scan_requires_google=current.free_scan_requires_google,
        cancel_at_period_end=agency.subscription_cancel_at_period_end,
    )


@router.post(
    "/agencies/{agencyId}/billing/checkout",
    response_model=CheckoutSessionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": (
                "`/problems/subscription-already-active`: the agency already "
                "has an active or trialing subscription."
            ),
            "content": {"application/problem+json": {}},
        }
    },
)
async def start_checkout(
    principal: RequireAdmin,
    db: DbDep,
    settings: SettingsDep,
    agency_id: str = Path(alias="agencyId"),
) -> Any:
    """Create a Stripe Checkout Session. Returns the URL to navigate to.

    **`201`, because a session really is created** — at Stripe, with an id and a
    lifetime, and this call is what makes it. That it lives in someone else's
    database does not make it less of a created resource.

    The agency's Stripe customer is created here too, if it does not have one
    yet, and REUSED if it does. A second visit to this endpoint does not mint a
    second customer — see `services/billing.py`, where the reuse and the unique
    constraint under it are explained.

    **This endpoint does not subscribe anybody.** It returns a URL. The
    subscription begins when Stripe says it did, over the webhook below.

    **Errors:** `401`, `403` (member role), `404` (another agency's id), `409
    subscription-already-active` when the agency's status is `active` or
    `trialing` (checked after auth and tenancy, before any Stripe call), `503
    checkout-in-progress` when another checkout holds the agency row for over
    3 s, `503
    service-unavailable` when Stripe fails expiring the previous open session, `503
    billing-not-configured` when `STRIPE_SECRET_KEY` or `STRIPE_PRICE_ID` is
    unset — carrying a detail that names the variable.
    """
    assert_own_agency(principal.agency_id, agency_id)

    url = await billing_service.create_checkout_session(
        db, principal.agency, settings=settings
    )
    # The customer id `ensure_customer` may have written is flushed, not
    # committed, until here. Committing after the Stripe call rather than before
    # is deliberate: if session creation fails, the agency is left with no
    # customer id and the next attempt makes one cleanly, rather than holding a
    # pointer to a customer that was created and then orphaned.
    await db.commit()

    return CheckoutSessionOut(url=url)


@router.post(
    "/agencies/{agencyId}/billing/portal",
    response_model=PortalSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def open_portal(
    principal: RequireAdmin,
    settings: SettingsDep,
    agency_id: str = Path(alias="agencyId"),
) -> Any:
    """Create a Stripe Billing Portal session. Returns the URL to navigate to.

    Update a card, cancel, download invoices — Stripe's own hosted screens, for
    one API call and no UI here. Building those would mean handling card
    details, dunning and invoice PDFs, which is a product rather than a feature,
    and one that already exists.

    Nothing is written, so nothing is committed. The `201` is for the session
    created at Stripe, exactly as above.

    **Errors:** `401`, `403`, `404`, `503 billing-not-configured` — including
    when the agency has never subscribed and so has no customer for the portal
    to be about.
    """
    assert_own_agency(principal.agency_id, agency_id)

    url = await billing_service.create_portal_session(principal.agency, settings=settings)
    return PortalSessionOut(url=url)


@router.post(
    "/billing/webhook",
    include_in_schema=True,
    response_model=WebhookAck,
    response_model_exclude_none=True,
)
async def stripe_webhook(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> WebhookAck:
    """Stripe telling us what happened. **The only writer of subscription state.**

    **No session auth, and that is not a hole.** The caller is a machine with no
    account; what authenticates it is an HMAC over the exact bytes of this
    request body, keyed on `STRIPE_WEBHOOK_SECRET`. That is a stronger claim
    than a session cookie makes, because it is a claim about the payload and not
    merely about the sender.

    **The raw body is read, not a parsed model.** `await request.body()` rather
    than a Pydantic parameter, because the signature covers the bytes Stripe
    sent. Letting FastAPI parse and re-serialise first would verify a signature
    against a payload that is equal as JSON and different as bytes, which fails
    for something as ordinary as key order. This is the single most common way
    webhook verification is written wrongly, and it fails closed and confusingly
    rather than obviously.

    **With no secret configured this refuses.** `503`, naming the variable, and
    it does not fall back to trusting the body — see `verify_webhook`, which
    explains why this is the opposite of the answer `services/email.py` gives
    for its own unset key.

    **An unrecognised event type is a `200`.** Stripe sends whatever the account
    is configured to send, and answering non-2xx to an event nobody wants makes
    Stripe retry it, back off, and eventually mark the endpoint unhealthy —
    which degrades delivery of the events that DO matter. The body says whether
    it was acted on, so a human reading `stripe listen` output can tell the
    difference between "handled" and "politely ignored".

    **Idempotent and ordered.** Each handled event id is recorded once; a
    redelivery answers `{"received": true, "handled": false, "duplicate": true}`
    and applies nothing. An event older than the agency's newest applied one
    answers `{"received": true, "handled": false, "stale": true}` and applies
    nothing. Unmatched and unhandled events answer `handled: false` alone.

    **Errors:** `400 invalid-webhook-signature` for a missing, malformed or
    wrong signature, and for a body that is not JSON. `503
    billing-not-configured` when `STRIPE_WEBHOOK_SECRET` is unset.
    """
    payload = await request.body()
    event = billing_service.verify_webhook(payload, stripe_signature, settings=settings)

    result = await billing_service.apply_event(db, event)
    if result.writes:
        await db.commit()
    else:
        # Nothing to keep (ignored or duplicate); release the agency lock now.
        await db.rollback()

    return WebhookAck(
        received=True,
        handled=result is billing_service.WebhookResult.APPLIED,
        duplicate=True if result is billing_service.WebhookResult.DUPLICATE else None,
        stale=True if result is billing_service.WebhookResult.STALE else None,
    )

"""Subscription billing — Epic 9.15.

Recorded in docs/api-contracts.md.

WHY THIS IS A NEW ROUTER AND NOT MORE OF `agencies.py`
------------------------------------------------------
Three of these four routes are agency-scoped and would sit perfectly well in
`agencies.py`. The fourth is why they do not.

`POST /billing/webhook`, which lands in a later commit, has **no session, no
principal, no role and no agency in its path.** It is authenticated by an HMAC
over its own raw body, its caller is a machine at Stripe, and the question it
answers is "did Stripe really send this" rather than "who are you and may you".
Every other route in `agencies.py`
opens by resolving a principal and checking a role; this one cannot, and a
router whose module docstring says "Owner or admin. A member holds a seat; they
do not decide who else does" would then be carrying a route that gate does not
describe.

Keeping them together would mean one module with two authority models in it,
and the failure mode of that is specific and bad: somebody adds a route, copies
the shape of the one above it, and copies the wrong one. `report.py` is the
precedent for a router spanning two path families with no prefix, so that is
the shape used here — `/agencies/{id}/billing/...` and `/billing/webhook`.

WHY THE AUTHENTICATED ROUTES ARE OWNER/ADMIN
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
URL; it does not write a status. The only thing in the
entire system that may mark an agency subscribed is a Stripe-signed webhook.
That is not defensive tidiness — it is the difference between a subscription
being a fact and a subscription being whatever the last request claimed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status

from ..deps import DbDep, RequireAdmin, SettingsDep, assert_own_agency
from ..schemas.billing import BillingStatusOut, CheckoutSessionOut
from ..services import billing as billing_service

router = APIRouter(tags=["billing"])


@router.get("/agencies/{agencyId}/billing", response_model=BillingStatusOut)
async def read_billing(
    principal: RequireAdmin,
    agency_id: str = Path(alias="agencyId"),
) -> Any:
    """This agency's subscription, as recorded. **Never asks Stripe.**

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

    return BillingStatusOut(
        subscription_status=agency.subscription_status,
        is_active=billing_service.is_active(agency),
        current_period_end=agency.subscription_current_period_end,
        # Whether the hosted portal has anything to open. Derived rather than
        # exposing the customer id, which the browser has no use for.
        has_billing_account=bool(agency.stripe_customer_id),
    )


@router.post(
    "/agencies/{agencyId}/billing/checkout",
    response_model=CheckoutSessionOut,
    status_code=status.HTTP_201_CREATED,
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

    **Errors:** `401`, `403` (member role), `404` (another agency's id), `503
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

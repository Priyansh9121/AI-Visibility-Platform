'use client';

/**
 * The billing section of Settings — Epic 9.15.
 *
 * Settings has carried the line "Billing, plans and usage limits" on its "not
 * built yet" list since Epic 9.13, with the reason attached: north-star.md §5.3
 * recorded pricing as undecided, and "showing a plan picker would imply a
 * decision nobody has made". A decision has now been made — one plan, $29/month
 * (then; $160 from 2026-09-22 and $150 since 2026-10-04, by founder decision, through the same
 * `PLAN_PRICE_USD`) — so the line comes off and the real thing takes its place. That is the same
 * rule 9.14 applied when seat management shipped, cutting in the same
 * direction.
 *
 * SHAPED ON `SeatsPanel`, WHICH IS THIS CODEBASE'S ANSWER TO THIS PROBLEM
 * -----------------------------------------------------------------------
 * The parent owns fetching and passes the current state as a prop; this owns
 * the interaction, calls the API itself, maps an `ApiProblem` to a sentence,
 * and hands the parent an `onChanged` callback. Nothing new was invented.
 *
 * WHAT THIS SCREEN SAYS OUT LOUD
 * ------------------------------
 * That nothing is gated. An agency with no subscription is not in a degraded
 * state and must not be shown one — no warning tone, no lock, no "upgrade to
 * continue". They are simply not paying yet, and everything works. Getting this
 * wrong would gate the founder's existing test accounts in the reader's mind if
 * nowhere else, which is precisely what this brief said not to do.
 *
 * And it says `past_due` plainly. north-star.md §5.4 row 4 asks for a
 * billing-failure state "rendered as honestly as `partial` is today" — the
 * precedent being `ScanStatus.PARTIAL`, which Epic 9.2 made a visible
 * degradation rather than a routine one. An agency whose payment failed sees
 * that in those words; it does not discover it as features quietly not working.
 *
 * WHERE SUBSCRIBE IS OFFERED — founder decision 4, 2026-10-01
 * -----------------------------------------------------------
 * Exactly where the API will start a Checkout, and nowhere else: no
 * subscription at all, `canceled`, and `incomplete_expired`. The last two are
 * over — the subscription ended, or never began — so an agency in either can
 * come back from here, with the same plan card, at the same price, beneath its
 * status. Every other status already has a subscription, and the API answers a
 * Checkout for it with a 409: `active` and `trialing` because the agency is
 * paying, `past_due`, `unpaid`, `paused` and `incomplete` because Stripe still
 * holds that subscription open and a second Checkout would create a second one
 * beside it. Those get their status and Manage billing, and no Subscribe, so
 * the panel never shows a button that can only fail.
 *
 * The one way the page and the API can still disagree is a page loaded before
 * the subscription changed. A subscription refusal from Checkout
 * (`subscription-already-active`, `subscription-needs-attention`) is that
 * disagreement, and the sentence the API sends points at Manage billing — so the
 * panel shows that button then, in the no-subscription state too, where it is
 * otherwise absent, and whether or not this page knew of a Stripe customer: the
 * portal reads the customer on the server. `checkout-in-progress` is a 409 as
 * well, but it says to try again in a moment and nothing about a subscription,
 * so it shows its sentence and nothing else (`lib/billing/subscriptionRefusal`).
 *
 * The no-competitor-reference rules: derived from the API's own `BillingStatus` shape and
 * the states it can actually be in. No competitor's billing screen was opened
 * or referenced.
 */

import { useEffect, useState, type JSX } from 'react';
import { Badge, Button, Card, CardBody } from '@avp/design-system';
import type { BadgeTone } from '@avp/design-system';
import type { BillingStatus } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { canCheckout, freeScanUsed } from '@/lib/billing/allowance';
import { isSubscriptionRefusal } from '@/lib/billing/subscriptionRefusal';
import { formatDay } from '@/lib/dates';
import { PricingCard } from '@/components/marketing/PricingCard';

// WHICH SUBSCRIPTIONS CAN START A CHECKOUT is `canCheckout`, in
// `lib/billing/allowance` — founder decision 4, 2026-10-01 — and not a set kept
// here. This panel kept its own copy until the UI review of 2026-10-01 found
// it was the same three values the scan-limit notice and the Settings lead
// read: no subscription at all (`null`), `canceled` and `incomplete_expired`.
// It is an allow-list on purpose: a status this screen has never seen is not
// one the API was told about either, and a Subscribe that might 409 is worse
// than Manage billing, which always has somewhere to go.

/**
 * How each Stripe status is said out loud, and how it is toned.
 *
 * Stripe's vocabulary, not ours — `models/tenancy.py` explains why the column
 * stores their string verbatim. This map is the one place it becomes English,
 * and an unrecognised status falls through to the status text itself rather
 * than to a blank: a status we have never seen is exactly the thing an operator
 * needs to be able to read back to us.
 */
const STATUS_COPY: Record<string, { label: string; tone: BadgeTone; sentence: string }> = {
  active: {
    label: 'Active',
    tone: 'success',
    sentence: 'Your subscription is live.',
  },
  trialing: {
    label: 'Trial',
    tone: 'success',
    sentence: 'You are inside a trial period. Nothing has been charged yet.',
  },
  past_due: {
    label: 'Payment failed',
    tone: 'warn',
    sentence:
      'The last payment did not go through. Nothing has been switched off, and nothing will be without telling you first — but the card on file needs attention.',
  },
  unpaid: {
    label: 'Unpaid',
    tone: 'warn',
    sentence:
      'Stripe has stopped retrying the last payment. The card on file needs attention.',
  },
  incomplete: {
    label: 'Incomplete',
    tone: 'neutral',
    sentence:
      'A subscription was started but the first payment was never confirmed. Manage billing shows what Stripe holds for it.',
  },
  incomplete_expired: {
    label: 'Expired',
    tone: 'neutral',
    sentence: 'A subscription was started and never confirmed, and it has since expired.',
  },
  canceled: {
    label: 'Canceled',
    tone: 'neutral',
    sentence:
      'This subscription has been cancelled. Starting a scan is back on the free allowance; every report you already have stays open.',
  },
  paused: {
    label: 'Paused',
    tone: 'neutral',
    sentence: 'This subscription is paused.',
  },
};

export interface BillingPanelProps {
  agencyId: string;
  /** Null when the caller may not read billing — a member gets a 403. */
  billing: BillingStatus | null;
  /** Why `billing` is null, in a sentence. */
  billingError?: string | null;
  /**
   * Set in tests to render a state a static render cannot reach: a failed
   * Subscribe, shown beside the plan card when there is one and in the status
   * card when there is not.
   *
   * There is deliberately no `onChanged` here, unlike `SeatsPanel`. Every
   * action on this panel navigates the browser to Stripe, so there is no
   * post-mutation state for the parent to re-read — and a callback that is
   * never called is a prop the next reader has to work out the truth about.
   */
  initialError?: string | null | undefined;
}

export function BillingPanel({
  agencyId,
  billing,
  billingError = null,
  initialError = null,
}: BillingPanelProps): JSX.Element {
  // Which call is in flight. Both buttons are disabled while either is, but
  // only the one that was clicked says "Opening…": they can share a screen now.
  const [opening, setOpening] = useState<'checkout' | 'portal' | null>(null);
  // A failure shows beside the button that caused it. The plan card is tall, so
  // an alert at the far end of it could be out of sight of Manage billing.
  const [checkoutError, setCheckoutError] = useState<string | null>(initialError);
  const [portalError, setPortalError] = useState<string | null>(null);
  // The API has refused a Checkout because this agency already has a
  // subscription. Sticky: it stays true if the next click fails some other way.
  const [refusedAsSubscribed, setRefusedAsSubscribed] = useState(false);

  useEffect(() => {
    // After `location.assign` the browser may keep this page in its
    // back/forward cache and restore it, exactly as it was, when the person
    // comes back from Stripe. State included: `opening` would still be set and
    // both buttons still disabled. `persisted` is true only for that restore; an
    // ordinary load has nothing to undo.
    function onPageShow(event: PageTransitionEvent): void {
      if (event.persisted) setOpening(null);
    }
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, []);

  async function manage(): Promise<void> {
    setOpening('portal');
    setCheckoutError(null);
    setPortalError(null);
    try {
      const { url } = await api.openBillingPortal(agencyId);
      window.location.assign(url);
    } catch (err) {
      setOpening(null);
      setPortalError(
        err instanceof ApiProblem
          ? err.problem.detail
          : 'The billing portal could not be opened. Nothing has changed.',
      );
    }
  }

  async function subscribe(): Promise<void> {
    setOpening('checkout');
    setCheckoutError(null);
    setPortalError(null);
    try {
      const { url } = await api.startCheckout(agencyId);
      // A full navigation, not a router push: the destination is Stripe's
      // domain, not a route in this app.
      window.location.assign(url);
    } catch (err) {
      setOpening(null);
      if (err instanceof ApiProblem) {
        setCheckoutError(err.problem.detail);
        // The type, not the status: the API refuses with a 409 under three
        // types, and only two of them (`subscription-already-active`,
        // `subscription-needs-attention`) say this agency has a subscription
        // and that Manage billing is where it is dealt with. The third,
        // `checkout-in-progress`, says to try again in a moment.
        if (isSubscriptionRefusal(err.problem)) setRefusedAsSubscribed(true);
      } else {
        setCheckoutError('Checkout could not be opened. Nothing was charged.');
      }
    }
  }

  if (billing === null) {
    return (
      <Card elevation="seated">
        <CardBody>
          <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
            {billingError ?? 'Billing is not available on this account.'}
          </p>
        </CardBody>
      </Card>
    );
  }

  const status = billing.subscriptionStatus;

  // The plan card, exactly as the public page shows it — the same component, in
  // its signed-in state, so the price a customer was quoted on the way in is the
  // price they see on the way to paying it. The same card in every state that
  // offers it (see `canCheckout`).
  const plan = canCheckout(status) ? (
    <PricingCard
      signedIn
      onSubscribe={subscribe}
      busy={opening === 'checkout'}
      disabled={opening !== null}
      error={checkoutError}
    />
  ) : null;

  // Offered whenever a subscription exists and a Stripe customer does, which is
  // a wider condition than `isActive` on purpose. A cancelled or past-due
  // subscription is exactly when somebody needs to reach their invoices and
  // their card, and hiding the door at that moment would be hiding it in the
  // only situation that makes it urgent. With no subscription at all it waits
  // for a refusal from Checkout: a customer who never finished Checkout has
  // nothing in the portal, but the API saying this agency has a subscription
  // means there is something there after all. And then it is offered whatever
  // `hasBillingAccount` said, which is this page's snapshot; the portal reads
  // the customer on the server, and the API's sentence points at the button.
  const showManage =
    (billing.hasBillingAccount && status !== null) || refusedAsSubscribed;

  // A failed portal belongs beside the button that opens it. A failed Subscribe
  // belongs beside the plan card; with no plan card on screen it can only be the
  // test seam's, and this is where it has always shown.
  const statusError = portalError ?? (plan === null ? checkoutError : null);
  const statusAlert =
    statusError != null ? (
      <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
        {statusError}
      </p>
    ) : null;

  const manageRow = showManage ? (
    <div className="flex flex-wrap items-center gap-4">
      <Button variant="secondary" onClick={() => void manage()} disabled={opening !== null}>
        {opening === 'portal' ? 'Opening portal…' : 'Manage billing'}
      </Button>
      <p className="max-w-measure text-ui-sm text-text-tertiary">
        Opens Stripe, where your card, your invoices and cancelling all live. We
        never see or store a card number.
      </p>
    </div>
  ) : null;

  // No subscription at all.
  if (status === null) {
    return (
      <div className="flex flex-col gap-4">
        <Card elevation="seated">
          <CardBody>
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone="neutral">No subscription</Badge>
              <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
                {freeScanUsed(billing)
                  ? 'You are not subscribed. Your free scan is used; subscribing is how you keep scanning. '
                  : 'You are not subscribed. Your agency’s first scan is free with a Google sign-in; subscribing is how you keep scanning. '}
                Reports you already have stay open, shareable and exportable either way.
              </p>
            </div>
          </CardBody>
        </Card>
        {plan}
        {/* Below the plan card, where the refusal's sentence is, not above it. */}
        {showManage && (
          <Card elevation="seated">
            <CardBody>
              <div className="flex flex-col gap-4">
                {statusAlert}
                {manageRow}
              </div>
            </CardBody>
          </Card>
        )}
      </div>
    );
  }

  const copy = STATUS_COPY[status];
  // Cancelled in the portal but still paid up. Manage billing stays below, so
  // the plan can be resumed before it ends.
  const ending = billing.isActive && billing.cancelAtPeriodEnd;

  const statusCard = (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={copy?.tone ?? 'neutral'}>{copy?.label ?? status}</Badge>
            {billing.isActive && billing.currentPeriodEnd != null && (
              // "Ends", not "Renews", once it has been cancelled — 2026-09-29.
              // Stripe's portal cancels at the end of the period, so the
              // subscription stays active (and the plan applies) until then.
              <p className="text-ui-base text-text-primary">
                {ending ? 'Ends' : 'Renews'} {formatDay(billing.currentPeriodEnd)}
              </p>
            )}
          </div>

          <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
            {ending
              ? 'Cancelled — you keep full access until then, and it won’t renew.'
              : (copy?.sentence ??
                `Stripe reports this subscription as “${status}”, which this screen does not have wording for yet. Starting a scan is on the free allowance until it is active again; every report you already have stays open.`)}
          </p>

          {statusAlert}
          {manageRow}
        </div>
      </CardBody>
    </Card>
  );

  // `canceled` and `incomplete_expired`: the status, then the way back in.
  return plan === null ? (
    statusCard
  ) : (
    <div className="flex flex-col gap-4">
      {statusCard}
      {plan}
    </div>
  );
}

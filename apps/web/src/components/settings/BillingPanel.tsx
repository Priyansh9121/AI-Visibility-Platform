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
 * Checkout for it with a 409: `active` and `trialing` because the agency has a
 * current subscription (paying, or inside its free trial), `past_due`, `unpaid`,
 * `paused` and `incomplete` because Stripe still
 * holds that subscription open and a second Checkout would create a second one
 * beside it. Those get their status and, for the owner, Manage billing, and no
 * Subscribe, so the panel never shows a button that can only fail.
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
 * THE FREE TRIAL — 2026-10-04
 * ---------------------------
 * While the agency is `trialing`, and the trial is not being cancelled, the status card
 * says "Free trial until <the trial's end, with its time, UTC>, then $<price> a month", from
 * the billing response's `trialEndsAt` and `PLAN_PRICE_USD`, in place of "Renews <date>": the
 * card has not been charged, and that instant is the one it will be, and so the cancel-by
 * deadline (`formatDayTime`: a day alone is hours of doubt, and the day is unpadded, as a
 * date is mid-sentence).
 * Under it, what to do: "Nothing has been charged yet. Cancel in Manage billing before then
 * and nothing will be." The second sentence points at Manage billing, which is the owner's
 * alone (3.130, below), so an admin reads "Nothing has been charged yet." and no more of it.
 *
 * Cancelled during the trial, "then $<price> a month" would be untrue, and so would "it won’t
 * renew" (a trial was going to be charged, not renewed): the "Ends <day>" line takes over, with
 * "Cancelled — you keep full access until then, and you will not be charged." A cancelled PAID
 * plan keeps "…and it won’t renew." In every other state the panel says nothing about a trial.
 *
 * A free trial needs a verified Google identity on the user who subscribes (2026-10-05, the free
 * scan's rule). `trialEligible` is therefore about THIS user, and where the API says the trial
 * is withheld only for want of one (`trialRequiresGoogle`) a sentence above the plan card says
 * subscribing now starts the paid plan at once and that Continue with Google gives the trial.
 *
 * The plan card, where it is offered, describes the trial's terms only where the billing
 * response says this agency would get it (`trialEligible`: the API's word, a trial is on AND
 * the agency's record has never had a subscription). Checkout gives one trial per agency, so a
 * cancelled agency is not promised another. The length arrives as `initialTrialDays` from the
 * route, which asked early; left out, the card asks for itself.
 *
 * WHO IS OFFERED THE CONTROLS — founder decision of 2026-10-11, launch checklist 3.130
 * ------------------------------------------------------------------------------------
 * Subscribe and Manage billing are the owner's alone: the API refuses Checkout and the portal to
 * an admin (`RequireOwner`), so an admin is not shown a button that can only fail. An admin reads
 * the status as an owner does, without the clause of a status sentence that points at Manage
 * billing (`ownerClause`), and where the owner would see the plan card or Manage billing, sees
 * one plain line: "Ask an owner to manage billing." (`role`, from `me.user.role`.) A member
 * never gets this far: the read is a 403 for them and `billing` is null.
 *
 * The no-competitor-reference rules: derived from the API's own `BillingStatus` shape and
 * the states it can actually be in. No competitor's billing screen was opened
 * or referenced.
 */

import { useEffect, useState, type JSX } from 'react';
import { Badge, Button, Card, CardBody, GoogleSignInButton } from '@avp/design-system';
import type { BadgeTone } from '@avp/design-system';
import type { BillingStatus, UserRole } from '@avp/shared-types';
import { api, ApiProblem, GOOGLE_SIGN_IN_URL } from '@/lib/api';
import {
  ASK_AN_OWNER_TO_MANAGE_BILLING,
  canChangeBilling,
  canCheckout,
  freeScanUsed,
} from '@/lib/billing/allowance';
import { isSubscriptionRefusal } from '@/lib/billing/subscriptionRefusal';
import { formatDay, formatDayTime } from '@/lib/dates';
import { PLAN_PRICE_USD, PricingCard } from '@/components/marketing/PricingCard';

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
 *
 * A clause that points at Manage billing is its own field, `ownerClause`, and is read only by
 * the owner (launch checklist 3.130): an admin has no Manage billing, and a sentence that sends
 * them to it would send them to a button that is not there. Appended to `sentence` with a
 * space, so the owner's text is the one string it always was.
 */
const STATUS_COPY: Record<
  string,
  { label: string; tone: BadgeTone; sentence: string; ownerClause?: string }
> = {
  active: {
    label: 'Active',
    tone: 'success',
    sentence: 'Your subscription is live.',
  },
  trialing: {
    label: 'Trial',
    tone: 'success',
    sentence: 'Nothing has been charged yet.',
    // "before then" is the trial's end, in the line above it. Said as a thing to do: the
    // person has a card on file and a date, and what they need is how not to be charged.
    ownerClause: 'Cancel in Manage billing before then and nothing will be.',
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
    sentence: 'A subscription was started but the first payment was never confirmed.',
    ownerClause: 'Manage billing shows what Stripe holds for it.',
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
  /**
   * The signed-in user's role (`me.user.role`). Only the owner is offered Subscribe and Manage
   * billing; an admin, who can read the status, is told to ask an owner instead (launch checklist
   * 3.130). Required, so that no caller can leave the controls on by forgetting it.
   */
  role: UserRole;
  /** Null when the caller may not read billing — a member gets a 403. */
  billing: BillingStatus | null;
  /** Why `billing` is null, in a sentence. */
  billingError?: string | null;
  /**
   * The free trial's length, when the page already asked for it: `app/settings/page.tsx` asks at
   * the top of its loader and passes the answer down through `SettingsView`, so the plan card
   * is drawn with its sentence and does not ask (`PricingCard`). Left out, the card asks for
   * itself. 0 is "no trial".
   */
  initialTrialDays?: number | undefined;
  /**
   * The page's own request for the plan, when it asked early and gave up waiting for the answer:
   * a card drawn untold awaits THIS rather than asking again, so there is one request for the
   * whole page, however slow it is (round three). Ignored when `initialTrialDays` is given.
   */
  trialPlan?: Promise<number> | undefined;
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
  role,
  billing,
  billingError = null,
  initialError = null,
  initialTrialDays,
  trialPlan,
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

  // Only the owner starts a Checkout or opens the portal (launch checklist 3.130). An admin has
  // the status above and, where the owner would have a control, the one line below.
  const mayChange = canChangeBilling(role);
  const askAnOwner = mayChange ? null : (
    <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
      {ASK_AN_OWNER_TO_MANAGE_BILLING}
    </p>
  );

  // The plan card, exactly as the public page shows it — the same component, in
  // its signed-in state, so the price a customer was quoted on the way in is the
  // price they see on the way to paying it. The same card in every state that
  // offers it (see `canCheckout`). Not for an admin: Subscribe is the owner's.
  const planCard = mayChange && canCheckout(status) ? (
    <PricingCard
      signedIn
      onSubscribe={subscribe}
      busy={opening === 'checkout'}
      disabled={opening !== null}
      error={checkoutError}
      // The API's word, not this file's: a trial is on AND this agency's record has never had
      // a subscription (the row test checkout applies, `trialEligible` on the billing response).
      // It used to be `status === null`, which cannot see a record that says otherwise.
      // `canceled` and `incomplete_expired` are offered the plan, and no trial. Only a true is a
      // promise: a response without the field (an API not yet deployed with it) promises nothing.
      trialEligible={billing.trialEligible === true}
      initialTrialDays={initialTrialDays}
      trialPlan={trialPlan}
    />
  ) : null;

  // A free trial needs a Google sign-in on the user who subscribes (2026-10-05, the free scan's
  // rule). For a user without one, where the API says the trial is the ONLY thing Google would
  // change (`trialRequiresGoogle`), say so beside the plan, with the same Continue with Google
  // the scan-limit notice carries: on the same address it links this account. A user with Google
  // sees nothing new. Only a true says it: a response without the field says nothing.
  const trialNeedsGoogle = planCard !== null && billing.trialRequiresGoogle === true;
  const plan =
    planCard !== null && trialNeedsGoogle ? (
      <div className="flex flex-col gap-4">
        <Card elevation="flat">
          <CardBody>
            <div className="flex flex-col gap-4" data-testid="trial-needs-google">
              <p className="max-w-measure text-ui-base leading-prose text-text-primary">
                Subscribing now starts the paid plan at once, with no free trial: a free trial
                needs a Google sign-in. Continue with Google using the same address you sign in
                with here; a subscription started after that begins with the free trial, unless
                this agency has subscribed before. A different Google account would start a new
                agency instead.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <GoogleSignInButton href={GOOGLE_SIGN_IN_URL} label="Continue with Google" />
              </div>
            </div>
          </CardBody>
        </Card>
        {planCard}
      </div>
    ) : (
      planCard
    );

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
  // Never for an admin, who is told to ask an owner (`askAnOwner`) where this would be.
  const showManage =
    mayChange && ((billing.hasBillingAccount && status !== null) || refusedAsSubscribed);

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

  // Where the owner has Manage billing, an admin has the line that says to ask an owner (and an
  // owner has `null` for `askAnOwner`, so for them this is what it always was).
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
  ) : (
    askAnOwner
  );

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
            {/* An admin: where the owner has the plan card, one line (launch checklist 3.130). */}
            {askAnOwner !== null && <div className="mt-4">{askAnOwner}</div>}
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
  // Cancelled in the portal but still paid up. For the owner, Manage billing stays below, so
  // the plan can be resumed before it ends.
  const ending = billing.isActive && billing.cancelAtPeriodEnd;
  // A trial under way and not being cancelled: when the card is first charged, and what it is
  // charged. Read off the STATUS, so a leftover date on any other state says nothing. With
  // the TIME, in UTC: the end is the cancel-by deadline, and a day alone leaves "until the 21st"
  // open to a reading that is hours wrong. The day is unpadded (`formatDayTime`): it sits
  // mid-sentence, where a leading zero reads as a typo.
  const trialLine =
    status === 'trialing' && !ending && billing.trialEndsAt != null
      ? `Free trial until ${formatDayTime(billing.trialEndsAt)}, then $${PLAN_PRICE_USD} a month`
      : null;
  // What a cancelled plan says. A cancelled TRIAL was never going to renew: it was going to be
  // charged, so "it won’t renew" would be the wrong thing to say, and what the person wants to
  // know is that they will not be charged. A cancelled paid plan keeps its own sentence.
  const endingSentence =
    status === 'trialing'
      ? 'Cancelled — you keep full access until then, and you will not be charged.'
      : 'Cancelled — you keep full access until then, and it won’t renew.';

  const statusCard = (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={copy?.tone ?? 'neutral'}>{copy?.label ?? status}</Badge>
            {trialLine !== null ? (
              <p className="text-ui-base text-text-primary">{trialLine}</p>
            ) : (
              billing.isActive &&
              billing.currentPeriodEnd != null && (
                // "Ends", not "Renews", once it has been cancelled — 2026-09-29.
                // Stripe's portal cancels at the end of the period, so the
                // subscription stays active (and the plan applies) until then.
                <p className="text-ui-base text-text-primary">
                  {ending ? 'Ends' : 'Renews'} {formatDay(billing.currentPeriodEnd)}
                </p>
              )
            )}
          </div>

          <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
            {ending
              ? endingSentence
              : copy !== undefined
                ? mayChange && copy.ownerClause !== undefined
                  ? `${copy.sentence} ${copy.ownerClause}`
                  : copy.sentence
                : `Stripe reports this subscription as “${status}”, which this screen does not have wording for yet. Starting a scan is on the free allowance until it is active again; every report you already have stays open.`}
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

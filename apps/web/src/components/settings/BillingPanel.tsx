'use client';

/**
 * The billing section of Settings — Epic 9.15.
 *
 * Settings has carried the line "Billing, plans and usage limits" on its "not
 * built yet" list since Epic 9.13, with the reason attached: north-star.md §5.3
 * recorded pricing as undecided, and "showing a plan picker would imply a
 * decision nobody has made". A decision has now been made — one plan, $29/month
 * — so the line comes off and the real thing takes its place. That is the same
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
 * ip-safety.md #1 and #5: derived from the API's own `BillingStatus` shape and
 * the states it can actually be in. No competitor's billing screen was opened
 * or referenced.
 */

import { useState, type JSX } from 'react';
import { Badge, Card, CardBody } from '@avp/design-system';
import type { BadgeTone } from '@avp/design-system';
import type { BillingStatus } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { formatDay } from '@/lib/dates';
import { PricingCard } from '@/components/marketing/PricingCard';

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
      'A subscription was started but the first payment was never confirmed. Starting again is the fix.',
  },
  incomplete_expired: {
    label: 'Expired',
    tone: 'neutral',
    sentence: 'A subscription was started and never confirmed, and it has since expired.',
  },
  canceled: {
    label: 'Canceled',
    tone: 'neutral',
    sentence: 'This subscription has been cancelled. Nothing in the product has changed.',
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
   * Set in tests to render a state a static render cannot reach.
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  async function subscribe(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.startCheckout(agencyId);
      // A full navigation, not a router push: the destination is Stripe's
      // domain, not a route in this app.
      window.location.assign(url);
    } catch (err) {
      setBusy(false);
      setError(
        err instanceof ApiProblem
          ? err.problem.detail
          : 'Checkout could not be opened. Nothing was charged.',
      );
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

  // No subscription at all. The plan card, exactly as the public page shows it
  // — the same component, in its signed-in state, so the price a customer was
  // quoted on the way in is the price they see on the way to paying it.
  if (billing.subscriptionStatus === null) {
    return (
      <div className="flex flex-col gap-4">
        <Card elevation="seated">
          <CardBody>
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone="neutral">No subscription</Badge>
              <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
                You are not subscribed. Everything in the product works anyway —
                there is nothing behind this that you are missing.
              </p>
            </div>
          </CardBody>
        </Card>
        <PricingCard
          signedIn
          onSubscribe={subscribe}
          busy={busy}
          error={error}
        />
      </div>
    );
  }

  const copy = STATUS_COPY[billing.subscriptionStatus];

  return (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={copy?.tone ?? 'neutral'}>
              {copy?.label ?? billing.subscriptionStatus}
            </Badge>
            {billing.isActive && billing.currentPeriodEnd != null && (
              <p className="text-ui-base text-text-primary">
                Renews {formatDay(billing.currentPeriodEnd)}
              </p>
            )}
          </div>

          <p className="max-w-measure text-ui-base leading-prose text-text-secondary">
            {copy?.sentence ??
              `Stripe reports this subscription as “${billing.subscriptionStatus}”, which this screen does not have wording for yet. Nothing in the product has changed.`}
          </p>

          {error != null && (
            <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
              {error}
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

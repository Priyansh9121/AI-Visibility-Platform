/**
 * Billing fixtures — Epic 9.15.
 *
 * Shaped to match what `GET /agencies/{id}/billing` actually returns, including
 * the two states easiest to render wrongly: the one every agency is in today
 * (nothing set, and NOT a degraded state), and `past_due`, which is a real
 * subscription that is not active.
 *
 * The date is fixed rather than relative, because `formatDay` is UTC and a
 * fixture computed from `Date.now()` produces an assertion that reads
 * differently depending on when CI ran.
 */

import type { BillingStatus } from '@avp/shared-types';

/** Every agency in the database today. Not an error, and not a warning. */
export const noSubscription: BillingStatus = {
  subscriptionStatus: null,
  isActive: false,
  currentPeriodEnd: null,
  hasBillingAccount: false,
};

export const activeSubscription: BillingStatus = {
  subscriptionStatus: 'active',
  isActive: true,
  currentPeriodEnd: '2026-09-21T14:13:20Z',
  hasBillingAccount: true,
};

/** Cancelled, and therefore with no next renewal date to show. */
export const canceledSubscription: BillingStatus = {
  subscriptionStatus: 'canceled',
  isActive: false,
  currentPeriodEnd: null,
  hasBillingAccount: true,
};

/**
 * A failed payment. north-star.md §5.4 row 4's case: it must be visible in
 * those words, not discovered as features quietly not working.
 */
export const pastDueSubscription: BillingStatus = {
  subscriptionStatus: 'past_due',
  isActive: false,
  currentPeriodEnd: '2026-09-21T14:13:20Z',
  hasBillingAccount: true,
};

/** A status this screen has no wording for — Stripe's vocabulary can grow. */
export const unknownStatusSubscription: BillingStatus = {
  subscriptionStatus: 'some_future_status',
  isActive: false,
  currentPeriodEnd: null,
  hasBillingAccount: true,
};

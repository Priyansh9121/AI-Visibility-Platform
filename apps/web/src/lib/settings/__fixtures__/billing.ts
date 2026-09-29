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
  scansUsedThisPeriod: 0,
  scanLimit: 1,
  periodResetsAt: null,
  freeScanRequiresGoogle: false,
  cancelAtPeriodEnd: false,
};

/** Free, and the one free scan already started. */
export const freeScanUsed: BillingStatus = {
  ...noSubscription,
  scansUsedThisPeriod: 1,
};

/**
 * Free, and this sign-in has no linked Google identity — 2026-09-29. The API
 * says so in `freeScanRequiresGoogle`; the web never works it out.
 */
export const freeScanNeedsGoogle: BillingStatus = {
  ...noSubscription,
  freeScanRequiresGoogle: true,
};

export const activeSubscription: BillingStatus = {
  subscriptionStatus: 'active',
  isActive: true,
  currentPeriodEnd: '2026-09-21T14:13:20Z',
  hasBillingAccount: true,
  scansUsedThisPeriod: 12,
  scanLimit: 40,
  periodResetsAt: '2026-10-01T00:00:00Z',
  freeScanRequiresGoogle: false,
  cancelAtPeriodEnd: false,
};

/**
 * Cancelled in Stripe's portal, paid to the end of the period — 2026-09-29.
 * Still `active` until then, so the plan still applies; the screen says
 * "Ends", not "Renews".
 */
export const activeEnding: BillingStatus = {
  ...activeSubscription,
  cancelAtPeriodEnd: true,
};

/** Paid and at the cap: the label says when it resets and offers no upgrade. */
export const activeAtCap: BillingStatus = {
  ...activeSubscription,
  scansUsedThisPeriod: 40,
};

/** Billing not configured on this deployment: no gate, no label. */
export const ungated: BillingStatus = {
  ...noSubscription,
  scansUsedThisPeriod: 7,
  scanLimit: null,
};

/** Cancelled, and therefore with no next renewal date to show. */
export const canceledSubscription: BillingStatus = {
  subscriptionStatus: 'canceled',
  isActive: false,
  currentPeriodEnd: null,
  hasBillingAccount: true,
  scansUsedThisPeriod: 3,
  scanLimit: 1,
  periodResetsAt: null,
  freeScanRequiresGoogle: false,
  cancelAtPeriodEnd: false,
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
  // A failed payment is free: the API's `is_active` says so, and the gate
  // reads `is_active`. One lifetime scan, already used by any real agency.
  scansUsedThisPeriod: 3,
  scanLimit: 1,
  periodResetsAt: null,
  freeScanRequiresGoogle: false,
  cancelAtPeriodEnd: false,
};

/** A status this screen has no wording for — Stripe's vocabulary can grow. */
export const unknownStatusSubscription: BillingStatus = {
  subscriptionStatus: 'some_future_status',
  isActive: false,
  currentPeriodEnd: null,
  hasBillingAccount: true,
  scansUsedThisPeriod: 1,
  scanLimit: 1,
  periodResetsAt: null,
  freeScanRequiresGoogle: false,
  cancelAtPeriodEnd: false,
};

/**
 * Which Checkout refusals mean "this agency already has a subscription" —
 * 2026-10-01.
 *
 * `POST /agencies/{id}/billing/checkout` answers 409 under three types:
 *
 *   - `subscription-already-active`: the agency has one it pays for.
 *   - `subscription-needs-attention`: it has one Stripe still holds open
 *     (`past_due`, `unpaid`, `paused`, `incomplete`).
 *   - `checkout-in-progress`: another checkout holds the agency for a moment.
 *     "Try again in a moment" — it says nothing about a subscription.
 *
 * The first two both say to manage the subscription from Manage billing in
 * Settings, so the panel and the scan-limit notice hand the person on to it. The
 * third must not be handed on, and its status is the same 409, so the TYPE
 * decides, not the status. Matched by how the type ends, the way
 * `scanLimitProblem` matches its own, so an absolute type URI reads the same as
 * a relative one.
 */

import type { ProblemDetail } from '@avp/shared-types';

const SUBSCRIPTION_REFUSALS = ['/subscription-already-active', '/subscription-needs-attention'];

/** Whether the API refused a Checkout because this agency already has a subscription. */
export function isSubscriptionRefusal(problem: ProblemDetail): boolean {
  return SUBSCRIPTION_REFUSALS.some((type) => problem.type.endsWith(type));
}

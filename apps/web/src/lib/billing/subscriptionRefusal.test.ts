/**
 * Which Checkout refusals mean "this agency already has a subscription" — 2026-10-01.
 *
 * `POST /agencies/{id}/billing/checkout` answers 409 under THREE types. Two of
 * them say the agency has a subscription, and Settings is where it is dealt
 * with; the third says another checkout holds the agency for a moment and to
 * try again. The panel and the scan-limit notice both hand a person on to
 * billing for the first two and must not for the third, so the TYPE decides —
 * the status cannot, because all three are 409.
 */

import { describe, it, expect } from 'vitest';
import type { ProblemDetail } from '@avp/shared-types';
import { isSubscriptionRefusal } from './subscriptionRefusal';

const problem = (type: string, status = 409): ProblemDetail => ({
  type,
  title: 'Problem',
  status,
  detail: 'A sentence.',
  instance: '/api/v1/agencies/agcy_01TEST/billing/checkout',
});

describe('the two subscription refusals', () => {
  it.each([
    ['/problems/subscription-already-active'],
    ['/problems/subscription-needs-attention'],
  ])('%s is one', (type) => {
    expect(isSubscriptionRefusal(problem(type))).toBe(true);
  });

  it('is recognised however the type is spelled, absolute or relative', () => {
    // As `scanLimitProblem` reads its own type: by how it ends.
    expect(
      isSubscriptionRefusal(problem('https://api.example/problems/subscription-needs-attention')),
    ).toBe(true);
  });

  it('matches a whole path segment, not the tail of a longer one', () => {
    expect(isSubscriptionRefusal(problem('/problems/not-subscription-already-active'))).toBe(false);
  });
});

describe('everything else is not', () => {
  it('checkout-in-progress is a 409 too, and says to try again in a moment', () => {
    expect(problem('/problems/checkout-in-progress').status).toBe(409);
    expect(isSubscriptionRefusal(problem('/problems/checkout-in-progress'))).toBe(false);
  });

  it.each([
    ['/problems/conflict', 409],
    ['/problems/billing-not-configured', 503],
    ['/problems/scan-limit-reached', 402],
    ['/problems/forbidden', 403],
    ['/problems/unknown', 500],
  ])('%s (%i) is not', (type, status) => {
    expect(isSubscriptionRefusal(problem(type, status))).toBe(false);
  });
});

/**
 * The remaining-count label and the 402 narrowing — 2026-09-28.
 *
 * The numbers are the API's; these tests assert only how they are said.
 */

import { describe, it, expect } from 'vitest';
import {
  ASK_AN_OWNER_TO_MANAGE_BILLING,
  allowanceLabel,
  canChangeBilling,
  canCheckout,
  freeScanUsed,
  scanLimitProblem,
} from './allowance';
import {
  activeAtCap,
  activeSubscription,
  canceledSubscription,
  freeScanNeedsGoogle,
  freeScanUsed as freeScanUsedBilling,
  noSubscription,
  pastDueSubscription,
  ungated,
} from '@/lib/settings/__fixtures__/billing';

describe('the remaining-count label', () => {
  it('free with the scan unused', () => {
    expect(allowanceLabel(noSubscription)).toBe('1 free scan');
  });

  it('free with the scan used', () => {
    expect(allowanceLabel(freeScanUsedBilling)).toBe('Free scan used');
  });

  it('a lapsed subscription is free', () => {
    expect(allowanceLabel(canceledSubscription)).toBe('Free scan used');
    expect(allowanceLabel(pastDueSubscription)).toBe('Free scan used');
  });

  it('paid, mid-month', () => {
    expect(allowanceLabel(activeSubscription)).toBe('12 of 40 scans this month');
  });

  it('paid at the cap says when it resets and offers no upgrade', () => {
    const label = allowanceLabel(activeAtCap)!;
    expect(label).toBe('40 of 40 scans this month · resets 1 Oct 2026');
    expect(label.toLowerCase()).not.toContain('upgrade');
  });

  it('no gate, no label', () => {
    expect(allowanceLabel(ungated)).toBeNull();
  });

  it('free, for a user the API says needs Google — 2026-09-29', () => {
    expect(allowanceLabel(freeScanNeedsGoogle)).toBe('Free scan needs Google sign-in');
  });

  it('reads the flag before the count, as the API refuses before counting', () => {
    // Pressing the button meets SCAN_LIMIT_VERIFY_REQUIRED whatever the count
    // says, so that is what the label says too.
    expect(allowanceLabel({ ...freeScanNeedsGoogle, scansUsedThisPeriod: 1 })).toBe(
      'Free scan needs Google sign-in',
    );
  });

  it('takes the answer from the API and works nothing out itself', () => {
    // Same agency, same count; only the flag differs — as it does between two
    // seats of one agency, because it is about the requesting user.
    expect(allowanceLabel({ ...freeScanNeedsGoogle, freeScanRequiresGoogle: false })).toBe(
      '1 free scan',
    );
  });
});

describe('narrowing a 402', () => {
  const base = { title: 'Scan limit reached', status: 402, instance: '/api/v1/x' };

  it('recognises the free and monthly codes', () => {
    const free = scanLimitProblem({
      ...base, type: '/problems/scan-limit-reached', detail: 'used',
      code: 'SCAN_LIMIT_FREE_USED', scansUsed: 1, scanLimit: 1, periodResetsAt: null,
    });
    expect(free?.code).toBe('SCAN_LIMIT_FREE_USED');
    const monthly = scanLimitProblem({
      ...base, type: '/problems/scan-limit-reached', detail: 'cap',
      code: 'SCAN_LIMIT_MONTHLY', scansUsed: 40, scanLimit: 40,
      periodResetsAt: '2026-10-01T00:00:00Z',
    });
    expect(monthly).toEqual({
      code: 'SCAN_LIMIT_MONTHLY', detail: 'cap', scansUsed: 40, scanLimit: 40,
      periodResetsAt: '2026-10-01T00:00:00Z',
    });
  });

  it('recognises the verify code — 2026-09-29', () => {
    const verify = scanLimitProblem({
      ...base, type: '/problems/scan-limit-reached', detail: 'google',
      code: 'SCAN_LIMIT_VERIFY_REQUIRED', scansUsed: 0, scanLimit: 1, periodResetsAt: null,
    });
    expect(verify).toEqual({
      code: 'SCAN_LIMIT_VERIFY_REQUIRED', detail: 'google', scansUsed: 0, scanLimit: 1,
      periodResetsAt: null,
    });
  });

  it('is not fooled by another 402 or another problem type', () => {
    expect(scanLimitProblem({ ...base, type: '/problems/other', detail: 'x' })).toBeNull();
    expect(scanLimitProblem({ ...base, status: 409, type: '/problems/scan-limit-reached', detail: 'x', code: 'SCAN_LIMIT_MONTHLY' })).toBeNull();
    expect(scanLimitProblem({ ...base, type: '/problems/scan-limit-reached', detail: 'x', code: 'SOMETHING_ELSE' })).toBeNull();
  });
});

describe('freeScanUsed, the one reading of "the free scan is gone"', () => {
  it('is true only for a non-subscriber at or over the limit', () => {
    expect(freeScanUsed(freeScanUsedBilling)).toBe(true);
    expect(freeScanUsed(pastDueSubscription)).toBe(true);
    expect(freeScanUsed(noSubscription)).toBe(false);
    expect(freeScanUsed(activeAtCap)).toBe(false);
    expect(freeScanUsed(activeSubscription)).toBe(false);
    expect(freeScanUsed(ungated)).toBe(false);
  });

  it('agrees with the label wherever the label speaks of the free scan', () => {
    for (const b of [noSubscription, freeScanUsedBilling, canceledSubscription, pastDueSubscription]) {
      expect(allowanceLabel(b) === 'Free scan used').toBe(freeScanUsed(b));
    }
  });
});

describe('canCheckout, the statuses Checkout accepts', () => {
  it('is no subscription, canceled and incomplete_expired, and nothing else', () => {
    expect(canCheckout(null)).toBe(true);
    expect(canCheckout('canceled')).toBe(true);
    expect(canCheckout('incomplete_expired')).toBe(true);
    for (const s of ['active', 'trialing', 'past_due', 'unpaid', 'paused', 'incomplete', 'never_seen']) {
      expect(canCheckout(s), s).toBe(false);
    }
  });
});

describe('canChangeBilling, who is offered Subscribe and Manage billing — launch checklist 3.130', () => {
  it('is the owner alone: an admin reads the status and a member does not even that', () => {
    expect(canChangeBilling('owner')).toBe(true);
    expect(canChangeBilling('admin')).toBe(false);
    expect(canChangeBilling('member')).toBe(false);
  });

  it('what an admin is told instead is the founder’s sentence, exactly', () => {
    expect(ASK_AN_OWNER_TO_MANAGE_BILLING).toBe('Ask an owner to manage billing.');
  });
});

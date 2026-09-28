/**
 * The remaining-count label and the 402 narrowing — 2026-09-28.
 *
 * The numbers are the API's; these tests assert only how they are said.
 */

import { describe, it, expect } from 'vitest';
import { allowanceLabel, scanLimitProblem } from './allowance';
import {
  activeAtCap,
  activeSubscription,
  canceledSubscription,
  freeScanUsed,
  noSubscription,
  pastDueSubscription,
  ungated,
} from '@/lib/settings/__fixtures__/billing';

describe('the remaining-count label', () => {
  it('free with the scan unused', () => {
    expect(allowanceLabel(noSubscription)).toBe('1 free scan');
  });

  it('free with the scan used', () => {
    expect(allowanceLabel(freeScanUsed)).toBe('Free scan used');
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

  it('is not fooled by another 402 or another problem type', () => {
    expect(scanLimitProblem({ ...base, type: '/problems/other', detail: 'x' })).toBeNull();
    expect(scanLimitProblem({ ...base, status: 409, type: '/problems/scan-limit-reached', detail: 'x', code: 'SCAN_LIMIT_MONTHLY' })).toBeNull();
    expect(scanLimitProblem({ ...base, type: '/problems/scan-limit-reached', detail: 'x', code: 'SOMETHING_ELSE' })).toBeNull();
  });
});

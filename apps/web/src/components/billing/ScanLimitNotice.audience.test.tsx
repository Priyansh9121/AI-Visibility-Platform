/**
 * Subscribe is hidden where Checkout cannot work — founder decision 4.
 *
 * Checkout is open to owner and admin only (`RequireAdmin`), and is refused
 * with a 409 unless the agency has no subscription or its last one is over
 * (`canceled`, `incomplete_expired`). The notice uses the billing data and the
 * role the page already loaded; with neither, it is what it always was.
 */
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { BillingStatus, UserRole } from '@avp/shared-types';
import { ScanLimitNotice } from './ScanLimitNotice';
import { BILLING_SETTINGS_HREF } from '@/lib/settings/sections';

const FREE = {
  code: 'SCAN_LIMIT_FREE_USED' as const, detail: 'used', scansUsed: 1, scanLimit: 1,
  periodResetsAt: null,
};
const MONTHLY = {
  code: 'SCAN_LIMIT_MONTHLY' as const, detail: 'cap', scansUsed: 40, scanLimit: 40,
  periodResetsAt: '2026-10-01T00:00:00Z',
};
const VERIFY = {
  code: 'SCAN_LIMIT_VERIFY_REQUIRED' as const, detail: 'google', scansUsed: 0, scanLimit: 1,
  periodResetsAt: null,
};

const billing = (subscriptionStatus: string | null): BillingStatus => ({
  subscriptionStatus,
  isActive: false,
  currentPeriodEnd: null,
  hasBillingAccount: subscriptionStatus !== null,
  scansUsedThisPeriod: 1,
  scanLimit: 1,
  periodResetsAt: null,
  freeScanRequiresGoogle: false,
  cancelAtPeriodEnd: false,
});

function render(
  problem: typeof FREE | typeof MONTHLY | typeof VERIFY,
  billingStatus?: BillingStatus | null,
  role?: UserRole | null,
): string {
  return renderToStaticMarkup(
    <ScanLimitNotice problem={problem} agencyId="agcy_01TEST" billing={billingStatus} role={role} />,
  );
}

const NEEDS_ATTENTION_FREE =
  "Your free scan is used. This agency&#x27;s subscription needs attention: fix it from Manage billing in Settings to keep scanning.";
const MEMBER_FREE =
  "Your free scan is used. Ask your agency&#x27;s owner or an admin to subscribe to keep scanning. The reports you already have stay open.";

describe('an owner or admin, where Checkout can work: as it always was', () => {
  it.each([
    ['no subscription', null],
    ['canceled', 'canceled'],
    ['incomplete_expired', 'incomplete_expired'],
  ])('%s keeps Subscribe on the free-used notice', (_what, status) => {
    for (const role of ['owner', 'admin'] as const) {
      const html = render(FREE, billing(status), role);
      expect(html).toContain('>Subscribe<');
      expect(html).toContain('Subscribe to keep scanning');
      expect(html).not.toContain(BILLING_SETTINGS_HREF);
    }
  });

  it('billing or role not known is also as it was', () => {
    for (const html of [render(FREE), render(FREE, null, 'owner'), render(FREE, null, null)]) {
      expect(html).toContain('>Subscribe<');
    }
  });

  it('keeps Subscribe beside Continue with Google on the verify notice', () => {
    const html = render(VERIFY, billing(null), 'owner');
    expect(html).toContain('>Subscribe<');
    expect(html).toContain('or subscribe.');
    expect(html).toContain('Continue with Google');
  });
});

describe('an owner or admin whose subscription needs attention', () => {
  it.each(['past_due', 'unpaid', 'paused', 'incomplete', 'active', 'some_new_status'])(
    '%s: no Subscribe, a sentence that points at Billing, and the Settings link',
    (status) => {
      const html = render(FREE, billing(status), 'owner');
      expect(html).not.toContain('>Subscribe<');
      expect(html).toContain(NEEDS_ATTENTION_FREE);
      expect(html).toContain(`href="${BILLING_SETTINGS_HREF}"`);
      expect(html).toContain('>Open Settings<');
    },
  );

  it('an admin is treated as an owner is', () => {
    const html = render(FREE, billing('past_due'), 'admin');
    expect(html).not.toContain('>Subscribe<');
    expect(html).toContain('>Open Settings<');
  });

  it('verify: keeps Continue with Google, drops Subscribe, and points at Billing', () => {
    const html = render(VERIFY, billing('past_due'), 'owner');
    expect(html).toContain('Continue with Google');
    expect(html).not.toContain('>Subscribe<');
    expect(html).toContain('Your free scan needs a Google sign-in, so we know one person gets one.');
    expect(html).toContain(
      "or fix this agency&#x27;s subscription from Manage billing in Settings. A different Google account would start a new agency instead.",
    );
    expect(html).not.toContain('or subscribe');
    expect(html).toContain(`href="${BILLING_SETTINGS_HREF}"`);
  });
});

describe('a member', () => {
  it('free used: asks for the owner, no Subscribe and no Settings link', () => {
    const html = render(FREE, null, 'member');
    expect(html).toContain(MEMBER_FREE);
    expect(html).not.toContain('>Subscribe<');
    expect(html).not.toContain(BILLING_SETTINGS_HREF);
    expect(html).not.toContain('Open Settings');
  });

  it('verify: keeps Continue with Google, and asks for the owner instead of subscribing', () => {
    const html = render(VERIFY, null, 'member');
    expect(html).toContain('Continue with Google');
    expect(html).toContain("or ask your agency&#x27;s owner or an admin to subscribe.");
    expect(html).not.toContain('>Subscribe<');
    expect(html).not.toContain(BILLING_SETTINGS_HREF);
  });

  it('is a member whatever billing says', () => {
    const html = render(FREE, billing('past_due'), 'member');
    expect(html).toContain(MEMBER_FREE);
    expect(html).not.toContain('Open Settings');
  });
});

describe('the monthly limit is unchanged for everyone', () => {
  it.each([
    [null, null],
    [billing('past_due'), 'owner' as const],
    [null, 'member' as const],
  ])('says when it resets and offers nothing', (b, role) => {
    const html = render(MONTHLY, b, role);
    expect(html).toContain('This agency has used its 40 scans for the month.');
    expect(html).not.toContain('>Subscribe<');
    expect(html).not.toContain('Open Settings');
  });
});

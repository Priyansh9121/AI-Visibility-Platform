/**
 * Subscribe is hidden where Checkout cannot work — founder decision 4.
 *
 * Checkout is open to the owner only (`RequireOwner`, launch checklist 3.130: an
 * admin gets a 403 as a member does), and is refused with a 409 unless the
 * agency has no subscription or its last one is over (`canceled`,
 * `incomplete_expired`). The notice uses the billing data and the role the page
 * already loaded; with neither, it is what it always was.
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
  trialEndsAt: null,
  trialEligible: false,
  trialRequiresGoogle: false,
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
// The founder's sentence (launch checklist 3.130), written out here: editing the notice cannot
// quietly edit it.
const ASK_AN_OWNER = 'Ask an owner to manage billing.';
const ADMIN_FREE = 'Your free scan is used. The reports you already have stay open.';
const MEMBER_FREE =
  "Your free scan is used. Ask your agency&#x27;s owner to subscribe to keep scanning. The reports you already have stay open.";

describe('an owner, where Checkout can work: as it always was', () => {
  it.each([
    ['no subscription', null],
    ['canceled', 'canceled'],
    ['incomplete_expired', 'incomplete_expired'],
  ])('%s keeps Subscribe on the free-used notice', (_what, status) => {
    const html = render(FREE, billing(status), 'owner');
    expect(html).toContain('>Subscribe<');
    expect(html).toContain('Subscribe to keep scanning');
    expect(html).not.toContain(BILLING_SETTINGS_HREF);
    expect(html).not.toContain(ASK_AN_OWNER);
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

describe('an owner whose subscription needs attention', () => {
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

/**
 * An admin reads billing and does not change it (launch checklist 3.130): Checkout is refused to
 * them by the API, so no state offers Subscribe, and where it would be there is one plain line.
 * No link to Settings either: its Billing section says the same.
 */
describe('an admin — never offered Subscribe, told to ask an owner', () => {
  const STATES: Array<[string, BillingStatus | null]> = [
    ['billing not loaded', null],
    ['no subscription', billing(null)],
    ['canceled', billing('canceled')],
    ['incomplete_expired', billing('incomplete_expired')],
    ['active', billing('active')],
    ['past_due', billing('past_due')],
  ];

  it.each(STATES)('free used, %s: the sentence, the line, no Subscribe and no link', (_what, b) => {
    const html = render(FREE, b, 'admin');
    expect(html).toContain(ADMIN_FREE);
    expect(html.split(ASK_AN_OWNER)).toHaveLength(2);
    expect(html).not.toContain('Subscribe');
    expect(html).not.toContain('<button');
    expect(html).not.toContain(BILLING_SETTINGS_HREF);
    expect(html).not.toContain('Open Settings');
    expect(html).not.toContain('owner or an admin');
  });

  it.each(STATES)('verify, %s: keeps Continue with Google, drops the offer to subscribe', (_what, b) => {
    const html = render(VERIFY, b, 'admin');
    expect(html).toContain('Continue with Google');
    expect(html).toContain(
      'Continue with Google using the same address you sign in with here. A different Google account would start a new agency instead.',
    );
    expect(html.split(ASK_AN_OWNER)).toHaveLength(2);
    expect(html).not.toContain('Subscribe');
    expect(html).not.toContain('subscribe');
    expect(html).not.toContain('<button');
    expect(html).not.toContain(BILLING_SETTINGS_HREF);
  });

  it('says it as a plain line in the secondary text style, not an alert', () => {
    for (const html of [render(FREE, null, 'admin'), render(VERIFY, null, 'admin')]) {
      expect(html).toMatch(
        /<p class="[^"]*\btext-text-secondary\b[^"]*">Ask an owner to manage billing\.<\/p>/,
      );
      expect(html).not.toContain('role="alert"');
    }
  });

  it('the owner and the member are never told to ask an owner', () => {
    for (const role of ['owner', 'member'] as const) {
      for (const problem of [FREE, VERIFY, MONTHLY]) {
        expect(render(problem, billing('canceled'), role)).not.toContain(ASK_AN_OWNER);
        expect(render(problem, billing('past_due'), role)).not.toContain(ASK_AN_OWNER);
      }
    }
  });
});

describe('a member', () => {
  it('free used: asks for the owner, no Subscribe and no Settings link', () => {
    const html = render(FREE, null, 'member');
    expect(html).toContain(MEMBER_FREE);
    expect(html).not.toContain('or an admin');
    expect(html).not.toContain('>Subscribe<');
    expect(html).not.toContain(BILLING_SETTINGS_HREF);
    expect(html).not.toContain('Open Settings');
  });

  it('verify: keeps Continue with Google, and asks for the owner instead of subscribing', () => {
    const html = render(VERIFY, null, 'member');
    expect(html).toContain('Continue with Google');
    expect(html).toContain("or ask your agency&#x27;s owner to subscribe.");
    // An admin cannot subscribe (launch checklist 3.130), so the sentence no longer sends anyone to one.
    expect(html).not.toContain('or an admin');
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
    [billing('past_due'), 'admin' as const],
  ])('says when it resets and offers nothing', (b, role) => {
    const html = render(MONTHLY, b, role);
    expect(html).toContain('This agency has used its 40 scans for the month.');
    expect(html).not.toContain('>Subscribe<');
    expect(html).not.toContain('Open Settings');
    // No control was taken away, so there is no line in its place.
    expect(html).not.toContain(ASK_AN_OWNER);
  });
});

/**
 * A 402 is a subscribe prompt, not an error — 2026-09-28.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ScanLimitNotice } from './ScanLimitNotice';
import { GOOGLE_SIGN_IN_URL } from '@/lib/api';

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

function render(problem: typeof FREE | typeof MONTHLY | typeof VERIFY): string {
  return renderToStaticMarkup(<ScanLimitNotice problem={problem} agencyId="agcy_01TEST" />);
}

describe('the free scan is used', () => {
  it('says so plainly and offers Subscribe, not an error', () => {
    const html = render(FREE);
    expect(html).toContain('Your free scan is used');
    expect(html).toContain('>Subscribe<');
    expect(html).toContain('stay open, shareable and exportable');
    expect(html).not.toContain('could not be started');
    expect(html).not.toContain('role="alert"');
  });
});

describe('the monthly cap is reached', () => {
  it('says when it resets and offers no upgrade and no subscribe', () => {
    const html = render(MONTHLY);
    expect(html).toContain('used its 40 scans for the month');
    expect(html).toContain('resets on 1 Oct 2026');
    expect(html).not.toContain('>Subscribe<');
    expect(html.toLowerCase()).not.toContain('upgrade');
  });
});

describe('the free scan needs a Google sign-in — 2026-09-29', () => {
  it('says why in one sentence, and is not an error', () => {
    const html = render(VERIFY);
    expect(html).toContain(
      'Your free scan needs a Google sign-in, so we know one person gets one. Continue with Google using this address, or subscribe.',
    );
    expect(html).not.toContain('could not be started');
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain('Your free scan is used');
  });

  it('offers Continue with Google, the sign-in page’s own entry point', () => {
    const html = render(VERIFY);
    // An anchor to the API, which redirects to Google — the same target the
    // sign-in panel's button has, not a second flow.
    expect(html).toContain(`href="${GOOGLE_SIGN_IN_URL}"`);
    expect(html).toContain('Continue with Google');
    expect(html).toContain('avp-google-btn');
  });

  it('offers Subscribe too, into the same Checkout, and Google comes first', () => {
    const html = render(VERIFY);
    expect(html).toContain('>Subscribe<');
    expect(html.indexOf('Continue with Google')).toBeLessThan(html.indexOf('>Subscribe<'));
  });
});

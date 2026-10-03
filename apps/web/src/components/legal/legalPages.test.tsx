/**
 * The public privacy and terms pages — 2026-09-29.
 *
 * What the brief asked these tests to hold: both routes render signed out,
 * with no session call; the prices and numbers come from the constants; no
 * `TODO(founder)` ever renders; the landing page links both. Plus the parts
 * of the policy most likely to drift from the code, pinned to the code: every
 * duration the privacy page states, and the scan counts the terms state, are
 * read out of the API's own source and compared. The provider list has its
 * own file, `providers.test.ts`.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createElement, type JSX } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { LandingView } from '@/components/marketing/LandingView';
import {
  PLAN_FREE_SCANS,
  PLAN_PRICE_USD,
  PLAN_SCANS_PER_MONTH,
  PLAN_SEATS,
} from '@/components/marketing/PricingCard';
import { OPERATOR } from '@/lib/legal/operator';
import { RETENTION } from '@/lib/legal/retention';
import { PrivacyPolicy } from './PrivacyPolicy';
import { TermsOfService } from './TermsOfService';

const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));
const read = (path: string): string => readFileSync(join(ROOT, path), 'utf8');

/** Markup, and the text a reader sees — tags and React's text-node separators gone. */
function page(component: () => JSX.Element): { html: string; text: string } {
  const html = renderToStaticMarkup(createElement(component));
  const text = html
    .replace(/<!--.*?-->/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ');
  return { html, text };
}

const PRIVACY = page(PrivacyPolicy);
const TERMS = page(TermsOfService);

/** The integer an API setting is written as, e.g. `60 * 60 * 24 * 14` or `3600`. */
function apiInt(path: string, pattern: RegExp): number {
  const expr = pattern.exec(read(path))?.[1];
  expect(expr, `${pattern} not found in ${path}`).toBeDefined();
  return expr!
    .split('*')
    .map((part) => Number(part.replace(/_/g, '').trim()))
    .reduce((a, b) => a * b, 1);
}

describe('both pages render signed out, from the landing page’s own parts', () => {
  it.each([
    ['privacy', PRIVACY, 'How this service handles personal information.'],
    ['terms', TERMS, 'The terms of using this service.'],
  ] as const)('%s: one h1, the shared header and the footer', (_name, rendered, title) => {
    expect(rendered.html.match(/<h1[\s>]/g)).toHaveLength(1);
    expect(rendered.html).toContain(`<h1 class="avp-section__heading">${title}</h1>`);
    // The landing page's header: wordmark and both forms — but not its section
    // nav, whose anchors are on another page — and the wordmark links home.
    expect(rendered.html).toContain('href="/"');
    expect(rendered.text).toContain('Log in');
    expect(rendered.text).toContain('Get started free');
    expect(rendered.html).not.toContain('Sections of this page');
    // The footer, linking both pages.
    expect(rendered.html).toContain('href="/privacy"');
    expect(rendered.html).toContain('href="/terms"');
    expect(rendered.text).toContain('Last updated 29 Sep 2026.');
  });

  it('no module either page is built from can reach the session API', () => {
    // The pages never call `api.me` — they cannot: nothing they are made of
    // imports the API client. (Mounted and watched in `app/legalRoutes.test.tsx`.)
    for (const file of [
      'apps/web/src/app/privacy/page.tsx',
      'apps/web/src/app/terms/page.tsx',
      'apps/web/src/components/legal/LegalPage.tsx',
      'apps/web/src/components/legal/PrivacyPolicy.tsx',
      'apps/web/src/components/legal/TermsOfService.tsx',
      'apps/web/src/components/marketing/MarketingHeader.tsx',
      'apps/web/src/components/marketing/MarketingFooter.tsx',
      'apps/web/src/lib/legal/operator.ts',
      'apps/web/src/lib/legal/retention.ts',
      'apps/web/src/lib/auth/frontDoor.ts',
    ]) {
      expect(read(file), file).not.toMatch(/from '@\/lib\/api'/);
    }
  });
});

describe('no TODO reaches either page', () => {
  it.each([
    ['privacy', PRIVACY],
    ['terms', TERMS],
  ] as const)('%s', (_name, rendered) => {
    // A founder-supplied value left as a placeholder must fail here, not ship.
    expect(rendered.html).not.toMatch(/TODO/i);
    expect(rendered.html).not.toMatch(/founder\)/i);
  });
});

describe('who runs it', () => {
  it('the ABN passes the ATO checksum, so a typo cannot go live', () => {
    const digits = OPERATOR.abn.replace(/\s/g, '').split('').map(Number);
    expect(digits).toHaveLength(11);
    digits[0]! -= 1;
    const weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19];
    const sum = digits.reduce((acc, d, i) => acc + d * weights[i]!, 0);
    expect(sum % 89).toBe(0);
  });

  it.each([
    ['privacy', PRIVACY],
    ['terms', TERMS],
  ] as const)('%s names the operator, the ABN and a working address', (_name, rendered) => {
    expect(rendered.text).toContain(`run by ${OPERATOR.name}, ${OPERATOR.trading} in ${OPERATOR.city}`);
    expect(rendered.text).toContain(`ABN ${OPERATOR.abn}`);
    expect(rendered.html).toContain(`href="mailto:${OPERATOR.email}"`);
  });
});

describe('the numbers come from the constants, and the constants from the API', () => {
  it('the terms state the plan in the constants’ numbers', () => {
    expect(TERMS.text).toContain(`$${PLAN_PRICE_USD} a month, billed monthly in US dollars`);
    // Literally as well as through the constant, so a repricing has to change this line
    // on purpose (2026-10-04: $150, the founder's decision).
    expect(TERMS.text).toContain('$150 a month, billed monthly in US dollars');
    expect(TERMS.text).not.toContain('$160');
    expect(TERMS.text).toContain(`${PLAN_SEATS} seats and ${PLAN_SCANS_PER_MONTH} scans a month`);
    expect(PLAN_FREE_SCANS).toBe(1);
    expect(TERMS.text).toContain('your agency’s first scan is free with a Google sign-in');
  });

  it('the terms page types no plan number by hand', () => {
    // Any "150", "40" or "3 seats" in the component's source is a second
    // copy of a constant, which is how a price comes to disagree with itself.
    // ("160" went with the 2026-10-04 repricing, and stays refused: a hand-typed
    // old price would be the stale copy this test exists to catch.)
    const source = read('apps/web/src/components/legal/TermsOfService.tsx');
    expect(source).not.toMatch(/\b150\b/);
    expect(source).not.toMatch(/\b160\b/);
    expect(source).not.toMatch(/\b40\b/);
    expect(source).not.toMatch(/\b3 seats\b/);
  });

  it('the web’s scan counts are the ones the API enforces', () => {
    const allowance = 'apps/api/src/avp_api/services/scan_allowance.py';
    expect(PLAN_SCANS_PER_MONTH).toBe(apiInt(allowance, /^PAID_SCANS_PER_MONTH = ([\d_]+)/m));
    expect(PLAN_FREE_SCANS).toBe(apiInt(allowance, /^FREE_SCAN_ALLOWANCE = ([\d_]+)/m));
  });

  it('every duration the privacy page states is the API’s setting', () => {
    const config = 'apps/api/src/avp_api/config.py';
    const seconds = (name: string) => apiInt(config, new RegExp(`${name}: int = ([\\d_ *]+)`));
    expect(RETENTION.sessionDays * 86_400).toBe(seconds('session_ttl_seconds'));
    expect(RETENTION.resetLinkHours * 3_600).toBe(seconds('password_reset_ttl_seconds'));
    expect(RETENTION.invitationDays * 86_400).toBe(seconds('invitation_ttl_seconds'));
    expect(RETENTION.googleSignUpMinutes * 60).toBe(seconds('google_sign_in_ttl_seconds'));
    expect(RETENTION.shareLinkDays).toBe(
      apiInt('apps/api/src/avp_api/services/share.py', /^SHARE_TTL = timedelta\(days=(\d+)\)/m),
    );
    expect(RETENTION.crawlPages).toBe(
      apiInt('apps/api/src/avp_api/services/intake.py', /max_pages: int = (\d+)/),
    );
  });

  it('and the privacy page states them', () => {
    expect(PRIVACY.text).toContain(`lasts ${RETENTION.sessionDays} days or until you sign out`);
    expect(PRIVACY.text).toContain(`Password-reset links expire after ${RETENTION.resetLinkHours} hour,`);
    expect(PRIVACY.text).toContain(`invitations after ${RETENTION.invitationDays} days`);
    expect(PRIVACY.text).toContain(`not finished after ${RETENTION.googleSignUpMinutes} minutes`);
    expect(PRIVACY.text).toContain(`expires after ${RETENTION.shareLinkDays} days`);
    expect(PRIVACY.text).toContain(`up to ${RETENTION.crawlPages} public pages`);
  });
});

describe('the privacy page says what the code does', () => {
  it('cites the facts-only rule as enforced, not promised', () => {
    expect(PRIVACY.text).toContain('The text of AI answers, and the copy of your clients’ web pages');
    expect(PRIVACY.text).toContain('This is enforced, not promised');
  });

  it('says there is no self-serve deletion, and how to ask for it', () => {
    expect(PRIVACY.text).toContain('There is no way to delete them yourself yet.');
    expect(PRIVACY.text).toMatch(/To have your account, or your agency and everything in it, deleted, email/);
  });

  it('names both cookies and says there are no trackers', () => {
    expect(PRIVACY.text).toContain('__Host-avp_session keeps you signed in');
    expect(PRIVACY.text).toContain('__Host-avp_google_flow is set only while a Google sign-in is in progress');
    expect(PRIVACY.text).toContain('lasts at most 11 minutes');
    expect(PRIVACY.text).toContain('There are no analytics, advertising or tracking scripts');
  });

  it('is written under Australian law, and borrows no other jurisdiction’s boilerplate', () => {
    expect(PRIVACY.text).toContain('Australian Privacy Principles in the Privacy Act 1988 (Cth)');
    expect(PRIVACY.html).toContain('href="https://www.oaic.gov.au"');
    expect(TERMS.text).toContain('Australian law applies');
    expect(TERMS.text).toContain('Australian Consumer Law');
    for (const { html } of [PRIVACY, TERMS]) {
      expect(html).not.toMatch(/GDPR|CCPA|California|European/i);
    }
  });
});

describe('the landing page links both', () => {
  it('from a footer, below everything else', () => {
    const html = renderToStaticMarkup(createElement(LandingView, { animate: false }));
    expect(html).toContain('<footer');
    expect(html).toContain('href="/privacy"');
    expect(html).toContain('href="/terms"');
    expect(html.indexOf('<footer')).toBeGreaterThan(html.indexOf('</main>'));
  });
});

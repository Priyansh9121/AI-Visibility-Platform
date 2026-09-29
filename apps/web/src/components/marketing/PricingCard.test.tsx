/**
 * The pricing card — Epic 9.15.
 *
 * Static-render assertions, the pattern LandingView.test.tsx and
 * SettingsView.test.tsx already use. There is no DOM in this suite, so what is
 * asserted is which control is rendered in which state, not what clicking it
 * does; the click destinations are covered by the live browser pass.
 *
 * Both states are tested because both are live: the landing page renders the
 * signed-out card, and Settings renders the signed-in one when an agency has no
 * active subscription. Neither branch exists only for this file.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { PricingCard, PLAN_PRICE_USD, PLAN_SEATS } from './PricingCard';

const render = (props: Parameters<typeof PricingCard>[0] = {}) =>
  renderToStaticMarkup(<PricingCard {...props} />);

describe('the price itself', () => {
  it('is $160 a month, stated as a number and a period', () => {
    const out = render();
    expect(out).toContain('$160');
    expect(out).toContain('per month');
  });

  it('exports the number rather than burying it in copy', () => {
    // Two places on the landing page quote this. A card that hard-coded the
    // string would let the hero and the card disagree, which is the one thing
    // a published price may not do.
    expect(PLAN_PRICE_USD).toBe(160);
    expect(render()).toContain(`$${PLAN_PRICE_USD}`);
  });

  it('names the seat count and ties it to the seat limit that already exists', () => {
    const out = render();
    expect(PLAN_SEATS).toBe(3);
    expect(out).toContain('3 seats per agency.');
    expect(out).not.toContain('the same seat limit every agency already gets');
  });

  it('promises one plan and no tier above it', () => {
    const out = render();
    expect(out).toContain('One plan');
    expect(out).toContain('there is no tier above this one');
  });
});

describe('signed out', () => {
  it('offers sign-up, because you cannot subscribe an agency that does not exist', () => {
    const out = render({ signedIn: false });
    expect(out).toContain('Get started');
    expect(out).not.toContain('>Subscribe<');
    expect(out).not.toContain('Opening checkout');
  });
});

describe('signed in', () => {
  it('offers checkout instead of sign-up', () => {
    const out = render({ signedIn: true });
    expect(out).toContain('Subscribe');
    expect(out).not.toContain('>Get started<');
  });

  it('says the button is working rather than going quiet', () => {
    const out = render({ signedIn: true, busy: true });
    expect(out).toContain('Opening checkout…');
    expect(out).toContain('disabled');
  });

  it('is not disabled when idle', () => {
    expect(render({ signedIn: true })).not.toContain('disabled');
  });

  it('renders a checkout failure as a sentence, in an alert', () => {
    const out = render({
      signedIn: true,
      error: 'Billing is not configured on this server.',
    });
    expect(out).toContain('Billing is not configured on this server.');
    expect(out).toContain('role="alert"');
  });

  it('shows no error region when there is no error', () => {
    expect(render({ signedIn: true })).not.toContain('role="alert"');
  });
});

describe('what the card is not allowed to say', () => {
  it('states the cap and what is never limited — 2026-09-28', () => {
    const out = render();
    // The plan gates STARTING a scan: forty a month, one free for life. It
    // never gates reading. The card says both, and claims nothing unlimited.
    expect(out).toContain('40 scans a month');
    expect(out).toContain('Viewing, sharing and exporting reports is never limited');
    expect(out.toLowerCase()).not.toContain('unlimited');
    expect(out).not.toContain('not metered');
  });

  it('says what is free and what the plan is for', () => {
    const out = render();
    // The free scan needs a Google sign-in since 2026-09-29.
    expect(out).toContain('Signing up is free. Your first scan is free with a Google sign-in.');
    expect(out).not.toContain('includes one full scan and report');
    expect(out).toContain('Subscribe to keep scanning');
    expect(out).not.toContain('Nothing in the product is behind');
    expect(out.toLowerCase()).not.toContain('free trial');
    expect(out.toLowerCase()).not.toContain('upgrade to unlock');
  });

  it('carries no fabricated social proof, the same as the page around it', () => {
    const out = render().toLowerCase();
    for (const tell of ['most popular', 'best value', 'recommended', 'trusted by', 'customers say']) {
      expect(out, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('promises no date for anything it does not do', () => {
    const out = render().toLowerCase();
    for (const tell of ['coming soon', 'shortly', 'next release', 'roadmap']) {
      expect(out, `found "${tell}"`).not.toContain(tell);
    }
  });
});

describe('no ad hoc styling', () => {
  it('is built from the shared Card', () => {
    expect(render()).toContain('avp-card');
  });

  it('emits no arbitrary-value and no raw-palette class', () => {
    const out = render({ signedIn: true, error: 'x' });
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

describe('the price on the card is the constant and nothing else', () => {
  it('carries no figure other than PLAN_PRICE_USD, and no price in words', () => {
    for (const out of [render(), render({ signedIn: true })]) {
      const prices = out.match(/\$\d[\d,]*/g) ?? [];
      expect(new Set(prices)).toEqual(new Set([`$${PLAN_PRICE_USD}`]));
      expect(out).not.toContain('$29');
      expect(out.toLowerCase()).not.toContain('twenty-nine');
      expect(out.toLowerCase()).not.toMatch(/\b(nineteen|twenty|thirty|forty|fifty|sixty|hundred)\b[^.]*\bdollars/);
    }
  });
});

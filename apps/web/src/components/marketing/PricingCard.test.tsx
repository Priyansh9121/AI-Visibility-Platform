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
  it('is $29 a month, stated as a number and a period', () => {
    const out = render();
    expect(out).toContain('$29');
    expect(out).toContain('per month');
  });

  it('exports the number rather than burying it in copy', () => {
    // Two places on the landing page quote this. A card that hard-coded the
    // string would let the hero and the card disagree, which is the one thing
    // a published price may not do.
    expect(PLAN_PRICE_USD).toBe(29);
    expect(render()).toContain(`$${PLAN_PRICE_USD}`);
  });

  it('names the seat count and ties it to the seat limit that already exists', () => {
    const out = render();
    expect(PLAN_SEATS).toBe(3);
    expect(out).toContain('3 seats');
    // The brief's point: say it on the card rather than leaving it implicit.
    // And say the honest version — the plan is not granting seats the agency
    // would otherwise lack, because every agency already starts with three.
    expect(out).toContain('the same seat limit every agency already gets');
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
  it('does not claim unlimited scans, because scans are not metered either way', () => {
    const out = render();
    // north-star.md §5.2 argues metering the scan is the right model and §5.4
    // records that none of it is built. The absence of a cap is an absence,
    // not a feature, and selling it as one would be a promise nobody has
    // costed — §5.1's per-scan COGS has never been measured.
    //
    // Asserted by counting rather than by absence: the word appears exactly
    // once on this card, inside the sentence that REFUSES the claim. A blunt
    // `not.toContain('unlimited')` failed against honest copy, which is the
    // same way LandingView's `\brated\b` assertion first went wrong.
    expect(out.toLowerCase().match(/unlimited/g) ?? []).toHaveLength(1);
    expect(out).toContain('rather than a promise of unlimited use');
    expect(out).toContain('Scan volume is not metered today');
  });

  it('does not pretend the product is gated behind the plan', () => {
    const out = render();
    // Sign-up and onboarding stay free. A card implying otherwise would gate
    // the existing test accounts in the reader's mind if nowhere else.
    expect(out).toContain('Signing up is free and stays free');
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

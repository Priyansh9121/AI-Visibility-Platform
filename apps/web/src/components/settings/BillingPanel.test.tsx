/**
 * The Settings billing section — Epic 9.15.
 *
 * Static-render assertions, the pattern SeatsPanel.test.tsx and
 * SettingsView.test.tsx already use.
 *
 * The load-bearing group is the last one. This brief's single most consequential
 * instruction was that nothing gets gated — sign-up and onboarding stay exactly
 * as free as they are — and the way that goes wrong is not a paywall somebody
 * writes on purpose. It is a screen that renders an unsubscribed agency as a
 * degraded one: a warning tone, a lock, an "upgrade to continue". The tests
 * assert the absence of that vocabulary, because the copy is the only place it
 * could appear.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { BillingPanel } from './BillingPanel';
import {
  activeSubscription,
  canceledSubscription,
  noSubscription,
  pastDueSubscription,
  unknownStatusSubscription,
} from '@/lib/settings/__fixtures__/billing';

const render = (props: Partial<Parameters<typeof BillingPanel>[0]> = {}) =>
  renderToStaticMarkup(
    <BillingPanel agencyId="agcy_01TEST" billing={noSubscription} {...props} />,
  );

describe('no subscription — the state every agency is in today', () => {
  it('says so without saying anything is wrong', () => {
    const html = render();
    expect(html).toContain('No subscription');
    expect(html).toContain('Everything in the product works anyway');
  });

  it('offers the plan at the price the public page quoted', () => {
    const html = render();
    // The SAME component the landing page renders, in its signed-in state, so
    // the price someone was quoted on the way in is the price they see on the
    // way to paying it.
    expect(html).toContain('$29');
    expect(html).toContain('per month');
    expect(html).toContain('Subscribe');
  });

  it('tones it neutral, never as a warning', () => {
    const html = render();
    expect(html).toContain('avp-badge--neutral');
    expect(html).not.toContain('avp-badge--warn');
    expect(html).not.toContain('avp-badge--danger');
  });
});

describe('active', () => {
  it('states the status and when it renews', () => {
    const html = render({ billing: activeSubscription });
    expect(html).toContain('Active');
    expect(html).toContain('Renews 21 Sep 2026');
    expect(html).toContain('avp-badge--success');
  });

  it('stops selling to somebody who has already bought', () => {
    const html = render({ billing: activeSubscription });
    expect(html).not.toContain('>Subscribe<');
    expect(html).not.toContain('per month');
  });
});

describe('canceled', () => {
  it('says the subscription ended and the product did not change', () => {
    const html = render({ billing: canceledSubscription });
    expect(html).toContain('Canceled');
    expect(html).toContain('Nothing in the product has changed.');
  });

  it('shows no renewal date, because there is no next renewal', () => {
    const html = render({ billing: canceledSubscription });
    expect(html).not.toContain('Renews');
  });

  it('does not dress a cancellation up as a failure', () => {
    const html = render({ billing: canceledSubscription });
    expect(html).toContain('avp-badge--neutral');
    expect(html).not.toContain('avp-badge--danger');
  });
});

describe('past_due — the billing failure state, rendered honestly', () => {
  it('says the payment failed, in those words', () => {
    // north-star.md §5.4 row 4: "rendered as honestly as `partial` is today".
    // An agency whose payment failed must SEE that, not discover it as
    // features quietly not working.
    const html = render({ billing: pastDueSubscription });
    expect(html).toContain('Payment failed');
    expect(html).toContain('The last payment did not go through');
    expect(html).toContain('avp-badge--warn');
  });

  it('is honest that nothing has been switched off, because nothing has', () => {
    const html = render({ billing: pastDueSubscription });
    expect(html).toContain('Nothing has been switched off');
  });

  it('does not claim a renewal it is not going to make', () => {
    const html = render({ billing: pastDueSubscription });
    // The fixture carries a period end; `past_due` is not active, so the
    // renewal line must not render from it.
    expect(html).not.toContain('Renews');
  });
});

describe('a status this screen has no wording for', () => {
  it('shows the raw status rather than a blank', () => {
    // Stripe's vocabulary can grow. A status nobody recognises is exactly the
    // thing an operator needs to be able to read back to us.
    const html = render({ billing: unknownStatusSubscription });
    expect(html).toContain('some_future_status');
    expect(html).toContain('does not have wording for yet');
  });

  it('still refuses to imply anything was withdrawn', () => {
    const html = render({ billing: unknownStatusSubscription });
    expect(html).toContain('Nothing in the product has changed.');
  });
});

describe('forbidden', () => {
  it('renders the reason rather than an empty panel', () => {
    const html = render({
      billing: null,
      billingError: 'Only an owner or an admin can see and change billing.',
    });
    expect(html).toContain('Only an owner or an admin can see and change billing.');
    expect(html).not.toContain('Subscribe');
  });

  it('falls back to a sentence when no reason was supplied', () => {
    const html = render({ billing: null });
    expect(html).toContain('Billing is not available on this account.');
  });
});

describe('a checkout failure', () => {
  it('is shown as a sentence in an alert, and says nothing was charged', () => {
    const html = render({
      initialError: 'STRIPE_PRICE_ID is not configured. Add it to your .env.',
    });
    expect(html).toContain('STRIPE_PRICE_ID is not configured');
    expect(html).toContain('role="alert"');
  });
});

describe('nothing here is gated', () => {
  it('uses none of the vocabulary of a paywall, in any state', () => {
    const states = [
      noSubscription,
      canceledSubscription,
      pastDueSubscription,
      unknownStatusSubscription,
    ];
    for (const billing of states) {
      const html = render({ billing }).toLowerCase();
      // Word-bounded for the reason SettingsView.test.tsx records: a bare
      // substring check on 'locked' matches inside "unlocked".
      for (const tell of [
        'upgrade to unlock',
        'locked',
        'restricted',
        'trial has ended',
        'to continue using',
        'read-only',
      ]) {
        expect(
          html,
          `found "${tell}" for ${billing.subscriptionStatus}`,
        ).not.toMatch(new RegExp(`\\b${tell}\\b`));
      }
    }
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value and no raw-palette class', () => {
    const html = render({ billing: pastDueSubscription, initialError: 'x' });
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

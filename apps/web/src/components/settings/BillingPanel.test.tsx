// @vitest-environment jsdom

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
 *
 * The last group, "mounted", is not a static render (2026-10-01). It is the
 * pattern SeatsPanel.test.tsx's invite tests use: the real panel in jsdom, the
 * API module mocked at its edge. Whether Subscribe works, what a 409 leaves on
 * screen and whether a page restored from the back/forward cache is still busy
 * are all things a static render never receives.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import type { BillingStatus, CheckoutSession, PortalSession } from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';
import { PricingCard } from '@/components/marketing/PricingCard';
import {
  activeEnding,
  activeSubscription,
  canceledSubscription,
  noSubscription,
  pastDueSubscription,
  unknownStatusSubscription,
} from '@/lib/settings/__fixtures__/billing';

const startCheckout = vi.fn<(agencyId: string) => Promise<CheckoutSession>>();
const openBillingPortal = vi.fn<(agencyId: string) => Promise<PortalSession>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      startCheckout: (agencyId: string) => startCheckout(agencyId),
      openBillingPortal: (agencyId: string) => openBillingPortal(agencyId),
    },
  };
});

// Imported after the mock so the panel sees the mocked module.
const { BillingPanel } = await import('./BillingPanel');

const render = (props: Partial<Parameters<typeof BillingPanel>[0]> = {}) =>
  renderToStaticMarkup(
    <BillingPanel agencyId="agcy_01TEST" billing={noSubscription} {...props} />,
  );

// The states the shared fixtures do not carry, built from the ones they do.
// Stripe's own names, because the column stores them verbatim.
const trialing: BillingStatus = { ...activeSubscription, subscriptionStatus: 'trialing' };
const unpaid: BillingStatus = { ...pastDueSubscription, subscriptionStatus: 'unpaid' };
const paused: BillingStatus = { ...canceledSubscription, subscriptionStatus: 'paused' };
const incomplete: BillingStatus = { ...canceledSubscription, subscriptionStatus: 'incomplete' };
const incompleteExpired: BillingStatus = {
  ...canceledSubscription,
  subscriptionStatus: 'incomplete_expired',
};
/**
 * No subscription, but a Stripe customer exists: somebody opened Checkout once
 * and never finished it. The API creates the customer when it creates the
 * session, so this is a state a real agency can be in.
 */
const customerNoSubscription: BillingStatus = { ...noSubscription, hasBillingAccount: true };

describe('no subscription — the state every agency is in today', () => {
  it('says so without saying anything is wrong', () => {
    const html = render();
    expect(html).toContain('No subscription');
    // With a Google sign-in since 2026-09-29.
    expect(html).toContain('Your agency’s first scan is free with a Google sign-in');
    expect(html).toContain('stay open, shareable and exportable');
  });

  it('offers the plan at the price the public page quoted', () => {
    const html = render();
    // The SAME component the landing page renders, in its signed-in state, so
    // the price someone was quoted on the way in is the price they see on the
    // way to paying it.
    expect(html).toContain('$160');
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

describe('managing an existing subscription', () => {
  it('offers the portal once a Stripe customer exists', () => {
    const html = render({ billing: activeSubscription });
    expect(html).toContain('Manage billing');
    expect(html).toContain('We never see or store a card number');
  });

  it('offers it for a CANCELLED subscription too', () => {
    // Wider than `isActive` on purpose: a cancelled or failed subscription is
    // exactly when somebody needs their invoices and their card, and hiding
    // the door then would hide it in the only situation that makes it urgent.
    expect(render({ billing: canceledSubscription })).toContain('Manage billing');
  });

  it('offers it for a failed payment too', () => {
    expect(render({ billing: pastDueSubscription })).toContain('Manage billing');
  });

  it('does not offer it to an agency that has never subscribed', () => {
    // The portal for one would be an empty page with no card and no invoices —
    // a worse answer than the button not being there.
    expect(render({ billing: noSubscription })).not.toContain('Manage billing');
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
    expect(html).toContain('every report you already have stays open');
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
    expect(html).toContain('every report you already have stays open');
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
      // The states decision 4 (2026-10-01) put a Subscribe on, or took one off.
      incompleteExpired,
      unpaid,
      paused,
      incomplete,
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

  it('holds for the status-card-plus-plan layout a cancelled agency now gets', () => {
    const html = render({ billing: canceledSubscription, initialError: 'x' });
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

describe('cancelled, but paid to the end of the period — 2026-09-29', () => {
  // Stripe's portal cancels at the end of the billing period: the subscription
  // stays active, with cancelAtPeriodEnd, until then.
  it('says Ends, never Renews, and why', () => {
    const html = render({ billing: activeEnding });
    expect(html).toContain('Ends 21 Sep 2026');
    expect(html).not.toContain('Renews');
    expect(html).toContain('Cancelled — you keep full access until then, and it won’t renew.');
    expect(html).not.toContain('Your subscription is live.');
  });

  it('keeps Manage billing, so it can be resumed', () => {
    expect(render({ billing: activeEnding })).toContain('Manage billing');
  });

  it('an active plan that is not cancelling still renews, with no cancelled line', () => {
    const html = render({ billing: activeSubscription });
    expect(html).toContain('Renews 21 Sep 2026');
    expect(html).not.toContain('Ends ');
    expect(html).not.toContain('Cancelled —');
  });
});

describe('Subscribe is offered exactly where the API will start a checkout — 2026-10-01', () => {
  // Founder decision 4. The API starts a checkout for no subscription, `canceled`
  // and `incomplete_expired`, and answers every other status with a 409. A
  // Subscribe on a refused state is a button that can only fail; a missing one on
  // an allowed state is a cancelled agency with no way back in. Until this date
  // the panel offered it for "no subscription" alone.
  const planCard = renderToStaticMarkup(<PricingCard signedIn />);

  const OFFERED: Array<[string, BillingStatus, string, string]> = [
    ['canceled', canceledSubscription, 'Canceled', 'every report you already have stays open'],
    ['incomplete_expired', incompleteExpired, 'Expired', 'it has since expired'],
  ];

  it('the reference card is the one the no-subscription state renders', () => {
    // Guards the assertions below it: a `planCard` that matched nothing would
    // let every one of them pass.
    expect(render({ billing: noSubscription })).toContain(planCard);
  });

  it.each(OFFERED)(
    '%s keeps its status card and offers the plan beneath it',
    (_status, billing, label, sentence) => {
      const html = render({ billing });
      expect(html).toContain(label);
      expect(html).toContain(sentence);
      expect(html).toContain('>Subscribe<');
      // The status first, then the way back in.
      expect(html.indexOf(sentence)).toBeLessThan(html.indexOf('>Subscribe<'));
    },
  );

  it.each(OFFERED)('%s is offered the same plan card, so the same price', (_status, billing) => {
    expect(render({ billing })).toContain(planCard);
  });

  it.each(OFFERED)('%s keeps Manage billing, for the invoices', (_status, billing) => {
    expect(render({ billing })).toContain('Manage billing');
  });

  it.each(OFFERED)(
    '%s with no Stripe customer is still offered Subscribe, and no Manage billing',
    (_status, billing) => {
      const html = render({ billing: { ...billing, hasBillingAccount: false } });
      expect(html).toContain('>Subscribe<');
      expect(html).not.toContain('Manage billing');
    },
  );

  const REFUSED: Array<[string, BillingStatus]> = [
    ['active', activeSubscription],
    ['active and ending', activeEnding],
    ['trialing', trialing],
    ['past_due', pastDueSubscription],
    ['unpaid', unpaid],
    ['paused', paused],
    ['incomplete', incomplete],
    // Not one of the three the API starts a checkout for, so not offered one.
    ['a status with no wording yet', unknownStatusSubscription],
  ];

  it.each(REFUSED)(
    '%s: no Subscribe and no plan, and Manage billing instead',
    (_status, billing) => {
      const html = render({ billing });
      expect(html).not.toContain('Subscribe');
      expect(html).not.toContain('per month');
      expect(html).toContain('Manage billing');
    },
  );
});

describe('incomplete — the first payment was never confirmed', () => {
  it('says Manage billing shows what Stripe holds, and no longer that starting again is the fix', () => {
    const html = render({ billing: incomplete });
    expect(html).toContain('Incomplete');
    expect(html).toContain(
      'A subscription was started but the first payment was never confirmed. Manage billing shows what Stripe holds for it.',
    );
    expect(html).not.toContain('Starting again');
    // Not "finish or cancel it": the portal is set up to cancel at the end of
    // the period and to update cards (docs/runbook.md), and nothing here knows
    // it can finish a first payment.
    expect(html).not.toContain('Finish or cancel');
  });
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('mounted — what a click does, 2026-10-01', () => {
  const CHECKOUT_URL = 'https://checkout.stripe.example/c/pay/cs_test_SENTINEL';
  const PORTAL_URL = 'https://billing.stripe.example/p/session/test_SENTINEL';
  const CHECKOUT_PATH = '/api/v1/agencies/agcy_01TEST/billing/checkout';
  const NOT_CONFIGURED = 'STRIPE_PRICE_ID is not configured. Add it to your .env.';
  const NEEDS_ATTENTION =
    'This agency already has a subscription that needs attention. Manage it from Manage billing in Settings.';
  const ALREADY_ACTIVE =
    'This agency already has a current subscription. Manage it from Manage billing in Settings.';
  const IN_PROGRESS = 'A checkout for this agency is already in progress. Try again in a moment.';

  const assign = vi.fn<(url: string) => void>();
  let root: Root | null = null;

  /** The panel for real, so a click reaches the API call the way a browser's would. */
  function mount(billing: BillingStatus): HTMLDivElement {
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() => root!.render(<BillingPanel agencyId="agcy_01TEST" billing={billing} />));
    return el;
  }

  /** Let the mocked request settle and React flush what it set. */
  const settle = () =>
    act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

  const button = (el: HTMLElement, label: string) =>
    [...el.querySelectorAll('button')].find((b) => b.textContent === label);

  async function click(el: HTMLElement, label: string): Promise<void> {
    const target = button(el, label);
    expect(target, `no "${label}" button on screen`).toBeDefined();
    act(() => target!.click());
    await settle();
  }

  const alerts = (el: HTMLElement) => [...el.querySelectorAll('[role="alert"]')];

  /** The browser showing this page: restored from its back/forward cache, or not. */
  const pageshow = (persisted: boolean) =>
    act(() => {
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted }));
    });

  const problem = (status: number, type: string, detail: string) =>
    new ApiProblem({ type, title: 'Problem', status, detail, instance: CHECKOUT_PATH });

  beforeEach(() => {
    startCheckout.mockReset();
    openBillingPortal.mockReset();
    assign.mockReset();
    // jsdom does not navigate, and its `location` cannot be spied on in place.
    vi.stubGlobal('location', { assign });
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  describe('Subscribe', () => {
    const FROM: Array<[string, BillingStatus]> = [
      ['no subscription', noSubscription],
      ['canceled', canceledSubscription],
      ['incomplete_expired', incompleteExpired],
    ];

    it.each(FROM)(
      '%s: starts a checkout for this agency and goes to the URL it returns',
      async (_status, billing) => {
        startCheckout.mockResolvedValue({ url: CHECKOUT_URL });
        const el = mount(billing);
        await click(el, 'Subscribe');
        expect(startCheckout).toHaveBeenCalledTimes(1);
        expect(startCheckout).toHaveBeenCalledWith('agcy_01TEST');
        expect(assign).toHaveBeenCalledWith(CHECKOUT_URL);
      },
    );

    it('says which call is opening: only the button that was clicked claims it', async () => {
      startCheckout.mockResolvedValue({ url: CHECKOUT_URL });
      const el = mount(canceledSubscription);
      await click(el, 'Subscribe');
      // The page is on its way to Stripe. Both buttons are inert; one says why.
      expect(button(el, 'Opening checkout…')?.disabled).toBe(true);
      expect(button(el, 'Manage billing')?.disabled).toBe(true);
      expect(button(el, 'Opening portal…')).toBeUndefined();
    });

    it('Manage billing, beside it, opens the portal and leaves Subscribe saying Subscribe', async () => {
      openBillingPortal.mockResolvedValue({ url: PORTAL_URL });
      const el = mount(canceledSubscription);
      await click(el, 'Manage billing');
      expect(openBillingPortal).toHaveBeenCalledWith('agcy_01TEST');
      expect(assign).toHaveBeenCalledWith(PORTAL_URL);
      expect(button(el, 'Opening portal…')?.disabled).toBe(true);
      expect(button(el, 'Opening checkout…')).toBeUndefined();
      expect(startCheckout).not.toHaveBeenCalled();
    });

    it('while the portal is opening, Subscribe is disabled, not merely inert, and still says Subscribe', async () => {
      openBillingPortal.mockReturnValue(new Promise<PortalSession>(() => {}));
      const el = mount(canceledSubscription);
      await click(el, 'Manage billing');
      // A button that looks pressable and does nothing misreports itself to
      // assistive technology; and it is not checkout that is opening.
      expect(button(el, 'Subscribe')?.disabled).toBe(true);
      expect(button(el, 'Opening checkout…')).toBeUndefined();
      await click(el, 'Subscribe');
      expect(startCheckout).not.toHaveBeenCalled();
      expect(assign).not.toHaveBeenCalled();
    });

    it('a failed Subscribe shows beside the plan, once, and the button is live again', async () => {
      startCheckout.mockRejectedValue(
        problem(503, '/problems/billing-not-configured', NOT_CONFIGURED),
      );
      const el = mount(canceledSubscription);
      await click(el, 'Subscribe');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(NOT_CONFIGURED);
      expect(alerts(el)[0]!.closest('.avp-card')!.textContent).toContain('One plan');
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
      expect(assign).not.toHaveBeenCalled();
    });

    it('a failed Manage billing shows beside Manage billing, once, and the button is live again', async () => {
      openBillingPortal.mockRejectedValue(new TypeError('Failed to fetch'));
      const el = mount(canceledSubscription);
      await click(el, 'Manage billing');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(
        'The billing portal could not be opened. Nothing has changed.',
      );
      const card = alerts(el)[0]!.closest('.avp-card')!;
      expect(card.textContent).toContain('Canceled');
      expect(card.textContent).not.toContain('One plan');
      expect(button(el, 'Manage billing')?.disabled).toBe(false);
    });
  });

  describe('a subscription refusal from checkout', () => {
    // The API says this agency already has a subscription: one it pays for
    // (`subscription-already-active`) or one that needs attention
    // (`subscription-needs-attention`). Either way the page showed no
    // subscription, or one it was offering to replace, and the sentence sends
    // the person to Manage billing. That button has to exist.
    it.each([
      ['/problems/subscription-needs-attention', NEEDS_ATTENTION],
      ['/problems/subscription-already-active', ALREADY_ACTIVE],
    ])('%s: shows the API’s sentence and the Manage billing it points at', async (type, detail) => {
      startCheckout.mockRejectedValue(problem(409, type, detail));
      const el = mount(customerNoSubscription);
      // Nothing to manage as far as this page knew...
      expect(button(el, 'Manage billing')).toBeUndefined();
      await click(el, 'Subscribe');
      // ...until the API says otherwise.
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(detail);
      expect(button(el, 'Manage billing')).toBeDefined();
      // Below the sentence that points at it, not above it.
      expect(
        alerts(el)[0]!.compareDocumentPosition(button(el, 'Manage billing')!) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      // Busy resets.
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
      expect(assign).not.toHaveBeenCalled();
    });

    it.each([
      // "Try again in a moment": another checkout holds the agency. It is a
      // 409, and it says nothing about a subscription, so it points nowhere.
      ['/problems/checkout-in-progress', IN_PROGRESS],
      ['/problems/a-conflict-nobody-has-named-yet', 'Something else is in the way.'],
    ])('goes by the type, not the status: a 409 of type %s shows its sentence only', async (type, detail) => {
      startCheckout.mockRejectedValue(problem(409, type, detail));
      const el = mount(customerNoSubscription);
      await click(el, 'Subscribe');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(detail);
      expect(button(el, 'Manage billing')).toBeUndefined();
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('shows Manage billing even when this page predates the Stripe customer', async () => {
      // `hasBillingAccount` is the page's snapshot. The portal reads the
      // customer server-side, and the sentence points at the button, so it has
      // to be there.
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION),
      );
      const el = mount(noSubscription);
      expect(button(el, 'Manage billing')).toBeUndefined();
      await click(el, 'Subscribe');
      expect(alerts(el)[0]!.textContent).toBe(NEEDS_ATTENTION);
      expect(button(el, 'Manage billing')).toBeDefined();
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('and for a cancelled agency whose page has no customer either', async () => {
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-already-active', ALREADY_ACTIVE),
      );
      const el = mount({ ...canceledSubscription, hasBillingAccount: false });
      expect(button(el, 'Manage billing')).toBeUndefined();
      await click(el, 'Subscribe');
      expect(button(el, 'Manage billing')).toBeDefined();
    });

    it('opens the portal from a Manage billing that appeared with no customer on the page', async () => {
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION),
      );
      openBillingPortal.mockResolvedValue({ url: PORTAL_URL });
      const el = mount(noSubscription);
      await click(el, 'Subscribe');
      await click(el, 'Manage billing');
      expect(openBillingPortal).toHaveBeenCalledWith('agcy_01TEST');
      expect(assign).toHaveBeenCalledWith(PORTAL_URL);
    });

    it('a 503 shows its sentence and reveals nothing', async () => {
      startCheckout.mockRejectedValue(
        problem(503, '/problems/billing-not-configured', NOT_CONFIGURED),
      );
      const el = mount(customerNoSubscription);
      await click(el, 'Subscribe');
      expect(alerts(el)[0]!.textContent).toBe(NOT_CONFIGURED);
      expect(button(el, 'Manage billing')).toBeUndefined();
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('stays revealed once seen: a later, different failure does not take it away', async () => {
      startCheckout
        .mockRejectedValueOnce(problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION))
        .mockRejectedValueOnce(problem(503, '/problems/billing-not-configured', NOT_CONFIGURED));
      const el = mount(customerNoSubscription);
      await click(el, 'Subscribe');
      await click(el, 'Subscribe');
      expect(alerts(el)[0]!.textContent).toBe(NOT_CONFIGURED);
      expect(button(el, 'Manage billing')).toBeDefined();
    });

    it('opens the portal from the revealed button, and its own failure replaces the 409 sentence', async () => {
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION),
      );
      openBillingPortal.mockRejectedValue(new TypeError('Failed to fetch'));
      const el = mount(customerNoSubscription);
      await click(el, 'Subscribe');
      await click(el, 'Manage billing');
      expect(openBillingPortal).toHaveBeenCalledWith('agcy_01TEST');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(
        'The billing portal could not be opened. Nothing has changed.',
      );
    });

    it('in a state that already has Manage billing, shows one of it, not two', async () => {
      // A cancelled agency whose page is stale: it re-subscribed in another tab.
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-already-active', ALREADY_ACTIVE),
      );
      const el = mount(canceledSubscription);
      await click(el, 'Subscribe');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(ALREADY_ACTIVE);
      const manageButtons = [...el.querySelectorAll('button')].filter(
        (b) => b.textContent === 'Manage billing',
      );
      expect(manageButtons).toHaveLength(1);
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });
  });

  describe('back from Stripe', () => {
    // After `location.assign` the browser may keep this page in its back/forward
    // cache and restore it, as it was, when the person comes back — React state
    // included. A button that said "Opening checkout…" still says it, disabled.
    it('a restored page has its Subscribe back', async () => {
      startCheckout.mockResolvedValue({ url: CHECKOUT_URL });
      const el = mount(noSubscription);
      await click(el, 'Subscribe');
      expect(button(el, 'Opening checkout…')?.disabled).toBe(true);
      pageshow(true);
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
      expect(button(el, 'Opening checkout…')).toBeUndefined();
    });

    it('and its Manage billing', async () => {
      openBillingPortal.mockResolvedValue({ url: PORTAL_URL });
      const el = mount(activeSubscription);
      await click(el, 'Manage billing');
      expect(button(el, 'Opening portal…')?.disabled).toBe(true);
      pageshow(true);
      expect(button(el, 'Manage billing')?.disabled).toBe(false);
      expect(button(el, 'Opening portal…')).toBeUndefined();
    });

    it('an ordinary page show does not interrupt a call in flight', async () => {
      startCheckout.mockReturnValue(new Promise<CheckoutSession>(() => {}));
      const el = mount(noSubscription);
      await click(el, 'Subscribe');
      pageshow(false);
      expect(button(el, 'Opening checkout…')?.disabled).toBe(true);
      pageshow(true);
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('stops listening when the panel unmounts', () => {
      const add = vi.spyOn(window, 'addEventListener');
      const remove = vi.spyOn(window, 'removeEventListener');
      try {
        mount(noSubscription);
        const listener = add.mock.calls.find(([type]) => type === 'pageshow')?.[1];
        expect(listener).toBeDefined();
        act(() => root!.unmount());
        root = null;
        expect(remove).toHaveBeenCalledWith('pageshow', listener);
      } finally {
        add.mockRestore();
        remove.mockRestore();
      }
    });
  });
});

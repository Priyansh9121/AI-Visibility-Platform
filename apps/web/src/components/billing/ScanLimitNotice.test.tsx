// @vitest-environment jsdom

/**
 * A 402 is a subscribe prompt, not an error — 2026-09-28.
 *
 * The last group, "mounted", presses Subscribe (2026-10-01): the real notice
 * in jsdom, the API module mocked at its edge, as BillingPanel.test.tsx and
 * SeatsPanel.test.tsx do. What a 409 leaves on screen, and whether a page
 * restored from the back/forward cache is still busy, are things a static
 * render never receives.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import type { CheckoutSession } from '@avp/shared-types';
import { ApiProblem, GOOGLE_SIGN_IN_URL } from '@/lib/api';

const startCheckout = vi.fn<(agencyId: string) => Promise<CheckoutSession>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: { ...real.api, startCheckout: (agencyId: string) => startCheckout(agencyId) },
  };
});

// Imported after the mock so the notice sees the mocked module.
const { ScanLimitNotice } = await import('./ScanLimitNotice');

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
      'Your free scan needs a Google sign-in, so we know one person gets one. Continue with Google using the same address you sign in with here, or subscribe. A different Google account would start a new agency instead.',
    );
    expect(html).not.toContain('could not be started');
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain('Your free scan is used');
  });

  it('says plainly that it must be the same address, and what another one does — 2026-09-29', () => {
    // "Using this address" pointed at nothing: the card never shows one, and a
    // different, unused Google account is not refused — it becomes a new
    // agency's sign-up. The rule and its consequence are both on the card.
    const html = render(VERIFY);
    expect(html).toContain('the same address you sign in with here');
    expect(html).toContain('A different Google account would start a new agency instead.');
    expect(html).not.toContain('using this address');
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

describe('no way to Settings until a checkout has been refused — 2026-10-01', () => {
  it.each([
    ['the free scan is used', FREE],
    ['the monthly cap is reached', MONTHLY],
    ['the free scan needs a Google sign-in', VERIFY],
  ])('%s: the notice links nowhere', (_name, problem) => {
    const html = render(problem);
    expect(html).not.toContain('Open Settings');
    expect(html).not.toContain('href="/settings');
  });

  it('the free-used Subscribe starts out as the one primary action', () => {
    expect(render(FREE)).toMatch(/avp-btn--primary[^>]*><span class="avp-btn__label">Subscribe</);
  });
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('mounted — Subscribe is pressed, 2026-10-01', () => {
  const CHECKOUT_URL = 'https://checkout.stripe.example/c/pay/cs_test_SENTINEL';
  const NOT_CONFIGURED = 'STRIPE_PRICE_ID is not configured. Add it to your .env.';
  const NEEDS_ATTENTION =
    'This agency already has a subscription that needs attention. Manage it from Manage billing in Settings.';
  const ALREADY_ACTIVE =
    'This agency already has a current subscription. Manage it from Manage billing in Settings.';
  const IN_PROGRESS = 'A checkout for this agency is already in progress. Try again in a moment.';
  // The Billing section of Settings, where Manage billing is.
  const BILLING = '/settings#billing';

  const navigate = vi.fn<(url: string) => void>();
  let root: Root | null = null;

  /** The notice for real, so a click reaches the API call the way a browser's would. */
  function mount(problem: typeof FREE | typeof VERIFY = FREE): HTMLDivElement {
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() =>
      root!.render(
        <ScanLimitNotice problem={problem} agencyId="agcy_01TEST" navigate={navigate} />,
      ),
    );
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
  const settingsLinks = (el: HTMLElement) => [...el.querySelectorAll(`a[href="${BILLING}"]`)];
  const subscribeClass = (el: HTMLElement) => button(el, 'Subscribe')!.className;

  /** The browser showing this page: restored from its back/forward cache, or not. */
  const pageshow = (persisted: boolean) =>
    act(() => {
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted }));
    });

  const problem = (status: number, type: string, detail: string) =>
    new ApiProblem({
      type,
      title: 'Problem',
      status,
      detail,
      instance: '/api/v1/agencies/agcy_01TEST/billing/checkout',
    });

  beforeEach(() => {
    startCheckout.mockReset();
    navigate.mockReset();
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    document.body.innerHTML = '';
  });

  it.each([
    ['the free scan is used', FREE],
    ['the free scan needs a Google sign-in', VERIFY],
  ])('%s: Subscribe starts a checkout and goes to the URL it returns', async (_name, which) => {
    startCheckout.mockResolvedValue({ url: CHECKOUT_URL });
    const el = mount(which);
    await click(el, 'Subscribe');
    expect(startCheckout).toHaveBeenCalledWith('agcy_01TEST');
    expect(navigate).toHaveBeenCalledWith(CHECKOUT_URL);
  });

  describe('a subscription refusal from checkout', () => {
    // This agency already has a subscription: one it pays for, or one that
    // needs attention. The API's sentence says "Manage it from Manage billing
    // in Settings", and this notice has no billing controls of its own — so it
    // gives the way there: to the Billing section, where Manage billing is.
    it.each([
      ['/problems/subscription-needs-attention', NEEDS_ATTENTION],
      ['/problems/subscription-already-active', ALREADY_ACTIVE],
    ])('%s: shows the sentence and a link to Settings', async (type, detail) => {
      startCheckout.mockRejectedValue(problem(409, type, detail));
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(detail);
      const links = settingsLinks(el);
      expect(links).toHaveLength(1);
      expect(links[0]!.textContent).toBe('Open Settings');
      expect(links[0]!.getAttribute('href')).toBe('/settings#billing');
      // After the sentence that points there, not before it.
      expect(
        alerts(el)[0]!.compareDocumentPosition(links[0]!) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      // Busy resets, and nothing was navigated to.
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
      expect(navigate).not.toHaveBeenCalled();
    });

    it.each([
      // "Try again in a moment": another checkout holds the agency. It is a
      // 409, and it says nothing about a subscription, so it points nowhere.
      ['/problems/checkout-in-progress', IN_PROGRESS],
      ['/problems/a-conflict-nobody-has-named-yet', 'Something else is in the way.'],
    ])('goes by the type, not the status: a 409 of type %s shows its sentence only', async (type, detail) => {
      startCheckout.mockRejectedValue(problem(409, type, detail));
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(detail);
      expect(settingsLinks(el)).toHaveLength(0);
      expect(el.querySelector('a[href^="/settings"]')).toBeNull();
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('does the same beside Continue with Google, which stays', async () => {
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION),
      );
      const el = mount(VERIFY);
      await click(el, 'Subscribe');
      expect(alerts(el)[0]!.textContent).toBe(NEEDS_ATTENTION);
      expect(settingsLinks(el)).toHaveLength(1);
      expect(el.querySelector(`a[href="${GOOGLE_SIGN_IN_URL}"]`)).not.toBeNull();
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('the link belongs to the refusal: a later, different failure takes it away', async () => {
      startCheckout
        .mockRejectedValueOnce(problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION))
        .mockRejectedValueOnce(problem(503, '/problems/billing-not-configured', NOT_CONFIGURED));
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(settingsLinks(el)).toHaveLength(1);
      await click(el, 'Subscribe');
      expect(alerts(el)[0]!.textContent).toBe(NOT_CONFIGURED);
      expect(settingsLinks(el)).toHaveLength(0);
    });
  });

  describe('Subscribe stops being the primary action once it can only fail again', () => {
    // The free-used notice's one primary button. After a subscription refusal
    // another press can only be refused again, so it steps back and the link
    // to Settings is what is left to do. Beside Continue with Google it was
    // secondary all along.
    it('goes secondary after a subscription refusal', async () => {
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION),
      );
      const el = mount(FREE);
      expect(subscribeClass(el)).toContain('avp-btn--primary');
      await click(el, 'Subscribe');
      expect(subscribeClass(el)).toContain('avp-btn--secondary');
      expect(subscribeClass(el)).not.toContain('avp-btn--primary');
    });

    it.each([
      ['checkout-in-progress, which a moment fixes', problem(409, '/problems/checkout-in-progress', IN_PROGRESS)],
      ['a 503, which a configuration fixes', problem(503, '/problems/billing-not-configured', NOT_CONFIGURED)],
      ['a network failure, which a retry may fix', new TypeError('Failed to fetch')],
    ])('stays primary after %s', async (_name, failure) => {
      startCheckout.mockRejectedValue(failure);
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(subscribeClass(el)).toContain('avp-btn--primary');
    });

    it('is primary again once a later press fails some other way', async () => {
      startCheckout
        .mockRejectedValueOnce(problem(409, '/problems/subscription-already-active', ALREADY_ACTIVE))
        .mockRejectedValueOnce(problem(503, '/problems/billing-not-configured', NOT_CONFIGURED));
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(subscribeClass(el)).toContain('avp-btn--secondary');
      await click(el, 'Subscribe');
      expect(subscribeClass(el)).toContain('avp-btn--primary');
    });

    it('beside Continue with Google it is secondary before and after', async () => {
      startCheckout.mockRejectedValue(
        problem(409, '/problems/subscription-needs-attention', NEEDS_ATTENTION),
      );
      const el = mount(VERIFY);
      expect(subscribeClass(el)).toContain('avp-btn--secondary');
      await click(el, 'Subscribe');
      expect(subscribeClass(el)).toContain('avp-btn--secondary');
    });
  });

  describe('any other failure is as it was', () => {
    it('a 503 shows its detail, no link, and Subscribe is live again', async () => {
      startCheckout.mockRejectedValue(
        problem(503, '/problems/billing-not-configured', NOT_CONFIGURED),
      );
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(alerts(el)).toHaveLength(1);
      expect(alerts(el)[0]!.textContent).toBe(NOT_CONFIGURED);
      expect(settingsLinks(el)).toHaveLength(0);
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('a network failure says checkout could not be opened, with no link', async () => {
      startCheckout.mockRejectedValue(new TypeError('Failed to fetch'));
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(alerts(el)[0]!.textContent).toBe('Checkout could not be opened.');
      expect(settingsLinks(el)).toHaveLength(0);
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });
  });

  describe('back from Stripe', () => {
    // After the navigation the browser may keep this page in its back/forward
    // cache and restore it, as it was, when the person comes back — React
    // state included. A button that said "Opening checkout…" still says it,
    // disabled.
    it('a restored page has its Subscribe back', async () => {
      startCheckout.mockResolvedValue({ url: CHECKOUT_URL });
      const el = mount(FREE);
      await click(el, 'Subscribe');
      expect(button(el, 'Opening checkout…')?.disabled).toBe(true);
      pageshow(true);
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
      expect(button(el, 'Opening checkout…')).toBeUndefined();
    });

    it('an ordinary page show does not interrupt a call in flight', async () => {
      startCheckout.mockReturnValue(new Promise<CheckoutSession>(() => {}));
      const el = mount(FREE);
      await click(el, 'Subscribe');
      pageshow(false);
      expect(button(el, 'Opening checkout…')?.disabled).toBe(true);
      pageshow(true);
      expect(button(el, 'Subscribe')?.disabled).toBe(false);
    });

    it('stops listening when the notice unmounts', () => {
      const add = vi.spyOn(window, 'addEventListener');
      const remove = vi.spyOn(window, 'removeEventListener');
      try {
        mount(FREE);
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

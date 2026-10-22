// @vitest-environment jsdom

/**
 * The front door's Google failure notice, mounted — 2026-09-29.
 *
 * A failed Google step used to land here signed out, and the notice lived on
 * the sign-in view alone. Since the free scan began to need a Google
 * sign-in, the scan-limit notice sends a SIGNED-IN user to Google to link
 * the account, so a failure can land here with the session still live — and
 * the intake screen said nothing, so Run a scan met the same notice again,
 * in a loop, with no explanation.
 *
 * The word-to-sentence mapping is tested as pure functions in
 * `lib/auth/googleFailure.test.ts`. What only a mount can show is which view
 * the notice lands on, in whose words, and that the query is gone afterwards:
 * `Home` does all three in an effect, which a static render never runs. The
 * pattern is `WorkspaceShell.test.tsx`'s — the real component in jsdom, the
 * API module mocked at its edge.
 *
 * The last group (round two, 2026-10-04) is the public page's early call for the free trial's
 * length: `Home` asks for it at the top of `load`, beside the session check, waits for it only
 * where it is the landing page that is about to be drawn (and then only a second and a half),
 * and hands the answer to `LandingView`, so the pricing card is drawn with its sentence in it.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { BillingPlan } from '@avp/shared-types';
import { ownerMe } from '@/lib/settings/__fixtures__/seats';
import { GOOGLE_REASONS, googleReasonSentence } from '@/lib/auth/googleFailure';

const me = vi.fn();
const billingPlan = vi.fn<() => Promise<BillingPlan>>();
// What the page handed the landing page and the pricing card, render by render, and how many
// times the card was mounted: "drawn with its sentence" is about the FIRST render, which a
// final DOM cannot tell from one that got it a moment later.
const seen = vi.hoisted(() => ({
  landing: [] as Array<number | undefined>,
  card: [] as Array<number | undefined>,
  // The promise the page handed the card (its own request for the plan), render by render.
  cardPlan: [] as Array<Promise<number> | undefined>,
  cardMounts: { count: 0 },
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, me: () => me(), billingPlan: () => billingPlan() } };
});

// The real components, each inside a recorder.
vi.mock('@/components/marketing/LandingView', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/components/marketing/LandingView')>();
  return {
    ...real,
    LandingView: (props: Parameters<typeof real.LandingView>[0]) => {
      seen.landing.push(props.initialTrialDays);
      return <real.LandingView {...props} />;
    },
  };
});
vi.mock('@/components/marketing/PricingCard', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/components/marketing/PricingCard')>();
  return {
    ...real,
    PricingCard: (props: Parameters<typeof real.PricingCard>[0]) => {
      seen.card.push(props.initialTrialDays);
      seen.cardPlan.push(props.trialPlan);
      useEffect(() => {
        seen.cardMounts.count += 1;
      }, []);
      return <real.PricingCard {...props} />;
    },
  };
});

// Imported after the mock so the page sees the mocked module.
const { default: Home } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TITLE = 'Google sign-in did not complete';
const INTAKE = 'Start with a website.';
// The founder's two sentences for `reason=busy` (A6 part 2, 2026-10-22), written out here and not
// read back from the maps: a reworded sentence has to come through this file.
const BUSY_SIGNED_OUT =
  'Google sign-in is busy right now. Wait a few minutes and try again, or sign in with your email and password.';
const BUSY_SIGNED_IN =
  'Google sign-in is busy right now, so nothing was linked. Wait a few minutes and try again.';
const FALLBACK = 'Google sign-in did not complete. Try again.';

/**
 * The alert region (`role="alert"`, which `ErrorState` puts around its title and its sentence) that
 * holds `sentence`, or null. `textContent` alone passes for a notice rendered anywhere on the page;
 * a screen reader announces only what is inside the region.
 */
const alertHolding = (el: HTMLElement, sentence: string): Element | null =>
  Array.from(el.querySelectorAll('[role="alert"]')).find((a) =>
    (a.textContent ?? '').includes(sentence),
  ) ?? null;

/** Let every pending request settle: the mocked `me` and the plan resolve within a few ticks. */
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

let root: Root | null = null;

/** Arrive at `/` with this query, the way Google's callback sends the browser. */
async function arrive(search: string): Promise<HTMLDivElement> {
  window.history.replaceState(null, '', `/${search}`);
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<Home />));
  await settle();
  return el;
}

beforeEach(() => {
  // No trial unless a test says there is one, and never the real `fetch`.
  billingPlan.mockReset();
  billingPlan.mockResolvedValue({ trialDays: 0 });
  seen.landing.length = 0;
  seen.card.length = 0;
  seen.cardPlan.length = 0;
  seen.cardMounts.count = 0;
});

/**
 * The plan requests a test left out, answered 0 when it is over: the loader shares a request
 * that is out with whoever asks next, so one left hanging would be the next test's answer.
 */
const letGo: Array<() => void> = [];

afterEach(async () => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  window.history.replaceState(null, '', '/');
  // A timeline test that failed part-way leaves answers waiting on a clock about to be thrown
  // away. Let them come, or the request they belong to is never settled and the next test is
  // handed it (the loader shares a request that is out).
  if (vi.isFakeTimers()) await vi.runAllTimersAsync();
  vi.useRealTimers();
  for (const release of letGo.splice(0)) release();
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe('signed in, back from a Google step that failed', () => {
  beforeEach(() => {
    me.mockReset();
    me.mockResolvedValue(ownerMe);
  });

  it('denied: the intake screen, with the notice above the form', async () => {
    const el = await arrive('?google=error&reason=denied');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(GOOGLE_REASONS.denied);
    expect(text).toContain(INTAKE);
    const html = el.innerHTML;
    expect(html.indexOf(TITLE)).toBeGreaterThan(-1);
    expect(html.indexOf(TITLE)).toBeLessThan(html.indexOf('<form'));
    expect(window.location.search).toBe('');
  });

  it("email-claimed: the words for someone signed in, naming this sign-in's address", async () => {
    const el = await arrive('?google=error&reason=email-claimed');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(googleReasonSentence('email-claimed', ownerMe.user.email));
    expect(text).toContain(`choose ${ownerMe.user.email}`);
    // Not the sign-in page's words: this person is not signing in.
    expect(text).not.toContain(GOOGLE_REASONS['email-claimed']);
    expect(text).toContain(INTAKE);
    expect(window.location.search).toBe('');
  });

  it('busy: the words for someone linking — nothing was linked, wait and try again', async () => {
    const el = await arrive('?google=error&reason=busy');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(BUSY_SIGNED_IN);
    // In the alert region, which also holds the title, so it is announced as one notice.
    expect(alertHolding(el, BUSY_SIGNED_IN)?.textContent ?? '').toContain(TITLE);
    // Not the sign-in page's words: this person is not signing in, and has no need of a password.
    expect(text).not.toContain(BUSY_SIGNED_OUT);
    expect(text).toContain(INTAKE);
    expect(window.location.search).toBe('');
  });

  it('a made-up word that a plain object also answers to: the fallback, not that property', async () => {
    const el = await arrive('?google=error&reason=constructor');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(FALLBACK);
    expect(text).toContain(INTAKE);
    expect(window.location.search).toBe('');
  });

  it('no failure in the query, no notice — and the query is left alone', async () => {
    const el = await arrive('?utm_source=newsletter');
    expect(el.textContent).toContain(INTAKE);
    expect(el.textContent).not.toContain(TITLE);
    expect(window.location.search).toBe('?utm_source=newsletter');
  });
});

describe('signed out: unchanged', () => {
  beforeEach(() => {
    me.mockReset();
    me.mockRejectedValue(
      new ApiProblem({
        type: '/problems/unauthenticated',
        title: 'Not signed in',
        status: 401,
        detail: 'Sign in to continue.',
        instance: '/api/v1/auth/me',
      }),
    );
  });

  it('denied: straight to the sign-in form, with the notice', async () => {
    const el = await arrive('?google=error&reason=denied');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(GOOGLE_REASONS.denied);
    expect(text).toContain('Sign in with Google');
    expect(text).not.toContain(INTAKE);
    expect(window.location.search).toBe('');
  });

  it('email-claimed: the sign-in page keeps its own words', async () => {
    const el = await arrive('?google=error&reason=email-claimed');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(GOOGLE_REASONS['email-claimed']);
    expect(text).toContain('Sign in with Google');
    expect(window.location.search).toBe('');
  });

  it('busy: the sign-in page says to wait, or to sign in with email and password', async () => {
    const el = await arrive('?google=error&reason=busy');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(BUSY_SIGNED_OUT);
    // In the alert region, which also holds the title, so it is announced as one notice.
    expect(alertHolding(el, BUSY_SIGNED_OUT)?.textContent ?? '').toContain(TITLE);
    expect(text).not.toContain(BUSY_SIGNED_IN);
    expect(text).toContain('Sign in with Google');
    // The sentence offers a password, so the field to type it in is on the same screen.
    expect(el.querySelector('input[type="password"]')).not.toBeNull();
    expect(text).not.toContain(INTAKE);
    expect(window.location.search).toBe('');
  });

  it('a made-up word that a plain object also answers to: the fallback, not that property', async () => {
    const el = await arrive('?google=error&reason=toString');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(FALLBACK);
    expect(text).toContain('Sign in with Google');
    expect(text).not.toContain(INTAKE);
    expect(window.location.search).toBe('');
  });
});

describe('the forms by URL, for the header on other public pages — 2026-09-29', () => {
  const signedOut = () =>
    me.mockRejectedValue(
      new ApiProblem({
        type: '/problems/unauthenticated',
        title: 'Not signed in',
        status: 401,
        detail: 'Sign in to continue.',
        instance: '/api/v1/auth/me',
      }),
    );

  beforeEach(() => {
    me.mockReset();
  });

  it('signed out, ?view=sign-in opens the sign-in form, and the query is cleared', async () => {
    signedOut();
    const el = await arrive('?view=sign-in');
    expect(el.textContent).toContain('Sign in with Google');
    expect(el.textContent).not.toContain(TITLE);
    expect(window.location.search).toBe('');
  });

  it('signed out, ?view=sign-up opens the sign-up form', async () => {
    signedOut();
    const el = await arrive('?view=sign-up');
    expect(el.textContent).toContain('Sign up with Google');
    expect(window.location.search).toBe('');
  });

  it('signed out with no view asked for, the landing page as before', async () => {
    signedOut();
    const el = await arrive('');
    expect(el.textContent).toContain('For SEO and digital marketing agencies');
    expect(el.textContent).not.toContain('Sign in with Google');
  });

  it('signed in, a view in the query changes nothing but is cleared', async () => {
    me.mockResolvedValue(ownerMe);
    const el = await arrive('?view=sign-in');
    expect(el.textContent).toContain(INTAKE);
    expect(window.location.search).toBe('');
  });
});

describe('the free trial on the public page — drawn with its sentence, round two', () => {
  const SENTENCE =
    'The first 30 days after you subscribe are free, if you subscribe from an account signed in with Google. You enter your card at checkout, and it is charged on day\u00a031 unless you cancel first. One free trial per agency.';
  const PROBE = 'after you subscribe';
  const signedOut = () =>
    me.mockRejectedValue(
      new ApiProblem({
        type: '/problems/unauthenticated',
        title: 'Not signed in',
        status: 401,
        detail: 'Sign in to continue.',
        instance: '/api/v1/auth/me',
      }),
    );

  beforeEach(() => {
    me.mockReset();
  });

  /** A plan request that stays out until the test answers it. */
  function pendingPlan(): (days: number) => void {
    let answer!: (days: number) => void;
    billingPlan.mockReturnValue(
      new Promise<BillingPlan>((resolve) => {
        answer = (days) => resolve({ trialDays: days });
      }),
    );
    letGo.push(() => answer(0));
    return answer;
  }

  it('asks for the plan beside the session check, not after it', async () => {
    let signIn!: (value: unknown) => void;
    me.mockReturnValue(new Promise((resolve) => (signIn = resolve)));
    billingPlan.mockResolvedValue({ trialDays: 30 });
    root = createRoot(document.body.appendChild(document.createElement('div')));
    await act(async () => {
      root!.render(<Home />);
    });
    // `me` has not answered, and the plan has already been asked for.
    expect(me).toHaveBeenCalledTimes(1);
    expect(billingPlan).toHaveBeenCalledTimes(1);
    await act(async () => {
      signIn(ownerMe);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  });

  it('the plan answers within the wait: the landing page, and its card, mount ONCE and already carry it', async () => {
    signedOut();
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await arrive('');
    // The first time the landing page was drawn it was told 30, and the card was too: nothing
    // was drawn without it and filled in after, which is a page that jumps.
    expect(seen.landing[0]).toBe(30);
    expect(new Set(seen.landing)).toEqual(new Set([30]));
    expect(seen.card[0]).toBe(30);
    expect(seen.cardMounts.count).toBe(1);
    expect(el.textContent).toContain(SENTENCE);
    // And one request for the whole page: the card, told the answer, did not ask again.
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('the plan fails: the page is drawn without a sentence, once, and nobody asks again', async () => {
    signedOut();
    billingPlan.mockRejectedValue(new TypeError('network down'));
    const el = await arrive('');
    expect(seen.landing[0]).toBe(0);
    expect(seen.cardMounts.count).toBe(1);
    expect(el.textContent).not.toContain(PROBE);
    expect(el.textContent).toContain('One plan. $150 a month.');
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('the plan says no trial: no sentence, still one request', async () => {
    signedOut();
    billingPlan.mockResolvedValue({ trialDays: 0 });
    const el = await arrive('');
    expect(el.textContent).not.toContain(PROBE);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  /**
   * The home page on a fake clock, for the tests that are about WHEN things happen. `me` answers
   * (as a 401) `meAfter` ms from the render, and the plan `planAfter` ms from when it is asked,
   * both on the fake clock, so a test steps through the timeline and looks at the page between.
   */
  async function onAFakeClock(meAfter: number, planAfter: number | 'never') {
    vi.useFakeTimers();
    window.history.replaceState(null, '', '/');
    me.mockImplementation(
      () =>
        new Promise((_, reject) =>
          setTimeout(
            () =>
              reject(
                new ApiProblem({
                  type: '/problems/unauthenticated',
                  title: 'Not signed in',
                  status: 401,
                  detail: 'Sign in to continue.',
                  instance: '/api/v1/auth/me',
                }),
              ),
            meAfter,
          ),
        ),
    );
    if (planAfter === 'never') {
      pendingPlan();
    } else {
      billingPlan.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({ trialDays: 30 }), planAfter)),
      );
    }
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    await act(async () => {
      root!.render(<Home />);
      await vi.advanceTimersByTimeAsync(0);
    });
    /** Move the clock on by `ms` and let everything it sets off settle. */
    const step = (ms: number) =>
      act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    return { el, step };
  }

  it('the session check is slower than the old wait and the plan comes first: the card is drawn TOLD, on ONE request', async () => {
    // Round three (N1). The plan answers at 1800 ms and the session check at 2000 ms. The wait
    // used to be counted from the CALL, so it ran out at 1500 ms, the page was drawn at 2000 ms
    // told nothing, the answer that had already come was thrown away, and the card asked again.
    // Counted from the draw point, an answer that has already come wins at once.
    const { el, step } = await onAFakeClock(2000, 1800);
    await step(1799);
    expect(seen.landing).toEqual([]);
    await step(1); // 1800: the plan has answered
    expect(seen.landing).toEqual([]);
    await step(199); // 1999
    expect(seen.landing).toEqual([]);
    await step(1); // 2000: the session check answers, and the landing page is drawn
    expect(seen.landing[0]).toBe(30);
    expect(new Set(seen.landing)).toEqual(new Set([30]));
    expect(seen.card[0]).toBe(30);
    expect(seen.cardMounts.count).toBe(1);
    expect(el.textContent).toContain(SENTENCE);
    // ONE request for the whole page: nothing asked a second time.
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('the plan is slower than the wait: drawn untold at the draw point plus half a second, the sentence later, still one request', async () => {
    // The session check answers at 100 ms, so the draw point is 100 ms and the landing page waits
    // half a second FROM THERE (600 ms), not from the call. The plan answers at 2000 ms.
    const { el, step } = await onAFakeClock(100, 2000);
    await step(599);
    expect(seen.landing).toEqual([]);
    expect(el.textContent).not.toContain('For SEO and digital marketing agencies');
    await step(1); // 600: the wait is over
    expect(seen.landing[0]).toBeUndefined();
    expect(el.textContent).toContain('For SEO and digital marketing agencies');
    expect(el.textContent).not.toContain(PROBE);
    expect(seen.cardMounts.count).toBe(1);
    // The card was drawn untold, so it awaits the SAME promise the page asked with: it was
    // handed it, and that promise is the one the plan's answer arrives on.
    expect(seen.cardPlan[0]).toBeDefined();
    await step(1399); // 1999
    expect(el.textContent).not.toContain(PROBE);
    await step(1); // 2000: the plan answers
    expect(el.textContent).toContain(SENTENCE);
    await expect(seen.cardPlan[0]).resolves.toBe(30);
    expect(seen.cardMounts.count).toBe(1);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('the landing page waits half a second at its draw point for a plan that has not answered, and no longer', async () => {
    // The session check is quick (drawn at once) and the plan never answers: the wait is exactly
    // 500 ms from the draw point. The card sits far below the fold, so the page is not held for it.
    const { el, step } = await onAFakeClock(0, 'never');
    await step(499);
    expect(seen.landing).toEqual([]);
    expect(el.textContent).not.toContain('For SEO and digital marketing agencies');
    await step(1);
    expect(seen.landing[0]).toBeUndefined();
    expect(el.textContent).toContain('For SEO and digital marketing agencies');
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('signed in: the plan is asked for in the background and never waited for', async () => {
    me.mockResolvedValue(ownerMe);
    pendingPlan();
    const el = await arrive('');
    expect(el.textContent).toContain(INTAKE);
    expect(seen.landing).toEqual([]);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('back from a failed Google step (the sign-in form, with its notice): not held up, and Back has the answer', async () => {
    signedOut();
    const answer = pendingPlan();
    const el = await arrive('?google=error&reason=denied');
    // The sign-in form and the notice are there although the plan has not answered.
    expect(el.textContent).toContain('Google sign-in did not complete');
    expect(seen.landing).toEqual([]);
    await act(async () => {
      answer(30);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const back = [...el.querySelectorAll('button')].find((b) => b.textContent === 'Back');
    expect(back, 'a Back button').toBeDefined();
    await act(async () => {
      back!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(seen.landing[0]).toBe(30);
    expect(el.textContent).toContain(SENTENCE);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('a sign-in form is not held up by the plan; going back to the landing page has the answer', async () => {
    signedOut();
    const answer = pendingPlan();
    const el = await arrive('?view=sign-in');
    // The form is there although the plan has not answered.
    expect(el.textContent).toContain('Sign in with Google');
    expect(seen.landing).toEqual([]);
    await act(async () => {
      answer(30);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const back = [...el.querySelectorAll('button')].find((b) => b.textContent === 'Back');
    expect(back, 'a Back button').toBeDefined();
    await act(async () => {
      back!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(seen.landing[0]).toBe(30);
    expect(el.textContent).toContain(SENTENCE);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });
});

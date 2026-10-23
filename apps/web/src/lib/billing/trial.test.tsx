// @vitest-environment jsdom

/**
 * The free trial, as the public pages learn and say it — 2026-10-04.
 *
 * The length of the trial is the API's (`STRIPE_TRIAL_DAYS`, read through
 * `GET /billing/plan`), so a public page never carries a copy of it. These mount the
 * pricing card and the landing page in jsdom with the API mocked at its edge, the pattern
 * `useScanAllowance.test.tsx` uses, because what matters here happens after mount: a request
 * goes out, an answer comes back or does not, and the page must say a trial only for a
 * positive number of days — never while it waits, never when the request fails, never for 0.
 * The words themselves, and every state a static render can reach, are asserted in
 * `PricingCard.test.tsx` and `LandingView.test.tsx`.
 *
 * Round two (2026-10-04): the page loaders ask for the plan early, in parallel with their
 * other first calls (`loadTrialDays`), and hand the answer down, so the card is drawn with its
 * sentence and does not appear one line taller a moment later. A card that was told the answer
 * does not ask.
 *
 * Round three: the wait for it is counted from the page's DRAW POINT, not from the call
 * (`within(promise, ms)`, half a second for the landing page and a second and a half for
 * Settings), so a session check slower than the wait cannot make the page throw away an answer
 * that already came. And a card drawn UNTOLD is handed the page's own promise and awaits that,
 * rather than asking again. The page-level behaviour is `app/home.test.tsx` and
 * `app/settings/page.test.tsx`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { BillingPlan } from '@avp/shared-types';

const billingPlan = vi.fn<() => Promise<BillingPlan>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: { ...real.api, billingPlan: () => billingPlan() },
  };
});

const {
  LANDING_PLAN_WAIT_MS,
  SETTINGS_PLAN_WAIT_MS,
  loadTrialDays,
  trialSentence,
  trialSentenceSignedOut,
  usePlanTrial,
  wholeTrialDays,
  within,
} = await import('./trial');
const { ApiProblem } = await import('@/lib/api');
const { PricingCard } = await import('@/components/marketing/PricingCard');
const { LandingView } = await import('@/components/marketing/LandingView');
const { BillingPanel } = await import('@/components/settings/BillingPanel');
const { SettingsView } = await import('@/components/settings/SettingsView');
const { ownerMe, seatsWithPending } = await import('@/lib/settings/__fixtures__/seats');
const { noSubscriptionTrialOffered } = await import('@/lib/settings/__fixtures__/billing');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// A no-break space (U+00A0) between "day" and the number, so the line never wraps between them.
const SENTENCE_30 =
  'The first 30 days after you subscribe are free. You enter your card at checkout, and it is charged on day\u00a031 unless you cancel first. One free trial per agency.';
// Words only the trial sentence carries: what a page must not contain while it says no trial.
// The signed-out card's sentence: the trial is for an account signed in with Google (2026-10-05).
const SENTENCE_30_SIGNED_OUT =
  'The first 30 days after you subscribe are free, if you subscribe from an account signed in with Google. You enter your card at checkout, and it is charged on day\u00a031 unless you cancel first. One free trial per agency.';

const PROBE = 'after you subscribe';

let root: Root | null = null;

/** Mount, and let the request the tree made, and the state it set, settle. */
async function mount(node: ReactElement): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(node);
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return el;
}

/**
 * A request that stays out until the test lets it go. `afterEach` lets every one go, 0 days,
 * because the loader shares a request that is out with whoever asks next: one left hanging
 * would be the next test's answer.
 */
const letGo: Array<() => void> = [];
function deferred<T extends BillingPlan>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  letGo.push(() => resolve({ trialDays: 0 } as T));
  return { promise, resolve };
}

const problem = new ApiProblem({
  type: '/problems/service-unavailable',
  title: 'Unavailable',
  status: 503,
  detail: 'Try again.',
  instance: '/api/v1/billing/plan',
});

beforeEach(() => {
  billingPlan.mockReset();
});

afterEach(async () => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  // Let any request a test left out settle, so it cannot reach the next one.
  for (const release of letGo.splice(0)) release();
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe('a number of trial days is a positive whole number, or it is none', () => {
  it.each([
    [30, 30],
    [1, 1],
    [730, 730],
    [0, 0],
    [-1, 0],
    [1.5, 0],
    [Number.NaN, 0],
    [Number.POSITIVE_INFINITY, 0],
    ['30', 0],
    [true, 0],
    [null, 0],
    [undefined, 0],
    [{}, 0],
  ])('%j is %j', (given, days) => {
    expect(wholeTrialDays(given)).toBe(days);
  });
});

describe('the sentence', () => {
  it('is the founder’s offer, in words: free days after you subscribe, card at checkout, charged the day after', () => {
    expect(trialSentence(30)).toBe(SENTENCE_30);
  });

  it('counts the day after the trial from the days it was given', () => {
    expect(trialSentence(14)).toContain('The first 14 days after you subscribe are free.');
    expect(trialSentence(14)).toContain('charged on day\u00a015 unless you cancel first');
    expect(trialSentence(1)).toContain('The first day after you subscribe is free.');
    expect(trialSentence(1)).toContain('charged on day\u00a02');
  });

  it('keeps "day" and its number together: a no-break space, never a plain one', () => {
    for (const days of [1, 14, 30, 99, 730]) {
      const sentence = trialSentence(days);
      expect(sentence).toContain(`day\u00a0${days + 1} `);
      expect(sentence).not.toContain(`day ${days + 1}`);
      // The one place it matters is the only place it is used: no other no-break space.
      expect(sentence.split('\u00a0').length - 1).toBe(1);
    }
  });

  it('says what the days are counted from: subscribing, not signing up or opening checkout', () => {
    expect(trialSentence(30)).toContain('after you subscribe');
    expect(trialSentence(30)).not.toMatch(/sign(ing)? up|after (you )?(sign|open|start)/i);
  });
});

describe('the signed-out sentence — the trial needs a Google sign-in, 2026-10-05', () => {
  it('is the same offer with its condition said in plain words', () => {
    expect(trialSentenceSignedOut(30)).toBe(SENTENCE_30_SIGNED_OUT);
    expect(trialSentenceSignedOut(1)).toContain(
      'The first day after you subscribe is free, if you subscribe from an account signed in with Google.',
    );
    expect(trialSentenceSignedOut(1)).toContain('charged on day\u00a02');
  });

  it('keeps every other clause of the signed-in sentence, and only the condition differs', () => {
    for (const days of [1, 14, 30, 730]) {
      const tail = trialSentence(days).replace(/^[^.]*\./, '');
      expect(trialSentenceSignedOut(days).endsWith(tail)).toBe(true);
      expect(trialSentenceSignedOut(days).split('\u00a0').length - 1).toBe(1);
    }
    expect(trialSentence(30)).not.toContain('Google');
  });
});

describe('loadTrialDays', () => {
  it('is the days the API says', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    await expect(loadTrialDays()).resolves.toBe(30);
  });

  it('is 0 when the API says 0', async () => {
    billingPlan.mockResolvedValue({ trialDays: 0 });
    await expect(loadTrialDays()).resolves.toBe(0);
  });

  it('is 0, and never an error, when the request fails', async () => {
    for (const failure of [problem, new TypeError('network down'), new Error('boom')]) {
      billingPlan.mockRejectedValueOnce(failure);
      await expect(loadTrialDays()).resolves.toBe(0);
    }
  });

  it('is 0 when the request cannot even be made', async () => {
    billingPlan.mockImplementationOnce(() => {
      throw new Error('thrown, not rejected');
    });
    await expect(loadTrialDays()).resolves.toBe(0);
  });

  it.each([
    ['a string', { trialDays: '30' }],
    ['a fraction', { trialDays: 30.5 }],
    ['a negative', { trialDays: -30 }],
    ['no field', {}],
    ['no body', null],
    ['an empty answer', undefined],
  ])('is 0 for %s: it claims only what it can read', async (_name, body) => {
    billingPlan.mockResolvedValue(body as unknown as BillingPlan);
    await expect(loadTrialDays()).resolves.toBe(0);
  });

  it('shares ONE request among callers that ask while it is out', async () => {
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const first = loadTrialDays();
    const second = loadTrialDays();
    pending.resolve({ trialDays: 30 });
    await expect(Promise.all([first, second])).resolves.toEqual([30, 30]);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('keeps nothing after it settles: the next ask goes to the API again', async () => {
    // So a tab left open across a deploy that turned the trial off stops saying it.
    billingPlan.mockResolvedValueOnce({ trialDays: 30 });
    await expect(loadTrialDays()).resolves.toBe(30);
    billingPlan.mockResolvedValueOnce({ trialDays: 0 });
    await expect(loadTrialDays()).resolves.toBe(0);
    expect(billingPlan).toHaveBeenCalledTimes(2);
  });
});

describe('usePlanTrial', () => {
  function Probe({
    initial,
    plan,
  }: {
    initial?: number;
    plan?: Promise<number>;
  }): ReactElement {
    return <output data-testid="days">{usePlanTrial(initial, plan)}</output>;
  }
  const days = (el: HTMLElement) => el.querySelector('[data-testid="days"]')!.textContent;

  it('is 0 until the API has answered, then its answer', async () => {
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    await act(async () => {
      root!.render(<Probe />);
    });
    expect(days(el)).toBe('0');
    await act(async () => {
      pending.resolve({ trialDays: 30 });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(days(el)).toBe('30');
  });

  it('is 0 when the request fails', async () => {
    billingPlan.mockRejectedValue(problem);
    const el = await mount(<Probe />);
    expect(days(el)).toBe('0');
  });

  it('is what a page told it, and a hook that was told does NOT ask: one request, the page’s', async () => {
    billingPlan.mockResolvedValue({ trialDays: 0 });
    const el = await mount(<Probe initial={14} />);
    expect(days(el)).toBe('14');
    expect(billingPlan).not.toHaveBeenCalled();
  });

  it('told 0 is told "no trial", and does not ask either', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<Probe initial={0} />);
    expect(days(el)).toBe('0');
    expect(billingPlan).not.toHaveBeenCalled();
  });

  it('is asked only when nobody told it, and follows a value that arrives later', async () => {
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    await act(async () => {
      root!.render(<Probe />);
    });
    expect(billingPlan).toHaveBeenCalledTimes(1);
    // The page's own answer arrives while the card's request is still out: it wins, at once.
    await act(async () => {
      root!.render(<Probe initial={7} />);
    });
    expect(days(el)).toBe('7');
    // And the request still out cannot take it back.
    await act(async () => {
      pending.resolve({ trialDays: 30 });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(days(el)).toBe('7');
  });

  it('untold but handed the page’s own promise, it awaits THAT and never asks: one request, the page’s', async () => {
    // The page asked early and gave up waiting; the card is drawn untold, and the request is
    // still out. The card reuses it, whether or not it has settled by the time the card mounts.
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const pages = loadTrialDays();
    const el = await mount(<Probe plan={pages} />);
    expect(days(el)).toBe('0');
    await act(async () => {
      pending.resolve({ trialDays: 30 });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(days(el)).toBe('30');
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('untold, handed a promise that settled BEFORE it mounted, it still shows the answer without asking', async () => {
    // The window a plain "ask again" would leave: the answer came, the request is no longer out,
    // and a second request would go. The promise carries the answer, so nothing is asked.
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const pages = loadTrialDays();
    await pages;
    await new Promise((resolve) => setTimeout(resolve, 0));
    const el = await mount(<Probe plan={pages} />);
    expect(days(el)).toBe('30');
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('told, it ignores the promise it was handed: the page’s answer is the answer', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<Probe initial={7} plan={loadTrialDays()} />);
    expect(days(el)).toBe('7');
    // The one request is the page's; the card asked for nothing of its own.
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('does nothing with an answer that arrives after the page is gone', async () => {
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      await mount(<Probe />);
      act(() => root!.unmount());
      root = null;
      await act(async () => {
        pending.resolve({ trialDays: 30 });
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(error).not.toHaveBeenCalled();
    } finally {
      error.mockRestore();
    }
  });
});

describe('the pricing card asks the API, and says a trial only for a positive answer', () => {
  it('says it once the API has said 30 days — and asks once', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<PricingCard />);
    expect(el.textContent).toContain(SENTENCE_30_SIGNED_OUT);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('says it on the signed-in card too, for an agency that can still have it', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<PricingCard signedIn />);
    expect(el.textContent).toContain(SENTENCE_30);
  });

  it('does not on the signed-in card of an agency that cannot', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<PricingCard signedIn trialEligible={false} />);
    expect(el.textContent).not.toContain(PROBE);
  });

  it('says nothing while the request is out', async () => {
    billingPlan.mockReturnValue(deferred<BillingPlan>().promise);
    const el = await mount(<PricingCard />);
    expect(el.textContent).not.toMatch(/trial|after you subscribe/i);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('says nothing when the API says there is no trial', async () => {
    billingPlan.mockResolvedValue({ trialDays: 0 });
    const el = await mount(<PricingCard />);
    expect(el.textContent).not.toMatch(/trial|after you subscribe/i);
  });

  it('says nothing when the request fails, and the rest of the card is whole', async () => {
    billingPlan.mockRejectedValue(problem);
    const el = await mount(<PricingCard />);
    expect(el.textContent).not.toMatch(/trial|after you subscribe/i);
    expect(el.textContent).toContain('$150');
    expect([...el.querySelectorAll('button')].map((b) => b.textContent)).toContain('Get started');
  });

  it('told 30 by its page, it says so at once and does not ask: no second request', async () => {
    billingPlan.mockResolvedValue({ trialDays: 0 });
    const el = await mount(<PricingCard initialTrialDays={30} />);
    expect(el.textContent).toContain(SENTENCE_30_SIGNED_OUT);
    expect(billingPlan).not.toHaveBeenCalled();
  });

  it('told 0 by its page, it says nothing and does not ask either', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<PricingCard initialTrialDays={0} />);
    expect(el.textContent).not.toContain(PROBE);
    expect(billingPlan).not.toHaveBeenCalled();
  });

  it('drawn untold with its page’s promise, it shows the sentence when that answers: still one request', async () => {
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const pages = loadTrialDays();
    const el = await mount(<PricingCard trialPlan={pages} />);
    expect(el.textContent).not.toContain(PROBE);
    await act(async () => {
      pending.resolve({ trialDays: 30 });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(el.textContent).toContain(SENTENCE_30_SIGNED_OUT);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('follows the API’s number, not a number of its own', async () => {
    billingPlan.mockResolvedValue({ trialDays: 14 });
    const el = await mount(<PricingCard />);
    expect(el.textContent).toContain(
      'The first 14 days after you subscribe are free, if you subscribe from an account signed in with Google.',
    );
    expect(el.textContent).toContain('charged on day\u00a015');
    // The share link's 30 days (launch checklist 3.86) is not the trial's number.
    expect(el.textContent).toContain('A shared link works for 30 days');
    expect((el.textContent ?? '').replace('A shared link works for 30 days', '')).not.toContain('30');
  });
});

describe('the landing page says the trial from the same single source', () => {
  it('asks the API ONCE for the whole page, and says the trial once, in the pricing section', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<LandingView animate={false} />);
    expect(billingPlan).toHaveBeenCalledTimes(1);
    const text = el.textContent ?? '';
    expect(text).toContain(SENTENCE_30_SIGNED_OUT);
    expect(text.split(PROBE).length - 1).toBe(1);
    expect(text.indexOf(SENTENCE_30_SIGNED_OUT)).toBeGreaterThan(text.indexOf('One plan. $150 a month.'));
  });

  it('says nothing about a trial when the API says 0', async () => {
    billingPlan.mockResolvedValue({ trialDays: 0 });
    const el = await mount(<LandingView animate={false} />);
    expect(el.textContent).not.toMatch(/trial|after you subscribe/i);
  });

  it('says nothing about a trial when the request fails, and the page is whole', async () => {
    billingPlan.mockRejectedValue(new TypeError('network down'));
    const el = await mount(<LandingView animate={false} />);
    expect(el.textContent).not.toMatch(/trial|after you subscribe/i);
    expect(el.textContent).toContain('One plan. $150 a month.');
    expect(el.textContent).toContain('$150/month after that.');
  });
});

describe('within — the wait starts at the draw point, on a promise asked earlier', () => {
  // Fake timers for this block only: the wait is the whole subject, and nothing here mounts.
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('names its waits: half a second for the landing page, a second and a half for Settings', () => {
    // The landing page's card sits far below the fold, so the page is not worth holding for it;
    // Settings' plan card is one screen in, and the page is already waiting on three calls.
    expect(LANDING_PLAN_WAIT_MS).toBe(500);
    expect(SETTINGS_PLAN_WAIT_MS).toBe(1500);
    expect(LANDING_PLAN_WAIT_MS).toBeLessThan(SETTINGS_PLAN_WAIT_MS);
  });

  it('is the API’s answer when it comes within the wait, and leaves no timer behind', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    await expect(within(loadTrialDays(), LANDING_PLAN_WAIT_MS)).resolves.toBe(30);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('is 0, not undefined, when the request fails: a failure is an answer, not a wait', async () => {
    billingPlan.mockRejectedValue(problem);
    await expect(within(loadTrialDays(), SETTINGS_PLAN_WAIT_MS)).resolves.toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a promise that has ALREADY settled wins at once, however long ago and however short the wait', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const asked = loadTrialDays();
    // Long since answered: far longer than either wait.
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(within(asked, 0)).resolves.toBe(30);
    await expect(within(asked, 1)).resolves.toBe(30);
    await expect(within(asked, LANDING_PLAN_WAIT_MS)).resolves.toBe(30);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('counts the wait from THIS call, not from when the promise was asked', async () => {
    // The page asked at the top and then spent two seconds on its session check. The wait that
    // starts now is a whole one, not what was left of one that started two seconds ago.
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const asked = loadTrialDays();
    await vi.advanceTimersByTimeAsync(2000);
    let answer: number | undefined | 'still waiting' = 'still waiting';
    void within(asked, LANDING_PLAN_WAIT_MS).then((days) => {
      answer = days;
    });
    await vi.advanceTimersByTimeAsync(LANDING_PLAN_WAIT_MS - 1);
    expect(answer).toBe('still waiting');
    await vi.advanceTimersByTimeAsync(1);
    expect(answer).toBeUndefined();
  });

  it('an answer that comes during the wait ends it at once, and is the answer', async () => {
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    let answer: number | undefined | 'still waiting' = 'still waiting';
    void within(loadTrialDays(), SETTINGS_PLAN_WAIT_MS).then((days) => {
      answer = days;
    });
    await vi.advanceTimersByTimeAsync(600);
    expect(answer).toBe('still waiting');
    pending.resolve({ trialDays: 14 });
    await vi.advanceTimersByTimeAsync(0);
    expect(answer).toBe(14);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('gives up on the wait, not on the promise: it still answers the card that was handed it', async () => {
    const pending = deferred<BillingPlan>();
    billingPlan.mockReturnValue(pending.promise);
    const asked = loadTrialDays();
    const waited = within(asked, LANDING_PLAN_WAIT_MS);
    await vi.advanceTimersByTimeAsync(LANDING_PLAN_WAIT_MS);
    await expect(waited).resolves.toBeUndefined();

    pending.resolve({ trialDays: 30 });
    await expect(asked).resolves.toBe(30);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });
});

describe('the trial paragraph is not a live region — it is read where it is, not announced', () => {
  // It appears after a request, which is what a live region is for, and what this is not: nothing
  // about it is urgent or changed by the person. If it sat inside `aria-live`, `role="status"`
  // or `role="alert"`, a screen reader would announce a price paragraph the moment it arrived.
  const LIVE = '[aria-live], [role="status"], [role="alert"]';

  function trialParagraph(root: ParentNode): HTMLElement {
    const found = [...root.querySelectorAll('p')].filter((p) => p.textContent?.includes(PROBE));
    expect(found, 'exactly one paragraph carries the trial sentence').toHaveLength(1);
    return found[0]!;
  }

  it('on the landing page', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(<LandingView animate={false} />);
    const paragraph = trialParagraph(el);
    expect(paragraph.textContent).toBe(SENTENCE_30_SIGNED_OUT);
    expect(paragraph.closest(LIVE)).toBeNull();
    expect(paragraph.querySelector(LIVE)).toBeNull();
  });

  it('on the plan card in Settings', async () => {
    billingPlan.mockResolvedValue({ trialDays: 30 });
    const el = await mount(
      <BillingPanel agencyId="agcy_01TEST" role="owner" billing={noSubscriptionTrialOffered} />,
    );
    const paragraph = trialParagraph(el);
    expect(paragraph.textContent).toBe(SENTENCE_30);
    expect(paragraph.closest(LIVE)).toBeNull();
  });

  it('on the Settings screen as a whole, whichever way the page told it', async () => {
    const el = await mount(
      <SettingsView
        state={{
          kind: 'ready',
          me: ownerMe,
          seats: seatsWithPending,
          seatsError: null,
          billing: noSubscriptionTrialOffered,
          billingError: null,
          trialDays: 30,
        }}
        onChanged={() => {}}
      />,
    );
    const paragraph = trialParagraph(el);
    expect(paragraph.textContent).toBe(SENTENCE_30);
    expect(paragraph.closest(LIVE)).toBeNull();
  });
});

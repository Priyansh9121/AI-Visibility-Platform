// @vitest-environment jsdom

/**
 * The Settings route's early call for the free trial's length — round two, 2026-10-04.
 *
 * The plan card in Billing says how long the trial is, and the length is the API's
 * (`GET /billing/plan`). Asked for when the card mounts, the sentence arrived a moment after
 * the card and pushed everything under it down a line. So the route asks at the top of its
 * loader, beside the first call (`me`), waits for the answer WITH the other loads (and no
 * longer than a second and a half from when it asked), and hands it down as `trialDays`:
 * `SettingsView` to `BillingPanel` to `PricingCard`, which is then drawn with its sentence and
 * does not ask.
 *
 * The real route, the real screen, the API mocked at its edge. The pricing card is wrapped in a
 * recorder, because "drawn with its sentence" is about the FIRST render, which the DOM left at
 * the end of a test cannot tell from one that got it a moment later.
 *
 * Round three: the wait is counted from the DRAW POINT (when the other loads are done), not from
 * when the plan was asked, so the three calls being slower than the wait cannot make the page
 * throw away an answer that already came; a card drawn untold awaits the page's own promise
 * instead of asking again; and a RELOAD (a seat change) whose plan call is slow keeps the
 * previous answer, so the sentence never blinks off and on.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { BillingPlan, BillingStatus, SeatList } from '@avp/shared-types';
import { ownerMe, seatsWithPending } from '@/lib/settings/__fixtures__/seats';
import {
  canceledSubscription,
  noSubscription,
  noSubscriptionTrialOffered,
} from '@/lib/settings/__fixtures__/billing';

const me = vi.fn();
const seats = vi.fn<() => Promise<SeatList>>();
const billing = vi.fn<() => Promise<BillingStatus>>();
const billingPlan = vi.fn<() => Promise<BillingPlan>>();
const seen = vi.hoisted(() => ({
  card: [] as Array<number | undefined>,
  // The promise the route handed the card (its own request for the plan), render by render.
  cardPlan: [] as Array<Promise<number> | undefined>,
  cardMounts: { count: 0 },
}));
// The route hands `SettingsView` its `onChanged` (the reload a seat change triggers): kept, so a
// test can press it without driving the seat panel.
const view = vi.hoisted(() => ({
  onChanged: undefined as undefined | (() => void | Promise<void>),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      me: () => me(),
      seats: () => seats(),
      billing: () => billing(),
      billingPlan: () => billingPlan(),
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

vi.mock('@/components/settings/SettingsView', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/components/settings/SettingsView')>();
  return {
    ...real,
    SettingsView: (props: Parameters<typeof real.SettingsView>[0]) => {
      view.onChanged = props.onChanged;
      return <real.SettingsView {...props} />;
    },
  };
});

// Imported after the mocks so the route sees them.
const { default: SettingsRoute } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SENTENCE =
  'The first 30 days after you subscribe are free. You enter your card at checkout, and it is charged on day\u00a031 unless you cancel first. One free trial per agency.';
const PROBE = 'after you subscribe';

let root: Root | null = null;
const letGo: Array<() => void> = [];

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

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

async function arrive(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(<SettingsRoute />);
  });
  await flush();
  return el;
}

beforeEach(() => {
  for (const mock of [me, seats, billing, billingPlan]) mock.mockReset();
  me.mockResolvedValue(ownerMe);
  seats.mockResolvedValue(seatsWithPending);
  billing.mockResolvedValue(noSubscriptionTrialOffered);
  billingPlan.mockResolvedValue({ trialDays: 30 });
  seen.card.length = 0;
  seen.cardPlan.length = 0;
  seen.cardMounts.count = 0;
});

afterEach(async () => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  // A timeline test that failed part-way leaves answers waiting on a clock about to be thrown
  // away. Let them come, or the request they belong to is never settled and the next test is
  // handed it (the loader shares a request that is out).
  if (vi.isFakeTimers()) await vi.runAllTimersAsync();
  vi.useRealTimers();
  for (const release of letGo.splice(0)) release();
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe('the plan is asked for beside the first call, not after the loads', () => {
  it('has been asked for before `me` has answered', async () => {
    let signIn!: (value: unknown) => void;
    me.mockReturnValue(new Promise((resolve) => (signIn = resolve)));
    root = createRoot(document.body.appendChild(document.createElement('div')));
    await act(async () => {
      root!.render(<SettingsRoute />);
    });
    expect(me).toHaveBeenCalledTimes(1);
    expect(billingPlan).toHaveBeenCalledTimes(1);
    // Nothing else has been asked yet: the seats and billing wait for `me`, the plan did not.
    expect(seats).not.toHaveBeenCalled();
    expect(billing).not.toHaveBeenCalled();
    await act(async () => {
      signIn(ownerMe);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  });
});

describe('the plan answers while the page loads: the card is drawn once, with its sentence', () => {
  it('first render, first mount, one request', async () => {
    const el = await arrive();
    expect(seen.card[0]).toBe(30);
    expect(new Set(seen.card)).toEqual(new Set([30]));
    expect(seen.cardMounts.count).toBe(1);
    expect(el.textContent).toContain(SENTENCE);
    // The page asked once; the card, told the answer, did not ask again.
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('the plan fails: the card is drawn without a sentence, and nobody asks again', async () => {
    billingPlan.mockRejectedValue(new TypeError('network down'));
    const el = await arrive();
    expect(seen.card[0]).toBe(0);
    expect(seen.cardMounts.count).toBe(1);
    expect(el.textContent).not.toContain(PROBE);
    // The rest of the card is there: a failed plan costs a sentence and nothing else.
    expect(el.textContent).toContain('$150');
    expect([...el.querySelectorAll('button')].map((b) => b.textContent)).toContain('Subscribe');
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('the plan says no trial: no sentence', async () => {
    billingPlan.mockResolvedValue({ trialDays: 0 });
    const el = await arrive();
    expect(seen.card[0]).toBe(0);
    expect(el.textContent).not.toContain(PROBE);
  });
});

describe('the sentence is for an agency the billing response says would get the trial', () => {
  it('an agency with no subscription whose record says no is offered the plan and no sentence', async () => {
    billing.mockResolvedValue(noSubscription);
    const el = await arrive();
    expect(el.textContent).not.toContain(PROBE);
    expect([...el.querySelectorAll('button')].map((b) => b.textContent)).toContain('Subscribe');
    // The answer was still asked for once, in the background, whatever the billing says.
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('a cancelled agency is offered the plan and no sentence', async () => {
    billing.mockResolvedValue(canceledSubscription);
    const el = await arrive();
    expect(el.textContent).not.toContain(PROBE);
    expect([...el.querySelectorAll('button')].map((b) => b.textContent)).toContain('Subscribe');
  });

  it('a member, who may not read billing, has no card and loses nothing else', async () => {
    billing.mockRejectedValue(
      new ApiProblem({
        type: '/problems/forbidden',
        title: 'Forbidden',
        status: 403,
        // What the API says to a member (`require_role`); the page shows its own sentence, below.
        detail: 'This action requires one of: admin, owner. Your role is member.',
        instance: '/api/v1/agencies/x/billing',
      }),
    );
    const el = await arrive();
    expect(el.textContent).toContain('Only an owner or an admin can see billing.');
    // Not "and change": an admin sees billing and only an owner changes it (launch checklist 3.130).
    expect(el.textContent).not.toContain('see and change billing');
    expect(el.textContent).not.toContain('Your role is member');
    expect(el.textContent).not.toContain(PROBE);
    expect(seen.cardMounts.count).toBe(0);
  });
});

/**
 * A call that answers `after` ms later, on whatever clock is running; at once for 0 (a zero-delay
 * timer would answer at the next tick of the clock, which makes three of them in a row land a
 * millisecond or two late and every step of a timeline after them off by that much).
 */
const later = <T,>(value: T, after: number): Promise<T> =>
  after === 0
    ? Promise.resolve(value)
    : new Promise((resolve) => setTimeout(() => resolve(value), after));

/**
 * The Settings route on a fake clock, for the tests that are about WHEN things happen: `me`, the
 * roster and billing each take `each` ms (they run one after the other), and the plan `planAfter`
 * ms from when it is asked. Returns the element and a `step` that moves the clock on and lets
 * everything it sets off settle.
 */
async function onAFakeClock(each: number, planAfter: number | 'never') {
  vi.useFakeTimers();
  me.mockImplementation(() => later(ownerMe, each));
  seats.mockImplementation(() => later(seatsWithPending, each));
  billing.mockImplementation(() => later(noSubscriptionTrialOffered, each));
  if (planAfter === 'never') {
    pendingPlan();
  } else {
    billingPlan.mockImplementation(() => later({ trialDays: 30 }, planAfter));
  }
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(<SettingsRoute />);
    await vi.advanceTimersByTimeAsync(0);
  });
  const step = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  return { el, step };
}

describe('a slow plan call cannot hold the page, and a slow page cannot lose the answer', () => {
  it('the three loads take longer than the old wait and the plan comes first: drawn TOLD, on ONE request', async () => {
    // Round three (N1). Three calls of 600 ms each (1800 ms in all); the plan answers at 1700 ms.
    // The wait used to be counted from the call, so it ran out at 1500 ms, the page was drawn at
    // 1800 ms told nothing, the answer was thrown away, and the card asked again.
    const { el, step } = await onAFakeClock(600, 1700);
    await step(1799);
    expect(seen.card).toEqual([]);
    expect(el.textContent).not.toContain('What you pay');
    await step(1); // 1800: the last of the three loads is in, and the plan answered at 1700
    expect(el.textContent).toContain('What you pay');
    expect(seen.card[0]).toBe(30);
    expect(new Set(seen.card)).toEqual(new Set([30]));
    expect(seen.cardMounts.count).toBe(1);
    expect(el.textContent).toContain(SENTENCE);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('the plan is slower than the wait: drawn untold at the draw point plus a second and a half, the sentence later, still one request', async () => {
    // The three loads are quick (0 ms), so the draw point is the start, and the wait runs from
    // there. The plan answers at 4000 ms.
    const { el, step } = await onAFakeClock(0, 4000);
    await step(1499);
    expect(seen.card).toEqual([]);
    expect(el.textContent).not.toContain('What you pay');
    await step(1); // 1500: the wait is over
    expect(el.textContent).toContain('What you pay');
    expect(seen.card[0]).toBeUndefined();
    expect(el.textContent).not.toContain(PROBE);
    expect(seen.cardMounts.count).toBe(1);
    // The card was drawn untold and awaits the SAME promise the page asked with: it was handed
    // it, and that promise is the one the plan's answer arrives on.
    expect(seen.cardPlan[0]).toBeDefined();
    await step(2499); // 3999
    expect(el.textContent).not.toContain(PROBE);
    await step(1); // 4000
    expect(el.textContent).toContain(SENTENCE);
    await expect(seen.cardPlan[0]).resolves.toBe(30);
    expect(seen.cardMounts.count).toBe(1);
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('Settings waits a second and a half at its draw point for a plan that has not answered, and no longer', async () => {
    const { el, step } = await onAFakeClock(0, 'never');
    await step(1499);
    expect(el.textContent).not.toContain('What you pay');
    await step(1);
    expect(el.textContent).toContain('What you pay');
    expect(seen.card[0]).toBeUndefined();
    expect(billingPlan).toHaveBeenCalledTimes(1);
  });

  it('a plan that answers before the other loads are done costs the page no wait at all', async () => {
    // `me`, the roster and billing take a while; the plan is back long before they are.
    seats.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(seatsWithPending), 20)),
    );
    const started = Date.now();
    const el = await arrive();
    // The roster is still on its way (20 ms), and the page is loading, not waiting on the plan.
    expect(el.textContent).toContain('Loading your settings…');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 80));
    });
    // Drawn as soon as the slowest of the other loads came in: far inside the plan's own wait.
    expect(Date.now() - started).toBeLessThan(1000);
    expect(el.textContent).toContain('What you pay');
    expect(el.textContent).toContain(SENTENCE);
    expect(seen.card[0]).toBe(30);
  });
});

describe('a reload whose plan call is slow keeps the previous answer — round three', () => {
  // A seat change re-runs the loader (`onChanged`). The card is already on screen, told 30. If the
  // new plan call is slow, the page used to draw the reload told nothing, and the card (asking
  // for itself) lost its sentence and got it back when the answer came: a blink. Now the
  // reload keeps what it was told before.
  it('the sentence never blinks off and on', async () => {
    const el = await arrive();
    expect(el.textContent).toContain(SENTENCE);
    expect(seen.card[0]).toBe(30);
    expect(view.onChanged, 'the route handed the screen its reload').toBeDefined();
    const rendersBefore = seen.card.length;

    // The reload: everything else quick, the plan slower than the wait.
    vi.useFakeTimers();
    pendingPlan();
    await act(async () => {
      void view.onChanged!();
      await vi.advanceTimersByTimeAsync(0);
    });
    // While it loads, the screen is as it was: the sentence is there.
    expect(el.textContent).toContain(SENTENCE);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    // The reload is drawn (the wait ran out): the sentence was never gone.
    expect(el.textContent).toContain(SENTENCE);
    const renders = seen.card.slice(rendersBefore);
    expect(renders.length).toBeGreaterThan(0);
    expect(new Set(renders)).toEqual(new Set([30]));
    // And the same card: not unmounted and mounted again.
    expect(seen.cardMounts.count).toBe(1);
    // Two requests in all: the first load's, and the reload's. The card asked for nothing.
    expect(billingPlan).toHaveBeenCalledTimes(2);
  });

  it('a reload whose plan call answers in time shows the new answer', async () => {
    const el = await arrive();
    expect(el.textContent).toContain(SENTENCE);
    billingPlan.mockResolvedValue({ trialDays: 14 });
    await act(async () => {
      await view.onChanged!();
    });
    await flush();
    expect(el.textContent).toContain('The first 14 days after you subscribe are free.');
    expect(el.textContent).not.toContain(SENTENCE);
    expect(seen.cardMounts.count).toBe(1);
  });

  it('a first load has nothing to keep: untold stays untold', async () => {
    // Nothing was said before, so there is no sentence to hold on to.
    const { el, step } = await onAFakeClock(0, 'never');
    await step(1500);
    expect(el.textContent).toContain('What you pay');
    expect(el.textContent).not.toContain(PROBE);
    expect(seen.card[0]).toBeUndefined();
  });
});

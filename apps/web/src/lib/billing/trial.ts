'use client';

/**
 * The free trial, as the public pages say it — 2026-10-04.
 *
 * The founder decided a free trial at checkout: the card entered there, charged on
 * day 31 unless the agency cancels. Its length is the API's `STRIPE_TRIAL_DAYS`, and
 * 0 there is no trial. This module is how a public page learns it, from
 * `GET /billing/plan` (`api.billingPlan`), so the number is stated in one place and
 * no page carries a copy of it. A copy would be what a page says after the founder
 * turns the trial off, which is the one thing these pages must never do.
 *
 * WHAT A PAGE MAY CLAIM, AND WHEN
 * -------------------------------
 * A sentence about a trial appears only for a positive whole number of days, and
 * every other state is NO SENTENCE AT ALL, never a hedged one: while the request is
 * out (the page renders without it first), when it fails (the API down, a proxy
 * error, a body that is not what was promised), and when the API says 0. Never a
 * trial that may not exist. `loadTrialDays` answers 0 for every failure and never
 * rejects, so no caller has an error to handle and none can forget to.
 *
 * ONE REQUEST, NOT ONE PER CARD. Two components that ask in the same moment share
 * the request that is already out. Nothing is kept after it settles: the next mount
 * asks again, so a tab left open across a deploy that turned the trial off stops
 * saying it at the next page, and the endpoint is `no-store` for the same reason.
 *
 * ASKED EARLY, SO THE CARD IS NOT A LINE TALLER A MOMENT LATER (round two). A card that
 * asks when it mounts draws without its sentence and then grows by one when the answer
 * lands, which moves everything under it. So the page loaders (`app/page.tsx`,
 * `app/settings/page.tsx`) call `loadTrialDays` at the top, beside their other first calls,
 * KEEP that promise, and pass the answer down as `initialTrialDays`: the card is drawn with its
 * sentence, and a card that was TOLD does not ask.
 *
 * THE WAIT STARTS AT THE DRAW POINT (round three). The page does not wait for the plan from
 * when it asked: it gets on with its other calls, and when it is ready to draw it races the
 * promise it kept against a FRESH timer (`within`). An answer that has already come wins at
 * once, however long ago; one that has not is waited for only a little (`LANDING_PLAN_WAIT_MS`,
 * `SETTINGS_PLAN_WAIT_MS`), so a slow plan call cannot hold a page. Counted from the call, a
 * session check slower than the wait made the page draw untold and throw away an answer that
 * had come.
 *
 * ONE REQUEST FOR THE WHOLE PAGE. When the wait runs out the page is drawn without a sentence
 * and hands the card the SAME promise (`usePlanTrial`'s second argument): the card awaits it
 * rather than asking, so the request is never made a second time, whether the answer comes
 * before the card mounts, after it, or a moment either side of it. A card handed nothing (one
 * mounted anywhere else) asks for itself, sharing a request that is already out.
 *
 * It describes the offer to a NEW agency, and says so ("One free trial per agency").
 * Whether a particular agency still gets it is its own history, which checkout
 * decides; Settings shows the offer only where the billing response says this agency
 * would get it (`trialEligible`, passed to `PricingCard`).
 */

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

/** U+00A0. Written as an escape, so it can be seen in the source and cannot be reformatted. */
const NO_BREAK_SPACE = '\u00a0';

/**
 * A number of trial days, or 0 for "no trial": anything that is not a positive whole
 * number (a string, a fraction, NaN, a negative, null, a missing field) is 0.
 */
export function wholeTrialDays(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : 0;
}

/**
 * What the pricing card says about the trial, for a positive number of days.
 *
 * Every clause is a fact about checkout. The first N days after you subscribe are free,
 * because the subscription is created with a trial of N days (`trial_period_days`), which
 * runs from the moment it is created, not from signing up or from opening a checkout; the
 * card is entered on Stripe's checkout page, because `payment_method_collection` is
 * `always`; it is charged when the trial ends, which is day N + 1 counting the day of
 * checkout as day 1, unless the agency cancels first; and the trial is given once per
 * agency. The card is "entered", not "taken": nothing is charged at checkout, and a card
 * number never reaches this service (the privacy page says so).
 *
 * A no-break space sits between "day" and its number, so the line never wraps between
 * them and leaves a bare "31" at the start of the next one.
 */
export function trialSentence(days: number): string {
  const first =
    days === 1
      ? 'The first day after you subscribe is free.'
      : `The first ${days} days after you subscribe are free.`;
  return (
    `${first} You enter your card at checkout, and it is charged on day${NO_BREAK_SPACE}${days + 1} ` +
    'unless you cancel first. One free trial per agency.'
  );
}

/**
 * What the pricing card says about the trial to someone who is NOT signed in — 2026-10-05.
 *
 * A trial needs a verified Google identity on the user who starts checkout (founder decision,
 * launch checklist 3.72: the free scan's rule), and the signed-out card cannot know whether the
 * visitor will have one. So it says the condition, in plain words, and nothing it cannot back:
 * the days after you subscribe are free if you subscribe from an account signed in with Google. The rest is
 * `trialSentence`'s, unchanged: the card is entered at checkout, charged on day N + 1 unless you
 * cancel first, one trial per agency. The signed-in card keeps `trialSentence`: Settings shows
 * it only where the API says THIS user's checkout would give the trial (`trialEligible`).
 */
export function trialSentenceSignedOut(days: number): string {
  const first =
    days === 1
      ? 'The first day after you subscribe is free, if you subscribe from an account signed in with Google.'
      : `The first ${days} days after you subscribe are free, if you subscribe from an account signed in with Google.`;
  return (
    `${first} You enter your card at checkout, and it is charged on day${NO_BREAK_SPACE}${days + 1} ` +
    'unless you cancel first. One free trial per agency.'
  );
}

let inflight: Promise<number> | null = null;

/**
 * The days of free trial the API offers a new agency; 0 for none, and 0 for any failure.
 * Never rejects. Callers that ask while a request is out share it.
 */
export function loadTrialDays(): Promise<number> {
  if (inflight !== null) return inflight;
  const asked: Promise<number> = Promise.resolve()
    .then(() => api.billingPlan())
    .then(
      (plan) => wholeTrialDays(plan?.trialDays),
      () => 0,
    );
  inflight = asked;
  void asked.then(() => {
    if (inflight === asked) inflight = null;
  });
  return asked;
}

/**
 * The longest the LANDING page waits for the plan, from its draw point. The pricing card sits far
 * below the fold, so the page is not worth holding for its sentence: half a second, and the card
 * awaits the page's own request after that.
 */
export const LANDING_PLAN_WAIT_MS = 500;

/**
 * The longest SETTINGS waits for the plan, from its draw point. Its plan card is one screen in and
 * the page is already waiting on three calls, so it is given longer than the landing page.
 */
export const SETTINGS_PLAN_WAIT_MS = 1500;

/**
 * A page's wait for a promise it asked with earlier: the answer if it is there within `waitMs` of
 * THIS call, `undefined` if it is not, meaning "not told" (the card then awaits the same promise).
 * The wait is counted from the call and not from when the promise was asked, and a promise that
 * has already settled wins at once, however long ago and however short `waitMs`. A failure inside
 * the wait is an answer (0, no trial: what `loadTrialDays` makes of one), not a wait. It gives up
 * on WAITING, not on the promise. Never rejects, and leaves no timer behind once it has an answer.
 */
export function within(
  plan: Promise<number>,
  waitMs: number,
): Promise<number | undefined> {
  return new Promise<number | undefined>((resolve) => {
    const timer = setTimeout(() => resolve(undefined), waitMs);
    void plan.then((days) => {
      clearTimeout(timer);
      resolve(days);
    });
  });
}

/**
 * The trial's length for a public page: 0 until the API has answered, and 0 for anything
 * but a positive whole number.
 *
 * `initial` is what the page already knows: given, it is the answer and the hook does NOT
 * ask (a page that asked early passes it down; so does a static render, which never runs
 * a request). Left out, the hook awaits `plan`, the page's own request, when it was handed
 * one (the page asked early and gave up waiting, so the answer is still to come, or came a
 * moment too late: either way it is on that promise), and asks for itself only when it was
 * handed nothing. It follows `initial` if that changes, and what it awaited cannot take a
 * given value back.
 */
export function usePlanTrial(initial?: number, plan?: Promise<number>): number {
  // What this hook got for itself, when nobody told it.
  const [asked, setAsked] = useState(0);
  const told = initial !== undefined;
  useEffect(() => {
    if (told) return;
    let live = true;
    void (plan ?? loadTrialDays()).then((answer) => {
      if (live) setAsked(answer);
    });
    return () => {
      live = false;
    };
  }, [told, plan]);
  return told ? wholeTrialDays(initial) : asked;
}

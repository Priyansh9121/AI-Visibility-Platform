'use client';

/**
 * The one plan, with a real price on it — Epic 9.15.
 *
 * WHY A PRICE APPEARS HERE AT ALL, WHEN EPIC 9.10 REFUSED TO PUBLISH ONE
 * ---------------------------------------------------------------------
 * `LandingView` shipped with a test named "advertises no price or plan,
 * because none is decided", and it was right at the time: north-star.md §5.3's
 * tiers were [HYPOTHESIS] and publishing one would have turned a working
 * assumption into a public promise.
 *
 * That changed by founder decision, not by drift. $29/month became a real,
 * published price backed by a real Stripe Price object (repriced to $160 on
 * 2026-09-22 and to $150 on 2026-10-04, both founder decisions — `PLAN_PRICE_USD`
 * below says what did and did not move), and §5.3 has been
 * updated to say so rather than left to contradict this page. The rest of Epic
 * 9.10's rule is untouched: no testimonial, no logo wall, no user count, no
 * invented scale. A price the founder decided to charge is a fact about us; a
 * customer quote we do not have is not.
 *
 * TWO STATES, BOTH REACHABLE
 * --------------------------
 * Signed out, the button is "Get started" and goes to sign-up — you cannot
 * subscribe an agency that does not exist yet. Signed in, it is "Subscribe" and
 * goes to Stripe Checkout. Those are two different intents and they get two
 * different destinations, the same argument the header makes for keeping "Log
 * in" separate from "Get started free".
 *
 * Both states are live call sites, not one real branch and one for the tests:
 * the landing page renders the signed-out card, and Settings renders the
 * signed-in card wherever the API will start a checkout: no subscription,
 * `canceled` and `incomplete_expired` (`canCheckout`, in `lib/billing/allowance`).
 *
 * The footnote under the button ("Signing up is free…") is the signed-out
 * card's alone. It is sign-up copy, and the signed-in card is for an agency
 * that has already signed up.
 *
 * WHAT THE CARD MAY CLAIM
 * -----------------------
 * Only capabilities this codebase ships today, the same bar `LandingView`'s
 * header comment sets for every sentence on the public page. Note in particular
 * what is NOT written here: "unlimited scans". Scan volume is genuinely not
 * metered yet — north-star.md §5.2 argues metering the scan is the right model
 * and §5.4 records that none of it is built — so the absence of a cap is an
 * absence, not a feature, and the card says exactly that.
 *
 * THE FREE TRIAL — 2026-10-04
 * ---------------------------
 * When the API says the plan has a trial (`GET /billing/plan`, `lib/billing/trial`),
 * the card says so, in one paragraph under the price: how many days after you subscribe
 * are free, that the card is entered at checkout and charged on the day after, unless the
 * agency cancels first, and that it is one trial per agency. The length is the API's, so
 * this file carries no copy of it. While the request is out, if it fails, and when the API
 * says 0, the card says NOTHING about a trial: never a trial that may not exist.
 *
 * The page that draws the card asks early and tells it (`initialTrialDays`), so the card is
 * drawn with its sentence and not a line taller a moment later; a card that was told does not
 * ask. A card the page could not tell in time awaits the page's own request (`trialPlan`), and
 * one that was handed nothing asks for itself when it mounts.
 *
 * Signed out, the card describes the offer to somebody who has not subscribed. Signed in,
 * in Settings, it describes it only where the billing response says this agency would get
 * it (`trialEligible`, which is the API's word: a trial is on and this agency has never had a
 * subscription): one that cancelled would be told of a trial checkout will not give it.
 *
 * The no-competitor-reference and no-borrowed-copy rules: derived from product-spec.md §3's core loop,
 * §5.4's pipeline and the seat model in `models/tenancy.py`. No competitor's
 * pricing page was opened, referenced or paraphrased while writing this.
 */

import type { JSX } from 'react';
import { Button, Card, CardBody } from '@avp/design-system';
import { trialSentence, usePlanTrial } from '@/lib/billing/trial';

/**
 * The published price, in whole dollars.
 *
 * A constant rather than a literal in the copy because it is asserted by test
 * and stated in two places on the page, and a price that disagrees with itself
 * across a screen is worse than no price. The authority for the NUMBER is the
 * Stripe Price object the checkout session is created against — this is the
 * page's copy of it, and the two are kept in step by hand today. If a second
 * price ever exists, this stops being tenable and the value has to come from
 * the API.
 *
 * Repriced $29 → $160 on 2026-09-22, again by founder decision. The number
 * moved here and in every test that pins it; the Stripe Price object did NOT
 * move with it that day. On 2026-09-23 a $160.00 Price (`price_1UIh3b…`) was
 * on the test-mode account and the local `STRIPE_PRICE_ID` was pointed at it;
 * the $29.00 Price (`price_1U9For…`) is still active, and Render's variable
 * still has to be changed by hand. That is exactly the "kept in step by hand"
 * deal the paragraph above describes. Build log, 2026-09-22 and 2026-09-23.
 *
 * Repriced $160 → $150 on 2026-10-04 (founder decision). The number moves here;
 * the Stripe side stays the founder's: a $160.00 test-mode Price has existed
 * since 2026-09-23, and a $150.00 one is for the founder to create in the
 * dashboard and name in `STRIPE_PRICE_ID` (locally and on Render). The two are
 * no longer kept in step by hand alone: the API states the same number in
 * `services/billing.py` (`PLAN_PRICE_USD`) and, when deployed, at start and every
 * hour, reads the configured Price and logs `billing.price_mismatch` when it is
 * not the plan; `planPrice.test.ts` fails if this constant and that one differ.
 */
export const PLAN_PRICE_USD = 150;

/**
 * Seats included, matching `Settings.default_seat_limit` (3) and
 * `Agency.seat_limit`'s default.
 *
 * Stated on the card rather than left implicit: an agency signing up already
 * gets three seats, so the plan is not granting them something they would
 * otherwise lack, and a card implying otherwise would be selling a fact.
 */
export const PLAN_SEATS = 3;

/**
 * Scans a month on the plan, and free scans for an agency's lifetime — the
 * API's `PAID_SCANS_PER_MONTH` and `FREE_SCAN_ALLOWANCE`
 * (`services/scan_allowance.py`), which are the authority and are what the
 * gate enforces.
 *
 * Copies, because a signed-out visitor has no billing response to read them
 * from. They used to be typed into this card's copy by hand; since the terms
 * page (2026-09-29) states them too, they are constants, and
 * `lib/legal/legalPages.test.tsx` reads the API's source and fails if either
 * copy drifts from it.
 */
export const PLAN_SCANS_PER_MONTH = 40;
export const PLAN_FREE_SCANS = 1;

export interface PricingCardProps {
  /** Signed-in visitors subscribe; signed-out visitors sign up first. */
  signedIn?: boolean | undefined;
  /** Signed-out action — the sign-up surface. */
  onGetStarted?: (() => void) | undefined;
  /** Signed-in action — create a Checkout Session and navigate to it. */
  onSubscribe?: (() => void | Promise<void>) | undefined;
  /** True while a checkout session is being created. */
  busy?: boolean | undefined;
  /**
   * Inert without claiming to be busy — 2026-10-01. Settings sets it while the
   * billing portal is opening: Subscribe must not look pressable and do
   * nothing, and it must not say "Opening checkout…" either, since checkout is
   * not what is opening. `busy` is for this card's own call. Signed in only.
   */
  disabled?: boolean | undefined;
  /** A failure from the checkout call, in a sentence. */
  error?: string | null | undefined;
  /**
   * Whether the free trial may be offered on THIS card at all: false for an agency that
   * has had a subscription, which checkout gives no trial to. Settings passes the billing
   * response's `trialEligible`; the public card is always offered it. Default true. Even
   * when true, nothing is said unless the API says the plan has a trial.
   */
  trialEligible?: boolean | undefined;
  /**
   * The trial's length, when the page already knows it: the page loaders ask the API early
   * (`loadTrialDays`), wait for it at their draw point (`within`) and pass the answer down, so the
   * card is drawn with its sentence and does not ask. 0 is "no trial", and also does not ask. Left
   * out, the card awaits `trialPlan` if it was handed one, and otherwise asks for itself when it
   * mounts. A static render never runs a request, so this is also how a test renders the card as
   * it is once the API has said the trial is this many days.
   */
  initialTrialDays?: number | undefined;
  /**
   * The page's own request for the plan, for a card drawn UNTOLD (no `initialTrialDays`): the page
   * asked early, gave up waiting for the answer, and hands the card the promise it asked with, so
   * the card awaits that instead of asking again. One request for the whole page, however slow
   * the plan is, and the sentence appears when it answers. Ignored when `initialTrialDays` is
   * given. Round three.
   */
  trialPlan?: Promise<number> | undefined;
}

export function PricingCard({
  signedIn = false,
  onGetStarted,
  onSubscribe,
  busy = false,
  disabled = false,
  error = null,
  trialEligible = true,
  initialTrialDays,
  trialPlan,
}: PricingCardProps): JSX.Element {
  // What the page told it, or what the page's own request answers, or (with neither) what it asks
  // for itself: 0 until the API has answered, and for every failure: no sentence then.
  const trialDays = usePlanTrial(initialTrialDays, trialPlan);
  return (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-col gap-6">
          <div>
            <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
              One plan
            </p>
            <p className="mt-3 flex items-baseline gap-2">
              <span className="font-display text-ed-xl leading-display tracking-display text-text-primary">
                ${PLAN_PRICE_USD}
              </span>
              <span className="text-ui-md text-text-secondary">per month</span>
            </p>
            <p className="mt-2 max-w-measure text-ui-base leading-prose text-text-secondary">
              {PLAN_SEATS} seats, billed monthly in US dollars. One plan, one price —
              there is no tier above this one to be upsold to, and no annual
              commitment to sign.
            </p>
            {trialEligible && trialDays > 0 && (
              // The terms of the free trial, beside the price they end in. Stronger than
              // the paragraph above on purpose: when the card is charged is the one thing
              // here nobody should miss.
              <p className="mt-3 max-w-measure text-ui-base leading-prose text-text-primary">
                {trialSentence(trialDays)}
              </p>
            )}
          </div>

          <ul className="flex flex-col gap-3">
            <Includes text={`${PLAN_SEATS} seats per agency.`} />
            <Includes text="Every prospect you want to scan, with industry and competitors detected for you rather than typed in." />
            <Includes text="Twenty intent-tagged questions per scan, put to four AI answer engines and read for facts." />
            <Includes text="The full narrative report — on screen, as a share link, and as a PDF." />
            <Includes text="Named fixes, ordered by what they would move and what they would cost you." />
          </ul>

          {/*
            The one number on this card the API also holds: forty is
            `PAID_SCANS_PER_MONTH` in `services/scan_allowance.py`, and the
            signed-in app reads it from the billing response. A signed-out
            visitor has no billing response, so it comes from
            `PLAN_SCANS_PER_MONTH` above, which a test holds to the API's.
          */}
          <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
            {PLAN_SCANS_PER_MONTH} scans a month. Viewing, sharing and exporting reports is never
            limited.
          </p>

          {error != null && (
            <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
              {error}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-4">
            {signedIn ? (
              <Button
                variant="primary"
                onClick={() => void onSubscribe?.()}
                disabled={busy || disabled}
              >
                {busy ? 'Opening checkout…' : 'Subscribe'}
              </Button>
            ) : (
              <Button variant="primary" onClick={onGetStarted}>
                Get started
              </Button>
            )}
            {/*
              The free scan needs a Google sign-in since 2026-09-29 — the
              one identity here somebody else has vouched for, and what
              keeps "free" from meaning "free per typed address".
              `services/scan_allowance.py` says why.

              Signed out only (2026-10-01). It is sign-up copy, written for
              somebody deciding whether to sign up. Signed in, the person has
              signed up, and may have scanned or subscribed before, so in
              Settings it was wrong for them. Signed in there is no footnote,
              and no other copy in its place.
            */}
            {!signedIn && (
              <p className="max-w-measure text-ui-sm text-text-tertiary">
                Signing up is free. Your first scan is free with a Google sign-in.
                Subscribe to keep scanning.
              </p>
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function Includes({ text }: { text: string }): JSX.Element {
  return (
    <li className="max-w-measure text-ui-base leading-prose text-text-secondary">
      {text}
    </li>
  );
}

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
 * That changed by founder decision, not by drift. $29/month is now a real,
 * published price backed by a real Stripe Price object, and §5.3 has been
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
 * signed-in card when an agency has no active subscription.
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
 * ip-safety.md #1, #5 and #8: derived from product-spec.md §3's core loop,
 * §5.4's pipeline and the seat model in `models/tenancy.py`. No competitor's
 * pricing page was opened, referenced or paraphrased while writing this.
 */

import type { JSX } from 'react';
import { Button, Card, CardBody } from '@avp/design-system';

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
 */
export const PLAN_PRICE_USD = 29;

/**
 * Seats included, matching `Settings.default_seat_limit` (3) and
 * `Agency.seat_limit`'s default.
 *
 * Stated on the card rather than left implicit: an agency signing up already
 * gets three seats, so the plan is not granting them something they would
 * otherwise lack, and a card implying otherwise would be selling a fact.
 */
export const PLAN_SEATS = 3;

export interface PricingCardProps {
  /** Signed-in visitors subscribe; signed-out visitors sign up first. */
  signedIn?: boolean | undefined;
  /** Signed-out action — the sign-up surface. */
  onGetStarted?: (() => void) | undefined;
  /** Signed-in action — create a Checkout Session and navigate to it. */
  onSubscribe?: (() => void | Promise<void>) | undefined;
  /** True while a checkout session is being created. */
  busy?: boolean | undefined;
  /** A failure from the checkout call, in a sentence. */
  error?: string | null | undefined;
}

export function PricingCard({
  signedIn = false,
  onGetStarted,
  onSubscribe,
  busy = false,
  error = null,
}: PricingCardProps): JSX.Element {
  return (
    <Card elevation="seated">
      <CardBody>
        <div className="flex flex-col gap-6">
          <div>
            <p className="text-ui-2xs uppercase tracking-caps text-text-tertiary">
              One plan
            </p>
            <p className="mt-3 flex items-baseline gap-2">
              <span className="font-editorial text-ed-xl leading-display tracking-display text-text-primary">
                ${PLAN_PRICE_USD}
              </span>
              <span className="text-ui-md text-text-secondary">per month</span>
            </p>
            <p className="mt-2 max-w-measure text-ui-base leading-prose text-text-secondary">
              {PLAN_SEATS} seats, billed monthly in US dollars. One plan, one price —
              there is no tier above this one to be upsold to, and no annual
              commitment to sign.
            </p>
          </div>

          <ul className="flex flex-col gap-3">
            <Includes text={`${PLAN_SEATS} seats — the same seat limit every agency already gets, so this plan is not selling you the seats you have.`} />
            <Includes text="Every prospect you want to scan, with industry and competitors detected for you rather than typed in." />
            <Includes text="Twenty-four intent-tagged questions per scan, put to AI answer engines and read for facts." />
            <Includes text="The full narrative report — on screen, as a share link, and as a PDF." />
            <Includes text="Named fixes, ordered by what they would move and what they would cost you." />
          </ul>

          <p className="max-w-measure text-ui-sm leading-prose text-text-tertiary">
            Scan volume is not metered today. That is an absence rather than a
            promise of unlimited use — usage limits are not built, and this card
            will say what they are when they exist.
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
                disabled={busy}
              >
                {busy ? 'Opening checkout…' : 'Subscribe'}
              </Button>
            ) : (
              <Button variant="primary" onClick={onGetStarted}>
                Get started
              </Button>
            )}
            <p className="max-w-measure text-ui-sm text-text-tertiary">
              Signing up is free and stays free. Nothing in the product is behind
              this plan today — subscribing is how you pay for it once it is
              worth paying for, not how you unlock it.
            </p>
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

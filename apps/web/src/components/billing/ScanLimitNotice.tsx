'use client';

/**
 * What a `402 scan-limit-reached` looks like on screen — 2026-09-28.
 *
 * Not an error: nothing failed, and an `ErrorState` would say something had.
 * The free case is a plain sentence and a **Subscribe** button that goes to
 * the same Checkout the pricing card uses. The monthly case says when the
 * allowance resets and offers nothing else, because there is no tier above
 * this one to upgrade to — a button that led nowhere would be worse than
 * none.
 *
 * The verify case (2026-09-29): the free scan needs a Google sign-in, and
 * this user has none linked. Two ways on, in the order the sentence gives
 * them — **Continue with Google**, the same anchor to `/auth/google/start`
 * the sign-in page carries (on the same address it links this account, and
 * works without signing out first), and **Subscribe**. Subscribe is the
 * secondary button here, so the free way through is not outshouted by the
 * paid one. If the Google step fails, the person lands back on `/` still
 * signed in, and the front door says why (2026-09-29, `app/page.tsx`).
 *
 * A refused Subscribe (2026-10-01). The free allowance also applies to an
 * agency whose subscription is `past_due`, `unpaid`, `paused` or `incomplete`,
 * so it can land here, and the API answers a Checkout for it with a 409:
 * Stripe still holds that subscription open. The API's sentence says to manage
 * it from Manage billing in Settings, and this card has no billing controls of
 * its own, so after that refusal it shows the sentence and a link to the
 * Billing section of Settings, and Subscribe steps back from primary — another
 * press can only be refused again. Any other failure is the sentence alone, and
 * Subscribe stays as it was. That includes `checkout-in-progress`: it is a 409
 * too, but it says to try again in a moment (`lib/billing/subscriptionRefusal`).
 */

import { useEffect, useState } from 'react';
import type { JSX } from 'react';
import { Button, Card, CardBody, GoogleSignInButton } from '@avp/design-system';
import { api, ApiProblem, GOOGLE_SIGN_IN_URL } from '@/lib/api';
import type { ScanLimitProblem } from '@/lib/billing/allowance';
import { isSubscriptionRefusal } from '@/lib/billing/subscriptionRefusal';
import { formatDay } from '@/lib/dates';
import { BILLING_SETTINGS_HREF } from '@/lib/settings/sections';

export interface ScanLimitNoticeProps {
  problem: ScanLimitProblem;
  /** Whose checkout to open. */
  agencyId: string;
  /** Test seam: what to do with the Checkout URL. Defaults to navigating. */
  navigate?: ((url: string) => void) | undefined;
}

export function ScanLimitNotice({ problem, agencyId, navigate }: ScanLimitNoticeProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  // What the last Subscribe said, and whether that was the API saying this
  // agency has a subscription. One state, so the link to Settings cannot outlive
  // the sentence that points at it.
  const [refusal, setRefusal] = useState<{ detail: string; hasSubscription: boolean } | null>(
    null,
  );

  useEffect(() => {
    // After the navigation the browser may keep this page in its back/forward
    // cache and restore it, exactly as it was, when the person comes back from
    // Stripe. State included: `busy` would still be set and Subscribe still
    // disabled. `persisted` is true only for that restore; an ordinary load has
    // nothing to undo.
    function onPageShow(event: PageTransitionEvent): void {
      if (event.persisted) setBusy(false);
    }
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, []);

  async function subscribe(): Promise<void> {
    setBusy(true);
    setRefusal(null);
    try {
      const session = await api.startCheckout(agencyId);
      (navigate ?? ((url: string) => window.location.assign(url)))(session.url);
    } catch (err) {
      setRefusal(
        err instanceof ApiProblem
          ? // The type, not the status: the API refuses with a 409 under three
            // types, and only two of them (`subscription-already-active`,
            // `subscription-needs-attention`) say this agency has a
            // subscription and that Settings is where it is dealt with.
            // `checkout-in-progress` says to try again in a moment.
            { detail: err.problem.detail, hasSubscription: isSubscriptionRefusal(err.problem) }
          : { detail: 'Checkout could not be opened.', hasSubscription: false },
      );
      setBusy(false);
    }
  }

  const failure =
    refusal != null ? (
      <div className="flex flex-col gap-2">
        <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
          {refusal.detail}
        </p>
        {refusal.hasSubscription && (
          <p className="text-ui-sm">
            <a
              href={BILLING_SETTINGS_HREF}
              className="font-medium text-beacon-600 underline underline-offset-2 transition-colors duration-hover ease-out hover:text-beacon-700"
            >
              Open Settings
            </a>
          </p>
        )}
      </div>
    ) : null;

  return (
    <Card elevation="flat">
      <CardBody>
        <div className="flex flex-col gap-4" data-testid="scan-limit-notice">
          {problem.code === 'SCAN_LIMIT_VERIFY_REQUIRED' ? (
            <>
              {/*
                "The same address", said plainly — 2026-09-29. It used to say
                "using this address", and nothing on this card shows one.
                Google's chooser lists every account in the browser, and a
                different, unused address is not refused: the callback takes
                it for a brand-new sign-up and asks for an agency name
                (`/sign-up/google`). So the rule is stated, and so is what
                breaking it does.
              */}
              <p className="max-w-measure text-ui-base leading-prose text-text-primary">
                Your free scan needs a Google sign-in, so we know one person gets one. Continue
                with Google using the same address you sign in with here, or subscribe. A
                different Google account would start a new agency instead.
              </p>
              {failure}
              <div className="flex flex-wrap items-center gap-3">
                <GoogleSignInButton href={GOOGLE_SIGN_IN_URL} label="Continue with Google" />
                <Button variant="secondary" onClick={() => void subscribe()} disabled={busy}>
                  {busy ? 'Opening checkout…' : 'Subscribe'}
                </Button>
              </div>
            </>
          ) : problem.code === 'SCAN_LIMIT_FREE_USED' ? (
            <>
              <p className="max-w-measure text-ui-base leading-prose text-text-primary">
                Your free scan is used. Subscribe to keep scanning — the reports you already have
                stay open, shareable and exportable.
              </p>
              {failure}
              <div>
                {/*
                  The one primary action, until the API says this agency has
                  a subscription: then another press can only be refused
                  again, and the link above is what is left to do.
                */}
                <Button
                  variant={refusal?.hasSubscription ? 'secondary' : 'primary'}
                  onClick={() => void subscribe()}
                  disabled={busy}
                >
                  {busy ? 'Opening checkout…' : 'Subscribe'}
                </Button>
              </div>
            </>
          ) : (
            <p className="max-w-measure text-ui-base leading-prose text-text-primary">
              This agency has used its {problem.scanLimit} scans for the month.
              {problem.periodResetsAt
                ? ` The allowance resets on ${formatDay(problem.periodResetsAt)}.`
                : ''}{' '}
              Reports already run stay open in the meantime.
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

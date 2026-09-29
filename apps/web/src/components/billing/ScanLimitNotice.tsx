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
 */

import { useState } from 'react';
import type { JSX } from 'react';
import { Button, Card, CardBody, GoogleSignInButton } from '@avp/design-system';
import { api, ApiProblem, GOOGLE_SIGN_IN_URL } from '@/lib/api';
import type { ScanLimitProblem } from '@/lib/billing/allowance';
import { formatDay } from '@/lib/dates';

export interface ScanLimitNoticeProps {
  problem: ScanLimitProblem;
  /** Whose checkout to open. */
  agencyId: string;
  /** Test seam: what to do with the Checkout URL. Defaults to navigating. */
  navigate?: ((url: string) => void) | undefined;
}

export function ScanLimitNotice({ problem, agencyId, navigate }: ScanLimitNoticeProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function subscribe(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const session = await api.startCheckout(agencyId);
      (navigate ?? ((url: string) => window.location.assign(url)))(session.url);
    } catch (err) {
      setError(err instanceof ApiProblem ? err.problem.detail : 'Checkout could not be opened.');
      setBusy(false);
    }
  }

  const failure =
    error != null ? (
      <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
        {error}
      </p>
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
                <Button variant="primary" onClick={() => void subscribe()} disabled={busy}>
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

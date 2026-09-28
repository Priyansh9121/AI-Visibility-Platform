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
 */

import { useState } from 'react';
import type { JSX } from 'react';
import { Button, Card, CardBody } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';
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

  return (
    <Card elevation="flat">
      <CardBody>
        <div className="flex flex-col gap-4" data-testid="scan-limit-notice">
          {problem.code === 'SCAN_LIMIT_FREE_USED' ? (
            <>
              <p className="max-w-measure text-ui-base leading-prose text-text-primary">
                Your free scan is used. Subscribe to keep scanning — the reports you already have
                stay open, shareable and exportable.
              </p>
              {error != null && (
                <p role="alert" className="max-w-measure text-ui-sm leading-prose text-danger">
                  {error}
                </p>
              )}
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

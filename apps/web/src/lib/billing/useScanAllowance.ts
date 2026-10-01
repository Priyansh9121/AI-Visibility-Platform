'use client';

/**
 * What a signed-in scan control is told about the agency's standing — 2026-09-28.
 *
 * `useScanStanding` reads `GET /agencies/{id}/billing` once, and `GET /auth/me`,
 * and hands back the remaining-count label beside the scan button ("1 free
 * scan", "12 of 40 scans this month"), the billing response itself and the
 * user's role. The billing route is owner/admin only; a member gets a 403 and
 * the label and the billing are null, so the label is simply absent for them —
 * the same degradation the settings page applies to the billing section. The
 * 402 they would meet on starting a scan is handled regardless.
 *
 * The scan-limit notice reads the billing and the role so it can leave
 * Subscribe out where Checkout would refuse. Each is null until known, and the
 * notice treats null as "as it always was". (A label-only hook used to sit
 * beside this one; both screens read the whole standing now, and it had no
 * caller left.)
 *
 * The role has a second source. `/auth/me` can fail, and then a member would be
 * offered Subscribe — their billing read failed as well, so the notice would
 * know nothing. That read is owner/admin only, and a 403 from it can mean one
 * thing: a member (another agency's id is a 404, not a 403). So a 403 stands in
 * for `member` when `/auth/me` cannot say; any other failure leaves the role
 * unknown, and `/auth/me`, when it answers, is never overruled.
 */

import { useEffect, useState } from 'react';
import type { BillingStatus, UserRole } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { allowanceLabel } from './allowance';

export interface ScanStanding {
  label: string | null;
  billing: BillingStatus | null;
  role: UserRole | null;
}

const UNKNOWN: ScanStanding = { label: null, billing: null, role: null };

export function useScanStanding(agencyId: string | null | undefined): ScanStanding {
  const [standing, setStanding] = useState<ScanStanding>(UNKNOWN);
  useEffect(() => {
    if (!agencyId) return;
    let cancelled = false;
    const billing = api.billing(agencyId).then(
      (b) => ({ billing: b, forbidden: false }),
      (e: unknown) => ({
        billing: null,
        forbidden: e instanceof ApiProblem && e.status === 403,
      }),
    );
    const role = api.me().then(
      (me) => me.user.role,
      () => null,
    );
    void Promise.all([billing, role])
      .then(([{ billing: b, forbidden }, r]) => {
        if (cancelled) return;
        setStanding({
          label: b ? allowanceLabel(b) : null,
          billing: b,
          role: r ?? (forbidden ? 'member' : null),
        });
      })
      .catch(() => {
        // The label is read off a response we did not write. If it cannot be
        // read the standing is simply not known, as it was before any of this
        // arrived; it must not be an unhandled rejection.
        if (!cancelled) setStanding(UNKNOWN);
      });
    return () => {
      cancelled = true;
    };
  }, [agencyId]);
  return standing;
}

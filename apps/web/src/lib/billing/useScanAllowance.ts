'use client';

/**
 * The remaining-count label for a signed-in scan control — 2026-09-28.
 *
 * Reads `GET /agencies/{id}/billing` once. That route is owner/admin only; a
 * member gets a 403 and this resolves to null, so the label is simply absent
 * for them — the same degradation the settings page applies to the billing
 * section. The 402 they would meet on starting a scan is handled regardless.
 */

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { allowanceLabel } from './allowance';

export function useScanAllowance(agencyId: string | null | undefined): string | null {
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    if (!agencyId) return;
    let cancelled = false;
    api
      .billing(agencyId)
      .then((billing) => {
        if (!cancelled) setLabel(allowanceLabel(billing));
      })
      .catch(() => {
        if (!cancelled) setLabel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [agencyId]);
  return label;
}

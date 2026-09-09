'use client';

/**
 * /clients/{clientId}/competitors — the field, dimension by dimension. Epic 13.
 *
 * Two reads. The client and its history come from the hook every screen in
 * this space shares; the latest scan's report is read once the history says
 * which scan that is, and it is the SAME document the Report item opens, so
 * a figure on this screen and a figure on the report are one row read twice.
 *
 * The report is allowed to be absent without failing the screen: a scan the
 * history lists whose report answers 404 is an ordinary state, handled the
 * way the Technical route handles a missing audit.
 */

import { use, useEffect, useState, type JSX } from 'react';
import type { Report } from '@avp/shared-types';
import {
  ClientCompetitorsView,
  type FieldState,
} from '@/components/client/ClientCompetitorsView';
import { latestScanId } from '@/components/client/ClientDetailView';
import { api, ApiProblem } from '@/lib/api';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientCompetitorsRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);
  const [field, setField] = useState<FieldState>({ kind: 'loading' });

  const scanId = state.kind === 'ready' ? latestScanId(state.history) : null;
  const ready = state.kind === 'ready';

  useEffect(() => {
    if (!ready) return;
    if (scanId === null) {
      // No scan with a reading: the view says so from the history alone.
      setField({ kind: 'ready', report: null });
      return;
    }
    let cancelled = false;
    setField({ kind: 'loading' });
    void (async () => {
      try {
        const report = await api.report(scanId);
        if (cancelled) return;
        setField({ kind: 'ready', report: report as Report });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiProblem && err.status === 404) {
          setField({ kind: 'ready', report: null });
          return;
        }
        setField({
          kind: 'error',
          title: 'The latest scan could not be read',
          detail:
            err instanceof ApiProblem ? err.problem.detail : 'The request did not complete.',
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, scanId]);

  return <ClientCompetitorsView state={state} field={field} me={me} />;
}

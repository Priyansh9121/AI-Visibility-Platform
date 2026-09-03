'use client';

/** /clients/{clientId}/gaps — which questions a rival owns. Epic B. */

import { use, useCallback, useEffect, useState, type JSX } from 'react';
import type { AnswerGaps } from '@avp/shared-types';
import {
  ClientAnswerGapsView,
  type AnswerGapsState,
} from '@/components/client/ClientAnswerGapsView';
import { api, ApiProblem } from '@/lib/api';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientGapsRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);

  /**
   * The grid is fetched separately from the client + history that
   * `useClientDetail` loads.
   *
   * Not folded into that hook, because it is the only request on this screen
   * that takes a PARAMETER an operator can change: picking an earlier scan
   * refetches the grid and must not also refetch the client, the history and
   * `me`. The other four screens in this space have nothing to re-request, so
   * pushing this into the shared hook would give all of them a scan selector
   * they do not have.
   */
  const [scanId, setScanId] = useState<string | null>(null);
  const [gaps, setGaps] = useState<AnswerGapsState>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.answerGaps(clientId, scanId ?? undefined);
        if (cancelled) return;
        setGaps({ kind: 'ready', data: data as AnswerGaps | null });
      } catch (err) {
        if (cancelled) return;
        setGaps({
          kind: 'error',
          title: 'These answer gaps could not be loaded',
          detail:
            err instanceof ApiProblem
              ? err.problem.detail
              : 'The request did not complete.',
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clientId, scanId]);

  const onSelectScan = useCallback((next: string) => {
    /*
     * Mark the current grid PENDING rather than dropping back to `loading`.
     *
     * `loading` unmounts the body, which took the scan picker down with it —
     * the control an operator had just used vanished from under their pointer
     * and reappeared somewhere else once the fetch returned. The grid is kept
     * on screen and dimmed instead, so the controls hold their position and
     * the stale numbers are visibly stale rather than replaced by a spinner.
     */
    setGaps((prev) =>
      prev.kind === 'ready' && prev.data != null
        ? { ...prev, pending: true }
        : { kind: 'loading' },
    );
    setScanId(next);
  }, []);

  return (
    <ClientAnswerGapsView
      state={state}
      gaps={gaps}
      me={me}
      onSelectScan={onSelectScan}
    />
  );
}

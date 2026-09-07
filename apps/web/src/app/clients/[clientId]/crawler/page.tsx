'use client';

/** /clients/{clientId}/crawler — what this site asks AI crawlers to do. Epic F. */

import { use, useCallback, useEffect, useState, type JSX } from 'react';
import type { CrawlerAccess } from '@avp/shared-types';
import {
  ClientCrawlerView,
  type CrawlerAccessState,
} from '@/components/client/ClientCrawlerView';
import { api, ApiProblem } from '@/lib/api';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientCrawlerRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);

  const [access, setAccess] = useState<CrawlerAccessState>({ kind: 'loading' });

  const load = useCallback(
    /*
     * `swap` distinguishes picking another scan from the first load.
     *
     * On a swap the last good reading stays on screen and dims, rather than
     * the body resetting to a spinner — which would unmount the scan picker
     * the operator just used and leave it reappearing somewhere else once the
     * fetch returned. Epic B found that live; this screen inherits the fix
     * rather than rediscovering it.
     */
    (scanId?: string, swap = false) => {
      let cancelled = false;
      if (swap) {
        setAccess((prev) =>
          prev.kind === 'ready' ? { ...prev, pending: true } : prev,
        );
      }
      void (async () => {
        try {
          const data = await api.aiCrawlerAccess(clientId, scanId);
          if (cancelled) return;
          setAccess({ kind: 'ready', data: data as CrawlerAccess | null });
        } catch (err) {
          if (cancelled) return;
          setAccess({
            kind: 'error',
            title: 'This crawler policy could not be loaded',
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
    },
    [clientId],
  );

  useEffect(() => load(), [load]);

  return (
    <ClientCrawlerView
      state={state}
      access={access}
      me={me}
      onSelectScan={(scanId) => load(scanId, true)}
    />
  );
}

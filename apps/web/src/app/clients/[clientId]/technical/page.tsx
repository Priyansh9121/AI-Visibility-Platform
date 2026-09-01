'use client';

/**
 * /clients/{clientId}/technical — the audit as its own destination. Epic 9.22.
 *
 * Three reads, because they answer different questions: identity, which scan
 * to audit-read, and the audit itself. The audit is allowed to be absent
 * without failing the screen — a client can exist and have been scanned
 * without the audit phase having produced anything.
 */

import { use, useEffect, useState, type JSX } from 'react';
import type { Client, ClientHistory, Me, TechnicalAudit } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import {
  ClientTechnicalView,
  type TechnicalState,
} from '@/components/client/ClientTechnicalView';

export default function ClientTechnicalRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const [state, setState] = useState<TechnicalState>({ kind: 'loading' });
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [identity, client, history] = await Promise.all([
          api.me(),
          api.client(clientId),
          api.clientHistory(clientId),
        ]);
        if (cancelled) return;
        setMe(identity);

        // The newest scan that produced a reading is the one whose audit
        // describes the site as it stands.
        const newest = history.scans[history.scans.length - 1];
        let audit: TechnicalAudit | null = null;
        if (newest) {
          try {
            audit = await api.audit(newest.scanId);
          } catch (err) {
            // A 404 here is a real, ordinary state: the scan ran but the audit
            // phase produced nothing. It must not take the screen down, the
            // same way a missing seat roster does not take Settings down.
            if (!(err instanceof ApiProblem && err.status === 404)) throw err;
          }
        }
        if (cancelled) return;
        setState({ kind: 'ready', client: client as Client, history: history as ClientHistory, audit });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: 'error',
          title:
            err instanceof ApiProblem && err.status === 401
              ? 'Sign in to see this client'
              : err instanceof ApiProblem && err.status === 404
                ? 'No such client'
                : 'This client could not be loaded',
          detail:
            err instanceof ApiProblem ? err.problem.detail : 'The request did not complete.',
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  return <ClientTechnicalView state={state} me={me} />;
}

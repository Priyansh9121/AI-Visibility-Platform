'use client';

/** /clients/{clientId}/alerts — what changed between scans. Epic E. */

import { use, useCallback, useEffect, useState, type JSX } from 'react';
import type { AlertFeed } from '@avp/shared-types';
import {
  ClientAlertsView,
  type AlertsState,
} from '@/components/client/ClientAlertsView';
import { api, ApiProblem } from '@/lib/api';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientAlertsRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);

  const [alerts, setAlerts] = useState<AlertsState>({ kind: 'loading' });
  /** Ids being acknowledged, so a row can say so rather than looking inert. */
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  /** Ids whose last attempt failed, so the row can say so. */
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const feed = await api.clientAlerts(clientId);
        if (cancelled) return;
        setAlerts({ kind: 'ready', feed: feed as AlertFeed });
      } catch (err) {
        if (cancelled) return;
        setAlerts({
          kind: 'error',
          title: 'These alerts could not be loaded',
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
  }, [clientId]);

  const onAcknowledge = useCallback(
    (alertId: string) => {
      setPending((prev) => new Set(prev).add(alertId));
      setFailed((prev) => {
        if (!prev.has(alertId)) return prev;
        const next = new Set(prev);
        next.delete(alertId);
        return next;
      });
      void (async () => {
        try {
          const done = await api.acknowledgeAlert(alertId);
          /*
           * Patched in place rather than refetching the feed.
           *
           * A refetch would rebuild the list and move every row an operator is
           * part-way through reading — and acknowledging is a bulk action, so
           * that would happen on every click. The server is the authority on
           * the timestamp, so the one it returns is what gets written here.
           */
          setAlerts((prev) =>
            prev.kind === 'ready'
              ? {
                  ...prev,
                  feed: {
                    ...prev.feed,
                    unacknowledged: Math.max(0, prev.feed.unacknowledged - 1),
                    alerts: prev.feed.alerts.map((a) =>
                      // `?? null`, not `?? undefined`: the field is nullable,
                      // and under `exactOptionalPropertyTypes` an absent key
                      // and a null one are different types. Null is what the
                      // API returns for "not acknowledged" and what every
                      // other read of this field already expects.
                      a.id === alertId
                        ? { ...a, acknowledgedAt: done.acknowledgedAt ?? null }
                        : a,
                    ),
                  },
                }
              : prev,
          );
        } catch {
          /*
           * Left outstanding, and SAID SO. An alert that looked acknowledged
           * but was not would be worse than one that has to be clicked twice —
           * but a button that simply re-enables with nothing else changed
           * reads as "the click did not register", so the operator clicks
           * again and again. The row states the failure and offers a retry.
           */
          setFailed((prev) => new Set(prev).add(alertId));
        } finally {
          setPending((prev) => {
            const next = new Set(prev);
            next.delete(alertId);
            return next;
          });
        }
      })();
    },
    [],
  );

  return (
    <ClientAlertsView
      state={state}
      alerts={alerts}
      me={me}
      onAcknowledge={onAcknowledge}
      acknowledging={pending}
      failed={failed}
    />
  );
}

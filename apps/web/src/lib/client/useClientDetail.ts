'use client';

/**
 * Load one client and its scan history — Epic 9.20.
 *
 * Shared by all three screens in a client's space, so the fetch, the error
 * wording and the 401/404 branches cannot drift between them. Three copies of
 * this effect is exactly how the four bespoke loading paragraphs Epic 9.11 had
 * to unify came about in the first place.
 */

import { useEffect, useState } from 'react';
import type { AlertFeed, Client, ClientHistory, Me } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import type { ClientDetailState } from '@/components/client/ClientDetailView';

export function useClientDetail(clientId: string): {
  state: ClientDetailState;
  me: Me | null;
  /**
   * This client's alerts — Epic E.
   *
   * Returned ALONGSIDE `state` rather than folded into it, so the four screens
   * that do not care about alerts are untouched and `ClientDetailState` keeps
   * meaning "the client and its history". The trends use it to annotate; the
   * Alerts tab fetches its own, because it needs the feed even when the client
   * record fails to load.
   *
   * `null` while loading AND on failure, deliberately. An annotation is an
   * enhancement to a chart that is already correct without it — a trend that
   * refused to draw because a secondary request failed would be a worse screen
   * than one drawn without markers.
   */
  alerts: AlertFeed | null;
} {
  const [state, setState] = useState<ClientDetailState>({ kind: 'loading' });
  const [me, setMe] = useState<Me | null>(null);
  const [alerts, setAlerts] = useState<AlertFeed | null>(null);

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
        /*
         * Alerts are fetched SEPARATELY and their failure is swallowed.
         *
         * They annotate charts that are complete without them, so putting this
         * in the `Promise.all` above would let a failed alert request blank the
         * whole client screen — trading a missing marker for a missing page.
         */
        void api
          .clientAlerts(clientId)
          .then((feed) => {
            if (!cancelled) setAlerts(feed as AlertFeed);
          })
          .catch(() => undefined);
        setState({
          kind: 'ready',
          client: client as Client,
          history: history as ClientHistory,
        });
      } catch (err) {
        if (cancelled) return;
        // 404 is what another agency's client id returns too — the API answers
        // "not found" rather than "forbidden" on purpose, so an id cannot be
        // probed for existence. The copy matches that: it does not claim the
        // client exists somewhere else.
        setState({
          kind: 'error',
          title:
            err instanceof ApiProblem && err.status === 401
              ? 'Sign in to see this client'
              : err instanceof ApiProblem && err.status === 404
                ? 'No such client'
                : 'This client could not be loaded',
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

  return { state, me, alerts };
}

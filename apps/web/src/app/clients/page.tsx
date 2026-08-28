'use client';

/**
 * /clients — every business this agency has added. Epic 9.13.
 *
 * `GET /clients` has existed since Epic 2 and nothing in the browser called it,
 * the same way `sign-up` sat unused until Epic 9.12. This is wiring, not a new
 * capability.
 *
 * The screen itself is `components/clients/ClientsView` — pure and prop-driven,
 * split out in Epic 9.14 so its states can be rendered statically and asserted
 * over. This file is only the fetching shell.
 */

import { useEffect, useState, type JSX } from 'react';
import type { Me } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { ClientsView, type ClientsState } from '@/components/clients/ClientsView';

export default function ClientsRoute(): JSX.Element {
  const [state, setState] = useState<ClientsState>({ kind: 'loading' });
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [identity, page] = await Promise.all([api.me(), api.clients()]);
        if (cancelled) return;
        setMe(identity);
        setState({ kind: 'ready', clients: page.data, more: page.nextCursor !== null });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: 'error',
          title:
            err instanceof ApiProblem && err.status === 401
              ? 'Sign in to see your clients'
              : 'Your clients could not be loaded',
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
  }, []);

  return <ClientsView state={state} me={me} />;
}

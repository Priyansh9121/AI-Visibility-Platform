'use client';

/**
 * /welcome — first run, straight after sign-up. Epic 9.13.
 *
 * The screen is `components/welcome/WelcomeView` — pure and prop-driven, split
 * out in Epic 9.14 so its steps can be rendered statically and asserted over.
 * This file owns the session check and the three-value state machine.
 */

import { useEffect, useState, type JSX } from 'react';
import type { ClientDetail, Me } from '@avp/shared-types';
import { api, ApiProblem } from '@/lib/api';
import { WelcomeView, type WelcomeState } from '@/components/welcome/WelcomeView';

export default function WelcomeRoute(): JSX.Element {
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<WelcomeState>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const identity = await api.me();
        if (!cancelled) {
          setMe(identity);
          setState({ kind: 'ask' });
        }
      } catch (err) {
        // Reaching /welcome without a session means the sign-up did not take,
        // or someone opened the URL directly. Either way the answer is the
        // front door, not an error card on a page that needs an account.
        if (!cancelled && err instanceof ApiProblem && err.status === 401) {
          window.location.assign('/');
        } else if (!cancelled) {
          setState({ kind: 'ask' });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <WelcomeView
      state={state}
      me={me}
      onStarted={() => setState({ kind: 'working' })}
      onClassified={(client: ClientDetail) => setState({ kind: 'result', client })}
      onReset={() => setState({ kind: 'ask' })}
    />
  );
}

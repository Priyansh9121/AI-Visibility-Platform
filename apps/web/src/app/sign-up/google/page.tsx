'use client';

/**
 * /sign-up/google?ticket=… — where Google's callback sends a NEW address.
 *
 * Epic 20. The API verified the person and parked what it learned under a
 * ten-minute ticket; this page reads it (without spending it, so a reload is
 * not a second sign-in), asks for the agency's name, and posts the ticket
 * back to create the agency. On success the session cookie is already set,
 * so it goes where sign-up goes: /welcome.
 *
 * The ticket travels in the query string, the trade `/reset-password/{token}`
 * and `/invite/{token}` make for the same reason: there is no session yet to
 * carry it any other way, and it lives ten minutes and once.
 */

import { useEffect, useState, type JSX } from 'react';
import { Button } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';
import {
  GoogleSignUpPanel,
  type GoogleSignUpState,
} from '@/components/google/GoogleSignUpPanel';

export default function GoogleSignUpRoute(): JSX.Element {
  const [ticket, setTicket] = useState('');
  const [state, setState] = useState<GoogleSignUpState>({ kind: 'loading' });

  useEffect(() => {
    const found = new URLSearchParams(window.location.search).get('ticket') ?? '';
    setTicket(found);
    if (!found) {
      setState({ kind: 'expired' });
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const pending = await api.googlePending(found);
        if (!cancelled) setState({ kind: 'ask', pending });
      } catch (err) {
        if (cancelled) return;
        // 400 is the ticket's own verdict; anything else is the API being
        // away, and the honest thing is still "start again".
        if (err instanceof ApiProblem) setState({ kind: 'expired' });
        else setState({ kind: 'expired' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto max-w-page px-6 py-18">
      <div className="mx-auto max-w-form">
        <Button variant="ghost" size="sm" onClick={() => window.location.assign('/')}>
          Back
        </Button>
      </div>
      <div className="mt-4">
        <GoogleSignUpPanel
          state={state}
          ticket={ticket}
          onSignedUp={() => window.location.assign('/welcome')}
        />
      </div>
    </main>
  );
}

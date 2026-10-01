'use client';

/**
 * /sign-up/google#ticket=… — where Google's callback sends a NEW address.
 *
 * Epic 20. The API verified the person and parked what it learned under a
 * ten-minute ticket; this page reads it (a peek that does not spend it), asks for the agency's name, and posts the ticket
 * back to create the agency. On success the session cookie is already set,
 * so it goes where sign-up goes: /welcome.
 *
 * The ticket travels in the URL fragment, like `/reset-password#{token}` and
 * `/invite#{token}`: never sent to a server, so in no request log or Referer.
 * It is read once, taken out of the address bar, and sent only in a POST body.
 * Because it leaves the address bar, a reload of this page does not keep the
 * sign-up alive: the person sees the expired state and starts again.
 */

import { useEffect, useRef, useState, type JSX } from 'react';
import { Button } from '@avp/design-system';
import { api, ApiProblem } from '@/lib/api';
import { takeGoogleTicket } from '@/lib/googleTicket';
import {
  GoogleSignUpPanel,
  type GoogleSignUpState,
} from '@/components/google/GoogleSignUpPanel';

export default function GoogleSignUpRoute(): JSX.Element {
  const [ticket, setTicket] = useState('');
  // Held in a ref: React's development double-run of effects would otherwise
  // read the already-cleared address bar a second time and call it expired.
  const taken = useRef<string | null>(null);
  const [state, setState] = useState<GoogleSignUpState>({ kind: 'loading' });

  useEffect(() => {
    taken.current ??= takeGoogleTicket(window);
    const found = taken.current;
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

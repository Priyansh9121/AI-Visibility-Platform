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
 * sign-up alive: with no ticket to read, the page shows the no-code state, in
 * the words /invite and /reset-password use for the same thing, and the person
 * starts again. A ticket the API refuses (a 400) is the expired state. An API
 * that does not answer, or answers 5xx, is not a refusal and is the unreachable
 * state, with a Try again that peeks once more.
 */

import { useEffect, useRef, useState, type JSX } from 'react';
import { Button } from '@avp/design-system';
import { api } from '@/lib/api';
import { apiWasAway } from '@/lib/apiAway';
import { takeGoogleTicket } from '@/lib/googleTicket';
import {
  GoogleSignUpPanel,
  type GoogleSignUpState,
} from '@/components/google/GoogleSignUpPanel';

export default function GoogleSignUpRoute(): JSX.Element {
  const [ticket, setTicket] = useState('');
  // Held in a ref: React's development double-run of effects would otherwise
  // read the already-cleared address bar a second time and show no-code.
  const taken = useRef<string | null>(null);
  const [state, setState] = useState<GoogleSignUpState>({ kind: 'loading' });
  // Bumped by "Try again": re-runs the peek, which does not spend the ticket.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    taken.current ??= takeGoogleTicket(window);
    const found = taken.current;
    setTicket(found);
    if (!found) {
      setState({ kind: 'no-code' });
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const pending = await api.googlePending(found);
        if (!cancelled) setState({ kind: 'ask', pending });
      } catch (err) {
        if (cancelled) return;
        // The API's own refusal of the ticket (a 400) is "expired". No answer
        // at all, or a 5xx (a proxy's non-JSON 502 reaches here as an
        // ApiProblem carrying that status), is the API being away: the ticket
        // may be fine, so say that and offer another try.
        setState({ kind: apiWasAway(err) ? 'unreachable' : 'expired' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

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
          onRetry={() => {
            setState({ kind: 'loading' });
            setAttempt((n) => n + 1);
          }}
          onSignedUp={() => window.location.assign('/welcome')}
        />
      </div>
    </main>
  );
}

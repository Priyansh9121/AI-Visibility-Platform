'use client';

/**
 * The feedback address, for a page that reads no `me` of its own — 2026-10-06.
 *
 * `GET /auth/me` carries `feedbackEmail`: the API's `FEEDBACK_EMAIL`, or null when it is unset.
 * Four screens already read `me` (Compare, Clients, a client's space, Settings: they show the
 * agency's name and its seats in the sidebar) and hand its `feedbackEmail` to the shell as a prop,
 * and the dashboard takes it from the `/auth/me` its standing read makes (`useScanStanding`), so
 * none of those makes a request for it. The REPORT reads no `me` at all, so it alone asks through
 * this hook, once, when it mounts: one more `GET /auth/me` on that page and on no other.
 *
 * It is the address, or NULL, and null for everything else: while the request is out, for an API
 * that has no such field yet (the web can be deployed before the API), for a value that is not one
 * address (`feedbackAddress`), and for any failure, including a signed-out session. It never
 * rejects, so a page has no error to handle and cannot forget to. A page that was not told an
 * address draws no link; it never guesses one.
 */

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { feedbackAddress } from '@/lib/feedback/subject';

export function useFeedbackEmail(): string | null {
  const [address, setAddress] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    // `Promise.resolve().then(...)` so that a call that throws before it returns a promise (the
    // module mocked without it, say) is a failure like any other and not an uncaught error.
    void Promise.resolve()
      .then(() => api.me())
      .then(
        (me) => {
          if (live) setAddress(feedbackAddress(me?.feedbackEmail));
        },
        () => undefined,
      );
    return () => {
      live = false;
    };
  }, []);
  return address;
}

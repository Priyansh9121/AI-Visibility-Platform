/**
 * The Google sign-up ticket, taken from the address bar.
 *
 * The API redirects to `/sign-up/google#ticket=<ticket>`. A fragment is never
 * sent to a server, so the ticket is in no request log and no Referer. It is
 * read once and removed from the address bar (`replaceState` with `null`
 * state, as `useFragmentToken` does), and from then on lives in component
 * state and a POST body.
 *
 * LEGACY, remove after one release: for the deploy window in which a person
 * was sent to `/sign-up/google?ticket=…` by the previous API (tickets live ten
 * minutes), a query ticket is accepted when there is no fragment, and moved
 * out of the address bar the same way. It is only ever read here, in the
 * browser; it is never put in a request to the API.
 */

import { parseFragment } from '@/lib/fragmentToken';

interface Loc {
  hash: string;
  search: string;
  pathname: string;
}

interface Win {
  location: Loc;
  history: { replaceState: (state: null, unused: string, url: string) => void };
}

/** `#ticket=abc` -> `abc`; anything else, or a malformed escape -> `''`. */
function fromHash(hash: string): string {
  const decoded = parseFragment(hash);
  return decoded.startsWith('ticket=') ? decoded.slice('ticket='.length).trim() : '';
}

/** The ticket, or `''` when the link carries none. Clears the address bar. */
export function takeGoogleTicket(win: Win): string {
  const loc = win.location;
  let ticket = fromHash(loc.hash);
  if (ticket === '') {
    // LEGACY (one release): a link minted before the ticket moved to the fragment.
    ticket = (new URLSearchParams(loc.search).get('ticket') ?? '').trim();
  }
  if (loc.hash !== '' || loc.search !== '') {
    win.history.replaceState(null, '', loc.pathname);
  }
  return ticket;
}

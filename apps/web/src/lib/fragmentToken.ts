'use client';

/**
 * A token carried in the URL fragment (`/share#<token>`).
 *
 * A fragment is never sent to a server and never appears in a Referer, so a
 * link's secret stays out of every request log. The page reads it after mount.
 *
 * `undefined` means "not read yet" — the server render and the first client
 * render both see it, so they match. `''` means the fragment was empty or
 * unreadable, which the caller shows as its incomplete-link state.
 *
 * `clear` removes the fragment from the address bar right after reading, for
 * single-use secrets (an invitation, a reset). The token then lives only in
 * component state. The value is held in a ref, so React's development
 * double-run of effects cannot read the already-cleared address a second time.
 *
 * When the fragment is kept (`clear` false) a `hashchange` in the same tab
 * updates the token.
 */

import { useEffect, useRef, useState } from 'react';

/** `#abc%20d` -> `abc d`; anything malformed -> `''`. */
export function parseFragment(hash: string): string {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (raw === '') return '';
  try {
    return decodeURIComponent(raw).trim();
  } catch {
    return '';
  }
}

export function useFragmentToken(clear: boolean): string | undefined {
  const [token, setToken] = useState<string | undefined>(undefined);
  const read = useRef<string | null>(null);

  useEffect(() => {
    if (read.current === null) {
      read.current = parseFragment(window.location.hash);
      if (clear && window.location.hash !== '') {
        // `null` state, not the current one: Next's patched
        // `replaceState` skips syncing its router when the state carries its
        // own marker, so the router would keep `#<token>` and could write it
        // back into the address bar on a later update.
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
      }
    }
    setToken(read.current);
    if (clear) return undefined;
    // A kept fragment can change in the same tab (a pasted link, a back/forward
    // step). Follow it, so the page never shows a report for an old token.
    const onHashChange = (): void => {
      read.current = parseFragment(window.location.hash);
      setToken(read.current);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [clear]);

  return token;
}

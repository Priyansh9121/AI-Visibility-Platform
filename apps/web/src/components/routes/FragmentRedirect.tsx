'use client';

/**
 * Old path-form links (`/share/{token}`) already sent to people still open.
 * This moves the token into the fragment form (`/share#{token}`) and never
 * fetches with it. `replace` keeps the path form out of history.
 */

import { useEffect, type JSX } from 'react';
import { LoadingState } from '@avp/design-system';

export function FragmentRedirect({
  path,
  token,
}: {
  path: '/share' | '/invite' | '/reset-password';
  token: string;
}): JSX.Element {
  const target = `${path}#${encodeURIComponent(token)}`;

  useEffect(() => {
    window.location.replace(`${path}#${encodeURIComponent(token)}`);
  }, [path, token]);

  return (
    <main className="mx-auto max-w-page px-6 py-18">
      <LoadingState message="Opening…" />
      {/* Always present: without JavaScript, or if the script fails to load,
          this is the way through. */}
      <p className="mt-4 text-ui-sm leading-prose text-text-secondary">
        <a href={target} className="underline">
          Open the link
        </a>
      </p>
      <noscript>
        <p className="mt-2 text-ui-sm leading-prose text-text-secondary">
          This page needs JavaScript. Use the link above to open it.
        </p>
      </noscript>
    </main>
  );
}

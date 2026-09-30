import type { JSX } from 'react';
import { LoadingState } from '@avp/design-system';

/** What the server and the first client render show while the fragment is unread. */
export function PendingLink(): JSX.Element {
  return (
    <main className="mx-auto max-w-page px-6 py-18">
      <LoadingState message="Opening the link…" />
    </main>
  );
}

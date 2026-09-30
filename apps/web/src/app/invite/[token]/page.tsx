'use client';

/**
 * /invite/{token} — the old path form, kept so links already sent still work.
 * It only forwards to /invite#{token}; see FragmentRedirect.
 */

import { use, type JSX } from 'react';
import { FragmentRedirect } from '@/components/routes/FragmentRedirect';

export default function InviteLegacyRoute({
  params,
}: {
  params: Promise<{ token: string }>;
}): JSX.Element {
  const { token } = use(params);
  return <FragmentRedirect path="/invite" token={token} />;
}

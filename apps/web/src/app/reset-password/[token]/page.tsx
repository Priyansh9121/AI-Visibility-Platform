'use client';

/**
 * /reset-password/{token} — the old path form, kept so links already sent still work.
 * It only forwards to /reset-password#{token}; see FragmentRedirect.
 */

import { use, type JSX } from 'react';
import { FragmentRedirect } from '@/components/routes/FragmentRedirect';

export default function ResetPasswordLegacyRoute({
  params,
}: {
  params: Promise<{ token: string }>;
}): JSX.Element {
  const { token } = use(params);
  return <FragmentRedirect path="/reset-password" token={token} />;
}

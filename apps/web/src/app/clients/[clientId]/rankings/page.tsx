'use client';

/** /clients/{clientId}/rankings — share of voice over time. Epic 9.20. */

import { use, type JSX } from 'react';
import { ClientRankingsView } from '@/components/client/ClientDetailView';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientRankingsRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me, alerts } = useClientDetail(clientId);
  return <ClientRankingsView state={state} me={me} alerts={alerts} />;
}

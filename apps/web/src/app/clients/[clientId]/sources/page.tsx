'use client';

/** /clients/{clientId}/sources — citations per domain over time. Epic 9.20. */

import { use, type JSX } from 'react';
import { ClientSourcesView } from '@/components/client/ClientDetailView';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientSourcesRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);
  return <ClientSourcesView state={state} me={me} />;
}

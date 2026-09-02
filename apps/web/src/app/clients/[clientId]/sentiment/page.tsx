'use client';

/** /clients/{clientId}/sentiment — how the engines describe this client. Epic A. */

import { use, type JSX } from 'react';
import { ClientSentimentView } from '@/components/client/ClientSentimentView';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientSentimentRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);
  return <ClientSentimentView state={state} me={me} />;
}

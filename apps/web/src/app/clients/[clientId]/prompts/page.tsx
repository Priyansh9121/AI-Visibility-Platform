'use client';

/** /clients/{clientId}/prompts — ad-hoc prompt testing. Epic 9.24. */

import { use, type JSX } from 'react';
import { ClientPromptsView } from '@/components/client/ClientPromptsView';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientPromptsRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);
  return <ClientPromptsView state={state} me={me} />;
}

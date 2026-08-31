'use client';

/**
 * /clients/{clientId} — the front door of one client's space. Epic 9.20.
 *
 * The fetching shell only; `ClientOverviewView` is the screen. Two calls
 * because they answer different questions: `client()` is the record's
 * identity (which exists even for a client that has never been scanned), and
 * `clientHistory()` is its readings.
 */

import { use, type JSX } from 'react';
import { ClientOverviewView } from '@/components/client/ClientDetailView';
import { useClientDetail } from '@/lib/client/useClientDetail';

export default function ClientOverviewRoute({
  params,
}: {
  params: Promise<{ clientId: string }>;
}): JSX.Element {
  const { clientId } = use(params);
  const { state, me } = useClientDetail(clientId);
  return <ClientOverviewView state={state} me={me} />;
}

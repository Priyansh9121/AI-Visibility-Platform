/**
 * The sidebar's client list — Epic 13.
 *
 * Pure, and separated from the shell for the reason `lib/client/trends.ts`
 * is: the interesting behaviour is what happens when a client has no score,
 * or several scans, or is missing from the window, and that is arithmetic
 * rather than rendering.
 *
 * WHERE THE SCORES COME FROM, AND WHAT THAT CANNOT CLAIM
 * ------------------------------------------------------
 * `GET /clients` carries no score — it has not needed one, because the
 * clients screen is a list of identities. The sidebar wants a dot and a
 * figure per client, and the one read that already carries a composite per
 * scan is the dashboard's recent-scans list. So the list is derived from
 * that: **the newest scan with a composite is the client's latest score.**
 *
 * The window is bounded — the endpoint caps at 50 scans — so a client whose
 * newest scored scan has fallen out of it is UNKNOWN here, not unscored. That
 * distinction is carried all the way to the row: unknown draws no dot at all,
 * because a grey dot beside a number-less name reads as "low", which would be
 * a claim this list has no evidence for. The eventual home for this figure is
 * a `latestComposite` on the clients list itself; that is its own small API
 * brief, and until it exists this derivation says exactly what it knows.
 */

import type { Client, Dashboard } from '@avp/shared-types';

export interface SidebarClient {
  id: string;
  label: string;
  domain: string;
  /**
   * A number when a scored scan was found; `null` when the client has scans
   * in the window and none of them carries a composite; absent when the
   * window holds no scan for the client at all.
   */
  score?: number | null;
}

function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Latest composite per client id, from the dashboard's recent scans.
 *
 * Newest first is what the endpoint promises; it is sorted here anyway, so
 * the rule "newest scored scan wins" does not depend on a promise made in a
 * different package.
 */
export function latestScores(dashboard: Pick<Dashboard, 'recentScans'>): Map<string, number | null> {
  const scans = [...dashboard.recentScans].sort((a, b) =>
    a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0,
  );
  const out = new Map<string, number | null>();
  for (const scan of scans) {
    const have = out.get(scan.clientId);
    if (typeof have === 'number') continue; // already found the newest scored scan
    const composite = num(scan.compositeScore);
    out.set(scan.clientId, composite);
  }
  return out;
}

/** The rows the sidebar draws, A to Z by the name an operator knows. */
export function sidebarClients(
  clients: readonly Client[],
  scores: ReadonlyMap<string, number | null>,
): SidebarClient[] {
  return clients
    .map((c) => {
      const row: SidebarClient = { id: c.id, label: c.brandName ?? c.name, domain: c.domain };
      if (scores.has(c.id)) row.score = scores.get(c.id) ?? null;
      return row;
    })
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));
}

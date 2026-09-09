import { describe, it, expect } from 'vitest';
import type { Client, Dashboard } from '@avp/shared-types';
import { latestScores, sidebarClients } from './clientList';

const scan = (
  id: string,
  clientId: string,
  createdAt: string,
  composite: string | null,
): Dashboard['recentScans'][number] =>
  ({
    id,
    clientId,
    clientName: clientId,
    clientDomain: `${clientId}.example`,
    status: composite === null ? 'running' : 'succeeded',
    compositeScore: composite,
    createdAt,
    finishedAt: null,
  }) as Dashboard['recentScans'][number];

describe('the newest scored scan is the latest score', () => {
  it('skips a newer scan that has no composite yet', () => {
    const scores = latestScores({
      recentScans: [
        scan('s3', 'c1', '2026-09-09T10:00:00Z', null), // running now
        scan('s2', 'c1', '2026-09-08T10:00:00Z', '33.93'),
        scan('s1', 'c1', '2026-09-01T10:00:00Z', '27.28'),
      ],
    });
    expect(scores.get('c1')).toBe(33.93);
  });

  it('does not trust the endpoint ordering', () => {
    const scores = latestScores({
      recentScans: [
        scan('s1', 'c1', '2026-09-01T10:00:00Z', '27.28'),
        scan('s2', 'c1', '2026-09-08T10:00:00Z', '33.93'),
      ],
    });
    expect(scores.get('c1')).toBe(33.93);
  });

  it('reports null for a client whose scans in the window all lack a composite', () => {
    const scores = latestScores({ recentScans: [scan('s1', 'c2', '2026-09-08T10:00:00Z', null)] });
    expect(scores.has('c2')).toBe(true);
    expect(scores.get('c2')).toBeNull();
  });

  it('says nothing about a client outside the window', () => {
    expect(latestScores({ recentScans: [] }).has('c9')).toBe(false);
  });
});

describe('sidebar rows', () => {
  const client = (id: string, name: string, brandName: string | null): Client =>
    ({ id, name, brandName, domain: `${id}.example` }) as Client;

  it('sorts by the name an operator knows, brand name first', () => {
    const rows = sidebarClients(
      [client('z', 'zammad.com', 'Zammad'), client('p', 'pirsch.io', 'Pirsch Analytics'), client('h', 'helpwise.io', null)],
      new Map(),
    );
    expect(rows.map((r) => r.label)).toEqual(['helpwise.io', 'Pirsch Analytics', 'Zammad']);
  });

  it('keeps the three score states apart', () => {
    const rows = sidebarClients(
      [client('a', 'A', null), client('b', 'B', null), client('c', 'C', null)],
      new Map<string, number | null>([
        ['a', 34],
        ['b', null],
      ]),
    );
    expect(rows.find((r) => r.id === 'a')?.score).toBe(34);
    expect(rows.find((r) => r.id === 'b')?.score).toBeNull();
    expect('score' in rows.find((r) => r.id === 'c')!).toBe(false);
  });
});

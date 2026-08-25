/**
 * Dashboard fixtures.
 *
 * Deliberately mirrors the states `apps/api/tests/test_dashboard.py` already
 * proves the endpoint produces, so the screen is tested against the same set of
 * realities the API is: an empty agency, an unscored scan that must still
 * appear, INSUFFICIENT_DATA arriving as null rather than zero, newest-first
 * ordering, and a full page at the endpoint's default limit.
 *
 * `compositeScore` is a STRING in every fixture. It is NUMERIC(5,2) in Postgres
 * and crosses the wire as a string so binary-float error never re-enters the
 * value — the encoding docs/api-contracts.md documents and the API test asserts
 * byte-for-byte.
 */

import type { Dashboard, ScanStatus, ScanSummary } from '@avp/shared-types';

const AGENCY = {
  id: 'agcy_01M0AGENCYFIXTURE00000001',
  name: 'Northlight Partners',
  slug: 'northlight-partners',
  seatLimit: 5,
  createdAt: '2026-08-01T09:00:00Z',
};

interface ScanOverrides {
  id?: string;
  clientId?: string;
  clientName?: string;
  clientDomain?: string;
  status?: ScanStatus;
  compositeScore?: string | null;
  createdAt?: string;
  finishedAt?: string | null;
}

export function scan(overrides: ScanOverrides = {}): ScanSummary {
  return {
    id: overrides.id ?? 'scan_01M0SCANFIXTURE000000001',
    clientId: overrides.clientId ?? 'clnt_01M0CLIENTFIXTURE0000001',
    clientName: overrides.clientName ?? 'Help Scout',
    clientDomain: overrides.clientDomain ?? 'helpscout.com',
    status: overrides.status ?? 'succeeded',
    compositeScore: overrides.compositeScore === undefined ? '38.35' : overrides.compositeScore,
    createdAt: overrides.createdAt ?? '2026-08-24T10:15:00Z',
    finishedAt: overrides.finishedAt === undefined ? '2026-08-24T10:21:01Z' : overrides.finishedAt,
  };
}

export function dashboard(
  recentScans: ScanSummary[],
  extra: Partial<Omit<Dashboard, 'recentScans' | 'agency'>> = {},
): Dashboard {
  return {
    agency: AGENCY,
    seats: { used: 2, limit: 5 },
    clientCount: extra.clientCount ?? Math.max(1, new Set(recentScans.map((s) => s.clientId)).size),
    scanCount: extra.scanCount ?? recentScans.length,
    recentScans,
    isEmpty: extra.isEmpty ?? false,
  };
}

/** A brand-new agency — the state Epic 1's acceptance criterion names. */
export const emptyDashboard: Dashboard = {
  agency: AGENCY,
  seats: { used: 1, limit: 5 },
  clientCount: 0,
  scanCount: 0,
  recentScans: [],
  isEmpty: true,
};

/** Clients exist but none has been scanned. `isEmpty` is false — a real gap. */
export const noScansYetDashboard: Dashboard = dashboard([], {
  clientCount: 2,
  scanCount: 0,
  isEmpty: false,
});

export const scoredDashboard: Dashboard = dashboard([scan()]);

/** INSUFFICIENT_DATA. Null, and never a zero. */
export const insufficientDataDashboard: Dashboard = dashboard([
  scan({ compositeScore: null, clientName: 'Quiet Signal', clientDomain: 'quietsignal.example' }),
]);

/** The LEFT join's reason for existing: a queued scan must not vanish. */
export const unscoredQueuedDashboard: Dashboard = dashboard([
  scan({
    status: 'queued',
    compositeScore: null,
    finishedAt: null,
    clientName: 'Queued Co',
    clientDomain: 'queued.example',
  }),
]);

/**
 * Detection ran; no scan ever did — Epic 9.6's regression scenario.
 *
 * `POST /clients/{clientId}/competitors` opens a scan for the CompetitorSet to
 * hang off. If nobody runs a scan afterwards, that QUEUED row stays open
 * indefinitely. It must NOT disable re-run for the client: nothing is running,
 * and re-run is precisely the action that would use it.
 */
export const detectionOnlyDashboard: Dashboard = dashboard([
  scan({
    status: 'queued',
    compositeScore: null,
    finishedAt: null,
    clientName: 'Detected Only',
    clientDomain: 'detected-only.example',
  }),
]);

/** A scan in flight — the re-run action must not offer to start a second. */
export const runningDashboard: Dashboard = dashboard([
  scan({
    status: 'running',
    compositeScore: null,
    finishedAt: null,
    clientName: 'Midflight',
    clientDomain: 'midflight.example',
  }),
]);

/**
 * The same client twice: an older finished scan and a newer running one.
 * Neither row may offer a re-run.
 */
export const runningPlusFinishedDashboard: Dashboard = dashboard([
  scan({
    id: 'scan_01M0SCANFIXTURE000000009',
    status: 'running',
    compositeScore: null,
    finishedAt: null,
  }),
  scan({ id: 'scan_01M0SCANFIXTURE000000008', status: 'succeeded', compositeScore: '41.20' }),
]);

/** PARTIAL is a real outcome with a real score behind it. */
export const partialDashboard: Dashboard = dashboard([
  scan({ status: 'partial', compositeScore: '55.82', clientName: 'Linear', clientDomain: 'linear.app' }),
]);

export const failedDashboard: Dashboard = dashboard([
  scan({ status: 'failed', compositeScore: null, finishedAt: '2026-08-24T10:16:30Z' }),
]);

/** Newest first, the order the endpoint returns and the screen must preserve. */
export const newestFirstDashboard: Dashboard = dashboard([
  scan({ id: 'scan_01M0SCANFIXTURE000000003', clientId: 'clnt_3', clientName: 'Third', clientDomain: 'c3.example' }),
  scan({ id: 'scan_01M0SCANFIXTURE000000002', clientId: 'clnt_2', clientName: 'Second', clientDomain: 'c2.example' }),
  scan({ id: 'scan_01M0SCANFIXTURE000000001', clientId: 'clnt_1', clientName: 'First', clientDomain: 'c1.example' }),
]);

/**
 * A full page at the endpoint's default limit of 10, against a scanCount of 42.
 * The screen must render the page it was given and not imply it is everything.
 */
export const fullPageDashboard: Dashboard = dashboard(
  Array.from({ length: 10 }, (_, i) =>
    scan({
      id: `scan_page_${String(i).padStart(2, '0')}`,
      clientId: `clnt_page_${i}`,
      clientName: `Client ${i}`,
      clientDomain: `client${i}.example`,
    }),
  ),
  { clientCount: 12, scanCount: 42 },
);

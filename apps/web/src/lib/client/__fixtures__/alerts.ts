/**
 * Alert fixtures — Epic E.
 *
 * Shaped after the one real event in `avp_dev`: Notion's net tone fell 58%,
 * 60% and 54% across all three engines between the scans of 29 and 31 August.
 * Three alerts on ONE scan is not a contrived case — it is what a per-engine
 * measure does when the whole picture moves — and it is the case the trend
 * annotation has to collapse into a single marker.
 */

import type { AlertFeed } from '@avp/shared-types';

const SCAN = '2026-08-31T02:39:36.000Z';
const BASELINE = '2026-08-29T04:55:27.000Z';

export const toneDeclineFeed: AlertFeed = {
  clientId: 'clnt_01AAA',
  minBaselineHours: 20,
  scansTotal: 2,
  scansCompared: 1,
  unacknowledged: 3,
  alerts: [
    {
      id: 'alrt_03',
      kind: 'sentiment_decline',
      detail: 'Net tone fell 60%, from +10 to +4.',
      engine: 'claude_search',
      scanId: 'scan_02',
      baselineScanId: 'scan_01',
      scannedAt: SCAN,
      baselineScannedAt: BASELINE,
      createdAt: SCAN,
      acknowledgedAt: null,
    },
    {
      id: 'alrt_02',
      kind: 'sentiment_decline',
      detail: 'Net tone fell 58%, from +12 to +5.',
      engine: 'claude',
      scanId: 'scan_02',
      baselineScanId: 'scan_01',
      scannedAt: SCAN,
      baselineScannedAt: BASELINE,
      createdAt: SCAN,
      acknowledgedAt: null,
    },
    {
      id: 'alrt_01',
      kind: 'sentiment_decline',
      detail: 'Net tone fell 54%, from +13 to +6.',
      engine: 'chatgpt',
      scanId: 'scan_02',
      baselineScanId: 'scan_01',
      scannedAt: SCAN,
      baselineScannedAt: BASELINE,
      createdAt: SCAN,
      acknowledgedAt: null,
    },
  ],
};

/** One acknowledged among three, so the split is visible. */
export const partlyAcknowledgedFeed: AlertFeed = {
  ...toneDeclineFeed,
  unacknowledged: 2,
  alerts: toneDeclineFeed.alerts.map((a, i) =>
    i === 0 ? { ...a, acknowledgedAt: '2026-09-01T09:00:00.000Z' } : a,
  ),
};

/**
 * Checked, and genuinely clear. `scansCompared` is 1 — a comparison happened
 * and produced nothing.
 */
export const allClearFeed: AlertFeed = {
  clientId: 'clnt_01AAA',
  minBaselineHours: 20,
  scansTotal: 2,
  scansCompared: 1,
  unacknowledged: 0,
  alerts: [],
};

/**
 * Never checked. `scansCompared` is 0 — every scan is a first scan or a
 * re-run, so nothing has ever had a baseline. The real state of nine of the
 * eleven clients in `avp_dev`, and the one an empty feed must not report as
 * an all-clear.
 */
export const neverComparedFeed: AlertFeed = {
  clientId: 'clnt_01AAA',
  minBaselineHours: 20,
  scansTotal: 3,
  scansCompared: 0,
  unacknowledged: 0,
  alerts: [],
};

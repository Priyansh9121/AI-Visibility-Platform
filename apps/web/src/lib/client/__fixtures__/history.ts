/**
 * Client-history fixtures — Epic 9.20.
 *
 * The three cases the brief names, and they are chosen because each one breaks
 * a different naive implementation:
 *
 *   - `oneScanHistory`      — a trend with one point is not a trend.
 *   - `threeScanHistory`    — the ordinary case, stable competitor set.
 *   - `shiftingSetHistory`  — a rival appears, vanishes and returns, and
 *                             another arrives late. This is the one that
 *                             separates "gap" from "zero".
 *
 * Shaped like the real `/clients/{id}/history` response, with figures in the
 * range the live `plausible.io` scans actually produced (share of voice in the
 * mid-30s for the subject, rivals from ~0.5 to ~22) so a rendered fixture looks
 * like the product rather than like test data.
 */

import type { ClientHistory, HistoryCitedDomain, HistoryScan } from '@avp/shared-types';

function domain(
  name: string,
  citations: number,
  citesSubject = false,
  competitorName: string | null = null,
): HistoryCitedDomain {
  return { domain: name, citations, citesSubject, competitorName };
}

function scan(
  id: string,
  when: string,
  opts: {
    status?: string;
    composite?: string | null;
    sov?: string | null;
    domains?: HistoryCitedDomain[];
    rivals?: [string, string][];
    /**
     * Per-engine tone — Epic A. `[engine, positive, neutral, negative,
     * unclassified]`. Omitted entirely for a scan predating the field, which
     * is a real state: every scan run before the tab existed has tone stored
     * (it has been classified since Epic 4) but a fixture is free to model the
     * empty case too.
     */
    tone?: [string, number, number, number, number][];
  } = {},
): HistoryScan {
  return {
    scanId: id,
    status: opts.status ?? 'succeeded',
    scannedAt: when,
    composite: opts.composite ?? '58.50',
    shareOfVoice: opts.sov ?? '36.27',
    citedDomains: opts.domains ?? [],
    competitors: (opts.rivals ?? []).map(([name, sov], i) => ({
      competitorId: `cmp_${name.toLowerCase().replace(/\W/g, '')}`,
      name,
      mentionRate: '40.00',
      shareOfVoice: sov,
      citationStrength: '10.00',
    })),
    sentiment: (opts.tone ?? []).map(
      ([engine, positive, neutral, negative, unclassified]) => ({
        engine,
        positive,
        neutral,
        negative,
        unclassified,
      }),
    ),
  } as HistoryScan;
}

const SUBJECT = 'plausible.io';

export const oneScanHistory: ClientHistory = {
  clientId: 'clnt_one',
  name: 'Plausible Analytics',
  domain: SUBJECT,
  scansWithoutData: 0,
  scans: [
    scan('scan_1', '2026-08-29T01:37:00Z', {
      domains: [domain(SUBJECT, 10, true), domain('matomo.org', 16)],
      rivals: [
        ['Matomo', '21.76'],
        ['Fathom Analytics', '9.84'],
      ],
    }),
  ],
};

/** One usable scan, and two that produced nothing. Both facts must survive. */
export const oneScanPlusDeadHistory: ClientHistory = {
  ...oneScanHistory,
  scansWithoutData: 2,
};

export const noScanHistory: ClientHistory = {
  clientId: 'clnt_none',
  name: 'Northaven Dental',
  domain: 'northaven-dental.com',
  scansWithoutData: 0,
  scans: [],
};

export const threeScanHistory: ClientHistory = {
  clientId: 'clnt_three',
  name: 'Plausible Analytics',
  domain: SUBJECT,
  scansWithoutData: 0,
  scans: [
    scan('scan_1', '2026-08-27T01:37:00Z', {
      composite: '58.75',
      sov: '36.41',
      tone: [
        ['chatgpt', 6, 2, 1, 3],
        ['claude', 4, 3, 2, 1],
        ['claude_search', 5, 1, 2, 4],
      ],
      domains: [
        domain(SUBJECT, 10, true),
        domain('matomo.org', 16, false, 'Matomo'),
        domain('posthog.com', 12, false, 'PostHog'),
      ],
      rivals: [
        ['Matomo', '21.76'],
        ['Fathom Analytics', '9.84'],
        ['PostHog', '12.95'],
      ],
    }),
    scan('scan_2', '2026-08-28T03:48:00Z', {
      status: 'partial',
      composite: '57.82',
      sov: '35.03',
      // `claude_search` is absent — a PARTIAL scan, one engine down. It must
      // draw no column rather than four zeros.
      tone: [
        ['chatgpt', 2, 1, 7, 2],
        ['claude', 5, 2, 1, 4],
      ],
      domains: [
        domain(SUBJECT, 8, true),
        domain('matomo.org', 19, false, 'Matomo'),
        domain('posthog.com', 9, false, 'PostHog'),
      ],
      rivals: [
        ['Matomo', '24.10'],
        ['Fathom Analytics', '8.20'],
        ['PostHog', '11.40'],
      ],
    }),
    scan('scan_3', '2026-08-29T04:32:00Z', {
      composite: '58.50',
      sov: '36.27',
      // ChatGPT's net across the three scans is exactly 0 — as much praise as
      // criticism. A deliberate case: zero is a real answer and must never
      // render the same as "never measured".
      tone: [
        ['chatgpt', 1, 2, 1, 1],
        ['claude', 6, 2, 1, 2],
        ['claude_search', 4, 3, 1, 3],
      ],
      domains: [
        domain(SUBJECT, 14, true),
        domain('matomo.org', 13, false, 'Matomo'),
        domain('posthog.com', 15, false, 'PostHog'),
      ],
      rivals: [
        ['Matomo', '20.10'],
        ['Fathom Analytics', '10.60'],
        ['PostHog', '13.30'],
      ],
    }),
  ],
};

/**
 * THE CASE THAT BREAKS NAIVE IMPLEMENTATIONS.
 *
 * `Fathom Analytics` is in scans 1 and 3 but NOT scan 2 — detection re-runs per
 * scan and did not surface it that day. `Seline` arrives only in scan 3.
 *
 * A correct chart draws Fathom as two segments with a hole in the middle and
 * Seline as a single late point; it must not join Fathom through zero (which
 * asserts a collapse that did not happen) and must not drop either rival.
 */
export const shiftingSetHistory: ClientHistory = {
  clientId: 'clnt_shift',
  name: 'Plausible Analytics',
  domain: SUBJECT,
  scansWithoutData: 0,
  scans: [
    scan('scan_1', '2026-08-27T01:37:00Z', {
      sov: '36.41',
      domains: [domain(SUBJECT, 10, true), domain('matomo.org', 16, false, 'Matomo')],
      rivals: [
        ['Matomo', '21.76'],
        ['Fathom Analytics', '9.84'],
      ],
    }),
    scan('scan_2', '2026-08-28T03:48:00Z', {
      sov: '35.03',
      domains: [domain(SUBJECT, 8, true), domain('matomo.org', 19, false, 'Matomo')],
      // Fathom is simply absent — not present with a zero.
      rivals: [['Matomo', '24.10']],
    }),
    scan('scan_3', '2026-08-29T04:32:00Z', {
      sov: '36.27',
      domains: [domain(SUBJECT, 14, true), domain('matomo.org', 13, false, 'Matomo')],
      rivals: [
        ['Matomo', '20.10'],
        ['Fathom Analytics', '10.60'],
        ['Seline', '0.52'],
      ],
    }),
  ],
};

/**
 * Scanned, and never once named — Epic A.
 *
 * Every answer omitted the client, so the classifier was never called and there
 * is no tone anywhere. The Sentiment tab must read this as the FINDING it is,
 * not as an empty screen, and must not report it as neutral.
 */
export const neverNamedHistory: ClientHistory = {
  clientId: 'clnt_absent',
  name: 'Quietbrook',
  domain: 'quietbrook.io',
  scansWithoutData: 0,
  scans: [
    scan('scan_q1', '2026-08-27T01:00:00Z', {
      composite: null,
      sov: null,
      tone: [
        ['chatgpt', 0, 0, 0, 8],
        ['claude', 0, 0, 0, 8],
      ],
    }),
  ],
};

/** One scan with tone. A single scan IS a readable tide — see `hasSentiment`. */
export const oneScanTonedHistory: ClientHistory = {
  ...oneScanHistory,
  scans: [
    scan('scan_only', '2026-08-27T01:37:00Z', {
      tone: [['chatgpt', 5, 2, 1, 2]],
    }),
  ],
};

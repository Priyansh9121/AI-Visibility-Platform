/**
 * Trend derivation — Epic 9.20.
 *
 * The assertions that carry weight are all about the SHAPE of the data
 * changing between scans, because that is where a trend built from a
 * re-detected competitor set quietly lies. A rival missing from one scan is not
 * a rival at zero, and neither is a domain that fell below what a scan
 * recorded.
 */

import { describe, it, expect } from 'vitest';
import {
  MAX_SERIES,
  alertAnnotations,
  hasTrend,
  intermittentRivals,
  rankingSeries,
  sourceSeries,
  trendPoints,
} from './trends';
import type { ClientHistory } from '@avp/shared-types';
import {
  noScanHistory,
  oneScanHistory,
  oneScanPlusDeadHistory,
  shiftingSetHistory,
  threeScanHistory,
} from './__fixtures__/history';

describe('one scan is not a trend', () => {
  it('says so for a single scan', () => {
    expect(hasTrend(oneScanHistory)).toBe(false);
  });

  it('says so for no scans at all', () => {
    expect(hasTrend(noScanHistory)).toBe(false);
  });

  it('is a trend from two readings onward', () => {
    expect(hasTrend(threeScanHistory)).toBe(true);
  });

  it('scans that produced nothing do not make a trend out of one reading', () => {
    // Three scans exist; one produced a reading. That is still one point.
    expect(oneScanPlusDeadHistory.scansWithoutData).toBe(2);
    expect(hasTrend(oneScanPlusDeadHistory)).toBe(false);
  });
});

describe('the x-axis', () => {
  it('has one column per scan, oldest first', () => {
    const points = trendPoints(threeScanHistory);
    expect(points).toHaveLength(3);
    expect(points.map((p) => p.label)).toEqual(['27 Aug', '28 Aug', '29 Aug']);
  });

  it('labels in UTC, so a test and a browser agree', () => {
    // 2026-08-29T04:32Z is the 29th in UTC and could be the 28th or 29th
    // locally. `lib/dates.ts` made the same choice for the same reason.
    const [, , third] = trendPoints(threeScanHistory);
    expect(third!.label).toBe('29 Aug');
    expect(third!.stamp).toBe('2026-08-29T04:32:00Z');
  });
});

describe('rankings — share of voice', () => {
  it('always includes the client, marked as the subject', () => {
    const series = rankingSeries(threeScanHistory);
    const subject = series.filter((s) => s.isSubject);
    expect(subject).toHaveLength(1);
    expect(subject[0]!.label).toBe('Plausible Analytics');
    expect(subject[0]!.values).toEqual([36.41, 35.03, 36.27]);
  });

  it('gives exactly one series the subject flag — the palette rule depends on it', () => {
    // design-direction.md §1: the client is beacon-600 and competitors are
    // neutral. Two subjects would put a rival in the brand colour.
    const series = rankingSeries(shiftingSetHistory);
    expect(series.filter((s) => s.isSubject)).toHaveLength(1);
  });

  it('every series spans every point, so nothing is silently shorter', () => {
    const series = rankingSeries(shiftingSetHistory);
    for (const s of series) {
      expect(s.values).toHaveLength(shiftingSetHistory.scans.length);
    }
  });

  /* ---- the case the module exists for ---- */

  it('a rival missing from a scan is NULL, never zero', () => {
    // Fathom is in scans 1 and 3, absent from 2. Zero would assert its share
    // collapsed to nothing that day; it was not measured.
    const fathom = rankingSeries(shiftingSetHistory).find(
      (s) => s.label === 'Fathom Analytics',
    );
    expect(fathom).toBeDefined();
    expect(fathom!.values).toEqual([9.84, null, 10.6]);
    expect(fathom!.values).not.toContain(0);
  });

  it('a rival that only ever appears once keeps its line', () => {
    // Seline arrives in scan 3 only. Dropping it would show fewer rivals than
    // the client has, with nothing saying so.
    const seline = rankingSeries(shiftingSetHistory).find((s) => s.label === 'Seline');
    expect(seline).toBeDefined();
    expect(seline!.values).toEqual([null, null, 0.52]);
  });

  it('names the rivals whose lines will have gaps', () => {
    expect(intermittentRivals(shiftingSetHistory)).toEqual(['Fathom Analytics', 'Seline']);
  });

  it('names none when the set never changed', () => {
    expect(intermittentRivals(threeScanHistory)).toEqual([]);
  });

  it('ranks rivals by their PEAK, so a rival that faded is still drawn', () => {
    // Ranking by the latest value would drop exactly the story worth telling.
    const labels = rankingSeries(threeScanHistory)
      .filter((s) => !s.isSubject)
      .map((s) => s.label);
    expect(labels[0]).toBe('Matomo');
    expect(labels).toContain('Fathom Analytics');
  });

  it('caps the series count, subject included', () => {
    expect(rankingSeries(threeScanHistory, 3)).toHaveLength(3);
    expect(rankingSeries(threeScanHistory, 3)[0]!.isSubject).toBe(true);
  });

  it('MAX_SERIES leaves room for the client plus rivals', () => {
    expect(MAX_SERIES).toBeGreaterThan(1);
  });
});

describe('sources — citations per domain', () => {
  it('pins the client own domain as the subject series', () => {
    const series = sourceSeries(threeScanHistory);
    const subject = series.filter((s) => s.isSubject);
    expect(subject).toHaveLength(1);
    expect(subject[0]!.label).toBe('plausible.io');
    expect(subject[0]!.values).toEqual([10, 8, 14]);
  });

  it('keeps the client even when rivals out-cite it', () => {
    // matomo.org totals 48 citations against plausible.io's 32. "Who cites
    // you" is the question this screen answers; dropping the client for being
    // outranked would answer a different one.
    const series = sourceSeries(threeScanHistory, 2);
    expect(series.some((s) => s.label === 'plausible.io')).toBe(true);
  });

  it('ranks by total across the history, not by the latest scan', () => {
    const labels = sourceSeries(threeScanHistory).map((s) => s.label);
    // matomo.org 16+19+13 = 48 beats posthog.com 12+9+15 = 36.
    expect(labels.indexOf('matomo.org')).toBeLessThan(labels.indexOf('posthog.com'));
  });

  it('a domain absent from a complete scan list really is zero', () => {
    // These fixtures carry two or three domains per scan, far below the 40-row
    // cap, so the list is complete and an absent domain is a measured zero
    // rather than an unknown.
    const series = sourceSeries(shiftingSetHistory);
    for (const s of series) {
      expect(s.values).toHaveLength(3);
      expect(s.values.every((v) => v !== null)).toBe(true);
    }
  });

  it('caps the series count', () => {
    expect(sourceSeries(threeScanHistory, 2)).toHaveLength(2);
  });
});

describe('the axis says which scan is which', () => {
  it('shows the clock when every scan landed on one day', () => {
    // Re-running a client twice in an afternoon is ordinary. Three columns all
    // reading "29 Aug" say nothing about order. Found live on the real
    // plausible.io history: 01:37, 03:48 and 04:32 on one date.
    const sameDay: ClientHistory = {
      ...threeScanHistory,
      scans: threeScanHistory.scans.map((s, i) => ({
        ...s,
        scannedAt: `2026-08-29T0${i + 1}:3${i}:00Z`,
      })),
    };
    expect(trendPoints(sameDay).map((p) => p.label)).toEqual(['01:30', '02:31', '03:32']);
  });

  it('shows the day when the scans span more than one', () => {
    expect(trendPoints(threeScanHistory).map((p) => p.label)).toEqual([
      '27 Aug',
      '28 Aug',
      '29 Aug',
    ]);
  });

  it('keeps the machine stamp whichever label is chosen', () => {
    // The hidden data table and React keys both read the stamp, so it must not
    // change with the display format.
    for (const p of trendPoints(threeScanHistory)) {
      expect(p.stamp).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }
  });

  it('a single scan keeps the day label — there is no order to disambiguate', () => {
    expect(trendPoints(oneScanHistory)[0]!.label).toBe('29 Aug');
  });
});

describe('alertAnnotations — Epic E', () => {
  const at = (stamp: string, id: string, detail: string) => ({
    id,
    kind: 'sentiment_decline',
    detail,
    engine: 'claude',
    scanId: 's',
    baselineScanId: 'b',
    scannedAt: stamp,
    baselineScannedAt: '2026-08-29T00:00:00.000Z',
    createdAt: stamp,
    acknowledgedAt: null as string | null,
  });
  const feedOf = (alerts: ReturnType<typeof at>[]) =>
    ({
      clientId: 'c',
      alerts,
      unacknowledged: alerts.length,
      scansTotal: 2,
      scansCompared: 1,
      minBaselineHours: 20,
    }) as never;

  it('keys on the stamp trendPoints uses, so a marker cannot land on the wrong column', () => {
    const stamp = '2026-08-31T02:39:36.000Z';
    const out = alertAnnotations(feedOf([at(stamp, 'a', 'Tone fell.')]));
    expect(Object.keys(out)).toEqual([stamp]);
  });

  it('collapses several alerts on one scan into a single marker', () => {
    // The real Notion event: three engines, three alerts, one scan. Three
    // triangles stacked on one date would read as three separate events.
    const stamp = '2026-08-31T02:39:36.000Z';
    const out = alertAnnotations(
      feedOf([
        at(stamp, 'a', 'Tone fell 60%.'),
        at(stamp, 'b', 'Tone fell 58%.'),
        at(stamp, 'c', 'Tone fell 54%.'),
      ]),
    );
    expect(Object.keys(out)).toHaveLength(1);
    expect(out[stamp]).toContain('3 alerts');
    expect(out[stamp]).toContain('60%');
  });

  it('still annotates an acknowledged alert', () => {
    // The mark says something happened at this scan, which stays true after
    // someone has looked at it. Hiding it would make the trend disagree with
    // the feed.
    const stamp = '2026-08-31T02:39:36.000Z';
    const seen = { ...at(stamp, 'a', 'Tone fell.'), acknowledgedAt: '2026-09-01T00:00:00Z' };
    expect(Object.keys(alertAnnotations(feedOf([seen])))).toEqual([stamp]);
  });

  it('is empty rather than throwing when the feed never loaded', () => {
    // A failed alert request must cost the markers, never the chart.
    expect(alertAnnotations(null)).toEqual({});
  });
});

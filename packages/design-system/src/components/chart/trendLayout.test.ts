import { describe, it, expect } from 'vitest';
import {
  layoutTrend,
  segmentPath,
  spreadLabels,
  truncateLabel,
  LABEL_MAX_CHARS,
  type TrendPoint,
} from './trendLayout.js';

const POINTS: TrendPoint[] = [
  { label: '27 Aug', stamp: '2026-08-27T00:00:00Z' },
  { label: '28 Aug', stamp: '2026-08-28T00:00:00Z' },
  { label: '29 Aug', stamp: '2026-08-29T00:00:00Z' },
];

const subject = (values: (number | null)[]) => ({
  key: 's',
  label: 'Subject',
  isSubject: true,
  values,
});

describe('a null breaks the line — the whole point of this module', () => {
  it('splits a series into segments around a gap', () => {
    const { series } = layoutTrend(POINTS, [subject([10, null, 30])]);
    expect(series[0]!.segments).toHaveLength(2);
    expect(series[0]!.segments[0]!.points).toHaveLength(1);
    expect(series[0]!.segments[1]!.points).toHaveLength(1);
  });

  it('never plots a gap as a point, at any y', () => {
    // The failure this guards: a gap rendered at y=0 draws a line diving to
    // the axis and back, which asserts a collapse that was never measured.
    const { series } = layoutTrend(POINTS, [subject([10, null, 30])]);
    expect(series[0]!.plotted.map((p) => p.index)).toEqual([0, 2]);
  });

  it('keeps one run when there is no gap', () => {
    const { series } = layoutTrend(POINTS, [subject([10, 20, 30])]);
    expect(series[0]!.segments).toHaveLength(1);
    expect(series[0]!.segments[0]!.points).toHaveLength(3);
  });

  it('treats NaN as a gap, not as a number', () => {
    const { series } = layoutTrend(POINTS, [subject([10, Number.NaN, 30])]);
    expect(series[0]!.plotted).toHaveLength(2);
  });

  it('a series measured nowhere is empty rather than absent', () => {
    // It still occupies a row, so a legend can say "not measured" instead of
    // the reader silently seeing one fewer rival than the client has.
    const { series } = layoutTrend(POINTS, [subject([null, null, null])]);
    expect(series).toHaveLength(1);
    expect(series[0]!.empty).toBe(true);
    expect(series[0]!.segments).toHaveLength(0);
  });

  it('leading and trailing gaps do not shift the remaining points', () => {
    const { series, columns } = layoutTrend(POINTS, [subject([null, 20, null])]);
    expect(series[0]!.plotted).toHaveLength(1);
    expect(series[0]!.plotted[0]!.x).toBeCloseTo(columns[1]!);
  });
});

describe('scale', () => {
  it('fits the data when no ceiling is given', () => {
    const { yMax } = layoutTrend(POINTS, [subject([3, 7, 9])]);
    expect(yMax).toBe(9);
  });

  it('honours an explicit ceiling, which a percentage needs', () => {
    const { yMax } = layoutTrend(POINTS, [subject([36, 35, 36])], { yMax: 100 });
    expect(yMax).toBe(100);
  });

  it('bigger values sit higher on the plot', () => {
    // y grows downward in SVG, so "higher value" means SMALLER y.
    const { series } = layoutTrend(POINTS, [subject([10, 50, 90])], { yMax: 100 });
    const [a, b, c] = series[0]!.plotted;
    expect(b!.y).toBeLessThan(a!.y);
    expect(c!.y).toBeLessThan(b!.y);
  });

  it('a zero sits on the baseline and a full value at the top', () => {
    const { series, plot } = layoutTrend(POINTS, [subject([0, 50, 100])], { yMax: 100 });
    expect(series[0]!.plotted[0]!.y).toBeCloseTo(plot.y + plot.height);
    expect(series[0]!.plotted[2]!.y).toBeCloseTo(plot.y);
  });

  it('does not overflow the plot when a value exceeds the ceiling', () => {
    const { series, plot } = layoutTrend(POINTS, [subject([10, 140, 30])], { yMax: 100 });
    expect(series[0]!.plotted[1]!.y).toBeGreaterThanOrEqual(plot.y);
  });

  it('survives a series with no numbers at all rather than dividing by zero', () => {
    const { yMax } = layoutTrend(POINTS, [subject([null, null, null])]);
    expect(Number.isFinite(yMax)).toBe(true);
    expect(yMax).toBeGreaterThan(0);
  });
});

describe('columns', () => {
  it('spreads points across the plot, first at the left edge and last at the right', () => {
    const { columns, plot } = layoutTrend(POINTS, [subject([1, 2, 3])]);
    expect(columns[0]).toBeCloseTo(plot.x);
    expect(columns[2]).toBeCloseTo(plot.x + plot.width);
  });

  it('centres a lone point rather than pinning it to the axis', () => {
    // A single dot at x=0 reads as a chart that has been cut off.
    const { columns, plot } = layoutTrend([POINTS[0]!], [subject([42])]);
    expect(columns[0]).toBeCloseTo(plot.x + plot.width / 2);
  });
});

describe('series identity', () => {
  it('indexes competitors from zero and leaves the subject out of that run', () => {
    // `seriesStyle('competitor', i)` cycles the neutral palette on this index,
    // so the subject must not consume a slot in it.
    const { series } = layoutTrend(POINTS, [
      { key: 'a', label: 'A', values: [1, 2, 3] },
      { key: 's', label: 'S', isSubject: true, values: [1, 2, 3] },
      { key: 'b', label: 'B', values: [1, 2, 3] },
    ]);
    expect(series.map((s) => s.seriesIndex)).toEqual([0, -1, 1]);
  });

  it('reports first and last only when there are two readings to compare', () => {
    const { series } = layoutTrend(POINTS, [subject([10, 20, 30]), subject([null, 20, null])]);
    expect(series[0]!.first).toBe(10);
    expect(series[0]!.last).toBe(30);
    expect(series[1]!.first).toBeNull();
    expect(series[1]!.last).toBeNull();
  });
});

describe('segmentPath', () => {
  it('moves once then lines, so a segment is one stroke', () => {
    const { series } = layoutTrend(POINTS, [subject([10, 20, 30])]);
    const d = segmentPath(series[0]!.segments[0]!);
    expect(d.startsWith('M')).toBe(true);
    expect(d.match(/M/g)).toHaveLength(1);
    expect(d.match(/L/g)).toHaveLength(2);
  });

  it('draws straight lines — a spline would invent readings between scans', () => {
    const d = segmentPath(layoutTrend(POINTS, [subject([10, 20, 30])]).series[0]!.segments[0]!);
    expect(d).not.toMatch(/[CQSTA]/);
  });
});

describe('spreadLabels — found in a browser, not by a test', () => {
  it('separates labels that would overwrite each other', () => {
    const out = spreadLabels([
      { key: 'a', y: 100 },
      { key: 'b', y: 103 },
      { key: 'c', y: 105 },
    ], 13);
    expect(out.a).toBe(100);
    expect(out.b).toBe(113);
    expect(out.c).toBe(126);
  });

  it('leaves labels that already clear each other alone', () => {
    const out = spreadLabels([{ key: 'a', y: 10 }, { key: 'b', y: 90 }], 13);
    expect(out).toEqual({ a: 10, b: 90 });
  });

  it('never reorders — a reader tracing a line must not cross another name', () => {
    const out = spreadLabels([
      { key: 'low', y: 200 },
      { key: 'high', y: 20 },
      { key: 'mid', y: 100 },
    ], 13);
    expect(out.high).toBeLessThan(out.mid!);
    expect(out.mid).toBeLessThan(out.low!);
  });

  it('handles a single label and none at all', () => {
    expect(spreadLabels([{ key: 'only', y: 42 }])).toEqual({ only: 42 });
    expect(spreadLabels([])).toEqual({});
  });
});

describe('truncateLabel', () => {
  it('leaves a name that fits alone', () => {
    expect(truncateLabel('plausible.io')).toBe('plausible.io');
  });

  it('shortens one that would run off the plot', () => {
    const long = 'analytics-alternatives-and-more.com';
    const out = truncateLabel(long);
    expect(out.length).toBeLessThanOrEqual(LABEL_MAX_CHARS);
    expect(out.endsWith('…')).toBe(true);
  });

  it('only ever shortens the DRAWN label', () => {
    // The hidden data table renders `s.label` directly, so the full name is
    // still available to a screen reader and to an export.
    expect(truncateLabel('x'.repeat(40)) === 'x'.repeat(40)).toBe(false);
  });
});

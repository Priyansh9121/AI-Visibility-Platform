import { describe, it, expect } from 'vitest';
import {
  layoutTide,
  netByEngine,
  type TideBuckets,
  type TidePointInput,
} from './sentimentTideLayout.js';

const b = (
  positive: number,
  neutral: number,
  negative: number,
  unclassified = 0,
): TideBuckets => ({ positive, neutral, negative, unclassified });

const POINTS: TidePointInput[] = [
  {
    label: '27 Aug',
    stamp: '2026-08-27T00:00:00Z',
    byEngine: { chatgpt: b(6, 2, 1, 3), claude: b(4, 3, 2, 1) },
  },
  {
    label: '29 Aug',
    stamp: '2026-08-29T00:00:00Z',
    byEngine: { chatgpt: b(2, 1, 7, 2), claude: b(5, 2, 1, 4) },
  },
];

describe('the waterline is what makes it readable at a glance', () => {
  it('is placed by the data, so a lopsided chart wastes no half', () => {
    // Measured on the real plausible.io history: tone is overwhelmingly
    // positive, and a centred waterline left the bottom 45% of the figure
    // empty. An all-positive chart puts zero at the FLOOR and spends the whole
    // plot on the data that exists.
    const up = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(5, 0, 0) } }]);
    expect(up.waterline).toBeCloseTo(up.plot.y + up.plot.height);
    const down = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(0, 0, 5) } }]);
    expect(down.waterline).toBeCloseTo(down.plot.y);
  });

  it('still draws all-positive and all-negative as mirror images', () => {
    // The property centring was chosen for, kept without the dead space: the
    // BARS are identical, only the zero line moves.
    const up = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(5, 0, 0) } }]);
    const down = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(0, 0, 5) } }]);
    expect(up.bars[0]!.positive.height).toBeCloseTo(down.bars[0]!.negative.height);
  });

  it('keeps ONE unit scale across both directions', () => {
    // The property that actually matters. Five positive and five negative must
    // be the same length, or the chart lies about magnitude.
    const l = layoutTide([
      { label: 'a', stamp: 'a', byEngine: { up: b(5, 0, 0), down: b(0, 0, 5) } },
    ]);
    const upBar = l.bars.find((x) => x.engine === 'up')!;
    const downBar = l.bars.find((x) => x.engine === 'down')!;
    expect(upBar.positive.height).toBeCloseTo(downBar.negative.height);
  });

  it('parks zero in the middle when there is nothing either way', () => {
    const l = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(0, 0, 0, 4) } }]);
    expect(l.waterline).toBeCloseTo(l.plot.y + l.plot.height / 2);
  });

  it('puts positive above the waterline and negative below it', () => {
    const l = layoutTide(POINTS);
    for (const bar of l.bars) {
      if (bar.positive.height > 0) {
        expect(bar.positive.y + bar.positive.height).toBeLessThanOrEqual(l.waterline + 0.001);
      }
      if (bar.negative.height > 0) {
        expect(bar.negative.y).toBeGreaterThanOrEqual(l.waterline - 0.001);
      }
    }
  });

  it('straddles the waterline with neutral, so it implies neither direction', () => {
    const l = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(0, 4, 0) } }]);
    const bar = l.bars[0]!;
    const above = l.waterline - bar.neutral.y;
    const below = bar.neutral.y + bar.neutral.height - l.waterline;
    expect(above).toBeCloseTo(below);
  });

  it('shares one scale between the two halves', () => {
    // Two independent scales would make a small negative look like a large one.
    const l = layoutTide([
      { label: 'a', stamp: 'a', byEngine: { e: b(10, 0, 1) } },
    ]);
    const bar = l.bars[0]!;
    expect(bar.positive.height / 10).toBeCloseTo(bar.negative.height / 1, 5);
  });

  it('gives a lopsided chart its space in proportion, not in halves', () => {
    // 10 up against 1 down: the positive side gets ~10/11 of the plot.
    const l = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(10, 0, 1) } }]);
    const above = l.waterline - l.plot.y;
    expect(above / l.plot.height).toBeCloseTo(10 / 11, 3);
  });

  it('never divides by a zero scale', () => {
    const l = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(0, 0, 0, 9) } }]);
    expect(l.scale).toBeGreaterThan(0);
    expect(Number.isFinite(l.bars[0]!.positive.height)).toBe(true);
    expect(l.empty).toBe(true);
  });
});

describe('"not named" is not a neutral, and not a bar', () => {
  it('carries the count but gives it no geometry', () => {
    const l = layoutTide(POINTS);
    const bar = l.bars.find((x) => x.engine === 'chatgpt' && x.pointIndex === 0)!;
    expect(bar.unclassified.count).toBe(3);
    // No y, no height — see the module note. A fourth rect on a chart of tones
    // would be read as a fourth tone.
    expect(bar.unclassified).toEqual({ count: 3 });
  });

  it('keeps it out of the neutral band', () => {
    const withNone = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(1, 1, 1, 0) } }]);
    const withMany = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(1, 1, 1, 50) } }]);
    expect(withMany.bars[0]!.neutral.height).toBeCloseTo(withNone.bars[0]!.neutral.height);
  });

  it('keeps it out of the classified denominator', () => {
    const l = layoutTide(POINTS);
    const bar = l.bars.find((x) => x.engine === 'chatgpt' && x.pointIndex === 0)!;
    expect(bar.classified).toBe(6 + 2 + 1);
  });

  it('reports a scan of nothing but unnamed answers as empty', () => {
    // Every answer omitted the client. There is no tone to plot, and the caller
    // must say so rather than drawing a flat line on the waterline.
    const l = layoutTide([{ label: 'a', stamp: 'a', byEngine: { e: b(0, 0, 0, 12) } }]);
    expect(l.empty).toBe(true);
  });
});

describe('an engine that did not answer has no column', () => {
  it('drops it rather than drawing four zeros', () => {
    const l = layoutTide([
      { label: 'a', stamp: 'a', byEngine: { chatgpt: b(1, 0, 0), claude: b(1, 0, 0) } },
      { label: 'b', stamp: 'b', byEngine: { chatgpt: b(2, 0, 0) } },
    ]);
    expect(l.bars.filter((x) => x.pointIndex === 1)).toHaveLength(1);
    expect(l.bars.some((x) => x.pointIndex === 1 && x.engine === 'claude')).toBe(false);
  });

  it('reports the absence so the caller can name it', () => {
    const l = layoutTide([
      { label: 'a', stamp: 'a', byEngine: { chatgpt: b(1, 0, 0), claude: b(1, 0, 0) } },
      { label: 'b', stamp: 'b', byEngine: { chatgpt: b(2, 0, 0) } },
    ]);
    expect(l.missing).toEqual([{ pointIndex: 1, engines: ['claude'] }]);
  });

  it('keeps the engine in the stable order anyway, so hues do not shift', () => {
    const l = layoutTide([
      { label: 'a', stamp: 'a', byEngine: { chatgpt: b(1, 0, 0), claude: b(1, 0, 0) } },
      { label: 'b', stamp: 'b', byEngine: { chatgpt: b(2, 0, 0) } },
    ]);
    expect(l.engines).toEqual(['chatgpt', 'claude']);
  });
});

describe('engine order is stable', () => {
  it('sorts rather than taking first-seen order', () => {
    // First-seen would reorder the whole chart the day the earliest scan lost
    // an engine, changing every column's hue for a reason nobody asked for.
    const a = layoutTide([{ label: 'x', stamp: 'x', byEngine: { zeta: b(1, 0, 0), alpha: b(1, 0, 0) } }]);
    expect(a.engines).toEqual(['alpha', 'zeta']);
  });

  it('gives each engine the same index at every point', () => {
    const l = layoutTide(POINTS);
    const indices = new Map<string, Set<number>>();
    for (const bar of l.bars) {
      indices.set(bar.engine, (indices.get(bar.engine) ?? new Set()).add(bar.engineIndex));
    }
    for (const set of indices.values()) expect(set.size).toBe(1);
  });
});

describe('geometry', () => {
  it('is deterministic — the same input always lays out the same', () => {
    // These columns get compared across months.
    expect(layoutTide(POINTS)).toEqual(layoutTide(POINTS));
  });

  it('keeps every bar inside the plot', () => {
    const l = layoutTide(POINTS);
    for (const bar of l.bars) {
      expect(bar.x).toBeGreaterThanOrEqual(l.plot.x - 0.001);
      expect(bar.x + bar.width).toBeLessThanOrEqual(l.plot.x + l.plot.width + 0.001);
      expect(bar.positive.y).toBeGreaterThanOrEqual(l.plot.y - 0.001);
      expect(bar.negative.y + bar.negative.height).toBeLessThanOrEqual(
        l.plot.y + l.plot.height + 0.001,
      );
    }
  });

  it('survives an empty history without throwing', () => {
    const l = layoutTide([]);
    expect(l.bars).toEqual([]);
    expect(l.empty).toBe(true);
  });

  it('honours a caller-supplied width, so the 9.21 bound can be derived', () => {
    expect(layoutTide(POINTS, { width: 900 }).width).toBe(900);
  });

  it('computes net as positive minus negative', () => {
    const l = layoutTide(POINTS);
    const bar = l.bars.find((x) => x.engine === 'chatgpt' && x.pointIndex === 1)!;
    expect(bar.net).toBe(2 - 7);
  });
});

describe('netByEngine', () => {
  it('sums the direction across the whole history', () => {
    expect(netByEngine(POINTS)).toEqual([
      { engine: 'chatgpt', net: 6 - 1 + (2 - 7), classified: 9 + 10 },
      { engine: 'claude', net: 4 - 2 + (5 - 1), classified: 9 + 8 },
    ]);
  });

  it('returns null, not zero, for an engine never classified anywhere', () => {
    // Zero is a real answer — as much praise as criticism — and must not be
    // the same value as "never measured".
    const out = netByEngine([
      { label: 'a', stamp: 'a', byEngine: { quiet: b(0, 0, 0, 5) } },
    ]);
    expect(out).toEqual([{ engine: 'quiet', net: null, classified: 0 }]);
  });

  it('distinguishes a genuine zero from a null', () => {
    const out = netByEngine([
      { label: 'a', stamp: 'a', byEngine: { even: b(3, 0, 3) } },
    ]);
    expect(out[0]!.net).toBe(0);
    expect(out[0]!.classified).toBe(6);
  });
});

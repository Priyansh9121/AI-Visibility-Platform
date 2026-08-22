import { describe, it, expect } from 'vitest';
import { layoutLedger, compositeScore, type LedgerDimension } from './ledgerLayout.js';

/** The real §6 weighting, used as the primary fixture. */
const DIMS = (subscores: readonly number[]): LedgerDimension[] => [
  { key: 'mention_rate', label: 'Mention Rate', weight: 30, subscore: subscores[0]! },
  { key: 'share_of_voice', label: 'Share of Voice', weight: 25, subscore: subscores[1]! },
  { key: 'citation_strength', label: 'Citation Strength', weight: 20, subscore: subscores[2]! },
  { key: 'sentiment', label: 'Sentiment', weight: 15, subscore: subscores[3]! },
  { key: 'technical', label: 'Technical Foundation', weight: 10, subscore: subscores[4]! },
];

describe('compositeScore', () => {
  it('matches the §6 weighted sum', () => {
    // 30*40 + 25*20 + 20*10 + 15*60 + 10*80 = 1200+500+200+900+800 = 3600 -> 36
    expect(compositeScore(DIMS([40, 20, 10, 60, 80]))).toBe(36);
  });

  it('returns null, not 0, for an empty dimension set', () => {
    // An unrunnable scan must never render as a bad score.
    expect(compositeScore([])).toBeNull();
  });

  it('is 100 for a perfect scan and 0 for total absence', () => {
    expect(compositeScore(DIMS([100, 100, 100, 100, 100]))).toBe(100);
    expect(compositeScore(DIMS([0, 0, 0, 0, 0]))).toBe(0);
  });
});

describe('layoutLedger — the load-bearing identity', () => {
  it('total lit height equals the composite score as a fraction of column height', () => {
    const height = 420;
    const cases = [
      [40, 20, 10, 60, 80],
      [0, 0, 0, 0, 0],
      [100, 100, 100, 100, 100],
      [7, 93, 41, 58, 12],
      [33.33, 66.67, 50, 12.5, 87.5],
    ];
    for (const subscores of cases) {
      const layout = layoutLedger(DIMS(subscores), { height });
      const totalLit = layout.segments.reduce((s, seg) => s + seg.litHeight, 0);
      const expected = (height * layout.composite!) / 100;
      // Tolerance covers only the 3dp geometry rounding, nothing structural.
      expect(totalLit).toBeCloseTo(expected, 2);
    }
  });

  it('stacks segments contiguously from the baseline with no gaps or overlap', () => {
    const layout = layoutLedger(DIMS([40, 20, 10, 60, 80]), { height: 420 });
    // Heaviest dimension sits at the bottom.
    expect(layout.segments[0]!.key).toBe('mention_rate');
    expect(layout.segments[0]!.y + layout.segments[0]!.height).toBeCloseTo(420, 3);
    for (let i = 1; i < layout.segments.length; i++) {
      const below = layout.segments[i - 1]!;
      const above = layout.segments[i]!;
      expect(above.y + above.height).toBeCloseTo(below.y, 3);
    }
    // Topmost segment closes out the column exactly.
    expect(layout.segments.at(-1)!.y).toBeCloseTo(0, 3);
  });

  it('sizes each segment by weight, not by score', () => {
    const layout = layoutLedger(DIMS([0, 100, 0, 100, 0]), { height: 400 });
    expect(layout.segments[0]!.height).toBeCloseTo(120, 3); // 30%
    expect(layout.segments[1]!.height).toBeCloseTo(100, 3); // 25%
    expect(layout.segments[4]!.height).toBeCloseTo(40, 3); //  10%
  });

  it('lights each segment from its own bottom edge', () => {
    const layout = layoutLedger(DIMS([50, 0, 0, 0, 0]), { height: 400 });
    const seg = layout.segments[0]!;
    expect(seg.litHeight).toBeCloseTo(60, 3); // half of 120
    expect(seg.litY + seg.litHeight).toBeCloseTo(seg.y + seg.height, 3);
  });
});

describe('layoutLedger — biggest gap', () => {
  it('ranks by recoverable points, not by lowest sub-score', () => {
    // Technical scores worst (10) but is only weighted 10 -> 9 points recoverable.
    // Mention Rate scores 50 but is weighted 30 -> 15 points recoverable.
    // The heavier dimension must win; that is the higher-leverage fix.
    const layout = layoutLedger(DIMS([50, 100, 100, 100, 10]));
    expect(layout.biggestGap?.key).toBe('mention_rate');
    expect(layout.biggestGap?.gap).toBeCloseTo(15, 3);
  });

  it('marks exactly one segment as the biggest gap', () => {
    const layout = layoutLedger(DIMS([40, 20, 10, 60, 80]));
    expect(layout.segments.filter((s) => s.isBiggestGap)).toHaveLength(1);
  });

  it('breaks ties deterministically on the lowest index so headlines never reshuffle', () => {
    // Two dimensions with identical recoverable points (30*0.5=15, 25*0.6=15).
    const dims: LedgerDimension[] = [
      { key: 'a', label: 'A', weight: 30, subscore: 50 },
      { key: 'b', label: 'B', weight: 25, subscore: 40 },
      { key: 'c', label: 'C', weight: 45, subscore: 100 },
    ];
    expect(dims[0]!.weight * (100 - dims[0]!.subscore) / 100).toBeCloseTo(15, 6);
    expect(dims[1]!.weight * (100 - dims[1]!.subscore) / 100).toBeCloseTo(15, 6);
    for (let i = 0; i < 5; i++) {
      expect(layoutLedger(dims).biggestGap?.key).toBe('a');
    }
  });

  it('reports no gap for a perfect score', () => {
    expect(layoutLedger(DIMS([100, 100, 100, 100, 100])).biggestGap).toBeNull();
  });

  it('picks the heaviest dimension when a brand is invisible everywhere', () => {
    const layout = layoutLedger(DIMS([0, 0, 0, 0, 0]));
    expect(layout.biggestGap?.key).toBe('mention_rate');
    expect(layout.composite).toBe(0);
  });
});

describe('layoutLedger — edge cases from scoring-spec.md', () => {
  it('renders INSUFFICIENT_DATA rather than zero for an empty set', () => {
    const layout = layoutLedger([]);
    expect(layout.composite).toBeNull();
    expect(layout.segments).toHaveLength(0);
    expect(layout.warnings[0]).toMatch(/INSUFFICIENT_DATA/);
  });

  it('clamps out-of-range sub-scores AND warns, never silently', () => {
    const layout = layoutLedger(DIMS([140, -20, 50, 50, 50]));
    expect(layout.segments[0]!.subscore).toBe(100);
    expect(layout.segments[1]!.subscore).toBe(0);
    expect(layout.warnings).toHaveLength(2);
    expect(layout.warnings.join(' ')).toMatch(/Clamped/);
  });

  it('normalises malformed weights but warns about the scoring config', () => {
    const dims: LedgerDimension[] = [
      { key: 'a', label: 'A', weight: 40, subscore: 100 },
      { key: 'b', label: 'B', weight: 40, subscore: 0 },
    ]; // sums to 80
    const layout = layoutLedger(dims, { height: 400 });
    const total = layout.segments.reduce((s, seg) => s + seg.height, 0);
    expect(total).toBeCloseTo(400, 3); // still fills the box
    expect(layout.warnings[0]).toMatch(/Weights sum to 80/);
    expect(layout.composite).toBeCloseTo(50, 3); // renormalised, not 40
  });
});

describe('layoutLedger — competitor ghosts', () => {
  it('places each cap line at the competitor composite on the same scale', () => {
    const layout = layoutLedger(DIMS([40, 20, 10, 60, 80]), {
      height: 400,
      competitors: [
        { name: 'Competitor A', dimensions: DIMS([100, 100, 100, 100, 100]) },
        { name: 'Competitor B', dimensions: DIMS([0, 0, 0, 0, 0]) },
        { name: 'Competitor C', dimensions: DIMS([50, 50, 50, 50, 50]) },
      ],
    });
    expect(layout.ghosts[0]).toMatchObject({ composite: 100, capY: 0 });
    expect(layout.ghosts[1]).toMatchObject({ composite: 0, capY: 400 });
    expect(layout.ghosts[2]).toMatchObject({ composite: 50, capY: 200 });
  });

  it('handles a scan with no competitors detected', () => {
    expect(layoutLedger(DIMS([40, 20, 10, 60, 80])).ghosts).toEqual([]);
  });
});

describe('layoutLedger — determinism', () => {
  it('produces byte-identical output across repeated runs', () => {
    // These numbers end up in exported PDFs a client compares month to month.
    const input = DIMS([33.33, 66.67, 41.5, 12.25, 87.5]);
    const first = JSON.stringify(layoutLedger(input, { height: 420 }));
    for (let i = 0; i < 20; i++) {
      expect(JSON.stringify(layoutLedger(input, { height: 420 }))).toBe(first);
    }
  });

  it('does not depend on object key order in the input', () => {
    const a = layoutLedger([{ key: 'x', label: 'X', weight: 100, subscore: 42 }]);
    const b = layoutLedger([{ subscore: 42, weight: 100, label: 'X', key: 'x' }]);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});

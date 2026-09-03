import { describe, it, expect } from 'vitest';
import {
  layoutGapGrid,
  gapSeverity,
  type GapBrandInput,
  type GapRowInput,
  type GapRowKind,
} from './gapGridLayout.js';

const BRANDS: GapBrandInput[] = [
  { name: 'Us', isSubject: true, promptsNamed: 2 },
  { name: 'Rival', isSubject: false, promptsNamed: 3 },
];

function row(
  promptId: string,
  kind: GapRowKind,
  {
    subjectNamedOn = 0,
    rivalsNamedOn = 0,
    enginesAnswered = 3,
    absentOn = enginesAnswered - subjectNamedOn,
    subjectCited = false,
    intent = 'awareness',
  }: Partial<GapRowInput> = {},
): GapRowInput {
  return {
    promptId,
    text: `prompt ${promptId}`,
    intent,
    kind,
    enginesAnswered,
    subjectNamedOn,
    rivalsNamedOn,
    subjectCited,
    absentOn,
    cells: [
      { brand: 'Us', namedOn: subjectNamedOn },
      { brand: 'Rival', namedOn: rivalsNamedOn },
    ],
  };
}

describe('the state that must never be counted as a gap', () => {
  it('does not mark a no-brands row as a gap', () => {
    // The client is as unnamed here as in an `absent` row. The difference is
    // that nobody won it, so there was nothing to lose.
    const { rows } = layoutGapGrid([row('p1', 'no_brands', { absentOn: 3 })], BRANDS);
    expect(rows[0]!.isGap).toBe(false);
  });

  it('marks a row a rival won as a gap', () => {
    const { rows } = layoutGapGrid([row('p1', 'absent', { rivalsNamedOn: 3 })], BRANDS);
    expect(rows[0]!.isGap).toBe(true);
  });

  it('keeps the two totals separate even when both look identical per row', () => {
    const layout = layoutGapGrid(
      [row('p1', 'absent', { rivalsNamedOn: 3 }), row('p2', 'no_brands')],
      BRANDS,
    );
    expect(layout.absent).toBe(1);
    expect(layout.noBrands).toBe(1);
    // Both rows have the subject absent on every engine — the count that would
    // conflate them if `isGap` were derived from `absentOn` instead of `kind`.
    expect(layout.rows.every((r) => r.absentOn === 3)).toBe(true);
  });

  it('sorts no-brands rows below every real verdict', () => {
    const layout = layoutGapGrid(
      [
        row('empty', 'no_brands'),
        row('covered', 'covered', { subjectNamedOn: 3 }),
        row('absent', 'absent', { rivalsNamedOn: 3 }),
      ],
      BRANDS,
    );
    expect(layout.rows.map((r) => r.promptId)).toEqual(['absent', 'covered', 'empty']);
  });
});

describe('recurrence is the default order', () => {
  it('ranks a gap every engine agrees on above one only some do', () => {
    const layout = layoutGapGrid(
      [
        row('some', 'partial', { subjectNamedOn: 2, rivalsNamedOn: 1, absentOn: 1 }),
        row('all', 'absent', { rivalsNamedOn: 3, absentOn: 3 }),
      ],
      BRANDS,
    );
    expect(layout.rows[0]!.promptId).toBe('all');
  });

  it('breaks a tie within a kind by how many engines were absent', () => {
    const layout = layoutGapGrid(
      [
        row('one', 'partial', { subjectNamedOn: 2, absentOn: 1 }),
        row('two', 'partial', { subjectNamedOn: 1, absentOn: 2 }),
      ],
      BRANDS,
    );
    expect(layout.rows.map((r) => r.promptId)).toEqual(['two', 'one']);
  });

  it('leaves the given order alone when asked to', () => {
    const input = [row('b', 'covered', { subjectNamedOn: 3 }), row('a', 'absent')];
    const layout = layoutGapGrid(input, BRANDS, { sort: 'order' });
    expect(layout.rows.map((r) => r.promptId)).toEqual(['b', 'a']);
  });

  it('is stable, so the same data does not reshuffle between renders', () => {
    const input = [
      row('a', 'absent', { rivalsNamedOn: 3 }),
      row('b', 'absent', { rivalsNamedOn: 3 }),
      row('c', 'absent', { rivalsNamedOn: 3 }),
    ];
    const first = layoutGapGrid(input, BRANDS).rows.map((r) => r.promptId);
    const second = layoutGapGrid(input, BRANDS).rows.map((r) => r.promptId);
    expect(first).toEqual(second);
  });

  it('orders severity absent first and no-brands last', () => {
    const kinds: GapRowKind[] = ['absent', 'partial', 'uncited', 'covered', 'no_brands'];
    const ranks = kinds.map(gapSeverity);
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
  });
});

describe('cells', () => {
  it('carries the fraction of engines that named the brand, not a boolean', () => {
    const { rows } = layoutGapGrid(
      [row('p', 'partial', { subjectNamedOn: 1, rivalsNamedOn: 3, enginesAnswered: 3 })],
      BRANDS,
    );
    const [us, rival] = rows[0]!.cells;
    expect(us!.intensity).toBeCloseTo(1 / 3);
    expect(rival!.intensity).toBe(1);
  });

  it('flags a rival that won an answer the subject did not', () => {
    const { rows } = layoutGapGrid(
      [row('p', 'absent', { subjectNamedOn: 0, rivalsNamedOn: 2 })],
      BRANDS,
    );
    expect(rows[0]!.cells.find((c) => c.brand === 'Rival')!.tookFromSubject).toBe(true);
    expect(rows[0]!.cells.find((c) => c.brand === 'Us')!.tookFromSubject).toBe(false);
  });

  it('does not flag a rival on a prompt the subject also won everywhere', () => {
    const { rows } = layoutGapGrid(
      [row('p', 'covered', { subjectNamedOn: 3, rivalsNamedOn: 3, absentOn: 0 })],
      BRANDS,
    );
    expect(rows[0]!.cells.every((c) => !c.tookFromSubject)).toBe(true);
  });

  it('never divides by zero when no engine answered', () => {
    const { rows } = layoutGapGrid(
      [row('p', 'unanswered', { enginesAnswered: 0, absentOn: 0 })],
      BRANDS,
    );
    expect(rows[0]!.cells.every((c) => Number.isFinite(c.intensity))).toBe(true);
    expect(rows[0]!.cells.every((c) => c.intensity === 0)).toBe(true);
  });

  it('marks the subject cell by name, so a rival sharing a prefix is not it', () => {
    const brands: GapBrandInput[] = [
      { name: 'Us', isSubject: true, promptsNamed: 1 },
      { name: 'Us Group', isSubject: false, promptsNamed: 1 },
    ];
    const input: GapRowInput = {
      ...row('p', 'partial', { subjectNamedOn: 1 }),
      cells: [
        { brand: 'Us', namedOn: 1 },
        { brand: 'Us Group', namedOn: 2 },
      ],
    };
    const { rows } = layoutGapGrid([input], brands);
    expect(rows[0]!.cells.map((c) => c.isSubject)).toEqual([true, false]);
  });
});

describe('totals', () => {
  it('counts every row exactly once', () => {
    const layout = layoutGapGrid(
      [
        row('a', 'absent'),
        row('b', 'absent'),
        row('c', 'covered', { subjectNamedOn: 3 }),
        row('d', 'no_brands'),
      ],
      BRANDS,
    );
    const summed = Object.values(layout.totals).reduce((a, b) => a + b, 0);
    expect(summed).toBe(4);
    expect(layout.totals.absent).toBe(2);
  });

  it('reports the widest engine count seen, for the denominator', () => {
    const layout = layoutGapGrid(
      [row('a', 'covered', { enginesAnswered: 2 }), row('b', 'covered', { enginesAnswered: 3 })],
      BRANDS,
    );
    expect(layout.engines).toBe(3);
  });

  it('handles an empty grid without inventing a row', () => {
    const layout = layoutGapGrid([], BRANDS);
    expect(layout.rows).toEqual([]);
    expect(layout.absent).toBe(0);
    expect(layout.engines).toBe(0);
  });
});

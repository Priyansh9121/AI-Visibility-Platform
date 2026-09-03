/**
 * Answer-gap grid layout — the arithmetic behind `GapGrid`, separated so it is
 * testable without a renderer. Same split `ledgerLayout.ts` established.
 *
 * WHY A GRID AND NOT A CHART
 * --------------------------
 * The question this answers is "which questions does somebody else own, and
 * who". That is two categorical axes — prompt and brand — with a small integer
 * in each cell, and there is no continuous dimension anywhere in it. A chart
 * would have to invent one. A grid shows every cell at once, sorts on any
 * column, and stays readable as a table for a screen reader, which a heatmap
 * drawn in SVG does not.
 *
 * WHAT A CELL ENCODES, AND WHY IT IS NOT A BOOLEAN
 * ------------------------------------------------
 * A cell is "how many of this prompt's engines named this brand" over "how many
 * answered". The engines disagree, and the disagreement IS the finding: a rival
 * named by one engine of three holds a different position from one named by all
 * three, and a boolean would flatten those into the same mark. Intensity
 * carries the fraction; the number is printed alongside for anyone who cannot
 * read intensity.
 *
 * THE ROW STATE THAT MUST NOT BE DRAWN AS A GAP
 * ----------------------------------------------
 * `no_brands` is a prompt where no engine named ANY brand. The client is as
 * unnamed there as in a row a rival won, and the two must not look alike: one
 * is a question being lost, the other is a question with no commercial answer
 * at all. `no_brands` rows are drawn WITHOUT the gap treatment and are sorted
 * to the bottom, never blended into the absent count. The same discipline
 * `sentimentTideLayout` applies to `unclassified`, for the same reason.
 */

/** How a row was classified server-side. Mirrors the API's `GapKind`. */
export type GapRowKind =
  | 'absent'
  | 'partial'
  | 'uncited'
  | 'covered'
  | 'no_brands'
  | 'unanswered';

export interface GapCellInput {
  brand: string;
  namedOn: number;
}

export interface GapRowInput {
  promptId: string;
  /** OUR OWN prompt text — see the API's ip-safety note. */
  text: string;
  intent: string;
  kind: GapRowKind;
  enginesAnswered: number;
  subjectNamedOn: number;
  rivalsNamedOn: number;
  subjectCited: boolean;
  absentOn: number;
  cells: readonly GapCellInput[];
}

export interface GapBrandInput {
  name: string;
  domain?: string | null;
  isSubject: boolean;
  promptsNamed: number;
}

export type GapSort = 'recurrence' | 'order' | 'intent';

export interface GapCellLayout extends GapCellInput {
  isSubject: boolean;
  /** 0-1. `namedOn` over the engines that answered — the fill intensity. */
  intensity: number;
  /** True when this brand won a row the subject lost. The cell worth seeing. */
  tookFromSubject: boolean;
}

export interface GapRowLayout extends Omit<GapRowInput, 'cells'> {
  cells: GapCellLayout[];
  /** Rows a reader should act on: a rival won some or all of the answers. */
  isGap: boolean;
}

export interface GapGridLayout {
  rows: GapRowLayout[];
  brands: GapBrandInput[];
  /** Counts per kind, in severity order — the summary above the grid. */
  totals: Record<GapRowKind, number>;
  /** Rows where a rival was named and the subject was not, on every engine. */
  absent: number;
  /** Prompts nobody answered with a brand. Reported, never counted as a gap. */
  noBrands: number;
  engines: number;
}

const SEVERITY: Record<GapRowKind, number> = {
  absent: 0,
  partial: 1,
  uncited: 2,
  covered: 3,
  // Below every real verdict. Not a gap, and not a success either — it is a
  // question that produced no brand answer from anyone.
  no_brands: 4,
  unanswered: 5,
};

/** Severity rank for a row kind. Exported so a caller can sort consistently. */
export function gapSeverity(kind: GapRowKind): number {
  return SEVERITY[kind] ?? SEVERITY.unanswered;
}

/**
 * Lay out the grid.
 *
 * `sort` defaults to recurrence — how many engines the subject was absent on —
 * because that is the axis that actually survives. Prompt text is regenerated
 * every scan, so "this prompt keeps failing" is not a question this data can
 * answer; "every engine agrees somebody else owns this" is.
 */
export function layoutGapGrid(
  rows: readonly GapRowInput[],
  brands: readonly GapBrandInput[],
  { sort = 'recurrence' as GapSort }: { sort?: GapSort } = {},
): GapGridLayout {
  const subjectName = brands.find((b) => b.isSubject)?.name ?? null;

  const laid: GapRowLayout[] = rows.map((row) => {
    const denominator = Math.max(1, row.enginesAnswered);
    return {
      ...row,
      // A rival took answers from this client. `no_brands` is excluded by
      // construction: nobody was named, so nobody took anything.
      isGap: row.kind === 'absent' || row.kind === 'partial',
      cells: row.cells.map((cell) => {
        const isSubject = cell.brand === subjectName;
        return {
          ...cell,
          isSubject,
          intensity:
            row.enginesAnswered === 0 ? 0 : Math.min(1, cell.namedOn / denominator),
          tookFromSubject:
            !isSubject && cell.namedOn > 0 && row.subjectNamedOn < row.enginesAnswered,
        };
      }),
    };
  });

  const sorted = [...laid].sort((a, b) => {
    if (sort === 'order') return 0;
    if (sort === 'intent') {
      return a.intent.localeCompare(b.intent) || gapSeverity(a.kind) - gapSeverity(b.kind);
    }
    // Recurrence: worst kind first, then by how many engines agree, then by
    // how many rivals piled on. Stable within a tie so the order does not
    // shuffle between renders of the same data.
    return (
      gapSeverity(a.kind) - gapSeverity(b.kind) ||
      b.absentOn - a.absentOn ||
      b.rivalsNamedOn - a.rivalsNamedOn
    );
  });

  const totals = laid.reduce<Record<GapRowKind, number>>(
    (acc, row) => {
      acc[row.kind] = (acc[row.kind] ?? 0) + 1;
      return acc;
    },
    { absent: 0, partial: 0, uncited: 0, covered: 0, no_brands: 0, unanswered: 0 },
  );

  return {
    rows: sorted,
    brands: [...brands],
    totals,
    absent: totals.absent,
    noBrands: totals.no_brands,
    engines: laid.reduce((max, r) => Math.max(max, r.enginesAnswered), 0),
  };
}

/** Human label for a row kind. One place, so the grid and the legend agree. */
export const GAP_KIND_LABEL: Record<GapRowKind, string> = {
  absent: 'Rival named, this client not',
  partial: 'Named by some engines',
  uncited: 'Named but not cited',
  covered: 'Named and cited',
  no_brands: 'No brand named by anyone',
  unanswered: 'No engine answered',
};

/** The short form, for a dense cell or a chip. */
export const GAP_KIND_SHORT: Record<GapRowKind, string> = {
  absent: 'Absent',
  partial: 'Partial',
  uncited: 'Uncited',
  covered: 'Covered',
  no_brands: 'No brands',
  unanswered: 'Unanswered',
};

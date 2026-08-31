/**
 * Trend layout — the arithmetic behind `TrendChart`, separated so it is
 * testable without a renderer. Same split `ledgerLayout.ts` established.
 *
 * WHY A NULL IS NOT A ZERO HERE, AND WHY THAT IS THE WHOLE FILE
 * -------------------------------------------------------------
 * A competitor set changes between scans: a rival is detected in March, is not
 * detected in April, and is back in May. The naive rendering joins March to May
 * through a point at zero, which asserts that the rival's share of voice fell
 * to nothing in April. It did not — it was not measured.
 *
 * So a series is a list of `number | null` aligned to the shared x-axis, a null
 * breaks the line into SEGMENTS, and a run of nulls is drawn as nothing at all
 * rather than as a floor. `segments()` is what enforces it.
 */

export interface TrendPoint {
  /** Label for this column of the x-axis — a date, already formatted. */
  label: string;
  /** Machine-readable stamp, for the hidden data table. */
  stamp: string;
}

export interface TrendSeriesInput {
  key: string;
  label: string;
  /** The client under analysis. Exactly one series may set this. */
  isSubject?: boolean;
  /**
   * One value per `TrendPoint`, in the same order. `null` means NOT MEASURED
   * at that point — never zero, and never interpolated across.
   */
  values: readonly (number | null)[];
}

export interface TrendSegment {
  /** Consecutive plotted points. Length 1 is a lone dot, not a line. */
  points: readonly { x: number; y: number; index: number; value: number }[];
}

export interface TrendSeriesLayout {
  key: string;
  label: string;
  isSubject: boolean;
  /** Index among competitors — drives the neutral palette. -1 for the subject. */
  seriesIndex: number;
  segments: readonly TrendSegment[];
  /** Every plotted point, flattened — for dot markers and the data table. */
  plotted: readonly { x: number; y: number; index: number; value: number }[];
  /** True when this series has no measured value anywhere. */
  empty: boolean;
  /** First and last measured values, for a stated change. Null when < 2 points. */
  first: number | null;
  last: number | null;
}

export interface TrendLayout {
  width: number;
  height: number;
  plot: { x: number; y: number; width: number; height: number };
  points: readonly TrendPoint[];
  series: readonly TrendSeriesLayout[];
  /** Y-axis ticks, in value units, ascending. */
  ticks: readonly { value: number; y: number }[];
  yMax: number;
  /** X positions of each column, so callers can draw gridlines and labels. */
  columns: readonly number[];
}

export interface TrendLayoutOptions {
  width?: number;
  height?: number;
  padding?: { top: number; right: number; bottom: number; left: number };
  /** Force the top of the scale. Omit to fit the data. */
  yMax?: number;
  /** How many horizontal rules. */
  tickCount?: number;
}

const DEFAULTS = {
  width: 720,
  height: 300,
  // The right gutter holds the series names, so it is sized for the longest
  // thing that realistically goes there — a registrable domain. At 108 units
  // `analytics-alternatives.com` ran off the edge of the Sources chart on live
  // data; `LABEL_MAX_CHARS` is the backstop for anything longer still.
  padding: { top: 16, right: 176, bottom: 34, left: 40 },
  tickCount: 4,
} as const;

/**
 * Longest series name drawn on the plot.
 *
 * Only the DRAWN label is shortened — the hidden data table carries the full
 * name, so nothing is lost to a screen reader or to an export.
 */
export const LABEL_MAX_CHARS = 26;

/** Shorten a series name for the plot gutter, never for the data table. */
export function truncateLabel(label: string, max = LABEL_MAX_CHARS): string {
  return label.length <= max ? label : `${label.slice(0, max - 1)}…`;
}

/**
 * Break a series into runs of consecutive measured points.
 *
 * The single most important function here — see the file header. A gap must
 * stay a gap.
 */
function segments(
  values: readonly (number | null)[],
  columns: readonly number[],
  toY: (v: number) => number,
): TrendSegment[] {
  const out: TrendSegment[] = [];
  let run: { x: number; y: number; index: number; value: number }[] = [];

  values.forEach((value, index) => {
    if (value === null || Number.isNaN(value)) {
      if (run.length > 0) out.push({ points: run });
      run = [];
      return;
    }
    run.push({ x: columns[index] ?? 0, y: toY(value), index, value });
  });
  if (run.length > 0) out.push({ points: run });
  return out;
}

/** A rounded ceiling for the y-axis, so the top rule is a readable number. */
function niceMax(raw: number): number {
  if (raw <= 0) return 10;
  if (raw <= 10) return Math.ceil(raw);
  if (raw <= 100) return Math.ceil(raw / 10) * 10;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  return Math.ceil(raw / magnitude) * magnitude;
}

export function layoutTrend(
  points: readonly TrendPoint[],
  series: readonly TrendSeriesInput[],
  options: TrendLayoutOptions = {},
): TrendLayout {
  const width = options.width ?? DEFAULTS.width;
  const height = options.height ?? DEFAULTS.height;
  const padding = options.padding ?? DEFAULTS.padding;
  const tickCount = options.tickCount ?? DEFAULTS.tickCount;

  const plot = {
    x: padding.left,
    y: padding.top,
    width: Math.max(1, width - padding.left - padding.right),
    height: Math.max(1, height - padding.top - padding.bottom),
  };

  // One column per point. A single point sits in the MIDDLE rather than at the
  // left edge — a lone dot pinned to the axis reads as a truncated chart.
  const columns = points.map((_, i) =>
    points.length === 1
      ? plot.x + plot.width / 2
      : plot.x + (plot.width * i) / (points.length - 1),
  );

  const measured = series.flatMap((s) =>
    s.values.filter((v): v is number => v !== null && !Number.isNaN(v)),
  );
  const yMax = options.yMax ?? niceMax(measured.length > 0 ? Math.max(...measured) : 10);
  const toY = (v: number) => plot.y + plot.height - (Math.min(v, yMax) / yMax) * plot.height;

  let competitorIndex = 0;
  const laidOut: TrendSeriesLayout[] = series.map((s) => {
    const isSubject = s.isSubject === true;
    const seriesIndex = isSubject ? -1 : competitorIndex++;
    const segs = segments(s.values, columns, toY);
    const plotted = segs.flatMap((seg) => seg.points);
    return {
      key: s.key,
      label: s.label,
      isSubject,
      seriesIndex,
      segments: segs,
      plotted,
      empty: plotted.length === 0,
      first: plotted.length > 1 ? plotted[0]!.value : null,
      last: plotted.length > 1 ? plotted[plotted.length - 1]!.value : null,
    };
  });

  const ticks = Array.from({ length: tickCount + 1 }, (_, i) => {
    const value = (yMax * i) / tickCount;
    return { value, y: toY(value) };
  });

  return { width, height, plot, points, series: laidOut, ticks, yMax, columns };
}

/**
 * Push end-of-line labels apart so none is written over another.
 *
 * Found in the live browser, not by a test: five rivals inside twelve points of
 * each other stacked their names into an unreadable block. A test asserting
 * "the label exists" passes on that, because it does.
 *
 * The labels keep their ORDER — this only separates them — so a reader
 * tracking a line to its name never crosses another label to get there.
 */
export function spreadLabels(
  anchors: readonly { key: string; y: number }[],
  minGap = 13,
): Record<string, number> {
  const sorted = [...anchors].sort((a, b) => a.y - b.y);
  const out: Record<string, number> = {};
  let previous = -Infinity;
  for (const a of sorted) {
    const y = Math.max(a.y, previous + minGap);
    out[a.key] = y;
    previous = y;
  }
  return out;
}

/** An SVG path for one segment. Straight lines — a spline invents readings. */
export function segmentPath(segment: TrendSegment): string {
  return segment.points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
    .join(' ');
}

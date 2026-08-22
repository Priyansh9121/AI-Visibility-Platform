/**
 * Luminance Ledger — layout geometry.
 *
 * Kept as a pure function, separate from the React component, because the
 * identity it encodes is load-bearing and must be unit-testable:
 *
 *   lit height of segment i = H x (weight_i/100) x (subscore_i/100)
 *   SUM(lit heights)        = H x score/100
 *
 * The total lit height of the column IS the composite score. The chart is not
 * an illustration of the number; it is the number, drawn. If that identity ever
 * breaks, the visualisation is lying, so it is asserted in tests rather than
 * trusted.
 *
 * See /docs/scoring-spec.md.
 */

import { visibilityColor } from '../../tokens/color.js';

export interface LedgerDimension {
  /** Stable key, e.g. 'mention_rate'. */
  key: string;
  /** Human label for the axis, e.g. 'Mention Rate'. */
  label: string;
  /** Weight in the composite, 0-100. Weights across all dimensions sum to 100. */
  weight: number;
  /** Normalised sub-score, 0-100. */
  subscore: number;
}

export interface LedgerCompetitor {
  /** Display name. Competitor NAMES are facts and may be stored (ip-safety #7). */
  name: string;
  dimensions: readonly LedgerDimension[];
}

export interface LedgerSegment {
  key: string;
  label: string;
  weight: number;
  subscore: number;
  /** Top edge of the full segment (SVG coords, y grows downward). */
  y: number;
  /** Full segment height — proportional to WEIGHT, i.e. points available. */
  height: number;
  /** Top edge of the lit portion. Lights from the segment's own bottom. */
  litY: number;
  /** Lit height — proportional to points EARNED. */
  litHeight: number;
  /** Points left on the table: weight x (100 - subscore) / 100. */
  gap: number;
  /** Fill for the lit portion, from the visibility ramp at this sub-score. */
  color: string;
  isBiggestGap: boolean;
}

export interface LedgerGhost {
  name: string;
  composite: number;
  /** y of the competitor's cap line — their composite, same scale as the column. */
  capY: number;
  index: number;
}

export interface LedgerLayout {
  /** Composite 0-100, or null when there is nothing to score. */
  composite: number | null;
  height: number;
  segments: readonly LedgerSegment[];
  /** Largest recoverable point gain — drives the report's "biggest gap" beat. */
  biggestGap: LedgerSegment | null;
  ghosts: readonly LedgerGhost[];
  /** Non-fatal data problems. Surfaced in the style guide and in dev builds. */
  warnings: readonly string[];
}

export interface LedgerLayoutOptions {
  /** Column height in SVG user units. */
  height?: number;
  competitors?: readonly LedgerCompetitor[];
}

/** Geometry is rounded so snapshots are stable across platforms. */
const DP = 3;
const r = (n: number): number => {
  const f = 10 ** DP;
  return Math.round(n * f) / f;
};

/** Composite score from a dimension set. Mirrors scoring-spec.md exactly. */
export function compositeScore(dimensions: readonly LedgerDimension[]): number | null {
  if (dimensions.length === 0) return null;
  const total = dimensions.reduce((sum, d) => sum + d.weight * clamp(d.subscore), 0);
  return r(total / 100);
}

export function layoutLedger(
  dimensions: readonly LedgerDimension[],
  options: LedgerLayoutOptions = {},
): LedgerLayout {
  const height = options.height ?? 420;
  const warnings: string[] = [];

  if (dimensions.length === 0) {
    // An unrunnable scan must never render as a bad score (scoring-spec.md).
    return {
      composite: null,
      height,
      segments: [],
      biggestGap: null,
      ghosts: [],
      warnings: ['No dimensions supplied — rendering INSUFFICIENT_DATA, not zero.'],
    };
  }

  const weightSum = dimensions.reduce((s, d) => s + d.weight, 0);
  if (Math.abs(weightSum - 100) > 0.01) {
    // Normalise so the column still fills its box, but say so loudly. A silent
    // renormalisation would hide a scoring-config defect behind a chart that
    // looks fine.
    warnings.push(
      `Weights sum to ${r(weightSum)}, not 100. Normalised for layout — check the scoring config.`,
    );
  }
  const scale = weightSum > 0 ? 100 / weightSum : 0;

  for (const d of dimensions) {
    if (d.subscore < 0 || d.subscore > 100) {
      // Clamp AND raise — a silent clamp hides pipeline defects (scoring-spec.md).
      warnings.push(`Sub-score for "${d.label}" is ${d.subscore}, outside 0-100. Clamped.`);
    }
  }

  const gaps = dimensions.map((d) => (d.weight * scale * (100 - clamp(d.subscore))) / 100);
  // Deterministic tie-break: lowest index wins, so equal gaps always resolve the
  // same way and a re-run never reshuffles the report's headline.
  let biggestIndex = -1;
  let biggestValue = -1;
  gaps.forEach((g, i) => {
    if (g > biggestValue + 1e-9) {
      biggestValue = g;
      biggestIndex = i;
    }
  });
  // A perfect score has no gap worth naming.
  if (biggestValue <= 1e-9) biggestIndex = -1;

  /*
   * Heaviest dimension sits at the BOTTOM. Two reasons: the column then reads
   * as light accumulating from the ground up, matching how the score
   * accumulates; and the most consequential dimension occupies the most stable
   * visual position rather than floating at the top.
   */
  const segments: LedgerSegment[] = [];
  let cursor = height; // walking upward from the baseline
  dimensions.forEach((d, i) => {
    const subscore = clamp(d.subscore);
    const segHeight = (height * d.weight * scale) / 100;
    const y = cursor - segHeight;
    const litHeight = (segHeight * subscore) / 100;
    const litY = cursor - litHeight;

    segments.push({
      key: d.key,
      label: d.label,
      weight: d.weight,
      subscore,
      y: r(y),
      height: r(segHeight),
      litY: r(litY),
      litHeight: r(litHeight),
      gap: r(gaps[i]!),
      color: visibilityColor(subscore),
      isBiggestGap: i === biggestIndex,
    });
    cursor = y;
  });

  const composite = compositeScore(dimensions.map((d) => ({ ...d, weight: d.weight * scale })));

  const ghosts: LedgerGhost[] = (options.competitors ?? []).map((c, index) => {
    const comp = compositeScore(c.dimensions) ?? 0;
    return {
      name: c.name,
      composite: comp,
      capY: r(height - (height * comp) / 100),
      index,
    };
  });

  return { composite, height, segments, biggestGap: segments[biggestIndex] ?? null, ghosts, warnings };
}

function clamp(n: number): number {
  return Math.min(100, Math.max(0, n));
}

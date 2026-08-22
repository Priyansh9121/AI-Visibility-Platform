/**
 * Colour tokens — "Lit / Unlit".
 *
 * Organising metaphor: the product measures presence vs absence in AI answers,
 * so visibility is encoded as luminance. Dim + desaturated = invisible.
 * Bright + saturated = cited and recommended.
 *
 * All values are OKLCH so that lightness steps are perceptually even and the
 * visibility ramp can be interpolated without hue drift or muddy midpoints.
 *
 * See /docs/design-system.md for the full rationale.
 */

/** An OKLCH colour as [lightness 0-1, chroma, hue degrees]. */
export type Oklch = readonly [l: number, c: number, h: number];

/** Format an OKLCH tuple as a CSS colour string. */
export function oklch([l, c, h]: Oklch, alpha = 1): string {
  const base = `${round(l, 4)} ${round(c, 4)} ${round(h, 2)}`;
  return alpha === 1 ? `oklch(${base})` : `oklch(${base} / ${round(alpha, 3)})`;
}

function round(n: number, dp: number): number {
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

/* ------------------------------------------------------------------ *
 * Neutrals — warm paper, cool ink
 *
 * The whole analytics category defaults to a cool blue-grey neutral. Rotating
 * the light end warm (hue 75) reads as paper rather than app chrome and makes
 * the product not-look-like the category at zero cost.
 *
 * Note the deliberate hue hand-off at ink-400: light neutrals are warm, dark
 * neutrals are cool (hue 265). Warm dark greys print muddy; cool dark greys
 * read like actual printed ink.
 * ------------------------------------------------------------------ */

export const paper = {
  '000': [0.995, 0.004, 75],
  '050': [0.978, 0.006, 75],
  '100': [0.958, 0.008, 75],
  '200': [0.925, 0.01, 75],
  '300': [0.87, 0.012, 75],
} as const satisfies Record<string, Oklch>;

export const ink = {
  '400': [0.62, 0.014, 75],
  '600': [0.47, 0.018, 265],
  '800': [0.285, 0.022, 265],
  '900': [0.185, 0.026, 265],
} as const satisfies Record<string, Oklch>;

/* ------------------------------------------------------------------ *
 * The visibility ramp — the load-bearing decision in this palette.
 *
 * Three properties held on purpose:
 *
 * 1. MONOTONIC IN LIGHTNESS (0.42 -> 0.815). Survives greyscale printing and
 *    photocopying. The traffic-light red->amber->green ramp does not: red and
 *    green resolve to near-identical greys. These reports get printed.
 * 2. CVD-SAFE. The warm->cool traverse (orange -> teal) is the axis that
 *    deuteranopes and protanopes reliably retain. ~8% of men would misread a
 *    red/green score.
 * 3. CHROMA RISES WITH LIGHTNESS, so "more visible" is simultaneously lighter
 *    and more saturated — the illumination metaphor is doubly encoded.
 *
 * HARD RULE: ramp colours are FILL-ONLY, never text. The high end cannot reach
 * 4.5:1 on paper. Use `onVisibility()` to pick a legible label colour.
 * ------------------------------------------------------------------ */

export const visibility = {
  '00': [0.42, 0.045, 35],
  '25': [0.545, 0.115, 45],
  '50': [0.655, 0.135, 65],
  '75': [0.735, 0.125, 150],
  '100': [0.815, 0.115, 195],
} as const satisfies Record<string, Oklch>;

const VISIBILITY_STOPS: readonly (readonly [number, Oklch])[] = [
  [0, visibility['00']],
  [25, visibility['25']],
  [50, visibility['50']],
  [75, visibility['75']],
  [100, visibility['100']],
];

/**
 * Interpolate the visibility ramp at a 0-100 score.
 *
 * Pure and deterministic: the same score always yields the same colour, which
 * matters because these colours end up in exported PDFs that clients compare
 * across months.
 */
export function visibilityAt(score: number): Oklch {
  const s = clamp(score, 0, 100);
  for (let i = 0; i < VISIBILITY_STOPS.length - 1; i++) {
    const [lo, loColor] = VISIBILITY_STOPS[i]!;
    const [hi, hiColor] = VISIBILITY_STOPS[i + 1]!;
    if (s <= hi) {
      const t = hi === lo ? 0 : (s - lo) / (hi - lo);
      return [
        lerp(loColor[0], hiColor[0], t),
        lerp(loColor[1], hiColor[1], t),
        lerp(loColor[2], hiColor[2], t),
      ];
    }
  }
  return VISIBILITY_STOPS[VISIBILITY_STOPS.length - 1]![1];
}

/** CSS string form of {@link visibilityAt}. */
export function visibilityColor(score: number, alpha = 1): string {
  return oklch(visibilityAt(score), alpha);
}

/**
 * Legible label colour for text sitting on a visibility fill.
 *
 * Threshold sits at L=0.62 — above that the fill is light enough that ink wins,
 * below it paper wins. Enforces the fill-only rule rather than trusting callers
 * to remember it.
 */
export function onVisibility(score: number): string {
  return visibilityAt(score)[0] >= 0.62 ? oklch(ink['900']) : oklch(paper['000']);
}

/** Band label for a score. Used for the ordinal read, never for colour choice. */
export function visibilityBand(score: number): 'absent' | 'barely' | 'emerging' | 'established' | 'beacon' {
  const s = clamp(score, 0, 100);
  if (s < 20) return 'absent';
  if (s < 40) return 'barely';
  if (s < 60) return 'emerging';
  if (s < 80) return 'established';
  return 'beacon';
}

/* ------------------------------------------------------------------ *
 * Brand accent — the lit end of the ramp, deepened for contrast on paper.
 * One brand, one colour: the client under analysis is ALWAYS beacon.
 * ------------------------------------------------------------------ */

export const beacon = {
  '050': [0.965, 0.018, 200],
  '100': [0.925, 0.038, 200],
  '400': [0.72, 0.11, 200],
  '600': [0.545, 0.125, 200],
  '700': [0.46, 0.105, 200],
} as const satisfies Record<string, Oklch>;

/* ------------------------------------------------------------------ *
 * Competitor series — deliberately NOT the visibility ramp.
 *
 * A competitor rendered in "good green" reads as an endorsement; one rendered
 * in "bad red" makes the report look like a hatchet job and costs it
 * credibility with the client's CMO — the one thing the report cannot afford.
 *
 * So competitors are neutral slate, separated by LIGHTNESS + FILL PATTERN
 * rather than hue. Non-judgmental, unlimited series count, prints in B&W.
 * ------------------------------------------------------------------ */

export const competitor = {
  '1': [0.55, 0.02, 265],
  '2': [0.64, 0.02, 265],
  '3': [0.73, 0.02, 265],
  '4': [0.8, 0.018, 265],
  '5': [0.86, 0.015, 265],
} as const satisfies Record<string, Oklch>;

export type CompetitorPattern = 'solid' | 'hatch-45' | 'dot' | 'hatch-135' | 'outline';

/** Pattern assignment per competitor index — the greyscale/print fallback. */
export const COMPETITOR_PATTERNS: readonly CompetitorPattern[] = [
  'solid',
  'hatch-45',
  'dot',
  'hatch-135',
  'outline',
];

/**
 * Resolve the visual treatment for a series.
 *
 * `subject` is the client/prospect being reported on and always wins the brand
 * accent. Competitors cycle the neutral family. This function is the single
 * place that rule is enforced — components must not pick series colours
 * themselves.
 */
export function seriesStyle(
  role: 'subject' | 'competitor',
  index = 0,
): { fill: string; pattern: CompetitorPattern; isSubject: boolean } {
  if (role === 'subject') {
    return { fill: oklch(beacon['600']), pattern: 'solid', isSubject: true };
  }
  const keys = ['1', '2', '3', '4', '5'] as const;
  const key = keys[index % keys.length]!;
  return {
    fill: oklch(competitor[key]),
    pattern: COMPETITOR_PATTERNS[index % COMPETITOR_PATTERNS.length]!,
    isSubject: false,
  };
}

/* ------------------------------------------------------------------ *
 * Semantics — deliberately narrow.
 *
 * These describe SYSTEM STATE (a scan failed, a quota is nearly spent) and are
 * never used for score values. Keeping the sets disjoint stops a red error
 * chip from being misread as "bad score" on a report page.
 * ------------------------------------------------------------------ */

export const semantic = {
  success: [0.55, 0.12, 150],
  warn: [0.7, 0.13, 75],
  danger: [0.55, 0.17, 25],
  info: beacon['600'],
} as const satisfies Record<string, Oklch>;

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

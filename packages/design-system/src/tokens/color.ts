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
 * Which context a chart is being drawn in.
 *
 * `'report'` is the DEFAULT, and that default is the guarantee. The report does
 * not opt out of the working palette — it never opts in, so a chart added to it
 * tomorrow by someone who has not read this file is restrained automatically.
 * Getting the report wrong requires typing `'working'` into it.
 */
export type SeriesPalette = 'report' | 'working';

/**
 * Resolve the visual treatment for a series.
 *
 * `subject` is the client/prospect being reported on and always wins the brand
 * accent — in BOTH contexts. design-direction.md §1's "one brand, one colour,
 * everywhere in the product" is not what this epic changed, and an operator
 * who learns that the teal line is their client on the dashboard must find the
 * same teal line in the document they send.
 *
 * WHAT THE CONTEXT CHANGES: COMPETITOR HUE
 * -----------------------------------------
 * §1 requires competitors to be non-judgmental — a rival in "good green" reads
 * as an endorsement, one in "bad red" reads as a hatchet job, and the report
 * cannot afford either in front of a CMO. On the REPORT that is enforced by
 * drawing them in neutral slate, separated by lightness and pattern.
 *
 * On a WORKING screen the audience is the operator, the artefact is never
 * printed and never sent, and five neutral greys at 1.5px are genuinely hard
 * to follow across a crowded trend line. So competitors take the bench hues —
 * which are categorical, carry no rank, and sit 30 degrees clear of every hue
 * that means anything. The non-judgmental requirement is met the same way, by
 * a palette with no good end and no bad end; it is just a palette you can
 * actually tell apart.
 *
 * THE PATTERN IS UNCONDITIONAL.
 * Both contexts keep the §1 dash/hatch assignment. It is what makes the chart
 * readable in greyscale and to a colour-blind operator, and neither of those
 * stops mattering because the screen is a Working one.
 *
 * This function is the single place the rule is enforced — components must not
 * pick series colours themselves.
 */
export function seriesStyle(
  role: 'subject' | 'competitor',
  index = 0,
  palette: SeriesPalette = 'report',
): { fill: string; pattern: CompetitorPattern; isSubject: boolean } {
  if (role === 'subject') {
    return { fill: oklch(beacon['600']), pattern: 'solid', isSubject: true };
  }
  const pattern = COMPETITOR_PATTERNS[index % COMPETITOR_PATTERNS.length]!;
  if (palette === 'working') {
    return { fill: benchColor(index), pattern, isSubject: false };
  }
  const keys = ['1', '2', '3', '4', '5'] as const;
  return { fill: oklch(competitor[keys[index % keys.length]!]), pattern, isSubject: false };
}

/* ------------------------------------------------------------------ *
 * THE WORKING-SCREEN ACCENT LAYER — "the bench"
 *
 * design-direction.md §0 has always split screens into PRESENTING (the report,
 * which gets printed and handed to a prospect's CMO) and WORKING (an
 * operator's own tools, 20 tabs open, running scans back to back). The report's
 * restraint is load-bearing for the reasons §1 gives — greyscale survival,
 * CVD safety, "document not dashboard". None of those reasons apply to the
 * dashboard, and applying them there anyway is what left every Working screen
 * monochrome with a single teal highlight.
 *
 * So this is a SECOND palette for the second context. `paper`/`ink` is the
 * document metaphor; `bench` is the operator's bench the document is assembled
 * on. It is ADDITIVE — nothing above this comment changed to make room for it.
 *
 * WHERE THE HUES CAME FROM
 * ------------------------
 * Generated from `ui-ux-pro-max`'s palette database (192 palettes, 446
 * chromatic entries once near-neutrals and unusable lightnesses are dropped),
 * converted to OKLCH and bucketed by hue. The database's own top
 * recommendation for "dense analytics dashboard" was the cool-blue/slate
 * family (#1E40AF, #3B82F6, #DBEAFE) — which is precisely the category default
 * §1 rotated the neutral axis away from, so the hex values were NOT imported.
 * What was taken is the hue MASS: the arc 258-343 is where the database's
 * chromatic entries actually cluster and where sRGB still has chroma to spend.
 *
 * THE RULE THAT KEEPS IT FROM MEANING ANYTHING IT SHOULDN'T
 * ---------------------------------------------------------
 * Every bench hue sits at least 30 degrees away from every hue that already
 * carries meaning in this system — the five visibility stops, `beacon`, and the
 * four semantics. A bench chip therefore cannot be misread as a score value or
 * as a system state, because it is nowhere near one on the wheel. This is not a
 * convention; `tokens.test.ts` fails if a hue is ever moved inside the buffer.
 *
 * Chroma is 0.185 against `beacon-600`'s 0.125 — 1.48x — and the layer spans
 * 85 degrees of hue where the old system had a single point. That is the whole
 * "richer and more saturated" claim, stated as two numbers that can be checked.
 * ------------------------------------------------------------------ */

/** One categorical accent: a stable key, a human name, and its hue. */
export interface BenchAccent {
  readonly key: string;
  readonly name: string;
  readonly hue: number;
}

/**
 * The seven accents, in assignment order.
 *
 * Ordering is stable and meaningless: index 3 is not "worse" than index 1,
 * which is the property the visibility ramp deliberately does NOT have.
 *
 * `crimson` was added in Epic B, when a seventh client section (Answer gaps)
 * would otherwise have wrapped `benchAccent` back to `cobalt` and given two
 * items in the SAME nav strip one colour.
 *
 * ---------------------------------------------------------------------------
 * THE LAYER IS NOW FULL, AND THAT IS ARITHMETIC RATHER THAN A PREFERENCE
 * ---------------------------------------------------------------------------
 * Two hard constraints bound where an accent may sit, and together they leave
 * exactly one usable arc:
 *
 *   1. `BENCH_HUE_BUFFER` — at least 30 degrees from every meaning-bearing hue
 *      (the five visibility stops, `beacon`, and the four semantics), so a chip
 *      cannot be read as a score or as a system state.
 *   2. The sRGB gamut at the SHARED chroma table below. `600` needs C 0.185 at
 *      L 0.55 and `700` needs C 0.158 at L 0.46. Blue cannot hold that: at hue
 *      241 the ceiling is 0.128, so an accent placed there would render
 *      visibly duller than its neighbours and break the equal-weight property
 *      that keeps categorical colour from implying rank.
 *
 * Solving both across the wheel yields ONE arc, 256.5 to 355 — 98.5 degrees.
 * The seven accents below occupy it at roughly 16 degrees apart, and `crimson`
 * at 355 is its last seat: 30 degrees exactly from `danger`, which is the
 * buffer's stated threshold.
 *
 * **An eighth accent cannot be added without giving something up.** The
 * roadmap's later screens (Alerts, Crawler activity, Prompt discovery) will
 * reach this wall. Fitting ten accents in 98.5 degrees means ~11 degrees
 * apart, which is below what hue alone separates at fixed lightness and
 * chroma. The choice at that point is between relaxing the buffer, letting
 * accents differ in chroma, or grouping the nav so that hue distinguishes
 * WITHIN a group rather than across all of it. That is a design decision, not
 * a token edit, and it is deliberately not pre-empted here.
 */
export const BENCH_ACCENTS: readonly BenchAccent[] = [
  { key: '1', name: 'cobalt', hue: 258 },
  { key: '2', name: 'indigo', hue: 275 },
  { key: '3', name: 'violet', hue: 292 },
  { key: '4', name: 'orchid', hue: 309 },
  { key: '5', name: 'magenta', hue: 326 },
  { key: '6', name: 'rose', hue: 343 },
  { key: '7', name: 'crimson', hue: 355 },
];

/** The one arc that clears both the meaning buffer and the sRGB gamut. */
export const BENCH_FEASIBLE_ARC = { from: 256.5, to: 355 } as const;

/** The minimum hue separation from any meaning-bearing colour. Asserted, not assumed. */
export const BENCH_HUE_BUFFER = 30;

export type BenchStop = '050' | '100' | '600' | '700';

/**
 * Lightness and chroma per stop, shared by all six hues.
 *
 * Held as one table rather than 24 hand-typed triples so that "every accent is
 * the same weight as every other" is structural. Two accents drifting apart in
 * lightness would make one of them read as more important, and categorical
 * colour must not imply rank.
 *
 * `700` is DEEPER than `600` in light mode — a press/hover deepening, matching
 * `beacon-700`.
 */
const BENCH_LIGHT: Record<BenchStop, readonly [l: number, c: number]> = {
  '050': [0.955, 0.035],
  '100': [0.905, 0.062],
  '600': [0.55, 0.185],
  '700': [0.46, 0.158],
};

function buildBench(): Record<string, Oklch> {
  const out: Record<string, Oklch> = {};
  for (const accent of BENCH_ACCENTS) {
    for (const [stop, [l, c]] of Object.entries(BENCH_LIGHT)) {
      out[`${accent.key}-${stop}`] = [l, c, accent.hue];
    }
  }
  return out;
}

/**
 * The light-mode bench palette, keyed `"{accent}-{stop}"` — e.g. `bench['3-600']`.
 *
 * Dark-mode values are NOT here. They cannot be derived by the same table:
 * blue at hue 258 simply cannot be both light and saturated inside sRGB, so the
 * dark scale is chroma-clamped per hue at the gamut boundary. They live in
 * tokens.css's `[data-theme='dark']` block, which is the one place in this
 * system that has always owned the neutral flip.
 */
export const bench: Record<string, Oklch> = buildBench();

/** The accent at a categorical index. Cycles, so any list length is safe. */
export function benchAccent(index: number): BenchAccent {
  const n = BENCH_ACCENTS.length;
  return BENCH_ACCENTS[((index % n) + n) % n]!;
}

/** CSS colour for a categorical index at a given stop. */
export function benchColor(index: number, stop: BenchStop = '600', alpha = 1): string {
  return oklch(bench[`${benchAccent(index).key}-${stop}`]!, alpha);
}

/**
 * The custom-property name for a categorical index — for call sites that must
 * stay theme-reactive.
 *
 * `benchColor` returns a LITERAL, which is correct for an SVG fill computed at
 * render time and wrong for anything that has to follow a theme switch. Chrome
 * that persists across a theme change should read the variable instead.
 */
export function benchVar(index: number, stop: BenchStop = '600'): string {
  return `var(--avp-bench-${benchAccent(index).key}-${stop})`;
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

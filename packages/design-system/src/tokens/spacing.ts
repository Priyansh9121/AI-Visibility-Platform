/**
 * Spacing tokens — 4px base with a non-linear tail.
 *
 * Pure 4px multiples give six near-identical options between 24 and 48, where
 * almost nothing needs that precision, and nothing useful past 64, where layout
 * actually lives. This scale is fine-grained where components need it and jumps
 * where sections need it. Above 24 the steps grow ~1.35x, so a section break
 * reads as intentional rather than "someone typed 44".
 */

export const space = {
  '0': '0',
  px: '1px',
  '0.5': '0.125rem', //  2
  '1': '0.25rem', //     4
  '1.5': '0.375rem', //  6
  '2': '0.5rem', //      8
  '3': '0.75rem', //    12
  '4': '1rem', //       16
  '5': '1.25rem', //    20
  '6': '1.5rem', //     24
  '8': '2rem', //       32
  '10': '2.5rem', //    40
  '14': '3.5rem', //    56
  '18': '4.5rem', //    72
  '24': '6rem', //      96
  '32': '8rem', //     128
} as const;

/**
 * The two tokens that matter more than the ramp itself.
 *
 * `rhythm` is the vertical grid the report's narrative beats snap to. When
 * score / gap / proof / fix / pitch all sit on one rhythm, the page reads as a
 * single document rather than five stacked widgets.
 *
 * `beat` is the standard gap BETWEEN narrative beats. It is larger than any
 * intra-beat spacing, so the document's five-part structure is legible from the
 * page thumbnail — which is how a client actually first sees an exported PDF.
 */
export const rhythm = '0.5rem'; // 8
export const beat = '4.5rem'; //  72

/**
 * THE SHAPE LOCK — Epic 14.
 *
 * One corner system, followed everywhere on a Working screen: cards, tiles
 * and the hero take `2xl`; buttons, inputs and nav rows take `lg`; chips take
 * `full`. A round button in a square layout, or a square card on a pill-button
 * screen, is what an assembled-not-authored interface looks like, so the rule
 * is written down and the styleguide shows it.
 *
 * The report keeps Epic 0's `sm` (3) and `md` (5) through the paper scope in
 * tokens.css, so no corner in the printed document moves by a pixel.
 */
export const radius = {
  none: '0',
  sm: '0.25rem', //    4
  md: '0.375rem', //   6
  lg: '0.5rem', //     8
  xl: '0.75rem', //   12
  '2xl': '1rem', //   16 — cards, tiles, the hero
  full: '9999px',
} as const;

/** Report page geometry. Matches A4/Letter proportions for PDF export parity. */
export const layout = {
  reportWidth: '52rem', //   832 — the exported report column
  reportGutter: '3.5rem', // 56
  appMax: '90rem', //      1440 — working-context app shell
} as const;

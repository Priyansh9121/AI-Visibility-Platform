/**
 * Typography tokens.
 *
 * Three faces, three jobs, all OFL-licensed via Google Fonts (ip-safety.md #4).
 *
 * - Fraunces  (editorial) — a serif is the loudest available signal that this
 *   is a document making an argument, not a metrics grid. Variable, with opsz
 *   and SOFT/WONK axes, so it sets warm at display sizes without going stiff.
 * - IBM Plex Sans (interface) — deliberately NOT Inter, which is the default of
 *   the entire category and reads as generic SaaS. Plex carries more character,
 *   stays unambiguous at 12-14px, and ships true TABULAR FIGURES, which are
 *   mandatory: this product is comparison tables full of numbers that must
 *   align down the column.
 * - IBM Plex Mono (evidence) — same skeleton as Plex Sans, so evidence blocks
 *   sit inside body copy without a visual seam. Monospacing marks "verbatim
 *   machine output", a useful honesty signal given ip-safety.md #7 limits what
 *   may be quoted.
 */

export const fontFamily = {
  editorial: "'Fraunces Variable', 'Fraunces', ui-serif, Georgia, 'Times New Roman', serif",
  ui: "'IBM Plex Sans', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
  mono: "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
} as const;

/**
 * TWO SCALES, ON PURPOSE.
 *
 * One modular ratio cannot serve both jobs. Interface work needs 13px and 14px
 * to be genuinely different things; editorial work needs headlines that leap
 * across a conference table. Forcing one ratio over both makes UI text bloated
 * or headlines timid.
 */

/** Interface track — ratio 1.125. Tight steps for dense, deliberate distinctions. */
export const fontSizeUi = {
  '2xs': '0.6875rem', // 11 — micro-labels, table superscripts
  xs: '0.75rem', //    12 — dense table cells, axis labels
  sm: '0.8125rem', //  13 — secondary UI text
  base: '0.875rem', // 14 — default UI body, table cells
  md: '1rem', //       16 — emphasised UI, form inputs
  lg: '1.125rem', //   18 — card titles
  xl: '1.25rem', //    20 — section headers in app chrome
} as const;

/** Editorial track — ratio 1.25 (major third). Wide steps for report hierarchy. */
export const fontSizeEditorial = {
  '2xs': '1.25rem', //  20
  xs: '1.5625rem', //   25
  sm: '1.9375rem', //   31
  md: '2.4375rem', //   39 — narrative beat headings
  lg: '3.0625rem', //   49
  xl: '3.8125rem', //   61
  '2xl': '4.75rem', //  76 — report cover title
} as const;

/**
 * The score numeral gets a bespoke size outside both ladders.
 * It is the single most important object in the product; it earns its own
 * size rather than being the top rung of a general-purpose scale.
 */
export const fontSizeScore = '7rem'; // 112

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
} as const;

export const lineHeight = {
  display: 1.15,
  ui: 1.35,
  prose: 1.6,
  mono: 1.5,
} as const;

export const letterSpacing = {
  /** Applied above 39px — large type needs negative tracking to hold together. */
  display: '-0.02em',
  normal: '0',
  /** All-caps micro-labels need positive tracking to stay readable. */
  caps: '0.06em',
} as const;

/** Report prose is capped so long-form narrative stays readable. */
export const measure = '68ch';

/**
 * Google Fonts URL for the three faces.
 * Kept here so there is exactly one place fonts are declared, and so an audit
 * against ip-safety.md #4 has a single thing to check.
 */
export const GOOGLE_FONTS_HREF =
  'https://fonts.googleapis.com/css2' +
  '?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700' +
  '&family=IBM+Plex+Sans:wght@400;500;600;700' +
  '&family=IBM+Plex+Mono:wght@400;500' +
  '&display=swap';

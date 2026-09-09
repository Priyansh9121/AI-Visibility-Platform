/**
 * Typography tokens.
 *
 * Four faces, four jobs, all OFL-licensed via Google Fonts (the licensed-assets rule).
 *
 * - Space Grotesk (display) — Epic 14. The product's own voice on every
 *   Working screen: headings, KPI figures, the hero numeral. A geometric
 *   grotesk with genuine character in its figures (the flat-based 1, the open
 *   4, the squared 0) and TRUE TABULAR FIGURES, so a row of KPI tiles aligns.
 *   Deliberately not Inter, and deliberately not the serif below: a dark,
 *   data-dense analytics screen set in an editorial serif was the single
 *   loudest thing the founder rejected about the light system.
 *
 * - Fraunces  (editorial) — THE REPORT'S face, and only the report's. A serif
 *   is the loudest available signal that this is a document making an
 *   argument, not a metrics grid, and the printed document still makes one.
 * - IBM Plex Sans (interface) — deliberately NOT Inter, which is the default of
 *   the entire category and reads as generic SaaS. Plex carries more character,
 *   stays unambiguous at 12-14px, and ships true TABULAR FIGURES, which are
 *   mandatory: this product is comparison tables full of numbers that must
 *   align down the column.
 * - IBM Plex Mono (evidence) — same skeleton as Plex Sans, so evidence blocks
 *   sit inside body copy without a visual seam. Monospacing marks "verbatim
 *   machine output", a useful honesty signal given the facts-only rule limits what
 *   may be quoted.
 */

export const fontFamily = {
  display: "'Space Grotesk', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
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

/**
 * The two display figures the dark identity adds — Epic 14.
 *
 * `kpi` is a figure in a tile; `hero` is the one figure at the top of a
 * screen — the portfolio's median on the dashboard, a client's latest score
 * on its Overview. The hero is fluid because it is the first thing on the
 * screen at every viewport, and a fixed 84px is either a wall at 375px or
 * timid at 1440.
 */
export const fontSizeKpi = '1.875rem'; // 30
export const fontSizeHero = 'clamp(3.5rem, 2.5rem + 3vw, 5.25rem)'; // 56 – 84

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
  /** The hero numeral, tighter still — at 80px letters read too far apart. */
  hero: '-0.04em',
  normal: '0',
  /** All-caps micro-labels need positive tracking to stay readable. */
  caps: '0.06em',
} as const;

/** Report prose is capped so long-form narrative stays readable. */
export const measure = '68ch';

/**
 * Google Fonts URL for the four faces.
 * Kept here so there is exactly one place fonts are declared, and so an audit
 * against the licensed-assets rule has a single thing to check.
 */
export const GOOGLE_FONTS_HREF =
  'https://fonts.googleapis.com/css2' +
  '?family=Space+Grotesk:wght@500;600;700' +
  '&family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700' +
  '&family=IBM+Plex+Sans:wght@400;500;600;700' +
  '&family=IBM+Plex+Mono:wght@400;500' +
  '&display=swap';

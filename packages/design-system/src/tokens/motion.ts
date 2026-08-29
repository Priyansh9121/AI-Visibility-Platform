/**
 * Motion tokens.
 *
 * Restrained by default. The one expressive moment is the score reveal, which
 * dissolves dim -> lit rather than counting a number up: the product states its
 * central metaphor in motion the first time you see it.
 *
 * Every consumer must honour prefers-reduced-motion. `reveal` in particular
 * degrades to an instant paint, never a slower version of itself.
 */

export const duration = {
  hover: '120ms',
  state: '200ms',
  layout: '320ms',
  /** Score reveal — long enough to read as illumination, not as lag. */
  reveal: '600ms',
} as const;

/**
 * How far apart siblings start when a set arrives in sequence — Epic 9.16.
 *
 * A delay, not a duration: `duration.reveal` already says how long one element
 * takes, and design-direction.md §4 defines that as *the* reveal. This says how
 * far apart the members of a group begin.
 *
 * Consumers should NOT read this value in JavaScript. `Reveal` sets an index as
 * a CSS custom property and the stylesheet multiplies, so a call site that
 * wants a different rhythm overrides `--avp-stagger-reveal` rather than doing
 * arithmetic. It is exported for the styleguide and for documentation.
 */
export const stagger = {
  reveal: '70ms',
} as const;

export const easing = {
  standard: 'cubic-bezier(0.2, 0, 0.2, 1)',
  out: 'cubic-bezier(0, 0, 0.2, 1)',
  /** Decelerating overshoot-free curve for the reveal. */
  reveal: 'cubic-bezier(0.22, 1, 0.36, 1)',
} as const;

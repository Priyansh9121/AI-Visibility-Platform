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

export const easing = {
  standard: 'cubic-bezier(0.2, 0, 0.2, 1)',
  out: 'cubic-bezier(0, 0, 0.2, 1)',
  /** Decelerating overshoot-free curve for the reveal. */
  reveal: 'cubic-bezier(0.22, 1, 0.36, 1)',
} as const;

/**
 * Motion helpers shared by every component that reveals something.
 *
 * WHY THIS FILE EXISTS AT ALL
 * ---------------------------
 * The reduced-motion read below was written out longhand in `ScoreDisplay` and
 * again in `LuminanceLedger` — the same five lines, twice. Epic 9.16 needed it a
 * third time, and three hand-copied versions of an accessibility check is how
 * one of them ends up subtly different and nobody notices, because the failure
 * is invisible to anyone who does not have the setting turned on.
 *
 * It is deliberately the SAME expression those two already used, extracted
 * rather than redesigned: `window.matchMedia?.('(prefers-reduced-motion:
 * reduce)').matches`, guarded for the server. Following the existing convention
 * was the instruction; naming it is what stops it being re-derived a fourth
 * time.
 */

/**
 * Whether the visitor has asked for reduced motion.
 *
 * Returns `false` during server rendering — not because the server knows the
 * answer, but because it cannot, and the components that call this all start in
 * their pre-reveal state and correct themselves in an effect. The stylesheet
 * carries the real guarantee: `.avp-reveal` is fully revealed under
 * `prefers-reduced-motion: reduce` in CSS, with no transition, so the promise
 * holds even if this function is never called and even if JavaScript never
 * runs at all.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
}

/**
 * Whether this browser can tell us when an element scrolls into view.
 *
 * Split out because the answer decides a **content visibility** question, not a
 * decorative one. Everything built on `useRevealOnIntersect` starts hidden and
 * is revealed by an observer callback; a browser with no `IntersectionObserver`
 * would therefore render a blank marketing page rather than an unanimated one.
 * Callers reveal immediately when this is false.
 */
export function supportsIntersectionObserver(): boolean {
  return typeof window !== 'undefined' && typeof window.IntersectionObserver === 'function';
}

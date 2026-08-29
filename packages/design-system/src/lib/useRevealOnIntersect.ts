'use client';

import { useEffect, useRef, useState, type RefObject } from 'react';
import { prefersReducedMotion, supportsIntersectionObserver } from './motion.js';

/**
 * Reveal once, when the element first intersects the viewport.
 *
 * ONE MECHANISM, TWO VERY DIFFERENT CONSUMERS
 * -------------------------------------------
 * `Reveal` wraps HTML and fades it up; `LuminanceLedger` lights SVG bars from
 * the baseline. They look nothing alike and they are the same event: "this is
 * on screen now, start." Extracting the trigger is what makes the chart part of
 * the page's rhythm instead of a second animation system that happens to run
 * nearby — which is exactly how the earlier standalone bar-by-bar build read,
 * and why it was dropped.
 *
 * WHY ONE OBSERVER AND NOT A SCROLL HANDLER
 * -----------------------------------------
 * A scroll listener runs on every frame of every scroll for the life of the
 * page, for a question that has one answer per element and never changes back.
 * `IntersectionObserver` answers it off the main thread and is then disconnected.
 *
 * WHY "ALREADY IN VIEW" NEEDS NO SEPARATE MODE
 * --------------------------------------------
 * An observer fires its first callback for every observed element immediately
 * after `observe()`, whether or not it is on screen — with `isIntersecting`
 * already true for anything in the viewport. So the hero, which is visible
 * before anyone scrolls, reveals on the first callback, and a section below the
 * fold reveals on a later one. Same code path, no "reveal on mount" flag.
 *
 * THREE WAYS THIS REVEALS IMMEDIATELY, ALL OF THEM DELIBERATE
 * ------------------------------------------------------------
 *   1. `enabled` is false — the caller does not want motion (tests, print).
 *   2. The visitor asked for reduced motion.
 *   3. The browser has no `IntersectionObserver`.
 *
 * The third is not defensive tidiness. Everything using this starts HIDDEN, so
 * a browser that cannot observe would otherwise be shown a blank page. Content
 * visibility must never depend on an optional browser API.
 */
export function useRevealOnIntersect<T extends Element>(
  enabled: boolean,
): { ref: RefObject<T | null>; revealed: boolean } {
  const ref = useRef<T | null>(null);
  // Starts revealed when motion is off, so a static render of a
  // non-animating tree is the finished markup rather than the hidden state.
  const [revealed, setRevealed] = useState(!enabled);

  useEffect(() => {
    if (!enabled) {
      setRevealed(true);
      return;
    }
    if (prefersReducedMotion() || !supportsIntersectionObserver()) {
      setRevealed(true);
      return;
    }

    const element = ref.current;
    if (element === null) {
      // Nothing to observe — reveal rather than leave content invisible.
      setRevealed(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          setRevealed(true);
          // One-way. The reveal is an arrival, not a state that tracks
          // scrolling — re-hiding content the reader has already read would
          // be motion for its own sake.
          observer.disconnect();
        }
      },
      // A small positive margin so a section starts moving as it comes up to
      // the fold rather than after it has arrived, and a low threshold so a
      // section taller than the viewport still triggers.
      { rootMargin: '0px 0px -8% 0px', threshold: 0.01 },
    );

    observer.observe(element);
    return () => observer.disconnect();
  }, [enabled]);

  return { ref, revealed };
}

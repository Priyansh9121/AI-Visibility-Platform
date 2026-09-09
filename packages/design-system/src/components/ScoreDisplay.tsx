'use client';

// Uses React hooks, so it must run on the client under the Next App Router.
// Marked at the component rather than the package level on purpose: Card,
// Badge, Table and the report primitives are pure and stay server-renderable,
// which Epic 7's server-side PDF render depends on.

import { useEffect, useState, type JSX } from 'react';
import { cn } from '../lib/cn.js';
import { visibilityColor, visibilityBand } from '../tokens/color.js';
import { VisibilityBadge } from './Badge.js';

export interface ScoreDisplayProps {
  /** 0-100, or null for INSUFFICIENT_DATA. */
  score: number | null;
  label?: string;
  animate?: boolean;
  /**
   * Render the ordinal badge under the band, painted from the SAME rounded
   * score as the numeral — Epic 16.
   *
   * The report used to place a `VisibilityBadge` beside this component and
   * hand it the unrounded composite. The numeral rounds before it paints, so
   * the two were two interpolations of the ramp a fraction apart: close enough
   * to match on most scores, and nothing made them match. Owning the badge
   * here makes the tie structural — one `rounded`, one call to the ramp for
   * each surface, and `ReportView.test.tsx` asserts the two strings are equal.
   */
  badge?: boolean;
  className?: string;
}

const BAND_COPY: Record<ReturnType<typeof visibilityBand>, string> = {
  absent: 'Effectively invisible to AI answer engines',
  barely: 'Occasionally surfaced, rarely cited',
  emerging: 'Present, but losing the answer to competitors',
  established: 'Consistently present across the prompt set',
  beacon: 'A default answer in this category',
};

/**
 * ScoreDisplay — the composite, at the size it deserves.
 *
 * The reveal dissolves dim-to-lit rather than counting the number up. A
 * count-up says "loading"; an illumination says what the product measures. It
 * is the one expressive motion in the system, and it states the central
 * metaphor the first time you see a report.
 */
export function ScoreDisplay({
  score,
  label = 'AI Visibility Score',
  animate = true,
  badge = false,
  className,
}: ScoreDisplayProps): JSX.Element {
  const [revealed, setRevealed] = useState(!animate);

  useEffect(() => {
    if (!animate) return;
    const reduced =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      setRevealed(true);
      return;
    }
    const id = requestAnimationFrame(() => setRevealed(true));
    return () => cancelAnimationFrame(id);
  }, [animate]);

  if (score === null) {
    return (
      <div className={cn('avp-score', 'avp-score--empty', className)}>
        <p className="avp-score__label">{label}</p>
        <p className="avp-score__numeral avp-score__numeral--empty">—</p>
        <p className="avp-score__band">Not enough data to score this scan</p>
      </div>
    );
  }

  const rounded = Math.round(score);
  return (
    <div className={cn('avp-score', className)}>
      <p className="avp-score__label">{label}</p>
      <p
        className="avp-score__numeral"
        style={{
          color: revealed ? visibilityColor(rounded) : 'var(--avp-vis-00)',
          opacity: revealed ? 1 : 0.35,
        }}
      >
        {rounded}
        <span className="avp-score__denominator">/100</span>
      </p>
      <p className="avp-score__band">{BAND_COPY[visibilityBand(rounded)]}</p>
      {badge && <VisibilityBadge score={rounded} className="avp-score__badge" />}
    </div>
  );
}

'use client';

// Uses React hooks for the reveal, so it must run on the client under the Next
// App Router. Marked at the component level, like ScoreDisplay.

import { useEffect, useState, type CSSProperties, type JSX, type ReactNode } from 'react';
import { cn } from '../lib/cn.js';
import { heroGradient, visibilityBand, visibilityColorDark } from '../tokens/color.js';
import { visibilityBandLabel } from './Badge.js';
import { prefersReducedMotion } from '../lib/motion.js';

export interface ScoreHeroProps {
  /** The micro-label above the figure — "Portfolio visibility", "Latest score". */
  label: string;
  /** 0-100, or null when there is nothing to show. */
  score: number | null;
  /**
   * What the absence is, when `score` is null. Stated in words rather than
   * drawn as a zero — the rule every figure in this product follows.
   */
  absence?: string;
  /** A line under the figure. Defaults to the band's label. */
  band?: ReactNode;
  /**
   * The change since the previous reading, in points. Rendered with its sign
   * and a word, so the direction is never carried by colour alone. Omit when
   * there is no previous reading to compare against — one reading is not a
   * direction.
   */
  delta?: number | null;
  /** Short facts beside the delta — the scan date, the denominator. */
  meta?: ReactNode;
  /** Whatever draws the score's structure: a ledger, a trend. */
  aside?: ReactNode;
  /** The dim-to-lit reveal on mount. Honours prefers-reduced-motion. */
  animate?: boolean;
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
 * ScoreHero — the one figure at the top of a Working screen. Epic 14.
 *
 * THE FOUNDER'S ASK, BUILT FROM THE DATA MODEL
 * --------------------------------------------
 * A hero KPI as the first thing on the dashboard and on a client's Overview:
 * large, bold, geometric, with a gradient. The figure here is always a
 * visibility SCORE, and its gradient is not a brand flourish — it is derived
 * from the visibility ramp at that score (`heroGradient`), and the glow
 * behind it is the same colour. So a 12 sits in dim clay and an 88 in lit
 * cyan, which is the Luminance Ledger's whole idea (the score, drawn as
 * light) at the scale the founder wanted it. The band under the figure says
 * the same thing in words, so the hue is never the only carrier.
 *
 * Distinct from `ScoreDisplay`, which is the REPORT's composite set in the
 * editorial serif and untouched by this epic. Two figures for two contexts,
 * like `TrendChart`'s two palettes — except here the contexts are different
 * enough (a printed document, a dark dashboard) to be different components.
 *
 * NULL IS NOT ZERO. An absent score renders the absence in words, no
 * gradient and no glow: there is nothing to light.
 */
export function ScoreHero({
  label,
  score,
  absence = 'Not scored yet',
  band,
  delta,
  meta,
  aside,
  animate = true,
  className,
}: ScoreHeroProps): JSX.Element {
  const [revealed, setRevealed] = useState(!animate);

  useEffect(() => {
    if (!animate) return;
    if (prefersReducedMotion()) {
      setRevealed(true);
      return;
    }
    const id = requestAnimationFrame(() => setRevealed(true));
    return () => cancelAnimationFrame(id);
  }, [animate]);

  const empty = score === null || Number.isNaN(score);
  const rounded = empty ? null : Math.round(Math.max(0, Math.min(100, score as number)));

  const style: CSSProperties | undefined =
    rounded === null
      ? undefined
      : ({
          '--avp-hero-gradient': heroGradient(rounded),
          '--avp-hero-glow': visibilityColorDark(rounded),
        } as CSSProperties);

  return (
    <section
      className={cn('avp-hero', aside != null && 'avp-hero--aside', className)}
      style={style}
      aria-label={
        rounded === null
          ? `${label}: ${absence}`
          : `${label}: ${rounded} out of 100, ${visibilityBandLabel(rounded)}`
      }
    >
      <div className="avp-hero__copy">
        <p className="avp-hero__label">{label}</p>
        {rounded === null ? (
          <p className="avp-hero__figure">
            <span className="avp-hero__numeral avp-hero__numeral--empty">{absence}</span>
          </p>
        ) : (
          <p className="avp-hero__figure">
            <span className={cn('avp-hero__numeral', !revealed && 'is-dim')}>{rounded}</span>
            <span className="avp-hero__denominator">/100</span>
          </p>
        )}
        {rounded !== null && (
          <p className="avp-hero__band">{band ?? BAND_COPY[visibilityBand(rounded)]}</p>
        )}
        {rounded === null && band != null && <p className="avp-hero__band">{band}</p>}
        {(meta != null || (delta != null && rounded !== null)) && (
          <p className="avp-hero__meta">
            {delta != null && rounded !== null && <Delta value={delta} />}
            {meta}
          </p>
        )}
      </div>
      {aside != null && <div className="avp-hero__aside">{aside}</div>}
    </section>
  );
}

/**
 * The change since the previous reading. Sign, figure and word; the colour is
 * the ramp's two ends rather than success/danger, because a lower score is a
 * finding, not a system fault — the same line GapGrid draws.
 */
function Delta({ value }: { value: number }): JSX.Element {
  const dir = value > 0.05 ? 'up' : value < -0.05 ? 'down' : 'flat';
  const sign = dir === 'up' ? '+' : dir === 'down' ? '−' : '±';
  return (
    <span className={cn('avp-hero__delta', `avp-hero__delta--${dir}`)}>
      {`${sign}${Math.abs(value).toFixed(1)} `}
      <span className="avp-visually-hidden">points </span>
      {dir === 'up' ? 'since last scan' : dir === 'down' ? 'since last scan' : 'unchanged'}
    </span>
  );
}

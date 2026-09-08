import type { JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';
import { visibilityBand, visibilityColor, onVisibility } from '../tokens/color.js';

export type BadgeTone = 'neutral' | 'beacon' | 'success' | 'warn' | 'danger';

export interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
  /**
   * Mark this state as ONGOING — a lit dot before the label that breathes.
   *
   * The only motion on a Working screen that runs without anybody doing
   * anything, and it is allowed for the one reason design-direction.md §4
   * permits: something really is changing. A scan that says "Running" is
   * running whether or not the operator is watching, and a badge that looks
   * identical to "Complete" makes the page look frozen between polls.
   *
   * It is the reveal's dim-to-lit dissolve put on a loop rather than a second
   * motion idea — same beacon, same easing, and its period is derived from
   * `--avp-duration-reveal` rather than being a new number. Under
   * `prefers-reduced-motion` the global block in base.css collapses the
   * animation and the keyframe's end state leaves the dot simply lit.
   */
  live?: boolean;
  className?: string;
}

/**
 * Badge — SYSTEM STATE only (scan failed, quota low, engine unavailable).
 *
 * Never use a semantic tone to express a score value. Keeping the semantic and
 * visibility palettes disjoint is what stops a red error chip from being
 * misread as "bad score" on a report page. Use VisibilityBadge for scores.
 */
export function Badge({
  tone = 'neutral',
  children,
  live = false,
  className,
}: BadgeProps): JSX.Element {
  return (
    <span className={cn('avp-badge', `avp-badge--${tone}`, className)}>
      {live && <span className="avp-badge__pulse" aria-hidden="true" />}
      {children}
    </span>
  );
}

export interface VisibilityBadgeProps {
  score: number;
  className?: string;
}

const BAND_LABEL: Record<ReturnType<typeof visibilityBand>, string> = {
  absent: 'Absent',
  barely: 'Barely visible',
  emerging: 'Emerging',
  established: 'Established',
  beacon: 'Highly visible',
};

/**
 * The word the badge shows for a score — exported so the rule has one home.
 *
 * The PDF renderer (`apps/api/services/report_pdf.py`) carries a Python copy
 * of this and of `visibilityBand`, because it cannot call either. On
 * 2026-09-08 that copy had drifted — different thresholds, different words —
 * and a prospect read "Barely visible" on the page and "Marginal" in the file
 * they downloaded from it. Both sides now assert against
 * `packages/shared-types/fixtures/visibility-bands.json`; this function is
 * what the TypeScript side of that assertion calls.
 */
export function visibilityBandLabel(score: number): string {
  return BAND_LABEL[visibilityBand(score)];
}

/**
 * VisibilityBadge — the ordinal read of a score.
 *
 * Fill comes from the ramp; the label colour is resolved by luminance, which
 * enforces the fill-only rule rather than leaving it to the caller.
 */
export function VisibilityBadge({ score, className }: VisibilityBadgeProps): JSX.Element {
  return (
    <span
      className={cn('avp-badge', 'avp-badge--visibility', className)}
      style={{ background: visibilityColor(score), color: onVisibility(score) }}
    >
      {visibilityBandLabel(score)}
    </span>
  );
}

import type { JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';
import { visibilityBand, visibilityColor, onVisibility } from '../tokens/color.js';

export type BadgeTone = 'neutral' | 'beacon' | 'success' | 'warn' | 'danger';

export interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}

/**
 * Badge — SYSTEM STATE only (scan failed, quota low, engine unavailable).
 *
 * Never use a semantic tone to express a score value. Keeping the semantic and
 * visibility palettes disjoint is what stops a red error chip from being
 * misread as "bad score" on a report page. Use VisibilityBadge for scores.
 */
export function Badge({ tone = 'neutral', children, className }: BadgeProps): JSX.Element {
  return <span className={cn('avp-badge', `avp-badge--${tone}`, className)}>{children}</span>;
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
 * VisibilityBadge — the ordinal read of a score.
 *
 * Fill comes from the ramp; the label colour is resolved by luminance, which
 * enforces the fill-only rule rather than leaving it to the caller.
 */
export function VisibilityBadge({ score, className }: VisibilityBadgeProps): JSX.Element {
  const band = visibilityBand(score);
  return (
    <span
      className={cn('avp-badge', 'avp-badge--visibility', className)}
      style={{ background: visibilityColor(score), color: onVisibility(score) }}
    >
      {BAND_LABEL[band]}
    </span>
  );
}

import type { JSX } from 'react';
import { cn } from '../lib/cn.js';
import { visibilityColor, visibilityBand } from '../tokens/color.js';

/**
 * Why a score has no number. Both are real states the API distinguishes; neither
 * is a zero, and neither may render as one.
 */
export type ScoreAbsence = 'measuring' | 'unscored';

export interface ScoreMeterProps {
  /** 0-100, or null when there is no score. */
  score: number | null;
  /**
   * Which absence this is, when `score` is null. Ignored when a score exists.
   *
   * `measuring` — the scan has not finished, so a score does not exist YET.
   * `unscored`  — the scan finished and still has no score: never scored, or
   *               scored as INSUFFICIENT_DATA. A different fact, and a
   *               permanent one until the scan is re-run.
   */
  absence?: ScoreAbsence;
  className?: string;
}

const ABSENCE_LABEL: Record<ScoreAbsence, string> = {
  measuring: 'Measuring',
  unscored: 'Not scored',
};

const BAND_LABEL: Record<ReturnType<typeof visibilityBand>, string> = {
  absent: 'Absent',
  barely: 'Barely visible',
  emerging: 'Emerging',
  established: 'Established',
  beacon: 'Highly visible',
};

/**
 * ScoreMeter — the composite at list scale.
 *
 * **The same identity as the Luminance Ledger, at one dimension.** The Ledger's
 * correctness condition is that total LIT LENGTH is the composite — the chart
 * *is* the number rather than a picture of it. Collapse that to a single
 * dimension and it is exactly this: a track whose lit fraction is `score/100`,
 * filled from the visibility ramp.
 *
 * **It is deliberately not a miniature Ledger.** A real Ledger needs per-
 * dimension weights to divide the column, and the dashboard's `ScanSummary`
 * carries only `compositeScore` — no sub-scores are served on that endpoint.
 * Drawing segments there would mean inventing the divisions, which breaks the
 * one property that makes the Ledger honest. One dimension is what the data
 * supports, so one dimension is what this draws.
 *
 * **Not a gauge, not a sparkline, not a trend.** A gauge implies a target; a
 * trend implies a previous value this component is not given. It shows one
 * measured number and how much of the available light it earned.
 *
 * NULL IS NOT ZERO
 * ----------------
 * An absent score renders an EMPTY track plus a stated reason, never a filled
 * one and never a bare dash. `ScoreDisplay` already set this precedent on the
 * report ("Not enough data to score this scan"); a dash alone reads as a
 * rendering fault rather than a fact about the scan. The track still draws, so
 * the row keeps its shape and an absent score is visibly an absence rather than
 * a gap in the layout.
 *
 * Pure and hook-free, so it stays server-renderable like Card, Badge and Table
 * — Epic 7's static-markup render path depends on that.
 */
export function ScoreMeter({
  score,
  absence = 'unscored',
  className,
}: ScoreMeterProps): JSX.Element {
  if (score === null || Number.isNaN(score)) {
    return (
      <div className={cn('avp-meter', 'avp-meter--empty', className)}>
        <div className="avp-meter__head">
          <span className="avp-meter__numeral avp-meter__numeral--empty">—</span>
          <span className="avp-meter__band">{ABSENCE_LABEL[absence]}</span>
        </div>
        <div className="avp-meter__track" role="presentation" />
      </div>
    );
  }

  const clamped = Math.max(0, Math.min(100, score));
  const rounded = Math.round(clamped);
  const band = visibilityBand(rounded);

  return (
    <div className={cn('avp-meter', className)}>
      <div className="avp-meter__head">
        <span className="avp-meter__numeral" style={{ color: visibilityColor(rounded) }}>
          {rounded}
        </span>
        <span className="avp-meter__band">{BAND_LABEL[band]}</span>
      </div>
      <div
        className="avp-meter__track"
        role="img"
        aria-label={`AI Visibility Score ${rounded} out of 100 — ${BAND_LABEL[band]}`}
      >
        {/* Lit length IS the score. Width is the only channel carrying value. */}
        <div
          className="avp-meter__lit"
          style={{ width: `${clamped}%`, background: visibilityColor(rounded) }}
        />
      </div>
    </div>
  );
}

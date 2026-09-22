import type { JSX } from 'react';
import { cn } from '../lib/cn.js';

export interface VerdictCounts {
  pass: number;
  warn: number;
  fail: number;
  /** Checks that do not apply to this site. Shown, never counted as passed. */
  notApplicable?: number;
}

/**
 * A tone is SYSTEM STATE or STRUCTURE, never a score — the rule the whole
 * component rests on. `success`/`warn`/`danger` are the semantic set;
 * `neutral` is a state with no charge ("allowed", "did not name you");
 * `beacon` is the client's own colour, for the one segment that IS the client
 * ("engines naming you"); `none` is the absence of any state at all — no rule
 * applies, nothing was decided — drawn as unpainted track with an inset line
 * so it is a segment you can count and not a gap you might miss.
 */
export type VerdictTone = 'success' | 'warn' | 'danger' | 'neutral' | 'beacon' | 'none';

export interface VerdictSegment {
  key: string;
  label: string;
  value: number;
  tone: VerdictTone;
}

interface VerdictBarCommon {
  /** Required, like every data mark in this system. */
  ariaLabel: string;
  /** Show the counts as text beside the bar. */
  showLegend?: boolean;
  className?: string;
}

/**
 * Two input shapes, one strip. `counts` is the original — the technical
 * audit's pass / warn / fail. `segments` (2026-09-22) is any ordered list of
 * named integers with a tone each, so the same strip can summarise a
 * robots.txt policy by verdict or a prompt run by what each engine did,
 * without a second component that draws the same thing.
 */
export type VerdictBarProps = VerdictBarCommon &
  (
    | { counts: VerdictCounts; segments?: undefined }
    | { segments: readonly VerdictSegment[]; counts?: undefined }
  );

/**
 * VerdictBar — pass / warn / fail as one proportional strip.
 *
 * WHY SEMANTIC COLOUR IS CORRECT HERE, WHEN IT USUALLY IS NOT
 * -----------------------------------------------------------
 * design-direction.md §1 keeps the semantic palette deliberately narrow so it
 * never competes with the visibility ramp for meaning: semantics are for
 * *system state* — a scan failed, a quota is nearly used — and **never for
 * score values**. A technical check's verdict is system state in exactly that
 * sense: `pass`/`warn`/`fail` is the audit reporting what it found, not a
 * measure of how visible a brand is. So `success`/`warn`/`danger` are the right
 * tokens, and the visibility ramp would be the wrong one — a ramp here would
 * imply these verdicts are a score, which is the confusion §1 exists to prevent.
 *
 * NOT A CHART, DELIBERATELY
 * -------------------------
 * No `ChartFrame`, no viewBox, no hidden data table. It is a one-dimensional
 * proportion drawn from three integers that are ALWAYS printed beside it — the
 * legend is the data table. Wrapping it in the chart contract would imply a
 * figure that can be read on its own, and this cannot: it is a summary of a
 * list that is on the same screen.
 *
 * Having no viewBox also means it cannot magnify its own type the way a
 * viewBox chart can (Epics 9.19 and 9.21) — there is no type inside it at all.
 *
 * Pure and hook-free, so it stays server-renderable.
 */
export function VerdictBar({
  ariaLabel,
  showLegend = true,
  className,
  ...input
}: VerdictBarProps): JSX.Element {
  if (input.segments !== undefined) {
    return (
      <SegmentStrip
        segments={input.segments}
        ariaLabel={ariaLabel}
        showLegend={showLegend}
        className={className}
      />
    );
  }
  const counts = input.counts;
  const notApplicable = counts.notApplicable ?? 0;
  // `not_applicable` is EXCLUDED from the denominator on purpose. A check that
  // does not apply to this site is not a check it passed, and folding it in
  // would inflate the bar with questions nobody asked.
  const measured = counts.pass + counts.warn + counts.fail;
  const pct = (n: number) => (measured === 0 ? 0 : (n / measured) * 100);

  return (
    <div className={cn('avp-verdict', className)}>
      <div className="avp-verdict__track" role="img" aria-label={ariaLabel}>
        {measured === 0 ? (
          <div className="avp-verdict__empty" />
        ) : (
          <>
            {counts.pass > 0 && (
              <div
                className="avp-verdict__seg avp-verdict__seg--pass"
                style={{ width: `${pct(counts.pass)}%` }}
              />
            )}
            {counts.warn > 0 && (
              <div
                className="avp-verdict__seg avp-verdict__seg--warn"
                style={{ width: `${pct(counts.warn)}%` }}
              />
            )}
            {counts.fail > 0 && (
              <div
                className="avp-verdict__seg avp-verdict__seg--fail"
                style={{ width: `${pct(counts.fail)}%` }}
              />
            )}
          </>
        )}
      </div>

      {showLegend && (
        <dl className="avp-verdict__legend">
          <Item label="Passed" value={counts.pass} tone="pass" />
          <Item label="Warned" value={counts.warn} tone="warn" />
          <Item label="Failed" value={counts.fail} tone="fail" />
          {notApplicable > 0 && <Item label="N/A" value={notApplicable} tone="na" />}
        </dl>
      )}
    </div>
  );
}

/**
 * The generalised strip. Same track, same legend-is-the-data-table contract,
 * same rule that a zero is listed and never drawn as a sliver of colour.
 */
function SegmentStrip({
  segments,
  ariaLabel,
  showLegend,
  className,
}: {
  segments: readonly VerdictSegment[];
  ariaLabel: string;
  showLegend: boolean;
  className: string | undefined;
}): JSX.Element {
  const measured = segments.reduce((n, s) => n + s.value, 0);
  const pct = (n: number) => (measured === 0 ? 0 : (n / measured) * 100);
  return (
    <div className={cn('avp-verdict', className)}>
      <div className="avp-verdict__track" role="img" aria-label={ariaLabel}>
        {measured === 0 ? (
          <div className="avp-verdict__empty" />
        ) : (
          segments
            .filter((s) => s.value > 0)
            .map((s) => (
              <div
                key={s.key}
                className={cn('avp-verdict__seg', `avp-verdict__seg--tone-${s.tone}`)}
                style={{ width: `${pct(s.value)}%` }}
              />
            ))
        )}
      </div>
      {showLegend && (
        <dl className="avp-verdict__legend">
          {segments.map((s) => (
            <Item key={s.key} label={s.label} value={s.value} tone={`tone-${s.tone}`} />
          ))}
        </dl>
      )}
    </div>
  );
}

function Item({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: 'pass' | 'warn' | 'fail' | 'na' | `tone-${VerdictTone}`;
}): JSX.Element {
  return (
    <div className="avp-verdict__item">
      <dt className="avp-verdict__label">
        <span className={cn('avp-verdict__dot', `avp-verdict__dot--${tone}`)} aria-hidden="true" />
        {label}
      </dt>
      <dd className="avp-verdict__value">{value}</dd>
    </div>
  );
}

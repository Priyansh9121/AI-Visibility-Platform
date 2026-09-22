import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';
import { seriesStyle, type SeriesPalette } from '../../tokens/color.js';
import { ChartFrame } from './ChartFrame.js';
import { ChartPatterns, patternPaint } from './ChartPatterns.js';
import { layoutShare, type ShareInput, type ShareLayoutOptions } from './shareLayout.js';

export interface ShareBarProps {
  /** Every brand in the field, the subject included, with its share or null. */
  shares: readonly ShareInput[];
  /** Required, like every chart here. */
  ariaLabel: string;
  title?: ReactNode;
  caption?: ReactNode;
  /** Suffix for the figures; the shares are percentages of the whole. */
  unit?: string;
  /** Report (neutral slate rivals) or Working (bench hues). The subject is beacon in both. */
  palette?: SeriesPalette;
  /** What a zero share means here, printed after the figure. */
  zeroNote?: string;
  /** What the outlined remainder is, when the tracked field is not the whole field. */
  remainderLabel?: string;
  className?: string;
  layoutOptions?: ShareLayoutOptions;
}

/**
 * ShareBar — share of voice at one moment, as one divided strip.
 *
 * WHY A STRIP AND NOT A DONUT
 * ---------------------------
 * Both products this one is measured against lead with a donut split by
 * competitor. A donut encodes by angle, and the one thing every shape in this
 * system already agrees on is LENGTH: the Ledger's lit height is the composite,
 * `ScoreMeter`'s lit fraction is the score, `VerdictBar`'s widths are the
 * counts. A strip whose segment widths ARE the shares is the same identity
 * again, and it stacks under the trend of the same figure without changing
 * language halfway down the page.
 *
 * The other reason is the absences. A donut with N brands wants N wedges, and
 * a brand with a zero share or no reading either becomes an invisible sliver
 * or, worse, a default-sized wedge beside a real 38.5%. Here a zero share is
 * never drawn: it is a stated line in the legend — "0%, named in none of the
 * answers" — and an unmeasured one says "not measured". The subject at zero
 * is the loudest thing on the strip precisely because it is not on it.
 *
 * THE COLOUR RULE IS §1's, UNCHANGED
 * ---------------------------------
 * The subject is beacon, solid. Rivals come from `seriesStyle('competitor')`:
 * neutral slate on the report, bench hues on a Working screen, and in both a
 * fill PATTERN per rival so a greyscale print keeps five brands as five
 * segments. The visibility ramp is never touched — a rival's share is not a
 * score, and painting it green or red would judge it.
 *
 * Pure and hook-free; the hidden data table carries every brand, absences
 * included, so the strip is readable without sight of it.
 */
export function ShareBar({
  shares,
  ariaLabel,
  title,
  caption,
  unit = '%',
  palette = 'report',
  zeroNote = 'named in none of the answers',
  remainderLabel = 'Other brands named',
  className,
  layoutOptions,
}: ShareBarProps): JSX.Element {
  const layout = layoutShare(shares, layoutOptions);
  const fmt = (v: number) => `${Number.isInteger(v) ? v : v.toFixed(1)}${unit}`;
  const paint = (isSubject: boolean, rivalIndex: number) => {
    const style = seriesStyle(isSubject ? 'subject' : 'competitor', Math.max(rivalIndex, 0), palette);
    return {
      fill: patternPaint(style.pattern, style.fill),
      stroke: style.pattern === 'outline' ? style.fill : undefined,
      outline: style.pattern === 'outline',
    };
  };

  return (
    <ChartFrame
      className={cn('avp-share', className)}
      title={title}
      caption={caption}
      ariaLabel={ariaLabel}
      // One viewBox unit is at most one CSS pixel — Epic 9.21's rule, for the
      // same reason: a strip stretched across a wide column would thicken its
      // hairlines and patterns with it.
      style={{ maxWidth: `${layout.width}px` }}
      dataTable={
        <table>
          <caption>{ariaLabel}</caption>
          <thead>
            <tr>
              <th scope="col">Brand</th>
              <th scope="col">Share</th>
            </tr>
          </thead>
          <tbody>
            {layout.segments.map((s) => (
              <tr key={s.key}>
                <th scope="row">{s.isSubject ? `${s.label} (this client)` : s.label}</th>
                <td>{fmt(s.value)}</td>
              </tr>
            ))}
            {layout.remainder && (
              <tr>
                <th scope="row">{remainderLabel}</th>
                <td>{fmt(layout.remainder.value)}</td>
              </tr>
            )}
            {layout.absent.map((a) => (
              <tr key={a.key}>
                <th scope="row">{a.isSubject ? `${a.label} (this client)` : a.label}</th>
                <td>{a.kind === 'zero' ? `${fmt(0)}, ${zeroNote}` : 'not measured'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="presentation"
        aria-hidden="true"
        className="avp-share__svg"
        focusable="false"
      >
        <ChartPatterns />
        {layout.segments.map((s) => {
          const p = paint(s.isSubject, s.rivalIndex);
          return (
            <rect
              key={s.key}
              className={cn(
                'avp-share__seg',
                s.isSubject && 'avp-share__seg--subject',
                p.outline && 'avp-share__seg--outline',
              )}
              x={s.x}
              y={0}
              width={s.width}
              height={layout.height}
              fill={p.fill}
              {...(p.stroke ? { stroke: p.stroke } : {})}
            />
          );
        })}
        {layout.remainder && (
          <rect
            className="avp-share__seg avp-share__seg--remainder"
            x={layout.remainder.x}
            y={0.5}
            width={layout.remainder.width}
            height={layout.height - 1}
          />
        )}
        {layout.segments.length === 0 && !layout.remainder && (
          // Nothing measured above zero: an empty track, never a full one.
          <rect className="avp-share__seg avp-share__seg--empty" x={0} y={0} width={layout.width} height={layout.height} />
        )}
      </svg>
      <dl className="avp-share__legend">
        {layout.segments.map((s) => {
          const p = paint(s.isSubject, s.rivalIndex);
          return (
            <div key={s.key} className="avp-share__item">
              <dt className={cn('avp-share__label', s.isSubject && 'avp-share__label--subject')}>
                <svg className="avp-share__swatch" viewBox="0 0 12 8" aria-hidden="true" focusable="false">
                  <rect
                    x={0.5}
                    y={0.5}
                    width={11}
                    height={7}
                    fill={p.fill}
                    stroke={p.stroke ?? 'var(--avp-line-strong)'}
                  />
                </svg>
                {s.label}
                {s.isSubject && <span className="avp-share__you"> · this client</span>}
              </dt>
              <dd className="avp-share__value">{fmt(s.value)}</dd>
            </div>
          );
        })}
        {layout.remainder && (
          <div className="avp-share__item">
            <dt className="avp-share__label">
              <svg className="avp-share__swatch" viewBox="0 0 12 8" aria-hidden="true" focusable="false">
                <rect className="avp-share__seg--remainder" x={0.5} y={0.5} width={11} height={7} />
              </svg>
              {remainderLabel}
            </dt>
            <dd className="avp-share__value">{fmt(layout.remainder.value)}</dd>
          </div>
        )}
        {layout.absent.map((a) => (
          <div key={a.key} className="avp-share__item avp-share__item--absent">
            <dt className={cn('avp-share__label', a.isSubject && 'avp-share__label--subject')}>
              {a.label}
              {a.isSubject && <span className="avp-share__you"> · this client</span>}
            </dt>
            <dd className="avp-share__absent">
              {a.kind === 'zero' ? `${fmt(0)}, ${zeroNote}` : 'not measured'}
            </dd>
          </div>
        ))}
      </dl>
    </ChartFrame>
  );
}

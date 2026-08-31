import type { JSX } from 'react';
import { cn } from '../../lib/cn.js';
import { seriesStyle } from '../../tokens/color.js';
import { ChartFrame } from './ChartFrame.js';
import {
  layoutTrend,
  segmentPath,
  spreadLabels,
  truncateLabel,
  type TrendPoint,
  type TrendSeriesInput,
  type TrendLayoutOptions,
} from './trendLayout.js';

export interface TrendChartProps {
  /** The x-axis, oldest first. */
  points: readonly TrendPoint[];
  series: readonly TrendSeriesInput[];
  /** Required, like every chart in this system. See ChartFrame. */
  ariaLabel: string;
  title?: string;
  caption?: string;
  /** Appended to each value in the legend and the data table. "%" or "". */
  unit?: string;
  /** Force the top of the scale — 100 for a percentage. */
  yMax?: number;
  height?: number;
  className?: string;
  layoutOptions?: TrendLayoutOptions;
}

/**
 * TrendChart — one line per series across a client's scan history.
 *
 * WHAT IT IS FOR, AND WHY THE LEDGER COULD NOT BE IT
 * ---------------------------------------------------
 * `LuminanceLedger` is a SNAPSHOT: one stacked column whose segment heights are
 * the §6 weights and whose lit fraction is one scan's value. Its correctness
 * condition — total lit height IS the composite — is a statement about a single
 * measurement, and there is no axis in it for time. `AnswerShelf` is ordinal
 * position within one scan's answers. Neither can carry a second scan, so this
 * is the first time-series shape in the system rather than a variant of one.
 *
 * THE COLOUR RULE IS THE SAME ONE THE LEDGER'S GHOST COLUMNS FOLLOW
 * -----------------------------------------------------------------
 * design-direction.md §1: the client is always `beacon-600`, solid; competitors
 * are drawn from the neutral slate family and separated by **lightness and
 * pattern rather than hue**, never by how well they are doing. A rival rendered
 * in "good green" reads as an endorsement and one in "bad red" reads as a
 * hatchet job, and the report cannot afford either. So competitor colour comes
 * from `seriesStyle('competitor', i)` — the same function the Ledger and the
 * DataTable use — and the visibility ramp is never touched here.
 *
 * The line's DASH is derived from that same call's `pattern`, which is how the
 * §1 rule ("solid -> 45deg hatch -> dot -> outline") expresses itself on a
 * stroke rather than a fill: five neutral greys are not distinguishable in
 * greyscale print, five dash patterns are.
 *
 * A NULL IS A GAP, NEVER A ZERO
 * ------------------------------
 * A competitor detected in one scan and not the next has no reading, not a
 * reading of nothing. `trendLayout.segments` breaks the line rather than
 * dropping it to the floor, and a series that vanishes entirely keeps its
 * legend row marked "not measured" rather than disappearing — a silently
 * dropped line is indistinguishable from a rival who was never there.
 *
 * Pure and hook-free, so it stays server-renderable like the rest of the
 * chart set.
 */
export function TrendChart({
  points,
  series,
  ariaLabel,
  title,
  caption,
  unit = '',
  yMax,
  height,
  className,
  layoutOptions,
}: TrendChartProps): JSX.Element {
  const layout = layoutTrend(points, series, {
    ...layoutOptions,
    ...(yMax != null ? { yMax } : {}),
    ...(height != null ? { height } : {}),
  });

  const fmt = (v: number) => `${Number.isInteger(v) ? v : v.toFixed(1)}${unit}`;

  // Names are written at the end of each line, so on a crowded chart they have
  // to be separated or they overwrite each other. Found in a live browser.
  const labelY = spreadLabels(
    layout.series
      .filter((s) => s.plotted.length > 0)
      .map((s) => ({ key: s.key, y: s.plotted[s.plotted.length - 1]!.y })),
  );

  return (
    <ChartFrame
      className={cn('avp-trend', className)}
      /*
       * THE CAP, AND WHY IT IS DERIVED RATHER THAN DECLARED — Epic 9.21.
       *
       * `.avp-trend__svg` is `width: 100%` over a fixed viewBox, so the chart
       * scales its TYPE and its STROKES with its container. Measured on the
       * live Rankings screen: a 720-unit chart stretched across a 1200px
       * Working column ran at 1.6x, rendering its 11px axis labels at 17.6px
       * against 14px body copy, and its 2.5px subject stroke at 4px. The
       * smallest type on the page became the largest thing on it, which is
       * what made the screen read as sparse and oversized.
       *
       * Bounding it at `layout.width` makes one viewBox unit at most one CSS
       * pixel, so the chart can never render larger than it was drawn.
       *
       * It is taken from the layout rather than written as a token because the
       * two must be the SAME number — a caller passing `layoutOptions.width`
       * would otherwise be squeezed by a cap that had not moved with it. This
       * cannot drift; a constant would need a test to stop it drifting.
       *
       * A MAX, so the chart still scales DOWN to fit a narrow viewport. That
       * direction has to keep working.
       */
      style={{ maxWidth: `${layout.width}px` }}
      {...(title != null ? { title } : {})}
      {...(caption != null ? { caption } : {})}
      ariaLabel={ariaLabel}
      dataTable={
        <table>
          <caption>{title ?? ariaLabel}</caption>
          <thead>
            <tr>
              <th scope="col">Series</th>
              {layout.points.map((p) => (
                <th scope="col" key={p.stamp}>
                  {p.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {layout.series.map((s) => (
              <tr key={s.key}>
                <th scope="row">{s.isSubject ? `${s.label} (this client)` : s.label}</th>
                {layout.points.map((p, i) => {
                  const hit = s.plotted.find((q) => q.index === i);
                  return (
                    <td key={p.stamp}>{hit ? fmt(hit.value) : 'not measured'}</td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        className="avp-trend__svg"
        aria-hidden="true"
        focusable="false"
      >
        {/* horizontal rules + y labels */}
        {layout.ticks.map((t) => (
          <g key={t.value}>
            <line
              x1={layout.plot.x}
              y1={t.y}
              x2={layout.plot.x + layout.plot.width}
              y2={t.y}
              className="avp-trend__rule"
            />
            <text
              x={layout.plot.x - 8}
              y={t.y + 4}
              textAnchor="end"
              className="avp-trend__tick"
            >
              {fmt(t.value)}
            </text>
          </g>
        ))}

        {/* x labels — one per scan */}
        {layout.points.map((p, i) => (
          <text
            key={p.stamp}
            x={layout.columns[i]}
            y={layout.plot.y + layout.plot.height + 20}
            textAnchor="middle"
            className="avp-trend__tick"
          >
            {p.label}
          </text>
        ))}

        {/*
          Competitors first, subject last, so the client's line is never
          crossed out by a rival's. Ordering IS the emphasis here — there is no
          z-index in SVG.
        */}
        {[...layout.series]
          .sort((a, b) => Number(a.isSubject) - Number(b.isSubject))
          .map((s) => {
            const style = seriesStyle(s.isSubject ? 'subject' : 'competitor', s.seriesIndex);
            const dash = s.isSubject ? undefined : DASH[style.pattern];
            const last = s.plotted[s.plotted.length - 1];
            return (
              <g
                key={s.key}
                className={cn('avp-trend__series', s.isSubject && 'is-subject')}
              >
                {s.segments.map((seg, i) => (
                  <path
                    key={i}
                    d={segmentPath(seg)}
                    fill="none"
                    stroke={style.fill}
                    strokeWidth={s.isSubject ? 2.5 : 1.5}
                    {...(dash != null ? { strokeDasharray: dash } : {})}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                ))}
                {/* A run of one has no line to draw, so it needs its own dot. */}
                {s.segments
                  .filter((seg) => seg.points.length === 1)
                  .map((seg) => (
                    <circle
                      key={`solo-${seg.points[0]!.index}`}
                      cx={seg.points[0]!.x}
                      cy={seg.points[0]!.y}
                      r={s.isSubject ? 3.5 : 2.5}
                      fill={style.fill}
                    />
                  ))}
                {/* The client gets a marker per reading; rivals stay plain. */}
                {s.isSubject &&
                  s.plotted.map((p) => (
                    <circle key={p.index} cx={p.x} cy={p.y} r={3} fill={style.fill} />
                  ))}
                {last && (
                  <text
                    x={layout.plot.x + layout.plot.width + 8}
                    y={(labelY[s.key] ?? last.y) + 4}
                    className={cn(
                      'avp-trend__label',
                      s.isSubject && 'avp-trend__label--subject',
                    )}
                    fill={s.isSubject ? style.fill : undefined}
                  >
                    {truncateLabel(s.label)}
                  </text>
                )}
              </g>
            );
          })}
      </svg>
    </ChartFrame>
  );
}

/**
 * The §1 competitor patterns, expressed on a stroke.
 *
 * `seriesStyle` returns a FILL pattern name because it was written for the
 * Ledger's bars and the shelf's marks. A line has no fill to hatch, so each
 * name maps to the dash that reads as its equivalent — and the point of both is
 * the same one §1 makes: five neutral greys are one grey in greyscale print,
 * five dash patterns are five lines.
 */
const DASH: Record<string, string | undefined> = {
  solid: undefined,
  'hatch-45': '6 3',
  dot: '1.5 3',
  'hatch-135': '9 3 2 3',
  outline: '3 3',
};

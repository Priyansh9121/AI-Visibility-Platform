import type { JSX } from 'react';
import { cn } from '../../lib/cn.js';
import { benchVar } from '../../tokens/color.js';
import { ChartFrame } from './ChartFrame.js';
import {
  layoutTide,
  type TideLayoutOptions,
  type TidePointInput,
} from './sentimentTideLayout.js';

export interface SentimentTideProps {
  /** Oldest scan first, like every time series in this system. */
  points: readonly TidePointInput[];
  /** Required, like every chart here. See ChartFrame. */
  ariaLabel: string;
  title?: string;
  caption?: string;
  /** Display name per engine key. Falls back to the raw key. */
  engineLabel?: Readonly<Record<string, string>>;
  /**
   * Bench accent index per engine key — Epic A.
   *
   * Passed in rather than derived here, so the hue an engine carries on this
   * chart is the SAME hue it carries on the Prompts screen. An operator who has
   * learnt that violet is ChatGPT must not have to re-learn it one tab across.
   */
  engineAccent?: Readonly<Record<string, number>>;
  height?: number;
  className?: string;
  layoutOptions?: TideLayoutOptions;
  /** Bars grow from the waterline on first paint. See the note below. */
  animate?: boolean;
}

/**
 * SentimentTide — how each engine described the client, scan by scan.
 *
 * WHY A DIVERGING SHAPE, AND WHY IT IS NOT A TrendChart
 * -----------------------------------------------------
 * `TrendChart` plots one value per series per point — a share, a count. Tone is
 * not one value: it is a split of a scan's answers into positive, neutral and
 * negative, and the thing an operator needs to read first is the DIRECTION, not
 * the magnitude. A diverging bar answers that before any number is read — a
 * column sitting mostly under the waterline is bad news at a glance.
 *
 * TWO COLOUR LANGUAGES, DOING TWO DIFFERENT JOBS
 * -----------------------------------------------
 * **Tone is encoded by position, not hue.** Positive is above the line and
 * negative below it, so the ordinal reading survives greyscale, colour-blind
 * vision, and a printer — the same properties design-direction.md §1 demands of
 * the visibility ramp, achieved here by geometry instead of luminance.
 *
 * **Hue therefore encodes the ENGINE**, which is genuinely categorical, and it
 * comes from the `bench-*` Working-screen layer via `engineAccent`. That frees
 * this chart from needing a third palette, and it means the engine an operator
 * recognises on Prompts is the same colour here.
 *
 * The semantic `success`/`danger` pair was considered for tone and rejected:
 * §1 reserves semantics for SYSTEM STATE precisely so a red chip is never read
 * as a bad score, and sentiment is one of the five scored dimensions. A red bar
 * on a Working screen beside a visibility figure is exactly the confusion that
 * rule exists to prevent. Lightness carries tone instead — the solid body of
 * the bar is the engine's hue, negative is drawn at reduced opacity with a
 * hatch, and position does the rest.
 *
 * WHAT IS DELIBERATELY NOT DRAWN
 * -------------------------------
 * Answers where the subject was never named. They have no tone — the classifier
 * is not even called for them — and a fourth rect on a chart of tones would be
 * read as a fourth tone. The count reaches the reader in the hidden data table
 * and in the caller's ledger, in words. See `sentimentTideLayout.ts`.
 *
 * An engine that failed a whole scan gets NO column rather than four zeros; a
 * flat column on the waterline reads as "described you neutrally" instead of
 * "was down". `layout.missing` reports it so the caller can say so.
 *
 * Pure and hook-free, so it stays server-renderable like the rest of the set.
 */
export function SentimentTide({
  points,
  ariaLabel,
  title,
  caption,
  engineLabel = {},
  engineAccent = {},
  height,
  className,
  layoutOptions,
  animate = false,
}: SentimentTideProps): JSX.Element {
  const layout = layoutTide(points, {
    ...layoutOptions,
    ...(height != null ? { height } : {}),
  });

  const name = (engine: string): string => engineLabel[engine] ?? engine;
  const hue = (engine: string, index: number): string =>
    benchVar(engineAccent[engine] ?? index);

  return (
    <ChartFrame
      className={cn('avp-tide', animate && 'avp-tide--animate', className)}
      /*
       * The Epic 9.21 bound, derived from the layout rather than declared.
       * An SVG at `width: 100%` over a fixed viewBox scales its TYPE with its
       * box; bounding at `layout.width` makes one viewBox unit at most one CSS
       * pixel, so this chart can never render larger than it was drawn. A MAX,
       * so it still scales DOWN on a narrow viewport.
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
              <th scope="col">Scan</th>
              <th scope="col">Engine</th>
              <th scope="col">Positive</th>
              <th scope="col">Neutral</th>
              <th scope="col">Negative</th>
              <th scope="col">Not named</th>
            </tr>
          </thead>
          <tbody>
            {layout.points.map((point, pointIndex) => {
              const bars = layout.bars.filter((x) => x.pointIndex === pointIndex);
              const absent =
                layout.missing.find((m) => m.pointIndex === pointIndex)?.engines ?? [];
              return [
                ...bars.map((bar) => (
                  <tr key={`${point.stamp}-${bar.engine}`}>
                    <th scope="row">{point.label}</th>
                    <td>{name(bar.engine)}</td>
                    <td>{bar.positive.count}</td>
                    <td>{bar.neutral.count}</td>
                    <td>{bar.negative.count}</td>
                    <td>{bar.unclassified.count}</td>
                  </tr>
                )),
                // Said in words, so a screen reader hears an outage as an
                // outage rather than as a row of zeros.
                ...absent.map((engine) => (
                  <tr key={`${point.stamp}-${engine}-absent`}>
                    <th scope="row">{point.label}</th>
                    <td>{name(engine)}</td>
                    <td colSpan={4}>did not answer</td>
                  </tr>
                )),
              ];
            })}
          </tbody>
        </table>
      }
    >
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        className="avp-tide__svg"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          {/*
            Negative is hatched as well as being below the line. §1's rule for
            competitor series applied to tone: one direction distinguished by
            pattern survives greyscale and colour-blind reading, where a hue
            difference alone would not.
          */}
          <pattern
            id="avp-tide-negative"
            width="4"
            height="4"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="4" height="4" fill="currentColor" opacity="0.28" />
            <line x1="0" y1="0" x2="0" y2="4" stroke="currentColor" strokeWidth="2" />
          </pattern>
        </defs>

        {/* The waterline itself — the only rule that matters on this chart. */}
        <line
          x1={layout.plot.x}
          y1={layout.waterline}
          x2={layout.plot.x + layout.plot.width}
          y2={layout.waterline}
          className="avp-tide__waterline"
        />

        {layout.bars.map((bar) => {
          const colour = hue(bar.engine, bar.engineIndex);
          return (
            <g
              key={`${bar.pointIndex}-${bar.engine}`}
              className="avp-tide__bar"
              style={{ color: colour } as Record<string, string>}
            >
              {bar.positive.height > 0 && (
                <rect
                  x={bar.x}
                  y={bar.positive.y}
                  width={bar.width}
                  height={bar.positive.height}
                  fill={colour}
                  className="avp-tide__seg avp-tide__seg--positive"
                />
              )}
              {bar.neutral.height > 0 && (
                <rect
                  x={bar.x}
                  y={bar.neutral.y}
                  width={bar.width}
                  height={bar.neutral.height}
                  fill={colour}
                  opacity={0.32}
                  className="avp-tide__seg avp-tide__seg--neutral"
                />
              )}
              {bar.negative.height > 0 && (
                <rect
                  x={bar.x}
                  y={bar.negative.y}
                  width={bar.width}
                  height={bar.negative.height}
                  fill="url(#avp-tide-negative)"
                  className="avp-tide__seg avp-tide__seg--negative"
                />
              )}
            </g>
          );
        })}

        {/* One label per scan. */}
        {layout.points.map((point) => (
          <text
            key={point.stamp}
            x={point.x}
            y={layout.plot.y + layout.plot.height + 20}
            textAnchor="middle"
            className="avp-tide__tick"
          >
            {point.label}
          </text>
        ))}

        {/*
          The zero line, labelled — and ONLY the zero line.
          "Positive" and "Negative" were written down the left edge at first and
          were clipped by the viewBox in a live browser ("SITIVE", "ATIVE"),
          because an 8-character label set in caps does not fit a chart's left
          gutter. They were also redundant: the lead paragraph above every
          instance of this chart already says which way is up, in a sentence,
          where it can be read rather than decoded. A single `0` is the
          conventional mark for a diverging axis and needs no gutter at all.
        */}
        <text
          x={layout.plot.x - 6}
          y={layout.waterline + 4}
          textAnchor="end"
          className="avp-tide__tick"
        >
          0
        </text>
      </svg>
    </ChartFrame>
  );
}

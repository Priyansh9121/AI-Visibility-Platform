'use client';

// Uses React hooks, so it must run on the client under the Next App Router.
// Marked at the component rather than the package level on purpose: Card,
// Badge, Table and the report primitives are pure and stay server-renderable,
// which Epic 7's server-side PDF render depends on.

import { useEffect, useState, useId, type JSX } from 'react';
import {
  layoutLedger,
  type LedgerCompetitor,
  type LedgerDimension,
} from './ledgerLayout.js';
import { ChartFrame } from './ChartFrame.js';
import { onVisibility } from '../../tokens/color.js';
import { cn } from '../../lib/cn.js';

export interface LuminanceLedgerProps {
  /** The client/prospect being reported on. */
  subjectName: string;
  dimensions: readonly LedgerDimension[];
  competitors?: readonly LedgerCompetitor[];
  /** Column height in SVG units. */
  height?: number;
  /** Animate the dim-to-lit reveal on mount. Honours prefers-reduced-motion. */
  animate?: boolean;
  /** Show the auto-derived "biggest gap" annotation. */
  annotateGap?: boolean;
  className?: string;
}

const COLUMN_WIDTH = 132;
const GHOST_WIDTH = 26;
const GHOST_GAP = 12;
const LABEL_GUTTER = 188;
const PAD_TOP = 28;
const PAD_BOTTOM = 34;

/**
 * THE LUMINANCE LEDGER — the product's signature visualisation.
 *
 * The score drawn as light. Each sub-score is a segment whose HEIGHT is its
 * weight (points available) and whose LIT PORTION is its value (points earned).
 * The unearned remainder is left as a dim, unlit void with a hairline marking
 * how far it could have reached.
 *
 * Because segment height tracks weight and lit fraction tracks value, the total
 * lit height of the column is exactly the composite score — the chart IS the
 * number rather than a picture of it. Asserted in ledgerLayout.test.ts.
 *
 * Competitors render as narrow GHOST columns: outline only, no ramp colour, a
 * single cap line at their composite. Deliberately plain — see the series
 * colour rule in tokens/color.ts for why competitors are never coloured by
 * quality.
 *
 * Explicitly not a gauge and not a radar chart. A gauge throws away the
 * per-dimension structure that makes the score actionable; a radar implies the
 * axes are commensurable and equally weighted, which under §6 they are not.
 */
export function LuminanceLedger({
  subjectName,
  dimensions,
  competitors = [],
  height = 420,
  animate = true,
  annotateGap = true,
  className,
}: LuminanceLedgerProps): JSX.Element {
  const layout = layoutLedger(dimensions, { height, competitors });
  const uid = useId().replace(/:/g, '');
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

  const ghostBlockWidth =
    competitors.length > 0 ? competitors.length * (GHOST_WIDTH + GHOST_GAP) + GHOST_GAP : 0;
  const width = LABEL_GUTTER + COLUMN_WIDTH + ghostBlockWidth + 24;
  const totalHeight = height + PAD_TOP + PAD_BOTTOM;
  const columnX = LABEL_GUTTER;

  if (layout.composite === null) {
    return (
      <div className={cn('avp-ledger avp-ledger--empty', className)}>
        <p className="avp-ledger__empty-title">Not enough data to score</p>
        <p className="avp-ledger__empty-body">
          This scan has no tracked prompts yet, so there is no visibility to measure. An
          unrunnable scan is never reported as a low score.
        </p>
      </div>
    );
  }

  const score = Math.round(layout.composite);

  return (
    <ChartFrame
      className={cn('avp-ledger', className)}
      ariaLabel={
        `AI Visibility Score for ${subjectName}: ${score} out of 100. ` +
        layout.segments
          .map((s) => `${s.label} ${Math.round(s.subscore)} of 100, weighted ${s.weight} percent`)
          .join('. ') +
        (layout.biggestGap
          ? `. Largest recoverable gap: ${layout.biggestGap.label}, worth ${layout.biggestGap.gap.toFixed(1)} points.`
          : '')
      }
      dataTable={
        <table>
          <caption>{`AI Visibility Score breakdown for ${subjectName}`}</caption>
          <thead>
            <tr>
              <th scope="col">Dimension</th>
              <th scope="col">Weight</th>
              <th scope="col">Sub-score</th>
              <th scope="col">Points earned</th>
              <th scope="col">Points available</th>
            </tr>
          </thead>
          <tbody>
            {layout.segments.map((s) => (
              <tr key={s.key}>
                <th scope="row">{s.label}</th>
                <td>{s.weight}%</td>
                <td>{Math.round(s.subscore)}</td>
                <td>{((s.weight * s.subscore) / 100).toFixed(1)}</td>
                <td>{s.weight}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Composite</th>
              <td colSpan={4}>{score} / 100</td>
            </tr>
          </tfoot>
        </table>
      }
    >
      <svg
        width="100%"
        viewBox={`0 0 ${width} ${totalHeight}`}
        className="avp-ledger__svg"
        aria-hidden="true"
        focusable="false"
      >
        <g transform={`translate(0, ${PAD_TOP})`}>
          {/* ---- subject column ---- */}
          {layout.segments.map((seg) => {
            const labelFits = seg.height >= 22;
            return (
              <g key={seg.key}>
                {/* unlit void: what this dimension could have been */}
                <rect
                  x={columnX}
                  y={seg.y}
                  width={COLUMN_WIDTH}
                  height={seg.height}
                  className="avp-ledger__void"
                />
                {/* lit portion: what was actually earned */}
                <rect
                  x={columnX}
                  y={seg.litY}
                  width={COLUMN_WIDTH}
                  height={seg.litHeight}
                  fill={seg.color}
                  className="avp-ledger__lit"
                  style={{
                    transformOrigin: `${columnX + COLUMN_WIDTH / 2}px ${seg.y + seg.height}px`,
                    transform: revealed ? 'scaleY(1)' : 'scaleY(0)',
                  }}
                />
                {/* segment boundary */}
                <line
                  x1={columnX}
                  y1={seg.y}
                  x2={columnX + COLUMN_WIDTH}
                  y2={seg.y}
                  className="avp-ledger__rule"
                />

                {/* dimension label in the gutter */}
                {labelFits && (
                  <>
                    <text
                      x={columnX - 16}
                      y={seg.y + seg.height / 2 - 5}
                      textAnchor="end"
                      className="avp-ledger__label"
                    >
                      {seg.label}
                    </text>
                    <text
                      x={columnX - 16}
                      y={seg.y + seg.height / 2 + 10}
                      textAnchor="end"
                      className="avp-ledger__weight"
                    >
                      {`${seg.weight}% weight`}
                    </text>
                  </>
                )}

                {/* sub-score inside the lit area, colour picked by luminance */}
                {seg.litHeight >= 20 && (
                  <text
                    x={columnX + COLUMN_WIDTH / 2}
                    y={seg.litY + Math.min(seg.litHeight, seg.height) / 2 + 4}
                    textAnchor="middle"
                    className="avp-ledger__value"
                    fill={onVisibility(seg.subscore)}
                    style={{ opacity: revealed ? 1 : 0 }}
                  >
                    {Math.round(seg.subscore)}
                  </text>
                )}
              </g>
            );
          })}

          {/* column outline drawn last so it sits above the fills */}
          <rect
            x={columnX}
            y={0}
            width={COLUMN_WIDTH}
            height={height}
            className="avp-ledger__outline"
          />

          {/* ---- biggest-gap annotation: this IS the report headline ---- */}
          {annotateGap && layout.biggestGap && layout.biggestGap.gap > 0.5 && (
            <g className="avp-ledger__annotation" style={{ opacity: revealed ? 1 : 0 }}>
              <line
                x1={columnX}
                y1={layout.biggestGap.y}
                x2={columnX + COLUMN_WIDTH}
                y2={layout.biggestGap.y}
                className="avp-ledger__gap-rule"
              />
              <rect
                x={columnX}
                y={layout.biggestGap.y}
                width={COLUMN_WIDTH}
                height={layout.biggestGap.height - layout.biggestGap.litHeight}
                className="avp-ledger__gap-zone"
              />
              <text
                x={columnX + COLUMN_WIDTH + 10}
                y={
                  layout.biggestGap.y +
                  (layout.biggestGap.height - layout.biggestGap.litHeight) / 2
                }
                className="avp-ledger__gap-label"
              >
                {`+${layout.biggestGap.gap.toFixed(1)} pts`}
              </text>
            </g>
          )}

          {/* ---- competitor ghost columns ---- */}
          {layout.ghosts.map((ghost) => {
            const gx = columnX + COLUMN_WIDTH + GHOST_GAP + ghost.index * (GHOST_WIDTH + GHOST_GAP);
            return (
              <g key={`${ghost.name}-${ghost.index}`}>
                <rect
                  x={gx}
                  y={0}
                  width={GHOST_WIDTH}
                  height={height}
                  className="avp-ledger__ghost"
                />
                <rect
                  x={gx}
                  y={ghost.capY}
                  width={GHOST_WIDTH}
                  height={height - ghost.capY}
                  className="avp-ledger__ghost-fill"
                />
                <line
                  x1={gx}
                  y1={ghost.capY}
                  x2={gx + GHOST_WIDTH}
                  y2={ghost.capY}
                  className="avp-ledger__ghost-cap"
                />
                <text
                  x={gx + GHOST_WIDTH / 2}
                  y={ghost.capY - 7}
                  textAnchor="middle"
                  className="avp-ledger__ghost-score"
                >
                  {Math.round(ghost.composite)}
                </text>
                <text
                  x={gx + GHOST_WIDTH / 2}
                  y={height + 20}
                  textAnchor="middle"
                  className="avp-ledger__ghost-name"
                >
                  {abbreviate(ghost.name)}
                </text>
              </g>
            );
          })}

          {/* baseline */}
          <line x1={columnX - 8} y1={height} x2={width} y2={height} className="avp-ledger__base" />
          <text x={columnX + COLUMN_WIDTH / 2} y={height + 20} textAnchor="middle" className="avp-ledger__subject">
            {subjectName}
          </text>
        </g>
        <title id={`ledger-${uid}`}>{`AI Visibility Score: ${score} of 100`}</title>
      </svg>
    </ChartFrame>
  );
}

/** Ghost columns are narrow; long competitor names need trimming. */
function abbreviate(name: string): string {
  return name.length <= 9 ? name : `${name.slice(0, 8)}…`;
}

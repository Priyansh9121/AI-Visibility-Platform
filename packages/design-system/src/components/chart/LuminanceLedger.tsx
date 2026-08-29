'use client';

// Uses React hooks, so it must run on the client under the Next App Router.
// Marked at the component rather than the package level on purpose: Card,
// Badge, Table and the report primitives are pure and stay server-renderable,
// which Epic 7's server-side PDF render depends on.

import { useEffect, useState, useId, type CSSProperties, type JSX } from 'react';
import {
  layoutLedger,
  type LedgerCompetitor,
  type LedgerDimension,
} from './ledgerLayout.js';
import { ChartFrame } from './ChartFrame.js';
import { onVisibility } from '../../tokens/color.js';
import { cn } from '../../lib/cn.js';
import { prefersReducedMotion } from '../../lib/motion.js';
import { useRevealOnIntersect } from '../../lib/useRevealOnIntersect.js';

export interface LuminanceLedgerProps {
  /** The client/prospect being reported on. */
  subjectName: string;
  dimensions: readonly LedgerDimension[];
  competitors?: readonly LedgerCompetitor[];
  /** Column height in SVG units. */
  height?: number;
  /** Animate the dim-to-lit reveal on mount. Honours prefers-reduced-motion. */
  animate?: boolean;
  /**
   * Light the dimensions ONE AT A TIME, when the chart scrolls into view —
   * Epic 9.16. **Defaults to `false`, and that default is a guardrail.**
   *
   * This component is rendered by two things with opposite requirements. The
   * marketing page wants the chart to build as you arrive at it, in the same
   * rhythm as the text around it. The REPORT is a document — it is printed, it
   * is PDF'd, and it is put in front of a prospect's CMO. `design-direction.md`
   * §0 makes the presenting context win ties, and its own Direction C was
   * declined partly because motion does not survive becoming a document.
   *
   * So the report must not acquire this, and the way to guarantee that is not a
   * comment asking people not to pass it. It is that **neither report call site
   * passes anything**, and the default they therefore get is the Epic 0
   * behaviour: every bar lights at once, on mount.
   *
   * Note this is the opposite default to `animate`, deliberately. `animate`
   * defaults TRUE because the dim-to-lit dissolve is the signature moment this
   * component exists for — design-direction.md §4 calls it "the metaphor stated
   * in motion the first time you see the product", and the report should have
   * it. Staggering is a page flourish, and a document should not acquire a
   * flourish by omission.
   *
   * Enforced by `ReportView.test.tsx`'s report-path regression test, not by
   * this paragraph.
   */
  staggerDimensions?: boolean;
  /** Show the auto-derived "biggest gap" annotation. */
  annotateGap?: boolean;
  /**
   * Draw the column as SHAPE ONLY — nothing has been measured yet. Epic 9.19.
   *
   * **Defaults to `false`, so the report is untouched**, the same guardrail
   * `staggerDimensions` uses and for the same reason: neither report call site
   * passes anything, so the default they get is Epic 0's behaviour.
   *
   * What it changes is what the chart CLAIMS, not how it is drawn. A ledger
   * given five dimensions at sub-score 0 already draws the right picture — an
   * unlit column with the real weights in the gutter — but it would still tell
   * a screen reader "AI Visibility Score: 0 out of 100" and tabulate five
   * zeroes. That is a measurement nobody took, and this product's whole
   * argument is that it does not assert figures it does not have. So this
   * suppresses the composite claim, the gap annotation and the per-dimension
   * values, and names the chart as the structure of a scan rather than the
   * result of one.
   *
   * It exists so an empty screen can be illustrated with THIS product's own
   * chart instead of a decorative graphic — see `EmptyState`'s note on why the
   * figure slot is not an icon slot.
   */
  unmeasured?: boolean;
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
  staggerDimensions = false,
  annotateGap = true,
  unmeasured = false,
  className,
}: LuminanceLedgerProps): JSX.Element {
  const layout = layoutLedger(dimensions, { height, competitors });
  const uid = useId().replace(/:/g, '');

  // TWO TRIGGERS, AND THE UNSTAGGERED ONE IS UNCHANGED FROM EPIC 0.
  //
  // Default (the report, the styleguide, the PDF): reveal on mount, via the
  // rAF below. Exactly the code that shipped in Epic 0, byte for byte.
  //
  // Staggered (the landing page only): reveal when the chart reaches the
  // viewport, using the same hook `Reveal` uses. That is not decoration — the
  // chart sits well below the fold on the marketing page, so a mount-triggered
  // build would have finished before anybody scrolled to it, and the bar-by-bar
  // sequence nobody ever saw would have been pure cost.
  const onIntersect = useRevealOnIntersect<SVGSVGElement>(animate && staggerDimensions);
  const [mountRevealed, setMountRevealed] = useState(!animate);

  useEffect(() => {
    if (!animate || staggerDimensions) return;
    if (prefersReducedMotion()) {
      setMountRevealed(true);
      return;
    }
    const id = requestAnimationFrame(() => setMountRevealed(true));
    return () => cancelAnimationFrame(id);
  }, [animate, staggerDimensions]);

  const revealed = staggerDimensions ? onIntersect.revealed : mountRevealed;

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
      className={cn(
        'avp-ledger',
        // The one marker the report-path regression test looks for.
        staggerDimensions && 'avp-ledger--staggered',
        unmeasured && 'avp-ledger--unmeasured',
        className,
      )}
      ariaLabel={
        unmeasured
          ? `The ${layout.segments.length} weighted dimensions an AI Visibility scan measures, none of them measured yet: ` +
            layout.segments.map((s) => `${s.label}, worth ${s.weight} points`).join('. ') +
            '.'
          : `AI Visibility Score for ${subjectName}: ${score} out of 100. ` +
            layout.segments
              .map((s) => `${s.label} ${Math.round(s.subscore)} of 100, weighted ${s.weight} percent`)
              .join('. ') +
            (layout.biggestGap
              ? `. Largest recoverable gap: ${layout.biggestGap.label}, worth ${layout.biggestGap.gap.toFixed(1)} points.`
              : '')
      }
      dataTable={
        unmeasured ? (
          <table>
            <caption>What an AI Visibility scan measures</caption>
            <thead>
              <tr>
                <th scope="col">Dimension</th>
                <th scope="col">Points available</th>
                <th scope="col">Measured</th>
              </tr>
            </thead>
            <tbody>
              {layout.segments.map((s) => (
                <tr key={s.key}>
                  <th scope="row">{s.label}</th>
                  <td>{s.weight}</td>
                  <td>Not yet</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
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
        )
      }
    >
      <svg
        ref={onIntersect.ref}
        width="100%"
        viewBox={`0 0 ${width} ${totalHeight}`}
        className="avp-ledger__svg"
        aria-hidden="true"
        focusable="false"
      >
        <g transform={`translate(0, ${PAD_TOP})`}>
          {/* ---- subject column ---- */}
          {layout.segments.map((seg, segIndex) => {
            const labelFits = seg.height >= 22;
            // Only emitted when staggering. An unstaggered ledger carries no
            // index at all, so the report's markup is unchanged rather than
            // merely unaffected — which is what the regression test asserts.
            const stagger: CSSProperties | undefined = staggerDimensions
              ? ({ '--avp-ledger-index': segIndex } as CSSProperties)
              : undefined;
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
                    ...stagger,
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

                {/* sub-score inside the lit area, colour picked by luminance.
                    Never drawn when unmeasured — a nothing-measured ledger has
                    no per-dimension figure to print, and at sub-score 0 there
                    is no lit area to print it in either. */}
                {!unmeasured && seg.litHeight >= 20 && (
                  <text
                    x={columnX + COLUMN_WIDTH / 2}
                    y={seg.litY + Math.min(seg.litHeight, seg.height) / 2 + 4}
                    textAnchor="middle"
                    className="avp-ledger__value"
                    fill={onVisibility(seg.subscore)}
                    style={{ opacity: revealed ? 1 : 0, ...stagger }}
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
          {annotateGap && !unmeasured && layout.biggestGap && layout.biggestGap.gap > 0.5 && (
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

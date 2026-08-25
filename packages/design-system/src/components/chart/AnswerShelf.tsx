import type { JSX } from 'react';
import {
  layoutAnswerShelf,
  shelfSummary,
  type ShelfRowInput,
  type ShelfLayoutOptions,
} from './answerShelfLayout.js';
import { ChartFrame } from './ChartFrame.js';
import { ChartPatterns, patternPaint } from './ChartPatterns.js';
import { beacon, oklch } from '../../tokens/color.js';
import { cn } from '../../lib/cn.js';

export interface AnswerShelfProps {
  /** The client/prospect being reported on. Always the beacon series. */
  subjectName: string;
  rows: readonly ShelfRowInput[];
  /** Short, declarative. Reads as a finding, not a chart title. */
  title?: string;
  caption?: string;
  className?: string;
  layoutOptions?: ShelfLayoutOptions;
}

const MARK_R = 5.5;
const NOTCH_R = 6;
const TICK_DY = 8.5;
/**
 * Right margin for the notch column's header, which is wider than a column.
 * The header is centred on the notch column and the column sits three pitches
 * clear of the last ordinal, so it has room on both sides.
 */
const NOTCH_LABEL_PAD = 44;

/**
 * THE ANSWER SHELF — the proof beat's visualisation.
 *
 * Direction A of `docs/design-direction.md` §5: *an AI answer has a limited
 * number of slots — who is standing in them?*
 *
 * One row per answer. Along each row, ordinal slots hold the brands the engine
 * actually named, in the order it named them. The subject renders as a filled
 * beacon marker; rivals as neutral slate with a print pattern; a citation in
 * that same answer hangs as a small anchor tick beneath the mark.
 *
 * When the subject is not named, its slot is drawn as an EXPLICIT EMPTY NOTCH
 * in a fixed column, never omitted. Stack the rows and those notches form a
 * vertical band of holes down the page: you see the shape of absence before
 * reading a word. That identity — every answered row carries exactly one
 * subject mark — is asserted in `answerShelfLayout.test.ts` rather than
 * trusted, because a row that silently renders nothing would not look like a
 * bug, it would look like a clean report.
 *
 * Rivals are never painted from the visibility ramp. A rival in "good green"
 * reads as an endorsement and one in "bad red" makes the report look like a
 * hatchet job — see the series colour rule in `tokens/color.ts`.
 *
 * The row label is OUR generated question. No engine answer text exists to
 * render: `EngineResult` has no column able to hold prose (ip-safety.md #7).
 */
export function AnswerShelf({
  subjectName,
  rows,
  title,
  caption,
  className,
  layoutOptions,
}: AnswerShelfProps): JSX.Element {
  const layout = layoutAnswerShelf(rows, layoutOptions);

  if (layout.rows.length === 0) {
    return (
      <div className={cn('avp-shelf avp-shelf--empty', className)}>
        <p className="avp-shelf__empty">No answers were recorded for this scan.</p>
      </div>
    );
  }

  const subjectFill = oklch(beacon['600']);
  const padTop = 22;
  const padBottom = 10;
  const height = layout.height + padTop + padBottom;

  return (
    <ChartFrame
      className={cn('avp-shelf', className)}
      title={title}
      caption={caption}
      ariaLabel={shelfSummary(layout, subjectName)}
      dataTable={
        <table>
          <caption>{`Which brands each answer named, and in what order, for ${subjectName}`}</caption>
          <thead>
            <tr>
              <th scope="col">Question</th>
              <th scope="col">Engine</th>
              <th scope="col">Brands named, in order</th>
              <th scope="col">{subjectName}&rsquo;s place</th>
              <th scope="col">Cited</th>
            </tr>
          </thead>
          <tbody>
            {layout.rows.map((laid) => {
              const named = laid.marks.map((m) => `${m.position}. ${m.entityName}`).join(', ');
              const place = !laid.answered
                ? 'No answer returned'
                : laid.subject.kind === 'present'
                  ? laid.subject.position > 0
                    ? `Named ${ordinal(laid.subject.position)}`
                    : 'Named, place not recorded'
                  : 'Not named';
              return (
                <tr key={laid.rowKey}>
                  <th scope="row">{laid.promptText}</th>
                  <td>{laid.engine}</td>
                  <td>
                    {named || 'None'}
                    {laid.hiddenCount > 0 ? ` (+${laid.hiddenCount} more)` : ''}
                  </td>
                  <td>{place}</td>
                  <td>
                    {laid.subject.kind === 'present' && laid.subject.cited ? 'Yes' : 'No'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      }
    >
      <svg
        viewBox={`0 0 ${layout.width + NOTCH_LABEL_PAD} ${height}`}
        width="100%"
        role="presentation"
        className="avp-shelf__svg"
      >
        <ChartPatterns />

        {/* Ordinal ruler. The shelf has places, and they are numbered. */}
        <g className="avp-shelf__ruler">
          {layout.ordinals.map((n) => (
            <text
              key={n}
              x={layout.labelWidth + (n - 1) * pitch(layout) + pitch(layout) / 2}
              y={12}
              textAnchor="middle"
              className="avp-shelf__ordinal"
            >
              {n}
            </text>
          ))}
          {/*
            The notch column's header. Clipped and right-anchored: the subject
            name can be any length, and centring an unbounded string on the
            last column pushed it straight out of the viewBox — the first
            render of this chart showed "6Help Scou" running off the edge.
          */}
          <text x={layout.notchX} y={12} textAnchor="middle" className="avp-shelf__ordinal">
            {truncate(subjectName, 12)}
          </text>
        </g>

        <g transform={`translate(0 ${padTop})`}>
          {layout.rows.map((laid) => (
            <g key={laid.rowKey} className="avp-shelf__row">
              <title>{laid.promptText}</title>

              {/* The row's baseline, so an empty shelf still reads as a row. */}
              <line
                x1={layout.labelWidth - 10}
                y1={laid.y}
                x2={layout.width - 6}
                y2={laid.y}
                className="avp-shelf__rule"
              />

              {/*
                Prompt AND engine. Each prompt is asked of every engine, so a
                label carrying only the question renders two rows that look
                like the same row printed twice — which is exactly how the
                first capture of this chart read.
              */}
              <text
                x={layout.labelWidth - 14}
                y={laid.y + 3.5}
                textAnchor="end"
                className="avp-shelf__label"
              >
                {truncate(laid.promptText, 30)}
                <tspan className="avp-shelf__engine"> · {laid.engine}</tspan>
              </text>

              {laid.marks.map((mark) => (
                <g key={mark.key}>
                  <circle
                    cx={mark.cx}
                    cy={mark.cy}
                    r={MARK_R}
                    fill={patternPaint(mark.pattern, mark.fill)}
                    stroke={mark.fill}
                    strokeWidth={1}
                  />
                  {mark.cited && (
                    <line
                      x1={mark.cx}
                      y1={mark.cy + TICK_DY}
                      x2={mark.cx}
                      y2={mark.cy + TICK_DY + 3}
                      stroke={mark.fill}
                      strokeWidth={1.5}
                    />
                  )}
                </g>
              ))}

              {/* The subject: a filled beacon, or the hole where it isn't. */}
              {laid.subject.kind === 'present' && (
                <>
                  <circle
                    cx={laid.subject.cx}
                    cy={laid.subject.cy}
                    r={MARK_R + 0.5}
                    fill={subjectFill}
                  />
                  {laid.subject.cited && (
                    <line
                      x1={laid.subject.cx}
                      y1={laid.subject.cy + TICK_DY}
                      x2={laid.subject.cx}
                      y2={laid.subject.cy + TICK_DY + 3}
                      stroke={subjectFill}
                      strokeWidth={1.5}
                    />
                  )}
                </>
              )}
              {laid.subject.kind === 'absent' && (
                <circle
                  cx={laid.subject.cx}
                  cy={laid.subject.cy}
                  r={NOTCH_R}
                  className="avp-shelf__notch"
                />
              )}
              {laid.subject.kind === 'unanswered' && (
                <line
                  x1={laid.subject.cx - 4}
                  y1={laid.subject.cy}
                  x2={laid.subject.cx + 4}
                  y2={laid.subject.cy}
                  className="avp-shelf__unanswered"
                />
              )}

              {laid.hiddenCount > 0 && (
                <text
                  x={layout.notchX - pitch(layout)}
                  y={laid.y + 3.5}
                  textAnchor="middle"
                  className="avp-shelf__more"
                >
                  +{laid.hiddenCount}
                </text>
              )}
            </g>
          ))}
        </g>
      </svg>
    </ChartFrame>
  );
}

/** Recover the column pitch from the layout, so the ruler tracks the marks. */
function pitch(layout: { labelWidth: number; ordinals: readonly number[]; notchX: number }): number {
  const n = layout.ordinals.length;
  // notchX = labelWidth + (n-1)*p + p/2 + 3p  =>  p = (notchX - labelWidth) / (n + 2.5)
  return (layout.notchX - layout.labelWidth) / (n + 2.5);
}

function ordinal(n: number): string {
  const suffix = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] ?? 'th';
  return `${n}${suffix}`;
}

/** Row labels are our own prompts; long ones are clipped for the axis only. */
function truncate(text: string, max = 42): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

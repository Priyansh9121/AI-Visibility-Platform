import type { JSX } from 'react';
import { cn } from '../../lib/cn.js';
import { benchAccent } from '../../tokens/color.js';
import {
  GAP_KIND_LABEL,
  GAP_KIND_SHORT,
  layoutGapGrid,
  type GapBrandInput,
  type GapRowInput,
  type GapRowLayout,
  type GapSort,
} from './gapGridLayout.js';

export interface GapGridProps {
  rows: readonly GapRowInput[];
  brands: readonly GapBrandInput[];
  /** Default `recurrence` — see `layoutGapGrid` for why that axis and not time. */
  sort?: GapSort;
  /** Accent index for the subject column. The screen's own `bench-*` hue. */
  accent?: number;
  caption?: string;
  /** Animate the cells in on mount. Off in tests and for reduced motion. */
  animate?: boolean;
  /**
   * A different scan is being fetched for this same grid.
   *
   * Dims and marks busy rather than unmounting, so the picker that triggered
   * it stays where the operator left their pointer. See the CSS note.
   */
  pending?: boolean;
  className?: string;
}

/**
 * The answer-gap grid — prompts down, brands across. Epic B.
 *
 * MARKUP IS A REAL TABLE, DELIBERATELY
 * -------------------------------------
 * Every other chart in this system is an SVG inside `ChartFrame`, which gives
 * it `role="img"`, an `aria-label` and a visually-hidden table equivalent. This
 * one is not, because it does not need to be: the data IS tabular, so the
 * accessible representation and the visible one can be the same object. An SVG
 * heatmap here would mean drawing a table and then hiding a second copy of it
 * for screen readers, which is two things to keep in step instead of one.
 *
 * `<th scope>` on both axes, so a cell is announced with its prompt and its
 * brand rather than as a bare number.
 *
 * COLOUR IS NEVER THE ONLY CARRIER
 * ---------------------------------
 * Every cell prints its count. Intensity is a second reading of the same
 * number, not the only one, which is what keeps the grid usable in greyscale
 * and under CVD — the same rule `VisibilityBadge` and the nav's current state
 * follow. The row verdict is a word in its own column, not a colour.
 *
 * THE SUBJECT COLUMN IS `beacon`, THE RIVALS ARE NEUTRAL
 * -------------------------------------------------------
 * design-direction.md §1: one brand, one colour, everywhere in the product. The
 * client under analysis is `beacon` here exactly as it is on every trend, and
 * rivals stay neutral rather than taking bench hues — on this screen a rival is
 * not a category to be told apart from other rivals, it is the thing that took
 * an answer, and colouring six of them six ways would say otherwise. The
 * screen's own bench accent belongs to the SCREEN, and is used on the header
 * rule and the subject column only.
 */
export function GapGrid({
  rows,
  brands,
  sort = 'recurrence',
  accent = 0,
  caption,
  animate = false,
  pending = false,
  className,
}: GapGridProps): JSX.Element {
  const layout = layoutGapGrid(rows, brands, { sort });
  const key = benchAccent(accent).key;

  return (
    <div
      className={cn(
        'avp-gapgrid',
        animate && 'avp-gapgrid--animate',
        pending && 'avp-gapgrid--pending',
        className,
      )}
      aria-busy={pending || undefined}
      /*
       * Focusable, because it scrolls. A horizontally scrollable region that
       * cannot take focus cannot be scrolled by keyboard, which on a narrow
       * viewport put the rival columns out of reach entirely for anyone
       * without a pointer. `role="region"` plus a name is what makes the tab
       * stop worth landing on rather than an unexplained one.
       */
      tabIndex={0}
      role="region"
      aria-label={caption ?? 'Answer gaps grid'}
      style={
        {
          '--avp-gapgrid-accent': `var(--avp-bench-${key}-600)`,
          '--avp-gapgrid-wash': `var(--avp-bench-${key}-050)`,
        } as Record<string, string>
      }
    >
      <table className="avp-gapgrid__table">
        {caption != null && <caption className="avp-gapgrid__caption">{caption}</caption>}
        <thead>
          <tr>
            <th scope="col" className="avp-gapgrid__corner">
              Prompt
            </th>
            <th scope="col" className="avp-gapgrid__verdict-head">
              Verdict
            </th>
            {layout.brands.map((brand) => (
              <th
                key={brand.name}
                scope="col"
                className={cn(
                  'avp-gapgrid__brand',
                  brand.isSubject && 'avp-gapgrid__brand--subject',
                )}
              >
                {/*
                  `title` because the name is TRUNCATED at 8rem — "Growthmarketingpro"
                  renders as "Growthmarketin…" and the full string was otherwise
                  reachable only by scrolling to the rivals table at the bottom
                  of the page. The same affordance `.avp-gapgrid__chip` already
                  uses for its verdict, so this extends a convention rather than
                  inventing one.
                */}
                <span className="avp-gapgrid__brand-name" title={brand.name}>
                  {brand.name}
                </span>
                <span className="avp-gapgrid__brand-count">
                  {brand.promptsNamed}
                  <span className="avp-visually-hidden"> prompts named this brand</span>
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {layout.rows.map((row) => (
            <Row key={row.promptId} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({ row }: { row: GapRowLayout }): JSX.Element {
  return (
    <tr
      className={cn(
        'avp-gapgrid__row',
        row.isGap && 'avp-gapgrid__row--gap',
        // Not a gap and not a win. Held apart from both so it cannot be read
        // as either — see gapGridLayout's note on `no_brands`.
        row.kind === 'no_brands' && 'avp-gapgrid__row--inert',
      )}
    >
      <th scope="row" className="avp-gapgrid__prompt">
        <span className="avp-gapgrid__prompt-text">{row.text}</span>
        <span className="avp-gapgrid__prompt-meta">
          {row.intent.replace(/_/g, ' ')}
          {row.subjectCited && (
            <>
              {' · '}
              <span className="avp-gapgrid__cited">cited</span>
            </>
          )}
        </span>
      </th>
      <td className="avp-gapgrid__verdict">
        <span
          className={cn('avp-gapgrid__chip', `avp-gapgrid__chip--${row.kind}`)}
          title={GAP_KIND_LABEL[row.kind]}
        >
          {GAP_KIND_SHORT[row.kind]}
        </span>
        {row.absentOn > 0 && row.kind !== 'no_brands' && (
          <span className="avp-gapgrid__recurrence">
            {row.absentOn}/{row.enginesAnswered}
            <span className="avp-visually-hidden">
              {' '}
              engines answered without naming this client
            </span>
          </span>
        )}
      </td>
      {row.cells.map((cell) => (
        <td
          key={cell.brand}
          className={cn(
            'avp-gapgrid__cell',
            cell.isSubject && 'avp-gapgrid__cell--subject',
            cell.tookFromSubject && 'avp-gapgrid__cell--took',
            cell.namedOn === 0 && 'avp-gapgrid__cell--none',
          )}
          style={{ '--avp-gapgrid-fill': String(cell.intensity) } as Record<string, string>}
        >
          {/* The number is always present. Intensity is a second reading of
              it, never the only one. */}
          <span className="avp-gapgrid__count">{cell.namedOn > 0 ? cell.namedOn : '·'}</span>
        </td>
      ))}
    </tr>
  );
}

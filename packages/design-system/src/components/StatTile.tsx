import type { JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';
import { benchAccent } from '../tokens/color.js';

export interface StatTileProps {
  /** The micro-label above the figure. */
  label: string;
  /** The figure itself. A node, so a ratio ("3 / 5") is as valid as a count. */
  value: ReactNode;
  /**
   * One line under the figure saying what it means, when the label cannot.
   *
   * Optional because a tile with nothing useful to add should say nothing — a
   * row of tiles each restating its own label is noise dressed as density.
   */
  note?: ReactNode;
  /**
   * Which Working-screen accent this tile carries — an index into
   * `BENCH_ACCENTS`, cycled, so a caller may pass a plain array position.
   *
   * Categorical and rank-free: tile 3 is not worse than tile 1. Omit it and the
   * tile is neutral, which is what a figure with no category should be.
   */
  accent?: number;
  /** Marks a figure an operator may need to act on. Weight, not only colour. */
  emphasis?: boolean;
  className?: string;
}

/**
 * StatTile — one figure on a Working screen.
 *
 * WHY THIS IS A COMPONENT NOW
 * ---------------------------
 * `DashboardView` and `ClientsView` each had a private `Stat` helper, and
 * `ClientsView`'s carried a note saying the duplication was deliberate: two
 * call sites is a coincidence, and "if a third screen wants it, that is the
 * point to move it." Epic 9.24 is that third screen — several of them — so
 * this is the move that note asked for rather than a new idea.
 *
 * WHY IT IS A TILE RATHER THAN A LABEL AND A NUMBER
 * -------------------------------------------------
 * The old helper was two stacked spans, and a row of them read as a caption
 * strip rather than as figures worth looking at — a large part of why these
 * screens felt like a monochrome table with a header above it. A tile has a
 * surface, a hairline and an accent rail, so a row of them is scannable at a
 * glance and the screen has something real holding its width.
 *
 * MARKUP: a `<div>` wrapping `<dt>`/`<dd>`, which is the grouping HTML defines
 * for a description list. Not an `<a>` — a tile is a figure, and wrapping a
 * dt/dd group in an anchor is neither valid nor useful. A figure that should
 * lead somewhere gets a link inside its note, where the link text can say where
 * it goes.
 *
 * ACCESSIBILITY: the accent is decoration and never the only carrier of
 * anything. Every tile states its label in words, and `emphasis` changes weight
 * as well as colour — design-direction.md's rule that active state is marked by
 * more than hue, applied here too.
 */
export function StatTile({
  label,
  value,
  note,
  accent,
  emphasis = false,
  className,
}: StatTileProps): JSX.Element {
  const accented = accent != null;
  const key = accented ? benchAccent(accent).key : null;

  return (
    <div
      className={cn(
        'avp-tile',
        accented && 'avp-tile--accented',
        emphasis && 'is-emphasis',
        className,
      )}
      /*
       * The accent is passed as a custom property rather than a colour literal
       * so the tile follows a theme switch. `benchColor` would freeze the light
       * value into the markup — see `benchVar` in tokens/color.ts.
       */
      style={
        key == null
          ? undefined
          : ({
              '--avp-tile-accent': `var(--avp-bench-${key}-600)`,
              '--avp-tile-wash': `var(--avp-bench-${key}-050)`,
              '--avp-tile-line': `var(--avp-bench-${key}-100)`,
            } as Record<string, string>)
      }
    >
      <dt className="avp-tile__label">{label}</dt>
      <dd className="avp-tile__value">{value}</dd>
      {note != null && <dd className="avp-tile__note">{note}</dd>}
    </div>
  );
}

export interface StatRowProps {
  children: ReactNode;
  /**
   * Minimum tile width before the row wraps to fewer columns.
   *
   * A column COUNT is deliberately not the knob. The same tiles have to sit
   * under a 90rem Working column and inside a narrow viewport, and a fixed
   * count breaks at one end or the other; a minimum width lets the row choose
   * its own count and fill the space it is given, which is what closes the
   * empty-margin problem rather than hiding it behind a narrower container.
   */
  min?: string;
  className?: string;
}

/** The auto-fitting grid a row of `StatTile`s sits in. */
export function StatRow({ children, min = '11rem', className }: StatRowProps): JSX.Element {
  return (
    <dl
      className={cn('avp-tile-row', className)}
      style={{ '--avp-tile-min': min } as Record<string, string>}
    >
      {children}
    </dl>
  );
}

import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';
import { benchAccent } from '../../tokens/color.js';

export interface LocalNavItemProps {
  href: string;
  label: string;
  /** The screen currently shown. */
  current?: boolean;
  /**
   * Marks a destination that leaves this record's space — rendered with a
   * trailing indicator so it is not a surprise.
   *
   * The client's Report is the case this exists for: it is a REAL path into
   * `/scans/{id}/report`, the document that already exists, rather than a
   * second copy of it rendered inside the record frame.
   */
  external?: boolean;
  /**
   * Which Working-screen accent this section carries — Epic 9.24.
   *
   * Same reasoning as `NavItem.accent`, one level down: a client's sections are
   * a strip of five words an operator moves between constantly, and a stable
   * hue per section makes the strip readable at a glance. Decoration only —
   * `aria-current` and weight carry the state.
   */
  accent?: number;
}

/**
 * One destination inside a record's own space.
 *
 * An `<a>` for the reason `NavItem` is one: every destination here is a real
 * URL and must be middle-clickable and bookmarkable. `aria-current="page"`
 * marks the active one rather than colour alone.
 *
 * **No disabled variant, same as `NavItem`.** An item either goes somewhere
 * real or is not in the nav.
 *
 * Rendered as an `<li>`, because every item now sits inside a
 * `LocalNavGroup`'s `<ul>` — the grouping is real structure a screen reader
 * can report ("Measurement, list, 6 items"), not a heading that happens to sit
 * above some links.
 */
export function LocalNavItem({
  href,
  label,
  current,
  external,
  accent,
}: LocalNavItemProps): JSX.Element {
  const key = accent == null ? null : benchAccent(accent).key;
  return (
    <li className="avp-localnav__slot">
    <a
      href={href}
      aria-current={current ? 'page' : undefined}
      className={cn('avp-localnav__item', current && 'is-current', key != null && 'has-accent')}
      style={
        key == null
          ? undefined
          : ({
              '--avp-localnav-accent': `var(--avp-bench-${key}-600)`,
              '--avp-localnav-wash': `var(--avp-bench-${key}-050)`,
            } as Record<string, string>)
      }
    >
      {label}
      {external === true && (
        <span className="avp-localnav__out" aria-hidden="true">
          ↗
        </span>
      )}
    </a>
    </li>
  );
}

export interface LocalNavGroupProps {
  /** The cluster's name. Short — it is a label, not a description. */
  label: string;
  children: ReactNode;
  className?: string;
}

/**
 * A labelled cluster of destinations inside a `LocalNav`.
 *
 * WHY THE NAV GROWS GROUPS RATHER THAN MORE HUES
 * ----------------------------------------------
 * The `bench-*` layer holds seven accents and no more: the 30-degree meaning
 * buffer and the sRGB gamut at the shared chroma table together leave one
 * usable arc, 256.5 to 355 degrees, and seven fill it (see `BENCH_ACCENTS`).
 * A client's space is heading for ten sections, so a flat strip would have run
 * out — and the ways out of that without groups all cost something real:
 * shrinking the buffer lets a chip be misread as a score or a system state,
 * and letting chroma vary makes one accent read as more important than another.
 *
 * Grouping costs neither. **A hue only has to be told apart from the others in
 * its OWN cluster**, so each cluster restarts at the first accent and a cluster
 * of five never approaches the ceiling.
 *
 * **This is not a new idea in this product — it is an existing one written
 * down.** `WorkspaceShell`'s sidebar and `ClientSpace`'s strip are on screen
 * together and have shared hues 0-3 since Epic 9.24: Dashboard and Overview
 * are both cobalt, Clients and Rankings are both violet. Nobody has read that
 * as a collision, because the two navs are different places doing different
 * jobs. A hue was already scoped to its nav rather than to the product; this
 * scopes it one level further, to its cluster.
 *
 * **The cost, stated plainly:** two items in the SAME strip can now share a
 * hue, separated by a group label rather than by being in a different region
 * of the screen. That is weaker separation than the sidebar/strip precedent,
 * and it is the deliberate trade — repetition across clusters is the mechanism
 * that buys the seats. Partitioning the arc between clusters instead would
 * keep every hue unique and buy nothing at all.
 *
 * Renders a `<ul>` with an accessible name, so the grouping is structure rather
 * than a heading that happens to sit above some links.
 */
export function LocalNavGroup({
  label,
  children,
  className,
}: LocalNavGroupProps): JSX.Element {
  return (
    <div className={cn('avp-localnav__group', className)}>
      <span className="avp-localnav__grouplabel" aria-hidden="true">
        {label}
      </span>
      <ul className="avp-localnav__grouplist" aria-label={label}>
        {children}
      </ul>
    </div>
  );
}

export interface LocalNavProps {
  /** Whose space this is — the record's name, and what it is under it. */
  title: ReactNode;
  subtitle?: ReactNode;
  /** Where this record sits. A link back to the list it came from. */
  back?: { href: string; label: string };
  /** Facts about the record, shown beside the title. */
  meta?: ReactNode;
  /** `LocalNavItem`s. */
  children: ReactNode;
  className?: string;
}

/**
 * LocalNav — navigation scoped to ONE record, inside the agency-wide shell.
 *
 * WHY THIS IS A SECOND KIND OF NAVIGATION RATHER THAN MORE SIDEBAR ITEMS
 * ----------------------------------------------------------------------
 * `AppShell`'s sidebar is agency-wide: every destination in it is about the
 * whole account, across every client at once. Depth about ONE client cannot go
 * there without either changing what the sidebar means or requiring a global
 * "selected client" that the rest of the product does not have.
 *
 * So this is a different level of the same tree, and it says so structurally:
 * a heading naming the record, a link back to the list it came from, and a
 * horizontal strip of destinations that are all inside it. An operator can
 * always tell whose space they are standing in, which is the thing a nested
 * navigation has to get right before anything else.
 *
 * Deliberately NOT a tab widget. These are pages with their own URLs, not
 * panels behind a `role="tablist"` — a tab control that swaps `aria-selected`
 * on navigation lies to a screen reader about what just happened.
 *
 * Pure and hook-free, so it stays server-renderable.
 */
export function LocalNav({
  title,
  subtitle,
  back,
  meta,
  children,
  className,
}: LocalNavProps): JSX.Element {
  return (
    <div className={cn('avp-localnav', className)}>
      {back != null && (
        <a href={back.href} className="avp-localnav__back">
          <span aria-hidden="true">←</span> {back.label}
        </a>
      )}
      <div className="avp-localnav__head">
        <div className="avp-localnav__identity">
          <h1 className="avp-localnav__title">{title}</h1>
          {subtitle != null && <p className="avp-localnav__subtitle">{subtitle}</p>}
        </div>
        {meta != null && <div className="avp-localnav__meta">{meta}</div>}
      </div>
      <nav className="avp-localnav__items" aria-label={`Sections for this client`}>
        {children}
      </nav>
    </div>
  );
}

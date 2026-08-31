import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';

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
 */
export function LocalNavItem({
  href,
  label,
  current,
  external,
}: LocalNavItemProps): JSX.Element {
  return (
    <a
      href={href}
      aria-current={current ? 'page' : undefined}
      className={cn('avp-localnav__item', current && 'is-current')}
    >
      {label}
      {external === true && (
        <span className="avp-localnav__out" aria-hidden="true">
          ↗
        </span>
      )}
    </a>
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

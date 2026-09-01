import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';
import { benchAccent } from '../../tokens/color.js';

export interface NavItemProps {
  href: string;
  label: string;
  /** Rendered before the label. Lucide only (ip-safety.md #4), or nothing. */
  icon?: ReactNode;
  /** The item for the screen currently shown. */
  current?: boolean;
  /** Short note under the label — for an item that leads somewhere honest but thin. */
  note?: string;
  /**
   * Which Working-screen accent this destination carries — Epic 9.24.
   *
   * The sidebar was a column of identical grey glyphs, which is the least
   * scannable thing a persistent frame can be: an operator reading it had to
   * read the WORDS to find the place they go twenty times a day. A stable hue
   * per destination makes it findable by shape and colour together.
   *
   * It is decoration and never the only signal. `aria-current` marks the active
   * item to a screen reader, weight and a rail mark it visually, and every item
   * still states its name in words — the colour is a fourth cue, not the cue.
   */
  accent?: number;
}

/**
 * One sidebar destination.
 *
 * An `<a>`, not a button with an onClick. Every destination here is a real URL,
 * so it should be middle-clickable, bookmarkable, and readable by a screen
 * reader as a link. `aria-current="page"` marks the active one rather than
 * colour alone.
 *
 * **There is no disabled variant on purpose.** A nav item that looks like a
 * destination and goes nowhere is the "button that does nothing" this brief
 * rules out. An item either links somewhere real or is not in the sidebar.
 */
export function NavItem({ href, label, icon, current, note, accent }: NavItemProps): JSX.Element {
  const key = accent == null ? null : benchAccent(accent).key;
  return (
    <a
      href={href}
      aria-current={current ? 'page' : undefined}
      className={cn('avp-nav__item', current && 'is-current', key != null && 'has-accent')}
      /* Custom property, not a literal, so the sidebar follows a theme switch. */
      style={
        key == null
          ? undefined
          : ({
              '--avp-nav-accent': `var(--avp-bench-${key}-600)`,
              '--avp-nav-wash': `var(--avp-bench-${key}-050)`,
            } as Record<string, string>)
      }
    >
      {icon != null && (
        <span className="avp-nav__icon" aria-hidden="true">
          {icon}
        </span>
      )}
      <span className="avp-nav__text">
        <span className="avp-nav__label">{label}</span>
        {note != null && <span className="avp-nav__note">{note}</span>}
      </span>
    </a>
  );
}

export interface AppShellProps {
  /** The agency's name — whose workspace this is. */
  brand: ReactNode;
  /** `NavItem`s. */
  nav: ReactNode;
  /** Rendered at the foot of the sidebar — sign out, seat usage. */
  footer?: ReactNode;
  children: ReactNode;
  /**
   * Let the content area run to the full app width rather than the report
   * measure. The report is a document and wants its measure; a table does not.
   */
  wide?: boolean;
  className?: string;
}

/**
 * AppShell — the persistent frame around every SIGNED-IN screen.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * --------------------------------
 * It does not restyle what it wraps. Epics 7, 9.8, 9.9 and 9.10 settled the
 * report, the dashboard and the landing page, and this frames them rather than
 * revisiting them — the content area is a plain column with a width choice and
 * nothing else.
 *
 * **The share page never gets this shell.** `/share/{token}` is read by a
 * stranger with no session and no account; wrapping it in someone else's
 * workspace navigation would be both confusing and a small information leak
 * about the agency's internals.
 *
 * The sidebar is a real `<nav>` with an accessible name, and collapses to a
 * horizontal strip on narrow viewports rather than becoming a hamburger — at
 * four destinations a drawer is machinery nobody needs.
 */
export function AppShell({
  brand,
  nav,
  footer,
  children,
  wide = false,
  className,
}: AppShellProps): JSX.Element {
  return (
    <div className={cn('avp-shell', className)}>
      <nav className="avp-shell__sidebar" aria-label="Main">
        <div className="avp-shell__brand">{brand}</div>
        <div className="avp-nav">{nav}</div>
        {footer != null && <div className="avp-shell__footer">{footer}</div>}
      </nav>
      <main className={cn('avp-shell__content', wide && 'avp-shell__content--wide')}>
        {children}
      </main>
    </div>
  );
}

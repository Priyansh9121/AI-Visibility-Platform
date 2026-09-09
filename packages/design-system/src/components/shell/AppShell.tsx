import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';
import { benchAccent, visibilityColor } from '../../tokens/color.js';
import { visibilityBandLabel } from '../Badge.js';

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
  /**
   * Marks a destination that leaves the current space — Epic 13.
   *
   * The client's Report is the case: a real path into `/scans/{id}/report`,
   * the document that already exists, rendered with a trailing indicator so
   * leaving is not a surprise. Same prop, same glyph, as `LocalNavItem`.
   */
  external?: boolean;
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
export function NavItem({
  href,
  label,
  icon,
  current,
  note,
  accent,
  external,
}: NavItemProps): JSX.Element {
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
      {external === true && (
        <span className="avp-nav__out" aria-hidden="true">
          ↗
        </span>
      )}
    </a>
  );
}

/* ====================================================================== *
 * THE SIDEBAR AS A MAP — Epic 13.
 *
 * Until this epic the sidebar was four agency-wide links and nothing else, and
 * everything about ONE client lived in a strip inside the content area
 * (`LocalNav`). Epic 13 reverses that on the founder's decision — the build
 * log entry "Epic 13 — the sidebar becomes the map" records what 9.20 argued
 * and why it changed — and the sidebar now has two MODES. The primitives below
 * are what a mode is built from; which items appear, and which mode, is the
 * app's business (`WorkspaceShell`).
 *
 * The rule 9.20 was protecting still holds and is the reason these are modes
 * rather than a mixed list: every item in the sidebar is about one thing, and
 * the head of the sidebar (`NavHead`) says which thing.
 * ====================================================================== */

export interface NavGroupProps {
  /** The cluster's name. Short — a wayfinding aid, not a destination. */
  label: string;
  /** `NavSlot`s. */
  children: ReactNode;
  className?: string;
}

/**
 * A labelled cluster of sidebar destinations.
 *
 * The same structure `LocalNavGroup` gave the strip in Epic B.1, carried
 * into the sidebar: a `<ul>` with an accessible name, so a screen reader
 * reports "Measurement, list, 6 items" rather than meeting ten undifferentiated
 * links. An accent inside a group is cluster-relative for the reason
 * `clientNav.ts` gives — the bench layer holds seven hues and a cluster never
 * approaches it.
 */
export function NavGroup({ label, children, className }: NavGroupProps): JSX.Element {
  return (
    <div className={cn('avp-nav__group', className)}>
      <span className="avp-nav__grouplabel" aria-hidden="true">
        {label}
      </span>
      <ul className="avp-nav__grouplist" aria-label={label}>
        {children}
      </ul>
    </div>
  );
}

/**
 * One list item inside a `NavGroup`, or inside a `NavPanel`.
 *
 * A plain flex `<li>`, never `display: contents` — the same refusal
 * `LocalNavItem` records: several browsers strip a `contents` list item from
 * the accessibility tree, taking the list semantics with it.
 */
export function NavSlot({ children, className }: { children: ReactNode; className?: string }): JSX.Element {
  return <li className={cn('avp-nav__slot', className)}>{children}</li>;
}

export interface NavDisclosureProps {
  label: string;
  icon?: ReactNode;
  accent?: number;
  /** Whether the panel this controls is open. Controlled — the app owns it. */
  expanded: boolean;
  onToggle: () => void;
  /** The `id` of the `NavPanel` this button opens. */
  controls: string;
  /**
   * The screen currently shown lives inside the panel — the Clients list
   * itself, say. Marks the row the way `NavItem.current` does, so an operator
   * standing on `/clients` sees the disclosure lit as well as its child.
   */
  current?: boolean;
}

/**
 * A sidebar row that OPENS rather than navigates.
 *
 * A `<button>`, deliberately, where `NavItem` is deliberately an `<a>`: this
 * row's job is to reveal the client list beneath it, and a link that also
 * toggled something would be two controls wearing one label. The destination
 * the label names — the clients screen — is the first row inside the panel it
 * opens, so nothing 9.13's rule protects is lost: every place is still one
 * click away, and no item is dead.
 *
 * `aria-expanded` and `aria-controls` carry the state; the chevron turns, but
 * the chevron is decoration and the attribute is the truth.
 */
export function NavDisclosure({
  label,
  icon,
  accent,
  expanded,
  onToggle,
  controls,
  current,
}: NavDisclosureProps): JSX.Element {
  const key = accent == null ? null : benchAccent(accent).key;
  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-controls={controls}
      onClick={onToggle}
      className={cn(
        'avp-nav__item',
        'avp-nav__item--disclosure',
        current && 'is-current',
        key != null && 'has-accent',
      )}
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
      </span>
      {/* Drawn here rather than imported: ip-safety.md #4 allows Lucide or
          custom-drawn, and the design system does not depend on Lucide. */}
      <svg
        className="avp-nav__chevron"
        viewBox="0 0 16 16"
        width="14"
        height="14"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

export interface NavPanelProps {
  /** Matches the disclosure's `controls`. */
  id: string;
  open: boolean;
  /** `NavSlot`s wrapping `NavSubItem`s. */
  children: ReactNode;
  /** Accessible name for the list inside. */
  label: string;
  className?: string;
}

/**
 * The list a `NavDisclosure` opens.
 *
 * Collapses by animating `grid-template-rows` from `1fr` to `0fr` — the one
 * accordion technique that needs no measured height and no JavaScript, and
 * the case the motion rules tolerate a non-transform property for, because a
 * collapse has no transform equivalent. While closed the contents are `inert`
 * and hidden from assistive tech, so a closed list cannot be tabbed through.
 */
export function NavPanel({ id, open, children, label, className }: NavPanelProps): JSX.Element {
  return (
    <div
      id={id}
      className={cn('avp-nav__panel', open && 'is-open', className)}
      aria-hidden={!open}
      // React 19 forwards `inert` as a boolean attribute.
      inert={!open}
    >
      <div className="avp-nav__panelinner">
        <ul className="avp-nav__sublist" aria-label={label}>
          {children}
        </ul>
      </div>
    </div>
  );
}

export interface NavSubItemProps {
  href: string;
  label: string;
  current?: boolean;
  /**
   * The row's score, if it has one — Epic 13's client list.
   *
   * Three states, and the difference between them is the product's whole
   * discipline about absent numbers: a number lights the dot on the ramp and
   * prints the figure; `null` means the client has been scored as having no
   * reading, which is a hollow dot and no figure; `undefined` means this list
   * does not know, which is no dot at all rather than a grey one that could be
   * read as "low".
   */
  score?: number | null;
  /** A short trailing note when there is no score to print. */
  note?: ReactNode;
}

/**
 * One row inside a `NavPanel` — a client in the Clients list.
 *
 * An `<a>`, for the reason every sidebar destination is one. The dot is the
 * visibility ramp doing what it does everywhere else in the product: hue for
 * how visible, never for anything else, and never the only carrier — the
 * figure beside it says the number.
 */
export function NavSubItem({ href, label, current, score, note }: NavSubItemProps): JSX.Element {
  const known = score !== undefined;
  const scored = typeof score === 'number';
  return (
    <a
      href={href}
      aria-current={current ? 'page' : undefined}
      className={cn('avp-nav__subitem', current && 'is-current')}
    >
      {known && (
        <span
          className={cn('avp-nav__dot', !scored && 'avp-nav__dot--empty')}
          aria-hidden="true"
          style={scored ? { background: visibilityColor(score as number) } : undefined}
        />
      )}
      <span className="avp-nav__sublabel">{label}</span>
      {scored ? (
        <span className="avp-nav__figure" aria-label={`score ${Math.round(score as number)} out of 100`}>
          {Math.round(score as number)}
        </span>
      ) : note != null ? (
        <span className="avp-nav__figure avp-nav__figure--note">{note}</span>
      ) : null}
    </a>
  );
}

export interface NavHeadProps {
  /** A link back to the level above — "All clients". */
  back?: { href: string; label: string };
  /** Whose space this is. */
  title: ReactNode;
  /** What it is, under the name — the domain. */
  subtitle?: ReactNode;
  /** A `NavScore`, typically. */
  children?: ReactNode;
  className?: string;
}

/**
 * The head of the sidebar in CLIENT mode — the thing that makes the mode
 * unambiguous.
 *
 * 9.20's rule was that an operator must always be able to tell whose space
 * they are standing in. In the strip that was a heading in the content; in
 * the sidebar it is this block, which replaces the agency brand for as long as
 * a client is selected and carries the way back at the top.
 */
export function NavHead({ back, title, subtitle, children, className }: NavHeadProps): JSX.Element {
  return (
    <div className={cn('avp-nav__head', className)}>
      {back != null && (
        <a href={back.href} className="avp-nav__back">
          <span aria-hidden="true">←</span> {back.label}
        </a>
      )}
      <p className="avp-nav__title">{title}</p>
      {subtitle != null && <p className="avp-nav__subtitle">{subtitle}</p>}
      {children}
    </div>
  );
}

export interface NavScoreProps {
  /** The latest composite. `null` when the client has never been scored. */
  score: number | null;
  className?: string;
}

/**
 * The client's latest score, at sidebar scale.
 *
 * The Ledger's identity in three words: a numeral in the ramp colour, the
 * denominator, and the band in words — so the hue is never the only carrier,
 * which is `ScoreMeter`'s rule at list scale and `VisibilityBadge`'s at chip
 * scale. Null renders as the absence it is, never as a zero.
 */
export function NavScore({ score, className }: NavScoreProps): JSX.Element {
  if (score === null || Number.isNaN(score)) {
    return (
      <p className={cn('avp-navscore', 'avp-navscore--empty', className)}>
        <span className="avp-navscore__band">Not scored yet</span>
      </p>
    );
  }
  const rounded = Math.round(Math.max(0, Math.min(100, score)));
  return (
    <p className={cn('avp-navscore', className)}>
      <span className="avp-navscore__numeral" style={{ color: visibilityColor(rounded) }}>
        {rounded}
        <span className="avp-navscore__denominator">/100</span>
      </span>
      <span className="avp-navscore__band">{visibilityBandLabel(rounded)}</span>
    </p>
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

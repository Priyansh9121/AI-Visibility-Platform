import type { JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';

export interface MetaChipProps {
  /**
   * A Lucide glyph (MIT) or nothing — the licensed-assets rule. Drawn at 13px
   * by the stylesheet whatever size the element was given, and hidden from
   * assistive tech: the text beside it is the fact, the glyph is only its
   * shape.
   */
  icon?: ReactNode;
  /** Set for a domain or an identifier, so the one machine-readable fact reads as one. */
  mono?: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * MetaChip — one FACT, given a shape. Epic 16.1.
 *
 * WHAT IT IS FOR
 * --------------
 * Wherever several short facts sit in one line at one weight — a domain, a
 * count, a date — they read as a log line, and the eye slides off. Giving
 * each its own shape turns the line into credentials. The report's byline was
 * the first place this was needed (`ReportMetaItem`, Epic 16); a Working
 * screen's hero meta and page-head aside are the same pattern, so this is the
 * one primitive both use. The CSS is token-only, so the same rule set draws
 * the paper chip inside `.avp-report` and the Working chip everywhere else,
 * and follows a theme switch without a second declaration.
 *
 * WHAT IT IS NOT — the rule that keeps three pills apart
 * -------------------------------------------------------
 * `Badge` is SYSTEM STATE: uppercase, tracked, tinted by a semantic tone.
 * `VisibilityBadge` is a SCORE: filled from the ramp. This is a FACT:
 * sentence case, a glyph, the seated tone and a hairline, and never a colour.
 * There is deliberately no `tone` and no `accent` prop — a chip that could be
 * tinted is a chip that could be misread as a state or a score, and the
 * buffer design-direction.md §1 keeps between the categorical hues and the
 * meaning-bearing ones is worth nothing if a neutral element can opt into
 * either.
 */
export function MetaChip({ icon, mono = false, children, className }: MetaChipProps): JSX.Element {
  return (
    <span className={cn('avp-chip', mono && 'avp-chip--mono', className)}>
      {icon != null && (
        <span className="avp-chip__icon" aria-hidden="true">
          {icon}
        </span>
      )}
      {children}
    </span>
  );
}

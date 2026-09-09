import type { JSX, ReactNode } from 'react';
import { cn } from '../lib/cn.js';

export interface PageHeadProps {
  /** A short structural label above the title — "Agency", "Clients". Optional. */
  eyebrow?: string;
  /** The screen's name, or whose screen it is. */
  title: ReactNode;
  /** Controls or short facts, to the right of the title. */
  aside?: ReactNode;
  className?: string;
}

/**
 * PageHead — the title block of a Working screen. Epic 14.
 *
 * One place for the display face at screen scale, so every screen's first
 * line is set the same way: the same size, the same weight, the same
 * tracking. Before this, three screens each carried their own header markup
 * with the same five utility classes, and the redesign would have meant
 * changing all three in step. Now it means changing one rule.
 *
 * Deliberately no border under it. The old headers drew a hairline across
 * the full column; on the dark ground the cards below carry the structure
 * and a rule above them reads as a second frame.
 */
export function PageHead({ eyebrow, title, aside, className }: PageHeadProps): JSX.Element {
  return (
    <header className={cn('avp-pagehead', className)}>
      <div>
        {eyebrow != null && <p className="avp-pagehead__eyebrow">{eyebrow}</p>}
        <h1 className="avp-pagehead__title">{title}</h1>
      </div>
      {aside != null && <div className="avp-pagehead__aside">{aside}</div>}
    </header>
  );
}

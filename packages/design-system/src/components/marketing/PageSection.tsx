import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';

export interface PageSectionProps {
  /** Small structural label above the heading. Optional. */
  eyebrow?: string;
  /** The section's claim. Editorial face, like the report's beat headings. */
  heading?: ReactNode;
  /** One or two sentences directly under the heading. */
  lead?: ReactNode;
  children?: ReactNode;
  /** Larger type and more space — for the first section on a page. */
  tone?: 'default' | 'lead';
  className?: string;
}

/**
 * PageSection — a section of a non-report page.
 *
 * **Why this is not `Beat`.** `Beat` is bound to `BeatId` and numbers itself
 * from `BEAT_SEQUENCE`: it exists to enforce the report's fixed narrative order
 * (score -> gap -> proof -> fix -> pitch, mandated by ip-safety.md #3). A public
 * page is not that document and must not borrow its numbering, or the sequence
 * stops meaning anything on the screen where it is load-bearing.
 *
 * **Why it is in the design system rather than in apps/web.** ip-safety.md #2:
 * a public marketing page is a customer-facing screen like any other, and a
 * hero styled locally would be exactly the ad hoc styling that rule prohibits.
 * Epic 0's build-before-screens discipline applies to marketing layout too.
 *
 * It carries no imagery, no decorative flourish and no fixed column count —
 * only the editorial voice the report already established, so the two read as
 * one product.
 */
export function PageSection({
  eyebrow,
  heading,
  lead,
  children,
  tone = 'default',
  className,
}: PageSectionProps): JSX.Element {
  return (
    <section className={cn('avp-section', tone === 'lead' && 'avp-section--lead', className)}>
      {eyebrow != null && <p className="avp-section__eyebrow">{eyebrow}</p>}
      {heading != null && <h2 className="avp-section__heading">{heading}</h2>}
      {lead != null && <p className="avp-section__lead">{lead}</p>}
      {children != null && <div className="avp-section__body">{children}</div>}
    </section>
  );
}

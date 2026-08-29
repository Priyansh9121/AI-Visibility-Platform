import type { JSX, ReactNode } from 'react';
import { cn } from '../../lib/cn.js';
import { Reveal, RevealGroup } from '../Reveal.js';

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
  /**
   * Reveal the eyebrow, heading, lead and body in sequence — Epic 9.16.
   *
   * **Defaults to false, and every existing caller keeps today's markup
   * exactly.** That matters because this component is not only a marketing
   * primitive: `SettingsView` builds five sections out of it and `WelcomeView`
   * one more. Settings is a dense, functional screen that this brief
   * deliberately leaves alone, so a default of `true` would have quietly
   * animated a screen nobody asked to animate.
   *
   * Staggering the PARTS is offered here rather than left to the call site
   * because they are props, not children — a caller cannot wrap `eyebrow` in
   * anything. A section that should reveal as one unit needs nothing from this
   * component: wrap the whole `<PageSection>` in a `<Reveal>`.
   */
  stagger?: boolean;
  /** Turn motion off entirely — tests, static renders, the print path. */
  animate?: boolean;
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
  stagger = false,
  animate = true,
  className,
}: PageSectionProps): JSX.Element {
  const parts = (
    <>
      {eyebrow != null && <p className="avp-section__eyebrow">{eyebrow}</p>}
      {heading != null && <h2 className="avp-section__heading">{heading}</h2>}
      {lead != null && <p className="avp-section__lead">{lead}</p>}
      {children != null && <div className="avp-section__body">{children}</div>}
    </>
  );

  // Not staggered: the Epic 0 markup, untouched. No wrappers, no extra boxes,
  // no `avp-reveal` anywhere — which is what keeps Settings identical.
  if (!stagger) {
    return (
      <section className={cn('avp-section', tone === 'lead' && 'avp-section--lead', className)}>
        {parts}
      </section>
    );
  }

  // Staggered: the same four parts, each in its own wrapper, revealing together
  // one step apart. Indices are written out rather than counted, because the
  // parts are optional — a section with no eyebrow must not leave a gap where
  // its delay would have been.
  let index = 0;
  const next = (): number => index++;

  return (
    <RevealGroup
      as="section"
      animate={animate}
      className={cn('avp-section', tone === 'lead' && 'avp-section--lead', className)}
    >
      {eyebrow != null && (
        <Reveal as="p" index={next()} animate={animate} className="avp-section__eyebrow">
          {eyebrow}
        </Reveal>
      )}
      {heading != null && (
        <h2 className="avp-section__heading">
          <Reveal as="span" index={next()} animate={animate}>
            {heading}
          </Reveal>
        </h2>
      )}
      {lead != null && (
        <Reveal as="p" index={next()} animate={animate} className="avp-section__lead">
          {lead}
        </Reveal>
      )}
      {children != null && (
        <Reveal index={next()} animate={animate} className="avp-section__body">
          {children}
        </Reveal>
      )}
    </RevealGroup>
  );
}

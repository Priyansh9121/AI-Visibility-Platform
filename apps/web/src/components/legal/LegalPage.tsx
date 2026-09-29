/**
 * The frame the public privacy and terms pages share — 2026-09-29.
 *
 * Why these pages exist at all: Google keeps an OAuth consent screen in
 * Testing, open only to listed test users, until it has a public privacy
 * policy on an authorised domain. The free scan needs a Google sign-in, so
 * without these pages no real prospect could get one.
 *
 * Built from the landing page's own parts, as the brief asked: the same
 * sticky header (`MarketingHeader`), the same `PageSection` voice and page
 * width, and the footer that now links here from every public page. The
 * document's title is the lead section's heading, rendered as the page's one
 * `h1` (`headingAs`). Everything is static: no session call, no effect, so the
 * text is in the server-rendered HTML that Google's review reads.
 *
 * DRAFTS FOR THE FOUNDER'S REVIEW — see `lib/legal/operator.ts`.
 */

import type { JSX, ReactNode } from 'react';
import { PageSection } from '@avp/design-system';
import { MarketingFooter } from '@/components/marketing/MarketingFooter';
import { MarketingHeader } from '@/components/marketing/MarketingHeader';
import { formatDay } from '@/lib/dates';
import { LEGAL_UPDATED } from '@/lib/legal/operator';

export interface LegalPageProps {
  eyebrow: string;
  title: string;
  lead: ReactNode;
  children: ReactNode;
}

export function LegalPage({ eyebrow, title, lead, children }: LegalPageProps): JSX.Element {
  return (
    <div className="flex flex-col">
      <MarketingHeader />

      {/* A step on the design system's own scale: it replaces Tailwind's, and a
          step it lacks (16 was written first) compiles to nothing — ten
          sections with no space between them. The design system's
          `tokens/reportIsolation.test.ts` guards exactly this. */}
      <main className="mx-auto flex w-full max-w-page flex-col gap-14 px-6 py-18">
        <PageSection tone="lead" headingAs="h1" eyebrow={eyebrow} heading={title} lead={lead}>
          <p className="text-ui-sm text-text-tertiary">
            Last updated {formatDay(LEGAL_UPDATED)}.
          </p>
        </PageSection>
        {children}
      </main>

      <MarketingFooter />
    </div>
  );
}

/** A paragraph at the landing page's reading measure. */
export function Para({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="max-w-measure text-ui-base leading-prose text-text-secondary">{children}</p>
  );
}

/**
 * One entry in a list, with an optional lead-in term. A block `li`, not a
 * flex one: a flex item drops the space between the term and the sentence
 * (the 2026-09-22 share-legend defect), and this must read as prose.
 */
export function Point({ term, children }: { term?: string; children: ReactNode }): JSX.Element {
  return (
    <li className="max-w-measure text-ui-base leading-prose text-text-secondary">
      {term != null && <span className="font-medium text-text-primary">{term}</span>}
      {term != null && ' '}
      {children}
    </li>
  );
}

export function Points({ children }: { children: ReactNode }): JSX.Element {
  return <ul className="flex flex-col gap-3">{children}</ul>;
}

/** A link inside running text — the underline treatment `ClientsView` uses. */
export function TextLink({ href, children }: { href: string; children: ReactNode }): JSX.Element {
  return (
    <a
      href={href}
      className="text-text-primary underline decoration-line-strong underline-offset-2 transition-colors duration-hover ease-out hover:text-beacon-600 hover:decoration-beacon-400"
    >
      {children}
    </a>
  );
}

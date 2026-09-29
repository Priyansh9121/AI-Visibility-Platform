'use client';

/**
 * The public pages' persistent header — Epic 9.15, shared since 2026-09-29.
 *
 * 9.10 shipped the landing page with no header at all: the only way to reach
 * the product was the hero's own button, and the only way to reach a SIGN-IN
 * form was to click a sign-up button and then read your way out of it. A
 * returning customer had no door.
 *
 * Sticky rather than merely present, because the landing page is long by
 * design — the honest-limitations section and the pricing card are both below
 * the fold, and a header that scrolls away takes "Log in" with it exactly when
 * a reader has decided.
 *
 * On the landing page the nav is two in-page anchors, plain `<a href="#…">`.
 * No router, no scroll library, no motion: the browser already does this
 * correctly, and the animation question is open from a previous epic and is
 * not being answered here by accident.
 *
 * ON ANY OTHER PUBLIC PAGE (the privacy and terms pages, 2026-09-29) there is
 * no section nav — those sections are not on that page — and the wordmark
 * links home. "Log in" and "Get started free" still open their forms, through
 * `/?view=…` (`lib/auth/frontDoor.ts`), because on `/` the two forms are view
 * states rather than routes.
 */

import type { JSX } from 'react';
import { Button } from '@avp/design-system';
import { frontDoorHref } from '@/lib/auth/frontDoor';

export interface MarketingHeaderProps {
  /**
   * The landing page's own section ids, for the in-page nav. Only the landing
   * page passes them; everywhere else the header links home instead.
   */
  sections?: { productId: string; pricingId: string } | undefined;
  /** The sign-up surface. Omitted away from `/`, where it navigates there. */
  onGetStarted?: (() => void) | undefined;
  /** The sign-in surface. Omitted away from `/`, where it navigates there. */
  onLogIn?: (() => void) | undefined;
  /** Test seam: how to go to a URL. Defaults to navigating. */
  navigate?: ((href: string) => void) | undefined;
}

export function MarketingHeader({
  sections,
  onGetStarted,
  onLogIn,
  navigate,
}: MarketingHeaderProps): JSX.Element {
  const go = navigate ?? ((href: string) => window.location.assign(href));
  const logIn = onLogIn ?? (() => go(frontDoorHref('sign-in')));
  const getStarted = onGetStarted ?? (() => go(frontDoorHref('sign-up')));

  return (
    <header className="sticky top-0 z-10 border-b border-line-hairline bg-surface-ground">
      <div className="mx-auto flex w-full max-w-page flex-wrap items-center gap-4 px-6 py-4">
        {/* The wordmark, not a logo: the licensed-assets rule permits custom-drawn marks
            and licensed fonts only, and no mark has been drawn. Type is the
            honest option, and it is the name `layout.tsx` already ships as the
            document title rather than a second name invented here. */}
        <a
          href={sections ? '#top' : '/'}
          className="font-display text-ui-lg tracking-display text-text-primary"
        >
          AI Visibility Platform
        </a>

        {sections && (
          <nav aria-label="Sections of this page" className="flex items-center gap-4">
            <a
              href={`#${sections.productId}`}
              className="text-ui-sm text-text-secondary hover:underline"
            >
              Product
            </a>
            <a
              href={`#${sections.pricingId}`}
              className="text-ui-sm text-text-secondary hover:underline"
            >
              Pricing
            </a>
          </nav>
        )}

        <div className="ml-auto flex items-center gap-3">
          {/*
            "Log in" is a button element rather than an anchor because sign-in
            is a view state on `/`, not a URL — see `app/page.tsx`. It is
            styled ghost so that the page has exactly one primary action, which
            is the one for people who do not have an account yet.
          */}
          <Button variant="ghost" size="sm" onClick={logIn}>
            Log in
          </Button>
          <Button variant="primary" size="sm" onClick={getStarted}>
            Get started free
          </Button>
        </div>
      </div>
    </header>
  );
}

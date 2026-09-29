/**
 * The public pages' footer — 2026-09-29.
 *
 * The landing page had none. It has one now because Google will not publish
 * an OAuth consent screen without a privacy policy on the app's own domain —
 * and Google sign-in is what the free scan needs — and a policy nobody can
 * reach from the home page is not really published. Two links, on every
 * public page: the privacy policy and the terms. A third since 2026-09-29: the
 * crawler page, which the crawler's user agent points site owners to.
 *
 * Plain anchors: all three are real routes, and nothing here needs a handler, so
 * the footer renders the same on a server page and inside the landing view.
 */

import type { JSX } from 'react';

export const PRIVACY_PATH = '/privacy';
export const TERMS_PATH = '/terms';
export const CRAWLER_PATH = '/crawler';

export function MarketingFooter(): JSX.Element {
  return (
    <footer className="border-t border-line-hairline">
      <div className="mx-auto flex w-full max-w-page flex-wrap items-center gap-4 px-6 py-8">
        <p className="text-ui-sm text-text-tertiary">AI Visibility Platform</p>
        <nav aria-label="Policies" className="ml-auto flex items-center gap-4">
          <a href={PRIVACY_PATH} className="text-ui-sm text-text-secondary hover:underline">
            Privacy
          </a>
          <a href={TERMS_PATH} className="text-ui-sm text-text-secondary hover:underline">
            Terms
          </a>
          <a href={CRAWLER_PATH} className="text-ui-sm text-text-secondary hover:underline">
            Crawler
          </a>
        </nav>
      </div>
    </footer>
  );
}

import type { Metadata } from 'next';
import type { ReactNode } from 'react';
// Imported from the tokens subpath, not the package barrel: the barrel pulls
// in client components, which would force this Server Component to bundle them.
import { GOOGLE_FONTS_HREF } from '@avp/design-system/tokens';
import { ThemeProvider } from '@/components/shell/ThemeProvider';
import './globals.css';

export const metadata: Metadata = {
  title: 'AI Visibility Platform',
  description: 'See how a brand appears when buyers ask AI assistants for a recommendation.',
};

/**
 * Proof, before the first paint, that scripting actually works here — Epic 9.16a.
 *
 * `.avp-reveal` is VISIBLE by default and only hides under `.avp-motion-ready`.
 * This one statement is what adds that class, and everything about how it is
 * delivered matters:
 *
 *   - **Inline**, so there is no network request that could fail or arrive late.
 *   - **Synchronous and in <head>**, so it runs before the body is painted.
 *     Content is therefore hidden from the very first frame when JS works, and
 *     never hidden at all when it does not — no flash in either direction.
 *   - **Not a React effect**, which is the whole point. An effect runs after
 *     hydration, and hydration is exactly the window this exists to cover.
 *
 * Why it is needed at all: `/invite/{token}` and `/reset-password/{token}` are
 * server-rendered, so their cards are in the HTML before any bundle runs. With
 * the hidden state as the CSS default those two screens were blank until
 * hydration — verified with `curl`, which returned the card markup carrying
 * `avp-reveal` and no `--revealed`. The four screens Epic 9.16 did first never
 * showed this because they are never server-rendered; the precedent was only
 * accidentally safe.
 *
 * If this script is ever removed, nothing breaks visibly — the product simply
 * stops animating. That is the correct failure direction and it is why the
 * default was inverted rather than patched.
 */
const MOTION_READY = "document.documentElement.classList.add('avp-motion-ready')";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The class above is added to <html> outside React's knowledge, which is
    // precisely what makes it beat hydration. `suppressHydrationWarning` tells
    // React that a difference on this element is expected rather than a bug.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: MOTION_READY }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* Fraunces + IBM Plex Sans/Mono, all OFL-1.1 (docs/ip-safety.md #4).
            The href is exported by the design system so fonts are declared in
            exactly one place. */}
        <link href={GOOGLE_FONTS_HREF} rel="stylesheet" />
      </head>
      {/*
        The theme — Epic 15. `ThemeProvider` writes `data-theme` on <html>
        from a synchronous inline script before hydration, the same way the
        MOTION_READY script above beats it, so the first paint is already in
        the chosen theme. Light is the default; the report ignores the
        attribute entirely (see tokens.css, `.avp-report`).
      */}
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}

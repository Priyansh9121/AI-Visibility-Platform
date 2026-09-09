'use client';

/**
 * The theme provider — Epic 15.
 *
 * `next-themes` (MIT), chosen per the `pick-ui-library` guidance for theme
 * switching with no flash on load, rather than hand-rolling the same thing.
 * It writes `data-theme` on `<html>` — the attribute every token block in
 * `tokens.css` keys off — from a synchronous inline script it injects before
 * hydration, which is the same "beat hydration with an inline script"
 * mechanism `layout.tsx`'s `MOTION_READY` already relies on.
 *
 * THE DEFAULT IS LIGHT, NOT SYSTEM. The founder's decision is that light is
 * the product's default; "Match system" is one of the three choices in
 * Settings, not the starting point. So `defaultTheme` is `light` and
 * `enableSystem` only makes the third option available.
 *
 * THE REPORT IS OUTSIDE THIS. `.avp-report` carries the paper scope on its
 * own root class in `tokens.css` and no longer shares a selector with any
 * theme attribute, so whatever this writes to `<html>` cannot reach the
 * document. `ReportView.test.tsx` and `reportIsolation.test.ts` hold that.
 */

import type { JSX, ReactNode } from 'react';
import { ThemeProvider as NextThemesProvider } from 'next-themes';

/** The three choices Settings offers. `system` resolves to one of the other two. */
export type ThemeChoice = 'light' | 'dark' | 'system';
export const THEME_CHOICES: readonly ThemeChoice[] = ['light', 'dark', 'system'];
export const THEME_STORAGE_KEY = 'avp.theme';

export function ThemeProvider({ children }: { children: ReactNode }): JSX.Element {
  return (
    <NextThemesProvider
      attribute="data-theme"
      defaultTheme="light"
      enableSystem
      storageKey={THEME_STORAGE_KEY}
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}

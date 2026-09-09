'use client';

/**
 * The appearance control in Settings — Epic 15.
 *
 * Three real choices, stated as words: Light (the default), Dark (Epic 14's
 * identity, kept whole as the opt-in), and Match system. Buttons with
 * `aria-pressed` inside a labelled group rather than a sun/moon switch — a
 * three-way choice is not a toggle, and a picture of the sun does not say
 * "match system".
 *
 * The provider resolves the choice before hydration, but this control reads
 * it AFTER mounting on purpose: on the server there is no stored choice, so
 * rendering the pressed state from `useTheme()` during hydration would
 * disagree with the markup the server sent. Until mounted, nothing is
 * pressed and nothing flashes.
 */

import { useEffect, useState, type JSX } from 'react';
import { useTheme } from 'next-themes';
import { Button } from '@avp/design-system';
import { THEME_CHOICES, type ThemeChoice } from '@/components/shell/ThemeProvider';

const LABEL: Record<ThemeChoice, string> = {
  light: 'Light',
  dark: 'Dark',
  system: 'Match system',
};

const NOTE: Record<ThemeChoice, string> = {
  light: 'The default. A light ground, white cards.',
  dark: 'Deep charcoal, lit accents. Easier on the eyes in a long session.',
  system: 'Follows the operating system, and changes when it does.',
};

export function ThemeControl(): JSX.Element {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const current = mounted ? (theme as ThemeChoice | undefined) ?? 'light' : null;

  return (
    <div className="flex flex-col gap-3">
      <div role="group" aria-label="Appearance" className="flex flex-wrap gap-2">
        {THEME_CHOICES.map((choice) => (
          <Button
            key={choice}
            size="sm"
            variant={current === choice ? 'primary' : 'secondary'}
            aria-pressed={current === choice}
            onClick={() => setTheme(choice)}
          >
            {LABEL[choice]}
          </Button>
        ))}
      </div>
      <p className="max-w-measure text-ui-sm text-text-secondary">
        {current === null ? NOTE.light : NOTE[current]}
      </p>
      <p className="max-w-measure text-ui-xs text-text-tertiary">
        The report is a document and keeps its own paper look whichever you choose.
      </p>
    </div>
  );
}

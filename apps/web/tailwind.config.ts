import type { Config } from 'tailwindcss';
import preset from '@avp/design-system/tailwind-preset';

/**
 * The preset REPLACES Tailwind's colour, spacing, font and shadow scales rather
 * than extending them — `bg-slate-500` does not compile here. That is how
 * docs/ip-safety.md #2 ("no ad hoc Tailwind defaults on customer-facing
 * screens") is enforced as a build error instead of a review comment.
 */
export default {
  presets: [preset as unknown as Config],
  content: [
    './src/**/*.{ts,tsx}',
    // The design system's own components carry the classes they need.
    '../../packages/design-system/src/**/*.{ts,tsx}',
  ],
} satisfies Config;

'use client';

/**
 * "Send feedback" — 2026-10-06.
 *
 * One anchor that opens an email to the address the API's `FEEDBACK_EMAIL` names, with the shape
 * of the page the user is on in the subject (`lib/feedback/subject`: never an id, a token or a
 * client's name). Drawn in two places only, the signed-in shell's footer and Settings, and only for
 * an address the page was told: with none, which is every deployment until the founder sets the
 * variable, it draws nothing at all, and the page is what it was before this existed.
 *
 * A real link, not a button that navigates: it works with a middle click, it can be copied, and a
 * screen reader says "link". On screen it says "Send feedback" and it is CALLED "Send feedback by
 * email": a visually hidden " by email" follows the words, so a screen-reader user is not surprised
 * that it opens a mail app, and the words on screen are the start of the name (WCAG 2.5.3, Label in
 * Name), so a voice user who says what they read is understood.
 *
 * It never wraps (`whitespace-nowrap`): at 60rem and below the sidebar is a narrow strip, and a link
 * that broke into "Send / feedback" there made the strip taller. The sidebar's look has a target 4 px
 * taller above and below its 17.55 px line (`py-1`), 25.5 px in all, so it is not a target of
 * 17.55 px that passes WCAG 2.5.8 only by the room round it. It is underlined in both looks, so it
 * is not told from the text round it by colour alone, and the design system's global
 * `:focus-visible` rule gives it its ring.
 *
 * It is `relative` for the hidden " by email": `.avp-visually-hidden` is absolutely positioned, and
 * with no positioned ancestor in the narrow strip (the sidebar is static there) it sat against the
 * viewport, outside the strip's own horizontal scroller, and gave the whole page a sideways scroll
 * at 360 px and narrower. Positioned against the link it moves with the strip.
 *
 * The pathname is the router's (`usePathname`), which is null outside it (a static render); the
 * subject then says only that it is feedback.
 */

import type { JSX } from 'react';
import { usePathname } from 'next/navigation';
import { feedbackMailto } from '@/lib/feedback/subject';

/** What the link says on screen, and the start of what it is called. */
export const FEEDBACK_LABEL = 'Send feedback';

/** The rest of its name, visually hidden: the link is called "Send feedback by email". */
export const FEEDBACK_HIDDEN_NAME = ' by email';

/**
 * `quiet` is the sidebar's: secondary text, small, underlined, as far left as the labels above it.
 * `inline` is for a sentence or a card in the page: the app's own text-link treatment, the one the
 * dashboard's "View report" and the scan-limit notice's "Open Settings" wear.
 */
const LOOKS = {
  quiet:
    'relative self-start whitespace-nowrap py-1 text-ui-sm text-text-secondary underline underline-offset-2 transition-colors duration-hover ease-out hover:text-text-primary',
  inline:
    'relative whitespace-nowrap font-medium text-beacon-600 underline underline-offset-2 transition-colors duration-hover ease-out hover:text-beacon-700',
} as const;

export function FeedbackLink({
  address,
  look = 'quiet',
}: {
  /** What the API sent as `feedbackEmail`: anything but one address draws nothing. */
  address: string | null | undefined;
  look?: keyof typeof LOOKS;
}): JSX.Element | null {
  // Before the early return: a hook is called on every render or on none.
  const pathname = usePathname();
  const href = feedbackMailto(address, pathname);
  if (href === null) return null;
  return (
    <a href={href} className={LOOKS[look]}>
      {FEEDBACK_LABEL}
      <span className="avp-visually-hidden">{FEEDBACK_HIDDEN_NAME}</span>
    </a>
  );
}

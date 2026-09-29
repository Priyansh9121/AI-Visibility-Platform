/**
 * The durations and limits the privacy page states — 2026-09-29.
 *
 * Every one is a copy of an API setting, because a policy that says "14 days"
 * while the code says something else is a false statement to the people it is
 * written for. `components/legal/legalPages.test.tsx` reads each value out of
 * the API's own source and fails if a copy here drifts from it.
 */
export const RETENTION = {
  /** `Settings.session_ttl_seconds` (`apps/api/src/avp_api/config.py`). */
  sessionDays: 14,
  /** `Settings.password_reset_ttl_seconds`. */
  resetLinkHours: 1,
  /** `Settings.invitation_ttl_seconds`. */
  invitationDays: 7,
  /** `Settings.google_sign_in_ttl_seconds` — the unfinished Google sign-up ticket. */
  googleSignUpMinutes: 10,
  /** `SHARE_TTL` (`services/share.py`). */
  shareLinkDays: 30,
  /** `add_client`'s `max_pages` (`services/intake.py`): the homepage and up to two more. */
  crawlPages: 3,
} as const;

/** "14 days", "1 hour" — a count with its unit. */
export function counted(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? '' : 's'}`;
}

/**
 * The sentence for a monthly rescan that started no scan — F2.
 *
 * The API records a code of its own (`skipReason`), never a message; this is the one place the
 * code becomes words. Every sentence says what is true of what the API does: a skipped month is
 * tried again on the first day of the next calendar month (UTC), which the API sends as
 * `retriesAt`, and the loop wakes every 10 minutes. A code this build does not know gets the general sentence, so a newer API never
 * shows a raw code.
 */

import { formatDay } from '@/lib/dates';

const WHY: Record<string, string> = {
  AGENCY_NOT_PAID: 'the agency does not have an active subscription',
  ALLOWANCE_USED: "the agency has already used this month's scans",
  USER_NOT_ADMIN:
    'the person who turned it on is no longer an owner or admin. Turning it off and on again as yourself takes it over',
  COULD_NOT_START: 'the scan could not be started',
};

export function skipSentence(reason: string, retriesAt: string | null | undefined): string {
  const why = WHY[reason] ?? 'the scan was not started';
  const again = retriesAt
    ? ` It will be tried again from ${formatDay(retriesAt)} (UTC), within about 10 minutes of then.`
    : '';
  const sooner = reason === 'AGENCY_NOT_PAID' ? ' Turn it off and on after subscribing to try sooner.' : '';
  return `The last monthly rescan did not run: ${why}.${again}${sooner}`;
}

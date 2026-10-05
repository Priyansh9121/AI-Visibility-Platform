/**
 * UTC date formatting for every screen — Epic 9.14.
 *
 * **Deliberately not `toLocaleDateString`.** `DashboardView` wrote that rule
 * down in Epic 9.3 and it holds everywhere: locale formatting varies with the
 * host's locale AND its timezone, so the same instant renders differently in CI
 * than in a browser, and an assertion over it is untrustworthy in both. The
 * seat panel's first draft reached for `toLocaleDateString('en-GB')` and its
 * test failed on ICU abbreviating September as "Sept" — the rule catching
 * exactly the class of bug it was written for.
 *
 * Moved out of `DashboardView` into a module of its own the moment a second
 * screen needed it, rather than copied. Two screens formatting dates two ways
 * is how a product starts looking assembled instead of designed, and the drift
 * is invisible until someone compares two tabs.
 *
 * Everything here is UTC. This product reports "scanned on" dates to clients in
 * other timezones, and a report that says a different date depending on who
 * opens it is a document nobody can quote.
 */

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

/** `04 Sep 2026, 09:12 UTC`. Em dash for a missing or unparseable value. */
export function formatStamp(iso: string | null | undefined): string {
  const at = parse(iso);
  if (at === null) return '—';
  const day = String(at.getUTCDate()).padStart(2, '0');
  const hh = String(at.getUTCHours()).padStart(2, '0');
  const mm = String(at.getUTCMinutes()).padStart(2, '0');
  return `${day} ${MONTHS[at.getUTCMonth()]} ${at.getUTCFullYear()}, ${hh}:${mm} UTC`;
}

/**
 * `4 Sep 2026`. Day only — for a deadline nobody acts on to the minute.
 *
 * Unpadded day, unlike `formatStamp`: that one lines up in a table column, and
 * this one sits mid-sentence where a leading zero reads as a typo.
 *
 * Returns the input unchanged when it cannot be parsed. An em dash would be
 * wrong here: `formatStamp` fills a cell whose value is genuinely optional,
 * whereas an invitation always HAS an expiry, so a dash would hide a real
 * defect behind something that looks like ordinary missing data.
 */
export function formatDay(iso: string): string {
  const at = parse(iso);
  if (at === null) return iso;
  return `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]} ${at.getUTCFullYear()}`;
}

/**
 * `4 Sep 2026, 09:12 UTC`. A day AND a time, UTC, for a deadline that sits mid-sentence and is
 * acted on to the hour: "Free trial until 3 Nov 2026, 09:05 UTC, then $150 a month".
 *
 * The day is unpadded, as in `formatDay` and for its reason: mid-sentence a leading zero reads as
 * a typo. The time is `formatStamp`'s (hour and minutes padded to two digits, then `UTC`). That
 * one pads its day too, because it lines up in a table column; this one is never in a column.
 *
 * Returns the input unchanged when it cannot be parsed, like `formatDay`: a deadline always HAS
 * a date, so a dash would hide a real defect behind something that looks like ordinary absence.
 */
export function formatDayTime(iso: string): string {
  const at = parse(iso);
  if (at === null) return iso;
  const hh = String(at.getUTCHours()).padStart(2, '0');
  const mm = String(at.getUTCMinutes()).padStart(2, '0');
  return `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]} ${at.getUTCFullYear()}, ${hh}:${mm} UTC`;
}

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null : at;
}

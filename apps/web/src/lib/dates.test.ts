/**
 * UTC date formatting — Epic 9.14.
 *
 * These exist because the seat panel's first draft used
 * `toLocaleDateString('en-GB')` and its test failed on ICU rendering September
 * as "Sept". `DashboardView` had already written the rule down in Epic 9.3 —
 * locale formatting varies with host locale AND timezone, so CI and a browser
 * disagree — and this module is that rule made reusable. The assertions below
 * are what "does not depend on the host" means concretely.
 */

import { describe, it, expect } from 'vitest';
import { formatDay, formatStamp } from './dates';

describe('formatStamp', () => {
  it('renders UTC with a padded day, for a column that lines up', () => {
    expect(formatStamp('2026-09-04T09:12:00Z')).toBe('04 Sep 2026, 09:12 UTC');
  });

  it('is unmoved by an offset in the input', () => {
    // Same instant, written three ways. A locale formatter would answer with
    // the host's timezone; this answers with the instant's.
    expect(formatStamp('2026-09-04T09:12:00Z')).toBe(
      formatStamp('2026-09-04T11:12:00+02:00'),
    );
    expect(formatStamp('2026-09-04T09:12:00Z')).toBe(
      formatStamp('2026-09-04T04:12:00-05:00'),
    );
  });

  it('crosses a date boundary by UTC, not by the host', () => {
    // 23:30 UTC is already the 5th in Sydney and still the 4th in New York.
    // Both must render as the 4th.
    expect(formatStamp('2026-09-04T23:30:00Z')).toContain('04 Sep 2026');
  });

  it('renders an em dash for nothing, rather than Invalid Date', () => {
    expect(formatStamp(null)).toBe('—');
    expect(formatStamp(undefined)).toBe('—');
    expect(formatStamp('')).toBe('—');
    expect(formatStamp('not-a-date')).toBe('—');
  });
});

describe('formatDay', () => {
  it('renders the day unpadded, for mid-sentence use', () => {
    expect(formatDay('2026-09-04T09:12:00Z')).toBe('4 Sep 2026');
    expect(formatDay('2026-09-14T09:12:00Z')).toBe('14 Sep 2026');
  });

  it('uses our own month table, not the host locale abbreviation', () => {
    // The bug this module was extracted for: en-GB ICU says "Sept".
    expect(formatDay('2026-09-04T00:00:00Z')).toContain('Sep');
    expect(formatDay('2026-09-04T00:00:00Z')).not.toContain('Sept');
  });

  it('covers every month with a three-letter abbreviation', () => {
    const months = Array.from({ length: 12 }, (_, i) =>
      formatDay(`2026-${String(i + 1).padStart(2, '0')}-15T00:00:00Z`).split(' ')[1],
    );
    expect(months).toEqual([
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ]);
    expect(months.every((m) => m?.length === 3)).toBe(true);
  });

  it('passes an unparseable value through rather than hiding it as a dash', () => {
    // Deliberately different from formatStamp. An invitation always HAS an
    // expiry, so a dash would disguise a real defect as ordinary missing data.
    expect(formatDay('not-a-date')).toBe('not-a-date');
  });
});

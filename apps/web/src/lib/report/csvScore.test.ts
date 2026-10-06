/**
 * When the figures file says "Not scored", and the button that offers it says so first — F3,
 * 2026-10-06.
 *
 * The file's overall-score cell is the stored composite only for a score whose status is `scored`
 * and that holds one. For a scan never scored (no score row), one with too little data
 * (`insufficient_data`), and a score row with no composite, it says `Not scored` and has no
 * dimension scores. The button describes the file before the click, so it must be told by the
 * same rule: `fileSaysNotScored`. A scan with too little data has a score row, so the PDF
 * button's own test (`score === null`) does not see it, and this does.
 *
 * `csv-score-cases.json` is read by `apps/api/tests/test_report_csv.py` too, which holds the
 * file's overall-score cell to it. Change the rule on one side and the other goes red on the case
 * that moved.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { Report } from '@avp/shared-types';
import { fileSaysNotScored } from './csvScore';

const FIXTURES = fileURLToPath(
  new URL('../../../../../packages/shared-types/fixtures/', import.meta.url),
);

type Case = {
  score: { status: string; composite: string | null } | null;
  fileSaysNotScored: boolean;
};

const cases = JSON.parse(readFileSync(`${FIXTURES}csv-score-cases.json`, 'utf8')) as Record<
  string,
  Case
>;
const reports = JSON.parse(readFileSync(`${FIXTURES}report-cases.json`, 'utf8')) as Record<
  string,
  Report
>;

describe('the shared cases', () => {
  it.each(Object.entries(cases))('%s', (_name, c) => {
    expect(fileSaysNotScored(c.score as unknown as Report['score'])).toBe(c.fileSaysNotScored);
  });

  it('cover a scan never scored, one with too little data and a score with no composite', () => {
    expect(Object.keys(cases).length).toBeGreaterThanOrEqual(5);
    expect(Object.values(cases).filter((c) => c.fileSaysNotScored).length).toBeGreaterThanOrEqual(4);
    expect(Object.values(cases).some((c) => c.score === null)).toBe(true);
    expect(Object.values(cases).some((c) => c.score?.status === 'insufficient_data')).toBe(true);
    expect(
      Object.values(cases).some((c) => c.score?.status === 'scored' && c.score.composite === null),
    ).toBe(true);
  });
});

describe('the rule', () => {
  it('says a scored scan is scored, and a scan with no score row is not', () => {
    expect(fileSaysNotScored(reports['scored-with-generated-fixes']!.score)).toBe(false);
    expect(fileSaysNotScored(reports['unscored']!.score)).toBe(true);
  });

  it('says a scan with too little data is not scored although it has a score row', () => {
    const score = {
      ...reports['scored-with-generated-fixes']!.score!,
      status: 'insufficient_data' as const,
      composite: null,
    };
    expect(score).not.toBeNull();
    expect(fileSaysNotScored(score)).toBe(true);
  });

  it('reads a report from an API with no score field at all as not scored', () => {
    expect(fileSaysNotScored(undefined)).toBe(true);
  });

  it('does not take a composite of zero for no composite: a score of 0.00 is a score', () => {
    const score = {
      ...reports['scored-with-generated-fixes']!.score!,
      composite: '0.00',
    };
    expect(fileSaysNotScored(score)).toBe(false);
  });
});

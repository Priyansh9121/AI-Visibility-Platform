/**
 * The report's authored copy is the same on screen and in the PDF — 2026-09-27.
 *
 * `report_narrative.py` mirrors five of this module's string tables "word for
 * word", and until this test nothing checked that. The exclusion detail for
 * NO_AUTHORITY_DATA had drifted by an apostrophe, and the PDF's degradation
 * table was missing two codes this side had sentences for.
 *
 * Same discipline as `crossLanguage.test.ts`: both suites read ONE checked-in
 * file, `packages/shared-types/fixtures/report-copy.json`. THIS side is the
 * source of truth and writes the file when asked:
 *
 *     AVP_WRITE_FIXTURES=1 pnpm test -- copyContract
 *
 * Otherwise it asserts the file still describes `strings.ts`, and the Python
 * suite asserts its port matches the same file. Change the copy on either
 * side and one of the two goes red on the string that moved.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  DEGRADATION_FLAG,
  DIMENSION_LABEL,
  DIMENSION_NOTE,
  EXCLUSION_REASON,
  FIX_FOR_DIMENSION,
} from './strings';

const FIXTURE = fileURLToPath(
  new URL('../../../../../packages/shared-types/fixtures/report-copy.json', import.meta.url),
);

/** The projection both languages compare. */
function projection() {
  return {
    dimensionLabel: DIMENSION_LABEL,
    dimensionNote: DIMENSION_NOTE,
    exclusionReason: EXCLUSION_REASON,
    degradationFlag: DEGRADATION_FLAG,
    fixForDimension: FIX_FOR_DIMENSION,
  };
}

if (process.env['AVP_WRITE_FIXTURES']) {
  writeFileSync(FIXTURE, `${JSON.stringify(projection(), null, 2)}\n`);
}

describe('the shared copy fixture still describes strings.ts', () => {
  const fixture = JSON.parse(readFileSync(FIXTURE, 'utf8')) as ReturnType<typeof projection>;

  for (const table of Object.keys(projection()) as (keyof ReturnType<typeof projection>)[]) {
    it(`${table} is what the Python side is held to`, () => {
      expect(projection()[table]).toEqual(fixture[table]);
    });
  }
});

describe('the Citation Strength copy says what the proxy cannot do', () => {
  // The industry-spread tier is counted through a classification whose
  // confidence is uncalibrated (build-log Epic 2.6/2.8, Finding 2). Copy that
  // presented the tier without saying so would imply a precision it lacks.
  it.each([
    ['DIMENSION_NOTE', DIMENSION_NOTE['citation_strength']!],
    ['EXCLUSION_REASON', EXCLUSION_REASON['NO_AUTHORITY_DATA']!.detail],
  ])('%s states the limitation plainly', (_name, text) => {
    expect(text).toContain('uncalibrated');
    expect(text).toContain('coarse proxy');
    expect(text).toContain('mis-classified');
  });

  it('the measured case says which answers it was measured on — v4, awareness only', () => {
    expect(DIMENSION_NOTE['citation_strength']).toContain(
      'only answers to questions that did not name the brand',
    );
    expect(DIMENSION_NOTE['citation_strength']).not.toContain(
      'including answers to questions that named the brand',
    );
  });

  it('both texts say market participants are out of the second tier', () => {
    for (const text of [
      DIMENSION_NOTE['citation_strength']!,
      EXCLUSION_REASON['NO_AUTHORITY_DATA']!.detail,
    ]) {
      expect(text).toContain('brands in a scanned market');
    }
  });
});

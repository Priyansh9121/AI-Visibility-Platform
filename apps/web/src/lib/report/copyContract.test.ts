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
  DIMENSION_MEANING,
  DIMENSION_NOTE,
  EXCLUSION_REASON,
  FIX_FOR_DIMENSION,
  POSITION_COPY,
  positionCell,
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
    positionCopy: POSITION_COPY,
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

  it('the exclusion detail says market participants are out of the second tier', () => {
    // The note carried this too until 2026-09-29, when it was cut to what a
    // client reads at a glance. The rule itself is in scoring-spec.md.
    expect(EXCLUSION_REASON['NO_AUTHORITY_DATA']!.detail).toContain('brands in a scanned market');
  });

  it('the note reads at a glance: the question it answers, in at most 45 words', () => {
    const note = DIMENSION_NOTE['citation_strength']!;
    expect(note).toContain(
      'When AI names the brand, how well does it back it with a trusted source?',
    );
    const words = note.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w));
    expect(words.length).toBeLessThanOrEqual(45);
  });

  // F3 (2026-10-09): since v5 the dimension is the MEAN authority behind an answer that names the
  // brand, a quality; its one-line meaning says how well, never how often (Mention Rate's question).
  it("the one-line meaning says how well an answer backs the brand, not how often", () => {
    const meaning = DIMENSION_MEANING['citation_strength']!;
    expect(meaning).toBe(
      'When answers name the brand, how well they back it with a trusted source, against the best-backed brand in the scan.',
    );
    expect(meaning).not.toContain('how often');
  });
});

describe('average position is reported, never scored, never zero', () => {
  it('a brand never named on an unprompted question reads as not named', () => {
    expect(positionCell(null, 0)).toBe('Not named');
    expect(positionCell(undefined, undefined)).toBe('Not named');
    expect(positionCell('0', 0)).toBe('Not named');
    expect(positionCell(null, 0)).not.toMatch(/\d/);
  });

  it('carries the count it is averaged over', () => {
    expect(positionCell('2.40', 9)).toBe('2.4 across 9 answers');
    expect(positionCell('1.00', 1)).toBe('1.0 across 1 answer');
  });

  it('the note says it does not enter the score', () => {
    expect(POSITION_COPY['note']).toContain('does not enter the score');
  });
});

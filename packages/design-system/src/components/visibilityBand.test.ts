/**
 * The visibility band table is the truth, and this side asserts it still
 * describes what the page does.
 *
 * `report_pdf._band` in apps/api is a Python copy of `visibilityBand` and of
 * the labels `VisibilityBadge` renders, kept for the reason
 * `report_narrative.py` is: the PDF is a Python process. It drifted — cut at
 * 15 and 35, said "Marginal" and "Dominant" — so on 2026-09-08 the same 27.28
 * read "Barely visible" on the share page and "Marginal" in the PDF downloaded
 * from it.
 *
 * So the rule is policed the way the narrative derivation already is: one
 * checked-in table, read by both suites.
 *
 *   packages/shared-types/fixtures/visibility-bands.json   every boundary
 *
 * This file asserts the table matches THIS implementation, which is the source
 * of truth; `apps/api/tests/test_visibility_band.py` asserts the PDF says the
 * same. Move a threshold or a word here and update the table; the Python suite
 * then tells you what the PDF still gets wrong.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { visibilityBand } from '../tokens/color.js';
import { visibilityBandLabel } from './Badge.js';

const FIXTURE = fileURLToPath(
  new URL('../../../shared-types/fixtures/visibility-bands.json', import.meta.url),
);

interface Case {
  score: number;
  band: ReturnType<typeof visibilityBand>;
  label: string;
}

const table = JSON.parse(readFileSync(FIXTURE, 'utf8')) as {
  thresholds: Record<string, [number, number]>;
  cases: Case[];
};

describe('the visibility band table describes what the page does', () => {
  it.each(table.cases)('$score is $band, shown as "$label"', ({ score, band, label }) => {
    expect(visibilityBand(score)).toBe(band);
    expect(visibilityBandLabel(score)).toBe(label);
  });

  it('covers both sides of every threshold, so it can police something', () => {
    const scores = new Set(table.cases.map((c) => c.score));
    for (const [lower] of Object.values(table.thresholds)) {
      if (lower === 0) continue;
      expect(scores.has(lower), `no case AT the ${lower} boundary`).toBe(true);
      expect(
        [...scores].some((s) => s > lower - 1 && s < lower),
        `no case just below ${lower}`,
      ).toBe(true);
    }
    expect(new Set(table.cases.map((c) => c.band))).toEqual(new Set(Object.keys(table.thresholds)));
  });

  it('names the two live scores the dry run saw disagree', () => {
    expect(visibilityBandLabel(27.28)).toBe('Barely visible');
    expect(visibilityBandLabel(17.46)).toBe('Absent');
  });
});

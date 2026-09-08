/**
 * Every reason code the scoring engine can emit has client-facing copy.
 *
 * `ReportView` falls back to the RAW CODE when a string is missing —
 * `DEGRADATION_FLAG[flag] ?? flag` renders the identifier itself as a bullet,
 * and an unknown exclusion shows it in a badge. On a document that gets sent
 * to a prospect's CMO, that is a machine identifier in the middle of prose.
 *
 * It had already happened twice. `NO_SENTIMENT_POPULATION` has been emitted
 * since Epic 5 with no copy at all, and scoring v2's `NO_AWARENESS_POPULATION`
 * would have been the second. Both were found by writing this test rather than
 * by anyone seeing a report.
 *
 * **The list below is hand-maintained, and that is the weakness.** It mirrors
 * the literals in `apps/api/src/avp_api/services/scoring.py` — every
 * `excluded[...] = "..."` and `flags.append("...")` — and nothing enforces the
 * mirror, so a code added there without being added here still ships. It is a
 * guard against forgetting the copy, not against forgetting this file. Deriving
 * it would need the codes in the shared contract, which is a larger change than
 * the defect warrants today.
 */

import { describe, it, expect } from 'vitest';
import { DEGRADATION_FLAG, EXCLUSION_REASON, VISIBILITY_FLAG } from './strings';

/** `excluded[Dimension...] = "..."` in scoring.py, plus compute_score's total-failure case. */
const EXCLUSION_CODES = [
  'NOT_YET_MEASURED',
  'NO_POPULATION',
  'NO_COMPETITOR_SET',
  'NO_ANSWERED_RESULTS',
  'NO_AWARENESS_POPULATION',
  'NO_AUTHORITY_DATA',
] as const;

/** `flags.append("...")` in scoring.py and citation_strength. */
const DEGRADATION_CODES = [
  'NO_AUTHORITY_DATA',
  'NO_CITATIONS_IN_SCAN',
  'NO_COMPETITOR_SET',
  'WEAK_COMPETITOR_SET',
  'TECHNICAL_FOUNDATION_NOT_MEASURED',
  'NO_ANSWERED_RESULTS',
  'NO_SENTIMENT_POPULATION',
  'NO_AWARENESS_POPULATION',
] as const;

/** Findings emitted by `report._visibility_flags`. */
const VISIBILITY_CODES = ['NAMED_ONLY_WHEN_PROMPTED'] as const;

describe('no reason code reaches a reader as a raw identifier', () => {
  it.each(VISIBILITY_CODES)('%s has a sentence', (code) => {
    expect(VISIBILITY_FLAG[code], `VISIBILITY_FLAG is missing ${code}`).toBeDefined();
  });

  it('a visibility finding never reads as reassurance', () => {
    // The framing this copy exists to avoid. "Answerable" and "recognised"
    // claim the engine knows the brand; a mention is a text match, so a
    // question that supplied the name proves nothing of the sort.
    for (const sentence of Object.values(VISIBILITY_FLAG)) {
      for (const word of ['answerable', 'recognised', 'recognized', 'at least']) {
        expect(sentence.toLowerCase(), `copy claims more than the data supports: ${word}`)
          .not.toContain(word);
      }
    }
  });

  it.each(EXCLUSION_CODES)('%s has a label and a detail', (code) => {
    const copy = EXCLUSION_REASON[code];
    expect(copy, `EXCLUSION_REASON is missing ${code}`).toBeDefined();
    expect(copy!.label.length).toBeGreaterThan(0);
    expect(copy!.detail.length).toBeGreaterThan(0);
  });

  it.each(DEGRADATION_CODES)('%s has a sentence', (code) => {
    expect(DEGRADATION_FLAG[code], `DEGRADATION_FLAG is missing ${code}`).toBeDefined();
  });

  it('no copy reads as a machine identifier', () => {
    // The failure this guards against is a placeholder that satisfies the
    // checks above by echoing the code back.
    for (const [code, copy] of Object.entries(EXCLUSION_REASON)) {
      expect(copy.label, `${code} label is the code`).not.toBe(code);
      expect(copy.detail).not.toContain(code);
    }
    for (const [code, sentence] of Object.entries(DEGRADATION_FLAG)) {
      expect(sentence, `${code} sentence is the code`).not.toBe(code);
      expect(sentence).not.toContain(code);
    }
    for (const [code, sentence] of Object.entries(VISIBILITY_FLAG)) {
      expect(sentence, `${code} sentence is the code`).not.toBe(code);
      expect(sentence).not.toContain(code);
    }
  });
});

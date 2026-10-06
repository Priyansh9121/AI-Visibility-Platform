/**
 * When the figures file says "Not scored" — F3, 2026-10-06.
 *
 * The file's overall-score cell (`services/report_csv._overall_score`) is the stored composite only
 * for a score whose status is `scored` and that holds a composite. For a scan that was never scored
 * (no score row), one with too little data to score (`insufficient_data`) and a score row that
 * holds no composite, it says `Not scored`, and the file has no dimension scores. The button that
 * offers the file describes it before the click, so it is told by this one rule and not by the PDF
 * button's own `score === null`, which does not see the second case: a scan with too little data
 * HAS a score row.
 *
 * Mirrored from the API, and held to it by `csv-score-cases.json`, which both
 * `apps/api/tests/test_report_csv.py` (the file's cell) and `csvScore.test.ts` (this) read.
 */

import type { Report } from '@avp/shared-types';

/** True when the file will say "Not scored" where the overall score would be. */
export function fileSaysNotScored(score: Report['score'] | undefined): boolean {
  return score == null || score.status !== 'scored' || score.composite == null;
}

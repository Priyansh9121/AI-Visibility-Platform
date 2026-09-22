/**
 * REAL MEASURED DATA, for tests only — never for the styleguide, whose
 * fixtures file forbids real brands (see `styleguide/fixtures.ts`).
 *
 * MSM AV (msmav.com.au), a professional audio-visual retailer: 20 prompts
 * across four engines, scanned 22 Sept 2026. The brief that added the share
 * strip asked for this scan as THE test case rather than invented numbers,
 * because it reaches every branch the layout has: the subject holds none of
 * the field, one rival (Avalliance) was in the competitor set and was named in
 * no answer, and the four real shares happen to sum to the whole.
 */
import type { ShareInput } from './shareLayout.js';

export const MSM_AV_FIELD: ShareInput[] = [
  { key: 'msmav', label: 'MSM AV', value: 0, isSubject: true },
  { key: 'sweetwater', label: 'Sweetwater', value: 38.5 },
  { key: 'avispl', label: 'AVI-SPL', value: 18.5 },
  { key: 'avalliance', label: 'Avalliance', value: 0 },
  { key: 'diversified', label: 'Diversified', value: 16.9 },
  { key: 'shure', label: 'Shure', value: 26.1 },
];

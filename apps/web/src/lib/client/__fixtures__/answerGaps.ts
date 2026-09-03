/**
 * Answer-gap fixtures — Epic B.
 *
 * Shaped after the real `avp_dev` rows rather than invented, because the two
 * states that are easy to get wrong only show up together: `PSM Digital`'s
 * worst scan carries 9 genuine absences beside 10 prompts where no brand was
 * named at all, and a fixture without both cannot catch a screen that sums
 * them.
 */

import type { AnswerGaps, AnswerGapRow } from '@avp/shared-types';

const ENGINES = ['chatgpt', 'claude', 'claude_search'];

function row(
  promptId: string,
  text: string,
  kind: AnswerGapRow['kind'],
  {
    subjectNamedOn = 0,
    rivalNamedOn = 0,
    subjectCited = false,
    intent = 'awareness',
  }: {
    subjectNamedOn?: number;
    rivalNamedOn?: number;
    subjectCited?: boolean;
    intent?: string;
  } = {},
): AnswerGapRow {
  return {
    promptId,
    text,
    intent,
    position: 0,
    kind,
    enginesAnswered: 3,
    subjectNamedOn,
    rivalsNamedOn: rivalNamedOn,
    noBrandOn: kind === 'no_brands' ? 3 : 0,
    subjectCited,
    absentOn: 3 - subjectNamedOn,
    cells: [
      { brand: 'PSM Digital', namedOn: subjectNamedOn },
      { brand: 'WebFX', namedOn: rivalNamedOn },
      { brand: 'Clutch', namedOn: rivalNamedOn > 0 ? 1 : 0 },
    ],
  };
}

/** A gap-heavy scan: rivals winning, and a large no-brand tail beside them. */
export const gapHeavy: AnswerGaps = {
  clientId: 'clnt_01AAA',
  name: 'PSM Digital',
  domain: 'psmdigitalagency.com.au',
  scanId: 'scan_02',
  scannedAt: '2026-08-30T10:00:00Z',
  availableScanIds: ['scan_02', 'scan_01'],
  engines: ENGINES,
  brands: [
    { name: 'PSM Digital', domain: 'psmdigitalagency.com.au', isSubject: true, answersNamed: 4, promptsNamed: 2 },
    { name: 'WebFX', domain: 'webfx.com', isSubject: false, answersNamed: 9, promptsNamed: 3 },
    { name: 'Clutch', domain: 'clutch.co', isSubject: false, answersNamed: 8, promptsNamed: 3 },
  ],
  rows: [
    row('p1', 'how do i find a good seo agency in australia', 'absent', { rivalNamedOn: 3 }),
    row('p2', 'webfx vs a smaller australian seo agency', 'absent', { rivalNamedOn: 3 }),
    row('p3', 'what should i look for when hiring someone to run my google ads', 'no_brands'),
    row('p4', 'best local marketing help for a small shop', 'no_brands'),
    row('p5', 'psm digital reviews', 'covered', {
      subjectNamedOn: 3,
      rivalNamedOn: 1,
      subjectCited: true,
      intent: 'bottom_funnel',
    }),
    row('p6', 'affordable seo services for tradies', 'partial', {
      subjectNamedOn: 1,
      rivalNamedOn: 2,
      intent: 'comparison',
    }),
  ],
  rivals: [
    { name: 'WebFX', domain: 'webfx.com', answersWon: 9, scansPresent: 2 },
    { name: 'Clutch', domain: 'clutch.co', answersWon: 8, scansPresent: 1 },
  ],
  prompts: 6,
  absent: 2,
  partial: 1,
  uncited: 0,
  covered: 1,
  noBrands: 2,
  unanswered: 0,
  subjectCitable: true,
};

/**
 * A scan where the client's own domain was never cited.
 *
 * Modelled on the real Notion scan, where 22 prompts named the brand and none
 * cited its domain. `uncited` must stay 0 and the screen must say WHY rather
 * than showing a zero that reads as "no citation gaps".
 */
export const notCitable: AnswerGaps = {
  ...gapHeavy,
  name: 'Notion',
  domain: 'notion.so',
  subjectCitable: false,
  uncited: 0,
  absent: 0,
  covered: 4,
  noBrands: 0,
  partial: 2,
  prompts: 6,
  rivals: [],
  rows: gapHeavy.rows.map((r) => ({ ...r, kind: 'covered', subjectNamedOn: 3, absentOn: 0 })),
};

/** A scan with no rivals detected — the grid has one column and says so. */
export const noRivals: AnswerGaps = {
  ...gapHeavy,
  brands: [gapHeavy.brands[0]!],
  rows: gapHeavy.rows.map((r) => ({ ...r, cells: [r.cells[0]!] })),
  rivals: [],
};

/** One scan only, so the picker must not render. */
export const singleScan: AnswerGaps = {
  ...gapHeavy,
  availableScanIds: ['scan_02'],
};

/**
 * Four audit fixes said more than the audit read — launch checklist 3.49.
 *
 * `ROBOTS_TXT_DISALLOW` ("nothing else on this list can take effect"), `NO_DATE_SIGNAL_AVAILABLE`
 * ("anywhere on the page"), `NO_ORGANIZATION_OR_LOCALBUSINESS` ("Nothing on the page states …
 * what this organisation is") and `CONTENT_STALE` ("publish a visible updated date") overstated
 * their finding in the report's fix list, and so in the PDF, which prints the API's copy of the
 * same table. The Technical screen already said the first three truly (3.39,
 * `technical.test.ts`).
 *
 * No shared fixture carries these four codes: `report-copy.json` has no detail-code table and
 * `report-cases.json` holds only `NO_FAQ_SCHEMA` and `NO_PRODUCT_OR_SERVICE_SCHEMA`. So the two
 * tables are held together here, the way `technical.test.ts` holds this screen to the API's
 * codes: this suite reads the API's table out of `report_narrative.py` and compares it with
 * `strings.ts` word for word. `apps/api/tests/test_audit_fix_wording.py` pins the text, and runs
 * what each sentence says the audit does against the audit's own code.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { ReportAuditFinding } from '@avp/shared-types';
import { deriveFixes } from './derive';
import { FIX_FOR_DETAIL_CODE } from './strings';

const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));
const read = (path: string): string => readFileSync(join(ROOT, path), 'utf8');
const NARRATIVE_PY = read('apps/api/src/avp_api/services/report_narrative.py');
const AUDIT_PY = read('apps/api/src/avp_api/services/technical_audit.py');

/**
 * One entry of the API's `FIX_FOR_DETAIL_CODE`: its string literals, joined as Python joins
 * adjacent ones, split at the commas that end a tuple element. A line that is not one literal
 * fails the read rather than being skipped, so a reformat cannot make the comparison vacuous.
 */
function apiEntry(code: string): { title: string; detail: string; effort: string } {
  const entry = new RegExp(`^    "${code}": \\(\\n([\\s\\S]*?)\\n    \\),$`, 'm').exec(NARRATIVE_PY);
  expect(entry, `report_narrative.py has no entry for ${code}`).not.toBeNull();
  const elements: string[] = [];
  let current = '';
  for (const line of entry![1]!.split('\n')) {
    const literal = /^\s+"([^"\\]*)"(,?)$/.exec(line);
    expect(literal, `${code}: not one string literal: ${line}`).not.toBeNull();
    current += literal![1]!;
    if (literal![2] === ',') {
      elements.push(current);
      current = '';
    }
  }
  expect(elements, `${code}: title, detail and effort`).toHaveLength(3);
  const [title, detail, effort] = elements as [string, string, string];
  return { title, detail, effort };
}

/**
 * For each code: the check it rides on and the status a report lists it with, its title, how
 * its detail now opens, and what it used to say, compared lower-cased. A report lists warnings, failures
 * and errors; `content_freshness` is `not_applicable` when it carries `NO_DATE_SIGNAL_AVAILABLE`,
 * so no real report holds that fix today, and its wording is held anyway.
 */
const REWORDED = {
  ROBOTS_TXT_DISALLOW: {
    checkKey: 'indexable',
    status: 'fail',
    title: 'Remove Disallow: / from the User-agent: * rules in robots.txt',
    opening: 'The audit found a Disallow: / rule under User-agent: * in the site’s robots.txt.',
    gone: [
      'nothing else on this list',
      'can take effect',
      'tells crawlers to stay out',
      'cannot cite',
      'stop robots.txt from blocking',
      'crawlers that build ai answers',
      'rules for all crawlers',
    ],
  },
  NO_DATE_SIGNAL_AVAILABLE: {
    checkKey: 'content_freshness',
    status: 'warn',
    title: 'Publish machine-readable dates on the main pages',
    opening: 'The audit read no date from the audited page’s Last-Modified header',
    gone: [
      'anywhere on the page',
      'no date could be found',
      'cannot be established either way',
      'in the structured data fixes this',
    ],
  },
  NO_ORGANIZATION_OR_LOCALBUSINESS: {
    checkKey: 'schema_business_entity',
    status: 'fail',
    title: 'Add Organization or LocalBusiness schema to the homepage',
    opening: 'The audit found none of Organization, Corporation, OnlineBusiness, LocalBusiness,',
    gone: [
      'nothing on the page states',
      'what this organisation is',
      'entity marker',
      'hangs off',
      'gives the audit one to find',
    ],
  },
  CONTENT_STALE: {
    checkKey: 'content_freshness',
    status: 'warn',
    title: 'Refresh the audited page and publish an updated dateModified in its JSON-LD',
    opening: 'The newest date the audit read from the audited page, in its Last-Modified header',
    gone: ['visible updated date', 'date signal', 'one of the few signals', 'whether an answer is still current'],
  },
} as const;

const CODES = Object.keys(REWORDED) as (keyof typeof REWORDED)[];

const finding = (code: keyof typeof REWORDED): ReportAuditFinding =>
  ({
    id: 'tchk_wording',
    checkKey: REWORDED[code].checkKey,
    status: REWORDED[code].status,
    value: null,
    detailCode: code,
  }) as ReportAuditFinding;

describe('four audit fixes say only what the audit read — launch checklist 3.49', () => {
  it.each(CODES)(
    '%s: the fix a report derives opens as the audit found it, and none of what it used to say is left',
    (code) => {
      const [fix] = deriveFixes([], [finding(code)]);
      expect(fix!.id).toBe(`audit:${REWORDED[code].checkKey}`);
      expect(fix!.title).toBe(REWORDED[code].title);
      expect(fix!.detail.startsWith(REWORDED[code].opening)).toBe(true);
      expect(fix!.effort).toBe(code === 'CONTENT_STALE' ? 'M' : 'S');
      for (const phrase of REWORDED[code].gone) {
        expect(fix!.title.toLowerCase(), phrase).not.toContain(phrase);
        expect(fix!.detail.toLowerCase(), phrase).not.toContain(phrase);
      }
    },
  );

  it('the screen’s table is the API narrative’s, word for word: all sixteen, the four of 3.49 among them', () => {
    // `report_narrative.py` is what the PDF prints; the screen must not say it differently.
    // The four are asserted by name so the read cannot pass by finding nothing, and the rest
    // are compared too: a table that drifted on a fourth would be seen the same way.
    const codes = Object.keys(FIX_FOR_DETAIL_CODE);
    expect(codes).toHaveLength(16);
    for (const code of CODES) expect(codes, code).toContain(code);
    for (const code of codes) {
      expect(FIX_FOR_DETAIL_CODE[code], code).toEqual(apiEntry(code));
    }
  });

  it('NO_ORGANIZATION_OR_LOCALBUSINESS names exactly the eight types the check matches, read back from the audit', () => {
    const tracked = [
      ...AUDIT_PY.matchAll(/^\s+"(?:organization|localbusiness)": \(([^)]*)\),$/gm),
    ].flatMap((m) => [...m[1]!.matchAll(/"([A-Za-z]+)"/g)].map((t) => t[1]!));
    // So an empty read cannot pass, and widening the check (launch checklist 3.48) fails here.
    expect(tracked).toHaveLength(8);
    const listed = /found none of (.+?) in the audited page/.exec(
      FIX_FOR_DETAIL_CODE.NO_ORGANIZATION_OR_LOCALBUSINESS!.detail,
    );
    expect(listed).not.toBeNull();
    expect(listed![1]!.split(/, | or /)).toEqual(tracked);
  });
});

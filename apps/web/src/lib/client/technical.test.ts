/**
 * Technical audit derivation — Epic 9.22.
 *
 * The assertion that carries the most weight is the first one: the Ledger drawn
 * from these dimensions must re-sum to the score the API stored. That is the
 * Ledger's correctness condition on the report, and it has to hold here too or
 * the column is a picture of a number rather than the number.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CHECK_DETAIL_SENTENCE,
  auditDimensions,
  checkDetailSentence,
  checkLabel,
  excludedComponents,
  groupChecks,
  num,
  tally,
} from './technical';
import { compositeScore } from '@avp/design-system';
import type { AuditCheck, TechnicalAudit } from '@avp/shared-types';
import { FIX_FOR_DETAIL_CODE } from '@/lib/report/strings';

/** plausible.io, exactly as the endpoint returns it. */
const PLAUSIBLE = {
  status: 'ok',
  technicalFoundation: '67.00',
  components: {
    indexation: '100',
    schema_presence: '60',
    structured_data: '0',
    content_freshness: '100',
  },
  componentWeights: {
    indexation: '30.00',
    schema_presence: '20.00',
    structured_data: '25.00',
    content_freshness: '25.00',
  },
  excludedComponents: {},
  checks: [],
} as unknown as TechnicalAudit;

/** linear.app — freshness unmeasurable, so the weights redistribute. */
const LINEAR = {
  status: 'ok',
  technicalFoundation: '40.00',
  components: { indexation: '100', schema_presence: '0', structured_data: '0' },
  componentWeights: {
    indexation: '40.00',
    schema_presence: '26.67',
    structured_data: '33.33',
  },
  excludedComponents: { content_freshness: 'NO_DATE_SIGNAL_AVAILABLE' },
  checks: [],
} as unknown as TechnicalAudit;

const check = (key: string, status: string): AuditCheck =>
  ({ id: key, checkKey: key, status, value: null, detailCode: null }) as unknown as AuditCheck;

describe('the ledger IS the sub-score, not a picture of it', () => {
  it('re-sums to the stored technical foundation', () => {
    // `compositeScore` is the design system's own weighted sum — the same one
    // `layoutLedger` uses to place the bars. If this drifts, the column height
    // stops matching the number printed beside it.
    expect(compositeScore(auditDimensions(PLAUSIBLE))).toBeCloseTo(67, 2);
  });

  it('re-sums when a component was excluded and its weight redistributed', () => {
    // The case that makes using EFFECTIVE weights load-bearing. With the
    // nominal 30/20/25/25 this would come out at 30, not 40.
    expect(compositeScore(auditDimensions(LINEAR))).toBeCloseTo(40, 2);
  });

  it('uses the effective weights, which sum to 100 even when one is missing', () => {
    const total = auditDimensions(LINEAR).reduce((sum, d) => sum + d.weight, 0);
    expect(total).toBeCloseTo(100, 1);
    expect(auditDimensions(LINEAR)).toHaveLength(3);
  });

  it('orders the components heaviest first, so the column stacks like the report', () => {
    expect(auditDimensions(PLAUSIBLE).map((d) => d.key)).toEqual([
      'indexation',
      'content_freshness',
      'structured_data',
      'schema_presence',
    ]);
  });

  it('draws nothing rather than guessing when the API sent no components', () => {
    const bare = { status: 'ok', checks: [] } as unknown as TechnicalAudit;
    expect(auditDimensions(bare)).toEqual([]);
  });
});

describe('an unmeasurable component is explained, not scored zero', () => {
  it('names it and says why', () => {
    const [e] = excludedComponents(LINEAR);
    expect(e!.label).toBe('Content Freshness');
    expect(e!.code).toBe('NO_DATE_SIGNAL_AVAILABLE');
    expect(e!.copy).toContain('publishing convention');
  });

  it('says nothing when everything was measurable', () => {
    expect(excludedComponents(PLAUSIBLE)).toEqual([]);
  });

  it('falls back to a plain sentence for a code it has never seen', () => {
    const odd = {
      ...LINEAR,
      excludedComponents: { indexation: 'SOME_NEW_CODE' },
    } as unknown as TechnicalAudit;
    expect(excludedComponents(odd)[0]!.copy).toContain('could not be measured');
  });
});

describe('the tally counts what it claims to count', () => {
  const CHECKS = [
    check('a', 'pass'),
    check('b', 'pass'),
    check('c', 'warn'),
    check('d', 'fail'),
    check('e', 'not_applicable'),
  ];

  it('counts each verdict', () => {
    const t = tally(CHECKS);
    expect([t.pass, t.warn, t.fail, t.not_applicable]).toEqual([2, 1, 1, 1]);
  });

  it('keeps not_applicable OUT of the denominator', () => {
    // A check that does not apply is not a check the site passed. Folding it in
    // would inflate "16 of 17" with a question nobody asked.
    expect(tally(CHECKS).measured).toBe(4);
  });

  it('counts an unknown status nowhere rather than inflating a bucket', () => {
    // The backend owns this enum. A value added there must not silently land
    // in "passed".
    const t = tally([...CHECKS, check('f', 'something_new')]);
    expect(t.measured).toBe(4);
    expect(t.pass).toBe(2);
  });

  it('survives an audit with no checks at all', () => {
    expect(tally([]).measured).toBe(0);
  });
});

describe('grouping shows structure without losing a check', () => {
  const CHECKS = [
    check('site_reachable', 'pass'),
    check('indexable', 'pass'),
    check('schema_present', 'pass'),
    check('cwv_lcp', 'pass'),
  ];

  it('groups along the seams the API already orders by', () => {
    expect(groupChecks(CHECKS).map((g) => g.title)).toEqual([
      'Crawlability',
      'Structured data',
      'Core Web Vitals',
    ]);
  });

  it('drops no check — an unknown key lands in Other', () => {
    const groups = groupChecks([...CHECKS, check('brand_new_check', 'warn')]);
    const flat = groups.flatMap((g) => g.checks.map((c) => c.checkKey));
    expect(flat).toContain('brand_new_check');
    expect(flat).toHaveLength(5);
    expect(groups.at(-1)!.title).toBe('Other');
  });

  it('omits a group entirely when it has no checks', () => {
    expect(groupChecks([check('site_reachable', 'pass')]).map((g) => g.title)).toEqual([
      'Crawlability',
    ]);
  });

  it('names Core Web Vitals as unweighted, because they are', () => {
    const cwv = groupChecks(CHECKS).find((g) => g.title === 'Core Web Vitals');
    expect(cwv!.note).toContain('no weight');
  });
});

describe('labels', () => {
  it('uses this product own words for a known check', () => {
    expect(checkLabel('cwv_lcp')).toBe('Largest Contentful Paint');
  });

  it('de-slugs an unknown key rather than showing a raw machine name', () => {
    expect(checkLabel('brand_new_check')).toBe('Brand new check');
  });
});

describe('num', () => {
  it('parses the API string decimals', () => {
    expect(num('67.00')).toBe(67);
  });

  it('is null for null, undefined and nonsense — never 0', () => {
    // A zero here would be a measured value. These are absences.
    expect(num(null)).toBeNull();
    expect(num(undefined)).toBeNull();
    expect(num('not-a-number')).toBeNull();
  });
});

describe('checkDetailSentence — launch checklist 3.39', () => {
  const MACHINE_CODE = /\b[A-Z]{3,}_[A-Z0-9_]+\b/;

  // The fix list's six whose wording does not stand on a row as the statement of what a
  // check found (round 2 of 3.39), and the six codes no fix words.
  const OVERRIDDEN = [
    'META_ROBOTS_NOINDEX',
    'MULTIPLE_H1',
    'NO_DATE_SIGNAL_AVAILABLE',
    'NO_ORGANIZATION_OR_LOCALBUSINESS',
    'NO_PRODUCT_OR_SERVICE_SCHEMA',
    'ROBOTS_TXT_DISALLOW',
  ];
  const NOT_IN_FIX = [
    'CLS_NOT_OBSERVED',
    'FIELD_METRIC_REQUIRES_REAL_USER_DATA',
    'LAB_MEASUREMENT_NOT_FIELD_DATA',
    'LCP_NOT_OBSERVED',
    'ROBOTS_DISALLOWED',
    'ROBOTS_UNREACHABLE',
  ];

  it('is the fix list’s sentence, word for word, for ten of its sixteen codes', () => {
    const codes = Object.keys(FIX_FOR_DETAIL_CODE);
    expect(codes).toHaveLength(16);
    const verbatim = codes.filter((code) => !OVERRIDDEN.includes(code));
    // Sixteen less the six below.
    expect(verbatim).toHaveLength(10);
    for (const code of verbatim) {
      expect(checkDetailSentence(code), code).toBe(FIX_FOR_DETAIL_CODE[code]!.detail);
    }
  });

  it('says its own sentence for the six whose fix wording does not stand on a row, looked up before the fix list', () => {
    const OWN: Record<string, string> = {
      ROBOTS_TXT_DISALLOW:
        'The site’s robots.txt has a rule that asks crawlers to stay out of the whole site (Disallow: /), except where another rule lets them in.',
      META_ROBOTS_NOINDEX:
        'The page is marked noindex, which asks search engines to leave it out of their indexes.',
      NO_PRODUCT_OR_SERVICE_SCHEMA:
        'The audited page has no Product or Service markup, so what the business sells has to be inferred from page copy rather than read from the markup.',
      NO_DATE_SIGNAL_AVAILABLE:
        'No usable Last-Modified header and no datePublished or dateModified in the page’s structured data was found, so its freshness cannot be established either way.',
      MULTIPLE_H1:
        'More than one top-level heading leaves the page’s subject ambiguous to anything parsing it.',
      NO_ORGANIZATION_OR_LOCALBUSINESS:
        'The audited page’s JSON-LD and microdata name none of the types this check looks for: Organization, Corporation, OnlineBusiness, LocalBusiness, Dentist, Restaurant, Store or MedicalBusiness.',
    };
    expect(Object.keys(OWN).sort()).toEqual([...OVERRIDDEN].sort());
    for (const [code, sentence] of Object.entries(OWN)) {
      expect(checkDetailSentence(code), code).toBe(sentence);
      // Still a code the fix list words, and worded differently there: the lookup order is
      // what decides which of the two a row shows.
      expect(FIX_FOR_DETAIL_CODE[code], code).toBeDefined();
      expect(sentence, code).not.toBe(FIX_FOR_DETAIL_CODE[code]!.detail);
    }
    // What the fix wording said that a row cannot stand behind.
    for (const [code, phrase] of [
      ['ROBOTS_TXT_DISALLOW', 'nothing else on this list'],
      ['META_ROBOTS_NOINDEX', 'excluded from the indexes these answers are drawn from'],
      ['NO_PRODUCT_OR_SERVICE_SCHEMA', 'Without it'],
      ['NO_DATE_SIGNAL_AVAILABLE', 'anywhere on the page'],
      ['MULTIPLE_H1', 'Several competing'],
      ['NO_ORGANIZATION_OR_LOCALBUSINESS', 'Nothing on the page states'],
    ] as const) {
      expect(FIX_FOR_DETAIL_CODE[code]!.detail, `${code}: the fix list is left as it was`).toContain(phrase);
      expect(checkDetailSentence(code), code).not.toContain(phrase);
    }
  });

  it('words the four lab and field codes, and the two a sitemap check can carry', () => {
    expect(Object.keys(CHECK_DETAIL_SENTENCE).sort()).toEqual([...NOT_IN_FIX, ...OVERRIDDEN].sort());
    for (const [code, sentence] of Object.entries(CHECK_DETAIL_SENTENCE)) {
      expect(sentence, code).not.toContain(code);
      expect(sentence, code).not.toMatch(MACHINE_CODE);
      expect(sentence.endsWith('.'), code).toBe(true);
      // Nothing the data cannot support: no figure, no verdict about the site.
      expect(sentence, code).not.toMatch(/\d/);
    }
    // The six have no fix wording at all, so there is nothing for the lookup order to decide.
    for (const code of NOT_IN_FIX) {
      expect(FIX_FOR_DETAIL_CODE[code], code).toBeUndefined();
    }
    // The file that decides a sitemap check may be a redirect's, so it is "the robots.txt this depends on".
    expect(CHECK_DETAIL_SENTENCE.ROBOTS_DISALLOWED).toBe(
      'The robots.txt this depends on asks crawlers like ours not to load this, so it was not checked.',
    );
  });

  it('is null for no code, an empty one, and one nothing words', () => {
    expect(checkDetailSentence(null)).toBeNull();
    expect(checkDetailSentence(undefined)).toBeNull();
    expect(checkDetailSentence('')).toBeNull();
    expect(checkDetailSentence('SOMETHING_NEW')).toBeNull();
    // Inherited names are not codes.
    expect(checkDetailSentence('constructor')).toBeNull();
    expect(checkDetailSentence('toString')).toBeNull();
  });

  describe('every code the audit can put on a check has a sentence — read back from technical_audit.py', () => {
    // The codes are read out of the API's own file, so a check that learns a new code
    // without wording here fails this suite instead of printing the code on the screen.
    const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));
    const source = readFileSync(join(ROOT, 'apps/api/src/avp_api/services/technical_audit.py'), 'utf8');

    // What the audit says went wrong with the CRAWL, not with a check: these ride on a
    // failed audit, which this screen answers with `auditFailureSentence` and no rows.
    const AUDIT_FAILURE = new Set(['FETCH_FAILED', 'BROWSER_ERROR', 'HTTP_']);
    const literals = [...source.matchAll(/"([A-Z][A-Z0-9]*_[A-Z0-9_]*)"/g)]
      .map((m) => m[1]!)
      .filter((code) => !AUDIT_FAILURE.has(code));
    // `ROBOTS_DISALLOWED` and `ROBOTS_UNREACHABLE` are imported constants, not literals: a
    // sitemap check carries one when robots.txt stops it (`_robots_file_checks`).
    const carried = new Set([...literals, 'ROBOTS_DISALLOWED', 'ROBOTS_UNREACHABLE']);

    it('finds the codes it is meant to find, so an empty read cannot pass', () => {
      for (const code of ['NO_CANONICAL_LINK', 'LCP_NOT_OBSERVED', 'FIELD_METRIC_REQUIRES_REAL_USER_DATA']) {
        expect(carried.has(code), code).toBe(true);
      }
      expect(carried.size).toBeGreaterThanOrEqual(22);
      expect(source).toContain('signals.sitemap_refusal');
      expect(source).toContain('ROBOTS_UNREACHABLE)');
    });

    it('has a sentence for each', () => {
      expect([...carried].filter((code) => checkDetailSentence(code) === null)).toEqual([]);
    });

    it('has no sentence for a code the audit no longer carries', () => {
      const wordedCodes = new Set([...Object.keys(FIX_FOR_DETAIL_CODE), ...Object.keys(CHECK_DETAIL_SENTENCE)]);
      expect([...wordedCodes].filter((code) => !carried.has(code))).toEqual([]);
    });
  });
});

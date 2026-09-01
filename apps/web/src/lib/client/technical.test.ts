/**
 * Technical audit derivation — Epic 9.22.
 *
 * The assertion that carries the most weight is the first one: the Ledger drawn
 * from these dimensions must re-sum to the score the API stored. That is the
 * Ledger's correctness condition on the report, and it has to hold here too or
 * the column is a picture of a number rather than the number.
 */

import { describe, it, expect } from 'vitest';
import {
  auditDimensions,
  checkLabel,
  excludedComponents,
  groupChecks,
  num,
  tally,
} from './technical';
import { compositeScore } from '@avp/design-system';
import type { AuditCheck, TechnicalAudit } from '@avp/shared-types';

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

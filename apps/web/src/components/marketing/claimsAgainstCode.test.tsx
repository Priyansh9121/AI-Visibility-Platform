/**
 * Sentences on the landing page and the pricing card, held to the code they describe — launch
 * checklist 3.63, 2026-10-04.
 *
 * `LandingView.test.tsx` and `PricingCard.test.tsx` pin the WORDS. A pinned sentence proves the
 * page says it, and cannot tell that it went on being said after it stopped being true: that is
 * how both components came to say fixes were ordered by what they would cost while the report
 * ordered them by the points each recovers. This file reads the other side of four claims.
 *
 *   1. "…ordered by the points each would recover, with the effort of each shown beside it" (the
 *      last pipeline step and the pricing card). That sentence is the SHORT FORM of the rule in
 *      `deriveFixes`: by the points each fix recovers, except that a fix for a noindex tag, a
 *      `Disallow: /` rule or a missing robots.txt leads and carries no figure, and fixes with no
 *      figure follow those that have one (the report's own lead says all three). The rule is
 *      tested here from literal gaps and findings, so nothing asserted depends on what a scan
 *      fixture happens to contain; and the real report is rendered and its fix list read back
 *      out of the markup, which is the only test that an effort is drawn beside each fix.
 *   2. "…overrule the result, which then stays in place for every later scan" (the rivals step):
 *      the product's own editor says it, and the API suite proves a later scan carries the list
 *      forward. That behaviour is in Python, so what is read here is the NAMES of its tests, and
 *      the sentence cannot outlive its proof.
 *   3. "…enforced by tests over the tables that hold anything taken from an engine's answer or a
 *      website, and over the report's own response schemas" (the facts-only paragraph): the
 *      tests are `tests/test_facts_only.py`'s, which cover a named list of tables and sweep the
 *      schemas the report module defines. Their names again, and not their source. They do not
 *      walk every table, and the report shows two free-text fields by design (a fix's title and
 *      detail, which are the product's own wording).
 *   4. "…the homepage checked for structured data, crawlability and freshness" (the audit step):
 *      those are the words of the report's own sentence about the dimension the audit feeds, and
 *      that dimension is one of five.
 */

import { beforeAll, describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import type { LedgerSegment } from '@avp/design-system';
import type { ReportAuditFinding } from '@avp/shared-types';
import { CompetitorEditor } from '@/components/report/CompetitorEditor';
import { ReportView } from '@/components/report/ReportView';
import { deriveFixes, deriveNarrative, type DerivedFix } from '@/lib/report/derive';
import { DIMENSION_LABEL, FIX_FOR_DIMENSION } from '@/lib/report/strings';
import { helpscoutReport } from '@/lib/report/__fixtures__/reports';
import { LandingView } from './LandingView';

const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));
const api = (path: string): string => readFileSync(join(ROOT, 'apps/api', path), 'utf8');

// --- literal inputs for `deriveFixes` ------------------------------------------------------

/** A segment as `deriveFixes` reads one: its key, the points left on the table, whether it is the biggest. */
const segment = (key: string, gap: number, isBiggestGap = false): LedgerSegment => ({
  key,
  label: key,
  weight: 0,
  subscore: 0,
  y: 0,
  height: 0,
  litY: 0,
  litHeight: 0,
  gap,
  color: '',
  isBiggestGap,
});

const finding = (
  checkKey: string,
  status: ReportAuditFinding['status'],
  detailCode: string,
): ReportAuditFinding => ({ id: `tchk_${checkKey}`, checkKey, status, value: null, detailCode });

// The points left on each dimension, in an order that is NOT the order of the gaps, so a list that
// comes out in order was put there by the derivation. One domain no one in the scan owns, which is
// the one fix `deriveFixes` is handed that has no figure and no audit check behind it.
const GAPS = [segment('share_of_voice', 17.5), segment('mention_rate', 5), segment('citation_strength', 19.3, true)];
const UNCLAIMED = [{ domain: 'eesel.ai', citations: 6 }];
const fixesFor = (findings: ReportAuditFinding[] = []): DerivedFix[] =>
  deriveFixes(GAPS, findings, [], [], UNCLAIMED, 3);

describe('the order of the fix list, from literal gaps and findings: the rule the page states in short form', () => {
  it('lists the fixes that carry a figure by that figure, largest first, however the gaps arrived', () => {
    const withFigure = fixesFor().filter((f) => f.pointsUpside !== undefined);
    expect(withFigure.length).toBeGreaterThan(1);
    expect(withFigure[0]!.id).toBe('gap:citation_strength');
    const figures = withFigure.map((f) => f.pointsUpside!);
    expect(figures).toEqual([...figures].sort((a, b) => b - a));
  });

  it('puts the fixes that carry none after every fix that carries one', () => {
    const fixes = fixesFor([finding('sitemap_present', 'warn', 'NO_SITEMAP_XML')]);
    expect(fixes.map((f) => f.id)).toEqual(expect.arrayContaining(['audit:sitemap_present', 'citation:eesel.ai']));
    const firstWithout = fixes.findIndex((f) => f.pointsUpside === undefined);
    expect(firstWithout).toBeGreaterThan(0);
    expect(fixes.slice(firstWithout).every((f) => f.pointsUpside === undefined)).toBe(true);
  });

  it('does not order them by effort: the fix that costs most is first when it recovers most, and last when it does not', () => {
    // The dearest and the cheapest fix the string table has. (The generator's own choice of effort
    // is laid over the list AFTER it is ordered, so only the table's efforts could ever rank it.)
    const rank = { S: 0, M: 1, L: 2 } as const;
    const byEffort = Object.entries(FIX_FOR_DIMENSION).sort(([, a], [, b]) => rank[a.effort] - rank[b.effort]);
    const cheap = byEffort[0]!;
    const dear = byEffort[byEffort.length - 1]!;
    expect(rank[dear[1].effort], 'the table gives every fix one effort, so there is no cost to be wrong about').toBeGreaterThan(
      rank[cheap[1].effort],
    );
    const order = (dearGap: number, cheapGap: number): string[] =>
      deriveFixes([segment(dear[0], dearGap), segment(cheap[0], cheapGap)], []).map((f) => f.id);
    expect(order(19.3, 5)).toEqual([`gap:${dear[0]}`, `gap:${cheap[0]}`]);
    expect(order(5, 19.3)).toEqual([`gap:${cheap[0]}`, `gap:${dear[0]}`]);
  });

  // The boundary of the short form. The page says "ordered by the points each would recover", and
  // these are the fixes that come first without one: a finding on the `indexable` or the
  // `robots_txt_present` check (`BLOCKING_FIX_IDS`). `report_narrative.py` and `fix_generator.py`
  // apply the same rule.
  it.each<[string, ReportAuditFinding, string]>([
    ['a noindex tag', finding('indexable', 'fail', 'META_ROBOTS_NOINDEX'), 'audit:indexable'],
    ['a Disallow: / rule in robots.txt', finding('indexable', 'fail', 'ROBOTS_TXT_DISALLOW'), 'audit:indexable'],
    ['a missing robots.txt', finding('robots_txt_present', 'warn', 'NO_ROBOTS_TXT'), 'audit:robots_txt_present'],
  ])('lists a fix for %s first, ahead of every fix with a figure, and gives it none', (_what, found, id) => {
    const fixes = fixesFor([found]);
    expect(fixes[0]!.id).toBe(id);
    expect(fixes[0]!.pointsUpside).toBeUndefined();
    expect(fixes[1]!.pointsUpside).toBeDefined();
  });

  it('leads with no other finding: a sitemap or a canonical link does not jump the figures', () => {
    for (const found of [
      finding('sitemap_present', 'warn', 'NO_SITEMAP_XML'),
      finding('canonical_present', 'warn', 'NO_CANONICAL_LINK'),
    ]) {
      expect(fixesFor([found])[0]!.pointsUpside).toBeDefined();
    }
  });
});

describe('the fix list as the real report draws it', () => {
  // Rendered once for the whole block. The assertions are about structure, so which scan the
  // fixture is does not matter beyond its having fixes to draw (and, for the last test, one of
  // them with a point figure).
  let html = '';
  let items: string[] = [];
  let derived: DerivedFix[] = [];
  beforeAll(() => {
    html = renderToStaticMarkup(<ReportView report={helpscoutReport} animate={false} />);
    items = html.match(/<li class="avp-fixlist__item">[\s\S]*?<\/li>/g) ?? [];
    derived = deriveNarrative(helpscoutReport).fixes;
  });
  const effortOf = (item: string): string | null =>
    /<span class="avp-tag">effort ([SML])<\/span>/.exec(item)?.[1] ?? null;
  const pointsOf = (item: string): number | null => {
    const found = /<span class="avp-tag avp-tag--upside">\+(\d+(?:\.\d+)?) pts<\/span>/.exec(item);
    return found ? Number(found[1]) : null;
  };

  it('draws one item for each fix the derivation produced, in its order', () => {
    expect(derived.length).toBeGreaterThan(0);
    expect(items).toHaveLength(derived.length);
  });

  it('shows the effort of every fix beside it, in the same row as its priority, with its figure when it has one', () => {
    items.forEach((item, i) => {
      const fix = derived[i]!;
      expect(effortOf(item), `fix ${i + 1} has no effort beside it`).toBe(fix.effort);
      expect(item).toMatch(
        /<div class="avp-fixlist__tags"><span class="avp-tag [^"]*">[a-z]+ priority<\/span><span class="avp-tag">effort [SML]<\/span>/,
      );
      expect(pointsOf(item)).toBe(fix.pointsUpside === undefined ? null : Number(fix.pointsUpside.toFixed(1)));
    });
  });

  it('says in its own lead what the page says: by the points each recovers, not by how quickly a fix can be done', () => {
    // The lead says it when the list carries a figure (`fixOrderSentence`); a list with none says
    // that instead, so this needs a fixture that has one.
    expect(
      derived.some((f) => f.pointsUpside !== undefined),
      'the fixture has no fix with a point figure, which is when the lead says how the list is ordered',
    ).toBe(true);
    expect(html).toContain('Ordered by the points each one recovers, not by how quickly it can be done.');
  });
});

describe('a hand-corrected rival list stays in place for every later scan', () => {
  it('is what the editor says, on the screen where the list is corrected', () => {
    const editor = renderToStaticMarkup(
      <CompetitorEditor clientId="cli_1" competitors={[]} onSaved={() => undefined} />,
    );
    expect(editor).toContain('A rival you add or keep by hand stays in place when the scan is run again.');
  });

  it('is proved by the API suite, and the proofs are still there', () => {
    const chain = api('tests/test_scan_chain.py');
    expect(chain).toContain('class TestCompetitorSetsCarryForward');
    expect(chain).toContain('def test_a_second_scan_reuses_the_set_without_re_detecting');
    expect(chain).toContain('def test_an_operators_correction_survives_into_the_next_scan');
  });
});

describe('the tests the page says enforce the facts-only rule are still there', () => {
  const source = api('tests/test_facts_only.py');

  it('cover a named list of the tables that hold what an engine or a site returned', () => {
    const listed = /^FACTS_ONLY_MODELS = \[([\s\S]*?)^\]/m.exec(source)?.[1] ?? '';
    // An engine's answer (its facts) and a website (the intake crawl's conclusions).
    for (const model of ['EngineResult', 'Citation', 'BrandMention', 'Competitor', 'TechnicalAudit', 'Client']) {
      expect(listed, `${model} is on the facts-only list`).toMatch(new RegExp(`^\\s*${model},`, 'm'));
    }
  });

  it('sweep the schemas of the report module', () => {
    expect(source).toContain('def test_report_projection_exposes_no_third_party_prose');
  });
});

describe('the audit step says what the report says about the dimension the audit feeds', () => {
  const landing = (): string => renderToStaticMarkup(<LandingView animate={false} />).replaceAll('&#x27;', "'");

  it('names the three inputs in the words of the report’s own sentence about Technical Foundation', () => {
    expect(FIX_FOR_DIMENSION.technical_foundation!.detail).toContain(
      'Structured data, crawlability and freshness are the inputs this dimension measures',
    );
    expect(landing()).toContain('The homepage checked for structured data, crawlability and freshness');
  });

  it('calls that dimension by the name the report gives it, and counts five', () => {
    expect(DIMENSION_LABEL.technical_foundation).toBe('Technical Foundation');
    expect(Object.keys(DIMENSION_LABEL)).toHaveLength(5);
    expect(landing()).toContain('Technical Foundation, one of the five weighted dimensions of the score.');
  });
});

/**
 * The intake classification result — Epic 2, restructured in Epic 9.11.
 *
 * The assertions that matter here are about HONESTY, not layout: an ambiguous
 * classification must not read as a result, and the model's uncalibrated
 * confidence must not read as a measurement.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClassificationResult } from './ClassificationResult';
import type { ClientDetail } from '@avp/shared-types';

const base = {
  id: 'clnt_01M0TESTCLIENT0000000001',
  agencyId: 'agcy_01M0TESTAGENCY000000001',
  name: 'Northaven Dental',
  brandName: 'Northaven Dental',
  domain: 'northaven-dental.com',
  kind: 'prospect',
  industry: 'dental practice',
  industryNiche: 'cosmetic and family dentistry',
  industryConfidence: 'high',
  industryConfidenceScore: '0.97',
  classificationStatus: 'classified',
  classificationReasonCode: null,
  classifiedAt: '2026-08-27T10:00:00Z',
  classifierModel: 'claude-opus-5',
  crawl: { pagesFetched: 3, wordCount: 1840, schemaTypes: ['LocalBusiness'] },
  createdAt: '2026-08-27T10:00:00Z',
  updatedAt: '2026-08-27T10:00:00Z',
} as unknown as ClientDetail;

const as = (o: Partial<Record<string, unknown>>) =>
  ({ ...base, ...o }) as unknown as ClientDetail;

const render = (client: ClientDetail) =>
  renderToStaticMarkup(<ClassificationResult client={client} onReset={() => {}} />);

describe('a classification is stated as a conclusion, not a field list', () => {
  it('reads as a sentence about the business', () => {
    const out = render(base);
    expect(out).toContain('Northaven Dental is a dental practice.');
    // The old shape was a <dl> of Industry / Niche / Confidence — the generic
    // field-list treatment ip-safety.md #3 rules against.
    expect(out).not.toContain('<dl');
    expect(out).not.toContain('>Industry<');
  });

  it('picks the right article so the sentence is grammatical', () => {
    expect(render(as({ industry: 'accounting firm' }))).toContain('is an accounting firm.');
    expect(render(as({ industry: 'dental practice' }))).toContain('is a dental practice.');
  });

  it('carries the niche as supporting detail, and omits it when absent', () => {
    expect(render(base)).toContain('cosmetic and family dentistry');
    expect(render(as({ industryNiche: null }))).not.toContain('More precisely');
  });
});

describe('the confidence number does not pretend to be a measurement', () => {
  it('shows the label but NOT a percentage', () => {
    const out = render(base);
    expect(out).toContain('Model-reported confidence: high');
    // build-log Epic 2.6 Finding 2 measured 0.97/0.97/0.97/0.97/0.96 across five
    // different sites. A number that does not move with its input carries no
    // information; rendering "97%" borrows authority it has not earned.
    expect(out).not.toContain('97%');
    expect(out).not.toContain('0.97');
  });

  it('says plainly why it is a label', () => {
    expect(render(base)).toContain('not calibrated');
  });

  it('omits the line entirely when there is no confidence at all', () => {
    expect(render(as({ industryConfidence: null }))).not.toContain('Model-reported confidence');
  });
});

describe('a non-result must not look like a result', () => {
  it('ambiguous says we could not tell, and states no industry', () => {
    const out = render(as({ classificationStatus: 'ambiguous', industry: null,
      classificationReasonCode: 'LOW_CONFIDENCE' }));
    expect(out).toContain('could not classify this business confidently');
    expect(out).toContain('LOW_CONFIDENCE');
    expect(out).toContain('Needs review');
    // Never a dash in a field that reads like data.
    expect(out).not.toContain('is a .');
    expect(out).not.toContain('>—<');
  });

  it('unclassifiable says what went wrong', () => {
    const out = render(as({ classificationStatus: 'unclassifiable', industry: null,
      classificationReasonCode: 'FETCH_FAILED' }));
    expect(out).toContain('could not read this site');
    expect(out).toContain('FETCH_FAILED');
    expect(out).toContain('Could not read');
  });
});

describe('the screen is not a dead end', () => {
  it('offers a route onward as well as a retry', () => {
    // It previously offered only "Scan another site", so a classified business
    // led nowhere and the next step of the core loop was reachable only by
    // finding the client again on the dashboard.
    const out = render(base);
    expect(out).toContain('Go to your scans');
    expect(out).toContain('Scan another site');
  });

  it('offers RUN A SCAN on a classified client — Epic 9.12', () => {
    // The core loop's actual next step (product-spec.md §3), finally reachable
    // from the screen that produced the classification.
    expect(render(base)).toContain('Run a scan');
  });

  it('does NOT offer a scan when there is no industry to scan against', () => {
    // The industry is what every competitor and prompt is generated from.
    // Spending a scan's worth of model calls on a guess is the one thing the
    // AMBIGUOUS path exists to prevent (Epic 2.3), so the action is withheld
    // rather than offered-and-failed.
    for (const st of ['ambiguous', 'unclassifiable'] as const) {
      const out = render(as({ classificationStatus: st, industry: null }));
      expect(out, st).not.toContain('Run a scan');
      // …but the screen still leads somewhere.
      expect(out, st).toContain('Go to your scans');
      expect(out, st).toContain('Scan another site');
    }
  });

  it('shows no scan-started confirmation before one is started', () => {
    const out = render(base);
    expect(out).not.toContain('Scan started.');
    expect(out).not.toContain('Watch it on your dashboard');
    expect(out).not.toContain('avp-errorstate');
  });

  it('reports what was actually read', () => {
    const out = render(base);
    expect(out).toContain('3 pages');
    expect(out).toContain('1840 words');
    expect(out).toContain('LocalBusiness');
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value or raw-palette utility', () => {
    const out = render(base);
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

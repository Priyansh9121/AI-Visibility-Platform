// @vitest-environment jsdom
/**
 * The intake classification result — Epic 2, restructured in Epic 9.11.
 *
 * The assertions that matter here are about HONESTY, not layout: an ambiguous
 * classification must not read as a result, and the model's uncalibrated
 * confidence must not read as a measurement.
 */

import { describe, it, expect, vi } from 'vitest';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
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
    // field-list treatment the narrative-report rule argues against.
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

  it('unclassifiable by address says the network is not public, not that the site did not load', () => {
    const out = render(as({ classificationStatus: 'unclassifiable', industry: null,
      classificationReasonCode: 'ADDRESS_NOT_PUBLIC' }));
    expect(out).toContain(
      'The site’s address, or an address the site sent the crawler to, is on a private or reserved network, so the crawler did not read the site.',
    );
    expect(out).toContain('Reason: ADDRESS_NOT_PUBLIC');
    expect(out).not.toContain('robots.txt');
    expect(out).not.toContain('did not load');
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

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('when the API does not answer to "Run a scan", the person is told in plain words', () => {
  const AWAY = 'We could not reach the server. Check your connection and try again.';

  async function runWith(fetchImpl: (url: string) => Promise<Response>) {
    vi.stubGlobal('fetch', vi.fn((url: string) => fetchImpl(url)));
    const el = document.createElement('div');
    document.body.appendChild(el);
    const root = createRoot(el);
    act(() => root.render(<ClassificationResult client={base} onReset={() => {}} />));
    await act(async () => {
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
    });
    const button = Array.from(el.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes('Run a scan'),
    )!;
    await act(async () => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
    });
    const text = el.textContent ?? '';
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
    return text;
  }
  const isScan = (url: string) => url.includes('/scans');

  it('a network failure shows the shared sentence and no port number', async () => {
    const text = await runWith(() => Promise.reject(new TypeError('Failed to fetch')));
    expect(text).toContain(AWAY);
    expect(text).not.toContain('8000');
    expect(text).not.toContain('did not respond');
  });

  it('a 5xx without a problem body shows the shared sentence', async () => {
    const text = await runWith((url) =>
      isScan(url)
        ? Promise.resolve(new Response('<html>Bad gateway</html>', { status: 502 }))
        : Promise.reject(new TypeError('Failed to fetch')),
    );
    expect(text).toContain(AWAY);
    expect(text).not.toContain('The server returned 502');
  });

  it('a 4xx problem still shows its own detail', async () => {
    const body = {
      type: '/problems/not-found',
      title: 'Not found',
      status: 404,
      detail: 'That client is gone.',
      instance: '/api/v1/scans',
    };
    const text = await runWith((url) =>
      isScan(url)
        ? Promise.resolve(
            new Response(JSON.stringify(body), {
              status: 404,
              headers: { 'Content-Type': 'application/problem+json' },
            }),
          )
        : Promise.reject(new TypeError('Failed to fetch')),
    );
    expect(text).toContain('That client is gone.');
    expect(text).not.toContain(AWAY);
  });
});

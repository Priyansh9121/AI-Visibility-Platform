// @vitest-environment jsdom
/**
 * The intake classification result — Epic 2, restructured in Epic 9.11.
 *
 * The assertions that matter here are about HONESTY, not layout: an ambiguous
 * classification must not read as a result, and the model's uncalibrated
 * confidence must not read as a measurement.
 */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClassificationResult } from './ClassificationResult';
import type { ClientDetail } from '@avp/shared-types';
import {
  CLASSIFICATION_FAILURE_CODES,
  ROBOTS_UNREACHABLE_ADVICE,
  ROBOTS_UNREACHABLE_SENTENCE,
} from '@/lib/report/strings';
import { SCAN_DURATION_SENTENCE } from '@/lib/scans/scanOutcome';

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
    // The sentence is the reason; the code under it was only its machine's word (3.39).
    expect(out).not.toContain('LOW_CONFIDENCE');
    expect(out).not.toContain('Reason:');
    expect(out).toContain('Needs review');
    // Never a dash in a field that reads like data.
    expect(out).not.toContain('is a .');
    expect(out).not.toContain('>—<');
  });

  it('unclassifiable says what went wrong, in a sentence and not the code', () => {
    const out = render(as({ classificationStatus: 'unclassifiable', industry: null,
      classificationReasonCode: 'FETCH_FAILED' }));
    expect(out).toContain('We could not read this site.');
    expect(out).toContain(
      'The crawler asked for the site’s home page and did not get a page it could read.',
    );
    expect(out).not.toContain('FETCH_FAILED');
    expect(out).toContain('>Could not classify<');
  });

  it('unclassifiable by address says the network is not public, not that the site did not load', () => {
    const out = render(as({ classificationStatus: 'unclassifiable', industry: null,
      classificationReasonCode: 'ADDRESS_NOT_PUBLIC' }));
    expect(out).toContain(
      'The site’s address, or an address the site sent the crawler to, is on a private or reserved network, so the crawler did not read the site.',
    );
    expect(out).not.toContain('ADDRESS_NOT_PUBLIC');
    expect(out).not.toContain('Reason:');
    expect(out).not.toContain('robots.txt');
    expect(out).not.toContain('did not load');
  });
});

describe('unclassifiable because robots.txt could not be read — launch checklist 3.41', () => {
  // Until 2026-10-03 this reached the card as ROBOTS_DISALLOWED. The crawler still
  // reads nothing; the card must say what happened and blame nobody.
  const out = render(as({ classificationStatus: 'unclassifiable', industry: null,
    classificationReasonCode: 'ROBOTS_UNREACHABLE' }));

  it('says what happened in a sentence of its own', () => {
    expect(out).toContain('We could not read this site.');
    expect(out).toContain(ROBOTS_UNREACHABLE_SENTENCE);
    expect(out).toContain(
      'The crawler tried to read the robots.txt of this site, or of an address the site sent it to, and did not get a usable answer. It obeys robots.txt, so without that answer it did not read the site.',
    );
    expect(out).toContain('>Could not classify<');
  });

  it('tells the person who typed the address what they can do, after the sentence', () => {
    expect(out).toContain(
      'Check that the address is right and the site opens in a browser, then try again.',
    );
    expect(out).toContain(`${ROBOTS_UNREACHABLE_SENTENCE} ${ROBOTS_UNREACHABLE_ADVICE}`);
  });

  it('never says the site asks crawlers to stay out, or that it did not load, and shows no code', () => {
    for (const words of [
      'stay out',
      'asks crawlers',
      'not to load',
      'did not load',
      'usually means',
      'Reason:',
      'ROBOTS_UNREACHABLE',
    ]) {
      expect(out, words).not.toContain(words);
    }
  });

  it('every other reason has a sentence of its own, and none of them is this one', () => {
    // Until 3.39 every other state shared "This usually means the site did not load" and
    // a `Reason:` line. The sentence per code is pinned below.
    const other = render(as({ classificationStatus: 'unclassifiable', industry: null,
      classificationReasonCode: 'FETCH_FAILED' }));
    expect(other).toContain(
      'The crawler asked for the site’s home page and did not get a page it could read.',
    );
    expect(other).not.toContain('did not load');
    expect(other).not.toContain('Reason:');
    expect(other).not.toContain(ROBOTS_UNREACHABLE_SENTENCE);
  });

  it('a robots.txt that really forbids the page says so, and is not given this sentence', () => {
    // 3.41 changed only which code an unreadable file is stored under. A file that is read
    // and forbids the page is the site's own word, and the card says that, in its own
    // sentence (3.39) and without the code it used to print under the generic one.
    const forbidden = render(as({ classificationStatus: 'unclassifiable', industry: null,
      classificationReasonCode: 'ROBOTS_DISALLOWED' }));
    expect(forbidden).toContain('We could not read this site.');
    expect(forbidden).toContain('asks crawlers like ours not to load its home page');
    expect(forbidden).not.toContain('ROBOTS_DISALLOWED');
    expect(forbidden).not.toContain('Reason:');
    expect(forbidden).not.toContain(ROBOTS_UNREACHABLE_SENTENCE);
    expect(forbidden).not.toContain(ROBOTS_UNREACHABLE_ADVICE);
  });

  it('still offers a way onward and no scan', () => {
    expect(out).not.toContain('Run a scan');
    expect(out).toContain('Go to your scans');
    expect(out).toContain('Scan another site');
  });
});

describe('an unclassifiable client says what happened, in a sentence, and never the code — launch checklist 3.39', () => {
  // One row per way a client is recorded unclassifiable: the crawl's codes
  // (`services/crawl.py`) and the classifier's own (`services/classify.py`). The sentences
  // are written out here, not read from `strings.ts`, so a change to the copy is a change
  // to this table. `TIMEOUT` is two failures under one code (the crawl's run cap and the
  // classifier's deadline), so its heading and sentence are true of either.
  const READ = 'We could not read this site.';
  const CLASSIFY = 'We could not classify this site.';
  // Three of the model provider's refusals (a key it refused, an account with no credit or
  // quota, a request it rejected) are each a problem on our side, and share one sentence:
  // which of the three it was is not something the person who typed the address can act on.
  const OUR_SIDE =
    'We could not use the model that classifies sites because of a problem on our side, so nothing was classified.';
  const CASES: [code: string, heading: string, sentence: string][] = [
    [
      'ADDRESS_NOT_PUBLIC',
      READ,
      'The site’s address, or an address the site sent the crawler to, is on a private or reserved network, so the crawler did not read the site.',
    ],
    [
      'ROBOTS_UNREACHABLE',
      READ,
      'The crawler tried to read the robots.txt of this site, or of an address the site sent it to, and did not get a usable answer. It obeys robots.txt, so without that answer it did not read the site.',
    ],
    [
      'ROBOTS_DISALLOWED',
      READ,
      'The robots.txt of this site, or of an address the site sent the crawler to, asks crawlers like ours not to load its home page. The crawler obeys robots.txt, so it did not read the site.',
    ],
    ['BROWSER_ERROR', READ, 'The crawler’s browser could not complete a read of the site.'],
    [
      'FETCH_FAILED',
      READ,
      'The crawler asked for the site’s home page and did not get a page it could read.',
    ],
    [
      'HTTP_503',
      READ,
      'The site answered with an error (HTTP 503) when the crawler asked for its home page.',
    ],
    [
      'HTTP_404',
      READ,
      'The site answered with an error (HTTP 404) when the crawler asked for its home page.',
    ],
    [
      'TIMEOUT',
      CLASSIFY,
      'Reading and classifying the site did not finish in the time we allow, so nothing was classified.',
    ],
    [
      'INSUFFICIENT_CONTENT',
      CLASSIFY,
      'The crawler read too little text from this site to tell what business it is, so nothing was classified.',
    ],
    [
      'NOT_A_BUSINESS_SITE',
      CLASSIFY,
      'The model found no operating business it could classify in what the crawler read, so nothing was classified.',
    ],
    ['PROVIDER_AUTH_FAILED', CLASSIFY, OUR_SIDE],
    [
      'PROVIDER_RATE_LIMITED',
      CLASSIFY,
      'The model’s provider asked us to slow down, so nothing was classified.',
    ],
    ['PROVIDER_QUOTA_EXHAUSTED', CLASSIFY, OUR_SIDE],
    ['PROVIDER_BAD_REQUEST', CLASSIFY, OUR_SIDE],
    [
      'PROVIDER_UNREACHABLE',
      CLASSIFY,
      'We could not reach the model’s provider, so nothing was classified.',
    ],
    [
      'PROVIDER_ERROR',
      CLASSIFY,
      'The request to the model’s provider failed, so nothing was classified.',
    ],
    [
      'PROVIDER_SCHEMA_VIOLATION',
      CLASSIFY,
      'The model answered, but not in the form we asked for, so nothing was classified.',
    ],
    ['PROVIDER_REFUSED', CLASSIFY, 'The model declined to answer, so nothing was classified.'],
    [
      'PROVIDER_NO_OUTPUT',
      CLASSIFY,
      'The model did not return an answer we could read, so nothing was classified.',
    ],
  ];
  // ALL_CAPS_WITH_UNDERSCORES is how every one of these is spelled. Single words
  // (`TIMEOUT`) are checked by name.
  const MACHINE_CODE = /\b[A-Z]{3,}_[A-Z0-9_]+\b/;
  const unclassifiable = (code: string | null) =>
    as({ classificationStatus: 'unclassifiable', industry: null, classificationReasonCode: code });

  it('the table has a row for every code the card words, and a sentence of its own for each but three', () => {
    const wordedCodes = CASES.map(([code]) => code).filter((code) => !code.startsWith('HTTP_'));
    expect([...wordedCodes].sort()).toEqual([...CLASSIFICATION_FAILURE_CODES].sort());
    // Every sentence is its own except the one three codes share (round 2 of 3.39).
    const sharers = CASES.filter(([, , sentence]) => sentence === OUR_SIDE).map(([code]) => code);
    expect(sharers.sort()).toEqual(['PROVIDER_AUTH_FAILED', 'PROVIDER_BAD_REQUEST', 'PROVIDER_QUOTA_EXHAUSTED']);
    expect(new Set(CASES.map(([, , sentence]) => sentence)).size).toBe(CASES.length - 2);
  });

  it.each(CASES)('%s: the heading and the sentence, once, and neither the code nor the old guess', (code, heading, sentence) => {
    const out = render(unclassifiable(code));
    expect(out).toContain(heading);
    expect(out.split(sentence).length - 1).toBe(1);
    expect(out).not.toContain(code);
    expect(out.match(MACHINE_CODE)).toBeNull();
    for (const words of ['Reason:', 'did not load', 'usually means', 'carried no description']) {
      expect(out, words).not.toContain(words);
    }
    // Still no scan to run.
    expect(out).not.toContain('Run a scan');
  });

  it('the status badge says "Could not classify" under every heading, because that is true under both', () => {
    // It said "Could not read", which the card's own sentence contradicts for every client
    // whose site the crawler read and the model step then failed (round 2 of 3.39). The
    // Clients list says the same (`ClientsView.test.tsx`).
    for (const [code] of [...CASES, [null]] as [string | null][]) {
      const out = render(unclassifiable(code));
      expect(out, String(code)).toContain('<span class="avp-badge avp-badge--danger">Could not classify</span>');
      expect(out, String(code)).not.toContain('>Could not read<');
    }
    // Both headings, with the badge beside them.
    const crawler = render(unclassifiable('FETCH_FAILED'));
    const model = render(unclassifiable('PROVIDER_REFUSED'));
    expect(crawler).toContain(READ);
    expect(model).toContain(CLASSIFY);
    for (const out of [crawler, model]) expect(out).toContain('>Could not classify<');
  });

  it('only a crawler that could not read the site says so: a failure of the model step says it could not classify', () => {
    for (const [code, heading] of CASES) {
      const out = render(unclassifiable(code));
      const other = heading === READ ? CLASSIFY : READ;
      expect(out, code).not.toContain(other);
    }
    // The model step is not the site: none of its sentences speak of this site or the site
    // (the one that says "sites" says what the model classifies), and the heading above
    // them does not say the site could not be read.
    for (const [code, , sentence] of CASES.filter(([c]) => c.startsWith('PROVIDER_'))) {
      expect(sentence, code).not.toMatch(/\b(this|the) site\b/i);
    }
  });

  it.each(['TIMEOUT', 'BROWSER_ERROR', 'BROWSER_BUSY', ...CASES.map(([c]) => c).filter((c) => c.startsWith('PROVIDER_'))])(
    '%s is a failure on our side, and never says the site did not load',
    (code) => {
      const out = render(unclassifiable(code));
      expect(out).not.toMatch(/did not load|would not load|failed to load|is down|not responding|unreachable site/i);
      expect(out).not.toContain('usually means');
      expect(out).not.toContain(code);
    },
  );

  it.each([['SOMETHING_NEW'], ['HTTP_200'], ['HTTP_OK'], [null]])(
    'a code the card has no wording for (%s) says nothing it cannot support, and shows no code',
    (code) => {
      const out = render(unclassifiable(code));
      expect(out).toContain('We could not classify this site.');
      expect(out).toContain('Nothing was classified, and we have no more specific reason to give.');
      expect(out).not.toContain('did not load');
      expect(out).not.toContain('Reason:');
      if (code !== null) expect(out).not.toContain(code);
    },
  );

  it('BROWSER_BUSY has no wording of its own, because no client can carry it', () => {
    // Since M1 a read that never got the crawler's one browser writes nothing and answers
    // 429 (`intake.run_classification`), so the code cannot reach this card. If one ever
    // did, it would read as any code the card does not know: nothing about the site.
    expect(CLASSIFICATION_FAILURE_CODES).not.toContain('BROWSER_BUSY');
    const out = render(unclassifiable('BROWSER_BUSY'));
    expect(out).toContain('Nothing was classified, and we have no more specific reason to give.');
    expect(out).not.toContain('BROWSER_BUSY');
  });
});

describe('the card words every code the API can record — read back from its source', () => {
  // The codes are read out of the API's own files, so a code added there without wording
  // here fails this suite instead of reaching a reader as `Reason: <code>` (or, now, as
  // the generic sentence, which is true but says less).
  // `__dirname`, not `import.meta.url`: this file runs under jsdom, where the latter is not a file URL.
  const ROOT = join(__dirname, '../../../..');
  const read = (path: string): string => readFileSync(join(ROOT, path), 'utf8');
  const SERVICES = 'apps/api/src/avp_api/services/';

  /** A one-line string constant in a Python source, exactly as written there. */
  function pythonString(file: string, name: string): string {
    const value = new RegExp(`^${name} = "([^"\\n]*)"$`, 'm').exec(read(`${SERVICES}${file}`))?.[1];
    expect(value, `${name} is not a one-line string in ${file}`).toBeDefined();
    return value!;
  }

  const literals = (file: string, key: 'reason_code' | 'error_code'): string[] =>
    [...read(`${SERVICES}${file}`).matchAll(new RegExp(`${key}="([A-Z][A-Z0-9_]+)"`, 'g'))].map(
      (m) => m[1]!,
    );

  // `LOW_CONFIDENCE` is the one reason an AMBIGUOUS client carries; the card's ambiguous
  // sentence is its words. `BROWSER_BUSY` is refused before anything is written.
  const AMBIGUOUS = ['LOW_CONFIDENCE'];
  const NEVER_RECORDED = ['BROWSER_BUSY'];

  const recorded = new Set<string>([
    // classify.py: the model step's own reasons, and the unclassifiable ones it decides.
    ...literals('classify.py', 'reason_code'),
    // crawl.py: the literals, and the constants it records (`_robots_refused`, `_address_not_public`).
    ...literals('crawl.py', 'error_code'),
    pythonString('crawl.py', 'ROBOTS_DISALLOWED'),
    pythonString('crawl.py', 'ROBOTS_UNREACHABLE'),
    pythonString('egress.py', 'ADDRESS_NOT_PUBLIC'),
    pythonString('browser_slot.py', 'BROWSER_BUSY'),
  ]);

  it('finds the codes it is meant to find, so an empty read cannot pass', () => {
    for (const code of ['TIMEOUT', 'BROWSER_ERROR', 'FETCH_FAILED', 'INSUFFICIENT_CONTENT', 'PROVIDER_NO_OUTPUT']) {
      expect(recorded.has(code), code).toBe(true);
    }
    expect(recorded.size).toBeGreaterThanOrEqual(18);
  });

  it('every code the API records for an unclassifiable client has a sentence', () => {
    const unworded = [...recorded].filter(
      (code) =>
        !AMBIGUOUS.includes(code) &&
        !NEVER_RECORDED.includes(code) &&
        !CLASSIFICATION_FAILURE_CODES.includes(code),
    );
    expect(unworded).toEqual([]);
  });

  it('every sentence is for a code the API still records, so no wording is left for a code that is gone', () => {
    const gone = CLASSIFICATION_FAILURE_CODES.filter((code) => !recorded.has(code));
    expect(gone).toEqual([]);
  });

  it('`HTTP_<status>` is still how the crawl records a home page that answered with an error', () => {
    expect(read(`${SERVICES}crawl.py`)).toContain('error_code=f"HTTP_{home[\'status\']}"');
  });

  it('a read that never got the browser still writes nothing: it is refused before the outcome is applied', () => {
    // The claim in the comment on `Unclassifiable`, and the reason BROWSER_BUSY has no copy.
    const source = read(`${SERVICES}intake.py`);
    const refused = source.indexOf('if crawl.error_code == BROWSER_BUSY:');
    expect(refused).toBeGreaterThan(-1);
    expect(source.indexOf('raise crawler_busy()', refused)).toBeGreaterThan(refused);
    expect(source.indexOf('raise crawler_busy()', refused)).toBeLessThan(
      source.indexOf('apply_outcome(client, outcome)', refused),
    );
    expect(source.indexOf('raise crawler_busy()', refused)).toBeLessThan(
      source.indexOf('await classify(crawl', refused),
    );
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

  it('a started scan says how long it takes in the one sentence, and never "five minutes"', async () => {
    // The hint said "This takes under five minutes", which a scan's own range does not
    // keep to (launch checklist 3.39). The sentence is the one the dashboard and the
    // getting-started checklist use.
    const text = await runWith((url) =>
      isScan(url)
        ? Promise.resolve(
            new Response(JSON.stringify({ id: 'scan_01M0TESTSCAN000000000001' }), {
              status: 201,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        : Promise.reject(new TypeError('Failed to fetch')),
    );
    expect(text).toContain('Scan started.');
    expect(text).toContain(`${SCAN_DURATION_SENTENCE} You can leave this page — it keeps running.`);
    expect(text.toLowerCase()).not.toContain('five minutes');
    expect(text).not.toContain('under five');
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

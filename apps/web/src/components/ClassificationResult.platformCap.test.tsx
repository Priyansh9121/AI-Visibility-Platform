// @vitest-environment jsdom

/**
 * "Run a scan" refused by the platform's daily cap shows the API's own sentence — D2.
 *
 * The cap answers with a 503, which `apiWasAway` used to read as "the server is away"; this
 * mounts the card with the API mocked at its edge, presses the button, and reads the alert.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ClientDetail } from '@avp/shared-types';

const runScanCall = vi.fn<(clientId: string) => Promise<{ id: string }>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      runScan: (clientId: string) => runScanCall(clientId),
      // The allowance label beside the button is not under test; both reads fail quietly.
      billing: () => Promise.reject(new Error('not under test')),
      me: () => Promise.reject(new Error('not under test')),
    },
  };
});

const { ApiProblem } = await import('@/lib/api');
const { API_AWAY_SENTENCE } = await import('@/lib/apiAway');
const { ClassificationResult } = await import('./ClassificationResult');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const CAP_SENTENCE =
  'AVP has started as many scans as it allows in a day, so this one was not started. ' +
  'Try again after midnight UTC.';

const client = {
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

let root: Root | null = null;

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  runScanCall.mockReset();
});

describe('a start the daily cap refuses says so in the API’s words', () => {
  it('shows the cap sentence in the alert, never the "server is away" text', async () => {
    runScanCall.mockRejectedValue(
      new ApiProblem({
        type: '/problems/platform-cap-reached',
        title: 'Daily scan limit reached',
        status: 503,
        detail: CAP_SENTENCE,
        instance: '/api/v1/clients/clnt_01M0TESTCLIENT0000000001/scans',
      }),
    );
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    await act(async () => root!.render(<ClassificationResult client={client} onReset={() => {}} />));

    const button = [...el.querySelectorAll('button')].find((b) => b.textContent === 'Run a scan');
    expect(button).toBeDefined();
    await act(async () => button!.click());

    expect(runScanCall).toHaveBeenCalledWith(client.id);
    const alert = el.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(CAP_SENTENCE);
    expect(el.textContent).not.toContain(API_AWAY_SENTENCE);
    el.remove();
  });
});

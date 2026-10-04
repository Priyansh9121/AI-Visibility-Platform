import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

/**
 * `billingPlan` — what the plan offers the public, today the length of its free trial
 * (2026-10-04). A READ and only a read: the public pricing card and the landing page call it
 * before anybody has signed up, so it must be a GET to the one URL the API serves, carry no
 * body, name no agency, and put nothing in the path that is not the route.
 */
describe('billingPlan', () => {
  it('is a GET to /api/v1/billing/plan, with no body, and returns what the API says', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ trialDays: 30 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.billingPlan()).resolves.toEqual({ trialDays: 30 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    // Exactly the route: no agency id, no query string, no trailing path.
    expect(String(url)).toBe('/api/v1/billing/plan');
    // A GET: `fetch` defaults to one when no method is set, and a write is never what this is.
    expect(init.method ?? 'GET').toBe('GET');
    expect(init.body).toBeUndefined();
    // The shared request helper's own rule, same-origin through the web app's `/api/v1` proxy.
    expect(init.credentials).toBe('include');
  });

  it('is the same single route however many times it is called: nothing in it varies', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => new Response(JSON.stringify({ trialDays: 0 }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await api.billingPlan();
    await api.billingPlan();

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/billing/plan',
      '/api/v1/billing/plan',
    ]);
  });

  it('throws the API problem on a refusal, which `loadTrialDays` turns into "no trial"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: '/problems/service-unavailable',
            title: 'Unavailable',
            status: 503,
            detail: 'Try again.',
            instance: '/api/v1/billing/plan',
          }),
          { status: 503, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    await expect(api.billingPlan()).rejects.toMatchObject({ status: 503 });
  });
});

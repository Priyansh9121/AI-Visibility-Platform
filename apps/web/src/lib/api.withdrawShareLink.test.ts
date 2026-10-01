import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('withdrawShareLink', () => {
  it('sends DELETE to /scans/{id}/share with credentials and accepts a 204', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(api.withdrawShareLink('scan_01ABC')).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/\/scans\/scan_01ABC\/share$/);
    expect(init.method).toBe('DELETE');
    expect(init.credentials).toBe('include');
  });

  it('throws the API problem on a refusal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ type: '/problems/not-found', title: 'Not found', status: 404, detail: 'No.', instance: '/x' }),
          { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    await expect(api.withdrawShareLink('scan_01ABC')).rejects.toMatchObject({ status: 404 });
  });
});

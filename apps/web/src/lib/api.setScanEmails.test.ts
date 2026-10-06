import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('setScanEmails', () => {
  it('PUTs the choice, alone, to /auth/me/scan-emails with credentials', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ available: true, enabled: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.setScanEmails(false)).resolves.toEqual({ available: true, enabled: false });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe('/api/v1/auth/me/scan-emails');
    expect(init.method).toBe('PUT');
    expect(init.credentials).toBe('include');
    expect(JSON.parse(init.body)).toEqual({ enabled: false });
  });

  it('throws the API problem on a refusal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ type: '/problems/authentication-required', title: 'No', status: 401, detail: 'No.', instance: '/x' }),
          { status: 401, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    await expect(api.setScanEmails(true)).rejects.toMatchObject({ status: 401 });
  });
});

/**
 * The Google sign-up ticket travels in a POST body, never in a URL — the API
 * half of moving it into the URL fragment.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from './api';

const TICKET = 'GOOGLETICKETsentinel0123456789abcdef';

afterEach(() => vi.unstubAllGlobals());

describe('googlePending', () => {
  it('POSTs the ticket in a JSON body and puts none in the URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ email: 'a@b.example', suggestedName: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    await api.googlePending(TICKET);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/\/auth\/google\/pending$/);
    expect(String(url)).not.toContain(TICKET);
    expect(String(url)).not.toContain('?');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ ticket: TICKET });
  });
});

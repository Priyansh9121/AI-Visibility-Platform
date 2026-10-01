/**
 * The share token travels in a POST body, never in a URL — the API half of
 * moving tokens into the URL fragment. A token in a URL lands in access logs;
 * a body does not.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from './api';

const TOKEN = 'SHARETOKENsentinel0123456789abcdef';

afterEach(() => vi.unstubAllGlobals());

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('publicReport', () => {
  it('POSTs the token in a JSON body and puts none in the URL', async () => {
    const fetchMock = stubFetch(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await api.publicReport(TOKEN);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/\/reports\/shared$/);
    expect(String(url)).not.toContain(TOKEN);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ token: TOKEN });
  });
});

describe('publicReportPdf', () => {
  it('POSTs the token in a JSON body, puts none in the URL, and returns a Blob', async () => {
    const fetchMock = stubFetch(
      new Response(new Blob(['%PDF-1.4'], { type: 'application/pdf' }), {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="acme-ai-visibility.pdf"' },
      }),
    );
    const blob = await api.publicReportPdf(TOKEN);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/\/reports\/shared\/pdf$/);
    expect(String(url)).not.toContain(TOKEN);
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual({ token: TOKEN });
    expect(blob).toBeInstanceOf(Blob);
    expect((blob as File).name).toBe('acme-ai-visibility.pdf');
  });

  it('throws the API problem on a 404, as every other call does', async () => {
    stubFetch(
      new Response(
        JSON.stringify({ type: '/problems/not-found', title: 'Not found', status: 404, detail: 'No.', instance: '/x' }),
        { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
      ),
    );
    await expect(api.publicReportPdf(TOKEN)).rejects.toMatchObject({ status: 404 });
  });

  it('a signed-in report PDF stays a GET by scan id', async () => {
    const fetchMock = stubFetch(new Response(new Blob(['x']), { status: 200 }));
    await api.reportPdf('scan-1');
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/\/scans\/scan-1\/report\.pdf$/);
    expect(init.method).toBe('GET');
  });
});

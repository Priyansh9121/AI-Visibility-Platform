/**
 * `api.reportCsv` — F3, 2026-10-06: the report's figures, as bytes, for a signed-in agency.
 *
 * A GET by scan id with the session cookie, like the PDF. What is held here is the part of the
 * contract the page leans on: the file arrives with the server's own name, the bytes are not
 * decoded on the way (the byte-order mark that makes a spreadsheet read UTF-8 must survive), and a
 * refusal is thrown as the same `ApiProblem` every other call throws.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { api, ApiProblem } from './api';

afterEach(() => vi.unstubAllGlobals());

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const BOM = [0xef, 0xbb, 0xbf];

describe('reportCsv', () => {
  it('is a GET by scan id, with the session, and puts no token anywhere', async () => {
    const fetchMock = stubFetch(new Response(new Blob(['x']), { status: 200 }));
    await api.reportCsv('scan_01ABC');
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe('/api/v1/scans/scan_01ABC/report.csv');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('include');
    expect(init.body).toBeUndefined();
    expect(JSON.stringify(init)).not.toMatch(/token/i);
  });

  it('returns the file under the name the server chose, bytes untouched', async () => {
    const bytes = new Uint8Array([...BOM, ...new TextEncoder().encode('section,engine\r\n')]);
    stubFetch(
      new Response(new Blob([bytes], { type: 'text/csv; charset=utf-8' }), {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="Help-Scout-ai-visibility-2026-10-01.csv"' },
      }),
    );
    const file = await api.reportCsv('scan_01ABC');
    expect(file).toBeInstanceOf(File);
    expect((file as File).name).toBe('Help-Scout-ai-visibility-2026-10-01.csv');
    expect(file.type).toContain('text/csv');
    const got = new Uint8Array(await file.arrayBuffer());
    expect([...got.slice(0, 3)]).toEqual(BOM);
    expect(got.length).toBe(bytes.length);
  });

  it('throws the 409 the API sends for a scan with no figures, with its sentence', async () => {
    stubFetch(
      new Response(
        JSON.stringify({
          type: '/problems/scan-not-exportable',
          title: "This scan's figures cannot be exported",
          status: 409,
          detail: 'This scan is still running, so its figures are not final.',
          instance: '/api/v1/scans/scan_01ABC/report.csv',
        }),
        { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
      ),
    );
    const caught = await api.reportCsv('scan_01ABC').catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(ApiProblem);
    const problem = caught as ApiProblem;
    expect(problem.status).toBe(409);
    expect(problem.type).toBe('/problems/scan-not-exportable');
    expect(problem.problem.detail).toBe('This scan is still running, so its figures are not final.');
  });

  it('throws a 404 as the API sends it when the switch is off', async () => {
    stubFetch(
      new Response(
        JSON.stringify({
          type: '/problems/not-found',
          title: 'Resource not found',
          status: 404,
          detail: 'Not Found',
          instance: '/api/v1/scans/scan_01ABC/report.csv',
        }),
        { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
      ),
    );
    await expect(api.reportCsv('scan_01ABC')).rejects.toMatchObject({ status: 404 });
  });
});

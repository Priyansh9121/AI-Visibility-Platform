// @vitest-environment jsdom

/**
 * What the PDF button says when the download fails. The signed-in route shows the problem's own
 * detail. The public (share-token) route shows it only for 429 and 503, which say nothing about
 * the link, and the generic sentence for anything else.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const publicReportPdf = vi.fn();
const reportPdf = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      publicReportPdf: (t: string) => publicReportPdf(t),
      reportPdf: (id: string) => reportPdf(id),
    },
  };
});

const { DownloadPdfButton } = await import('./DownloadPdfButton');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const GENERIC = 'The PDF could not be prepared. Try again.';
const DETAIL = 'A sentence only the API wrote.';

const problem = (status: number) =>
  new ApiProblem({
    type: '/problems/x',
    title: 'Title',
    status,
    detail: DETAIL,
    instance: '/api/v1/x',
  });

let root: Root | null = null;

async function clickWith(props: { token?: string; scanId?: string }): Promise<string | null> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(<DownloadPdfButton subjectName="Help Scout" {...props} />);
  });
  await act(async () => {
    el.querySelector('button')!.click();
  });
  await act(async () => {
    await Promise.resolve();
  });
  return el.querySelector('[role="alert"]')?.textContent ?? null;
}

beforeEach(() => {
  publicReportPdf.mockReset();
  reportPdf.mockReset();
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('the public route', () => {
  it.each([429, 503])('shows the problem detail for a %i', async (status) => {
    publicReportPdf.mockRejectedValue(problem(status));
    expect(await clickWith({ token: 'tok' })).toBe(DETAIL);
  });

  it.each([400, 404, 422, 500, 502])('shows only the generic sentence for a %i', async (status) => {
    publicReportPdf.mockRejectedValue(problem(status));
    expect(await clickWith({ token: 'tok' })).toBe(GENERIC);
  });
});

describe('the signed-in route', () => {
  it.each([404, 429, 500])('keeps showing the problem detail for a %i', async (status) => {
    reportPdf.mockRejectedValue(problem(status));
    expect(await clickWith({ scanId: 'scan_1' })).toBe(DETAIL);
  });
});

// @vitest-environment jsdom

/**
 * /share when the link does not open a report — the UI review of 2026-10-01.
 *
 * Every failure is one failure here, on purpose: the API answers 404 for a
 * malformed, unknown, revoked and EXPIRED token alike, so the page cannot say
 * which, and distinguishing a 404 from a 500 would tell a guesser which of their
 * guesses was shaped like a real token. What it can say is the list of things
 * that could be wrong, and that list used to leave out "expired" — the one a
 * person who kept a link for a month is most likely to be holding (links expire
 * after 30 days).
 *
 * The real route in jsdom, the API client mocked at its edge, the address bar
 * left real. Only the sentence is under test: the page's headers and behaviour
 * are `referrerPolicy.test.ts`'s and `fragmentRoutes.test.tsx`'s.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const publicReport = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: { ...real.api, publicReport: (token: string) => publicReport(token) },
  };
});

const { default: PublicReportRoute } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TITLE = 'This report link is not available';
const DETAIL =
  'The link may be incomplete, expired or withdrawn. Ask whoever sent it for a current one.';

const problem = (status: number) =>
  new ApiProblem({
    type: '/problems/not-found',
    title: 'Not found',
    status,
    detail: 'No report with that link.',
    instance: '/api/v1/reports/shared',
  });

let root: Root | null = null;

async function openAt(hash: string): Promise<HTMLDivElement> {
  window.history.replaceState(null, '', `/share${hash}`);
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(<PublicReportRoute />);
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  return el;
}

beforeEach(() => {
  publicReport.mockReset();
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  window.history.replaceState(null, '', '/');
});

describe('a link that does not open a report', () => {
  it('says it may be incomplete, expired or withdrawn', async () => {
    publicReport.mockRejectedValue(problem(404));
    const el = await openAt('#sometoken');
    expect(publicReport).toHaveBeenCalledWith('sometoken');
    expect(el.textContent).toContain(TITLE);
    expect(el.textContent).toContain(DETAIL);
  });

  it('says the same with no fragment at all, without asking the API', async () => {
    const el = await openAt('');
    expect(publicReport).not.toHaveBeenCalled();
    expect(el.textContent).toContain(TITLE);
    expect(el.textContent).toContain(DETAIL);
  });

  it.each([
    ['a 404', problem(404)],
    ['a 500', problem(500)],
    ['no answer at all', new TypeError('Failed to fetch')],
  ])('says the same after %s: one failure, so a guess learns nothing', async (_what, failure) => {
    publicReport.mockRejectedValue(failure);
    const el = await openAt('#sometoken');
    expect(el.textContent).toContain(TITLE);
    expect(el.textContent).toContain(DETAIL);
    // And not which of them it was.
    expect(el.textContent).not.toContain('No report with that link');
  });
});

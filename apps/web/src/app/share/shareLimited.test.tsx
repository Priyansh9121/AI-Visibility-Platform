// @vitest-environment jsdom

/**
 * /share when this network has asked too often — F5c.
 *
 * A 429 (this network asked too often) and a 503 (unavailable for now) say nothing about the
 * link. Each gets its own screen with a Reload, not the "link not available" one, which would
 * send a person to ask for a new link they do not need.
 *
 * The real route in jsdom, the API client mocked at its edge, the address bar
 * left real. Only the sentence is under test: the page's headers and behaviour
 * are `referrerPolicy.test.ts`'s and `fragmentRoutes.test.tsx`'s.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
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
const LIMITED = 'Too many requests from your network';
const LIMITED_DETAIL = 'Wait a few minutes, then reload this page.';
const UNAVAILABLE = 'This is temporarily unavailable';
const UNAVAILABLE_DETAIL = 'Please try again shortly.';

const problem = (status: number) =>
  new ApiProblem(
    status === 429
      ? {
          type: '/problems/rate-limited',
          title: 'Too many requests',
          status,
          detail: 'Too many attempts. Try again in 3 minutes.',
          instance: '/api/v1/reports/shared',
        }
      : {
          type: status === 503 ? '/problems/service-unavailable' : '/problems/not-found',
          title: status === 503 ? 'Service unavailable' : 'Not found',
          status,
          detail: 'No report with that link.',
          instance: '/api/v1/reports/shared',
        },
  );

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

const reloadButton = (el: HTMLElement) =>
  Array.from(el.querySelectorAll('button')).find((b) => b.textContent === 'Reload');

describe('a 429 from the API', () => {
  it('says the network asked too often, not that the link is bad, and offers Reload', async () => {
    publicReport.mockRejectedValue(problem(429));
    const el = await openAt('#sometoken');
    const alert = el.querySelector('main [role="alert"]');
    expect(alert?.textContent).toContain(LIMITED);
    expect(alert?.textContent).toContain(LIMITED_DETAIL);
    expect(el.textContent).not.toContain(TITLE);
    expect(el.textContent).not.toContain('expired or withdrawn');
    expect(reloadButton(el)).toBeDefined();
  });

  it('the Reload button calls location.reload (jsdom cannot spy on it, so the source is read)', () => {
    // jsdom's `location.reload` cannot be replaced, so the wiring is pinned in the source.
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'page.tsx'), 'utf8');
    expect(src).toContain('onClick={() => window.location.reload()}');
  });
});

describe('a 503 from the API', () => {
  it('says it is temporarily unavailable, not that the link is bad, and offers Reload', async () => {
    publicReport.mockRejectedValue(problem(503));
    const el = await openAt('#sometoken');
    const alert = el.querySelector('main [role="alert"]');
    expect(alert?.textContent).toContain(UNAVAILABLE);
    expect(alert?.textContent).toContain(UNAVAILABLE_DETAIL);
    expect(el.textContent).not.toContain(TITLE);
    expect(el.textContent).not.toContain('Ask whoever sent it');
    expect(reloadButton(el)).toBeDefined();
  });
});

describe('everything else is still the link-not-available screen', () => {
  it.each([404, 500, 502])('a %i', async (status) => {
    publicReport.mockRejectedValue(problem(status));
    const el = await openAt('#sometoken');
    expect(el.querySelector('main [role="alert"]')?.textContent).toContain(TITLE);
    expect(el.textContent).not.toContain(LIMITED);
    expect(el.textContent).not.toContain(UNAVAILABLE);
  });
});

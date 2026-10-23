// @vitest-environment jsdom

/**
 * Sign out, from the real API client to the footer — copy fixes, 1e.
 *
 * `WorkspaceShell.signOut.test.tsx` mocks `api.logOut` and hands the shell a problem it made
 * itself, so what it cannot show is the thing the shell's 401 rule rests on: that the client
 * really does turn the API's 401 into an `ApiProblem` whose `status` is 401, whatever the body
 * is. This file leaves `@/lib/api` alone and puts a `fetch` under it, as `lib/api.*.test.ts` does,
 * so the answer travels the whole way: a response, `request()`, the shell's handler, and the
 * footer (or the home page).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { WorkspaceShell } from './WorkspaceShell';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SENTENCE = 'Sign-out did not complete. Try again.';

const assign = vi.fn<(url: string) => void>();
const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();

let root: Root | null = null;

beforeEach(() => {
  assign.mockReset();
  fetchMock.mockReset();
  window.sessionStorage.clear();
  vi.stubGlobal('location', { assign });
  vi.stubGlobal('fetch', fetchMock);
  // jsdom lays nothing out and scrolls nothing, and has no `scrollIntoView`.
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    value: vi.fn(),
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

function mounted(): HTMLDivElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => {
    root!.render(
      <WorkspaceShell current="dashboard" agencyName="Local Dev Agency 2">
        <p>the page</p>
      </WorkspaceShell>,
    );
  });
  return el;
}

async function pressSignOut(el: HTMLElement): Promise<void> {
  const button = [...el.querySelectorAll<HTMLButtonElement>('.avp-shell__footer button')].find(
    (b) => b.textContent === 'Sign out',
  )!;
  act(() => {
    button.click();
  });
  await flush();
}

const said = (el: HTMLElement) =>
  [...el.querySelectorAll('.avp-shell__footer [role="alert"] p')].map((p) => p.textContent);

const problemBody = (status: number, type: string) =>
  JSON.stringify({ type, title: 'Problem', status, detail: 'A sentence.', instance: '/api/v1/auth/logout' });
const PROBLEM_JSON = { 'Content-Type': 'application/problem+json' };

describe('what the API answers to POST /auth/logout, through the real client', () => {
  it('asks the way the client always has: a POST to /api/v1/auth/logout, with the cookie', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const el = mounted();

    await pressSignOut(el);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe('/api/v1/auth/logout');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
  });

  it('a 204 goes to the home page', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const el = mounted();

    await pressSignOut(el);

    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/');
    expect(said(el)).toEqual([]);
  });

  const SIGNED_OUT_ALREADY: Array<[string, () => Response]> = [
    [
      'a 401 with the API’s problem document',
      () =>
        new Response(problemBody(401, '/problems/authentication-required'), {
          status: 401,
          headers: PROBLEM_JSON,
        }),
    ],
    ['a 401 with an HTML body (a proxy’s)', () => new Response('<h1>Unauthorized</h1>', { status: 401 })],
    ['a 401 with no body at all', () => new Response(null, { status: 401 })],
    ['a 401 with JSON that is not a problem document', () => new Response('{"ok":false}', { status: 401 })],
  ];

  it.each(SIGNED_OUT_ALREADY)('%s counts as signed out and goes to the home page', async (_what, answer) => {
    fetchMock.mockResolvedValue(answer());
    const el = mounted();

    await pressSignOut(el);

    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/');
    expect(said(el)).toEqual([]);
  });

  const FAILED: Array<[string, () => Promise<Response>]> = [
    [
      'a 503 with the API’s problem document (Redis is down)',
      async () =>
        new Response(problemBody(503, '/problems/service-unavailable'), {
          status: 503,
          headers: PROBLEM_JSON,
        }),
    ],
    [
      'a 403 with the API’s problem document (the origin was refused)',
      async () =>
        new Response(problemBody(403, '/problems/origin-not-allowed'), {
          status: 403,
          headers: PROBLEM_JSON,
        }),
    ],
    ['a 500 with a plain-text body', async () => new Response('Internal Server Error', { status: 500 })],
    ['a 502 with an HTML body (a proxy’s)', async () => new Response('<h1>Bad gateway</h1>', { status: 502 })],
    ['a 500 with no body at all', async () => new Response(null, { status: 500 })],
    [
      'no answer at all: the network failed',
      () => Promise.reject(new TypeError('Failed to fetch')),
    ],
  ];

  it.each(FAILED)('%s says so, and the person stays where they are', async (_what, answer) => {
    fetchMock.mockImplementation(answer);
    const el = mounted();

    await pressSignOut(el);

    expect(said(el)).toEqual([SENTENCE]);
    expect(assign).not.toHaveBeenCalled();
    expect(el.textContent).toContain('the page');
  });
});

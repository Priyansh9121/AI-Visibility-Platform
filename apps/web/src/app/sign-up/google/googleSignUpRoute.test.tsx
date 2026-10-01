// @vitest-environment jsdom

/**
 * /sign-up/google, mounted — founder decision 6, 2026-10-01.
 *
 * One "expired" screen used to stand for two different things: the API
 * refusing a ticket, and a page that has no ticket to ask about. The second is
 * what a reload does, because the ticket is taken out of the address bar once
 * it is read, so it now says that, in the words /invite and /reset-password
 * use for the same thing. The panel's own test pins each state's copy; what
 * only a mount can show is which state each arrival lands on.
 *
 * The real route in jsdom, with the API client mocked at its edge
 * (`home.test.tsx`'s pattern) and the address bar left real, because reading
 * the ticket out of it and clearing it afterwards is the thing under test.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { GooglePending } from '@avp/shared-types';

const googlePending = vi.fn<(ticket: string) => Promise<GooglePending>>();
const assign = vi.fn<(url: string) => void>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: { ...real.api, googlePending: (ticket: string) => googlePending(ticket) },
  };
});

// Imported after the mock so the route sees the mocked module.
const { default: GoogleSignUpRoute } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NO_CODE = {
  title: 'This sign-in link is not valid',
  detail:
    "This page no longer has the link's code (it is removed from the address bar once read). Start again from the sign-in page.",
};
const EXPIRED = {
  title: 'That Google sign-in has expired',
  detail: 'It works for ten minutes and once. Start again from the sign-in page.',
};

const pending: GooglePending = { email: 'dana@northlight.example', suggestedName: 'Dana Whitfield' };

/** What the API answers to a ticket it does not know, has spent, or has let lapse. */
const refused = () =>
  new ApiProblem({
    type: '/problems/invalid-google-ticket',
    title: 'That Google sign-in has expired',
    status: 400,
    detail: 'That Google sign-in has expired. Start again from the sign-in page.',
    instance: '/api/v1/auth/google/pending',
  });

/** Let every pending microtask settle: the mocked peek resolves on the next tick. */
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

let root: Root | null = null;

/** Be at /sign-up/google with this fragment, the way Google's callback redirect leaves the browser. */
function open(hash: string): void {
  window.history.replaceState(null, '', `/sign-up/google${hash}`);
}

async function mount(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<GoogleSignUpRoute />));
  await settle();
  return el;
}

function unmount(): void {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
}

/** The refusal card's two sentences, as a person reads them: title, then detail. */
const refusal = (el: HTMLElement) =>
  [...el.querySelectorAll('[role="alert"] p')].map((p) => p.textContent);

const button = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll('button')].find((b) => b.textContent === label);

beforeEach(() => {
  googlePending.mockReset();
  assign.mockReset();
  // jsdom does not navigate and its `location` cannot be spied on in place, so
  // `assign` is a spy; everything else reads through to the real address bar.
  const real = window.location;
  vi.stubGlobal('location', {
    get hash() {
      return real.hash;
    },
    get search() {
      return real.search;
    },
    get pathname() {
      return real.pathname;
    },
    assign,
  });
});

afterEach(() => {
  unmount();
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});

describe('a page opened with no ticket in the fragment', () => {
  it("says it no longer has the link's code, and never asks the API", async () => {
    open('');
    const el = await mount();
    expect(refusal(el)).toEqual([NO_CODE.title, NO_CODE.detail]);
    expect(googlePending).not.toHaveBeenCalled();
  });

  it('is what a reload gets: the first visit takes the ticket out of the address bar, so the second has none', async () => {
    googlePending.mockResolvedValue(pending);
    open('#ticket=abc_DEF-1');

    const first = await mount();
    expect(first.textContent).toContain('Name your agency');
    expect(googlePending).toHaveBeenCalledTimes(1);
    expect(googlePending).toHaveBeenCalledWith('abc_DEF-1');
    expect(window.location.hash).toBe('');

    // The reload: the same page, at the address the first visit left behind.
    unmount();
    const second = await mount();
    expect(refusal(second)).toEqual([NO_CODE.title, NO_CODE.detail]);
    expect(second.textContent).not.toContain('Name your agency');
    expect(googlePending).toHaveBeenCalledTimes(1);
  });
});

describe('a ticket the peek fails on', () => {
  it.each([
    ['a 400 from the API', refused()],
    ['an API that does not answer', new TypeError('Failed to fetch')],
  ])('%s is the expired state, in the words it always had', async (_what, failure) => {
    googlePending.mockRejectedValue(failure);
    open('#ticket=spent');
    const el = await mount();
    expect(googlePending).toHaveBeenCalledWith('spent');
    expect(refusal(el)).toEqual([EXPIRED.title, EXPIRED.detail]);
  });
});

describe('both refusals link to the sign-in page', () => {
  const ARRIVALS: Array<[string, string, Error | null]> = [
    ['a page with no ticket', '', null],
    ['a ticket the API refuses', '#ticket=spent', refused()],
  ];

  it.each(ARRIVALS)('%s: "Back to sign in" goes to /', async (_what, hash, failure) => {
    if (failure !== null) googlePending.mockRejectedValue(failure);
    open(hash);
    const el = await mount();
    const back = button(el, 'Back to sign in');
    expect(back, 'no "Back to sign in" button on screen').toBeDefined();
    act(() => back!.click());
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/');
  });
});

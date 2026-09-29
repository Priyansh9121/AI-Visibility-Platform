// @vitest-environment jsdom

/**
 * The front door's Google failure notice, mounted — 2026-09-29.
 *
 * A failed Google step used to land here signed out, and the notice lived on
 * the sign-in view alone. Since the free scan began to need a Google
 * sign-in, the scan-limit notice sends a SIGNED-IN user to Google to link
 * the account, so a failure can land here with the session still live — and
 * the intake screen said nothing, so Run a scan met the same notice again,
 * in a loop, with no explanation.
 *
 * The word-to-sentence mapping is tested as pure functions in
 * `lib/auth/googleFailure.test.ts`. What only a mount can show is which view
 * the notice lands on, in whose words, and that the query is gone afterwards:
 * `Home` does all three in an effect, which a static render never runs. The
 * pattern is `WorkspaceShell.test.tsx`'s — the real component in jsdom, the
 * API module mocked at its edge.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ownerMe } from '@/lib/settings/__fixtures__/seats';
import { GOOGLE_REASONS, googleReasonSentence } from '@/lib/auth/googleFailure';

const me = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, me: () => me() } };
});

// Imported after the mock so the page sees the mocked module.
const { default: Home } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TITLE = 'Google sign-in did not complete';
const INTAKE = 'Start with a website.';

/** Let every pending microtask settle — the mocked `me` resolves on the next tick. */
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

let root: Root | null = null;

/** Arrive at `/` with this query, the way Google's callback sends the browser. */
async function arrive(search: string): Promise<HTMLDivElement> {
  window.history.replaceState(null, '', `/${search}`);
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<Home />));
  await settle();
  return el;
}

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  window.history.replaceState(null, '', '/');
});

describe('signed in, back from a Google step that failed', () => {
  beforeEach(() => {
    me.mockReset();
    me.mockResolvedValue(ownerMe);
  });

  it('denied: the intake screen, with the notice above the form', async () => {
    const el = await arrive('?google=error&reason=denied');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(GOOGLE_REASONS.denied);
    expect(text).toContain(INTAKE);
    const html = el.innerHTML;
    expect(html.indexOf(TITLE)).toBeGreaterThan(-1);
    expect(html.indexOf(TITLE)).toBeLessThan(html.indexOf('<form'));
    expect(window.location.search).toBe('');
  });

  it("email-claimed: the words for someone signed in, naming this sign-in's address", async () => {
    const el = await arrive('?google=error&reason=email-claimed');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(googleReasonSentence('email-claimed', ownerMe.user.email));
    expect(text).toContain(`choose ${ownerMe.user.email}`);
    // Not the sign-in page's words: this person is not signing in.
    expect(text).not.toContain(GOOGLE_REASONS['email-claimed']);
    expect(text).toContain(INTAKE);
    expect(window.location.search).toBe('');
  });

  it('no failure in the query, no notice — and the query is left alone', async () => {
    const el = await arrive('?utm_source=newsletter');
    expect(el.textContent).toContain(INTAKE);
    expect(el.textContent).not.toContain(TITLE);
    expect(window.location.search).toBe('?utm_source=newsletter');
  });
});

describe('signed out: unchanged', () => {
  beforeEach(() => {
    me.mockReset();
    me.mockRejectedValue(
      new ApiProblem({
        type: '/problems/unauthenticated',
        title: 'Not signed in',
        status: 401,
        detail: 'Sign in to continue.',
        instance: '/api/v1/auth/me',
      }),
    );
  });

  it('denied: straight to the sign-in form, with the notice', async () => {
    const el = await arrive('?google=error&reason=denied');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(GOOGLE_REASONS.denied);
    expect(text).toContain('Sign in with Google');
    expect(text).not.toContain(INTAKE);
    expect(window.location.search).toBe('');
  });

  it('email-claimed: the sign-in page keeps its own words', async () => {
    const el = await arrive('?google=error&reason=email-claimed');
    const text = el.textContent ?? '';
    expect(text).toContain(TITLE);
    expect(text).toContain(GOOGLE_REASONS['email-claimed']);
    expect(text).toContain('Sign in with Google');
    expect(window.location.search).toBe('');
  });
});

// @vitest-environment jsdom

/**
 * The completion form's failure sentence when the API does not answer — and
 * when it answers with a server error, which says no more about the person's
 * request than silence does (the UI review of 2026-10-01).
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const completeGoogleSignUp = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: { ...real.api, completeGoogleSignUp: (p: unknown) => completeGoogleSignUp(p) },
  };
});

const { GoogleSignUpPanel } = await import('./GoogleSignUpPanel');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('submitting while the API does not answer', () => {
  it('says the server could not be reached, with no port number', async () => {
    completeGoogleSignUp.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() =>
      root!.render(
        <GoogleSignUpPanel
          state={{ kind: 'ask', pending: { email: 'dana@northlight.example', suggestedName: 'Dana' } }}
          ticket="t"
          onSignedUp={() => {}}
          animate={false}
        />,
      ),
    );
    await act(async () => {
      el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(el.textContent).toContain(
      'We could not reach the server. Check your connection and try again.',
    );
    expect(el.textContent).not.toContain('8000');
  });
});

const UNREACHABLE = 'We could not reach the server. Check your connection and try again.';

/** An ApiProblem as `request` builds it, whatever the status. */
const problem = (status: number, detail: string, errors?: Array<{ field: string; message: string }>) =>
  new ApiProblem({
    type: '/problems/unknown',
    title: 'Request failed',
    status,
    detail,
    instance: '/api/v1/auth/google/complete',
    ...(errors ? { errors } : {}),
  } as never);

async function submitFailing(failure: unknown): Promise<HTMLDivElement> {
  completeGoogleSignUp.mockRejectedValue(failure);
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() =>
    root!.render(
      <GoogleSignUpPanel
        state={{ kind: 'ask', pending: { email: 'dana@northlight.example', suggestedName: 'Dana' } }}
        ticket="t"
        onSignedUp={() => {}}
        animate={false}
      />,
    ),
  );
  await act(async () => {
    el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await act(async () => {
    await Promise.resolve();
  });
  return el;
}

describe('submitting while the server is failing, not refusing', () => {
  it.each([
    ['a 502 from a proxy', problem(502, 'The server returned 502.')],
    ['a 500', problem(500, 'The server returned 500.')],
    ['a 503', problem(503, 'The server returned 503.')],
    ['a problem with no status', problem(0, 'The server returned 0.')],
  ])('%s says the server could not be reached, not what the proxy said', async (_what, failure) => {
    const el = await submitFailing(failure);
    expect(el.textContent).toContain(UNREACHABLE);
    expect(el.textContent).not.toContain('The server returned');
  });
});

describe('submitting while the API refuses', () => {
  it.each([400, 403, 409])('a %i shows the API\'s own sentence', async (status) => {
    const el = await submitFailing(problem(status, 'That Google sign-in has expired.'));
    expect(el.textContent).toContain('Could not create your agency');
    expect(el.textContent).toContain('That Google sign-in has expired.');
    expect(el.textContent).not.toContain(UNREACHABLE);
  });

  it('a 422 with named fields puts each message on its field, and no sentence above the form', async () => {
    const el = await submitFailing(
      problem(422, 'Validation failed.', [{ field: 'agencyName', message: 'Name your agency.' }]),
    );
    expect(el.textContent).toContain('Name your agency.');
    expect(el.textContent).not.toContain('Could not create your agency');
    expect(el.textContent).not.toContain(UNREACHABLE);
  });
});

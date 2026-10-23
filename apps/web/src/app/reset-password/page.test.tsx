// @vitest-environment jsdom

/**
 * /reset-password#{link}, mounted — A4 fix round, the UI review's M1.
 *
 * The API refuses a new password it will not take with a 422 whose `errors` name the field
 * (`newPassword`, the key `ResetPasswordConfirm` has), and the route reads exactly that key to put
 * the sentence under the box. `ResetPasswordView.test.tsx` renders the refused state; nothing
 * drove the route's own mapping from the API's answer to that state, so a drift between the key the
 * API sends and the key this reads would have shown the general line instead of the sentence, and
 * no test would have said so.
 *
 * The real route in jsdom, the API client mocked at its edge and the address bar left real, as
 * `googleSignUpRoute.test.tsx` does it.
 *
 * A5 part 3 (the founder, 2026-10-22 item d): every REFUSED link, by whichever path it is refused
 * (the API's two 400 sentences, and an address with no code in it), shows the one title "This
 * reset link can't be used", and the sentence under it is the one that path always had.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const confirmPasswordReset = vi.fn<(token: string, newPassword: string) => Promise<void>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      confirmPasswordReset: (token: string, newPassword: string) =>
        confirmPasswordReset(token, newPassword),
    },
  };
});

// Imported after the mock so the route sees the mocked module.
const { default: ResetPasswordRoute } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BREACHED = 'This password has appeared in a data breach. Choose a different one.';
const TITLE = "This reset link can't be used";
const NOT_VALID = 'That reset link is not valid. Links work once and expire after an hour.';
const USED =
  'This link has already been used. If you just set a new password, sign in with it. Otherwise, request a new link.';
const NO_CODE =
  "This page no longer has the link's code (it is removed from the address bar once read). Open the link from your email again.";
const LINK = 'a-reset-link-code';
const CHOSEN = 'a-long-enough-value-1';

let root: Root | null = null;

beforeEach(() => {
  confirmPasswordReset.mockReset();
  // Be at /reset-password with the link in the fragment, as the email's link leaves the browser.
  window.history.replaceState(null, '', `/reset-password#${LINK}`);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

/** Let every pending microtask settle: the mocked request rejects on the next tick. */
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

/** The `role="alert"` element an input's `aria-describedby` names, if it names one. */
function alertFor(input: Element): HTMLElement | null {
  for (const id of (input.getAttribute('aria-describedby') ?? '').split(' ')) {
    const target = document.getElementById(id);
    if (target?.getAttribute('role') === 'alert') return target;
  }
  return null;
}

describe('a refusal of the new password from the server lands on the new password field', () => {
  it('puts the breached-password sentence under New password and stays on the form', async () => {
    confirmPasswordReset.mockRejectedValue(
      new ApiProblem({
        type: '/problems/validation-failed',
        title: 'Request validation failed',
        status: 422,
        detail: 'One or more fields were invalid.',
        instance: '/api/v1/auth/reset-password/confirm',
        errors: [{ field: 'newPassword', message: BREACHED, type: 'value_error' }],
      }),
    );
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() => root!.render(<ResetPasswordRoute />));
    await settle();

    const input = el.querySelector<HTMLInputElement>('input[autocomplete="new-password"]')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, CHOSEN);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => {
      el.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
    });
    await settle();

    expect(confirmPasswordReset).toHaveBeenCalledWith(LINK, CHOSEN);
    const refused = el.querySelector<HTMLInputElement>('input[autocomplete="new-password"]')!;
    expect(refused.getAttribute('aria-invalid')).toBe('true');
    expect(alertFor(refused)?.textContent).toBe(BREACHED);
    // Still the form: the link is good, and the screen for a link that is not has not appeared.
    expect(el.querySelector('form')).not.toBeNull();
    expect(el.querySelector('.avp-errorstate')).toBeNull();
    expect(el.textContent).not.toContain('One or more fields were invalid.');
    expect(el.textContent).not.toContain(TITLE);
    expect(el.textContent).not.toContain('This reset link is not valid');
  });
});

/** Mount the route with a link in the address bar, type a password and submit it. */
async function submitAPassword(): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<ResetPasswordRoute />));
  await settle();
  const input = el.querySelector<HTMLInputElement>('input[autocomplete="new-password"]')!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, CHOSEN);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  act(() => {
    el.querySelector('form')!.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  return el;
}

/** The alert's two lines: the title, and the sentence under it. */
function refusalOf(el: HTMLElement): { title: string | undefined; detail: string | undefined } {
  const alert = el.querySelector<HTMLElement>('[role="alert"]');
  return {
    title: alert?.querySelector('.avp-errorstate__title')?.textContent ?? undefined,
    detail: alert?.querySelector('.avp-errorstate__detail')?.textContent ?? undefined,
  };
}

describe('every refused link shows one title, and the sentence under it is unchanged', () => {
  it.each([
    ['the link is unknown, expired, or its account cannot use it', NOT_VALID],
    ['the link was used, retired, or is being redeemed right now', USED],
  ])('%s', async (_cause, detail) => {
    confirmPasswordReset.mockRejectedValue(
      new ApiProblem({
        type: '/problems/invalid-reset-token',
        title: 'That reset link is not valid',
        status: 400,
        detail,
        instance: '/api/v1/auth/reset-password/confirm',
      }),
    );

    const el = await submitAPassword();

    expect(refusalOf(el)).toEqual({ title: TITLE, detail });
    expect(el.textContent).not.toContain('This reset link is not valid');
    // The screen for a link that cannot be used has no form, and offers a way on.
    expect(el.querySelector('form')).toBeNull();
    expect(el.textContent).toContain('Ask for a new link');
  });

  it('an address with no code in it shows the same title over its own sentence', async () => {
    window.history.replaceState(null, '', '/reset-password');
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() => root!.render(<ResetPasswordRoute />));
    await settle();

    expect(refusalOf(el)).toEqual({ title: TITLE, detail: NO_CODE });
    expect(el.querySelector('form')).toBeNull();
    expect(confirmPasswordReset).not.toHaveBeenCalled();
  });
});

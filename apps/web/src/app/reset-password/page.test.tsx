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
    expect(el.textContent).not.toContain('This reset link is not valid');
  });
});

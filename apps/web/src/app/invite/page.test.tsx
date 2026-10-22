// @vitest-environment jsdom

/**
 * /invite#{link}, mounted — A4 fix round, the UI review's M1.
 *
 * The API refuses a password it will not take with a 422 whose `errors` name the field
 * (`password`, the key `AcceptInvitationRequest` has), and the route reads exactly that key to put
 * the sentence under the box. `AcceptInvitationView.test.tsx` renders the refused state; nothing
 * drove the route's own mapping from the API's answer to that state, so a drift between the key
 * the API sends and the key this reads would have shown the general line instead of the sentence.
 *
 * The real route in jsdom, the API client mocked at its edge and the address bar left real, as
 * `googleSignUpRoute.test.tsx` does it.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const acceptInvitation =
  vi.fn<(token: string, fullName: string, password: string) => Promise<unknown>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      acceptInvitation: (token: string, fullName: string, password: string) =>
        acceptInvitation(token, fullName, password),
    },
  };
});

// Imported after the mock so the route sees the mocked module.
const { default: AcceptInvitationRoute } = await import('./page');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BREACHED = 'This password has appeared in a data breach. Choose a different one.';
const LINK = 'an-invitation-code';
const NAME = 'Dana Whitfield';
const CHOSEN = 'a-long-enough-value-1';

let root: Root | null = null;

beforeEach(() => {
  acceptInvitation.mockReset();
  // Be at /invite with the link in the fragment, as the email's link leaves the browser.
  window.history.replaceState(null, '', `/invite#${LINK}`);
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

function type(input: HTMLInputElement, value: string): void {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('a refusal of the password from the server lands on the password field', () => {
  it('puts the breached-password sentence under Password and keeps the name and the invitation', async () => {
    acceptInvitation.mockRejectedValue(
      new ApiProblem({
        type: '/problems/validation-failed',
        title: 'Request validation failed',
        status: 422,
        detail: 'One or more fields were invalid.',
        instance: '/api/v1/auth/invitations/accept',
        errors: [{ field: 'password', message: BREACHED, type: 'value_error' }],
      }),
    );
    const el = document.createElement('div');
    document.body.appendChild(el);
    root = createRoot(el);
    act(() => root!.render(<AcceptInvitationRoute />));
    await settle();

    type(el.querySelector<HTMLInputElement>('input[autocomplete="name"]')!, NAME);
    type(el.querySelector<HTMLInputElement>('input[autocomplete="new-password"]')!, CHOSEN);
    act(() => {
      el.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
    });
    await settle();

    expect(acceptInvitation).toHaveBeenCalledWith(LINK, NAME, CHOSEN);
    const password = el.querySelector<HTMLInputElement>('input[autocomplete="new-password"]')!;
    expect(password.getAttribute('aria-invalid')).toBe('true');
    expect(alertFor(password)?.textContent).toBe(BREACHED);
    const name = el.querySelector<HTMLInputElement>('input[autocomplete="name"]')!;
    expect(name.getAttribute('aria-invalid')).toBeNull();
    expect(name.value).toBe(NAME);
    // Still the form: the invitation is good, and the screen for one that is not has not appeared.
    expect(el.querySelector('form')).not.toBeNull();
    expect(el.querySelector('.avp-errorstate')).toBeNull();
    expect(el.textContent).not.toContain('One or more fields were invalid.');
  });
});

// @vitest-environment jsdom

/**
 * Ask for a reset link — Epic 9.13.
 *
 * The assertions here are about what this screen must NOT be able to say. The
 * backend was built so that an address with an account and one without get
 * byte-identical responses; a screen that rendered "we couldn't find that
 * email" would hand the oracle straight back.
 *
 * Since 2026-09-29 it must also not say "a link is on its way" when this
 * service cannot send email. Which of the two it says is decided by the API's
 * `emailConfigured`, so those tests MOUNT the panel, submit it, and answer
 * with a mocked API — the pattern `app/home.test.tsx` set.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import type { ResetRequestResult } from '@avp/shared-types';
import { OPERATOR } from '@/lib/legal/operator';

const requestPasswordReset = vi.fn<(email: string) => Promise<ResetRequestResult>>();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: { ...real.api, requestPasswordReset: (email: string) => requestPasswordReset(email) },
  };
});

// Imported after the mock so the panel sees the mocked module.
const { ForgotPasswordPanel } = await import('./ForgotPasswordPanel');
const { GOOGLE_SIGN_IN_URL } = await import('@/lib/api');

const render = () =>
  renderToStaticMarkup(<ForgotPasswordPanel onBackToSignIn={() => {}} />);

describe('the form asks for one thing', () => {
  it('collects an email and nothing else', () => {
    const out = render();
    expect(out).toContain('>Email<');
    expect(out).toContain('>Continue<');
    // No password field on a page reached by someone who has lost theirs.
    expect(out).not.toContain('type="password"');
  });

  it('promises no email before it knows one can be sent', () => {
    // The API says whether email is on only in its answer. Until then the
    // form must not say "we will email", which production cannot yet do.
    const out = render().toLowerCase();
    for (const promise of ['we will email', 'email me a link', 'on its way', 'check your email']) {
      expect(out, `found "${promise}"`).not.toContain(promise);
    }
  });

  it('offers a way back', () => {
    expect(render()).toContain('Remembered it?');
  });
});

describe('it cannot reveal whether an account exists', () => {
  it('has no copy anywhere that could name an unknown address', () => {
    const out = render().toLowerCase();
    for (const tell of [
      'no account',
      'not found',
      "couldn't find",
      'could not find',
      'unknown email',
      'no user',
      "doesn't exist",
      'does not exist',
    ]) {
      expect(out, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('shows no error state before anything is submitted', () => {
    const out = render();
    expect(out).not.toContain('avp-errorstate');
    expect(out).not.toContain('avp-field__error');
  });
});

describe('no ad hoc styling', () => {
  it('uses the width token and emits no off-system class', () => {
    const out = render();
    expect(out).toContain('max-w-form');
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

describe('the card arrives — Epic 9.16', () => {
  // Rendered WITH animation on, which is what a browser gets. The rest of this
  // file renders the default, so it would see the finished markup either way.
  const live = () => renderToStaticMarkup(<ForgotPasswordPanel onBackToSignIn={() => {}} />);

  it('wraps the card in a single reveal, not a stagger', () => {
    const out = live();
    expect(out).toContain('avp-reveal');
    // One object cannot be a sequence, so no sibling offsets are emitted.
    expect(out).not.toContain('--avp-reveal-index');
  });

  it('adds no box — the layout classes stay on the same element', () => {
    // `Reveal` REPLACES the wrapper rather than nesting inside it.
    expect(live()).toMatch(/class="avp-reveal[^"]*max-w-form/);
  });

  it('starts hidden and renders finished when motion is off', () => {
    expect(live()).not.toContain('avp-reveal--revealed');
    expect(renderToStaticMarkup(<ForgotPasswordPanel onBackToSignIn={() => {}} animate={false} />)).toContain('avp-reveal--revealed');
  });

  it('still carries every word it carried before', () => {
    // Revealing hides content visually until it arrives; it must never remove
    // it from the document.
    const out = live();
    expect(out).toContain('Email');
  });
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** Let every pending microtask settle — the mocked request resolves on the next tick. */
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

let root: Root | null = null;

/** Mount the panel, type `address`, submit, and wait for the answer. */
async function submitted(address: string): Promise<HTMLDivElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root!.render(<ForgotPasswordPanel onBackToSignIn={() => {}} animate={false} />));
  const input = el.querySelector<HTMLInputElement>('input[type="email"]')!;
  act(() => {
    // React tracks the value itself; set it the way a browser would, then
    // announce it, so the controlled field's onChange sees the typing.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, address);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  act(() => {
    el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await settle();
  return el;
}

const answer = (emailConfigured: boolean): ResetRequestResult => ({
  status: 'accepted',
  detail: 'Whatever the API said; the panel words it itself.',
  emailConfigured,
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('with email not set up — 2026-09-29', () => {
  beforeEach(() => {
    requestPasswordReset.mockReset();
    requestPasswordReset.mockResolvedValue(answer(false));
  });

  it("says no email was sent, in the founder's words, and does not say check your inbox", async () => {
    const el = await submitted('someone@example.com');
    expect(requestPasswordReset).toHaveBeenCalledWith('someone@example.com');
    const text = el.textContent ?? '';
    expect(text).toContain(
      `Password reset by email isn’t available yet. Sign in with Google if your account uses this address, or email ${OPERATOR.email} and we’ll reset it by hand.`,
    );
    for (const untrue of ['Check your email', 'on its way', 'in spam']) {
      expect(text).not.toContain(untrue);
    }
  });

  it('offers Google, the operator by email, and the way back', async () => {
    const el = await submitted('someone@example.com');
    expect(el.querySelector(`a[href="${GOOGLE_SIGN_IN_URL}"]`)?.textContent).toContain(
      'Sign in with Google',
    );
    expect(el.querySelector(`a[href="mailto:${OPERATOR.email}"]`)?.textContent).toBe(OPERATOR.email);
    expect(el.textContent).toContain('Back to sign in');
    // A different address would get the same answer, so it is not offered.
    expect(el.textContent).not.toContain('Use a different address');
  });

  it('says the same for an address with no account — the answer is about the service', async () => {
    // The API returns one body for every address; the panel has nothing else
    // to go on, so two addresses must render identically.
    const first = (await submitted('has.account@example.com')).innerHTML;
    act(() => root?.unmount());
    document.body.innerHTML = '';
    const second = (await submitted('nobody@example.com')).innerHTML;
    expect(second).toBe(first);
  });
});

describe('with email set up', () => {
  beforeEach(() => {
    requestPasswordReset.mockReset();
    requestPasswordReset.mockResolvedValue(answer(true));
  });

  it('says to check the inbox, as before, and nothing about email being unavailable', async () => {
    const el = await submitted('someone@example.com');
    const text = el.textContent ?? '';
    expect(text).toContain('Check your email');
    expect(text).toContain('If that address has an account, a reset link is on its way.');
    expect(text).toContain('works once and expires in an hour');
    expect(text).not.toContain('isn’t available yet');
    expect(text).not.toContain(OPERATOR.email);
    expect(text).toContain('Use a different address');
  });
});

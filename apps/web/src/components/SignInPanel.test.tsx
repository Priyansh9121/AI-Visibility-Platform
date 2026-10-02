// @vitest-environment jsdom

/**
 * Sign in — Epic 2, brought up to standard in Epic 9.11.
 *
 * Static render only: this asserts the initial, unsubmitted state and the
 * structural guarantees. The submit paths are behavioural and belong to the
 * API's own auth tests, which already cover 401 and session revocation.
 */

import { describe, it, expect, vi } from 'vitest';
import { act, type JSX } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApiProblem, GOOGLE_SIGN_IN_URL } from '@/lib/api';
import { SignInPanel } from './SignInPanel';

const logIn = vi.fn();
vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, logIn: (...a: unknown[]) => logIn(...a) } };
});

const render = () =>
  renderToStaticMarkup(
    <SignInPanel
      onSignedIn={() => {}}
      onSwitchToSignUp={() => {}}
      onForgotPassword={() => {}}
    />,
  );

describe('the form is built from the design system', () => {
  it('uses real fields with bound labels, not styled inputs', () => {
    const out = render();
    expect(out).toContain('avp-field');
    expect(out).toContain('>Email<');
    expect(out).toContain('>Password<');
    expect(out).toContain('avp-btn');
  });

  it('wires autocomplete so a password manager can fill it', () => {
    // Matched case-insensitively: React 19 emits the prop as `autoComplete`,
    // and HTML attribute names are case-insensitive, so the browser honours it
    // either way. Asserting the exact casing would test React, not this form.
    const out = render().toLowerCase();
    expect(out).toContain('autocomplete="email"');
    expect(out).toContain('autocomplete="current-password"');
  });

  it('does not ship the password field as type=text', () => {
    expect(render()).toContain('type="password"');
  });
});

describe('width comes from a token, not a hardcoded value', () => {
  it('uses max-w-form rather than an arbitrary Tailwind value', () => {
    const out = render();
    // Epic 9.11 retired `max-w-[26rem]` here — the only arbitrary value this
    // component had, and exactly the off-system styling the design-system-only rule bans.
    expect(out).toContain('max-w-form');
    expect(out).not.toMatch(/max-w-\[/);
  });

  it('emits no arbitrary-value or raw-palette utility anywhere', () => {
    const out = render();
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

describe('it is honest about what it can and cannot do', () => {
  it('offers a real route to creating an account', () => {
    // Until Epic 9.12 this was prose describing an offline process, because
    // there was no sign-up form anywhere in apps/web. It is now a control that
    // switches the view.
    const out = render();
    expect(out).toContain('No account yet?');
    expect(out).toContain('Create your agency');
    expect(out).toContain('avp-btn');
  });

  it('shows no error state before anything has been submitted', () => {
    const out = render();
    expect(out).not.toContain('avp-errorstate');
    expect(out).not.toContain('avp-field__error');
  });
});

describe('password reset is reachable from here', () => {
  it('offers a forgot-password route — Epic 9.13', () => {
    // Until 9.13 there was no reset flow at all, so there was nothing to link
    // to and someone who forgot their password had no path forward.
    expect(render()).toContain('Forgot your password?');
  });
});

describe('the card arrives — Epic 9.16', () => {
  // Rendered WITH animation on, which is what a browser gets. The rest of this
  // file renders the default, so it would see the finished markup either way.
  const live = () => renderToStaticMarkup(<SignInPanel onSignedIn={() => {}} onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />);

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
    expect(renderToStaticMarkup(<SignInPanel onSignedIn={() => {}} onSwitchToSignUp={() => {}} onForgotPassword={() => {}} animate={false} />)).toContain('avp-reveal--revealed');
  });

  it('still carries every word it carried before', () => {
    // Revealing hides content visually until it arrives; it must never remove
    // it from the document.
    const out = live();
    expect(out).toContain('Sign in');
    expect(out).toContain('Forgot your password?');
  });
});

describe('Google is beside the password, not instead of it', () => {
  it('carries the Google button, under an "or" rule, with the form still there', () => {
    const out = render();
    // The button is an anchor to the API, which redirects to Google.
    expect(out).toContain(`href="${GOOGLE_SIGN_IN_URL}"`);
    expect(out).toContain('Sign in with Google');
    expect(out).toContain('avp-google-btn');
    expect(out).toMatch(/aria-hidden="true"><\/span>or<span/);
    // The password form is not replaced by it.
    expect(out).toContain('>Email<');
    expect(out).toContain('>Password<');
  });
});

// --- 429: the server's own words, in the existing error UI -------------------

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('when the server answers 429', () => {
  it('shows its detail in the error state, not a generic or network message', async () => {
    logIn.mockRejectedValue(
      new ApiProblem({
        type: '/problems/rate-limited',
        title: 'Too many requests',
        status: 429,
        detail: 'Too many attempts. Try again in 15 minutes.',
        instance: '/api/v1/auth/x',
      }),
    );
    const el = document.createElement('div');
    document.body.appendChild(el);
    const root = createRoot(el);
    act(() => root.render(<SignInPanel onSignedIn={() => {}} onSwitchToSignUp={() => {}} onForgotPassword={() => {}} animate={false} />));
    const type = (input: HTMLInputElement, value: string) =>
      act(() => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    el.querySelectorAll<HTMLInputElement>('input').forEach((i) => type(i, i.type === 'email' ? 'a@b.example' : 'a-long-enough-value-1'));
    act(() => {
      el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const text = el.textContent ?? '';
    expect(text).toContain('Too many attempts. Try again in 15 minutes.');
    expect(text).not.toContain('did not respond');
    expect(text).not.toContain('did not complete');
    expect(el.querySelector('.avp-errorstate')).not.toBeNull();
    act(() => root.unmount());
    document.body.innerHTML = '';
  });
});

const AWAY = 'We could not reach the server. Check your connection and try again.';
const away502 = () =>
  new ApiProblem({
    type: '/problems/unknown',
    title: 'Request failed',
    status: 502,
    detail: 'The server returned 502.',
    instance: '/api/v1/x',
  });
const typeInto = (input: HTMLInputElement, value: string) =>
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
async function submitForm(panel: JSX.Element): Promise<{ text: string; cleanup: () => void }> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  const root = createRoot(el);
  act(() => root.render(panel));
  el.querySelectorAll<HTMLInputElement>('input').forEach((i) =>
    typeInto(i, i.type === 'email' ? 'a@b.example' : 'a-long-enough-value-1'),
  );
  act(() => {
    el.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  return {
    text: el.textContent ?? '',
    cleanup: () => {
      act(() => root.unmount());
      document.body.innerHTML = '';
    },
  };
}

describe('when the API does not answer, the person is told in plain words', () => {
  it('a network failure shows the shared sentence and no port number', async () => {
    logIn.mockRejectedValue(new TypeError('Failed to fetch'));
    const { text, cleanup } = await submitForm(<SignInPanel onSignedIn={() => {}} onSwitchToSignUp={() => {}} onForgotPassword={() => {}} animate={false} />);
    expect(text).toContain(AWAY);
    expect(text).not.toContain('8000');
    expect(text).not.toContain('did not respond');
    cleanup();
  });

  it("a 5xx that is not the API's own problem body shows the shared sentence", async () => {
    logIn.mockRejectedValue(away502());
    const { text, cleanup } = await submitForm(<SignInPanel onSignedIn={() => {}} onSwitchToSignUp={() => {}} onForgotPassword={() => {}} animate={false} />);
    expect(text).toContain(AWAY);
    expect(text).not.toContain('The server returned 502');
    cleanup();
  });

  it('a 4xx problem still shows its own detail', async () => {
    logIn.mockRejectedValue(
      new ApiProblem({
        type: '/problems/x',
        title: 'Nope',
        status: 403,
        detail: 'The API says no, in its own words.',
        instance: '/api/v1/x',
      }),
    );
    const { text, cleanup } = await submitForm(<SignInPanel onSignedIn={() => {}} onSwitchToSignUp={() => {}} onForgotPassword={() => {}} animate={false} />);
    expect(text).toContain('The API says no, in its own words.');
    expect(text).not.toContain(AWAY);
    cleanup();
  });
});

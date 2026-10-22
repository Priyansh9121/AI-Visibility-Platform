// @vitest-environment jsdom

/**
 * Create an agency — Epic 9.12.
 *
 * Static render only, per Epic 9.11's finding: this repo has no DOM-driving
 * test library, and adding one would need a licence review under
 * the dependency-licensing rule. Submit-path behaviour is asserted where it is testable —
 * the API's own auth tests already cover 201, 409 and 422.
 */

import { describe, it, expect, vi } from 'vitest';
import { act, type JSX } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApiProblem, GOOGLE_SIGN_IN_URL } from '@/lib/api';
import { SignUpPanel } from './SignUpPanel';

const signUp = vi.fn();
vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, signUp: (...a: unknown[]) => signUp(...a) } };
});

const render = () =>
  renderToStaticMarkup(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} />);

describe('it collects exactly what the endpoint requires', () => {
  it('has a field for each of the four required properties', () => {
    // SignUpRequest.required = [agencyName, fullName, email, password].
    // Read off the generated OpenAPI schema, not guessed.
    const out = render();
    expect(out).toContain('Agency name');
    expect(out).toContain('Your name');
    expect(out).toContain('Email');
    expect(out).toContain('Password');
  });

  it('asks for nothing the endpoint does not accept', () => {
    const out = render();
    // No plan picker, no billing, no seat count. north-star.md §5.4 has not
    // decided pricing, and putting a plan in front of a sign-up form would
    // imply a decision that has not been made.
    expect(out).not.toMatch(/\$\s?\d/);
    expect(out).not.toContain('Plan');
    expect(out).not.toContain('Card number');
    expect(out).not.toContain('Seats');
    expect(out).not.toContain('Confirm password');
  });

  it('marks every field required so the browser catches an empty submit', () => {
    expect(render().match(/required=""/g)).toHaveLength(4);
  });
});

describe('it describes the password rule without re-implementing it', () => {
  it('states the server minimum', () => {
    // api-contracts.md: 12-256 characters, NIST SP 800-63B.
    expect(render()).toContain('At least 12 characters');
  });

  it('imposes no composition rule of its own', () => {
    const out = render();
    // Composition rules push people toward predictable substitutions, which is
    // exactly why the API does not have any. The form must not invent one.
    expect(out).not.toContain('uppercase');
    expect(out).not.toContain('special character');
    expect(out).not.toMatch(/pattern="/);
  });

  it('tells the password manager this is a NEW credential', () => {
    // `new-password` rather than `current-password`, or a manager offers the
    // existing one instead of generating.
    expect(render().toLowerCase()).toContain('autocomplete="new-password"');
  });
});

describe('it is honest about what an agency is', () => {
  it('says the agency name is what appears on reports', () => {
    expect(render()).toContain('appears on every report you send');
  });

  it('says the account is the owner seat', () => {
    // The API makes the first user of an agency the owner, always.
    expect(render()).toContain('the owner');
  });
});

describe('it offers a route back to sign-in', () => {
  it('links rather than describing an offline process', () => {
    const out = render();
    expect(out).toContain('Already have an account?');
    expect(out).toContain('avp-btn');
  });
});

describe('no ad hoc styling', () => {
  it('uses the width token and emits no off-system class', () => {
    const out = render();
    expect(out).toContain('max-w-form');
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });

  it('shows no error state before anything is submitted', () => {
    const out = render();
    expect(out).not.toContain('avp-errorstate');
    expect(out).not.toContain('avp-field__error');
  });
});

describe('the card arrives — Epic 9.16', () => {
  // Rendered WITH animation on, which is what a browser gets. The rest of this
  // file renders the default, so it would see the finished markup either way.
  const live = () => renderToStaticMarkup(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} />);

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
    expect(renderToStaticMarkup(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} animate={false} />)).toContain('avp-reveal--revealed');
  });

  it('still carries every word it carried before', () => {
    // Revealing hides content visually until it arrives; it must never remove
    // it from the document.
    const out = live();
    expect(out).toContain('Agency name');
    expect(out).toContain('Create agency');
  });
});

describe('Google is beside the password, not instead of it', () => {
  it('carries the Google button, under an "or" rule, with the form still there', () => {
    const out = render();
    expect(out).toContain(`href="${GOOGLE_SIGN_IN_URL}"`);
    expect(out).toContain('Sign up with Google');
    expect(out).toContain('avp-google-btn');
    expect(out).toMatch(/aria-hidden="true"><\/span>or<span/);
    expect(out).toContain('>Email<');
    expect(out).toContain('>Password<');
  });
});

// --- 429: the server's own words, in the existing error UI -------------------

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('when the server answers 429', () => {
  it('shows its detail in the error state, not a generic or network message', async () => {
    signUp.mockRejectedValue(
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
    act(() => root.render(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} animate={false} />));
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
async function submitForm(
  panel: JSX.Element,
): Promise<{ el: HTMLElement; text: string; cleanup: () => void }> {
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
    el,
    text: el.textContent ?? '',
    cleanup: () => {
      act(() => root.unmount());
      document.body.innerHTML = '';
    },
  };
}

describe('when the API does not answer, the person is told in plain words', () => {
  it('a network failure shows the shared sentence and no port number', async () => {
    signUp.mockRejectedValue(new TypeError('Failed to fetch'));
    const { text, cleanup } = await submitForm(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} animate={false} />);
    expect(text).toContain(AWAY);
    expect(text).not.toContain('8000');
    expect(text).not.toContain('did not respond');
    cleanup();
  });

  it("a 5xx that is not the API's own problem body shows the shared sentence", async () => {
    signUp.mockRejectedValue(away502());
    const { text, cleanup } = await submitForm(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} animate={false} />);
    expect(text).toContain(AWAY);
    expect(text).not.toContain('The server returned 502');
    cleanup();
  });

  it('a 4xx problem still shows its own detail', async () => {
    signUp.mockRejectedValue(
      new ApiProblem({
        type: '/problems/x',
        title: 'Nope',
        status: 403,
        detail: 'The API says no, in its own words.',
        instance: '/api/v1/x',
      }),
    );
    const { text, cleanup } = await submitForm(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} animate={false} />);
    expect(text).toContain('The API says no, in its own words.');
    expect(text).not.toContain(AWAY);
    cleanup();
  });
});

describe('a sign-up the server cannot complete says its neutral sentence', () => {
  it('shows the 409 sign-up-not-possible detail on the form and keeps the way to sign in', async () => {
    const sentence =
      "We couldn't create an account with that email. If you already have one, sign in or reset your password.";
    signUp.mockRejectedValue(
      new ApiProblem({
        type: '/problems/sign-up-not-possible',
        title: sentence,
        status: 409,
        detail: sentence,
        instance: '/api/v1/auth/sign-up',
      }),
    );
    const { text, cleanup } = await submitForm(<SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} animate={false} />);
    expect(text).toContain(sentence);
    expect(text).toContain('Sign in');
    expect(text).not.toContain('already exists');
    cleanup();
  });
});

const BREACHED = 'This password has appeared in a data breach. Choose a different one.';

/** The `role="alert"` element an input's `aria-describedby` names, if it names one. */
function alertFor(input: Element): HTMLElement | null {
  for (const id of (input.getAttribute('aria-describedby') ?? '').split(' ')) {
    const target = document.getElementById(id);
    if (target?.getAttribute('role') === 'alert') return target;
  }
  return null;
}

describe('a refusal of the password from the server lands on the password field', () => {
  // A4 fix round, the UI review's M1: the API names `password` for a sign-up, and this form reads
  // exactly that key. Nothing else pinned it, and a drift would show nothing at all: the named
  // errors are not empty, so the general line is skipped, and no box matches.
  it('puts the breached-password sentence on the password box, announced, and nowhere else', async () => {
    signUp.mockRejectedValue(
      new ApiProblem({
        type: '/problems/validation-failed',
        title: 'Request validation failed',
        status: 422,
        detail: 'One or more fields were invalid.',
        instance: '/api/v1/auth/sign-up',
        errors: [{ field: 'password', message: BREACHED, type: 'value_error' }],
      }),
    );
    const { el, text, cleanup } = await submitForm(
      <SignUpPanel onSignedUp={() => {}} onSwitchToSignIn={() => {}} animate={false} />,
    );

    const password = el.querySelector<HTMLInputElement>('input[type="password"]')!;
    expect(password.getAttribute('aria-invalid')).toBe('true');
    expect(alertFor(password)?.textContent).toBe(BREACHED);
    for (const other of el.querySelectorAll('input:not([type="password"])')) {
      expect(other.getAttribute('aria-invalid')).toBeNull();
    }
    expect(el.querySelector('.avp-errorstate')).toBeNull();
    expect(text).not.toContain('One or more fields were invalid.');
    cleanup();
  });
});

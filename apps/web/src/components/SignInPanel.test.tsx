/**
 * Sign in — Epic 2, brought up to standard in Epic 9.11.
 *
 * Static render only: this asserts the initial, unsubmitted state and the
 * structural guarantees. The submit paths are behavioural and belong to the
 * API's own auth tests, which already cover 401 and session revocation.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { SignInPanel } from './SignInPanel';

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
    // component had, and exactly the off-system styling ip-safety.md #2 bans.
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

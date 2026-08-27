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

const render = () => renderToStaticMarkup(<SignInPanel onSignedIn={() => {}} />);

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
  it('says how to get an account, because it cannot create one', () => {
    // There is no sign-up form in apps/web. Rather than leave a stranger who
    // followed the landing page CTA staring at a form they cannot use, the
    // panel says what to do instead. The real fix is a sign-up flow — named as
    // a follow-up in build-log Epic 9.11, deliberately not built here.
    expect(render()).toContain('No account yet?');
  });

  it('shows no error state before anything has been submitted', () => {
    const out = render();
    expect(out).not.toContain('avp-errorstate');
    expect(out).not.toContain('avp-field__error');
  });
});

/**
 * Ask for a reset link — Epic 9.13.
 *
 * The assertions here are about what this screen must NOT be able to say. The
 * backend was built so that an address with an account and one without get
 * byte-identical responses; a screen that rendered "we couldn't find that
 * email" would hand the oracle straight back.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ForgotPasswordPanel } from './ForgotPasswordPanel';

const render = () =>
  renderToStaticMarkup(<ForgotPasswordPanel onBackToSignIn={() => {}} />);

describe('the form asks for one thing', () => {
  it('collects an email and nothing else', () => {
    const out = render();
    expect(out).toContain('>Email<');
    expect(out).toContain('Email me a link');
    // No password field on a page reached by someone who has lost theirs.
    expect(out).not.toContain('type="password"');
  });

  it('states the link rules up front', () => {
    const out = render();
    expect(out).toContain('works once');
    expect(out).toContain('expires in an hour');
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

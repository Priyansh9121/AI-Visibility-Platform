/**
 * Sign in with Google — Epic 20.
 *
 * What is asserted is the part that is a compliance requirement rather than
 * a taste: the wording is one of Google's three, the mark is present and
 * decorative, the colours are Google's literals (the one place a Working
 * component carries any), and it is an anchor to where it was told to go.
 */
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { GoogleSignInButton } from './GoogleSignInButton.js';

const css = readFileSync(
  fileURLToPath(new URL('../styles/components.css', import.meta.url)),
  'utf8',
);

describe('GoogleSignInButton', () => {
  it('is an anchor to the URL it is given, with the default wording', () => {
    const html = renderToStaticMarkup(<GoogleSignInButton href="/api/v1/auth/google/start" />);
    expect(html).toContain('<a class="avp-google-btn" href="/api/v1/auth/google/start">');
    expect(html).toContain('Sign in with Google');
  });

  it('carries the standard-colour mark, hidden from assistive tech', () => {
    const html = renderToStaticMarkup(<GoogleSignInButton href="#" />);
    expect(html).toContain('avp-google-btn__mark" aria-hidden="true"');
    for (const colour of ['#EA4335', '#4285F4', '#FBBC05', '#34A853']) expect(html).toContain(colour);
  });

  it('accepts the sign-up and continue wordings', () => {
    expect(renderToStaticMarkup(<GoogleSignInButton href="#" label="Sign up with Google" />)).toContain(
      'Sign up with Google',
    );
    expect(renderToStaticMarkup(<GoogleSignInButton href="#" label="Continue with Google" />)).toContain(
      'Continue with Google',
    );
  });

  it("follows Google's published light and dark values in the stylesheet", () => {
    const light = css.slice(css.indexOf('.avp-google-btn {'), css.indexOf("[data-theme='dark'] .avp-google-btn {"));
    expect(light).toContain('background: #ffffff');
    expect(light).toContain('inset 0 0 0 1px #747775');
    expect(light).toContain('color: #1f1f1f');
    expect(light).toContain('height: 40px');
    expect(light).toContain('font-size: 14px');
    expect(light).toContain('line-height: 20px');
    expect(light).toContain('font-weight: 500');
    const dark = css.slice(css.indexOf("[data-theme='dark'] .avp-google-btn {"));
    expect(dark).toContain('background: #131314');
    expect(dark).toContain('inset 0 0 0 1px #8e918f');
    expect(dark).toContain('color: #e3e3e3');
  });
});

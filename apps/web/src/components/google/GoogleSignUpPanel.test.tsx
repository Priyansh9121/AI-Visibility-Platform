/**
 * The Google sign-up completion — Epic 20. Static render, three states.
 */
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { GoogleSignUpPanel } from './GoogleSignUpPanel';

const pending = { email: 'dana@northlight.example', suggestedName: 'Dana Whitfield' };

describe('GoogleSignUpPanel', () => {
  it('asks for the agency name and the person, shows the verified address, and no password', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'ask', pending }} ticket="t" onSignedUp={() => {}} animate={false} />,
    );
    expect(html).toContain('Name your agency');
    expect(html).toContain('dana@northlight.example');
    expect(html).toContain('Agency name');
    expect(html).toContain('value="Dana Whitfield"');
    expect(html).not.toContain('type="password"');
    expect(html).toContain('Create agency');
  });

  it('an expired ticket says so and offers the way back', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'expired' }} ticket="" onSignedUp={() => {}} animate={false} />,
    );
    expect(html).toContain('That Google sign-in has expired');
    expect(html).toContain('Back to sign in');
    expect(html).not.toContain('Agency name');
  });

  it('says it is checking while it loads', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'loading' }} ticket="t" onSignedUp={() => {}} animate={false} />,
    );
    expect(html).toContain('Checking your Google sign-in');
  });
});

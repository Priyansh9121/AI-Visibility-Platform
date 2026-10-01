/**
 * The Google sign-up completion — Epic 20. Static render, four states.
 *
 * `no-code` (the page has no ticket to ask about, typically a reload) and
 * `expired` (the API refused the ticket) are two different things and say so;
 * which one a given arrival lands on is `app/sign-up/google/googleSignUpRoute.test.tsx`.
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
    expect(html).toContain('It works for ten minutes and once. Start again from the sign-in page.');
    expect(html).toContain('Back to sign in');
    expect(html).not.toContain('Agency name');
    // The API refused this ticket; the page did not lose it. Not the no-code words.
    expect(html).not.toContain('no longer has the link');
  });

  it('a page with no ticket says so in the words invite and reset use, and offers the way back', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'no-code' }} ticket="" onSignedUp={() => {}} animate={false} />,
    );
    // A static render writes the apostrophe as &#x27;; compare the words a person reads.
    const read = html.replaceAll('&#x27;', "'");
    expect(read).toContain('This sign-in link is not valid');
    expect(read).toContain(
      "This page no longer has the link's code (it is removed from the address bar once read). Start again from the sign-in page.",
    );
    expect(html).toContain('Back to sign in');
    expect(html).not.toContain('Agency name');
    // Nothing was refused and nothing timed out, so it does not talk about ten minutes.
    expect(html).not.toContain('has expired');
    expect(html).not.toContain('ten minutes');
  });

  it('says it is checking while it loads', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'loading' }} ticket="t" onSignedUp={() => {}} animate={false} />,
    );
    expect(html).toContain('Checking your Google sign-in');
  });
});

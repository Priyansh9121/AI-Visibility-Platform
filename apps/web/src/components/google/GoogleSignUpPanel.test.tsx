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

  it('an unreachable API says so, offers Try again, and does not say expired', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'unreachable' }} ticket="t" onSignedUp={() => {}} onRetry={() => {}} animate={false} />,
    );
    expect(html).toContain('We could not reach the server');
    expect(html).toContain('Check your connection and try again. The sign-in link works for ten minutes.');
    expect(html).toContain('Try again');
    expect(html).not.toContain('can no longer be used');
  });

  it('a refused ticket says it can no longer be used, why, and offers the way back', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'expired' }} ticket="" onSignedUp={() => {}} animate={false} />,
    );
    // Not "expired": since 3.22 a ticket opened in another browser is refused too.
    expect(html).toContain('That Google sign-in can no longer be used');
    expect(html).toContain(
      'It works once, for ten minutes, in the browser that started it. Start again from the sign-in page.',
    );
    expect(html).not.toContain('has expired');
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
    expect(html).not.toContain('can no longer be used');
    expect(html).not.toContain('ten minutes');
  });

  it('says it is checking while it loads', () => {
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'loading' }} ticket="t" onSignedUp={() => {}} animate={false} />,
    );
    expect(html).toContain('Checking your Google sign-in');
  });

  it('sets the checking line in secondary text: it is a focus target now, and tertiary is under 4.5:1 at 13px', () => {
    // Measured in a browser on the page ground: 4.35:1 light, 4.42:1 dark. After
    // "Try again" focus lands on this line, so it is read, not glanced past.
    const html = renderToStaticMarkup(
      <GoogleSignUpPanel state={{ kind: 'loading' }} ticket="t" onSignedUp={() => {}} animate={false} />,
    );
    const line = html.match(/<p\b[^>]*role="status"[^>]*>/)?.[0] ?? '';
    expect(line).toContain('text-text-secondary');
    expect(line).not.toContain('text-text-tertiary');
  });
});

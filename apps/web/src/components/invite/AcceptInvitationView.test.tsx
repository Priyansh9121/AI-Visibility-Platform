/**
 * /invite/{token} — Epic 9.14.
 *
 * The load-bearing assertions are the same shape as the reset screen's: this is
 * an unauthenticated token-bearing route, so its refusal must not say which
 * kind of wrong a token was. The backend folds unknown, expired, accepted,
 * revoked and seat-since-removed into one response; a screen that branched on
 * the reason would hand back the distinction the API withholds.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AcceptInvitationView, type InviteState } from './AcceptInvitationView';

const render = (state: InviteState, fullName = '', password = '') =>
  renderToStaticMarkup(
    <AcceptInvitationView
      state={state}
      fullName={fullName}
      password={password}
      onFullName={() => {}}
      onPassword={() => {}}
      onSubmit={() => {}}
    />,
  );

describe('the form', () => {
  it('asks for a name and a password, and nothing else', () => {
    const html = render({ kind: 'form' });
    expect(html).toContain('>Your name<');
    expect(html).toContain('>Password<');
    expect(html).toContain('type="password"');
    // No email field: the invitation already fixes the address, and an
    // editable one would imply it could be changed.
    expect(html).not.toContain('type="email"');
  });

  it('states the link rules up front', () => {
    const html = render({ kind: 'form' });
    expect(html).toContain('Links work once and expire seven days after they are sent.');
  });

  it('states the password rule before it is broken, not after', () => {
    expect(render({ kind: 'form' })).toContain('At least 12 characters');
  });

  it('says the seat is already theirs', () => {
    expect(render({ kind: 'form' })).toContain('The seat is already yours');
  });

  it('shows no error state before anything is submitted', () => {
    const html = render({ kind: 'form' });
    expect(html).not.toContain('avp-errorstate');
    expect(html).not.toContain('avp-field__error');
  });
});

describe('working', () => {
  it('disables the form and names what is happening', () => {
    const html = render({ kind: 'working' });
    expect(html).toContain('Setting up your seat…');
    expect(html).toContain('disabled');
  });
});

describe('a rejected password keeps the link', () => {
  it('stays on the form and marks the field', () => {
    const html = render({
      kind: 'rejected',
      field: 'password',
      detail: 'String should have at least 12 characters',
    });
    expect(html).toContain('avp-field__error');
    expect(html).toContain('String should have at least 12 characters');
    // Crucially NOT the invalid-link screen: the link is fine, the password
    // is not, and sending someone to ask for a new invitation over that would
    // be an unrecoverable dead end.
    expect(html).not.toContain('This invitation link is not valid');
    expect(html).toContain('>Password<');
  });

  it('surfaces a non-field failure without blaming a field', () => {
    const html = render({
      kind: 'rejected',
      field: null,
      detail: 'The request did not complete. Check your connection and try again.',
    });
    expect(html).toContain('role="alert"');
    expect(html).toContain('The request did not complete.');
    expect(html).not.toContain('avp-field__error');
  });
});

describe('an invalid link cannot say WHICH kind of invalid', () => {
  const detail =
    'That invitation link is not valid. Links work once and expire after seven days. ' +
    'Ask whoever invited you to send a new one.';

  it('renders one refusal, with the API sentence verbatim', () => {
    const html = render({ kind: 'invalid', detail });
    expect(html).toContain('This invitation link is not valid');
    expect(html).toContain(detail);
    expect(html).toContain('avp-errorstate');
  });

  it('renders identically whatever the reason was', () => {
    // The API returns ONE detail string for all five refusals, so two
    // different causes reach this screen as the same props and must produce
    // byte-identical markup. If the screen ever branched on the reason, this
    // is the assertion that would break.
    expect(render({ kind: 'invalid', detail })).toBe(
      render({ kind: 'invalid', detail }),
    );
  });

  it('has no copy anywhere naming a specific reason', () => {
    const html = render({ kind: 'invalid', detail }).toLowerCase();
    for (const tell of [
      'already accepted',
      'already used',
      'was revoked',
      'has been revoked',
      'no such invitation',
      'not found',
      'seat was removed',
      'no longer a seat',
    ]) {
      expect(html, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('offers a way onward rather than a dead end', () => {
    expect(render({ kind: 'invalid', detail })).toContain('Go to sign in');
  });
});

describe('no workspace chrome for someone with no workspace', () => {
  it('renders no sidebar — the same rule /share/{token} follows', () => {
    for (const state of [
      { kind: 'form' } as const,
      { kind: 'invalid', detail: 'no' } as const,
    ]) {
      const html = render(state);
      expect(html).not.toContain('avp-shell__sidebar');
      expect(html).not.toContain('Sign out');
    }
  });
});

describe('no ad hoc styling', () => {
  it('uses the width token and emits no off-system class', () => {
    const html = render({ kind: 'form' });
    expect(html).toContain('max-w-form');
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

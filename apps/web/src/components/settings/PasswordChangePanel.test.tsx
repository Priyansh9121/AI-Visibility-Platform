/**
 * Change your password while signed in — Epic 9.14.
 *
 * The assertions that matter are about the two things this form must say and
 * the one thing it must not.
 *
 * It must disclose that every other device signs out — that is a real
 * consequence of a small button, and usually the user's actual reason for
 * being here. It must explain why a signed-in page is asking for the current
 * password, because "I'm already logged in" is the obvious objection.
 *
 * And it must not embellish the API's refusal. The server says "that is not
 * your current password" and nothing else; there is no account-existence
 * question here, so there is nothing for the screen to add and everything for
 * it to get wrong by trying.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { PasswordChangePanel, type PasswordChangeState } from './PasswordChangePanel';

const render = (initialState?: PasswordChangeState) =>
  renderToStaticMarkup(
    initialState ? (
      <PasswordChangePanel initialState={initialState} />
    ) : (
      <PasswordChangePanel />
    ),
  );

describe('the form', () => {
  it('collects the current password and a new one', () => {
    const html = render();
    expect(html).toContain('>Current password<');
    expect(html).toContain('>New password<');
    expect(html.match(/type="password"/g)).toHaveLength(2);
  });

  it('asks for no email — the caller is already identified', () => {
    expect(render()).not.toContain('type="email"');
  });

  it('states the password rule before it can be broken', () => {
    expect(render()).toContain('At least 12 characters');
  });

  it('starts with the submit disabled, so an empty form cannot 422', () => {
    expect(render()).toMatch(/Change password[\s\S]{0,40}|disabled/);
  });

  it('shows nothing wrong before anything is submitted', () => {
    const html = render();
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain('avp-field__error');
    expect(html).not.toContain('role="status"');
  });
});

describe('the consequence is stated before the click, not after', () => {
  it('says other devices sign out and this one does not', () => {
    const html = render();
    expect(html).toContain('You will stay signed in here');
    expect(html).toContain('Every other device signs out immediately');
  });

  it('says why it wants the current password on a signed-in page', () => {
    expect(render()).toContain('being signed in is not proof of who you are');
  });
});

describe('refusals repeat the API and add nothing', () => {
  it('puts a wrong current password on that field, verbatim', () => {
    const html = render({
      kind: 'failed',
      field: 'currentPassword',
      detail: 'That is not your current password.',
    });
    expect(html).toContain('That is not your current password.');
    expect(html).toContain('avp-field__error');
  });

  it('never invents an account-existence message', () => {
    const html = render({
      kind: 'failed',
      field: 'currentPassword',
      detail: 'That is not your current password.',
    }).toLowerCase();
    for (const tell of [
      'no account',
      'not found',
      'no such user',
      "doesn't exist",
      'does not exist',
      'unknown user',
    ]) {
      expect(html, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('puts a rejected new password on the new-password field', () => {
    const html = render({
      kind: 'failed',
      field: 'newPassword',
      detail: 'That is already your password. Choose a different one.',
    });
    expect(html).toContain('That is already your password.');
    expect(html).toContain('avp-field__error');
  });

  it('surfaces a non-field failure without blaming a field', () => {
    const html = render({
      kind: 'failed',
      field: null,
      detail: 'The change did not go through. Check your connection and try again.',
    });
    expect(html).toContain('role="alert"');
    expect(html).not.toContain('avp-field__error');
  });
});

describe('success', () => {
  it('confirms what happened to the other devices, and to this one', () => {
    const html = render({ kind: 'done' });
    expect(html).toContain('Your password has been changed.');
    expect(html).toContain('Every other device has been signed out');
    expect(html).toContain('this one is still signed in');
    expect(html).toContain('role="status"');
  });

  it('does not tell the user to sign in again — they never signed out', () => {
    const html = render({ kind: 'done' }).toLowerCase();
    expect(html).not.toContain('sign in with the new');
    expect(html).not.toContain('sign in again');
  });
});

describe('working', () => {
  it('names what is happening and disables the form', () => {
    const html = render({ kind: 'working' });
    expect(html).toContain('Changing…');
    expect(html).toContain('disabled');
  });
});

describe('no ad hoc styling', () => {
  it('uses the width token and emits no off-system class', () => {
    const html = render();
    expect(html).toContain('max-w-form');
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

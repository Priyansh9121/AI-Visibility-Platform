/**
 * The reset-password screen — Epic 9.13, tested in Epic 9.14 (Part E backfill).
 *
 * THE PROPERTY THIS FILE EXISTS FOR
 * ---------------------------------
 * `test_password_reset.py` proves the BACKEND cannot distinguish an unknown
 * token from an expired one, an already-used one, or one belonging to a
 * suspended account: all four raise the same class, get the same `400`, and
 * carry the same detail string. That guarantee was never checked at the point
 * it reaches a person, and it is exactly the kind of guarantee a screen can
 * quietly undo — one extra branch, one helpful "that link has expired", and the
 * oracle the backend went out of its way to withhold is handed straight back.
 *
 * So the assertions below are about what this screen must NOT be able to say,
 * and about there being exactly ONE refusal state for it to say it in.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ResetPasswordView, type ResetState } from './ResetPasswordView';

/** The single sentence the API returns for all four refusals. */
const API_DETAIL =
  'That reset link is not valid. Links work once and expire after an hour.';

const render = (state: ResetState, password = '') =>
  renderToStaticMarkup(
    <ResetPasswordView
      state={state}
      password={password}
      onPassword={() => {}}
      onSubmit={() => {}}
      onGoToSignIn={() => {}}
    />,
  );

describe('the form', () => {
  it('asks for one password and nothing else', () => {
    const html = render({ kind: 'form' });
    expect(html).toContain('>New password<');
    expect(html.match(/type="password"/g)).toHaveLength(1);
    // No email field: whoever opened this link already proved which inbox.
    expect(html).not.toContain('type="email"');
    // And no current-password field — they do not know it, which is why they
    // are here. That is the whole difference from the authenticated change.
    expect(html).not.toContain('Current password');
  });

  it('states the password rule before it can be broken', () => {
    expect(render({ kind: 'form' })).toContain('At least 12 characters');
  });

  it('says what setting it will do to other sessions', () => {
    expect(render({ kind: 'form' })).toContain('signs out every other session');
  });

  it('shows nothing wrong before anything is submitted', () => {
    const html = render({ kind: 'form' });
    expect(html).not.toContain('avp-errorstate');
    expect(html).not.toContain('avp-field__error');
  });
});

describe('invalid and expired are INDISTINGUISHABLE on this screen', () => {
  it('renders byte-identically for every refusal, because the API sends one string', () => {
    // Four different causes, one response. If the screen ever branched on the
    // reason — or the API ever started sending four strings — this breaks.
    const unknown = render({ kind: 'invalid', detail: API_DETAIL });
    const expired = render({ kind: 'invalid', detail: API_DETAIL });
    const used = render({ kind: 'invalid', detail: API_DETAIL });
    const suspended = render({ kind: 'invalid', detail: API_DETAIL });
    expect(new Set([unknown, expired, used, suspended]).size).toBe(1);
  });

  it('has exactly ONE refusal state in the type, so there is nowhere to branch', () => {
    // A second refusal state is how "expired" and "unknown" would diverge, and
    // it would diverge in the view rather than in the API where the test is.
    const states: ResetState['kind'][] = [
      'form',
      'working',
      'done',
      'rejected',
      'invalid',
    ];
    // `rejected` is a PASSWORD failure, not a link failure — it keeps the form.
    expect(states.filter((k) => k === 'invalid')).toHaveLength(1);
  });

  it('titles the refusal without naming a reason', () => {
    const html = render({ kind: 'invalid', detail: API_DETAIL });
    expect(html).toContain('This reset link is not valid');
    // The title must not itself pick one of the four.
    const title = 'This reset link is not valid';
    for (const tell of ['expired', 'already used', 'unknown', 'not found', 'suspended']) {
      expect(title.toLowerCase(), `title names "${tell}"`).not.toContain(tell);
    }
  });

  it('passes the API detail through verbatim rather than re-authoring it', () => {
    // The API's sentence mentions the RULES ("work once and expire after an
    // hour") — a general statement, not a claim about this token. The screen
    // must not upgrade it into one, so it prints exactly what it was given.
    expect(render({ kind: 'invalid', detail: API_DETAIL })).toContain(API_DETAIL);
  });

  it('carries no copy of its own that could name a cause', () => {
    const html = render({ kind: 'invalid', detail: API_DETAIL });
    const ownCopy = html.replace(API_DETAIL, '').toLowerCase();
    for (const tell of [
      'has expired',
      'already been used',
      'already used',
      'no such link',
      'not found',
      'unknown token',
      'account is suspended',
      'was revoked',
    ]) {
      expect(ownCopy, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('offers a way onward rather than a dead end', () => {
    expect(render({ kind: 'invalid', detail: API_DETAIL })).toContain('Ask for a new link');
  });
});

describe('a rejected password keeps the link', () => {
  it('stays on the form and marks the field', () => {
    const html = render({
      kind: 'rejected',
      detail: 'String should have at least 12 characters',
    });
    expect(html).toContain('avp-field__error');
    expect(html).toContain('String should have at least 12 characters');
    // Crucially NOT the invalid-link screen. The link is fine; sending someone
    // to request a new one over a weak password is an unrecoverable dead end.
    expect(html).not.toContain('This reset link is not valid');
    expect(html).toContain('>New password<');
  });
});

describe('success', () => {
  it('says the sessions are gone and sends them to sign in', () => {
    const html = render({ kind: 'done' });
    expect(html).toContain('Password changed');
    expect(html).toContain('Every session that was open has been signed out');
    expect(html).toContain('Sign in with your new password');
  });

  it('does NOT claim the caller is still signed in', () => {
    // The opposite of `change-password`, deliberately: this flow proves control
    // of an inbox, not knowledge of a password, so it signs everyone out.
    const html = render({ kind: 'done' }).toLowerCase();
    expect(html).not.toContain('you will stay signed in');
    expect(html).not.toContain('this one is still signed in');
  });
});

describe('working', () => {
  it('names what is happening and disables the form', () => {
    const html = render({ kind: 'working' });
    expect(html).toContain('Setting your password…');
    expect(html).toContain('disabled');
  });
});

describe('no workspace chrome for someone with no session', () => {
  it('renders no sidebar in any state — the rule /share/{token} follows', () => {
    for (const state of [
      { kind: 'form' } as const,
      { kind: 'done' } as const,
      { kind: 'invalid', detail: API_DETAIL } as const,
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

describe('the card arrives — Epic 9.16', () => {
  // Rendered WITH animation on, which is what a browser gets. The rest of this
  // file renders the default, so it would see the finished markup either way.
  const live = (state: ResetState = { kind: 'form' }) => render(state);
  const still = (state: ResetState = { kind: 'form' }) =>
    renderToStaticMarkup(
      <ResetPasswordView
        state={state}
        password=""
        onPassword={() => {}}
        onSubmit={() => {}}
        onGoToSignIn={() => {}}
        animate={false}
      />,
    );

  it('wraps the card in a single reveal, not a stagger', () => {
    const out = live();
    expect(out).toContain('avp-reveal');
    // One object cannot be a sequence, so no sibling offsets are emitted.
    expect(out).not.toContain('--avp-reveal-index');
  });

  it('adds no box — the layout classes stay on the same element', () => {
    // `Reveal` REPLACES the card column rather than nesting inside it.
    expect(live()).toMatch(/class="avp-reveal[^"]*max-w-form/);
  });

  it('leaves the <main> landmark alone', () => {
    const out = live();
    expect(out).toMatch(/<main[^>]*max-w-page/);
    expect(out).not.toMatch(/<main[^>]*avp-reveal/);
  });

  it('starts hidden and renders finished when motion is off', () => {
    expect(live()).not.toContain('avp-reveal--revealed');
    expect(still()).toContain('avp-reveal--revealed');
  });

  it('reveals all three states, not only the form', () => {
    // This screen has three branches where the panels had one or two. A
    // wrapper missed in one of them is invisible until somebody redeems a
    // dead link and watches the page not move.
    for (const state of [
      { kind: 'form' },
      { kind: 'done' },
      { kind: 'invalid', detail: API_DETAIL },
    ] as ResetState[]) {
      expect(live(state), `${state.kind} does not reveal`).toContain('avp-reveal');
      expect(still(state), `${state.kind} does not settle`).toContain('avp-reveal--revealed');
    }
  });

  it('still carries every word it carried before', () => {
    expect(live()).toContain('Choose a new password');
    expect(live({ kind: 'done' })).toContain('Password changed');
    expect(live({ kind: 'invalid', detail: API_DETAIL })).toContain(
      'This reset link is not valid',
    );
    expect(live({ kind: 'invalid', detail: API_DETAIL })).toContain(API_DETAIL);
  });
});

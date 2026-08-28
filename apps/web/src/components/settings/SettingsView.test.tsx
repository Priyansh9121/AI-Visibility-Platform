/**
 * The settings screen — Epic 9.13, tested in Epic 9.14 (Part E backfill).
 *
 * This screen shipped with zero component tests because every state that
 * mattered lived inside an effect the static renderer never runs. Splitting the
 * pure `SettingsView` out of the route is what makes them reachable — the same
 * split `DashboardView` and `ReportView` already use.
 *
 * The load-bearing assertion is the last group. This screen's whole reason to
 * exist is that it says what is NOT built rather than hiding it, and the
 * failure mode is a line that stays on the list after the thing ships. So the
 * test asserts in both directions: seat management must be gone from the
 * missing list, and the three genuinely-absent things must still be on it.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { SettingsView, type SettingsState } from './SettingsView';
import {
  memberMe,
  ownerMe,
  seatsWithPending,
} from '@/lib/settings/__fixtures__/seats';

const render = (state: SettingsState) =>
  renderToStaticMarkup(<SettingsView state={state} onChanged={() => {}} />);

const READY: SettingsState = {
  kind: 'ready',
  me: ownerMe,
  seats: seatsWithPending,
  seatsError: null,
};

describe('loading', () => {
  it('says what is being waited on, with no spinner', () => {
    const html = render({ kind: 'loading' });
    expect(html).toContain('Loading your settings…');
    expect(html).toContain('role="status"');
    expect(html).not.toContain('avp-errorstate');
  });

  it('still renders the shell, so navigation works before data arrives', () => {
    const html = render({ kind: 'loading' });
    expect(html).toContain('avp-shell');
    expect(html).toContain('href="/dashboard"');
  });
});

describe('error', () => {
  it('renders one error state carrying the supplied copy', () => {
    const html = render({
      kind: 'error',
      title: 'Sign in to see your settings',
      detail: 'No session cookie was supplied.',
    });
    expect(html).toContain('Sign in to see your settings');
    expect(html).toContain('No session cookie was supplied.');
    expect(html).toContain('role="alert"');
  });

  it('shows no agency facts it does not have', () => {
    const html = render({ kind: 'error', title: 'Nope', detail: 'Nope.' });
    expect(html).not.toContain('Northlight Partners');
    expect(html).not.toContain('Signed in as');
  });
});

describe('ready', () => {
  it('states the agency, the account and the seat position', () => {
    const html = render(READY);
    expect(html).toContain('Northlight Partners');
    expect(html).toContain('dana@northlight.example');
    expect(html).toContain('3 of 3');
  });

  it('names the agency, never us', () => {
    expect(render(READY)).not.toContain('AI Visibility Platform');
  });

  it('renders the seat panel rather than a promise of one', () => {
    const html = render(READY);
    expect(html).toContain('Who can sign in');
    expect(html).toContain('Invite someone');
    expect(html).toContain('Sam Okafor');
  });
});

describe('the seat list is allowed to fail on its own', () => {
  it('keeps the rest of the screen when the roster is forbidden', () => {
    const html = render({
      kind: 'ready',
      me: memberMe,
      seats: null,
      seatsError: 'Only an owner or an admin can see and change who holds a seat.',
    });
    // Identity survives — losing the whole page over a permission a member was
    // never meant to have would be the wrong trade.
    expect(html).toContain('kit@northlight.example');
    expect(html).toContain('Only an owner or an admin');
    expect(html).not.toContain('Invite someone');
  });
});

describe('the "not built yet" list is maintained in both directions', () => {
  it('no longer claims seat management is missing', () => {
    const html = render(READY);
    expect(html).not.toContain('the HTTP endpoints do not');
    expect(html).not.toContain('Inviting or removing seats');
  });

  it('no longer claims an authenticated password change is missing', () => {
    const html = render(READY);
    expect(html).not.toContain('an authenticated change is a different endpoint');
    expect(html).not.toContain('Changing your password while signed in.');
    // The real form is there instead.
    expect(html).toContain('Change your password');
    expect(html).toContain('>Current password<');
  });

  it('still names the things that genuinely are missing', () => {
    const html = render(READY);
    expect(html).toContain('white-labelling is still name-and-slug only');
    expect(html).toContain('Changing the seat limit');
    expect(html).toContain('Billing, plans and usage limits');
  });

  it('promises no dates and offers no controls for what is absent', () => {
    const html = render(READY).toLowerCase();
    for (const tell of ['coming soon', 'shortly', 'next release', 'roadmap']) {
      expect(html, `found "${tell}"`).not.toContain(tell);
    }
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value and no raw-palette class', () => {
    const html = render(READY);
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

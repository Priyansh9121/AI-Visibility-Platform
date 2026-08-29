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
import {
  activeSubscription,
  noSubscription,
} from '@/lib/settings/__fixtures__/billing';

const render = (state: SettingsState) =>
  renderToStaticMarkup(<SettingsView state={state} onChanged={() => {}} />);

const READY: SettingsState = {
  kind: 'ready',
  me: ownerMe,
  seats: seatsWithPending,
  seatsError: null,
  billing: noSubscription,
  billingError: null,
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
      billing: null,
      billingError: 'Only an owner or an admin can see and change billing.',
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

  it('no longer claims billing is missing', () => {
    const html = render(READY);
    expect(html).not.toContain('Billing, plans and usage limits');
    expect(html).not.toContain('records pricing as undecided');
    // The real thing is there instead.
    expect(html).toContain('What you pay');
    expect(html).toContain('$29 a month');
  });

  it('still names the things that genuinely are missing', () => {
    const html = render(READY);
    expect(html).toContain('white-labelling is still name-and-slug only');
    expect(html).toContain('Changing the seat limit');
    expect(html).toContain('Usage limits and any view of how much you have used');
  });

  it('promises no dates and offers no controls for what is absent', () => {
    const html = render(READY).toLowerCase();
    for (const tell of ['coming soon', 'shortly', 'next release', 'roadmap']) {
      expect(html, `found "${tell}"`).not.toContain(tell);
    }
  });
});

describe('billing', () => {
  it('says plainly that nothing is behind the plan', () => {
    const html = render(READY);
    expect(html).toContain('subscribing is how you pay for this, not how you unlock it');
    expect(html).toContain('there is nothing behind this that you are missing');
  });

  it('offers the plan card when there is no subscription', () => {
    const html = render(READY);
    expect(html).toContain('No subscription');
    expect(html).toContain('Subscribe');
    expect(html).toContain('$29');
  });

  it('states the renewal date when a subscription is live', () => {
    const html = render({ ...READY, billing: activeSubscription });
    expect(html).toContain('Active');
    expect(html).toContain('Renews 21 Sep 2026');
    // And stops selling to someone who has already bought.
    expect(html).not.toContain('>Subscribe<');
  });

  it('degrades on its own when a member may not read it', () => {
    const html = render({
      kind: 'ready',
      me: memberMe,
      seats: null,
      seatsError: 'nope',
      billing: null,
      billingError: 'Only an owner or an admin can see and change billing.',
    });
    // The rest of the page survives, exactly as it does for the seat roster.
    expect(html).toContain('kit@northlight.example');
    expect(html).toContain('Only an owner or an admin can see and change billing.');
  });

  it('never renders an unsubscribed agency as broken', () => {
    const html = render(READY).toLowerCase();
    // WORD-BOUNDED, and the reason is in the file already: a bare
    // `toContain('rated')` once matched inside "Generated". Here a bare
    // `toContain('locked')` matched inside the password panel's "an unlocked
    // laptop is a valid session too" — an honest sentence in a different
    // section. The tell is the whole word, not the letters.
    for (const tell of ['upgrade to unlock', 'locked', 'restricted', 'suspended']) {
      expect(html, `found "${tell}"`).not.toMatch(new RegExp(`\\b${tell}\\b`));
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

describe('Settings deliberately did NOT get arrival motion — Epic 9.16', () => {
  /*
   * Stated as a test because it is a scope decision, not an oversight.
   * Settings is a dense, functional screen — five PageSections of roster,
   * password and billing — where arrival motion costs attention and buys
   * nothing. The landing page and the auth cards were given it; this was not.
   *
   * The mechanism is `PageSection`'s `stagger` prop defaulting to false, which
   * this screen never sets. Without the assertion, flipping that default would
   * animate Settings and nothing would notice.
   */
  it('emits no reveal markup at all', () => {
    const html = render(READY);
    expect(html).not.toContain('avp-reveal');
    expect(html).not.toContain('--avp-reveal-index');
  });

  it('still renders its sections, so the assertion is not passing on absence', () => {
    const html = render(READY);
    expect(html).toContain('avp-section');
    expect(html).toContain('What you pay');
  });
});

/**
 * Width and density — Epic 9.19.
 *
 * design-direction.md §0 splits every screen into Working and Presenting, and
 * this is the screen that was on the wrong side of it: an operator's account
 * and seat roster, rendered at `--avp-report-width`, which is the measure a
 * DOCUMENT is read at. The dashboard and clients have been `wide` since Epic
 * 9.13. This is the correction and the test that keeps it.
 */
describe('Settings is a Working screen', () => {
  it('takes the app width rather than the report measure', () => {
    expect(render(READY)).toContain('avp-shell__content--wide');
  });

  it('is wide in every state, including the ones with no data', () => {
    // The shell is rendered by all three branches, so a loading or errored
    // Settings must not narrow back to the document measure mid-session.
    expect(render({ kind: 'loading' })).toContain('avp-shell__content--wide');
    expect(
      render({ kind: 'error', title: 'nope', detail: 'nope' }),
    ).toContain('avp-shell__content--wide');
  });

  it('nothing inside grows unbounded with it', () => {
    // The width buys table columns and the agency grid, not 90rem-long lines:
    // every panel still caps its own prose and its own forms.
    const html = render(READY);
    expect(html).toContain('max-w-measure');
    expect(html).toContain('max-w-form');
  });

  it('sets the four agency facts as a grid, not a tall thin column', () => {
    const html = render(READY);
    expect(html).toContain('lg:grid-cols-4');
    expect(html).toContain('Signed in as');
  });
});

describe('what is still missing is a numbered list, not small print', () => {
  it('numbers each entry in the report fix list idiom', () => {
    const html = render(READY);
    expect(html).toContain('>01<');
    expect(html).toContain('>02<');
    expect(html).toContain('>03<');
  });

  it('is an ordered list, because the numbers are structure and not decoration', () => {
    expect(render(READY)).toContain('<ol');
  });

  it('still says all three things, word for word', () => {
    // The point of the section is that it is true, so a visual pass must not
    // quietly drop an entry while restyling it.
    const html = render(READY);
    expect(html).toContain('white-labelling is still name-and-slug only');
    expect(html).toContain('there is no number here to change yet');
    expect(html).toContain('there is no cap to hit and no figure to look at');
  });
});

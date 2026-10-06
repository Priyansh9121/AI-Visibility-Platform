/**
 * "Send feedback" on Settings — 2026-10-06.
 *
 * Settings is the second place the link lives, with a few words about what it does, and it is
 * drawn from the same `me` the screen already holds: no request of its own. Like the footer it is
 * there only for an address the API sent. With none, which is the default, the screen is exactly
 * what it was: no section, no link, no word about feedback.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { SettingsView, type SettingsState } from './SettingsView';
import { memberMe, ownerMe, seatsWithPending } from '@/lib/settings/__fixtures__/seats';
import { noSubscription } from '@/lib/settings/__fixtures__/billing';

// Settings is where the router says the user is. Set from the start, and not only in `beforeEach`:
// the `describe` blocks below draw their markup while the file is collected, before any test runs.
const route = vi.hoisted(() => ({ pathname: '/settings' as string | null }));
vi.mock('next/navigation', () => ({ usePathname: () => route.pathname }));

const ADDRESS = 'pilot-feedback@avp-pilot.example';

const ready = (feedbackEmail: string | null, me = ownerMe): SettingsState => ({
  kind: 'ready',
  me: { ...me, feedbackEmail },
  seats: seatsWithPending,
  seatsError: null,
  billing: noSubscription,
  billingError: null,
});

const render = (state: SettingsState) =>
  renderToStaticMarkup(<SettingsView state={state} onChanged={() => {}} />);

const mailtos = (html: string) => [...html.matchAll(/href="(mailto:[^"]*)"/g)].map((m) => m[1]!);

/** The text of some markup as a reader sees it: the tags dropped and the entities React writes undone. */
const textOf = (markup: string) =>
  markup
    .replace(/<[^>]+>/g, '')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');

/** The exact words under the Feedback heading (round 2: four short sentences, and what to do if nothing opens). */
const LEAD =
  `Opens an email to ${ADDRESS} in your email app. The subject names the kind of screen you are on, ` +
  "never a client's name or an id. Nothing is sent until you send it. " +
  'If nothing opens, write to that address yourself.';

/** The Feedback section's markup: from its eyebrow to the next section's. */
function section(html: string): string {
  const start = html.indexOf('avp-section__eyebrow">Feedback<');
  expect(start, 'no Feedback section').toBeGreaterThan(-1);
  const end = html.indexOf('<section', start);
  return html.slice(start, end === -1 ? undefined : end);
}

beforeEach(() => {
  route.pathname = '/settings';
});

describe('with an address, Settings has a Feedback section', () => {
  const html = render(ready(ADDRESS));

  it('says what the link does, exactly, and names the address', () => {
    const body = section(html);
    expect(body).toContain('Tell us what you think.');
    const lead = textOf(body.match(/<p class="avp-section__lead">(.*?)<\/p>/)![1]!);
    expect(lead).toBe(LEAD);
  });

  it('says it in four short sentences, the last for somebody whose email app does not open', () => {
    const sentences = LEAD.split(/(?<=\.)\s+/);
    expect(sentences).toEqual([
      `Opens an email to ${ADDRESS} in your email app.`,
      "The subject names the kind of screen you are on, never a client's name or an id.",
      'Nothing is sent until you send it.',
      'If nothing opens, write to that address yourself.',
    ]);
  });

  it('draws the link in the section, beside the one in the sidebar’s footer', () => {
    expect(mailtos(section(html))).toHaveLength(1);
    expect(mailtos(html)).toHaveLength(2);
    // Each is called "Send feedback by email": the words on screen, then a hidden "by email".
    const names = [...html.matchAll(/<a [^>]*href="mailto:[^>]*>(.*?)<\/a>/g)].map((m) => textOf(m[1]!));
    expect(names).toEqual(['Send feedback by email', 'Send feedback by email']);
  });

  it('writes the same subject from both, which is the screen the user is on', () => {
    const subjects = mailtos(html).map((href) => new URL(href).searchParams.get('subject'));
    expect(subjects).toEqual(['AVP feedback: /settings', 'AVP feedback: /settings']);
  });

  it('comes after Billing and before the list of what is not built', () => {
    const billing = html.indexOf('avp-section__eyebrow">Billing<');
    const feedback = html.indexOf('avp-section__eyebrow">Feedback<');
    const missing = html.indexOf('avp-section__eyebrow">Not built yet<');
    expect(billing).toBeGreaterThan(-1);
    expect(billing).toBeLessThan(feedback);
    expect(feedback).toBeLessThan(missing);
  });

  it('is offered to a member as much as to an owner: it is for every signed-in user', () => {
    const asMember = render(ready(ADDRESS, memberMe));
    expect(mailtos(asMember)).toHaveLength(2);
    expect(section(asMember)).toContain(ADDRESS);
  });

  it('is in no loading or error state, which have no address to offer', () => {
    for (const state of [
      { kind: 'loading' } as const,
      { kind: 'error', title: 'Sign in to see your settings', detail: 'No session.' } as const,
    ]) {
      const bare = render(state);
      expect(mailtos(bare)).toEqual([]);
      expect(bare.toLowerCase()).not.toContain('feedback');
    }
  });
});

describe('with no address Settings is exactly what it was', () => {
  const without = render(ready(null));

  it.each([
    ['empty', ''],
    ['blank', '   '],
    ['not an address', 'pilot.example'],
    ['two addresses', 'a@pilot.example, b@pilot.example'],
  ])('%s draws byte for byte what null does', (_what, value) => {
    expect(render(ready(value))).toBe(without);
  });

  it('has no Feedback section, no link and no word about feedback', () => {
    expect(without).not.toContain('avp-section__eyebrow">Feedback<');
    expect(mailtos(without)).toEqual([]);
    expect(without.toLowerCase()).not.toContain('feedback');
  });

  it('still has every section it had', () => {
    for (const eyebrow of ['Settings', 'Appearance', 'Team', 'Your account', 'Billing', 'Not built yet']) {
      expect(without).toContain(`avp-section__eyebrow">${eyebrow}<`);
    }
  });
});

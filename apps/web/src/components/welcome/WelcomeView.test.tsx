/**
 * The onboarding screen — Epic 9.13, tested in Epic 9.14 (Part E backfill).
 *
 * The assertions that matter are about what this wizard deliberately does NOT
 * have, because all three were decisions recorded in Epic 9.13's build log and
 * all three are the kind of thing a later edit adds back without noticing:
 *
 *   - **no scan button of its own.** `ClassificationResult` grew one in 9.12,
 *     and a second would be the second scan-triggering path this project has
 *     spent two epics avoiding.
 *   - **no special treatment for the agency's own client.** Nothing in the
 *     data model records "this one is ours", so a badge would have to be
 *     inferred, and inferring it from the name is wrong for any agency whose
 *     trading name differs from its URL.
 *   - **a way out at every step.** Someone who signed up to scan a prospect
 *     should not be held here.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { WelcomeView, type WelcomeState } from './WelcomeView';
import { clientsMe } from '@/lib/clients/__fixtures__/clients';
import type { ClientDetail, Me } from '@avp/shared-types';

const CLASSIFIED = {
  id: 'clnt_01AAA',
  agencyId: 'agcy_01NORTHLIGHT',
  kind: 'client',
  name: 'northlight.example',
  domain: 'northlight.example',
  brandName: 'Northlight Partners',
  industry: 'Digital marketing agency',
  industryNiche: 'AI visibility',
  classificationStatus: 'classified',
  classificationConfidence: '0.92',
  crawl: null,
  createdAt: '2026-08-28T09:00:00Z',
  updatedAt: '2026-08-28T09:00:00Z',
} as unknown as ClientDetail;

const render = (state: WelcomeState, me: Me | null = clientsMe) =>
  renderToStaticMarkup(
    <WelcomeView
      state={state}
      me={me}
      onStarted={() => {}}
      onClassified={() => {}}
      onReset={() => {}}
    />,
  );

describe('loading', () => {
  it('names what is being waited on', () => {
    const html = render({ kind: 'loading' });
    expect(html).toContain('Setting up your workspace…');
    expect(html).toContain('role="status"');
  });

  it('shows no form until the session is known', () => {
    expect(render({ kind: 'loading' })).not.toContain('avp-field');
  });
});

describe('the ask', () => {
  it('greets the agency by name', () => {
    expect(render({ kind: 'ask' })).toContain('Welcome, Northlight Partners');
  });

  it('degrades to a plain greeting when identity has not arrived', () => {
    const html = render({ kind: 'ask' }, null);
    expect(html).toContain('Welcome');
    expect(html).not.toContain('Welcome, ');
  });

  it('explains why to start with your own site', () => {
    const html = render({ kind: 'ask' });
    expect(html).toContain('Start with a site you already know.');
    expect(html).toContain('You know that site better than any prospect');
  });

  it('renders the existing intake form rather than a forked one', () => {
    // `IntakeForm` unmodified — the whole point of this screen being
    // orchestration. Its own label is the proof it was not re-authored.
    expect(render({ kind: 'ask' })).toContain('avp-field');
  });
});

describe('working', () => {
  it('names the steps actually happening, with no progress bar', () => {
    const html = render({ kind: 'working' });
    expect(html).toContain('Reading the site');
    expect(html).toContain('Fetching the homepage and a few key pages');
    expect(html).toContain('Working out the industry and brand name');
    // The rule intake set in Epic 2: this product cannot measure real
    // progress, and a bar that fills on a timer is a lie the user catches.
    expect(html).not.toContain('role="progressbar"');
    expect(html).not.toContain('<progress');
  });

  it('is honest about the wait rather than silent about it', () => {
    expect(render({ kind: 'working' })).toContain('This usually takes a few seconds.');
  });
});

describe('the result', () => {
  it('renders the classification and a way onward', () => {
    const html = render({ kind: 'result', client: CLASSIFIED });
    expect(html).toContain('Northlight Partners');
    expect(html).toContain('That is your workspace set up.');
    expect(html).toContain('Go to your dashboard');
  });

  it('says the agency’s own client is an ordinary row', () => {
    const html = render({ kind: 'result', client: CLASSIFIED });
    expect(html).toContain('it is an ordinary client, with nothing special about it');
  });

  it('gives the agency’s own client no badge beyond its classification', () => {
    // Nothing in the data model records "this one is ours". A badge would have
    // to be inferred, and inferring it from the name is wrong for any agency
    // whose trading name differs from its URL.
    //
    // Asserted on BADGES rather than on words: the copy legitimately says "your
    // own site" — in the sentence that exists to state the client is ordinary —
    // so a word sweep would fail on the very reassurance it is checking for.
    // What must not appear is a second badge marking the row as special.
    const html = render({ kind: 'result', client: CLASSIFIED });
    // Matched on the class ATTRIBUTE, not the token: an element renders as
    // `class="avp-badge avp-badge--success"`, so counting bare occurrences of
    // `avp-badge` counts each element twice.
    const badges = html.match(/class="avp-badge/g) ?? [];
    expect(badges).toHaveLength(1);
    expect(html).toContain('avp-badge--success');
    expect(html).toContain('>Identified<');
  });

  it('never labels the row as the agency’s own', () => {
    const html = render({ kind: 'result', client: CLASSIFIED }).toLowerCase();
    for (const tell of ['this is you', 'your account', 'pinned', 'owned by you']) {
      expect(html, `found "${tell}"`).not.toContain(tell);
    }
  });
});

describe('the wizard adds no second scan-triggering path', () => {
  it('has no scan button of its own at any step', () => {
    for (const state of [
      { kind: 'ask' } as const,
      { kind: 'working' } as const,
      { kind: 'result', client: CLASSIFIED } as const,
    ]) {
      const html = render(state);
      // `ClassificationResult` supplies "Run a scan" itself, from Epic 9.12.
      // What must not appear is a SECOND one authored by the wizard.
      expect((html.match(/Run a scan/g) ?? []).length).toBeLessThanOrEqual(1);
    }
  });
});

describe('it is skippable', () => {
  it('offers a way out before a client exists', () => {
    const html = render({ kind: 'ask' });
    expect(html).toContain('Would rather start with a prospect?');
    expect(html).toContain('Skip for now');
  });

  it('offers a way out after one does', () => {
    expect(render({ kind: 'result', client: CLASSIFIED })).toContain(
      'Go to your dashboard',
    );
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value and no raw-palette class', () => {
    const html = render({ kind: 'result', client: CLASSIFIED });
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

describe('the step arrives — Epic 9.16', () => {
  const props = {
    state: { kind: 'ask' } as const,
    me: null,
    onStarted: () => {},
    onClassified: () => {},
    onReset: () => {},
  };

  it('reveals the whole step as one unit, with no stagger', () => {
    const out = renderToStaticMarkup(<WelcomeView {...props} />);
    expect(out).toContain('avp-reveal');
    // Someone who has just chosen a password is waiting to get on with
    // something. A four-beat sequence here would be nothing happening at
    // exactly the wrong moment.
    expect(out).not.toContain('--avp-reveal-index');
  });

  it('renders finished when motion is off', () => {
    const out = renderToStaticMarkup(<WelcomeView {...props} animate={false} />);
    expect(out).toContain('avp-reveal--revealed');
  });

  it('still says everything it said before', () => {
    const out = renderToStaticMarkup(<WelcomeView {...props} />);
    expect(out).toContain('Start with a site you already know.');
  });
});

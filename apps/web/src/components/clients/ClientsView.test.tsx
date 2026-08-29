/**
 * The clients screen — Epic 9.13, tested in Epic 9.14 (Part E backfill).
 *
 * Shipped with zero component tests because its states lived inside an effect
 * a static render never runs. Splitting `ClientsView` out of the route is what
 * made them reachable.
 *
 * Two assertions carry weight beyond "it renders". A missing industry must not
 * become a bare dash that reads like data — the same rule `ScoreMeter` follows
 * for a null score. And the "most recent page only" copy must stay true: it is
 * a claim about the API's sort order, and if that order ever flipped the
 * sentence would be a lie the screen tells confidently.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClientsView, type ClientsState } from './ClientsView';
import {
  clientsMe,
  oneClient,
  severalClients,
  unreadableClient,
} from '@/lib/clients/__fixtures__/clients';

const render = (state: ClientsState, me = clientsMe) =>
  renderToStaticMarkup(<ClientsView state={state} me={me} />);

const READY: ClientsState = { kind: 'ready', clients: severalClients, more: false };

describe('loading', () => {
  it('names what is being waited on, with no spinner', () => {
    const html = render({ kind: 'loading' });
    expect(html).toContain('Loading your clients…');
    expect(html).toContain('role="status"');
    expect(html).not.toContain('avp-table');
  });

  it('renders the shell, so navigation works before the data arrives', () => {
    const html = render({ kind: 'loading' });
    expect(html).toContain('avp-shell');
    expect(html).toContain('href="/dashboard"');
  });
});

describe('error', () => {
  it('renders one error state with the supplied copy', () => {
    const html = render({
      kind: 'error',
      title: 'Sign in to see your clients',
      detail: 'Reports are scoped to the agency that ran the scan.',
    });
    expect(html).toContain('Sign in to see your clients');
    expect(html).toContain('role="alert"');
    expect(html).not.toContain('avp-table');
  });
});

describe('empty', () => {
  it('writes an empty state rather than an empty grid', () => {
    const html = render({ kind: 'ready', clients: [], more: false });
    expect(html).toContain('No businesses yet');
    expect(html).toContain('avp-empty');
  });

  it('offers the way out rather than naming it in prose — Epic 9.19', () => {
    // The copy used to end "Use Compare to scan the first one", which named a
    // destination the sentence could not reach. The empty state now carries
    // the control itself.
    const html = render({ kind: 'ready', clients: [], more: false });
    expect(html).toContain('Scan the first business');
    expect(html).toContain('avp-btn--primary');
  });

  it('does not draw header figures over an empty list', () => {
    // Three zeroes is not a summary, it is furniture.
    const html = render({ kind: 'ready', clients: [], more: false });
    expect(html).not.toContain('Awaiting review');
  });

  it('counts zero honestly rather than hiding the heading', () => {
    expect(render({ kind: 'ready', clients: [], more: false })).toContain('0 businesses');
  });

  it('says "One business" rather than "1 businesses"', () => {
    const html = render({ kind: 'ready', clients: oneClient, more: false });
    expect(html).toContain('One business');
    expect(html).not.toContain('1 businesses');
  });
});

describe('ready', () => {
  it('lists every client with its domain', () => {
    const html = render(READY);
    expect(html).toContain('Northaven Dental');
    expect(html).toContain('northaven-dental.com');
    expect(html).toContain('quietbrook.io');
  });

  it('prefers the brand name and falls back to the record name', () => {
    const html = render(READY);
    expect(html).toContain('Meridian Labs');
    // The unreadable client has no brandName, so its `name` is shown.
    expect(html).toContain('quietbrook.io');
  });

  it('renders each classification status as its own state', () => {
    const html = render(READY);
    expect(html).toContain('Identified');
    expect(html).toContain('Could not read');
    expect(html).toContain('Needs review');
    expect(html).toContain('Pending');
  });

  it('names the agency in the shell, never us', () => {
    const html = render(READY);
    expect(html).toContain('Northlight Partners');
    expect(html).not.toContain('AI Visibility Platform');
  });
});

describe('a missing industry is a sentence, not a dash', () => {
  it('says "Not identified" where classification found nothing', () => {
    const html = render({ kind: 'ready', clients: [unreadableClient], more: false });
    expect(html).toContain('Not identified');
    // A bare em dash in a data column reads as a value. The dashboard's
    // ScoreMeter settled this for a null score; the rule is the same here.
    expect(html).not.toContain('>—<');
  });
});

describe('the truncation notice is a claim about sort order', () => {
  it('appears only when there is another page', () => {
    expect(render({ ...READY, more: true } as ClientsState)).toContain(
      'Showing the most recent page only.',
    );
    expect(render(READY)).not.toContain('Showing the most recent page only.');
  });

  it('says MOST RECENT, which is what the endpoint actually returns', () => {
    // `routers/clients.py` orders `id DESC` over ULIDs, so page one is the
    // newest. `test_intake.py::test_list_paginates_newest_first` has asserted
    // that since Epic 2. A previous review flagged this copy as possibly
    // backwards; it is not, and this is where the frontend records that.
    const html = render({ ...READY, more: true } as ClientsState);
    expect(html).toContain('most recent');
    expect(html).not.toContain('oldest');
  });
});

describe('it ends at the boundary of what is real', () => {
  it('offers no rename, delete, or bulk action — no endpoint exists', () => {
    const html = render(READY).toLowerCase();
    for (const tell of ['rename', 'delete', 'archive', 'select all', 'bulk']) {
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

/**
 * The header figures — Epic 9.19.
 *
 * A visual pass, so the assertions are about what is CLAIMED rather than about
 * layout: each figure has to be a count of the same `classificationStatus` the
 * badge column already renders, or the header is asserting something the table
 * below it contradicts.
 */
describe('the classification split in the header', () => {
  it('counts the states the rows already show', () => {
    // severalClients: one classified, one unclassifiable, one ambiguous, one
    // pending — so identified 1, awaiting review 2 (ambiguous + pending),
    // unreadable 1.
    const html = render(READY);
    expect(html).toContain('Identified');
    expect(html).toContain('Awaiting review');
    expect(html).toContain('Unreadable');
  });

  it('folds pending in with ambiguous rather than showing a fourth mostly-zero figure', () => {
    const ambiguousAndPending = severalClients.filter(
      (c) => c.classificationStatus === 'ambiguous' || c.classificationStatus === 'pending',
    ).length;
    expect(ambiguousAndPending).toBe(2);
    const html = render(READY);
    // The two are summed into one figure, so `2` appears under that label.
    expect(html).toContain('>Awaiting review</dt><dd class="text-ui-lg font-medium text-text-primary">2</dd>');
  });

  it('agrees with the badge column rather than counting something else', () => {
    const html = render({ kind: 'ready', clients: [unreadableClient], more: false });
    expect(html).toContain('Could not read');
    expect(html).toContain('>Unreadable</dt><dd class="text-ui-lg font-medium text-text-primary">1</dd>');
    expect(html).toContain('>Identified</dt><dd class="text-ui-lg font-medium text-text-primary">0</dd>');
  });
});

describe('width — design-direction.md §0', () => {
  it('is a Working screen and takes the app width, not the report measure', () => {
    expect(render(READY)).toContain('avp-shell__content--wide');
  });
});

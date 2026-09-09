/**
 * A client's own space — Epic 9.20.
 *
 * The three cases the brief names are all here: one scan, several, and a
 * competitor set that changed between them. What each asserts is that the
 * screen says something TRUE about thin or shifting data rather than drawing a
 * shape the data does not support.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ClientOverviewView,
  ClientRankingsView,
  ClientSourcesView,
  latestScanId,
  type ClientDetailState,
} from './ClientDetailView';
import type { Client, ClientHistory } from '@avp/shared-types';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import {
  noScanHistory,
  oneScanHistory,
  oneScanPlusDeadHistory,
  shiftingSetHistory,
  threeScanHistory,
} from '@/lib/client/__fixtures__/history';

const CLIENT = identifiedClient as Client;

const ready = (history: ClientHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});

const overview = (h: ClientHistory) =>
  renderToStaticMarkup(<ClientOverviewView state={ready(h)} me={clientsMe} />);
const sources = (h: ClientHistory) =>
  renderToStaticMarkup(<ClientSourcesView state={ready(h)} me={clientsMe} />);
const rankings = (h: ClientHistory) =>
  renderToStaticMarkup(<ClientRankingsView state={ready(h)} me={clientsMe} />);

describe('the hero’s facts take the fact’s shape — Epic 16.2', () => {
  it('sets the scan date, share of voice and rival count as chips beside the delta', () => {
    const html = overview(threeScanHistory);
    const meta = html.slice(html.indexOf('avp-hero__meta'), html.indexOf('avp-hero__aside'));
    expect(meta.match(/class="avp-chip"/g)?.length).toBe(3);
    expect(meta).toMatch(/avp-chip[^>]*>.*?Scanned /);
    expect(meta).toMatch(/share of voice/);
    expect(meta).toMatch(/rivals? in the set/);
    // The delta is a reading, not a credential: still text, still not a chip.
    expect(meta).toMatch(/avp-hero__delta/);
    expect(meta).not.toMatch(/avp-chip[^>]*>[^<]*since last scan/);
  });

  it('says the absence in a sentence, not a chip', () => {
    // Scans that were never scored: the hero renders, and its meta line is
    // an explanation rather than credentials.
    const unscored: ClientHistory = {
      ...threeScanHistory,
      scans: threeScanHistory.scans.map((s) => ({ ...s, composite: null })),
    };
    const html = overview(unscored);
    expect(html).toContain('No scan of this client has produced a reading.');
    const meta = html.slice(html.indexOf('avp-hero__meta'), html.indexOf('</section>'));
    expect(meta).not.toContain('avp-chip');
  });
});

describe('the space says whose it is, and how to leave', () => {
  it('names the client and its domain', () => {
    const html = overview(threeScanHistory);
    expect(html).toContain(CLIENT.brandName ?? CLIENT.name);
    expect(html).toContain(CLIENT.domain);
  });

  it('carries a way back to the list it came from', () => {
    expect(overview(threeScanHistory)).toContain('All clients');
  });

  it('sits inside the shell, which is in client mode — Epic 13', () => {
    const html = overview(threeScanHistory);
    expect(html).toContain('avp-shell');
    expect(html).toContain('avp-nav__head');
    expect(html).not.toContain('avp-localnav');
  });

  it('is a Working screen and takes the app width', () => {
    expect(overview(threeScanHistory)).toContain('avp-shell__content--wide');
  });

  it('marks exactly one section as current, per screen', () => {
    for (const html of [overview(threeScanHistory), sources(threeScanHistory), rankings(threeScanHistory)]) {
      expect((html.match(/aria-current="page"/g) ?? []).length).toBe(1);
    }
  });
});

describe('Report is a path into the existing document, not a copy of it', () => {
  it('links out to the newest scan report', () => {
    expect(latestScanId(threeScanHistory)).toBe('scan_3');
    expect(overview(threeScanHistory)).toContain('href="/scans/scan_3/report"');
  });

  it('renders no report of its own inside the client frame', () => {
    // The report is a Presenting-context document at --avp-report-width (72rem, Epic 17).
    // Rendering it inside a wide Working shell would change the artefact.
    const html = overview(threeScanHistory);
    expect(html).not.toContain('avp-report__title');
    expect(html).not.toContain('avp-beat');
  });

  it('omits the item entirely when there is no report to open', () => {
    // Not a dead nav item — `NavItem`'s rule, and it holds here too.
    const html = overview(noScanHistory);
    expect(latestScanId(noScanHistory)).toBeNull();
    expect(html).not.toContain('/report"');
  });
});

describe('one scan is stated, not drawn', () => {
  it('says so on Sources rather than plotting a single point', () => {
    const html = sources(oneScanHistory);
    expect(html).toContain('One scan is not a trend yet');
    expect(html).toContain('avp-empty');
    expect(html).not.toContain('avp-trend__svg');
  });

  it('says so on Rankings too', () => {
    const html = rankings(oneScanHistory);
    expect(html).toContain('One scan is not a trend yet');
    expect(html).not.toContain('avp-trend__svg');
  });

  it('names what would change it, in this product own voice for thin data', () => {
    // The Ledger's "Not enough data to score" and ScoreMeter's named absences
    // both state the fact and what would resolve it. This matches, rather than
    // inventing a new tone.
    expect(sources(oneScanHistory)).toContain('A second scan is what turns');
    expect(rankings(oneScanHistory)).toContain('Run another scan');
  });

  it('counts scans that produced nothing rather than hiding them', () => {
    const html = sources(oneScanPlusDeadHistory);
    expect(html).toContain('2 further scans produced no reading');
  });

  it('a client never scanned is a different sentence from one scanned once', () => {
    const html = overview(noScanHistory);
    expect(html).toContain('This client has not been scanned');
    expect(html).not.toContain('One scan is not a trend yet');
  });
});

describe('several scans draw a real trend', () => {
  it('Sources plots one line per domain with the client pinned in', () => {
    const html = sources(threeScanHistory);
    expect(html).toContain('avp-trend__svg');
    expect(html).toContain('plausible.io');
    expect(html).toContain('matomo.org');
  });

  it('Rankings plots share of voice on a 0-100 axis', () => {
    const html = rankings(threeScanHistory);
    expect(html).toContain('avp-trend__svg');
    expect(html).toContain('100%');
  });

  it('Rankings says WHY it is share of voice and not the composite', () => {
    // The reason is load-bearing: there is no per-competitor composite, so a
    // reader must not assume the lines are scores.
    expect(rankings(threeScanHistory)).toContain('no per-competitor composite');
  });

  it('both charts carry a data table for screen readers', () => {
    for (const html of [sources(threeScanHistory), rankings(threeScanHistory)]) {
      expect(html).toContain('avp-visually-hidden');
      expect(html).toContain('<table>');
    }
  });

  it('the overview lists the history oldest first, matching the charts', () => {
    const html = overview(threeScanHistory);
    expect(html).toContain('Oldest first');
    // Scoped to the TABLE. The nav's Report link points at the newest scan and
    // sits above the table, so searching the whole document finds scan_3 first
    // and says nothing about row order.
    const body = html.slice(html.indexOf('<tbody>'));
    expect(body.indexOf('scan_1')).toBeLessThan(body.indexOf('scan_3'));
  });
});

describe('a competitor set that changed does not break or silently drop a line', () => {
  it('keeps a rival that vanished for one scan', () => {
    const html = rankings(shiftingSetHistory);
    expect(html).toContain('Fathom Analytics');
  });

  it('keeps a rival that only appeared in the last scan', () => {
    expect(rankings(shiftingSetHistory)).toContain('Seline');
  });

  it('explains the gaps in words instead of leaving them to be noticed', () => {
    const html = rankings(shiftingSetHistory);
    // React escapes the apostrophe, so the assertion matches what is served.
    expect(html).toContain('were not in every scan&#x27;s competitor set');
    expect(html).toContain('not measured');
  });

  it('says nothing about gaps when the set never changed', () => {
    expect(rankings(threeScanHistory)).not.toContain('not in every scan&#x27;s competitor set');
  });

  it('still renders a chart rather than falling over', () => {
    expect(rankings(shiftingSetHistory)).toContain('avp-trend__svg');
    expect(sources(shiftingSetHistory)).toContain('avp-trend__svg');
  });
});

describe('loading and failure', () => {
  it('renders the shell while loading, so navigation works before data', () => {
    const html = renderToStaticMarkup(
      <ClientOverviewView state={{ kind: 'loading' }} me={clientsMe} />,
    );
    expect(html).toContain('avp-shell');
    expect(html).toContain('role="status"');
  });

  it('offers a way back when the client cannot be loaded', () => {
    const html = renderToStaticMarkup(
      <ClientOverviewView
        state={{ kind: 'error', title: 'No such client', detail: 'It does not exist.' }}
        me={clientsMe}
      />,
    );
    expect(html).toContain('No such client');
    expect(html).toContain('role="alert"');
    expect(html).toContain('Back to clients');
  });
});

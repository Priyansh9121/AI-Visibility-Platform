/**
 * The Alerts screen — Epic E.
 *
 * The assertion that matters most is the one that fails silently: an empty
 * feed presented as an all-clear when in fact nothing was ever comparable.
 * Nine of the eleven clients in the real database are in exactly that state.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClientAlertsView, type AlertsState } from './ClientAlertsView';
import type { ClientDetailState } from './ClientDetailView';
import type { Client, ClientHistory } from '@avp/shared-types';
import { accentFor } from './clientNav';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import { threeScanHistory } from '@/lib/client/__fixtures__/history';
import {
  allClearFeed,
  neverComparedFeed,
  partlyAcknowledgedFeed,
  toneDeclineFeed,
} from '@/lib/client/__fixtures__/alerts';

const CLIENT = identifiedClient as Client;

const ready = (history: ClientHistory = threeScanHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});

const render = (alerts: AlertsState, onAcknowledge?: (id: string) => void) =>
  renderToStaticMarkup(
    <ClientAlertsView
      state={ready()}
      alerts={alerts}
      me={clientsMe}
      {...(onAcknowledge ? { onAcknowledge } : {})}
    />,
  );

const feed = (f: typeof toneDeclineFeed): AlertsState => ({ kind: 'ready', feed: f });

describe('the tab exists and sits where B.1 reserved a seat for it', () => {
  it('joins the client’s LocalNav', () => {
    const html = render(feed(toneDeclineFeed));
    expect(html).toContain('href="/clients/clnt_01AAA/alerts"');
    expect(html).toContain('aria-current="page"');
  });

  it('wears its own cluster hue, taken from the nav table', () => {
    const html = render(feed(toneDeclineFeed));
    const accent = accentFor('alerts');
    expect(accent).not.toBeNull();
    expect(html).toContain(`--avp-bench-${accent! + 1}-600`);
  });

  it('sits in the Investigation cluster', () => {
    expect(render(feed(toneDeclineFeed))).toMatch(
      /<ul[^>]*aria-label="Investigation"/,
    );
  });
});

describe('an empty feed is never presented as an all-clear', () => {
  it('says nothing was compared when nothing was', () => {
    // The real state of nine of eleven clients in avp_dev: one scan each, so a
    // baseline can never exist and no alert can ever fire. Reporting "0
    // alerts" alone would claim they had been checked.
    const html = render(feed(neverComparedFeed));
    expect(html).toContain('Nothing compared yet');
    expect(html).toContain('Nothing has been checked');
    expect(html).toContain('not the same as nothing being wrong');
  });

  it('says so plainly when a comparison DID happen and found nothing', () => {
    const html = render(feed(allClearFeed));
    expect(html).toContain('Nothing has changed enough to report');
    expect(html).toContain('This is an all-clear, not an absence of data');
  });

  it('gives the two states different words', () => {
    const never = render(feed(neverComparedFeed));
    const clear = render(feed(allClearFeed));
    expect(never).not.toBe(clear);
    expect(never).not.toContain('all-clear');
  });

  it('names the baseline window, so the rule is legible', () => {
    expect(render(feed(neverComparedFeed))).toContain('20 hours older');
  });
});

describe('the feed', () => {
  it('explains why close-together scans are not compared', () => {
    // The correction the whole epic turns on, said to the operator rather than
    // buried in a service module.
    const html = render(feed(toneDeclineFeed));
    expect(html).toContain('measure the same reality twice');
  });

  it('shows each alert’s detail and which two scans it spans', () => {
    const html = render(feed(toneDeclineFeed));
    expect(html).toContain('Net tone fell 60%, from +10 to +4.');
    // Both timestamps, so "since when" needs no second request. Asserted on
    // the VALUE rather than on `dateTime=` — React's casing of that attribute
    // is a detail of the renderer, and the machine-readable stamp being
    // present is the thing that matters.
    expect(html).toContain('2026-08-31T02:39:36.000Z');
    expect(html).toContain('2026-08-29T04:55:27.000Z');
    expect(html).toContain('<time');
  });

  it('names the engine on a per-engine finding', () => {
    const html = render(feed(toneDeclineFeed));
    expect(html).toContain('Claude + search');
    expect(html).toContain('ChatGPT');
  });

  it('counts outstanding separately from acknowledged', () => {
    const html = render(feed(partlyAcknowledgedFeed));
    expect(html).toMatch(/Outstanding<\/dt><dd[^>]*>2</);
    expect(html).toMatch(/Acknowledged<\/dt><dd[^>]*>1</);
  });

  it('reports how many scans could be compared at all', () => {
    // 1 of 2 — the figure that stops a short feed reading as a quiet one.
    expect(render(feed(toneDeclineFeed))).toMatch(
      /Scans compared<\/dt><dd[^>]*>1 \/ 2</,
    );
  });

  it('hides acknowledged entries behind a toggle rather than deleting them', () => {
    const html = render(feed(partlyAcknowledgedFeed));
    expect(html).toContain('Show 1 acknowledged');
  });

  it('offers an acknowledge action only on outstanding entries', () => {
    const html = render(feed(partlyAcknowledgedFeed), () => {});
    const buttons = html.match(/Acknowledge</g) ?? [];
    // Two outstanding alerts, two buttons — the acknowledged one has none.
    expect(buttons.length).toBe(2);
  });

  it('offers no acknowledge action when the screen was given no handler', () => {
    expect(render(feed(toneDeclineFeed))).not.toContain('>Acknowledge<');
  });
});

describe('the craft pass — things that felt wrong in the hand', () => {
  it('hides an already-acknowledged row on a fresh load, but offers it', () => {
    /*
     * The row acknowledged in an EARLIER session is filtered out — an operator
     * arriving at the feed wants what is outstanding. What must not happen is
     * a row vanishing the moment it is acknowledged in THIS session, which is
     * what `emil-design-eng` caught: the row dropped out of the list under the
     * cursor and the rows below jumped up. On the real data that fires twice
     * in a row, because Notion's tone event put three alerts on one scan.
     *
     * That in-session behaviour needs a click, which a static render cannot
     * reach; the settled STYLING it depends on is asserted in the design
     * system, where the rule lives.
     */
    const html = render(feed(partlyAcknowledgedFeed), () => {});
    expect(html).not.toContain('Net tone fell 60%');
    expect(html).toContain('Show 1 acknowledged');
  });

  it('styles the engine as the discriminator it is', () => {
    // Three rows share the badge "Tone declined"; the engine is the only thing
    // telling them apart, so it cannot be the quietest text in the row. Since
    // Epic 16.2 it is a MetaChip — a fact beside a state — and never a badge,
    // so the two pills in the row cannot be read as two states.
    const html = render(feed(toneDeclineFeed));
    expect(html).toMatch(/avp-chip[^>]*>(?:<span[^>]*>.*?<\/span>)?Claude/);
    const rows = html.split(/class="[^"]*\bavp-alert\b[^"]*"/).slice(1);
    expect(rows.length).toBe(3);
    for (const row of rows) expect(row.match(/class="avp-badge /g)?.length).toBe(1);
    const row = rows[0]!;
    // The two dates are one fact, and each is still a machine-readable <time>.
    expect(row).toMatch(/avp-chip[^>]*>.*?<time dateTime="[^"]+">[^<]+<\/time> vs <time/);
  });

  it('says so when an acknowledgement failed, rather than just re-enabling', () => {
    const html = renderToStaticMarkup(
      <ClientAlertsView
        state={ready()}
        alerts={feed(toneDeclineFeed)}
        me={clientsMe}
        onAcknowledge={() => {}}
        failed={new Set(['alrt_01'])}
      />,
    );
    expect(html).toContain('That did not save.');
    expect(html).toContain('Try again');
  });

  it('shows a pending row as in flight rather than inert', () => {
    const html = renderToStaticMarkup(
      <ClientAlertsView
        state={ready()}
        alerts={feed(toneDeclineFeed)}
        me={clientsMe}
        onAcknowledge={() => {}}
        acknowledging={new Set(['alrt_01'])}
      />,
    );
    expect(html).toContain('Acknowledging…');
  });
});

describe('states', () => {
  it('reports a failed alert load without blaming the client record', () => {
    const html = render({
      kind: 'error',
      title: 'These alerts could not be loaded',
      detail: 'The request did not complete.',
    });
    expect(html).toContain('These alerts could not be loaded');
  });

  it('keeps the nav while the feed loads', () => {
    const html = render({ kind: 'loading' });
    expect(html).toContain('Reading alerts');
    expect(html).toContain('href="/clients/clnt_01AAA/alerts"');
  });
});

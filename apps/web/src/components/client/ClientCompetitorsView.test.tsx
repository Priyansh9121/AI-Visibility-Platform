/**
 * The Competitors screen — Epic 13.
 *
 * The assertion that matters most is the HONESTY one: there is no
 * per-competitor composite, and a screen that draws six ledgers side by side
 * is the easiest place in the product to imply one. So the first describe
 * counts composite claims on the page and expects exactly one — the client's
 * — however many rivals the scan found.
 *
 * The rest is the section's seat in the nav, its hue from the nav table
 * rather than a literal, and the empty and error states each saying what is
 * actually the case rather than rendering a grid of nothing.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Client, ClientHistory, Report } from '@avp/shared-types';
import { ClientCompetitorsView, type FieldState } from './ClientCompetitorsView';
import type { ClientDetailState } from './ClientDetailView';
import { accentFor } from './clientNav';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import {
  noScanHistory,
  shiftingSetHistory,
  threeScanHistory,
} from '@/lib/client/__fixtures__/history';
import {
  helpscoutReport,
  mixedCompetitorSetReport,
  noCompetitorSetReport,
  unscoredReport,
} from '@/lib/report/__fixtures__/reports';

const CLIENT = identifiedClient as Client;
const REPORT = helpscoutReport as unknown as Report;

const ready = (history: ClientHistory = threeScanHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});

const shown = (report: Report | null): FieldState => ({ kind: 'ready', report });

const render = (field: FieldState, state: ClientDetailState = ready()) =>
  renderToStaticMarkup(<ClientCompetitorsView state={state} field={field} me={clientsMe} />);

const text = (html: string) => html.replace(/<[^>]*>/g, ' ');

describe('the section exists and sits in the sidebar', () => {
  it('joins the client’s map at its own path, marked current', () => {
    const html = render(shown(REPORT));
    expect(html).toContain('href="/clients/clnt_01AAA/competitors"');
    expect((html.match(/aria-current="page"/g) ?? []).length).toBe(1);
  });

  it('wears its own cluster hue, taken from the nav table', () => {
    // Never a second literal: if the nav table moves this section, the screen
    // follows — Epic B.1's lesson.
    const html = render(shown(REPORT));
    const accent = accentFor('competitors');
    expect(accent).not.toBeNull();
    expect(html).toContain(`--avp-bench-${accent! + 1}-600`);
  });

  it('names the section in the content, not the client — Epic 13', () => {
    expect(render(shown(REPORT))).toContain('<h1');
    expect(render(shown(REPORT))).toContain('Competitors</h1>');
  });
});

describe('exactly one composite on the page, and it is the client’s', () => {
  it('speaks one score for five rivals', () => {
    const html = render(shown(REPORT));
    expect((html.match(/out of 100/g) ?? []).length).toBe(1);
    expect(html).toContain('AI Visibility Score for Help Scout');
    expect((html.match(/AI Visibility Score for/g) ?? []).length).toBe(1);
  });

  it('draws every rival as a partial column with no composite', () => {
    const html = render(shown(REPORT));
    const rivals = REPORT.competitorSet!.competitors.length;
    expect((html.match(/avp-ledger--partial/g) ?? []).length).toBe(rivals);
    expect((html.match(/No composite — measured on 3 of 5 dimensions/g) ?? []).length).toBe(rivals);
    expect((html.match(/No combined score/g) ?? []).length).toBe(rivals);
  });

  it('hatches the two subject-only dimensions on each rival, and none on the client', () => {
    const html = render(shown(REPORT));
    const rivals = REPORT.competitorSet!.competitors.length;
    expect((html.match(/avp-ledger__void--unmeasured/g) ?? []).length).toBe(rivals * 2);
    expect(html).toContain('Sentiment: not measured for Zendesk');
    expect(html).toContain('Technical Foundation: not measured for Zendesk');
    expect(html).not.toContain('not measured for Help Scout');
  });

  it('draws every column compact and in the same shape', () => {
    const html = render(shown(REPORT));
    const columns = REPORT.competitorSet!.competitors.length + 1;
    expect((html.match(/avp-ledger--compact/g) ?? []).length).toBe(columns);
    // Every column has the same viewBox: same weights, same height.
    const boxes = new Set(
      [...html.matchAll(/viewBox="([^"]+)" class="avp-ledger__svg"/g)].map((m) => m[1]),
    );
    expect(boxes.size).toBe(1);
  });

  it('says in words who has a score and who does not', () => {
    const t = text(render(shown(REPORT)));
    expect(t).toContain('Only Help Scout has a combined score');
    expect(t).toContain('Rivals are measured on 3 of the 5 dimensions');
    expect(t).toContain('Sentiment and Technical Foundation are measured for Help Scout only');
  });
});

describe('the grid', () => {
  it('leads with the client, then rivals in detection rank', () => {
    const html = render(shown(REPORT));
    const at = (name: string) => html.indexOf(`>${name}</span>`);
    expect(at('Help Scout')).toBeGreaterThan(-1);
    expect(at('Help Scout')).toBeLessThan(at('Zendesk'));
    expect(at('Zendesk')).toBeLessThan(at('Freshdesk'));
    expect(at('Freshdesk')).toBeLessThan(at('Front'));
    expect(html).toContain('This client');
  });

  it('prints a rival’s lead over the client, signed, and no delta for the client', () => {
    const html = render(shown(REPORT));
    // Zendesk mention rate 83.33 against 100.00.
    expect(html).toContain('−16.7');
    expect(html).toContain('Lead over Help Scout');
  });

  it('says how many scans carried each rival', () => {
    const html = render(shown(REPORT));
    // The fixture report's rivals are not in the fixture history's sets, so
    // every card reports zero of three — honestly, not as an error.
    expect(html).toContain('In 0 of 3 scans');
  });

  it('marks a rival named by hand', () => {
    expect(render(shown(mixedCompetitorSetReport))).toContain('Named by hand');
    expect(render(shown(REPORT))).not.toContain('Named by hand');
  });

  it('says no rival leads when none does, and lists the leads when they do', () => {
    expect(text(render(shown(REPORT)))).toContain(
      'No rival leads Help Scout on any dimension both sides are measured on',
    );
    const ahead: Report = {
      ...REPORT,
      competitorSet: {
        ...REPORT.competitorSet!,
        competitors: REPORT.competitorSet!.competitors.map((c) =>
          c.name === 'Zendesk' ? { ...c, shareOfVoice: '40.00' } : c,
        ),
      },
    };
    const html = render(shown(ahead));
    expect(text(html)).toContain('Zendesk  leads on Share of Voice');
    expect(html).toContain('+10.0');
  });

  it('names rivals that were not in every scan’s set', () => {
    const html = render(shown(REPORT), ready(shiftingSetHistory));
    expect(html).toContain('competitor set');
    expect(html).toContain('Each card says how many scans carried its rival');
  });
});

describe('the empty voices say what is the case', () => {
  it('a client never scanned has nobody to compare', () => {
    const html = render(shown(null), ready(noScanHistory));
    expect(html).toContain('This client has not been scanned');
    expect(html).not.toContain('avp-ledger');
  });

  it('a scan whose report is missing says so, and draws nothing', () => {
    const html = render(shown(null));
    expect(html).toContain('The latest scan has no report to read');
    expect(html).not.toContain('avp-ledger');
  });

  it('an unscored scan is never compared as a low one', () => {
    const html = render(shown(unscoredReport));
    expect(html).toContain('The latest scan was not scored');
    expect(html).not.toContain('avp-ledger');
    expect(html).toContain('Open the report');
  });

  it('a scan with no competitor set points at the report, where a set is made', () => {
    const html = render(shown(noCompetitorSetReport));
    expect(html).toContain('No competitors in this scan');
    expect(html).toContain('detection did not run');
    expect(html).toContain('Open the report');
    expect(html).not.toContain('avp-ledger');
  });

  it('a report still loading, and one that failed, leave the frame standing', () => {
    const loading = render({ kind: 'loading' });
    expect(loading).toContain('Reading the latest scan');
    expect(loading).toContain('href="/clients/clnt_01AAA/competitors"');
    const failed = render({ kind: 'error', title: 'Could not read', detail: 'Timed out.' });
    expect(failed).toContain('Could not read');
    expect(failed).toContain('Timed out.');
    expect(failed).toContain('href="/clients/clnt_01AAA/competitors"');
  });

  it('a client that failed to load shows the error inside the space', () => {
    const html = render(shown(REPORT), {
      kind: 'error',
      title: 'No such client',
      detail: 'Nothing here.',
    });
    expect(html).toContain('No such client');
    expect(html).not.toContain('avp-ledger');
  });
});

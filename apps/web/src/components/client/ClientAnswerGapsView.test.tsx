/**
 * The Answer gaps screen — Epic B.
 *
 * The assertions that matter are the ones that fail silently if wrong: a
 * prompt nobody answered with a brand counted as a loss, a citation gap
 * claimed on a client whose domain was never cited at all, and a rival column
 * that looks complete when detection never ran.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClientAnswerGapsView, type AnswerGapsState } from './ClientAnswerGapsView';
import { accentFor } from './clientNav';
import type { ClientDetailState } from './ClientDetailView';
import type { Client, ClientHistory } from '@avp/shared-types';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import { threeScanHistory } from '@/lib/client/__fixtures__/history';
import {
  gapHeavy,
  noRivals,
  notCitable,
  singleScan,
} from '@/lib/client/__fixtures__/answerGaps';

const CLIENT = identifiedClient as Client;

const ready = (history: ClientHistory = threeScanHistory): ClientDetailState => ({
  kind: 'ready',
  client: CLIENT,
  history,
});

const render = (gaps: AnswerGapsState, onSelectScan?: (id: string) => void) =>
  renderToStaticMarkup(
    <ClientAnswerGapsView
      state={ready()}
      gaps={gaps}
      me={clientsMe}
      {...(onSelectScan ? { onSelectScan } : {})}
    />,
  );

const withData = (data: typeof gapHeavy): AnswerGapsState => ({ kind: 'ready', data });

describe('the tab exists and says whose space it is', () => {
  it('joins the client’s LocalNav as its own destination', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('href="/clients/clnt_01AAA/gaps"');
    expect(html).toContain('Answer gaps');
    expect(html).toContain('aria-current="page"');
  });

  it('wears the hue its own nav item wears', () => {
    // Asserted against the nav table rather than against a hue literal. The
    // literal was `--avp-bench-7-600` for exactly as long as accents were
    // global; Epic B.1 made them cluster-relative and this became cobalt. A
    // test pinned to the number would have had to be rewritten to stay green
    // and would have proved nothing either time — what must hold is that the
    // screen's figures and its nav item agree.
    const html = render(withData(gapHeavy));
    const accent = accentFor('gaps');
    expect(accent).not.toBeNull();
    expect(html).toContain(`--avp-bench-${accent! + 1}-600`);
  });

  it('gives every accented tile a different hue', () => {
    /*
     * The regression this exists for reached a browser screenshot. The tiles
     * carried literal accents (6, 0, 2), which only held while the screen's
     * own accent happened to be 6; Epic B.1 moved it to 0 and two tiles in one
     * row became the same blue. Asserted as a PROPERTY, so it holds wherever
     * the nav table puts this screen next.
     */
    const html = render(withData(gapHeavy));
    const hues = [...html.matchAll(/--avp-tile-accent:\s*var\(--avp-bench-(\d)-600\)/g)].map(
      (m) => m[1],
    );
    expect(hues.length).toBeGreaterThanOrEqual(3);
    expect(new Set(hues).size).toBe(hues.length);
  });

  it('sits in the Investigation cluster, under its label', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('Investigation');
    // The cluster is a real list, not a heading that happens to sit above
    // some links — so a screen reader reports the grouping.
    expect(html).toMatch(/<ul[^>]*aria-label="Investigation"/);
  });
});

describe('a prompt nobody answered with a brand is not a gap', () => {
  it('gives it its own figure, apart from the rivals-took count', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('Named no one');
    expect(html).toContain('Not a gap');
  });

  it('never sums the two into one headline number', () => {
    const html = render(withData(gapHeavy));
    // 2 absent and 2 no-brands in the fixture. A screen that added them would
    // print 4 as the gap count, which is the regression this catches.
    const rivalsTook = html.match(/Rivals took<\/dt><dd[^>]*>(\d+)</);
    expect(rivalsTook?.[1]).toBe('2');
    const namedNoOne = html.match(/Named no one<\/dt><dd[^>]*>(\d+)</);
    expect(namedNoOne?.[1]).toBe('2');
  });

  it('says in words that nobody won those prompts', () => {
    expect(render(withData(gapHeavy))).toContain('nobody won these');
  });

  it('marks the row as inert in the grid rather than as a gap', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('avp-gapgrid__row--inert');
    expect(html).toContain('avp-gapgrid__row--gap');
  });
});

describe('a claim the data cannot support is not made', () => {
  it('reports no citation-gap number when the domain was never cited', () => {
    const html = render(withData(notCitable));
    expect(html).toContain('cannot be measured');
    // An em dash, not a zero: zero would read as "no citation gaps found".
    expect(html).toMatch(/Named, not cited<\/dt><dd[^>]*>—</);
  });

  it('reports the number once the domain proves citable', () => {
    const html = render(withData(gapHeavy));
    expect(html).toMatch(/Named, not cited<\/dt><dd[^>]*>0</);
    expect(html).not.toContain('cannot be measured');
  });

  it('says the grid is bounded by competitor detection when no rival is found', () => {
    const html = render(withData(noRivals));
    expect(html).toContain('No rivals were detected');
    expect(html).toContain('Run competitor detection');
  });

  it('names the limit even when rivals were found', () => {
    expect(render(withData(gapHeavy))).toContain('A rival never detected cannot appear here');
  });
});

describe('recurrence is reported on the axis that survives', () => {
  it('counts rivals across the whole history, not this scan', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('Rivals taking answers, across every scan');
    expect(html).toContain('WebFX');
  });

  it('says why a rival is the thing that recurs and a prompt is not', () => {
    expect(render(withData(gapHeavy))).toContain(
      'Prompts are written fresh for every scan',
    );
  });

  it('omits the ledger when no rival has taken anything', () => {
    expect(render(withData(notCitable))).not.toContain('Rivals taking answers');
  });
});

describe('the scan picker', () => {
  it('appears only when there is more than one scan to pick', () => {
    const many = render(withData(gapHeavy), () => {});
    expect(many).toContain('>Scan<');
    const one = render(withData(singleScan), () => {});
    expect(one).not.toContain('>Scan<');
  });

  it('is absent when the screen was given no way to change scans', () => {
    expect(render(withData(gapHeavy))).not.toContain('>Scan<');
  });
});

describe('states', () => {
  it('shows an empty screen, not an error, when nothing has been scanned', () => {
    const html = render({ kind: 'ready', data: null });
    expect(html).toContain('Nothing measured yet');
    expect(html).toContain('Run a scan');
  });

  it('reports a failed grid load without blaming the client record', () => {
    const html = render({
      kind: 'error',
      title: 'These answer gaps could not be loaded',
      detail: 'The request did not complete.',
    });
    expect(html).toContain('These answer gaps could not be loaded');
  });

  it('renders the client frame while the grid is still loading', () => {
    const html = render({ kind: 'loading' });
    expect(html).toContain('Reading this scan');
    // The nav is present throughout, so the screen does not appear to vanish.
    expect(html).toContain('href="/clients/clnt_01AAA/gaps"');
  });
});

describe('swapping scans keeps the control that did it', () => {
  it('holds the grid on screen, dimmed, rather than unmounting the body', () => {
    // The first draft dropped to a loading state here, which took the scan
    // picker down with it — the control an operator had just used vanished
    // from under their pointer.
    const html = render({ kind: 'ready', data: gapHeavy, pending: true }, () => {});
    expect(html).toContain('avp-gapgrid--pending');
    expect(html).toContain('aria-busy="true"');
    // The picker is still there.
    expect(html).toContain('>Scan<');
    expect(html).not.toContain('Reading this scan');
  });

  it('is not busy when nothing is being swapped', () => {
    const html = render(withData(gapHeavy), () => {});
    expect(html).not.toContain('avp-gapgrid--pending');
    expect(html).not.toContain('aria-busy');
  });

  it('still shows a plain loading state on the very first load', () => {
    // There is no previous grid to keep, so dimming has nothing to dim.
    expect(render({ kind: 'loading' })).toContain('Reading this scan');
  });
});

describe('the grid itself', () => {
  it('prints every cell’s count, so colour is never the only carrier', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('avp-gapgrid__count');
    // A brand named on no engine still gets a mark rather than an empty cell.
    expect(html).toContain('·');
  });

  it('is a real table with headers on both axes', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('<th scope="col"');
    expect(html).toContain('<th scope="row"');
  });

  it('puts the subject first and marks it as the subject', () => {
    const html = render(withData(gapHeavy));
    expect(html).toContain('avp-gapgrid__brand--subject');
    const subjectAt = html.indexOf('PSM Digital');
    const rivalAt = html.indexOf('WebFX');
    expect(subjectAt).toBeGreaterThan(-1);
    expect(subjectAt).toBeLessThan(rivalAt);
  });

  it('scrolls inside its own container so the page never scrolls sideways', () => {
    expect(render(withData(gapHeavy))).toContain('avp-gapgrid');
  });

  it('is reachable by keyboard, because it is what scrolls', () => {
    // A scrollable region that cannot take focus cannot be scrolled without a
    // pointer, which on a narrow viewport put the rival columns out of reach.
    const html = render(withData(gapHeavy));
    expect(html).toContain('tabindex="0"');
    expect(html).toContain('role="region"');
    // And the tab stop is named, so landing on it says what it is.
    expect(html).toMatch(/role="region"[^>]*aria-label="[^"]+"/);
  });
});

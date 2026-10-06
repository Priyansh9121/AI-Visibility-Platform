// @vitest-environment jsdom

/**
 * "Download figures (CSV)" — F3, 2026-10-06.
 *
 * What matters here is what the control promises and what it does when the API says no.
 *
 * It says what is in the file, in plain words, before the click, and what is not. It leads with
 * "Some of this report's figures", because the file has some of them and not the report's other
 * tables (who got named, the engines' agreement, citation totals, a fix's explanation): the date and
 * whether the scan was complete or partial, the overall score and the rest, then, in a second
 * paragraph below the row, what the file does not have and that scores, shares and sentiment keep
 * two decimal places where the page rounds some. The caps the report keeps (12 domains a table)
 * are said in the lead. For a scan with no score it says the file will say "Not scored", as the
 * PDF button does before its click. When the API refuses (a 409 for a scan
 * that has no figures yet) the person gets the API's own sentence under the button and no file; a
 * 404, which is the switch turned off after the page loaded, gets one fixed sentence and not the
 * API's "Not Found". And it takes a scan id and nothing else: there is no token form of this
 * control, because no share link has a CSV.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const reportCsv = vi.fn();
const saveBlob = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: { ...real.api, reportCsv: (id: string) => reportCsv(id) },
    saveBlob: (blob: Blob, name: string) => saveBlob(blob, name),
  };
});

const { DownloadCsvButton } = await import('./DownloadCsvButton');
const { ApiProblem } = await import('@/lib/api');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SOURCE = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'DownloadCsvButton.tsx'), 'utf8');
const code = (source: string) =>
  source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

// The button's words, verbatim: the lead beside the button (for a scored scan and for one with no
// score) and the second paragraph below it, which is the same for both.
const LEAD_SCORED =
  'Some of this report’s figures, one fact to a row, to open in a spreadsheet: the scan’s date and whether it was complete or partial, the overall score, each dimension’s score (or why it was left out), share of voice, whether each engine named the client and where, the domains cited (up to 12 in each of the report’s two tables) and the fix list.';
const LEAD_UNSCORED =
  'Some of this report’s figures, one fact to a row, to open in a spreadsheet: the scan’s date and whether it was complete or partial, share of voice, whether each engine named the client and where, the domains cited (up to 12 in each of the report’s two tables) and the fix list. This scan has no score, so the file says “Not scored” and has no dimension scores.';
const SECOND =
  'The file does not have the weights, who else the answers named, the rivals’ mention rate, citation strength or average position, or the audit’s checks. Scores, shares and sentiment keep two decimal places; the page rounds some of them.';
const NOT_AVAILABLE = 'The figures file is not available for this report.';

const problem = (status: number, detail: string) =>
  new ApiProblem({
    type: status === 409 ? '/problems/scan-not-exportable' : '/problems/not-found',
    title: status === 409 ? "This scan's figures cannot be exported" : 'Resource not found',
    status,
    detail,
    instance: '/api/v1/scans/scan_01ABC/report.csv',
  });

let root: Root | null = null;

async function mount(unscored?: boolean): Promise<HTMLElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(
      unscored === undefined ? (
        <DownloadCsvButton scanId="scan_01ABC" />
      ) : (
        <DownloadCsvButton scanId="scan_01ABC" unscored={unscored} />
      ),
    );
  });
  return el;
}

const button = (el: HTMLElement) => el.querySelector('button') as HTMLButtonElement;
const settle = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

beforeEach(() => {
  reportCsv.mockReset();
  saveBlob.mockReset();
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('the control at rest', () => {
  it('is one button, from the design system, labelled as the brief words it', () => {
    const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" />);
    expect(html).toContain('Download figures (CSV)');
    expect(html).toContain('avp-btn');
    expect(html.match(/<button\b/g)).toHaveLength(1);
  });

  it('says what is in the file, in the words pinned here, and nothing about a report or a PDF', () => {
    const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" />);
    expect(html).toContain(LEAD_SCORED);
    expect(html).toContain(SECOND);
    const lower = html.toLowerCase();
    for (const tell of ['pdf', 'full report', 'same report', 'share link', 'link', 'token']) {
      expect(lower, `found "${tell}"`).not.toContain(tell);
    }
  });

  it('promises only what the file holds: it names the overall score, says what is not in it, and the caps', () => {
    const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" />);
    // What it holds now: the overall score, the date and whether the scan was complete or partial.
    expect(html).toContain('the scan’s date and whether it was complete or partial, the overall score');
    expect(html).not.toContain('whether it finished');
    // It is some of the report's figures: it leads with that and never claims the report's.
    expect(html).toContain('Some of this report’s figures, one fact to a row');
    expect(html).not.toContain('The figures this report shows');
    // What it does not hold, named, with the subject written out: it is a paragraph of its own.
    expect(html).toContain(
      'The file does not have the weights, who else the answers named, the rivals’ mention rate, citation strength or average position, or the audit’s checks.',
    );
    expect(html).not.toContain('It does not have');
    // The cap on the domains, where the file is described.
    expect(html).toContain('(up to 12 in each of the report’s two tables)');
    // It does not promise every figure, or the report.
    expect(html).not.toMatch(/every figure|all the figures|the whole report/i);
  });

  it('says once that scores, shares and sentiment keep two decimal places and the page rounds some of them', () => {
    for (const unscored of [false, true]) {
      const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" unscored={unscored} />);
      const sentence = 'Scores, shares and sentiment keep two decimal places; the page rounds some of them.';
      expect(html.split(sentence)).toHaveLength(2);
      // Exactly the figures that have two places (the API's tests pin which they are): it does
      // not say "numbers", and it makes no claim about the points, which are one place.
      expect(html).not.toContain('Numbers keep');
      expect(html).not.toMatch(/points/i);
    }
  });

  it('is the same sentence whether the scan is scored or the prop is left out', () => {
    const left = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" />);
    const scored = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" unscored={false} />);
    expect(left).toBe(scored);
  });

  it('says before the click that an unscored scan’s file says “Not scored” and has no dimension scores', () => {
    const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" unscored />);
    expect(html).toContain(LEAD_UNSCORED);
    expect(html).toContain(SECOND);
    // The sentence after "This scan has no score…" names its subject: "It" would be the scan.
    expect(html).not.toContain('It does not have');
    // True of every way a scan is not scored, so it does not say "yet": a scan with too little
    // data is not waiting for a score.
    expect(html).not.toContain('no score yet');
    // It no longer says the file has the overall score or each dimension’s score.
    expect(html).not.toContain('the overall score, each dimension’s score');
    // And it still offers the download: a degraded report is still a report.
    expect(html).toContain('Download figures (CSV)');
  });

  it('shows no error before anything is attempted, and an empty status region', () => {
    const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" />);
    expect(html).not.toContain('role="alert"');
    expect(html).toContain('<span role="status" class="sr-only"></span>');
  });

  it('is a button that fetches, not a link that navigates', () => {
    const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" />);
    expect(html).not.toMatch(/<a\b/);
    expect(html).not.toContain('href=');
    expect(html).not.toContain('download=');
  });
});

describe('its words are in two paragraphs, the lead beside the button', () => {
  it.each([
    ['a scored scan', false, LEAD_SCORED],
    ['a scan with no score', true, LEAD_UNSCORED],
  ])('keeps the lead in the row and the rest below it for %s', async (_what, unscored, lead) => {
    const el = await mount(unscored);
    const row = button(el).parentElement!;
    const paragraphs = [...el.querySelectorAll('p')];
    expect(paragraphs).toHaveLength(2);
    const [first, second] = paragraphs;
    // The lead sits beside the button, in the row; the rest is a paragraph of its own under it.
    expect(first!.textContent).toBe(lead);
    expect(row.contains(first!)).toBe(true);
    expect(second!.textContent).toBe(SECOND);
    expect(row.contains(second!)).toBe(false);
    expect(second!.parentElement).toBe(row.parentElement);
    expect(row.nextElementSibling).toBe(second);
    // Same type on both, so the two read as one piece of copy.
    expect(second!.className).toBe(first!.className);
  });

  it('aligns the row to the top, so the button does not float beside the middle of the lead', async () => {
    const el = await mount();
    const row = button(el).parentElement!;
    expect(row.className).toContain('items-start');
    expect(row.className).not.toContain('items-center');
    expect(row.className).toContain('flex-wrap');
  });

  it('is a lead of about two lines beside the button and not the whole of it in one block', async () => {
    const el = await mount();
    const [first, second] = [...el.querySelectorAll('p')];
    // Neither paragraph is the whole of what the control says.
    expect(first!.textContent!.length).toBeLessThan(`${LEAD_SCORED} ${SECOND}`.length - 150);
    expect(second!.textContent!.length).toBeLessThan(first!.textContent!.length);
    expect(first!.textContent).not.toContain('does not have');
  });
});

describe('a download', () => {
  it('asks for this scan, hands the browser the file under the name the server chose', async () => {
    const file = new File(['' + String.fromCharCode(0xfeff) + 'section\r\n'], 'Help-Scout-ai-visibility-2026-10-01.csv', { type: 'text/csv' });
    reportCsv.mockResolvedValue(file);
    const el = await mount();
    await act(async () => {
      button(el).click();
    });
    await settle();
    expect(reportCsv).toHaveBeenCalledTimes(1);
    expect(reportCsv).toHaveBeenCalledWith('scan_01ABC');
    expect(saveBlob).toHaveBeenCalledTimes(1);
    expect(saveBlob).toHaveBeenCalledWith(file, 'Help-Scout-ai-visibility-2026-10-01.csv');
    expect(el.querySelector('[role="status"]')!.textContent).toBe('CSV downloaded');
    expect(el.querySelector('[role="alert"]')).toBeNull();
  });

  it('falls back to a fixed name, with no product in it, when the browser does not show the server’s', async () => {
    const plain = new Blob(['x'], { type: 'text/csv' });
    reportCsv.mockResolvedValue(plain);
    const el = await mount();
    await act(async () => {
      button(el).click();
    });
    await settle();
    expect(saveBlob).toHaveBeenCalledWith(plain, 'ai-visibility.csv');
  });

  it('says it is preparing while the request is out, and ignores a second press', async () => {
    let finish: (blob: Blob) => void = () => undefined;
    reportCsv.mockReturnValue(
      new Promise<Blob>((resolve) => {
        finish = resolve;
      }),
    );
    const el = await mount();
    await act(async () => {
      button(el).click();
    });
    expect(button(el).textContent).toBe('Preparing…');
    expect(button(el).getAttribute('aria-disabled')).toBe('true');
    expect(button(el).disabled).toBe(false); // focus stays where it is
    expect(el.querySelector('[role="status"]')!.textContent).toBe('Preparing the CSV…');
    await act(async () => {
      button(el).click();
      button(el).click();
    });
    expect(reportCsv).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish(new File(['x'], 'Help-Scout-ai-visibility-2026-10-01.csv'));
    });
    await settle();
    expect(button(el).textContent).toBe('Download figures (CSV)');
    expect(saveBlob).toHaveBeenCalledTimes(1);
  });
});

describe('when the API says no', () => {
  it.each([
    [
      409,
      'This scan is still running, so its figures are not final. Try again when it has finished.',
      'This scan is still running, so its figures are not final. Try again when it has finished.',
    ],
    [
      409,
      'This scan failed, so its figures cannot be exported.',
      'This scan failed, so its figures cannot be exported.',
    ],
    // The switch turned off while the page is open: the API's "Not Found" is not shown.
    [404, 'Not Found', NOT_AVAILABLE],
  ])('shows the %i as a sentence a person can read, and saves nothing', async (status, detail, shown) => {
    reportCsv.mockRejectedValue(problem(status, detail));
    const el = await mount();
    await act(async () => {
      button(el).click();
    });
    await settle();
    const alert = el.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toBe(shown);
    expect(alert!.textContent).not.toBe('Not Found');
    expect(saveBlob).not.toHaveBeenCalled();
    // The alert is tied to the button, and the button is usable again.
    expect(button(el).getAttribute('aria-describedby')).toBe(alert!.id);
    expect(button(el).textContent).toBe('Download figures (CSV)');
    expect(button(el).getAttribute('aria-disabled')).toBeNull();
    expect(el.querySelector('[role="status"]')!.textContent).toBe('');
  });

  it('says one fixed sentence when the request never completed', async () => {
    reportCsv.mockRejectedValue(new TypeError('Failed to fetch'));
    const el = await mount();
    await act(async () => {
      button(el).click();
    });
    await settle();
    expect(el.querySelector('[role="alert"]')!.textContent).toBe(
      'The figures could not be prepared. Try again.',
    );
    expect(saveBlob).not.toHaveBeenCalled();
  });

  it('clears the sentence on the next try, and a good one leaves none', async () => {
    reportCsv.mockRejectedValueOnce(problem(409, 'This scan is still running.'));
    reportCsv.mockResolvedValueOnce(new File(['x'], 'Help-Scout-ai-visibility-2026-10-01.csv'));
    const el = await mount();
    await act(async () => {
      button(el).click();
    });
    await settle();
    expect(el.querySelector('[role="alert"]')).not.toBeNull();
    await act(async () => {
      button(el).click();
    });
    await settle();
    expect(el.querySelector('[role="alert"]')).toBeNull();
    expect(saveBlob).toHaveBeenCalledTimes(1);
  });
});

describe('what the control is allowed to know', () => {
  it('takes a scan id, and whether the scan is unscored as the PDF button is told: no token form', () => {
    const source = code(SOURCE);
    expect(source).toMatch(
      /export interface DownloadCsvButtonProps \{\s*scanId: string;\s*unscored\?: boolean \| undefined;\s*\}/,
    );
    expect(source).not.toMatch(/token/i);
    expect(source).not.toMatch(/publicReport/);
    expect(source.match(/api\.\w+/g)).toEqual(['api.reportCsv']);
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value and no raw-palette class', () => {
    const html = renderToStaticMarkup(<DownloadCsvButton scanId="scan_01ABC" />);
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
    expect(html).not.toMatch(/style=/);
    expect(code(SOURCE)).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe('it works like the PDF control while it works', () => {
  const PDF = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'DownloadPdfButton.tsx'), 'utf8');
  const busyLabel = (source: string) => /\{busy \? '([^']+)' :/.exec(code(source))?.[1];

  it('has the PDF button’s busy label, and its status words with the file named', () => {
    expect(busyLabel(SOURCE)).toBe('Preparing…');
    expect(busyLabel(SOURCE)).toBe(busyLabel(PDF));
    expect(code(SOURCE)).toContain("busy ? 'Preparing the CSV…' : done ? 'CSV downloaded' : ''");
    expect(code(PDF)).toContain("busy ? 'Preparing the PDF…' : done ? 'PDF downloaded' : ''");
  });

  it('keeps focus on the button while busy, as the PDF does: aria-disabled, never disabled', () => {
    for (const source of [SOURCE, PDF]) {
      expect(code(source)).toContain('aria-disabled={busy ? true : undefined}');
      expect(code(source)).toContain('if (busy) return;');
      expect(code(source)).not.toMatch(/(?<!aria-)disabled=\{/);
    }
  });
});

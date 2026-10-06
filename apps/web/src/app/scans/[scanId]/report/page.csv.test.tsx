// @vitest-environment jsdom

/**
 * The signed-in report draws "Download figures (CSV)" when the report says so — F3, 2026-10-06.
 *
 * The API's switch (`REPORT_CSV_ENABLED`) and the scan's status decide whether the file can be had,
 * and the report says the result in `csvExportAvailable`. The page draws the control from that field
 * and from nothing else, so with the switch unset (the default) the field is false, nothing is
 * drawn, and the page is the page it was. The two downloads share one wrapper, so they sit one
 * above the other with the same gap whether the second is there or not. The figures control is
 * told whether the scan is unscored by the rule the FILE uses for "Not scored" (never scored, too
 * little data, or no composite: `lib/report/csvScore.ts`), which is wider than the PDF control's
 * own `score === null`; the PDF control is told what it always was. The real route in jsdom with
 * the API mocked at its edge, as `feedbackWiring.test.tsx` mounts it.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { generatedFixesReport } from '@/lib/report/__fixtures__/reports';
import { noSubscription } from '@/lib/settings/__fixtures__/billing';

vi.mock('next/navigation', () => ({ usePathname: () => '/scans/scan_01ABC/report' }));

const report = vi.fn();
const me = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      report: (id: string) => report(id),
      me: () => me(),
      billing: async () => noSubscription,
      billingPlan: async () => ({ trialDays: 0 }),
      logOut: async () => undefined,
    },
  };
});

// The heavy bodies as stand-ins: what is under test is the chrome above the document.
vi.mock('@/components/report/ReportView', () => ({
  ReportView: () => <div data-testid="report-body">the report</div>,
}));
vi.mock('@/components/report/CompetitorEditor', () => ({ CompetitorEditor: () => null }));
vi.mock('@/components/report/ShareLinkBar', () => ({ ShareLinkBar: () => null }));
vi.mock('@/components/report/DownloadPdfButton', () => ({
  DownloadPdfButton: ({ unscored }: { unscored?: boolean }) => (
    <div data-testid="pdf-button" data-unscored={String(unscored)}>
      pdf
    </div>
  ),
}));

const { default: ReportRoute } = await import('./page');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SCAN_ID = 'scan_01ABC';
const LABEL = 'Download figures (CSV)';
const params = Object.assign(Promise.resolve({ scanId: SCAN_ID }), {
  status: 'fulfilled' as const,
  value: { scanId: SCAN_ID },
});

const withFlag = (flag: boolean | undefined) => {
  const { csvExportAvailable: _drop, ...rest } = generatedFixesReport;
  return (flag === undefined ? rest : { ...rest, csvExportAvailable: flag }) as typeof generatedFixesReport;
};

let root: Root | null = null;

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

async function mount(node: ReactElement): Promise<HTMLElement> {
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(node);
  });
  await settle();
  await settle();
  return el;
}

async function open(flag: boolean | undefined): Promise<HTMLElement> {
  report.mockResolvedValue(withFlag(flag));
  return mount(<ReportRoute params={params} />);
}

const csvButtons = (el: HTMLElement) =>
  [...el.querySelectorAll('button')].filter((b) => b.textContent === LABEL);

// The control's own root, which is the nearest column of flex above its button.
const csvRoot = (el: HTMLElement) => csvButtons(el)[0]!.closest('div.flex-col')!;

// Both downloads sit in one wrapper, whether or not the second is drawn.
const WRAPPER = 'mt-4 mb-6 flex flex-col gap-3';

const UNSCORED_NOTE =
  'This scan has no score, so the file says “Not scored” and has no dimension scores.';

// What the PDF control is told, as the page hands it over.
const pdfUnscored = (el: HTMLElement) =>
  el.querySelector('[data-testid="pdf-button"]')!.getAttribute('data-unscored');

// A score row, as the API serves it, in each of the ways the file says "Not scored".
const scoreRow = generatedFixesReport.score!;
const tooLittleData = { ...scoreRow, status: 'insufficient_data', composite: null };
const noComposite = { ...scoreRow, composite: null };

beforeEach(() => {
  report.mockReset();
  me.mockReset();
  me.mockRejectedValue(new TypeError('Failed to fetch')); // no feedback address: no link
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

describe('when the report says the export is available', () => {
  it('draws the control once, above the document and not in it', async () => {
    const el = await open(true);
    const buttons = csvButtons(el);
    expect(buttons).toHaveLength(1);
    const frame = el.querySelector('.avp-report-frame')!;
    expect(frame).not.toBeNull();
    expect(frame.contains(buttons[0]!)).toBe(false);
    // Document order: before the frame the report sits in, and after the PDF control.
    const pdf = el.querySelector('[data-testid="pdf-button"]')!;
    expect(buttons[0]!.compareDocumentPosition(frame) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(pdf.compareDocumentPosition(buttons[0]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(el.querySelector('[data-testid="report-body"]')).not.toBeNull();
  });

  it('puts both downloads in one wrapper: the PDF control first, the figures control second', async () => {
    const el = await open(true);
    const pdf = el.querySelector('[data-testid="pdf-button"]')!;
    const wrapper = pdf.parentElement!;
    expect(wrapper.className).toBe(WRAPPER);
    expect([...wrapper.children]).toEqual([pdf, csvRoot(el)]);
  });

  it('tells both controls a scan that was never scored is unscored', async () => {
    report.mockResolvedValue({ ...withFlag(true), score: null } as typeof generatedFixesReport);
    const el = await mount(<ReportRoute params={params} />);
    expect(csvButtons(el)).toHaveLength(1);
    expect(csvRoot(el).textContent).toContain(UNSCORED_NOTE);
    expect(pdfUnscored(el)).toBe('true');
  });

  it('tells the figures control a scan with too little data is unscored, as its file says "Not scored"', async () => {
    report.mockResolvedValue({
      ...withFlag(true),
      score: tooLittleData,
    } as unknown as typeof generatedFixesReport);
    const el = await mount(<ReportRoute params={params} />);
    expect(csvButtons(el)).toHaveLength(1);
    const text = csvRoot(el).textContent!;
    expect(text).toContain(UNSCORED_NOTE);
    // It is not told the file has what the file will not have.
    expect(text).not.toContain('the overall score, each dimension’s score');
    // The PDF control is not changed: it is told what it always was, that a score row exists.
    expect(pdfUnscored(el)).toBe('false');
  });

  it('tells the figures control a score row with no composite is unscored too', async () => {
    report.mockResolvedValue({
      ...withFlag(true),
      score: noComposite,
    } as unknown as typeof generatedFixesReport);
    const el = await mount(<ReportRoute params={params} />);
    expect(csvRoot(el).textContent).toContain(UNSCORED_NOTE);
    expect(pdfUnscored(el)).toBe('false');
  });

  it('does not say so for a scored scan, and tells both controls it is scored', async () => {
    const el = await open(true);
    expect(csvRoot(el).textContent).not.toContain(UNSCORED_NOTE);
    expect(csvRoot(el).textContent).toContain('the overall score, each dimension’s score');
    expect(pdfUnscored(el)).toBe('false');
  });

  it('is drawn for this scan: the API route is asked for this scan id and no other', async () => {
    const el = await open(true);
    expect(report).toHaveBeenCalledWith(SCAN_ID);
    expect(el.textContent).toContain('whether each engine named the client and where');
  });
});

describe('when it does not', () => {
  it.each([
    ['false', false],
    ['absent: an API that has no such field yet', undefined],
  ])('draws nothing when the field is %s', async (_what, flag) => {
    const el = await open(flag);
    expect(csvButtons(el)).toHaveLength(0);
    expect(el.textContent).not.toContain('figures (CSV)');
    expect(el.innerHTML.toLowerCase()).not.toContain('csv');
    expect(el.querySelector('[data-testid="pdf-button"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="report-body"]')).not.toBeNull();
    // The PDF control is alone in its wrapper, which is the same wrapper.
    const wrapper = el.querySelector('[data-testid="pdf-button"]')!.parentElement!;
    expect(wrapper.className).toBe(WRAPPER);
    expect(wrapper.children).toHaveLength(1);
  });

  it('is the page it was, exactly, when the field is false or missing', async () => {
    const off = (await open(false)).innerHTML;
    act(() => root?.unmount());
    root = null;
    document.body.innerHTML = '';
    const missing = (await open(undefined)).innerHTML;
    expect(missing).toBe(off);
  });

  it('adds the control and moves nothing else when it is true', async () => {
    const off = (await open(false)).innerHTML;
    act(() => root?.unmount());
    root = null;
    document.body.innerHTML = '';

    const el = await open(true);
    csvRoot(el).remove();
    expect(el.innerHTML).toBe(off);
  });
});

describe('the control is the report’s to offer and the page’s to draw, once', () => {
  it('is drawn in one place in the page source, under the report’s own field', () => {
    const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'page.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    expect(source.match(/<DownloadCsvButton\b/g)).toHaveLength(1);
    // (The comment above the control leaves an empty `{}` once comments are stripped.)
    expect(source).toContain('view.report.csvExportAvailable === true');
    // The figures control follows the file's rule, from the one place that holds it; the PDF
    // control keeps the test it has always had.
    expect(source).toContain("import { fileSaysNotScored } from '@/lib/report/csvScore';");
    expect(source).toContain('unscored={view.report.score === null}');
    expect(source.match(/unscored=\{/g)).toHaveLength(2);
    expect(source).toMatch(
      /<div className="mt-4 mb-6 flex flex-col gap-3">\s*<DownloadPdfButton\b[^>]*?\/>\s*(?:\{\}\s*)?\{view\.report\.csvExportAvailable === true && \(\s*<DownloadCsvButton scanId=\{scanId\} unscored=\{fileSaysNotScored\(view\.report\.score\)\} \/>\s*\)\}\s*<\/div>/,
    );
  });
});

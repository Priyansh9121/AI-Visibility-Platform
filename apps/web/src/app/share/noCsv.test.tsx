// @vitest-environment jsdom

/**
 * A share link has no CSV — F3, 2026-10-06.
 *
 * The figures file is the agency's: signed in, scoped to the agency, and built from the signed-in
 * report. A share link opens the report page and its PDF and nothing more, so `/share` must never
 * draw the control, whatever the payload it was given says. Held three ways: the page is mounted
 * with a payload that says the export IS available (the worst case: an API that blanked nothing),
 * the sources are read for who may draw the control or call its route, and the document both
 * screens render is read for the control too, since it is the part that gets sent.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { generatedFixesReport } from '@/lib/report/__fixtures__/reports';

const publicReport = vi.fn();

vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, publicReport: (token: string) => publicReport(token) } };
});
vi.mock('@/components/report/ReportView', () => ({
  ReportView: () => <div data-testid="report-body">the report</div>,
}));

const { default: PublicReportRoute } = await import('./page');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;

async function openAt(hash: string): Promise<HTMLDivElement> {
  window.history.replaceState(null, '', `/share${hash}`);
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(<PublicReportRoute />);
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  return el;
}

beforeEach(() => publicReport.mockReset());

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
  window.history.replaceState(null, '', '/');
});

describe('the share page', () => {
  it('draws no CSV control even for a payload that says the export is available', async () => {
    publicReport.mockResolvedValue({ ...generatedFixesReport, csvExportAvailable: true });
    const el = await openAt('#sometoken');
    // Not vacuous: the report is there, and so is the PDF control a stranger does get.
    expect(el.querySelector('[data-testid="report-body"]')).not.toBeNull();
    expect(el.textContent).toContain('Download PDF');
    expect(el.textContent).not.toContain('figures (CSV)');
    expect(el.innerHTML.toLowerCase()).not.toContain('csv');
    expect([...el.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Download PDF']);
  });

  it('is the same page whether the payload says true or false', async () => {
    publicReport.mockResolvedValue({ ...generatedFixesReport, csvExportAvailable: false });
    const off = (await openAt('#sometoken')).innerHTML;
    act(() => root?.unmount());
    root = null;
    document.body.innerHTML = '';
    publicReport.mockResolvedValue({ ...generatedFixesReport, csvExportAvailable: true });
    const on = (await openAt('#sometoken')).innerHTML;
    expect(on).toBe(off);
  });
});

// -- the sources ---------------------------------------------------------------------------

// `src`, from this file's own path (app/share/noCsv.test.tsx).
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === '__fixtures__' ? [] : sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const SOURCES = sources(ROOT).map((path) => ({
  file: relative(ROOT, path),
  text: code(readFileSync(path, 'utf8')),
}));
const filesSaying = (needle: RegExp) =>
  SOURCES.filter(({ text }) => needle.test(text)).map(({ file }) => file).sort();

describe('who can draw the control and who can ask for the file, read off the sources', () => {
  it('reads the sources it should', () => {
    expect(SOURCES.length).toBeGreaterThan(100);
    expect(SOURCES.some(({ file }) => file === 'app/share/page.tsx')).toBe(true);
  });

  it('draws it from the signed-in report page and from nowhere else', () => {
    expect(filesSaying(/<DownloadCsvButton\b/)).toEqual(['app/scans/[scanId]/report/page.tsx']);
    expect(filesSaying(/DownloadCsvButton/)).toEqual([
      'app/scans/[scanId]/report/page.tsx',
      'components/report/DownloadCsvButton.tsx',
    ]);
  });

  it('calls its route from the control and nowhere else', () => {
    expect(filesSaying(/\breportCsv\b/)).toEqual([
      'components/report/DownloadCsvButton.tsx',
      'lib/api.ts',
    ]);
    // The route's own path, which only the client names (not the report's field of that prefix).
    expect(filesSaying(/\/report\.csv\b/)).toEqual(['lib/api.ts']);
  });

  it('keeps both share routes, and the document they render, free of it', () => {
    for (const file of ['app/share/page.tsx', 'app/share/[token]/page.tsx']) {
      const text = SOURCES.find((s) => s.file === file)!.text;
      expect(text, file).not.toMatch(/csv/i);
    }
    const view = SOURCES.find((s) => s.file === 'components/report/ReportView.tsx')!.text;
    expect(view).not.toMatch(/csv/i);
    expect(view).not.toContain('Download figures');
  });
});

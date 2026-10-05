/**
 * The competitor editor for a list with no rivals and for no list at all (launch checklist
 * 3.78, 3.79). The report draws it for both, signed in only, so an operator can name rivals; the
 * shared report passes no slot and draws none. Every sentence is pinned verbatim, and each is true
 * of what the API does: a save is written to the client's next list, for later scans, and a scan
 * that has started keeps the list and the figures it has.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Report } from '@avp/shared-types';
import { ReportView } from './ReportView';
import { CompetitorEditor } from './CompetitorEditor';
import { COMPETITOR_EDITOR_COPY, DETECTION_STATUS, NO_COMPETITOR_LIST } from '@/lib/report/strings';
import { helpscoutReport, noCompetitorSetReport } from '@/lib/report/__fixtures__/reports';

const emptySetReport = {
  ...helpscoutReport,
  competitorSet: { ...helpscoutReport.competitorSet!, status: 'no_signal', competitors: [] },
} as unknown as Report;

const withEditor = (report: Report) =>
  renderToStaticMarkup(
    <ReportView
      report={report}
      animate={false}
      competitorEditor={
        <CompetitorEditor
          clientId="cli_1"
          competitors={report.competitorSet?.competitors ?? []}
          setMissing={report.competitorSet === null}
        />
      }
    />,
  );

const plain = (html: string) =>
  html.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, '’').replace(/\s+/g, ' ');

const src = (rel: string) => readFileSync(resolve(__dirname, '../..', rel), 'utf8');

const SAVED_FOR_LATER =
  'Rivals you name here are saved for later scans, which compare against them. This scan’s list and score do not change. A list cannot be saved while a scan of this client is running.';

describe('the editor is drawn for a list with no rivals', () => {
  it('under the empty card, with the lead for an empty list', () => {
    const html = withEditor(emptySetReport);
    expect(html).toContain('Name the rivals');
    expect(html).not.toContain('Correct this list');
    expect(plain(html)).toContain(
      `This scan’s competitor list has no rivals: none were found, or the list was emptied by hand. ${SAVED_FOR_LATER}`,
    );
    expect(html.indexOf('Name the rivals')).toBeGreaterThan(html.indexOf(DETECTION_STATUS.no_signal.label));
  });

  it('the empty card is true of a list emptied by hand as well as one detection left empty', () => {
    const html = plain(withEditor(emptySetReport));
    expect(html).toContain('No rivals on the list');
    expect(html).toContain(
      'This scan’s competitor list has no rivals, so no comparison is shown. Either search results and AI answers surfaced no usable set, or the list was emptied by hand. Neither is a finding about the brand.',
    );
    expect(html).not.toContain('Neither search results nor AI answers surfaced a usable competitor set');
  });
});

describe('the heading levels around the editor', () => {
  it('the empty and the missing card label is an h3, so the open editor’s h4 follows an h3', () => {
    expect(withEditor(emptySetReport)).toMatch(/<h3[^>]*>No rivals on the list<\/h3>/);
    expect(withEditor(noCompetitorSetReport)).toMatch(
      new RegExp(`<h3[^>]*>${NO_COMPETITOR_LIST.label}</h3>`),
    );
  });
});

describe('the editor is drawn for a scan with no competitor set', () => {
  it('under its own card, with the lead for a missing list', () => {
    const html = withEditor(noCompetitorSetReport);
    expect(html).toContain('Name the rivals');
    expect(plain(html)).toContain(`No competitor list is recorded for this scan. ${SAVED_FOR_LATER}`);
    expect(plain(html)).toContain(NO_COMPETITOR_LIST.label);
    expect(plain(html)).toContain(
      'This scan has no competitor list, so no comparison is shown. That is a limit of the data, not a finding about the brand.',
    );
  });
});

describe('what a save does is said for the scan on screen, and only what is true of it', () => {
  const render = (scanFinished: boolean, competitors: Report['competitorSet'] = null) =>
    plain(
      renderToStaticMarkup(
        <CompetitorEditor
          clientId="cli_1"
          competitors={competitors?.competitors ?? []}
          setMissing={competitors === null}
          scanFinished={scanFinished}
        />,
      ),
    );

  it('a finished scan says its list and score do not change, and that the list is for later scans', () => {
    const t = render(true);
    expect(t).toContain('Rivals you name here are saved for later scans, which compare against them.');
    expect(t).toContain('This scan’s list and score do not change. A list cannot be saved while a scan of this client is running.');
    expect(t).not.toContain('a rival named here shows no figures for it');
    expect(t).not.toContain('score does not change');
  });

  it('a scan still queued or running does not say its list does not change: a queued one takes the list', () => {
    const t = render(false);
    expect(t).not.toContain('This scan’s list and score do not change');
    expect(t).toContain(
      'A list cannot be saved while a scan of this client is running. Rivals you name here are saved as the client’s list, and later scans compare against them. A scan that has not started yet uses the list when it runs.',
    );
  });

  it('a list with rivals says the same of a correction, in both forms', () => {
    const rivals = helpscoutReport.competitorSet;
    expect(render(true, rivals)).toContain(
      'Detection is automated and imperfect. A list you correct here is saved for later scans, and a rival you add or keep by hand stays in place on them. This scan’s list and score do not change. A list cannot be saved while a scan of this client is running.',
    );
    const running = render(false, rivals);
    expect(running).toContain(
      'Detection is automated and imperfect. A list cannot be saved while a scan of this client is running. A list you correct here is saved as the client’s list, and a rival you add or keep by hand stays in place on later scans. A scan that has not started yet uses the list when it runs.',
    );
    expect(running).not.toContain('This scan’s list and score do not change');
  });
});

describe('a list with rivals keeps its button', () => {
  it('and the sentence under the table', () => {
    const html = withEditor(helpscoutReport);
    expect(html).toContain('Correct this list');
    expect(html).not.toContain('Name the rivals');
    expect(plain(html)).toContain(
      'Detection is automated and imperfect. A list you correct here is saved for later scans, and a rival you add or keep by hand stays in place on them. This scan’s list and score do not change. A list cannot be saved while a scan of this client is running.',
    );
  });
});

describe('the shared report draws no editor', () => {
  it('for an empty or missing list when no slot is passed', () => {
    for (const report of [emptySetReport, noCompetitorSetReport]) {
      const html = renderToStaticMarkup(<ReportView report={report} animate={false} />);
      expect(html).not.toContain('Name the rivals');
      expect(html).not.toContain('Correct this list');
    }
  });

  it('the public share page passes no competitorEditor slot and imports no editor', () => {
    const share = src('app/share/page.tsx').replace(/\/\*[\s\S]*?\*\//g, '');
    expect(share).not.toContain('CompetitorEditor');
    expect(share).not.toMatch(/competitorEditor\s*=/);
  });
});

describe('the signed-in report page', () => {
  it('draws the editor whether or not the scan has a set', () => {
    const page = src('app/scans/[scanId]/report/page.tsx');
    expect(page).toContain('setMissing={view.report.competitorSet === null}');
    expect(page).toContain('competitors={view.report.competitorSet?.competitors ?? []}');
    expect(page).toContain("scanFinished={!['queued', 'running'].includes(view.report.scanStatus)}");
    expect(page).not.toMatch(/view\.report\.competitorSet\s*\?\s*\(\s*<CompetitorEditor/);
  });

  it('does not reload the report after a save: the saved list is for later scans', () => {
    const page = src('app/scans/[scanId]/report/page.tsx')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    expect(page).not.toContain('onSaved');
    expect(page).not.toContain('useCallback');
    expect(page).not.toContain('reload');
  });
});

describe('the open editor sentences', () => {
  it('are the pinned wording, and the component uses them', () => {
    expect(COMPETITOR_EDITOR_COPY.noneYet).toBe('No competitors named yet. Add the first below.');
    expect(COMPETITOR_EDITOR_COPY.emptiedStaysEmpty).toBe(
      'No competitors on the list. A list saved empty stays empty: later scans carry it forward, and compare against no rivals until some are named here.',
    );
    expect(COMPETITOR_EDITOR_COPY.openHeading).toBe('Name the competitors');
    expect(COMPETITOR_EDITOR_COPY.openNote).toBe(
      'Saving replaces the client’s whole list and marks every entry on it as set by hand, including ones you did not change. The corroboration figure is cleared with it, because it measures agreement between two automated signals and neither produced this list.',
    );
    expect(COMPETITOR_EDITOR_COPY.openNoteFinished).toBe(
      'The list is saved for later scans. This scan’s list and score do not change. A list cannot be saved while a scan of this client is running.',
    );
    expect(COMPETITOR_EDITOR_COPY.openNoteRunning).toBe(
      'A list cannot be saved while a scan of this client is running. The list is saved as the client’s list. A scan that has not started yet uses it when it runs.',
    );
    const editor = src('components/report/CompetitorEditor.tsx');
    expect(editor).toContain('COPY.noneYet');
    expect(editor).toContain('COPY.emptiedStaysEmpty');
    expect(editor).toContain('COPY.openHeading');
    expect(editor).toContain('COPY.openNote');
    expect(editor).not.toContain('unmeasurable');
    expect(editor).not.toContain('clears the set');
  });

  it('say, in the status line after a save, that the list is for later scans', () => {
    expect(COMPETITOR_EDITOR_COPY.saved).toBe(
      'Saved for later scans. This scan’s list and score do not change.',
    );
    expect(COMPETITOR_EDITOR_COPY.savedRunning).toBe(
      'Saved as the client’s list. A scan that has not started yet uses it when it runs.',
    );
  });

  it('keep the old claims out of every sentence the editor can say', () => {
    for (const sentence of Object.values(COMPETITOR_EDITOR_COPY)) {
      expect(sentence).not.toContain('score does not change');
      expect(sentence).not.toContain('shows no figures');
      expect(sentence).not.toContain('become the client’s list');
    }
  });
});

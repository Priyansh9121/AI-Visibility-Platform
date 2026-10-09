/**
 * The report's question line and the past-year flag — F6 section 3.
 *
 * "Same questions as the scan of 9 Oct 2026" / "New questions" under the evidence beat's figures,
 * nothing for a report from before the field existed (and the shared report carries it as well);
 * a question that names an earlier year carries a marker, and one line says how many do. The
 * pointer to the client's Prompts tab is on the signed-in report only: a prospect has no such tab.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Report } from '@avp/shared-types';
import { ReportView } from './ReportView';
import { helpscoutReport } from '@/lib/report/__fixtures__/reports';

// The fixture's own questions include one that names 2025; the tests below start from a report
// whose questions name no past year, and add one on purpose.
const fixture = helpscoutReport as unknown as Report;
const base: Report = {
  ...fixture,
  proof: {
    ...fixture.proof,
    promptShelf: (fixture.proof.promptShelf ?? []).map((row) => ({
      ...row,
      promptText: row.promptText.replace('2025', '2026'),
    })),
  },
};
const render = (report: Report) => renderToStaticMarkup(<ReportView report={report} animate={false} />);
const renderPublic = (report: Report) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} publicView />);

/** The words a reader reads: the markup with its tags taken out. */
const text = (html: string) => html.replace(/<[^>]+>/g, '');

const withSet = (questionSet: NonNullable<Report['questionSet']> | null): Report => ({
  ...base,
  questionSet,
});

/** The shelf's first question carries `suffix`, in every engine's row (the scan is from 2026). */
const withQuestionEnding = (suffix: string): Report => {
  const shelf = base.proof.promptShelf ?? [];
  const first = shelf[0]!.promptId;
  return {
    ...base,
    proof: {
      ...base.proof,
      promptShelf: shelf.map((row) =>
        row.promptId === first ? { ...row, promptText: `${row.promptText} ${suffix}` } : row,
      ),
    },
  };
};

describe('the line about the questions', () => {
  it('says the questions are the same ones, and from which scan', () => {
    // The API sends the source scan's day (UTC), never a time of day.
    const html = render(withSet({ reused: true, sourceScanDate: '2026-10-09' }));
    expect(html).toContain('Same questions as the scan of 9 Oct 2026');
    expect(html).not.toContain('>New questions<');
  });

  it('says new questions were written', () => {
    const html = render(withSet({ reused: false, sourceScanDate: null }));
    expect(html).toContain('>New questions<');
  });

  it('prints nothing for a report from before the field', () => {
    const html = render(withSet(null));
    expect(html).not.toContain('Same questions as');
    expect(html).not.toContain('>New questions<');
    expect(html).not.toContain('report-questions');
  });

  it('is on the shared report too', () => {
    const html = renderPublic(withSet({ reused: true, sourceScanDate: '2026-10-09' }));
    expect(html).toContain('Same questions as the scan of 9 Oct 2026');
  });
});

describe('what each engine returned names the questions as the scan’s own set', () => {
  it('says "question set", which is true of a set copied from an earlier scan as of a new one', () => {
    // U2: "generated set" was untrue of a reused one.
    const sets: Parameters<typeof withSet>[0][] = [
      { reused: true, sourceScanDate: '2026-10-09' },
      { reused: false, sourceScanDate: null },
      null,
    ];
    for (const set of sets) {
      const html = render(withSet(set));
      expect(html).toContain('3 prompts from this scan’s question set');
      expect(html).not.toContain('generated set');
    }
  });
});

describe('the past-year flag', () => {
  it('marks a question naming an earlier year and counts it, pointing at the Prompts tab', () => {
    const html = render(withQuestionEnding('in 2025'));
    expect(text(html)).toContain(
      '1 question names a past year. New questions can be written on the client’s Prompts tab.',
    );
    expect(html).toMatch(/what&#x27;s a good shared inbox tool for a small support team in 2025/);
    expect(html).toContain('data-testid="past-year-question"');
    expect(html).toContain('>Past year<');
  });

  it('links "Prompts tab" to the client’s Prompts tab, in the report’s own link style, and changes no word', () => {
    // U9: the sentence says where new questions are written; on the signed-in report it can take you there.
    const html = render(withQuestionEnding('in 2025'));
    const anchors = html.match(/<a [^>]*>Prompts tab<\/a>/g) ?? [];
    expect(anchors).toHaveLength(1);
    expect(anchors[0]).toContain(`href="/clients/${base.subject.clientId}/prompts"`);
    expect(anchors[0]).toContain('class="text-beacon-700 underline underline-offset-2"');
    expect(text(html)).toContain(
      '1 question names a past year. New questions can be written on the client’s Prompts tab.',
    );
  });

  it("leaves the pointer off the shared report, where there is no client's Prompts tab", () => {
    const html = renderPublic(withQuestionEnding('in 2025'));
    expect(html).toContain('1 question names a past year.');
    expect(html).not.toContain('Prompts tab');
    expect(html).not.toContain(`/clients/${base.subject.clientId}`);
    expect(html).toContain('>Past year<');
  });

  it("does not flag the scan's own year, a range ending in it, or a later year", () => {
    for (const suffix of ['in 2026', 'for 2025-26', 'by 2030']) {
      const html = render(withQuestionEnding(suffix));
      expect(html, suffix).not.toContain('past year');
      expect(html, suffix).not.toContain('>Past year<');
    }
  });

  it('judges a year against the scan, not against the calendar', () => {
    const html = render({ ...withQuestionEnding('in 2025'), scannedAt: '2025-03-01T00:00:00Z' });
    expect(html).not.toContain('past year');
  });

  it('prints no flag for a report with no questions on its shelf', () => {
    const html = render({ ...base, proof: { ...base.proof, promptShelf: [] } });
    expect(html).not.toContain('past year');
  });
});

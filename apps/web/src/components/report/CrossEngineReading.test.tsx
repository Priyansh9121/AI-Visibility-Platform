/**
 * Where the engines disagree — Epic 9.23, Layer 3.
 *
 * The value of this beat is entirely in what it refuses to claim. A cross-
 * engine reading that reports an outage as a disagreement, or silence as
 * consensus, is worse than no reading at all: both turn a gap in the data into
 * a finding about the client's visibility.
 *
 * So the happy path is one test and the rest are the two refusals.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ReportView } from './ReportView';
import { divergentReport, helpscoutReport, insufficientDataReport } from '@/lib/report/__fixtures__/reports';

type Report = Parameters<typeof ReportView>[0]['report'];

const render = (report: Report) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} />);

describe('the disagreement is the finding', () => {
  it('names how many questions the engines split on', () => {
    const html = render(divergentReport);
    expect(html).toContain('Where the engines disagree');
    expect(html).toContain('On 3 of 4 comparable questions');
  });

  it('says what the split means for a buyer, not just that it happened', () => {
    // A count alone is a statistic. The sentence is what makes it a finding.
    expect(render(divergentReport)).toContain(
      'sees a different shortlist from a buyer who asks the other',
    );
  });

  it('gives each engine its own mention count and sentiment', () => {
    const html = render(divergentReport);
    expect(html).toContain('Named Help Scout in 4 of 4');
    expect(html).toContain('Named Help Scout in 1 of 4');
    expect(html).toContain('sentiment 50.00');
  });

  it('reports the agreement rate over the comparable denominator', () => {
    expect(render(divergentReport)).toContain('25.00% agreement across 4 comparable questions');
  });
});

describe('it refuses to turn an outage into a finding', () => {
  it('an engine that did not answer is shown as silent, not as a miss', () => {
    // "Did not answer" and "named you 0 times" are different claims about a
    // client, and only one of them is true when a provider was down.
    const silent: Report = {
      ...divergentReport,
      proof: {
        ...divergentReport.proof,
        crossEngine: {
          ...divergentReport.proof.crossEngine,
          standings: [
            { engine: 'claude', answered: 4, mentioned: 4, mentionRate: '100.00', sentiment: '75.00' },
            { engine: 'chatgpt', answered: 0, mentioned: 0, mentionRate: '0.00', sentiment: null },
          ],
        },
      },
    };

    const html = render(silent);
    expect(html).toContain('Did not answer');
    expect(html).not.toContain('Named Help Scout in 0 of 0');
  });
});

describe('it refuses to report silence as consensus', () => {
  it('nothing comparable says so in words, and shows no percentage', () => {
    const alone: Report = {
      ...divergentReport,
      proof: {
        ...divergentReport.proof,
        crossEngine: {
          standings: [
            { engine: 'claude', answered: 4, mentioned: 4, mentionRate: '100.00', sentiment: '75.00' },
          ],
          splits: [],
          comparablePrompts: 0,
          agreementRate: null,
        },
      },
    };

    const html = render(alone);
    expect(html).toContain('there was nothing to compare');
    expect(html).toContain('not the same as');
    // The claim a null rate must never become.
    expect(html).not.toContain('100% agreement');
    expect(html).not.toContain('agreement across 0 comparable');
  });

  it('visibility agreement is never called a shared verdict', () => {
    // THE DEFECT THE FIRST LIVE RUN FOUND — Epic 9.24, front.com.
    //
    // Zero splits and three sentiments of 75.00, 58.33 and 41.67: the engines
    // agreed completely on whether Front appears and disagreed on tone in
    // eight of twelve prompts. The copy said they "gave the same verdict",
    // which the standings rendered beneath it contradicted. A report may not
    // make a claim its own page disproves.
    const agreedOnVisibilityOnly: Report = {
      ...divergentReport,
      proof: {
        ...divergentReport.proof,
        crossEngine: {
          standings: [
            { engine: 'chatgpt', answered: 12, mentioned: 12, mentionRate: '100.00', sentiment: '75.00' },
            { engine: 'claude', answered: 12, mentioned: 12, mentionRate: '100.00', sentiment: '58.33' },
            { engine: 'claude_search', answered: 12, mentioned: 12, mentionRate: '100.00', sentiment: '41.67' },
          ],
          splits: [],
          comparablePrompts: 12,
          agreementRate: '100.00',
        },
      },
    };

    const html = render(agreedOnVisibilityOnly);
    expect(html).not.toContain('same verdict');
    // It says what was actually compared, and hands the tone to the standings.
    expect(html).toContain('named Help Scout on all 12 comparable questions');
    expect(html).toContain('the tone each takes is below, and it can differ');
    // The spread the sentence must not paper over is still on the page.
    expect(html).toContain('sentiment 75.00');
    expect(html).toContain('sentiment 41.67');
  });

  it('genuine unanimity is stated as unanimity, and is a different sentence', () => {
    // helpscoutReport is real data whose two engines agreed on all three
    // prompts. That IS consensus, and must not be flattened into the
    // nothing-to-compare wording above.
    const html = render(helpscoutReport);
    expect(html).toContain('named Help Scout on all 3 comparable questions');
    expect(html).toContain('3 comparable questions');
    expect(html).not.toContain('there was nothing to compare');
  });

  it('a scan with no answered results renders no reading at all', () => {
    // Zero standings: there is nothing to say, and an empty section with a
    // heading would imply there was.
    expect(render(insufficientDataReport)).not.toContain('Where the engines disagree');
  });
});

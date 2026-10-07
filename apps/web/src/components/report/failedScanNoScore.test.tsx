/**
 * A FAILED scan has no score on screen — checklist 3.24. The API sends none
 * (`test_failed_scan_hidden.py` pins score, dimensions, the sub-score and the fix
 * points). These pin the narrow part that is the screen's own: the narrative, the
 * headline and the lines built from the composite show none even if a score
 * reaches them, and say why in the founder's words. Other score-derived notes on
 * the page (formula versions, the inputs fingerprint) rely on the API's null.
 * Every other status keeps its copy.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Report } from '@avp/shared-types';
import { ReportView } from './ReportView';
import { DashboardView } from '../dashboard/DashboardView';
import { latestScores } from '@/lib/shell/clientList';
import { dashboard, scan } from '@/lib/dashboard/__fixtures__/dashboards';
import { deriveNarrative, scoreHeading } from '@/lib/report/derive';
import { helpscoutReport, unscoredReport } from '@/lib/report/__fixtures__/reports';

const render = (report: Report, publicView = false) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} publicView={publicView} />);

// A report as it would look if a score leaked through for a failed scan.
const failedWithScore: Report = { ...helpscoutReport, scanStatus: 'failed' };
const failedNoScore: Report = {
  ...helpscoutReport,
  scanStatus: 'failed',
  score: null,
  dimensions: [],
};

describe('the report of a failed scan', () => {
  it('derives a failed narrative with no composite, even if a score is present', () => {
    expect(failedWithScore.score).not.toBeNull();
    const n = deriveNarrative(failedWithScore);
    expect(n.status).toBe('failed');
    expect(n.composite).toBeNull();
    expect(n.potentialComposite).toBeNull();
    expect(scoreHeading(n, 'Help Scout')).toBe('Help Scout has no score: this scan failed.');
  });

  it.each([false, true])('says it has no score, and not the "not computed yet" line (public=%s)', (pub) => {
    for (const r of [failedNoScore, failedWithScore]) {
      const html = render(r, pub);
      expect(html).toContain('This scan failed, so it has no score.');
      expect(html).toContain('has no score: this scan failed.');
      expect(html).not.toContain('composite is simply not computed yet');
      expect(html).not.toContain('has not been scored yet');
    }
  });

  it('draws no ledger column and no score numeral', () => {
    const html = render(failedWithScore);
    expect(html).not.toContain('Help Scout scores');
    expect(html).not.toContain('if these fixes close every gap');
    expect(html).not.toContain(' today.');
  });

  it('leaves every other status with its own copy', () => {
    for (const scanStatus of ['succeeded', 'partial', 'cancelled', 'running']) {
      const html = render({ ...unscoredReport, scanStatus });
      expect(html, scanStatus).toContain('This scan has run but has not been scored.');
      expect(html, scanStatus).not.toContain('This scan failed, so it has no score.');
    }
    expect(deriveNarrative(helpscoutReport).status).toBe('scored');
  });
});

describe('a failed report that still carries dimensions', () => {
  it('derives no dimensions, and draws no gap beat or dimension ledger', () => {
    expect(failedWithScore.dimensions.length).toBeGreaterThan(0);
    expect(deriveNarrative(failedWithScore).dimensions).toEqual([]);
    const html = render(failedWithScore);
    expect(html).not.toContain('is costing the most');
    // The scored report does draw them, so the absence below means something.
    const scored = render(helpscoutReport);
    expect(scored).toContain('avp-ledger');
    expect(html).not.toContain('avp-ledger');
  });

  it('says in the gap beat that no gap was measured, not that results are coming', () => {
    for (const r of [failedNoScore, failedWithScore]) {
      const html = render(r);
      expect(html).toContain('This scan failed, so no gap was measured.');
      expect(html).not.toContain('Once this scan produces engine results');
    }
  });

  it('leaves the gap beat copy of an unscored scan as it was', () => {
    expect(render(unscoredReport)).toContain('Once this scan produces engine results');
  });
});

describe('the dashboard row of a failed scan', () => {
  it('draws no meter fill even if the API sent a composite', () => {
    const html = renderToStaticMarkup(
      <DashboardView
        dashboard={dashboard([scan({ status: 'failed', compositeScore: '72.00' })])}
      />,
    );
    expect(html).toContain('Not scored');
    expect(html).not.toContain('avp-meter__lit');
    // The row itself. The API sends no composite for a failed scan, so the page-level
    // figures that read it (the portfolio hero) are the API's to keep clear.
    const row = html.slice(html.indexOf('<tbody>'));
    expect(row).not.toContain('72');
  });

  it('the hero median and the sidebar ignore a failed scan that carries a composite', () => {
    const d = dashboard([
      scan({ id: 'scan_f', clientId: 'clnt_a', status: 'failed', compositeScore: '72.00', createdAt: '2026-08-25T10:00:00Z' }),
      scan({ id: 'scan_s', clientId: 'clnt_b', clientName: 'Linear', status: 'succeeded', compositeScore: '40.00' }),
    ]);
    const html = renderToStaticMarkup(<DashboardView dashboard={d} />);
    expect(html).toContain('Portfolio visibility: 40 out of 100');
    expect(html).not.toContain('72');
    const scores = latestScores(d);
    expect(scores.get('clnt_a')).toBeNull();
    expect(scores.get('clnt_b')).toBe(40);
  });

  it('still lights the meter for a partial scan', () => {
    const html = renderToStaticMarkup(
      <DashboardView dashboard={dashboard([scan({ status: 'partial', compositeScore: '55.82' })])} />,
    );
    expect(html).toContain('avp-meter__lit');
  });
});

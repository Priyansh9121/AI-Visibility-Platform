/**
 * The dashboard screen — Epic 9.3.
 *
 * Rendered to static markup and asserted over, the approach ReportView.test.tsx
 * and the design system's own render tests use.
 *
 * The states below are deliberately the same ones `test_dashboard.py` proves
 * the endpoint produces — empty, unscored-still-appears, null-not-zero,
 * newest-first, a bounded page. An endpoint that is careful about a degraded
 * scan is worth nothing if the screen renders it as a zero anyway, so each of
 * those guarantees is re-asserted here at the point it reaches a human.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { DashboardView, formatStamp } from './DashboardView';
import {
  detectionOnlyDashboard,
  emptyDashboard,
  failedDashboard,
  fullPageDashboard,
  insufficientDataDashboard,
  newestFirstDashboard,
  noScansYetDashboard,
  partialDashboard,
  runningDashboard,
  runningPlusFinishedDashboard,
  scoredDashboard,
  unscoredQueuedDashboard,
} from '@/lib/dashboard/__fixtures__/dashboards';
import type { Dashboard } from '@avp/shared-types';

const render = (dashboard: Dashboard, props: Partial<Parameters<typeof DashboardView>[0]> = {}) =>
  renderToStaticMarkup(
    <DashboardView dashboard={dashboard} onRerun={() => {}} {...props} />,
  );

describe('agency identity and seat usage', () => {
  it('names the agency, not us', () => {
    const html = render(scoredDashboard);
    expect(html).toContain('Northlight Partners');
    expect(html).not.toContain('AI Visibility Platform');
  });

  it('shows seats used against the limit, and the counts', () => {
    const html = render(fullPageDashboard);
    expect(html).toContain('2 / 5');
    expect(html).toContain('>12<'); // clients
    expect(html).toContain('>42<'); // scans
  });
});

describe('the empty state is written, not blank', () => {
  it('an agency with nothing renders copy and a way forward, not a table', () => {
    const html = render(emptyDashboard);
    expect(html).toContain('No scans yet');
    expect(html).toContain('Add your first client');
    // No table at all — an empty grid with headers is not an empty state.
    expect(html).not.toContain('avp-table');
  });

  it('is honest about the wait rather than silent about it', () => {
    expect(render(emptyDashboard)).toContain('about six minutes');
  });

  it('clients but no scans is its own state, not the empty agency one', () => {
    // isEmpty is false here — the endpoint only sets it when there are neither
    // clients nor scans, so this gap is reachable and must say something.
    const html = render(noScansYetDashboard);
    expect(html).toContain('2 clients added, but no scans have been run yet.');
    expect(html).not.toContain('Add your first client');
    expect(html).toContain('avp-table');
  });
});

describe('a null score is a null score, never a zero', () => {
  it('INSUFFICIENT_DATA renders an em dash', () => {
    const html = render(insufficientDataDashboard);
    expect(html).toContain('—');
    expect(html).not.toContain('0.0');
    expect(html).not.toContain('avp-badge--visibility');
  });

  it('an unscored queued scan still appears, with no score', () => {
    // The endpoint LEFT-joins the score so this row survives. If the screen
    // dropped it, that care would be wasted.
    const html = render(unscoredQueuedDashboard);
    expect(html).toContain('queued.example');
    expect(html).toContain('Queued');
    expect(html).not.toContain('0.0');
  });

  it('a real score renders its decimal value and an ordinal band', () => {
    const html = render(scoredDashboard);
    expect(html).toContain('38.4'); // 38.35, to one decimal
    expect(html).toContain('avp-badge--visibility');
  });
});

describe('every scan status reaches the screen as itself', () => {
  it('partial is shown as partial, not as success or failure', () => {
    const html = render(partialDashboard);
    expect(html).toContain('Partial');
    expect(html).toContain('avp-badge--warn');
    // And it keeps its score: a partial scan still measured something.
    expect(html).toContain('55.8');
  });

  it('failed is toned as danger', () => {
    expect(render(failedDashboard)).toContain('avp-badge--danger');
  });

  it('succeeded is toned as success', () => {
    expect(render(scoredDashboard)).toContain('avp-badge--success');
  });
});

describe('ordering and page bounds', () => {
  it('renders rows in the order given — newest first', () => {
    const html = render(newestFirstDashboard);
    const positions = ['Third', 'Second', 'First'].map((n) => html.indexOf(n));
    expect(positions.every((p) => p >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it('renders exactly the page it was handed, inventing and dropping nothing', () => {
    const html = render(fullPageDashboard);
    const rows = html.match(/<tr[ >]/g) ?? [];
    // 10 body rows plus the header row.
    expect(rows).toHaveLength(11);
    for (let i = 0; i < 10; i += 1) expect(html).toContain(`client${i}.example`);
  });
});

describe('each row links to its own report', () => {
  it('links by scan id', () => {
    const html = render(newestFirstDashboard);
    expect(html).toContain('href="/scans/scan_01M0SCANFIXTURE000000003/report"');
    expect(html).toContain('href="/scans/scan_01M0SCANFIXTURE000000001/report"');
  });

  it('a scan with no score still links — the report explains why there is none', () => {
    expect(render(unscoredQueuedDashboard)).toContain('/report"');
  });
});

describe('re-run reflects work already under way', () => {
  it('offers a re-run on a finished scan', () => {
    const html = render(scoredDashboard);
    expect(html).toContain('Re-run');
    expect(html).not.toContain('disabled=""');
  });

  it('does not offer a second scan while one is running', () => {
    const html = render(runningDashboard);
    expect(html).toContain('Running…');
    expect(html).toContain('disabled=""');
  });

  it('disables re-run on EVERY row of a client that has one in flight', () => {
    // get_or_create_scan reuses an unfinished scan, but the running scan is
    // uncommitted for its whole duration, so a second POST would create a
    // second scan and spend a second scan's worth of model calls. The older
    // finished row must not become a side door to that.
    const html = render(runningPlusFinishedDashboard);
    expect(html.match(/disabled=""/g) ?? []).toHaveLength(2);
    expect(html).not.toContain('>Re-run<');
  });

  it('shows a request already in flight from this browser as starting', () => {
    const html = render(scoredDashboard, {
      rerunning: new Set([scoredDashboard.recentScans[0]!.clientId]),
    });
    expect(html).toContain('Starting…');
    expect(html).toContain('disabled=""');
  });

  it('surfaces a failed re-run instead of swallowing it', () => {
    const html = render(scoredDashboard, { rerunError: 'The provider was unreachable.' });
    expect(html).toContain('The scan could not be started');
    expect(html).toContain('The provider was unreachable.');
  });

  it('a detect-only run does not disable re-run forever', () => {
    // Epic 9.6's regression. Running competitor detection opens a QUEUED scan
    // for the CompetitorSet to hang off. Treating QUEUED as busy disabled
    // re-run for that client indefinitely, on the strength of a scan nobody
    // had started — and it read as "Queued", which looks correct and does not
    // invite the question. A pilot agency that detects without immediately
    // scanning hit this.
    const html = render(detectionOnlyDashboard);
    expect(html).toContain('Queued');
    expect(html).toContain('>Re-run<');
    expect(html).not.toContain('disabled=""');
  });

  it('a running scan still blocks it — the guard did not simply go away', () => {
    const html = render(runningDashboard);
    expect(html).toContain('disabled=""');
    expect(html).not.toContain('>Re-run<');
  });

  it('a queued scan alongside a running one is still blocked', () => {
    // The block is per CLIENT, not per row. A client whose scan is running must
    // not become re-runnable because some other row of theirs reads queued.
    const board = {
      ...runningDashboard,
      recentScans: [
        runningDashboard.recentScans[0]!,
        { ...runningDashboard.recentScans[0]!, id: 'scan_queued_sibling', status: 'queued' as const },
      ],
    };
    const html = render(board);
    expect(html.match(/disabled=""/g) ?? []).toHaveLength(2);
  });

  it('renders read-only when no handler is supplied', () => {
    const html = renderToStaticMarkup(<DashboardView dashboard={scoredDashboard} />);
    expect(html).toContain('disabled=""');
  });
});

describe('timestamps are stable regardless of where they render', () => {
  it('formats in UTC, so CI and a browser agree', () => {
    expect(formatStamp('2026-08-24T10:15:00Z')).toBe('24 Aug 2026, 10:15 UTC');
  });

  it('a scan that has not finished shows an em dash, not a fake time', () => {
    expect(formatStamp(null)).toBe('—');
    expect(formatStamp(undefined)).toBe('—');
  });

  it('an unparseable stamp degrades rather than rendering Invalid Date', () => {
    expect(formatStamp('not-a-date')).toBe('—');
  });
});

describe('the screen carries no third-party prose', () => {
  it('renders only identity, status, numbers and timestamps', () => {
    // ip-safety.md #7 — the dashboard shows facts about scans. Nothing an
    // engine or a competitor page said may reach it.
    const html = render(fullPageDashboard) + render(partialDashboard);
    expect(html).not.toContain('answer');
    expect(html).not.toContain('snippet');
    expect(html).not.toContain('“');
  });
});

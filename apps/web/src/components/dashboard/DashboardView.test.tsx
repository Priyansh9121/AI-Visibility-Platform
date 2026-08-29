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
  it('INSUFFICIENT_DATA renders an em dash and SAYS it is not scored', () => {
    const html = render(insufficientDataDashboard);
    expect(html).toContain('—');
    expect(html).not.toContain('0.0');
    // Epic 9.9: a dash alone read as a rendering fault. The absence is now
    // named. This scan FINISHED without a score, which is a permanent fact
    // until it is re-run — not the same as one still being measured.
    expect(html).toContain('Not scored');
    expect(html).not.toContain('Measuring');
    // The track still draws, so the row keeps its shape...
    expect(html).toContain('avp-meter__track');
    // ...but nothing is lit. A zero-width fill would read as a score of 0.
    expect(html).toContain('avp-meter--empty');
    expect(html).not.toContain('avp-meter__lit');
  });

  it('an unscored queued scan still appears, and reads as MEASURING not unscored', () => {
    // The endpoint LEFT-joins the score so this row survives. If the screen
    // dropped it, that care would be wasted.
    const html = render(unscoredQueuedDashboard);
    expect(html).toContain('queued.example');
    expect(html).toContain('Queued');
    expect(html).not.toContain('0.0');
    // The distinction Epic 9.9 added: no score YET is not no score. Both are
    // null in the payload; `status` is what separates them, and the screen
    // must not collapse them back together.
    expect(html).toContain('Measuring');
    expect(html).not.toContain('Not scored');
    expect(html).not.toContain('avp-meter__lit');
  });

  it('a real score renders the same rounded number the report shows', () => {
    const html = render(scoredDashboard);
    // 38.35 renders as 38, matching ScoreDisplay's Math.round on the report.
    // Epic 9.9 changed this from '38.4' deliberately: a dashboard showing 38.4
    // beside a report showing 38 is exactly the two-screens-one-product
    // mismatch this pass exists to close. Sub-point precision is not a
    // distinction an agency operator acts on.
    expect(html).toContain('>38<');
    expect(html).not.toContain('38.4');
    // Lit length IS the score — the Luminance Ledger's identity at list scale.
    expect(html).toContain('avp-meter__lit');
    expect(html).toContain('width:38.35%'); // the BAR keeps full precision
    expect(html).toContain('Barely visible'); // visibilityBand(38) — under 40
  });

  it('the meter never lights a band it did not earn', () => {
    // Guards the direction of the mapping. A meter that filled by row index,
    // or inverted, would still render a bar and still pass every assertion
    // above about a bar existing.
    // 55.82 -> numeral 56, but the lit length stays 55.82%: the numeral is
    // rounded for reading, the BAR is the number. Same discipline as the
    // Ledger, whose lit height is the composite exactly.
    const html = render(partialDashboard);
    expect(html).toContain('width:55.82%');
    expect(html).toContain('>56<');
    expect(html).toContain('Emerging');
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

describe('the page says when it is watching, and when it has stopped', () => {
  it('tells the user the page updates itself while a scan runs', () => {
    // A page that silently rearranges itself is unsettling. Epic 9.7 polls, so
    // it says so.
    const html = render(runningDashboard, { live: true });
    expect(html).toContain('this page updates itself');
  });

  it('says nothing when nothing is running', () => {
    expect(render(scoredDashboard, { live: false })).not.toContain('this page updates itself');
    expect(render(scoredDashboard)).not.toContain('this page updates itself');
  });

  it('surfaces a stalled poller rather than showing a stale page as if it were live', () => {
    const html = render(runningDashboard, {
      live: true,
      pollProblem: 'Live updates have stopped — the server could not be reached.',
    });
    expect(html).toContain('This page has stopped updating');
    expect(html).toContain('the server could not be reached');
  });

  it('shows no such notice when polling is healthy', () => {
    expect(render(runningDashboard, { live: true })).not.toContain(
      'This page has stopped updating',
    );
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

/**
 * The empty states, rebuilt in Epic 9.19.
 *
 * Both were already written rather than blank — Epic 9.3 and 9.9 saw to that —
 * so what these assert is the second half: that they are drawn in this
 * product's own language, and that the drawing does not start asserting
 * measurements nobody took.
 */
describe('a brand-new agency is shown the shape of a scan, not a graphic', () => {
  it('draws the five real dimensions and their real weights', () => {
    // Not example data. These are scoring-spec.md §6's weights, the same five
    // every report is built from — which is the whole reason it is honest to
    // draw them before anything has been measured.
    const html = render(emptyDashboard);
    for (const [label, weight] of [
      ['Mention Rate', 30],
      ['Share of Voice', 25],
      ['Citation Strength', 20],
      ['Sentiment', 15],
      ['Technical Foundation', 10],
    ] as const) {
      expect(html).toContain(label);
      expect(html).toContain(`${weight}% weight`);
    }
  });

  it('the weights sum to 100, so the figure is the real scoring model', () => {
    // A drawing whose parts did not add up would be decoration wearing the
    // Ledger's clothes.
    expect(30 + 25 + 20 + 15 + 10).toBe(100);
  });

  it('claims no score, because none has been taken', () => {
    const html = render(emptyDashboard);
    expect(html).toContain('avp-ledger--unmeasured');
    expect(html).toContain('none of them measured yet');
    expect(html).not.toContain('0 out of 100');
  });

  it('does not animate — the dashboard performs no entrance', () => {
    // `animate={false}` means the bars are painted in their final state on
    // first paint. There is nothing to light anyway; what this guards is the
    // screen acquiring a build-in on a page checked fifty times a day.
    expect(render(emptyDashboard)).not.toContain('avp-ledger--staggered');
  });

  it('still says everything it said before, and still offers the way out', () => {
    const html = render(emptyDashboard);
    expect(html).toContain('No scans yet');
    expect(html).toContain('about six minutes');
    expect(html).toContain('Add your first client');
  });
});

describe('clients but no scans gets the same treatment, without the figure', () => {
  it('keeps its own sentence — it is a different fact from an empty agency', () => {
    const html = render(noScansYetDashboard);
    expect(html).toContain('2 clients added, but no scans have been run yet.');
    expect(html).not.toContain('Add your first client');
  });

  it('is an EmptyState with an action rather than a bare line in a table cell', () => {
    const html = render(noScansYetDashboard);
    expect(html).toContain('avp-empty');
    expect(html).toContain('Run the first scan');
  });

  it('carries no ledger — there is nothing to explain twice', () => {
    // The unmeasured ledger belongs to the agency that has never scanned
    // anything. Repeating it here would make the figure furniture.
    expect(render(noScansYetDashboard)).not.toContain('avp-ledger');
  });
});

/**
 * Status motion — Epic 9.19.
 *
 * The dashboard is deliberately excluded from arrival motion, and stays so.
 * What it gains is motion tied to something that is genuinely still happening,
 * which on this screen is exactly one thing: a scan that is running while the
 * page re-reads itself every five seconds.
 */
describe('the live dot marks what is actually happening', () => {
  it('breathes on a running scan', () => {
    expect(render(runningDashboard)).toContain('avp-badge__pulse');
  });

  it('does not breathe on a finished one', () => {
    expect(render(scoredDashboard)).not.toContain('avp-badge__pulse');
    expect(render(failedDashboard)).not.toContain('avp-badge__pulse');
  });

  it('marks the running row and not its finished neighbour', () => {
    // Two rows for the same client, one running and one complete. Exactly one
    // dot, or the mark says nothing.
    const html = render(runningPlusFinishedDashboard, { live: true });
    const dots = html.split('avp-badge__pulse').length - 1;
    // One on the running row, one on the header's "Live" chip.
    expect(dots).toBe(2);
  });

  it('the header chip appears only while the page is really polling', () => {
    expect(render(runningPlusFinishedDashboard, { live: true })).toContain('>Live<');
    expect(render(runningPlusFinishedDashboard)).not.toContain('>Live<');
  });

  it('a queued scan is not marked live — nothing is running yet', () => {
    expect(render(unscoredQueuedDashboard)).not.toContain('avp-badge__pulse');
  });
});

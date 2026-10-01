/**
 * The public report never says whether a scan counted — checklist 3.15.
 *
 * `ReportView` shows the agency, on the signed-in report, why a scan is partial
 * or failed and whether it counted ("It counts as one of your scans."). To a
 * prospect that sentence is wrong. The component hides it when it is given
 * `publicView` (`ReportView.test.tsx` pins that), and this is the other half:
 * the share route has to pass it, and the signed-in route must not.
 *
 * A SOURCE scan rather than a render, for the reason `reportWidth.test.ts`
 * gives: both routes are client components that resolve their params and
 * fetch, and what is guarded is one prop on one JSX element.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

const SHARE_ROUTE = read('./page.tsx');
const SHARE_LEGACY_ROUTE = read('./[token]/page.tsx');
const REPORT_ROUTE = read('../scans/[scanId]/report/page.tsx');

describe('the share route hands ReportView its public view', () => {
  it('renders exactly one ReportView, and it is a publicView', () => {
    expect(SHARE_ROUTE.match(/<ReportView\b/g)).toHaveLength(1);
    expect(SHARE_ROUTE).toMatch(/<ReportView\b[^>]*\bpublicView\b[^>]*\/>/);
  });

  it('does not reach for the outcome sentence itself', () => {
    expect(SHARE_ROUTE).not.toMatch(/scanOutcome/);
    expect(SHARE_LEGACY_ROUTE).not.toMatch(/scanOutcome|ReportView/);
  });
});

describe('the signed-in route does not', () => {
  it('renders ReportView without publicView, so the agency is told', () => {
    // Not vacuous: the route does render the document, and does not opt out.
    expect(REPORT_ROUTE).toMatch(/<ReportView\b/);
    expect(REPORT_ROUTE).not.toMatch(/\bpublicView\b/);
  });
});

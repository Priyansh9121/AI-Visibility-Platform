/**
 * The report route renders at the PRESENTING width — Epic 14.1, corrected.
 *
 * design-direction.md's width table (Epic 9.19) governs every route:
 * `/dashboard`, `/clients` and `/settings` are Working screens at
 * `--avp-app-max`; `/scans/{id}/report` and `/share/{token}` are Presenting
 * screens at `--avp-report-width`. Epic 14.1's first cut put `wide` on the
 * report route to solve a framing problem and, in doing so, quietly moved the
 * route to the Working width — the desk it added then spanned 90rem around a
 * 52rem page. This is the assertion that stops that happening again without
 * someone first amending the table.
 *
 * A SOURCE scan rather than a render, on purpose: the route is a client
 * component that resolves `params` and fetches, and what this guards is a
 * prop on a JSX element, which is exactly what a grep sees and a render
 * would have to mock its way to. `reportIsolation.test.ts` guards the same
 * surfaces the same way.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

const REPORT_ROUTE = read('./[scanId]/report/page.tsx');
const SHARE_ROUTE = read('../share/[token]/page.tsx');
const DASHBOARD_ROUTE = read('../dashboard/page.tsx');
const SHELL_CSS = read('../../../../../packages/design-system/src/styles/components.css');

/** Every `<WorkspaceShell …>` opening tag in a source, with its props. */
const shells = (source: string): string[] =>
  [...source.matchAll(/<WorkspaceShell\b([^>]*)>/g)].map((m) => m[1]!);

describe('the report route keeps the Presenting width', () => {
  it('renders the shell without `wide`, in every branch', () => {
    const tags = shells(REPORT_ROUTE);
    // Loading, error and ready all frame the route; none may widen it.
    expect(tags.length).toBeGreaterThanOrEqual(3);
    for (const props of tags) expect(props).not.toMatch(/\bwide\b/);
  });

  it('is not a vacuous check — a Working route does use `wide`', () => {
    expect(shells(DASHBOARD_ROUTE).some((props) => /\bwide\b/.test(props))).toBe(true);
  });

  it('gets its measure from the shell default, which IS report width plus padding', () => {
    // The default rule is what the route falls back to without `wide`. If
    // that ever stops being the report measure, this route drifts again.
    const rule = SHELL_CSS.match(/\.avp-shell__content\s*\{([^}]*)\}/);
    expect(rule, '.avp-shell__content rule missing').not.toBeNull();
    expect(rule![1]).toMatch(/max-width:\s*calc\(var\(--avp-report-width\)/);
    const wide = SHELL_CSS.match(/\.avp-shell__content--wide\s*\{([^}]*)\}/);
    expect(wide![1]).toMatch(/--avp-app-max/);
  });

  it('the share route never used the shell at all, so the table holds there by construction', () => {
    expect(SHARE_ROUTE).not.toContain('WorkspaceShell');
    expect(SHARE_ROUTE).toContain('max-w-report');
  });
});

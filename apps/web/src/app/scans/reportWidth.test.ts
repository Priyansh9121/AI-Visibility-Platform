/**
 * The report route renders at the REPORT width, and the desk hugs the page —
 * Epic 14.1 (corrected), reversed on purpose by Epic 17.
 *
 * design-direction.md's width table governs every route. Since Epic 9.19 it
 * put `/scans/{id}/report` and `/share/{token}` at `--avp-report-width`,
 * 52rem, and this file's first version asserted the route never carried
 * `wide`, because Epic 14.1's first cut had used `wide` to solve a framing
 * problem and quietly moved the route to the Working measure — the desk it
 * added then spanned 90rem around a 52rem page.
 *
 * Epic 17 changed the rule, not the bug. The founder saw the 52rem page live
 * twice and found it narrow, so the table's row for these two routes is now
 * 72rem, and the route DOES carry `wide` — but the frame caps itself at the
 * report width, so the desk still hugs the page whatever the shell's
 * measure. The assertion flipped deliberately, and what it guards is what
 * 14.1 actually cared about: a desk that fits its page.
 *
 * A SOURCE scan rather than a render, on purpose: the route is a client
 * component that resolves `params` and fetches, and what this guards is a
 * prop on a JSX element and a rule in a stylesheet, which is exactly what a
 * grep sees and a render would have to mock its way to.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

const REPORT_ROUTE = read('./[scanId]/report/page.tsx');
const SHARE_ROUTE = read('../share/[token]/page.tsx');
const COMPARE_ROUTE = read('../page.tsx');
const SHELL_CSS = read('../../../../../packages/design-system/src/styles/components.css');
const TOKENS_CSS = read('../../../../../packages/design-system/src/styles/tokens.css');

/** Every `<WorkspaceShell …>` opening tag in a source, with its props. */
const shells = (source: string): string[] =>
  [...source.matchAll(/<WorkspaceShell\b([^>]*)>/g)].map((m) => m[1]!);

const rule = (selector: string): string => {
  // Anchored at a line start, so `.avp-report` cannot match the tail of
  // `.avp-report-frame > .avp-report`.
  const m = SHELL_CSS.match(new RegExp('(?:^|\\n)' + selector.replace(/[.\-]/g, '\\$&') + '\\s*\\{([^}]*)\\}'));
  expect(m, `${selector} rule missing`).not.toBeNull();
  return m![1]!;
};

describe('the report route is wide, and the desk still hugs the page — Epic 17', () => {
  it('renders the shell WITH `wide`, in every branch', () => {
    const tags = shells(REPORT_ROUTE);
    // Loading, error and ready all frame the route; all three widen it, so
    // the frame never jumps between branches.
    expect(tags.length).toBeGreaterThanOrEqual(3);
    for (const props of tags) expect(props).toMatch(/\bwide\b/);
  });

  it('is not a vacuous check — a prose route does NOT use `wide`', () => {
    // Compare (`/` signed in) is a page of prose on the shell default.
    expect(shells(COMPARE_ROUTE).some((props) => /\bwide\b/.test(props))).toBe(false);
  });

  it('the frame caps itself at the report width, so the hug does not depend on the shell', () => {
    // This is the half Epic 14.1 cared about. If the frame ever loses its
    // cap, `wide` puts a 90rem desk around a 72rem page again.
    expect(rule('.avp-report-frame')).toMatch(/max-width:\s*calc\(var\(--avp-report-width\)/);
    expect(rule('.avp-report-frame')).toMatch(/margin-inline:\s*auto/);
    expect(rule('.avp-report')).toMatch(/max-width:\s*var\(--avp-report-width\)/);
  });

  it('the shell default is the reading page, not the report', () => {
    expect(rule('.avp-shell__content')).toMatch(/max-width:\s*calc\(var\(--avp-page-width\)/);
    expect(rule('.avp-shell__content--wide')).toMatch(/--avp-app-max/);
  });

  it('the table\u2019s numbers are the tokens\u2019 numbers', () => {
    expect(TOKENS_CSS).toMatch(/--avp-report-width:\s*72rem/);
    expect(TOKENS_CSS).toMatch(/--avp-page-width:\s*52rem/);
    expect(1).toBe(2); // THROWAWAY: proving the gate catches a failing assertion
  });

  it('the share route never used the shell; its page is the report width and its states are prose', () => {
    expect(SHARE_ROUTE).not.toContain('WorkspaceShell');
    expect(SHARE_ROUTE).toContain('avp-report-frame--page');
    expect(SHARE_ROUTE).toContain('max-w-report');
    expect(SHARE_ROUTE).toContain('max-w-page');
  });
});

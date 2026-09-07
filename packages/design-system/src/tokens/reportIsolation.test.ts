import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * THE GUARANTEE: the report never references the Working-screen accent layer.
 *
 * Epic 9.24 gave every Working screen a second, saturated palette (`bench-*`)
 * and left the report exactly as it was. That promise is worth nothing written
 * in a comment — comments do not fail. The report is the artefact this product
 * sells: it gets printed, photocopied, and read by a prospect's CMO across a
 * conference table, and design-direction.md §1's restraint is what makes it
 * survive all three. A single `bench` chip finding its way into it would be
 * invisible in review and fatal in a meeting.
 *
 * So this is the assertion. It scans the report's real source — the document
 * primitives, the view that assembles them, and both routes that serve it —
 * and fails if any of them so much as names a bench token.
 *
 * WHY IT ALSO ASSERTS THE OPPOSITE
 * ---------------------------------
 * A guard that passes because nothing anywhere uses the tokens is a guard that
 * has stopped testing anything. The `positive control` block below fails if the
 * Working screens are NOT using bench — so this file cannot go green by the
 * feature having been quietly reverted.
 *
 * NOT LISTED, AND WHY: the PDF export. `apps/api/services/pdf.py` is a
 * hand-rolled PDF writer whose only tonal control is a `grey: bool` — it reads
 * no stylesheet and no token file, in any language, so it cannot reference
 * these tokens even in principle. Listing it would be a vacuous assertion
 * dressed as a real one.
 */

const here = dirname(fileURLToPath(import.meta.url));

/** Walk up to the workspace root, so these paths do not depend on the cwd. */
function repoRoot(): string {
  let dir = here;
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, 'pnpm-workspace.yaml'))) return dir;
    dir = dirname(dir);
  }
  throw new Error('workspace root not found from ' + here);
}

const ROOT = repoRoot();

/**
 * Every surface that renders the document a client receives.
 *
 * A directory pulls in everything below it, so a file ADDED to the report
 * tomorrow is covered without anyone remembering to add it here — which is the
 * failure mode a hand-listed set of files has.
 */
const REPORT_SURFACES: readonly string[] = [
  'packages/design-system/src/components/report',
  'apps/web/src/components/report',
  'apps/web/src/app/share',
  'apps/web/src/app/scans',
];

/**
 * Where the accent layer is actually reached from.
 *
 * TWO LISTS, BECAUSE THERE ARE TWO WAYS TO REACH IT. Working SCREENS never name
 * a bench token — they pass `accent={n}` or `palette="working"` and let the
 * design system resolve it, which is the whole point of having a design system.
 * The COMPONENTS are where the tokens are named. A control that looked for
 * token names in `apps/web` would fail on a correct implementation, which is
 * how this one was first written and why it is written twice now.
 */
const ACCENT_COMPONENTS: readonly string[] = [
  'packages/design-system/src/components/StatTile.tsx',
  'packages/design-system/src/components/shell/AppShell.tsx',
  'packages/design-system/src/components/shell/LocalNav.tsx',
];

/** Screens design-direction.md §0 puts in the Working column. */
const WORKING_SURFACES: readonly string[] = [
  'apps/web/src/components/dashboard',
  'apps/web/src/components/clients',
  'apps/web/src/components/client',
  'apps/web/src/components/shell',
];

/** How a Working screen opts in without naming a token. */
const OPT_IN = /accent=\{|palette="working"/;

const SOURCE = /\.(ts|tsx|css)$/;

function filesUnder(rel: string): string[] {
  const abs = join(ROOT, rel);
  if (!existsSync(abs)) return [];
  if (statSync(abs).isFile()) return SOURCE.test(abs) ? [abs] : [];
  const out: string[] = [];
  for (const entry of readdirSync(abs)) {
    out.push(...filesUnder(join(rel, entry)));
  }
  return out;
}

/**
 * Any reference to a bench token, in any of the forms it can be written.
 *
 * All four are checked because there are four ways to reach the same colour and
 * a guard that knew only one of them would be trivially bypassed by accident:
 * the custom property, the Tailwind utility, and the two TypeScript helpers.
 */
const BENCH_REFERENCE = /--avp-bench-|\bbench-[1-7]\b|\bbenchColor\b|\bbenchVar\b|\bBENCH_ACCENTS\b/;

describe('the report is isolated from the Working-screen accent layer', () => {
  const reportFiles = REPORT_SURFACES.flatMap(filesUnder);

  it('finds the report surfaces it is supposed to be guarding', () => {
    // If the report is moved or renamed, this fails LOUDLY rather than the
    // scan below silently passing over an empty file list.
    expect(reportFiles.length).toBeGreaterThan(0);
    for (const surface of REPORT_SURFACES) {
      expect(filesUnder(surface).length, `no source under ${surface}`).toBeGreaterThan(0);
    }
  });

  for (const surface of REPORT_SURFACES) {
    it(`${surface} references no bench token`, () => {
      const offenders = filesUnder(surface)
        .filter((f) => BENCH_REFERENCE.test(readFileSync(f, 'utf8')))
        .map((f) => relative(ROOT, f));
      expect(offenders).toEqual([]);
    });
  }

  /**
   * The stylesheet is one file for the whole product, so it cannot be guarded
   * by path. It is guarded by SELECTOR instead: any rule scoped to a report
   * class is a report surface wherever it happens to sit in the file.
   */
  it('no report-scoped CSS rule reads a bench token', () => {
    const css = readFileSync(
      join(ROOT, 'packages/design-system/src/styles/components.css'),
      'utf8',
    );
    const offenders: string[] = [];
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = match[1] ?? '';
      const body = match[2] ?? '';
      if (/avp-report/.test(selector) && BENCH_REFERENCE.test(body)) {
        offenders.push(selector.trim().split('\n').pop()!.trim());
      }
    }
    expect(offenders).toEqual([]);
  });

  /**
   * POSITIVE CONTROL — two halves, because reaching the layer takes two steps.
   *
   * Without these, deleting the whole accent layer would turn this file green:
   * every "references no bench token" assertion would pass trivially, and the
   * guard would be reporting success for a feature that no longer existed.
   */
  it('the design-system components that own the accent do reference it', () => {
    const silent = ACCENT_COMPONENTS.filter(
      (f) => !BENCH_REFERENCE.test(readFileSync(join(ROOT, f), 'utf8')),
    );
    expect(silent).toEqual([]);
  });

  it('every Working screen opts in — by prop, which is how a design system is used', () => {
    const notOptedIn = WORKING_SURFACES.filter(
      (s) => !filesUnder(s).some((f) => OPT_IN.test(readFileSync(f, 'utf8'))),
    );
    expect(notOptedIn).toEqual([]);
  });

  it('and the report opts in nowhere', () => {
    // The mirror of the assertion above, on the other column of §0's table.
    const optedIn = REPORT_SURFACES.flatMap(filesUnder)
      .filter((f) => OPT_IN.test(readFileSync(f, 'utf8')))
      .map((f) => relative(ROOT, f));
    expect(optedIn).toEqual([]);
  });

  /**
   * The default is the real guarantee — see `SeriesPalette` in color.ts. A
   * chart added to the report by someone who has never read this file is
   * restrained automatically, because getting it wrong takes an explicit
   * `palette="working"` rather than an omission.
   *
   * Asserted as BEHAVIOUR in render.test.tsx ("defaults to the restrained
   * palette"), which renders the chart and reads the colours it actually
   * emitted. A regex over the source would pass on a component that had been
   * rewritten to ignore the prop.
   */
  it('has a working accent layer for the guard to be about', () => {
    const color = readFileSync(join(ROOT, 'packages/design-system/src/tokens/color.ts'), 'utf8');
    expect(color).toContain('BENCH_ACCENTS');
  });
});

/**
 * OFF-SCALE UTILITIES — added in Epic 9.24, after two landed in one afternoon.
 *
 * The Tailwind preset REPLACES the spacing scale rather than extending it, so a
 * class like `w-40` or `h-2.5` does not exist and compiles to NOTHING. That is
 * the intended defence — ip-safety.md #2 forbids ad hoc Tailwind defaults — but
 * it fails silently: the element simply has no width, and every test still
 * passes because jsdom applies no stylesheet and a static render has no box.
 *
 * Two were found by eye in a live browser during this epic:
 *   * `h-2.5 w-2.5` on a new legend swatch, which therefore rendered at zero
 *     size and made the colour key invisible.
 *   * `w-40` on the Technical screen's VerdictBar, dead since Epic 9.22.
 *
 * The second is the reason this test exists rather than a code review note: it
 * had been shipped, reviewed and screenshotted, and nobody saw it for two
 * epics. A grep is better at this than a person.
 */
describe('spacing utilities exist in the preset that replaced Tailwind’s scale', () => {
  const SCALE = new Set([
    '0', 'px', '0.5', '1', '1.5', '2', '3', '4', '5', '6', '8', '10', '14',
    '18', '24', '32', 'rhythm', 'beat',
  ]);

  /** Utilities whose numeric suffix is resolved from `theme.spacing`. */
  const PREFIXES =
    'p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y|w|h|min-w|min-h|size|inset|top|right|bottom|left';

  // Width and height also accept fractions, `full`, `auto`, `screen`, `min`,
  // `max`, `fit` and arbitrary `[...]` values. Only NUMERIC suffixes are
  // resolved from the spacing scale, so only those are checked.
  const CLASS = new RegExp(`\\b(?:${PREFIXES})-(\\d+(?:\\.\\d+)?)\\b`, 'g');

  const SURFACES = [
    'apps/web/src',
    'packages/design-system/src/components',
    'packages/design-system/src/styleguide',
  ];

  for (const surface of SURFACES) {
    it(`${surface} uses no off-scale spacing utility`, () => {
      const offenders: string[] = [];
      for (const file of filesUnder(surface)) {
        const source = readFileSync(file, 'utf8');
        for (const [full, value] of source.matchAll(CLASS)) {
          // Only look inside className strings; `grid-cols-2` and friends are
          // not spacing and their own scales are untouched by the preset.
          if (!SCALE.has(value!)) offenders.push(`${relative(ROOT, file)}: ${full}`);
        }
      }
      expect([...new Set(offenders)]).toEqual([]);
    });
  }
});

/**
 * OFF-PALETTE COLOUR UTILITIES — Epic E, the same failure one axis over.
 *
 * The spacing guard above was written after two dead `w-*` classes shipped. A
 * dead COLOUR class fails identically and was not covered: `text-semantic-danger`
 * was written on the Alerts screen's failure message, compiled to nothing, and
 * left an error message rendering in body ink — visible, but not marked as an
 * error, which is the one thing it had to be.
 *
 * The preset flattens the semantics to top level (`danger`, not
 * `semantic.danger`), so the `semantic-` prefix is exactly the mistake a
 * developer reading `tokens/color.ts` makes: the TS export is `semantic.danger`
 * and the utility is `text-danger`.
 */
describe('colour utilities exist in the preset', () => {
  /** Prefixes whose suffix is resolved from `theme.colors`. */
  const COLOUR_PREFIXES = 'text|bg|border|fill|stroke|ring|outline|decoration';
  // The mistake this catches: reaching for the TypeScript export's shape.
  const NESTED = new RegExp(`\\b(?:${COLOUR_PREFIXES})-semantic-[a-z]+\\b`, 'g');

  const SURFACES = [
    'apps/web/src',
    'packages/design-system/src/components',
    'packages/design-system/src/styleguide',
  ];

  for (const surface of SURFACES) {
    it(`${surface} names no colour the preset does not expose`, () => {
      const offenders: string[] = [];
      for (const file of filesUnder(surface)) {
        for (const [full] of readFileSync(file, 'utf8').matchAll(NESTED)) {
          offenders.push(`${relative(ROOT, file)}: ${full}`);
        }
      }
      expect([...new Set(offenders)]).toEqual([]);
    });
  }

  /**
   * The `-semantic-` check above catches ONE wrong prefix. It does not check
   * that a suffix resolves, and Epic F wrote two classes that prove the gap:
   * `border-line-subtle` (the group has `hairline` and `strong`, never
   * `subtle`) and `rounded-card` (the radii are `sm|md|lg|xl|full|none`).
   *
   * Both compiled to nothing. `border-line-subtle` left a row separator that
   * simply was not drawn, on a screen whose rows are only distinguishable by
   * it — the same silent-no-op failure as `h-2.5` and `text-semantic-danger`,
   * on the third axis in a row. So this resolves the suffix instead of
   * pattern-matching one known mistake.
   *
   * Scoped to the NESTED colour groups and to `rounded-`, because those have
   * closed key sets. `text-` and `bg-` at top level are deliberately not
   * checked here: `text-` is shared with the font-size scale (`text-ui-base`),
   * so a general check there produces false positives rather than findings.
   */
  const GROUPS: Record<string, readonly string[]> = {
    line: ['hairline', 'strong'],
    surface: ['ground', 'sunken', 'seated'],
    text: ['primary', 'body', 'secondary', 'tertiary'],
  };
  const RADII = ['none', 'sm', 'md', 'lg', 'xl', 'full'];

  const GROUPED = new RegExp(
    `\\b(?:${COLOUR_PREFIXES})-(${Object.keys(GROUPS).join('|')})-([a-z][a-z0-9]*)\\b`,
    'g',
  );
  const ROUNDED = /\brounded(?:-[trbl][lr]?)?-([a-z][a-z0-9]*)\b/g;

  for (const surface of SURFACES) {
    it(`${surface} resolves every grouped colour and radius suffix`, () => {
      const offenders: string[] = [];
      for (const file of filesUnder(surface)) {
        const source = readFileSync(file, 'utf8');
        for (const [full, group, key] of source.matchAll(GROUPED)) {
          if (!GROUPS[group!]!.includes(key!)) {
            offenders.push(`${relative(ROOT, file)}: ${full}`);
          }
        }
        for (const [full, key] of source.matchAll(ROUNDED)) {
          if (!RADII.includes(key!)) offenders.push(`${relative(ROOT, file)}: ${full}`);
        }
      }
      expect([...new Set(offenders)]).toEqual([]);
    });
  }
});

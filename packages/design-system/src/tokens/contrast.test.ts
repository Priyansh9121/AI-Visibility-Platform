import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  composite,
  contrastRatio,
  parseBlock,
  parseOklch,
  resolveVars,
  scopeOf,
  splitTopLevel,
  type Colour,
  type Rgb,
} from './contrast';

/**
 * WCAG contrast measured on the real tokens, not on numbers in a comment.
 * Values are parsed out of tokens.css; var() references resolve inside the file.
 */
const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../styles/tokens.css'), 'utf8');

const root = parseBlock(css, ':root');
const dark = parseBlock(css, "[data-theme='dark']");
const report = parseBlock(css, '.avp-report');

const light = scopeOf(root);
const darkScope = scopeOf(root, dark);
const reportScope = scopeOf(root, report);
// The report inside a dark app: the paper block over the dark one, which is what
// `.avp-report` redeclaring every surface is for.
const reportInDarkApp = scopeOf(root, dark, report);

function colour(scope: Map<string, string>, name: string) {
  const raw = scope.get(name);
  if (raw === undefined) throw new Error(`missing ${name}`);
  return parseOklch(resolveVars(raw, scope));
}

function surface(scope: Map<string, string>, name: string): Rgb {
  const c = colour(scope, name);
  expect(c.alpha, `${name} must be opaque`).toBe(1);
  return c.rgb;
}

/** The focus ring's first shadow (the 2px ring), composited at its alpha over the surface. */
function focusRingRatio(scope: Map<string, string>, surfaceName: string): number {
  const ring = resolveVars(scope.get('--avp-focus-ring') ?? '', scope);
  const first = splitTopLevel(ring)[0] ?? '';
  const m = /^0 0 0 2px (oklch\(.+\))$/.exec(first);
  if (!m || m[1] === undefined) throw new Error(`first shadow is not a 2px ring: ${first}`);
  const bg = surface(scope, surfaceName);
  return contrastRatio(composite(parseOklch(m[1]), bg), bg);
}

function textRatio(scope: Map<string, string>, textName: string, surfaceName: string): number {
  return contrastRatio(surface(scope, textName), surface(scope, surfaceName));
}

describe('contrast helper is pinned to known measurements', () => {
  it('dark text-tertiary 0.58 on dark seated is 4.09:1', () => {
    const bg = surface(darkScope, '--avp-surface-seated');
    const fg = parseOklch('oklch(0.58 0.012 75)').rgb;
    expect(Math.abs(contrastRatio(fg, bg) - 4.09)).toBeLessThan(0.05);
  });

  it('the old light focus ring (alpha 0.6) on light seated is 1.66:1', () => {
    const bg = surface(light, '--avp-surface-seated');
    const ring = composite(parseOklch('oklch(0.72 0.11 200 / 0.6)'), bg);
    expect(Math.abs(contrastRatio(ring, bg) - 1.66)).toBeLessThan(0.05);
  });
});

/*
 * Deliberately not covered, because no text sits on them: surface-void (meter
 * tracks, verdict dots and chart fills only, in components.css and apps/web)
 * and surface-desk (the report frame's backdrop).
 */
describe('text-tertiary is at least 4.5:1 on every surface text sits on (WCAG 1.4.3)', () => {
  const surfaces = ['--avp-surface-seated', '--avp-surface-raised', '--avp-surface-ground', '--avp-surface-sunken'];

  for (const s of surfaces) {
    it(`light ${s}`, () => {
      expect(textRatio(light, '--avp-text-tertiary', s)).toBeGreaterThanOrEqual(4.5);
    });
    it(`dark ${s}`, () => {
      expect(textRatio(darkScope, '--avp-text-tertiary', s)).toBeGreaterThanOrEqual(4.5);
    });
  }

  // Table rows, gap-grid rows and nav items sit under the hover wash.
  const hovered = (scope: Map<string, string>, s: string): Rgb => {
    const bg = surface(scope, s);
    return composite(colour(scope, '--avp-surface-hover'), bg);
  };
  for (const s of surfaces) {
    it(`light ${s} under the hover wash`, () => {
      expect(contrastRatio(surface(light, '--avp-text-tertiary'), hovered(light, s))).toBeGreaterThanOrEqual(4.5);
    });
    it(`dark ${s} under the hover wash`, () => {
      expect(contrastRatio(surface(darkScope, '--avp-text-tertiary'), hovered(darkScope, s))).toBeGreaterThanOrEqual(
        4.5,
      );
    });
  }
});

describe('focus ring is at least 3:1 against its surface (WCAG 1.4.11)', () => {
  const surfaces = ['--avp-surface-seated', '--avp-surface-raised', '--avp-surface-ground', '--avp-surface-sunken'];

  for (const s of surfaces) {
    it(`light ${s}`, () => {
      expect(focusRingRatio(light, s)).toBeGreaterThanOrEqual(3);
    });
    it(`dark ${s}`, () => {
      expect(focusRingRatio(darkScope, s)).toBeGreaterThanOrEqual(3);
    });
  }

  for (const s of ['--avp-surface-ground', '--avp-surface-sunken', '--avp-surface-seated']) {
    it(`report paper ${s}`, () => {
      expect(focusRingRatio(reportScope, s)).toBeGreaterThanOrEqual(3);
    });
  }
});

/*
 * A FILLED BUTTON'S RING — checklist 3.33.
 *
 * The plain ring is beacon-600, which is also where the primary button's accent
 * gradient ends, so on a primary the ring's inner edge was lost against the fill
 * (1.0:1; 1.13:1 against the report's paper fill). The danger fill is the same
 * lightness. A filled button therefore takes `--avp-focus-ring-filled`: a 2px gap
 * in the colour of the surface it sits on, then the 2px ring, then the halo. Two
 * blocks spell it: the light theme (the card) and the report (its paper page, in
 * an app of either theme). The dark theme aliases the plain ring and does not move.
 *
 * Read from the real files, as everything above is: tokens.css for the colours,
 * components.css and base.css for the rules that apply them. The report's paper
 * beacon-600 (chroma 0.125) is outside sRGB, which this helper clips where a
 * browser maps it; the margins below are wide enough that neither reading matters.
 */
const stylesDir = join(dirname(fileURLToPath(import.meta.url)), '../styles');
const componentsCss = readFileSync(join(stylesDir, 'components.css'), 'utf8');
const baseCss = readFileSync(join(stylesDir, 'base.css'), 'utf8');
// The app's own entry stylesheet: it says which of this sheet and Tailwind's preflight comes first.
const globalsCss = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../../../../apps/web/src/app/globals.css'),
  'utf8',
);

interface Layer {
  spread: number;
  css: string;
  colour: Colour;
}

/** A token's shadows, each `0 0 0 <n>px <oklch>`, innermost first. */
function shadowLayers(scope: Map<string, string>, token: string): Layer[] {
  const value = resolveVars(scope.get(token) ?? '', scope);
  return splitTopLevel(value).map((layer) => {
    const m = /^0 0 0 (\d+)px (oklch\(.+\))$/.exec(layer);
    if (!m || m[2] === undefined) throw new Error(`${token}: not a spread shadow: ${layer}`);
    return { spread: Number(m[1]), css: m[2], colour: parseOklch(m[2]) };
  });
}

const FILLS = [
  'primary base (beacon-600)',
  'primary gradient start',
  'primary gradient end',
  'danger',
] as const;

/** What a filled button is painted with: the primary's base colour, both ends of its gradient, the danger fill. */
function fillOf(scope: Map<string, string>, name: (typeof FILLS)[number]): Rgb {
  if (name === 'danger') return surface(scope, '--avp-danger');
  if (name === 'primary base (beacon-600)') return surface(scope, '--avp-beacon-600');
  const gradient = resolveVars(scope.get('--avp-gradient-accent') ?? '', scope);
  const stops = [...gradient.matchAll(/oklch\([^)]*\)/g)].map((m) => parseOklch(m[0]).rgb);
  const stop = stops[name === 'primary gradient start' ? 0 : stops.length - 1];
  if (stops.length !== 2 || stop === undefined) throw new Error(`the accent gradient is not two stops: ${gradient}`);
  return stop;
}

/** Where a filled button's ring is spelled, and the surfaces its outside edge can end on. */
const FILLED_SCOPES: readonly { where: string; scope: Map<string, string>; surfaces: string[] }[] = [
  {
    where: 'light theme',
    scope: light,
    surfaces: ['--avp-surface-seated', '--avp-surface-raised', '--avp-surface-ground', '--avp-surface-sunken'],
  },
  {
    where: 'report',
    scope: reportScope,
    surfaces: ['--avp-surface-ground', '--avp-surface-sunken', '--avp-surface-seated'],
  },
  {
    where: 'report inside a dark app',
    scope: reportInDarkApp,
    surfaces: ['--avp-surface-ground', '--avp-surface-sunken', '--avp-surface-seated'],
  },
];

for (const { where, scope, surfaces } of FILLED_SCOPES) {
  describe(`a filled button keeps its focus ring off its fill, ${where} (WCAG 1.4.11)`, () => {
    it('is a 2px gap, a 2px ring, then the halo out to 8px', () => {
      expect(shadowLayers(scope, '--avp-focus-ring-filled').map((l) => l.spread)).toEqual([2, 4, 8]);
    });

    it("keeps --avp-focus-ring's own ring and halo, unchanged, outside the gap", () => {
      const plain = shadowLayers(scope, '--avp-focus-ring');
      const filled = shadowLayers(scope, '--avp-focus-ring-filled');
      expect(filled[1]?.css).toBe(plain[0]?.css);
      expect(filled[2]?.css).toBe(plain[1]?.css);
    });

    it('paints the gap in an opaque surface token, so it follows the surface', () => {
      expect(scope.get('--avp-focus-ring-filled')).toMatch(
        /^0 0 0 2px var\(--avp-surface-(seated|raised|ground|sunken)\),/,
      );
      expect(shadowLayers(scope, '--avp-focus-ring-filled')[0]?.colour.alpha).toBe(1);
    });

    it('is needed: the plain ring is lost against every one of these fills', () => {
      // 1.00 to 1.04 in the light theme and 1.13 on the report's paper fill: far under 3:1.
      const plain = shadowLayers(scope, '--avp-focus-ring')[0]!.colour.rgb;
      for (const name of FILLS) {
        expect(contrastRatio(plain, fillOf(scope, name)), name).toBeLessThan(1.5);
      }
    });

    for (const name of FILLS) {
      it(`the gap is at least 3:1 against the ${name} fill`, () => {
        const gap = shadowLayers(scope, '--avp-focus-ring-filled')[0]!.colour.rgb;
        expect(contrastRatio(gap, fillOf(scope, name))).toBeGreaterThanOrEqual(3);
      });
    }

    it('the ring is at least 3:1 against the gap it sits on', () => {
      const [gap, ring] = shadowLayers(scope, '--avp-focus-ring-filled');
      expect(ring!.colour.alpha).toBe(1);
      expect(contrastRatio(ring!.colour.rgb, gap!.colour.rgb)).toBeGreaterThanOrEqual(3);
    });

    for (const s of surfaces) {
      it(`the ring is at least 3:1 against ${s} outside it`, () => {
        const ring = shadowLayers(scope, '--avp-focus-ring-filled')[1]!.colour;
        const bg = surface(scope, s);
        expect(contrastRatio(composite(ring, bg), bg)).toBeGreaterThanOrEqual(3);
      });
    }

    it('still clears 3:1 while hovered, when the whole button, shadows included, brightens', () => {
      // components.css: `filter: brightness(1.08)` on a hovered primary or danger button
      // lifts the fill and its box-shadow together. Multiplied in sRGB, the larger change.
      const lift = (c: Rgb): Rgb => [Math.min(1, c[0] * 1.08), Math.min(1, c[1] * 1.08), Math.min(1, c[2] * 1.08)];
      const [gap, ring] = shadowLayers(scope, '--avp-focus-ring-filled');
      for (const name of FILLS) {
        expect(contrastRatio(lift(gap!.colour.rgb), lift(fillOf(scope, name))), name).toBeGreaterThanOrEqual(3);
      }
      expect(contrastRatio(lift(gap!.colour.rgb), lift(ring!.colour.rgb))).toBeGreaterThanOrEqual(3);
    });
  });
}

describe('which scopes spell the filled ring, and what a report inside a dark app gets', () => {
  const resolved = (scope: Map<string, string>) => resolveVars(scope.get('--avp-focus-ring-filled') ?? '', scope);

  it('the dark theme keeps the plain ring, so nothing there moves', () => {
    expect(resolved(darkScope)).not.toBe('');
    expect(resolved(darkScope)).toBe(resolveVars(darkScope.get('--avp-focus-ring') ?? '', darkScope));
  });

  it('the report block declares its own three-layer ring: not the plain ring, not a :root value read through', () => {
    // A token only `:root` declares is resolved against the LIGHT surfaces and inherited as
    // that computed value, so the report must declare its own against its paper ones.
    const own = report.get('--avp-focus-ring-filled');
    expect(own).toBeDefined();
    expect(own).not.toMatch(/^var\(--avp-focus-ring\)$/);
    expect(splitTopLevel(own ?? '')).toHaveLength(3);
  });

  it('a report inside a dark app resolves to the paper version, not the dark theme ring', () => {
    expect(resolved(reportInDarkApp)).toBe(resolved(reportScope));
    expect(resolved(reportInDarkApp)).not.toBe(resolved(darkScope));
    expect(shadowLayers(reportInDarkApp, '--avp-focus-ring-filled').map((l) => l.spread)).toEqual([2, 4, 8]);
  });

  it('the report gap is the colour of the page its buttons sit on, so it reads as air', () => {
    // `.avp-report` paints `--avp-surface-ground`, and neither a beat nor the competitor
    // editor paints a surface of its own over it: both primaries sit straight on that page.
    const page = rulesOf(componentsCss).find((r) => r.selector === '.avp-report')?.body ?? '';
    expect(page).toMatch(/background:\s*var\(--avp-surface-ground\)/);
    for (const scope of [reportScope, reportInDarkApp]) {
      const gap = shadowLayers(scope, '--avp-focus-ring-filled')[0]!.colour.rgb;
      expect(contrastRatio(gap, surface(scope, '--avp-surface-ground'))).toBe(1);
    }
  });
});

/** CSS specificity of one complex selector: [ids, classes + attributes + pseudo-classes, types + pseudo-elements]. */
type Spec = [number, number, number];
const bySpecificity = (a: Spec, b: Spec): number => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

function specificity(selector: string): Spec {
  let rest = selector.trim();
  const total: Spec = [0, 0, 0];
  // :where() counts for nothing; :not(), :is() and :has() count as their most specific argument.
  for (let m = /:(where|not|is|has)\(/.exec(rest); m !== null; m = /:(where|not|is|has)\(/.exec(rest)) {
    const open = m.index + m[0].length - 1;
    let depth = 0;
    let close = open;
    for (; close < rest.length; close += 1) {
      if (rest[close] === '(') depth += 1;
      else if (rest[close] === ')') {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    if (m[1] !== 'where') {
      const top = splitTopLevel(rest.slice(open + 1, close))
        .map(specificity)
        .reduce((a, b) => (bySpecificity(a, b) >= 0 ? a : b), [0, 0, 0] as Spec);
      total[0] += top[0];
      total[1] += top[1];
      total[2] += top[2];
    }
    rest = `${rest.slice(0, m.index)} ${rest.slice(close + 1)}`;
  }
  const count = (re: RegExp): number => {
    const found = rest.match(re)?.length ?? 0;
    rest = rest.replace(re, ' ');
    return found;
  };
  total[0] += count(/#[\w-]+/g);
  total[2] += count(/::[\w-]+(\([^)]*\))?/g);
  total[1] += count(/\[[^\]]*\]/g);
  total[1] += count(/\.[\w-]+/g);
  total[1] += count(/:[\w-]+(\([^)]*\))?/g);
  total[2] += count(/(^|[\s>+~])[a-zA-Z][\w-]*/g);
  return total;
}

/** Every `selector { body }` in a stylesheet, at-rule bodies flattened and comments dropped. */
function rulesOf(sheet: string): { selector: string; body: string }[] {
  const bare = sheet.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...bare.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
    selector: (m[1] ?? '').trim(),
    body: (m[2] ?? '').trim(),
  }));
}

describe('the specificity helper the cascade check below relies on', () => {
  it.each<[string, Spec]>([
    ['.a', [0, 1, 0]],
    ['.a:focus-visible', [0, 2, 0]],
    [':where(a, button, [tabindex]):focus-visible', [0, 1, 0]],
    [':-moz-focusring', [0, 1, 0]],
    ['.avp-btn:active:not(:disabled)', [0, 3, 0]],
    ['a.b > button', [0, 1, 2]],
    ['#x .y[data-z]', [1, 2, 0]],
    ['.a::before', [0, 1, 1]],
    [':is(.a, #b) .c', [1, 1, 0]],
  ])('%s is %j', (selector, expected) => {
    expect(specificity(selector)).toEqual(expected);
  });
});

describe('the ring a filled button takes is applied by a rule that beats the global one', () => {
  const FILLED_SELECTORS = ['.avp-btn--danger:focus-visible', '.avp-btn--primary:focus-visible'];
  const filledRules = () =>
    rulesOf(componentsCss).filter((r) => /\.avp-btn--(primary|danger):focus-visible/.test(r.selector));
  const globalRule = () => {
    const found = rulesOf(baseCss).filter((r) => /box-shadow:\s*var\(--avp-focus-ring\)/.test(r.body));
    expect(found, 'base.css has one global focus rule').toHaveLength(1);
    return found[0]!;
  };

  it('is one rule for a focused primary and a focused danger button, spelled in the token', () => {
    const rules = filledRules();
    expect(rules).toHaveLength(1);
    expect(splitTopLevel(rules[0]!.selector).sort()).toEqual(FILLED_SELECTORS);
    expect(rules[0]!.body).toMatch(/(^|;|\s)box-shadow:\s*var\(--avp-focus-ring-filled\)\s*;/);
  });

  it('carries a transparent 2px outline: Firefox cannot draw its own ring inside the gap, and forced colours keeps an indicator', () => {
    const rules = filledRules();
    expect(rules).toHaveLength(1);
    expect(rules[0]!.body).toMatch(/(^|;|\s)outline:\s*2px solid transparent\s*;?(\s|$)/);
  });

  it("is more specific than Tailwind preflight's :-moz-focusring { outline: auto }, which loads after this sheet and beats base.css's outline: none", () => {
    // `apps/web/src/app/globals.css` imports this stylesheet and then `@tailwind base`; preflight
    // (tailwindcss 3.4, src/css/preflight.css) says `:-moz-focusring { outline: auto }`. On equal
    // specificity the later rule wins, so in Firefox it beats base.css's `outline: none` and draws
    // the native ring, which on a filled button lands inside the gap. This rule must out-rank it.
    const designSystem = globalsCss.indexOf("@import '@avp/design-system/styles.css'");
    expect(designSystem, 'globals.css imports the design-system stylesheet').toBeGreaterThanOrEqual(0);
    expect(globalsCss.indexOf('@tailwind base'), 'and then preflight').toBeGreaterThan(designSystem);

    const preflight = specificity(':-moz-focusring');
    expect(bySpecificity(specificity(globalRule().selector), preflight), 'the global rule only ties it').toBe(0);
    const selectors = filledRules().flatMap((r) => splitTopLevel(r.selector));
    expect(selectors.length).toBeGreaterThan(0);
    for (const selector of selectors) {
      expect(bySpecificity(specificity(selector), preflight), selector).toBeGreaterThan(0);
    }
  });

  it('is measured against what the buttons are really painted with', () => {
    const body = (selector: string) => rulesOf(componentsCss).find((r) => r.selector === selector)?.body ?? '';
    expect(body('.avp-btn--primary')).toContain('var(--avp-beacon-600)');
    expect(body('.avp-btn--primary')).toContain('var(--avp-gradient-accent)');
    expect(body('.avp-btn--danger')).toContain('var(--avp-danger)');
  });

  it('the global rule is :where(...):focus-visible, and :where() leaves it the one pseudo-class', () => {
    // Not zero: `:where()` contributes nothing, but `:focus-visible` after it is a pseudo-class.
    expect(globalRule().selector).toMatch(/^:where\([^)]*\):focus-visible$/);
    expect(specificity(globalRule().selector)).toEqual([0, 1, 0]);
  });

  it('is strictly more specific than the global rule, so file order cannot decide it', () => {
    const global = specificity(globalRule().selector);
    // The selectors the stylesheet really has, not the list above: this must fail on its own.
    const selectors = filledRules().flatMap((r) => splitTopLevel(r.selector));
    expect(selectors.length).toBeGreaterThan(0);
    for (const selector of selectors) {
      expect(bySpecificity(specificity(selector), global), selector).toBeGreaterThan(0);
    }
  });

  it('is the only rule on a button that sets a box-shadow, so nothing else can override it', () => {
    const shadowed = rulesOf(componentsCss).filter(
      (r) => /\.avp-btn\b/.test(r.selector) && /(^|;|\s)box-shadow\s*:/.test(r.body),
    );
    expect(shadowed.map((r) => splitTopLevel(r.selector).sort())).toEqual([FILLED_SELECTORS]);
  });
});

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
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

/*
 * THE SIGN-OUT SENTENCE IS `text-danger` ON THE SIDEBAR — copy fixes, 1e.
 *
 * The sidebar's footer sits on `--avp-surface-sunken`, and a failed sign-out puts the founder's
 * sentence there in the error colour. Measured on these tokens it is 4.61:1 in the light theme (0.11
 * above AA, so a small change to the danger token or to the sunken surface could take it under
 * without a single other test noticing) and 6.83:1 in the dark. Only this pair is pinned: the other
 * surfaces the error colour is used on are cards and the page, and are not this change's.
 */
describe('text-danger is at least 4.5:1 on the sidebar surface (WCAG 1.4.3)', () => {
  it('light --avp-surface-sunken', () => {
    expect(textRatio(light, '--avp-danger', '--avp-surface-sunken')).toBeGreaterThanOrEqual(4.5);
  });
  it('dark --avp-surface-sunken', () => {
    expect(textRatio(darkScope, '--avp-danger', '--avp-surface-sunken')).toBeGreaterThanOrEqual(4.5);
  });
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
 * in the colour of the surface it sits on, then the 2px ring, then the halo. Three
 * blocks spell it: the light theme (the card), the dark theme (its card too, since
 * checklist 3.40: its translucent plain ring was 1.6 to 2.0:1 against the primary
 * fill) and the report (its paper page, in an app of either theme).
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
// Tailwind's own preflight, from the copy the app installs: the rules that load after this sheet.
const webDir = join(dirname(fileURLToPath(import.meta.url)), '../../../../apps/web');
const tailwindDir = dirname(createRequire(join(webDir, 'package.json')).resolve('tailwindcss/package.json'));
const preflightCss = readFileSync(join(tailwindDir, 'src/css/preflight.css'), 'utf8');

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

/**
 * The ring a person sees on `surfaceName`: the ring layer over the halo layer over the surface, in
 * the order a box-shadow paints them. A solid ring hides what is under it, so on the light theme and
 * the report it is the ring's own colour on every surface; the dark theme's is translucent (70%), and
 * what is seen is its blend with the halo and the surface.
 */
function paintedRing(
  scope: Map<string, string>,
  token: '--avp-focus-ring' | '--avp-focus-ring-filled',
  surfaceName: string,
): Rgb {
  const layers = shadowLayers(scope, token);
  const [ring, halo] = token === '--avp-focus-ring' ? [layers[0], layers[1]] : [layers[1], layers[2]];
  return composite(ring!.colour, composite(halo!.colour, surface(scope, surfaceName)));
}

/**
 * Where a filled button's ring is spelled, and the surfaces its outside edge can end on. `solid` is
 * whether the ring layer is opaque (the light theme's and the report's are; the dark theme's is its
 * plain ring's own 70%), and `plainLostBelow` is how far under 3:1 the plain ring, as painted, is
 * against every fill: 1.00 to 1.04 in the light theme, 1.13 on the report's paper fill, and in the
 * dark theme 1.01 to 2.01 (the primary's two ends and the danger fill; checklist 3.40).
 */
const FILLED_SCOPES: readonly {
  where: string;
  scope: Map<string, string>;
  surfaces: string[];
  solid: boolean;
  plainLostBelow: number;
}[] = [
  {
    where: 'light theme',
    scope: light,
    surfaces: ['--avp-surface-seated', '--avp-surface-raised', '--avp-surface-ground', '--avp-surface-sunken'],
    solid: true,
    plainLostBelow: 1.5,
  },
  {
    where: 'dark theme',
    scope: darkScope,
    surfaces: ['--avp-surface-seated', '--avp-surface-raised', '--avp-surface-ground', '--avp-surface-sunken'],
    solid: false,
    plainLostBelow: 3,
  },
  {
    where: 'report',
    scope: reportScope,
    surfaces: ['--avp-surface-ground', '--avp-surface-sunken', '--avp-surface-seated'],
    solid: true,
    plainLostBelow: 1.5,
  },
  {
    where: 'report inside a dark app',
    scope: reportInDarkApp,
    surfaces: ['--avp-surface-ground', '--avp-surface-sunken', '--avp-surface-seated'],
    solid: true,
    plainLostBelow: 1.5,
  },
];

for (const { where, scope, surfaces, solid, plainLostBelow } of FILLED_SCOPES) {
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

    it('is needed: the plain ring, as painted, is lost against every one of these fills', () => {
      // 1.00 to 1.04 in the light theme and 1.13 on the report's paper fill; 1.01 to 2.01 in the dark theme.
      for (const s of surfaces) {
        const plain = paintedRing(scope, '--avp-focus-ring', s);
        for (const name of FILLS) {
          expect(contrastRatio(plain, fillOf(scope, name)), `${name} on ${s}`).toBeLessThan(plainLostBelow);
        }
      }
    });

    for (const name of FILLS) {
      it(`the gap is at least 3:1 against the ${name} fill`, () => {
        const gap = shadowLayers(scope, '--avp-focus-ring-filled')[0]!.colour.rgb;
        expect(contrastRatio(gap, fillOf(scope, name))).toBeGreaterThanOrEqual(3);
      });
    }

    it('the ring, as painted, is at least 3:1 against the gap it sits on', () => {
      const [gap, ring] = shadowLayers(scope, '--avp-focus-ring-filled');
      // A solid ring does not depend on what is under it; the dark theme's does, so it is measured on every surface.
      if (solid) expect(ring!.colour.alpha).toBe(1);
      for (const s of surfaces) {
        const painted = paintedRing(scope, '--avp-focus-ring-filled', s);
        expect(contrastRatio(painted, gap!.colour.rgb), s).toBeGreaterThanOrEqual(3);
      }
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
      const [gap] = shadowLayers(scope, '--avp-focus-ring-filled');
      for (const name of FILLS) {
        expect(contrastRatio(lift(gap!.colour.rgb), lift(fillOf(scope, name))), name).toBeGreaterThanOrEqual(3);
      }
      // The ring as painted, lifted whole: a close reading for the dark theme's translucent ring, whose
      // margin (5.6:1 and up) is wide enough that how a filter blends it does not matter.
      for (const s of surfaces) {
        const painted = lift(paintedRing(scope, '--avp-focus-ring-filled', s));
        expect(contrastRatio(lift(gap!.colour.rgb), painted), s).toBeGreaterThanOrEqual(3);
      }
    });
  });
}

describe('which scopes spell the filled ring, and what a report inside a dark app gets', () => {
  const resolved = (scope: Map<string, string>) => resolveVars(scope.get('--avp-focus-ring-filled') ?? '', scope);

  it('the dark theme spells its own gap-then-ring: three layers, not an alias of the plain ring (checklist 3.40)', () => {
    // It was `var(--avp-focus-ring)`: against the primary fill that ring is 1.6 to 2.0:1, against the danger fill 1.0.
    const own = dark.get('--avp-focus-ring-filled');
    expect(own).toBeDefined();
    expect(own).not.toMatch(/^var\(--avp-focus-ring\)$/);
    expect(splitTopLevel(own ?? '')).toHaveLength(3);
    expect(resolved(darkScope)).not.toBe(resolveVars(darkScope.get('--avp-focus-ring') ?? '', darkScope));
  });

  it("the dark theme's gap is the dark card colour, so it reads as air on a card and a faint band elsewhere", () => {
    // The same choice as the light theme's, for the same reason: cards hold the forms and their actions.
    expect(dark.get('--avp-focus-ring-filled')).toMatch(/^0 0 0 2px var\(--avp-surface-seated\),/);
    const gap = shadowLayers(darkScope, '--avp-focus-ring-filled')[0]!.colour.rgb;
    expect(contrastRatio(gap, surface(darkScope, '--avp-surface-seated'))).toBe(1);
    for (const s of ['--avp-surface-sunken', '--avp-surface-ground', '--avp-surface-raised']) {
      expect(contrastRatio(gap, surface(darkScope, s)), s).toBeLessThan(1.25);
    }
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
    ['html :where(a, button, [tabindex]):focus-visible', [0, 1, 1]],
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

  it("is more specific than Tailwind preflight's :-moz-focusring { outline: auto }, which loads after this sheet and wins a tie on file order", () => {
    // `apps/web/src/app/globals.css` imports this stylesheet and then `@tailwind base`; preflight
    // (tailwindcss 3.4, src/css/preflight.css) says `:-moz-focusring { outline: auto }`. On equal
    // specificity the later rule wins, so in Firefox it would beat a (0,1,0) rule here (the ring's
    // rule, which only ties it) and draw the native ring, which on a filled button lands inside the
    // gap. This rule must out-rank it.
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

/*
 * THE GLOBAL OUTLINE — checklist 3.40.
 *
 * base.css drew the focus ring with a box-shadow and said `outline: none`. Forced colours drops
 * every shadow, so a control with no outline of its own showed no focus there; and in Firefox
 * Tailwind's preflight (`:-moz-focusring { outline: auto }`, loaded after this sheet, at the same
 * (0,1,0) as that rule) put Firefox's own ring back on every one of them, inside the shadow. A
 * transparent 2px outline is nothing on a normal screen, is painted in a system colour in forced
 * colours, and, from a rule that outranks preflight, leaves Firefox no ring of its own.
 *
 * It is a rule of its own and not a line in the ring's: that rule has to stay at (0,1,0), so a
 * component's own radius (`.avp-btn`'s, the field's) still beats its `border-radius` on file
 * order. A rule that outranked preflight would shrink every button's corners on focus.
 */
describe('every focusable control keeps a transparent outline, which forced colours paints and Firefox cannot replace (checklist 3.40)', () => {
  const bare = (sheet: string) => sheet.replace(/\/\*[\s\S]*?\*\//g, '');
  const OUTLINE = /(^|;|\s)outline:\s*2px solid transparent\s*;?(\s|$)/;
  const ringRule = () => {
    const found = rulesOf(baseCss).filter((r) => /box-shadow:\s*var\(--avp-focus-ring\)/.test(r.body));
    expect(found, 'base.css has one rule that draws the ring').toHaveLength(1);
    return found[0]!;
  };
  const outlineRule = () => {
    const found = rulesOf(baseCss).filter((r) => OUTLINE.test(r.body));
    expect(found, 'base.css has one rule that sets the transparent outline').toHaveLength(1);
    return found[0]!;
  };
  /** The list of elements a `:where(...)` or `:is(...)` names. */
  const elementsOf = (selector: string) => /:(?:where|is)\(([^)]*)\)/.exec(selector)?.[1];

  it('base.css says outline: none nowhere, so no rule in it can leave a control without one', () => {
    expect(bare(baseCss)).not.toMatch(/outline\s*:\s*none/);
  });

  it('is one rule, for the same elements and the same state as the ring, that sets the outline and neither the ring nor the radius', () => {
    const rule = outlineRule();
    expect(rule.selector).toMatch(/:focus-visible$/);
    expect(elementsOf(rule.selector)).toBeDefined();
    expect(elementsOf(rule.selector)).toBe(elementsOf(ringRule().selector));
    expect(rule.body).not.toMatch(/box-shadow|border-radius/);
  });

  it("outranks Tailwind preflight's :-moz-focusring { outline: auto }, which loads after this sheet and which the ring's own rule only ties", () => {
    const designSystem = globalsCss.indexOf("@import '@avp/design-system/styles.css'");
    expect(designSystem, 'globals.css imports the design-system stylesheet').toBeGreaterThanOrEqual(0);
    expect(globalsCss.indexOf('@tailwind base'), 'and then preflight').toBeGreaterThan(designSystem);

    // The rule in the copy of Tailwind the app installs, not a selector spelled here.
    const preflight = rulesOf(preflightCss).filter((r) => r.selector === ':-moz-focusring');
    expect(preflight, "preflight's rule").toHaveLength(1);
    expect(preflight[0]!.body).toMatch(/outline:\s*auto/);
    const theirs = specificity(preflight[0]!.selector);
    expect(bySpecificity(specificity(ringRule().selector), theirs), "the ring's rule only ties it").toBe(0);
    expect(bySpecificity(specificity(outlineRule().selector), theirs)).toBeGreaterThan(0);
  });

  it('outranks the rule that says outline: none on a field input, which would otherwise keep a focused field out of forced colours', () => {
    const none = rulesOf(componentsCss).filter(
      (r) => /(^|;|\s)outline:\s*none/.test(r.body) && /\.avp-field__input\b/.test(r.selector),
    );
    expect(none.length, 'the field input does say outline: none').toBeGreaterThan(0);
    const mine = specificity(outlineRule().selector);
    for (const r of none) {
      for (const selector of splitTopLevel(r.selector)) {
        expect(bySpecificity(mine, specificity(selector)), selector).toBeGreaterThan(0);
      }
    }
  });

  it("yields to a component's own :focus-visible outline, so a control that draws one keeps it", () => {
    const own = rulesOf(componentsCss)
      .flatMap((r) => splitTopLevel(r.selector).map((selector) => ({ selector, body: r.body })))
      .filter((r) => /:focus-visible$/.test(r.selector) && /(^|;|\s)outline\s*:/.test(r.body));
    expect(own.length, 'components draw their own focus outlines').toBeGreaterThan(0);
    const mine = specificity(outlineRule().selector);
    for (const r of own) expect(bySpecificity(specificity(r.selector), mine), r.selector).toBeGreaterThan(0);
  });

  /** Every spelling of "no outline" in a declaration block. */
  const NO_OUTLINE = [/(^|;|\s)outline(-style)?\s*:\s*none\b/, /(^|;|\s)outline(-width)?\s*:\s*0(px)?(?![\d.])/];
  /** The selectors, among those that end in `:focus-visible`, whose block takes a control's outline away. */
  const removesOutline = (sheet: string) =>
    rulesOf(sheet)
      .flatMap((r) => splitTopLevel(r.selector).map((selector) => ({ selector, body: r.body })))
      .filter((r) => /:focus-visible$/.test(r.selector) && NO_OUTLINE.some((re) => re.test(r.body)))
      .map((r) => r.selector);

  it('no component rule on :focus-visible says outline: none, so none can take a control back out of forced colours', () => {
    // `.avp-google-btn:focus-visible` did until 3.40, and the test above counted it as a component
    // drawing its own outline (it matches any `outline:`), so it would not have stopped the line returning.
    expect(removesOutline(componentsCss)).toEqual([]);
  });

  it('and that check does see such a rule, in each spelling of it, and passes one that draws or only offsets an outline', () => {
    expect(removesOutline('.g:focus-visible { outline: none; box-shadow: var(--avp-focus-ring); }')).toEqual([
      '.g:focus-visible',
    ]);
    expect(removesOutline('.g:focus-visible { outline:none }')).toEqual(['.g:focus-visible']);
    expect(removesOutline('.g:focus-visible { outline-style: none; }')).toEqual(['.g:focus-visible']);
    expect(removesOutline('.g:focus-visible { outline-width: 0px; }')).toEqual(['.g:focus-visible']);
    expect(removesOutline('.a:focus-visible, .b:focus-visible { outline: 0; }')).toEqual([
      '.a:focus-visible',
      '.b:focus-visible',
    ]);
    expect(removesOutline('.g:focus-visible { outline: 2px solid transparent; }')).toEqual([]);
    expect(removesOutline('.g:focus-visible { outline: 2px solid red; outline-offset: 2px; }')).toEqual([]);
    expect(removesOutline('.g:focus-visible { outline: 0.5px solid red; }')).toEqual([]);
    expect(removesOutline('.g:hover { outline: none; }')).toEqual([]);
  });

  it("leaves the ring's rule where it was, no more specific than a component class, so a component's own radius still wins", () => {
    const button = rulesOf(componentsCss).find((r) => r.selector === '.avp-btn');
    expect(button?.body).toMatch(/border-radius:/);
    expect(ringRule().body).toMatch(/border-radius:/);
    expect(bySpecificity(specificity(ringRule().selector), specificity('.avp-btn'))).toBeLessThanOrEqual(0);
  });
});

/*
 * A FOCUSED FIELD IS LIT ONCE — checklist 3.40.
 *
 * The ring belongs on the wrapper (`.avp-field__control:focus-within`), so the affix and the input
 * read as one control. The input is a focusable element too, so the global rule drew the ring on it
 * as well: a second ring inside the first, a text field always matching `:focus-visible`. Both
 * `TextField` and `SelectField` put the input in that wrapper under `.avp-field__input`.
 */
describe('a focused field is lit once: the wrapper keeps its ring and the input takes none of its own (checklist 3.40)', () => {
  const ruleFor = (selector: string) => rulesOf(componentsCss).find((r) => r.selector === selector);
  const ringSelector = () => {
    const found = rulesOf(baseCss).filter((r) => /box-shadow:\s*var\(--avp-focus-ring\)/.test(r.body));
    expect(found, 'base.css has one rule that draws the ring').toHaveLength(1);
    return found[0]!.selector;
  };

  it('the input takes no shadow while focused', () => {
    expect(ruleFor('.avp-field__input:focus-visible')?.body).toMatch(/(^|;|\s)box-shadow:\s*none\s*;?(\s|$)/);
  });

  it("and that rule outranks the global ring's, so file order cannot decide it", () => {
    const field = specificity('.avp-field__input:focus-visible');
    expect(bySpecificity(field, specificity(ringSelector()))).toBeGreaterThan(0);
  });

  it("the wrapper's ring stays, valid or invalid", () => {
    expect(ruleFor('.avp-field__control:focus-within')?.body).toMatch(/box-shadow:\s*var\(--avp-focus-ring\)/);
    expect(ruleFor('.avp-field__control.is-invalid:focus-within')?.body).toMatch(/box-shadow:/);
  });

  it('nothing else gives the input a shadow', () => {
    const shadowed = rulesOf(componentsCss).filter(
      (r) => /\.avp-field__(input|select)\b/.test(r.selector) && /(^|;|\s)box-shadow\s*:/.test(r.body),
    );
    expect(shadowed.map((r) => r.selector)).toEqual(['.avp-field__input:focus-visible']);
  });
});

/*
 * THE PRIMARY BUTTON'S RIM — checklist 3.40.
 *
 * `.avp-btn` has a 1px transparent border, which the background shows through (a background paints
 * to the border box). A gradient is sized to the padding box and repeats, so the border showed the
 * far end of the next tile: a 1px line round every primary button in the gradient's other colour.
 * Sized to the border box (`background-origin`) the gradient covers the whole box once.
 */
describe('the primary button is not rimmed by its own gradient (checklist 3.40)', () => {
  const body = (selector: string) => rulesOf(componentsCss).find((r) => r.selector === selector)?.body ?? '';

  it('sizes the gradient to the border box, so it does not tile into the 1px transparent border round it', () => {
    expect(body('.avp-btn')).toMatch(/(^|;|\s)border:\s*1px solid transparent\s*;/);
    expect(body('.avp-btn--primary')).toMatch(/(^|;|\s)background-origin:\s*border-box\s*;/);
  });

  it("changes neither the button's size nor its colours: the fill, the gradient and the text colour are as they were", () => {
    const primary = body('.avp-btn--primary');
    expect(primary).toMatch(/background-color:\s*var\(--avp-beacon-600\)/);
    expect(primary).toMatch(/background-image:\s*var\(--avp-gradient-accent\)/);
    expect(primary).toMatch(/(^|;|\s)color:\s*var\(--avp-on-accent\)/);
    expect(primary).not.toMatch(/(^|;|\s)(border|padding|margin|width|height|background-clip|background-repeat)\s*:/);
  });
});

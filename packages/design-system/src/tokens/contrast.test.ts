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

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  oklch,
  paper,
  ink,
  visibility,
  beacon,
  competitor,
  visibilityAt,
  visibilityColor,
  visibilityBand,
  onVisibility,
  seriesStyle,
} from './color.js';

const css = readFileSync(fileURLToPath(new URL('../styles/tokens.css', import.meta.url)), 'utf8');

/** Pull a custom property value out of the :root block. */
function cssVar(name: string): string | null {
  const m = css.match(new RegExp(`--avp-${name}:\\s*([^;]+);`));
  return m ? m[1]!.trim() : null;
}

describe('token parity: TS <-> CSS', () => {
  // Two hand-maintained copies of a palette diverge within a month. This is
  // the test that stops it.
  const groups: [string, Record<string, readonly [number, number, number]>][] = [
    ['paper', paper],
    ['ink', ink],
    ['beacon', beacon],
    ['competitor', competitor],
  ];

  for (const [prefix, group] of groups) {
    for (const [key, value] of Object.entries(group)) {
      it(`--avp-${prefix}-${key} matches the TS token`, () => {
        expect(cssVar(`${prefix}-${key}`)).toBe(oklch(value));
      });
    }
  }

  for (const [key, value] of Object.entries(visibility)) {
    it(`--avp-vis-${key} matches the TS token`, () => {
      expect(cssVar(`vis-${key}`)).toBe(oklch(value));
    });
  }
});

describe('visibility ramp invariants', () => {
  it('is strictly monotonic in lightness, so it survives greyscale print', () => {
    // The traffic-light ramp fails exactly here: red and green share a grey.
    for (let s = 1; s <= 100; s++) {
      expect(visibilityAt(s)[0]).toBeGreaterThan(visibilityAt(s - 1)[0]);
    }
  });

  it('traverses warm to cool for CVD safety', () => {
    expect(visibilityAt(0)[2]).toBeLessThan(60); // orange-red end
    expect(visibilityAt(100)[2]).toBeGreaterThan(180); // teal end
  });

  it('raises chroma with lightness so visibility is doubly encoded', () => {
    expect(visibilityAt(100)[1]).toBeGreaterThan(visibilityAt(0)[1]);
  });

  it('clamps out-of-range input to the ramp ends', () => {
    expect(visibilityColor(-50)).toBe(visibilityColor(0));
    expect(visibilityColor(150)).toBe(visibilityColor(100));
  });

  it('hits the declared stops exactly', () => {
    expect(visibilityColor(0)).toBe(oklch(visibility['00']));
    expect(visibilityColor(25)).toBe(oklch(visibility['25']));
    expect(visibilityColor(50)).toBe(oklch(visibility['50']));
    expect(visibilityColor(75)).toBe(oklch(visibility['75']));
    expect(visibilityColor(100)).toBe(oklch(visibility['100']));
  });

  it('is deterministic — the same score always yields the same colour', () => {
    // These colours land in PDFs clients compare across months.
    for (const s of [0, 13, 37.5, 62.4, 99]) {
      expect(visibilityColor(s)).toBe(visibilityColor(s));
    }
  });
});

describe('fill-only rule', () => {
  it('picks ink on light fills and paper on dark fills', () => {
    expect(onVisibility(100)).toBe(oklch(ink['900'])); // L 0.815 -> ink
    expect(onVisibility(0)).toBe(oklch(paper['000'])); // L 0.420 -> paper
  });

  it('switches at the L=0.62 threshold', () => {
    for (let s = 0; s <= 100; s++) {
      const expected = visibilityAt(s)[0] >= 0.62 ? oklch(ink['900']) : oklch(paper['000']);
      expect(onVisibility(s)).toBe(expected);
    }
  });

  it('never returns a ramp colour as a text colour', () => {
    const rampFills = Object.values(visibility).map((c) => oklch(c));
    for (let s = 0; s <= 100; s += 5) {
      expect(rampFills).not.toContain(onVisibility(s));
    }
  });
});

describe('visibilityBand', () => {
  it('maps scores to the declared bands', () => {
    expect(visibilityBand(0)).toBe('absent');
    expect(visibilityBand(19.9)).toBe('absent');
    expect(visibilityBand(20)).toBe('barely');
    expect(visibilityBand(40)).toBe('emerging');
    expect(visibilityBand(60)).toBe('established');
    expect(visibilityBand(80)).toBe('beacon');
    expect(visibilityBand(100)).toBe('beacon');
  });
});

describe('series colour rule', () => {
  it('always gives the subject the brand accent', () => {
    for (let i = 0; i < 10; i++) {
      expect(seriesStyle('subject', i).fill).toBe(oklch(beacon['600']));
      expect(seriesStyle('subject', i).isSubject).toBe(true);
    }
  });

  it('never assigns a visibility-ramp colour to a competitor', () => {
    // A competitor in "good green" reads as an endorsement; in "bad red" the
    // report reads as a hatchet job. Both cost the report its credibility.
    const rampFills = Object.values(visibility).map((c) => oklch(c));
    for (let i = 0; i < 12; i++) {
      expect(rampFills).not.toContain(seriesStyle('competitor', i).fill);
    }
  });

  it('cycles neutral fills and patterns so series stay distinguishable in B&W', () => {
    expect(seriesStyle('competitor', 0).pattern).toBe('solid');
    expect(seriesStyle('competitor', 1).pattern).toBe('hatch-45');
    expect(seriesStyle('competitor', 5).fill).toBe(seriesStyle('competitor', 0).fill);
  });
});

describe('tailwind preset exposes the tokens it claims to', () => {
  /*
   * Added in Epic 7. The `--avp-leading-*` tokens existed in tokens.css from
   * Epic 0 but were never listed in the preset, so `leading-prose` compiled to
   * nothing — a class that looks applied, reads as applied in review, and does
   * nothing. Silent no-ops are the worst failure mode a design system has, so
   * the preset is now checked against the stylesheet rather than trusted.
   */
  const every = (obj: Record<string, unknown>): string[] =>
    Object.values(obj).flatMap((value) =>
      typeof value === 'string'
        ? [value]
        : Array.isArray(value)
          ? [String(value[0])]
          : value && typeof value === 'object'
            ? every(value as Record<string, unknown>)
            : [],
    );

  it('every var() the preset references is defined in tokens.css', async () => {
    const { default: preset } = await import('../tailwind-preset.js');
    const referenced = new Set(
      every(preset.theme as unknown as Record<string, unknown>)
        .flatMap((value) => [...value.matchAll(/var\(--avp-([a-z0-9-_]+)\)/g)])
        .map((match) => match[1]!),
    );
    expect(referenced.size).toBeGreaterThan(50);
    const missing = [...referenced].filter((name) => cssVar(name) === null).sort();
    expect(missing).toEqual([]);
  });

  it('the leading tokens are reachable as utilities', async () => {
    const { default: preset } = await import('../tailwind-preset.js');
    const lineHeight = preset.theme.extend.lineHeight as Record<string, string>;
    expect(Object.keys(lineHeight).sort()).toEqual(['display', 'mono', 'prose', 'ui']);
    for (const value of Object.values(lineHeight)) {
      expect(value).toMatch(/^var\(--avp-leading-/);
    }
  });
});

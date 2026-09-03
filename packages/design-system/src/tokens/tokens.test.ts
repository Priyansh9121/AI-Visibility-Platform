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
  bench,
  benchAccent,
  benchColor,
  benchVar,
  semantic,
  BENCH_ACCENTS,
  BENCH_HUE_BUFFER,
  BENCH_FEASIBLE_ARC,
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
    ['bench', bench],
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

  /*
   * Epic 7.1 extends the same guard to the stylesheet. The preset was checked
   * against tokens.css from Epic 7, but `components.css` — where every one of
   * the system's own components is actually painted — was not. The Answer
   * Shelf was written against `--avp-border-hairline`, which has never
   * existed; the real token is `--avp-line-hairline`. It rendered, it looked
   * plausible, and the rule it drew was invisible. Same silent no-op the
   * preset check was written for, one file over.
   */
  /*
   * Epic 9.16. `.avp-reveal-group` is the element an IntersectionObserver
   * watches, and `display: contents` removes an element's box — so an observer
   * given one never fires. The whole hero and every pipeline step stayed
   * invisible, and no unit test could see it: jsdom computes no layout and a
   * static render has no observer at all. It took loading the page.
   *
   * Asserted at the stylesheet level because that is where the mistake was.
   */
  /*
   * Epic 9.16a, and the more important of the two stylesheet guards.
   *
   * `.avp-reveal` must be VISIBLE by default. The hidden state belongs only
   * under `.avp-motion-ready`, which a synchronous inline script in the
   * document head adds before the first paint.
   *
   * 9.16 had it the other way round and reasoned about the ways JS might fail
   * to reveal. The reasoning missed server rendering entirely: `/invite/{token}`
   * and `/reset-password/{token}` put their card in the HTML, so those two
   * screens were blank from paint until hydration. Nothing caught it — jsdom
   * applies no stylesheet, a static render has no browser, and the four screens
   * done first are never server-rendered so they could not show it.
   *
   * Asserted at the stylesheet level because that is where the default lives.
   */
  it('leaves `.avp-reveal` visible by default, hiding only under .avp-motion-ready', () => {
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );

    const base = components.match(/(?:^|\n)\.avp-reveal\s*\{([^}]*)\}/);
    expect(base, '.avp-reveal base rule is missing').not.toBeNull();
    // The failure this exists for: an SSR'd card that paints blank.
    expect(base![1]).not.toMatch(/opacity\s*:\s*0/);
    expect(base![1]).toMatch(/opacity\s*:\s*1/);

    // And the arrival still exists, gated on the class.
    const gated = components.match(/\.avp-motion-ready\s+\.avp-reveal\s*\{([^}]*)\}/);
    expect(gated, '.avp-motion-ready .avp-reveal rule is missing').not.toBeNull();
    expect(gated![1]).toMatch(/opacity\s*:\s*0/);
  });

  it('never gives the observed reveal group `display: contents`', () => {
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );
    const block = components.match(/\.avp-reveal-group\s*\{([^}]*)\}/);
    expect(block, '.avp-reveal-group rule is missing').not.toBeNull();
    expect(block![1]).not.toMatch(/display\s*:\s*contents/);
  });

  it('every var() components.css references is defined in tokens.css', () => {
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );
    // Skip the fallback arm of `var(--x, fallback)` — the fallback is the
    // point of that form, so a missing first arm there is deliberate.
    const referenced = new Set(
      [...components.matchAll(/var\(\s*--avp-([a-z0-9-_]+)\s*[,)]/g)].map((m) => m[1]!),
    );
    expect(referenced.size).toBeGreaterThan(30);
    const declaredHere = new Set(
      [...components.matchAll(/^\s*--avp-([a-z0-9-_]+):/gm)].map((m) => m[1]!),
    );
    const missing = [...referenced]
      .filter((name) => cssVar(name) === null && !declaredHere.has(name))
      .sort();
    expect(missing).toEqual([]);
  });

  /**
   * Epic 9.19 — the Working screens' motion, asserted where it lives.
   *
   * The brief for that pass said to EXTEND the motion language rather than
   * invent a second one alongside it, so the test is not "these rules exist"
   * but "these rules are spelled in the existing tokens". A hand-typed `150ms`
   * anywhere below is the failure this catches, and it is invisible to every
   * other kind of test in this repo: jsdom applies no stylesheet and a static
   * render has no computed style.
   */
  it('the Working screens move only in durations the system already defines', () => {
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );

    const ruleFor = (selector: string): string => {
      const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const m = components.match(new RegExp(`(?:^|\n)${escaped}\\s*\\{([^}]*)\\}`));
      expect(m, `${selector} rule is missing`).not.toBeNull();
      return m![1]!;
    };

    // The three surfaces the pass touched, and the token each is spelled in.
    expect(ruleFor('.avp-table tbody td')).toMatch(/transition:[^;]*--avp-duration-hover/);
    expect(ruleFor('.avp-badge')).toMatch(/transition:[^;]*--avp-duration-state/);
    expect(ruleFor('.avp-meter__lit')).toMatch(/transition:[^;]*--avp-duration-layout/);
    expect(ruleFor('.avp-meter__track')).toMatch(/transition:[^;]*--avp-duration-state/);

    // Epic B's grid. The cells fill in at the LAYOUT tier and carry no
    // stagger: a hand-typed per-row delay is exactly what this test exists to
    // catch, and the only stagger token the system defines is reveal-tier and
    // belongs to the Report. See the note above the rule in components.css.
    const gapCells = ruleFor('.avp-gapgrid--animate .avp-gapgrid__cell');
    expect(gapCells).toMatch(/animation:[^;]*--avp-duration-layout/);
    expect(gapCells).not.toMatch(/animation-delay/);
    expect(ruleFor('.avp-gapgrid--pending')).toMatch(
      /transition:[^;]*--avp-duration-state/,
    );

    // Row hover EASES. An instant repaint is the default-browser feel Epic
    // 9.19 removed from `.avp-table`, and this grid reintroduced it until the
    // motion audit caught it — the cell's own transition does not cover the
    // row, which paints underneath the cells.
    expect(ruleFor('.avp-gapgrid__row')).toMatch(
      /transition:[^;]*--avp-duration-hover/,
    );

    // The CELL's transition is a data change, not a hover: hover draws an
    // outline and never moves the fill, so the only repaint is a scan swap.
    expect(ruleFor('.avp-gapgrid__cell')).toMatch(
      /transition:[^;]*--avp-duration-state/,
    );

    // Epic E. The alert row's opacity transition serves BOTH the settling of
    // an acknowledged row and the hover that lifts it back — and the hover is
    // the frequent trigger, so it takes the hover tier. The mirror image of
    // the GapGrid finding above.
    expect(ruleFor('.avp-alert')).toMatch(/transition:[^;]*--avp-duration-hover/);
    // And it transitions ONLY opacity: `border-color` was in the list while
    // the acknowledged state set the value `.avp-card` already had, so that
    // half animated nothing. Asserted on the DECLARATION, not the rule body —
    // the body also carries the comment explaining the removal.
    const alertTransition = ruleFor('.avp-alert').match(/transition:[^;]*/)![0];
    expect(alertTransition).not.toMatch(/border-color/);
    expect(alertTransition).toMatch(/opacity/);
    expect(ruleFor('.avp-alert__failure')).toMatch(
      /transition:[^;]*--avp-duration-state/,
    );

    // The live dot is a LOOP, which nothing else in this system is, so its
    // period is derived from the reveal rather than being a fourth number.
    const pulse = ruleFor('.avp-badge__pulse');
    expect(pulse).toMatch(/animation:\s*avp-live-breath\s*calc\(\s*var\(--avp-duration-reveal\)/);

    // And it ends lit, so reduced motion (which collapses the animation to one
    // 0.01ms iteration) leaves a lit dot rather than one frozen at 35%.
    const frames = components.match(/@keyframes\s+avp-live-breath\s*\{([\s\S]*?)\n\}/);
    expect(frames, 'avp-live-breath keyframes are missing').not.toBeNull();
    expect(frames![1]).toMatch(/100%\s*\{\s*opacity:\s*1;\s*\}/);
  });

  /**
   * Epic 9.23 — every clickable primitive answers a press.
   *
   * Before this, `:active` appeared ZERO times in the whole stylesheet: nothing
   * in the system gave any feedback that a press had landed. That is the
   * highest-leverage motion gap the audit found — higher than any arrival
   * animation, because it is the one moment a user is actively waiting for a
   * response.
   *
   * Asserted at the stylesheet level because that is where it lives, and
   * because jsdom applies no stylesheet and a static render has no `:active`.
   */
  it('gives every interactive primitive a press state', () => {
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );

    const PRESSABLE = [
      '.avp-btn:active:not(:disabled)',
      '.avp-nav__item:active',
      '.avp-localnav__item:active',
      '.avp-localnav__back:active',
    ];

    for (const selector of PRESSABLE) {
      const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const rule = components.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
      expect(rule, `${selector} has no press state`).not.toBeNull();
      // Subtle, per the press-feedback budget — never scale(0) and never a
      // shrink big enough to read as a layout change.
      expect(rule![1]).toMatch(/transform:\s*scale\(0\.9[5-8]\)/);
    }
  });

  it('animates the press with the hover duration, not a hand-typed one', () => {
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );
    // 120ms sits inside the 100-160ms press-feedback budget. A press that is
    // not in the transition list would snap instead of easing.
    for (const base of ['.avp-btn', '.avp-nav__item', '.avp-localnav__item', '.avp-localnav__back']) {
      const escaped = base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const rule = components.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`));
      expect(rule, `${base} base rule is missing`).not.toBeNull();
      expect(rule![1]).toMatch(/transition:[^;]*transform[^;]*--avp-duration-hover/);
    }
  });

  it('drops the press movement under reduced motion, keeping colour', () => {
    // The global block in base.css collapses DURATIONS, which would make the
    // press instant rather than absent — the element would still jump. Reduced
    // motion asks for less movement, not faster movement.
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );
    const blocks = [...components.matchAll(/@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/g)]
      .map((m) => m[1]!)
      .join('\n');
    expect(blocks).toMatch(/\.avp-btn:active/);
    expect(blocks).toMatch(/transform:\s*none\s*!important/);
  });

  /**
   * Epic 9.23 — no hover rule may fire on a touch device.
   *
   * A touch device fires `:hover` on tap and leaves it STUCK until the next tap
   * elsewhere, so the last thing pressed keeps a hover colour that reads as a
   * selection nothing selected. All nine hover rules in this file were ungated.
   *
   * This walks the stylesheet and fails on any `:hover` that is not inside a
   * `@media (hover: hover)` block — so a hover rule added later is caught the
   * day it is added, rather than needing this list to be maintained.
   */
  it('gates every hover rule behind a real pointer', () => {
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );

    // Track brace depth, and whether we are inside a hover-gated media block.
    let depth = 0;
    const gateDepths: number[] = [];
    const ungated: string[] = [];

    for (const rawLine of components.split('\n')) {
      const line = rawLine.trim();
      // A `:hover` in a comment is prose, not a rule.
      const isComment = line.startsWith('*') || line.startsWith('/*') || line.startsWith('//');

      if (!isComment && line.includes(':hover') && !line.startsWith('@media')) {
        if (gateDepths.length === 0) ungated.push(line);
      }

      if (/^@media[^{]*\(hover:\s*hover\)/.test(line)) gateDepths.push(depth);

      for (const ch of rawLine) {
        if (ch === '{') depth += 1;
        if (ch === '}') {
          depth -= 1;
          if (gateDepths.length > 0 && depth === gateDepths[gateDepths.length - 1]) {
            gateDepths.pop();
          }
        }
      }
    }

    expect(ungated, `ungated hover rules:\n${ungated.join('\n')}`).toEqual([]);
  });

  it('still HAS hover rules — the gate must not pass by deleting them', () => {
    // Without this, removing every :hover rule would satisfy the test above.
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );
    const hovers = components.match(/^\s*\.[\w-]+[^{\n]*:hover[^{\n]*\{/gm) ?? [];
    expect(hovers.length).toBeGreaterThanOrEqual(9);
  });

  it('does NOT gate focus or active — those must work on every input type', () => {
    // Gating focus would strip a keyboard affordance; gating active would strip
    // the one piece of feedback a touch device gets right.
    const components = readFileSync(
      fileURLToPath(new URL('../styles/components.css', import.meta.url)),
      'utf8',
    );
    const gated = [...components.matchAll(/@media \(hover: hover\)[^{]*\{([\s\S]*?)\n\}/g)]
      .map((m) => m[1]!)
      .join('\n');
    expect(gated).not.toMatch(/:active/);
    expect(gated).not.toMatch(/:focus/);
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

/* ==================================================================== *
 * The Working-screen accent layer — Epic 9.24.
 * ==================================================================== */

describe('bench hues cannot be confused with anything that already means something', () => {
  /**
   * Every hue in this system that a viewer has been taught to read: the five
   * visibility stops, the brand, and the four semantics. A categorical chip
   * landing near one of these would be read as a score or as a system state.
   */
  const MEANING: Record<string, number> = {
    'vis-00': visibility['00'][2],
    'vis-25': visibility['25'][2],
    'vis-50': visibility['50'][2],
    'vis-75': visibility['75'][2],
    'vis-100': visibility['100'][2],
    beacon: beacon['600'][2],
    success: semantic.success[2],
    warn: semantic.warn[2],
    danger: semantic.danger[2],
  };

  const separation = (a: number, b: number): number => {
    const d = Math.abs(a - b) % 360;
    return Math.min(d, 360 - d);
  };

  for (const accent of BENCH_ACCENTS) {
    for (const [name, hue] of Object.entries(MEANING)) {
      it(`${accent.name} (${accent.hue}) stays ${BENCH_HUE_BUFFER}deg clear of ${name}`, () => {
        expect(separation(accent.hue, hue)).toBeGreaterThanOrEqual(BENCH_HUE_BUFFER);
      });
    }
  }

  it('is genuinely wider in hue than the single accent it supplements', () => {
    const hues = BENCH_ACCENTS.map((a) => a.hue);
    // beacon alone spans 0deg. This is the "wider hue range" claim, as a number.
    expect(Math.max(...hues) - Math.min(...hues)).toBeGreaterThanOrEqual(80);
    expect(new Set(hues).size).toBe(BENCH_ACCENTS.length);
  });

  it('is genuinely more saturated than beacon-600', () => {
    // The "higher saturation" claim, as a number: 0.185 against 0.125.
    for (const accent of BENCH_ACCENTS) {
      expect(bench[`${accent.key}-600`]![1]).toBeGreaterThan(beacon['600'][1]);
    }
  });

  it('holds every accent at the same weight, so colour implies no rank', () => {
    // Two accents differing in lightness would make one read as more important,
    // which is exactly the property the visibility ramp has and this must not.
    for (const stop of ['050', '100', '600', '700'] as const) {
      const ls = BENCH_ACCENTS.map((a) => bench[`${a.key}-${stop}`]![0]);
      expect(new Set(ls).size).toBe(1);
    }
  });

  it('deepens rather than lightens from 600 to 700, like beacon does', () => {
    for (const accent of BENCH_ACCENTS) {
      expect(bench[`${accent.key}-700`]![0]).toBeLessThan(bench[`${accent.key}-600`]![0]);
    }
  });
});

/* --------------------------------------------------------------------- *
 * Epic B — why the layer stops at seven.
 *
 * Epic 9.24 argued the bench hues from where a palette database's chromatic
 * mass sits. That says where colour is AVAILABLE; it does not say where colour
 * is USABLE, and the difference is what caps this layer. These tests hold the
 * two constraints that decide it, so a later epic that wants an eighth accent
 * has to confront the arithmetic rather than append a row and watch a chip
 * render duller than its neighbours.
 * --------------------------------------------------------------------- */

/** OKLCH -> linear sRGB. Enough to answer "is this colour representable". */
function toSrgb(l: number, c: number, hDeg: number): [number, number, number] {
  const h = (hDeg * Math.PI) / 180;
  const a = c * Math.cos(h);
  const b = c * Math.sin(h);
  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894841775 * a - 1.291485548 * b;
  const [L3, M3, S3] = [l_ ** 3, m_ ** 3, s_ ** 3];
  return [
    4.0767416621 * L3 - 3.3077115913 * M3 + 0.2309699292 * S3,
    -1.2684380046 * L3 + 2.6097574011 * M3 - 0.3413193965 * S3,
    -0.0041960863 * L3 - 0.7034186147 * M3 + 1.707614701 * S3,
  ];
}

const inGamut = (l: number, c: number, h: number): boolean =>
  toSrgb(l, c, h).every((v) => v >= -1e-6 && v <= 1 + 1e-6);

describe('the bench layer is full, and the cap is arithmetic', () => {
  it('renders every accent at the chroma the shared table claims', () => {
    // The equal-weight property is only real if sRGB can actually SHOW it.
    // An accent whose 600 stop is out of gamut gets clamped by the browser and
    // renders duller than its neighbours — which reads as rank, the one thing
    // categorical colour must not imply. Hue 241 fails this by a wide margin
    // (ceiling 0.128 against the required 0.185), which is why the arc does
    // not extend into blue however much room the 30deg buffer leaves there.
    for (const accent of BENCH_ACCENTS) {
      for (const stop of ['600', '700'] as const) {
        const [l, c] = [bench[`${accent.key}-${stop}`]![0], bench[`${accent.key}-${stop}`]![1]];
        expect(
          inGamut(l, c, accent.hue),
          `${accent.name} ${stop} is outside sRGB and will render clamped`,
        ).toBe(true);
      }
    }
  });

  it('places every accent inside the one arc that satisfies both constraints', () => {
    for (const accent of BENCH_ACCENTS) {
      expect(accent.hue).toBeGreaterThanOrEqual(BENCH_FEASIBLE_ARC.from);
      expect(accent.hue).toBeLessThanOrEqual(BENCH_FEASIBLE_ARC.to);
    }
  });

  it('has no room left: the arc cannot seat an eighth accent', () => {
    // 98.5deg of arc. Seven accents sit ~16deg apart; an eighth would force
    // them to ~14 and a tenth to ~11, below what hue alone separates at fixed
    // lightness and chroma. A later epic needing more must change the SYSTEM
    // — group the nav, or let chroma vary — not this array. If that decision
    // is taken deliberately, this test is the one to rewrite.
    const span = BENCH_FEASIBLE_ARC.to - BENCH_FEASIBLE_ARC.from;
    const spacing = span / BENCH_ACCENTS.length;
    expect(spacing).toBeGreaterThanOrEqual(14);
    expect(BENCH_ACCENTS.length).toBe(7);
  });

  it('keeps enough separation between neighbouring accents to tell them apart', () => {
    const sorted = [...BENCH_ACCENTS].map((a) => a.hue).sort((x, y) => x - y);
    for (let i = 1; i < sorted.length; i++) {
      expect(sorted[i]! - sorted[i - 1]!).toBeGreaterThanOrEqual(12);
    }
  });
});

describe('benchAccent', () => {
  it('cycles, so any list length is safe', () => {
    const n = BENCH_ACCENTS.length;
    expect(benchAccent(0)).toBe(benchAccent(n));
    expect(benchAccent(1)).toBe(benchAccent(n + 1));
  });

  it('handles a negative index without returning undefined', () => {
    expect(benchAccent(-1)).toBe(BENCH_ACCENTS[BENCH_ACCENTS.length - 1]);
  });

  it('is stable — the same index always yields the same accent', () => {
    for (const i of [0, 3, 7, 12]) expect(benchColor(i)).toBe(benchColor(i));
  });

  it('benchVar names the custom property for the same accent benchColor resolves', () => {
    for (let i = 0; i < 8; i++) {
      expect(benchVar(i)).toBe(`var(--avp-bench-${benchAccent(i).key}-600)`);
      expect(benchColor(i)).toBe(oklch(bench[`${benchAccent(i).key}-600`]!));
    }
  });
});

describe('seriesStyle is where the two contexts diverge', () => {
  it('defaults to the report palette — the report never opts in', () => {
    expect(seriesStyle('competitor', 0)).toEqual(seriesStyle('competitor', 0, 'report'));
  });

  it('keeps competitors neutral on the report', () => {
    for (let i = 0; i < 5; i++) {
      expect(seriesStyle('competitor', i, 'report').fill).toBe(oklch(competitor[
        (['1', '2', '3', '4', '5'] as const)[i]!
      ]));
    }
  });

  it('draws competitors from bench on a Working screen', () => {
    for (let i = 0; i < 6; i++) {
      expect(seriesStyle('competitor', i, 'working').fill).toBe(benchColor(i));
    }
  });

  it('gives the client beacon in BOTH contexts — one brand, one colour', () => {
    for (const palette of ['report', 'working'] as const) {
      expect(seriesStyle('subject', 0, palette).fill).toBe(oklch(beacon['600']));
      expect(seriesStyle('subject', 3, palette).isSubject).toBe(true);
    }
  });

  it('never gives a competitor the brand colour, in either context', () => {
    for (const palette of ['report', 'working'] as const) {
      for (let i = 0; i < 12; i++) {
        expect(seriesStyle('competitor', i, palette).fill).not.toBe(oklch(beacon['600']));
      }
    }
  });

  it('keeps the pattern assignment unconditional, so greyscale and CVD still work', () => {
    for (let i = 0; i < 12; i++) {
      expect(seriesStyle('competitor', i, 'working').pattern).toBe(
        seriesStyle('competitor', i, 'report').pattern,
      );
    }
  });

  it('never returns a visibility ramp colour for a competitor, in either context', () => {
    const ramp = Object.values(visibility).map((c) => oklch(c));
    for (const palette of ['report', 'working'] as const) {
      for (let i = 0; i < 12; i++) {
        expect(ramp).not.toContain(seriesStyle('competitor', i, palette).fill);
      }
    }
  });
});

/*
 * Epic E. `.avp-alert` and `.avp-alert.is-acknowledged` were class names on a
 * component before they were rules in this stylesheet — they rendered fine and
 * styled nothing, which is the silent no-op the `var()` guard above exists for
 * in its other form. Found by a craft review rather than by a test, so this is
 * the test.
 */
describe('an acknowledged alert row looks settled', () => {
  const components = readFileSync(
    fileURLToPath(new URL('../styles/components.css', import.meta.url)),
    'utf8',
  );

  it('defines the classes the alert feed actually uses', () => {
    expect(components).toMatch(/\.avp-alert\s*\{/);
    expect(components).toMatch(/\.avp-alert\.is-acknowledged\s*\{/);
  });

  it('recedes by opacity rather than by greying its words', () => {
    // Every word in the row is still true after somebody has read it. Greying
    // the text would say the finding had expired rather than been seen.
    const rule = components.match(/\.avp-alert\.is-acknowledged\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect(rule![1]).toMatch(/opacity/);
    // `[^-]` so `border-color` does not count as recolouring the text.
    expect(rule![1]).not.toMatch(/[^-]color:/);
  });
});

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

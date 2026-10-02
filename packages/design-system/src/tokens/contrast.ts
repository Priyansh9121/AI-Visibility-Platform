/**
 * Pure colour maths for the contrast test: parse custom properties out of
 * tokens.css, convert oklch to sRGB, composite alpha in gamma-encoded sRGB
 * (what a browser does for a box-shadow) and compute the WCAG 2 ratio.
 */

export type Rgb = [number, number, number]; // gamma-encoded sRGB, 0..1

export interface Colour {
  rgb: Rgb;
  alpha: number;
}

/** Declarations of the top-level block whose selector is exactly `selector`. */
export function parseBlock(css: string, selector: string): Map<string, string> {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = new Map<string, string>();
  const re = /^([^\s{}][^{}]*?)\s*\{([^{}]*)\}/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(stripped))) {
    if (m[1] !== selector) continue;
    for (const decl of (m[2] ?? '').split(';')) {
      const i = decl.indexOf(':');
      if (i < 0) continue;
      const name = decl.slice(0, i).trim();
      if (name.startsWith('--')) out.set(name, decl.slice(i + 1).trim());
    }
  }
  return out;
}

/** Layer blocks: later blocks override earlier ones (the cascade for a themed scope). */
export function scopeOf(...blocks: Map<string, string>[]): Map<string, string> {
  const s = new Map<string, string>();
  for (const b of blocks) for (const [k, v] of b) s.set(k, v);
  return s;
}

/** Resolve var(--x) references against `scope`. */
export function resolveVars(value: string, scope: Map<string, string>): string {
  let v = value;
  for (let i = 0; i < 10 && v.includes('var('); i += 1) {
    v = v.replace(/var\(\s*(--[\w-]+)\s*\)/g, (_, name: string) => {
      const r = scope.get(name);
      if (r === undefined) throw new Error(`unresolved ${name}`);
      return r;
    });
  }
  return v;
}

/** Split on commas that are not inside parentheses. */
export function splitTopLevel(value: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let cur = '';
  for (const ch of value) {
    if (ch === '(') depth += 1;
    if (ch === ')') depth -= 1;
    if (ch === ',' && depth === 0) {
      parts.push(cur.trim());
      cur = '';
    } else cur += ch;
  }
  if (cur.trim()) parts.push(cur.trim());
  return parts;
}

const enc = (x: number): number => {
  const c = Math.min(1, Math.max(0, x));
  return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
};
const lin = (x: number): number => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);

export function oklchToRgb(L: number, C: number, hDeg: number): Rgb {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    enc(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    enc(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    enc(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

/** Parse `oklch(L C H)` or `oklch(L C H / A)`. */
export function parseOklch(value: string): Colour {
  const m = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\s*\)$/.exec(value.trim());
  if (!m) throw new Error(`not an oklch colour: ${value}`);
  return {
    rgb: oklchToRgb(Number(m[1]), Number(m[2]), Number(m[3])),
    alpha: m[4] === undefined ? 1 : Number(m[4]),
  };
}

/** Composite fg (with its alpha) over an opaque bg, in gamma-encoded sRGB. */
export function composite(fg: Colour, bg: Rgb): Rgb {
  const mix = (f: number, b: number): number => fg.alpha * f + (1 - fg.alpha) * b;
  return [mix(fg.rgb[0], bg[0]), mix(fg.rgb[1], bg[1]), mix(fg.rgb[2], bg[2])];
}

export function luminance(rgb: Rgb): number {
  return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
}

export function contrastRatio(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

import preset from '../tailwind-preset';

/**
 * Finds stock Tailwind classes that tailwind-preset.ts REPLACED and therefore
 * compile to nothing (see stockClasses.test.ts). What is allowed is derived by
 * reading the preset object, so adding a token needs no edit here. The only
 * hard-coded names are Tailwind's own DEFAULT vocabulary (the stock palette,
 * text sizes, radii, shadows, families) which is what we are hunting; they are
 * flagged unless the preset itself defines the same key.
 */

export interface StockFinding {
  line: number;
  className: string;
  reason: string;
}

const theme = preset.theme;
const has = (obj: object, key: string) => Object.prototype.hasOwnProperty.call(obj, key);

// Tailwind's stock palette names (the default theme the preset removed).
const STOCK_PALETTE = [
  'slate', 'gray', 'zinc', 'neutral', 'stone', 'red', 'orange', 'amber', 'yellow', 'lime',
  'green', 'emerald', 'teal', 'cyan', 'sky', 'blue', 'indigo', 'violet', 'purple', 'fuchsia',
  'pink', 'rose',
];
// Keyword colours: flagged only if the preset does not define them.
const KEYWORD_COLOURS = ['white', 'black', 'transparent', 'current', 'inherit'];

// Utilities whose value is resolved from theme.colors. Longest first.
const COLOUR_PREFIXES = [
  'border-[trblxyse]', 'ring-offset', 'placeholder', 'decoration', 'outline', 'stroke',
  'border', 'shadow', 'caret', 'accent', 'divide', 'fill', 'ring', 'from', 'via', 'text',
  'to', 'bg',
];
const COLOUR_RE = new RegExp(
  `^(?:${COLOUR_PREFIXES.join('|')})-(${[...STOCK_PALETTE, ...KEYWORD_COLOURS].join('|')})(?:-(\\d{2,3}))?$`,
);

// Utilities whose numeric suffix is resolved from theme.spacing.
const SPACING_PREFIXES = [
  'p', 'px', 'py', 'pt', 'pb', 'pl', 'pr', 'ps', 'pe',
  'm', 'mx', 'my', 'mt', 'mb', 'ml', 'mr', 'ms', 'me',
  'gap', 'gap-x', 'gap-y', 'space-x', 'space-y',
  'w', 'h', 'min-w', 'min-h', 'max-w', 'max-h', 'size',
  'inset', 'inset-x', 'inset-y', 'top', 'right', 'bottom', 'left', 'start', 'end',
  'translate-x', 'translate-y', 'basis',
];
const SPACING_RE = new RegExp(`^(?:${SPACING_PREFIXES.join('|')})-(\\d+(?:\\.\\d+)?)$`);

const TEXT_SIZE_RE = /^text-(xs|sm|base|lg|xl|[2-9]xl)$/;
const SHADOW_RE = /^shadow(?:-(sm|md|lg|xl|2xl|inner))?$/;
const ROUNDED_RE = /^rounded(?:-(?:t|r|b|l|s|e|tl|tr|br|bl|ss|se|es|ee))?(?:-([a-z0-9]+))?$/;
const FONT_FAMILY_RE = /^font-(sans|serif|mono)$/;

/** Split on `sep` at bracket depth 0, so `data-[a:b]:x` and `bg-[url(a:b)]` survive. */
function splitTop(s: string, sep: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let cur = '';
  for (const ch of s) {
    if (ch === '[' || ch === '(') depth++;
    else if (ch === ']' || ch === ')') depth--;
    if (ch === sep && depth === 0) {
      parts.push(cur);
      cur = '';
    } else cur += ch;
  }
  parts.push(cur);
  return parts;
}

/** Returns why `cls` compiles to nothing under the preset, or null. */
export function classifyClass(cls: string): string | null {
  let u = splitTop(cls, ':').pop() ?? '';
  u = u.replace(/^!/, '').replace(/!$/, '').replace(/^-/, '');
  if (u === '' || u.includes('[')) return null; // arbitrary values are not theme lookups

  // Opacity modifier: bg-white/50.
  const slash = splitTop(u, '/');
  const noOpacity = slash.length === 2 ? slash[0]! : u;

  const colour = COLOUR_RE.exec(noOpacity);
  if (colour) {
    const [, name, shade] = colour;
    const colors = theme.colors as unknown as Record<string, unknown>;
    const entry = has(colors, name!) ? colors[name!] : undefined;
    const defined = shade
      ? typeof entry === 'object' && entry !== null && has(entry, shade)
      : typeof entry === 'string';
    if (!defined) return `colour: ${name}${shade ? '-' + shade : ''} is not in the preset palette`;
    return null;
  }

  const size = TEXT_SIZE_RE.exec(u);
  if (size) {
    return has(theme.fontSize, u.slice(5)) ? null : 'text size: stock size is not in the preset fontSize';
  }

  const shadow = SHADOW_RE.exec(u);
  if (shadow) {
    const key = shadow[1] ?? 'DEFAULT';
    return has(theme.boxShadow, key) ? null : 'shadow: stock shadow is not in the preset boxShadow';
  }

  const rounded = ROUNDED_RE.exec(u);
  if (rounded && u.startsWith('rounded')) {
    // A bare `rounded` or `rounded-t` needs a DEFAULT key.
    const key = rounded[1] ?? 'DEFAULT';
    return has(theme.borderRadius, key) ? null : 'radius: not a key of the preset borderRadius';
  }

  const family = FONT_FAMILY_RE.exec(u);
  if (family) {
    return has(theme.fontFamily, family[1]!) ? null : 'font family: not in the preset fontFamily';
  }

  const spacing = SPACING_RE.exec(u);
  if (spacing) {
    return has(theme.spacing, spacing[1]!) ? null : 'spacing: value is not in the preset spacing scale';
  }

  return null;
}

// ---------------------------------------------------------------------------
// Source scanning
// ---------------------------------------------------------------------------

interface Chunk {
  text: string;
  line: number;
}

/**
 * Yields every string-literal chunk (', ", ` and the text parts of templates;
 * `${}` bodies are scanned as code) with its start line. Comments are skipped.
 * Quote strings end at a newline so an apostrophe in JSX text cannot swallow
 * the rest of the file.
 */
function stringChunks(src: string): Chunk[] {
  const out: Chunk[] = [];
  let i = 0;
  let line = 1;

  function code(untilBrace: boolean): void {
    let depth = 0;
    while (i < src.length) {
      const ch = src[i]!;
      const next = src[i + 1];
      if (ch === '\n') {
        line++;
        i++;
      } else if (ch === '/' && next === '/') {
        while (i < src.length && src[i] !== '\n') i++;
      } else if (ch === '/' && next === '*') {
        i += 2;
        while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) {
          if (src[i] === '\n') line++;
          i++;
        }
        i += 2;
      } else if (ch === '"' || ch === "'") {
        const start = line;
        i++;
        let text = '';
        while (i < src.length && src[i] !== ch && src[i] !== '\n') {
          if (src[i] === '\\') {
            text += src[i + 1] ?? '';
            i += 2;
          } else text += src[i++];
        }
        if (src[i] === ch) i++;
        out.push({ text, line: start });
      } else if (ch === '`') {
        i++;
        let text = '';
        let start = line;
        while (i < src.length && src[i] !== '`') {
          if (src[i] === '\\') {
            text += src[i + 1] ?? '';
            i += 2;
          } else if (src[i] === '$' && src[i + 1] === '{') {
            out.push({ text, line: start });
            text = '';
            i += 2;
            code(true);
            start = line;
          } else {
            if (src[i] === '\n') line++;
            text += src[i++];
          }
        }
        i++;
        out.push({ text, line: start });
      } else {
        if (untilBrace) {
          if (ch === '{') depth++;
          else if (ch === '}') {
            if (depth === 0) {
              i++;
              return;
            }
            depth--;
          }
        }
        i++;
      }
    }
  }

  code(false);
  return out;
}

/** Bare (hyphen-less) Tailwind utilities that commonly sit in a class list. */
const BARE_UTILITIES = new Set([
  'flex', 'grid', 'block', 'inline', 'hidden', 'contents', 'relative', 'absolute', 'fixed',
  'sticky', 'static', 'truncate', 'underline', 'italic', 'uppercase', 'lowercase',
  'capitalize', 'border', 'ring', 'outline', 'shadow', 'rounded', 'transition', 'container',
  'grow', 'shrink', 'isolate', 'antialiased', 'resize', 'invisible', 'visible',
]);

/** A token that looks like a utility: a hyphenated or variant token, or a known bare one. */
const utilityish = (tok: string) =>
  (/[a-z0-9][-:][a-z0-9!\-[]/i.test(tok) && !/[.,;]$/.test(tok)) || BARE_UTILITIES.has(tok);

function scanTokens(text: string, startLine: number, found: StockFinding[]): void {
  const tokens: Array<{ tok: string; line: number }> = [];
  let line = startLine;
  for (const piece of text.split(/(\s+)/)) {
    if (/^\s+$/.test(piece)) line += (piece.match(/\n/g) ?? []).length;
    else if (piece) tokens.push({ tok: piece, line });
  }
  // Bare words (`shadow`, `rounded`) are also English. Only treat them as
  // classes when the string is plausibly a class list.
  const classList = tokens.filter((t) => utilityish(t.tok)).length * 2 >= tokens.length;
  for (const { tok, line: l } of tokens) {
    const bare = !/[-:]/.test(tok);
    if (bare && !classList) continue;
    const reason = classifyClass(tok);
    if (reason) found.push({ line: l, className: tok, reason });
  }
}

export function findStockClasses(source: string, kind: 'ts' | 'tsx' | 'css'): StockFinding[] {
  const found: StockFinding[] = [];
  if (kind === 'css') {
    // Only @apply bodies name utilities. Blank comments, keep newlines.
    const clean = source.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
    for (const m of clean.matchAll(/@apply\s+([^;}]*)/g)) {
      const before = clean.slice(0, m.index! + m[0].indexOf(m[1]!));
      const startLine = (before.match(/\n/g) ?? []).length + 1;
      scanTokens(m[1]!.replace(/!important/g, ''), startLine, found);
    }
    return found;
  }
  for (const chunk of stringChunks(source)) scanTokens(chunk.text, chunk.line, found);
  return found;
}

import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { classifyClass, findStockClasses } from './stockClasses';

/**
 * STOCK TAILWIND CLASSES THE PRESET REMOVED.
 *
 * tailwind-preset.ts REPLACES colours, spacing, font family, font size, radius
 * and shadow. `bg-white text-gray-500 text-sm shadow-md` therefore compile to
 * NOTHING: `next build` passes, every jsdom test passes (no stylesheet), and the
 * element renders unstyled. reportIsolation.test.ts only catches a few shapes
 * of that (numeric spacing, `-semantic-`, grouped suffixes). This file catches
 * the whole family, over every source tree that ships, and derives what is
 * allowed from the preset itself so a new token needs no edit here.
 */

const FLAGGED: Array<[string, string]> = [
  // Stock palette colours, on every colour-taking prefix.
  ['bg-white', 'colour'],
  ['bg-black', 'colour'],
  ['bg-slate-500', 'colour'],
  ['text-gray-500', 'colour'],
  ['border-red-600', 'colour'],
  ['border-t-blue-500', 'colour'],
  ['ring-indigo-500', 'colour'],
  ['ring-offset-white', 'colour'],
  ['outline-emerald-400', 'colour'],
  ['fill-zinc-900', 'colour'],
  ['stroke-amber-300', 'colour'],
  ['from-pink-500', 'colour'],
  ['via-purple-500', 'colour'],
  ['to-rose-500', 'colour'],
  ['divide-neutral-200', 'colour'],
  ['placeholder-stone-400', 'colour'],
  ['decoration-sky-500', 'colour'],
  ['accent-teal-500', 'colour'],
  ['caret-lime-500', 'colour'],
  ['shadow-cyan-500', 'colour'],
  ['bg-gray-50/50', 'colour'],
  ['hover:bg-white', 'colour'],
  ['dark:md:text-gray-300', 'colour'],
  ['!bg-white', 'colour'],
  ['data-[state=open]:bg-white', 'colour'],
  // Stock text sizes.
  ['text-xs', 'text size'],
  ['text-sm', 'text size'],
  ['text-base', 'text size'],
  ['text-lg', 'text size'],
  ['text-xl', 'text size'],
  ['text-2xl', 'text size'],
  ['text-9xl', 'text size'],
  ['md:text-sm', 'text size'],
  // Stock shadows.
  ['shadow', 'shadow'],
  ['shadow-sm', 'shadow'],
  ['shadow-md', 'shadow'],
  ['shadow-lg', 'shadow'],
  ['shadow-xl', 'shadow'],
  ['shadow-2xl', 'shadow'],
  ['shadow-inner', 'shadow'],
  ['hover:shadow-lg', 'shadow'],
  // Radius.
  ['rounded', 'radius'],
  ['rounded-3xl', 'radius'],
  ['rounded-t', 'radius'],
  ['rounded-t-3xl', 'radius'],
  ['rounded-card', 'radius'],
  // Font family.
  ['font-sans', 'font family'],
  ['font-serif', 'font family'],
  // Off-scale spacing.
  ['w-40', 'spacing'],
  ['h-2.5', 'spacing'],
  ['p-7', 'spacing'],
  ['-mt-9', 'spacing'],
  ['gap-x-12', 'spacing'],
  ['max-w-64', 'spacing'],
  ['translate-x-7', 'spacing'],
  ['lg:px-20', 'spacing'],
];

const ALLOWED = [
  // The preset's own colours, roles, radii, shadows, sizes, families, spacing.
  'bg-surface-raised', 'text-text-primary', 'border-line-hairline', 'bg-paper-100',
  'text-on-accent', 'bg-bench-1-050', 'fill-vis-75', 'stroke-ink-800', 'ring-beacon-600',
  'bg-danger', 'text-warn', 'bg-success/50', 'from-beacon-400', 'to-signal-400',
  'text-kpi', 'text-hero', 'text-score', 'text-ui-base', 'text-ed-2xl', 'text-ui-2xs',
  'shadow-raised', 'shadow-none', 'shadow-focus', 'shadow-selected', 'hover:shadow-lifted',
  'font-display', 'font-editorial', 'font-ui', 'font-mono',
  'rounded-none', 'rounded-sm', 'rounded-md', 'rounded-lg', 'rounded-xl', 'rounded-2xl',
  'rounded-full', 'rounded-t-lg', 'rounded-tl-md', 'rounded-b-full',
  'p-4', 'px-0.5', 'gap-1.5', 'mt-rhythm', 'h-beat', 'w-px', '-mt-4', 'space-y-8', 'w-32',
  'min-w-0', 'inset-0', 'top-2', 'max-w-measure', 'max-w-report', 'translate-x-1',
  // Non-theme utilities that share a prefix.
  'text-left', 'text-center', 'text-right', 'text-wrap', 'text-balance', 'text-nowrap',
  'text-ellipsis', 'text-clip', 'text-justify',
  'border', 'border-0', 'border-2', 'border-4', 'border-8', 'border-solid', 'border-dashed',
  'border-t', 'border-x', 'border-y', 'border-b-2', 'border-collapse', 'border-separate',
  'ring', 'ring-0', 'ring-1', 'ring-2', 'ring-4', 'ring-8', 'ring-inset', 'ring-offset-2',
  'outline', 'outline-none', 'outline-offset-2', 'outline-2', 'outline-dashed',
  'bg-gradient-to-r', 'bg-gradient-to-br', 'bg-cover', 'bg-center', 'bg-fixed', 'bg-no-repeat',
  'bg-accent', 'bg-clip-text', 'bg-cover',
  'shadow-[0_0_0_1px_var(--avp-line-hairline)]',
  'divide-y', 'divide-x-2', 'font-bold', 'font-semibold', 'font-medium', 'font-normal',
  'grid-cols-2', 'w-1/2', 'w-full', 'h-screen', 'h-auto', 'min-h-screen', 'max-w-none',
  'bg-[var(--avp-surface-ground)]', 'stroke-2', 'stroke-0', 'fill-current', 'text-current',
  'decoration-2', 'underline', 'leading-6', 'z-10', 'opacity-50', 'flex', 'rounded-[3px]',
];

describe('classifyClass: stock classes the preset removed are flagged', () => {
  for (const [cls, why] of FLAGGED) {
    it(`flags ${cls} (${why})`, () => {
      const reason = classifyClass(cls);
      expect(reason).not.toBeNull();
      expect(reason).toContain(why);
    });
  }
});

describe('classifyClass: preset classes and non-theme utilities pass', () => {
  for (const cls of ALLOWED) {
    it(`allows ${cls}`, () => {
      expect(classifyClass(cls)).toBeNull();
    });
  }

  it('flags transparent/current/inherit only if the preset does not define them', async () => {
    const preset = (await import('../tailwind-preset')).default;
    for (const key of ['transparent', 'current', 'inherit'] as const) {
      const defined = key in preset.theme.colors;
      const cls = `bg-${key}`;
      expect(classifyClass(cls) === null).toBe(defined);
    }
  });
});

describe('findStockClasses: scanning source', () => {
  it('reports the line and class in a className string', () => {
    const src = ['export const A = () => (', '  <div className="flex bg-white p-4">x</div>', ');'].join('\n');
    expect(findStockClasses(src, 'tsx')).toEqual([
      expect.objectContaining({ line: 2, className: 'bg-white' }),
    ]);
  });

  it('finds classes in template literals and inside ${} expressions', () => {
    const src = 'const c = `flex ${on ? "text-sm" : "text-ui-sm"} shadow-md`;';
    const found = findStockClasses(src, 'ts').map((f) => f.className).sort();
    expect(found).toEqual(['shadow-md', 'text-sm']);
  });

  it('ignores line, block and JSX comments but keeps line numbers', () => {
    const src = [
      '// bg-slate-500 does not compile',
      '/* text-blue-600',
      '   shadow-md */',
      '<div>{/* bg-white */}</div>',
      'const x = "bg-white"; // text-sm',
      'const u = "https://example.test/a";',
    ].join('\n');
    expect(findStockClasses(src, 'tsx')).toEqual([
      expect.objectContaining({ line: 5, className: 'bg-white' }),
    ]);
  });

  it('does not flag identifiers or prose that merely contain a stock word', () => {
    const src = [
      'const shadow = 1; const rounded = true;',
      'const msg = "a soft shadow and rounded corners";',
      'const boxShadow = "var(--x)";',
    ].join('\n');
    expect(findStockClasses(src, 'ts')).toEqual([]);
  });

  it('scans @apply lines in css, and only those', () => {
    const css = [
      '.a { box-shadow: var(--x); border-color: red; text-align: center; }',
      '.b { @apply bg-white text-ui-sm; }',
    ].join('\n');
    expect(findStockClasses(css, 'css')).toEqual([
      expect.objectContaining({ line: 2, className: 'bg-white' }),
    ]);
  });
});

describe('findStockClasses: traps that compile fine but paint a stock default', () => {
  const names = (src: string, kind: 'ts' | 'tsx' | 'css' = 'tsx') =>
    findStockClasses(src, kind).map((f) => f.className);

  it('flags any dark: variant (Tailwind follows the OS, not data-theme)', () => {
    expect(names('<div className="flex dark:bg-surface-raised p-4" />')).toEqual(['dark:bg-surface-raised']);
    expect(names('<div className="flex md:dark:p-4 p-2" />')).toEqual(['md:dark:p-4']);
    expect(names('.a { @apply flex dark:p-4; }', 'css')).toEqual(['dark:p-4']);
  });

  it('does not flag words that merely start with dark', () => {
    expect(names('<div className="flex data-[theme=dark]:p-4 p-2" />')).toEqual([]);
    expect(names('const s = "dark: a heading in prose";', 'ts')).toEqual([]);
  });

  it('flags a bare ring width with no ring colour in the same class list', () => {
    expect(names('<div className="flex ring p-4" />')).toEqual(['ring']);
    expect(names('<div className="flex ring-2 p-4" />')).toEqual(['ring-2']);
    expect(names('<div className="flex focus:ring-4 p-4" />')).toEqual(['focus:ring-4']);
  });

  it('accepts a ring width paired with a preset ring colour, and ring-0', () => {
    expect(names('<div className="flex ring-2 ring-beacon-600 p-4" />')).toEqual([]);
    expect(names('<div className="flex ring-2 ring-inset ring-offset-2 ring-beacon-600" />')).toEqual([]);
    expect(names('<div className="flex ring-0 p-4" />')).toEqual([]);
    expect(names('<div className="flex ring-inset p-4" />')).toEqual([]);
  });

  it('flags a bare border width with no border colour in the same class list', () => {
    expect(names('<div className="flex border p-4" />')).toEqual(['border']);
    expect(names('<div className="flex border-b-2 p-4" />')).toEqual(['border-b-2']);
    expect(names('<div className="flex border-t p-4" />')).toEqual(['border-t']);
    expect(names('<div className="flex border-2 border-dashed p-4" />')).toEqual(['border-2']);
  });

  it('accepts a border width paired with a preset border colour', () => {
    expect(names('<div className="flex border border-line-hairline p-4" />')).toEqual([]);
    expect(names('<div className="flex border-b-2 border-b-line-ink p-4" />')).toEqual([]);
    expect(names('<div className="flex border border-transparent p-4" />')).toEqual([]);
    expect(names('<div className="flex border-0 border-solid p-4" />')).toEqual([]);
  });

  it('does not treat prose containing "border" or "ring" as classes', () => {
    expect(names('const s = "a thin border around the ring";', 'ts')).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// The tree.
// ---------------------------------------------------------------------------

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, '..', '..', '..', '..');
const SKIP_DIRS = new Set(['node_modules', 'dist', '.next', 'dist-styleguide']);
const SCANNER_FILES = new Set(['stockClasses.ts', 'stockClasses.test.ts']);

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walk(full));
    else if (/\.(ts|tsx|css)$/.test(name) && !/\.test\./.test(name) && !SCANNER_FILES.has(name)) {
      out.push(full);
    }
  }
  return out;
}

const TREES = [
  'apps/web/src',
  ...readdirSync(join(ROOT, 'packages'))
    .filter((p) => statSync(join(ROOT, 'packages', p)).isDirectory())
    .map((p) => `packages/${p}/src`),
];

describe('no stock Tailwind class the preset removed appears in shipped source', () => {
  for (const tree of TREES) {
    it(`${tree} is clean`, () => {
      const findings: string[] = [];
      for (const file of walk(join(ROOT, tree))) {
        const kind = file.endsWith('.css') ? 'css' : 'tsx';
        for (const f of findStockClasses(readFileSync(file, 'utf8'), kind)) {
          findings.push(`${relative(ROOT, file)}:${f.line}: ${f.className} (${f.reason})`);
        }
      }
      expect(findings).toEqual([]);
    });
  }

  it('walks a non-trivial number of files (the scan is not vacuous)', () => {
    const n = TREES.reduce((sum, t) => sum + walk(join(ROOT, t)).length, 0);
    expect(n).toBeGreaterThan(50);
  });
});

/**
 * Tokens live in the URL fragment: /share#…, /invite#…, /reset-password#….
 *
 * A fragment is never sent to a server and never appears in a Referer. The
 * old path forms (/share/{token} …) stay as redirects for links already sent.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import { parseFragment } from '@/lib/fragmentToken';
import { FragmentRedirect } from '@/components/routes/FragmentRedirect';
import SharePage from './share/page';
import InvitePage from './invite/page';
import ResetPage from './reset-password/page';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const SRC = join(HERE, '..');
const read = (rel: string): string => readFileSync(join(HERE, rel), 'utf8');

const ROUTES = [
  { dir: 'share', path: '/share', clears: false },
  { dir: 'invite', path: '/invite', clears: true },
  { dir: 'reset-password', path: '/reset-password', clears: true },
] as const;

describe('parseFragment', () => {
  it('trims the # and decodes', () => {
    expect(parseFragment('#abc%20d_-')).toBe('abc d_-');
  });
  it('is empty for no fragment, a bare #, or a malformed escape', () => {
    expect(parseFragment('')).toBe('');
    expect(parseFragment('#')).toBe('');
    expect(parseFragment('#%E0%A4%A')).toBe('');
  });
});

describe('the fragment pages', () => {
  it.each([SharePage, InvitePage, ResetPage])(
    'server render is a neutral loading state, with no form and no error',
    (Page) => {
      const html = renderToStaticMarkup(<Page />);
      expect(html).toMatch(/Loading|Opening/);
      expect(html).not.toContain('<form');
      expect(html).not.toContain('not valid');
      expect(html).not.toContain('not available');
    },
  );

  it.each(ROUTES)('$path reads the token from the fragment, not from params', ({ dir, clears }) => {
    const src = read(`${dir}/page.tsx`);
    expect(src).toContain(`useFragmentToken(${clears})`);
    expect(src).not.toContain('params');
  });

  it('the hook reads window.location.hash and clears it only when asked', () => {
    const src = readFileSync(join(SRC, 'lib/fragmentToken.ts'), 'utf8');
    expect(src).toContain('window.location.hash');
    // null state: Next's patched replaceState would otherwise keep #<token> in its router.
    expect(src).toContain(
      "window.history.replaceState(null, '', window.location.pathname + window.location.search);",
    );
    expect(src).not.toContain('window.history.state');
    expect(src).toMatch(/if \(clear && /);
  });

  it('a kept fragment follows hashchange, and the listener is removed', () => {
    const src = readFileSync(join(SRC, 'lib/fragmentToken.ts'), 'utf8');
    expect(src).toContain("window.addEventListener('hashchange', onHashChange)");
    expect(src).toContain("window.removeEventListener('hashchange', onHashChange)");
    // Only the kept-fragment (share) form listens; a cleared one has nothing to follow.
    expect(src).toMatch(/if \(clear\) return undefined;[\s\S]*addEventListener/);
  });

  it("an empty fragment shows the flow's not-available state, derived while rendering", () => {
    expect(read('share/page.tsx')).toContain("token === ''");
    for (const dir of ['invite', 'reset-password']) {
      const src = read(`${dir}/page.tsx`);
      expect(src).toContain("token === '' ? { kind: 'invalid', detail: NO_CODE } : formState");
      // No effect switches state after the first paint.
      expect(src).not.toContain('useEffect');
      expect(src).toContain('Open the link from your email again.');
      expect(src).not.toContain('The link is incomplete');
    }
  });
});

describe('the old path routes redirect and never fetch', () => {
  it.each(ROUTES)('/$dir/[token] forwards to $path#…', ({ dir, path }) => {
    const src = read(`${dir}/[token]/page.tsx`);
    expect(src).toContain(`<FragmentRedirect path="${path}" token={token} />`);
    expect(src).not.toMatch(/\bapi\b/);
    expect(src).not.toContain('fetch(');
  });

  it('FragmentRedirect always renders a plain link to the fragment form, and a noscript note', () => {
    const html = renderToStaticMarkup(<FragmentRedirect path="/share" token="a b/c" />);
    expect(html).toContain('href="/share#a%20b%2Fc"');
    expect(html).toContain('Open the link');
    expect(html).toContain('<noscript>');
    expect(html).toContain('needs JavaScript');
  });

  it('FragmentRedirect uses location.replace with the encoded token', () => {
    const src = readFileSync(join(SRC, 'components/routes/FragmentRedirect.tsx'), 'utf8');
    expect(src).toContain('window.location.replace(`${path}#${encodeURIComponent(token)}`)');
    expect(src).not.toContain('fetch(');
    expect(src).not.toMatch(/\bapi\b/);
  });
});

describe('no web source builds a token path link', () => {
  function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) return sources(p);
      return /\.(ts|tsx)$/.test(name) && name !== 'fragmentRoutes.test.tsx' ? [p] : [];
    });
  }

  it('no template literal starts /share/${, /invite/${ or /reset-password/${', () => {
    const offenders = sources(SRC).filter((f) =>
      /(share|invite|reset-password)\/\$\{/.test(readFileSync(f, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('no /reports/${ URL either', () => {
    const offenders = sources(SRC).filter((f) => /\/reports\/\$\{/.test(readFileSync(f, 'utf8')));
    expect(offenders).toEqual([]);
  });
});

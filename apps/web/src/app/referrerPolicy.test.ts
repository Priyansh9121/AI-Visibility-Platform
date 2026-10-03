/**
 * No Referer from a page whose address is a credential — 2026-09-29.
 *
 * The public report at /share#{token} links out to every source it cites and
 * shows an agency's logo from wherever that agency keeps it. `next.config.mjs`
 * sends `Referrer-Policy: no-referrer` with it, so none of those hosts is
 * handed the report's address — and with the other pages whose address is a
 * live credential. The routes are found on disk, so a new `[token]` route
 * without the header fails here. (The header as a real `next start` sends it
 * is in the build log's 2026-09-29 entry.)
 */

import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { getPathMatch } from 'next/dist/shared/lib/router/utils/path-match';
import nextConfig from '../../next.config.mjs';

const APP = fileURLToPath(new URL('.', import.meta.url));
const NO_REFERRER = { key: 'Referrer-Policy', value: 'no-referrer' };
/**
 * Exactly this, never a substring match: since founder decision 3 (2026-10-01)
 * robots.txt disallows nothing, so this header is the only thing keeping a
 * credential-address page out of search. `googlebot: noindex` must not pass.
 */
const NOINDEX_NOFOLLOW = 'noindex, nofollow';

/**
 * Every page under app/ with a `[token]` segment at it or above it, as a Next path
 * pattern: `/share/:token` and, should one ever exist, `/share/:token/print`. A page
 * nested under a token carries the same token in its address, so it is a credential
 * page as much as the one at the token. `dir` and `root` are for the synthetic tree
 * below; by default this reads the real app.
 */
function tokenRoutes(dir: string = APP, root: string = APP): string[] {
  const found: string[] = [];
  const segments = relative(root, dir)
    .split(sep)
    .filter((s) => s !== '' && !/^\(.*\)$/.test(s)); // a route group is no part of the address
  if (segments.includes('[token]') && existsSync(join(dir, 'page.tsx'))) {
    found.push(`/${segments.join('/').replace(/\[token\]/g, ':token')}`);
  }
  for (const name of readdirSync(dir)) {
    if (statSync(join(dir, name)).isDirectory()) found.push(...tokenRoutes(join(dir, name), root));
  }
  return found;
}

async function rulesFor(source: string) {
  const rules = (await nextConfig.headers?.()) ?? [];
  return rules.filter((rule) => rule.source === source).flatMap((rule) => rule.headers);
}

describe('pages whose address is a credential send no Referer', () => {
  it('the public report does', async () => {
    expect(await rulesFor('/share/:token')).toContainEqual(NO_REFERRER);
  });

  it('finds the token routes on disk, so passing means something', () => {
    expect(tokenRoutes().sort()).toEqual(['/invite/:token', '/reset-password/:token', '/share/:token']);
  });

  // The fragment routes are the live form. A fragment never reaches a server,
  // but the page still links out and loads fonts and logos.
  it.each(['/share', '/invite', '/reset-password'])('the fragment route %s does, and exists on disk', async (source) => {
    expect(existsSync(join(APP, source.slice(1), 'page.tsx'))).toBe(true);
    expect(await rulesFor(source)).toContainEqual(NO_REFERRER);
  });

  it.each([...tokenRoutes(), '/sign-up/google'])('%s does', async (source) => {
    // /sign-up/google carries an unfinished Google sign-up (its ticket is in the fragment).
    expect(await rulesFor(source)).toContainEqual(NO_REFERRER);
  });
});

/**
 * A page BELOW a `[token]` segment is a credential page too: `/share/[token]/print`
 * would carry the token in its address as `/share/[token]` does. The rules name each
 * page by its exact path, so a page nobody wrote an entry for gets no `no-referrer`.
 * `tokenRoutes` finds such pages at any depth, so the tests above fail for one until it
 * has its entry. None exists today, so that is proved here on a synthetic app tree.
 */
describe('a page nested under a token page is a credential page too', () => {
  let tree: string;

  beforeAll(() => {
    tree = mkdtempSync(join(tmpdir(), 'avp-app-'));
    for (const dir of [
      'share/[token]',
      'share/[token]/print',
      'invite/[token]/step/two', // nothing at the token itself, two levels below it
      '(legal)/reset-password/[token]/print', // a route group is no part of the address
      'clients/[clientId]',
      'clients/[clientId]/alerts',
    ]) {
      mkdirSync(join(tree, dir), { recursive: true });
      writeFileSync(join(tree, dir, 'page.tsx'), '');
    }
  });
  afterAll(() => rmSync(tree, { recursive: true, force: true }));

  type Entry = { source: string; headers: { key: string; value: string }[] };
  const entry = (source: string): Entry => ({
    source,
    headers: [NO_REFERRER, { key: 'X-Robots-Tag', value: NOINDEX_NOFOLLOW }],
  });
  /** The routes that no entry names with a `no-referrer`, which is what `rulesFor` finds for each. */
  const withoutNoReferrer = (routes: string[], entries: Entry[]) =>
    routes.filter(
      (route) =>
        !entries
          .filter((e) => e.source === route)
          .flatMap((e) => e.headers)
          .some((h) => h.key === NO_REFERRER.key && h.value === NO_REFERRER.value),
    );

  it('finds a page at any depth below a token, through a route group, and no other page', () => {
    expect(tokenRoutes(tree, tree).sort()).toEqual([
      '/invite/:token/step/two',
      '/reset-password/:token/print',
      '/share/:token',
      '/share/:token/print',
    ]);
  });

  it('flags every one that has no entry of its own', () => {
    // An entry for the page at the token (`/share/:token`) does not reach the one below it.
    expect(withoutNoReferrer(tokenRoutes(tree, tree), [entry('/share/:token')]).sort()).toEqual([
      '/invite/:token/step/two',
      '/reset-password/:token/print',
      '/share/:token/print',
    ]);
  });

  it('stops flagging them once each has its entry', () => {
    const entries = tokenRoutes(tree, tree).map(entry);
    expect(withoutNoReferrer(tokenRoutes(tree, tree), entries)).toEqual([]);
  });
});

/**
 * Same routes, same reason: a page whose address is a credential is not for a
 * search index. One list in next.config.mjs sends both headers.
 */
describe('pages whose address is a credential are kept out of search', () => {
  const tokenPages = [
    ...tokenRoutes(),
    '/share',
    '/invite',
    '/reset-password',
    '/sign-up/google',
  ];

  it.each(tokenPages)('%s sends X-Robots-Tag: noindex and no-referrer', async (source) => {
    const headers = await rulesFor(source);
    expect(headers).toContainEqual(NO_REFERRER);
    const robots = headers.filter((h) => h.key === 'X-Robots-Tag');
    expect(robots.length).toBeGreaterThan(0);
    for (const h of robots) expect(h.value).toBe(NOINDEX_NOFOLLOW);
  });

  it('every rule that sends no-referrer also sends noindex', async () => {
    const rules = (await nextConfig.headers?.()) ?? [];
    const noReferrer = rules.filter((r) => r.headers.some((h) => h.key === 'Referrer-Policy' && h.value === 'no-referrer'));
    expect(noReferrer.length).toBeGreaterThan(0);
    const missing = noReferrer
      .filter((r) => !r.headers.some((h) => h.key === 'X-Robots-Tag' && h.value === NOINDEX_NOFOLLOW))
      .map((r) => r.source);
    expect(missing).toEqual([]);
  });

  // Match concrete URLs the way Next does (strict path-to-regexp), so a
  // site-wide or pattern rule such as '/((?!_next).*)' cannot noindex a public
  // page and slip past an exact-string comparison of `source`.
  async function headersSentFor(url: string) {
    const rules = (await nextConfig.headers?.()) ?? [];
    return rules
      .filter((rule) => getPathMatch(rule.source, { strict: true, removeUnnamedParams: true })(url) !== false)
      .flatMap((rule) => rule.headers);
  }

  it.each(['/', '/crawler', '/privacy', '/terms'])('the public page %s gets no X-Robots-Tag from any rule', async (url) => {
    expect((await headersSentFor(url)).filter((h) => h.key === 'X-Robots-Tag')).toEqual([]);
  });

  it.each([
    '/share',
    '/invite',
    '/reset-password',
    '/share/abc123',
    '/invite/abc123',
    '/reset-password/abc123',
    '/sign-up/google',
  ])('the concrete URL %s gets noindex and no-referrer', async (url) => {
    const headers = await headersSentFor(url);
    expect(headers).toContainEqual(NO_REFERRER);
    const robots = headers.filter((h) => h.key === 'X-Robots-Tag');
    expect(robots.length).toBeGreaterThan(0);
    for (const h of robots) expect(h.value).toBe(NOINDEX_NOFOLLOW);
  });
});

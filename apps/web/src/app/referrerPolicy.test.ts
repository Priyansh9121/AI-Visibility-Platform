/**
 * No Referer from a page whose address is a credential — 2026-09-29.
 *
 * The public report at /share/{token} links out to every source it cites and
 * shows an agency's logo from wherever that agency keeps it. `next.config.mjs`
 * sends `Referrer-Policy: no-referrer` with it, so none of those hosts is
 * handed the report's address — and with the other pages whose address is a
 * live credential. The routes are found on disk, so a new `[token]` route
 * without the header fails here. (The header as a real `next start` sends it
 * is in the build log's 2026-09-29 entry.)
 */

import { describe, it, expect } from 'vitest';
import { existsSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import nextConfig from '../../next.config.mjs';

const APP = fileURLToPath(new URL('.', import.meta.url));
const NO_REFERRER = { key: 'Referrer-Policy', value: 'no-referrer' };

/** Every page under app/ with a `[token]` segment, as a Next path pattern. */
function tokenRoutes(dir: string = APP): string[] {
  const found: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (!statSync(path).isDirectory()) continue;
    if (name === '[token]' && existsSync(join(path, 'page.tsx'))) {
      found.push(`/${relative(APP, path).split(sep).join('/').replace('[token]', ':token')}`);
    }
    found.push(...tokenRoutes(path));
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

  it.each([...tokenRoutes(), '/sign-up/google'])('%s does', async (source) => {
    // /sign-up/google carries an unfinished Google sign-up's ticket in its query.
    expect(await rulesFor(source)).toContainEqual(NO_REFERRER);
  });
});

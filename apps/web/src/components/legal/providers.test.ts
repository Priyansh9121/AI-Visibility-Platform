/**
 * The privacy page names every provider the code reaches — 2026-09-29.
 *
 * Not a list kept by hand and trusted. This re-runs the grep the list was
 * built from — every `http(s)://` host in the four source trees, and every
 * runtime dependency in the manifests — on every run, and holds the result
 * against `__fixtures__/outboundProviders.ts`:
 *
 *   - a host or dependency the fixture has not classified fails, so a new
 *     provider cannot arrive without somebody deciding what it is;
 *   - a classified one that the code no longer shows fails, so the fixture
 *     stays exactly what the grep finds rather than a list that only grows;
 *   - a provider the privacy page does not name fails.
 *
 * Reads the repository from disk, so it runs in CI's web job, which checks out
 * the whole tree.
 */

import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { PrivacyPolicy } from './PrivacyPolicy';
import {
  NOT_PROVIDER_DEPENDENCIES,
  NOT_PROVIDER_HOSTS,
  PROVIDERS,
} from './__fixtures__/outboundProviders';

const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));

/** What is scanned: the code that runs, not its tests or their fixtures. */
const TREES: readonly { dir: string; exts: readonly string[] }[] = [
  { dir: 'apps/api/src', exts: ['.py'] },
  { dir: 'apps/workers/src', exts: ['.py'] },
  { dir: 'apps/web/src', exts: ['.ts', '.tsx', '.css'] },
  { dir: 'packages/design-system/src', exts: ['.ts', '.tsx', '.css'] },
];
const LOOSE_FILES = ['apps/web/next.config.mjs'];
const SKIP = /\.test\.|__fixtures__|__harness__|__pycache__|node_modules/;

const PYTHON_MANIFESTS = ['apps/api/pyproject.toml', 'apps/workers/pyproject.toml'];
const NPM_MANIFESTS = [
  'apps/web/package.json',
  'packages/design-system/package.json',
  'packages/shared-types/package.json',
];

function walk(dir: string, exts: readonly string[]): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (SKIP.test(path)) continue;
    if (statSync(path).isDirectory()) out.push(...walk(path, exts));
    else if (exts.some((ext) => name.endsWith(ext))) out.push(path);
  }
  return out;
}

const FILES = [
  ...TREES.flatMap(({ dir, exts }) => walk(join(ROOT, dir), exts)),
  ...LOOSE_FILES.map((file) => join(ROOT, file)),
];

/** Every literal host, with the files it appears in. A dynamic `https://{…}` matches nothing. */
function hostsInSource(): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const file of FILES) {
    for (const match of readFileSync(file, 'utf8').matchAll(/https?:\/\/([a-z0-9-]+(?:\.[a-z0-9-]+)*)/gi)) {
      const host = match[1]!.toLowerCase();
      found.set(host, [...(found.get(host) ?? []), relative(ROOT, file)]);
    }
  }
  return found;
}

function pythonDependencies(manifest: string): string[] {
  const text = readFileSync(join(ROOT, manifest), 'utf8');
  const block = /^dependencies = \[([\s\S]*?)^\]/m.exec(text)?.[1] ?? '';
  return [...block.matchAll(/^\s*"([A-Za-z0-9_.-]+)/gm)].map((m) => m[1]!.toLowerCase());
}

function npmDependencies(manifest: string): string[] {
  const json = JSON.parse(readFileSync(join(ROOT, manifest), 'utf8')) as {
    dependencies?: Record<string, string>;
  };
  return Object.keys(json.dependencies ?? {});
}

const HOSTS = hostsInSource();
const DEPENDENCIES = new Set([
  ...PYTHON_MANIFESTS.flatMap(pythonDependencies),
  ...NPM_MANIFESTS.flatMap(npmDependencies),
]);
const PROVIDER_HOSTS = new Set(PROVIDERS.flatMap((p) => p.hosts ?? []));
const PROVIDER_DEPENDENCIES = new Set(PROVIDERS.flatMap((p) => p.dependencies ?? []));

/** The page as a reader sees it: tags and React's text-node separators gone. */
const PAGE = renderToStaticMarkup(createElement(PrivacyPolicy))
  .replace(/<!--.*?-->/g, '')
  .replace(/<[^>]+>/g, ' ')
  .replace(/&amp;/g, '&')
  .replace(/\s+/g, ' ');

describe('the grep finds what it should, so passing means something', () => {
  it('scans the code that runs, in all four trees', () => {
    expect(FILES.length).toBeGreaterThan(150);
    for (const { dir } of TREES) {
      expect(FILES.some((file) => relative(ROOT, file).startsWith(dir)), dir).toBe(true);
    }
  });

  it('sees hosts and dependencies known to be there', () => {
    expect(HOSTS.has('api.openai.com')).toBe(true);
    expect(HOSTS.has('fonts.googleapis.com')).toBe(true);
    expect(DEPENDENCIES.has('anthropic')).toBe(true);
    expect(DEPENDENCIES.has('next')).toBe(true);
  });
});

describe('every outside party the code reaches is classified', () => {
  it('every host in the source is a named provider or classified as not one', () => {
    const unclassified = [...HOSTS]
      .filter(([host]) => !PROVIDER_HOSTS.has(host) && !NOT_PROVIDER_HOSTS.includes(host))
      .map(([host, files]) => `${host} (${files.join(', ')})`);
    expect(
      unclassified,
      'a new outside host — classify it in __fixtures__/outboundProviders.ts, and if it is a ' +
        'provider, name it on the privacy page',
    ).toEqual([]);
  });

  it('every runtime dependency is a named provider’s library or classified as not one', () => {
    const unclassified = [...DEPENDENCIES].filter(
      (dep) => !PROVIDER_DEPENDENCIES.has(dep) && !NOT_PROVIDER_DEPENDENCIES.includes(dep),
    );
    expect(
      unclassified,
      'a new runtime dependency — classify it in __fixtures__/outboundProviders.ts',
    ).toEqual([]);
  });

  it('classifies nothing the grep no longer finds, so the fixture is the grep and not a wish list', () => {
    expect(NOT_PROVIDER_HOSTS.filter((host) => !HOSTS.has(host))).toEqual([]);
    expect(NOT_PROVIDER_DEPENDENCIES.filter((dep) => !DEPENDENCIES.has(dep))).toEqual([]);
    expect(
      NOT_PROVIDER_HOSTS.filter((host) => PROVIDER_HOSTS.has(host)),
      'a host classified both ways',
    ).toEqual([]);
  });
});

describe('the privacy page names every provider, and only ones the code reaches', () => {
  it.each(PROVIDERS.map((p) => [p.pageName, p] as const))('names %s', (name) => {
    expect(PAGE).toContain(name);
  });

  it.each(PROVIDERS.filter((p) => p.deployment === undefined).map((p) => [p.pageName, p] as const))(
    '%s is really reached by the code',
    (_name, provider) => {
      const byHost = (provider.hosts ?? []).some((host) => HOSTS.has(host));
      const byDependency = (provider.dependencies ?? []).some((dep) => DEPENDENCIES.has(dep));
      const byEvidence =
        provider.evidence !== undefined &&
        provider.evidence.pattern.test(readFileSync(join(ROOT, provider.evidence.file), 'utf8'));
      expect(byHost || byDependency || byEvidence).toBe(true);
    },
  );

  it('says, for each provider the code cannot show, where the knowledge comes from', () => {
    const deployment = PROVIDERS.filter((p) => p.deployment !== undefined);
    expect(deployment.map((p) => p.pageName).sort()).toEqual(['Render', 'Supabase']);
    for (const provider of deployment) expect(provider.deployment!.length).toBeGreaterThan(40);
  });
});

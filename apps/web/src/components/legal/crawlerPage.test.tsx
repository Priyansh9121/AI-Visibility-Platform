/**
 * The public /crawler page — 2026-09-29.
 *
 * The crawler's user agent points site owners here, so the page has to be the
 * truth about the crawler. What these tests hold:
 *
 *   - the user agent it prints is the one the crawler sends — the template
 *     and path are read out of the API's `crawl.py`, not copied, and the page
 *     is rendered and its printed string compared with them;
 *   - every other fact it states is read back from the API's source the same
 *     way (`legalPages.test.tsx`'s pattern), including the two negatives: it
 *     does not obey robots.txt, and nothing runs it on a schedule;
 *   - no placeholder address is left anywhere in apps/.
 *
 * Signed-out rendering, mounted with effects, is in `app/legalRoutes.test.tsx`.
 */

import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { LandingView } from '@/components/marketing/LandingView';
import { CRAWLER, crawlerUserAgent, requestOrigin } from '@/lib/legal/crawler';
import { OPERATOR } from '@/lib/legal/operator';
import { RETENTION } from '@/lib/legal/retention';
import { CrawlerInfo } from './CrawlerInfo';

const ROOT = fileURLToPath(new URL('../../../../../', import.meta.url));
const read = (path: string): string => readFileSync(join(ROOT, path), 'utf8');

const CRAWL = 'apps/api/src/avp_api/services/crawl.py';
const AUDIT = 'apps/api/src/avp_api/services/technical_audit.py';
const AUDIT_RUNNER = 'apps/api/src/avp_api/services/audit_runner.py';

const ORIGIN = 'https://ai-visibility-platform-1.onrender.com';

function page(origin: string): { html: string; text: string } {
  const html = renderToStaticMarkup(createElement(CrawlerInfo, { origin }));
  const text = html
    .replace(/<!--.*?-->/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ');
  return { html, text };
}

const CRAWLER_PAGE = page(ORIGIN);

/** A one-line string constant in the API's source, exactly as written there. */
function pythonString(name: string): string {
  const value = new RegExp(`^${name} = "([^"\\n]*)"$`, 'm').exec(read(CRAWL))?.[1];
  expect(value, `${name} is not a one-line string in ${CRAWL}`).toBeDefined();
  return value!;
}

/** The printed user agent, as the page renders it. */
function printed(html: string): string | undefined {
  return /<code data-user-agent="">([^<]*)<\/code>/.exec(html)?.[1];
}

describe('the user agent on the page is the one the crawler sends', () => {
  it('the template and the page’s path are the crawler’s own, read from crawl.py', () => {
    expect(CRAWLER.userAgentTemplate).toBe(pythonString('USER_AGENT_TEMPLATE'));
    expect(CRAWLER.infoPath).toBe(pythonString('CRAWLER_INFO_PATH'));
    expect(CRAWLER.userAgentTemplate).toContain(`(compatible; ${CRAWLER.name}/`);
  });

  it('the page prints it filled in with its own address, as crawl.py fills it', () => {
    // What `USER_AGENT_TEMPLATE.format(info_url=f"{base}{CRAWLER_INFO_PATH}")`
    // builds when PUBLIC_WEB_BASE_URL is this origin.
    const python = pythonString('USER_AGENT_TEMPLATE').replace(
      '{info_url}',
      `${ORIGIN}${pythonString('CRAWLER_INFO_PATH')}`,
    );
    expect(printed(CRAWLER_PAGE.html)).toBe(python);
    expect(printed(CRAWLER_PAGE.html)).toBe(crawlerUserAgent(ORIGIN));
    expect(crawlerUserAgent(`${ORIGIN}/`)).toBe(python);
  });

  it('follows the address it is served at, so a new domain needs no change', () => {
    expect(printed(page('https://visibility.example.com').html)).toContain(
      '+https://visibility.example.com/crawler)',
    );
  });

  it('reads that address from the request as Render’s proxy and a local server send it', () => {
    const headers = (entries: Record<string, string>) => new Headers(entries);
    expect(
      requestOrigin(headers({ host: 'ai-visibility-platform-1.onrender.com', 'x-forwarded-proto': 'https' })),
    ).toBe(ORIGIN);
    expect(requestOrigin(headers({ host: 'localhost:3000' }))).toBe('http://localhost:3000');
    expect(
      requestOrigin(
        headers({ host: 'internal:10000', 'x-forwarded-host': 'a.example, b.example', 'x-forwarded-proto': 'https,http' }),
      ),
    ).toBe('https://a.example');
  });
});

describe('what it fetches is what the code fetches', () => {
  it('when a site is added: the homepage and the linked pages crawl.py looks for', () => {
    const tuple = /^CANDIDATE_PATHS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)$/m.exec(read(CRAWL))?.[1];
    expect(tuple).toBeDefined();
    expect([...tuple!.matchAll(/"([^"]+)"/g)].map((m) => m[1])).toEqual([...CRAWLER.candidatePaths]);

    const paths = CRAWLER.candidatePaths;
    expect(CRAWLER_PAGE.text).toContain(`${paths.slice(0, -1).join(', ')} or ${paths.at(-1)}`);
    expect(CRAWLER_PAGE.text).toContain(`up to ${RETENTION.crawlPages - 1} more pages on the same site`);
    expect(CRAWLER_PAGE.text).toContain(`at most ${RETENTION.crawlPages} pages in all`);
    // The crawl blocks images, fonts and video at the router.
    expect(read(CRAWL)).toMatch(/\\\.\(png\|jpe\?g\|gif\|webp\|svg\|ico\|woff2\?\|ttf\|mp4\|webm\)\$/);
    expect(CRAWLER_PAGE.text).toContain('It does not download their images, fonts or video.');
  });

  it('with each scan: robots.txt, the sitemap unless robots names one, and the homepage once', () => {
    const audit = read(AUDIT);
    expect(audit).toContain('await client.get(f"{base}/robots.txt")');
    expect(audit).toContain('if not found.sitemap_present:');
    expect(audit).toContain('await client.get(f"{base}/sitemap.xml")');
    const runner = read(AUDIT_RUNNER);
    expect(runner).toContain('signals = await audit_site(f"https://{client.domain}")');
    expect(runner).toContain('row.pages_crawled = 1 if signals.ok else 0');
    expect(CRAWLER_PAGE.text).toContain(
      'It requests /robots.txt, and /sitemap.xml unless robots.txt names a sitemap',
    );
    expect(CRAWLER_PAGE.text).toContain('That is one page per scan.');
  });

  it('says it does not obey robots.txt — and fails the day it starts to', () => {
    expect(CRAWLER_PAGE.text).toContain('It does not obey robots.txt.');
    expect(CRAWLER_PAGE.text).not.toMatch(/(respects|honou?rs|obeys|follows) robots\.txt/i);
    // Tripwires: the crawl never fetches or parses robots.txt, and in the
    // audit nothing about robots.txt decides whether a page is loaded.
    expect(read(CRAWL)).not.toMatch(/\/robots\.txt["'`]|robotparser|can_fetch|evaluate_robots/);
    const auditSite = /^async def audit_site\([\s\S]*?(?=^\S)/m.exec(read(AUDIT))?.[0];
    expect(auditSite).toBeDefined();
    expect(auditSite).toContain('page.goto(');
    expect(auditSite).not.toMatch(/^\s*(if|elif|while)\b[^\n]*robots/m);
  });

  it('says nothing runs it on a schedule, because nothing does', () => {
    const workers = join(ROOT, 'apps/workers/src');
    const python = (dir: string): string[] =>
      readdirSync(dir).flatMap((name) => {
        const path = join(dir, name);
        if (name === '__pycache__') return [];
        return statSync(path).isDirectory() ? python(path) : name.endsWith('.py') ? [path] : [];
      });
    for (const file of python(workers)) {
      expect(readFileSync(file, 'utf8'), relative(ROOT, file)).not.toMatch(/beat_schedule|crontab/);
    }
    expect(CRAWLER_PAGE.text).toContain('It has no schedule.');
  });
});

describe('the page itself', () => {
  it('one h1, the shared header, and the footer linking all three public pages', () => {
    expect(CRAWLER_PAGE.html.match(/<h1[\s>]/g)).toHaveLength(1);
    expect(CRAWLER_PAGE.html).toContain(
      `<h1 class="avp-section__heading">What ${CRAWLER.name} does on a website.</h1>`,
    );
    for (const href of ['/', '/privacy', '/terms', '/crawler']) {
      expect(CRAWLER_PAGE.html).toContain(`href="${href}"`);
    }
  });

  it('names who runs it and a working address', () => {
    expect(CRAWLER_PAGE.text).toContain(
      `${CRAWLER.name} is run by ${OPERATOR.name}, ${OPERATOR.trading} in ${OPERATOR.city}`,
    );
    expect(CRAWLER_PAGE.html).toContain(`href="mailto:${OPERATOR.email}"`);
  });

  it('carries no TODO', () => {
    expect(CRAWLER_PAGE.html).not.toMatch(/TODO|founder\)/i);
  });

  it('nothing it is built from can reach the session API', () => {
    for (const file of [
      'apps/web/src/app/crawler/page.tsx',
      'apps/web/src/components/legal/CrawlerInfo.tsx',
      'apps/web/src/lib/legal/crawler.ts',
    ]) {
      expect(read(file), file).not.toMatch(/from '@\/lib\/api'/);
    }
  });

  it('the landing page’s footer links it', () => {
    const html = renderToStaticMarkup(createElement(LandingView, { animate: false }));
    expect(html.slice(html.indexOf('<footer'))).toContain('href="/crawler"');
  });
});

describe('no placeholder address is left anywhere in apps/', () => {
  // `.invalid` is the top-level domain reserved for addresses that must never
  // resolve — the placeholder the user agent carried until 2026-09-29.
  const SKIP_DIRS = new Set([
    'node_modules', '.next', '.venv', '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', 'dist', 'dist-styleguide',
  ]);

  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      if (SKIP_DIRS.has(name) || name === '.env' || name.endsWith('.tsbuildinfo')) return [];
      const path = join(dir, name);
      return statSync(path).isDirectory() ? files(path) : [path];
    });
  }

  it('no address in apps/ is on a .invalid host', () => {
    const all = files(join(ROOT, 'apps'));
    // Passing means something: all three apps, source, tests and scripts.
    expect(all.length).toBeGreaterThan(300);
    for (const known of [CRAWL, 'apps/api/scripts/verify_google_live.py', 'apps/workers/pyproject.toml']) {
      expect(all.map((file) => relative(ROOT, file))).toContain(known);
    }
    const placeholder = /https?:\/\/[a-z0-9.-]+\.invalid\b/i;
    const hits = all
      .filter((file) => {
        const bytes = readFileSync(file);
        return !bytes.subarray(0, 1024).includes(0) && placeholder.test(bytes.toString('utf8'));
      })
      .map((file) => relative(ROOT, file));
    expect(hits).toEqual([]);
  });
});

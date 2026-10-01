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
 *     way (`legalPages.test.tsx`'s pattern): that it obeys robots.txt — a
 *     check before every fetch, required in the source since 2026-09-29 —
 *     and that nothing runs it on a schedule;
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
import { ROBOTS_DISALLOWED } from '@/lib/report/strings';
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

/** One top-level Python function's source: from its `def` to the next top-level statement. */
function topLevel(source: string, name: string): string {
  const start = source.search(new RegExp(`^(?:async )?def ${name}\\(`, 'm'));
  expect(start, `def ${name} not found`).toBeGreaterThan(-1);
  const rest = source.slice(start);
  const end = rest.search(/\n(?:async def |def |class |[A-Za-z_][A-Za-z0-9_]* = |@|# =)/);
  return end === -1 ? rest : rest.slice(0, end);
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
    expect(audit).toContain('robots = await gate.file(base)');
    expect(audit).toContain('if not found.sitemap_present:');
    expect(audit).toContain('await gate.follow("GET", f"{base}/sitemap.xml")');
    const runner = read(AUDIT_RUNNER);
    expect(runner).toContain('signals = await audit_site(f"https://{client.domain}")');
    expect(runner).toContain('row.pages_crawled = 1 if signals.ok else 0');
    expect(CRAWLER_PAGE.text).toContain(
      'It requests /robots.txt, and /sitemap.xml unless robots.txt names a sitemap',
    );
    expect(CRAWLER_PAGE.text).toContain('That is one page per scan.');
  });

  it('says it obeys robots.txt, with the token the parser matches and an example to paste', () => {
    expect(CRAWLER_PAGE.text).toContain('It obeys robots.txt.');
    expect(CRAWLER_PAGE.text).not.toMatch(/does not obey/i);
    // The group it follows is named by the token crawl.py hands the parser.
    expect(CRAWLER.name).toBe(pythonString('CRAWLER_TOKEN'));
    expect(CRAWLER_PAGE.text).toContain(`follows the group that names ${CRAWLER.name}`);
    expect(CRAWLER_PAGE.html).toContain(
      `<code data-robots-example="">User-agent: ${CRAWLER.name}\nDisallow: /</code>`,
    );
    // The two sentences the brief asked for, and the rest of the rules.
    expect(CRAWLER_PAGE.text).toContain('are not checked one by one');
    expect(CRAWLER_PAGE.text).toContain(
      `It ignores Crawl-delay: it fetches at most ${RETENTION.crawlPages} pages in a run`,
    );
    expect(CRAWLER_PAGE.text).toContain('a 404, a 410 or another 4xx answer), everything is allowed');
    expect(CRAWLER_PAGE.text).toContain(
      'answers with an error (5xx), asks it to slow down (429), or does not answer in time, it fetches nothing',
    );
    // ...which is what the parser does with a status (ai_crawlers.crawl_policy).
    const policy = read('apps/api/src/avp_api/services/ai_crawlers.py');
    expect(policy).toMatch(
      /if status is None or status == 429 or status >= 500:\n\s+return CrawlPolicy\(PolicyBasis\.UNREACHABLE\)/,
    );
    expect(policy).toMatch(
      /if 400 <= status < 500:\n\s+return CrawlPolicy\(PolicyBasis\.UNAVAILABLE\)/,
    );
  });

  it('requires the robots.txt check before every fetch — and fails the day one loses it', () => {
    // Where each check sits relative to the fetch it guards, in the API's own
    // source. The behaviour is tested in the API's suite
    // (`test_robots_obeyed.py`); this holds the page's claim to the code.
    const before = (source: string, check: string, fetch: string) => {
      const at = source.indexOf(check);
      expect(at, `missing: ${check}`).toBeGreaterThan(-1);
      expect(source.indexOf(fetch, at), `${fetch} is not after ${check}`).toBeGreaterThan(at);
    };
    const crawl = read(CRAWL);
    const gateFollow = /async def follow\([\s\S]*?(?=\n    async def |\n\S)/.exec(crawl)?.[0] ?? '';
    before(gateFollow, 'if not await self.allows(url):', 'await client.request(');
    const crawlSite = topLevel(crawl, 'crawl_site');
    before(crawlSite, 'home_url = await gate.resolve(start_url)', 'await _crawl(');
    const browser = topLevel(crawl, '_crawl');
    before(browser, 'if not await gate.allows(target):', '_fetch_page(page, target, timeout_ms, guard)');
    before(browser, '_fetch_page(page, home_url, timeout_ms, guard)', 'if not await gate.allows(page.url):');

    const audit = read(AUDIT);
    const auditSite = topLevel(audit, 'audit_site');
    before(auditSite, 'home_url = await gate.resolve(start_url)', 'async_playwright()');
    before(auditSite, 'page.goto(\n                        home_url', 'if not await gate.allows(page.url):');
    // The browser is only ever handed the address the gate resolved.
    expect(auditSite).not.toMatch(/page\.goto\(\s*start_url/);
    expect(crawlSite + browser).not.toMatch(/_fetch_page\(page, start_url/);
  });

  it('the reason code on the report and the technical screen is the API’s', () => {
    expect(ROBOTS_DISALLOWED).toBe(pythonString('ROBOTS_DISALLOWED'));
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

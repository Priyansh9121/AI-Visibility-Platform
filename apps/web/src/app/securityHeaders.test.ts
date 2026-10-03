/**
 * Security headers on every page — launch checklist 3.1, 2026-10-03.
 *
 * `next.config.mjs` sends one set of security headers on every page but the
 * proxied `/api/v1` (the API sends its own there), and a stricter Referrer-Policy
 * on the pages whose address is a credential. What a browser GETS is the result of
 * Next applying every rule that matches a path, so this computes that, rather than
 * reading the config and trusting it. Both halves are Next's own code:
 *
 *   - which rules match a URL: `buildCustomRoute`, the function `next start` builds
 *     each header rule's matcher with (strict path-to-regexp, unnamed groups
 *     dropped, an optional trailing slash). The older test next to this one calls
 *     `getPathMatch` directly, which leaves out that last step;
 *   - how matching rules are applied: as `resolve-routes.js` does it. In order, a
 *     later rule's value for a key replacing an earlier one's, and each value
 *     compiled against the route's named params when it has any.
 *
 * The same paths were fetched from a real `next start` when this was written; this
 * is what keeps what that showed true.
 */

import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCustomRoute } from 'next/dist/server/lib/router-utils/filesystem';
import { compileNonPath } from 'next/dist/shared/lib/router/utils/prepare-destination';
import { GOOGLE_FONTS_HREF } from '@avp/design-system/tokens';
import nextConfig from '../../next.config.mjs';

const APP = fileURLToPath(new URL('.', import.meta.url));

// Written out here, not read from the config, so editing the config cannot
// quietly edit what is pinned.
const PERMISSIONS_POLICY =
  'camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()';
const REPORT_ONLY =
  "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' " +
  "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' " +
  "data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; " +
  "form-action 'self'; object-src 'none'";

/** Everything but /api/v1 sends this. */
const SITE_WIDE: Record<string, string> = {
  'Strict-Transport-Security': 'max-age=63072000; includeSubDomains',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "frame-ancestors 'none'",
  'Permissions-Policy': PERMISSIONS_POLICY,
  'Content-Security-Policy-Report-Only': REPORT_ONLY,
};
/** The pages whose address is a credential send the same, but stricter, and are kept out of search. */
const CREDENTIAL_PAGE: Record<string, string> = {
  ...SITE_WIDE,
  'Referrer-Policy': 'no-referrer',
  'X-Robots-Tag': 'noindex, nofollow',
};

const CREDENTIAL_URLS = [
  '/share',
  '/share/abc',
  '/reset-password',
  '/reset-password/abc',
  '/invite',
  '/invite/abc',
  '/sign-up/google',
];
/**
 * Credential pages that are not under a `[token]` directory: the fragment forms of the
 * share, invitation and reset links (the credential is in the fragment), and the Google
 * sign-up (its ticket is). Every page UNDER a `[token]` segment is one too, found on
 * disk by `pages()`.
 */
const FRAGMENT_CREDENTIAL_PAGES = ['/share', '/invite', '/reset-password', '/sign-up/google'];

const SITE_WIDE_URLS = [
  '/',
  '/dashboard',
  '/clients/x',
  '/scans/x/report',
  '/crawler',
  '/privacy',
  '/terms',
  '/settings',
  // Not pages, but served by the same app: an asset, a 404, robots.txt.
  '/_next/static/chunks/main.js',
  '/robots.txt',
  '/does-not-exist',
  // Near the API's path and not under it.
  '/api',
  '/api/v10/health',
  '/x/api/v1/health',
];
const API_URLS = [
  '/api/v1',
  '/api/v1/',
  '/api/v1/health',
  '/api/v1/auth/google/start',
  '/api/v1/auth/google/callback',
  '/api/v1/billing/webhook',
  '/api/v1/scans/x/report.pdf',
  // Next matches paths without regard to case, and the rewrite does too.
  '/API/V1/health',
  '/Api/V1',
];

type Rule = { source: string; headers: { key: string; value: string }[] };

async function configRules(): Promise<Rule[]> {
  return ((await nextConfig.headers?.()) ?? []) as Rule[];
}

/**
 * The headers Next sends for `pathname` from `rules`, as `resolve-routes.js` builds
 * them: every matching rule in order; a later value for the same key replaces an
 * earlier one (the key as written, as Next keys it); a value is compiled against
 * the matched route's named params, if it has any.
 */
function sentFor(pathname: string, rules: Rule[]): Record<string, string> {
  const sent: Record<string, string> = {};
  for (const rule of rules) {
    const params = buildCustomRoute('header', rule).match(pathname);
    if (params === false) continue;
    const hasParams = Object.keys(params).length > 0;
    for (const { key, value } of rule.headers) {
      sent[hasParams ? compileNonPath(key, params) : key] = hasParams
        ? compileNonPath(value, params)
        : value;
    }
  }
  return sent;
}

async function sentOn(pathname: string): Promise<Record<string, string>> {
  return sentFor(pathname, await configRules());
}

type Page = {
  /** A concrete URL: `[token]` and every other `[segment]` becomes `x`, a route group vanishes. */
  url: string;
  /** A `[token]` segment is this page's own directory or any one above it. */
  credential: boolean;
};

/**
 * Every page under `root` (the app's own directory unless a test gives another). A
 * page BELOW a `[token]` segment is a credential page as much as the one AT it: its
 * address carries the same token (`/share/[token]/print`).
 */
function pages(dir: string = APP, root: string = APP): Page[] {
  const found: Page[] = [];
  if (existsSync(join(dir, 'page.tsx'))) {
    const segments = relative(root, dir)
      .split(sep)
      .filter((s) => s !== '' && !/^\(.*\)$/.test(s));
    found.push({
      url: `/${segments.map((s) => s.replace(/^\[.+\]$/, 'x')).join('/')}`,
      credential: segments.includes('[token]'),
    });
  }
  for (const name of readdirSync(dir)) {
    if (statSync(join(dir, name)).isDirectory()) found.push(...pages(join(dir, name), root));
  }
  return found;
}

const urlsOf = (list: Page[]) => list.map((p) => p.url);
const sameHeaders = (a: Record<string, string>, b: Record<string, string>) =>
  JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());

/**
 * The credential pages in `list` that do NOT send the credential set under `rules`:
 * every page at or below a `[token]` segment, and the fragment links' pages.
 */
function credentialPagesMissingTheirHeaders(list: Page[], rules: Rule[]): string[] {
  return list
    .filter((p) => p.credential || FRAGMENT_CREDENTIAL_PAGES.includes(p.url))
    .filter((p) => !sameHeaders(sentFor(p.url, rules), CREDENTIAL_PAGE))
    .map((p) => p.url)
    .sort();
}

/** A policy as directive -> sources. */
function directives(policy: string): Map<string, string[]> {
  return new Map(
    policy.split(';').map((d) => {
      const [name, ...sources] = d.trim().split(/\s+/);
      return [name!, sources] as const;
    }),
  );
}

describe('the matching is Next’s own, so passing means something', () => {
  it('finds the pages on disk', () => {
    const urls = urlsOf(pages());
    expect(urls.length).toBeGreaterThan(20);
    for (const url of ['/', '/dashboard', '/clients/x/crawler', '/scans/x/report', '/share/x']) {
      expect(urls).toContain(url);
    }
  });

  it('matches a credential page to its own rule and reads its param', async () => {
    const rule = (await configRules()).find((r) => r.source === '/share/:token')!;
    expect(buildCustomRoute('header', rule).match('/share/abc')).toEqual({ token: 'abc' });
  });

  it('keeps no header name that differs from another only by case', async () => {
    // `resolve-routes.js` keys what it sends by the name as written. Two spellings
    // of one header would both be set, in key order, and the later key would win
    // whatever the rule order said.
    const names = (await configRules()).flatMap((r) => r.headers.map((h) => h.key));
    const spellings = new Map<string, Set<string>>();
    for (const name of names) {
      const lower = name.toLowerCase();
      spellings.set(lower, (spellings.get(lower) ?? new Set()).add(name));
    }
    expect([...spellings.values()].filter((s) => s.size > 1)).toEqual([]);
  });
});

describe('every page sends the security headers, with these exact values', () => {
  it.each(SITE_WIDE_URLS)('%s sends the site-wide set and nothing else', async (url) => {
    expect(await sentOn(url)).toEqual(SITE_WIDE);
  });

  it.each(Object.entries(SITE_WIDE))('%s is sent as written', async (key, value) => {
    for (const url of ['/', '/dashboard', '/clients/x', '/scans/x/report']) {
      expect((await sentOn(url))[key], url).toBe(value);
    }
  });

  it('every page on disk gets the whole set, and only the credential pages differ', async () => {
    const rules = await configRules();
    for (const { url, credential } of pages()) {
      const isCredential = credential || FRAGMENT_CREDENTIAL_PAGES.includes(url);
      expect(sentFor(url, rules), url).toEqual(isCredential ? CREDENTIAL_PAGE : SITE_WIDE);
    }
  });

  it('keeps the public pages indexable: the site-wide rule sends no X-Robots-Tag', async () => {
    for (const url of ['/', '/crawler', '/privacy', '/terms']) {
      expect(Object.keys(await sentOn(url)), url).not.toContain('X-Robots-Tag');
    }
  });

  it('sends HSTS for two years with subdomains and without preload (that list is hard to leave)', async () => {
    const value = (await sentOn('/'))['Strict-Transport-Security']!;
    expect(value).toBe('max-age=63072000; includeSubDomains');
    expect(value).not.toMatch(/preload/i);
  });
});

describe('the credential pages keep no-referrer and noindex, because their rules come last', () => {
  it.each(CREDENTIAL_URLS)('%s sends no-referrer, noindex and the rest of the set', async (url) => {
    expect(await sentOn(url)).toEqual(CREDENTIAL_PAGE);
  });

  it('puts the site-wide rule first in the config, which is what lets them win', async () => {
    const rules = await configRules();
    const siteWide = rules.findIndex((r) => r.headers.some((h) => h.key === 'Strict-Transport-Security'));
    expect(siteWide).toBe(0);
    const credentialRules = rules.filter((r) => r.headers.some((h) => h.value === 'no-referrer'));
    // At least the seven there are today: a page added under a token adds an entry.
    expect(credentialRules.length).toBeGreaterThanOrEqual(7);
    for (const rule of credentialRules) expect(rules.indexOf(rule)).toBeGreaterThan(siteWide);
  });

  it('would LOSE if the site-wide rule came last: the order is doing the work', async () => {
    // The negative control, kept as a test. Same rules, same matcher, reversed:
    // the site-wide Referrer-Policy replaces no-referrer on a credential page.
    const rules = await configRules();
    expect(sentFor('/share/abc', rules)['Referrer-Policy']).toBe('no-referrer');
    expect(sentFor('/share/abc', [...rules].reverse())['Referrer-Policy']).toBe(
      'strict-origin-when-cross-origin',
    );
  });
});

describe('a page below a [token] segment is a credential page too, wherever it sits', () => {
  // `/share/[token]/print` would carry the same token in its address as
  // `/share/[token]`, so it must send no-referrer and noindex as well. The rules in
  // next.config.mjs name each page, so a page nobody added a rule for gets only the
  // site-wide set. This finds every such page on disk and reports the ones that do.
  it('every credential page on disk sends no-referrer and noindex, and there are some to check', async () => {
    const found = pages();
    expect(
      credentialPagesMissingTheirHeaders(found, await configRules()),
      "a page at or below a [token] segment (or a fragment link's page) sends the site-wide " +
        'set only: add its path to the token list in next.config.mjs, after the site-wide rule',
    ).toEqual([]);
    expect(urlsOf(found.filter((p) => p.credential))).toEqual(
      expect.arrayContaining(['/share/x', '/invite/x', '/reset-password/x']),
    );
  });

  // No such page exists today, so the rule is proved on a synthetic app tree: what the
  // finder reports for it, and how the check behaves with rules that cover only the
  // pages as they are now.
  describe('proved on a synthetic tree, because none exists today', () => {
    let tree: string;

    beforeAll(() => {
      tree = mkdtempSync(join(tmpdir(), 'avp-app-'));
      for (const dir of [
        'share/[token]',
        'share/[token]/print',
        'invite/[token]',
        '(legal)/reset-password/[token]',
        '(legal)/reset-password/[token]/print',
        'clients/[clientId]',
        'clients/[clientId]/alerts',
      ]) {
        mkdirSync(join(tree, dir), { recursive: true });
        writeFileSync(join(tree, dir, 'page.tsx'), '');
      }
    });
    afterAll(() => rmSync(tree, { recursive: true, force: true }));

    /** An entry like those in the token list in next.config.mjs. */
    const tokenEntry = (source: string): Rule => ({
      source,
      headers: [
        { key: 'Referrer-Policy', value: 'no-referrer' },
        { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
      ],
    });
    /** The rules as they stand today (the site-wide rule, then one entry per page at a token), plus `entries`. */
    const rulesWith = async (...entries: string[]): Promise<Rule[]> => [
      (await configRules())[0]!, // the site-wide rule, first
      ...['/share/:token', '/invite/:token', '/reset-password/:token', ...entries].map(tokenEntry),
    ];

    it('finds pages below a token at any depth, through a route group, and no other page', () => {
      const found = pages(tree, tree).sort((a, b) => (a.url < b.url ? -1 : 1));
      expect(found).toEqual([
        { url: '/clients/x', credential: false },
        { url: '/clients/x/alerts', credential: false },
        { url: '/invite/x', credential: true },
        { url: '/reset-password/x', credential: true },
        { url: '/reset-password/x/print', credential: true },
        { url: '/share/x', credential: true },
        { url: '/share/x/print', credential: true },
      ]);
    });

    it('reports the nested pages that only the site-wide rule reaches', async () => {
      expect(credentialPagesMissingTheirHeaders(pages(tree, tree), await rulesWith())).toEqual([
        '/reset-password/x/print',
        '/share/x/print',
      ]);
      // What such a page is sent today: the set any public page gets, and no noindex.
      expect(sentFor('/share/x/print', await rulesWith())).toEqual(SITE_WIDE);
    });

    it('stops reporting a page once its own entry is in the list', async () => {
      expect(
        credentialPagesMissingTheirHeaders(
          pages(tree, tree),
          await rulesWith('/share/:token/print', '/reset-password/:token/print'),
        ),
      ).toEqual([]);
    });
  });
});

describe('/api/v1 gets nothing from this config: the API sends its own set there', () => {
  it.each(API_URLS)('%s matches no header rule', async (url) => {
    expect(await sentOn(url)).toEqual({});
  });

  it('is the exact complement of the rewrite that proxies it', async () => {
    const rules = await configRules();
    const rewrites = (await nextConfig.rewrites?.()) as unknown as { source: string; destination: string }[];
    expect(rewrites.map((r) => r.source)).toEqual(['/api/v1/:path*']);
    const proxied = buildCustomRoute('rewrite', rewrites[0]!);
    const urls = [
      ...API_URLS,
      ...SITE_WIDE_URLS,
      ...CREDENTIAL_URLS,
      ...urlsOf(pages()),
      '/api/v1x',
      '/apiv1',
    ];
    for (const url of urls) {
      const goesToTheApi = proxied.match(url) !== false;
      const getsTheSet = Object.keys(sentFor(url, rules)).length > 0;
      expect(getsTheSet, `${url} (proxied: ${goesToTheApi})`).toBe(!goesToTheApi);
    }
  });
});

describe('the Content-Security-Policy', () => {
  it('ENFORCES frame-ancestors alone, so it cannot stop a script, a style or an image', async () => {
    const enforced = (await sentOn('/'))['Content-Security-Policy']!;
    expect(directives(enforced)).toEqual(new Map([['frame-ancestors', ["'none'"]]]));
  });

  it('sends the full policy as Report-Only, exactly', async () => {
    expect((await sentOn('/'))['Content-Security-Policy-Report-Only']).toBe(REPORT_ONLY);
  });

  it('never allows eval, a wildcard host or plain http, and closes object, base and frames', async () => {
    const policy = (await sentOn('/'))['Content-Security-Policy-Report-Only']!;
    expect(policy).not.toContain('unsafe-eval');
    expect(policy).not.toMatch(/(^|\s)\*(\s|;|$)/);
    expect(policy).not.toContain('http:');
    const parsed = directives(policy);
    expect(parsed.get('default-src')).toEqual(["'self'"]);
    expect(parsed.get('object-src')).toEqual(["'none'"]);
    expect(parsed.get('frame-ancestors')).toEqual(["'none'"]);
    expect(parsed.get('base-uri')).toEqual(["'self'"]);
  });

  it('names exactly the hosts the fonts come from, and no other', async () => {
    // The stylesheet's host is the one the design system links; the files that
    // stylesheet names come from fonts.gstatic.com.
    const policy = (await sentOn('/'))['Content-Security-Policy-Report-Only']!;
    const stylesheetOrigin = new URL(GOOGLE_FONTS_HREF).origin;
    expect(stylesheetOrigin).toBe('https://fonts.googleapis.com');
    const parsed = directives(policy);
    expect(parsed.get('style-src')).toContain(stylesheetOrigin);
    expect(parsed.get('font-src')).toContain('https://fonts.gstatic.com');
    const named = [...policy.matchAll(/https:\/\/[a-z0-9.-]+/g)].map((m) => m[0]);
    expect(new Set(named)).toEqual(
      new Set(['https://fonts.googleapis.com', 'https://fonts.gstatic.com']),
    );
  });

  it('lets https images through, because a report shows an agency’s logo from anywhere', async () => {
    // ReportView renders `<img src={agency.logoUrl}>`; the API accepts https:// only.
    const policy = (await sentOn('/'))['Content-Security-Policy-Report-Only']!;
    expect(directives(policy).get('img-src')).toEqual(["'self'", 'data:', 'https:']);
  });
});

describe('the Permissions-Policy', () => {
  it('switches off exactly what nothing here uses, and nothing else', async () => {
    const policy = (await sentOn('/'))['Permissions-Policy']!;
    expect(policy).toBe(PERMISSIONS_POLICY);
    expect(policy.split(', ')).toEqual([
      'camera=()',
      'microphone=()',
      'geolocation=()',
      'payment=()',
      'usb=()',
      'browsing-topics=()',
    ]);
  });
});

describe('the stack is not announced', () => {
  it('turns Next’s X-Powered-By header off', () => {
    // Next adds `X-Powered-By: Next.js` to every page unless told not to. It tells a
    // stranger what runs here. The API's twin, uvicorn's Server header, is off in
    // docker-entrypoint.sh (`test_dockerfile.py` holds that one).
    expect(nextConfig.poweredByHeader).toBe(false);
  });
});

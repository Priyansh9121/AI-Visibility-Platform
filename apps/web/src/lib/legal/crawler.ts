/**
 * What the crawler does, for the public /crawler page — 2026-09-29.
 *
 * The crawler is the API's (`services/crawl.py` when a site is added,
 * `services/technical_audit.py` with each scan), so every value here is a
 * copy of one there, and `components/legal/crawlerPage.test.tsx` reads each
 * back out of the API's source and fails if a copy drifts. The page count is
 * `RETENTION.crawlPages`, which `legalPages.test.tsx` already holds to the API.
 */
export const CRAWLER = {
  /**
   * `USER_AGENT_TEMPLATE` (`crawl.py`). `{info_url}` is this page's address:
   * the API fills it from `PUBLIC_WEB_BASE_URL`, and the page from the address
   * it was requested at, which in a correct deployment is the same thing.
   */
  userAgentTemplate: 'Mozilla/5.0 (compatible; AVPScanBot/0.1; +{info_url}) Playwright/Chromium',
  /** `CRAWLER_INFO_PATH` (`crawl.py`): where the user agent points. */
  infoPath: '/crawler',
  /** The product token in the user agent: what a site owner can match on. */
  name: 'AVPScanBot',
  /** `CANDIDATE_PATHS` (`crawl.py`): the linked pages it reads beyond the homepage. */
  candidatePaths: ['/about', '/about-us', '/services', '/what-we-do', '/products'],
} as const;

/** The exact User-Agent the crawler sends, for a web app reachable at `origin`. */
export function crawlerUserAgent(origin: string): string {
  return CRAWLER.userAgentTemplate.replace(
    '{info_url}',
    `${origin.replace(/\/+$/, '')}${CRAWLER.infoPath}`,
  );
}

interface HeaderReader {
  get(name: string): string | null;
}

/** A proxy's header can list several values; the first is the client-facing one. */
function first(value: string | null): string | null {
  const head = value?.split(',')[0]?.trim();
  return head ? head : null;
}

/**
 * The origin a request reached this app at — `https://…onrender.com` on
 * Render, whose proxy sets `x-forwarded-proto`; `http://localhost:3000` in
 * development.
 *
 * READ FROM THE REQUEST, WHICH THE API NEVER DOES FOR A LINK. The API builds
 * share, reset and invitation links from configuration because a link built
 * from a spoofed Host carries a real token to somebody else. This address goes
 * nowhere: it is printed back to the one person whose request supplied it, on
 * a page that is rendered per request and so is never cached for anyone else.
 */
export function requestOrigin(headers: HeaderReader): string {
  const host = first(headers.get('x-forwarded-host')) ?? first(headers.get('host')) ?? 'localhost';
  const proto = first(headers.get('x-forwarded-proto')) ?? 'http';
  return `${proto}://${host}`;
}

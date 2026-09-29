import type { Metadata } from 'next';
import { headers } from 'next/headers';
import { CrawlerInfo } from '@/components/legal/CrawlerInfo';
import { requestOrigin } from '@/lib/legal/crawler';

/**
 * /crawler — public, 2026-09-29.
 *
 * The address in the crawler's user agent, which the API builds from
 * `PUBLIC_WEB_BASE_URL`. No session call on this route or anywhere under it:
 * a site owner reading it is never signed in.
 *
 * Rendered per request, unlike /privacy and /terms, because the user agent it
 * prints contains this page's own address, and the web app has no setting
 * that holds it: the page reads the address it was requested at
 * (`requestOrigin` says why that is safe here).
 */
export const metadata: Metadata = {
  title: 'Crawler — AI Visibility Platform',
  description: 'What AVPScanBot, the AI Visibility Platform crawler, fetches from a website, how often, and how to reach its operator.',
};

export default async function CrawlerRoute() {
  return <CrawlerInfo origin={requestOrigin(await headers())} />;
}

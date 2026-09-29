'use client';

// A client boundary although nothing here is interactive, like the privacy
// and terms pages: the header's buttons are client code. Next still renders
// the whole page to HTML on the server, per request (`app/crawler/page.tsx`).

/**
 * The crawler page — 2026-09-29.
 *
 * Where the crawler's user agent points: a site owner who finds AVPScanBot in
 * their logs lands here. Until this date the user agent pointed at a
 * placeholder address.
 *
 * EVERY SENTENCE STATES SOMETHING THE CODE DOES, traced on 2026-09-29, and
 * the values are constants that `crawlerPage.test.tsx` reads back from the
 * API's source (`lib/legal/crawler.ts`, `lib/legal/retention.ts`).
 *
 * It says the crawler does NOT obey robots.txt because it does not: the audit
 * reads the file to report on it, and nothing consults it before a fetch. The
 * test holds that too, so the day robots support is added this page fails
 * until it says so.
 */

import type { JSX } from 'react';
import { PageSection } from '@avp/design-system';
import { PRIVACY_PATH } from '@/components/marketing/MarketingFooter';
import { CRAWLER, crawlerUserAgent } from '@/lib/legal/crawler';
import { OPERATOR } from '@/lib/legal/operator';
import { RETENTION, counted } from '@/lib/legal/retention';
import { LegalPage, Para, Point, Points, TextLink } from './LegalPage';

/** "a, b or c" */
function either(items: readonly string[]): string {
  return items.length < 2
    ? items.join('')
    : `${items.slice(0, -1).join(', ')} or ${items[items.length - 1]}`;
}

export function CrawlerInfo({ origin }: { origin: string }): JSX.Element {
  return (
    <LegalPage
      eyebrow="Crawler"
      title={`What ${CRAWLER.name} does on a website.`}
      lead={`${CRAWLER.name} is this service’s crawler. It visits a website only when an agency using AI Visibility Platform has added that site as a client, and this page lists everything it fetches there.`}
    >
      <PageSection heading="How it identifies itself">
        <Para>Every request it makes carries this user agent:</Para>
        <p className="max-w-measure break-words rounded-md border border-line-hairline bg-surface-sunken p-4 font-mono text-ui-sm leading-mono text-text-primary">
          <code data-user-agent="">{crawlerUserAgent(origin)}</code>
        </p>
        <Para>
          The address in it is this page. Requests for the files a page loads, such as its scripts
          and stylesheets, carry the same user agent, because the crawler is a real browser:
          headless Chromium, which also runs each page’s scripts.
        </Para>
      </PageSection>

      <PageSection heading="What it fetches">
        <Points>
          <Point term="When a site is added.">
            It opens the homepage, then up to {counted(RETENTION.crawlPages - 1, 'more page')} on
            the same site that the homepage links to, chosen from addresses that start{' '}
            {either(CRAWLER.candidatePaths)}: at most {counted(RETENTION.crawlPages, 'page')} in
            all. It does not download their images, fonts or video. The same happens again if the
            agency asks for the site to be read again.
          </Point>
          <Point term="With each scan.">
            It requests /robots.txt, and /sitemap.xml unless robots.txt names a sitemap, then loads
            the homepage once in full, images included, to check how it is built and how quickly
            it loads. That is one page per scan. If the load times out, it loads the homepage once
            more.
          </Point>
        </Points>
        <Para>
          What happens to what it reads is set out in the{' '}
          <TextLink href={PRIVACY_PATH}>privacy policy</TextLink>.
        </Para>
      </PageSection>

      <PageSection heading="How often">
        <Para>
          It has no schedule. It runs only when a person using this service asks for it: when an
          agency adds a site, asks for it to be read again, or runs a scan of it. Nothing repeats
          on its own.
        </Para>
      </PageSection>

      <PageSection heading="robots.txt">
        <Para>
          It does not obey robots.txt. With each scan it reads the file to report what it tells AI
          crawlers, but it fetches the pages above whether or not the file allows them.
        </Para>
        <Para>
          To keep it off a site, refuse requests whose user agent contains {CRAWLER.name}. Every
          request it makes carries that name.
        </Para>
      </PageSection>

      <PageSection heading="Contact">
        <Para>
          {CRAWLER.name} is run by {OPERATOR.name}, {OPERATOR.trading} in {OPERATOR.city},{' '}
          {OPERATOR.country}, who operates this service. To ask about it, or to report a problem it
          caused, email <TextLink href={`mailto:${OPERATOR.email}`}>{OPERATOR.email}</TextLink>.
        </Para>
      </PageSection>
    </LegalPage>
  );
}

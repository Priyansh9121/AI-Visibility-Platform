/**
 * The Technical screen — a `partial` audit is a scored audit.
 *
 * `partial` means the page was read and scored with a component excluded (no
 * date signal, usually); `failed` means it was not read and not scored. This
 * screen once treated everything but `ok` as unreadable, so a scored audit
 * read "The site could not be read". These tests hold the line between them.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClientTechnicalView, type TechnicalState } from './ClientTechnicalView';
import type { Client, TechnicalAudit } from '@avp/shared-types';
import { PARTIAL_AUDIT_LABEL, PARTIAL_AUDIT_NOTE } from '@/lib/report/strings';
import { clientsMe, identifiedClient } from '@/lib/clients/__fixtures__/clients';
import { threeScanHistory } from '@/lib/client/__fixtures__/history';

const CLIENT = identifiedClient as Client;

const BASE: TechnicalAudit = {
  id: 'aud_01AAA',
  scanId: 'scan_01AAA',
  urlAudited: 'https://example.com/',
  status: 'ok',
  errorCode: null,
  technicalFoundation: '72.00',
  pagesCrawled: 1,
  canonicalPresent: true,
  h1Count: 1,
  hasFaqSchema: false,
  hasLocalbusinessSchema: false,
  hasOrganizationSchema: true,
  hasProductSchema: false,
  hasSitemap: true,
  isIndexable: true,
  robotsAllowsCrawl: true,
  wordCount: 400,
  components: {
    indexation: '100.00',
    content_freshness: '50.00',
    structured_data: '60.00',
    schema_presence: '70.00',
  },
  componentWeights: {
    indexation: '30.00',
    content_freshness: '20.00',
    structured_data: '25.00',
    schema_presence: '25.00',
  },
  excludedComponents: {},
  checks: [
    { id: 'chk_1', checkKey: 'indexable', status: 'pass', detailCode: null, value: null },
    { id: 'chk_2', checkKey: 'sitemap_present', status: 'fail', detailCode: null, value: null },
  ],
  createdAt: '2026-09-29T00:00:00Z',
  updatedAt: '2026-09-29T00:00:00Z',
};

const PARTIAL: TechnicalAudit = {
  ...BASE,
  status: 'partial',
  technicalFoundation: '81.33',
  components: {
    indexation: '100.00',
    structured_data: '60.00',
    schema_presence: '70.00',
  },
  componentWeights: {
    indexation: '40.00',
    structured_data: '33.33',
    schema_presence: '26.67',
  },
  excludedComponents: { content_freshness: 'NO_DATE_SIGNAL_AVAILABLE' },
};

const FAILED: TechnicalAudit = {
  ...BASE,
  status: 'failed',
  errorCode: 'FETCH_FAILED',
  technicalFoundation: null,
  components: {},
  componentWeights: {},
  checks: [],
};

const render = (audit: TechnicalAudit | null) => {
  const state: TechnicalState = {
    kind: 'ready',
    client: CLIENT,
    history: threeScanHistory,
    audit,
  };
  return renderToStaticMarkup(<ClientTechnicalView state={state} me={clientsMe} />);
};

describe('a partial audit renders as the scored audit it is', () => {
  const html = render(PARTIAL);

  it('never says the site could not be read', () => {
    expect(html).not.toContain('could not be read');
    expect(html).not.toContain('An unreachable site');
  });

  it('shows its score and its header figures', () => {
    expect(html).toContain('Technical foundation');
    expect(html).toContain('81');
    expect(html).toContain('Pages crawled');
    expect(html).toContain('Passing');
    expect(html).toContain('Failing');
  });

  it('names what was not measured and why', () => {
    expect(html).toContain('Not measured');
    expect(html).toContain('Content Freshness');
    expect(html).toContain('The site publishes no date signal at all');
  });

  it('still lists every check', () => {
    expect(html).toContain('Every check');
  });
});

describe('a failed audit is the only unreadable one', () => {
  it('says the site could not be read, with its reason and no figures', () => {
    const html = render(FAILED);
    expect(html).toContain('The site could not be read');
    expect(html).toContain('Reason: FETCH_FAILED');
    expect(html).not.toContain('Pages crawled');
    expect(html).not.toContain('Not measured');
  });

  it('words a robots.txt refusal as the site’s choice, not an unreachable site', () => {
    const html = render({ ...FAILED, errorCode: 'ROBOTS_DISALLOWED' });
    expect(html).toContain('asks crawlers like ours to stay out');
    expect(html).not.toContain('could not be read');
  });
});

describe('a site whose address is not public', () => {
  it('says the crawler did not read it, and never mentions robots.txt', () => {
    const html = render({ ...FAILED, errorCode: 'ADDRESS_NOT_PUBLIC' });
    expect(html).toContain('Address not public');
    expect(html).toContain('is on a private or reserved network, so the crawler did not read the site');
    expect(html).toContain('Reason: ADDRESS_NOT_PUBLIC');
    expect(html).not.toContain('robots.txt');
    expect(html).not.toContain('could not be read');
  });
});

describe('an ok audit is unchanged', () => {
  const html = render(BASE);

  it('renders score, figures and checks, with no Not measured card', () => {
    expect(html).toContain('Pages crawled');
    expect(html).toContain('Every check');
    expect(html).not.toContain('could not be read');
    expect(html).not.toContain('Not measured');
  });
});

describe('no audit at all', () => {
  it('says the site has not been audited', () => {
    expect(render(null)).toContain('has not been audited');
  });
});

describe('a partial audit says it is partial, and only a partial audit does', () => {
  const NOTE = 'Some signals were unavailable, so the sub-score was computed from what could be measured.';

  it('shows the badge and the sentence, in the report’s exact words', () => {
    const html = render(PARTIAL);
    expect(PARTIAL_AUDIT_LABEL).toBe('Partial audit');
    expect(PARTIAL_AUDIT_NOTE).toBe(NOTE);
    expect(html).toContain('Partial audit');
    expect(html).toContain(NOTE);
  });

  it('does not show either on an ok or a failed audit', () => {
    for (const audit of [BASE, FAILED]) {
      const html = render(audit);
      expect(html).not.toContain('Partial audit');
      expect(html).not.toContain(NOTE);
    }
  });

  it('counts the parts it drew: three for partial, four for ok', () => {
    expect(render(PARTIAL)).not.toContain('four parts');
    expect(render(PARTIAL)).toContain('three parts');
    expect(render(BASE)).toContain('four parts');
  });
});

describe('the Not measured card is named and lists its components', () => {
  it('has an h3 the card is labelled by, and a list item per component', () => {
    const html = render({
      ...PARTIAL,
      excludedComponents: {
        content_freshness: 'NO_DATE_SIGNAL_AVAILABLE',
        schema_presence: 'NO_DATE_SIGNAL_AVAILABLE',
      },
    });
    expect(html).toMatch(/<h3[^>]*id="technical-not-in-score"[^>]*>Not in this score<\/h3>/);
    expect(html).toContain('aria-labelledby="technical-not-in-score"');
    const card = html.slice(html.indexOf('technical-not-in-score'));
    expect(card).toContain('<ul');
    expect((card.match(/<li[ >]/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });
});

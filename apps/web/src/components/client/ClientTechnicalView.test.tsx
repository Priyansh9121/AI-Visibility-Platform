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
import {
  AUDIT_FAILURE_GENERIC,
  AUDIT_FAILURE_SENTENCE,
  EXCLUSION_REASON,
  PARTIAL_AUDIT_LABEL,
  PARTIAL_AUDIT_NOTE,
  ROBOTS_UNREACHABLE_ADVICE,
  ROBOTS_UNREACHABLE_SENTENCE,
  auditFailureSentence,
} from '@/lib/report/strings';
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

/** How many times `needle` is said: a sentence the screen repeats reads as a fault. */
const occurrences = (html: string, needle: string): number => html.split(needle).length - 1;

describe('a partial audit renders as the scored audit it is', () => {
  const html = render(PARTIAL);

  it('never says the site could not be read', () => {
    expect(html).not.toContain('could not be read');
    // The failed audit's own sentence, which says the crawler could not read the site.
    expect(html).not.toContain('A site the crawler could not read');
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
  it('says the site could not be read, with what happened in a sentence and no figures', () => {
    const html = render(FAILED);
    expect(html).toContain('The site could not be read');
    // The body is true of every failure, a busy crawler and a cut run included: it says
    // the crawler could not read the site, never that the site was unreachable.
    expect(html).toContain(
      'A site the crawler could not read is not a site with a bad technical foundation, so nothing here is scored. The audit records what went wrong rather than guessing.',
    );
    expect(html).not.toContain('An unreachable site');
    expect(html).toContain(AUDIT_FAILURE_SENTENCE.FETCH_FAILED);
    expect(html).not.toContain('FETCH_FAILED');
    expect(html).not.toContain('Reason:');
    expect(html).not.toContain('Pages crawled');
    expect(html).not.toContain('Not measured');
  });

  it('words a robots.txt refusal as the site’s choice, not an unreachable site, and never as the code', () => {
    const html = render({ ...FAILED, errorCode: 'ROBOTS_DISALLOWED' });
    expect(html).toContain('asks crawlers like ours to stay out');
    expect(html).not.toContain('could not be read');
    expect(html).not.toContain('ROBOTS_DISALLOWED');
    expect(html).not.toContain('Reason:');
    // The body already says why, and where to look next; a note under it that
    // said it again would be the same sentence twice.
    expect(occurrences(html, EXCLUSION_REASON.CRAWL_DISALLOWED!.detail)).toBe(1);
    // No note at all: EmptyState renders an empty note paragraph (and its gap)
    // for any note that is not null, so an empty string would sneak one back.
    expect(html).not.toContain('avp-empty__note');
  });
});

describe('a site whose robots.txt could not be read — launch checklist 3.41', () => {
  // Until 2026-10-03 this was stored as ROBOTS_DISALLOWED, and this screen said "This
  // site asks crawlers like ours to stay out" of a site that never answered.
  const html = render({ ...FAILED, errorCode: 'ROBOTS_UNREACHABLE' });

  it('says what happened, once, in the words the report uses', () => {
    expect(html).toContain(EXCLUSION_REASON.CRAWL_POLICY_UNREADABLE!.label);
    expect(html).toContain(ROBOTS_UNREACHABLE_SENTENCE);
    expect(occurrences(html, EXCLUSION_REASON.CRAWL_POLICY_UNREADABLE!.detail)).toBe(1);
  });

  it('never says the site asks crawlers to stay out, never blames it, never prints the code', () => {
    for (const words of [
      'stay out',
      'asks crawlers',
      'not to load its pages',
      'Blocked by robots.txt',
      'The site could not be read',
      'Reason:',
      'ROBOTS_UNREACHABLE',
      'CRAWL_POLICY_UNREADABLE',
      'ROBOTS_DISALLOWED',
    ]) {
      expect(html, words).not.toContain(words);
    }
    expect(html).not.toContain('Pages crawled');
    // Title and body are the whole of it: no note element at all.
    expect(html).not.toContain('avp-empty__note');
    // The advice is the intake card's: nobody here has just typed an address.
    expect(html).not.toContain(ROBOTS_UNREACHABLE_ADVICE);
  });

  it('leaves the real refusal’s words to the file that forbids the page', () => {
    const refusal = render({ ...FAILED, errorCode: 'ROBOTS_DISALLOWED' });
    expect(refusal).toContain('asks crawlers like ours to stay out');
    expect(refusal).not.toContain(ROBOTS_UNREACHABLE_SENTENCE);
  });
});

describe('a site whose address is not public', () => {
  it('says the crawler did not read it, never mentions robots.txt, and never prints the code', () => {
    const html = render({ ...FAILED, errorCode: 'ADDRESS_NOT_PUBLIC' });
    expect(html).toContain('Address not public');
    expect(html).toContain('is on a private or reserved network, so the crawler did not read the site');
    expect(html).not.toContain('ADDRESS_NOT_PUBLIC');
    expect(html).not.toContain('Reason:');
    expect(html).not.toContain('robots.txt');
    expect(html).not.toContain('could not be read');
    expect(occurrences(html, EXCLUSION_REASON.CRAWL_ADDRESS_NOT_PUBLIC!.detail)).toBe(1);
    expect(html).not.toContain('avp-empty__note');
  });
});

describe('a failed audit says what happened in a sentence, and never the code', () => {
  // The same sentence the report's failed-audit card shows (`auditFailureSentence`,
  // launch checklist 3.26), one per way the audit can fail.
  const cases: [code: string, sentence: string][] = [
    ['BROWSER_ERROR', AUDIT_FAILURE_SENTENCE.BROWSER_ERROR!],
    ['FETCH_FAILED', AUDIT_FAILURE_SENTENCE.FETCH_FAILED!],
    ['TIMEOUT', AUDIT_FAILURE_SENTENCE.TIMEOUT!],
    ['BROWSER_BUSY', AUDIT_FAILURE_SENTENCE.BROWSER_BUSY!],
    ['HTTP_503', auditFailureSentence('HTTP_503')],
    ['SOMETHING_NEW', AUDIT_FAILURE_GENERIC],
  ];

  it('has a sentence of its own for each code, and the status the crawler saw', () => {
    expect(new Set(cases.map(([, sentence]) => sentence)).size).toBe(cases.length);
    expect(auditFailureSentence('HTTP_503')).toContain('HTTP 503');
    expect(auditFailureSentence('SOMETHING_NEW')).toBe(AUDIT_FAILURE_GENERIC);
  });

  it.each(cases)('%s: the sentence, once, and not the code', (code, sentence) => {
    const html = render({ ...FAILED, errorCode: code });
    expect(html).toContain('The site could not be read');
    expect(html).not.toMatch(/unreachable/i);
    expect(occurrences(html, sentence)).toBe(1);
    expect(html).not.toContain(code);
    expect(html).not.toContain('Reason:');
    expect(html).not.toContain('(HTTP_');
    expect(html).not.toContain('Pages crawled');
  });

  it('a failed audit that carries no code says the generic sentence, as the report does', () => {
    const html = render({ ...FAILED, errorCode: null });
    expect(html).toContain('The site could not be read');
    expect(occurrences(html, AUDIT_FAILURE_GENERIC)).toBe(1);
    expect(html).not.toContain('Reason:');
  });
});

describe('no failed state prints a machine code', () => {
  // ALL_CAPS_WITH_UNDERSCORES is how every audit code is spelled: FETCH_FAILED,
  // BROWSER_ERROR, HTTP_503, ROBOTS_DISALLOWED, ADDRESS_NOT_PUBLIC. It does not
  // see a single word, so `TIMEOUT` is also checked by name below.
  const MACHINE_CODE = /\b[A-Z]{3,}_[A-Z0-9_]+\b/;
  const codes: (string | null)[] = [
    'ROBOTS_DISALLOWED',
    'ROBOTS_UNREACHABLE',
    'ADDRESS_NOT_PUBLIC',
    'BROWSER_ERROR',
    'FETCH_FAILED',
    'TIMEOUT',
    'BROWSER_BUSY',
    'HTTP_503',
    'HTTP_404',
    'SOMETHING_NEW',
    null,
  ];

  it('the pattern sees the codes it is guarding against', () => {
    for (const code of codes.filter((c) => c !== null && c !== 'TIMEOUT')) {
      expect(`Reason: ${code}`, String(code)).toMatch(MACHINE_CODE);
    }
    expect(AUDIT_FAILURE_GENERIC).not.toMatch(MACHINE_CODE);
  });

  it.each(codes)('errorCode %s', (code) => {
    const html = render({ ...FAILED, errorCode: code });
    expect(html.match(MACHINE_CODE)).toBeNull();
    if (code !== null) expect(html).not.toContain(code);
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

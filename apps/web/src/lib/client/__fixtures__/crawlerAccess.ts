/**
 * AI crawler access fixtures — Epic F.
 *
 * Every one of these is a REAL shape read off a live client domain in
 * `avp_dev` at the time Epic F was built, not an invented case:
 *
 *   * `notionAccess`    — notion.so. Blocks five SEO and commerce crawlers by
 *                         name and says nothing about any AI one, so exactly
 *                         one roster agent (Amazonbot, a SEARCH crawler) comes
 *                         back blocked and thirteen are allowed through `*`.
 *                         The only client of nine with a real finding.
 *   * `silentAccess`    — plausible.io. A robots.txt of one `Sitemap:` line,
 *                         no groups at all, so every agent is UNSPECIFIED.
 *                         This is the COMMON case, not the corner case.
 *   * `permissiveAccess`— linear.app. A `*` group with housekeeping disallows
 *                         (`/api/`, `/cdn-cgi/`). Everything allowed, and the
 *                         disallow count is carried without becoming a verdict.
 *   * `unreadableAccess`— robots.txt could not be fetched. Every verdict
 *                         UNKNOWN, which must never render as a permissive
 *                         grid.
 *
 * The roster is trimmed to six agents here rather than all fourteen: the
 * assertions are about grouping, ordering and vocabulary, and fourteen rows of
 * fixture would obscure which one each test is about.
 */

import type { CrawlerAccess, CrawlerAgent } from '@avp/shared-types';

const agent = (
  name: string,
  vendor: string,
  purpose: CrawlerAgent['purpose'],
  verdict: CrawlerAgent['verdict'],
  ruleSource: CrawlerAgent['ruleSource'],
  matchedToken: string | null,
  disallowRules = 0,
): CrawlerAgent => ({
  agent: name,
  vendor,
  purpose,
  verdict,
  ruleSource,
  matchedToken,
  disallowRules,
});

const SCANNED = '2026-09-03T10:26:08.000Z';

/** notion.so — one explicit block, thirteen allowed through the wildcard. */
export const notionAccess: CrawlerAccess = {
  clientId: 'clnt_01AAA',
  scanId: 'scan_01NOTION',
  scannedAt: SCANNED,
  urlAudited: 'https://notion.so',
  robotsReadable: true,
  // Sorted as the API sorts them: worst verdict first, then by what a block
  // costs — so the blocked SEARCH crawler leads.
  agents: [
    agent('Amazonbot', 'Amazon', 'search', 'blocked', 'explicit', 'amazonbot', 1),
    agent('OAI-SearchBot', 'OpenAI', 'search', 'allowed', 'wildcard', '*', 11),
    agent('PerplexityBot', 'Perplexity', 'search', 'allowed', 'wildcard', '*', 11),
    agent('ChatGPT-User', 'OpenAI', 'user_action', 'allowed', 'wildcard', '*', 11),
    agent('ClaudeBot', 'Anthropic', 'training', 'allowed', 'wildcard', '*', 11),
    agent('GPTBot', 'OpenAI', 'training', 'allowed', 'wildcard', '*', 11),
  ],
  summary: {
    total: 6,
    allowed: 5,
    blocked: 1,
    unspecified: 0,
    unknown: 0,
    searchBlocked: 1,
    explicit: 1,
  },
  availableScanIds: ['scan_01NOTION', 'scan_01NOTION_OLD'],
};

/** plausible.io — no groups at all, so nobody is named. The common case. */
export const silentAccess: CrawlerAccess = {
  clientId: 'clnt_01AAA',
  scanId: 'scan_01SILENT',
  scannedAt: SCANNED,
  urlAudited: 'https://plausible.io',
  robotsReadable: true,
  agents: [
    agent('Amazonbot', 'Amazon', 'search', 'unspecified', 'none', null),
    agent('OAI-SearchBot', 'OpenAI', 'search', 'unspecified', 'none', null),
    agent('PerplexityBot', 'Perplexity', 'search', 'unspecified', 'none', null),
    agent('ChatGPT-User', 'OpenAI', 'user_action', 'unspecified', 'none', null),
    agent('ClaudeBot', 'Anthropic', 'training', 'unspecified', 'none', null),
    agent('GPTBot', 'OpenAI', 'training', 'unspecified', 'none', null),
  ],
  summary: {
    total: 6,
    allowed: 0,
    blocked: 0,
    unspecified: 6,
    unknown: 0,
    searchBlocked: 0,
    explicit: 0,
  },
  availableScanIds: ['scan_01SILENT'],
};

/** linear.app — housekeeping disallows that must not become a verdict. */
export const permissiveAccess: CrawlerAccess = {
  clientId: 'clnt_01AAA',
  scanId: 'scan_01LINEAR',
  scannedAt: SCANNED,
  urlAudited: 'https://linear.app',
  robotsReadable: true,
  agents: [
    agent('Amazonbot', 'Amazon', 'search', 'allowed', 'wildcard', '*', 2),
    agent('OAI-SearchBot', 'OpenAI', 'search', 'allowed', 'wildcard', '*', 2),
    agent('ClaudeBot', 'Anthropic', 'training', 'allowed', 'wildcard', '*', 2),
  ],
  summary: {
    total: 3,
    allowed: 3,
    blocked: 0,
    unspecified: 0,
    unknown: 0,
    searchBlocked: 0,
    explicit: 0,
  },
  availableScanIds: ['scan_01LINEAR'],
};

/** robots.txt unreachable — never to be drawn as "everything is fine". */
export const unreadableAccess: CrawlerAccess = {
  clientId: 'clnt_01AAA',
  scanId: 'scan_01DOWN',
  scannedAt: SCANNED,
  urlAudited: 'https://epic7-degraded.example',
  robotsReadable: false,
  agents: [
    agent('OAI-SearchBot', 'OpenAI', 'search', 'unknown', 'unreadable', null),
    agent('GPTBot', 'OpenAI', 'training', 'unknown', 'unreadable', null),
  ],
  summary: {
    total: 2,
    allowed: 0,
    blocked: 0,
    unspecified: 0,
    unknown: 2,
    searchBlocked: 0,
    explicit: 0,
  },
  availableScanIds: ['scan_01DOWN'],
};

/**
 * A site whose `*` group disallows everything — the blanket block.
 *
 * Not drawn from `avp_dev` (no client there does this) but a real and common
 * shape, and the case the row's rule-source line exists for in its second
 * half: the verdict is a block that NOBODY chose per-crawler, inherited from
 * a rule usually written years earlier for a different reason. That is a
 * different conversation with a client than a named block, so the row has to
 * be able to tell them apart.
 */
export const blanketBlockAccess: CrawlerAccess = {
  clientId: 'clnt_01AAA',
  scanId: 'scan_01BLANKET',
  scannedAt: SCANNED,
  urlAudited: 'https://example.invalid',
  robotsReadable: true,
  agents: [
    agent('OAI-SearchBot', 'OpenAI', 'search', 'blocked', 'wildcard', '*', 1),
    agent('GPTBot', 'OpenAI', 'training', 'blocked', 'wildcard', '*', 1),
  ],
  summary: {
    total: 2,
    allowed: 0,
    blocked: 2,
    unspecified: 0,
    unknown: 0,
    searchBlocked: 1,
    explicit: 0,
  },
  availableScanIds: ['scan_01BLANKET'],
};

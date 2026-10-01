/**
 * /robots.txt — founder decision 2026-09-30: AI crawlers are ALLOWED, both
 * search and training. Founder decision 3 (2026-10-01): nothing is disallowed,
 * not even the credential-address pages. A crawler that obeys robots.txt never
 * fetches a disallowed page, so it never sees that page's noindex; those pages
 * rely on their `X-Robots-Tag: noindex, nofollow` header (next.config.mjs),
 * which a crawler can only see if it may fetch the page.
 */

import { describe, it, expect } from 'vitest';
import type { MetadataRoute } from 'next';
import { resolveRobots } from 'next/dist/build/webpack/loaders/metadata/resolve-route-data';
import robots from './robots';

const TOKEN_PATHS = ['/share', '/share/abc123', '/invite', '/invite/abc123', '/reset-password', '/reset-password/abc123'];
const PUBLIC_PAGES = ['/', '/crawler', '/privacy', '/terms'];
const AI_CRAWLERS = [
  'GPTBot',
  'OAI-SearchBot',
  'ChatGPT-User',
  'ClaudeBot',
  'Claude-SearchBot',
  'Claude-User',
  'PerplexityBot',
  'Perplexity-User',
  'Google-Extended',
  'CCBot',
  'Applebot-Extended',
];

type Rule = MetadataRoute.Robots['rules'] extends infer R ? (R extends unknown[] ? R[number] : R) : never;

function rules(): Rule[] {
  const r = robots().rules;
  return Array.isArray(r) ? r : [r];
}
const list = (v: string | string[] | undefined): string[] => (v === undefined ? [] : Array.isArray(v) ? v : [v]);
const agents = (rule: Rule) => list(rule.userAgent);
/** robots.txt Disallow is a prefix match. */
const blocked = (rule: Rule, path: string) => list(rule.disallow).some((d) => d !== '' && path.startsWith(d));

describe('robots.txt', () => {
  it('has a * rule that allows / and has no disallow', () => {
    const star = rules().find((r) => agents(r).includes('*'));
    expect(star).toBeDefined();
    expect(list(star!.allow)).toContain('/');
    expect(list(star!.disallow).filter((d) => d !== '')).toEqual([]);
  });

  it('does not block the token pages or their /:token forms for any agent, so crawlers can see their noindex', () => {
    for (const rule of rules()) {
      for (const p of TOKEN_PATHS) expect(blocked(rule, p)).toBe(false);
    }
  });

  it('blocks no public page for any agent', () => {
    for (const rule of rules()) {
      expect(list(rule.disallow)).not.toContain('/');
      for (const p of PUBLIC_PAGES) expect(blocked(rule, p)).toBe(false);
    }
  });

  it('never blocks an AI crawler; any explicit AI group allows / and disallows nothing', () => {
    for (const rule of rules()) {
      const ai = agents(rule).filter((a) => AI_CRAWLERS.some((c) => c.toLowerCase() === a.toLowerCase()));
      if (ai.length === 0) continue;
      expect(list(rule.allow)).toContain('/');
      expect(list(rule.disallow).filter((d) => d !== '')).toEqual([]);
    }
  });

  it('names no sitemap, since none is served', () => {
    expect(robots().sitemap).toBeUndefined();
  });

  it('renders as the text Next serves, with no Disallow line at all', () => {
    const text = resolveRobots(robots());
    expect(text).toContain('User-Agent: *');
    expect(text).toContain('Allow: /');
    expect(text).not.toMatch(/Disallow:/i);
    expect(text).not.toMatch(/Sitemap:/i);
  });
});

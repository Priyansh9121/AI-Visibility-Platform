/**
 * /robots.txt — founder decision 2026-09-30: AI crawlers are ALLOWED, both
 * search and training. Only the credential-address pages are kept out.
 */

import { describe, it, expect } from 'vitest';
import type { MetadataRoute } from 'next';
import { resolveRobots } from 'next/dist/build/webpack/loaders/metadata/resolve-route-data';
import robots from './robots';

const DISALLOWED = ['/share', '/invite', '/reset-password'];
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
  it('has a * rule that allows / and disallows exactly the three token pages', () => {
    const star = rules().find((r) => agents(r).includes('*'));
    expect(star).toBeDefined();
    expect(list(star!.allow)).toContain('/');
    expect([...list(star!.disallow)].sort()).toEqual([...DISALLOWED].sort());
  });

  it('covers the legacy /share/:token forms by prefix', () => {
    for (const rule of rules()) {
      for (const p of ['/share/abc123', '/invite/abc123', '/reset-password/abc123', '/share', '/invite', '/reset-password']) {
        expect(blocked(rule, p)).toBe(true);
      }
    }
  });

  it('blocks no public page for any agent', () => {
    for (const rule of rules()) {
      expect(list(rule.disallow)).not.toContain('/');
      for (const p of PUBLIC_PAGES) expect(blocked(rule, p)).toBe(false);
    }
  });

  it('never blocks an AI crawler; any explicit AI group allows / and repeats the disallows', () => {
    for (const rule of rules()) {
      const ai = agents(rule).filter((a) => AI_CRAWLERS.some((c) => c.toLowerCase() === a.toLowerCase()));
      if (ai.length === 0) continue;
      expect(list(rule.allow)).toContain('/');
      expect([...list(rule.disallow)].sort()).toEqual([...DISALLOWED].sort());
    }
  });

  it('names no sitemap, since none is served', () => {
    expect(robots().sitemap).toBeUndefined();
  });

  it('renders as the text Next serves', () => {
    const text = resolveRobots(robots());
    expect(text).toContain('User-Agent: *');
    expect(text).toContain('Allow: /\n');
    for (const d of DISALLOWED) expect(text).toContain(`Disallow: ${d}\n`);
    expect(text).not.toContain('Disallow: /\n');
    expect(text).not.toMatch(/Sitemap:/i);
  });
});

import type { MetadataRoute } from 'next';

// Founder decision 2026-09-30: AI crawlers are allowed, search and training
// alike, so there is no per-bot group and no block on any public page.
// Founder decision 3 (2026-10-01): nothing is disallowed at all. The pages whose
// address is a credential (/share, /invite, /reset-password and their /:token
// forms) rely on their `X-Robots-Tag: noindex, nofollow` header
// (next.config.mjs), which a crawler can only see if it may fetch the page. A
// disallow would stop a compliant crawler fetching it, so a leaked legacy URL
// could still be listed URL-only. No sitemap is named: none is served.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: '*', allow: '/' }],
  };
}

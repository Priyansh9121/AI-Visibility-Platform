import type { MetadataRoute } from 'next';

// Founder decision 2026-09-30: AI crawlers are allowed, search and training
// alike, so there is no per-bot group and no block on any public page. Only the
// pages whose address is a credential are disallowed (a prefix match, so the
// /share/:token forms are covered too). Those pages also send
// `X-Robots-Tag: noindex, nofollow` (next.config.mjs). No sitemap is named:
// none is served.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: '*', allow: '/', disallow: ['/share', '/invite', '/reset-password'] }],
  };
}

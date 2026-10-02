const LOCAL_API_TARGET = 'http://localhost:8000';

/**
 * Where the rewrite below sends /api/v1: the API's origin, from the server-only
 * env var API_PROXY_TARGET. Pure, so the config can use it and tests can call it.
 * Messages name the variable and the fault, never the value (it may carry a
 * credential if someone pastes one).
 *
 * @param {Record<string, string | undefined>} env
 * @returns {string} an origin: scheme, host and optional port
 */
export function apiProxyTarget(env) {
  const raw = (env.API_PROXY_TARGET ?? '').trim();
  if (raw === '') {
    if (env.NODE_ENV === 'production') {
      throw new Error(
        "API_PROXY_TARGET must be set to the API's address (on Render: the API service's internal address)",
      );
    }
    return LOCAL_API_TARGET;
  }
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error('API_PROXY_TARGET is not a valid URL');
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('API_PROXY_TARGET must use http or https');
  }
  if (url.username !== '' || url.password !== '') {
    throw new Error('API_PROXY_TARGET must not contain a username or password');
  }
  if (url.search !== '' || url.hash !== '') {
    throw new Error('API_PROXY_TARGET must not contain a query or fragment');
  }
  if (url.pathname !== '/' && url.pathname !== '') {
    throw new Error('API_PROXY_TARGET must not contain a path, only the address');
  }
  // A public Render URL works, and quietly breaks the per-IP rate limits: the
  // request crosses Render's and Cloudflare's edge a second time, the client-IP
  // headers become the web service's own address, and every visitor shares one
  // bucket. Only the internal address keeps them as the web's edge set them.
  const host = url.hostname.toLowerCase();
  if (env.NODE_ENV === 'production' && (host === 'onrender.com' || host.endsWith('.onrender.com'))) {
    throw new Error(
      "API_PROXY_TARGET must be the API service's internal address (Render: API service, Connect, Internal), not its public onrender.com URL",
    );
  }
  return url.origin;
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The design system ships TypeScript source rather than a build artefact, so
  // Next must compile it. Keeps the package editable without a watch-build in
  // between, which matters while the system is still changing.
  transpilePackages: ['@avp/design-system', '@avp/shared-types'],
  eslint: { ignoreDuringBuilds: true },

  // No next/image is used anywhere in the web app, so the image optimiser is
  // off: no optimiser, no `sharp`, and none of its LGPL libvips binaries in the
  // install (root package.json drops `sharp` with ignoredOptionalDependencies).
  // Founder decision 2026-10-01. If next/image is ever adopted, revisit both.
  images: { unoptimized: true },

  // NO REFERER FROM A PAGE WHOSE ADDRESS IS A CREDENTIAL — 2026-09-29.
  //
  // A shared report's address is its token, and so is a reset or invitation
  // link's; a Google sign-up in progress carries its ticket in the fragment (a
  // legacy link may still carry it in the query, and the header covers that
  // and the page itself). From
  // these pages the browser also asks other hosts for things: the fonts, an
  // agency's logo, a cited source someone clicks. Under a lax policy — some
  // older browsers' default — each of those requests is sent the full address.
  // `no-referrer` sends nothing. The report's source links already carry
  // rel="noreferrer"; this covers every other request and any future link.
  // Nothing here needs a Referer: the API's CORS reads Origin.
  //
  // The same pages also send `X-Robots-Tag: noindex, nofollow`: a credential
  // address is not for a search index. One list, so the two cannot drift.
  async headers() {
    return [
      '/share',
      '/invite',
      '/reset-password',
      '/share/:token',
      '/reset-password/:token',
      '/invite/:token',
      '/sign-up/google',
    ].map(
      (source) => ({
        source,
        headers: [
          { key: 'Referrer-Policy', value: 'no-referrer' },
          { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
        ],
      }),
    );
  },

  // THE API IS SAME-ORIGIN — 2026-10-02.
  //
  // The web app and the API are different sites on Render, so a session cookie
  // set by the API was third-party and browsers drop those. The browser now
  // calls /api/v1 on the web app's own origin and this rewrite hands it to the
  // API, so the cookie is first-party (SameSite=Lax, `__Host-` prefix). The
  // Google callback comes back through here too, so its cookie lands on the web
  // host.
  //
  // Point API_PROXY_TARGET (server-only, never NEXT_PUBLIC_) at the API's Render
  // INTERNAL address. Then no second Render or Cloudflare edge sits in front of
  // the API, and the client-IP headers its per-IP rate limits read arrive
  // exactly as the web's edge set them.
  //
  // Rewrites are fixed at build time: changing API_PROXY_TARGET needs a rebuild
  // (Render redeploys on an env change). Read inside rewrites(), not at import,
  // so importing this file in a test never throws.
  async rewrites() {
    const target = apiProxyTarget(process.env);
    return [{ source: '/api/v1/:path*', destination: `${target}/api/v1/:path*` }];
  },

  experimental: {
    // The slowest synchronous API call, POST /clients/{id}/prompt-runs, can take
    // about 122 s. Next's default proxy timeout is 30 s.
    proxyTimeout: 150_000,
  },

  webpack(config) {
    // The design system writes ESM-correct import specifiers (`./Card.js`)
    // that point at TypeScript sources. Node and tsc resolve those; webpack
    // does not, unless told that a `.js` specifier may resolve to `.ts`/`.tsx`.
    // The alternative — dropping extensions from the design system's imports —
    // would break it under plain Node ESM, so the fix belongs here.
    config.resolve.extensionAlias = {
      ...config.resolve.extensionAlias,
      '.js': ['.ts', '.tsx', '.js'],
      '.mjs': ['.mts', '.mjs'],
    };
    return config;
  },
};

export default nextConfig;

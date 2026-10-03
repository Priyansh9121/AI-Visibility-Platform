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

// Two values the API sends too, each on ONE line: `avp_api/security_headers.py`
// holds the same two, and a test (test_security_headers.py) reads these lines to
// hold them equal, so neither side can drift from the other.
const STRICT_TRANSPORT_SECURITY = 'max-age=63072000; includeSubDomains';
const PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()';

// The full policy, sent as Report-Only (see `headers()`). Each source is here
// because the code loads it:
//   - scripts: Next's own inline bootstrap, layout.tsx's MOTION_READY and the
//     theme script next-themes injects are all inline, hence 'unsafe-inline';
//   - styles: the design system's stylesheet, React's style attributes (inline)
//     and the Google Fonts stylesheet (GOOGLE_FONTS_HREF);
//   - fonts: the files that stylesheet names, from fonts.gstatic.com;
//   - images: this site's own, and `https:` because the report shows an agency's
//     logo from wherever the agency keeps it (ReportView.tsx; the API accepts
//     https:// only, `schemas/agency.py`);
//   - connect: the browser calls /api/v1 on this origin and nothing else.
// No source is named for Google sign-in or Stripe: both are navigations the
// page makes (an anchor to /api/v1/auth/google/start, window.location.assign
// to the URL the API returns), which a fetch directive does not govern, and
// there is no Stripe.js. No form here posts to another host (every one is
// handled in script, onSubmit), so `form-action 'self'` has nothing to report.
const CSP_REPORT_ONLY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' data: https:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join('; ');

// Every page but /api/v1. The lookahead refuses `api/v1` followed by a slash or
// the end, so /api/v1, /api/v1/ and everything under it are left out, and
// /api/v10 is not. It is the exact complement of the rewrite below
// (/api/v1/:path*); securityHeaders.test.ts matches both with Next's own matcher.
const EVERYTHING_BUT_THE_API = '/((?!api/v1(?:/|$)).*)';

const SECURITY_HEADERS = [
  { key: 'Strict-Transport-Security', value: STRICT_TRANSPORT_SECURITY },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Content-Security-Policy', value: "frame-ancestors 'none'" },
  { key: 'Permissions-Policy', value: PERMISSIONS_POLICY },
  { key: 'Content-Security-Policy-Report-Only', value: CSP_REPORT_ONLY },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // No `X-Powered-By: Next.js` on any response (launch checklist 3.1): it tells a
  // stranger what runs here. The API's twin is uvicorn's `Server` header, which
  // docker-entrypoint.sh turns off.
  poweredByHeader: false,
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

  // SECURITY HEADERS ON EVERY PAGE — 2026-10-03 (launch checklist 3.1).
  //
  // Every route except /api/v1 sends SECURITY_HEADERS. The API sends its own on
  // /api/v1 (`avp_api/security_headers.py`) and this app only proxies it, so
  // that path is left out and a browser gets one set there, the API's.
  //
  //   Strict-Transport-Security: two years, subdomains included, no `preload`
  //     (the list is slow to leave). Browsers ignore it over plain http, so
  //     `next dev` is untouched. Every subdomain of the host this ships on must
  //     serve https first, or `includeSubDomains` locks users out of it.
  //   X-Content-Type-Options: nosniff. A script or stylesheet is never guessed
  //     from its bytes.
  //   Referrer-Policy: strict-origin-when-cross-origin. Another host is sent
  //     this origin and never a path or query; some older browsers default to
  //     sending the whole address. The token pages below send `no-referrer`.
  //   X-Frame-Options and CSP `frame-ancestors 'none'`: nobody frames this app,
  //     the shared report included. Both, because the older header is the only
  //     one some old browsers read. The ENFORCED Content-Security-Policy is that
  //     one directive alone, so it cannot stop a script, style or image loading.
  //   Permissions-Policy: switches off what nothing here uses (camera,
  //     microphone, location, payment, USB, the Topics API).
  //
  // THE FULL POLICY IS REPORT-ONLY. The browser logs what it would have blocked
  // and blocks nothing. Three reasons it is not enforced yet:
  //   - Next writes its own inline scripts into every page (the built /privacy
  //     carries eight), and two of them are this app's (MOTION_READY, and
  //     next-themes' theme script). A policy that refuses inline script stops
  //     the app starting, so the policy keeps 'unsafe-inline' for scripts, which
  //     protects nothing against injected script. Dropping it needs a fresh
  //     nonce on every response (Next does that through middleware, and a page
  //     that uses the nonce is rendered per request): a bigger change than this
  //     one, and a decision about its cost.
  //   - Google sign-in and Stripe Checkout could not be exercised here against
  //     the real services. By the code both are top-level navigations, which the
  //     policy does not govern; that is not the same as having watched them work.
  //   - A real branded report was not loaded here. Agency logos come from any
  //     https host, which is why `img-src` allows `https:`.
  // There is no report endpoint, so a violation shows only in the browser's
  // console. Read it on the live site (signed in too) before enforcing.
  //
  // ORDER MATTERS. Next applies every rule that matches a path, in order, and a
  // later rule's value for the same header replaces an earlier one's. The
  // site-wide rule is FIRST, so the token pages' `no-referrer` below wins on
  // their paths. securityHeaders.test.ts proves it with Next's own matcher.
  //
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
  //
  // A page NESTED under a `[token]` segment (say /share/[token]/print) is a
  // credential page too and needs its own entry here: securityHeaders.test.ts
  // and referrerPolicy.test.ts find every page under a `[token]` segment on disk
  // and fail until it sends both.
  async headers() {
    return [
      // First, on purpose: see ORDER MATTERS above.
      { source: EVERYTHING_BUT_THE_API, headers: SECURITY_HEADERS },
      ...[
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
      ),
    ];
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

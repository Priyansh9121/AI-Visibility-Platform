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

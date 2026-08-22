/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The design system ships TypeScript source rather than a build artefact, so
  // Next must compile it. Keeps the package editable without a watch-build in
  // between, which matters while the system is still changing.
  transpilePackages: ['@avp/design-system', '@avp/shared-types'],
  eslint: { ignoreDuringBuilds: true },

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

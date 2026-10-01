/**
 * next/image is not used anywhere in the web app, so the config turns the image
 * optimiser off (founder decision 2026-10-01). That is what lets the install
 * drop `sharp` and its LGPL libvips binaries. If someone adds next/image, this
 * test is the place to decide again.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import nextConfig from '../next.config.mjs';

describe('next.config images', () => {
  it('runs with the image optimiser off', () => {
    expect(nextConfig.images?.unoptimized).toBe(true);
  });

  it('keeps sharp out of the install', () => {
    const root = JSON.parse(
      readFileSync(fileURLToPath(new URL('../../../package.json', import.meta.url)), 'utf8'),
    );
    expect(root.pnpm.ignoredOptionalDependencies).toContain('sharp');
  });
});

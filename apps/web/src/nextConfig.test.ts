/**
 * next/image is not used anywhere in the web app, so the config turns the image
 * optimiser off (founder decision 2026-10-01). That is what lets the install
 * drop `sharp` and its LGPL libvips binaries. If someone adds next/image, this
 * test is the place to decide again.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import nextConfig, { apiProxyTarget } from '../next.config.mjs';
import { GOOGLE_SIGN_IN_URL } from './lib/api';

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

describe('next.config API proxy', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('rewrites /api/v1 to the API target with exactly one rule', async () => {
    vi.stubEnv('API_PROXY_TARGET', 'http://avp-api:10000');
    const rules = await nextConfig.rewrites!();
    expect(rules).toEqual([
      { source: '/api/v1/:path*', destination: 'http://avp-api:10000/api/v1/:path*' },
    ]);
  });

  it('normalises a trailing slash and surrounding space', async () => {
    vi.stubEnv('API_PROXY_TARGET', '  https://api.example.test/ ');
    const rules = await nextConfig.rewrites!();
    expect(rules).toEqual([
      { source: '/api/v1/:path*', destination: 'https://api.example.test/api/v1/:path*' },
    ]);
  });

  it('refuses a missing target in production, naming the variable', () => {
    for (const value of [undefined, '', '   ']) {
      expect(() => apiProxyTarget({ NODE_ENV: 'production', API_PROXY_TARGET: value })).toThrow(
        /API_PROXY_TARGET/,
      );
    }
  });

  it('falls back to localhost:8000 outside production', () => {
    expect(apiProxyTarget({ NODE_ENV: 'development' })).toBe('http://localhost:8000');
    expect(apiProxyTarget({ NODE_ENV: 'test', API_PROXY_TARGET: '' })).toBe('http://localhost:8000');
    expect(apiProxyTarget({})).toBe('http://localhost:8000');
  });

  it('throws on bad targets without echoing credentials', () => {
    const bad = [
      'http://host/api',
      'http://host/?x=1',
      'http://host/#frag',
      'http://user:s3cret@host',
      'http://:s3cret@host',
      'ftp://host',
      'not a url',
    ];
    for (const value of bad) {
      let message = '';
      try {
        apiProxyTarget({ NODE_ENV: 'production', API_PROXY_TARGET: value });
      } catch (err) {
        message = (err as Error).message;
      }
      expect(message, value).toMatch(/API_PROXY_TARGET/);
      expect(message, value).not.toContain('s3cret');
    }
  });

  it("refuses the API's public onrender.com URL in production, and only there", () => {
    for (const value of [
      'https://ai-visibility-platform-5gk4.onrender.com',
      'https://API.ONRENDER.COM/',
      'https://onrender.com',
    ]) {
      expect(() => apiProxyTarget({ NODE_ENV: 'production', API_PROXY_TARGET: value }), value).toThrow(
        /internal address/,
      );
    }
    // The internal address Render gives a service: a bare name and a port.
    expect(
      apiProxyTarget({ NODE_ENV: 'production', API_PROXY_TARGET: 'http://ai-visibility-platform-5gk4:10000' }),
    ).toBe('http://ai-visibility-platform-5gk4:10000');
    // A host that merely contains the words is not refused.
    expect(
      apiProxyTarget({ NODE_ENV: 'production', API_PROXY_TARGET: 'http://onrender.com.example.test' }),
    ).toBe('http://onrender.com.example.test');
    expect(
      apiProxyTarget({ NODE_ENV: 'development', API_PROXY_TARGET: 'https://x.onrender.com' }),
    ).toBe('https://x.onrender.com');
  });

  it('keeps the proxy open past the slowest synchronous API call', () => {
    expect(nextConfig.experimental?.proxyTimeout).toBeGreaterThanOrEqual(130000);
  });
});

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx|mjs|js)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

describe('same-origin API', () => {
  it('no source file reads NEXT_PUBLIC_API_BASE_URL', () => {
    const root = fileURLToPath(new URL('.', import.meta.url));
    const offenders = sourceFiles(root).filter((f) =>
      readFileSync(f, 'utf8').includes('NEXT_PUBLIC_API_BASE_URL'),
    );
    expect(offenders).toEqual([]);
  });

  it('Google sign-in is a same-origin navigation', () => {
    expect(GOOGLE_SIGN_IN_URL).toBe('/api/v1/auth/google/start');
  });
});

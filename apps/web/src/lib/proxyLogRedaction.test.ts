import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  redactProxyTarget,
  redactBodyLimitWarning,
  installProxyLogRedaction,
} from './proxyLogRedaction';

describe('redactProxyTarget', () => {
  it('drops the callback query and fragment', () => {
    expect(
      redactProxyTarget('http://api:10000/api/v1/auth/google/callback?code=abc&state=xyz#f'),
    ).toBe('http://api:10000/api/v1/auth/google/callback');
  });

  it('redacts a legacy report token, keeping .pdf', () => {
    expect(redactProxyTarget('http://api:10000/api/v1/reports/TOKEN_abc-1')).toBe(
      'http://api:10000/api/v1/reports/…',
    );
    expect(redactProxyTarget('http://api:10000/api/v1/reports/TOKEN_abc-1.pdf')).toBe(
      'http://api:10000/api/v1/reports/….pdf',
    );
    expect(redactProxyTarget('http://api:10000/api/v1/reports/shared/TOKEN')).toBe(
      'http://api:10000/api/v1/reports/…',
    );
  });

  it('keeps the two open report routes', () => {
    expect(redactProxyTarget('http://api:10000/api/v1/reports/shared')).toBe(
      'http://api:10000/api/v1/reports/shared',
    );
    expect(redactProxyTarget('http://api:10000/api/v1/reports/shared/pdf?x=1')).toBe(
      'http://api:10000/api/v1/reports/shared/pdf',
    );
  });

  it('never returns a query for an unparseable target', () => {
    expect(redactProxyTarget('garbage?code=secret')).not.toContain('secret');
  });
});

describe('installProxyLogRedaction', () => {
  const original = console.error;
  afterEach(() => {
    console.error = original;
  });

  it('rewrites the proxy failure line and reduces the error to its code', () => {
    const spy = vi.fn();
    console.error = spy;
    installProxyLogRedaction();
    const err = Object.assign(new Error('connect to secret@host failed'), { code: 'ECONNREFUSED' });
    console.error('Failed to proxy http://api:10000/api/v1/auth/google/callback?code=a&state=b', err);
    expect(spy).toHaveBeenCalledWith(
      'Failed to proxy http://api:10000/api/v1/auth/google/callback',
      'ECONNREFUSED',
    );
  });

  it('falls back to the error name, never message or stack', () => {
    const spy = vi.fn();
    console.error = spy;
    installProxyLogRedaction();
    console.error('Failed to proxy http://api:10000/api/v1/x', new TypeError('leaky'));
    expect(spy).toHaveBeenCalledWith('Failed to proxy http://api:10000/api/v1/x', 'TypeError');
  });

  it('passes other calls through unchanged', () => {
    const spy = vi.fn();
    console.error = spy;
    installProxyLogRedaction();
    const e = new Error('boom');
    console.error('something else', e, 1);
    expect(spy).toHaveBeenCalledWith('something else', e, 1);
  });

  it('wraps once when installed twice', () => {
    const spy = vi.fn();
    console.error = spy;
    installProxyLogRedaction();
    const wrapped = console.error;
    installProxyLogRedaction();
    expect(console.error).toBe(wrapped);
    console.error('Failed to proxy http://a/api/v1/x?q=1', new Error('m'));
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe('the body-limit warning', () => {
  const originalWarn = console.warn;
  const originalError = console.error;
  afterEach(() => {
    console.warn = originalWarn;
    console.error = originalError;
  });

  const line = (url: string) =>
    `Request body exceeded 10MB for ${url}. Only the first 10MB will be available unless configured. See https://nextjs.org/docs for more details.`;

  it('drops the query and redacts a report token from the path', () => {
    expect(redactBodyLimitWarning(line('/api/v1/auth/google/callback?code=a&state=b'))).toBe(
      line('/api/v1/auth/google/callback'),
    );
    expect(redactBodyLimitWarning(line('/api/v1/reports/TOKEN.pdf'))).toBe(
      line('/api/v1/reports/….pdf'),
    );
  });

  it('keeps nothing of a line it cannot read', () => {
    expect(redactBodyLimitWarning('Request body exceeded something secret=1')).toBe(
      'Request body exceeded the limit.',
    );
  });

  it('is installed on console.warn, once, and leaves other warnings alone', () => {
    const spy = vi.fn();
    console.warn = spy;
    console.error = vi.fn();
    installProxyLogRedaction();
    const wrapped = console.warn;
    installProxyLogRedaction();
    expect(console.warn).toBe(wrapped);
    console.warn(line('/api/v1/reports/TOKEN?k=1'));
    console.warn('another warning', 2);
    expect(spy).toHaveBeenNthCalledWith(1, line('/api/v1/reports/…'));
    expect(spy).toHaveBeenNthCalledWith(2, 'another warning', 2);
  });
});

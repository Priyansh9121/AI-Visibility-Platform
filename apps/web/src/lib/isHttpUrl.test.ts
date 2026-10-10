import { describe, it, expect } from 'vitest';
import { isHttpUrl } from './isHttpUrl';

describe('isHttpUrl', () => {
  it.each([
    'http://example.com',
    'https://example.com/a/b?q=1#frag',
    'HTTPS://Example.com/Path',
    'Http://example.com:8080/x',
    'https://g2.com/a@b',
    'https://g2.com/é',
    'https://g2.com?next=a@b',
  ])('accepts %s', (value) => {
    expect(isHttpUrl(value)).toBe(true);
  });

  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'JAVASCRIPT:alert(1)',
    'data:text/html;base64,PHNjcmlwdD4=',
    'DaTa:text/html,x',
    'vbscript:msgbox(1)',
    'VBScript:msgbox(1)',
    ' javascript:alert(1)',
    '\tjavascript:alert(1)',
    '\u0001javascript:alert(1)',
    '\u0000http://example.com',
    ' http://example.com',
    '\nhttps://example.com',
    'java\nscript:alert(1)',
    'http://exa mple.com',
    'http://example.com/a\nb',
    'https://example.com\n',
    'http:///nohost',
    'https://',
    '//example.com/x',
    '/relative/path',
    'example.com',
    'ftp://example.com/x',
    'file:///etc/passwd',
    '',
    '   ',
    'https://evil.example\\@www.g2.com/x',
    'https://g2.com/a\\b',
    'https://g2.com@evil.com/x',
    'https://user:pw@g2.com/x',
    'httpſ://x',
    'httpſs://x',
    'https://g2.com/a\u0085b',
    'https://g2.com/a\ufeffb',
    'https://g2.com/a\u00a0b',
    'https://g2.com/a\u2028b',
  ])('refuses %j', (value) => {
    expect(isHttpUrl(value)).toBe(false);
  });

  it('refuses null and undefined', () => {
    expect(isHttpUrl(null)).toBe(false);
    expect(isHttpUrl(undefined)).toBe(false);
  });
});

/**
 * F5c: a data-driven link in the report is a link only when it is http(s).
 *
 * Cited URLs come from AI answers. The API blanks any other scheme; the view checks again with
 * `isHttpUrl`, so a value that got through (an older response, a cache) still renders as text,
 * never as an `href` or an `<img src>`.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ReportView } from './ReportView';
import { helpscoutReport } from '@/lib/report/__fixtures__/reports';

type Report = Parameters<typeof ReportView>[0]['report'];

const render = (report: Report) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} publicView />);

/** The first competitor row ("front.com") with another URL. */
const withSample = (sampleUrl: string | null): Report => ({
  ...helpscoutReport,
  proof: {
    ...helpscoutReport.proof,
    competitorCitedDomains: helpscoutReport.proof.competitorCitedDomains.map((row, i) =>
      i === 0 ? { ...row, sampleUrl } : row,
    ),
  },
});

const REFUSED = [
  'javascript:alert(1)',
  'JavaScript:alert(1)',
  'data:text/html;base64,PHNjcmlwdD4=',
  'DaTa:text/html,x',
  'vbscript:msgbox(1)',
  'VBScript:msgbox(1)',
  ' javascript:alert(1)',
  '\tjavascript:alert(1)',
  '\u0001javascript:alert(1)',
  ' https://front.com/x',
];

describe('a cited domain links out only over http(s)', () => {
  it.each(['http://front.com/a', 'https://front.com/a'])('links %s', (url) => {
    const html = render(withSample(url));
    expect(html).toContain(`href="${url}"`);
    expect(html).toContain('rel="noreferrer nofollow"');
  });

  const ANCHORS = /target="_blank"/g;
  const anchors = (html: string) => (html.match(ANCHORS) ?? []).length;

  it.each(REFUSED)('renders %j as the domain in text, with no link', (url) => {
    const html = render(withSample(url));
    // The cell itself reads as the bare domain, not wrapped in an anchor...
    expect(html).toContain('>front.com</td>');
    expect(html).not.toMatch(/<a [^>]*>front\.com<\/a>/);
    // ...and exactly one outward link fewer than when every URL is valid.
    expect(anchors(html)).toBe(anchors(render(withSample('https://front.com/a'))) - 1);
    // The one row's link is gone, the domain is still there, and the value is nowhere in an attribute.
    expect(html).toContain('front.com');
    expect(html).not.toContain('href="front.com');
    expect(html.toLowerCase()).not.toContain('javascript:');
    expect(html.toLowerCase()).not.toContain('vbscript:');
    expect(html.toLowerCase()).not.toContain('href="data:');
    expect(html).not.toContain('href=" https');
    // The other rows keep theirs.
    expect(html).toContain('href="https://www.zendesk.com/');
  });

  it('shows a null sample url as text', () => {
    const html = render(withSample(null));
    expect(html).toContain('front.com');
    expect(html).not.toContain('href="https://front.com');
  });
});

describe('the agency logo is an image only over http(s)', () => {
  const withLogo = (logoUrl: string | null): Report => ({
    ...helpscoutReport,
    agency: { ...helpscoutReport.agency, logoUrl },
  });

  it('draws an https logo', () => {
    expect(render(withLogo('https://cdn.example/logo.png'))).toContain(
      'src="https://cdn.example/logo.png"',
    );
  });

  it.each(REFUSED)('falls back to the agency name for %j', (url) => {
    const html = render(withLogo(url));
    expect(html).not.toContain('<img');
    expect(html).toContain(helpscoutReport.agency.name);
  });
});

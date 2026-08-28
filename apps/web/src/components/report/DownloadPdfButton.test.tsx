/**
 * "Download PDF" — Epic 9.14.
 *
 * Two things matter here and neither is the button.
 *
 * The copy must not let anyone believe the PDF is a fuller artefact than the
 * page. It is the same five beats from the same payload, and describing it as
 * an "export" or a "full report" would be a promise the endpoint does not keep.
 *
 * And when the scan has no score, the screen says so BEFORE the click. A PDF
 * that turns out to read "Not scored" after it has been forwarded to a client
 * is a worse outcome than one that was never downloaded.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { DownloadPdfButton, slug } from './DownloadPdfButton';

const render = (props: Partial<Parameters<typeof DownloadPdfButton>[0]> = {}) =>
  renderToStaticMarkup(
    <DownloadPdfButton scanId="scan_01ABC" subjectName="Help Scout" {...props} />,
  );

describe('the control', () => {
  it('offers one action, from the design system', () => {
    const html = render();
    expect(html).toContain('Download PDF');
    expect(html).toContain('avp-btn');
  });

  it('shows no error before anything is attempted', () => {
    expect(render()).not.toContain('role="alert"');
  });
});

describe('it does not oversell what the file is', () => {
  it('says it is the same report as the page', () => {
    expect(render()).toContain('The same report as this page');
  });

  it('never calls it an export, a full report, or a summary', () => {
    const html = render().toLowerCase();
    for (const tell of [
      'export',
      'full report',
      'detailed report',
      'complete report',
      'summary',
      'branded',
      'white-label',
    ]) {
      expect(html, `found "${tell}"`).not.toContain(tell);
    }
  });
});

describe('an unscored scan is disclosed before the download, not after', () => {
  it('says the PDF will say "Not scored"', () => {
    const html = render({ unscored: true });
    expect(html).toContain('this scan has no score yet');
    expect(html).toContain('rather than showing a number');
  });

  it('still offers the download — a degraded report is still a report', () => {
    const html = render({ unscored: true });
    expect(html).toContain('Download PDF');
    expect(html).not.toContain('disabled');
  });

  it('says nothing of the sort when the scan is scored', () => {
    expect(render({ unscored: false })).not.toContain('no score yet');
  });
});

describe('it works for a stranger holding a share token', () => {
  it('renders identically whether given a scanId or a token', () => {
    // The reader gets the same document and the same words. The only thing
    // that differs is which endpoint is called, which is invisible here.
    expect(render({ scanId: 'scan_01ABC', token: undefined })).toBe(
      render({ scanId: undefined, token: 'a-share-token' }),
    );
  });

  it('leaks no token into the markup', () => {
    expect(render({ scanId: undefined, token: 'a-secret-share-token' })).not.toContain(
      'a-secret-share-token',
    );
  });
});

describe('slug mirrors the server filename rule', () => {
  it('keeps only the characters the server keeps', () => {
    expect(slug('Help Scout')).toBe('Help-Scout');
    expect(slug('Acme (Holdings) Ltd.')).toBe('Acme-Holdings-Ltd');
    expect(slug('Café Nero')).toBe('Caf-Nero');
  });

  it('never produces an empty or dot-leading filename', () => {
    expect(slug('')).toBe('report');
    expect(slug('...')).toBe('report');
    expect(slug('   ')).toBe('report');
  });

  it('bounds the length, like the server does', () => {
    expect(slug('a'.repeat(200)).length).toBe(60);
  });
});

describe('no ad hoc styling', () => {
  it('emits no arbitrary-value and no raw-palette class', () => {
    const html = render({ unscored: true });
    expect(html).not.toMatch(/class="[^"]*\b(bg|text|border|max-w)-\[/);
    expect(html).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

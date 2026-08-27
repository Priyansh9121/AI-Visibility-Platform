/**
 * The public landing page — Epic 9.10.
 *
 * Rendered to static markup and asserted over, the approach ReportView.test.tsx
 * and DashboardView.test.tsx use.
 *
 * The bulk of these are NEGATIVE assertions, and deliberately so. The risk on a
 * marketing page is not that a heading fails to render — it is that a claim
 * appears which nothing in the product supports. A test suite that only checked
 * the copy was present would pass just as happily on invented testimonials.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { LandingView } from './LandingView';

const html = () => renderToStaticMarkup(<LandingView animate={false} />);

describe('the page explains the product', () => {
  it('names the buyer it is written for', () => {
    expect(html()).toContain('For SEO and digital marketing agencies');
  });

  it('describes the scan in terms of what is actually built', () => {
    const out = html();
    expect(out).toContain('Twenty-four questions, tagged by buying stage');
    expect(out).toContain('six minutes');
  });

  it('leads with a call to action that reaches the sign-up surface', () => {
    expect(html()).toContain('Scan a website');
  });

  it('renders every section it promises, in order', () => {
    const out = html();
    const order = [
      'For SEO and digital marketing agencies',
      'What a scan does',
      'The report',
      'What is different',
      'What you can stand behind',
      'What it does not do yet',
      'Start',
    ];
    let cursor = -1;
    for (const label of order) {
      const at = out.indexOf(label);
      expect(at, `${label} is missing`).toBeGreaterThan(-1);
      expect(at, `${label} is out of order`).toBeGreaterThan(cursor);
      cursor = at;
    }
  });
});

describe('nothing on this page is fabricated', () => {
  it('carries no testimonial, logo wall, user count or press mention', () => {
    const out = html();
    // The specific shapes a page like this is usually padded with. This product
    // has none of them yet, and north-star.md §7's trust argument is worth
    // nothing on a page that opens with an invented one.
    for (const tell of [
      'testimonial',
      'trusted by',
      'as featured in',
      'as seen in',
      'customers say',
      'join thousands',
      'brands trust',
      'reviews',
      '★',
    ]) {
      expect(out.toLowerCase(), `found "${tell}"`).not.toContain(tell);
    }
    // Word-bounded: a bare `toContain('rated')` matches inside "Generated",
    // which is how this assertion first failed on honest copy.
    expect(out.toLowerCase()).not.toMatch(/\brated\b/);
  });

  it('claims no scale it cannot evidence', () => {
    const out = html();
    // Any "N+ brands / agencies / customers / users" construction.
    expect(out).not.toMatch(/\d[\d,]*\s*\+?\s*(brands|agencies|customers|users|companies|teams)/i);
  });

  it('does not name a real client or publish anyone real score', () => {
    const out = html();
    // Epic 9.8 scanned a real, named business. Its score is not this page's to
    // publish — consent was never asked for, and a page arguing that its data
    // discipline is trustworthy cannot open by breaching a client confidence.
    expect(out).not.toContain('psmdigitalagency');
    expect(out).not.toContain('PSM');
    expect(out).not.toContain('28.89');
  });

  it('labels its example chart AS an example, right next to it', () => {
    const out = html();
    expect(out).toContain('Example — illustrative figures, not a real client');
    // The label must sit before the chart in the markup, not in a footnote
    // below the fold where it stops doing its job.
    expect(out.indexOf('illustrative figures')).toBeLessThan(out.indexOf('avp-ledger'));
  });

  it('states what the product cannot do, not only what it can', () => {
    const out = html();
    expect(out).toContain('What it does not do yet');
    // React escapes the apostrophe in static markup, so match around it.
    expect(out).toContain('measures one vendor');
    expect(out).toContain('models today');
    expect(out).toContain('no PDF export yet');
  });

  it('advertises no price or plan, because none is decided', () => {
    const out = html();
    // north-star.md §5.3's tiers are [HYPOTHESIS] and have met no customer.
    // Publishing them would turn a working assumption into a public promise.
    expect(out).not.toMatch(/\$\s?\d/);
    expect(out).not.toContain('per month');
    expect(out).not.toContain('/mo');
    expect(out).toContain('Access is by conversation while this is in pilot');
  });
});

describe('it is built from the design system, not styled locally', () => {
  it('uses the shared section primitive rather than a hand-rolled hero', () => {
    const out = html();
    expect(out).toContain('avp-section');
    expect(out).toContain('avp-section--lead');
  });

  it('shows the product own signature visualisation, not stock artwork', () => {
    const out = html();
    expect(out).toContain('avp-ledger');
    expect(out).toContain('avp-meter');
    // No external asset of any kind: ip-safety.md #4.
    expect(out).not.toContain('<img');
    expect(out).not.toContain('background-image');
  });

  it('carries no ad hoc colour or type value', () => {
    const out = html();
    // The Epic 9.8 failure mode: a class that looks plausible and resolves to
    // nothing. Arbitrary-value and raw-palette utilities are the two shapes
    // that would bypass the preset entirely.
    expect(out).not.toMatch(/class="[^"]*\b(bg|text|border)-\[/);
    expect(out).not.toMatch(/class="[^"]*\b(slate|gray|zinc|blue|red|green)-\d{3}\b/);
  });
});

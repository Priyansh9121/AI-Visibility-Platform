/**
 * White-label branding, and the line it must not cross — Epic 9.22.
 *
 * An agency may change a logo and ONE colour. The interesting tests are all
 * about the second half of that sentence.
 *
 * The report's palette is notation rather than decoration. `--avp-vis-*`
 * encodes the score on a monotonic lightness ramp that survives greyscale and
 * colour-vision deficiency; `--avp-competitor-{1..5}` is deliberately neutral
 * so no rival reads as endorsed or attacked; `--avp-beacon-*` marks the
 * subject BEING SCANNED, who is the prospect and not the agency — a confusion
 * worth naming, because "the brand on this report" and "the brand this report
 * is about" are different brands; and the semantic four say a scan failed or a
 * quota is low. An agency free to recolour any of them changes what the
 * document means.
 *
 * The isolation is structural rather than conventional: `--avp-agency-accent`
 * is set inline on the masthead element and nowhere above it, so it is not in
 * scope for a single beat of the report. These tests assert that property
 * directly, in the spirit of the cross-language and trend sweeps elsewhere —
 * scan the whole rendered surface and fail if the wrong thing appears in it.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ReportView } from './ReportView';
import { helpscoutReport } from '@/lib/report/__fixtures__/reports';

const ACCENT = '#c11574';
const LOGO = 'https://cdn.example/acme-logo.png';

type Report = Parameters<typeof ReportView>[0]['report'];

const render = (report: Report) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} />);

const branded = (overrides: Partial<Report['agency']>): Report => ({
  ...helpscoutReport,
  agency: { ...helpscoutReport.agency, ...overrides },
});

/** Every token family that encodes meaning. None may carry an agency's colour. */
const ENCODED_TOKEN_PREFIXES = ['vis-', 'beacon-', 'competitor-'] as const;
const SEMANTIC_TOKENS = ['success', 'warn', 'danger', 'info'] as const;

describe('an agency can put its own mark on the report', () => {
  it('renders the logo in the masthead in place of the agency name', () => {
    const html = render(branded({ logoUrl: LOGO }));
    expect(html).toContain(`src="${LOGO}"`);
    // The name becomes the alt text rather than disappearing: a reader with
    // images off, and a screen reader, still learn whose report this is.
    expect(html).toContain(`alt="${helpscoutReport.agency.name}"`);
  });

  it('falls back to the agency name when no logo is set', () => {
    const html = render(branded({ logoUrl: null }));
    expect(html).not.toContain('<img');
    expect(html).toContain(helpscoutReport.agency.name);
  });

  it('applies the accent colour to the letterhead rule', () => {
    const html = render(branded({ accentColor: ACCENT }));
    expect(html).toContain('--avp-agency-accent');
    expect(html).toContain(ACCENT);
  });

  it('an unbranded agency renders exactly what it rendered before', () => {
    // The default must be untouched, or every existing agency's report changes
    // the day this ships.
    const plain = render(branded({ logoUrl: null, accentColor: null }));
    expect(plain).not.toContain('--avp-agency-accent');
    expect(plain).toContain('bg-line-hairline');
  });
});

describe('the accent colour cannot reach anything that encodes meaning', () => {
  const html = render(branded({ logoUrl: LOGO, accentColor: ACCENT }));

  it('appears at most in the masthead, never in a beat', () => {
    // The masthead is everything before the first beat. An accent occurring
    // after it would mean the colour had leaked into the document body.
    const firstBeat = html.indexOf('avp-beat--');
    expect(firstBeat).toBeGreaterThan(0);

    let index = html.indexOf(ACCENT);
    const occurrences: number[] = [];
    while (index !== -1) {
      occurrences.push(index);
      index = html.indexOf(ACCENT, index + 1);
    }
    expect(occurrences.length).toBeGreaterThan(0);
    for (const at of occurrences) {
      expect(at).toBeLessThan(firstBeat);
    }
  });

  it('never appears in the same element as an encoded token', () => {
    // Split into elements and check no single tag carries both. This is the
    // assertion that would fail if someone hoisted the variable to the page
    // root "so the footer can use it too".
    const tags = html.match(/<[^>]+>/g) ?? [];
    const offenders = tags.filter((tag) => {
      if (!tag.includes(ACCENT) && !tag.includes('--avp-agency-accent')) return false;
      return (
        ENCODED_TOKEN_PREFIXES.some((p) => tag.includes(p)) ||
        SEMANTIC_TOKENS.some((t) => tag.includes(`-${t}`))
      );
    });
    expect(offenders).toEqual([]);
  });

  it('does not define the accent variable on the page root', () => {
    // Structural isolation: out of scope for the report body means an encoded
    // token could not read it even if a future stylesheet asked.
    const article = html.slice(html.indexOf('<article'), html.indexOf('>', html.indexOf('<article')) + 1);
    expect(article).not.toContain('--avp-agency-accent');
  });
});

describe('branding cannot impersonate the subject or a competitor', () => {
  it('the logo is the agency’s, and the subject keeps its own identity', () => {
    // `--avp-beacon-*` marks the client being scanned. The agency's mark is
    // the seller's; conflating them would make the report look like it was
    // produced BY the company it is about.
    const html = render(branded({ logoUrl: LOGO, accentColor: ACCENT }));
    const subject = helpscoutReport.subject.brandName ?? helpscoutReport.subject.name;
    expect(html).toContain(subject);
    expect(html).toContain(`alt="${helpscoutReport.agency.name}"`);
    expect(helpscoutReport.agency.name).not.toEqual(subject);
  });

  it('carries no agency colour into the competitor series', () => {
    const html = render(branded({ accentColor: ACCENT }));
    const proofAt = html.indexOf('avp-beat--proof');
    expect(proofAt).toBeGreaterThan(0);
    expect(html.slice(proofAt)).not.toContain(ACCENT);
  });
});

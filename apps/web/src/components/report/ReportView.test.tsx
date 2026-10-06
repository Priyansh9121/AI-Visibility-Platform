/**
 * The report screen — Epic 7.
 *
 * Rendered to static markup and asserted over, the same approach the design
 * system's own render tests use. Two things are being checked: that the
 * narrative structure the narrative-report rule mandates is actually present, and — the
 * part that matters most for this epic — that the rendered HTML contains no
 * third-party prose, because this is the first screen that puts collected facts
 * on a page rather than in a table.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { BEAT_SEQUENCE, layoutLedger } from '@avp/design-system';
import { ReportView } from './ReportView';
import type { Report } from '@avp/shared-types';
import { deriveNarrative, toLedgerDimensions } from '@/lib/report/derive';
import {
  AUDIT_FAILURE_GENERIC,
  AUDIT_FAILURE_SENTENCE,
  EXCLUSION_REASON,
  FIX_FOR_DETAIL_CODE,
  ROBOTS_UNREACHABLE_ADVICE,
  ROBOTS_UNREACHABLE_SENTENCE,
  auditFailureSentence,
  checkLabel,
} from '@/lib/report/strings';
import {
  addressNotPublicReport,
  auditFailedExcludedReport,
  crawlDisallowedReport,
  failedAuditReport,
  generatedFixesReport,
  helpscoutReport,
  manualOverrideReport,
  mixedCompetitorSetReport,
  insufficientDataReport,
  noCompetitorSetReport,
  notYetMeasuredReport,
  failedScanReport,
  partialDeadlineReport,
  robotsUnreachableReport,
  robotsUnreachableWithFindingsReport,
  unscoredReport,
  weakSignalReport,
} from '@/lib/report/__fixtures__/reports';

const render = (report: Parameters<typeof ReportView>[0]['report']) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} />);

/** The shared report: the same document, as the person it was sent to opens it. */
const renderPublic = (report: Report) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} publicView />);

/** A report whose technical audit failed with `errorCode`, Technical Foundation left out as `AUDIT_FAILED`. */
const failedWith = (errorCode: string | null): Report => ({
  ...auditFailedExcludedReport,
  audit: { ...auditFailedExcludedReport.audit!, errorCode },
});

/** The proof beat: where the audit's card is drawn. */
const proofBeat = (html: string): string =>
  html.slice(html.indexOf('avp-beat--proof'), html.indexOf('avp-beat--fix'));

/**
 * The card the proof beat draws for an audit that failed with a code the three crawl reasons
 * do not name, as the markup has it: a title-only line, then its sentence. The crawl-code
 * cards carry their reason's label as a badge in the title line, so they are not it. Null when
 * the page has no such card.
 */
const auditCard = (html: string): { title: string; sentence: string } | null => {
  const m = proofBeat(html).match(
    /<p class="text-ui-base font-medium text-text-primary">([^<]*)<\/p><p class="mt-2 text-ui-sm[^"]*">([^<]*)<\/p>/,
  );
  return m ? { title: m[1]!, sentence: m[2]! } : null;
};

/**
 * What that card said until 2026-10-05 (launch checklist 3.87, follow-up): that the site "could
 * not be read" and a crawl "did not complete", though a busy crawler never began one and an
 * error status was the page's own answer, and a rule about sites the crawler could not read
 * that said nothing of the score.
 */
const OLD_CARD_WORDS = [
  'The site could not be read',
  'The crawl of',
  'did not complete, so nothing was measured from the page',
  'A site the crawler could not read',
  'is not a site with a bad technical foundation',
  'the dimension is left unscored rather than scored zero',
];

/** The card's sentence: what happened, in the code's own words, then what is true of every failure. */
const cardSentence = (errorCode: string | null, address: string | null | undefined): string =>
  `${auditFailureSentence(errorCode)} The technical check of ${address ?? 'the site'} could not be completed for this scan, so nothing was measured from the page and Technical Foundation is left out of the score.`;

describe('the narrative structure is the page', () => {
  it('renders all five beats, in order', () => {
    const html = render(helpscoutReport);
    const positions = BEAT_SEQUENCE.map((id) => html.indexOf(`avp-beat--${id}`));
    expect(positions.every((p) => p >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it('leads with the score, not a grid of tiles', () => {
    const html = render(helpscoutReport);
    expect(html.indexOf('avp-beat--score')).toBeLessThan(html.indexOf('avp-beat--proof'));
    expect(html).toContain('avp-score');
  });

  it('states the biggest gap as a claim in the heading', () => {
    // Computed from the stored sub-scores, so the heading cannot disagree with
    // the chart beneath it.
    expect(render(helpscoutReport)).toContain('Citation Strength is costing the most');
  });

  it('carries the agency’s identity, not ours', () => {
    const html = render(helpscoutReport);
    expect(html).toContain('Scoring Verification');
    expect(html).not.toContain('AI Visibility Platform');
  });
});

describe('the score beat is composed, not listed — Epic 16', () => {
  const header = (html: string) => html.slice(0, html.indexOf('avp-beat--score'));
  const scoreBeat = (html: string) =>
    html.slice(html.indexOf('avp-beat--score'), html.indexOf('avp-beat--gap'));
  const gapBeat = (html: string) =>
    html.slice(html.indexOf('avp-beat--gap'), html.indexOf('avp-beat--proof'));

  it('credentials the scan as five shaped facts, not a run of grey text', () => {
    const head = header(render(helpscoutReport));
    expect(head.match(/avp-report__meta-item/g)).toHaveLength(5);
    expect(head).toContain('avp-chip--mono');
    expect(head).toContain('helpscout.com');
    expect(head).toContain('3 prompts');
    expect(head).toContain('2 engines');
    // Every glyph is decorative and says so; the text beside it is the fact.
    expect(head.match(/<span class="avp-chip__icon" aria-hidden="true">/g)).toHaveLength(5);
  });

  it('frames the numeral and its explanation as one unit', () => {
    const beat = scoreBeat(render(helpscoutReport));
    expect(beat).toContain('avp-scoreblock');
    expect(beat.indexOf('avp-scoreblock__figure')).toBeLessThan(beat.indexOf('avp-scoreblock__explain'));
    expect(beat.indexOf('avp-score__numeral')).toBeLessThan(beat.indexOf('avp-prose'));
  });

  it('draws the ledger compact beside the numeral, and in full only in the gap beat', () => {
    const html = render(helpscoutReport);
    expect(scoreBeat(html).match(/avp-ledger--compact/g)).toHaveLength(1);
    expect(gapBeat(html)).toContain('avp-ledger__svg');
    expect(gapBeat(html)).not.toContain('avp-ledger--compact');
    // Still the one chart: the compact column's hidden table carries the composite.
    expect(scoreBeat(html)).toContain('58 / 100');
  });

  it('draws no small column when there is no score to draw', () => {
    for (const report of [insufficientDataReport, unscoredReport]) {
      const html = render(report);
      expect(scoreBeat(html)).not.toContain('avp-ledger');
      expect(scoreBeat(html)).not.toContain('avp-badge--visibility');
    }
  });

  it('paints the badge from the same rounded score as the numeral', () => {
    // The fixture's composite is 58.24. Rounded, the numeral is 58; the badge
    // used to be handed 58.24 and interpolate the ramp a fraction away — a
    // colour that matched by coincidence. Now both come from one number.
    const beat = scoreBeat(render(helpscoutReport));
    const numeral = beat.match(/avp-score__numeral" style="color:(oklch\([^)]*\))/)?.[1];
    const badge = beat.match(/avp-badge--visibility avp-score__badge" style="background:(oklch\([^)]*\))/)?.[1];
    expect(numeral).toBeDefined();
    expect(badge).toBe(numeral);
    expect(beat.indexOf('avp-score__badge')).toBeGreaterThan(beat.indexOf('avp-score__band'));
  });
});

describe('degraded data renders as degraded, never as bad', () => {
  it('INSUFFICIENT_DATA shows no score at all rather than a zero', () => {
    const html = render(insufficientDataReport);
    expect(html).toContain('avp-score--empty');
    expect(html).toContain('not enough data');
    // No ledger is drawn at all. The component has an empty state, but mounting
    // a chart with nothing to plot only repeats the sentence above it — the gap
    // beat explains the absence in words instead.
    expect(html).not.toContain('avp-ledger__svg');
    expect(html).toContain('No gap can be measured until there is a score.');
    // And nowhere on the page does the absence become a zero.
    expect(html).not.toMatch(/>0<\/text>/);
    expect(html).toContain('—');
  });

  it('an unscored scan says so, and still shows its evidence', () => {
    const html = render(unscoredReport);
    expect(html).toContain('has not been scored');
    expect(html).toContain('avp-beat--proof');
  });

  it('states a shared exclusion reason once, not once per dimension', () => {
    // Every dimension on an unscoreable scan carries the same reason. Printing
    // the identical paragraph five times reads as a fault in the page.
    const html = render(insufficientDataReport);
    const occurrences = html.split('No engine returned an answer for this scan, so there is').length - 1;
    expect(occurrences).toBe(1);
    expect(html).toContain('Every dimension');
    // And it still names what was excluded, with what each was worth.
    expect(html).toContain('Mention Rate (30%)');
  });

  it('does not print a heading over an empty engine list', () => {
    expect(render(insufficientDataReport)).not.toContain('What each engine returned');
    expect(render(helpscoutReport)).toContain('What each engine returned');
  });

  it('distinguishes the three exclusion reasons in words a viewer can tell apart', () => {
    expect(render(noCompetitorSetReport)).toContain('No comparison was made');
    expect(render(notYetMeasuredReport)).toContain('Not yet checked');
    expect(render(insufficientDataReport)).toContain('No answers came back');
  });

  it('says an excluded dimension is excluded, not zero', () => {
    const html = render(noCompetitorSetReport);
    expect(html).toContain('Share of Voice');
    expect(html).toContain('normally 25% of the score');
    expect(html).toContain('rather than awarding points for a comparison that could not be made');
  });

  it('a weak competitor set carries its caveat next to the comparison', () => {
    const html = render(weakSignalReport);
    expect(html).toContain('Weakly corroborated');
    expect(html).toContain('Treat this rival set as a starting point.');
    // A correction never reaches this report, its PDF or its share link, so it is not asked for.
    expect(html).not.toContain('correct it');
    expect(html).not.toContain('sending the report on');
  });

  it('a failed audit says the technical check could not be completed, and does not blame the site', () => {
    const html = render(failedWith('FETCH_FAILED'));
    const card = auditCard(html);
    // The reason's own label for a title, and a sentence true of every failure, a busy crawler
    // that never started and a page that answered with an error included: the check could not
    // be completed, nothing was measured from the page, and the dimension is left out of the
    // score. It says nothing of a read, and puts nothing on the site.
    expect(card?.title).toBe(EXCLUSION_REASON.AUDIT_FAILED!.label);
    expect(card?.sentence).toBe(cardSentence('FETCH_FAILED', 'https://helpscout.com'));
    expect(html).not.toContain('FETCH_FAILED');
    expect(html).toContain(AUDIT_FAILURE_SENTENCE.FETCH_FAILED);
    for (const words of OLD_CARD_WORDS) expect(html, words).not.toContain(words);
    expect(html).not.toContain('A site we could not reach');
    expect(html).not.toContain('no structural signals were collected');
  });

  it('says one plain sentence per failure code, and never the code, signed in or public', () => {
    const cases: [string, string][] = [
      ['BROWSER_ERROR', AUDIT_FAILURE_SENTENCE.BROWSER_ERROR!],
      ['FETCH_FAILED', AUDIT_FAILURE_SENTENCE.FETCH_FAILED!],
      ['TIMEOUT', AUDIT_FAILURE_SENTENCE.TIMEOUT!],
      ['BROWSER_BUSY', AUDIT_FAILURE_SENTENCE.BROWSER_BUSY!],
      ['HTTP_503', auditFailureSentence('HTTP_503')],
      ['SOMETHING_NEW', auditFailureSentence('SOMETHING_NEW')],
    ];
    expect(new Set(cases.map(([, sentence]) => sentence)).size).toBe(cases.length);
    expect(cases[4]![1]).toContain('HTTP 503');
    expect(cases[5]![1]).toBe(AUDIT_FAILURE_GENERIC);
    // An audit that failed with no code at all says the generic sentence, as an unknown code does.
    const every: [string | null, string][] = [...cases, [null, AUDIT_FAILURE_GENERIC]];
    const address = auditFailedExcludedReport.audit!.urlAudited;
    for (const [code, sentence] of every) {
      const label = String(code);
      for (const html of [render(failedWith(code)), renderPublic(failedWith(code))]) {
        expect(auditCard(html)?.title, label).toBe(EXCLUSION_REASON.AUDIT_FAILED!.label);
        expect(auditCard(html)?.sentence, label).toBe(cardSentence(code, address));
        expect(html, label).toContain(sentence);
        for (const words of OLD_CARD_WORDS) {
          expect(html, `${label}: ${words}`).not.toContain(words);
        }
        expect(html, label).not.toContain('A site we could not reach');
        if (code !== null) expect(html, label).not.toContain(code);
        expect(html, label).not.toContain('(HTTP_');
      }
    }
  });

  it('the card keeps the reason’s two claims, with the address it audited, or “the site” when it has none', () => {
    // The score beat shows the reason (`EXCLUSION_REASON.AUDIT_FAILED`) and this card sits in the
    // same report, so it must not say less, or other, than that. "This scan" is true here: a
    // report is of one scan, signed in or shared. If the reason is reworded, this fails.
    const reason = EXCLUSION_REASON.AUDIT_FAILED!.detail;
    const checkClaim = 'The technical check of the site could not be completed for this scan';
    const leftOut = 'Technical Foundation is left out of the score.';
    expect(reason).toContain(checkClaim);
    expect(reason).toContain(leftOut);
    const bare: Report = {
      ...failedWith('TIMEOUT'),
      audit: { ...failedWith('TIMEOUT').audit!, urlAudited: null },
    };
    for (const html of [render(bare), renderPublic(bare)]) {
      const sentence = auditCard(html)?.sentence ?? '';
      expect(sentence).toContain(checkClaim);
      expect(sentence.endsWith(`and ${leftOut}`)).toBe(true);
    }
    for (const html of [render(failedWith('TIMEOUT')), renderPublic(failedWith('TIMEOUT'))]) {
      expect(auditCard(html)?.sentence).toContain(
        'The technical check of https://helpscout.com could not be completed for this scan, so nothing was measured from the page',
      );
    }
  });

  it('the three crawl codes never get that card, or its title', () => {
    // They have their own words. This card is for every other failure and claims nothing of them.
    for (const code of ['ROBOTS_DISALLOWED', 'ROBOTS_UNREACHABLE', 'ADDRESS_NOT_PUBLIC']) {
      for (const html of [render(failedWith(code)), renderPublic(failedWith(code))]) {
        expect(auditCard(html), code).toBeNull();
        expect(proofBeat(html), code).not.toContain(EXCLUSION_REASON.AUDIT_FAILED!.label);
      }
    }
  });

  it('a missing audit says the check has not run', () => {
    const html = render(notYetMeasuredReport);
    expect(html).toContain('has not been audited');
    expect(html).toContain('not a check that failed');
  });

  it('explains a degradation flag rather than printing the code', () => {
    // The Help Scout fixture is a v1.1 score, so it carries NO_AUTHORITY_DATA
    // as a flag. Since v2.1 the sentence says what that formula actually did
    // rather than claiming a comparison the arithmetic never made.
    const html = render(helpscoutReport);
    expect(html).toContain('no external authority source');
    expect(html).toContain('cannot rise above about a point for any site');
    expect(html).not.toContain('NO_AUTHORITY_DATA');
  });

  it('shows average position beside the rivals, not named for a brand with none, never 0', () => {
    // v4. The Help Scout fixture predates the field, so every row is "not
    // named" — which is exactly the cell that must never read as 0.
    const html = render(helpscoutReport);
    expect(html).toContain('Average position');
    expect(html).toContain('Not named');
    expect(html).toContain('does not enter the score');
    const withPositions = {
      ...helpscoutReport,
      score: { ...helpscoutReport.score, averagePosition: '2.40', positionCount: 9 },
    } as unknown as Report;
    expect(render(withPositions)).toContain('2.4 across 9 answers');
  });

  it('says what Citation Strength is measured from, limitation included', () => {
    // v3. The Help Scout fixture carries the dimension as included, so the
    // note prints; it must name the proxy for what it is. And since v5, what
    // it asks: how a brand is backed when it is named — in 45 words or fewer
    // since 2026-09-29.
    const html = render(helpscoutReport);
    expect(html).toContain(
      'When AI names the brand, how often does it back it with a trusted source?',
    );
    expect(html).toContain('uncalibrated industry labels');
    expect(html).toContain('coarse proxy');
    expect(html).toContain('only answers to questions that did not name the brand');
  });
});

describe('the fix beat names specific changes', () => {
  it('turns the real NO_FAQ_SCHEMA warning into a concrete instruction', () => {
    const html = render(helpscoutReport);
    expect(html).toContain('Add FAQPage schema to the pages that answer buyer questions');
  });

  it('shows the recoverable points beside the fixes that have them', () => {
    expect(render(helpscoutReport)).toContain('+19.3 pts');
  });

  it('offers nothing to fix when nothing was measured', () => {
    expect(render(insufficientDataReport)).toContain('nothing specific to fix');
  });
});

describe('the pitch is arithmetic, not sales language', () => {
  it('projects a composite from the points on the page', () => {
    const html = render(helpscoutReport);
    expect(html).toContain('58 today. 99 with the fixes above.');
  });

  it('makes no claim it cannot support', () => {
    const html = render(helpscoutReport).toLowerCase();
    for (const unsupported of [
      'revenue', 'roi', 'traffic', 'leads', 'conversion', 'guarantee',
      'million', 'x more', 'skyrocket', 'dominate', 'act now', 'limited time',
    ]) {
      expect(html).not.toContain(unsupported);
    }
  });

  it('declines to project when there is no score to project from', () => {
    expect(render(insufficientDataReport)).toContain('no projection to make');
  });
});

// ---------------------------------------------------------------------------
// The facts-only rule — the render gate.
//
// Epic 7 is the first epic that RENDERS the facts other epics collected. The
// database rule has been enforced since Epic 4; what is new is that a violation
// would now be visible on a page in front of a prospect. These assertions run
// against the actual emitted HTML rather than against a schema.
// ---------------------------------------------------------------------------

describe('facts-only: the rendered page carries facts only', () => {
  const html = render(helpscoutReport);

  it('shows every citation as a domain and a link, never as quoted text', () => {
    // The real scan cited 45 sources. Each appears as its domain, linked out.
    expect(html).toContain('helpscout.com');
    expect(html).toContain('<a href="https://');
    expect(html).toContain('rel="noreferrer nofollow"');
    // A quotation would need quote marks around borrowed prose. The only
    // typographic quotes on the page are in our own copy's apostrophes.
    expect(html).not.toContain('<blockquote');
    expect(html).not.toContain('&ldquo;');
  });

  it('attributes a cited domain to the competitor it belongs to', () => {
    // The real scan cites zendesk.com and front.com once each. Those rows lead
    // the "cited instead" table, because who is cited in the subject's place is
    // the evidence the proof beat exists to show.
    expect(html).toContain('zendesk.com');
    expect(html).toContain('front.com');
    const cited = html.slice(html.indexOf('Cited instead'));
    expect(cited.indexOf('front.com')).toBeLessThan(cited.indexOf('eesel.ai'));
  });

  it('shows every competitor as a name and a domain, and nothing else', () => {
    for (const competitor of helpscoutReport.competitorSet!.competitors) {
      expect(html).toContain(competitor.name);
    }
    // There is no description to render — the column does not exist upstream.
    for (const competitor of helpscoutReport.competitorSet!.competitors) {
      expect((competitor as Record<string, unknown>).description).toBeUndefined();
      expect((competitor as Record<string, unknown>).tagline).toBeUndefined();
    }
  });

  it('renders no engine answer text, because there is none to render', () => {
    // EngineResult has no text-bearing column at all (asserted in the API suite
    // against the real column types). This is the UI-side restatement: the
    // Evidence primitive takes a prompt and label/value facts, and there is no
    // prop through which prose could arrive.
    const payload = JSON.stringify(helpscoutReport);
    for (const field of ['"answer"', '"text"', '"snippet"', '"excerpt"', '"responseText"']) {
      expect(payload).not.toContain(field);
    }
  });

  it('resolves audit findings through our own string table, not stored prose', () => {
    // The API sends NO_FAQ_SCHEMA; the page says what we say it means.
    expect(html).not.toContain('NO_FAQ_SCHEMA');
    expect(html).toContain('FAQPage schema');
  });

  it('uses no off-system colour value', () => {
    // The Tailwind preset removes the stock palette, so an off-system class
    // could not compile — but inline styles bypass Tailwind entirely, so any
    // raw hex or rgb() in the markup is checked directly. The design system's
    // own components emit oklch() from the token ramp, which is expected.
    const inlineStyles = [...html.matchAll(/style="([^"]*)"/g)].map((m) => m[1]!).join(';');
    expect(inlineStyles).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(inlineStyles).not.toMatch(/\brgba?\(/i);
  });
});

/**
 * Reports built from the fixtures for launch checklist 3.49's fix-beat sentences: a list with no
 * point figure, a list of one, and a list the wording was generated for. Gaps are
 * `weight x (100 - subscore) / 100`, so a subscore of 99 leaves every dimension under the 2 points
 * a fix needs, and 100 leaves none.
 */
type Findings = NonNullable<Report['audit']>['findings'];
const finding = (checkKey: string, status: string, detailCode: string) =>
  ({ id: `tchk_${checkKey}`, checkKey, status, value: null, detailCode }) as Findings[number];
const withFindings = (base: Report, findings: Findings): Report => ({
  ...base,
  audit: { ...base.audit!, findings },
});
const withoutUnclaimedDomain = (base: Report): Report => ({
  ...base,
  proof: { ...base.proof!, unclaimedCitedDomains: [] },
});
const withSubscores = (base: Report, subscores: Record<string, string>): Report => ({
  ...base,
  dimensions: base.dimensions.map((d) => {
    const subscore = subscores[d.key];
    return subscore === undefined ? d : { ...d, subscore };
  }),
});
const ALL_NEARLY_LIT = {
  mention_rate: '99.00',
  share_of_voice: '99.00',
  citation_strength: '99.00',
  sentiment: '99.00',
  technical_foundation: '99.00',
};
/** Scored, but every gap is under 2 points: the fix list is the audit's findings and the citation. */
const scoredWithNoGapToFix = withSubscores(helpscoutReport, ALL_NEARLY_LIT);
/** One fix with a figure, and nothing else: the citation gap, no findings, no unclaimed domain. */
const oneGapOnly = withoutUnclaimedDomain(
  withFindings(withSubscores(helpscoutReport, { share_of_voice: '100.00', sentiment: '100.00' }), []),
);

describe('the fix beat with Epic 8 generated copy', () => {
  const html = render(generatedFixesReport);

  it('renders the generated instruction rather than the string-table one', () => {
    expect(html).not.toContain('Add FAQPage schema to the pages that answer buyer questions');
    expect(html).toContain('Add FAQPage markup to the pages that answer buyer questions');
  });

  it('names the biggest gap concretely, per §7 Epic 8', () => {
    // "correctly names those gaps with actionable language" — the citation
    // gap named with the domains that actually took the citations.
    expect(html).toContain('45 citations were collected and only 3 pointed at helpscout.com');
    expect(html).toContain('eesel.ai');
  });

  it('still shows the same point figures the chart drew', () => {
    // The generator never touched these. Same chip, same arithmetic.
    expect(html).toContain('+19.3 pts');
    expect(html).toContain('58 today. 99 with the fixes above.');
  });

  it('makes no claim it cannot support, in GENERATED copy', () => {
    // The same sweep as the deterministic report, run over model-authored
    // text. This is the assertion the server-side BANNED_CLAIMS check in
    // services/fix_generator.py exists to keep true.
    const lowered = html.toLowerCase();
    for (const unsupported of [
      'revenue', 'roi', 'traffic', 'leads', 'conversion', 'guarantee',
      'million', 'x more', 'skyrocket', 'dominate', 'act now', 'limited time',
    ]) {
      expect(lowered).not.toContain(unsupported);
    }
  });

  it('renders no raw detail code, even though the generator was given them', () => {
    // The generator sees NO_FAQ_SCHEMA and must write what it means. A code
    // reaching the page would be the Epic 7 guarantee broken by the enrichment.
    expect(html).not.toContain('NO_FAQ_SCHEMA');
    expect(html).not.toContain('NO_PRODUCT_OR_SERVICE_SCHEMA');
  });

  it('says the wording was generated and the arithmetic was not', () => {
    expect(html).toContain('written for this scan from its own figures');
  });

  it('says what the wording did not change: which changes appear, their order and the points are computed, not written — launch checklist 3.49', () => {
    // It used to call all three "unchanged arithmetic — the same gap calculation that drew the
    // chart", and then said the points came "by the same gap calculation" as well: the paragraph
    // above it already says where the points come from. Only the points are the gap calculation:
    // the order puts a fix for a noindex tag, a Disallow: / rule or a missing robots.txt first and
    // sorts the fixes with no figure by id, and which changes appear follows rules (a cap, the
    // audit's findings, the unclaimed domain). The PDF says the same (`test_report_pdf.py`).
    expect(html).toContain(
      'Which changes appear, their order, and the points beside them are computed, not written.',
    );
    expect(html).not.toContain('computed, not written, the points by');
    expect(html).not.toContain('unchanged arithmetic');
  });

  it('says only what is on the page: with no point figure there are no points beside the changes', () => {
    // The wording is generated for the audit's findings too, so a list with no figure can carry it.
    const report = withSubscores(generatedFixesReport, ALL_NEARLY_LIT);
    const fixes = deriveNarrative(report).fixes;
    expect(fixes.some((f) => f.generated)).toBe(true);
    expect(fixes.every((f) => f.pointsUpside === undefined)).toBe(true);
    const page = render(report);
    expect(page).toContain('Which changes appear and their order are computed, not written.');
    expect(page).not.toContain('the points beside them');
  });

  it('is true of the list: the wording lies over the derived list and moves no change, no order and no figure', () => {
    const derived = deriveNarrative({ ...generatedFixesReport, actionItems: [] }).fixes;
    const worded = deriveNarrative(generatedFixesReport).fixes;
    expect(worded.some((f) => f.generated)).toBe(true);
    expect(derived.some((f) => f.generated)).toBe(false);
    expect(worded.map((f) => [f.id, f.source, f.pointsUpside])).toEqual(
      derived.map((f) => [f.id, f.source, f.pointsUpside]),
    );
    // And the figures are the chart's own: each gap fix carries its segment's gap, from the design
    // system's `layoutLedger`, the function that draws the chart.
    const chart = new Map(
      layoutLedger(toLedgerDimensions(generatedFixesReport.dimensions)).segments.map((s) => [s.key, s.gap]),
    );
    const gapFixes = worded.filter((f) => f.source === 'gap');
    expect(gapFixes.length).toBeGreaterThan(0);
    for (const fix of gapFixes) {
      const gap = chart.get(fix.id.replace('gap:', ''));
      expect(gap, fix.id).toBeDefined();
      expect(fix.pointsUpside, fix.id).toBe(Math.round(gap! * 10) / 10);
    }
  });

  it('says it only when some wording was generated', () => {
    for (const report of [helpscoutReport, crawlDisallowedReport]) {
      expect(render(report)).not.toContain('computed, not written');
    }
  });

  it('uses no off-system colour value', () => {
    const inlineStyles = [...html.matchAll(/style="([^"]*)"/g)].map((m) => m[1]!).join(';');
    expect(inlineStyles).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(inlineStyles).not.toMatch(/\brgba?\(/i);
  });
});

describe('competitor provenance and the override affordance — Epic 3.6', () => {
  it('marks a rival the agency named, and says what the mark means', () => {
    const html = render(manualOverrideReport);
    expect(html).toContain('set by hand');
    expect(html).toContain('were named by the agency rather than found by');
  });

  it('marks nothing when every rival came from detection', () => {
    // The badge is provenance, not decoration. A fully auto-detected set must
    // carry no mark at all, or the mark stops meaning anything.
    const html = render(helpscoutReport);
    expect(html).not.toContain('set by hand');
    expect(html).not.toContain('were named by the agency');
  });

  it('never marks the subject as set by hand', () => {
    // The subject row is the client, not a detected rival, and has no
    // isManualOverride to read — it must not inherit the flag from anywhere.
    const html = render(manualOverrideReport);
    const start = html.indexOf('is-subject');
    // To the row's own closing tag. A fixed-width slice runs into the next
    // <tr>, which in this fixture IS the marked one — the first version of
    // this test failed for that reason rather than for a real defect.
    const subjectRow = html.slice(start, html.indexOf('</tr>', start));
    expect(subjectRow).toContain('Help Scout');
    expect(subjectRow).not.toContain('set by hand');
  });

  it('renders no editing affordance without the slot', () => {
    // A report rendered for a client is a document. The editor is operator
    // chrome and exists only when a caller passes it.
    const html = render(manualOverrideReport);
    expect(html).not.toContain('Correct this list');
    expect(html).not.toContain('Correct the competitor list');
  });

  it('renders the slot under the competitor table when one is passed', () => {
    const html = renderToStaticMarkup(
      <ReportView
        report={manualOverrideReport}
        animate={false}
        competitorEditor={<p>operator chrome</p>}
      />,
    );
    expect(html).toContain('operator chrome');
    // Under the rivals table, not floating at the end of the document.
    expect(html.indexOf('operator chrome')).toBeGreaterThan(html.indexOf('Rivals in the same'));
    expect(html.indexOf('operator chrome')).toBeLessThan(html.indexOf('avp-beat--fix'));
  });

  it('stays facts-only with an override present', () => {
    // The facts-only rule: a manually added competitor is a name and a domain, the
    // same as a detected one. The override path introduces no prose field.
    const payload = JSON.stringify(manualOverrideReport);
    for (const field of ['"description"', '"tagline"', '"summary"', '"positioning"', '"note"']) {
      expect(payload).not.toContain(field);
    }
  });

  it('makes no claim it cannot support, with an override present', () => {
    const lowered = render(manualOverrideReport).toLowerCase();
    for (const unsupported of [
      'revenue', 'roi', 'traffic', 'leads', 'conversion', 'guarantee',
      'million', 'x more', 'skyrocket', 'dominate', 'act now', 'limited time',
    ]) {
      expect(lowered).not.toContain(unsupported);
    }
  });
});

describe('the corroboration figure says what it covers — Finding 3, Epic 3.11', () => {
  it('renders the figure at all', () => {
    // It was computed, persisted, and projected since Epic 3, and never put on
    // the page. The note under the table meanwhile pointed the reader at "the
    // corroboration figure above", which was not there to look at.
    const html = render(helpscoutReport);
    expect(html).toContain('80%');
    expect(html).toContain('Search results and AI answers independently surfaced');
  });

  it('states the scope when part of the list was set by hand', () => {
    // The whole of Finding 3. 0.750 over a five-row table whose first row
    // nothing corroborated is a claim about four rows, and the page has to say
    // so — otherwise the reader carries it across all five.
    const html = render(mixedCompetitorSetReport);
    expect(html).toContain('75%');
    expect(html).toContain('4 rivals detection found');
    expect(html).toContain('The remaining 1 of the 5 below were set by hand');
  });

  it('adds no scope caveat when detection produced the whole list', () => {
    // The caveat has to be absent when it does not apply, or it reads as
    // boilerplate and stops being information.
    const html = render(helpscoutReport);
    expect(html).toContain('5 rivals detection found');
    expect(html).not.toContain('The remaining');
  });

  it('renders no figure when there is none to render', () => {
    // A null confidence means the two signals could not be compared — one of
    // them did not run, or an operator replaced the set. Rendering 0% there
    // would assert that they looked and disagreed.
    const html = render(manualOverrideReport);
    expect(html).not.toContain('Search results and AI answers independently surfaced');
  });

  it('does not fabricate a percentage from a set nothing was detected in', () => {
    const html = render(noCompetitorSetReport);
    expect(html).not.toContain('Search results and AI answers independently surfaced');
  });
});

describe('the answer shelf — Epic 7.1, Direction A', () => {
  const html = render(helpscoutReport);

  it('renders one row per answer, additively — the tables are untouched', () => {
    // The decision was additive: the shelf answers what the aggregate tables
    // structurally cannot, and the tables keep answering what they always did.
    expect(html).toContain('avp-shelf');
    expect(html).toContain('Who got named');       // mention-share table
    expect(html).toContain('Sources these answers cited'); // citation tables
  });

  it('labels rows with our own generated questions', () => {
    // The facts-only rule permits our own content. These are the prompts this
    // system wrote and sent — the same strings /scans/{id}/prompts returns.
    expect(html).toContain('help scout vs zendesk for a small team');
    expect(html).toContain('shared inbox tool for a small support team');
  });

  it('states the finding in the chart title rather than labelling the chart', () => {
    // Help Scout is named in all six answers on this real scan.
    expect(html).toContain('Help Scout appears in every answer this scan measured');
  });

  it('carries the accessibility contract ChartFrame requires', () => {
    expect(html).toMatch(/role="img" aria-label="[^"]+"/);
    expect(html).toContain('Across 6 answers, Help Scout was named in 6');
    // And the hidden table equivalent, which exported PDFs carry into the
    // accessibility tree.
    expect(html).toContain('avp-visually-hidden');
    expect(html).toContain('Brands named, in order');
  });

  it('reproduces the real ordinals rather than a ranking of its own', () => {
    // On this scan Help Scout is named FIRST in every answer and cited in only
    // two of them — the shelf shows both facts, which is the argument.
    expect(html).toContain('Named 1st');
  });

  it('names rivals but reproduces no engine or competitor prose', () => {
    // Entity names are facts (the facts-only rule). Anything describing them is not.
    expect(html).toContain('Zendesk');
    for (const word of ['snippet', 'excerpt', 'according to', 'the answer said']) {
      expect(html.toLowerCase()).not.toContain(word);
    }
  });

  it('emits no off-system colour from the new chart', () => {
    const inlineStyles = [...html.matchAll(/style="([^"]*)"/g)].map((m) => m[1]!).join(';');
    expect(inlineStyles).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(inlineStyles).not.toMatch(/\brgba?\(/i);
    // Chart marks are painted with SVG attributes, not style="", so those are
    // swept too — the ramp must not reach a competitor.
    const paints = [...html.matchAll(/(?:fill|stroke)="([^"]*)"/g)].map((m) => m[1]!);
    for (const paint of paints) {
      expect(paint).not.toMatch(/#[0-9a-f]{3,8}\b/i);
      expect(paint).not.toMatch(/\brgba?\(/i);
    }
  });

  it('is absent, not broken, when a scan has no shelf to draw', () => {
    const degraded = render(insufficientDataReport);
    expect(degraded).not.toContain('avp-shelf');
    // and the beat still renders its other evidence
    expect(degraded).toContain('proof');
  });
});

describe('the unclaimed-domain fix — Epic 7.1, Direction C', () => {
  const html = render(helpscoutReport);

  it('names the heaviest domain nobody owns as a concrete change', () => {
    // The real scan: eesel.ai cited 6 times, helpscout.com 3. Before this the
    // fix beat could only ever say "improve citation strength".
    expect(html).toContain('Get onto eesel.ai');
    expect(html).toContain('cited 6 times');
  });

  it('says the domain belongs to neither the subject nor a rival', () => {
    expect(html).toContain('belongs to neither you nor any rival in this scan');
  });

  it('lists the runners-up so the fix is a plan, not a single bet', () => {
    expect(html).toContain('featurebase.app');
    expect(html).toContain('hiverhq.com');
  });

  it('does not crowd out the concrete audit fixes Epic 7 protected', () => {
    expect(html).toContain('FAQPage schema');
    expect(html).toContain('Audit fixes carry no point figure');
  });

  it('describes the domain by name and count only, never by its content', () => {
    // The facts-only rule: we have never read the cited page, and nothing here
    // claims to know what is on it. Asserted over the fix's OWN strings rather
    // than the whole document, so the page's `<article>` tag cannot mask it.
    const fix = deriveNarrative(helpscoutReport).fixes.find((f) => f.source === 'citation')!;
    const text = `${fix.title} ${fix.detail}`.toLowerCase();
    for (const word of [
      'article', 'blog post', 'review of', 'they say', 'writes', 'claims that',
      'according to', 'guide to', 'page about',
    ]) {
      expect(text).not.toContain(word);
    }
    // What it DOES contain is a domain and two counts.
    expect(text).toContain('eesel.ai');
    expect(text).toMatch(/\b6 times\b/);
  });

  it('does not borrow the audit disclaimer to explain its own missing points', () => {
    // The discriminating case, and the reason the disclaimer stays keyed to
    // `source === 'audit'` rather than to "has no points". A failed audit
    // yields no audit fixes, but the citations that same scan collected still
    // yield the domain fix — which also carries no point figure. Keying the
    // note on the absence of points would print "Audit fixes carry no point
    // figure" on a report with no audit fix in the list at all.
    const narrative = deriveNarrative(failedAuditReport);
    expect(narrative.fixes.some((f) => f.source === 'citation')).toBe(true);
    expect(narrative.fixes.some((f) => f.source === 'audit')).toBe(false);

    const out = render(failedAuditReport);
    expect(out).toContain('Get onto eesel.ai');
    expect(out).not.toContain('Audit fixes carry no point figure');
  });

  it('offers no domain fix when nothing unclaimed cleared the floor', () => {
    const degraded = render(insufficientDataReport);
    expect(degraded).not.toContain('Get onto');
  });
});

/**
 * THE REPORT/MARKETING BOUNDARY — Epic 9.16.
 *
 * `LuminanceLedger` is rendered by two things with opposite requirements. The
 * landing page wants the chart to build bar by bar as you scroll to it. The
 * report is a DOCUMENT: it is printed, it is PDF'd, and it is put in front of a
 * prospect's CMO. `design-direction.md` §0 makes the presenting context win
 * ties, and §5's Direction C was declined partly because motion does not
 * survive becoming a document.
 *
 * The guardrail is that `staggerDimensions` defaults to `false` and neither
 * report route passes it. These tests are what stop that being a promise. They
 * fail if someone threads the new behaviour through the report by mistake — by
 * passing the prop, by flipping the default, or by coupling it to `animate`,
 * which the report DOES set and which is `true` on both routes in production.
 */
describe('the report never acquires the landing page motion', () => {
  const REPORTS = [
    ['helpscout', helpscoutReport],
    ['unscored', unscoredReport],
    ['no competitor set', noCompetitorSetReport],
    ['weak signal', weakSignalReport],
  ] as const;

  for (const [name, report] of REPORTS) {
    it(`carries no stagger markup — ${name}`, () => {
      const html = render(report);
      expect(html).not.toContain('avp-ledger--staggered');
      expect(html).not.toContain('--avp-ledger-index');
    });
  }

  it('carries none of it with animation ON, which is what production sends', () => {
    // The discriminating case. Every other test in this file renders with
    // `animate={false}`, so a stagger accidentally coupled to `animate` would
    // be invisible to all of them — and both report routes leave `animate` at
    // its default of `true`. This is the render that would catch it.
    const html = renderToStaticMarkup(<ReportView report={helpscoutReport} animate />);
    expect(html).toContain('avp-ledger');
    expect(html).not.toContain('avp-ledger--staggered');
    expect(html).not.toContain('--avp-ledger-index');
  });

  it('carries none of it in the public share view either', () => {
    // `/share` renders the same component with `publicView`, and is the
    // route a prospect actually opens.
    const html = renderToStaticMarkup(<ReportView report={helpscoutReport} publicView animate />);
    expect(html).not.toContain('avp-ledger--staggered');
    expect(html).not.toContain('--avp-ledger-index');
  });

  it('has no arrival motion anywhere on the page, not only in the chart', () => {
    // The report is not made of `Reveal`s at all. A beat that faded up as it
    // scrolled would be motion inside the thing that gets printed.
    const html = renderToStaticMarkup(<ReportView report={helpscoutReport} animate />);
    expect(html).not.toContain('avp-reveal');
  });

  for (const [name, report] of REPORTS) {
    it(`the full ledger is never compact, and nothing is partial — ${name} (Epic 13, amended Epic 16)`, () => {
      // The Competitors screen draws the same component small and with
      // rival-blind dimensions hatched. As written in Epic 13 this asserted
      // the report carried neither anywhere. Epic 16 amended it on purpose:
      // the score beat now draws ONE compact column beside the numeral (the
      // positive assertion lives in the Epic 16 block above), so the guard
      // here is narrower and no weaker — the gap beat's full column is still
      // Epic 0's drawing, no second compact column exists, and the partial
      // hatch is still something the document never carries.
      const html = render(report);
      const gap = html.slice(html.indexOf('avp-beat--gap'), html.indexOf('avp-beat--proof'));
      expect(gap).not.toContain('avp-ledger--compact');
      expect(html.match(/avp-ledger--compact/g)?.length ?? 0).toBeLessThanOrEqual(1);
      expect(html).not.toContain('avp-ledger--partial');
      expect(html).not.toContain('ledger-hatch');
    });
  }

  for (const [name, report] of REPORTS) {
    it(`carries none of the dark identity's opt-ins — ${name} (Epic 14)`, () => {
      // Epic 14 made the product dark and gave two charts Working-only
      // treatments: the Ledger's dark ramp (the working palette prop) and the
      // Trend's gradient area (`area`). Both default off; the document renders
      // on the paper scope and must carry neither. Same guardrail, extended.
      const html = render(report);
      expect(html).not.toContain('avp-ledger--working');
      expect(html).not.toContain('avp-trend__area');
      expect(html).not.toContain('avp-hero');
    });
  }

  it('still renders the ledger, so the tests above are not passing on absence', () => {
    // Without this, deleting the chart entirely would make every assertion in
    // this group pass.
    const html = render(helpscoutReport);
    expect(html).toContain('avp-ledger');
    expect(html).toContain('avp-ledger__lit');
  });
});

/**
 * THE REPORT IS UNCHANGED BY EPIC 9.19 — the same guardrail, one epic later.
 *
 * That pass widened Settings, gave the Working screens hover and status motion,
 * and rebuilt their empty states around `EmptyState` and an unmeasured Ledger.
 * The report was explicitly out of scope: its width and its exclusion from
 * arrival motion stay exactly as they are, because it is the artefact that gets
 * printed and handed to a prospect (design-direction.md §0).
 *
 * The width is settled by `.avp-report`'s own `max-width: var(--avp-report-width)`
 * (72rem since Epic 17, the founder's decision) and by the frame capping
 * itself at it; the routes' side is `reportWidth.test.ts`.
 * Everything else in the group is the same shape as the 9.16 block above: the
 * new behaviours are all opt-in, and these fail if one is threaded through by
 * mistake.
 */
describe('the report acquires none of the Working screens visual pass', () => {
  const REPORTS = [
    ['helpscout', helpscoutReport],
    ['unscored', unscoredReport],
    ['no competitor set', noCompetitorSetReport],
    ['weak signal', weakSignalReport],
  ] as const;

  for (const [name, report] of REPORTS) {
    it(`never draws its ledger as unmeasured — ${name}`, () => {
      // The empty-state mode. On a report the score is the whole document, so
      // a ledger that declined to state one would be a blank argument.
      expect(render(report)).not.toContain('avp-ledger--unmeasured');
    });
  }

  it('keeps the document measure — it is not a Working screen', () => {
    // `.avp-report` carries `max-width: var(--avp-report-width)`; the routes
    // pass `wide` since Epic 17 and the FRAME caps itself, which
    // `reportWidth.test.ts` asserts. This asserts the component side: the
    // document itself never renders the Working shell's class.
    const html = render(helpscoutReport);
    expect(html).toContain('avp-report');
    expect(html).not.toContain('avp-shell__content--wide');
  });

  it('carries no live dot, with animation ON, which is what production sends', () => {
    // The report has plenty of Badges — detection status, manual overrides,
    // audit failure. None of them is a thing that is still happening: a report
    // is a finished measurement, and a breathing chip on a printed page is
    // motion in a document.
    const html = renderToStaticMarkup(<ReportView report={helpscoutReport} animate />);
    expect(html).toContain('avp-badge');
    expect(html).not.toContain('avp-badge__pulse');
  });

  it('carries none of it in the public share view either', () => {
    const html = renderToStaticMarkup(<ReportView report={helpscoutReport} publicView animate />);
    expect(html).not.toContain('avp-badge__pulse');
    expect(html).not.toContain('avp-ledger--unmeasured');
    expect(html).not.toContain('avp-empty');
  });

  it('states its degraded cases in its own words, not in an EmptyState', () => {
    // `EmptyState` is Working-screen furniture: dashed, with a button that
    // navigates. The report's absences are part of the argument it makes and
    // are written as prose inside the beat that would have carried the figure.
    for (const report of [unscoredReport, noCompetitorSetReport]) {
      expect(render(report)).not.toContain('avp-empty');
    }
  });
});


describe('a site whose robots.txt keeps the crawler out — 2026-09-29', () => {
  const html = render(crawlDisallowedReport);

  it('says why Technical Foundation is left out, in the copy both sides share', () => {
    expect(html).toContain(EXCLUSION_REASON.CRAWL_DISALLOWED!.label);
    expect(html).toContain(
      'This site’s robots.txt asks crawlers like ours not to load its pages, so its technical foundation wasn’t measured.',
    );
    // Not the other reasons' words: the audit ran, and nothing failed.
    expect(html).not.toContain('Not yet checked');
    expect(html).not.toContain('The site could not be read');
    expect(html).not.toContain(EXCLUSION_REASON.AUDIT_FAILED!.label);
  });

  it('still shows what robots.txt itself says, and the blanket block as a fix', () => {
    expect(html).toContain('What the site’s robots.txt says');
    expect(html).toContain('Checks that did not pass');
    expect(html).toContain(checkLabel('indexable'));
    const narrative = deriveNarrative(crawlDisallowedReport);
    expect(narrative.fixes.map((f) => f.id)).toContain('audit:indexable');
    expect(narrative.exclusions.map((e) => [e.key, e.reason])).toEqual([
      ['technical_foundation', 'CRAWL_DISALLOWED'],
    ]);
  });

  it('words the blanket rule as what the file says: not the crawlers that build AI answers shut out, and not the rest of the list void — launch checklist 3.49', () => {
    // The fix reads as the audit found it, on a report whose crawler was kept out and so
    // on one it was let into: it says what the rule asks and never whether we got in.
    const fix = FIX_FOR_DETAIL_CODE.ROBOTS_TXT_DISALLOW!;
    expect(html).toContain(fix.title);
    expect(html).toContain(fix.detail);
    expect(html).toContain('The audit found a Disallow: / rule under User-agent: * in the site’s robots.txt.');
    for (const phrase of [
      'nothing else on this list',
      'can take effect',
      'tells crawlers to stay out',
      'cannot cite',
      'crawlers that build AI answers',
    ]) {
      expect(html, phrase).not.toContain(phrase);
    }
  });

  it('a failed audit that is no refusal is still worded as one, in its own card', () => {
    expect(auditCard(render(failedWith('TIMEOUT')))?.title).toBe(EXCLUSION_REASON.AUDIT_FAILED!.label);
  });
});

describe('the fix beat says how it is ordered, and is ordered that way — launch checklist 3.49', () => {
  // `derive.ts` sorts a finding on the `indexable` or `robots_txt_present` check first, then the
  // fixes with a point figure, largest first, then the rest, by id. The lead used to say only
  // "Ordered by the points each one recovers", which is untrue of the first. It says the first rule
  // only when the list has a fix it applies to, so a report that never read the site, or whose
  // address is not public, still never mentions robots.txt; and it says nothing of a ranking by
  // points, or of where the points come from, when no fix in the list has a point figure.
  // `derive.test.ts` holds the order on synthetic findings; this holds the sentences and the
  // order as the page draws them.
  const LEAD_WITH_A_PROMOTED_FIX =
    'Ordered by the points each one recovers, not by how quickly it can be done. The exception is a fix for a noindex tag, a Disallow: / rule in robots.txt or a missing robots.txt, which comes first. Changes with no point figure follow those that have one.';
  const LEAD_WITHOUT =
    'Ordered by the points each one recovers, not by how quickly it can be done. Changes with no point figure follow those that have one.';
  const LEAD_NO_FIGURES =
    'None of these changes has a point figure, so none is ranked by the points it recovers.';
  const LEAD_NO_FIGURES_PROMOTED = `${LEAD_NO_FIGURES} A fix for a noindex tag, a Disallow: / rule in robots.txt or a missing robots.txt is listed first.`;
  const POINT_FIGURES =
    'The point figures come from the same arithmetic that drew the chart above: a dimension’s gap is its weight multiplied by how far short of 100 it scored.';
  const publicRender = (report: Parameters<typeof render>[0]) =>
    renderToStaticMarkup(<ReportView report={report} animate={false} publicView />);
  const titlesInOrder = (html: string, report: Report) => {
    const positions = deriveNarrative(report).fixes.map((f) => html.indexOf(f.title));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  };

  it('says the exception when the list has a fix it applies to, signed in and public', () => {
    expect(deriveNarrative(crawlDisallowedReport).fixes.map((f) => f.id)).toContain('audit:indexable');
    for (const html of [render(crawlDisallowedReport), publicRender(crawlDisallowedReport)]) {
      expect(html).toContain(LEAD_WITH_A_PROMOTED_FIX);
      expect(html).toContain(POINT_FIGURES);
      expect(html).not.toContain(LEAD_WITHOUT);
    }
  });

  it('does not say it when the list has none, so robots.txt is not mentioned for no reason', () => {
    // The reports below have fixes (gaps, or the citation) and no finding on either check.
    for (const report of [helpscoutReport, addressNotPublicReport, robotsUnreachableReport]) {
      const ids = deriveNarrative(report).fixes.map((f) => f.id);
      expect(ids, 'a list with a fix beat').not.toEqual([]);
      expect(ids).not.toContain('audit:indexable');
      expect(ids).not.toContain('audit:robots_txt_present');
      for (const html of [render(report), publicRender(report)]) {
        expect(html).toContain(LEAD_WITHOUT);
        expect(html).toContain(POINT_FIGURES);
        expect(html).not.toContain('a Disallow: / rule in robots.txt');
      }
    }
  });

  it('no longer says the order is by points alone, or splices the exception into one sentence', () => {
    for (const report of [helpscoutReport, crawlDisallowedReport]) {
      const html = render(report);
      expect(html).not.toContain('how quickly it can be done. The point figures');
      expect(html).not.toContain('can be done, except that a fix for');
      expect(html).not.toContain('After it, changes with no point figure');
    }
  });

  it('draws the fixes in that order: the promoted one first, though it carries no figure', () => {
    const html = render(crawlDisallowedReport);
    const fixes = deriveNarrative(crawlDisallowedReport).fixes;
    expect(fixes[0]!.id).toBe('audit:indexable');
    expect(fixes[0]!.pointsUpside).toBeUndefined();
    titlesInOrder(html, crawlDisallowedReport);
    // After it: every fix with a figure, then every fix without one, and none the other way round.
    const rest = fixes.slice(1);
    const firstWithout = rest.findIndex((f) => f.pointsUpside === undefined);
    expect(firstWithout).toBeGreaterThan(0);
    expect(rest.slice(firstWithout).every((f) => f.pointsUpside === undefined)).toBe(true);
    const figures = rest.slice(0, firstWithout).map((f) => f.pointsUpside!);
    expect(figures).toEqual([...figures].sort((a, b) => b - a));
  });

  describe('a list where no fix has a point figure', () => {
    // An unscored report, and a scored one whose gaps are all under 2 points: the lead has no
    // ranking by points to describe, and no point figures to say the origin of.
    const reports: [string, Report][] = [
      ['an unscored report', unscoredReport],
      ['a scored report with no gap to fix', scoredWithNoGapToFix],
    ];

    it.each(reports)('%s says so, and describes neither a ranking by points nor point figures', (_name, report) => {
      const fixes = deriveNarrative(report).fixes;
      expect(fixes.length).toBeGreaterThan(0);
      expect(fixes.every((f) => f.pointsUpside === undefined)).toBe(true);
      for (const html of [render(report), publicRender(report)]) {
        expect(html).toContain(LEAD_NO_FIGURES);
        expect(html).not.toContain(LEAD_NO_FIGURES_PROMOTED);
        expect(html).not.toContain('Ordered by the points each one recovers');
        expect(html).not.toContain('The point figures come from');
        expect(html).not.toContain('a Disallow: / rule in robots.txt');
        // What it says of the audit's fixes does not depend on there being figures.
        expect(html).toContain('Fixes drawn from the technical audit name the exact check that did not pass.');
      }
    });

    it('lists the audit’s fixes by check, then the citation fix, and says no more of the order', () => {
      expect(deriveNarrative(unscoredReport).fixes.map((f) => f.id)).toEqual([
        'audit:schema_faq',
        'audit:schema_product_or_service',
        'citation:eesel.ai',
      ]);
      titlesInOrder(render(unscoredReport), unscoredReport);
    });

    it('says a promoted fix is listed first, and the rest follow by check', () => {
      // `audit:canonical_present` sorts before `audit:indexable`, and still comes after it.
      const report = withFindings(unscoredReport, [
        finding('canonical_present', 'warn', 'NO_CANONICAL_LINK'),
        finding('indexable', 'fail', 'ROBOTS_TXT_DISALLOW'),
        ...unscoredReport.audit!.findings,
      ]);
      expect(deriveNarrative(report).fixes.map((f) => f.id)).toEqual([
        'audit:indexable',
        'audit:canonical_present',
        'audit:schema_faq',
        'audit:schema_product_or_service',
        'citation:eesel.ai',
      ]);
      for (const html of [render(report), publicRender(report)]) {
        expect(html).toContain(LEAD_NO_FIGURES_PROMOTED);
        expect(html).not.toContain('Ordered by the points each one recovers');
        expect(html).not.toContain('The point figures come from');
      }
      titlesInOrder(render(report), report);
    });

    it('is not a failed scan’s report, which has no fix list to lead', () => {
      expect(deriveNarrative(failedScanReport).fixes).toEqual([]);
      const html = render(failedScanReport);
      expect(html).not.toContain(LEAD_NO_FIGURES);
      expect(html).not.toContain('Ordered by the points each one recovers');
    });
  });

  describe('the heading counts its changes, and says whose they are', () => {
    // "1 changes" was wrong, and "specific changes the audit named" was untrue of a list that is
    // only the citation fix, which is not an audit finding. The PDF's heading is the same words
    // (`test_report_pdf.py`).
    it('says "change" for one and "changes" for more, with and without points', () => {
      expect(render(helpscoutReport)).toContain('6 changes, worth 40.6 points.');
      expect(deriveNarrative(oneGapOnly).fixes.map((f) => f.id)).toEqual(['gap:citation_strength']);
      expect(render(oneGapOnly)).toContain('1 change, worth 19.3 points.');
      expect(render(unscoredReport)).toContain('3 specific changes from this scan.');
      const oneFinding = withoutUnclaimedDomain(
        withFindings(unscoredReport, [finding('canonical_present', 'warn', 'NO_CANONICAL_LINK')]),
      );
      expect(deriveNarrative(oneFinding).fixes.map((f) => f.id)).toEqual(['audit:canonical_present']);
      expect(render(oneFinding)).toContain('1 specific change from this scan.');
    });

    it('is true of a list that is only the citation fix', () => {
      const citationOnly = withFindings(unscoredReport, []);
      expect(deriveNarrative(citationOnly).fixes.map((f) => f.id)).toEqual(['citation:eesel.ai']);
      expect(render(citationOnly)).toContain('1 specific change from this scan.');
    });

    it('never says "1 changes", "1 specific changes" or "the audit named"', () => {
      for (const report of [helpscoutReport, oneGapOnly, unscoredReport, scoredWithNoGapToFix, withFindings(unscoredReport, [])]) {
        for (const html of [render(report), publicRender(report)]) {
          expect(html).not.toContain('1 changes');
          expect(html).not.toContain('1 specific changes');
          expect(html).not.toContain('the audit named');
        }
      }
    });
  });
});

/**
 * robots.txt could not be read — launch checklist 3.41. Until 2026-10-03 this was
 * stored as ROBOTS_DISALLOWED, and the report said "This site's robots.txt asks
 * crawlers like ours not to load its pages" of a site that never answered. The
 * crawler still fetches nothing; the report must say what happened.
 */
describe('a site whose robots.txt could not be read — launch checklist 3.41', () => {
  const html = render(robotsUnreachableReport);
  const publicHtml = renderToStaticMarkup(
    <ReportView report={robotsUnreachableReport} animate={false} publicView />,
  );

  it('says what happened, in the copy both sides share', () => {
    expect(html).toContain(EXCLUSION_REASON.CRAWL_POLICY_UNREADABLE!.label);
    expect(html).toContain(ROBOTS_UNREACHABLE_SENTENCE);
    expect(html).toContain(
      'The crawler tried to read the robots.txt of this site, or of an address the site sent it to, and did not get a usable answer. It obeys robots.txt, so without that answer it did not read the site.',
    );
    expect(html).toContain('The site was not read');
  });

  it('keeps the intake’s advice off the report: only the person who typed the address can act on it', () => {
    expect(html).not.toContain(ROBOTS_UNREACHABLE_ADVICE);
    expect(html).not.toContain('then try again');
  });

  it('lets the title row wrap, as the “Left out” card does', () => {
    // The label badge is long: on a narrow screen it must drop under the title, not push
    // the card wider than the page.
    const title = html.slice(html.indexOf('The site was not read') - 200, html.indexOf('The site was not read'));
    expect(title).toContain('flex flex-wrap items-center');
  });

  it('never says the site asks crawlers to stay out, or that it blocked anything', () => {
    for (const page of [html, publicHtml]) {
      for (const words of [
        'stay out',
        'asks crawlers',
        'not to load its pages',
        'Blocked by robots.txt',
        'What the site’s robots.txt says',
      ]) {
        expect(page, words).not.toContain(words);
      }
      // The refusal's own sentence is for a file that was read and forbids the page.
      expect(page).not.toContain(EXCLUSION_REASON.CRAWL_DISALLOWED!.detail);
    }
    // ...and it is not a failed crawl: the page was never asked for.
    expect(html).not.toContain('The site could not be read');
    expect(html).not.toContain(EXCLUSION_REASON.AUDIT_FAILED!.label);
    expect(html).not.toContain('Not yet checked');
  });

  it('shows no machine code', () => {
    for (const code of [
      'ROBOTS_UNREACHABLE',
      'CRAWL_POLICY_UNREADABLE',
      'ROBOTS_DISALLOWED',
      'CRAWL_DISALLOWED',
    ]) {
      expect(html, code).not.toContain(code);
      expect(publicHtml, code).not.toContain(code);
    }
  });

  it('derives the exclusion under its own reason, and no fix about robots.txt', () => {
    const narrative = deriveNarrative(robotsUnreachableReport);
    expect(narrative.exclusions.map((e) => [e.key, e.reason])).toEqual([
      ['technical_foundation', 'CRAWL_POLICY_UNREADABLE'],
    ]);
    // An unreadable file is not a missing one: no "publish a robots.txt", and no
    // "stop robots.txt blocking crawlers" for a file nobody read.
    const ids = narrative.fixes.map((f) => f.id);
    expect(ids).not.toContain('audit:robots_txt_present');
    expect(ids).not.toContain('audit:indexable');
  });

  it('a file that really forbids the page keeps its own words', () => {
    const disallowed = render(crawlDisallowedReport);
    expect(disallowed).toContain(EXCLUSION_REASON.CRAWL_DISALLOWED!.detail);
    expect(disallowed).not.toContain(ROBOTS_UNREACHABLE_SENTENCE);
  });

  it('lists no table when every check was not applicable', () => {
    expect(html).not.toContain('Checks that did not pass');
    expect(html).not.toContain('The checks that did not pass are below.');
  });
});

describe('a site whose own robots.txt was missing but whose redirect did not answer — 3.41, round 2', () => {
  const html = render(robotsUnreachableWithFindingsReport);
  const narrative = deriveNarrative(robotsUnreachableWithFindingsReport);

  it('still says it could not read robots.txt, and never that the site asks crawlers to stay out', () => {
    expect(html).toContain('The site was not read');
    expect(html).toContain(ROBOTS_UNREACHABLE_SENTENCE);
    for (const words of [
      'stay out',
      'asks crawlers',
      'Blocked by robots.txt',
      'The site could not be read',
      EXCLUSION_REASON.AUDIT_FAILED!.label,
    ]) {
      expect(html, words).not.toContain(words);
    }
  });

  it('lists what the site’s own answers did say, the way the refusal’s card does', () => {
    expect(html).toContain('The checks that did not pass are below.');
    expect(html).toContain('Checks that did not pass');
    expect(html).toContain(checkLabel('robots_txt_present'));
    expect(html).toContain(checkLabel('sitemap_present'));
    // Same table, same badges: a warning is a warning.
    expect(html).toContain('>warn<');
  });

  it('and the fix list carries them, because they are true of the site', () => {
    const ids = narrative.fixes.map((f) => f.id);
    expect(ids).toContain('audit:robots_txt_present');
    expect(ids).toContain('audit:sitemap_present');
  });

  it('the report with every check not applicable carries no such fixes', () => {
    const ids = deriveNarrative(robotsUnreachableReport).fixes.map((f) => f.id);
    expect(ids).not.toContain('audit:robots_txt_present');
    expect(ids).not.toContain('audit:sitemap_present');
  });
});

describe('a site whose address is not public — the egress guard', () => {
  const html = render(addressNotPublicReport);

  it('says so in the shared copy, and never blames robots.txt', () => {
    expect(html).toContain(EXCLUSION_REASON.CRAWL_ADDRESS_NOT_PUBLIC!.label);
    expect(html).toContain(
      'The site’s address, or an address the site sent the crawler to, is on a private or reserved network, so the crawler did not read the site.',
    );
    expect(html).not.toContain('robots.txt');
    expect(html).not.toContain('The site could not be read');
    expect(html).not.toContain(EXCLUSION_REASON.AUDIT_FAILED!.label);
  });

  it('derives the exclusion under its own reason', () => {
    const narrative = deriveNarrative(addressNotPublicReport);
    expect(narrative.exclusions.map((e) => [e.key, e.reason])).toEqual([
      ['technical_foundation', 'CRAWL_ADDRESS_NOT_PUBLIC'],
    ]);
  });
});

/**
 * WHY THE SCAN ENDED AS IT DID — checklist 3.15.
 *
 * The signed-in report says why a scan is partial or failed, and whether it
 * counted against the agency's allowance: "It counts as one of your scans." The
 * public `/share` report must never say it — to a prospect that sentence is
 * wrong, and the report is the document being sent. `ReportView` serves both
 * routes, and `publicView` is what tells them apart (the pitch beat's operator
 * CTA is already gated on it); `app/share/scanOutcomeGate.test.ts` pins that the
 * share route passes it.
 */
describe('the signed-in report says how its scan ended — checklist 3.15', () => {
  const COUNTS = 'It counts as one of your scans.';
  const FREE = 'It does not use up one of your scans.';
  const OUTCOME = 'data-testid="scan-outcome"';
  const renderPublic = (report: Report) =>
    renderToStaticMarkup(<ReportView report={report} publicView animate={false} />);
  // Everything before the first beat: the line belongs to the header.
  const header = (html: string) => html.slice(0, html.indexOf('avp-beat--score'));

  it('a partial scan that ran out of time says so, and that it counts', () => {
    const head = header(render(partialDeadlineReport));
    expect(head).toContain(OUTCOME);
    expect(head).toContain(
      `This scan ran out of time. These results use the prompts that finished. ${COUNTS}`,
    );
  });

  it('a partial scan with no code says some engines did not answer', () => {
    const head = header(render({ ...helpscoutReport, scanStatus: 'partial' }));
    expect(head).toContain(
      `Some engines did not answer. These results use the answers that came back. ${COUNTS}`,
    );
  });

  it('a failed scan says why, and that it does not count — the API serves a report for one', () => {
    const html = render(failedScanReport);
    expect(header(html)).toContain(
      `None of the AI engines answered, so there are no results. ${FREE}`,
    );
    // Still the whole document: the line explains the report, it does not replace it.
    expect(html).toContain('avp-beat--score');
    expect(html).toContain('avp-beat--pitch');
  });

  it('says it once, as one line', () => {
    const html = render(partialDeadlineReport);
    expect(html.match(/data-testid="scan-outcome"/g)).toHaveLength(1);
  });

  it('says nothing for a scan that succeeded or has not finished', () => {
    // `scanErrorCode` is set on purpose: a stale code on a clean scan is not a story.
    for (const scanStatus of ['succeeded', 'queued', 'running', 'cancelled']) {
      const html = render({ ...helpscoutReport, scanStatus, scanErrorCode: 'EXECUTION_FAILED' });
      expect(html, scanStatus).not.toContain(OUTCOME);
      expect(html, scanStatus).not.toContain('one of your scans');
    }
    // And the plain fixture, which predates the field entirely.
    expect(render(helpscoutReport)).not.toContain(OUTCOME);
  });

  it('the public report never says it — a prospect is not spending a scan', () => {
    const reports: Report[] = [
      partialDeadlineReport,
      failedScanReport,
      { ...helpscoutReport, scanStatus: 'partial' },
    ];
    for (const report of reports) {
      const html = renderPublic(report);
      expect(html).not.toContain(OUTCOME);
      expect(html).not.toContain('one of your scans');
      expect(html).not.toContain('use up');
      // A whole document, so the absence is not a failed render passing.
      expect(html).toContain('avp-beat--score');
      expect(html).toContain('avp-beat--pitch');
      // The same report, signed in, DOES say it: the gate is `publicView`, nothing else.
      expect(render(report)).toContain(OUTCOME);
    }
  });

  it('never prints the code, signed in or public', () => {
    const codes =
      /SCAN_DEADLINE_EXCEEDED|ALL_ENGINE_CALLS_FAILED|EXECUTION_FAILED|EXECUTOR_LOST|EXECUTOR_NEVER_STARTED/;
    for (const report of [partialDeadlineReport, failedScanReport]) {
      expect(render(report)).not.toMatch(codes);
      expect(renderPublic(report)).not.toMatch(codes);
    }
  });

  it('is a line of design-token type on the paper, not a new piece of chrome', () => {
    const html = render(partialDeadlineReport);
    const line = html.match(/<p\b[^>]*data-testid="scan-outcome"[^>]*>/)?.[0] ?? '';
    expect(line, 'the outcome line is a <p>').not.toBe('');
    expect(line).toContain('text-text-secondary');
    expect(line).not.toMatch(/\b(slate|gray|zinc|blue|red|green|amber|yellow)-\d{2,3}\b/);
    expect(line).not.toContain('dark:');
    expect(line).not.toContain('style=');
    // Inside the document, so it sits on the paper scope in both themes.
    expect(html.indexOf('class="avp-report')).toBeLessThan(html.indexOf(OUTCOME));
    // Not a Badge or a live dot: a finished measurement carries no motion.
    expect(header(html)).not.toContain('avp-badge__pulse');
  });
});

/**
 * A technical audit that failed — launch checklist 3.87. It used to leave Technical
 * Foundation excluded as NOT_YET_MEASURED, which reads "Not yet checked" of a scan whose
 * audit exists and failed. It has its own words now, and no code reaches the page.
 */
describe('a site whose technical audit failed — launch checklist 3.87', () => {
  const html = render(auditFailedExcludedReport);
  const publicHtml = renderToStaticMarkup(
    <ReportView report={auditFailedExcludedReport} animate={false} publicView />,
  );

  it('has its own label and sentence in the shared map', () => {
    expect(EXCLUSION_REASON.AUDIT_FAILED).toEqual({
      label: 'Technical check failed',
      detail:
        'The technical check of the site could not be completed for this scan. Technical Foundation is left out of the score.',
    });
  });

  it('shows the reason’s words on the report and the shared report', () => {
    for (const page of [html, publicHtml]) {
      expect(page).toContain(EXCLUSION_REASON.AUDIT_FAILED!.label);
      expect(page).toContain(EXCLUSION_REASON.AUDIT_FAILED!.detail);
    }
  });

  it('never says “Not yet checked” of an audit that failed, and shows no machine code', () => {
    for (const page of [html, publicHtml]) {
      expect(page).not.toContain('Not yet checked');
      expect(page).not.toContain(EXCLUSION_REASON.NOT_YET_MEASURED!.detail);
      for (const code of ['AUDIT_FAILED', 'NOT_YET_MEASURED', 'TIMEOUT']) {
        expect(page, code).not.toContain(code);
      }
    }
  });

  it('derives the exclusion under its own reason, and no fix telling the client to change anything', () => {
    const narrative = deriveNarrative(auditFailedExcludedReport);
    expect(narrative.exclusions.map((e) => [e.key, e.reason])).toEqual([
      ['technical_foundation', 'AUDIT_FAILED'],
    ]);
    expect(narrative.fixes.map((f) => f.id)).not.toContain('gap:technical_foundation');
  });

  it('a scan with no audit row keeps its own words', () => {
    const none = render(notYetMeasuredReport);
    expect(none).toContain(EXCLUSION_REASON.NOT_YET_MEASURED!.label);
    expect(none).not.toContain(EXCLUSION_REASON.AUDIT_FAILED!.label);
  });
});

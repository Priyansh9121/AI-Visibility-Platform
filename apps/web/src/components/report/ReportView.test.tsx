/**
 * The report screen — Epic 7.
 *
 * Rendered to static markup and asserted over, the same approach the design
 * system's own render tests use. Two things are being checked: that the
 * narrative structure ip-safety.md #3 mandates is actually present, and — the
 * part that matters most for this epic — that the rendered HTML contains no
 * third-party prose, because this is the first screen that puts collected facts
 * on a page rather than in a table.
 */

import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { BEAT_SEQUENCE } from '@avp/design-system';
import { ReportView } from './ReportView';
import {
  failedAuditReport,
  generatedFixesReport,
  helpscoutReport,
  manualOverrideReport,
  mixedCompetitorSetReport,
  insufficientDataReport,
  noCompetitorSetReport,
  notYetMeasuredReport,
  unscoredReport,
  weakSignalReport,
} from '@/lib/report/__fixtures__/reports';

const render = (report: Parameters<typeof ReportView>[0]['report']) =>
  renderToStaticMarkup(<ReportView report={report} animate={false} />);

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
    expect(html).toContain('rather than awarding points for a detection that did not happen');
  });

  it('a weak competitor set carries its caveat next to the comparison', () => {
    const html = render(weakSignalReport);
    expect(html).toContain('Weakly corroborated');
    expect(html).toContain('correct it before sending the report on');
  });

  it('a failed audit reads as unreadable, not as a bad site', () => {
    const html = render(failedAuditReport);
    expect(html).toContain('The site could not be read');
    expect(html).toContain('is not a site with a bad technical foundation');
    expect(html).toContain('FETCH_FAILED');
  });

  it('a missing audit says the check has not run', () => {
    const html = render(notYetMeasuredReport);
    expect(html).toContain('has not been audited');
    expect(html).toContain('not a check that failed');
  });

  it('explains a degradation flag rather than printing the code', () => {
    const html = render(helpscoutReport);
    expect(html).toContain('measured against the best-cited brand in this scan');
    expect(html).not.toContain('NO_AUTHORITY_DATA');
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
// ip-safety.md #7 — the render gate.
//
// Epic 7 is the first epic that RENDERS the facts other epics collected. The
// database rule has been enforced since Epic 4; what is new is that a violation
// would now be visible on a page in front of a prospect. These assertions run
// against the actual emitted HTML rather than against a schema.
// ---------------------------------------------------------------------------

describe('ip-safety: the rendered page carries facts only', () => {
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
    // ip-safety.md #7: a manually added competitor is a name and a domain, the
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

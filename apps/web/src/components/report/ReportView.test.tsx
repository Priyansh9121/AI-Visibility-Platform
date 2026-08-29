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
import { deriveNarrative } from '@/lib/report/derive';
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
    // ip-safety.md #7 permits our own content. These are the prompts this
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
    // Entity names are facts (ip-safety.md #7). Anything describing them is not.
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
    // ip-safety.md #7: we have never read the cited page, and nothing here
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
    // `/share/{token}` renders the same component with `publicView`, and is the
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

  it('still renders the ledger, so the tests above are not passing on absence', () => {
    // Without this, deleting the chart entirely would make every assertion in
    // this group pass.
    const html = render(helpscoutReport);
    expect(html).toContain('avp-ledger');
    expect(html).toContain('avp-ledger__lit');
  });
});

/**
 * The narrative derivation — Epic 7.
 *
 * These are the tests that matter most on this screen. The report's argument is
 * computed, so a wrong computation is not a cosmetic bug: it is a document that
 * tells a client to fix the wrong thing, with a number attached.
 *
 * The primary fixture is REAL DATA from the Epic 5/6 verification run, not a
 * tidy invention — Help Scout's citation strength really is 3.70, and the
 * biggest gap really is a dimension other than the lowest-weighted one, which
 * is the case the gap formula exists to get right.
 */

import { describe, it, expect } from 'vitest';
import { layoutLedger } from '@avp/design-system';
import {
  aheadOnDimensions,
  deriveFixes,
  deriveNarrative,
  gapHeading,
  scoreHeading,
  toLedgerDimensions,
} from './derive';
import {
  failedAuditReport,
  generatedFixesReport,
  helpscoutReport,
  insufficientDataReport,
  noCompetitorSetReport,
  notYetMeasuredReport,
  unscoredReport,
} from './__fixtures__/reports';

describe('the ledger identity survives the round trip', () => {
  it('total lit height equals the stored composite', () => {
    // scoring-spec.md: "The total lit height of the column is exactly the
    // composite score." If the report's dimensions do not reproduce the stored
    // number, the chart on the page is lying about the score beside it.
    const dimensions = toLedgerDimensions(helpscoutReport.dimensions);
    const layout = layoutLedger(dimensions, { height: 1000 });

    const litTotal = layout.segments.reduce((sum, s) => sum + s.litHeight, 0);
    const stored = Number.parseFloat(helpscoutReport.score!.composite!);

    expect(litTotal / 10).toBeCloseTo(stored, 2);
    expect(layout.composite).toBeCloseTo(stored, 2);
  });

  it('reproduces the composite the scoring engine stored, to the hundredth', () => {
    const narrative = deriveNarrative(helpscoutReport);
    const recomputed = layoutLedger(narrative.dimensions).composite!;
    expect(recomputed).toBeCloseTo(58.24, 2);
    // The DISPLAYED number is the stored one, never the recomputed one.
    expect(narrative.composite).toBe(58.24);
  });
});

describe('the biggest gap is computed, not authored', () => {
  it('picks citation strength on the real Help Scout scan', () => {
    // gap = weight x (100 - subscore) / 100:
    //   mention_rate       30 x   0.00 / 100 =  0.00
    //   share_of_voice     25 x  70.00 / 100 = 17.50
    //   citation_strength  20 x  96.30 / 100 = 19.26  <- largest
    //   sentiment          15 x  25.00 / 100 =  3.75
    //   technical          10 x  12.50 / 100 =  1.25
    const narrative = deriveNarrative(helpscoutReport);
    expect(narrative.biggestGap?.key).toBe('citation_strength');
    expect(narrative.biggestGap?.gap).toBeCloseTo(19.26, 2);
  });

  it('ranks by recoverable points, not by the lowest sub-score', () => {
    // The trap this formula exists to avoid: citation_strength (3.70) IS the
    // lowest sub-score here and does win — so the discriminating case is that a
    // heavier dimension with a milder deficit can outrank a lighter disaster.
    const narrative = deriveNarrative({
      ...helpscoutReport,
      dimensions: [
        { key: 'mention_rate', weight: '30.00', subscore: '50.00', included: true, exclusionReason: null },
        { key: 'sentiment', weight: '15.00', subscore: '10.00', included: true, exclusionReason: null },
        { key: 'share_of_voice', weight: '25.00', subscore: '100.00', included: true, exclusionReason: null },
        { key: 'citation_strength', weight: '20.00', subscore: '100.00', included: true, exclusionReason: null },
        { key: 'technical_foundation', weight: '10.00', subscore: '100.00', included: true, exclusionReason: null },
      ],
    } as typeof helpscoutReport);

    // mention_rate: 30 x 50/100 = 15.0 beats sentiment: 15 x 90/100 = 13.5,
    // even though sentiment scores 10 and mention_rate scores 50.
    expect(narrative.biggestGap?.key).toBe('mention_rate');
  });

  it('names the gap in the heading with its point value', () => {
    const narrative = deriveNarrative(helpscoutReport);
    expect(gapHeading(narrative)).toBe('Citation Strength is costing the most — 19.3 points.');
  });

  it('has no gap to name when there is no score', () => {
    expect(gapHeading(deriveNarrative(insufficientDataReport))).toMatch(/until there is a score/);
  });
});

describe('excluded dimensions are never rendered as zeros', () => {
  it('drops them from the ledger rather than drawing an unlit column', () => {
    const narrative = deriveNarrative(noCompetitorSetReport);
    expect(narrative.dimensions.map((d) => d.key)).not.toContain('share_of_voice');
    // A zero-subscore segment would draw a full-height void that reads as total
    // failure on a dimension the scoring engine refused to score at all.
    expect(narrative.dimensions.every((d) => d.subscore > 0)).toBe(true);
  });

  it('surfaces the exclusion with its distinct reason', () => {
    const narrative = deriveNarrative(noCompetitorSetReport);
    expect(narrative.exclusions).toEqual([
      { key: 'share_of_voice', label: 'Share of Voice', reason: 'NO_COMPETITOR_SET', weight: 25 },
    ]);
  });

  it('keeps NOT_YET_MEASURED and NO_COMPETITOR_SET apart', () => {
    // Epic 5's build log: one is a capability we have not built, the other is a
    // detection that came back empty. Collapsing them loses the distinction the
    // whole reason-code design exists to preserve.
    expect(deriveNarrative(notYetMeasuredReport).exclusions[0]!.reason).toBe('NOT_YET_MEASURED');
    expect(deriveNarrative(noCompetitorSetReport).exclusions[0]!.reason).toBe('NO_COMPETITOR_SET');
  });

  it('still reproduces the composite from the surviving dimensions', () => {
    // Redistribution means the included weights sum to 100, so the ledger
    // identity holds even with a dimension missing.
    const narrative = deriveNarrative(noCompetitorSetReport);
    const weights = narrative.dimensions.reduce((sum, d) => sum + d.weight, 0);
    expect(weights).toBeCloseTo(100, 2);
    expect(layoutLedger(narrative.dimensions).composite).toBeCloseTo(67.65, 1);
    // Worth stating: excluding a WEAK dimension raises the composite, because
    // its weight moves onto dimensions that score better. That is correct — the
    // score is only ever a claim about what was measured.
    expect(narrative.composite!).toBeGreaterThan(
      Number.parseFloat(helpscoutReport.score!.composite!),
    );
  });
});

describe('degraded scores are not bad scores', () => {
  it('INSUFFICIENT_DATA yields a null composite, never a zero', () => {
    const narrative = deriveNarrative(insufficientDataReport);
    expect(narrative.status).toBe('insufficient_data');
    expect(narrative.composite).toBeNull();
    expect(narrative.potentialComposite).toBeNull();
  });

  it('a never-scored scan is distinguishable from an unscoreable one', () => {
    expect(deriveNarrative(unscoredReport).status).toBe('not_scored');
    expect(deriveNarrative(insufficientDataReport).status).toBe('insufficient_data');
  });

  it('says so in the heading rather than announcing a low score', () => {
    expect(scoreHeading(deriveNarrative(insufficientDataReport), 'Acme')).toMatch(
      /not enough data/i,
    );
    expect(scoreHeading(deriveNarrative(unscoredReport), 'Acme')).toMatch(/not been scored/i);
  });
});

describe('the fix list', () => {
  it('is built from the real audit findings and the real gaps', () => {
    const narrative = deriveNarrative(helpscoutReport);
    const ids = narrative.fixes.map((f) => f.id);

    // The gap beat's dimension leads.
    expect(ids[0]).toBe('gap:citation_strength');
    // And the audit's actual warnings are named specifically — the real scan
    // warned NO_FAQ_SCHEMA, so the fix says FAQPage schema, not "improve SEO".
    expect(narrative.fixes.some((f) => f.title.includes('FAQPage schema'))).toBe(true);
  });

  it('does not let low-value dimension fixes crowd out named audit findings', () => {
    // The regression this cap exists for: five dimensions all outrank an audit
    // finding on points, so an uncapped list is entirely abstract and the two
    // concrete, checkable changes the crawl returned never appear.
    const narrative = deriveNarrative(helpscoutReport);
    expect(narrative.fixes.filter((f) => f.source === 'audit').length).toBeGreaterThanOrEqual(2);
    // technical_foundation's gap is 1.25 points — below the floor, so it is
    // dropped rather than printed as advice.
    expect(narrative.fixes.map((f) => f.id)).not.toContain('gap:technical_foundation');
  });

  it('orders by recoverable points, not by ease', () => {
    const narrative = deriveNarrative(helpscoutReport);
    const withPoints = narrative.fixes.filter((f) => f.pointsUpside !== undefined);
    const points = withPoints.map((f) => f.pointsUpside!);
    expect(points).toEqual([...points].sort((a, b) => b - a));
  });

  it('marks the biggest gap high priority', () => {
    const narrative = deriveNarrative(helpscoutReport);
    expect(narrative.fixes.find((f) => f.id === 'gap:citation_strength')!.priority).toBe('high');
  });

  it('gives audit fixes no invented point value', () => {
    // Technical Foundation moves, but this system does not measure how much any
    // single check contributes. A fabricated number would be worse than none.
    const narrative = deriveNarrative(helpscoutReport);
    for (const fix of narrative.fixes.filter((f) => f.source === 'audit')) {
      expect(fix.pointsUpside).toBeUndefined();
    }
  });

  it('never tells a client to fix a dimension we have not measured', () => {
    // NOT_YET_MEASURED is our missing capability. Generating "fix your technical
    // foundation" from it would blame a client for a check that never ran.
    const narrative = deriveNarrative(notYetMeasuredReport);
    expect(narrative.fixes.map((f) => f.id)).not.toContain('gap:technical_foundation');
  });

  it('promotes a crawl-blocking check above everything else', () => {
    const fixes = deriveFixes(
      layoutLedger(toLedgerDimensions(helpscoutReport.dimensions)).segments,
      [
        { id: 'x', checkKey: 'indexable', status: 'fail', value: null, detailCode: 'META_ROBOTS_NOINDEX' },
        ...helpscoutReport.audit!.findings,
      ] as NonNullable<typeof helpscoutReport.audit>['findings'],
    );
    // Nothing else on the list can take effect underneath a noindex.
    expect(fixes[0]!.id).toBe('audit:indexable');
  });

  it('produces nothing to fix when there is nothing measured', () => {
    expect(deriveNarrative(insufficientDataReport).fixes).toEqual([]);
  });
});

describe('the pitch is arithmetic', () => {
  it('projects the composite the listed fixes would produce', () => {
    const narrative = deriveNarrative(helpscoutReport);
    expect(narrative.recoverablePoints).toBeCloseTo(
      narrative.fixes.reduce((s, f) => s + (f.pointsUpside ?? 0), 0),
      1,
    );
    expect(narrative.potentialComposite).toBe(
      Math.min(100, Math.round((narrative.composite! + narrative.recoverablePoints) * 10) / 10),
    );
    // On the real scan: 58.24 + 40.6 recoverable = 98.8.
    expect(narrative.potentialComposite).toBeCloseTo(98.8, 1);
  });

  it('never projects above 100', () => {
    const narrative = deriveNarrative({
      ...helpscoutReport,
      score: { ...helpscoutReport.score!, composite: '99.00' },
    } as typeof helpscoutReport);
    expect(narrative.potentialComposite).toBeLessThanOrEqual(100);
  });

  it('compares per-dimension and never invents a competitor composite', () => {
    const ahead = aheadOnDimensions(helpscoutReport);
    // Every entry names a dimension both sides genuinely have a figure for.
    for (const entry of ahead) {
      expect(['mention_rate', 'share_of_voice', 'citation_strength']).toContain(
        entry.dimensionKey,
      );
      expect(entry.delta).toBeGreaterThan(0);
    }
    // The API never sends one, so nothing here could read one.
    for (const competitor of helpscoutReport.competitorSet!.competitors) {
      expect((competitor as Record<string, unknown>).composite).toBeUndefined();
    }
  });

  it('yields no comparison when there is no competitor set', () => {
    expect(aheadOnDimensions(noCompetitorSetReport)).toEqual([]);
  });
});

describe('determinism', () => {
  it('derives an identical narrative from identical input', () => {
    const a = deriveNarrative(helpscoutReport);
    const b = deriveNarrative(helpscoutReport);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it('does not reorder the competitor comparison between runs', () => {
    expect(aheadOnDimensions(helpscoutReport)).toEqual(aheadOnDimensions(helpscoutReport));
  });
});

describe('a failed audit is not a bad audit', () => {
  it('carries no findings to turn into fixes', () => {
    // Restated in Epic 7.1. The assertion was `every(source === 'gap')`, which
    // was the same claim while `gap` and `audit` were the only two sources.
    // The claim being made is about the AUDIT: a crawl that failed produced no
    // findings, so it contributes no fixes. It says nothing about whether the
    // citations that same scan collected can still produce one — they can, and
    // the audit failing does not make them less true.
    const narrative = deriveNarrative(failedAuditReport);
    expect(narrative.fixes.every((f) => f.source !== 'audit')).toBe(true);
    expect(narrative.fixes.some((f) => f.source === 'gap')).toBe(true);
  });
});

describe('Epic 8 enriches the fix list without re-deciding it', () => {
  it('replaces the string-table wording with the generated wording', () => {
    // Epic 7's copy for this finding is the fixed table entry; Epic 8's names
    // the specific pages, because it could see the rest of the scan.
    const before = deriveNarrative(helpscoutReport).fixes.find((f) => f.id === 'audit:schema_faq');
    const after = deriveNarrative(generatedFixesReport).fixes.find(
      (f) => f.id === 'audit:schema_faq',
    );
    expect(before!.title).toBe('Add FAQPage schema to the pages that answer buyer questions');
    expect(after!.title).not.toBe(before!.title);
    // Still names the same concrete artefact — enrichment, not replacement of
    // the subject. The generated version adds who it is for.
    expect(after!.title).toContain('FAQPage');
    expect(after!.title).toContain('Help Scout');
  });

  it('names the biggest gap in language the string table could not produce', () => {
    // §7's acceptance criterion, on the real scan: citation_strength's 19.26pt
    // gap, named with the domains that actually took the citations.
    const fix = deriveNarrative(generatedFixesReport).fixes.find(
      (f) => f.id === 'gap:citation_strength',
    );
    // The string table's version could not say any of this: it has no access
    // to the citation counts or to which domains actually took them.
    expect(fix!.detail).toContain('45');
    expect(fix!.detail).toContain('helpscout.com');
    expect(fix!.detail).toContain('eesel.ai');
  });

  it('keeps Epic 7 selection, ordering and point arithmetic exactly', () => {
    const before = deriveNarrative(helpscoutReport);
    const after = deriveNarrative(generatedFixesReport);
    expect(after.fixes.map((f) => f.id)).toEqual(before.fixes.map((f) => f.id));
    expect(after.fixes.map((f) => f.pointsUpside)).toEqual(
      before.fixes.map((f) => f.pointsUpside),
    );
    expect(after.recoverablePoints).toBe(before.recoverablePoints);
    expect(after.potentialComposite).toBe(before.potentialComposite);
  });

  it('takes priority and effort from the generator, which reasoned about them', () => {
    // Epic 7's heuristic made share_of_voice `medium` because its gap is under
    // the 8-point line for `high`. The generator made it `high`, having seen
    // that mention rate is already 100 so depth of mention is the only lever
    // left. That difference is the epic.
    const before = deriveNarrative(helpscoutReport).fixes.find(
      (f) => f.id === 'gap:share_of_voice',
    );
    const after = deriveNarrative(generatedFixesReport).fixes.find(
      (f) => f.id === 'gap:share_of_voice',
    );
    expect(before!.priority).toBe('medium');
    expect(after!.priority).toBe('high');
    expect(after!.effort).toBe('M');
  });

  it('never takes a point value from a generated item', () => {
    // An audit fix has no measurable point value. A model asked for one would
    // supply a plausible number rather than no number.
    for (const fix of deriveNarrative(generatedFixesReport).fixes) {
      if (fix.source === 'audit') expect(fix.pointsUpside).toBeUndefined();
    }
  });

  it('marks generated items as a flag, never as a source of their own', () => {
    // Restated in Epic 7.1, preserving the guard's actual target.
    //
    // The claim is that model-authored WORDING is not a measurement: a
    // generated item has no independent existence, it is the same measured
    // candidate worded better, so `generated` must stay a flag. The original
    // assertion expressed that as "source is gap or audit", which was exact
    // while those were the only measurements. Epic 7.1 adds a third real one —
    // a count of citations to a domain nobody owns — so the enumeration is now
    // stated as the closed set of MEASUREMENTS, and the thing being excluded
    // is named directly rather than implied by its absence.
    const fixes = deriveNarrative(generatedFixesReport).fixes;
    const MEASUREMENTS = ['gap', 'audit', 'citation'];
    expect(fixes.every((f) => MEASUREMENTS.includes(f.source))).toBe(true);
    expect(fixes.some((f) => (f.source as string) === 'generated')).toBe(false);

    // Every fix Epic 8 built a candidate for is marked. The citation fix is
    // not one of them: Epic 8's `build_candidates` covers gaps and audit
    // findings, and extending it to a third source needs a migration, a change
    // at the model boundary, and paid calls to verify. Epic 7.1 stopped short
    // of that deliberately and left this fix on the deterministic string-table
    // floor — which is exactly where Epic 7 left the whole list before Epic 8
    // enriched it, and `enrich`'s documented behaviour for an unmatched item.
    const enriched = fixes.filter((f) => f.source !== 'citation');
    expect(enriched.length).toBeGreaterThan(0);
    expect(enriched.every((f) => f.generated)).toBe(true);
    expect(fixes.find((f) => f.source === 'citation')?.generated).toBeUndefined();
  });

  it('keeps the two point-less sources distinct from each other', () => {
    // The other half of what the original guard was protecting: the fix beat
    // shows its audit disclaimer when an AUDIT fix is present, and that
    // condition must not drift into "any fix without points" — the citation
    // fix has no points either, and it is not an audit finding.
    const narrative = deriveNarrative(helpscoutReport);
    const pointless = narrative.fixes.filter((f) => f.pointsUpside === undefined);
    expect(pointless.some((f) => f.source === 'audit')).toBe(true);
    expect(pointless.some((f) => f.source === 'citation')).toBe(true);
  });

  it('falls back to the string table for a candidate with no generated item', () => {
    const partial = {
      ...generatedFixesReport,
      actionItems: generatedFixesReport.actionItems!.filter(
        (i) => i.sourceKey !== 'schema_faq',
      ),
    };
    const fix = deriveNarrative(partial).fixes.find((f) => f.id === 'audit:schema_faq');
    expect(fix!.title).toBe('Add FAQPage schema to the pages that answer buyer questions');
    expect(fix!.generated).toBeUndefined();
  });

  it('ignores a generated item that matches no candidate', () => {
    // The two candidate implementations disagreeing must cost wording, never
    // a claim: an unmatched item does not merge and does not appear.
    const stray = {
      ...generatedFixesReport,
      actionItems: [
        { ...generatedFixesReport.actionItems![0]!, sourceKey: 'backlink_authority' },
      ],
    };
    const narrative = deriveNarrative(stray);
    expect(narrative.fixes.map((f) => f.id)).toEqual(
      deriveNarrative(helpscoutReport).fixes.map((f) => f.id),
    );
    expect(narrative.fixes.every((f) => !f.generated)).toBe(true);
  });

  it('stays pure and synchronous with generated content attached', () => {
    // The content arrives on the payload. A derivation that fetched it could
    // not be rendered with renderToStaticMarkup.
    const a = deriveNarrative(generatedFixesReport);
    const b = deriveNarrative(generatedFixesReport);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});

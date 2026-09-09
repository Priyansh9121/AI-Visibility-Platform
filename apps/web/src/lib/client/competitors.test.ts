/**
 * The field's columns — Epic 13.
 *
 * The assertion that matters is the SHAPE one: a rival's column is the
 * subject's column with two segments unmeasured, never a three-segment column
 * of its own. A three-segment column normalised to full height is exactly
 * the weight-basis error api-contracts.md warns about, and it is the natural
 * thing to write.
 */

import { describe, it, expect } from 'vitest';
import type { Report } from '@avp/shared-types';
import { fieldColumns, fieldLeads, presence } from './competitors';
import {
  helpscoutReport,
  noCompetitorSetReport,
  unscoredReport,
} from '@/lib/report/__fixtures__/reports';
import { shiftingSetHistory, threeScanHistory } from '@/lib/client/__fixtures__/history';

const REPORT = helpscoutReport as unknown as Report;

describe('fieldColumns — the subject leads, then rivals by rank', () => {
  const columns = fieldColumns(REPORT);

  it('puts the subject first and the rivals in detection rank', () => {
    expect(columns[0]!.isSubject).toBe(true);
    expect(columns[0]!.name).toBe('Help Scout');
    expect(columns.slice(1).map((c) => c.rank)).toEqual([1, 2, 3, 4, 5]);
    expect(columns[1]!.name).toBe('Zendesk');
  });

  it('gives every rival the SUBJECT’S shape, not a shape of its own', () => {
    const subject = columns[0]!;
    for (const rival of columns.slice(1)) {
      expect(rival.dimensions.map((d) => d.key)).toEqual(subject.dimensions.map((d) => d.key));
      expect(rival.dimensions.map((d) => d.weight)).toEqual(subject.dimensions.map((d) => d.weight));
    }
  });

  it('marks the two subject-only dimensions unmeasured on every rival, at zero', () => {
    for (const rival of columns.slice(1)) {
      const byKey = Object.fromEntries(rival.dimensions.map((d) => [d.key, d]));
      expect(byKey['sentiment']!.measured).toBe(false);
      expect(byKey['sentiment']!.subscore).toBe(0);
      expect(byKey['technical_foundation']!.measured).toBe(false);
      expect(byKey['mention_rate']!.measured).toBe(true);
      expect(rival.measuredCount).toBe(3);
    }
    expect(columns[0]!.measuredCount).toBe(5);
    expect(columns[0]!.dimensions.every((d) => d.measured === true)).toBe(true);
  });

  it('reads a rival’s figures off the report’s own competitor rows', () => {
    const zendesk = columns[1]!;
    const byKey = Object.fromEntries(zendesk.dimensions.map((d) => [d.key, d.subscore]));
    expect(byKey['mention_rate']).toBe(83.33);
    expect(byKey['share_of_voice']).toBe(25);
    expect(byKey['citation_strength']).toBe(3.7);
  });

  it('keeps a real zero as a zero, measured', () => {
    // Kustomer was named in no answer: that is a reading of 0, not an
    // absence, and it must light nothing rather than hatch.
    const kustomer = columns.find((c) => c.name === 'Kustomer')!;
    const mr = kustomer.dimensions.find((d) => d.key === 'mention_rate')!;
    expect(mr.subscore).toBe(0);
    expect(mr.measured).toBe(true);
  });

  it('signs a rival’s delta as its lead over the subject, and gives the subject none', () => {
    const zendesk = columns[1]!;
    const mr = zendesk.readings.find((r) => r.key === 'mention_rate')!;
    // 83.33 − 100.00
    expect(mr.delta).toBe(-16.7);
    const sov = zendesk.readings.find((r) => r.key === 'share_of_voice')!;
    expect(sov.delta).toBe(-5);
    for (const r of columns[0]!.readings) expect(r.delta).toBeNull();
    expect(columns[0]!.readings.map((r) => r.key)).toEqual([
      'mention_rate',
      'share_of_voice',
      'citation_strength',
    ]);
  });

  it('hatches a comparable dimension a rival has no figure on, rather than scoring it zero', () => {
    const report: Report = {
      ...REPORT,
      competitorSet: {
        ...REPORT.competitorSet!,
        competitors: [{ ...REPORT.competitorSet!.competitors[0]!, citationStrength: null }],
      },
    };
    const [, rival] = fieldColumns(report);
    const cs = rival!.dimensions.find((d) => d.key === 'citation_strength')!;
    expect(cs.measured).toBe(false);
    expect(rival!.measuredCount).toBe(2);
    expect(rival!.readings.find((r) => r.key === 'citation_strength')!.value).toBeNull();
    expect(rival!.readings.find((r) => r.key === 'citation_strength')!.delta).toBeNull();
  });

  it('drops a dimension excluded for everyone from every column', () => {
    // Share of voice is excluded with NO_COMPETITOR_SET, and there are no
    // rivals — but the subject's shape must still be the four included ones.
    const [subject, ...rivals] = fieldColumns(noCompetitorSetReport);
    expect(rivals).toEqual([]);
    expect(subject!.dimensions.map((d) => d.key)).not.toContain('share_of_voice');
    expect(subject!.dimensions.length).toBe(4);
    expect(subject!.readings.map((r) => r.key)).toEqual(['mention_rate', 'citation_strength']);
  });

  it('is empty for an unscored scan — no shape to build a rival to', () => {
    expect(fieldColumns(unscoredReport)).toEqual([]);
  });

  it('carries provenance: a rival named by hand says so', () => {
    const report: Report = {
      ...REPORT,
      competitorSet: {
        ...REPORT.competitorSet!,
        competitors: REPORT.competitorSet!.competitors.map((c, i) =>
          i === 1 ? { ...c, isManualOverride: true } : c,
        ),
      },
    };
    const columns = fieldColumns(report);
    expect(columns[2]!.isManual).toBe(true);
    expect(columns[1]!.isManual).toBe(false);
    expect(columns[0]!.isManual).toBe(false);
  });
});

describe('fieldLeads — who is ahead, on what', () => {
  it('is empty when no rival beats the subject on a comparable dimension', () => {
    // Help Scout leads its whole field on the fixture.
    expect(fieldLeads(fieldColumns(REPORT))).toEqual([]);
  });

  it('lists leads largest first, read off the same deltas the cards print', () => {
    const report: Report = {
      ...REPORT,
      competitorSet: {
        ...REPORT.competitorSet!,
        competitors: REPORT.competitorSet!.competitors.map((c) =>
          c.name === 'Zendesk'
            ? { ...c, shareOfVoice: '40.00', citationStrength: '8.70' }
            : c.name === 'Front'
              ? { ...c, shareOfVoice: '31.00' }
              : c,
        ),
      },
    };
    const leads = fieldLeads(fieldColumns(report));
    expect(leads.map((l) => [l.name, l.key, l.delta])).toEqual([
      ['Zendesk', 'share_of_voice', 10],
      ['Zendesk', 'citation_strength', 5],
      ['Front', 'share_of_voice', 1],
    ]);
  });
});

describe('presence — how many scans carried a rival', () => {
  it('counts the scans whose set named the rival', () => {
    expect(presence(threeScanHistory, 'Matomo')).toEqual({ seen: 3, of: 3 });
  });

  it('reports a rival that came and went as seen in fewer than all', () => {
    const names = new Set(shiftingSetHistory.scans.flatMap((s) => s.competitors.map((c) => c.name)));
    const partial = [...names].find(
      (n) => presence(shiftingSetHistory, n).seen < shiftingSetHistory.scans.length,
    );
    expect(partial).toBeDefined();
    expect(presence(shiftingSetHistory, 'Nobody At All')).toEqual({
      seen: 0,
      of: shiftingSetHistory.scans.length,
    });
  });
});

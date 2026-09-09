/**
 * The field, as columns — Epic 13.
 *
 * Pure, and separated from the view for the reason `lib/client/trends.ts` is:
 * the interesting behaviour is what happens to the SHAPE of a rival's column
 * when a dimension is excluded for everyone, or unmeasured for one rival, and
 * that is arithmetic rather than rendering. It is asserted directly.
 *
 * THE RULE THIS FILE EXISTS TO KEEP
 * ---------------------------------
 * There is no per-competitor composite, and there deliberately never has been
 * (api-contracts.md, Epic 5): sentiment is classified toward the subject only
 * and the technical audit is of the subject's own site, so 25% of the weight
 * has no rival input. A rival "score" over the other 75% would be computed on
 * a different basis from the subject's and would draw every rival shorter
 * than they are.
 *
 * So a rival's column is built to the SUBJECT'S shape — the same dimensions,
 * the same weights, in the same order — with the dimensions nobody measured
 * for rivals carried as `measured: false`. The Luminance Ledger draws those as
 * hatched shape, prints "Not measured" for them, and speaks no composite for
 * the column. Two columns of one shape can be compared segment by segment;
 * that is the whole point of drawing them the same.
 *
 * EVERY FIGURE HERE IS ALREADY ON THE REPORT. `report.score` is the subject's
 * stored score; `report.competitorSet` carries the per-rival figures the
 * report's own comparison table shows, derived on read from persisted rows.
 * Nothing is fetched and nothing new is computed beyond a subtraction.
 */

import type { LedgerDimension } from '@avp/design-system';
import type { ClientHistory, Report, ReportCompetitor } from '@avp/shared-types';
import { toLedgerDimensions } from '@/lib/report/derive';
import { num } from '@/lib/client/technical';

/**
 * The three dimensions a rival has a figure on, keyed by the report's
 * dimension key, valued by the field that carries it on a competitor row.
 * The other two — sentiment and technical foundation — are the subject's own.
 */
export const RIVAL_FIELD = {
  mention_rate: 'mentionRate',
  share_of_voice: 'shareOfVoice',
  citation_strength: 'citationStrength',
} as const;

export interface FieldReading {
  key: string;
  label: string;
  /** This brand's sub-score on the dimension, or null where it has none. */
  value: number | null;
  /**
   * This brand's lead over the subject, to one decimal. Null for the subject
   * itself, and null where either side has no figure — never a zero, which
   * would claim a tie nobody measured.
   */
  delta: number | null;
}

export interface FieldColumn {
  key: string;
  name: string;
  domain: string | null;
  isSubject: boolean;
  /** Named by an operator rather than found by detection — provenance, as the report shows it. */
  isManual: boolean;
  /** Both detection signals agreed. Always true for the subject, which was not detected. */
  corroborated: boolean;
  /** Detection rank; 0 for the subject, which leads the grid. */
  rank: number;
  /** The subject's dimensions, in the subject's order, with `measured` set per brand. */
  dimensions: LedgerDimension[];
  /** How many of `dimensions` this brand actually has a reading on. */
  measuredCount: number;
  /** The comparable dimensions only, as figures beside the column. */
  readings: FieldReading[];
}

/**
 * One column per brand: the subject first, then rivals in detection rank.
 *
 * Empty when the scan has no included dimensions — an unscored scan has no
 * shape to build a rival's column to, and building one to the nominal weights
 * would draw a comparison the score itself refused to make.
 */
export function fieldColumns(report: Report): FieldColumn[] {
  const subjectDims = toLedgerDimensions(report.dimensions ?? []);
  if (subjectDims.length === 0) return [];

  const subjectName = report.subject.brandName ?? report.subject.name;
  const subjectValue = new Map(subjectDims.map((d) => [d.key, d.subscore]));

  const subject: FieldColumn = {
    key: '__subject__',
    name: subjectName,
    domain: report.subject.domain,
    isSubject: true,
    isManual: false,
    corroborated: true,
    rank: 0,
    dimensions: subjectDims.map((d) => ({ ...d, measured: true })),
    measuredCount: subjectDims.length,
    readings: subjectDims
      .filter((d) => d.key in RIVAL_FIELD)
      .map((d) => ({ key: d.key, label: d.label, value: d.subscore, delta: null })),
  };

  const rivals = [...(report.competitorSet?.competitors ?? [])]
    // Rank, then name: two loads of one set must not reorder the grid.
    .sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name))
    .map((c) => rivalColumn(c, subjectDims, subjectValue));

  return [subject, ...rivals];
}

function rivalColumn(
  competitor: ReportCompetitor,
  subjectDims: readonly LedgerDimension[],
  subjectValue: ReadonlyMap<string, number>,
): FieldColumn {
  const dimensions: LedgerDimension[] = subjectDims.map((d) => {
    const value = rivalValue(competitor, d.key);
    // Zero and null are different, and both occur. A rival named in no
    // answer genuinely scored 0 on mention rate — that is a reading, and it
    // draws as an unlit segment. Null is a dimension nobody measures for
    // rivals, or one this rival has no figure on, and it draws as shape.
    return value === null
      ? { ...d, subscore: 0, measured: false }
      : { ...d, subscore: value, measured: true };
  });

  const readings: FieldReading[] = subjectDims
    .filter((d) => d.key in RIVAL_FIELD)
    .map((d) => {
      const value = rivalValue(competitor, d.key);
      const ours = subjectValue.get(d.key);
      return {
        key: d.key,
        label: d.label,
        value,
        delta: value === null || ours === undefined ? null : round1(value - ours),
      };
    });

  return {
    key: competitor.id,
    name: competitor.name,
    domain: competitor.domain ?? null,
    isSubject: false,
    isManual: competitor.isManualOverride,
    corroborated: competitor.corroborated,
    rank: competitor.rank,
    dimensions,
    measuredCount: dimensions.filter((d) => d.measured !== false).length,
    readings,
  };
}

/** A rival's figure on a dimension, or null where rivals have none. */
function rivalValue(competitor: ReportCompetitor, key: string): number | null {
  const field = (RIVAL_FIELD as Record<string, keyof ReportCompetitor | undefined>)[key];
  if (field === undefined) return null;
  return num(competitor[field] as string | null | undefined);
}

/**
 * Which rivals lead the subject, and on what — largest lead first.
 *
 * Read off the columns rather than recomputed from the report, so the list
 * beside the grid cannot disagree with the deltas printed on the cards.
 */
export function fieldLeads(
  columns: readonly FieldColumn[],
): { name: string; key: string; label: string; delta: number }[] {
  const out: { name: string; key: string; label: string; delta: number }[] = [];
  for (const column of columns) {
    if (column.isSubject) continue;
    for (const r of column.readings) {
      if (r.delta !== null && r.delta > 0) {
        out.push({ name: column.name, key: r.key, label: r.label, delta: r.delta });
      }
    }
  }
  // Name then dimension as the tie-break, so identical data never reorders.
  out.sort(
    (a, b) => b.delta - a.delta || a.name.localeCompare(b.name) || a.key.localeCompare(b.key),
  );
  return out;
}

/**
 * How many of this client's scans carried each rival — the cross-scan half
 * of "is this rival always here". A competitor set is re-detected per scan,
 * so a rival in the latest set may be new, and one missing from it may have
 * been in every earlier one; the card says which.
 */
export function presence(history: ClientHistory, name: string): { seen: number; of: number } {
  return {
    seen: history.scans.filter((s) => s.competitors.some((c) => c.name === name)).length,
    of: history.scans.length,
  };
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/**
 * Narrative derivation — Epic 7.
 *
 * The report's argument is COMPUTED, not authored. Every claim below traces to
 * a stored number: the headline gap comes from the ledger's own gap arithmetic,
 * the fix list from audit detail codes and the gap ranking, the pitch from the
 * points those fixes recover. Nothing here writes a sentence about a client
 * that the data did not produce.
 *
 * Why the derivation lives here and not in scoring.py
 * ---------------------------------------------------
 * `gap_i = weight_i x (100 - subscore_i) / 100` is already implemented, and
 * already unit-tested, inside the design system's `layoutLedger` — it is what
 * draws the unlit portion of each segment. Reimplementing it in Python would
 * create two sources for one number, and the failure mode is the worst kind:
 * the chart annotating one dimension while the headline names another. Calling
 * the same function that draws the chart makes that disagreement impossible by
 * construction.
 *
 * scoring-spec.md: "The report's biggest gap narrative beat is derived from
 * this, not authored separately."
 */

import { layoutLedger, type LedgerDimension, type LedgerSegment } from '@avp/design-system';
import type { ActionItem, Report, ReportAuditFinding, ReportDimension } from '@avp/shared-types';
import {
  FIX_FOR_DETAIL_CODE,
  FIX_FOR_DIMENSION,
  dimensionLabel,
  type FixCopy,
} from './strings';

/** How many fixes the fix beat carries. A list nobody finishes is not a plan. */
const MAX_FIXES = 5;

/**
 * Dimension fixes are capped, and audit fixes get the remaining slots.
 *
 * Without this the list is all dimension fixes: there are five dimensions and
 * every one with any gap outranks an audit finding, which carries no point
 * value at all. The Help Scout scan showed the failure directly — four abstract
 * "improve this dimension" items, and the two concrete findings the crawl
 * actually returned ("no FAQ schema", "no Product schema") pushed off the end.
 * A named, checkable change is the more useful recommendation even when its
 * point value is unmeasurable, so it gets guaranteed room.
 */
const MAX_DIMENSION_FIXES = 3;

/**
 * A dimension gap below this is not worth a line in the plan.
 *
 * A fix worth 1.3 points is noise next to one worth 19, and printing it
 * dilutes the list without telling anyone anything they can act on.
 */
const MIN_GAP_POINTS = 2;

export interface DerivedFix {
  id: string;
  title: string;
  detail: string;
  priority: 'high' | 'medium' | 'low';
  effort: 'S' | 'M' | 'L';
  /**
   * Composite points this fix could recover, where that is knowable.
   *
   * Present only for dimension-level fixes, where the gap arithmetic gives a
   * real figure. An audit fix contributes to Technical Foundation but not in a
   * proportion this system measures, so it carries no number rather than an
   * invented one.
   */
  pointsUpside?: number;
  /** Where this came from — for the build log and for debugging, not display. */
  source: 'gap' | 'audit';
  /**
   * Whether the wording above was written by Epic 8's generator rather than
   * read from the string table.
   *
   * Deliberately a flag and not a third `source` value. `source` answers "what
   * measurement produced this fix", and the answer is still the gap or the
   * audit finding — a generated item has no independent existence, it is the
   * same candidate worded better. Widening `source` would also let the audit
   * disclaimer in the fix beat silently stop rendering.
   */
  generated?: boolean;
}

export interface DerivedNarrative {
  /** Null when there is no scoreable data. Never zero in that case. */
  composite: number | null;
  status: 'scored' | 'insufficient_data' | 'not_scored';
  /** Included dimensions, heaviest first, ready for the Luminance Ledger. */
  dimensions: LedgerDimension[];
  /** Dimensions left out of the score, with the reason kept distinct. */
  exclusions: { key: string; label: string; reason: string; weight: number }[];
  /** The largest recoverable point gain. Null for a perfect or unscoreable scan. */
  biggestGap: LedgerSegment | null;
  /** Every segment, so the gap beat can rank the runners-up. */
  segments: readonly LedgerSegment[];
  fixes: DerivedFix[];
  /** Total points the listed fixes could recover, to 1dp. */
  recoverablePoints: number;
  /** Composite if every listed dimension fix landed fully. Null when unscoreable. */
  potentialComposite: number | null;
  /** Rivals ahead of the subject on a dimension both sides actually have. */
  aheadOnDimensions: { competitorName: string; dimensionKey: string; delta: number }[];
  /** Geometry warnings from the ledger — a scoring-config defect, not a UI one. */
  warnings: readonly string[];
}

/**
 * The three dimensions with a per-competitor figure.
 *
 * Sentiment is classified toward the subject only and Technical Foundation is
 * the subject's own site, so a "competitor composite" would be computed on a
 * different weight basis and would not be comparable. The report compares
 * per-dimension instead — see api-contracts.md, Epic 5.
 */
const COMPARABLE_DIMENSIONS = ['mention_rate', 'share_of_voice', 'citation_strength'] as const;

/** Decimals cross the wire as strings (scoring-spec.md rule 3). */
function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === 'number' ? value : Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/**
 * Included dimensions, in the order the API supplied (heaviest first).
 *
 * An excluded dimension is dropped rather than passed as zero. Passing it as
 * zero would draw a full-height unlit segment that reads as a total failure on
 * that dimension — asserting exactly what the scoring engine refused to assert.
 * The exclusions are surfaced separately instead, with their reasons.
 */
export function toLedgerDimensions(dimensions: readonly ReportDimension[]): LedgerDimension[] {
  return dimensions
    .filter((d) => d.included)
    .map((d) => ({
      key: d.key,
      label: dimensionLabel(d.key),
      weight: num(d.weight) ?? 0,
      subscore: num(d.subscore) ?? 0,
    }));
}

/** Derive the whole narrative from one report payload. */
export function deriveNarrative(report: Report): DerivedNarrative {
  const dimensions = toLedgerDimensions(report.dimensions ?? []);
  const exclusions = (report.dimensions ?? [])
    .filter((d) => !d.included)
    .map((d) => ({
      key: d.key,
      label: dimensionLabel(d.key),
      reason: d.exclusionReason ?? 'NOT_YET_MEASURED',
      weight: num(d.weight) ?? 0,
    }));

  // The same call that draws the chart. One gap arithmetic, not two.
  const layout = layoutLedger(dimensions);

  const status: DerivedNarrative['status'] =
    report.score === null || report.score === undefined
      ? 'not_scored'
      : report.score.status === 'insufficient_data'
        ? 'insufficient_data'
        : 'scored';

  // The STORED composite is authoritative, never the recomputed one.
  // scoring-spec.md rule 2 fixes rounding once, at storage; recomputing for
  // display risks a report whose headline disagrees with the score row a
  // client could be shown in the same breath.
  const composite = status === 'scored' ? num(report.score?.composite ?? null) : null;

  const fixes = deriveFixes(
    layout.segments,
    report.audit?.findings ?? [],
    exclusions,
    report.actionItems ?? [],
  );

  // Only dimension fixes carry a points figure, so only they can be summed.
  const recoverablePoints = round1(
    fixes.reduce((sum, f) => sum + (f.pointsUpside ?? 0), 0),
  );
  const potentialComposite =
    composite === null ? null : Math.min(100, round1(composite + recoverablePoints));

  return {
    composite,
    status,
    dimensions,
    exclusions,
    biggestGap: layout.biggestGap,
    segments: layout.segments,
    fixes,
    recoverablePoints,
    potentialComposite,
    aheadOnDimensions: aheadOnDimensions(report),
    warnings: layout.warnings,
  };
}

/**
 * The fix list.
 *
 * Ordered by recoverable points, because that is the only ranking the data
 * supports — a fix worth 19 points outranks one worth 4 regardless of how
 * satisfying either is to make. Audit fixes follow, ordered by the severity the
 * API already sorted them into, because they are concrete and cheap even where
 * their point value is not separately measurable.
 *
 * Blocking checks are the exception: a site telling crawlers to stay out is
 * promoted to the top whatever its point value, because nothing else on the
 * list can take effect until it is fixed.
 */
export function deriveFixes(
  segments: readonly LedgerSegment[],
  findings: readonly ReportAuditFinding[],
  exclusions: readonly { key: string; reason: string }[] = [],
  generated: readonly ActionItem[] = [],
): DerivedFix[] {
  const fixes: DerivedFix[] = [];

  // --- dimension fixes, ranked by points left on the table ---------------
  const ranked = [...segments]
    .filter((s) => s.gap >= MIN_GAP_POINTS)
    .sort((a, b) => b.gap - a.gap || a.key.localeCompare(b.key))
    .slice(0, MAX_DIMENSION_FIXES);

  for (const segment of ranked) {
    const copy = FIX_FOR_DIMENSION[segment.key];
    if (!copy) continue;
    fixes.push({
      id: `gap:${segment.key}`,
      title: copy.title,
      detail: copy.detail,
      priority: segment.isBiggestGap ? 'high' : segment.gap >= 8 ? 'medium' : 'low',
      effort: copy.effort,
      pointsUpside: round1(segment.gap),
      source: 'gap',
    });
  }

  // --- audit fixes, from the codes the crawl actually returned -----------
  for (const finding of findings) {
    const code = finding.detailCode;
    if (!code) continue;
    const copy: FixCopy | undefined = FIX_FOR_DETAIL_CODE[code];
    if (!copy) continue;
    fixes.push({
      id: `audit:${finding.checkKey}`,
      title: copy.title,
      detail: copy.detail,
      priority: finding.status === 'fail' || finding.status === 'error' ? 'high' : 'medium',
      effort: copy.effort,
      source: 'audit',
    });
  }

  // A dimension excluded because we have not measured it yet is OUR gap, not
  // the client's. It must never generate a fix telling them to change something.
  const notTheirProblem = new Set(
    exclusions.filter((e) => e.reason === 'NOT_YET_MEASURED').map((e) => `gap:${e.key}`),
  );

  const ordered = fixes.filter((f) => !notTheirProblem.has(f.id));
  ordered.sort((a, b) => {
    // Crawl-blocking checks first: nothing else can take effect underneath one.
    const blocking = (f: DerivedFix) =>
      f.id === 'audit:indexable' || f.id === 'audit:robots_txt_present' ? 0 : 1;
    if (blocking(a) !== blocking(b)) return blocking(a) - blocking(b);
    if ((b.pointsUpside ?? 0) !== (a.pointsUpside ?? 0)) {
      return (b.pointsUpside ?? 0) - (a.pointsUpside ?? 0);
    }
    return a.id.localeCompare(b.id);
  });

  return enrich(ordered.slice(0, MAX_FIXES), generated);
}

/**
 * Overlay Epic 8's generated wording onto the list this file just built.
 *
 * Only four fields move: title, detail, priority and effort. Which fixes exist,
 * what order they are in, and what each is worth stay exactly as derived above,
 * because those are arithmetic and the generator was never shown enough to
 * re-decide them. `pointsUpside` in particular is never taken from a generated
 * item — an audit fix has no measurable point value and a model asked for one
 * would supply a plausible number rather than no number.
 *
 * Matching is by key, and a miss is silent by design. A generated item whose
 * candidate this derivation did not produce simply does not merge, and a fix
 * with no generated counterpart keeps its string-table copy. The two lists
 * disagreeing costs wording, never a claim.
 */
function enrich(fixes: DerivedFix[], generated: readonly ActionItem[]): DerivedFix[] {
  if (generated.length === 0) return fixes;

  const byKey = new Map(generated.map((item) => [`${item.source}:${item.sourceKey}`, item]));

  return fixes.map((fix) => {
    const item = byKey.get(fix.id);
    if (!item?.title) return fix;
    return {
      ...fix,
      title: item.title,
      detail: item.detail ?? fix.detail,
      priority: item.priority,
      effort: item.effort,
      generated: true,
    };
  });
}

/**
 * Which rivals are ahead, and on what.
 *
 * Per-dimension only. There is deliberately no competitor composite to compare
 * against — comparing the subject's five-dimension composite to a rival's
 * three-dimension one would understate every rival by construction.
 */
export function aheadOnDimensions(report: Report): DerivedNarrative['aheadOnDimensions'] {
  const subject = report.score;
  if (!subject) return [];

  const out: DerivedNarrative['aheadOnDimensions'] = [];
  for (const competitor of report.competitorSet?.competitors ?? []) {
    for (const key of COMPARABLE_DIMENSIONS) {
      const camel = key === 'mention_rate'
        ? 'mentionRate'
        : key === 'share_of_voice'
          ? 'shareOfVoice'
          : 'citationStrength';
      const theirs = num((competitor as Record<string, unknown>)[camel] as string | null);
      const ours = num((subject as Record<string, unknown>)[camel] as string | null);
      if (theirs === null || ours === null) continue;
      if (theirs > ours) {
        out.push({
          competitorName: competitor.name,
          dimensionKey: key,
          delta: round1(theirs - ours),
        });
      }
    }
  }
  // Largest lead first; name then dimension as a total tie-break, so two loads
  // of identical data never reorder the evidence.
  out.sort(
    (a, b) =>
      b.delta - a.delta ||
      a.competitorName.localeCompare(b.competitorName) ||
      a.dimensionKey.localeCompare(b.dimensionKey),
  );
  return out;
}

/**
 * The score beat's heading — a claim, never a category label.
 *
 * design-system.md §7: "You appear in fewer than half the answers buyers see"
 * does the work; "Mention Rate" does not. Which claim gets made is chosen by
 * the number, so the heading cannot contradict the chart under it.
 */
export function scoreHeading(narrative: DerivedNarrative, subjectName: string): string {
  if (narrative.status === 'not_scored') {
    return `${subjectName} has not been scored yet.`;
  }
  if (narrative.status === 'insufficient_data' || narrative.composite === null) {
    return `There is not enough data yet to score ${subjectName}.`;
  }
  const score = Math.round(narrative.composite);
  if (score >= 80) return `${subjectName} is one of the names these answers reach for.`;
  if (score >= 60) return `${subjectName} shows up, but not reliably enough to be the default.`;
  if (score >= 35) return `${subjectName} is present in some answers and absent from many.`;
  if (score > 0) return `${subjectName} is close to invisible when buyers ask.`;
  return `${subjectName} did not appear in any answer we measured.`;
}

/** The gap beat's heading. Names the dimension the arithmetic picked. */
export function gapHeading(narrative: DerivedNarrative): string {
  const gap = narrative.biggestGap;
  if (!gap) {
    return narrative.status === 'scored'
      ? 'There is no single dimension left to recover.'
      : 'No gap can be measured until there is a score.';
  }
  return `${gap.label} is costing the most — ${gap.gap.toFixed(1)} points.`;
}

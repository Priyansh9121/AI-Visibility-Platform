/**
 * Answer Shelf — layout geometry and data shaping.
 *
 * Direction A of `docs/design-direction.md` §5: *an AI answer has a limited
 * number of slots — who is standing in them?*
 *
 * Kept as a pure function, separate from the React component, for the same
 * reason `ledgerLayout.ts` is: this chart also carries a correctness condition
 * that must be asserted rather than trusted.
 *
 *   EVERY ANSWERED ROW CARRIES EXACTLY ONE SUBJECT MARK.
 *
 * Present, and it is a filled marker at the ordinal the answer put it in.
 * Absent, and it is an explicit empty notch. Never nothing. The whole argument
 * of this visualisation is that a stack of rows shows *the shape of absence*
 * before a word is read — and a row that renders no mark at all when the
 * subject is missing quietly deletes the finding it exists to show. That would
 * not look like a bug; it would look like a clean report.
 *
 * The notch sits in a fixed column at the end of the ordinal track rather than
 * at a guessed ordinal, for two reasons: a brand that was not named has no
 * ordinal, and inventing one would state a fact the answer did not; and a fixed
 * column is what makes the absences line up into a vertical band down the page.
 */

import { seriesStyle, type CompetitorPattern } from '../../tokens/color.js';

/** One brand standing in one ordinal slot of one answer. Facts only. */
export interface ShelfSlotInput {
  /** 1-based ordinal within the answer, as the extractor recorded it. */
  position: number;
  entityName: string;
  entityDomain?: string | null;
  isSubject: boolean;
  competitorName?: string | null;
  /** A citation in this same answer was attributed to this entity. */
  cited?: boolean;
}

/** One answer — the row. */
export interface ShelfRowInput {
  promptId: string;
  /** OUR generated question. Never the engine's answer. */
  promptText: string;
  promptPosition: number;
  engine: string;
  /** False when the engine errored. Distinct from answering without naming us. */
  answered: boolean;
  subjectPresent: boolean;
  subjectPosition?: number | null;
  subjectCited?: boolean;
  slots: readonly ShelfSlotInput[];
}

export interface ShelfMark {
  key: string;
  entityName: string;
  position: number;
  isSubject: boolean;
  cited: boolean;
  /** Series fill. Subject is beacon; everyone else is neutral slate. */
  fill: string;
  /** Print/greyscale fallback. Never applied to the subject. */
  pattern: CompetitorPattern;
  cx: number;
  cy: number;
}

/** The subject's mark on a row — a filled marker, or the hole where it isn't. */
export type SubjectMark =
  | { kind: 'present'; position: number; cited: boolean; cx: number; cy: number }
  | { kind: 'absent'; cx: number; cy: number }
  /** The engine did not answer. We have no answer to be absent from. */
  | { kind: 'unanswered'; cx: number; cy: number };

export interface ShelfRow {
  promptId: string;
  promptText: string;
  promptPosition: number;
  engine: string;
  answered: boolean;
  /** Non-subject brands, in the order the answer named them. */
  marks: readonly ShelfMark[];
  subject: SubjectMark;
  /** Slots beyond the visible track. Rendered as a count, never dropped silently. */
  hiddenCount: number;
  y: number;
  rowKey: string;
}

export interface ShelfLayout {
  rows: readonly ShelfRow[];
  width: number;
  height: number;
  /** x of the fixed notch column, for the axis rule and the legend. */
  notchX: number;
  labelWidth: number;
  /** How many answered rows did not name the subject — the headline count. */
  absentCount: number;
  answeredCount: number;
  /** Visible ordinal positions, for the column ruler. */
  ordinals: readonly number[];
  warnings: readonly string[];
}

export interface ShelfLayoutOptions {
  /** Ordinal positions drawn before the notch column. */
  maxSlots?: number;
  rowHeight?: number;
  slotPitch?: number;
  labelWidth?: number;
}

const DEFAULTS = {
  maxSlots: 6,
  rowHeight: 26,
  slotPitch: 24,
  // Wide enough for a truncated prompt AND its engine. Both are needed: each
  // prompt is asked of every engine, so a row labelled only with the question
  // reads as the same row printed twice.
  labelWidth: 320,
} as const;

/** Geometry rounded so output is byte-stable across platforms, as the ledger is. */
const DP = 3;
const r = (n: number): number => {
  const f = 10 ** DP;
  return Math.round(n * f) / f;
};

/**
 * Shape rows of facts into shelf geometry.
 *
 * Pure and deterministic: the same rows always produce the same layout, because
 * these coordinates reach exported PDFs that clients compare month to month.
 */
export function layoutAnswerShelf(
  rows: readonly ShelfRowInput[],
  options: ShelfLayoutOptions = {},
): ShelfLayout {
  const maxSlots = Math.max(1, options.maxSlots ?? DEFAULTS.maxSlots);
  const rowHeight = options.rowHeight ?? DEFAULTS.rowHeight;
  const slotPitch = options.slotPitch ?? DEFAULTS.slotPitch;
  const labelWidth = options.labelWidth ?? DEFAULTS.labelWidth;

  const warnings: string[] = [];

  // x of ordinal column n (1-based). The notch column sits well past the last
  // ordinal so it reads as its own track rather than as one more place on the
  // shelf — and so its header, which carries the subject's name, has clear
  // space either side of it rather than colliding with the last ordinal.
  const columnX = (n: number): number => r(labelWidth + (n - 1) * slotPitch + slotPitch / 2);
  const notchX = r(columnX(maxSlots) + slotPitch * 3);

  // One neutral shade per rival, assigned by first appearance across the WHOLE
  // shelf, so a rival keeps its shade from row to row. Assigning per row would
  // make the same brand change colour down the page.
  const seriesIndex = new Map<string, number>();
  for (const row of rows) {
    for (const slot of row.slots) {
      if (slot.isSubject) continue;
      if (!seriesIndex.has(slot.entityName)) seriesIndex.set(slot.entityName, seriesIndex.size);
    }
  }

  const out: ShelfRow[] = [];
  let absentCount = 0;
  let answeredCount = 0;

  rows.forEach((row, index) => {
    const cy = r(index * rowHeight + rowHeight / 2);
    if (row.answered) answeredCount += 1;

    // Non-subject brands only. The subject is placed separately below, because
    // its mark has to exist whether or not a slot for it does.
    const others = [...row.slots]
      .filter((s) => !s.isSubject)
      .sort((a, b) => a.position - b.position || a.entityName.localeCompare(b.entityName));

    const visible = others.filter((s) => s.position <= maxSlots);
    const hiddenCount = others.length - visible.length;

    const marks: ShelfMark[] = visible.map((slot) => {
      const style = seriesStyle('competitor', seriesIndex.get(slot.entityName) ?? 0);
      return {
        key: `${row.promptId}:${row.engine}:${slot.position}:${slot.entityName}`,
        entityName: slot.entityName,
        position: slot.position,
        isSubject: false,
        cited: slot.cited === true,
        fill: style.fill,
        pattern: style.pattern,
        cx: columnX(slot.position),
        cy,
      };
    });

    let subject: SubjectMark;
    if (!row.answered) {
      subject = { kind: 'unanswered', cx: notchX, cy };
    } else if (row.subjectPresent) {
      const position = row.subjectPosition ?? row.slots.find((s) => s.isSubject)?.position ?? null;
      if (position === null) {
        // Named, but no ordinal was recorded. The mark still has to exist — it
        // just goes in the notch column, where it says "named, place unknown"
        // rather than claiming a position the answer never gave.
        warnings.push(`${row.promptId}/${row.engine}: named without an ordinal`);
        subject = { kind: 'present', position: 0, cited: row.subjectCited === true, cx: notchX, cy };
      } else {
        subject = {
          kind: 'present',
          position,
          cited: row.subjectCited === true,
          // Past the visible track the subject falls back to the notch column
          // rather than off the edge. It is never dropped by the cap.
          cx: position <= maxSlots ? columnX(position) : notchX,
          cy,
        };
      }
    } else {
      absentCount += 1;
      subject = { kind: 'absent', cx: notchX, cy };
    }

    out.push({
      promptId: row.promptId,
      promptText: row.promptText,
      promptPosition: row.promptPosition,
      engine: row.engine,
      answered: row.answered,
      marks,
      subject,
      hiddenCount,
      y: cy,
      rowKey: `${row.promptId}:${row.engine}`,
    });
  });

  return {
    rows: out,
    width: r(notchX + slotPitch),
    height: r(rows.length * rowHeight),
    notchX,
    labelWidth,
    absentCount,
    answeredCount,
    ordinals: Array.from({ length: maxSlots }, (_, i) => i + 1),
    warnings,
  };
}

/**
 * The spoken summary for `ChartFrame`'s required `ariaLabel`.
 *
 * Reads as the finding, not as a description of a picture — a screen-reader
 * user and a PDF accessibility audit both get the argument, not "chart".
 */
export function shelfSummary(layout: ShelfLayout, subjectName: string): string {
  const { answeredCount, absentCount } = layout;
  if (answeredCount === 0) return `No engine returned an answer, so there is no shelf to read.`;
  const named = answeredCount - absentCount;
  return (
    `Across ${answeredCount} answers, ${subjectName} was named in ${named} and absent from ` +
    `${absentCount}. Each row is one answer, with the brands it named in the order it named them.`
  );
}

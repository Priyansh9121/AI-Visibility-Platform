/**
 * Share of voice at ONE moment, as a divided strip — the layout half.
 *
 * Pure, and separated from the drawing for the reason `trendLayout.ts` is:
 * what matters here is what happens to a share that is ZERO and a share that
 * was NEVER MEASURED, and that is arithmetic worth asserting directly.
 *
 * THE TWO ABSENCES, KEPT APART
 * ----------------------------
 * A field of brands shares the answers; the shares sum to the whole. A brand
 * can arrive here with three kinds of value:
 *
 *   - a share above zero — it gets a segment whose width IS the share;
 *   - exactly zero — it was in the field and was named in none of the
 *     answers. That is a finding, and it is the whole reason this shape
 *     exists for the subject. It cannot be a segment (a zero-width segment
 *     is invisible), so it is a stated line in the legend and the table;
 *   - `null` — not measured: the rival was not in the set, or the scan did
 *     not produce a reading. A different fact from zero, and said so.
 *
 * Neither absence may become a wedge, a sliver, or an "equal share": a donut
 * that splits the field into N equal-looking slots for N brands asserts a
 * distribution nobody measured. This layout only ever draws measured, non-zero
 * shares, and lists the rest by name with the reason.
 *
 * THE REMAINDER
 * -------------
 * The tracked field rarely IS the whole field: the engines name brands nobody
 * tracks. When the measured shares sum to less than the whole, the difference
 * is drawn as an OUTLINE segment labelled as such — brands that were named
 * but are not in this client's competitor set — so the strip still sums to the
 * whole and the gap reads as "someone else", not as empty. Under one point
 * is treated as rounding and not drawn: every share is a rounded figure, and
 * four of them can miss the whole by a few tenths without anyone missing.
 * Over the whole (rounding the other way) is scaled back to it.
 */

export interface ShareInput {
  key: string;
  label: string;
  /** Percent of the whole, or null when not measured. */
  value: number | null;
  isSubject?: boolean;
}

export interface ShareSegment {
  key: string;
  label: string;
  value: number;
  isSubject: boolean;
  /** Position among the drawn RIVALS, for the pattern/lightness cycle. */
  rivalIndex: number;
  /** Left edge and width in viewBox units. */
  x: number;
  width: number;
}

export type ShareAbsence = 'zero' | 'unmeasured';

export interface ShareAbsent {
  key: string;
  label: string;
  kind: ShareAbsence;
  isSubject: boolean;
}

export interface ShareLayout {
  width: number;
  height: number;
  segments: ShareSegment[];
  absent: ShareAbsent[];
  /** Percent of the whole named brands outside the tracked field hold; null when none is drawn. */
  remainder: { x: number; width: number; value: number } | null;
  /** The measured, non-zero shares summed, before any scaling. */
  measuredTotal: number;
}

export interface ShareLayoutOptions {
  width?: number;
  height?: number;
  /** The whole the shares are fractions of. 100 for percentages. */
  whole?: number;
}

export const SHARE_WIDTH = 720;
export const SHARE_HEIGHT = 14;
/** Below this, a shortfall against the whole is rounding, not a remainder. */
export const REMAINDER_FLOOR = 1;

export function layoutShare(
  shares: readonly ShareInput[],
  options: ShareLayoutOptions = {},
): ShareLayout {
  const width = options.width ?? SHARE_WIDTH;
  const height = options.height ?? SHARE_HEIGHT;
  const whole = options.whole ?? 100;

  const drawn: ShareInput[] = [];
  const absent: ShareAbsent[] = [];
  for (const share of shares) {
    if (share.value === null || Number.isNaN(share.value)) {
      absent.push({ key: share.key, label: share.label, kind: 'unmeasured', isSubject: !!share.isSubject });
    } else if (share.value <= 0) {
      absent.push({ key: share.key, label: share.label, kind: 'zero', isSubject: !!share.isSubject });
    } else {
      drawn.push(share);
    }
  }

  // The subject first, then rivals largest first — the strip reads left to
  // right as "this client, then who holds the rest". Ties keep input order.
  const subject = drawn.filter((s) => s.isSubject);
  const rivals = drawn
    .filter((s) => !s.isSubject)
    .map((s, i) => ({ s, i }))
    .sort((a, b) => (b.s.value! - a.s.value!) || a.i - b.i)
    .map(({ s }) => s);

  const measuredTotal = drawn.reduce((n, s) => n + (s.value ?? 0), 0);
  // Rounded shares can overshoot the whole by tenths; scale back rather than
  // overflow the strip. They can undershoot too; that is the remainder below.
  const scale = measuredTotal > whole ? whole / measuredTotal : 1;

  const segments: ShareSegment[] = [];
  let x = 0;
  let rivalIndex = 0;
  for (const s of [...subject, ...rivals]) {
    const value = s.value! * scale;
    const w = (value / whole) * width;
    segments.push({
      key: s.key,
      label: s.label,
      value: s.value!,
      isSubject: !!s.isSubject,
      rivalIndex: s.isSubject ? -1 : rivalIndex++,
      x,
      width: w,
    });
    x += w;
  }

  // A remainder is a claim — "brands nobody tracks hold this much" — and it
  // needs a measurement under it. Measured zeros are one: every tracked brand
  // at zero means the whole went elsewhere. Nulls are not: if nothing was
  // measured, nothing is known about the rest of the field either.
  const measuredAny = shares.some((s) => s.value !== null && !Number.isNaN(s.value));
  const shortfall = whole - measuredTotal * scale;
  const remainder =
    measuredAny && shortfall >= REMAINDER_FLOOR
      ? { x, width: (shortfall / whole) * width, value: shortfall }
      : null;

  return { width, height, segments, absent, remainder, measuredTotal };
}

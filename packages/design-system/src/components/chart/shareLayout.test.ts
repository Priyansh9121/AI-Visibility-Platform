/**
 * The share strip's arithmetic — built from a real scan, not invented numbers.
 *
 * MSM AV (msmav.com.au), 20 prompts, four engines, scanned 22 Sept 2026: the
 * subject holds 0% of the field, one rival (Avalliance) sits at exactly 0.0
 * beside four with real shares, and the four real shares happen to sum to the
 * whole. That one scan exercises every branch this layout has.
 */
import { describe, it, expect } from 'vitest';
import { REMAINDER_FLOOR, SHARE_WIDTH, layoutShare } from './shareLayout.js';
import { MSM_AV_FIELD } from './shareLayout.fixtures.js';

const MSM_AV = MSM_AV_FIELD;

describe('layoutShare on the MSM AV scan', () => {
  const layout = layoutShare(MSM_AV);

  it('draws the four rivals with real shares, largest first, and nothing else', () => {
    expect(layout.segments.map((s) => s.label)).toEqual([
      'Sweetwater',
      'Shure',
      'AVI-SPL',
      'Diversified',
    ]);
  });

  it('a zero-share rival is NOT a segment — it is a stated absence', () => {
    // A confident wedge for Avalliance next to Sweetwater's 38.5% would be
    // the silent lie this shape exists to refuse.
    expect(layout.segments.find((s) => s.label === 'Avalliance')).toBeUndefined();
    expect(layout.absent).toContainEqual({
      key: 'avalliance',
      label: 'Avalliance',
      kind: 'zero',
      isSubject: false,
    });
  });

  it('the subject at zero is the first thing listed as absent, and is marked as the subject', () => {
    expect(layout.absent[0]).toEqual({ key: 'msmav', label: 'MSM AV', kind: 'zero', isSubject: true });
  });

  it('segment widths ARE the shares — the strip is the number, not a picture of it', () => {
    const byLabel = Object.fromEntries(layout.segments.map((s) => [s.label, s]));
    expect(byLabel['Sweetwater']!.width).toBeCloseTo((38.5 / 100) * SHARE_WIDTH, 6);
    expect(byLabel['Shure']!.x).toBeCloseTo(byLabel['Sweetwater']!.width, 6);
    expect(layout.measuredTotal).toBeCloseTo(100, 6);
  });

  it('draws no remainder when the tracked field is the whole field', () => {
    expect(layout.remainder).toBeNull();
  });
});

describe('the other absences and edges', () => {
  it('null is "not measured", never zero and never a segment', () => {
    const layout = layoutShare([
      { key: 'me', label: 'Plausible', value: 36.3, isSubject: true },
      { key: 'r', label: 'Matomo', value: null },
    ]);
    expect(layout.segments.map((s) => s.label)).toEqual(['Plausible']);
    expect(layout.absent).toEqual([
      { key: 'r', label: 'Matomo', kind: 'unmeasured', isSubject: false },
    ]);
  });

  it('brands named outside the tracked field are an outlined remainder, not empty space', () => {
    const layout = layoutShare([
      { key: 'me', label: 'Plausible', value: 36.3, isSubject: true },
      { key: 'r', label: 'Matomo', value: 21.0 },
    ]);
    expect(layout.remainder).not.toBeNull();
    expect(layout.remainder!.value).toBeCloseTo(100 - 57.3, 6);
    expect(layout.remainder!.x).toBeCloseTo(((36.3 + 21.0) / 100) * SHARE_WIDTH, 6);
  });

  it('a shortfall under one point is rounding, and is not drawn as a remainder', () => {
    const layout = layoutShare([
      { key: 'a', label: 'A', value: 60.2 },
      { key: 'b', label: 'B', value: 39.4 },
    ]);
    expect(100 - layout.measuredTotal).toBeLessThan(REMAINDER_FLOOR);
    expect(layout.remainder).toBeNull();
  });

  it('shares that overshoot the whole by rounding are scaled back, never overflow', () => {
    const layout = layoutShare([
      { key: 'a', label: 'A', value: 60.3 },
      { key: 'b', label: 'B', value: 40.1 },
    ]);
    const end = layout.segments[layout.segments.length - 1]!;
    expect(end.x + end.width).toBeCloseTo(SHARE_WIDTH, 6);
    // The VALUES stay what was measured; only the drawing is scaled.
    expect(layout.segments.map((s) => s.value)).toEqual([60.3, 40.1]);
  });

  it('the subject is drawn first even when a rival holds more', () => {
    const layout = layoutShare([
      { key: 'r', label: 'Big Rival', value: 70 },
      { key: 'me', label: 'Me', value: 10, isSubject: true },
    ]);
    expect(layout.segments[0]!.label).toBe('Me');
    expect(layout.segments[0]!.rivalIndex).toBe(-1);
    expect(layout.segments[1]!.rivalIndex).toBe(0);
  });

  it('every tracked brand measured at zero means the whole went to brands nobody tracks', () => {
    const layout = layoutShare([
      { key: 'me', label: 'Me', value: 0, isSubject: true },
      { key: 'r', label: 'Rival', value: 0 },
    ]);
    expect(layout.segments).toEqual([]);
    expect(layout.remainder).toEqual({ x: 0, width: SHARE_WIDTH, value: 100 });
  });

  it('an empty field is all absence and no segments', () => {
    const layout = layoutShare([{ key: 'me', label: 'Me', value: null, isSubject: true }]);
    expect(layout.segments).toEqual([]);
    expect(layout.remainder).toBeNull();
    expect(layout.absent[0]!.kind).toBe('unmeasured');
  });
});

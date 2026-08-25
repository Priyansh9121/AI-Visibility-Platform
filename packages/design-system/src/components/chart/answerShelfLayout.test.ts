import { describe, it, expect } from 'vitest';
import {
  layoutAnswerShelf,
  shelfSummary,
  type ShelfRowInput,
  type ShelfSlotInput,
} from './answerShelfLayout.js';
import { beacon, competitor, oklch, visibility } from '../../tokens/color.js';

const slot = (
  position: number,
  entityName: string,
  extra: Partial<ShelfSlotInput> = {},
): ShelfSlotInput => ({
  position,
  entityName,
  isSubject: false,
  cited: false,
  ...extra,
});

const row = (over: Partial<ShelfRowInput> = {}): ShelfRowInput => ({
  promptId: 'p1',
  promptText: 'best help desk software',
  promptPosition: 1,
  engine: 'claude',
  answered: true,
  subjectPresent: true,
  subjectPosition: 2,
  subjectCited: false,
  slots: [slot(1, 'Zendesk'), slot(2, 'Help Scout', { isSubject: true })],
  ...over,
});

describe('layoutAnswerShelf — the load-bearing identity', () => {
  it('gives EVERY answered row exactly one subject mark, present or absent', () => {
    // The whole argument of this chart is the shape of absence. A row that
    // renders no mark when the subject is missing deletes the finding, and it
    // does not look like a bug — it looks like a clean report.
    const rows = [
      row({ promptId: 'a', subjectPresent: true, subjectPosition: 2 }),
      row({ promptId: 'b', subjectPresent: false, subjectPosition: null, slots: [slot(1, 'Zendesk')] }),
      row({ promptId: 'c', subjectPresent: false, subjectPosition: null, slots: [] }),
      row({ promptId: 'd', subjectPresent: true, subjectPosition: 1, slots: [slot(1, 'Help Scout', { isSubject: true })] }),
    ];
    const layout = layoutAnswerShelf(rows);

    expect(layout.rows).toHaveLength(4);
    for (const laid of layout.rows) {
      expect(laid.subject).toBeDefined();
      expect(['present', 'absent']).toContain(laid.subject.kind);
    }
    expect(layout.rows.filter((laid) => laid.subject.kind === 'absent')).toHaveLength(2);
    expect(layout.absentCount).toBe(2);
    expect(layout.answeredCount).toBe(4);
  });

  it('counts an absence even when the answer named nobody at all', () => {
    const layout = layoutAnswerShelf([
      row({ subjectPresent: false, subjectPosition: null, slots: [] }),
    ]);
    expect(layout.rows[0]!.marks).toHaveLength(0);
    expect(layout.rows[0]!.subject.kind).toBe('absent');
    expect(layout.absentCount).toBe(1);
  });

  it('never draws an absence for an answer we did not get', () => {
    // We have no answer to be absent from. Drawing a hole would blame the
    // client for our own failed request.
    const layout = layoutAnswerShelf([
      row({ answered: false, subjectPresent: false, subjectPosition: null, slots: [] }),
    ]);
    expect(layout.rows[0]!.subject.kind).toBe('unanswered');
    expect(layout.absentCount).toBe(0);
    expect(layout.answeredCount).toBe(0);
  });

  it('places every absence in the same column, so the holes form a band', () => {
    const rows = Array.from({ length: 5 }, (_, i) =>
      row({
        promptId: `p${i}`,
        subjectPresent: false,
        subjectPosition: null,
        // Deliberately varying rival counts — the band must not wobble with them.
        slots: Array.from({ length: i + 1 }, (_, j) => slot(j + 1, `Rival ${j}`)),
      }),
    );
    const layout = layoutAnswerShelf(rows);

    const xs = new Set(layout.rows.map((laid) => laid.subject.cx));
    expect(xs.size).toBe(1);
    expect([...xs][0]).toBe(layout.notchX);
  });
});

describe('layoutAnswerShelf — ordinals', () => {
  it('puts the subject at the ordinal the answer gave it', () => {
    const layout = layoutAnswerShelf([row({ subjectPosition: 3 })], { maxSlots: 6 });
    const mark = layout.rows[0]!.subject;
    expect(mark.kind).toBe('present');
    if (mark.kind !== 'present') throw new Error('unreachable');
    expect(mark.position).toBe(3);
    expect(mark.cx).toBeLessThan(layout.notchX);
  });

  it('keeps stored ordinals rather than re-indexing the visible slots', () => {
    // A brand named 5th must not render as 2nd because the brands at 2, 3 and
    // 4 were not in the competitor set.
    const layout = layoutAnswerShelf([
      row({ slots: [slot(1, 'Zendesk'), slot(5, 'Front')], subjectPresent: false, subjectPosition: null }),
    ]);
    expect(layout.rows[0]!.marks.map((m) => m.position)).toEqual([1, 5]);
  });

  it('orders marks by ordinal, with a total tie-break', () => {
    const layout = layoutAnswerShelf([
      row({
        subjectPresent: false,
        subjectPosition: null,
        slots: [slot(2, 'Beta'), slot(1, 'Zulu'), slot(2, 'Alpha')],
      }),
    ]);
    expect(layout.rows[0]!.marks.map((m) => m.entityName)).toEqual(['Zulu', 'Alpha', 'Beta']);
  });

  it('is byte-stable across repeat runs', () => {
    // These coordinates reach PDFs clients compare month to month.
    const rows = [row(), row({ promptId: 'p2', subjectPosition: 4 })];
    const first = JSON.stringify(layoutAnswerShelf(rows));
    for (let i = 0; i < 20; i++) {
      expect(JSON.stringify(layoutAnswerShelf(rows))).toBe(first);
    }
  });
});

describe('layoutAnswerShelf — the cap', () => {
  it('never drops the subject off the end of the track', () => {
    // The cap is a display concern. Letting it decide whether the client
    // appears at all would let a layout constant fabricate an absence.
    const layout = layoutAnswerShelf([row({ subjectPosition: 11 })], { maxSlots: 4 });
    const mark = layout.rows[0]!.subject;
    expect(mark.kind).toBe('present');
    if (mark.kind !== 'present') throw new Error('unreachable');
    expect(mark.position).toBe(11);
    expect(mark.cx).toBe(layout.notchX);
  });

  it('reports what it hid rather than truncating silently', () => {
    const layout = layoutAnswerShelf(
      [
        row({
          subjectPresent: false,
          subjectPosition: null,
          slots: Array.from({ length: 9 }, (_, i) => slot(i + 1, `Rival ${i}`)),
        }),
      ],
      { maxSlots: 4 },
    );
    expect(layout.rows[0]!.marks).toHaveLength(4);
    expect(layout.rows[0]!.hiddenCount).toBe(5);
  });

  it('warns rather than inventing a place when a named subject has no ordinal', () => {
    const layout = layoutAnswerShelf([
      row({ subjectPresent: true, subjectPosition: null, slots: [slot(1, 'Zendesk')] }),
    ]);
    const mark = layout.rows[0]!.subject;
    expect(mark.kind).toBe('present');
    expect(layout.rows[0]!.subject.cx).toBe(layout.notchX);
    expect(layout.warnings).toHaveLength(1);
    expect(layout.absentCount).toBe(0);
  });
});

describe('layoutAnswerShelf — the series colour rule', () => {
  it('paints the subject beacon and never a ramp colour', () => {
    const layout = layoutAnswerShelf([row()]);
    // The subject mark carries no fill of its own — the component paints it
    // beacon — but no rival may borrow the brand accent either.
    const fills = layout.rows[0]!.marks.map((m) => m.fill);
    expect(fills).not.toContain(oklch(beacon['600']));
  });

  it('paints rivals neutral slate, never the visibility ramp', () => {
    // design-system.md: a rival in "good green" reads as an endorsement, and
    // one in "bad red" makes the report look like a hatchet job.
    const layout = layoutAnswerShelf([
      row({
        subjectPresent: false,
        subjectPosition: null,
        slots: Array.from({ length: 6 }, (_, i) => slot(i + 1, `Rival ${i}`)),
      }),
    ]);
    const rampColours = new Set(Object.values(visibility).map((v) => oklch(v)));
    const slateColours = new Set(Object.values(competitor).map((v) => oklch(v)));
    for (const mark of layout.rows[0]!.marks) {
      expect(rampColours.has(mark.fill)).toBe(false);
      expect(slateColours.has(mark.fill)).toBe(true);
    }
  });

  it('gives a rival the same shade in every row it appears in', () => {
    // Assigning per row would make one brand change colour down the page.
    const layout = layoutAnswerShelf([
      row({ promptId: 'a', subjectPresent: false, subjectPosition: null, slots: [slot(1, 'Zendesk'), slot(2, 'Front')] }),
      row({ promptId: 'b', subjectPresent: false, subjectPosition: null, slots: [slot(1, 'Front'), slot(2, 'Zendesk')] }),
    ]);
    const shade = (i: number, name: string) =>
      layout.rows[i]!.marks.find((m) => m.entityName === name)!.fill;
    expect(shade(0, 'Zendesk')).toBe(shade(1, 'Zendesk'));
    expect(shade(0, 'Front')).toBe(shade(1, 'Front'));
    expect(shade(0, 'Zendesk')).not.toBe(shade(0, 'Front'));
  });

  it('gives every rival a print pattern, so greyscale still separates them', () => {
    const layout = layoutAnswerShelf([
      row({
        subjectPresent: false,
        subjectPosition: null,
        slots: Array.from({ length: 5 }, (_, i) => slot(i + 1, `Rival ${i}`)),
      }),
    ]);
    const patterns = layout.rows[0]!.marks.map((m) => m.pattern);
    expect(new Set(patterns).size).toBe(5);
  });
});

describe('layoutAnswerShelf — citation ticks', () => {
  it('carries citation presence per answer, not per scan', () => {
    const layout = layoutAnswerShelf([
      row({ subjectCited: true }),
      row({ promptId: 'p2', engine: 'claude_search', subjectCited: false }),
    ]);
    const cited = (i: number) => {
      const m = layout.rows[i]!.subject;
      return m.kind === 'present' ? m.cited : false;
    };
    expect(cited(0)).toBe(true);
    expect(cited(1)).toBe(false);
  });

  it('lets a rival be named without being cited, and vice versa', () => {
    const layout = layoutAnswerShelf([
      row({
        subjectPresent: false,
        subjectPosition: null,
        slots: [slot(1, 'Zendesk', { cited: true }), slot(2, 'Front', { cited: false })],
      }),
    ]);
    expect(layout.rows[0]!.marks.map((m) => m.cited)).toEqual([true, false]);
  });
});

describe('shelfSummary', () => {
  it('reads as the finding, not as a description of a picture', () => {
    const layout = layoutAnswerShelf([
      row({ subjectPresent: true }),
      row({ promptId: 'b', subjectPresent: false, subjectPosition: null, slots: [slot(1, 'Zendesk')] }),
      row({ promptId: 'c', subjectPresent: false, subjectPosition: null, slots: [slot(1, 'Front')] }),
    ]);
    const text = shelfSummary(layout, 'Help Scout');
    expect(text).toContain('Help Scout was named in 1 and absent from 2');
    expect(text).not.toMatch(/\bchart\b/i);
  });

  it('says so plainly when no engine answered', () => {
    const layout = layoutAnswerShelf([row({ answered: false, subjectPresent: false, slots: [] })]);
    expect(shelfSummary(layout, 'Help Scout')).toContain('no shelf to read');
  });
});

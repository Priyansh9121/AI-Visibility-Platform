/**
 * What a refused save of the competitor list says — launch checklist 3.113.
 *
 * The API answers a save made from a list that has since changed with 409
 * `/problems/competitor-list-changed` and the list as it stands. The editor and the rival
 * suggestions card each say what changed, by comparing the list they read with that one. Held here:
 * the comparison (added, removed, struck, and nothing visible), the sentence and its pinned wording,
 * and reading the refusal off the wire.
 */

import { describe, it, expect } from 'vitest';
import type { CompetitorSet } from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';
import {
  COMPETITOR_LIST_CHANGED,
  LIST_CHANGED_COPY,
  MOST_NAMED,
  compareLists,
  listChangedProblem,
  listChangedSentence,
  listChangedSentenceFor,
  type ListChange,
  type ListedRival,
} from './competitorListChange';

const rival = (name: string, domain: string | null = null): ListedRival => ({ name, domain });
const names = (rivals: readonly ListedRival[]) => rivals.map((r) => r.name);

const listOf = (...rivals: ListedRival[]): CompetitorSet =>
  ({
    id: 'cset_1',
    version: 'b'.repeat(32),
    competitors: rivals.map((r, i) => ({ id: `comp_${i}`, rank: i + 1, ...r })),
  }) as unknown as CompetitorSet;

const refusalWith = (extra: Record<string, unknown>, type = COMPETITOR_LIST_CHANGED) =>
  new ApiProblem({
    type,
    title: 'The competitor list changed',
    status: 409,
    detail: 'The list changed since you read it.',
    instance: '/api/v1/clients/cli_1/competitors',
    ...extra,
  } as never);

describe('comparing the list the writer read with the list as it stands', () => {
  it('finds a rival that was added', () => {
    const change = compareLists([rival('Zendesk')], [rival('Zendesk'), rival('Deakin')]);
    expect(names(change.added)).toEqual(['Deakin']);
    expect(change.removed).toEqual([]);
  });

  it('finds a rival that was removed', () => {
    const change = compareLists([rival('Zendesk'), rival('Intercom')], [rival('Zendesk')]);
    expect(names(change.removed)).toEqual(['Intercom']);
    expect(change.added).toEqual([]);
  });

  it('finds a rival somebody struck as one that was removed: the API shows the list without struck rivals', () => {
    // A struck rival is a tombstone the API leaves out of `current`. All the comparison can see is
    // that the name is gone from the list, and "removed" is what the person did.
    const read = [rival('Zendesk', 'zendesk.com'), rival('Intercom', 'intercom.com')];
    const current = [rival('Zendesk', 'zendesk.com')];
    expect(names(compareLists(read, current).removed)).toEqual(['Intercom']);
  });

  it('finds an added and a removed rival together, each side in the order of its list', () => {
    const change = compareLists(
      [rival('A'), rival('B'), rival('C')],
      [rival('D'), rival('A'), rival('E')],
    );
    expect(names(change.added)).toEqual(['D', 'E']);
    expect(names(change.removed)).toEqual(['B', 'C']);
  });

  it('finds nothing when the same rivals are on both lists, whatever their order or domains', () => {
    const read = [rival('A', 'a.com'), rival('B', 'b.com')];
    const now = [rival('B', 'b.org'), rival('A', null)];
    expect(compareLists(read, now)).toEqual({ added: [], removed: [] });
  });

  it('takes one name for another however it was typed: case and spaces are not a change', () => {
    const change = compareLists([rival('Help  Scout')], [rival(' help scout ')]);
    expect(change).toEqual({ added: [], removed: [] });
  });

  it('finds everything added to an empty list, and everything removed from a list now empty', () => {
    expect(names(compareLists([], [rival('A')]).added)).toEqual(['A']);
    expect(names(compareLists([rival('A')], []).removed)).toEqual(['A']);
  });
});

describe('the sentence', () => {
  const after = LIST_CHANGED_COPY.afterOpening;

  it('names the client’s list and the one rival added', () => {
    expect(listChangedSentence({ added: [rival('deakin.edu.au')], removed: [] }, after)).toBe(
      'The client’s list changed after you opened it: deakin.edu.au was added. Your change was not saved.',
    );
  });

  it('names the client’s list when nothing visible differs', () => {
    expect(listChangedSentence({ added: [], removed: [] }, after)).toBe(
      'The client’s list changed after you opened it. Your change was not saved.',
    );
  });

  // The change may be a scan's own detection or the person's other tab, so "Someone" would be false;
  // and "This list" has no antecedent in the card, and reads as the scan's list in the editor.
  it('does not say who changed the list, and says whose list it is', () => {
    const shapes: ListChange[] = [
      { added: [rival('A')], removed: [] },
      { added: [], removed: [rival('A')] },
      { added: [rival('A')], removed: [rival('B')] },
      { added: [], removed: [] },
    ];
    for (const change of shapes) {
      for (const when of [LIST_CHANGED_COPY.afterOpening, LIST_CHANGED_COPY.justNow]) {
        const sentence = listChangedSentence(change, when);
        expect(sentence.startsWith(`The client’s list changed ${when}`)).toBe(true);
        expect(sentence).not.toMatch(/someone/i);
        expect(sentence).not.toContain('This list');
      }
    }
  });

  it('gives a rival its domain in brackets, unless the name is the domain', () => {
    expect(listChangedSentence({ added: [rival('Deakin', 'deakin.edu.au')], removed: [] }, after)).toBe(
      'The client’s list changed after you opened it: Deakin (deakin.edu.au) was added. Your change was not saved.',
    );
    expect(listChangedSentence({ added: [rival('Deakin', 'DEAKIN')], removed: [] }, after)).toBe(
      'The client’s list changed after you opened it: Deakin was added. Your change was not saved.',
    );
  });

  it('agrees in number: was for one, were for several, joined as a list', () => {
    expect(listChangedSentence({ added: [rival('A'), rival('B')], removed: [] }, after)).toContain(
      ': A and B were added.',
    );
    expect(
      listChangedSentence({ added: [rival('A'), rival('B'), rival('C')], removed: [] }, after),
    ).toContain(': A, B and C were added.');
    expect(listChangedSentence({ added: [], removed: [rival('A')] }, after)).toContain(
      ': A was removed.',
    );
  });

  it('says an added and a removed rival in one sentence', () => {
    expect(listChangedSentence({ added: [rival('A')], removed: [rival('B'), rival('C')] }, after)).toBe(
      'The client’s list changed after you opened it: A was added and B and C were removed. Your change was not saved.',
    );
  });

  it('counts, and names no one, when more than three differ on one side', () => {
    const many = ['A', 'B', 'C', 'D'].map((n) => rival(n));
    expect(MOST_NAMED).toBe(3);
    expect(listChangedSentence({ added: many, removed: [] }, after)).toBe(
      'The client’s list changed after you opened it: 4 rivals were added. Your change was not saved.',
    );
    expect(listChangedSentence({ added: many.slice(0, 3), removed: many }, after)).toContain(
      ': A, B and C were added and 4 rivals were removed.',
    );
  });

  it('takes the writer’s own words for when: the editor opened the list, the card read it just now', () => {
    expect(LIST_CHANGED_COPY.justNow).toBe('just now');
    expect(listChangedSentence({ added: [rival('A')], removed: [] }, LIST_CHANGED_COPY.justNow)).toBe(
      'The client’s list changed just now: A was added. Your change was not saved.',
    );
    expect(listChangedSentence({ added: [], removed: [] }, LIST_CHANGED_COPY.justNow)).toBe(
      'The client’s list changed just now. Your change was not saved.',
    );
  });

  it('always says the change was not saved', () => {
    expect(LIST_CHANGED_COPY.notSaved).toBe('Your change was not saved.');
  });
});

describe('the sentence for a refusal', () => {
  it('compares the list that was read with the list the refusal carries', () => {
    const sentence = listChangedSentenceFor(
      [rival('Zendesk')],
      { current: listOf(rival('Zendesk'), rival('Deakin', 'deakin.edu.au')) },
      LIST_CHANGED_COPY.afterOpening,
    );
    expect(sentence).toBe(
      'The client’s list changed after you opened it: Deakin (deakin.edu.au) was added. Your change was not saved.',
    );
  });

  it('names no one when the refusal carries no list', () => {
    expect(
      listChangedSentenceFor([rival('Zendesk')], { current: null }, LIST_CHANGED_COPY.afterOpening),
    ).toBe('The client’s list changed after you opened it. Your change was not saved.');
  });
});

describe('reading the refusal off the wire', () => {
  it('knows the refusal by its type and hands back the list as it stands', () => {
    const current = listOf(rival('Zendesk'));
    const read = listChangedProblem(refusalWith({ current }));
    expect(read).not.toBeNull();
    expect(read!.current!.version).toBe('b'.repeat(32));
    expect(names(read!.current!.competitors ?? [])).toEqual(['Zendesk']);
  });

  it('is still the refusal when its list is null, and says there is no list to read', () => {
    expect(listChangedProblem(refusalWith({ current: null }))).toEqual({ current: null });
    expect(listChangedProblem(refusalWith({}))).toEqual({ current: null });
  });

  it.each([
    ['a string', 'the list'],
    ['a list with no version', { competitors: [{ name: 'A' }] }],
    ['a list whose competitors are not a list', { version: 'v', competitors: 'A' }],
    ['a list with a row that has no name', { version: 'v', competitors: [{ domain: 'a.com' }] }],
  ])('reads %s as no list, not as a list it can compare', (_what, current) => {
    expect(listChangedProblem(refusalWith({ current }))).toEqual({ current: null });
  });

  it('is not the refusal for any other problem, whatever it carries', () => {
    const other = refusalWith({ current: listOf(rival('A')) }, '/problems/conflict');
    expect(listChangedProblem(other)).toBeNull();
    expect(listChangedProblem(new Error('offline'))).toBeNull();
    expect(listChangedProblem('/problems/competitor-list-changed')).toBeNull();
    expect(listChangedProblem(undefined)).toBeNull();
  });
});

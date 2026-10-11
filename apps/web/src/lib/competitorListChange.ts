/**
 * A save of the competitor list that was refused because the list is no longer the one the writer
 * read — launch checklist 3.113.
 *
 * `PUT /clients/{id}/competitors` replaces the whole list. A writer that read the list, and was
 * then overtaken by somebody else's save or by a new scan's detection, would strike every rival
 * it never saw: later scans skip a struck rival and detection cannot bring it back. So a save
 * names the `version` of the list it read, and the API, which writes nothing in that case,
 * answers 409 `/problems/competitor-list-changed` when the list has moved on. The problem carries
 * `current`, the list as it stands.
 *
 * This module reads that answer and says, in one plain sentence, what changed: it compares the list
 * the writer read with `current`. Both writers use it, the report's competitor editor and the
 * rival suggestions card, so the two say the same thing in the same words.
 *
 * What a sentence can name is the rivals that are on one list and not the other. `current` is the
 * list as the API shows it, and that leaves out a struck rival, so a rival somebody removed (struck)
 * is, to this comparison, a rival that is no longer on the list: it is said to be removed. A change
 * the names do not show (the order, a domain, a rival now set by hand) is said without names.
 */

import type { CompetitorSet } from '@avp/shared-types';
import { ApiProblem } from '@/lib/api';

/** The problem type of the refusal (`errors.CompetitorListChanged`). */
export const COMPETITOR_LIST_CHANGED = '/problems/competitor-list-changed';

/** A rival as a list shows it: the editor's drafts, the API's rows and the card's all fit. */
export interface ListedRival {
  name: string;
  domain?: string | null | undefined;
}

/** What differs between two lists, by name. Each side is in the order its list has. */
export interface ListChange {
  /** On the list now, and not on the one the writer read. */
  added: ListedRival[];
  /** On the list the writer read, and not on the one now (somebody removed it, or struck it). */
  removed: ListedRival[];
}

export const NO_CHANGE_SEEN: ListChange = { added: [], removed: [] };

/** The words of the sentence, in one place so a test can pin each. */
export const LIST_CHANGED_COPY = {
  /** For the editor, which reads the list when it opens. */
  afterOpening: 'after you opened it',
  /** For the card, which reads the list as it saves. */
  justNow: 'just now',
  notSaved: 'Your change was not saved.',
} as const;

/** More than this many names on one side are counted instead of listed. */
export const MOST_NAMED = 3;

const isRow = (value: unknown): boolean =>
  typeof value === 'object' && value !== null && typeof (value as { name?: unknown }).name === 'string';

/** The `current` member of the refusal, if it is a list: it comes off the wire as `unknown`. */
function asList(value: unknown): CompetitorSet | null {
  if (typeof value !== 'object' || value === null) return null;
  const { competitors, version } = value as { competitors?: unknown; version?: unknown };
  if (!Array.isArray(competitors) || !competitors.every(isRow)) return null;
  return typeof version === 'string' ? (value as CompetitorSet) : null;
}

/**
 * The refusal, if `err` is one: the list as it stands now (null if the answer carried none, which
 * is the API's way of saying the client has no list at all, or one this cannot read). Null if `err`
 * is anything else, which the caller handles as it handled it before.
 */
export function listChangedProblem(err: unknown): { current: CompetitorSet | null } | null {
  if (!(err instanceof ApiProblem) || err.type !== COMPETITOR_LIST_CHANGED) return null;
  return { current: asList(err.problem['current']) };
}

/** The same rival, however it was typed: trimmed, one space between words, lower case. */
const same = (name: string): string => name.trim().replace(/\s+/g, ' ').toLowerCase();

/** What differs between the list the writer read and the list as it stands, by name. */
export function compareLists(
  read: readonly ListedRival[],
  now: readonly ListedRival[],
): ListChange {
  const had = new Set(read.map((r) => same(r.name)));
  const has = new Set(now.map((r) => same(r.name)));
  return {
    added: now.filter((r) => !had.has(same(r.name))),
    removed: read.filter((r) => !has.has(same(r.name))),
  };
}

/** "Deakin (deakin.edu.au)", or the name alone when it has no domain or is the domain. */
const label = (r: ListedRival): string =>
  r.domain && same(r.domain) !== same(r.name) ? `${r.name} (${r.domain})` : r.name;

/** "A", "A and B", "A, B and C". */
function joined(names: readonly string[]): string {
  if (names.length < 2) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** "Deakin was added", "A and B were removed", "5 rivals were added", or '' for none. */
function said(rivals: readonly ListedRival[], what: 'added' | 'removed'): string {
  if (rivals.length === 0) return '';
  if (rivals.length > MOST_NAMED) return `${rivals.length} rivals were ${what}`;
  return `${joined(rivals.map(label))} ${rivals.length === 1 ? 'was' : 'were'} ${what}`;
}

/**
 * The one sentence for a refused save: "The client’s list changed after you opened it: Deakin
 * (deakin.edu.au) was added. Your change was not saved." With no name to give (the lists hold the
 * same rivals, or `current` could not be read): "The client’s list changed after you opened it. Your
 * change was not saved." `when` is how the writer came by its list (`LIST_CHANGED_COPY.afterOpening`
 * or `.justNow`). It does not say who changed the list, because it cannot know: it may be another
 * person, the person's other tab or a scan's own detection. And it says whose list it is: "this
 * list" has no antecedent in the card, and in the editor it reads as the scan's own list.
 */
export function listChangedSentence(change: ListChange, when: string): string {
  const parts = [said(change.added, 'added'), said(change.removed, 'removed')].filter(Boolean);
  const lead =
    parts.length > 0
      ? `The client’s list changed ${when}: ${parts.join(' and ')}.`
      : `The client’s list changed ${when}.`;
  return `${lead} ${LIST_CHANGED_COPY.notSaved}`;
}

/**
 * The sentence for a refusal, from the list the writer read and the refusal's `current`. When
 * `current` is not a list there is nothing to compare, and the sentence names no one.
 */
export function listChangedSentenceFor(
  read: readonly ListedRival[],
  refusal: { current: CompetitorSet | null },
  when: string,
): string {
  const now = refusal.current?.competitors;
  return listChangedSentence(now ? compareLists(read, now) : NO_CHANGE_SEEN, when);
}

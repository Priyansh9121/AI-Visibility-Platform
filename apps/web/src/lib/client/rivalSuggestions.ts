/**
 * Probable rivals on a client's Competitors page — the body a save sends, the refusals made before
 * it, and the words around it (F2, 2026-10-09).
 *
 * A domain with the client's own restricted ending (edu.au for rmit.edu.au) that the latest scan's
 * answers named ("Deakin University") is a probable rival: the report's "Get onto" skips it
 * (`services/report.py`) and this page offers it as a rival instead, never the report. The agency
 * adds it through `PUT /clients/{id}/competitors`, which REPLACES the whole list, so the body is the
 * client's current list plus this one. That is the competitor editor's body and its client-side
 * refusals (`CompetitorEditor`): a row per rival as `{ name, domain }`, at most ten, no name twice.
 * The refusals the API makes itself (a platform or publisher, a scan running) come back through the
 * PUT and are shown in the API's own sentence.
 */

import type { Competitor, CompetitorInput, RivalSuggestion } from '@avp/shared-types';

/** Mirrors the API's `max_length=10` on ReplaceCompetitorsRequest.competitors (and the editor's). */
export const MAX_RIVALS = 10;

const times = (n: number): string => `${n} ${n === 1 ? 'time' : 'times'}`;
const answers = (n: number): string => `${n} ${n === 1 ? 'answer' : 'answers'}`;

/** The founder's line: "deakin.edu.au: cited 6 times, named in 4 answers. Add it as a rival?" */
export const suggestionRest = (s: Pick<RivalSuggestion, 'citations' | 'answersNaming'>): string =>
  `: cited ${times(s.citations)}, named in ${answers(s.answersNaming)}. Add it as a rival?`;

export const suggestionSentence = (s: RivalSuggestion): string => `${s.domain}${suggestionRest(s)}`;

/** The client's current list plus this suggestion, in the editor's shape. */
export function rivalsBody(
  current: readonly Pick<Competitor, 'name' | 'domain'>[],
  add: Pick<RivalSuggestion, 'name' | 'domain'>,
): CompetitorInput[] {
  return [
    ...current.map((c) => ({ name: c.name, domain: c.domain?.trim() || null })),
    { name: add.name, domain: add.domain },
  ];
}

/** The host a domain names, however it was typed: lower case, an internationalised label in DNS form. */
function host(domain: string): string {
  try {
    return new URL(`http://${domain.trim()}`).hostname;
  } catch {
    return domain.trim().toLowerCase();
  }
}

/** The list already holds this domain: somebody added it since the suggestions were read. */
export const alreadyARival = (
  current: readonly Pick<Competitor, 'domain'>[],
  add: Pick<RivalSuggestion, 'domain'>,
): boolean => current.some((c) => c.domain != null && host(c.domain) === host(add.domain));

/** What the editor refuses before it saves: a full list, or a name already on it. Null if neither. */
export function refusalBeforeSaving(
  current: readonly Pick<Competitor, 'name'>[],
  add: Pick<RivalSuggestion, 'name'>,
): string | null {
  if (current.length >= MAX_RIVALS) return `A set carries at most ${MAX_RIVALS} competitors.`;
  if (current.some((c) => c.name.toLowerCase() === add.name.toLowerCase())) {
    return `${add.name} is already on the list.`;
  }
  return null;
}

/**
 * The line for a 422 that names a row's domain (`competitors.<i>.domain`): "<that row's name>: <the
 * API's sentence>", as the editor says it. Empty when the problem names no row's domain.
 */
export function rowProblem(
  fieldErrors: Readonly<Record<string, string>>,
  rows: readonly Pick<CompetitorInput, 'name'>[],
): string {
  for (const [field, message] of Object.entries(fieldErrors)) {
    const match = /^competitors\.(\d+)\.domain$/.exec(field);
    const row = match ? rows[Number(match[1])] : undefined;
    if (row) return `${row.name}: ${message}`;
  }
  return '';
}

export const RIVAL_SUGGESTIONS_COPY = {
  title: 'Probable rivals',
  /**
   * What makes a site a probable rival. The answers named a brand whose name matches the site's
   * label, not the site itself, and the sentence says so.
   */
  intro:
    'Sites that share this client’s domain ending, were cited in the latest scan, and match a brand named in the scan’s answers.',
  button: 'Add as a rival',
  /** Said, politely, once the PUT has answered. */
  added: (domain: string) => `${domain} is now a rival. The next scan measures it.`,
  /** Somebody added it first: no save was made. */
  alreadyThere: (domain: string) => `${domain} is already a rival.`,
  /**
   * The PUT failed in a way that says nothing (a dropped connection, a problem with no words). The
   * save may still have been done, so the line does not say it was not. A retry is safe: the next
   * press re-reads the list and says the domain is already a rival.
   */
  saveFailed: 'The change may not have been saved. Try again.',
  /** The list is read before anything is saved, so when it cannot be read, nothing was saved. */
  listUnreadable: 'The current list of rivals could not be read, so nothing was added. Try again.',
  /**
   * Beside the sentence for a save refused because the list changed between the card reading it and
   * saving (`lib/competitorListChange`): read the suggestions again.
   */
  refresh: 'Refresh the suggestions',
  /** Said, politely, once they have been read again. */
  refreshed: 'Showing the latest suggestions.',
  /**
   * Said instead of `refreshed` when the suggestions read again are none: the card stays up, because
   * it has just said something, and a line that says it is showing suggestions over no rows is false.
   */
  refreshedNone: 'There are no suggestions now.',
  /**
   * They could not be read again; the card is as it was, and the sentence about the refused save
   * stays above this one.
   */
  refreshFailed: 'The suggestions could not be refreshed. Try again.',
} as const;

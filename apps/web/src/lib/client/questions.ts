/**
 * The sentences the Prompts tab says about the client's questions — F6 section 3.
 *
 * The API sends dates and names, never a message; this is the one place they become words. The
 * days are UTC and unpadded (`formatDay`), as the report prints "Same questions as the scan of
 * 9 Oct 2026".
 */

import type { CurrentQuestionSet } from '@avp/shared-types';
import { formatDay } from '@/lib/dates';

export const WRITE_NEW_QUESTIONS = 'Write new questions for the next scan';
export const UNDO_NEW_QUESTIONS = 'Undo';
export const NEW_QUESTIONS_ASKED = 'The next scan will ask new questions.';
/** Said in the status region after a request for new questions is taken back from a client with a set. */
export const NEW_QUESTIONS_TAKEN_BACK = 'The next scan will ask the same questions again.';
export const NO_QUESTIONS_YET = 'The next scan will write this client’s questions.';
export const QUESTIONS_NOT_LOADED = 'This client’s questions could not be loaded.';
export const LOADING_QUESTIONS = 'Loading this client’s questions…';
export const RIVALS_CHANGED =
  'These questions were written with your rivals as they were then; write new questions to ask about the current ones.';

/** Where the set came from: when it was written and, if a later scan copied it, since when. */
export function originSentence(set: Pick<CurrentQuestionSet, 'writtenAt' | 'reusedSince'>): string {
  if (!set.writtenAt) return 'These questions were written for an earlier scan.';
  const written = `These questions were written on ${formatDay(set.writtenAt)}`;
  return set.reusedSince
    ? `${written} and reused since the scan of ${formatDay(set.reusedSince)}.`
    : `${written}.`;
}

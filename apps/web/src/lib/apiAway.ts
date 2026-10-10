import { ApiProblem } from '@/lib/api';

/**
 * Whether a failed call says the API was AWAY, as opposed to the API saying no.
 *
 * Two shapes. No answer at all is a network error, which is not an `ApiProblem`.
 * And an answer that is not the API's own: a proxy's non-JSON 502 reaches
 * `request()` as an `ApiProblem` carrying that status, and one can arrive with
 * no status at all. A 5xx is the server failing rather than refusing, except the
 * API's own daily-limit 503 (`platform-cap-reached`), which is a refusal with a sentence. None of
 * them tells the person anything about what they asked for, so a screen says it
 * could not reach the server, or that it does not know how the request ended,
 * instead of repeating a status line. A 4xx is the API's own refusal and carries
 * its own sentence.
 *
 * One rule, named once: `/sign-up/google` decides between "expired" and
 * "unreachable" with it, and so do the form that completes that sign-up and the
 * share bar's withdraw.
 */
export function apiWasAway(err: unknown): boolean {
  if (!(err instanceof ApiProblem) || !err.status) return true;
  // The daily scan limit is a 503 the API itself sends, with a sentence for the person; it is
  // the API saying no, not the API being away.
  if (err.problem.type?.endsWith('/problems/platform-cap-reached')) return false;
  return err.status >= 500;
}

/**
 * Whether the API ANSWERED with a sentence meant for the person: any refusal `apiWasAway` does
 * not count as away, and also its own `service-unavailable` 503 ("This is temporarily unavailable.
 * Please try again shortly."), which the API sends when a limit it must count cannot be counted
 * (F3: the paid AI calls fail closed when Redis cannot say). `apiWasAway` stays as it was for the
 * screens that have always read that 503 as away (sign-in, sign-up); a screen that spends money
 * and says so asks this instead, so it shows the API's words and never "could not reach the
 * server".
 */
export function apiAnsweredWithWords(err: unknown): err is ApiProblem {
  if (!(err instanceof ApiProblem)) return false;
  if (!apiWasAway(err)) return true;
  return err.problem.type?.endsWith('/problems/service-unavailable') === true;
}

/**
 * What a form says when `apiWasAway` is true: the one sentence, so no screen
 * retypes it or puts a developer's instruction in its place. Submitting again
 * is the way to try again.
 */
export const API_AWAY_SENTENCE =
  'We could not reach the server. Check your connection and try again.';
